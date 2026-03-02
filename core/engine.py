# core/engine.py — Enterprise WhatsApp Clinic Engine (V4.5.2)
# ✅ First message specialty inquiry -> direct specialty reply (not greeting menu)
# ✅ Doctor inquiry reply has NO "2️⃣ See available doctors"
# ✅ Deterministic specialty extraction (Arabic dialect + English)
# ✅ Dentist mapping fixed (priority)
# ✅ No emergency logic here (notice exists in greeting)
# ✅ Auto-correct 21 -> 12 kept in specialty selection list

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone, timedelta
import random
import re

STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ABANDONED = "ABANDONED"

STATE_LANG = "LANG_SELECT"
STATE_MENU = "MAIN_MENU"

STATE_BOOK_DEPT = "BOOK_DEPT"
STATE_BOOK_DOCTOR = "BOOK_DOCTOR"
STATE_BOOK_DATE = "BOOK_DATE"
STATE_BOOK_SLOT = "BOOK_SLOT"
STATE_BOOK_PATIENT = "BOOK_PATIENT"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"

STATE_RESCHEDULE_LOOKUP = "RESCHEDULE_LOOKUP"
STATE_CANCEL_LOOKUP = "CANCEL_LOOKUP"

STATE_CLOSED = "CLOSED"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "CLINIC_ENGINE_V4_5_2"

SESSION_EXPIRE_SECONDS = 60 * 60
BOOKING_CONFIRM_EXPIRE_SECONDS = 5 * 60

CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"

RECEPTION_PHONE = "+966XXXXXXXX"
EMERGENCY_NUMBER = "997"

DEPTS = [
    {"key": "general", "en": "General Medicine / Internal Medicine", "ar": "الطب العام / الباطنة"},
    {"key": "peds", "en": "Pediatrics", "ar": "طب الأطفال"},
    {"key": "gyn", "en": "Obstetrics & Gynecology", "ar": "أمراض النساء والتوليد"},
    {"key": "ortho", "en": "Orthopedics", "ar": "جراحة العظام"},
    {"key": "derm", "en": "Dermatology", "ar": "الأمراض الجلدية"},
    {"key": "ent", "en": "ENT (Otolaryngology)", "ar": "الأنف والأذن والحنجرة"},
    {"key": "cardio", "en": "Cardiology", "ar": "أمراض القلب"},
    {"key": "dental", "en": "Dentistry", "ar": "طب الأسنان"},
    {"key": "neuro", "en": "Neurology", "ar": "الأعصاب"},
    {"key": "physio", "en": "Physiotherapy", "ar": "العلاج الطبيعي"},
    {"key": "ophthal", "en": "Ophthalmology (Eye)", "ar": "طب العيون"},
    {"key": "uro", "en": "Urology", "ar": "المسالك البولية"},
]

DOCTORS_BY_DEPT_KEY = {
    "general": [
        {"key": "dr_ahmed", "en": "Dr. Ahmed", "ar": "د. أحمد"},
        {"key": "dr_sara", "en": "Dr. Sara", "ar": "د. سارة"},
    ],
    "peds": [{"key": "dr_mona", "en": "Dr. Mona", "ar": "د. منى"}],
    "gyn": [{"key": "dr_huda", "en": "Dr. Huda", "ar": "د. هدى"}],
    "ortho": [{"key": "dr_khaled", "en": "Dr. Khaled", "ar": "د. خالد"}],
    "derm": [{"key": "dr_ali", "en": "Dr. Ali", "ar": "د. علي"}],
    "ent": [{"key": "dr_faisal", "en": "Dr. Faisal", "ar": "د. فيصل"}],
    "cardio": [{"key": "dr_nasser", "en": "Dr. Nasser", "ar": "د. ناصر"}],
    "dental": [{"key": "dr_laila", "en": "Dr. Laila", "ar": "د. ليلى"}],
    "neuro": [{"key": "dr_omar", "en": "Dr. Omar", "ar": "د. عمر"}],
    "physio": [{"key": "dr_rana", "en": "Dr. Rana", "ar": "د. رنا"}],
    "ophthal": [{"key": "dr_nour", "en": "Dr. Nour", "ar": "د. نور"}],
    "uro": [{"key": "dr_yousef", "en": "Dr. Yousef", "ar": "د. يوسف"}],
}

