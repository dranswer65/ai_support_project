from __future__ import annotations

import os
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, List
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from core.tenant_reception_auth import resolve_and_validate_tenant_reception
from core.saas_limits import enforce_appointment_limit
from core.patient_schema import upsert_patient_registry
from core.auth_rbac import decode_access_token
from core.audit_logger import log_audit_event
from core.auth_rbac import get_current_user_context


# Optional services
try:
    from services.events.pg_bus import bus as reception_bus
except Exception:
    reception_bus = None

try:
    from services.messaging.whatsapp_sender import wa_send_text
except Exception:
    wa_send_text = None

try:
    from services.messaging.message_templates import (
        build_reception_message,
        build_appointment_confirmed_message,
        build_insurance_pending_message,
    )
except Exception:
    build_reception_message = None
    build_appointment_confirmed_message = None
    build_insurance_pending_message = None

try:
    from core.slot_holds_store_pg import confirm_hold_create_appointment
except Exception:
    confirm_hold_create_appointment = None


router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


def _parse_optional_date(value: Any) -> Optional[date]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Expected YYYY-MM-DD",
        )


def _hhmm(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    return s[:5]


def _make_direct_appointment_id() -> str:
    return f"APT-RCP-{uuid.uuid4().hex[:12].upper()}"


def _insurance_fields_from_request_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "insurance_provider": row.get("insurance_provider"),
        "insurance_plan": row.get("insurance_plan"),
        "insurance_member_id": row.get("insurance_member_id"),
        "insurance_policy_number": row.get("insurance_policy_number"),
        "insurance_expiry": row.get("insurance_expiry"),
        "insurance_status": row.get("insurance_status") or "SELF_PAY",
        "insurance_verified": bool(row.get("insurance_verified") or False),
    }


def _insurance_fields_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "insurance_provider": str(payload.get("insurance_provider") or "").strip() or None,
        "insurance_plan": str(payload.get("insurance_plan") or "").strip() or None,
        "insurance_member_id": str(payload.get("insurance_member_id") or "").strip() or None,
        "insurance_policy_number": str(payload.get("insurance_policy_number") or "").strip() or None,
        "insurance_expiry": _parse_optional_date(payload.get("insurance_expiry")),
        "insurance_status": str(payload.get("insurance_status") or "SELF_PAY").strip().upper() or "SELF_PAY",
        "insurance_verified": bool(payload.get("insurance_verified") or False),
    }


