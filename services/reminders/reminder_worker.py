from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from services.reminders.reminder_scheduler import (
    get_due_reminders,
    mark_reminder_scheduled,
    mark_reminder_sent,
    mark_reminder_failed,
)
from services.messaging.message_templates import build_appointment_reminder_message

try:
    from services.messaging.whatsapp_sender import wa_send_text
except Exception:
    wa_send_text = None

try:
    from core.usage_logger import log_usage_event
except Exception:
    log_usage_event = None


def _doctor_name_from_job(job: Dict[str, Any]) -> str:
    return str(
        job.get("doctor_name")
        or job.get("doctor_label")
        or job.get("doctor_key")
        or ""
    ).strip()


def _appt_time_hhmm(value: Any) -> str:
    return str(value or "").strip()[:5]


async def send_due_reminders(
    db: AsyncSession,
    *,
    include_two_hour_reminder: bool = True,
) -> Dict[str, Any]:
    """
    Finds due reminders, sends WhatsApp messages, and updates reminder logs.

    Returns summary like:
    {
        "ok": True,
        "checked": 10,
        "claimed": 3,
        "sent": 2,
        "failed": 1
    }
    """
    jobs: List[Dict[str, Any]] = await get_due_reminders(
        db,
        include_two_hour_reminder=include_two_hour_reminder,
    )

    checked = len(jobs)
    claimed = 0
    sent = 0
    failed = 0

    if wa_send_text is None:
        return {
            "ok": False,
            "checked": checked,
            "claimed": 0,
            "sent": 0,
            "failed": checked,
            "error": "whatsapp_sender_not_configured",
        }

    for job in jobs:
        appointment_id = int(job["appointment_id"])
        tenant_id = str(job.get("tenant_id") or "").strip()
        reminder_type = str(job.get("reminder_type") or "").strip()

        won_claim = await mark_reminder_scheduled(
            db,
            tenant_id=tenant_id,
            appointment_id=appointment_id,
            reminder_type=reminder_type,
        )

        if not won_claim:
            continue

        claimed += 1

        patient_mobile = str(job.get("patient_mobile") or "").strip()
        if not patient_mobile:
            failed += 1
            await mark_reminder_failed(
                db,
                appointment_id=appointment_id,
                reminder_type=reminder_type,
                error_text="patient_mobile_missing",
            )
            await db.commit()
            continue

        try:
            message_text = await build_appointment_reminder_message(
                db,
                tenant_id=tenant_id,
                language=str(job.get("language") or "en").strip().lower(),
                clinic_name=str(job.get("clinic_name") or tenant_id).strip(),
                patient_name=str(job.get("patient_name") or "").strip(),
                doctor_name=_doctor_name_from_job(job),
                date=str(job.get("appt_date") or "").strip(),
                time=_appt_time_hhmm(job.get("appt_time")),
            )

            if not message_text:
                raise ValueError("empty reminder message")

            wa_send_text(patient_mobile, message_text)

            await mark_reminder_sent(
                db,
                appointment_id=appointment_id,
                reminder_type=reminder_type,
            )

            if log_usage_event is not None:
                await log_usage_event(
                    db,
                    tenant_id,
                    "whatsapp_messages",
                    1,
                )

            sent += 1
            await db.commit()

        except Exception as e:
            failed += 1
            await mark_reminder_failed(
                db,
                appointment_id=appointment_id,
                reminder_type=reminder_type,
                error_text=str(e),
            )
            await db.commit()

    return {
        "ok": True,
        "checked": checked,
        "claimed": claimed,
        "sent": sent,
        "failed": failed,
    }