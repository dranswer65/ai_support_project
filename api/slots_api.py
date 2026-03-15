from __future__ import annotations

import os
import base64
import json
from datetime import datetime, timedelta, date as date_cls
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Header, Query
from sqlalchemy import text

from database import AsyncSessionLocal
from core.slot_generator import generate_slots, DEFAULT_TZ as GEN_DEFAULT_TZ
from core.slot_schema import ensure_slot_tables
from core.slot_holds_store_pg import (
    cleanup_expired_holds,
    create_slot_hold,
    release_slot_hold,
    confirm_hold_create_appointment,
)
from core.tenant_auth import resolve_and_validate_tenant_admin

router = APIRouter()

WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()
DEFAULT_TZ = (os.getenv("CLINIC_TZ", "Asia/Riyadh") or "Asia/Riyadh").strip()


# =========================================================
# HELPERS
# =========================================================
def _parse_date_yyyy_mm_dd(s: Optional[str]) -> Optional[date_cls]:
    try:
        if not s:
            return None
        return date_cls.fromisoformat((s or "").strip())
    except Exception:
        return None


def _clinic_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def _normalize_slot_time_str(slot_time_val: Any) -> str:
    t = str(slot_time_val or "").strip()
    return t[:5] if len(t) >= 5 else t


def _encode_cursor(last_date: date_cls, last_time: str) -> str:
    payload = {"d": last_date.isoformat(), "t": last_time}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> Optional[Tuple[date_cls, str]]:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + pad).encode("utf-8"))
        obj = json.loads(raw.decode("utf-8"))
        d = date_cls.fromisoformat(str(obj["d"]))
        t = str(obj["t"])[:5]
        return d, t
    except Exception:
        return None


