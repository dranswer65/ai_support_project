# core/engine.py — Clinic Booking Demo Engine
# Arabic-first, strict menu, AI-assisted discovery, tenant-ready keys (dept_key/doctor_key)
# Fixes:
# - Greeting detection (won’t be treated as invalid input)
# - Arabic-Indic digits supported (١٢٣ -> 123)
# - No “ID copied from mobile”
# - “Thanks/شكراً” after CLOSED -> polite close (no forced numeric)
# - Session expiry: long inactivity resets to menu
# - Less clutter: footer shown mostly during flows

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone

# ----------------------------
# States
# ----------------------------
STATE_ACTIVE = "ACTIVE"
STATE_MENU = "MENU"

STATE_BOOK_DEPT = "BOOK_DEPT"
STATE_BOOK_DOCTOR = "BOOK_DOCTOR"
STATE_BOOK_DATE = "BOOK_DATE"
STATE_BOOK_SLOT = "BOOK_SLOT"
STATE_BOOK_PATIENT = "BOOK_PATIENT"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"

STATE_RESCHEDULE_LOOKUP = "RESCHEDULE_LOOKUP"
STATE_RESCHEDULE_NEW_DATE = "RESCHEDULE_NEW_DATE"
STATE_RESCHEDULE_NEW_SLOT = "RESCHEDULE_NEW_SLOT"
STATE_RESCHEDULE_CONFIRM = "RESCHEDULE_CONFIRM"

STATE_CANCEL_LOOKUP = "CANCEL_LOOKUP"
STATE_CANCEL_CONFIRM = "CANCEL_CONFIRM"

STATE_CLOSED = "CLOSED"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "CLINIC_BOOKING_ENGINE_V1"

# ----------------------------
# Timing rules (no scheduler; evaluated only when user sends message)
# ----------------------------
PASSIVE_TIMEOUT_SECONDS = 5 * 60      # show "continue" prompt after 5 min inactivity (in-flow only)
SESSION_EXPIRE_SECONDS = 60 * 60      # after 60 min, reset to main menu (any state)

# ----------------------------
# Demo catalog
# ----------------------------
CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"

RECEPTION_PHONE = "055500000000"  # show once in greeting

DEPTS = [
    {"key": "general", "en": "General Medicine", "ar": "الطب العام"},
    {"key": "peds", "en": "Pediatrics", "ar": "طب الأطفال"},
    {"key": "gyn", "en": "Gynecology", "ar": "أمراض النساء والتوليد"},
    {"key": "ortho", "en": "Orthopedics", "ar": "جراحة العظام"},
    {"key": "derm", "en": "Dermatology", "ar": "الأمراض الجلدية"},
    {"key": "ent", "en": "ENT", "ar": "الأنف والأذن والحنجرة"},
    {"key": "cardio", "en": "Cardiology", "ar": "أمراض القلب"},
    {"key": "dental", "en": "Dentistry", "ar": "طب الأسنان"},
    {"key": "neuro", "en": "Neurology", "ar": "الأعصاب"},
    {"key": "physio", "en": "Physiotherapy", "ar": "العلاج الطبيعي"},
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
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "17:00", "17:30", "18:00", "18:30"]

CLINIC_TIMINGS_AR = "مواعيد العمل: يوميًا من 9:00 صباحًا إلى 9:00 مساءً (عدا الجمعة)."
CLINIC_TIMINGS_EN = "Hospital hours: daily 9:00 AM to 9:00 PM (except Friday)."

INSURANCE_AR = "التأمينات المعتمدة: بوبا، التعاونية، ميدغلف (مثال)."
INSURANCE_EN = "Approved insurances: Bupa, Tawuniya, Medgulf (example)."

# ----------------------------
# Result type
# ----------------------------
@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]

# ----------------------------
# Helpers
# ----------------------------
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩١٢٣٤٥٦٧٨٩", "01234567890123456789")
# Note: includes both Arabic-Indic (٠١٢...) and Eastern Arabic-Indic (١٢٣...)

def _normalize_digits(s: str) -> str:
    return (s or "").translate(_ARABIC_DIGITS)

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _norm(t: str) -> str:
    return (t or "").strip()

def _norm_low(t: str) -> str:
    return _normalize_digits((t or "").strip().lower())

def _lang(lang: str) -> str:
    l = (lang or "").strip().lower()
    return "ar" if l.startswith("ar") else "en"

def _is_digit_choice(t: str) -> bool:
    return _norm_low(t).isdigit()

def _to_int(t: str, default: int = -1) -> int:
    try:
        return int(_norm_low(t))
    except Exception:
        return default

def _is_goodbye(t: str) -> bool:
    tl = _norm_low(t)
    return tl in {"bye", "goodbye", "see you", "مع السلامة", "سلام", "الى اللقاء", "إلى اللقاء", "باي"}

def _is_thanks(t: str) -> bool:
    tl = _norm_low(t)
    return tl in {"thanks", "thank you", "thx", "شكرا", "شكرًا", "مشكور", "الله يعطيك العافية", "شكراً"}

def _is_ack(t: str) -> bool:
    tl = _norm_low(t)
    return tl in {"ok", "okay", "sure", "alright", "done", "تمام", "تم", "اوكي", "حسنًا", "حسنا", "أكيد"}