SLOTS = [
    "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30",
    "17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30",
]

CLINIC_TIMINGS_AR = "مواعيد العمل: يوميًا من 10:00 صباحًا إلى 2:00 ظهرًا ومن 5:00 مساءً إلى 9:00 مساءً (عدا الجمعة)."
CLINIC_TIMINGS_EN = "Hospital hours: daily 10:00 AM–2:00 PM and 5:00 PM–9:00 PM (except Friday)."
INSURANCE_AR = "التأمينات المعتمدة: بوبا، التعاونية، ميدغلف (مثال)."
INSURANCE_EN = "Accepted insurance: Bupa, Tawuniya, Medgulf (example)."
LOCATION_AR = "الموقع: (تجريبي) سيتم إضافة رابط خرائط جوجل لاحقًا."
LOCATION_EN = "Location: (demo) Google Maps link will be added later."
CONTACT_AR = f"📞 الاستقبال: {RECEPTION_PHONE}\n🚑 الطوارئ: {EMERGENCY_NUMBER}"
CONTACT_EN = f"📞 Reception: {RECEPTION_PHONE}\n🚑 Emergency: {EMERGENCY_NUMBER}"


@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_AR_CHARS_RE = re.compile(r"[\u0600-\u06FF]")
_EN_CHARS_RE = re.compile(r"[A-Za-z]")


def _normalize_digits(s: str) -> str:
    return (s or "").translate(_ARABIC_DIGITS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _clean_input(text: str) -> str:
    t = (text or "").strip()
    for ch in ["،", ",", "٫", ";", "؛", "。"]:
        t = t.replace(ch, "")
    t = " ".join(t.split())
    return t


def _norm(t: str) -> str:
    return _clean_input(t)


def _low(t: str) -> str:
    return _normalize_digits(_clean_input(t).lower())


def _lang(x: str) -> str:
    x = (x or "").strip().lower()
    return "ar" if x.startswith("ar") else "en"


def _is_digit_choice(t: str) -> bool:
    return _low(t).isdigit()


def _to_int(t: str, default: int = -1) -> int:
    try:
        return int(_low(t))
    except Exception:
        return default


def _is_thanks(text: str) -> bool:
    tl = _low(text)
    return tl in {"thanks", "thank you", "thx", "شكرا", "شكراً", "شكرًا", "مشكور", "الله يعطيك العافية"}


def _set_bot(sess: Dict[str, Any], msg: str) -> None:
    sess["last_bot_message"] = msg
    sess["last_bot_ts"] = _utcnow_iso()
    sess["last_step"] = sess.get("state")


def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "engine": ENGINE_MARKER,
        "user_id": user_id,
        "status": STATUS_ACTIVE,
        "state": STATE_LANG,
        "last_step": STATE_LANG,
        "language": "ar",
        "language_locked": False,
        "text_direction": "rtl",
        "has_greeted": False,
        "mistakes": 0,
        "last_user_ts": _utcnow_iso(),
        "last_bot_ts": None,
        "last_bot_message": "",
        "intent": None,
        "dept_key": None,
        "dept_label": None,
        "doctor_key": None,
        "doctor_label": None,
        "date": None,
        "slot": None,
        "patient_name": None,
        "patient_mobile": None,
        "patient_id": None,
        "appt_ref": None,
        "handoff_active": False,
        "escalation_flag": False,
        "pending_patient": {"name": None, "mobile": None, "pid": None},
        "confirm_expires_at": None,
    }


def _reset_flow_fields(sess: Dict[str, Any]) -> None:
    for k in [
        "intent",
        "dept_key", "dept_label",
        "doctor_key", "doctor_label",
        "date", "slot",
        "patient_name", "patient_mobile", "patient_id",
        "appt_ref",
        "confirm_expires_at",
    ]:
        sess[k] = None
    sess["mistakes"] = 0
    sess["pending_patient"] = {"name": None, "mobile": None, "pid": None}