async def _get_tenant_settings(db: AsyncSession, tenant_id: str) -> Dict[str, Any]:
    res = await db.execute(
        text(
            """
            SELECT
                ts.tenant_id,
                ts.clinic_name,
                ts.language,
                ts.ai_tone,
                ts.timezone,
                ts.whatsapp_greeting,
                ts.reminder_hours_before,
                ts.enable_ai_booking,
                ts.enable_reception_direct_booking
            FROM tenant_settings ts
            WHERE ts.tenant_id = :tenant_id
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )

    row = res.mappings().first()
    if row:
        item = dict(row)
        item["enable_ai_booking"] = bool(item.get("enable_ai_booking", True))
        item["enable_reception_direct_booking"] = bool(
            item.get("enable_reception_direct_booking", True)
        )
        return item

    res2 = await db.execute(
        text(
            """
            SELECT
                tenant_id,
                clinic_name,
                default_language AS language,
                default_tone AS ai_tone,
                timezone
            FROM tenants
            WHERE tenant_id = :tenant_id
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )

    row2 = res2.mappings().first()
    if row2:
        item = dict(row2)
        item["enable_ai_booking"] = True
        item["enable_reception_direct_booking"] = True
        return item

    return {
        "tenant_id": tenant_id,
        "clinic_name": tenant_id,
        "language": "en",
        "ai_tone": "formal",
        "timezone": "Asia/Riyadh",
        "enable_ai_booking": True,
        "enable_reception_direct_booking": True,
    }


async def _require_direct_booking_enabled(db: AsyncSession, tenant_id: str) -> Dict[str, Any]:
    settings = await _get_tenant_settings(db, tenant_id)
    if not bool(settings.get("enable_reception_direct_booking")):
        raise HTTPException(
            status_code=403,
            detail="reception_direct_booking_disabled_for_tenant",
        )
    return settings


def _cookie_value_from_raw_cookie(raw_cookie: str, name: str) -> str:
    raw_cookie = str(raw_cookie or "")
    for part in raw_cookie.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip() == name:
            return v.strip()
    return ""


def _extract_auth_token_from_request(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    bearer = ""
    if auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()

    cookie_token = (request.cookies.get("sp_access_token") or "").strip()

    return (
        request.headers.get("X-Reception-Token")
        or request.query_params.get("token")
        or bearer
        or cookie_token
        or ""
    ).strip()


def _extract_auth_token_from_ws(ws: WebSocket) -> str:
    auth = (ws.headers.get("Authorization") or "").strip()
    bearer = ""
    if auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()

    cookie_token = _cookie_value_from_raw_cookie(
        ws.headers.get("cookie") or "",
        "sp_access_token",
    )

    return (
        ws.headers.get("X-Reception-Token")
        or ws.query_params.get("token")
        or bearer
        or cookie_token
        or ""
    ).strip()


def _tenant_hint_from_request(request: Request) -> Optional[str]:
    tenant = (
        request.query_params.get("tenant")
        or request.query_params.get("client")
        or request.headers.get("X-Tenant-Id")
        or ""
    ).strip()
    return tenant or None


def _tenant_hint_from_ws(ws: WebSocket) -> Optional[str]:
    tenant = (
        ws.query_params.get("tenant")
        or ws.query_params.get("client")
        or ws.headers.get("X-Tenant-Id")
        or ""
    ).strip()
    return tenant or None


async def _load_user_context_from_jwt_token(
    db: AsyncSession,
    token: str,
) -> Dict[str, Any]:
    claims = decode_access_token(token)

    try:
        user_id = int(claims.get("sub"))
    except Exception:
        raise HTTPException(status_code=401, detail="invalid_token")

    tenant_id = (claims.get("tenant_id") or "").strip() or None
    role_code = str(claims.get("role_code") or "").strip().upper()
    is_platform_admin = bool(claims.get("is_platform_admin"))

    user_res = await db.execute(
        text(
            """
            SELECT id, email, full_name, mobile, is_active, is_platform_admin
            FROM users
            WHERE id = :user_id
            LIMIT 1
            """
        ),
        {"user_id": user_id},
    )
    user = user_res.mappings().first()

    if not user:
        raise HTTPException(status_code=401, detail="user_not_found")

    if not bool(user["is_active"]):
        raise HTTPException(status_code=403, detail="user_inactive")

    permissions: List[str] = []

    if is_platform_admin:
        perm_res = await db.execute(
            text(
                """
                SELECT permission_code
                FROM permissions
                ORDER BY permission_code ASC
                """
            )
        )
        permissions = [str(r["permission_code"]) for r in perm_res.mappings().all()]
    else:
        if not tenant_id:
            raise HTTPException(status_code=403, detail="tenant_missing_in_token")

        perm_res = await db.execute(
            text(
                """
                SELECT DISTINCT p.permission_code
                FROM tenant_users tu
                JOIN roles r
                  ON r.id = tu.role_id
                JOIN role_permissions rp
                  ON rp.role_id = r.id
                JOIN permissions p
                  ON p.id = rp.permission_id
                WHERE tu.user_id = :user_id
                  AND tu.tenant_id = :tenant_id
                  AND tu.is_active = TRUE
                  AND r.role_code = :role_code
                ORDER BY p.permission_code ASC
                """
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role_code": role_code,
            },
        )
        permissions = [str(r["permission_code"]) for r in perm_res.mappings().all()]

    return {
        "user_id": user_id,
        "email": str(user["email"]),
        "full_name": str(user.get("full_name") or ""),
        "mobile": str(user.get("mobile") or ""),
        "tenant_id": tenant_id,
        "role_code": role_code,
        "is_platform_admin": is_platform_admin,
        "permissions": permissions,
    }


def _assert_reception_role(ctx: Dict[str, Any]) -> None:
    if bool(ctx.get("is_platform_admin")):
        return

    role_code = str(ctx.get("role_code") or "").strip().upper()
    allowed = {"RECEPTION", "CLINIC_ADMIN"}
    if role_code not in allowed:
        raise HTTPException(status_code=403, detail="role_forbidden")


async def _resolve_reception_access_by_request(
    request: Request,
    db: AsyncSession,
) -> str:
    tenant_hint = _tenant_hint_from_request(request)
    token = _extract_auth_token_from_request(request)
    fallback_tenant = os.getenv("WA_DEFAULT_CLIENT") or "default"

    # 1) Legacy reception token
    if token:
        try:
            return await resolve_and_validate_tenant_reception(
                db=db,
                tenant_id=tenant_hint or fallback_tenant,
                reception_token=token,
                fallback_tenant_id=fallback_tenant,
            )
        except HTTPException:
            pass

    # 2) JWT access
    if not token:
        raise HTTPException(status_code=401, detail="missing_access_token")

    ctx = await _load_user_context_from_jwt_token(db, token)
    _assert_reception_role(ctx)

    ctx_tenant = str(ctx.get("tenant_id") or "").strip() or None

    if bool(ctx.get("is_platform_admin")):
        return tenant_hint or ctx_tenant or fallback_tenant

    effective_tenant = tenant_hint or ctx_tenant
    if not effective_tenant:
        raise HTTPException(status_code=400, detail="tenant_required")

    if ctx_tenant and effective_tenant != ctx_tenant:
        raise HTTPException(status_code=403, detail="tenant_mismatch")

    return effective_tenant


async def _resolve_reception_access_by_ws(
    ws: WebSocket,
    db: AsyncSession,
) -> str:
    tenant_hint = _tenant_hint_from_ws(ws)
    token = _extract_auth_token_from_ws(ws)
    fallback_tenant = os.getenv("WA_DEFAULT_CLIENT") or "default"

    # 1) Legacy reception token
    if token:
        try:
            return await resolve_and_validate_tenant_reception(
                db=db,
                tenant_id=tenant_hint or fallback_tenant,
                reception_token=token,
                fallback_tenant_id=fallback_tenant,
            )
        except HTTPException:
            pass

    # 2) JWT access
    if not token:
        raise HTTPException(status_code=401, detail="missing_access_token")

    ctx = await _load_user_context_from_jwt_token(db, token)
    _assert_reception_role(ctx)

    ctx_tenant = str(ctx.get("tenant_id") or "").strip() or None

    if bool(ctx.get("is_platform_admin")):
        return tenant_hint or ctx_tenant or fallback_tenant

    effective_tenant = tenant_hint or ctx_tenant
    if not effective_tenant:
        raise HTTPException(status_code=400, detail="tenant_required")

    if ctx_tenant and effective_tenant != ctx_tenant:
        raise HTTPException(status_code=403, detail="tenant_mismatch")

    return effective_tenant


async def require_reception_access(request: Request, db: AsyncSession) -> str:
    return await _resolve_reception_access_by_request(request, db)


async def require_ws_reception_access(ws: WebSocket, db: AsyncSession) -> str:
    return await _resolve_reception_access_by_ws(ws, db)


async def _notify_reception_event(db: AsyncSession, event: Dict[str, Any]):
    try:
        payload = json.dumps(event)
        await db.execute(
            text("SELECT pg_notify(:ch,:payload)"),
            {"ch": "reception_events", "payload": payload},
        )
    except Exception:
        pass


@router.get("/reception", response_class=HTMLResponse)
async def reception_page(request: Request):
    async with AsyncSessionLocal() as db:
        await require_reception_access(request, db)

    return templates.TemplateResponse(
        "reception.html",
        {"request": request},
    )


@router.websocket("/ws/reception")
async def ws_reception(ws: WebSocket):
    async with AsyncSessionLocal() as db:
        try:
            await require_ws_reception_access(ws, db)
        except HTTPException:
            await ws.close(code=1008)
            return

    if reception_bus is None:
        await ws.accept()
        await ws.send_json({"type": "error", "message": "realtime_disabled"})
        await ws.close()
        return

    await reception_bus.connect(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await reception_bus.disconnect(ws)


@router.get("/api/reception/config")
async def reception_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)
    settings = await _get_tenant_settings(db, tenant_id)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "clinic_name": settings.get("clinic_name") or tenant_id,
        "language": settings.get("language") or "en",
        "ai_tone": settings.get("ai_tone") or "formal",
        "timezone": settings.get("timezone") or "Asia/Riyadh",
        "enable_ai_booking": bool(settings.get("enable_ai_booking", True)),
        "enable_reception_direct_booking": bool(
            settings.get("enable_reception_direct_booking", True)
        ),
    }


@router.get("/api/reception/doctors")
async def reception_doctors(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    res = await db.execute(
        text(
            """
            SELECT
                doctor_key,
                doctor_name,
                specialty_key,
                specialty_label,
                is_active
            FROM doctors
            WHERE tenant_id = :tenant_id
              AND is_active = TRUE
            ORDER BY doctor_name ASC, doctor_key ASC
            """
        ),
        {"tenant_id": tenant_id},
    )

    items = [dict(r) for r in res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": items,
    }


@router.get("/api/reception/patients/search")
async def reception_search_patients(
    request: Request,
    q: str = "",
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    q = (q or "").strip()
    if not q:
        return {"ok": True, "items": []}

    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    res = await db.execute(
        text(
            """
            SELECT
                patient_id,
                full_name,
                mobile,
                national_id,
                insurance_provider,
                insurance_plan,
                insurance_member_id,
                insurance_policy_number,
                insurance_expiry,
                insurance_status,
                insurance_verified,
                created_at,
                updated_at
            FROM patients
            WHERE tenant_id = :tenant_id
              AND (
                    COALESCE(patient_id, '') ILIKE '%' || :q || '%'
                    OR COALESCE(full_name, '') ILIKE '%' || :q || '%'
                    OR COALESCE(mobile, '') ILIKE '%' || :q || '%'
                    OR COALESCE(national_id, '') ILIKE '%' || :q || '%'
                  )
            ORDER BY updated_at DESC
            LIMIT :limit
            """
        ),
        {
            "tenant_id": tenant_id,
            "q": q,
            "limit": limit,
        },
    )

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [dict(r) for r in res.mappings().all()],
    }


@router.post("/api/reception/patients/upsert")
async def reception_upsert_patient(
    request: Request,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    full_name = str(payload.get("full_name") or payload.get("patient_name") or "").strip()
    mobile = str(payload.get("mobile") or payload.get("patient_mobile") or "").strip() or None
    national_id = str(payload.get("national_id") or payload.get("patient_id") or "").strip() or None
    patient_id = str(payload.get("registry_patient_id") or "").strip() or None

    if not full_name:
        raise HTTPException(status_code=400, detail="full_name required")

    insurance_data = _insurance_fields_from_payload(payload)

    out = await upsert_patient_registry(
        db,
        tenant_id=tenant_id,
        patient_id=patient_id,
        full_name=full_name,
        mobile=mobile,
        national_id=national_id,
        insurance_provider=insurance_data["insurance_provider"],
        insurance_plan=insurance_data["insurance_plan"],
        insurance_member_id=insurance_data["insurance_member_id"],
        insurance_policy_number=insurance_data["insurance_policy_number"],
        insurance_expiry=insurance_data["insurance_expiry"],
        insurance_status=insurance_data["insurance_status"],
        insurance_verified=insurance_data["insurance_verified"],
    )

    await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "created": bool(out.get("created")),
        "patient": out.get("patient"),
    }


@router.get("/api/reception/requests")
async def list_requests(
    request: Request,
    status: str = "PENDING",
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    res = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND (:status='ALL' OR status=:status)
            ORDER BY created_at DESC
            LIMIT 300
            """
        ),
        {"tenant_id": tenant_id, "status": status},
    )

    rows = res.mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/api/reception/requests/{request_id}")
async def get_request(
    request: Request,
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    res = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )

    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not Found")

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "item": dict(row),
    }


@router.post("/api/reception/requests/{request_id}/update")
async def update_request(
    request: Request,
    request_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    status = str(payload.get("status") or "").strip().upper()
    receptionist_note = str(payload.get("receptionist_note") or "").strip() or None
    send_message = bool(payload.get("send_message") or False)
    message_text = str(payload.get("message_text") or "").strip() or None

    allowed_statuses = {"PENDING", "APPROVED", "REJECTED", "CONTACTED"}
    if status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")

    insurance_data = _insurance_fields_from_payload(payload)

    allowed_insurance_statuses = {
        "SELF_PAY",
        "INSURED_UNVERIFIED",
        "INSURED_VERIFIED",
        "INSURANCE_EXPIRED",
        "APPROVAL_REQUIRED",
    }
    if insurance_data["insurance_status"] not in allowed_insurance_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"invalid insurance_status: {insurance_data['insurance_status']}",
        )

    req_res = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )
    req = req_res.mappings().first()

    if not req:
        raise HTTPException(status_code=404, detail="request_not_found")

    await db.execute(
        text(
            """
            UPDATE appointment_requests
            SET
                status = :status,
                receptionist_note = :receptionist_note,
                insurance_provider = :insurance_provider,
                insurance_plan = :insurance_plan,
                insurance_member_id = :insurance_member_id,
                insurance_policy_number = :insurance_policy_number,
                insurance_expiry = :insurance_expiry,
                insurance_status = :insurance_status,
                insurance_verified = :insurance_verified,
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
            "status": status,
            "receptionist_note": receptionist_note,
            **insurance_data,
        },
    )

    await _notify_reception_event(
        db,
        {
            "type": "appointment_request_updated",
            "tenant_id": tenant_id,
            "request_id": request_id,
            "status": status,
        },
    )

    actor_user_id = None
    actor_email = None
    role_code = None

    try:
        ctx = await get_current_user_context(request, db)
        actor_user_id = ctx.get("user_id")
        actor_email = ctx.get("email")
        role_code = ctx.get("role_code")
    except Exception:
        pass

    await log_audit_event(
        db,
        tenant_id=tenant_id,
        action="insurance_updated",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        role_code=role_code,
        entity_type="appointment_request",
        entity_id=request_id,
        metadata={
            "status": status,
            "insurance_provider": insurance_data.get("insurance_provider"),
            "insurance_plan": insurance_data.get("insurance_plan"),
            "insurance_member_id": insurance_data.get("insurance_member_id"),
            "insurance_status": insurance_data.get("insurance_status"),
            "insurance_verified": insurance_data.get("insurance_verified"),
        },
    )

    await db.commit()

    whatsapp_sent = False
    whatsapp_error = None

    patient_mobile = str(req.get("patient_mobile") or "").strip()

    if send_message and patient_mobile and wa_send_text is not None:
        try:
            if message_text:
                wa_send_text(patient_mobile, message_text)
                whatsapp_sent = True
            elif build_reception_message is not None:
                settings = await _get_tenant_settings(db, tenant_id)

                clinic_name = str(settings.get("clinic_name") or tenant_id).strip()
                language = str(settings.get("language") or "en").strip().lower()
                tone = str(settings.get("ai_tone") or "formal").strip().lower()

                if language not in {"en", "ar"}:
                    language = "en"

                built = build_reception_message(
                    status=status,
                    patient_name=str(req.get("patient_name") or "").strip(),
                    doctor=str(req.get("doctor_label") or req.get("doctor_key") or "").strip(),
                    date=str(req.get("appt_date") or "").strip(),
                    time=_hhmm(req.get("appt_time")),
                    language=language,
                    tone=tone,
                    clinic_name=clinic_name,
                )

                if built:
                    wa_send_text(patient_mobile, built)
                    whatsapp_sent = True

        except Exception as e:
            whatsapp_error = str(e)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "status": status,
        "whatsapp_sent": whatsapp_sent,
        "whatsapp_error": whatsapp_error,
    }


