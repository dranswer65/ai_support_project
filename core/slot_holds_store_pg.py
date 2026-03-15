# core/slot_holds_store_pg.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(d: str) -> Optional[date]:
    """
    Accepts 'YYYY-MM-DD' and returns datetime.date.
    Returns None if invalid.
    """
    try:
        return date.fromisoformat((d or "").strip())
    except Exception:
        return None


async def cleanup_expired_holds(db: AsyncSession, tenant_id: str) -> int:
    tenant_id = (tenant_id or "").strip() or "default"

    # Expire holds
    res = await db.execute(
        text("""
            UPDATE slot_holds
            SET status = 'EXPIRED',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND status = 'HELD'
              AND expires_at <= NOW()
            RETURNING doctor_key, slot_date, slot_time;
        """),
        {"tenant_id": tenant_id},
    )
    expired = res.mappings().all()

    # Release slots back to OPEN (only if still HELD)
    for r in expired:
        await db.execute(
            text("""
                UPDATE appointment_slots
                SET status = 'OPEN',
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = :slot_date
                  AND slot_time = :slot_time
                  AND status = 'HELD';
            """),
            {
                "tenant_id": tenant_id,
                "doctor_key": r["doctor_key"],
                "slot_date": r["slot_date"],   # already a DATE from DB
                "slot_time": r["slot_time"],
            },
        )

    return len(expired)


async def create_slot_hold(
    *,
    db: AsyncSession,
    tenant_id: str,
    doctor_key: str,
    slot_date: str,   # "YYYY-MM-DD"
    slot_time: str,   # "HH:MM"
    user_id: str,
    hold_minutes: int = 2,
) -> Dict[str, Any]:
    tenant_id = (tenant_id or "").strip() or "default"
    doctor_key = (doctor_key or "").strip()
    slot_time = (slot_time or "").strip()
    user_id = (user_id or "").strip()

    d = _parse_date(slot_date)
    if not d:
        return {"ok": False, "reason": "invalid_slot_date"}

    if not doctor_key or not slot_time or not user_id:
        return {"ok": False, "reason": "missing_fields"}

    if hold_minutes < 1:
        hold_minutes = 1
    if hold_minutes > 10:
        hold_minutes = 10

    await cleanup_expired_holds(db, tenant_id)

    # Ensure slot exists and is OPEN
    res = await db.execute(
        text("""
            SELECT status
            FROM appointment_slots
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
            LIMIT 1;
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": d,          # pass a real DATE object
            "slot_time": slot_time,
        },
    )
    row = res.mappings().first()
    if not row:
        return {"ok": False, "reason": "slot_not_found"}

    if str(row["status"]).upper() != "OPEN":
        return {"ok": False, "reason": f"slot_not_open:{row['status']}"}

    hold_id = f"HOLD-{uuid.uuid4().hex[:12].upper()}"
    expires_at = _utcnow() + timedelta(minutes=hold_minutes)

    # Insert hold (partial unique index prevents concurrent active holds)
    try:
        await db.execute(
            text("""
                INSERT INTO slot_holds
                    (tenant_id, hold_id, doctor_key, slot_date, slot_time, user_id, status, expires_at, created_at, updated_at)
                VALUES
                    (:tenant_id, :hold_id, :doctor_key, :slot_date, :slot_time, :user_id, 'HELD', :expires_at, NOW(), NOW());
            """),
            {
                "tenant_id": tenant_id,
                "hold_id": hold_id,
                "doctor_key": doctor_key,
                "slot_date": d,
                "slot_time": slot_time,
                "user_id": user_id,
                "expires_at": expires_at,
            },
        )
    except Exception:
        # Usually unique partial index uq_active_hold_per_slot hits
        return {"ok": False, "reason": "slot_already_held"}

    # Mark slot HELD (only if OPEN)
    await db.execute(
        text("""
            UPDATE appointment_slots
            SET status = 'HELD',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
              AND status = 'OPEN';
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "slot_date": d,
            "slot_time": slot_time,
        },
    )

    await db.commit()
    return {"ok": True, "hold_id": hold_id, "expires_at": expires_at.isoformat()}


