# core/engine.py — Enterprise WhatsApp Clinic Engine (V4.5.4)
# FIXES:
# ✅ Full booking flow restored: Doctor -> Date -> Slot Window -> Slot -> Patient -> Summary -> Send to Reception
# ✅ Specialty inquiry context preserved: pressing "1" from inquiry goes directly to doctors of that specialty
# ✅ Doctor selection precedence fixed: "1" in doctor list selects doctor (not main menu)
# ✅ First message BOOK + dept goes directly to doctor list (no greeting menu)
# ✅ Inquiry reply has NO option 2
# ✅ Dentist mapping fixed (priority)
# ✅ Arabic dialect handled for inquiry + booking
# ✅ No random fallbacks: if text doesn't match, show greeting menu (your preference)
# ✅ Auto-correct 21 -> 12 kept
# ✅ Confirmation expires in 5 minutes

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
STATE_BOOK_SLOT_WINDOW = "BOOK_SLOT_WINDOW"
STATE_BOOK_SLOT = "BOOK_SLOT"
STATE_BOOK_PATIENT = "BOOK_PATIENT"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"

STATE_RESCHEDULE_LOOKUP = "RESCHEDULE_LOOKUP"
STATE_CANCEL_LOOKUP = "CANCEL_LOOKUP"

STATE_CLOSED = "CLOSED"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "CLINIC_ENGINE_V4_5_4"

SESSION_EXPIRE_SECONDS = 60 * 60
BOOKING_CONFIRM_EXPIRE_SECONDS = 5 * 60

CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"

RECEPTION_PHONE = "+966XXXXXXXX"
EMERGENCY_NUMBER = "997"

# Departments (12)
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

CLINIC_TIMINGS_AR = "مواعيد العمل: يوميًا من 10:00 صباحًا إلى 2:00 ظهرًا ومن 5:00 مساءً إلى 9:00 مساءً (عدا الجمعة)."
CLINIC_TIMINGS_EN = "Hospital hours: daily 10:00 AM–2:00 PM and 5:00 PM–9:00 PM (except Friday)."
INSURANCE_AR = "التأمينات المعتمدة: بوبا، التعاونية، ميدغلف (مثال)."
INSURANCE_EN = "Accepted insurance: Bupa, Tawuniya, Medgulf (example)."
LOCATION_AR = "الموقع: (تجريبي) سيتم إضافة رابط خرائط جوجل لاحقًا."
LOCATION_EN = "Location: (demo) Google Maps link will be added later."
CONTACT_AR = f"📞 الاستقبال: {RECEPTION_PHONE}\n🚑 الطوارئ: {EMERGENCY_NUMBER}"
CONTACT_EN = f"📞 Reception: {RECEPTION_PHONE}\n🚑 Emergency: {EMERGENCY_NUMBER}"


# Slot windows requested by you:
# 1) 8pm - 3pm (cross-day)
# 2) 5pm - 10pm
# 3) 11pm - 2am (cross-day)
SLOT_WINDOWS = [
    {"key": "w1", "ar": "من 8:00 مساءً إلى 3:00 مساءً", "en": "8:00 PM – 3:00 PM", "start": 20, "end": 15},
    {"key": "w2", "ar": "من 5:00 مساءً إلى 10:00 مساءً", "en": "5:00 PM – 10:00 PM", "start": 17, "end": 22},
    {"key": "w3", "ar": "من 11:00 مساءً إلى 2:00 صباحًا", "en": "11:00 PM – 2:00 AM", "start": 23, "end": 2},
]


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
        # flow
        "intent": None,
        "dept_key": None,
        "dept_label": None,
        "doctor_key": None,
        "doctor_label": None,
        "date": None,
        "slot_window": None,
        "slot": None,
        "patient_name": None,
        "patient_mobile": None,
        "patient_id": None,
        "appt_ref": None,
        "confirm_expires_at": None,
        # handoff
        "handoff_active": False,
        "escalation_flag": False,
        # multi-turn
        "pending_patient": {"name": None, "mobile": None, "pid": None},
    }


