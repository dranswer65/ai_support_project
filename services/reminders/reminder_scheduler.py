from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_TZ = "Asia/Riyadh"


def _safe_tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _parse_appt_local(
    appt_date: str | None,
    appt_time: str | None,
    tz_name: str | None,
) -> datetime | None:
    d = str(appt_date or "").strip()
    t = str(appt_time or "").strip()[:5]

    if not d or not t:
        return None

    try:
        naive = datetime.fromisoformat(f"{d} {t}")
        return naive.replace(tzinfo=_safe_tz(tz_name))
    except Exception:
        return None


def _window_matches(
    *,
    now_local: datetime,
    appt_local: datetime,
    hours_before: int,
    tolerance_minutes: int = 10,
) -> bool:
    diff = appt_local - now_local
    low = timedelta(hours=hours_before, minutes=-tolerance_minutes)
    high = timedelta(hours=hours_before, minutes=tolerance_minutes)
    return low <= diff <= high


async def _already_sent(
    db: AsyncSession,
    *,
    appointment_id: int,
    reminder_type: str,
) -> bool:
    res = await db.execute(
        text(
            """
            SELECT 1
            FROM reminder_logs
            WHERE appointment_id = :appointment_id
              AND reminder_type = :reminder_type
            LIMIT 1;
            """
        ),
        {
            "appointment_id": appointment_id,
            "reminder_type": reminder_type,
        },
    )
    return bool(res.first())


async def _log_reminder(
    db: AsyncSession,
    *,
    tenant_id: str,
    appointment_id: int,
    reminder_type: str,
    status: str = "PENDING",
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO reminder_logs (
                tenant_id,
                appointment_id,
                reminder_type,
                sent_at,
                status
            )
            VALUES (
                :tenant_id,
                :appointment_id,
                :reminder_type,
                NOW(),
                :status
            )
            ON CONFLICT (appointment_id, reminder_type)
            DO NOTHING;
            """
        ),
        {
            "tenant_id": tenant_id,
            "appointment_id": appointment_id,
            "reminder_type": reminder_type,
            "status": status,
        },
    )


async def get_due_reminders(
    db: AsyncSession,
    *,
    include_two_hour_reminder: bool = True,
) -> List[Dict[str, Any]]:
    """
    Returns appointment reminder jobs that should be sent now.

    Each item contains:
      - appointment_id
      - tenant_id
      - patient_name
      - patient_mobile
      - doctor_key
      - appt_date
      - appt_time
      - clinic_name
      - timezone
      - language
      - reminder_hours_before
      - reminder_type
    """
    res = await db.execute(
        text(
            """
            SELECT
                a.id AS appointment_id,
                a.tenant_id,
                a.patient_name,
                a.patient_mobile,
                a.doctor_key,
                a.appt_date,
                a.appt_time,
                a.status,

                COALESCE(ts.clinic_name, t.clinic_name, a.tenant_id) AS clinic_name,
                COALESCE(ts.timezone, t.timezone, :default_tz) AS timezone,
                COALESCE(ts.language, t.default_language, 'en') AS language,
                COALESCE(ts.reminder_hours_before, 24) AS reminder_hours_before

            FROM appointments a
            LEFT JOIN tenants t
              ON t.tenant_id = a.tenant_id
            LEFT JOIN tenant_settings ts
              ON ts.tenant_id = a.tenant_id

            WHERE a.status = 'CONFIRMED'
              AND COALESCE(a.patient_mobile, '') <> ''
            ORDER BY a.tenant_id ASC, a.appt_date ASC, a.appt_time ASC;
            """
        ),
        {"default_tz": DEFAULT_TZ},
    )

    rows = [dict(r) for r in res.mappings().all()]
    due_jobs: List[Dict[str, Any]] = []

    for row in rows:
        appointment_id = int(row["appointment_id"])
        tenant_id = str(row.get("tenant_id") or "").strip()
        tz_name = str(row.get("timezone") or DEFAULT_TZ).strip()
        reminder_hours_before = int(row.get("reminder_hours_before") or 24)

        appt_local = _parse_appt_local(
            row.get("appt_date"),
            row.get("appt_time"),
            tz_name,
        )
        if not appt_local:
            continue

        now_local = datetime.now(_safe_tz(tz_name))

        # Main reminder
        if _window_matches(
            now_local=now_local,
            appt_local=appt_local,
            hours_before=reminder_hours_before,
        ):
            already = await _already_sent(
                db,
                appointment_id=appointment_id,
                reminder_type="REMINDER_MAIN",
            )
            if not already:
                job = dict(row)
                job["reminder_type"] = "REMINDER_MAIN"
                due_jobs.append(job)
                continue

        # Optional 2-hour reminder
        if include_two_hour_reminder:
            if _window_matches(
                now_local=now_local,
                appt_local=appt_local,
                hours_before=2,
            ):
                already = await _already_sent(
                    db,
                    appointment_id=appointment_id,
                    reminder_type="REMINDER_2H",
                )
                if not already:
                    job = dict(row)
                    job["reminder_type"] = "REMINDER_2H"
                    due_jobs.append(job)

    return due_jobs


async def mark_reminder_scheduled(
    db: AsyncSession,
    *,
    tenant_id: str,
    appointment_id: int,
    reminder_type: str,
) -> bool:
    """
    Claim reminder work once. Returns True if this call won the claim.
    """
    exists = await _already_sent(
        db,
        appointment_id=appointment_id,
        reminder_type=reminder_type,
    )
    if exists:
        return False

    await _log_reminder(
        db,
        tenant_id=tenant_id,
        appointment_id=appointment_id,
        reminder_type=reminder_type,
        status="PENDING",
    )
    return True


async def mark_reminder_sent(
    db: AsyncSession,
    *,
    appointment_id: int,
    reminder_type: str,
) -> None:
    await db.execute(
        text(
            """
            UPDATE reminder_logs
            SET
                status = 'SENT',
                sent_at = NOW()
            WHERE appointment_id = :appointment_id
              AND reminder_type = :reminder_type;
            """
        ),
        {
            "appointment_id": appointment_id,
            "reminder_type": reminder_type,
        },
    )


async def mark_reminder_failed(
    db: AsyncSession,
    *,
    appointment_id: int,
    reminder_type: str,
    error_text: str | None = None,
) -> None:
    await db.execute(
        text(
            """
            UPDATE reminder_logs
            SET
                status = 'FAILED',
                error_text = :error_text,
                sent_at = NOW()
            WHERE appointment_id = :appointment_id
              AND reminder_type = :reminder_type;
            """
        ),
        {
            "appointment_id": appointment_id,
            "reminder_type": reminder_type,
            "error_text": (error_text or "")[:1000],
        },
    )