def _is_greeting(t: str) -> bool:
    tl = _norm_low(t)
    # keep short, avoid false positives
    en = {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}
    ar = {"السلام عليكم", "السلام عليكم ورحمة الله", "السلام عليكم ورحمه الله", "مرحبا", "أهلا", "اهلا", "هلا", "صباح الخير", "مساء الخير"}
    if tl in en:
        return True
    return any(p in tl for p in ar)

def _set_bot(sess: Dict[str, Any], msg: str) -> None:
    sess["last_bot_message"] = msg
    sess["last_bot_ts"] = _utcnow_iso()

def _utility_footer(lang: str) -> str:
    # show in flows; not needed in every single menu message
    if lang == "ar":
        return "\n\n0️⃣ القائمة الرئيسية\n9️⃣ موظف الاستقبال"
    return "\n\n0️⃣ Main Menu\n9️⃣ Reception"

def _closing(lang: str) -> str:
    if lang == "ar":
        return f"نشكر لكم اختيار *{CLINIC_NAME_AR}*.\nنتمنى لكم دوام الصحة والعافية ونسعد بخدمتكم دائماً."
    return f"Thank you for choosing *{CLINIC_NAME_EN}*.\nWe wish you good health and look forward to serving you."

def _emergency_warning(lang: str) -> str:
    if lang == "ar":
        return (
            "تنبيه مهم:\n"
            "في حال وجود حالة طبية طارئة، يرجى الاتصال بخدمات الطوارئ فوراً أو التوجه إلى أقرب مستشفى.\n"
            "هذه الخدمة غير مخصصة للحالات الطارئة."
        )
    return (
        "Important notice:\n"
        "If you are experiencing a medical emergency, please contact emergency services immediately or visit the nearest hospital.\n"
        "This WhatsApp service is not intended for emergency medical situations."
    )

def _greet_if_needed(sess: Dict[str, Any], lang: str, msg: str) -> str:
    if sess.get("has_greeted"):
        return msg
    sess["has_greeted"] = True
    if lang == "ar":
        return (
            f"أهلاً بكم في *{CLINIC_NAME_AR}*.\n"
            "نشكركم على تواصلكم معنا.\n"
            "أنا المساعد الافتراضي للمستشفى.\n"
            f"إذا رغبت بالتواصل المباشر مع الاستقبال: *{RECEPTION_PHONE}*\n\n"
            f"{msg}"
        )
    return (
        f"Welcome to *{CLINIC_NAME_EN}*.\n"
        "Thank you for contacting us.\n"
        "I am the hospital virtual assistant.\n"
        f"If you need Reception directly: *{RECEPTION_PHONE}*\n\n"
        f"{msg}"
    )

def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "engine": ENGINE_MARKER,
        "user_id": user_id,
        "state": STATE_ACTIVE,
        "language": "ar",
        "text_direction": "rtl",
        "has_greeted": False,
        "menu_shown": False,
        "mistakes": 0,
        "timeout_pending": False,
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
        "patient_mobile": None,
        "patient_id": None,
        "patient_name": None,
        "appt_ref": None,
        "last_closed_at": None,
    }

def _reset_flow_fields(sess: Dict[str, Any]) -> None:
    for k in [
        "intent",
        "dept_key", "dept_label",
        "doctor_key", "doctor_label",
        "date", "slot",
        "patient_name", "patient_mobile", "patient_id",
        "appt_ref",
    ]:
        sess[k] = None
    sess["mistakes"] = 0
    sess["timeout_pending"] = False

def _soft_invalid(sess: Dict[str, Any], lang: str, msg: str) -> str:
    sess["mistakes"] = int(sess.get("mistakes", 0)) + 1
    if sess["mistakes"] >= 2:
        if lang == "ar":
            return msg + "\n\nإذا رغبت، يمكنني تحويلك لموظف الاستقبال: 9️⃣"
        return msg + "\n\nIf you prefer, I can transfer you to Reception: 9️⃣"
    return msg

# ----------------------------
# Intent discovery
# ----------------------------
def _looks_like_emergency(text: str) -> bool:
    t = _norm_low(text)
    emergency_keys = [
        "chest pain", "shortness of breath", "severe bleeding", "unconscious", "stroke", "heart attack",
        "emergency", "bleeding", "seizure",
        "ألم صدر", "ضيق تنفس", "نزيف شديد", "فاقد الوعي", "جلطة", "سكتة", "نوبة قلبية", "طارئ", "نزيف", "اختلاج",
    ]
    return any(k in t for k in emergency_keys)

def _looks_like_angry(text: str) -> bool:
    t = _norm_low(text)
    bad = ["stupid", "idiot", "scam", "fraud", "shit", "fuck", "bitch"]
    bad_ar = ["نصب", "احتيال", "غبي", "سيء", "زبالة", "لعنة"]
    return any(w in t for w in bad) or any(w in t for w in bad_ar)

