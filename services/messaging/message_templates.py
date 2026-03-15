from __future__ import annotations

from typing import Dict, Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# =========================================================
# LEGACY FALLBACK TEMPLATE REGISTRY
# (kept for backwards compatibility with reception flow)
# =========================================================

TEMPLATES: Dict[str, Dict[str, Dict[str, str]]] = {
    "ar": {
        "APPROVED": {
            "formal":
                "تم تأكيد طلب حجز الموعد.\n"
                "الطبيب: {doctor}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
                "{clinic}",

            "friendly":
                "تم تأكيد موعدك لدينا 👍\n"
                "الطبيب: {doctor}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "نرجو الحضور قبل الموعد بـ 15 دقيقة.\n"
                "{clinic}",

            "premium":
                "يسعدنا تأكيد موعدكم.\n"
                "الطبيب: {doctor}\n"
                "التاريخ: {date}\n"
                "الوقت: {time}\n\n"
                "نرجو التكرم بالحضور قبل الموعد بـ 15 دقيقة.\n\n"
                "{clinic}",
        },

        "REJECTED": {
            "formal":
                "نأسف، الموعد المطلوب غير متوفر حالياً.\n"
                "يرجى إرسال تاريخ أو وقت آخر مناسب لكم.\n\n"
                "{clinic}",

            "friendly":
                "عذراً، هذا الموعد غير متاح.\n"
                "أرسل لنا وقتاً آخر وسنقوم بالمساعدة 👍\n\n"
                "{clinic}",

            "premium":
                "نعتذر، الموعد المطلوب غير متوفر.\n"
                "يسعدنا مساعدتكم في اختيار موعد آخر مناسب.\n\n"
                "{clinic}",
        },

        "CONTACTED": {
            "formal":
                "تم استلام طلبكم.\n"
                "سيقوم موظف الاستقبال بالتواصل معكم قريباً.\n\n"
                "{clinic}",

            "friendly":
                "تم استلام طلبك 👍\n"
                "سيقوم فريق الاستقبال بالتواصل معك قريباً.\n\n"
                "{clinic}",

            "premium":
                "تم استلام طلبكم بنجاح.\n"
                "سيقوم فريق الاستقبال بالتواصل معكم في أقرب وقت.\n\n"
                "{clinic}",
        },
    },

    "en": {
        "APPROVED": {
            "formal":
                "Your appointment request has been confirmed.\n"
                "Doctor: {doctor}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Please arrive 15 minutes before your appointment.\n\n"
                "{clinic}",

            "friendly":
                "Your appointment is confirmed 👍\n"
                "Doctor: {doctor}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Please arrive 15 minutes early.\n"
                "{clinic}",

            "premium":
                "We are pleased to confirm your appointment.\n"
                "Doctor: {doctor}\n"
                "Date: {date}\n"
                "Time: {time}\n\n"
                "Kindly arrive 15 minutes before your scheduled time.\n\n"
                "{clinic}",
        },

        "REJECTED": {
            "formal":
                "Unfortunately the requested appointment slot is not available.\n"
                "Please reply with another date or time.\n\n"
                "{clinic}",

            "friendly":
                "Sorry, that slot is not available.\n"
                "Please send another time that works for you.\n\n"
                "{clinic}",

            "premium":
                "We regret to inform you that the requested slot is unavailable.\n"
                "Our team will gladly assist you in selecting another time.\n\n"
                "{clinic}",
        },

        "CONTACTED": {
            "formal":
                "Your request has been received.\n"
                "Our reception team will contact you shortly.\n\n"
                "{clinic}",

            "friendly":
                "We received your request 👍\n"
                "Our reception team will contact you soon.\n\n"
                "{clinic}",

            "premium":
                "Your request has been successfully received.\n"
                "Our reception team will contact you shortly.\n\n"
                "{clinic}",
        },
    }
}


# =========================================================
# FALLBACKS
# =========================================================

DEFAULT_LANGUAGE = "ar"
DEFAULT_TONE = "formal"


# =========================================================
# SAFE FORMATTER
# =========================================================

def _safe_format(template: str, values: Dict[str, Any]) -> str:
    safe_values = {k: ("" if v is None else str(v)) for k, v in values.items()}
    try:
        return template.format(**safe_values)
    except Exception:
        return template


# =========================================================
# DB TEMPLATE LOOKUP
# =========================================================

