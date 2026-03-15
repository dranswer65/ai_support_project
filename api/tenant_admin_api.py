from __future__ import annotations

import os
import csv
import io
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from fastapi import Request
from core.api_auth import require_platform_api_access
from core.audit_logger import log_audit_event
from core.auth_rbac import get_current_user_context
from database import AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from fastapi import Request

from core.tenant_schema import (
    ensure_tenant_tables,
    ensure_billing_tables,
    create_tenant_with_tokens,
)

from core.patient_schema import (
    ensure_patient_tables,
    upsert_patient_registry,
)

from core.slot_generator import generate_slots, DEFAULT_TZ as GEN_DEFAULT_TZ


try:
    from services.messaging.whatsapp_sender import wa_send_text
except Exception:
    wa_send_text = None


try:
    from services.messaging.message_templates import (
        build_reception_message,
        build_appointment_cancelled_message,
    )
except Exception:
    build_reception_message = None
    build_appointment_cancelled_message = None


router = APIRouter()
print("[tenant_admin_api] loaded from:", __file__)
ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()


def require_platform_admin(x_admin_token: str):
    expected = ADMIN_TOKEN
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")

    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _get_tenant_message_context(db, tenant_id: str) -> Dict[str, Any]:

    res = await db.execute(
        text(
            """
            SELECT
                COALESCE(ts.clinic_name, t.clinic_name, t.tenant_id) AS clinic_name,
                COALESCE(ts.language, t.default_language, 'en') AS language
            FROM tenants t
            LEFT JOIN tenant_settings ts
              ON ts.tenant_id = t.tenant_id
            WHERE t.tenant_id = :tenant_id
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )

    row = res.mappings().first()

    return dict(row) if row else {
        "clinic_name": tenant_id,
        "language": "en",
    }


async def get_tenant_admin_token(tenant_id: str) -> Optional[str]:

    tenant_id = (tenant_id or "").strip()

    if not tenant_id:
        return None

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT admin_token
                FROM tenant_tokens
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        row = res.mappings().first()

        return str(row["admin_token"]) if row else None


async def get_tenant_reception_token(tenant_id: str) -> Optional[str]:

    tenant_id = (tenant_id or "").strip()

    if not tenant_id:
        return None

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT reception_token
                FROM tenant_tokens
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        row = res.mappings().first()

        return str(row["reception_token"]) if row else None

@router.get("/admin/test-wa-direct")
async def admin_test_wa_direct(
    request: Request,
    to: str,
):
    from services.messaging.whatsapp_sender import wa_send_text

    try:
        out = wa_send_text(to, "Direct sender test from SupportPilot")
        return {"ok": True, "out": out}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"whatsapp_send_failed: {str(e)}")

# --------------------------------------------------
# TENANT MANAGEMENT
# --------------------------------------------------


@router.post("/admin/tenants/create")
async def admin_create_tenant(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        out = await create_tenant_with_tokens(
            db,
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            clinic_name=str(payload.get("clinic_name") or "").strip(),
            default_language=str(payload.get("default_language") or "en").strip(),
            default_tone=str(payload.get("default_tone") or "formal").strip(),
            timezone=str(payload.get("timezone") or "Asia/Riyadh").strip(),
            country_code=str(payload.get("country_code") or "").strip() or None,
            whatsapp_phone_id=str(payload.get("whatsapp_phone_id") or "").strip() or None,
            whatsapp_verify_token=str(payload.get("whatsapp_verify_token") or "").strip() or None,
        )

    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)

    return out


@router.get("/admin/tenants/list")
async def admin_list_tenants(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    tenant_id,
                    clinic_name,
                    status,
                    default_language,
                    default_tone,
                    timezone,
                    country_code,
                    created_at,
                    updated_at
                FROM tenants
                ORDER BY created_at DESC
                """
            )
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/tenants/{tenant_id}/activate")
async def admin_activate_tenant(
    tenant_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        await db.execute(
            text(
                """
                UPDATE tenants
                SET status='ACTIVE',
                    updated_at=NOW()
                WHERE tenant_id=:tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )

        await db.commit()

    return {"ok": True, "tenant_id": tenant_id}


@router.post("/admin/tenants/{tenant_id}/deactivate")
async def admin_deactivate_tenant(
    tenant_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        await db.execute(
            text(
                """
                UPDATE tenants
                SET status='INACTIVE',
                    updated_at=NOW()
                WHERE tenant_id=:tenant_id
                """
            ),
            {"tenant_id": tenant_id},
        )

        await db.commit()

    return {"ok": True, "tenant_id": tenant_id}

@router.get("/admin/tenants/{tenant_id}")
async def admin_get_tenant(
    tenant_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    t.tenant_id,
                    t.clinic_name,
                    t.status,
                    t.default_language,
                    t.default_tone,
                    t.timezone,
                    t.country_code,
                    t.whatsapp_phone_id,
                    t.whatsapp_verify_token,
                    t.created_at,
                    t.updated_at,
                    tt.admin_token,
                    tt.reception_token,
                    tt.public_api_key
                FROM tenants t
                LEFT JOIN tenant_tokens tt
                  ON tt.tenant_id = t.tenant_id
                WHERE t.tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        row = res.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="tenant_not_found")

    return {"ok": True, "item": dict(row)}

@router.get("/admin/tenant-settings")
async def admin_get_tenant_settings(
    request: Request,
    tenant_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):

    # ---- ACCESS CONTROL ----
    if ADMIN_TOKEN and (x_admin_token or "").strip() == ADMIN_TOKEN:
        pass
    else:
        async with AsyncSessionLocal() as db:
            ctx = await get_current_user_context(request, db)

        if not ctx or not bool(ctx.get("is_platform_admin")):
            raise HTTPException(status_code=403, detail="Forbidden")

    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    ts.tenant_id,
                    ts.clinic_name,
                    ts.logo_url,
                    ts.brand_color,
                    ts.timezone,
                    ts.language,
                    ts.whatsapp_greeting,
                    ts.ai_tone,
                    ts.reminder_hours_before,
                    ts.enable_ai_booking,
                    ts.enable_reception_direct_booking,
                    ts.created_at,
                    ts.updated_at
                FROM tenant_settings ts
                WHERE ts.tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        row = res.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="tenant_settings_not_found")

    item = dict(row)
    item["enable_ai_booking"] = bool(item.get("enable_ai_booking", True))
    item["enable_reception_direct_booking"] = bool(
        item.get("enable_reception_direct_booking", False)
    )

    return {"ok": True, "item": item}


@router.post("/admin/tenant-settings/upsert")
async def admin_upsert_tenant_settings(
    request: Request,
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):

    # ---- ACCESS CONTROL ----
    if ADMIN_TOKEN and (x_admin_token or "").strip() == ADMIN_TOKEN:
        pass
    else:
        async with AsyncSessionLocal() as db:
            ctx = await get_current_user_context(request, db)

        if not ctx or not bool(ctx.get("is_platform_admin")):
            raise HTTPException(status_code=403, detail="Forbidden")

    tenant_id = str(payload.get("tenant_id") or "").strip()
    clinic_name = str(payload.get("clinic_name") or "").strip()
    logo_url = str(payload.get("logo_url") or "").strip() or None
    brand_color = str(payload.get("brand_color") or "").strip() or None
    timezone = str(payload.get("timezone") or "Asia/Riyadh").strip()
    language = str(payload.get("language") or "en").strip().lower()
    whatsapp_greeting = str(payload.get("whatsapp_greeting") or "").strip() or None
    ai_tone = str(payload.get("ai_tone") or "formal").strip().lower()
    reminder_hours_before = int(payload.get("reminder_hours_before") or 24)
    enable_ai_booking = bool(payload.get("enable_ai_booking", True))
    enable_reception_direct_booking = bool(
        payload.get("enable_reception_direct_booking", False)
    )

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    if not clinic_name:
        raise HTTPException(status_code=400, detail="clinic_name required")

    if language not in {"en", "ar"}:
        raise HTTPException(status_code=400, detail="invalid language")

    if ai_tone not in {"formal", "friendly"}:
        raise HTTPException(status_code=400, detail="invalid ai_tone")

    if reminder_hours_before < 1 or reminder_hours_before > 168:
        raise HTTPException(
            status_code=400,
            detail="reminder_hours_before must be between 1 and 168",
        )

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        if not chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        await db.execute(
            text(
                """
                INSERT INTO tenant_settings (
                    tenant_id,
                    clinic_name,
                    logo_url,
                    brand_color,
                    timezone,
                    language,
                    whatsapp_greeting,
                    ai_tone,
                    reminder_hours_before,
                    enable_ai_booking,
                    enable_reception_direct_booking,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :clinic_name,
                    :logo_url,
                    :brand_color,
                    :timezone,
                    :language,
                    :whatsapp_greeting,
                    :ai_tone,
                    :reminder_hours_before,
                    :enable_ai_booking,
                    :enable_reception_direct_booking,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tenant_id)
                DO UPDATE SET
                    clinic_name = EXCLUDED.clinic_name,
                    logo_url = EXCLUDED.logo_url,
                    brand_color = EXCLUDED.brand_color,
                    timezone = EXCLUDED.timezone,
                    language = EXCLUDED.language,
                    whatsapp_greeting = EXCLUDED.whatsapp_greeting,
                    ai_tone = EXCLUDED.ai_tone,
                    reminder_hours_before = EXCLUDED.reminder_hours_before,
                    enable_ai_booking = EXCLUDED.enable_ai_booking,
                    enable_reception_direct_booking = EXCLUDED.enable_reception_direct_booking,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "clinic_name": clinic_name,
                "logo_url": logo_url,
                "brand_color": brand_color,
                "timezone": timezone,
                "language": language,
                "whatsapp_greeting": whatsapp_greeting,
                "ai_tone": ai_tone,
                "reminder_hours_before": reminder_hours_before,
                "enable_ai_booking": enable_ai_booking,
                "enable_reception_direct_booking": enable_reception_direct_booking,
            },
        )

        await db.execute(
            text(
                """
                UPDATE tenants
                SET
                    clinic_name = :clinic_name,
                    timezone = :timezone,
                    default_language = :language,
                    default_tone = :ai_tone,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "clinic_name": clinic_name,
                "timezone": timezone,
                "language": language,
                "ai_tone": ai_tone,
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
            action="tenant_settings_changed",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="tenant_settings",
            entity_id=tenant_id,
            metadata={
                "clinic_name": clinic_name,
                "timezone": timezone,
                "language": language,
                "ai_tone": ai_tone,
                "reminder_hours_before": reminder_hours_before,
                "enable_ai_booking": enable_ai_booking,
                "enable_reception_direct_booking": enable_reception_direct_booking,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "clinic_name": clinic_name,
        "timezone": timezone,
        "language": language,
        "ai_tone": ai_tone,
        "reminder_hours_before": reminder_hours_before,
        "enable_ai_booking": enable_ai_booking,
        "enable_reception_direct_booking": enable_reception_direct_booking,
    }


@router.get("/admin/message-templates")
async def admin_get_message_templates(
    tenant_id: str,
    template_key: str = "",
    language: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    template_key = (template_key or "").strip().lower()
    language = (language or "").strip().lower()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        tenant_chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not tenant_chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    tenant_id,
                    template_key,
                    language,
                    template_text,
                    is_active,
                    created_at,
                    updated_at
                FROM message_templates
                WHERE tenant_id = :tenant_id
                  AND (:template_key = '' OR template_key = :template_key)
                  AND (:language = '' OR language = :language)
                ORDER BY template_key ASC, language ASC, id ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "template_key": template_key,
                "language": language,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/message-templates/upsert")
async def admin_upsert_message_template(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    template_key = str(payload.get("template_key") or "").strip().lower()
    language = str(payload.get("language") or "").strip().lower()
    template_text = str(payload.get("template_text") or "").strip()
    is_active = bool(payload.get("is_active", True))

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not template_key:
        raise HTTPException(status_code=400, detail="template_key required")
    if language not in {"en", "ar"}:
        raise HTTPException(status_code=400, detail="invalid language")
    if not template_text:
        raise HTTPException(status_code=400, detail="template_text required")

    allowed_keys = {
        "appointment_confirmed",
        "appointment_reminder",
        "appointment_cancelled",
        "insurance_pending",
    }
    if template_key not in allowed_keys:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported template_key: {template_key}",
        )

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        tenant_chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not tenant_chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        await db.execute(
            text(
                """
                INSERT INTO message_templates (
                    tenant_id,
                    template_key,
                    language,
                    template_text,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :template_key,
                    :language,
                    :template_text,
                    :is_active,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tenant_id, template_key, language)
                DO UPDATE SET
                    template_text = EXCLUDED.template_text,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "template_key": template_key,
                "language": language,
                "template_text": template_text,
                "is_active": is_active,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "template_key": template_key,
        "language": language,
        "is_active": is_active,
    }


@router.post("/admin/message-templates/seed-defaults")
async def admin_seed_default_templates(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:

        chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )

        if not chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        await db.execute(
            text(
                """
                INSERT INTO message_templates
                    (tenant_id, template_key, language, template_text, is_active, created_at, updated_at)
                VALUES
                    (:tenant,'appointment_confirmed','en',
                    'Hello {patient_name}\n\nYour appointment at {clinic_name} is confirmed.\nDoctor: {doctor_name}\nDate: {date}\nTime: {time}\n\nPlease arrive 15 minutes early.',
                    TRUE,NOW(),NOW()),

                    (:tenant,'appointment_confirmed','ar',
                    'مرحبًا {patient_name}\n\nتم تأكيد موعدكم في {clinic_name}.\nالطبيب: {doctor_name}\nالتاريخ: {date}\nالوقت: {time}\n\nيرجى الحضور قبل الموعد بـ 15 دقيقة.',
                    TRUE,NOW(),NOW()),

                    (:tenant,'appointment_reminder','en',
                    'Hello {patient_name}\n\nReminder from {clinic_name}.\nDoctor: {doctor_name}\nDate: {date}\nTime: {time}\n\nReply if you need assistance.',
                    TRUE,NOW(),NOW()),

                    (:tenant,'appointment_reminder','ar',
                    'مرحبًا {patient_name}\n\nتذكير من {clinic_name}.\nالطبيب: {doctor_name}\nالتاريخ: {date}\nالوقت: {time}\n\nيرجى الرد إذا احتجتم المساعدة.',
                    TRUE,NOW(),NOW())
                ON CONFLICT (tenant_id, template_key, language)
                DO NOTHING
                """
            ),
            {"tenant": tenant_id},
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "message": "default templates seeded",
    }


@router.get("/admin/analytics/overview")
async def analytics_overview(
    request: Request,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        await require_platform_api_access(request, db, x_admin_token)

        await ensure_tenant_tables(db)
        await ensure_patient_tables(db)

        res = await db.execute(
            text(
                """
                WITH doctor_counts AS (
                    SELECT tenant_id, COUNT(*)::int AS doctors_count
                    FROM doctors
                    GROUP BY tenant_id
                ),
                schedule_rule_counts AS (
                    SELECT tenant_id, COUNT(*)::int AS schedule_rules_count
                    FROM doctor_schedule_rules
                    GROUP BY tenant_id
                ),
                slot_counts AS (
                    SELECT tenant_id, COUNT(*)::int AS slots_count
                    FROM appointment_slots
                    GROUP BY tenant_id
                ),
                request_stats AS (
                    SELECT
                        tenant_id,
                        COUNT(*)::int AS requests_count,
                        COUNT(*) FILTER (WHERE status = 'PENDING')::int AS pending_requests,
                        COUNT(*) FILTER (WHERE status IN ('CONFIRMED','APPROVED'))::int AS confirmed_requests,
                        MAX(created_at) AS last_request_at
                    FROM appointment_requests
                    GROUP BY tenant_id
                ),
                appointment_stats AS (
                    SELECT
                        tenant_id,
                        COUNT(*)::int AS appointments_count,
                        COUNT(*) FILTER (
                            WHERE appt_date = CURRENT_DATE::text
                        )::int AS today_appointments,
                        MAX(created_at) AS last_appointment_at
                    FROM appointments
                    GROUP BY tenant_id
                ),
                patient_stats AS (
                    SELECT
                        tenant_id,
                        COUNT(*)::int AS patients_count,
                        MAX(created_at) AS last_patient_at
                    FROM patients
                    GROUP BY tenant_id
                )
                SELECT
                    t.tenant_id,
                    t.clinic_name,
                    t.status,
                    t.timezone,
                    t.country_code,
                    COALESCE(dc.doctors_count, 0) AS doctors_count,
                    COALESCE(src.schedule_rules_count, 0) AS schedule_rules_count,
                    COALESCE(sc.slots_count, 0) AS slots_count,
                    COALESCE(rs.requests_count, 0) AS requests_count,
                    COALESCE(rs.pending_requests, 0) AS pending_requests,
                    COALESCE(rs.confirmed_requests, 0) AS confirmed_requests,
                    COALESCE(ap.appointments_count, 0) AS appointments_count,
                    COALESCE(ap.today_appointments, 0) AS today_appointments,
                    COALESCE(ps.patients_count, 0) AS patients_count,
                    rs.last_request_at,
                    ap.last_appointment_at,
                    ps.last_patient_at,
                    t.created_at,
                    t.updated_at,
                    tt.admin_token,
                    tt.reception_token,
                    tt.public_api_key
                FROM tenants t
                LEFT JOIN tenant_tokens tt
                  ON tt.tenant_id = t.tenant_id
                LEFT JOIN doctor_counts dc
                  ON dc.tenant_id = t.tenant_id
                LEFT JOIN schedule_rule_counts src
                  ON src.tenant_id = t.tenant_id
                LEFT JOIN slot_counts sc
                  ON sc.tenant_id = t.tenant_id
                LEFT JOIN request_stats rs
                  ON rs.tenant_id = t.tenant_id
                LEFT JOIN appointment_stats ap
                  ON ap.tenant_id = t.tenant_id
                LEFT JOIN patient_stats ps
                  ON ps.tenant_id = t.tenant_id
                ORDER BY t.created_at DESC
                """
            )
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/analytics/dashboard")
async def admin_analytics_dashboard(
    request: Request,
    tenant_id: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    tenant_id = (tenant_id or "").strip()

    async with AsyncSessionLocal() as db:
        await require_platform_api_access(request, db, x_admin_token)
        await ensure_patient_tables(db)

        r = await db.execute(
            text(
                """
                WITH today_appts AS (
                    SELECT COUNT(*) AS c
                    FROM appointments
                    WHERE appt_date = CURRENT_DATE::text
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                today_cancelled AS (
                    SELECT COUNT(*) AS c
                    FROM appointments
                    WHERE appt_date = CURRENT_DATE::text
                      AND status = 'CANCELLED'
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                today_completed AS (
                    SELECT COUNT(*) AS c
                    FROM appointments
                    WHERE appt_date = CURRENT_DATE::text
                      AND status = 'COMPLETED'
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                today_no_show AS (
                    SELECT COUNT(*) AS c
                    FROM appointments
                    WHERE appt_date = CURRENT_DATE::text
                      AND status = 'NO_SHOW'
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                doctors_active AS (
                    SELECT COUNT(*) AS c
                    FROM doctors
                    WHERE is_active = TRUE
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                open_slots AS (
                    SELECT COUNT(*) AS c
                    FROM appointment_slots
                    WHERE slot_date = CURRENT_DATE
                      AND status = 'OPEN'
                      AND (:tenant = '' OR tenant_id = :tenant)
                ),
                patients_total AS (
                    SELECT COUNT(*) AS c
                    FROM patients
                    WHERE (:tenant = '' OR tenant_id = :tenant)
                )
                SELECT
                    (SELECT c FROM today_appts) AS today_appointments,
                    (SELECT c FROM today_cancelled) AS today_cancelled,
                    (SELECT c FROM today_completed) AS today_completed,
                    (SELECT c FROM today_no_show) AS today_no_show,
                    (SELECT c FROM doctors_active) AS doctors_active,
                    (SELECT c FROM open_slots) AS open_slots,
                    (SELECT c FROM patients_total) AS patients_total
                """
            ),
            {"tenant": tenant_id},
        )

        row = r.mappings().first()

    return {"ok": True, "items": dict(row) if row else {}}

@router.get("/admin/plans/list")
async def admin_list_plans(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    code,
                    name,
                    monthly_price,
                    currency,
                    max_doctors,
                    max_monthly_appointments,
                    max_ai_conversations,
                    is_active,
                    created_at,
                    updated_at
                FROM plans
                ORDER BY monthly_price ASC, name ASC
                """
            )
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/subscriptions/list")
async def admin_subscriptions_list(
    request: Request,
    tenant_id: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        await require_platform_api_access(request, db, x_admin_token)

    tenant_id = (tenant_id or "").strip()
    status = (status or "").strip().upper()

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    s.id,
                    s.tenant_id,
                    t.clinic_name,
                    s.plan_code,
                    p.name AS plan_name,
                    p.monthly_price,
                    p.currency,
                    p.max_doctors,
                    p.max_monthly_appointments,
                    p.max_ai_conversations,
                    s.status,
                    s.started_at,
                    s.ends_at,
                    s.trial_ends_at,
                    s.stripe_customer_id,
                    s.stripe_subscription_id,
                    s.created_at,
                    s.updated_at
                FROM subscriptions s
                LEFT JOIN tenants t
                  ON t.tenant_id = s.tenant_id
                LEFT JOIN plans p
                  ON p.code = s.plan_code
                WHERE (:tenant_id = '' OR s.tenant_id = :tenant_id)
                  AND (:status = '' OR UPPER(s.status) = :status)
                ORDER BY s.created_at DESC, s.tenant_id ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "status": status,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/subscriptions/upsert")
async def admin_upsert_subscription(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    plan_code = str(payload.get("plan_code") or "").strip().lower()
    status = str(payload.get("status") or "TRIAL").strip().upper()
    stripe_customer_id = str(payload.get("stripe_customer_id") or "").strip() or None
    stripe_subscription_id = str(payload.get("stripe_subscription_id") or "").strip() or None
    started_at = str(payload.get("started_at") or "").strip() or None
    ends_at = str(payload.get("ends_at") or "").strip() or None
    trial_ends_at = str(payload.get("trial_ends_at") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not plan_code:
        raise HTTPException(status_code=400, detail="plan_code required")
    if status not in {"TRIAL", "ACTIVE", "PAST_DUE", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="invalid subscription status")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        tenant_chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not tenant_chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        plan_chk = await db.execute(
            text(
                """
                SELECT code
                FROM plans
                WHERE code = :code
                LIMIT 1
                """
            ),
            {"code": plan_code},
        )
        if not plan_chk.mappings().first():
            raise HTTPException(status_code=404, detail="plan_not_found")

        await db.execute(
            text(
                """
                INSERT INTO subscriptions (
                    tenant_id,
                    plan_code,
                    status,
                    started_at,
                    ends_at,
                    trial_ends_at,
                    stripe_customer_id,
                    stripe_subscription_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :plan_code,
                    :status,
                    CAST(:started_at AS TIMESTAMPTZ),
                    CAST(:ends_at AS TIMESTAMPTZ),
                    CAST(:trial_ends_at AS TIMESTAMPTZ),
                    :stripe_customer_id,
                    :stripe_subscription_id,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tenant_id)
                DO UPDATE SET
                    plan_code = EXCLUDED.plan_code,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    ends_at = EXCLUDED.ends_at,
                    trial_ends_at = EXCLUDED.trial_ends_at,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "plan_code": plan_code,
                "status": status,
                "started_at": started_at,
                "ends_at": ends_at,
                "trial_ends_at": trial_ends_at,
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "plan_code": plan_code,
        "status": status,
    }


@router.post("/admin/usage/log")
async def admin_log_usage_event(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    metric_code = str(payload.get("metric_code") or "").strip()
    quantity = int(payload.get("quantity") or 1)
    event_date = str(payload.get("event_date") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not metric_code:
        raise HTTPException(status_code=400, detail="metric_code required")
    if quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        await db.execute(
            text(
                """
                INSERT INTO usage_events (
                    tenant_id,
                    metric_code,
                    quantity,
                    event_date,
                    created_at
                )
                VALUES (
                    :tenant_id,
                    :metric_code,
                    :quantity,
                    COALESCE(CAST(:event_date AS DATE), CURRENT_DATE),
                    NOW()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "metric_code": metric_code,
                "quantity": quantity,
                "event_date": event_date,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "metric_code": metric_code,
        "quantity": quantity,
    }


@router.get("/admin/billing/overview")
async def admin_billing_overview(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        res = await db.execute(
            text(
                """
                WITH subscription_stats AS (
                    SELECT
                        COUNT(*)::int AS total_subscriptions,
                        COUNT(*) FILTER (WHERE status = 'ACTIVE')::int AS active_subscriptions,
                        COUNT(*) FILTER (WHERE status = 'TRIAL')::int AS trial_subscriptions,
                        COUNT(*) FILTER (WHERE status = 'PAST_DUE')::int AS past_due_subscriptions,
                        COUNT(*) FILTER (WHERE status = 'CANCELLED')::int AS cancelled_subscriptions
                    FROM subscriptions
                ),
                mrr_stats AS (
                    SELECT
                        COALESCE(SUM(p.monthly_price) FILTER (WHERE s.status = 'ACTIVE'), 0)::numeric(10,2) AS mrr
                    FROM subscriptions s
                    LEFT JOIN plans p
                      ON p.code = s.plan_code
                ),
                usage_this_month AS (
                    SELECT
                        COALESCE(SUM(quantity), 0)::int AS total_usage_this_month
                    FROM usage_events
                    WHERE date_trunc('month', event_date) = date_trunc('month', CURRENT_DATE)
                )
                SELECT
                    ss.total_subscriptions,
                    ss.active_subscriptions,
                    ss.trial_subscriptions,
                    ss.past_due_subscriptions,
                    ss.cancelled_subscriptions,
                    ms.mrr,
                    utm.total_usage_this_month
                FROM subscription_stats ss
                CROSS JOIN mrr_stats ms
                CROSS JOIN usage_this_month utm
                """
            )
        )

        row = res.mappings().first() or {}

    return {"ok": True, "item": dict(row)}

@router.get("/admin/usage/overview")
async def admin_usage_overview(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        res = await db.execute(
            text(
                """
                WITH usage_month AS (
                    SELECT
                        tenant_id,
                        SUM(
                            CASE WHEN metric_code = 'ai_conversations' THEN quantity ELSE 0 END
                        )::int AS ai_used,
                        SUM(
                            CASE WHEN metric_code = 'appointments_created' THEN quantity ELSE 0 END
                        )::int AS appt_used
                    FROM usage_events
                    WHERE date_trunc('month', event_date) = date_trunc('month', CURRENT_DATE)
                    GROUP BY tenant_id
                ),
                doctor_counts AS (
                    SELECT
                        tenant_id,
                        COUNT(*) FILTER (WHERE is_active = TRUE)::int AS doctors_used
                    FROM doctors
                    GROUP BY tenant_id
                )
                SELECT
                    t.tenant_id,
                    t.clinic_name,
                    COALESCE(s.plan_code, '') AS plan_code,
                    COALESCE(um.ai_used, 0) AS ai_used,
                    COALESCE(p.max_ai_conversations, 0) AS ai_limit,
                    COALESCE(um.appt_used, 0) AS appt_used,
                    COALESCE(p.max_monthly_appointments, 0) AS appt_limit,
                    COALESCE(dc.doctors_used, 0) AS doctors_used,
                    COALESCE(p.max_doctors, 0) AS doctors_limit
                FROM tenants t
                LEFT JOIN subscriptions s
                  ON s.tenant_id = t.tenant_id
                LEFT JOIN plans p
                  ON p.code = s.plan_code
                LEFT JOIN usage_month um
                  ON um.tenant_id = t.tenant_id
                LEFT JOIN doctor_counts dc
                  ON dc.tenant_id = t.tenant_id
                ORDER BY t.clinic_name ASC, t.tenant_id ASC
                """
            )
        )

        items = [dict(r) for r in res.mappings().all()]

    summary = {
        "ai_used": sum(int(x.get("ai_used") or 0) for x in items),
        "ai_limit": sum(int(x.get("ai_limit") or 0) for x in items),
        "appt_used": sum(int(x.get("appt_used") or 0) for x in items),
        "appt_limit": sum(int(x.get("appt_limit") or 0) for x in items),
        "doctors_used": sum(int(x.get("doctors_used") or 0) for x in items),
        "doctors_limit": sum(int(x.get("doctors_limit") or 0) for x in items),
    }

    return {
        "ok": True,
        "summary": summary,
        "items": items,
    }

@router.get("/admin/doctors/platform/list")
async def admin_platform_list_doctors(
    tenant_id: str = "",
    q: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    q = (q or "").strip()

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    d.tenant_id,
                    t.clinic_name,
                    d.doctor_key,
                    d.doctor_name,
                    d.specialty_key,
                    d.specialty_label,
                    d.is_active,
                    d.updated_at
                FROM doctors d
                LEFT JOIN tenants t
                  ON t.tenant_id = d.tenant_id
                WHERE (:tenant_id = '' OR d.tenant_id = :tenant_id)
                  AND (
                        :q = ''
                        OR COALESCE(d.doctor_key, '') ILIKE '%' || :q || '%'
                        OR COALESCE(d.doctor_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(d.specialty_label, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY d.tenant_id ASC, d.doctor_name ASC, d.doctor_key ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "q": q,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/doctors/platform/upsert")
async def admin_platform_upsert_doctor(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    doctor_key = str(payload.get("doctor_key") or "").strip()
    doctor_name = str(payload.get("doctor_name") or "").strip()
    specialty_key = str(payload.get("specialty_key") or "").strip() or None
    specialty_label = str(payload.get("specialty_label") or "").strip() or None
    is_active = bool(payload.get("is_active", True))

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")
    if not doctor_name:
        raise HTTPException(status_code=400, detail="doctor_name required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        from core.saas_limits import enforce_doctor_limit
        await enforce_doctor_limit(db, tenant_id)

        await db.execute(
            text(
                """
                INSERT INTO doctors (
                    tenant_id,
                    doctor_key,
                    doctor_name,
                    specialty_key,
                    specialty_label,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :doctor_key,
                    :doctor_name,
                    :specialty_key,
                    :specialty_label,
                    :is_active,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (tenant_id, doctor_key)
                DO UPDATE SET
                    doctor_name = EXCLUDED.doctor_name,
                    specialty_key = EXCLUDED.specialty_key,
                    specialty_label = EXCLUDED.specialty_label,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "doctor_name": doctor_name,
                "specialty_key": specialty_key,
                "specialty_label": specialty_label,
                "is_active": is_active,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
    }


@router.post("/admin/doctors/platform/{tenant_id}/{doctor_key}/activate")
async def admin_platform_activate_doctor(
    tenant_id: str,
    doctor_key: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                UPDATE doctors
                SET is_active = TRUE,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
            },
        )
        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "is_active": True,
    }


@router.post("/admin/doctors/platform/{tenant_id}/{doctor_key}/deactivate")
async def admin_platform_deactivate_doctor(
    tenant_id: str,
    doctor_key: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                UPDATE doctors
                SET is_active = FALSE,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
            },
        )
        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "is_active": False,
    }


@router.get("/admin/doctors/workload")
async def admin_doctor_workload(
    tenant_id: str,
    doctor_key: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    doctor_key = (doctor_key or "").strip()

    if not tenant_id or not doctor_key:
        raise HTTPException(status_code=400, detail="tenant_id and doctor_key required")

    async with AsyncSessionLocal() as db:

        res = await db.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE appt_date = CURRENT_DATE::text
                          AND status <> 'CANCELLED'
                    ) AS today_count,

                    COUNT(*) FILTER (
                        WHERE appt_date >= CURRENT_DATE::text
                          AND status <> 'CANCELLED'
                    ) AS upcoming_count,

                    COUNT(*) FILTER (
                        WHERE appt_date < CURRENT_DATE::text
                          AND status <> 'CANCELLED'
                    ) AS completed_count
                FROM appointments
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
            },
        )

        row = res.mappings().first() or {}

    return {
        "ok": True,
        "today": row.get("today_count", 0),
        "upcoming": row.get("upcoming_count", 0),
        "completed": row.get("completed_count", 0),
    }


@router.get("/admin/doctors/daily-schedule")
async def admin_doctor_daily_schedule(
    tenant_id: str,
    doctor_key: str,
    appt_date: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    doctor_key = (doctor_key or "").strip()
    appt_date = (appt_date or "").strip()

    if not tenant_id or not doctor_key:
        raise HTTPException(status_code=400, detail="tenant_id and doctor_key required")

    async with AsyncSessionLocal() as db:

        if appt_date:
            res = await db.execute(
                text(
                    """
                    SELECT
                        id,
                        patient_name,
                        patient_mobile,
                        appt_date,
                        appt_time,
                        status,
                        insurance_provider,
                        insurance_plan,
                        insurance_member_id
                    FROM appointments
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                      AND appt_date = :appt_date
                    ORDER BY appt_time ASC, created_at ASC
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "doctor_key": doctor_key,
                    "appt_date": appt_date,
                },
            )
        else:
            res = await db.execute(
                text(
                    """
                    SELECT
                        id,
                        patient_name,
                        patient_mobile,
                        appt_date,
                        appt_time,
                        status,
                        insurance_provider,
                        insurance_plan,
                        insurance_member_id
                    FROM appointments
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                      AND appt_date = CURRENT_DATE::text
                    ORDER BY appt_time ASC, created_at ASC
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "doctor_key": doctor_key,
                },
            )

        items = [dict(r) for r in res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "appt_date": appt_date or None,
        "items": items,
    }


@router.get("/admin/calendar/doctors")
async def admin_calendar_doctors(
    tenant_id: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    d.tenant_id,
                    t.clinic_name,
                    d.doctor_key,
                    d.doctor_name,
                    d.specialty_label,
                    d.is_active
                FROM doctors d
                LEFT JOIN tenants t
                  ON t.tenant_id = d.tenant_id
                WHERE (:tenant_id = '' OR d.tenant_id = :tenant_id)
                ORDER BY d.tenant_id ASC, d.doctor_name ASC
                """
            ),
            {"tenant_id": tenant_id},
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/doctors/workload/cards")
async def admin_doctors_workload_cards(
    tenant_id: str = "",
    doctor_key: str = "",
    date: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    doctor_key = (doctor_key or "").strip()
    date = (date or "").strip()

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                WITH base_date AS (
                    SELECT COALESCE(NULLIF(:date, ''), CURRENT_DATE::text) AS ref_date_text
                ),
                doctor_base AS (
                    SELECT
                        d.tenant_id,
                        t.clinic_name,
                        d.doctor_key,
                        d.doctor_name,
                        d.specialty_label,
                        d.is_active,
                        (SELECT ref_date_text FROM base_date) AS ref_date_text
                    FROM doctors d
                    LEFT JOIN tenants t
                      ON t.tenant_id = d.tenant_id
                    WHERE (:tenant_id = '' OR d.tenant_id = :tenant_id)
                      AND (:doctor_key = '' OR d.doctor_key = :doctor_key)
                ),
                appt_stats AS (
                    SELECT
                        a.tenant_id,
                        a.doctor_key,
                        COUNT(*) FILTER (
                            WHERE a.appt_date = (SELECT ref_date_text FROM base_date)
                        )::int AS today_appointments,

                        COUNT(*) FILTER (
                            WHERE a.appt_date >= (SELECT ref_date_text FROM base_date)
                              AND COALESCE(a.status, '') <> 'CANCELLED'
                        )::int AS upcoming_appointments,

                        COUNT(*) FILTER (
                            WHERE a.appt_date >= (SELECT ref_date_text FROM base_date)
                              AND a.appt_date <= (
                                  (SELECT ref_date_text FROM base_date)::date + INTERVAL '6 day'
                              )::date::text
                        )::int AS weekly_appointments,

                        COUNT(*) FILTER (
                            WHERE UPPER(COALESCE(a.status, '')) = 'COMPLETED'
                        )::int AS completed_appointments,

                        COUNT(*) FILTER (
                            WHERE UPPER(COALESCE(a.status, '')) = 'CANCELLED'
                        )::int AS cancelled_appointments,

                        COUNT(*) FILTER (
                            WHERE UPPER(COALESCE(a.status, '')) = 'NO_SHOW'
                        )::int AS no_show_appointments,

                        MIN(a.appt_date) FILTER (
                            WHERE a.appt_date >= (SELECT ref_date_text FROM base_date)
                              AND COALESCE(a.status, '') <> 'CANCELLED'
                        ) AS next_appt_date
                    FROM appointments a
                    WHERE (:tenant_id = '' OR a.tenant_id = :tenant_id)
                      AND (:doctor_key = '' OR a.doctor_key = :doctor_key)
                    GROUP BY a.tenant_id, a.doctor_key
                ),
                next_time AS (
                    SELECT DISTINCT ON (a.tenant_id, a.doctor_key)
                        a.tenant_id,
                        a.doctor_key,
                        a.appt_date AS next_appt_date,
                        a.appt_time AS next_appt_time
                    FROM appointments a
                    WHERE (:tenant_id = '' OR a.tenant_id = :tenant_id)
                      AND (:doctor_key = '' OR a.doctor_key = :doctor_key)
                      AND a.appt_date >= (SELECT ref_date_text FROM base_date)
                      AND COALESCE(a.status, '') <> 'CANCELLED'
                    ORDER BY a.tenant_id, a.doctor_key, a.appt_date ASC, a.appt_time ASC
                )
                SELECT
                    db.tenant_id,
                    db.clinic_name,
                    db.doctor_key,
                    db.doctor_name,
                    db.specialty_label,
                    db.is_active,

                    COALESCE(s.today_appointments, 0) AS today_appointments,
                    COALESCE(s.upcoming_appointments, 0) AS upcoming_appointments,
                    COALESCE(s.weekly_appointments, 0) AS weekly_appointments,
                    COALESCE(s.completed_appointments, 0) AS completed_appointments,
                    COALESCE(s.cancelled_appointments, 0) AS cancelled_appointments,
                    COALESCE(s.no_show_appointments, 0) AS no_show_appointments,

                    nt.next_appt_date,
                    nt.next_appt_time
                FROM doctor_base db
                LEFT JOIN appt_stats s
                  ON s.tenant_id = db.tenant_id
                 AND s.doctor_key = db.doctor_key
                LEFT JOIN next_time nt
                  ON nt.tenant_id = db.tenant_id
                 AND nt.doctor_key = db.doctor_key
                ORDER BY db.tenant_id ASC, db.doctor_name ASC, db.doctor_key ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "date": date,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {
        "ok": True,
        "date": date or None,
        "items": items,
    }


@router.get("/admin/calendar/slots")
async def admin_calendar_slots(
    tenant_id: str,
    doctor_key: str,
    date_from: str,
    date_to: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    doctor_key = (doctor_key or "").strip()
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")
    if not date_from:
        raise HTTPException(status_code=400, detail="date_from required")
    if not date_to:
        raise HTTPException(status_code=400, detail="date_to required")

    try:
        date_from_obj = date.fromisoformat(date_from)
        date_to_obj = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="date_from and date_to must be in YYYY-MM-DD format",
        )

    if date_from_obj > date_to_obj:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        slot_res = await db.execute(
            text(
                """
                SELECT
                    slot_date,
                    slot_time,
                    status,
                    updated_at
                FROM appointment_slots
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date >= :date_from
                  AND slot_date <= :date_to
                ORDER BY slot_date ASC, slot_time ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "date_from": date_from_obj,
                "date_to": date_to_obj,
            },
        )
        slots = [dict(r) for r in slot_res.mappings().all()]

        off_res = await db.execute(
            text(
                """
                SELECT
                    starts_at,
                    ends_at,
                    reason,
                    created_at
                FROM doctor_time_off
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND starts_at::date <= :date_to
                  AND ends_at::date >= :date_from
                ORDER BY starts_at ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "date_from": date_from_obj,
                "date_to": date_to_obj,
            },
        )
        time_off = [dict(r) for r in off_res.mappings().all()]

        rules_res = await db.execute(
            text(
                """
                SELECT
                    day_of_week,
                    start_time,
                    end_time,
                    slot_minutes,
                    is_active
                FROM doctor_schedule_rules
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                ORDER BY day_of_week ASC, start_time ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
            },
        )
        schedule_rules = [dict(r) for r in rules_res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "date_from": date_from,
        "date_to": date_to,
        "slots": slots,
        "time_off": time_off,
        "schedule_rules": schedule_rules,
    }

@router.post("/admin/calendar/generate-slots")
async def admin_calendar_generate_slots(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    days_ahead = int(payload.get("days_ahead") or 14)
    tz_name = str(payload.get("tz_name") or "Asia/Riyadh").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        stats = await generate_slots(
            db=db,
            tenant_id=tenant_id,
            tz_name=tz_name or GEN_DEFAULT_TZ,
            days_ahead=days_ahead,
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
        action="doctor_schedule_changed",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        role_code=role_code,
        entity_type="calendar_slots",
        entity_id=doctor_key or tenant_id,
        metadata={
            "doctor_key": doctor_key,
            "days_ahead": days_ahead,
            "tz_name": tz_name,
            "change_type": "generate_slots",
        },
    )

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "stats": stats,
    }


@router.post("/admin/calendar/timeoff/add")
async def admin_calendar_add_timeoff(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = str(payload.get("tenant_id") or "").strip()
    doctor_key = str(payload.get("doctor_key") or "").strip()
    starts_at = str(payload.get("starts_at") or "").strip()
    ends_at = str(payload.get("ends_at") or "").strip()
    reason = str(payload.get("reason") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")
    if not starts_at:
        raise HTTPException(status_code=400, detail="starts_at required")
    if not ends_at:
        raise HTTPException(status_code=400, detail="ends_at required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        await db.execute(
            text(
                """
                INSERT INTO doctor_time_off (
                    tenant_id,
                    doctor_key,
                    starts_at,
                    ends_at,
                    reason,
                    created_at
                )
                VALUES (
                    :tenant_id,
                    :doctor_key,
                    :starts_at,
                    :ends_at,
                    :reason,
                    NOW()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "reason": reason,
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
        action="doctor_schedule_changed",
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        role_code=role_code,
        entity_type="doctor_time_off",
        entity_id=doctor_key,
        metadata={
            "doctor_key": doctor_key,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "reason": reason,
            "change_type": "timeoff_add",
        },
    )

    await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
    }

async def _get_appointment_by_ref(
    db: AsyncSession,
    appointment_ref: str,
) -> Optional[Dict[str, Any]]:
    ref = str(appointment_ref or "").strip()
    if not ref:
        return None

    row = None

    # 1) try numeric internal id
    if ref.isdigit():
        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    appointment_id,
                    tenant_id,
                    doctor_key,
                    patient_name,
                    patient_mobile,
                    patient_id,
                    appt_date,
                    appt_time,
                    status,
                    notes,
                    booking_source,
                    insurance_provider,
                    insurance_plan,
                    insurance_member_id,
                    insurance_policy_number,
                    insurance_expiry,
                    insurance_status,
                    insurance_verified
                FROM appointments
                WHERE id = :id
                LIMIT 1
                """
            ),
            {"id": int(ref)},
        )
        row = res.mappings().first()

    # 2) fallback to business appointment_id
    if not row:
        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    appointment_id,
                    tenant_id,
                    doctor_key,
                    patient_name,
                    patient_mobile,
                    patient_id,
                    appt_date,
                    appt_time,
                    status,
                    notes,
                    booking_source,
                    insurance_provider,
                    insurance_plan,
                    insurance_member_id,
                    insurance_policy_number,
                    insurance_expiry,
                    insurance_status,
                    insurance_verified
                FROM appointments
                WHERE appointment_id = :appointment_id
                LIMIT 1
                """
            ),
            {"appointment_id": ref},
        )
        row = res.mappings().first()

    return dict(row) if row else None

@router.get("/admin/appointments/list")
async def admin_list_all_appointments(
    tenant_id: str = "",
    status: str = "ALL",
    doctor_key: str = "",
    q: str = "",
    limit: int = 200,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    status = (status or "ALL").strip().upper()
    doctor_key = (doctor_key or "").strip()
    q = (q or "").strip()

    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    a.tenant_id,
                    t.clinic_name,
                    a.doctor_key,
                    a.patient_id,
                    a.patient_name,
                    a.patient_mobile,
                    a.appt_date AS slot_date,
                    a.appt_time AS slot_time,
                    a.insurance_provider,
                    a.insurance_plan,
                    a.insurance_member_id,
                    a.insurance_policy_number,
                    a.insurance_expiry,
                    a.insurance_status,
                    a.insurance_verified,
                    a.status,
                    a.created_at
                FROM appointments a
                LEFT JOIN tenants t
                  ON t.tenant_id = a.tenant_id
                WHERE (:tenant_id = '' OR a.tenant_id = :tenant_id)
                  AND (:status = 'ALL' OR UPPER(a.status) = :status)
                  AND (:doctor_key = '' OR a.doctor_key = :doctor_key)
                  AND (
                        :q = ''
                        OR COALESCE(a.patient_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.patient_mobile, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.patient_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.doctor_key, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.tenant_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.insurance_provider, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.insurance_member_id, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY
                    a.appt_date DESC,
                    a.appt_time DESC,
                    a.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "status": status,
                "doctor_key": doctor_key,
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}

@router.get("/admin/reminders/list")
async def admin_list_reminders(
    tenant_id: str = "",
    status: str = "",
    reminder_type: str = "",
    limit: int = 200,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    status = (status or "").strip().upper()
    reminder_type = (reminder_type or "").strip().upper()

    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    rl.id,
                    rl.tenant_id,
                    t.clinic_name,
                    rl.appointment_id,
                    rl.reminder_type,
                    rl.status,
                    rl.sent_at,
                    a.patient_id,
                    a.patient_name,
                    a.patient_mobile,
                    a.doctor_key,
                    a.appt_date,
                    a.appt_time
                FROM reminder_logs rl
                LEFT JOIN tenants t
                  ON t.tenant_id = rl.tenant_id
                LEFT JOIN appointments a
                  ON a.id = rl.appointment_id
                WHERE (:tenant_id = '' OR rl.tenant_id = :tenant_id)
                  AND (:status = '' OR UPPER(rl.status) = :status)
                  AND (:reminder_type = '' OR UPPER(rl.reminder_type) = :reminder_type)
                ORDER BY rl.sent_at DESC, rl.id DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "status": status,
                "reminder_type": reminder_type,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/reminders/test")
async def test_reminder(
    tenant_id: str,
    appointment_id: int,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await db.execute(
            text(
                """
                INSERT INTO reminder_logs (
                    tenant_id,
                    appointment_id,
                    reminder_type,
                    status
                )
                VALUES (
                    :tenant_id,
                    :appointment_id,
                    'REMINDER_MAIN',
                    'SENT'
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "appointment_id": appointment_id,
            },
        )

        await db.commit()

    return {"ok": True}


@router.get("/admin/appointments/tenants")
async def admin_appointments_tenants(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    tenant_id,
                    clinic_name,
                    status
                FROM tenants
                ORDER BY clinic_name ASC, tenant_id ASC
                """
            )
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}

@router.post("/admin/appointments/{appointment_ref}/cancel")
async def admin_cancel_any_appointment(
    request: Request,
    appointment_ref: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)

        row = await _get_appointment_by_ref(db, appointment_ref)
        if not row:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        current_status = str(row.get("status") or "").upper()
        if current_status == "CANCELLED":
            return {
                "ok": True,
                "appointment_id": row.get("appointment_id"),
                "id": row.get("id"),
                "status": "CANCELLED",
            }

        appt_date_raw = str(row.get("appt_date") or "").strip()
        if not appt_date_raw:
            raise HTTPException(status_code=400, detail="appointment_date_missing")

        try:
            slot_date_obj = date.fromisoformat(appt_date_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_appointment_date")

        slot_time_hhmm = str(row.get("appt_time") or "").strip()[:5]
        if not slot_time_hhmm:
            raise HTTPException(status_code=400, detail="appointment_time_missing")

        await db.execute(
            text(
                """
                UPDATE appointments
                SET
                    status = 'CANCELLED',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": int(row["id"])},
        )

        await db.execute(
            text(
                """
                UPDATE appointment_slots
                SET
                    status = 'OPEN',
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = :slot_date
                  AND slot_time = :slot_time
                """
            ),
            {
                "tenant_id": str(row["tenant_id"]),
                "doctor_key": str(row["doctor_key"]),
                "slot_date": slot_date_obj,
                "slot_time": slot_time_hhmm,
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
            tenant_id=str(row["tenant_id"]),
            action="appointment_cancelled",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="appointment",
            entity_id=str(row.get("appointment_id") or row.get("id")),
            metadata={
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
                "doctor_key": str(row.get("doctor_key") or ""),
                "appt_date": appt_date_raw,
                "appt_time": slot_time_hhmm,
            },
        )

        await db.commit()

        whatsapp_sent = False
        whatsapp_error = None

        patient_mobile = str(row.get("patient_mobile") or "").strip()
        if (
            patient_mobile
            and wa_send_text is not None
            and build_appointment_cancelled_message is not None
        ):
            try:
                ctx = await _get_tenant_message_context(db, str(row["tenant_id"]))
                clinic_name = str(ctx.get("clinic_name") or row["tenant_id"]).strip()
                language = str(ctx.get("language") or "en").strip().lower()

                if language not in {"en", "ar"}:
                    language = "en"

                message_text = await build_appointment_cancelled_message(
                    db,
                    tenant_id=str(row["tenant_id"]),
                    language=language,
                    clinic_name=clinic_name,
                    patient_name=str(row.get("patient_name") or ""),
                    doctor_name=str(row.get("doctor_key") or ""),
                    date=appt_date_raw,
                    time=slot_time_hhmm,
                )

                if message_text:
                    wa_send_text(patient_mobile, message_text)
                    whatsapp_sent = True

            except Exception as e:
                whatsapp_error = str(e)

    return {
        "ok": True,
        "id": row.get("id"),
        "appointment_id": row.get("appointment_id"),
        "status": "CANCELLED",
        "whatsapp_sent": whatsapp_sent,
        "whatsapp_error": whatsapp_error,
    }

@router.post("/admin/appointments/{appointment_ref}/complete")
async def admin_complete_any_appointment(
    request: Request,
    appointment_ref: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        row = await _get_appointment_by_ref(db, appointment_ref)
        if not row:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        await db.execute(
            text(
                """
                UPDATE appointments
                SET status = 'COMPLETED',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": int(row["id"])},
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
            tenant_id=str(row["tenant_id"]),
            action="appointment_completed",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="appointment",
            entity_id=str(row.get("appointment_id") or row.get("id")),
            metadata={
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
            },
        )

        await db.commit()

    return {
        "ok": True,
        "id": row.get("id"),
        "appointment_id": row.get("appointment_id"),
        "status": "COMPLETED",
    }

@router.post("/admin/appointments/{appointment_ref}/no-show")
async def mark_no_show(
    request: Request,
    appointment_ref: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)

        row = await _get_appointment_by_ref(db, appointment_ref)
        if not row:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        current_status = str(row.get("status") or "").upper()
        if current_status == "NO_SHOW":
            return {
                "ok": True,
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
                "status": "NO_SHOW",
            }

        await db.execute(
            text(
                """
                UPDATE appointments
                SET status = 'NO_SHOW',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": int(row["id"])},
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
            tenant_id=str(row["tenant_id"]),
            action="appointment_no_show",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="appointment",
            entity_id=str(row.get("appointment_id") or row.get("id")),
            metadata={
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
                "doctor_key": row.get("doctor_key"),
                "appt_date": row.get("appt_date"),
                "appt_time": str(row.get("appt_time") or "")[:5],
            },
        )

        await db.commit()

    return {
        "ok": True,
        "id": row.get("id"),
        "appointment_id": row.get("appointment_id"),
        "status": "NO_SHOW",
    }


@router.post("/admin/appointments/{appointment_id}/reschedule")
async def admin_reschedule_any_appointment(
    appointment_id: int,
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):

    new_slot_date = str(payload.get("slot_date") or "").strip()
    new_slot_time = str(payload.get("slot_time") or "").strip()

    if not new_slot_date:
        raise HTTPException(status_code=400, detail="slot_date required")
    if not new_slot_time:
        raise HTTPException(status_code=400, detail="slot_time required")

    new_slot_time = new_slot_time[:5]

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    tenant_id,
                    doctor_key,
                    appt_date,
                    appt_time,
                    status
                FROM appointments
                WHERE id = :id
                LIMIT 1
                """
            ),
            {"id": appointment_id},
        )
        appt = res.mappings().first()

        if not appt:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        if str(appt.get("status") or "").upper() == "CANCELLED":
            raise HTTPException(status_code=400, detail="cannot_reschedule_cancelled_appointment")

        tenant_id = str(appt["tenant_id"])
        doctor_key = str(appt["doctor_key"])
        old_slot_date = str(appt["appt_date"] or "")
        old_slot_time = str(appt["appt_time"] or "")[:5]

        chk = await db.execute(
            text(
                """
                SELECT status
                FROM appointment_slots
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = CAST(:slot_date AS DATE)
                  AND slot_time = :slot_time
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "slot_date": new_slot_date,
                "slot_time": new_slot_time,
            },
        )
        slot = chk.mappings().first()

        if not slot:
            raise HTTPException(status_code=404, detail="target_slot_not_found")

        if str(slot.get("status") or "").upper() != "OPEN":
            raise HTTPException(status_code=400, detail="target_slot_not_available")

        await db.execute(
            text(
                """
                UPDATE appointment_slots
                SET status = 'OPEN',
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = CAST(:slot_date AS DATE)
                  AND slot_time = :slot_time
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "slot_date": old_slot_date,
                "slot_time": old_slot_time,
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
                  AND slot_date = CAST(:slot_date AS DATE)
                  AND slot_time = :slot_time
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "slot_date": new_slot_date,
                "slot_time": new_slot_time,
            },
        )

        await db.execute(
            text(
                """
                UPDATE appointments
                SET appt_date = :appt_date,
                    appt_time = :appt_time,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": appointment_id,
                "appt_date": new_slot_date,
                "appt_time": new_slot_time,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "appointment_id": appointment_id,
        "status": "RESCHEDULED",
        "slot_date": new_slot_date,
        "slot_time": new_slot_time,
    }

def _build_appointment_reminder_text(row: Dict[str, Any]) -> str:
    clinic_name = str(row.get("clinic_name") or "Clinic").strip()
    patient_name = str(row.get("patient_name") or "").strip() or "Patient"
    doctor_name = str(row.get("doctor_key") or "").strip() or "Doctor"
    slot_date = str(row.get("slot_date") or row.get("appt_date") or "").strip()
    slot_time = str(row.get("slot_time") or row.get("appt_time") or "").strip()[:5]

    language = str(row.get("language") or "").strip().lower()

    if language == "ar":
        return (
            f"🔔 تذكير من {clinic_name}\n\n"
            f"عزيزي/عزيزتي {patient_name}،\n"
            f"هذا تذكير بموعدك.\n\n"
            f"👨‍⚕️ الطبيب: {doctor_name}\n"
            f"📅 التاريخ: {slot_date}\n"
            f"⏰ الوقت: {slot_time}\n\n"
            f"في حال رغبتك في تغيير الموعد يرجى التواصل مع العيادة."
        ).strip()

    return (
        f"🔔 Reminder from {clinic_name}\n\n"
        f"Dear {patient_name},\n"
        f"This is a reminder for your appointment.\n\n"
        f"👨‍⚕️ Doctor: {doctor_name}\n"
        f"📅 Date: {slot_date}\n"
        f"⏰ Time: {slot_time}\n\n"
        f"If you need to reschedule please contact the clinic."
    ).strip()

@router.post("/admin/appointments/{appointment_ref}/send-reminder")
async def admin_send_appointment_reminder(
    request: Request,
    appointment_ref: str,
    payload: Dict[str, Any] | None = None,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    if wa_send_text is None:
        raise HTTPException(status_code=500, detail="whatsapp_sender_not_configured")

    payload = payload or {}
    custom_message = str(payload.get("message_text") or "").strip()

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)

        row = await _get_appointment_by_ref(db, appointment_ref)
        if not row:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        to_user = str(row.get("patient_mobile") or "").strip()
        if not to_user:
            raise HTTPException(status_code=400, detail="patient_mobile_missing")

        reminder_row = {
            "id": row.get("id"),
            "appointment_id": row.get("appointment_id"),
            "tenant_id": row.get("tenant_id"),
            "clinic_name": row.get("clinic_name"),
            "doctor_key": row.get("doctor_key"),
            "patient_name": row.get("patient_name"),
            "patient_mobile": row.get("patient_mobile"),
            "slot_date": row.get("appt_date"),
            "slot_time": row.get("appt_time"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
        }

        message_text = custom_message or _build_appointment_reminder_text(reminder_row)
        if not message_text:
            raise HTTPException(status_code=400, detail="message_text_empty")
        try:
            wa_send_text(to_user, message_text)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"whatsapp_send_failed: {str(e)}"
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
            tenant_id=str(row["tenant_id"]),
            action="appointment_reminder_sent",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="appointment",
            entity_id=str(row.get("appointment_id") or row.get("id")),
            metadata={
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
                "sent_to": to_user,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "id": row.get("id"),
        "appointment_id": row.get("appointment_id"),
        "sent_to": to_user,
        "message_text": message_text,
    }


@router.post("/admin/appointments/{appointment_ref}/send-message")
async def admin_send_appointment_message(
    request: Request,
    appointment_ref: str,
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    if wa_send_text is None:
        raise HTTPException(status_code=500, detail="whatsapp_sender_not_configured")

    message_text = str(payload.get("message_text") or "").strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message_text_required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)

        row = await _get_appointment_by_ref(db, appointment_ref)
        if not row:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        to_user = str(row.get("patient_mobile") or "").strip()
        if not to_user:
            raise HTTPException(status_code=400, detail="patient_mobile_missing")

        wa_send_text(to_user, message_text)

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
            tenant_id=str(row["tenant_id"]),
            action="appointment_message_sent",
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            role_code=role_code,
            entity_type="appointment",
            entity_id=str(row.get("appointment_id") or row.get("id")),
            metadata={
                "id": row.get("id"),
                "appointment_id": row.get("appointment_id"),
                "sent_to": to_user,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "id": row.get("id"),
        "appointment_id": row.get("appointment_id"),
        "sent_to": to_user,
        "message_text": message_text,
    }


@router.get("/admin/patients/list")
async def admin_list_patients(
    tenant_id: str = "",
    q: str = "",
    is_active: str = "ALL",
    limit: int = 200,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    q = (q or "").strip()
    is_active = (is_active or "ALL").strip().upper()

    if is_active not in {"ALL", "ACTIVE", "INACTIVE"}:
        raise HTTPException(status_code=400, detail="invalid is_active")

    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    p.id,
                    p.tenant_id,
                    t.clinic_name,
                    p.patient_id,
                    p.full_name,
                    p.mobile,
                    p.national_id,
                    p.date_of_birth,
                    p.gender,
                    p.address,
                    p.insurance_provider,
                    p.insurance_plan,
                    p.insurance_member_id,
                    p.insurance_policy_number,
                    p.insurance_expiry,
                    p.insurance_status,
                    p.insurance_verified,
                    p.is_active,
                    p.created_at,
                    p.updated_at,
                    COALESCE(a_stats.total_appointments, 0) AS total_appointments,
                    a_stats.last_appointment_date
                FROM patients p
                LEFT JOIN tenants t
                  ON t.tenant_id = p.tenant_id
                LEFT JOIN (
                    SELECT
                        tenant_id,
                        patient_id,
                        COUNT(*)::int AS total_appointments,
                        MAX(appt_date) AS last_appointment_date
                    FROM appointments
                    WHERE patient_id IS NOT NULL
                      AND patient_id <> ''
                    GROUP BY tenant_id, patient_id
                ) a_stats
                  ON a_stats.tenant_id = p.tenant_id
                 AND a_stats.patient_id = p.patient_id
                WHERE (:tenant_id = '' OR p.tenant_id = :tenant_id)
                  AND (
                        :is_active = 'ALL'
                        OR (:is_active = 'ACTIVE' AND p.is_active = TRUE)
                        OR (:is_active = 'INACTIVE' AND p.is_active = FALSE)
                      )
                  AND (
                        :q = ''
                        OR COALESCE(p.patient_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.full_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.mobile, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.national_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.insurance_provider, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.insurance_member_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY p.updated_at DESC, p.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "q": q,
                "is_active": is_active,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/patients/history")
async def admin_patient_history(
    q: str = "",
    limit: int = 100,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q required")

    if limit < 1:
        limit = 1
    if limit > 300:
        limit = 300

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    a.tenant_id,
                    t.clinic_name,
                    a.patient_id,
                    a.doctor_key,
                    a.patient_name,
                    a.patient_mobile,
                    a.appt_date AS slot_date,
                    a.appt_time AS slot_time,
                    a.insurance_provider,
                    a.insurance_plan,
                    a.insurance_member_id,
                    a.insurance_policy_number,
                    a.insurance_expiry,
                    a.insurance_status,
                    a.insurance_verified,
                    a.status,
                    a.created_at
                FROM appointments a
                LEFT JOIN tenants t
                  ON t.tenant_id = a.tenant_id
                WHERE
                    COALESCE(a.patient_id, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.patient_name, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.patient_mobile, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.insurance_provider, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.insurance_member_id, '') ILIKE '%' || :q || '%'
                ORDER BY
                    COALESCE(a.patient_mobile, '') ASC,
                    COALESCE(a.patient_name, '') ASC,
                    a.appt_date DESC,
                    a.appt_time DESC,
                    a.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/patients/profile")
async def admin_patient_profile(
    tenant_id: str,
    patient_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    patient_id = (patient_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id required")

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        patient_res = await db.execute(
            text(
                """
                SELECT
                    p.id,
                    p.tenant_id,
                    t.clinic_name,
                    p.patient_id,
                    p.full_name,
                    p.mobile,
                    p.national_id,
                    p.date_of_birth,
                    p.gender,
                    p.address,
                    p.insurance_provider,
                    p.insurance_plan,
                    p.insurance_member_id,
                    p.insurance_policy_number,
                    p.insurance_expiry,
                    p.insurance_status,
                    p.insurance_verified,
                    p.is_active,
                    p.created_at,
                    p.updated_at
                FROM patients p
                LEFT JOIN tenants t
                  ON t.tenant_id = p.tenant_id
                WHERE p.tenant_id = :tenant_id
                  AND p.patient_id = :patient_id
                LIMIT 1
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
            },
        )
        patient_row = patient_res.mappings().first()

        if not patient_row:
            raise HTTPException(status_code=404, detail="patient_not_found")

        appt_res = await db.execute(
            text(
                """
                SELECT
                    id,
                    doctor_key,
                    appt_date,
                    appt_time,
                    status,
                    insurance_provider,
                    insurance_plan,
                    insurance_member_id,
                    insurance_status,
                    insurance_verified,
                    created_at
                FROM appointments
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id
                ORDER BY appt_date DESC, appt_time DESC, created_at DESC
                LIMIT 100
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
            },
        )
        appointments = [dict(r) for r in appt_res.mappings().all()]

    return {
        "ok": True,
        "patient": dict(patient_row),
        "appointments": appointments,
    }


@router.post("/admin/patients/{tenant_id}/{patient_id}/deactivate")
async def admin_deactivate_patient(
    tenant_id: str,
    patient_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    patient_id = (patient_id or "").strip()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                UPDATE patients
                SET is_active = FALSE,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "patient_id": patient_id, "is_active": False}


@router.post("/admin/patients/{tenant_id}/{patient_id}/activate")
async def admin_activate_patient(
    tenant_id: str,
    patient_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    patient_id = (patient_id or "").strip()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                UPDATE patients
                SET is_active = TRUE,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "patient_id": patient_id, "is_active": True}


@router.get("/admin/appointments/export.csv")
async def admin_export_appointments_csv(
    tenant_id: str = "",
    status: str = "ALL",
    q: str = "",
    limit: int = 1000,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    status = (status or "ALL").strip().upper()
    q = (q or "").strip()

    if limit < 1:
        limit = 1
    if limit > 5000:
        limit = 5000

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    a.tenant_id,
                    t.clinic_name,
                    a.patient_id,
                    a.doctor_key,
                    a.patient_name,
                    a.patient_mobile,
                    a.appt_date AS slot_date,
                    a.appt_time AS slot_time,
                    a.insurance_provider,
                    a.insurance_plan,
                    a.insurance_member_id,
                    a.insurance_policy_number,
                    a.insurance_expiry,
                    a.insurance_status,
                    a.insurance_verified,
                    a.status,
                    a.created_at
                FROM appointments a
                LEFT JOIN tenants t
                  ON t.tenant_id = a.tenant_id
                WHERE (:tenant_id = '' OR a.tenant_id = :tenant_id)
                  AND (:status = 'ALL' OR UPPER(a.status) = :status)
                  AND (
                        :q = ''
                        OR COALESCE(a.patient_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.patient_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.patient_mobile, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.doctor_key, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.tenant_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.insurance_provider, '') ILIKE '%' || :q || '%'
                        OR COALESCE(a.insurance_member_id, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY
                    a.appt_date DESC,
                    a.appt_time DESC,
                    a.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "status": status,
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "appointment_row_id",
        "tenant_id",
        "clinic_name",
        "patient_id",
        "doctor_key",
        "patient_name",
        "patient_mobile",
        "slot_date",
        "slot_time",
        "insurance_provider",
        "insurance_plan",
        "insurance_member_id",
        "insurance_policy_number",
        "insurance_expiry",
        "insurance_status",
        "insurance_verified",
        "status",
        "created_at",
    ])

    for it in items:
        writer.writerow([
            it.get("id"),
            it.get("tenant_id"),
            it.get("clinic_name"),
            it.get("patient_id"),
            it.get("doctor_key"),
            it.get("patient_name"),
            it.get("patient_mobile"),
            it.get("slot_date"),
            it.get("slot_time"),
            it.get("insurance_provider"),
            it.get("insurance_plan"),
            it.get("insurance_member_id"),
            it.get("insurance_policy_number"),
            it.get("insurance_expiry"),
            it.get("insurance_status"),
            it.get("insurance_verified"),
            it.get("status"),
            it.get("created_at"),
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="appointments_export.csv"'
        },
    )


@router.get("/admin/patients/export.csv")
async def admin_export_patients_csv(
    tenant_id: str = "",
    q: str = "",
    is_active: str = "ALL",
    limit: int = 5000,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    tenant_id = (tenant_id or "").strip()
    q = (q or "").strip()
    is_active = (is_active or "ALL").strip().upper()

    if is_active not in {"ALL", "ACTIVE", "INACTIVE"}:
        raise HTTPException(status_code=400, detail="invalid is_active")

    if limit < 1:
        limit = 1
    if limit > 10000:
        limit = 10000

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    p.id,
                    p.tenant_id,
                    t.clinic_name,
                    p.patient_id,
                    p.full_name,
                    p.mobile,
                    p.national_id,
                    p.date_of_birth,
                    p.gender,
                    p.address,
                    p.insurance_provider,
                    p.insurance_plan,
                    p.insurance_member_id,
                    p.insurance_policy_number,
                    p.insurance_expiry,
                    p.insurance_status,
                    p.insurance_verified,
                    p.is_active,
                    p.created_at,
                    p.updated_at,
                    COALESCE(a_stats.total_appointments, 0) AS total_appointments,
                    a_stats.last_appointment_date
                FROM patients p
                LEFT JOIN tenants t
                  ON t.tenant_id = p.tenant_id
                LEFT JOIN (
                    SELECT
                        tenant_id,
                        patient_id,
                        COUNT(*)::int AS total_appointments,
                        MAX(appt_date) AS last_appointment_date
                    FROM appointments
                    WHERE patient_id IS NOT NULL
                      AND patient_id <> ''
                    GROUP BY tenant_id, patient_id
                ) a_stats
                  ON a_stats.tenant_id = p.tenant_id
                 AND a_stats.patient_id = p.patient_id
                WHERE (:tenant_id = '' OR p.tenant_id = :tenant_id)
                  AND (
                        :is_active = 'ALL'
                        OR (:is_active = 'ACTIVE' AND p.is_active = TRUE)
                        OR (:is_active = 'INACTIVE' AND p.is_active = FALSE)
                      )
                  AND (
                        :q = ''
                        OR COALESCE(p.patient_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.full_name, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.mobile, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.national_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.insurance_provider, '') ILIKE '%' || :q || '%'
                        OR COALESCE(p.insurance_member_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY p.updated_at DESC, p.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "q": q,
                "is_active": is_active,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "row_id",
        "tenant_id",
        "clinic_name",
        "patient_id",
        "full_name",
        "mobile",
        "national_id",
        "date_of_birth",
        "gender",
        "address",
        "insurance_provider",
        "insurance_plan",
        "insurance_member_id",
        "insurance_policy_number",
        "insurance_expiry",
        "insurance_status",
        "insurance_verified",
        "is_active",
        "total_appointments",
        "last_appointment_date",
        "created_at",
        "updated_at",
    ])

    for it in items:
        writer.writerow([
            it.get("id"),
            it.get("tenant_id"),
            it.get("clinic_name"),
            it.get("patient_id"),
            it.get("full_name"),
            it.get("mobile"),
            it.get("national_id"),
            it.get("date_of_birth"),
            it.get("gender"),
            it.get("address"),
            it.get("insurance_provider"),
            it.get("insurance_plan"),
            it.get("insurance_member_id"),
            it.get("insurance_policy_number"),
            it.get("insurance_expiry"),
            it.get("insurance_status"),
            it.get("insurance_verified"),
            it.get("is_active"),
            it.get("total_appointments"),
            it.get("last_appointment_date"),
            it.get("created_at"),
            it.get("updated_at"),
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="patients_export.csv"'
        },
    )


@router.get("/admin/patients/history/export.csv")
async def admin_export_patient_history_csv(
    q: str = "",
    limit: int = 1000,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):


    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q required")

    if limit < 1:
        limit = 1
    if limit > 5000:
        limit = 5000

    async with AsyncSessionLocal() as db:

        await ensure_tenant_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    a.tenant_id,
                    t.clinic_name,
                    a.patient_id,
                    a.doctor_key,
                    a.patient_name,
                    a.patient_mobile,
                    a.appt_date AS slot_date,
                    a.appt_time AS slot_time,
                    a.insurance_provider,
                    a.insurance_plan,
                    a.insurance_member_id,
                    a.insurance_policy_number,
                    a.insurance_expiry,
                    a.insurance_status,
                    a.insurance_verified,
                    a.status,
                    a.created_at
                FROM appointments a
                LEFT JOIN tenants t
                  ON t.tenant_id = a.tenant_id
                WHERE
                    COALESCE(a.patient_id, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.patient_name, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.patient_mobile, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.insurance_provider, '') ILIKE '%' || :q || '%'
                    OR COALESCE(a.insurance_member_id, '') ILIKE '%' || :q || '%'
                ORDER BY
                    COALESCE(a.patient_mobile, '') ASC,
                    COALESCE(a.patient_name, '') ASC,
                    a.appt_date DESC,
                    a.appt_time DESC,
                    a.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "appointment_row_id",
        "tenant_id",
        "clinic_name",
        "patient_id",
        "doctor_key",
        "patient_name",
        "patient_mobile",
        "slot_date",
        "slot_time",
        "insurance_provider",
        "insurance_plan",
        "insurance_member_id",
        "insurance_policy_number",
        "insurance_expiry",
        "insurance_status",
        "insurance_verified",
        "status",
        "created_at",
    ])

    for it in items:
        writer.writerow([
            it.get("id"),
            it.get("tenant_id"),
            it.get("clinic_name"),
            it.get("patient_id"),
            it.get("doctor_key"),
            it.get("patient_name"),
            it.get("patient_mobile"),
            it.get("slot_date"),
            it.get("slot_time"),
            it.get("insurance_provider"),
            it.get("insurance_plan"),
            it.get("insurance_member_id"),
            it.get("insurance_policy_number"),
            it.get("insurance_expiry"),
            it.get("insurance_status"),
            it.get("insurance_verified"),
            it.get("status"),
            it.get("created_at"),
        ])

    output.seek(0)

    safe_name = q.replace('"', "").replace("/", "_").replace("\\", "_").replace(" ", "_")
    filename = f"patient_history_{safe_name or 'export'}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )
@router.get("/admin/audit-logs")
async def admin_audit_logs(
    request: Request,
    tenant_id: str = "",
    action: str = "",
    limit: int = 50,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        await require_platform_api_access(request, db, x_admin_token)

        tenant_id = (tenant_id or "").strip()
        action = (action or "").strip()

        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500

        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    tenant_id,
                    actor_user_id,
                    actor_email,
                    role_code,
                    action,
                    entity_type,
                    entity_id,
                    metadata,
                    created_at
                FROM audit_logs
                WHERE (:tenant_id = '' OR tenant_id = :tenant_id)
                  AND (:action = '' OR action = :action)
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "action": action,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}
print(
    "[tenant_admin_api] routes with audit:",
    [r.path for r in router.routes if "audit" in r.path]
)