async def release_slot_hold(
    *,
    db: AsyncSession,
    tenant_id: str,
    hold_id: str,
) -> Dict[str, Any]:
    tenant_id = (tenant_id or "").strip() or "default"
    hold_id = (hold_id or "").strip()

    res = await db.execute(
        text("""
            SELECT doctor_key, slot_date, slot_time, status
            FROM slot_holds
            WHERE tenant_id = :tenant_id AND hold_id = :hold_id
            LIMIT 1;
        """),
        {"tenant_id": tenant_id, "hold_id": hold_id},
    )
    hold = res.mappings().first()
    if not hold:
        return {"ok": False, "reason": "hold_not_found"}

    if str(hold["status"]).upper() != "HELD":
        return {"ok": False, "reason": f"hold_not_active:{hold['status']}"}

    await db.execute(
        text("""
            UPDATE slot_holds
            SET status = 'RELEASED',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id AND hold_id = :hold_id;
        """),
        {"tenant_id": tenant_id, "hold_id": hold_id},
    )

    await db.execute(
        text("""
            UPDATE appointment_slots
            SET status = 'OPEN',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
              AND status = 'HELD';
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": hold["doctor_key"],
            "slot_date": hold["slot_date"],  # DATE from DB
            "slot_time": hold["slot_time"],
        },
    )

    await db.commit()
    return {"ok": True, "hold_id": hold_id}


async def confirm_hold_create_appointment(
    *,
    db: AsyncSession,
    tenant_id: str,
    hold_id: str,
    patient_name: Optional[str] = None,
    patient_mobile: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    tenant_id = (tenant_id or "").strip() or "default"
    hold_id = (hold_id or "").strip()

    # Load hold
    res = await db.execute(
        text("""
            SELECT doctor_key, slot_date, slot_time, user_id, status, expires_at
            FROM slot_holds
            WHERE tenant_id = :tenant_id AND hold_id = :hold_id
            LIMIT 1;
        """),
        {"tenant_id": tenant_id, "hold_id": hold_id},
    )
    hold = res.mappings().first()
    if not hold:
        return {"ok": False, "reason": "hold_not_found"}

    if str(hold["status"]).upper() != "HELD":
        return {"ok": False, "reason": f"hold_not_active:{hold['status']}"}

    if hold["expires_at"] <= _utcnow():
        # expire and reopen slot
        await db.execute(
            text("""
                UPDATE slot_holds
                SET status = 'EXPIRED', updated_at = NOW()
                WHERE tenant_id = :tenant_id AND hold_id = :hold_id;
            """),
            {"tenant_id": tenant_id, "hold_id": hold_id},
        )
        await db.execute(
            text("""
                UPDATE appointment_slots
                SET status = 'OPEN', updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND slot_date = :slot_date
                  AND slot_time = :slot_time
                  AND status = 'HELD';
            """),
            {
                "tenant_id": tenant_id,
                "doctor_key": hold["doctor_key"],
                "slot_date": hold["slot_date"],
                "slot_time": hold["slot_time"],
            },
        )
        await db.commit()
        return {"ok": False, "reason": "hold_expired"}

    # Slot must still be HELD
    res2 = await db.execute(
        text("""
            SELECT status
            FROM appointment_slots
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
            LIMIT 1;
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": hold["doctor_key"],
            "slot_date": hold["slot_date"],
            "slot_time": hold["slot_time"],
        },
    )
    slot = res2.mappings().first()
    if not slot or str(slot["status"]).upper() != "HELD":
        return {"ok": False, "reason": "slot_not_held_anymore"}

    from core.saas_limits import enforce_appointment_limit
    await enforce_appointment_limit(db, tenant_id)
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"

    # confirm hold
    await db.execute(
        text("""
            UPDATE slot_holds
            SET status = 'CONFIRMED',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id AND hold_id = :hold_id;
        """),
        {"tenant_id": tenant_id, "hold_id": hold_id},
    )

    # slot -> BOOKED
    await db.execute(
        text("""
            UPDATE appointment_slots
            SET status = 'BOOKED',
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND slot_date = :slot_date
              AND slot_time = :slot_time
              AND status = 'HELD';
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": hold["doctor_key"],
            "slot_date": hold["slot_date"],
            "slot_time": hold["slot_time"],
        },
    )

    # create appointment row
    # IMPORTANT:
    # appointments table uses appt_date / appt_time, not slot_date / slot_time
    await db.execute(
        text("""
            INSERT INTO appointments
                (
                    tenant_id,
                    appointment_id,
                    hold_id,
                    doctor_key,
                    appt_date,
                    appt_time,
                    user_id,
                    patient_name,
                    patient_mobile,
                    notes,
                    status,
                    created_at,
                    updated_at
                )
            VALUES
                (
                    :tenant_id,
                    :appointment_id,
                    :hold_id,
                    :doctor_key,
                    :appt_date,
                    :appt_time,
                    :user_id,
                    :patient_name,
                    :patient_mobile,
                    :notes,
                    'CONFIRMED',
                    NOW(),
                    NOW()
                );
        """),
        {
            "tenant_id": tenant_id,
            "appointment_id": appointment_id,
            "hold_id": hold_id,
            "doctor_key": hold["doctor_key"],
            "appt_date": str(hold["slot_date"]),
            "appt_time": str(hold["slot_time"])[:5],
            "user_id": hold["user_id"],
            "patient_name": patient_name,
            "patient_mobile": patient_mobile,
            "notes": notes,
        },
    )

    from core.usage_logger import log_usage_event

    await log_usage_event(
        db,
        tenant_id,
        "appointments_created",
        1
    )


    await db.commit()
    return {"ok": True, "appointment_id": appointment_id}