def _classify_intent(text: str) -> str:
    t = _norm_low(text)

    if t in {"9", "reception", "human", "agent", "موظف", "الاستقبال", "موظف الاستقبال"}:
        return "RECEPTION"

    if any(k in t for k in ["insurance", "insurances", "approved insurance", "coverage", "tawuniya", "bupa"]):
        return "INSURANCE"
    if any(k in t for k in ["تأمين", "التأمين", "التأمينات", "التأمينات المعتمدة", "بوبا", "التعاونية", "ميدغلف"]):
        return "INSURANCE"

    if any(k in t for k in ["timings", "hours", "working hours", "clinic hours", "open", "close"]):
        return "TIMINGS"
    if any(k in t for k in ["مواعيد العمل", "ساعات العمل", "دوام", "متى تفتح", "متى تغلق"]):
        return "TIMINGS"

    if any(k in t for k in ["doctor info", "doctors", "doctor information", "doctor list"]):
        return "DOCTOR_INFO"
    if any(k in t for k in ["معلومات الطبيب", "الأطباء", "معلومات عن الأطباء", "قائمة الأطباء"]):
        return "DOCTOR_INFO"

    if any(k in t for k in ["reschedule", "change appointment", "move appointment"]):
        return "RESCHEDULE"
    if any(k in t for k in ["تعديل", "تغيير", "تأجيل", "تعديل موعد", "تغيير موعد"]):
        return "RESCHEDULE"

    if any(k in t for k in ["cancel appointment", "cancel", "delete appointment"]):
        return "CANCEL"
    if any(k in t for k in ["إلغاء", "الغاء", "إلغاء موعد", "الغاء موعد"]):
        return "CANCEL"

    if any(k in t for k in ["book", "booking", "appointment", "schedule", "see a doctor", "visit"]):
        return "BOOK"
    if any(k in t for k in ["حجز", "موعد", "احجز", "أريد حجز", "اريد حجز", "حجز موعد"]):
        return "BOOK"

    return "UNKNOWN"

# ----------------------------
# Prompts
# ----------------------------
def _main_menu_text(lang: str) -> str:
    if lang == "ar":
        return (
            "كيف يمكنني خدمتك اليوم؟\n\n"
            "1️⃣ حجز موعد\n"
            "2️⃣ تعديل موعد\n"
            "3️⃣ إلغاء موعد\n"
            "4️⃣ معلومات عن الأطباء\n"
            "5️⃣ مواعيد العمل\n"
            "6️⃣ التأمينات المعتمدة\n"
            "9️⃣ التحدث مع موظف الاستقبال"
        )
    return (
        "How may I assist you today?\n\n"
        "1️⃣ Book Appointment\n"
        "2️⃣ Reschedule Appointment\n"
        "3️⃣ Cancel Appointment\n"
        "4️⃣ Doctor Information\n"
        "5️⃣ Hospital Timings\n"
        "6️⃣ Approved Insurances\n"
        "9️⃣ Speak to Reception"
    )

def _dept_prompt(lang: str) -> str:
    lines = [f"{i}. {d['ar'] if lang=='ar' else d['en']}" for i, d in enumerate(DEPTS, start=1)]
    if lang == "ar":
        return "يرجى اختيار القسم الطبي الذي ترغب في زيارته:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الخيار)"
    return "Please select the department you want to visit:\n\n" + "\n".join(lines) + "\n\n(Reply with number)"

def _doctor_prompt(lang: str, dept_key: str) -> str:
    docs = DOCTORS_BY_DEPT_KEY.get(dept_key, [])
    lines = [f"{i}. {doc['ar'] if lang=='ar' else doc['en']}" for i, doc in enumerate(docs, start=1)]
    if lang == "ar":
        return "الأطباء المتاحون في هذا القسم هم:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الخيار)"
    return "Available doctors in this department:\n\n" + "\n".join(lines) + "\n\n(Reply with number)"

def _date_prompt(lang: str) -> str:
    if lang == "ar":
        return "يرجى كتابة التاريخ المناسب للموعد (مثال: 2026-02-25)"
    return "Please enter your preferred appointment date (example: 2026-02-25)"

def _slot_prompt(lang: str, date_str: str) -> str:
    if not SLOTS:
        if lang == "ar":
            return f"لا توجد مواعيد متاحة بتاريخ {date_str} حالياً. يرجى اختيار تاريخ آخر."
        return f"No slots are available on {date_str} at the moment. Please choose another date."

    lines = [f"{i}. {s}" for i, s in enumerate(SLOTS, start=1)]
    if lang == "ar":
        return f"المواعيد المتاحة بتاريخ {date_str} هي:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الخيار)"
    return f"Available time slots on {date_str}:\n\n" + "\n".join(lines) + "\n\n(Reply with number)"

def _patient_info_prompt(lang: str) -> str:
    if lang == "ar":
        return (
            "لإتمام الحجز، يرجى تزويدنا بالبيانات التالية (يفضل إرسالها في رسالة واحدة):\n"
            "• الاسم الكامل\n"
            "• رقم الجوال\n"
            "• رقم الهوية / الإقامة (اختياري)\n"
            "\nملاحظة: هذه الخدمة ليست مخصصة للحالات الطارئة."
        )
    return (
        "To proceed with the booking, please share (preferably in one message):\n"
        "• Full Name\n"
        "• Mobile Number\n"
        "• National ID / Iqama (optional)\n"
        "\nNote: This service is not for medical emergencies."
    )

def _insurance_text(lang: str) -> str:
    return INSURANCE_AR if lang == "ar" else INSURANCE_EN