# =========================================================
# ADMIN: ENSURE TABLES
# =========================================================
@router.post("/admin/slots/ensure")
async def admin_ensure_slot_tables(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

    return {"ok": True, "tenant_id": tenant_id}


# =========================================================
# ADMIN: DOCTORS
# =========================================================
@router.post("/admin/doctors/upsert")
async def admin_doctors_upsert(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    doctor_key = str(payload.get("doctor_key") or "").strip()
    doctor_name = str(payload.get("doctor_name") or "").strip()

    if not doctor_key or not doctor_name:
        raise HTTPException(status_code=400, detail="doctor_key and doctor_name required")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        await db.execute(
            text(
                """
                INSERT INTO doctors
                    (
                        tenant_id, doctor_key, doctor_name,
                        specialty_key, specialty_label,
                        is_active, created_at, updated_at
                    )
                VALUES
                    (
                        :tenant_id, :doctor_key, :doctor_name,
                        :specialty_key, :specialty_label,
                        :is_active, NOW(), NOW()
                    )
                ON CONFLICT (tenant_id, doctor_key)
                DO UPDATE SET
                    doctor_name = EXCLUDED.doctor_name,
                    specialty_key = EXCLUDED.specialty_key,
                    specialty_label = EXCLUDED.specialty_label,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW();
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "doctor_name": doctor_name,
                "specialty_key": payload.get("specialty_key"),
                "specialty_label": payload.get("specialty_label"),
                "is_active": bool(payload.get("is_active", True)),
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "doctor_key": doctor_key}


@router.get("/admin/doctors/list")
async def admin_doctors_list(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    doctor_key,
                    doctor_name,
                    specialty_key,
                    specialty_label,
                    is_active,
                    updated_at
                FROM doctors
                WHERE tenant_id = :tenant_id
                ORDER BY doctor_name ASC;
                """
            ),
            {"tenant_id": tenant_id},
        )
        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "tenant_id": tenant_id, "items": items}


# =========================================================
# ADMIN: SCHEDULE RULES
# =========================================================
@router.post("/admin/schedule_rules/upsert")
async def admin_schedule_rules_upsert(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    doctor_key = str(payload.get("doctor_key") or "").strip()
    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")

    try:
        dow = int(payload.get("day_of_week"))
    except Exception:
        raise HTTPException(status_code=400, detail="day_of_week must be int 0..6")

    start_time = str(payload.get("start_time") or "").strip()
    end_time = str(payload.get("end_time") or "").strip()
    if not start_time or not end_time:
        raise HTTPException(status_code=400, detail="start_time and end_time required")

    slot_minutes = int(payload.get("slot_minutes") or 15)
    if slot_minutes < 5 or slot_minutes > 60:
        raise HTTPException(status_code=400, detail="slot_minutes must be between 5 and 60")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        chk = await db.execute(
            text(
                """
                SELECT 1
                FROM doctors
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id, "doctor_key": doctor_key},
        )
        if not chk.first():
            raise HTTPException(status_code=400, detail="Doctor not found. Create doctor first via /admin/doctors/upsert")

        await db.execute(
            text(
                """
                INSERT INTO doctor_schedule_rules
                    (
                        tenant_id, doctor_key, day_of_week,
                        start_time, end_time, slot_minutes,
                        is_active, created_at, updated_at
                    )
                VALUES
                    (
                        :tenant_id, :doctor_key, :day_of_week,
                        :start_time, :end_time, :slot_minutes,
                        :is_active, NOW(), NOW()
                    )
                ON CONFLICT (tenant_id, doctor_key, day_of_week, start_time, end_time)
                DO UPDATE SET
                    slot_minutes = EXCLUDED.slot_minutes,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW();
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "day_of_week": dow,
                "start_time": start_time,
                "end_time": end_time,
                "slot_minutes": slot_minutes,
                "is_active": bool(payload.get("is_active", True)),
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "doctor_key": doctor_key}


@router.get("/admin/schedule_rules/list")
async def admin_schedule_rules_list(
    doctor_key: Optional[str] = Query(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        if doctor_key:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        day_of_week,
                        start_time,
                        end_time,
                        slot_minutes,
                        is_active,
                        updated_at
                    FROM doctor_schedule_rules
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                    ORDER BY day_of_week, start_time;
                    """
                ),
                {"tenant_id": tenant_id, "doctor_key": doctor_key},
            )
        else:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        day_of_week,
                        start_time,
                        end_time,
                        slot_minutes,
                        is_active,
                        updated_at
                    FROM doctor_schedule_rules
                    WHERE tenant_id = :tenant_id
                    ORDER BY doctor_key, day_of_week, start_time;
                    """
                ),
                {"tenant_id": tenant_id},
            )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "tenant_id": tenant_id, "items": items}


# =========================================================
# ADMIN: TIME OFF
# =========================================================
@router.post("/admin/timeoff/add")
async def admin_timeoff_add(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    doctor_key = str(payload.get("doctor_key") or "").strip()
    starts_at = str(payload.get("starts_at") or "").strip()
    ends_at = str(payload.get("ends_at") or "").strip()

    if not doctor_key or not starts_at or not ends_at:
        raise HTTPException(status_code=400, detail="doctor_key, starts_at, ends_at required")

    try:
        sdt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        edt = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="starts_at/ends_at must be ISO datetime with timezone")

    if edt <= sdt:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        await db.execute(
            text(
                """
                INSERT INTO doctor_time_off
                    (tenant_id, doctor_key, starts_at, ends_at, reason, created_at)
                VALUES
                    (:tenant_id, :doctor_key, :starts_at, :ends_at, :reason, NOW());
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "starts_at": sdt,
                "ends_at": edt,
                "reason": payload.get("reason"),
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "doctor_key": doctor_key}


@router.get("/admin/timeoff/list")
async def admin_timeoff_list(
    doctor_key: Optional[str] = Query(default=None),
    limit: int = 200,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        if doctor_key:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        starts_at,
                        ends_at,
                        reason,
                        created_at
                    FROM doctor_time_off
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                    ORDER BY starts_at DESC
                    LIMIT :limit;
                    """
                ),
                {"tenant_id": tenant_id, "doctor_key": doctor_key, "limit": limit},
            )
        else:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        starts_at,
                        ends_at,
                        reason,
                        created_at
                    FROM doctor_time_off
                    WHERE tenant_id = :tenant_id
                    ORDER BY starts_at DESC
                    LIMIT :limit;
                    """
                ),
                {"tenant_id": tenant_id, "limit": limit},
            )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "tenant_id": tenant_id, "items": items}


# =========================================================
# ADMIN: GENERATE + LIST SLOTS
# =========================================================
@router.post("/admin/slots/generate")
async def admin_generate_slots(
    days_ahead: int = 14,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        stats = await generate_slots(
            db=db,
            tenant_id=tenant_id,
            tz_name=os.getenv("CLINIC_TZ", GEN_DEFAULT_TZ),
            days_ahead=days_ahead,
        )

    return {"ok": True, "tenant_id": tenant_id, "stats": stats}


@router.get("/admin/slots/list")
async def admin_slots_list(
    doctor_key: Optional[str] = Query(default=None),
    limit: int = 50,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        if doctor_key:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        slot_date,
                        slot_time,
                        status,
                        updated_at
                    FROM appointment_slots
                    WHERE tenant_id = :tenant_id
                      AND doctor_key = :doctor_key
                    ORDER BY slot_date ASC, slot_time ASC
                    LIMIT :limit;
                    """
                ),
                {"tenant_id": tenant_id, "doctor_key": doctor_key, "limit": limit},
            )
        else:
            res = await db.execute(
                text(
                    """
                    SELECT
                        doctor_key,
                        slot_date,
                        slot_time,
                        status,
                        updated_at
                    FROM appointment_slots
                    WHERE tenant_id = :tenant_id
                    ORDER BY slot_date ASC, slot_time ASC
                    LIMIT :limit;
                    """
                ),
                {"tenant_id": tenant_id, "limit": limit},
            )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "tenant_id": tenant_id, "items": items}


