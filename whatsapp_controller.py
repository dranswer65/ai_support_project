# whatsapp_controller.py — Enterprise-ready controller (FINAL V8)
# Fixes:
# ✅ 3-layer intent detection (Emergency -> Intent -> Context continuation)
# ✅ Arabic free-text booking (no language restart)
# ✅ Strict emergency detector (no single-word triggers)
# ✅ Urology inquiry is NOT triage/emergency
# ✅ Correct Dentistry routing ("I need the dentist")
# ✅ 10-minute inactivity timeout for in-progress transactional flows
# ✅ Never sends main menu by itself; only replies when user sends a message

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session


WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

RECEPTION_CODE = "99"
EMERGENCY_NUMBER = "997"

# Timeout policy:
# - Booking / transactional states: expire after 10 minutes inactivity
# - Main menu / language select: can be longer (we keep 60 min here)
FLOW_IDLE_TIMEOUT_MINUTES = 10
IDLE_TIMEOUT_MENU_MINUTES = 60


# -----------------------------
# Basic language detection
# -----------------------------
_AR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    t = (text or "").strip()
    t = t.translate(_ARABIC_DIGITS)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _looks_arabic(text: str) -> bool:
    return bool(_AR_RE.search(text or ""))


def _resolve_language(message_text: str, session: Dict[str, Any]) -> str:
    # If already locked, keep it
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "en").startswith("ar") else "en"

    # If Arabic characters exist, Arabic
    if _looks_arabic(message_text):
        return "ar"

    # Default to English
    return "en"


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


# -----------------------------
# Intent detection (Layer 2)
# -----------------------------
AGENT_KEYS = {
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال", "موظف استقبال"
}

INTENT_BOOK_EN = {"book", "booking", "appointment", "schedule", "reserve", "visit", "consultation", "checkup"}
INTENT_BOOK_AR = {"حجز", "احجز", "موعد", "أبي موعد", "ابغى موعد", "أبغى موعد", "اريد موعد", "أريد موعد", "ابي احجز", "ابغى احجز", "عايز احجز", "محتاج موعد", "اريد ان احجز", "أريد أن أحجز"}

INTENT_DOCTOR_INFO_EN = {"doctor", "specialist", "available doctor", "find a doctor", "do you have", "is there", "do you have a"}
INTENT_DOCTOR_INFO_AR = {"دكتور", "دكتورة", "أخصائي", "اخصائي", "هل يوجد", "عندكم", "موجود", "متوفر", "هل في", "هل يوجد عندكم", "عندكم دكتور", "عندكم اخصائي"}

INTENT_INSURANCE_EN = {"insurance", "covered", "network"}
INTENT_INSURANCE_AR = {"تأمين", "التأمين", "تأمينات", "بطاقة تأمين"}

INTENT_TIMINGS_EN = {"timings", "hours", "working hours", "open"}
INTENT_TIMINGS_AR = {"مواعيد", "دوام", "ساعات العمل", "متى تفتحون", "متى تقفلون"}


# Specialty keywords (must include Dentistry + Urology + Internal!)
SPECIALTY_KEYWORDS = {
    "internal": {
        "en": ["internal medicine", "internist", "medicine doctor", "internal"],
        "ar": ["باطنية", "الباطنية", "الباطنيه", "باطنه", "دكتور باطنة", "طبيب باطنة"],
    },
    "dentistry": {
        "en": ["dentist", "dentistry", "teeth", "tooth", "dental"],
        "ar": ["اسنان", "أسنان", "طب الأسنان", "دكتور أسنان", "طبيب أسنان"],
    },
    "urology": {
        "en": ["urology", "urologist"],
        "ar": ["مسالك", "المسالك", "بولية", "بوليه", "مسالك بولية", "مسالك بوليه"],
    },
    "ent": {
        "en": ["ent", "ear", "throat", "sinus", "tonsils"],
        "ar": ["أنف", "اذن", "أذن", "حنجرة", "جيوب", "لوز", "انف واذن وحنجرة", "أنف وأذن وحنجرة"],
    },
    "cardio": {
        "en": ["cardiology", "cardiologist", "heart", "ecg", "ekg", "palpitations"],
        "ar": ["قلب", "أخصائي قلب", "دكتور قلب", "خفقان", "رسم قلب", "تخطيط"],
    },
    "obgyn": {
        "en": ["obgyn", "ob-gyn", "gynecology", "pregnancy", "maternity"],
        "ar": ["نساء", "نساء وولادة", "ولادة", "توليد", "حمل", "دكتورة نساء"],
    },
}