def _timings_text(lang: str) -> str:
    if lang == "ar":
        return CLINIC_TIMINGS_AR + "\n" + INSURANCE_AR
    return CLINIC_TIMINGS_EN + "\n" + INSURANCE_EN

def _build_confirmation(sess: Dict[str, Any], lang: str) -> str:
    pid = sess.get("patient_id")
    pid_line = ""
    if pid:
        pid_line = (f"🪪 الهوية/الإقامة: {pid}\n" if lang == "ar" else f"🪪 ID/Iqama: {pid}\n")

    if lang == "ar":
        return (
            "تأكيد الحجز ✅\n"
            f"👤 الاسم: {sess.get('patient_name')}\n"
            f"📱 الجوال: {sess.get('patient_mobile')}\n"
            + pid_line
            + f"👨‍⚕️ الطبيب: {sess.get('doctor_label')}\n"
            + f"🏥 القسم: {sess.get('dept_label')}\n"
            + f"📅 التاريخ: {sess.get('date')}\n"
            + f"⏰ الوقت: {sess.get('slot')}\n\n"
            "يرجى الرد:\n"
            "1️⃣ تأكيد\n"
            "2️⃣ تعديل\n"
            "3️⃣ إلغاء"
        )

    return (
        "Confirm booking ✅\n"
        f"👤 Name: {sess.get('patient_name')}\n"
        f"📱 Mobile: {sess.get('patient_mobile')}\n"
        + pid_line
        + f"👨‍⚕️ Doctor: {sess.get('doctor_label')}\n"
        + f"🏥 Department: {sess.get('dept_label')}\n"
        + f"📅 Date: {sess.get('date')}\n"
        + f"⏰ Time: {sess.get('slot')}\n\n"
        "Please reply:\n"
        "1️⃣ Confirm\n"
        "2️⃣ Change\n"
        "3️⃣ Cancel"
    )

def _step_prompt(sess: Dict[str, Any], lang: str) -> str:
    st = sess.get("state")
    if st == STATE_BOOK_DEPT:
        return _dept_prompt(lang) + _utility_footer(lang)
    if st == STATE_BOOK_DOCTOR:
        return _doctor_prompt(lang, sess.get("dept_key") or "") + _utility_footer(lang)
    if st == STATE_BOOK_DATE:
        return _date_prompt(lang) + _utility_footer(lang)
    if st == STATE_BOOK_SLOT:
        return _slot_prompt(lang, sess.get("date") or "") + _utility_footer(lang)
    if st == STATE_BOOK_PATIENT:
        return _patient_info_prompt(lang) + _utility_footer(lang)
    if st == STATE_BOOK_CONFIRM:
        return _build_confirmation(sess, lang) + _utility_footer(lang)

    if st == STATE_RESCHEDULE_LOOKUP:
        return (("يرجى تزويدنا برقم الموعد أو رقم الجوال المسجل للمتابعة." if lang == "ar"
                 else "Please share your appointment reference or registered phone number to proceed.")
                + _utility_footer(lang))
    if st == STATE_RESCHEDULE_NEW_DATE:
        return (("يرجى اختيار التاريخ الجديد للموعد (مثال: 2026-02-25)" if lang == "ar"
                 else "Please enter the new date for the appointment (example: 2026-02-25)")
                + _utility_footer(lang))
    if st == STATE_RESCHEDULE_NEW_SLOT:
        return _slot_prompt(lang, sess.get("date") or "") + _utility_footer(lang)

    if st == STATE_CANCEL_LOOKUP:
        return (("يرجى تزويدنا برقم الموعد أو رقم الجوال المسجل لإتمام الإلغاء." if lang == "ar"
                 else "Please share your appointment reference or registered phone number to cancel.")
                + _utility_footer(lang))

    return _main_menu_text(lang)