async def get_message_template(
    db: AsyncSession,
    *,
    tenant_id: str,
    template_key: str,
    language: str,
) -> Optional[str]:
    tenant_id = (tenant_id or "").strip()
    template_key = (template_key or "").strip().lower()
    language = (language or "en").strip().lower()

    if not tenant_id or not template_key:
        return None

    res = await db.execute(
        text(
            """
            SELECT template_text
            FROM message_templates
            WHERE tenant_id = :tenant_id
              AND template_key = :template_key
              AND language = :language
              AND is_active = TRUE
            LIMIT 1;
            """
        ),
        {
            "tenant_id": tenant_id,
            "template_key": template_key,
            "language": language,
        },
    )
    row = res.mappings().first()

    if row and row.get("template_text"):
        return str(row["template_text"])

    # fallback to English if exact language not found
    if language != "en":
        res2 = await db.execute(
            text(
                """
                SELECT template_text
                FROM message_templates
                WHERE tenant_id = :tenant_id
                  AND template_key = :template_key
                  AND language = 'en'
                  AND is_active = TRUE
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "template_key": template_key,
            },
        )
        row2 = res2.mappings().first()
        if row2 and row2.get("template_text"):
            return str(row2["template_text"])

    return None


async def render_message_template(
    db: AsyncSession,
    *,
    tenant_id: str,
    template_key: str,
    language: str,
    values: Dict[str, Any],
) -> Optional[str]:
    template = await get_message_template(
        db,
        tenant_id=tenant_id,
        template_key=template_key,
        language=language,
    )
    if not template:
        return None

    return _safe_format(template, values)


# =========================================================
# PHASE 9 HELPERS
# =========================================================

async def build_appointment_confirmed_message(
    db: AsyncSession,
    *,
    tenant_id: str,
    language: str,
    clinic_name: str,
    patient_name: str | None,
    doctor_name: str | None,
    date: str | None,
    time: str | None,
) -> str:
    rendered = await render_message_template(
        db,
        tenant_id=tenant_id,
        template_key="appointment_confirmed",
        language=language,
        values={
            "patient_name": patient_name or "",
            "doctor_name": doctor_name or "",
            "date": date or "",
            "time": time or "",
            "clinic_name": clinic_name or "",
        },
    )

    if rendered:
        return rendered

    # hard fallback if DB template not found
    if (language or "").lower() == "ar":
        return (
            f"مرحبًا {patient_name or ''}\n\n"
            f"تم تأكيد موعدكم في {clinic_name or ''} ✅\n\n"
            f"الطبيب: {doctor_name or '-'}\n"
            f"التاريخ: {date or '-'}\n"
            f"الوقت: {time or '-'}\n\n"
            f"يرجى الحضور قبل الموعد بـ 15 دقيقة."
        ).strip()

    return (
        f"Hello {patient_name or ''}\n\n"
        f"Your appointment has been confirmed at {clinic_name or ''}.\n\n"
        f"Doctor: {doctor_name or '-'}\n"
        f"Date: {date or '-'}\n"
        f"Time: {time or '-'}\n\n"
        f"Please arrive 15 minutes early."
    ).strip()


async def build_appointment_reminder_message(
    db: AsyncSession,
    *,
    tenant_id: str,
    language: str,
    clinic_name: str,
    patient_name: str | None,
    doctor_name: str | None,
    date: str | None,
    time: str | None,
) -> str:
    rendered = await render_message_template(
        db,
        tenant_id=tenant_id,
        template_key="appointment_reminder",
        language=language,
        values={
            "patient_name": patient_name or "",
            "doctor_name": doctor_name or "",
            "date": date or "",
            "time": time or "",
            "clinic_name": clinic_name or "",
        },
    )

    if rendered:
        return rendered

    # hard fallback if DB template not found
    if (language or "").lower() == "ar":
        return (
            f"مرحبًا {patient_name or ''}\n\n"
            f"تذكير من {clinic_name or ''} 🏥\n"
            f"لديكم موعد قريبًا.\n\n"
            f"الطبيب: {doctor_name or '-'}\n"
            f"التاريخ: {date or '-'}\n"
            f"الوقت: {time or '-'}\n\n"
            f"يرجى الرد إذا احتجتم المساعدة."
        ).strip()

    return (
        f"Hello {patient_name or ''}\n\n"
        f"Reminder from {clinic_name or ''}.\n"
        f"You have an appointment soon.\n\n"
        f"Doctor: {doctor_name or '-'}\n"
        f"Date: {date or '-'}\n"
        f"Time: {time or '-'}\n\n"
        f"Please reply if you need help."
    ).strip()

async def build_appointment_cancelled_message(
    db: AsyncSession,
    *,
    tenant_id: str,
    language: str,
    clinic_name: str,
    patient_name: str | None,
    doctor_name: str | None,
    date: str | None,
    time: str | None,
) -> str:
    rendered = await render_message_template(
        db,
        tenant_id=tenant_id,
        template_key="appointment_cancelled",
        language=language,
        values={
            "patient_name": patient_name or "",
            "doctor_name": doctor_name or "",
            "date": date or "",
            "time": time or "",
            "clinic_name": clinic_name or "",
        },
    )

    if rendered:
        return rendered

    if (language or "").lower() == "ar":
        return (
            f"مرحبًا {patient_name or ''}\n\n"
            f"تم إلغاء موعدكم في {clinic_name or ''}.\n\n"
            f"الطبيب: {doctor_name or '-'}\n"
            f"التاريخ: {date or '-'}\n"
            f"الوقت: {time or '-'}\n\n"
            f"يرجى الرد إذا رغبتم في إعادة جدولة الموعد."
        ).strip()

    return (
        f"Hello {patient_name or ''}\n\n"
        f"Your appointment at {clinic_name or ''} has been cancelled.\n\n"
        f"Doctor: {doctor_name or '-'}\n"
        f"Date: {date or '-'}\n"
        f"Time: {time or '-'}\n\n"
        f"Please reply if you would like to reschedule."
    ).strip()


async def build_insurance_pending_message(
    db: AsyncSession,
    *,
    tenant_id: str,
    language: str,
    clinic_name: str,
    patient_name: str | None,
    doctor_name: str | None,
    date: str | None,
    time: str | None,
) -> str:
    rendered = await render_message_template(
        db,
        tenant_id=tenant_id,
        template_key="insurance_pending",
        language=language,
        values={
            "patient_name": patient_name or "",
            "doctor_name": doctor_name or "",
            "date": date or "",
            "time": time or "",
            "clinic_name": clinic_name or "",
        },
    )

    if rendered:
        return rendered

    if (language or "").lower() == "ar":
        return (
            f"مرحبًا {patient_name or ''}\n\n"
            f"طلب موعدكم في {clinic_name or ''} قيد مراجعة التأمين.\n\n"
            f"الطبيب: {doctor_name or '-'}\n"
            f"التاريخ: {date or '-'}\n"
            f"الوقت: {time or '-'}\n\n"
            f"قد يتواصل معكم فريقنا إذا احتجنا إلى معلومات تأمين إضافية."
        ).strip()

    return (
        f"Hello {patient_name or ''}\n\n"
        f"Your appointment request at {clinic_name or ''} is pending insurance review.\n\n"
        f"Doctor: {doctor_name or '-'}\n"
        f"Date: {date or '-'}\n"
        f"Time: {time or '-'}\n\n"
        f"Our team may contact you if more insurance details are needed."
    ).strip()


# =========================================================
# LEGACY RECEPTION BUILDER
# (kept so current reception flow does not break)
# =========================================================

def build_reception_message(
    *,
    status: str,
    patient_name: str | None,
    doctor: str | None,
    date: str | None,
    time: str | None,
    language: str,
    tone: str,
    clinic_name: str,
) -> str:
    """
    Build receptionist message from legacy in-code templates.

    Parameters
    ----------
    status : APPROVED / REJECTED / CONTACTED
    language : ar / en
    tone : formal / friendly / premium
    """

    status = (status or "").upper()
    language = (language or DEFAULT_LANGUAGE).lower()
    tone = (tone or DEFAULT_TONE).lower()

    if language not in TEMPLATES:
        language = DEFAULT_LANGUAGE

    lang_templates = TEMPLATES[language]

    if status not in lang_templates:
        return ""

    tone_templates = lang_templates[status]

    if tone not in tone_templates:
        tone = DEFAULT_TONE
        if tone not in tone_templates:
            tone = next(iter(tone_templates.keys()))

    template = tone_templates[tone]

    return _safe_format(
        template,
        {
            "patient": patient_name or "",
            "doctor": doctor or "",
            "date": date or "",
            "time": time or "",
            "clinic": clinic_name or "",
        },
    )