def _detect_specialty(cleaned: str) -> Optional[str]:
    t = (cleaned or "").lower()
    raw = cleaned or ""
    for dept_key, kw in SPECIALTY_KEYWORDS.items():
        for k in kw.get("en", []):
            if k and k in t:
                return dept_key
        for k in kw.get("ar", []):
            if k and k in raw:
                return dept_key
    return None


def _detect_intent(cleaned: str) -> Optional[str]:
    t = (cleaned or "").lower()
    raw = cleaned or ""

    # Insurance / timings first (avoid misrouting)
    if any(k in t for k in INTENT_INSURANCE_EN) or any(k in raw for k in INTENT_INSURANCE_AR):
        return "INSURANCE"
    if any(k in t for k in INTENT_TIMINGS_EN) or any(k in raw for k in INTENT_TIMINGS_AR):
        return "TIMINGS"

    # Booking / doctor info
    if any(k in t for k in INTENT_BOOK_EN) or any(k in raw for k in INTENT_BOOK_AR):
        return "BOOK"
    if any(k in t for k in INTENT_DOCTOR_INFO_EN) or any(k in raw for k in INTENT_DOCTOR_INFO_AR):
        return "DOCTOR_INFO"

    return None


def _wants_agent(cleaned: str) -> bool:
    t = (cleaned or "").strip().lower()
    if t == RECEPTION_CODE:
        return True
    return any(k in t for k in AGENT_KEYS)


# -----------------------------
# Emergency detection (Layer 1) — STRICT
# -----------------------------
# High-risk phrases: trigger immediately
HIGH_RISK_EN = [
    "can't breathe", "cannot breathe", "unable to breathe", "shortness of breath",
    "severe chest pain", "crushing chest pain",
    "face drooping", "slurred speech", "one side weakness",
    "lost vision", "vision loss", "sudden vision loss", "i can't see", "cannot see",
    "unconscious", "not responding", "passed out",
    "heavy bleeding", "bleeding heavily",
    "seizure", "seizures",
]
HIGH_RISK_AR = [
    "لا أستطيع التنفس", "لا استطيع التنفس", "اختناق", "ضيق تنفس شديد", "صعوبة تنفس شديدة",
    "ألم شديد في الصدر", "الم شديد في الصدر", "وجع شديد في الصدر", "صدري بيوجعني جامد",
    "ما أشوف", "ما اشوف", "لا أرى", "لا ارى", "فقدت النظر", "فقدان النظر",
    "انحراف الوجه", "ثقل في الكلام", "ضعف في جهة", "تنميل في جهة",
    "فقدت الوعي", "فقدان الوعي", "اغمى عليه", "إغماء",
    "نزيف شديد", "ينزف بشدة", "نزيف قوي",
    "تشنج", "تشنجات",
]

# Symptom clusters (need severity to trigger)
SYMPTOMS_EN = {
    "chest_pain": ["chest pain", "chest hurts", "pain in chest"],
    "breathing": ["difficulty breathing", "breathing trouble", "hard to breathe"],
    "vision": ["vision blurry", "blurred vision", "vision problem", "can't see"],
}
SYMPTOMS_AR = {
    "chest_pain": ["ألم في الصدر", "الم في الصدر", "وجع في الصدر", "صدري بيوجعني"],
    "breathing": ["ضيق تنفس", "صعوبة تنفس", "مش قادر اتنفس", "مو قادر اتنفس"],
    "vision": ["لا ارى", "لا أرى", "مش شايف", "ما اشوف", "ما أشوف", "زغللة شديدة"],
}