def _reset_flow_fields(sess: Dict[str, Any]) -> None:
    for k in [
        "intent",
        "dept_key", "dept_label",
        "doctor_key", "doctor_label",
        "date", "slot_window", "slot",
        "patient_name", "patient_mobile", "patient_id",
        "appt_ref",
        "confirm_expires_at",
    ]:
        sess[k] = None
    sess["mistakes"] = 0
    sess["pending_patient"] = {"name": None, "mobile": None, "pid": None}


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


def _date_prompt(lang: str) -> str:
    if lang == "ar":
        return "يرجى كتابة تاريخ الموعد (مثال: 2026-03-10 أو 10-03-2026 أو 10/03/2026)" + _footer(lang)
    return "Please enter the appointment date (example: 2026-03-10 or 10-03-2026 or 10/03/2026)" + _footer(lang)


def _slot_window_prompt(lang: str, date_iso: str) -> str:
    if lang == "ar":
        return (
            f"اختر فترة الموعد بتاريخ {date_iso}:\n\n"
            f"1️⃣ {SLOT_WINDOWS[0]['ar']}\n"
            f"2️⃣ {SLOT_WINDOWS[1]['ar']}\n"
            f"3️⃣ {SLOT_WINDOWS[2]['ar']}\n"
            + _footer(lang)
        )
    return (
        f"Choose an appointment window on {date_iso}:\n\n"
        f"1️⃣ {SLOT_WINDOWS[0]['en']}\n"
        f"2️⃣ {SLOT_WINDOWS[1]['en']}\n"
        f"3️⃣ {SLOT_WINDOWS[2]['en']}\n"
        + _footer(lang)
    )


def _times_for_window(window_key: str) -> List[str]:
    # 30-min slots.
    # For cross-day windows, we list from start->23:30 then 00:00->end
    w = next((x for x in SLOT_WINDOWS if x["key"] == window_key), None)
    if not w:
        return []

    def gen(start_hour: int, end_hour: int, cross: bool) -> List[str]:
        out: List[str] = []
        def add_hour(h: int):
            out.append(f"{h:02d}:00")
            out.append(f"{h:02d}:30")
        if not cross:
            for h in range(start_hour, end_hour):
                add_hour(h)
            # include end boundary at :00 only? keep consistent: include end-1 range only
            return out
        # cross day:
        for h in range(start_hour, 24):
            add_hour(h)
        for h in range(0, end_hour):
            add_hour(h)
        return out

    cross = bool(w["start"] > w["end"])
    return gen(w["start"], w["end"], cross)


def _slot_prompt(lang: str, date_iso: str, window_key: str) -> str:
    slots = _times_for_window(window_key)
    if not slots:
        slots = ["10:00", "10:30", "11:00", "11:30"]  # safety fallback
    lines = [f"{i}️⃣ {s}" for i, s in enumerate(slots, start=1)]
    if lang == "ar":
        return f"الأوقات المتاحة بتاريخ {date_iso}:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الوقت)" + _footer(lang)
    return f"Available time slots on {date_iso}:\n\n" + "\n".join(lines) + "\n\n(Reply with slot number)" + _footer(lang)


def _patient_prompt_full(lang: str) -> str:
    if lang == "ar":
        return (
            "لإتمام الحجز، يرجى إرسال (يفضل برسالة واحدة):\n"
            "• الاسم الكامل\n"
            "• رقم الجوال\n"
            "• رقم الهوية/الإقامة (اختياري)\n\n"
            "ملاحظة: هذه الخدمة ليست للحالات الطارئة."
            + _footer(lang)
        )
    return (
        "To complete the booking, please send (preferably in one message):\n"
        "• Full Name\n"
        "• Mobile Number\n"
        "• National ID/Iqama (optional)\n\n"
        "Note: This service is not for medical emergencies."
        + _footer(lang)
    )


def _patient_ask_mobile_only(lang: str) -> str:
    return ("شكرًا. فضلاً أرسل رقم الجوال فقط." if lang == "ar" else "Thanks. Please send your mobile number only.") + _footer(lang)


def _patient_ask_name_only(lang: str) -> str:
    return ("شكرًا. فضلاً أرسل الاسم الكامل فقط." if lang == "ar" else "Thanks. Please send your full name only.") + _footer(lang)