def _seconds_since(prev_iso: Optional[str]) -> Optional[float]:
    dt = _parse_iso(prev_iso)
    if not dt:
        return None
    return (_utcnow() - dt).total_seconds()


def _session_expired_from(prev_iso: Optional[str]) -> bool:
    sec = _seconds_since(prev_iso)
    if sec is None:
        return False
    return sec >= SESSION_EXPIRE_SECONDS


def _greeting_menu_ar() -> str:
    return (
        f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
        "نرحب بكم في خدمة المساعد الافتراضي لحجز المواعيد والاستفسارات العامة.\n\n"
        "⚠️ تنبيه هام:\n"
        "إذا كنت تعاني من أعراض طارئة مثل ألم شديد في الصدر، صعوبة في التنفس، نزيف حاد أو فقدان مفاجئ للوعي، "
        f"يرجى الاتصال فورًا على {EMERGENCY_NUMBER} أو مراجعة قسم الطوارئ.\n"
        "هذه الخدمة مخصصة للمواعيد والاستفسارات غير الطارئة فقط.\n\n"
        "كيف يمكنني مساعدتك اليوم؟\n\n"
        "1️⃣ حجز موعد\n"
        "2️⃣ تعديل موعد\n"
        "3️⃣ إلغاء موعد\n"
        "4️⃣ البحث عن طبيب\n"
        "5️⃣ مواعيد العمل\n"
        "6️⃣ التأمينات المعتمدة\n"
        "7️⃣ الموقع والاتجاهات\n"
        "8️⃣ معلومات التواصل\n"
        "99️⃣ التحدث مع موظف الاستقبال"
    )


def _greeting_menu_en() -> str:
    return (
        f"Welcome to *{CLINIC_NAME_EN}* 🏥\n"
        "The official virtual assistant for appointments and general inquiries.\n\n"
        "⚠️ Important Notice:\n"
        "If you are experiencing a medical emergency such as severe chest pain, difficulty breathing, heavy bleeding, "
        "or loss of consciousness, please call 997 immediately or proceed to the nearest Emergency Department.\n"
        "This service is intended for non-emergency appointments and inquiries only.\n\n"
        "How may I assist you today?\n\n"
        "1️⃣ Book an Appointment\n"
        "2️⃣ Reschedule Appointment\n"
        "3️⃣ Cancel Appointment\n"
        "4️⃣ Find a Doctor\n"
        "5️⃣ Hospital Timings\n"
        "6️⃣ Accepted Insurance\n"
        "7️⃣ Location & Directions\n"
        "8️⃣ Contact Information\n"
        "99️⃣ Speak to Reception"
    )


def _main_menu(lang: str) -> str:
    if lang == "ar":
        return (
            "القائمة الرئيسية:\n\n"
            "1️⃣ حجز موعد\n"
            "2️⃣ تعديل موعد\n"
            "3️⃣ إلغاء موعد\n"
            "4️⃣ البحث عن طبيب\n"
            "5️⃣ مواعيد العمل\n"
            "6️⃣ التأمينات المعتمدة\n"
            "7️⃣ الموقع والاتجاهات\n"
            "8️⃣ معلومات التواصل\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Main Menu:\n\n"
        "1️⃣ Book Appointment\n"
        "2️⃣ Reschedule Appointment\n"
        "3️⃣ Cancel Appointment\n"
        "4️⃣ Find a Doctor\n"
        "5️⃣ Hospital Timings\n"
        "6️⃣ Accepted Insurance\n"
        "7️⃣ Location & Directions\n"
        "8️⃣ Contact Information\n"
        "99️⃣ Reception"
    )


def _footer(lang: str) -> str:
    if lang == "ar":
        return "\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "\n\n0️⃣ Main Menu\n99️⃣ Reception"


def _dept_prompt(lang: str) -> str:
    lines = [f"{i}️⃣ {d['ar'] if lang == 'ar' else d['en']}" for i, d in enumerate(DEPTS, start=1)]
    if lang == "ar":
        return "يرجى اختيار التخصص:\n\n" + "\n".join(lines) + "\n\n(يمكنك إرسال رقم أو اسم التخصص)" + _footer(lang)
    return "Please select a specialty:\n\n" + "\n".join(lines) + "\n\n(Reply with number or type the specialty)" + _footer(lang)