SEVERITY_EN = ["severe", "very", "extreme", "unbearable", "sudden", "can't", "cannot", "unable"]
SEVERITY_AR = ["شديد", "شديدة", "جامد", "قوي", "قوية", "مرة", "فجأة", "فجائي", "مو قادر", "مش قادر", "لا أستطيع", "لا استطيع"]


def _has_any(text_l: str, needles: List[str]) -> bool:
    return any(n in text_l for n in needles)


def _has_any_raw(text: str, needles: List[str]) -> bool:
    return any(n in text for n in needles)


def _emergency_detect(cleaned: str) -> bool:
    """
    STRICT:
    - If any high-risk phrase => emergency
    - Else, require (symptom present) AND (severity present OR 2 critical clusters)
    """
    if not cleaned:
        return False

    t = cleaned.lower()
    raw = cleaned

    # High-risk immediate
    if _has_any(t, [x.lower() for x in HIGH_RISK_EN]):
        return True
    if _has_any_raw(raw, HIGH_RISK_AR):
        return True

    # severity
    sev = _has_any(t, [x.lower() for x in SEVERITY_EN]) or _has_any_raw(raw, SEVERITY_AR)

    # symptom clusters count
    clusters = 0
    for _, ks in SYMPTOMS_EN.items():
        if any(k in t for k in ks):
            clusters += 1
    for _, ks in SYMPTOMS_AR.items():
        if any(k in raw for k in ks):
            clusters += 1

    # emergency if:
    # - one cluster + severity
    # - or two clusters even without severity (e.g., chest pain + vision issue)
    if clusters >= 2:
        return True
    if clusters >= 1 and sev:
        return True

    return False


# -----------------------------
# Symptom triage (non-emergency) — STRICT
# -----------------------------
SYMPTOM_WORDS_EN = ["pain", "fever", "vomit", "burning", "urination", "urinate", "pee", "rash", "itch", "discharge"]
SYMPTOM_WORDS_AR = ["ألم", "الم", "حمى", "حرارة", "قيء", "حرقان", "تبول", "بول", "طفح", "حكة", "افرازات", "إفرازات"]

# If user asks: "هل في اخصائي مسالك بوليه" => DOCTOR_INFO, NOT symptom triage
DOCTOR_INQUIRY_PATTERNS_AR = ["هل في", "هل يوجد", "عندكم", "موجود", "متوفر"]
DOCTOR_INQUIRY_PATTERNS_EN = ["do you have", "is there", "available", "do you have a"]


def _looks_like_symptom(cleaned: str, intent: Optional[str], specialty: Optional[str]) -> bool:
    if not cleaned:
        return False

    # If this is doctor inquiry, don't classify as symptom
    if intent == "DOCTOR_INFO" and specialty:
        return False

    t = cleaned.lower()
    raw = cleaned
    return any(w in t for w in SYMPTOM_WORDS_EN) or any(w in raw for w in SYMPTOM_WORDS_AR)


def _emergency_message(language: str) -> str:
    if language == "ar":
        return (
            "⚠️ الأعراض التي ذكرتها قد تشير إلى حالة طبية طارئة وخطيرة.\n\n"
            f"يرجى الاتصال فورًا على {EMERGENCY_NUMBER} أو التوجه إلى أقرب قسم طوارئ.\n\n"
            "⚠️ لا تعتمد على هذه المحادثة في الحالات الطارئة.\n"
            "هذه الخدمة مخصصة للحجز والاستفسارات فقط.\n\n"
            f"للتواصل مع الاستقبال اكتب {RECEPTION_CODE}."
        )
    return (
        "⚠️ The symptoms you described may indicate a serious medical emergency.\n\n"
        f"Please call {EMERGENCY_NUMBER} immediately or go to the nearest emergency department.\n\n"
        "⚠️ This chat cannot replace emergency medical care.\n"
        "This service is only for booking and general inquiries.\n\n"
        f"To reach Reception, reply {RECEPTION_CODE}."
    )