# =========================================================
# PUBLIC: /slots/available
# =========================================================
@router.get("/slots/available")
async def slots_available(
    doctor_key: str = Query(..., description="doctor_key, e.g. cardio_1"),
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    days_ahead: int = Query(default=14, ge=1, le=60),
    page_size: int = Query(default=20, ge=1, le=60),
    next_cursor: Optional[str] = Query(default=None, description="Opaque cursor from previous response"),
    tz_name: Optional[str] = Query(default=None, description="IANA tz e.g. Asia/Riyadh"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
):
    doctor_key = (doctor_key or "").strip()
    if not doctor_key:
        raise HTTPException(status_code=400, detail="doctor_key required")

    tenant_id = (x_tenant_id or WA_DEFAULT_CLIENT or "default").strip()

    tz_final = (tz_name or os.getenv("CLINIC_TZ", DEFAULT_TZ) or DEFAULT_TZ).strip()
    try:
        tz = ZoneInfo(tz_final)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid tz_name")

    now_local = datetime.now(tz)
    today_local = now_local.date()
    now_time_str = now_local.strftime("%H:%M")

    df = _parse_date_yyyy_mm_dd(date_from)
    dt = _parse_date_yyyy_mm_dd(date_to)

    if df and not dt:
        dt = df
    if dt and not df:
        df = today_local

    if not df and not dt:
        df = today_local
        dt = today_local + timedelta(days=days_ahead)

    if not df or not dt:
        raise HTTPException(status_code=400, detail="invalid date_from/date_to")
    if dt < df:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")

    cursor_pair = _decode_cursor(next_cursor or "")
    cursor_date: Optional[date_cls] = None
    cursor_time: Optional[str] = None
    if next_cursor:
        if not cursor_pair:
            raise HTTPException(status_code=400, detail="invalid next_cursor")
        cursor_date, cursor_time = cursor_pair

    async with AsyncSessionLocal() as db:
        await ensure_slot_tables(db)

        await cleanup_expired_holds(db, tenant_id)
        await db.commit()

        chk = await db.execute(
            text(
                """
                SELECT 1
                FROM doctors
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id, "doctor_key": doctor_key},
        )
        if not chk.first():
            raise HTTPException(status_code=404, detail="doctor_not_found")

        keyset_sql = ""
        params: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "date_from": df,
            "date_to": dt,
            "today": today_local,
            "now_time": now_time_str,
            "tz_name": tz_final,
            "slot_minutes": 15,
            "page_size_plus": page_size + 1,
        }

        if cursor_date and cursor_time:
            keyset_sql = " AND (s.slot_date, s.slot_time) > (:cursor_date, :cursor_time) "
            params["cursor_date"] = cursor_date
            params["cursor_time"] = cursor_time

        res = await db.execute(
            text(
                f"""
                SELECT s.slot_date, s.slot_time
                FROM appointment_slots s
                WHERE s.tenant_id = :tenant_id
                  AND s.doctor_key = :doctor_key
                  AND s.status = 'OPEN'
                  AND s.slot_date >= :date_from
                  AND s.slot_date <= :date_to
                  AND (
                        s.slot_date > :today
                        OR (s.slot_date = :today AND s.slot_time >= :now_time)
                      )
                  {keyset_sql}
                  AND NOT EXISTS (
                        SELECT 1
                        FROM slot_holds h
                        WHERE h.tenant_id = s.tenant_id
                          AND h.doctor_key = s.doctor_key
                          AND h.slot_date = s.slot_date
                          AND h.slot_time = s.slot_time
                          AND h.status = 'HELD'
                          AND h.expires_at > NOW()
                      )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM doctor_time_off t
                        WHERE t.tenant_id = s.tenant_id
                          AND t.doctor_key = s.doctor_key
                          AND (
                                (((s.slot_date + (s.slot_time::time)) AT TIME ZONE :tz_name) < t.ends_at)
                            AND ((((s.slot_date + (s.slot_time::time)) AT TIME ZONE :tz_name)
                                  + (:slot_minutes * INTERVAL '1 minute')) > t.starts_at)
                              )
                      )
                ORDER BY s.slot_date ASC, s.slot_time ASC
                LIMIT :page_size_plus;
                """
            ),
            params,
        )
        rows = res.mappings().all()

    has_more = len(rows) > page_size
    page_rows = rows[:page_size]

    items: List[Dict[str, str]] = []
    for r in page_rows:
        sd: date_cls = r["slot_date"]
        st = _normalize_slot_time_str(r["slot_time"])
        items.append({"slot_date": sd.isoformat(), "slot_time": st})

    out_cursor: Optional[str] = None
    if has_more and items:
        last = items[-1]
        out_cursor = _encode_cursor(date_cls.fromisoformat(last["slot_date"]), last["slot_time"])

    by_date: Dict[str, List[str]] = {}
    for it in items:
        by_date.setdefault(it["slot_date"], []).append(it["slot_time"])

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "tz": tz_final,
        "date_from": df.isoformat(),
        "date_to": dt.isoformat(),
        "page_size": page_size,
        "returned": len(items),
        "has_more": has_more,
        "next_cursor": out_cursor,
        "by_date": [{"date": d, "times": by_date[d]} for d in sorted(by_date.keys())],
        "items": items,
    }


# =========================================================
# ADMIN: HOLDS
# =========================================================
@router.post("/admin/holds/cleanup")
async def admin_holds_cleanup(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        expired = await cleanup_expired_holds(db, tenant_id)
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "expired": expired}


@router.post("/admin/holds/create")
async def admin_hold_create(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        out = await create_slot_hold(
            db=db,
            tenant_id=tenant_id,
            doctor_key=str(payload.get("doctor_key") or ""),
            slot_date=str(payload.get("slot_date") or ""),
            slot_time=str(payload.get("slot_time") or ""),
            user_id=str(payload.get("user_id") or ""),
            hold_minutes=int(payload.get("hold_minutes") or 2),
        )

    return out


@router.post("/admin/holds/{hold_id}/release")
async def admin_hold_release(
    hold_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        out = await release_slot_hold(db=db, tenant_id=tenant_id, hold_id=hold_id)

    return out


@router.post("/admin/holds/{hold_id}/confirm")
async def admin_hold_confirm(
    hold_id: str,
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

        out = await confirm_hold_create_appointment(
            db=db,
            tenant_id=tenant_id,
            hold_id=hold_id,
            patient_name=str(payload.get("patient_name") or ""),
            patient_mobile=str(payload.get("patient_mobile") or ""),
            notes=str(payload.get("notes") or ""),
        )

    return out


# =========================================================
# ADMIN: RECEPTION DASHBOARD SUPPORT APIs
# =========================================================
@router.get("/admin/appointment_requests/pending")
async def admin_list_pending_requests(
    limit: int = Query(default=100, ge=1, le=500),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )

        res = await db.execute(
            text(
                """
                SELECT
                    request_id,
                    doctor_key,
                    doctor_label,
                    appt_date,
                    appt_time,
                    patient_name,
                    patient_mobile,
                    created_at
                FROM appointment_requests
                WHERE tenant_id = :tenant_id
                  AND status = 'PENDING'
                ORDER BY created_at ASC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "limit": limit,
            },
        )
        items = [dict(r) for r in res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "count": len(items),
        "items": items,
    }