def _doctor_prompt(lang: str, dept_key: str) -> str:
    docs = DOCTORS_BY_DEPT_KEY.get(dept_key, [])
    lines = [f"{i}️⃣ {doc['ar'] if lang == 'ar' else doc['en']}" for i, doc in enumerate(docs, start=1)]
    if lang == "ar":
        return "الأطباء المتاحون:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الطبيب)" + _footer(lang)
    return "Available doctors:\n\n" + "\n".join(lines) + "\n\n(Reply with doctor number)" + _footer(lang)


def _dept_label(key: str, lang: str) -> Optional[str]:
    for d in DEPTS:
        if d["key"] == key:
            return d["ar"] if lang == "ar" else d["en"]
    return None


# ✅ NEW: inquiry reply WITHOUT option 2
def _specialty_inquiry_reply(lang: str, dept_key: str) -> str:
    label = _dept_label(dept_key, lang) or dept_key
    if lang == "ar":
        return (
            f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n\n"
            f"نعم، لدينا قسم *{label}* ✅\n\n"
            "هل ترغب بـ:\n"
            "1️⃣ حجز موعد\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        f"Welcome to *{CLINIC_NAME_EN}* 🏥\n\n"
        f"Yes — we have a *{label}* department ✅\n\n"
        "Would you like to:\n"
        "1️⃣ Book an appointment\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )


_INQUIRY_TRIGGERS_AR = [
    "هل", "عندكم", "موجود", "متوفر", "متاح", "مداوم", "دوام",
    "ابي", "أبي", "ابغى", "أبغى", "نبغي", "نبي", "عايز", "اريد", "أريد",
    "استفسر", "استفسار", "اسأل", "سؤال", "ابي استفسر", "أبغي استفسر",
]
_INQUIRY_TRIGGERS_EN = [
    "do you have", "is there", "available", "open", "enquire", "inquire", "enquiry", "inquiry",
    "i want to enquire", "i want to inquire", "i need", "specialist", "doctor",
]

_BOOK_TRIGGERS_AR = ["احجز", "حجز", "موعد", "ابغى احجز", "أبغى احجز", "عايز احجز", "اريد حجز", "أريد حجز"]
_BOOK_TRIGGERS_EN = ["book", "appointment", "schedule", "reserve", "i want to book", "need appointment"]

# Priority list prevents "dentist" being captured by ENT
_DEPT_SYNONYMS_PRIORITY: List[Tuple[str, List[str]]] = [
    ("dental", [
        "اسنان", "أسنان", "سنان", "ضرس", "ضروس", "تقويم", "لثة",
        "dentist", "dental", "dentistry", "tooth", "teeth", "toothache",
    ]),
    ("ent", [
        "انف", "أذن", "اذن", "حنجرة", "لوز", "جيوب", "اللوز",
        "otolaryngologist", "otolaryngology", "ent", "ear", "nose", "throat", "tonsil", "sinus",
    ]),
    ("cardio", ["قلب", "القلب", "نبض", "cardio", "cardiology", "heart", "palpitation"]),
    ("neuro", ["اعصاب", "الأعصاب", "العصبيه", "العصبية", "عصبيه", "عصبية", "neuro", "neurology", "migraine", "headache"]),
    ("derm", ["جلدية", "جلديه", "جلد", "حبوب", "اكزيما", "derm", "dermatology", "skin", "rash", "eczema", "acne"]),
    ("ortho", ["عظام", "ركبة", "ظهر", "كسور", "ortho", "orthopedic", "bone", "knee", "back"]),
    ("peds", ["اطفال", "الأطفال", "طفل", "عيال", "paediatrician", "pediatrician", "pediatrics", "kids", "child"]),
    ("gyn", ["نساء", "نسائي", "حمل", "ولادة", "دورة", "obgyn", "gyn", "pregnancy", "period"]),
    ("uro", [
        "مسالك", "المسالك", "بولية", "المسالك البولية", "بروستات",
        "urologist", "urology", "prostate",
    ]),
    ("ophthal", ["عيون", "عين", "نظر", "شبكية", "ophthalmology", "eye", "vision", "retina", "optometry"]),
    ("physio", ["علاج طبيعي", "فيزيو", "physio", "physiotherapy", "rehab"]),
    ("general", [
        "باطنه", "الباطنه", "باطنيه", "الباطنيه", "باطني", "باطنية",
        "internal medicine", "internist", "internal", "medicine",
    ]),
]


def _detect_language_from_text(text: str) -> Optional[str]:
    t = text or ""
    if _AR_CHARS_RE.search(t):
        return "ar"
    if _EN_CHARS_RE.search(t):
        return "en"
    return None


def _contains_any(hay: str, needles: List[str]) -> bool:
    h = _low(hay)
    raw = hay or ""
    for n in needles:
        nlow = _low(n)
        if nlow and nlow in h:
            return True
        if n and n in raw:
            return True
    return False


def _extract_dept_key(text: str) -> Optional[str]:
    t = _low(text)
    raw = text or ""
    for key, words in _DEPT_SYNONYMS_PRIORITY:
        for w in words:
            wlow = _low(w)
            if wlow and wlow in t:
                return key
            if w and w in raw:
                return key
    return None


def _detect_intent(text: str) -> Optional[str]:
    if _contains_any(text, _BOOK_TRIGGERS_AR) or _contains_any(text, _BOOK_TRIGGERS_EN):
        return "BOOK"
    if _contains_any(text, _INQUIRY_TRIGGERS_AR) or _contains_any(text, _INQUIRY_TRIGGERS_EN):
        return "SPECIALTY_INQUIRY"
    return None


def _soft_invalid(sess: Dict[str, Any], lang: str, msg: str) -> str:
    sess["mistakes"] = int(sess.get("mistakes", 0)) + 1
    if sess["mistakes"] >= 2:
        if lang == "ar":
            return msg + "\n\nإذا رغبت، يمكنني تحويلك لموظف الاستقبال: 99"
        return msg + "\n\nIf you prefer, I can connect you to Reception: 99"
    return msg


def handle_turn(
    user_id: str,
    message_text: str,
    language: str,
    session_in: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    raw = _norm(message_text)
    low = _low(message_text)

    lang = _lang(sess.get("language") or language or "ar")
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    intent = _detect_intent(message_text)
    dept_key = _extract_dept_key(message_text)

    # ✅ FIX 1: First session specialty inquiry should answer immediately (not greeting menu)
    if not bool(sess.get("has_greeted")):
        guessed = _detect_language_from_text(message_text)
        if guessed in {"ar", "en"}:
            sess["language_locked"] = True
            sess["language"] = guessed
            sess["text_direction"] = "rtl" if guessed == "ar" else "ltr"
            lang = guessed

        # If user asked about specialty on first message -> answer it directly
        if dept_key and (intent == "SPECIALTY_INQUIRY" or intent is None):
            sess["has_greeted"] = True
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            sess["intent"] = "SPECIALTY_INQUIRY"
            sess["dept_key"] = dept_key
            sess["dept_label"] = _dept_label(dept_key, lang)
            out = _specialty_inquiry_reply(lang, dept_key)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        # Otherwise greet menu
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        sess["has_greeted"] = True
        out = _greeting_menu_ar() if lang == "ar" else _greeting_menu_en()
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Normal 0/menu behavior
    if low in {"0", "٠"}:
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if low == "99":
        sess["state"] = STATE_ESCALATION
        sess["last_step"] = STATE_ESCALATION
        sess["escalation_flag"] = True
        out = (
            "تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
            if lang == "ar" else
            "Connecting you to Reception ✅ Please wait... (Reply 0 for menu)"
        )
        _set_bot(sess, out)
        return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

    if _is_thanks(raw):
        out = (
            "العفو ✅ إذا احتجت أي شيء آخر اكتب 0 لعرض القائمة."
            if lang == "ar" else
            "You’re welcome ✅ If you need anything else, reply 0 for the menu."
        )
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # ✅ FIX 2: Specialty inquiry ALWAYS answers (not greeting/menu fallback)
    if dept_key and (intent == "SPECIALTY_INQUIRY" or intent is None):
        sess["intent"] = "SPECIALTY_INQUIRY"
        sess["dept_key"] = dept_key
        sess["dept_label"] = _dept_label(dept_key, lang)
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _specialty_inquiry_reply(lang, dept_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Menu state
    if sess.get("state") != STATE_MENU and sess.get("state") != STATE_BOOK_DEPT:
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU

    if sess.get("state") == STATE_MENU:
        if _is_digit_choice(raw):
            choice = _to_int(raw)

            if choice == 1:
                _reset_flow_fields(sess)
                sess["intent"] = "BOOK"
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 4:
                _reset_flow_fields(sess)
                sess["intent"] = "SPECIALTY_INQUIRY"
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 5:
                out = (CLINIC_TIMINGS_AR if lang == "ar" else CLINIC_TIMINGS_EN) + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 6:
                out = (INSURANCE_AR if lang == "ar" else INSURANCE_EN) + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 7:
                out = (LOCATION_AR if lang == "ar" else LOCATION_EN) + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 8:
                out = (CONTACT_AR if lang == "ar" else CONTACT_EN) + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 99:
                sess["state"] = STATE_ESCALATION
                sess["last_step"] = STATE_ESCALATION
                sess["escalation_flag"] = True
                out = (
                    "تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
                    if lang == "ar" else
                    "Connecting you to Reception ✅ Please wait... (Reply 0 for menu)"
                )
                _set_bot(sess, out)
                return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

            # Invalid menu number -> show menu (NOT greeting)
            msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid menu number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        # Free text in menu: if not specialty inquiry -> show greeting menu (your rule)
        out = _greeting_menu_ar() if lang == "ar" else _greeting_menu_en()
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # BOOK DEPT selection (kept for your flow)
    if sess.get("state") == STATE_BOOK_DEPT:
        if _is_digit_choice(raw):
            n = _to_int(raw, -1)
            if n == 21:  # ✅ auto-correct
                n = 12
            idx = n - 1
        else:
            idx = -1

        dept_key2 = None
        dept_label2 = None

        if 0 <= idx < len(DEPTS):
            dept_key2 = DEPTS[idx]["key"]
            dept_label2 = DEPTS[idx]["ar"] if lang == "ar" else DEPTS[idx]["en"]
        else:
            k2 = _extract_dept_key(message_text)
            if k2:
                dept_key2 = k2
                dept_label2 = _dept_label(k2, lang)

        if not dept_key2:
            msg = ("يرجى اختيار تخصص صحيح." if lang == "ar" else "Please choose a valid specialty.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        # If this was inquiry flow, reply inquiry; if booking, you can continue later
        if sess.get("intent") == "SPECIALTY_INQUIRY":
            sess["dept_key"] = dept_key2
            sess["dept_label"] = dept_label2
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _specialty_inquiry_reply(lang, dept_key2)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        # Booking path (keep minimal here)
        sess["dept_key"] = dept_key2
        sess["dept_label"] = dept_label2
        sess["state"] = STATE_BOOK_DOCTOR
        sess["last_step"] = STATE_BOOK_DOCTOR
        out = _doctor_prompt(lang, dept_key2)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # fallback
    sess["state"] = STATE_MENU
    sess["last_step"] = STATE_MENU
    out = _main_menu(lang)
    _set_bot(sess, out)
    return EngineResult(out, sess, [])


def run_engine(
    session: Dict[str, Any],
    user_message: str,
    language: str,
    arabic_tone: Optional[str] = None,
    kpi_signals: Optional[list] = None,
) -> Dict[str, Any]:
    user_id = (session or {}).get("user_id") or "unknown"
    res = handle_turn(user_id=user_id, message_text=user_message, language=language, session_in=session)
    return {"reply_text": res.reply_text, "session": res.session, "actions": res.actions}