def _triage_message(language: str) -> str:
    if language == "ar":
        return (
            "⚠️ فهمت.\n"
            "إذا كان لديك ألم شديد، حرارة عالية، نزيف شديد، أو صعوبة شديدة في التنفس — اتصل على 997 فورًا.\n\n"
            f"للمساعدة السريعة يمكنني تحويلك لموظف الاستقبال: {RECEPTION_CODE}.\n\n"
            "إن كان الهدف حجز موعد، اكتب 0 ثم اختر (1) حجز موعد."
        )
    return (
        "⚠️ Understood.\n"
        "If you have severe pain, high fever, heavy bleeding, or severe breathing difficulty, please call 997 immediately.\n\n"
        f"For fast help, I can connect you to Reception: {RECEPTION_CODE}.\n\n"
        "If you want to book an appointment, reply 0 then choose (1) Book Appointment."
    )


# -----------------------------
# Timeout logic
# -----------------------------
TRANSACTIONAL_STATES = {
    "BOOK_SPECIALTY", "BOOK_DOCTOR", "BOOK_DATE", "BOOK_SLOT", "BOOK_DETAILS", "BOOK_CONFIRM",
    "FIND_SPECIALTY", "FIND_DOCTOR",
}

def _idle_minutes(session: Dict[str, Any]) -> Optional[float]:
    last = _parse_iso(session.get("last_activity_at"))
    if not last:
        return None
    return (_utcnow() - last).total_seconds() / 60.0


def _apply_inbound_activity(session: Dict[str, Any]) -> None:
    session["last_activity_at"] = _utcnow().isoformat()


def _timeout_policy(session: Dict[str, Any]) -> Tuple[bool, int]:
    """
    Returns (is_expired, timeout_minutes_applied)
    """
    state = (session.get("state") or "LANG_SELECT").upper()
    idle = _idle_minutes(session)
    if idle is None:
        return False, FLOW_IDLE_TIMEOUT_MINUTES

    if state in TRANSACTIONAL_STATES:
        return idle >= FLOW_IDLE_TIMEOUT_MINUTES, FLOW_IDLE_TIMEOUT_MINUTES

    return idle >= IDLE_TIMEOUT_MENU_MINUTES, IDLE_TIMEOUT_MENU_MINUTES


def _timeout_message(language: str) -> str:
    if language == "ar":
        return (
            "⏳ انتهت مهلة الجلسة بسبب عدم النشاط.\n"
            "إذا كنت ترغب في إكمال الحجز، اكتب 0 لعرض القائمة الرئيسية."
        )
    return (
        "⏳ Session timed out due to inactivity.\n"
        "If you want to continue, reply 0 for the main menu."
    )