@router.get("/admin/appointments/today")
async def admin_list_today_appointments(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    tz_name = os.getenv("CLINIC_TZ", DEFAULT_TZ)
    today = datetime.now(ZoneInfo(tz_name)).date()

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )

        res = await db.execute(
            text(
                """
                SELECT
                    doctor_key,
                    patient_name,
                    patient_mobile,
                    slot_date,
                    slot_time,
                    status
                FROM appointments
                WHERE tenant_id = :tenant_id
                  AND slot_date = :today
                ORDER BY slot_time ASC
                """
            ),
            {
                "tenant_id": tenant_id,
                "today": today,
            },
        )
        items = [dict(r) for r in res.mappings().all()]

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "date": str(today),
        "count": len(items),
        "items": items,
    }


@router.post("/admin/appointments/reschedule")
async def admin_reschedule_appointment(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    appointment_id = payload.get("appointment_id")
    new_date = payload.get("slot_date")
    new_time = payload.get("slot_time")

    if not appointment_id:
        raise HTTPException(status_code=400, detail="appointment_id required")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )

        res = await db.execute(
            text(
                """
                SELECT doctor_key
                FROM appointments
                WHERE tenant_id = :tenant_id
                  AND id = :id
                """
            ),
            {"tenant_id": tenant_id, "id": appointment_id},
        )
        appt = res.mappings().first()

        if not appt:
            raise HTTPException(status_code=404, detail="appointment_not_found")

        doctor_key = appt["doctor_key"]

        chk = await db.execute(
            text(
                """
                SELECT status
                FROM appointment_slots
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = :slot_date
                  AND slot_time = :slot_time
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "slot_date": new_date,
                "slot_time": new_time,
            },
        )
        slot = chk.mappings().first()

        if not slot or slot["status"] != "OPEN":
            raise HTTPException(status_code=400, detail="slot_not_available")

        await db.execute(
            text(
                """
                UPDATE appointments
                SET slot_date = :slot_date,
                    slot_time = :slot_time
                WHERE tenant_id = :tenant_id
                  AND id = :id
                """
            ),
            {
                "tenant_id": tenant_id,
                "slot_date": new_date,
                "slot_time": new_time,
                "id": appointment_id,
            },
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id}


@router.post("/admin/appointments/cancel")
async def admin_cancel_appointment(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    appointment_id = payload.get("appointment_id")

    if not appointment_id:
        raise HTTPException(status_code=400, detail="appointment_id required")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )

        await db.execute(
            text(
                """
                UPDATE appointments
                SET status = 'CANCELLED'
                WHERE tenant_id = :tenant_id
                  AND id = :id
                """
            ),
            {"tenant_id": tenant_id, "id": appointment_id},
        )
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id}


@router.post("/admin/appointments/confirm")
async def admin_confirm_appointment(
    payload: Dict[str, Any],
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id required")

    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )
        await ensure_slot_tables(db)

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
            {"tenant_id": tenant_id, "request_id": request_id},
        )
        req = res.mappings().first()

        if not req:
            raise HTTPException(status_code=404, detail="appointment_request_not_found")

        if req["status"] != "PENDING":
            raise HTTPException(status_code=400, detail="request_already_processed")

        doctor_key = req["doctor_key"]
        slot_date = req["appt_date"]
        slot_time = req["appt_time"]

        chk = await db.execute(
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
                "slot_time": slot_time,
            },
        )
        slot = chk.mappings().first()

        if not slot:
            raise HTTPException(status_code=404, detail="slot_not_found")

        if slot["status"] != "OPEN":
            raise HTTPException(status_code=400, detail="slot_not_available")

        await db.execute(
            text(
                """
                INSERT INTO appointments
                (
                    tenant_id,
                    doctor_key,
                    patient_name,
                    patient_mobile,
                    slot_date,
                    slot_time,
                    status,
                    created_at
                )
                VALUES
                (
                    :tenant_id,
                    :doctor_key,
                    :patient_name,
                    :patient_mobile,
                    :slot_date,
                    :slot_time,
                    'CONFIRMED',
                    NOW()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "patient_name": req["patient_name"],
                "patient_mobile": req["patient_mobile"],
                "slot_date": slot_date,
                "slot_time": slot_time,
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
                "slot_time": slot_time,
            },
        )

        await db.execute(
            text(
                """
                UPDATE appointment_requests
                SET status = 'CONFIRMED',
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND request_id = :request_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "status": "CONFIRMED",
    }


# =========================================================
# ADMIN: RESET SLOT TABLES (DEV ONLY)
# =========================================================
@router.post("/admin/reset-slot-system")
async def admin_reset_slot_system(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    async with AsyncSessionLocal() as db:
        tenant_id = await resolve_and_validate_tenant_admin(
            db,
            x_tenant_id=x_tenant_id,
            x_admin_token=x_admin_token,
            fallback_tenant_id=WA_DEFAULT_CLIENT,
        )

        await db.execute(text("DROP TABLE IF EXISTS appointments CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS slot_holds CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS appointment_slots CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS doctor_time_off CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS doctor_schedule_rules CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS doctors CASCADE"))
        await db.commit()

    return {"ok": True, "tenant_id": tenant_id, "message": "slot system reset"}