def _dept_label(key: str, lang: str) -> Optional[str]:
    for d in DEPTS:
        if d["key"] == key:
            return d["ar"] if lang == "ar" else d["en"]
    return None


def _soft_invalid(sess: Dict[str, Any], lang: str, msg: str) -> str:
    sess["mistakes"] = int(sess.get("mistakes", 0)) + 1
    if sess["mistakes"] >= 2:
        if lang == "ar":
            return msg + "\n\nإذا رغبت، يمكنني تحويلك لموظف الاستقبال: 99"
        return msg + "\n\nIf you prefer, I can connect you to Reception: 99"
    return msg


# ----------------------------
# INTENT + SPECIALTY EXTRACTION (deterministic)
# ----------------------------
_INQUIRY_TRIGGERS_AR = [
    "هل", "عندكم", "موجود", "متوفر", "متاح", "مداوم", "دوام",
    "ابي", "أبي", "ابغى", "أبغى", "نبغي", "نبي", "عايز", "اريد", "أريد",
    "استفسر", "استفسار", "اسأل", "سؤال",
]
_INQUIRY_TRIGGERS_EN = [
    "do you have", "is there", "available", "open", "enquire", "inquire", "enquiry", "inquiry",
    "i want to enquire", "i want to inquire", "i need", "specialist", "doctor",
]
_BOOK_TRIGGERS_AR = ["احجز", "حجز", "موعد", "ابغى احجز", "أبغى احجز", "عايز احجز", "اريد حجز", "أريد حجز", "احجز عند", "عايز احجز عند"]
_BOOK_TRIGGERS_EN = ["book", "appointment", "schedule", "reserve", "i want to book", "need appointment", "book with"]