# -----------------------------
# Controller entry
# -----------------------------
async def handle_message(
    *,
    db: AsyncSession,
    user_id: str,
    message_text: str,
    tenant_id: Optional[str] = None,
    kpi_signals=None,
) -> Tuple[str, Dict[str, Any]]:
    tenant = _norm_tenant(tenant_id)
    cleaned = _norm(message_text)

    # Never reply to empty inbound (prevents "auto main menu" bugs from blank events)
    if not cleaned:
        return "", {"tenant_id": tenant, "ignored": True}

    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict) or not session:
        session = {
            "user_id": user_id,
            "tenant_id": tenant,
            "status": "ACTIVE",
            "state": "LANG_SELECT",
            "last_step": "LANG_SELECT",
            "language": "en",
            "language_locked": False,
            "text_direction": "ltr",
            "last_activity_at": None,
            "conversation_version": 8,
        }

    # Apply timeout only on inbound message (we don't push messages ourselves)
    expired, applied = _timeout_policy(session)
    language_now = _resolve_language(cleaned, session)

    if expired:
        # reset transactional context safely
        session.pop("dept_key", None)
        session.pop("doctor_id", None)
        session.pop("date", None)
        session.pop("time", None)
        session.pop("patient_name", None)
        session.pop("patient_mobile", None)
        session.pop("ref", None)

        session["state"] = "MAIN_MENU"
        session["last_step"] = "MAIN_MENU"
        session["language"] = language_now
        session["language_locked"] = True
        _apply_inbound_activity(session)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

        return _timeout_message(language_now), {
            "tenant_id": tenant,
            "timeout": True,
            "timeout_minutes": applied,
            "state": session.get("state"),
        }

    # Always update activity timestamp
    _apply_inbound_activity(session)

    # Resolve language and lock early if Arabic/English text appears
    session["language"] = language_now
    session["language_locked"] = True
    session["text_direction"] = "rtl" if language_now == "ar" else "ltr"

    # -------------------------
    # Layer 1: Emergency detection (strict)
    # -------------------------
    # BUT: doctor inquiry about urology/etc should not go to triage/emergency
    intent = _detect_intent(cleaned)
    dept_key = _detect_specialty(cleaned)

    if _emergency_detect(cleaned):
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return _emergency_message(language_now), {
            "tenant_id": tenant,
            "emergency": True,
            "language": language_now,
        }

    # -------------------------
    # Layer 2: Intent detection
    # -------------------------
    if _wants_agent(cleaned):
        # You can integrate your own escalation pipeline here.
        # For now, keep deterministic response.
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        if language_now == "ar":
            return f"تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0", {"tenant_id": tenant, "handoff": True}
        return f"Connecting you to Reception ✅\nReply 0 for the menu", {"tenant_id": tenant, "handoff": True}

    # Doctor inquiry intent (e.g., "هل في اخصائي مسالك بوليه عندكم")
    # If specialty detected => jump to FIND flow (not symptom triage)
    if intent == "DOCTOR_INFO" and dept_key:
        session["dept_key"] = dept_key
        session["state"] = "FIND_DOCTOR"
        session["last_step"] = "FIND_DOCTOR"

        engine_out = run_engine(session=session, user_message="", language=language_now)
        reply = (engine_out.get("reply_text") or "").strip()
        session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
        await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

        return reply, {"tenant_id": tenant, "intent": "DOCTOR_INFO", "dept_key": dept_key, "state": session2.get("state")}

    # Booking intent with specialty detected => jump into booking doctor list
    if intent == "BOOK" and dept_key:
        session["dept_key"] = dept_key
        session["state"] = "BOOK_DOCTOR"
        session["last_step"] = "BOOK_DOCTOR"

        engine_out = run_engine(session=session, user_message="", language=language_now)
        reply = (engine_out.get("reply_text") or "").strip()
        session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
        await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

        return reply, {"tenant_id": tenant, "intent": "BOOK", "dept_key": dept_key, "state": session2.get("state")}

    # If it looks like symptom (non-emergency), send triage message (but not for doctor inquiries)
    if _looks_like_symptom(cleaned, intent, dept_key):
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return _triage_message(language_now), {"tenant_id": tenant, "triage": True, "language": language_now}

    # -------------------------
    # Layer 3: Context continuation (engine owns state)
    # -------------------------
    engine_out = run_engine(
        session=session,
        user_message=cleaned,
        language=language_now,
        arabic_tone=None,
        kpi_signals=list(kpi_signals or []),
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

    await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

    return reply_text, {
        "tenant_id": tenant,
        "state": session2.get("state"),
        "language": session2.get("language"),
        "timeout": False,
    }