@router.get("/api/reception/available-slots")
async def reception_available_slots(
    request: Request,
    doctor_key: str = "",
    appt_date: str = "",
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)
    await _require_direct_booking_enabled(db, tenant_id)

    doctor_key = (doctor_key or "").strip()
    appt_date = (appt_date or "").strip()

    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")
    if not appt_date:
        raise HTTPException(status_code=400, detail="appt_date required")

    slot_date = _parse_optional_date(appt_date)
    if not slot_date:
        raise HTTPException(status_code=400, detail="invalid_date")

    res = await db.execute(
        text(
            """
            SELECT slot_time
            FROM appointment_slots
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND status = 'OPEN'
            ORDER BY slot_time ASC
            """
        ),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": slot_date,
        },
    )

    items = [str(r["slot_time"])[:5] for r in res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "appt_date": appt_date,
        "items": items,
    }


@router.post("/api/reception/requests/{request_id}/insurance-pending")
async def mark_request_insurance_pending(
    request: Request,
    request_id: str,
    payload: Dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)
    payload = payload or {}

    insurance_status = str(
        payload.get("insurance_status") or "APPROVAL_REQUIRED"
    ).strip().upper()

    allowed_statuses = {
        "INSURED_UNVERIFIED",
        "APPROVAL_REQUIRED",
        "INSURANCE_EXPIRED",
    }
    if insurance_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"invalid insurance_status: {insurance_status}",
        )

    res = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )

    req = res.mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="request_not_found")

    insurance_provider = str(payload.get("insurance_provider") or req.get("insurance_provider") or "").strip() or None
    insurance_plan = str(payload.get("insurance_plan") or req.get("insurance_plan") or "").strip() or None
    insurance_member_id = str(payload.get("insurance_member_id") or req.get("insurance_member_id") or "").strip() or None
    insurance_policy_number = str(payload.get("insurance_policy_number") or req.get("insurance_policy_number") or "").strip() or None
    insurance_expiry = (
        _parse_optional_date(payload.get("insurance_expiry"))
        if payload.get("insurance_expiry")
        else req.get("insurance_expiry")
    )
    receptionist_note = str(payload.get("receptionist_note") or req.get("receptionist_note") or "").strip() or None

    await db.execute(
        text(
            """
            UPDATE appointment_requests
            SET
                status = 'CONTACTED',
                insurance_provider = :insurance_provider,
                insurance_plan = :insurance_plan,
                insurance_member_id = :insurance_member_id,
                insurance_policy_number = :insurance_policy_number,
                insurance_expiry = :insurance_expiry,
                insurance_status = :insurance_status,
                insurance_verified = FALSE,
                receptionist_note = :receptionist_note,
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
            "insurance_provider": insurance_provider,
            "insurance_plan": insurance_plan,
            "insurance_member_id": insurance_member_id,
            "insurance_policy_number": insurance_policy_number,
            "insurance_expiry": insurance_expiry,
            "insurance_status": insurance_status,
            "receptionist_note": receptionist_note,
        },
    )

    await _notify_reception_event(
        db,
        {
            "type": "appointment_request_updated",
            "tenant_id": tenant_id,
            "request_id": request_id,
            "status": "CONTACTED",
        },
    )

    await db.commit()

    whatsapp_sent = False
    whatsapp_error = None

    patient_mobile = str(req.get("patient_mobile") or "").strip()
    patient_name = str(req.get("patient_name") or "").strip()
    doctor_label = str(req.get("doctor_label") or req.get("doctor_key") or "").strip()
    appt_date = str(req.get("appt_date") or "").strip()
    appt_time = str(req.get("appt_time") or "").strip()[:5]

    if patient_mobile and wa_send_text is not None and build_insurance_pending_message is not None:
        try:
            tenant_settings = await _get_tenant_settings(db, tenant_id)

            clinic_name = str(
                tenant_settings.get("clinic_name")
                or req.get("dept_label")
                or tenant_id
            ).strip()

            language = str(tenant_settings.get("language") or "en").strip().lower()
            if language not in {"en", "ar"}:
                language = "en"

            message_text = await build_insurance_pending_message(
                db,
                tenant_id=tenant_id,
                language=language,
                clinic_name=clinic_name,
                patient_name=patient_name,
                doctor_name=doctor_label,
                date=appt_date,
                time=appt_time,
            )

            if message_text:
                wa_send_text(patient_mobile, message_text)
                whatsapp_sent = True

        except Exception as e:
            whatsapp_error = str(e)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "insurance_status": insurance_status,
        "whatsapp_sent": whatsapp_sent,
        "whatsapp_error": whatsapp_error,
    }

@router.post("/api/reception/book-direct")
async def reception_book_direct(
    request: Request,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)
    tenant_settings = await _require_direct_booking_enabled(db, tenant_id)

    doctor_key = str(payload.get("doctor_key") or "").strip()
    appt_date = str(payload.get("appt_date") or "").strip()
    appt_time = _hhmm(payload.get("appt_time"))
    patient_name = str(payload.get("patient_name") or "").strip()
    patient_mobile = str(payload.get("patient_mobile") or "").strip()
    patient_id_input = str(payload.get("patient_id") or "").strip() or None
    notes = str(payload.get("notes") or "").strip() or None

    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")
    if not appt_date:
        raise HTTPException(status_code=400, detail="appt_date required")
    if not appt_time:
        raise HTTPException(status_code=400, detail="appt_time required")
    if not patient_name:
        raise HTTPException(status_code=400, detail="patient_name required")
    if not patient_mobile:
        raise HTTPException(status_code=400, detail="patient_mobile required")

    slot_date = _parse_optional_date(appt_date)
    if not slot_date:
        raise HTTPException(status_code=400, detail="invalid_date")

    insurance_data = _insurance_fields_from_payload(payload)

    allowed_insurance_statuses = {
        "SELF_PAY",
        "INSURED_UNVERIFIED",
        "INSURED_VERIFIED",
        "INSURANCE_EXPIRED",
        "APPROVAL_REQUIRED",
    }
    if insurance_data["insurance_status"] not in allowed_insurance_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"invalid insurance_status: {insurance_data['insurance_status']}",
        )

    await enforce_appointment_limit(db, tenant_id)

    slot_res = await db.execute(
        text(
            """
            SELECT status
            FROM appointment_slots
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": slot_date,
            "slot_time": appt_time,
        },
    )
    slot = slot_res.mappings().first()

    if not slot:
        raise HTTPException(status_code=404, detail="slot_not_found")

    if str(slot.get("status") or "").upper() != "OPEN":
        raise HTTPException(status_code=400, detail="slot_not_available")

    patient_out = await upsert_patient_registry(
        db,
        tenant_id=tenant_id,
        patient_id=None,
        full_name=patient_name,
        mobile=patient_mobile,
        national_id=patient_id_input,
        insurance_provider=insurance_data["insurance_provider"],
        insurance_plan=insurance_data["insurance_plan"],
        insurance_member_id=insurance_data["insurance_member_id"],
        insurance_policy_number=insurance_data["insurance_policy_number"],
        insurance_expiry=insurance_data["insurance_expiry"],
        insurance_status=insurance_data["insurance_status"],
        insurance_verified=insurance_data["insurance_verified"],
    )
    registry_patient = patient_out.get("patient") or {}
    registry_patient_id = str(registry_patient.get("patient_id") or patient_id_input or "").strip() or None

    appointment_id = _make_direct_appointment_id()

    await db.execute(
        text(
            """
            INSERT INTO appointments
            (
                tenant_id,
                appointment_id,
                doctor_key,
                appt_date,
                appt_time,
                patient_name,
                patient_mobile,
                patient_id,
                status,
                notes,
                booking_source,
                insurance_provider,
                insurance_plan,
                insurance_member_id,
                insurance_policy_number,
                insurance_expiry,
                insurance_status,
                insurance_verified,
                created_at,
                updated_at
            )
            VALUES
            (
                :tenant_id,
                :appointment_id,
                :doctor_key,
                :appt_date,
                :appt_time,
                :patient_name,
                :patient_mobile,
                :patient_id,
                'CONFIRMED',
                :notes,
                'RECEPTION_DESK',
                :insurance_provider,
                :insurance_plan,
                :insurance_member_id,
                :insurance_policy_number,
                :insurance_expiry,
                :insurance_status,
                :insurance_verified,
                NOW(),
                NOW()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "appointment_id": appointment_id,
            "doctor_key": doctor_key,
            "appt_date": appt_date,
            "appt_time": appt_time,
            "patient_name": patient_name,
            "patient_mobile": patient_mobile,
            "patient_id": registry_patient_id,
            "notes": notes,
            **insurance_data,
        },
    )

    await db.execute(
        text(
            """
            UPDATE appointment_slots
            SET status = 'BOOKED',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
            """
        ),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": slot_date,
            "slot_time": appt_time,
        },
    )

    await _notify_reception_event(
        db,
        {
            "type": "direct_booking_created",
            "tenant_id": tenant_id,
            "appointment_id": appointment_id,
            "patient_id": registry_patient_id,
            "doctor_key": doctor_key,
            "appt_date": appt_date,
            "appt_time": appt_time,
            "booking_source": "RECEPTION_DESK",
        },
    )

    actor_user_id = None
    actor_email = None
    role_code = None

    try:
        ctx = await get_current_user_context(request, db)
        actor_user_id = ctx.get("user_id")
        actor_email = ctx.get("email")
        role_code = ctx.get("role_code")
    except Exception:
        pass

    await log_audit_event(
        db,
        tenant_id=tenant_id,
        action="appointment_confirmed",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        role_code=role_code,
        entity_type="appointment",
        entity_id=appointment_id,
        metadata={
            "doctor_key": doctor_key,
            "appt_date": appt_date,
            "appt_time": appt_time,
        },
    )

    await db.commit()

    whatsapp_sent = False
    whatsapp_error = None

    if patient_mobile and wa_send_text is not None and build_appointment_confirmed_message is not None:
        try:
            clinic_name = str(tenant_settings.get("clinic_name") or tenant_id).strip()
            language = str(tenant_settings.get("language") or "en").strip().lower()
            if language not in {"en", "ar"}:
                language = "en"

            doctor_res = await db.execute(
                text(
                    """
                    SELECT doctor_name
                    FROM doctors
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "doctor_key": doctor_key,
                },
            )
            doctor_row = doctor_res.mappings().first()
            doctor_name = str(doctor_row.get("doctor_name") or doctor_key) if doctor_row else doctor_key

            message_text = await build_appointment_confirmed_message(
                db,
                tenant_id=tenant_id,
                language=language,
                clinic_name=clinic_name,
                patient_name=patient_name,
                doctor_name=doctor_name,
                date=appt_date,
                time=appt_time,
            )

            if message_text:
                wa_send_text(patient_mobile, message_text)
                whatsapp_sent = True

        except Exception as e:
            whatsapp_error = str(e)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "appointment_id": appointment_id,
        "patient_id": registry_patient_id,
        "booking_source": "RECEPTION_DESK",
        "patient_created": bool(patient_out.get("created")),
        "whatsapp_sent": whatsapp_sent,
        "whatsapp_error": whatsapp_error,
    }