# ----------------------------
# Patient info parsing (safe)
# Fix: do NOT auto-set ID from mobile
# ----------------------------
def _extract_name_mobile_id(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw0 = (raw or "").strip()
    if not raw0:
        return None, None, None

    raw = _normalize_digits(raw0)

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    name = lines[0] if lines else raw

    # collect digit sequences (8+ digits) and keep order
    seqs: List[str] = []
    cur = []
    for ch in raw:
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
    # only set ID if a SECOND distinct sequence exists (and differs from mobile)
    if len(seqs) >= 2:
        a = "".join(c for c in (mobile or "") if c.isdigit())
        for cand in seqs[1:]:
            b = "".join(c for c in cand if c.isdigit())
            if 8 <= len(b) <= 15 and b != a:
                pid = cand
                break

    # also allow explicit ID keywords in text
    low = _norm_low(raw0)
    if pid is None and any(k in low for k in ["iqama", "id", "national id", "هوية", "الإقامة", "اقامة", "رقم الهوية"]):
        # pick last 8-15 digit sequence if exists and not equal to mobile
        a = "".join(c for c in (mobile or "") if c.isdigit())
        for cand in reversed(seqs):
            b = "".join(c for c in cand if c.isdigit())
            if 8 <= len(b) <= 15 and b != a:
                pid = cand
                break

    return name, mobile, pid

# ----------------------------
# Timeout / expiry
# ----------------------------
def _seconds_since_last_user(sess: Dict[str, Any]) -> Optional[float]:
    last = _parse_iso(sess.get("last_user_ts"))
    if not last:
        return None
    return (datetime.now(timezone.utc) - last).total_seconds()

def _passive_timeout_detect(sess: Dict[str, Any]) -> bool:
    st = sess.get("state")
    if not st or st in {STATE_ACTIVE, STATE_MENU, STATE_CLOSED, STATE_ESCALATION}:
        return False
    sec = _seconds_since_last_user(sess)
    if sec is None:
        return False
    return sec >= PASSIVE_TIMEOUT_SECONDS

def _session_expired(sess: Dict[str, Any]) -> bool:
    sec = _seconds_since_last_user(sess)
    if sec is None:
        return False
    return sec >= SESSION_EXPIRE_SECONDS

def _timeout_prompt(lang: str) -> str:
    if lang == "ar":
        return (
            "لاحظنا عدم وجود رد خلال الفترة الماضية.\n"
            "يرجى الرد:\n"
            "1️⃣ متابعة\n"
            "0️⃣ القائمة الرئيسية\n"
            "9️⃣ موظف الاستقبال"
        )
    return (
        "It looks like there was no response for a while.\n"
        "Please reply:\n"
        "1️⃣ Continue\n"
        "0️⃣ Main Menu\n"
        "9️⃣ Reception"
    )

# ----------------------------
# Global commands
# ----------------------------
def _handle_global_commands(sess: Dict[str, Any], text: str, lang: str) -> Optional[EngineResult]:
    tlow = _norm_low(text)

    if tlow == "0":
        sess["state"] = STATE_MENU
        sess["mistakes"] = 0
        sess["timeout_pending"] = False
        out = _main_menu_text(lang)
        out = _greet_if_needed(sess, lang, out)
        _set_bot(sess, out)
        sess["menu_shown"] = True
        return EngineResult(out, sess, [])

    if tlow == "9":
        sess["state"] = STATE_ESCALATION
        sess["timeout_pending"] = False
        if lang == "ar":
            out = "يتم حالياً تحويل طلبكم إلى موظف الاستقبال.\nسيتم الرد عليكم في أقرب وقت خلال ساعات العمل الرسمية."
        else:
            out = "Your request is being transferred to our reception team.\nA staff member will respond shortly during working hours."
        _set_bot(sess, out)
        return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

    if _is_goodbye(text):
        sess["state"] = STATE_CLOSED
        sess["last_closed_at"] = _utcnow_iso()
        sess["timeout_pending"] = False
        out = _closing(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    return None

# ----------------------------
# Main turn handler
# ----------------------------
def handle_turn(
    user_id: str,
    message_text: str,
    language: str,
    session_in: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    lang = _lang(language or sess.get("language") or "ar")
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    raw = _norm(message_text)
    tlow = _norm_low(message_text)
    actions: List[Dict[str, Any]] = []

    # --- session expiry: after long inactivity, reset to menu
    if _session_expired(sess) and sess.get("state") not in {STATE_CLOSED, STATE_ESCALATION}:
        _reset_flow_fields(sess)
        sess["state"] = STATE_MENU
        sess["timeout_pending"] = False
        out = ("تم إنهاء الجلسة بسبب عدم النشاط. يرجى اختيار من القائمة للمتابعة."
               if lang == "ar"
               else "Your session expired due to inactivity. Please choose from the menu to continue.")
        out = _greet_if_needed(sess, lang, out + "\n\n" + _main_menu_text(lang))
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # Emergency / anger (always first)
    if _looks_like_emergency(message_text):
        out = _greet_if_needed(sess, lang, _emergency_warning(lang))
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    if _looks_like_angry(message_text):
        sess["state"] = STATE_ESCALATION
        if lang == "ar":
            out = "نفهم انزعاجكم، ونعتذر عن أي إزعاج.\nسيتم تحويلكم الآن إلى موظف الاستقبال للمساعدة."
        else:
            out = "I understand your frustration, and I’m sorry for the inconvenience.\nI’m transferring you to Reception to assist you."
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "anger_detected"}])

    # If user sends greeting at any time -> reset to menu (unless they are mid-confirm step)
    if _is_greeting(message_text) and sess.get("state") not in {STATE_BOOK_CONFIRM}:
        sess["state"] = STATE_MENU
        sess["timeout_pending"] = False
        out = _greet_if_needed(sess, lang, _main_menu_text(lang))
        _set_bot(sess, out)
        sess["menu_shown"] = True
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # Passive timeout handling (in-flow only)
    if sess.get("timeout_pending"):
        g = _handle_global_commands(sess, raw, lang)
        if g:
            sess["last_user_ts"] = _utcnow_iso()
            return g

        # if user replies "1" -> continue; any other text -> reset to menu (prevents reminder loop)
        if _is_digit_choice(raw) and _to_int(raw) == 1:
            sess["timeout_pending"] = False
            out = _step_prompt(sess, lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            sess["last_user_ts"] = _utcnow_iso()
            return EngineResult(out, sess, actions)

        sess["timeout_pending"] = False
        sess["state"] = STATE_MENU
        out = ("تم إيقاف الخطوات السابقة. يرجى اختيار من القائمة للمتابعة."
               if lang == "ar"
               else "Previous step was paused. Please choose from the menu to continue.")
        out = _greet_if_needed(sess, lang, out + "\n\n" + _main_menu_text(lang))
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    if _passive_timeout_detect(sess):
        g = _handle_global_commands(sess, raw, lang)
        if g:
            sess["last_user_ts"] = _utcnow_iso()
            return g
        sess["timeout_pending"] = True
        out = _greet_if_needed(sess, lang, _timeout_prompt(lang))
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # Update last_user_ts now
    sess["last_user_ts"] = _utcnow_iso()

    # Global commands
    g = _handle_global_commands(sess, raw, lang)
    if g:
        return g

    # If CLOSED: thanks/ack -> polite close; otherwise show menu
    if sess.get("state") == STATE_CLOSED:
        if _is_thanks(raw) or _is_ack(raw) or tlow in {"no", "لا"}:
            out = ("يسعدنا خدمتك دائماً ✅ إذا رغبت بحجز جديد اكتب 0 لعرض القائمة."
                   if lang == "ar"
                   else "Happy to help ✅ If you’d like a new booking, reply 0 to see the menu.")
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["state"] = STATE_MENU

    # First contact: show menu once
    if not sess.get("menu_shown"):
        sess["state"] = STATE_MENU

    # MENU / ACTIVE
    if sess.get("state") in {STATE_MENU, STATE_ACTIVE}:
        sess["menu_shown"] = True

        # if user just says thanks in menu, be polite (don’t force numeric)
        if _is_thanks(raw):
            out = ("العفو ✅ إذا رغبت بخدمة أخرى اكتب 0 لعرض القائمة."
                   if lang == "ar"
                   else "You’re welcome ✅ If you need anything else, reply 0 for the menu.")
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if _is_digit_choice(raw):
            choice = _to_int(raw)

            if choice == 1:
                _reset_flow_fields(sess)
                sess["intent"] = "BOOK"
                sess["state"] = STATE_BOOK_DEPT
                out = _step_prompt(sess, lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 2:
                _reset_flow_fields(sess)
                sess["intent"] = "RESCHEDULE"
                sess["state"] = STATE_RESCHEDULE_LOOKUP
                out = _step_prompt(sess, lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 3:
                _reset_flow_fields(sess)
                sess["intent"] = "CANCEL"
                sess["state"] = STATE_CANCEL_LOOKUP
                out = _step_prompt(sess, lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 4:
                _reset_flow_fields(sess)
                sess["intent"] = "DOCTOR_INFO"
                sess["state"] = STATE_BOOK_DEPT
                out = _step_prompt(sess, lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 5:
                out = _timings_text(lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 6:
                out = _insurance_text(lang)
                out = _greet_if_needed(sess, lang, out)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 9:
                return _handle_global_commands(sess, "9", lang) or EngineResult(_main_menu_text(lang), sess, actions)

            out = _soft_invalid(sess, lang, ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid option number."))
            out = _greet_if_needed(sess, lang, _main_menu_text(lang)) + "\n\n" + out
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        # free text classify
        intent = _classify_intent(message_text)

        if intent == "RECEPTION":
            return _handle_global_commands(sess, "9", lang) or EngineResult(_main_menu_text(lang), sess, actions)

        if intent == "TIMINGS":
            out = _timings_text(lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if intent == "INSURANCE":
            out = _insurance_text(lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if intent == "DOCTOR_INFO":
            _reset_flow_fields(sess)
            sess["intent"] = "DOCTOR_INFO"
            sess["state"] = STATE_BOOK_DEPT
            out = _step_prompt(sess, lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if intent == "RESCHEDULE":
            _reset_flow_fields(sess)
            sess["intent"] = "RESCHEDULE"
            sess["state"] = STATE_RESCHEDULE_LOOKUP
            out = _step_prompt(sess, lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if intent == "CANCEL":
            _reset_flow_fields(sess)
            sess["intent"] = "CANCEL"
            sess["state"] = STATE_CANCEL_LOOKUP
            out = _step_prompt(sess, lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if intent == "BOOK":
            _reset_flow_fields(sess)
            sess["intent"] = "BOOK"
            sess["state"] = STATE_BOOK_DEPT
            out = _step_prompt(sess, lang)
            out = _greet_if_needed(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        out = _greet_if_needed(sess, lang, _main_menu_text(lang))
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ----------------------------
    # BOOK / DOCTOR_INFO flow
    # ----------------------------
    if sess.get("state") == STATE_BOOK_DEPT:
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        dept_key = None
        dept_label = None

        if 0 <= idx < len(DEPTS):
            dept_key = DEPTS[idx]["key"]
            dept_label = DEPTS[idx]["ar"] if lang == "ar" else DEPTS[idx]["en"]
        else:
            for d in DEPTS:
                if _norm_low(d["ar"]) in tlow or _norm_low(d["en"]) in tlow:
                    dept_key = d["key"]
                    dept_label = d["ar"] if lang == "ar" else d["en"]
                    break

        if not dept_key:
            out = _soft_invalid(sess, lang, ("يرجى اختيار رقم قسم صحيح." if lang == "ar" else "Please choose a valid department number."))
            out = out + "\n\n" + (_dept_prompt(lang) + _utility_footer(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["dept_key"] = dept_key
        sess["dept_label"] = dept_label
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_DOCTOR
        out = _doctor_prompt(lang, dept_key) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_DOCTOR:
        docs = DOCTORS_BY_DEPT_KEY.get(sess.get("dept_key") or "", [])
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1

        chosen_label = None
        chosen_key = None

        if 0 <= idx < len(docs):
            chosen_key = docs[idx].get("key")
            chosen_label = docs[idx]["ar"] if lang == "ar" else docs[idx]["en"]
        else:
            for doc in docs:
                if _norm_low(doc["ar"]) in tlow or _norm_low(doc["en"]) in tlow:
                    chosen_key = doc.get("key")
                    chosen_label = doc["ar"] if lang == "ar" else doc["en"]
                    break

        if not chosen_label:
            out = _soft_invalid(sess, lang, ("يرجى اختيار رقم طبيب صحيح." if lang == "ar" else "Please choose a valid doctor number."))
            out = out + "\n\n" + (_doctor_prompt(lang, sess.get("dept_key") or "") + _utility_footer(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["doctor_key"] = chosen_key
        sess["doctor_label"] = chosen_label
        sess["mistakes"] = 0

        if sess.get("intent") == "DOCTOR_INFO":
            if lang == "ar":
                out = (
                    f"تم عرض الأطباء المتاحين لقسم *{sess.get('dept_label')}*.\n"
                    "هل ترغبون بحجز موعد؟\n\n"
                    "1️⃣ حجز موعد\n0️⃣ القائمة الرئيسية\n9️⃣ موظف الاستقبال"
                )
            else:
                out = (
                    f"Available doctors for *{sess.get('dept_label')}* are listed above.\n"
                    "Would you like to book an appointment?\n\n"
                    "1️⃣ Book Appointment\n0️⃣ Main Menu\n9️⃣ Reception"
                )
            _set_bot(sess, out)
            sess["state"] = STATE_MENU
            return EngineResult(out, sess, actions)

        sess["state"] = STATE_BOOK_DATE
        out = _date_prompt(lang) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_DATE:
        d = _normalize_digits(_norm(message_text))
        if len(d) < 8:
            out = _soft_invalid(sess, lang, ("يرجى كتابة التاريخ بالشكل الصحيح: 2026-02-25" if lang == "ar" else "Please enter date like: 2026-02-25"))
            out = out + "\n\n" + (_date_prompt(lang) + _utility_footer(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["date"] = d
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT
        out = _slot_prompt(lang, d) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_SLOT:
        if not SLOTS:
            out = _slot_prompt(lang, sess.get("date") or "") + _utility_footer(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        if not (0 <= idx < len(SLOTS)):
            out = _soft_invalid(sess, lang, ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid time slot number."))
            out = out + "\n\n" + (_slot_prompt(lang, sess.get("date") or "") + _utility_footer(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["slot"] = SLOTS[idx]
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_PATIENT
        out = _patient_info_prompt(lang) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_PATIENT:
        name, mobile, pid = _extract_name_mobile_id(message_text)

        sess["patient_name"] = name
        sess["patient_mobile"] = mobile
        sess["patient_id"] = pid

        if not sess.get("patient_name") or not sess.get("patient_mobile"):
            out = (
                ("فضلاً ارسل:\n• الاسم الكامل\n• رقم الجوال\n• رقم الهوية/الإقامة (اختياري)" if lang == "ar"
                 else "Please send:\n• Full Name\n• Mobile Number\n• National ID/Iqama (optional)")
                + _utility_footer(lang)
            )
            out = _soft_invalid(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_CONFIRM
        out = _build_confirmation(sess, lang) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_CONFIRM:
        # allow thanks/ok -> treat as "not confirming" and keep prompt (or move to menu)
        if _is_thanks(raw) or _is_ack(raw) or _is_greeting(raw):
            out = ("يرجى تأكيد الطلب باختيار 1 أو تعديل 2 أو إلغاء 3." if lang == "ar"
                   else "Please confirm by choosing 1, or change with 2, or cancel with 3.")
            out = out + _utility_footer(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        if _is_digit_choice(raw):
            c = _to_int(raw)

            if c == 1:
                if lang == "ar":
                    out = (
                        "تم استلام طلب الحجز ✅\n"
                        "تم إرسال طلب الحجز إلى قسم الاستقبال بالمستشفى لتأكيد الموعد.\n"
                        "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
                        + _closing(lang)
                    )
                else:
                    out = (
                        "Booking request received ✅\n"
                        "Your booking request has been sent to hospital reception for confirmation.\n"
                        "Please arrive 15 minutes before your appointment.\n\n"
                        + _closing(lang)
                    )

                _set_bot(sess, out)
                sess["state"] = STATE_CLOSED
                sess["last_closed_at"] = _utcnow_iso()

                actions.append({
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
                        "notes": "",
                    },
                })
                return EngineResult(out, sess, actions)

            if c == 2:
                sess["state"] = STATE_BOOK_DEPT
                sess["mistakes"] = 0
                out = ("تمام. لنعد لاختيار القسم.\n\n" if lang == "ar" else "Okay. Let’s choose the department again.\n\n")
                out = out + (_dept_prompt(lang) + _utility_footer(lang))
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if c == 3:
                sess["state"] = STATE_CANCEL_CONFIRM
                out = ("هل ترغب بإلغاء هذا الطلب؟\n1️⃣ نعم\n2️⃣ لا" if lang == "ar" else "Do you want to cancel this request?\n1️⃣ Yes\n2️⃣ No")
                out += _utility_footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

        out = _soft_invalid(sess, lang, ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3."))
        out = out + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ----------------------------
    # RESCHEDULE flow (demo)
    # ----------------------------
    if sess.get("state") == STATE_RESCHEDULE_LOOKUP:
        sess["appt_ref"] = _norm(message_text)[:80]
        sess["state"] = STATE_RESCHEDULE_NEW_DATE
        out = ("يرجى اختيار التاريخ الجديد للموعد (مثال: 2026-02-25)" if lang == "ar"
               else "Please enter the new date for the appointment (example: 2026-02-25)")
        out += _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_NEW_DATE:
        d = _normalize_digits(_norm(message_text))
        if len(d) < 8:
            out = _soft_invalid(sess, lang, ("يرجى كتابة التاريخ بالشكل الصحيح: 2026-02-25" if lang == "ar" else "Please enter date like: 2026-02-25"))
            out += _utility_footer(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["date"] = d
        sess["state"] = STATE_RESCHEDULE_NEW_SLOT
        out = _slot_prompt(lang, d) + _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_NEW_SLOT:
        if not SLOTS:
            out = _slot_prompt(lang, sess.get("date") or "") + _utility_footer(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        if not (0 <= idx < len(SLOTS)):
            out = _soft_invalid(sess, lang, ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number."))
            out += _utility_footer(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["slot"] = SLOTS[idx]
        sess["state"] = STATE_RESCHEDULE_CONFIRM
        if lang == "ar":
            out = (
                f"يرجى تأكيد رغبتكم في تعديل الموعد إلى:\n📅 {sess.get('date')}\n⏰ {sess.get('slot')}\n\n"
                "1️⃣ تأكيد\n2️⃣ رجوع للقائمة"
            )
        else:
            out = (
                f"Please confirm rescheduling to:\n📅 {sess.get('date')}\n⏰ {sess.get('slot')}\n\n"
                "1️⃣ Confirm\n2️⃣ Back to Menu"
            )
        out += _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_CONFIRM:
        if _is_digit_choice(raw) and _to_int(raw) == 1:
            out = ("تم استلام طلب التعديل ✅\nسيقوم الاستقبال بتأكيد الموعد الجديد قريبًا.\n\n" + _closing(lang)
                   if lang == "ar"
                   else "Reschedule request received ✅\nReception will confirm the new appointment shortly.\n\n" + _closing(lang))
            _set_bot(sess, out)
            sess["state"] = STATE_CLOSED
            sess["last_closed_at"] = _utcnow_iso()

            actions.append({
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "RESCHEDULE",
                    "status": "PENDING",
                    "appt_date": sess.get("date"),
                    "appt_time": sess.get("slot"),
                    "notes": f"appt_ref={sess.get('appt_ref')}",
                },
            })
            return EngineResult(out, sess, actions)

        if _is_digit_choice(raw) and _to_int(raw) == 2:
            sess["state"] = STATE_MENU
            out = _greet_if_needed(sess, lang, _main_menu_text(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        out = _soft_invalid(sess, lang, ("يرجى اختيار 1 للتأكيد أو 2 للقائمة." if lang == "ar" else "Please choose 1 to confirm or 2 for menu."))
        out += _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ----------------------------
    # CANCEL flow (demo)
    # ----------------------------
    if sess.get("state") == STATE_CANCEL_LOOKUP:
        sess["appt_ref"] = _norm(message_text)[:80]
        sess["state"] = STATE_CANCEL_CONFIRM
        out = ("يرجى تأكيد إلغاء الموعد:\n1️⃣ تأكيد الإلغاء\n2️⃣ رجوع"
               if lang == "ar" else "Please confirm cancellation:\n1️⃣ Confirm cancel\n2️⃣ Back")
        out += _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_CANCEL_CONFIRM:
        if _is_digit_choice(raw) and _to_int(raw) == 1:
            out = (
                "تم إلغاء الموعد بنجاح ✅\nفي حال رغبتكم بحجز موعد جديد، يسعدنا خدمتك.\n\n" + _closing(lang)
                if lang == "ar"
                else "Your appointment has been successfully cancelled ✅\nIf you need a new appointment, we will be pleased to assist you.\n\n" + _closing(lang)
            )
            _set_bot(sess, out)
            sess["state"] = STATE_CLOSED
            sess["last_closed_at"] = _utcnow_iso()

            actions.append({
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "CANCEL",
                    "status": "PENDING",
                    "notes": f"appt_ref={sess.get('appt_ref')}",
                },
            })
            return EngineResult(out, sess, actions)

        if _is_digit_choice(raw) and _to_int(raw) == 2:
            sess["state"] = STATE_MENU
            out = _greet_if_needed(sess, lang, _main_menu_text(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        out = _soft_invalid(sess, lang, ("يرجى اختيار 1 أو 2." if lang == "ar" else "Please choose 1 or 2."))
        out += _utility_footer(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Fallback
    sess["state"] = STATE_MENU
    out = _greet_if_needed(sess, lang, _main_menu_text(lang))
    _set_bot(sess, out)
    return EngineResult(out, sess, actions)

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