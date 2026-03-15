from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from database import AsyncSessionLocal
from services.messaging.message_templates import build_appointment_reminder_message


WA_ACCESS_TOKEN = (os.getenv("WA_ACCESS_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
DEFAULT_TZ = (os.getenv("CLINIC_TZ") or "Asia/Riyadh").strip()


def wa_send_text(to_wa_id: str, text_: str):
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        return

    body = (text_ or "").strip()
    if not body:
        return

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": body[:4000]},
    }

    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        requests.post(url, headers=headers, json=payload, timeout=30)
    except Exception:
        pass


def _safe_zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _parse_local_appt_dt(appt_date: str, appt_time: str, tz_name: str) -> datetime | None:
    try:
        hhmm = str(appt_time or "").strip()[:5]
        naive = datetime.fromisoformat(f"{appt_date} {hhmm}")
        return naive.replace(tzinfo=_safe_zoneinfo(tz_name))
    except Exception:
        return None


async def _build_reminder_message(
    db,
    *,
    tenant_id: str,
    clinic_name: str,
    patient_name: str,
    doctor_key: str,
    appt_date: str,
    appt_time: str,
    language: str,
) -> str:
    message = await build_appointment_reminder_message(
        db,
        tenant_id=tenant_id,
        language=language,
        clinic_name=clinic_name,
        patient_name=patient_name,
        doctor_name=doctor_key,
        date=appt_date,
        time=(appt_time or "")[:5],
    )

    return (message or "").strip()


async def _try_claim_reminder(
    db,
    *,
    tenant_id: str,
    appointment_id: int,
    reminder_type: str,
) -> bool:
    """
    Returns True only once per (appointment_id, reminder_type).
    Prevents duplicate reminder sends across worker loops.
    """
    try:
        res = await db.execute(
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
                    'SENT'
                )
                ON CONFLICT (appointment_id, reminder_type)
                DO NOTHING
                RETURNING id;
                """
            ),
            {
                "tenant_id": tenant_id,
                "appointment_id": appointment_id,
                "reminder_type": reminder_type,
            },
        )
        row = res.first()
        return bool(row)
    except Exception:
        return False


async def run_appointment_reminders():
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text(
                """
                SELECT
                    a.id,
                    a.tenant_id,
                    a.patient_name,
                    a.patient_mobile,
                    a.doctor_key,
                    a.appt_date,
                    a.appt_time,

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
                """
            ),
            {"default_tz": DEFAULT_TZ},
        )

        rows = r.mappings().all()

        for appt in rows:
            try:
                appointment_id = int(appt["id"])
                tenant_id = str(appt.get("tenant_id") or "").strip()
                tz_name = str(appt.get("timezone") or DEFAULT_TZ).strip()
                reminder_hours_before = int(appt.get("reminder_hours_before") or 24)

                appt_date = str(appt.get("appt_date") or "")
                appt_time = str(appt.get("appt_time") or "")
                patient_name = str(appt.get("patient_name") or "")
                patient_mobile = str(appt.get("patient_mobile") or "").strip()
                doctor_key = str(appt.get("doctor_key") or "")
                clinic_name = str(appt.get("clinic_name") or "")
                language = str(appt.get("language") or "en").strip().lower()

                appt_dt_local = _parse_local_appt_dt(appt_date, appt_time, tz_name)
                if not appt_dt_local:
                    continue

                now_local = datetime.now(_safe_zoneinfo(tz_name))
                diff = appt_dt_local - now_local

                main_low = timedelta(hours=reminder_hours_before, minutes=-10)
                main_high = timedelta(hours=reminder_hours_before, minutes=10)

                short_low = timedelta(hours=2, minutes=-10)
                short_high = timedelta(hours=2, minutes=10)

                if main_low <= diff <= main_high:
                    claimed = await _try_claim_reminder(
                        db,
                        tenant_id=tenant_id,
                        appointment_id=appointment_id,
                        reminder_type="REMINDER_MAIN",
                    )
                    if claimed:
                        message = await _build_reminder_message(
                            db,
                            tenant_id=tenant_id,
                            clinic_name=clinic_name,
                            patient_name=patient_name,
                            doctor_key=doctor_key,
                            appt_date=appt_date,
                            appt_time=appt_time,
                            language=language,
                        )
                        if message:
                            wa_send_text(patient_mobile, message)
                        await db.commit()
                    continue

                if short_low <= diff <= short_high:
                    claimed = await _try_claim_reminder(
                        db,
                        tenant_id=tenant_id,
                        appointment_id=appointment_id,
                        reminder_type="REMINDER_2H",
                    )
                    if claimed:
                        message = await _build_reminder_message(
                            db,
                            tenant_id=tenant_id,
                            clinic_name=clinic_name,
                            patient_name=patient_name,
                            doctor_key=doctor_key,
                            appt_date=appt_date,
                            appt_time=appt_time,
                            language=language,
                        )
                        if message:
                            wa_send_text(patient_mobile, message)
                        await db.commit()

            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass
                continue