@router.post("/admin/appointments/confirm")
async def confirm_appointment(
    request: Request,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    tenant_id = await require_reception_access(request, db)

    request_id = payload.get("request_id")
    notes = payload.get("notes") or ""

    insurance_expiry = _parse_optional_date(payload.get("insurance_expiry"))

    res = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id=:tenant_id
              AND request_id=:request_id
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "request_id": request_id},
    )

    req = res.mappings().first()
    if not req:
        raise HTTPException(404, "request_not_found")

    doctor_key = req["doctor_key"]
    appt_date = req["appt_date"]
    appt_time = _hhmm(req["appt_time"])

    slot_date = _parse_optional_date(appt_date)
    if not slot_date:
        raise HTTPException(400, "invalid_date")

    await enforce_appointment_limit(db, tenant_id)

    slot_res = await db.execute(
        text(
            """
            SELECT status
            FROM appointment_slots
            WHERE tenant_id=:tenant_id
              AND doctor_key=:doctor_key
              AND slot_date=:slot_date
              AND slot_time=:slot_time
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": slot_date,
            "slot_time": appt_time,
        },
    )

    slot = slot_res.mappings().first()
    if not slot:
        raise HTTPException(404, "slot_not_found")

    if str(slot["status"]).upper() != "OPEN":
        raise HTTPException(400, "slot_not_available")

    insurance_data = _insurance_fields_from_request_row(req)

    if insurance_expiry is not None:
        insurance_data["insurance_expiry"] = insurance_expiry

    if payload.get("insurance_provider") is not None:
        insurance_data["insurance_provider"] = str(payload.get("insurance_provider") or "").strip() or None

    if payload.get("insurance_plan") is not None:
        insurance_data["insurance_plan"] = str(payload.get("insurance_plan") or "").strip() or None

    if payload.get("insurance_member_id") is not None:
        insurance_data["insurance_member_id"] = str(payload.get("insurance_member_id") or "").strip() or None

    if payload.get("insurance_policy_number") is not None:
        insurance_data["insurance_policy_number"] = str(payload.get("insurance_policy_number") or "").strip() or None

    if payload.get("insurance_status") is not None:
        insurance_data["insurance_status"] = str(payload.get("insurance_status") or "SELF_PAY").strip().upper() or "SELF_PAY"

    if payload.get("insurance_verified") is not None:
        insurance_data["insurance_verified"] = bool(payload.get("insurance_verified"))

    patient_out = await upsert_patient_registry(
        db,
        tenant_id=tenant_id,
        full_name=str(req.get("patient_name") or "").strip() or "Unknown Patient",
        mobile=str(req.get("patient_mobile") or "").strip() or None,
        national_id=str(req.get("patient_id") or "").strip() or None,
        insurance_provider=insurance_data["insurance_provider"],
        insurance_plan=insurance_data["insurance_plan"],
        insurance_member_id=insurance_data["insurance_member_id"],
        insurance_policy_number=insurance_data["insurance_policy_number"],
        insurance_expiry=insurance_data["insurance_expiry"],
        insurance_status=str(insurance_data["insurance_status"] or "SELF_PAY"),
        insurance_verified=bool(insurance_data["insurance_verified"]),
    )
    registry_patient = patient_out.get("patient") or {}
    registry_patient_id = registry_patient.get("patient_id")

    appointment_id = f"APT-MANUAL-{request_id}"

    await db.execute(
        text(
            """
            INSERT INTO appointments
            (
                tenant_id,
                appointment_id,
                doctor_key,
                appt_date,
                appt_time,
                patient_name,
                patient_mobile,
                patient_id,
                status,
                request_id,
                notes,
                booking_source,
                insurance_provider,
                insurance_plan,
                insurance_member_id,
                insurance_policy_number,
                insurance_expiry,
                insurance_status,
                insurance_verified
            )
            VALUES
            (
                :tenant_id,
                :appointment_id,
                :doctor_key,
                :appt_date,
                :appt_time,
                :patient_name,
                :patient_mobile,
                :patient_id,
                'CONFIRMED',
                :request_id,
                :notes,
                'WHATSAPP_AI',
                :insurance_provider,
                :insurance_plan,
                :insurance_member_id,
                :insurance_policy_number,
                :insurance_expiry,
                :insurance_status,
                :insurance_verified
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "appointment_id": appointment_id,
            "doctor_key": doctor_key,
            "appt_date": appt_date,
            "appt_time": appt_time,
            "patient_name": req["patient_name"],
            "patient_mobile": req["patient_mobile"],
            "patient_id": registry_patient_id,
            "request_id": request_id,
            "notes": notes,
            **insurance_data,
        },
    )

    await db.execute(
        text(
            """
            UPDATE appointment_slots
            SET status='BOOKED',
                updated_at = NOW()
            WHERE tenant_id=:tenant_id
              AND doctor_key=:doctor_key
              AND slot_date=:slot_date
              AND slot_time=:slot_time
            """
        ),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": slot_date,
            "slot_time": appt_time,
        },
    )

    await db.execute(
        text(
            """
            UPDATE appointment_requests
            SET status='APPROVED',
                updated_at = NOW()
            WHERE tenant_id=:tenant_id
              AND request_id=:request_id
            """
        ),
        {"tenant_id": tenant_id, "request_id": request_id},
    )

    await _notify_reception_event(
        db,
        {
            "type": "appointment_confirmed",
            "tenant_id": tenant_id,
            "request_id": request_id,
            "appointment_id": appointment_id,
            "patient_id": registry_patient_id,
            "booking_source": "WHATSAPP_AI",
        },
    )

    await _notify_reception_event(
        db,
        {
            "type": "appointment_confirmed",
            "tenant_id": tenant_id,
            "request_id": request_id,
            "appointment_id": appointment_id,
            "patient_id": registry_patient_id,
            "booking_source": "WHATSAPP_AI",
        },
    )

    actor_user_id = None
    actor_email = None
    role_code = None

    try:
        ctx = await get_current_user_context(request, db)
        actor_user_id = ctx.get("user_id")
        actor_email = ctx.get("email")
        role_code = ctx.get("role_code")
    except Exception:
        pass

    await log_audit_event(
        db,
        tenant_id=tenant_id,
        action="appointment_confirmed",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        role_code=role_code,
        entity_type="appointment",
        entity_id=appointment_id,
        metadata={
            "request_id": request_id,
            "patient_id": registry_patient_id,
            "doctor_key": doctor_key,
            "appt_date": appt_date,
            "appt_time": appt_time,
            "booking_source": "WHATSAPP_AI",
        },
    )

    await db.commit()

    await db.commit()

    sent_whatsapp = False
    whatsapp_error = None

    patient_mobile = str(req.get("patient_mobile") or "").strip()
    patient_name = str(req.get("patient_name") or "").strip()
    doctor_label = str(req.get("doctor_label") or req.get("doctor_key") or "").strip()

    if patient_mobile and wa_send_text is not None and build_appointment_confirmed_message is not None:
        try:
            tenant_settings = await _get_tenant_settings(db, tenant_id)

            clinic_name = str(
                tenant_settings.get("clinic_name")
                or req.get("dept_label")
                or tenant_id
            ).strip()

            language = str(tenant_settings.get("language") or "en").strip().lower()
            if language not in {"en", "ar"}:
                language = "en"

            message_text = await build_appointment_confirmed_message(
                db,
                tenant_id=tenant_id,
                language=language,
                clinic_name=clinic_name,
                patient_name=patient_name,
                doctor_name=doctor_label,
                date=str(appt_date or ""),
                time=str(appt_time or "")[:5],
            )

            if message_text:
                wa_send_text(patient_mobile, message_text)
                sent_whatsapp = True

        except Exception as e:
            whatsapp_error = str(e)

    return {
        "ok": True,
        "appointment_id": appointment_id,
        "patient_id": registry_patient_id,
        "booking_source": "WHATSAPP_AI",
        "whatsapp_sent": sent_whatsapp,
        "whatsapp_error": whatsapp_error,
    }