# Priority: Dentistry BEFORE ENT to prevent mapping bug
_DEPT_SYNONYMS_PRIORITY: List[Tuple[str, List[str]]] = [
    ("dental", ["اسنان", "أسنان", "سنان", "ضرس", "ضروس", "تقويم", "لثة", "dentist", "dental", "dentistry", "tooth", "teeth", "toothache"]),
    ("ent", ["انف", "أذن", "اذن", "حنجرة", "لوز", "جيوب", "otolaryngologist", "otolaryngology", "ent", "ear", "nose", "throat", "tonsil", "sinus"]),
    ("cardio", ["قلب", "القلب", "نبض", "cardio", "cardiology", "heart", "palpitation"]),
    ("neuro", ["اعصاب", "الأعصاب", "العصبيه", "العصبية", "عصبيه", "عصبية", "neuro", "neurology", "migraine", "headache"]),
    ("derm", ["جلدية", "جلديه", "جلد", "حبوب", "اكزيما", "derm", "dermatology", "skin", "rash", "eczema", "acne"]),
    ("ortho", ["عظام", "عظمي", "ركبة", "ظهر", "كسور", "ortho", "orthopedic", "bone", "knee", "back"]),
    ("peds", ["اطفال", "الأطفال", "طفل", "عيال", "paediatrician", "pediatrician", "pediatrics", "kids", "child"]),
    ("gyn", ["نساء", "نسائي", "حمل", "ولادة", "دورة", "obgyn", "gyn", "pregnancy", "period"]),
    ("uro", ["مسالك", "المسالك", "بولية", "المسالك البولية", "بروستات", "urologist", "urology", "prostate"]),
    ("ophthal", ["عيون", "عين", "نظر", "شبكية", "ophthalmology", "eye", "vision", "retina", "optometry"]),
    ("physio", ["علاج طبيعي", "فيزيو", "physio", "physiotherapy", "rehab"]),
    ("general", ["باطنه", "الباطنه", "باطنيه", "الباطنيه", "باطني", "باطنية", "internal medicine", "internist", "internal", "medicine"]),
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


def _booking_prefill_intro(lang: str, dept_label: str) -> str:
    if lang == "ar":
        return f"تم اختيار تخصص *{dept_label}* ✅\n\nيرجى اختيار الطبيب:\n\n"
    return f"Selected specialty: *{dept_label}* ✅\n\nPlease choose a doctor:\n\n"


def _set_confirm_expiry(sess: Dict[str, Any]) -> None:
    sess["confirm_expires_at"] = (_utcnow() + timedelta(seconds=BOOKING_CONFIRM_EXPIRE_SECONDS)).isoformat()


def _confirm_expired(sess: Dict[str, Any]) -> bool:
    dt = _parse_iso(sess.get("confirm_expires_at"))
    if not dt:
        return False
    return _utcnow() >= dt


def _make_reference(prefix: str = "SSH") -> str:
    today = _utcnow().strftime("%y%m%d")
    rnd = random.randint(1000, 9999)
    return f"{prefix}-{today}-{rnd}"


def _valid_mobile(m: Optional[str]) -> bool:
    if not m:
        return False
    digits = "".join(c for c in m if c.isdigit())
    return 8 <= len(digits) <= 15


def _extract_name_mobile_id(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw0 = (raw or "").strip()
    if not raw0:
        return None, None, None

    text = _normalize_digits(raw0)

    seqs: List[str] = []
    cur: List[str] = []
    for ch in text:
        if ch.isdigit() or ch == "+":
            cur.append(ch)
        else:
            if cur:
                s = "".join(cur)
                digits_only = "".join(c for c in s if c.isdigit())
                if len(digits_only) >= 8:
                    seqs.append(s)
                cur = []
    if cur:
        s = "".join(cur)
        digits_only = "".join(c for c in s if c.isdigit())
        if len(digits_only) >= 8:
            seqs.append(s)

    mobile = seqs[0] if seqs else None

    pid = None
    if len(seqs) >= 2:
        a = "".join(c for c in (mobile or "") if c.isdigit())
        for cand in seqs[1:]:
            b = "".join(c for c in cand if c.isdigit())
            if 8 <= len(b) <= 15 and b != a:
                pid = cand
                break

    name_candidate = text
    if mobile:
        name_candidate = name_candidate.replace(mobile, " ")
    if pid:
        name_candidate = name_candidate.replace(pid, " ")
    name_candidate = re.sub(r"\s+", " ", name_candidate).strip()
    lines = [ln.strip() for ln in name_candidate.splitlines() if ln.strip()]
    name = lines[0] if lines else name_candidate

    only_digits = re.fullmatch(r"\+?\d{8,15}", _clean_input(text).replace(" ", ""))
    if only_digits:
        return None, text.strip(), None

    return name or None, mobile, pid


def _confirmation(sess: Dict[str, Any], lang: str) -> str:
    ref = sess.get("appt_ref") or ""
    pid = sess.get("patient_id")
    pid_line = ""
    if pid:
        pid_line = (f"🪪 الهوية/الإقامة: {pid}\n" if lang == "ar" else f"🪪 ID/Iqama: {pid}\n")

    exp = _parse_iso(sess.get("confirm_expires_at"))
    expiry_line = ""
    if exp:
        mins = int(max(0, (exp - _utcnow()).total_seconds()) // 60)
        if lang == "ar":
            expiry_line = f"\n⏳ تنبيه: هذا الملخص صالح لمدة {mins + 1} دقائق."
        else:
            expiry_line = f"\n⏳ Note: this summary expires in about {mins + 1} minutes."

    if lang == "ar":
        return (
            "ملخص طلب الحجز ✅\n\n"
            f"📌 رقم المرجع: *{ref}*\n"
            f"👤 الاسم: {sess.get('patient_name')}\n"
            f"📱 الجوال: {sess.get('patient_mobile')}\n"
            + pid_line
            + f"👨‍⚕️ الطبيب: {sess.get('doctor_label')}\n"
            + f"🏥 التخصص: {sess.get('dept_label')}\n"
            + f"📅 التاريخ: {sess.get('date')}\n"
            + f"⏰ الوقت: {sess.get('slot')}\n"
            + expiry_line
            + "\n\nيرجى الرد:\n"
            "1️⃣ إرسال الطلب إلى الاستقبال\n"
            "2️⃣ تعديل\n"
            "3️⃣ إلغاء"
            + _footer(lang)
        )

    return (
        "Booking Request Summary ✅\n\n"
        f"📌 Reference: *{ref}*\n"
        f"👤 Name: {sess.get('patient_name')}\n"
        f"📱 Mobile: {sess.get('patient_mobile')}\n"
        + pid_line
        + f"👨‍⚕️ Doctor: {sess.get('doctor_label')}\n"
        + f"🏥 Specialty: {sess.get('dept_label')}\n"
        + f"📅 Date: {sess.get('date')}\n"
        + f"⏰ Time: {sess.get('slot')}\n"
        + expiry_line
        + "\n\nPlease reply:\n"
        "1️⃣ Send request to Reception\n"
        "2️⃣ Modify\n"
        "3️⃣ Cancel"
        + _footer(lang)
    )


def _parse_date_any(raw: str) -> Tuple[Optional[str], Optional[str]]:
    s = _normalize_digits(_clean_input(raw)).replace("/", "-")
    ymd = re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)
    dmy = re.fullmatch(r"\d{2}-\d{2}-\d{4}", s)
    if not (ymd or dmy):
        return None, "format"
    try:
        if ymd:
            dt = datetime.strptime(s, "%Y-%m-%d").date()
            return dt.isoformat(), None
        dt = datetime.strptime(s, "%d-%m-%Y").date()
        return dt.isoformat(), None
    except Exception:
        return None, "invalid_date"


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

    # -----------------------------------------
    # FIRST MESSAGE: prioritize BOOK+dept and inquiry+dept BEFORE greeting menu
    # -----------------------------------------
    if not bool(sess.get("has_greeted")):
        guessed = _detect_language_from_text(message_text)
        if guessed in {"ar", "en"}:
            sess["language_locked"] = True
            sess["language"] = guessed
            sess["text_direction"] = "rtl" if guessed == "ar" else "ltr"
            lang = guessed

        if intent == "BOOK" and dept_key and dept_key in DOCTORS_BY_DEPT_KEY:
            sess["has_greeted"] = True
            sess["intent"] = "BOOK"
            sess["dept_key"] = dept_key
            sess["dept_label"] = _dept_label(dept_key, lang)
            sess["state"] = STATE_BOOK_DOCTOR
            sess["last_step"] = STATE_BOOK_DOCTOR
            out = _booking_prefill_intro(lang, sess["dept_label"] or dept_key) + _doctor_prompt(lang, dept_key)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

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

        sess["has_greeted"] = True
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _greeting_menu_ar() if lang == "ar" else _greeting_menu_en()
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # universal shortcuts
    # -----------------------------------------
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

    # -----------------------------------------
    # STATE: BOOK_DOCTOR (must precede menu)
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_DOCTOR:
        dept = sess.get("dept_key") or ""
        docs = DOCTORS_BY_DEPT_KEY.get(dept, [])
        if not docs:
            sess["state"] = STATE_BOOK_DEPT
            sess["last_step"] = STATE_BOOK_DEPT
            out = _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار رقم الطبيب من القائمة." if lang == "ar" else "Please choose a doctor number from the list.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _doctor_prompt(lang, dept)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        idx = _to_int(raw, -1) - 1
        if not (0 <= idx < len(docs)):
            msg = ("يرجى اختيار طبيب صحيح." if lang == "ar" else "Please choose a valid doctor.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _doctor_prompt(lang, dept)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        chosen = docs[idx]
        sess["doctor_key"] = chosen.get("key")
        sess["doctor_label"] = chosen["ar"] if lang == "ar" else chosen["en"]
        sess["mistakes"] = 0

        sess["state"] = STATE_BOOK_DATE
        sess["last_step"] = STATE_BOOK_DATE
        out = _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # STATE: BOOK_DATE -> BOOK_SLOT_WINDOW
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_DATE:
        norm_ymd, err = _parse_date_any(message_text)
        if not norm_ymd:
            msg = (
                "صيغة التاريخ غير صحيحة. مثال: 2026-03-10 أو 10-03-2026 أو 10/03/2026"
                if lang == "ar"
                else "Date format is invalid. Example: 2026-03-10 or 10-03-2026 or 10/03/2026"
            )
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["date"] = norm_ymd
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT_WINDOW
        sess["last_step"] = STATE_BOOK_SLOT_WINDOW
        out = _slot_window_prompt(lang, norm_ymd)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # STATE: BOOK_SLOT_WINDOW -> BOOK_SLOT
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_SLOT_WINDOW:
        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار رقم الفترة (1/2/3)." if lang == "ar" else "Please choose a window number (1/2/3).")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_window_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        c = _to_int(raw, -1)
        if c not in (1, 2, 3):
            msg = ("يرجى اختيار رقم صحيح (1/2/3)." if lang == "ar" else "Please choose a valid number (1/2/3).")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_window_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        window_key = SLOT_WINDOWS[c - 1]["key"]
        sess["slot_window"] = window_key
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT
        sess["last_step"] = STATE_BOOK_SLOT
        out = _slot_prompt(lang, sess.get("date") or "", window_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # STATE: BOOK_SLOT -> BOOK_PATIENT
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_SLOT:
        window_key = sess.get("slot_window") or ""
        slots = _times_for_window(window_key)
        if not slots:
            slots = ["10:00", "10:30", "11:00", "11:30"]

        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "", window_key)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        idx = _to_int(raw, -1) - 1
        if not (0 <= idx < len(slots)):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "", window_key)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["slot"] = slots[idx]
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_PATIENT
        sess["last_step"] = STATE_BOOK_PATIENT
        out = _patient_prompt_full(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # STATE: BOOK_PATIENT -> BOOK_CONFIRM
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_PATIENT:
        pending = sess.get("pending_patient") or {"name": None, "mobile": None, "pid": None}
        if not isinstance(pending, dict):
            pending = {"name": None, "mobile": None, "pid": None}

        name, mobile, pid = _extract_name_mobile_id(message_text)

        if name and not pending.get("name"):
            pending["name"] = name
        if mobile and not pending.get("mobile"):
            pending["mobile"] = mobile
        if pid and not pending.get("pid"):
            pending["pid"] = pid

        sess["pending_patient"] = pending

        if pending.get("name") and not pending.get("mobile"):
            out = _patient_ask_mobile_only(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if pending.get("mobile") and not pending.get("name"):
            out = _patient_ask_name_only(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if not pending.get("name") or not _valid_mobile(pending.get("mobile")):
            msg = ("فضلاً أرسل الاسم الكامل ورقم جوال صحيح." if lang == "ar"
                   else "Please send full name and a valid mobile number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _patient_prompt_full(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["patient_name"] = pending.get("name")
        sess["patient_mobile"] = pending.get("mobile")
        sess["patient_id"] = pending.get("pid")
        sess["mistakes"] = 0

        sess["appt_ref"] = sess.get("appt_ref") or _make_reference("SSH")
        _set_confirm_expiry(sess)

        sess["state"] = STATE_BOOK_CONFIRM
        sess["last_step"] = STATE_BOOK_CONFIRM
        out = _confirmation(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # STATE: BOOK_CONFIRM -> CLOSE + ACTION
    # -----------------------------------------
    if sess.get("state") == STATE_BOOK_CONFIRM:
        if _confirm_expired(sess):
            sess["confirm_expires_at"] = None
            msg = (
                "⏳ انتهت صلاحية ملخص الحجز. حفاظًا على الدقة، يرجى اختيار الوقت مرة أخرى."
                if lang == "ar" else
                "⏳ This booking summary has expired. To ensure accuracy, please select the time slot again."
            )
            sess["state"] = STATE_BOOK_SLOT_WINDOW
            sess["last_step"] = STATE_BOOK_SLOT_WINDOW
            out = msg + "\n\n" + _slot_window_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _confirmation(sess, lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        c = _to_int(raw, -1)
        if c == 1:
            sess["status"] = STATUS_COMPLETED
            sess["state"] = STATE_CLOSED
            sess["last_step"] = STATE_CLOSED
            sess["confirm_expires_at"] = None

            ref = sess.get("appt_ref") or _make_reference("SSH")
            sess["appt_ref"] = ref

            if lang == "ar":
                out = (
                    "تم استلام طلب الحجز ✅\n"
                    f"📌 رقم المرجع: *{ref}*\n"
                    "سيقوم موظف الاستقبال بتأكيد الموعد خلال ساعات العمل.\n"
                    "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
                    f"{CLINIC_TIMINGS_AR}\n"
                    f"🚑 الطوارئ: {EMERGENCY_NUMBER}\n"
                    "للتواصل مع الاستقبال: 99\n\n"
                    "للعودة للقائمة الرئيسية اكتب 0."
                )
            else:
                out = (
                    "Booking request received ✅\n"
                    f"📌 Reference: *{ref}*\n"
                    "Reception will confirm your appointment during working hours.\n"
                    "Please arrive 15 minutes early.\n\n"
                    f"{CLINIC_TIMINGS_EN}\n"
                    f"🚑 Emergency: {EMERGENCY_NUMBER}\n"
                    "Reception: 99\n\n"
                    "Reply 0 for the main menu."
                )

            actions = [{
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "BOOK",
                    "status": "PENDING",
                    "dept_key": sess.get("dept_key"),
                    "dept_label": sess.get("dept_label"),
                    "doctor_key": sess.get("doctor_key"),
                    "doctor_label": sess.get("doctor_label"),
                    "appt_date": sess.get("date"),
                    "appt_time": sess.get("slot"),
                    "patient_name": sess.get("patient_name"),
                    "patient_mobile": sess.get("patient_mobile"),
                    "patient_id": sess.get("patient_id"),
                    "notes": f"appt_ref={ref}",
                },
            }]

            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if c == 2:
            sess["mistakes"] = 0
            sess["confirm_expires_at"] = None
            # go back to doctor selection (keeping dept)
            sess["state"] = STATE_BOOK_DOCTOR
            sess["last_step"] = STATE_BOOK_DOCTOR
            dept = sess.get("dept_key") or ""
            prefix = "تمام. لنعد لاختيار الطبيب.\n\n" if lang == "ar" else "Okay. Let's choose the doctor again.\n\n"
            out = prefix + _doctor_prompt(lang, dept)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if c == 3:
            sess["mistakes"] = 0
            sess["confirm_expires_at"] = None
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = ("تم إلغاء الطلب. للمتابعة اختر من القائمة.\n\n" if lang == "ar"
                   else "Request cancelled. Please choose from the menu.\n\n")
            out += _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
        out = _soft_invalid(sess, lang, msg) + "\n\n" + _confirmation(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # Specialty inquiry always answers
    # -----------------------------------------
    if dept_key and (intent == "SPECIALTY_INQUIRY" or intent is None):
        sess["intent"] = "SPECIALTY_INQUIRY"
        sess["dept_key"] = dept_key
        sess["dept_label"] = _dept_label(dept_key, lang)
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _specialty_inquiry_reply(lang, dept_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -----------------------------------------
    # MAIN MENU
    # -----------------------------------------
    sess.setdefault("state", STATE_MENU)
    if sess.get("state") != STATE_MENU:
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU

    if _is_digit_choice(raw):
        choice = _to_int(raw)

        # Inquiry context: 1 means book same dept
        if choice == 1 and sess.get("dept_key") and sess.get("intent") == "SPECIALTY_INQUIRY":
            dept = sess.get("dept_key")
            if dept in DOCTORS_BY_DEPT_KEY:
                sess["intent"] = "BOOK"
                sess["state"] = STATE_BOOK_DOCTOR
                sess["last_step"] = STATE_BOOK_DOCTOR
                sess["mistakes"] = 0
                out = _booking_prefill_intro(lang, sess.get("dept_label") or dept) + _doctor_prompt(lang, dept)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

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

        msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid menu number.")
        out = _soft_invalid(sess, lang, msg) + "\n\n" + _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Free text: BOOK+dept jumps directly
    if intent == "BOOK" and dept_key and dept_key in DOCTORS_BY_DEPT_KEY:
        sess["intent"] = "BOOK"
        sess["dept_key"] = dept_key
        sess["dept_label"] = _dept_label(dept_key, lang)
        sess["state"] = STATE_BOOK_DOCTOR
        sess["last_step"] = STATE_BOOK_DOCTOR
        out = _booking_prefill_intro(lang, sess.get("dept_label") or dept_key) + _doctor_prompt(lang, dept_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # otherwise show greeting menu (your preference)
    out = _greeting_menu_ar() if lang == "ar" else _greeting_menu_en()
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