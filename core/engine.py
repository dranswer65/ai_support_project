# core/engine.py — Enterprise WhatsApp Clinic Demo Engine (Sellable SaaS MVP)
#
# Goals:
# ✅ Enterprise welcome + trust + emergency disclaimer
# ✅ Language selection (1 Arabic / 2 English) and LOCK it per session (no mixing)
# ✅ Global navigation:
#    0  = Main Menu (ALWAYS)
#    99 = Speak to Reception (ALWAYS)
# ✅ Agent/Reception keywords override (ALWAYS)
# ✅ Strict workflow steps (no auto-advance)
# ✅ Date validation + flexible formats (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY)
# ✅ Reject past dates
# ✅ Reference number after booking (SSH-YYYYMMDD-xxxxx)
# ✅ status model: ACTIVE | COMPLETED | ABANDONED
# ✅ last_step stored for dashboard/debug
#
# No integrations, no SMS, no lab module (kept as "handoff to reception" for now)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone, date
import re
import random


# ----------------------------
# Status (3-level model)
# ----------------------------
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ABANDONED = "ABANDONED"


# ----------------------------
# States
# ----------------------------
STATE_LANG_SELECT = "LANG_SELECT"
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

ENGINE_MARKER = "ENTERPRISE_CLINIC_ENGINE_V2"


# ----------------------------
# Timing rules (evaluated only on user message)
# ----------------------------
SESSION_EXPIRE_SECONDS = 60 * 60  # 60 minutes -> reset to menu (no confusion)
MAX_INVALIDS_BEFORE_EXIT = 2


# ----------------------------
# Demo tenant catalog (MVP)
# ----------------------------
CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"

EMERGENCY_NUMBER = "997"
RECEPTION_PHONE = "055500000000"

# Locations module (simple, sellable for demo)
LOCATION_NAME_AR = "فرع الرياض (مثال)"
LOCATION_NAME_EN = "Riyadh Branch (example)"
MAPS_LINK = "https://maps.google.com/?q=Shireen+Specialist+Hospital"  # placeholder

CONTACT_EMAIL = "reception@shireen-hospital.example"  # placeholder

DEPTS = [
    {"key": "general", "en": "General Medicine", "ar": "الطب العام"},
    {"key": "peds", "en": "Pediatrics", "ar": "طب الأطفال"},
    {"key": "gyn", "en": "Obstetrics & Gynecology", "ar": "أمراض النساء والتوليد"},
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
        {"key": "dr_sara", "en": "Dr. Sara Al-Mutairi (Consultant)", "ar": "د. سارة المطيري (استشاري)"},
        {"key": "dr_ahmed", "en": "Dr. Ahmed Al-Qahtani (Specialist)", "ar": "د. أحمد القحطاني (أخصائي)"},
    ],
    "peds": [{"key": "dr_mona", "en": "Dr. Mona (Specialist)", "ar": "د. منى (أخصائي)"}],
    "gyn": [{"key": "dr_huda", "en": "Dr. Huda (Consultant)", "ar": "د. هدى (استشاري)"}],
    "ortho": [{"key": "dr_khaled", "en": "Dr. Khaled (Specialist)", "ar": "د. خالد (أخصائي)"}],
    "derm": [{"key": "dr_ali", "en": "Dr. Ali (Specialist)", "ar": "د. علي (أخصائي)"}],
    "ent": [{"key": "dr_faisal", "en": "Dr. Faisal (Specialist)", "ar": "د. فيصل (أخصائي)"}],
    "cardio": [{"key": "dr_nasser", "en": "Dr. Nasser (Consultant)", "ar": "د. ناصر (استشاري)"}],
    "dental": [{"key": "dr_laila", "en": "Dr. Laila (Specialist)", "ar": "د. ليلى (أخصائي)"}],
    "neuro": [{"key": "dr_omar", "en": "Dr. Omar (Consultant)", "ar": "د. عمر (استشاري)"}],
    "physio": [{"key": "dr_rana", "en": "Dr. Rana (Specialist)", "ar": "د. رنا (أخصائي)"}],
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "17:00", "17:30", "18:00", "18:30"]

CLINIC_TIMINGS_AR = "مواعيد العمل: يوميًا من 9:00 صباحًا إلى 9:00 مساءً (عدا الجمعة)."
CLINIC_TIMINGS_EN = "Hospital hours: daily 9:00 AM to 9:00 PM (except Friday)."

INSURANCE_LIST = ["Bupa", "Tawuniya", "MedGulf", "Other"]


# ----------------------------
# Result type
# ----------------------------
@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]


# ----------------------------
# Helpers: normalization
# ----------------------------
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

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

def _norm(s: str) -> str:
    return (s or "").strip()

def _low(s: str) -> str:
    return _normalize_digits((s or "").strip().lower())

def _lang(lang: str) -> str:
    l = (lang or "").strip().lower()
    return "ar" if l.startswith("ar") else "en"

def _is_digit(s: str) -> bool:
    return _low(s).isdigit()

def _to_int(s: str, default: int = -1) -> int:
    try:
        return int(_low(s))
    except Exception:
        return default


# ----------------------------
# Intent overrides
# ----------------------------
_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال"
]

def _wants_agent(text: str) -> bool:
    t = _low(text)
    return any(k in t for k in _AGENT_KEYS) or t == "99"

def _is_thanks(text: str) -> bool:
    t = _low(text)
    return t in {"thanks", "thank you", "thx", "شكرا", "شكراً", "شكرًا", "الله يعطيك العافية", "مشكور"}

def _is_greeting(text: str) -> bool:
    t = _low(text)
    if not t:
        return False
    en = {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}
    if t in en:
        return True
    ar_parts = ["السلام عليكم", "مرحبا", "أهلا", "اهلا", "هلا", "صباح الخير", "مساء الخير"]
    return any(p in t for p in ar_parts)

def _looks_like_emergency(text: str) -> bool:
    t = _low(text)
    keys = [
        "chest pain", "shortness of breath", "severe bleeding", "unconscious", "stroke", "heart attack",
        "emergency", "bleeding", "seizure",
        "ألم صدر", "ضيق تنفس", "نزيف شديد", "فاقد الوعي", "جلطة", "سكتة", "نوبة قلبية", "طارئ", "نزيف", "اختلاج",
    ]
    return any(k in t for k in keys)

def _emergency_msg(lang: str) -> str:
    if lang == "ar":
        return (
            "تنبيه مهم:\n"
            f"إذا كانت الحالة طارئة، يرجى الاتصال بـ *{EMERGENCY_NUMBER}* فورًا أو التوجه لأقرب طوارئ.\n"
            "هذه الخدمة غير مخصصة للحالات الطارئة."
        )
    return (
        "Important notice:\n"
        f"If this is a medical emergency, please call *{EMERGENCY_NUMBER}* immediately or go to the nearest ER.\n"
        "This WhatsApp service is not intended for emergency situations."
    )


# ----------------------------
# Date parsing/validation
# ----------------------------
_DATE_RE_1 = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")   # YYYY-MM-DD
_DATE_RE_2 = re.compile(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*$")   # DD-MM-YYYY

def _parse_user_date(text: str) -> Optional[str]:
    """
    Accepts:
      - YYYY-MM-DD
      - DD-MM-YYYY
      - DD/MM/YYYY
    Returns normalized YYYY-MM-DD or None.
    """
    t = _normalize_digits(_norm(text))
    m1 = _DATE_RE_1.match(t)
    if m1:
        y, mo, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            dt = date(y, mo, d)
            return dt.isoformat()
        except Exception:
            return None

    m2 = _DATE_RE_2.match(t)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            dt = date(y, mo, d)
            return dt.isoformat()
        except Exception:
            return None

    return None

def _is_past_date(iso_yyyy_mm_dd: str) -> bool:
    try:
        y, mo, d = [int(x) for x in iso_yyyy_mm_dd.split("-")]
        dt = date(y, mo, d)
        return dt < datetime.now(timezone.utc).date()
    except Exception:
        return False


# ----------------------------
# Session helpers
# ----------------------------
def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "engine": ENGINE_MARKER,
        "user_id": user_id,

        "status": STATUS_ACTIVE,
        "state": STATE_LANG_SELECT,
        "last_step": STATE_LANG_SELECT,

        "language": "ar",
        "language_locked": False,
        "text_direction": "rtl",

        "has_greeted": False,
        "menu_shown": False,

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

        "patient_mobile": None,
        "patient_id": None,
        "patient_name": None,

        "appt_ref": None,
        "last_closed_at": None,

        "escalation_flag": False,
    }

def _set_bot(sess: Dict[str, Any], msg: str) -> None:
    sess["last_bot_message"] = msg
    sess["last_bot_ts"] = _utcnow_iso()

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

def _seconds_since_last_user(sess: Dict[str, Any]) -> Optional[float]:
    last = _parse_iso(sess.get("last_user_ts"))
    if not last:
        return None
    return (datetime.now(timezone.utc) - last).total_seconds()

def _maybe_expire(sess: Dict[str, Any], lang: str) -> Optional[str]:
    sec = _seconds_since_last_user(sess)
    if sec is None:
        return None
    if sec >= SESSION_EXPIRE_SECONDS and sess.get("state") not in {STATE_CLOSED, STATE_ESCALATION}:
        _reset_flow_fields(sess)
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        if lang == "ar":
            return "تم إنهاء الجلسة بسبب عدم النشاط. يرجى اختيار من القائمة للمتابعة.\n\n" + _main_menu(lang)
        return "Your session expired due to inactivity. Please choose from the menu to continue.\n\n" + _main_menu(lang)
    return None


# ----------------------------
# Enterprise messages
# ----------------------------
def _enterprise_welcome(lang: str) -> str:
    if lang == "ar":
        return (
            f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
            "المساعد الرسمي عبر واتساب.\n\n"
            "يمكنني مساعدتكم في:\n"
            "• حجز وإدارة المواعيد\n"
            "• معلومات الأطباء\n"
            "• التأمينات المعتمدة\n"
            "• مواعيد العمل والموقع\n\n"
            f"للحالات الطارئة يرجى الاتصال بـ *{EMERGENCY_NUMBER}* فورًا.\n\n"
            "يرجى اختيار اللغة المفضلة:\n"
            "1️⃣ العربية\n"
            "2️⃣ English\n\n"
            "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
        )
    return (
        f"Welcome to *{CLINIC_NAME_EN}* 🏥\n"
        "Official WhatsApp Virtual Assistant.\n\n"
        "I can help you with:\n"
        "• Appointment booking & management\n"
        "• Doctor information\n"
        "• Accepted insurance\n"
        "• Timings & location\n\n"
        f"For medical emergencies, please call *{EMERGENCY_NUMBER}* immediately.\n\n"
        "Please select your preferred language:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "To speak with Reception anytime, type *Agent* or 99"
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
            "9️⃣ معلومات التواصل\n"
            "0️⃣ القائمة الرئيسية\n"
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
        "7️⃣ Locations & Directions\n"
        "9️⃣ Contact Information\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Speak to Reception"
    )

def _contact_info(lang: str) -> str:
    if lang == "ar":
        return (
            "معلومات التواصل:\n"
            f"📞 الاستقبال: *{RECEPTION_PHONE}*\n"
            f"✉️ البريد: *{CONTACT_EMAIL}*\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Contact information:\n"
        f"📞 Reception: *{RECEPTION_PHONE}*\n"
        f"✉️ Email: *{CONTACT_EMAIL}*\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )

def _locations(lang: str) -> str:
    if lang == "ar":
        return (
            "الموقع والاتجاهات:\n"
            f"📍 {LOCATION_NAME_AR}\n"
            f"🗺️ {MAPS_LINK}\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Locations & directions:\n"
        f"📍 {LOCATION_NAME_EN}\n"
        f"🗺️ {MAPS_LINK}\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )

def _timings(lang: str) -> str:
    if lang == "ar":
        return CLINIC_TIMINGS_AR + "\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return CLINIC_TIMINGS_EN + "\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _insurance_menu(lang: str) -> str:
    if lang == "ar":
        return (
            "يرجى اختيار شركة التأمين:\n\n"
            "1️⃣ Bupa\n"
            "2️⃣ Tawuniya\n"
            "3️⃣ MedGulf\n"
            "4️⃣ أخرى\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Please select your insurance provider:\n\n"
        "1️⃣ Bupa\n"
        "2️⃣ Tawuniya\n"
        "3️⃣ MedGulf\n"
        "4️⃣ Other\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )

def _insurance_result(lang: str, choice: str) -> str:
    # Demo text (no integration)
    if lang == "ar":
        return (
            f"تم استلام طلبكم ✅\n"
            f"شركة التأمين: *{choice}*\n\n"
            "مبدئيًا: التأمين معتمد لبعض الأقسام وقد تختلف التغطية حسب الخطة.\n"
            "للتأكيد النهائي يمكن تحويلكم للاستقبال.\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        f"Received ✅\n"
        f"Insurance: *{choice}*\n\n"
        "Generally accepted for selected services; coverage depends on your plan.\n"
        "For final verification, we can connect you to Reception.\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )

def _dept_prompt(lang: str) -> str:
    lines = []
    for i, d in enumerate(DEPTS, start=1):
        label = d["ar"] if lang == "ar" else d["en"]
        lines.append(f"{i}️⃣ {label}")
    if lang == "ar":
        return "يرجى اختيار التخصص:\n\n" + "\n".join(lines) + "\n\n(يمكنك إرسال رقم أو اسم التخصص)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Please select a specialty:\n\n" + "\n".join(lines) + "\n\n(Reply with number or type the specialty)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _doctor_prompt(lang: str, dept_key: str) -> str:
    docs = DOCTORS_BY_DEPT_KEY.get(dept_key, [])
    if not docs:
        if lang == "ar":
            return "لا توجد بيانات أطباء لهذا التخصص حاليًا.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
        return "No doctors found for this specialty.\n\n0️⃣ Main Menu\n99️⃣ Reception"

    lines = []
    for i, doc in enumerate(docs, start=1):
        label = doc["ar"] if lang == "ar" else doc["en"]
        lines.append(f"{i}️⃣ {label}")
    if lang == "ar":
        return "الأطباء المتاحون:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الطبيب)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Available doctors:\n\n" + "\n".join(lines) + "\n\n(Reply with doctor number)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _date_prompt(lang: str) -> str:
    if lang == "ar":
        return (
            "يرجى إدخال تاريخ الموعد.\n"
            "أمثلة مقبولة:\n"
            "• 2026-02-28\n"
            "• 28-02-2026\n"
            "• 28/02/2026\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Please enter the appointment date.\n"
        "Accepted examples:\n"
        "• 2026-02-28\n"
        "• 28-02-2026\n"
        "• 28/02/2026\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )

def _slot_prompt(lang: str, iso_date: str) -> str:
    lines = [f"{i}️⃣ {s}" for i, s in enumerate(SLOTS, start=1)]
    if lang == "ar":
        return f"الأوقات المتاحة بتاريخ *{iso_date}*:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الوقت)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return f"Available time slots on *{iso_date}*:\n\n" + "\n".join(lines) + "\n\n(Reply with slot number)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _patient_prompt(lang: str) -> str:
    if lang == "ar":
        return (
            "لإتمام الحجز، يرجى إرسال البيانات (يفضل في رسالة واحدة):\n"
            "• الاسم الكامل\n"
            "• رقم الجوال\n"
            "• رقم الهوية/الإقامة (اختياري)\n\n"
            f"تنبيه: للحالات الطارئة اتصل بـ *{EMERGENCY_NUMBER}*.\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "To proceed, please share (preferably in one message):\n"
        "• Full name\n"
        "• Mobile number\n"
        "• National ID / Iqama (optional)\n\n"
        f"Emergency: call *{EMERGENCY_NUMBER}*.\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )


# ----------------------------
# Patient parsing (safe)
# ----------------------------
def _extract_name_mobile_id(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw0 = (raw or "").strip()
    if not raw0:
        return None, None, None

    rawN = _normalize_digits(raw0)

    lines = [ln.strip() for ln in rawN.splitlines() if ln.strip()]
    name = lines[0] if lines else rawN

    # digit sequences >=8
    seqs: List[str] = []
    cur: List[str] = []
    for ch in rawN:
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

    low0 = _low(raw0)
    if pid is None and any(k in low0 for k in ["iqama", "id", "national id", "هوية", "الإقامة", "اقامة", "رقم الهوية"]):
        a = "".join(c for c in (mobile or "") if c.isdigit())
        for cand in reversed(seqs):
            b = "".join(c for c in cand if c.isdigit())
            if 8 <= len(b) <= 15 and b != a:
                pid = cand
                break

    return name, mobile, pid


# ----------------------------
# Reference number
# ----------------------------
def _make_reference(prefix: str = "SSH") -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    rnd = random.randint(10000, 99999)
    return f"{prefix}-{today}-{rnd}"


# ----------------------------
# Confirmation template
# ----------------------------
def _confirm_summary(sess: Dict[str, Any], lang: str) -> str:
    ref = sess.get("appt_ref") or "—"
    pid = sess.get("patient_id")
    pid_line = ""
    if pid:
        pid_line = (f"🪪 الهوية/الإقامة: {pid}\n" if lang == "ar" else f"🪪 ID/Iqama: {pid}\n")

    if lang == "ar":
        return (
            "ملخص الموعد:\n\n"
            f"المرجع: *{ref}*\n"
            f"👤 الاسم: {sess.get('patient_name')}\n"
            f"📱 الجوال: {sess.get('patient_mobile')}\n"
            + pid_line +
            f"👨‍⚕️ الطبيب: {sess.get('doctor_label')}\n"
            f"🏥 التخصص: {sess.get('dept_label')}\n"
            f"📅 التاريخ: {sess.get('date')}\n"
            f"⏰ الوقت: {sess.get('slot')}\n\n"
            "يرجى التأكيد:\n"
            "1️⃣ تأكيد الموعد\n"
            "2️⃣ تعديل\n"
            "3️⃣ إلغاء\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Appointment Summary:\n\n"
        f"Reference: *{ref}*\n"
        f"👤 Name: {sess.get('patient_name')}\n"
        f"📱 Mobile: {sess.get('patient_mobile')}\n"
        + pid_line +
        f"👨‍⚕️ Doctor: {sess.get('doctor_label')}\n"
        f"🏥 Specialty: {sess.get('dept_label')}\n"
        f"📅 Date: {sess.get('date')}\n"
        f"⏰ Time: {sess.get('slot')}\n\n"
        "Please confirm:\n"
        "1️⃣ Confirm Appointment\n"
        "2️⃣ Modify\n"
        "3️⃣ Cancel\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Reception"
    )


# ----------------------------
# Invalid handling with exit
# ----------------------------
def _invalid_with_exit(sess: Dict[str, Any], lang: str, msg: str) -> str:
    sess["mistakes"] = int(sess.get("mistakes", 0)) + 1
    if sess["mistakes"] >= MAX_INVALIDS_BEFORE_EXIT:
        if lang == "ar":
            return (
                msg + "\n\n"
                "خيارات المساعدة:\n"
                "1️⃣ إعادة المحاولة\n"
                "0️⃣ القائمة الرئيسية\n"
                "99️⃣ موظف الاستقبال"
            )
        return (
            msg + "\n\n"
            "Help options:\n"
            "1️⃣ Try again\n"
            "0️⃣ Main Menu\n"
            "99️⃣ Reception"
        )
    return msg


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

    # ---- language lock
    lang = _lang(language or sess.get("language") or "ar")
    if bool(sess.get("language_locked")):
        lang = _lang(sess.get("language") or lang)

    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    raw = _norm(message_text)
    tlow = _low(message_text)
    actions: List[Dict[str, Any]] = []

    # ---- session expiry
    expired_msg = _maybe_expire(sess, lang)
    if expired_msg:
        _set_bot(sess, expired_msg)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(expired_msg, sess, actions)

    # ---- update last_user_ts early
    sess["last_user_ts"] = _utcnow_iso()

    # ---- emergency first
    if _looks_like_emergency(message_text):
        out = _emergency_msg(lang)
        _set_bot(sess, out)
        sess["last_step"] = sess.get("state")
        return EngineResult(out, sess, actions)

    # ---- agent override ALWAYS
    if _wants_agent(message_text):
        sess["state"] = STATE_ESCALATION
        sess["status"] = STATUS_ACTIVE
        sess["escalation_flag"] = True
        sess["last_step"] = STATE_ESCALATION
        if lang == "ar":
            out = "جاري تحويلكم إلى موظف الاستقبال. يرجى الانتظار..."
        else:
            out = "Connecting you to a reception officer. Please wait..."
        _set_bot(sess, out)
        actions.append({"type": "ESCALATE", "reason": "user_requested_agent"})
        return EngineResult(out, sess, actions)

    # ---- politeness (do not break flow; but avoid robotic errors)
    if _is_thanks(message_text) and sess.get("state") in {STATE_MENU, STATE_CLOSED, STATE_LANG_SELECT}:
        if lang == "ar":
            out = "العفو. إذا احتجتم أي خدمة أخرى اكتبوا 0 لعرض القائمة."
        else:
            out = "You’re welcome. If you need anything else, reply 0 for the main menu."
        _set_bot(sess, out)
        sess["last_step"] = sess.get("state")
        return EngineResult(out, sess, actions)

    # ---- greetings:
    # If greeting and language NOT locked yet -> show welcome/language selection
    if _is_greeting(message_text) and not bool(sess.get("language_locked")):
        sess["state"] = STATE_LANG_SELECT
        sess["last_step"] = STATE_LANG_SELECT
        sess["status"] = STATUS_ACTIVE
        out = _enterprise_welcome("ar")  # show bilingual language selection prompt
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ---- 0 always = menu
    if tlow == "0":
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        sess["status"] = STATUS_ACTIVE
        out = _main_menu(lang)
        _set_bot(sess, out)
        sess["menu_shown"] = True
        return EngineResult(out, sess, actions)

    # ---- if session first time / not greeted
    if not sess.get("has_greeted"):
        sess["has_greeted"] = True
        sess["state"] = STATE_LANG_SELECT
        sess["last_step"] = STATE_LANG_SELECT
        sess["status"] = STATUS_ACTIVE
        out = _enterprise_welcome("ar")
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ----------------------------
    # Language selection state
    # ----------------------------
    if sess.get("state") == STATE_LANG_SELECT:
        if _is_digit(raw):
            c = _to_int(raw)
            if c == 1:
                sess["language"] = "ar"
                sess["language_locked"] = True
                sess["text_direction"] = "rtl"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                sess["menu_shown"] = True
                out = _main_menu("ar")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)
            if c == 2:
                sess["language"] = "en"
                sess["language_locked"] = True
                sess["text_direction"] = "ltr"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                sess["menu_shown"] = True
                out = _main_menu("en")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

        # If user typed Arabic/English text here, be polite and repeat selection
        out = _enterprise_welcome("ar")
        _set_bot(sess, out)
        sess["last_step"] = STATE_LANG_SELECT
        return EngineResult(out, sess, actions)

    # ----------------------------
    # CLOSED behavior
    # ----------------------------
    if sess.get("state") == STATE_CLOSED:
        # only reopen via 0 or a clear intent. Otherwise provide polite close
        if _is_thanks(message_text) or tlow in {"ok", "تمام"}:
            if lang == "ar":
                out = "تم ✅ إذا احتجتم أي مساعدة لاحقًا يمكنكم مراسلتنا في أي وقت."
            else:
                out = "All set ✅ If you need help later, message us anytime."
            _set_bot(sess, out)
            sess["last_step"] = STATE_CLOSED
            return EngineResult(out, sess, actions)

        # any other message -> show menu (clean reset)
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ----------------------------
    # MENU state
    # ----------------------------
    if sess.get("state") == STATE_MENU:
        sess["menu_shown"] = True

        if _is_digit(raw):
            choice = _to_int(raw)

            if choice == 1:
                _reset_flow_fields(sess)
                sess["intent"] = "BOOK"
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 2:
                _reset_flow_fields(sess)
                sess["intent"] = "RESCHEDULE"
                sess["state"] = STATE_RESCHEDULE_LOOKUP
                sess["last_step"] = STATE_RESCHEDULE_LOOKUP
                if lang == "ar":
                    out = "يرجى إدخال رقم المرجع أو رقم الجوال المسجل.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                else:
                    out = "Please enter your reference number or registered mobile.\n\n0️⃣ Main Menu\n99️⃣ Reception"
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 3:
                _reset_flow_fields(sess)
                sess["intent"] = "CANCEL"
                sess["state"] = STATE_CANCEL_LOOKUP
                sess["last_step"] = STATE_CANCEL_LOOKUP
                if lang == "ar":
                    out = "يرجى إدخال رقم المرجع أو رقم الجوال المسجل لإتمام الإلغاء.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                else:
                    out = "Please enter your reference number or registered mobile to cancel.\n\n0️⃣ Main Menu\n99️⃣ Reception"
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 4:
                _reset_flow_fields(sess)
                sess["intent"] = "FIND_DOCTOR"
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 5:
                out = _timings(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            if choice == 6:
                out = _insurance_menu(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                sess["intent"] = "INSURANCE"
                return EngineResult(out, sess, actions)

            if choice == 7:
                out = _locations(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            if choice == 9:
                out = _contact_info(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            if choice == 99:
                # already handled by _wants_agent, but keep safe
                return handle_turn(user_id, "99", lang, sess)

            # invalid
            msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid option number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _main_menu(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_MENU
            return EngineResult(out, sess, actions)

        # Insurance follow-up (if user is in menu and intent insurance)
        if sess.get("intent") == "INSURANCE":
            # allow "1..4" as text too
            if _is_digit(raw):
                c = _to_int(raw)
                if 1 <= c <= 4:
                    out = _insurance_result(lang, INSURANCE_LIST[c - 1])
                    sess["intent"] = None
                    _set_bot(sess, out)
                    sess["last_step"] = STATE_MENU
                    return EngineResult(out, sess, actions)
            # otherwise show insurance menu again
            out = _insurance_menu(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_MENU
            return EngineResult(out, sess, actions)

        # free text -> show menu (no confusion)
        out = _main_menu(lang)
        _set_bot(sess, out)
        sess["last_step"] = STATE_MENU
        return EngineResult(out, sess, actions)

    # ----------------------------
    # BOOK / FIND_DOCTOR flow
    # ----------------------------
    if sess.get("state") == STATE_BOOK_DEPT:
        # Accept number or department name
        dept_key = None
        dept_label = None

        if _is_digit(raw):
            idx = _to_int(raw) - 1
            if 0 <= idx < len(DEPTS):
                dept_key = DEPTS[idx]["key"]
                dept_label = DEPTS[idx]["ar"] if lang == "ar" else DEPTS[idx]["en"]
        else:
            # match by text
            for d in DEPTS:
                if _low(d["ar"]) in tlow or _low(d["en"]) in tlow:
                    dept_key = d["key"]
                    dept_label = d["ar"] if lang == "ar" else d["en"]
                    break

        if not dept_key:
            msg = ("يرجى اختيار تخصص صحيح." if lang == "ar" else "Please choose a valid specialty.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _dept_prompt(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_DEPT
            return EngineResult(out, sess, actions)

        sess["dept_key"] = dept_key
        sess["dept_label"] = dept_label
        sess["mistakes"] = 0

        sess["state"] = STATE_BOOK_DOCTOR
        sess["last_step"] = STATE_BOOK_DOCTOR
        out = _doctor_prompt(lang, dept_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_DOCTOR:
        # If user typed date here -> do NOT advance
        if _parse_user_date(raw):
            msg = ("يرجى اختيار الطبيب أولاً (اكتب رقم الطبيب)." if lang == "ar" else "Please choose a doctor first (reply with doctor number).")
            out = msg + "\n\n" + _doctor_prompt(lang, sess.get("dept_key") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_DOCTOR
            return EngineResult(out, sess, actions)

        docs = DOCTORS_BY_DEPT_KEY.get(sess.get("dept_key") or "", [])
        chosen_key = None
        chosen_label = None

        if _is_digit(raw):
            idx = _to_int(raw) - 1
            if 0 <= idx < len(docs):
                chosen_key = docs[idx].get("key")
                chosen_label = docs[idx]["ar"] if lang == "ar" else docs[idx]["en"]
        else:
            for doc in docs:
                if _low(doc["ar"]) in tlow or _low(doc["en"]) in tlow:
                    chosen_key = doc.get("key")
                    chosen_label = doc["ar"] if lang == "ar" else doc["en"]
                    break

        if not chosen_label:
            msg = ("يرجى اختيار طبيب صحيح." if lang == "ar" else "Please choose a valid doctor.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _doctor_prompt(lang, sess.get("dept_key") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_DOCTOR
            return EngineResult(out, sess, actions)

        sess["doctor_key"] = chosen_key
        sess["doctor_label"] = chosen_label
        sess["mistakes"] = 0

        # If user was only finding doctor -> go back to menu
        if sess.get("intent") == "FIND_DOCTOR":
            sess["intent"] = None
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            if lang == "ar":
                out = f"تم اختيار الطبيب: *{chosen_label}*.\nهل ترغبون بحجز موعد؟\n\n1️⃣ حجز موعد\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
            else:
                out = f"Selected doctor: *{chosen_label}*.\nWould you like to book an appointment?\n\n1️⃣ Book Appointment\n0️⃣ Main Menu\n99️⃣ Reception"
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        # proceed to date
        sess["state"] = STATE_BOOK_DATE
        sess["last_step"] = STATE_BOOK_DATE
        out = _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_DATE:
        iso = _parse_user_date(raw)
        if not iso:
            msg = ("صيغة التاريخ غير صحيحة." if lang == "ar" else "Invalid date format.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_DATE
            return EngineResult(out, sess, actions)

        if _is_past_date(iso):
            msg = ("لا يمكن اختيار تاريخ سابق. يرجى اختيار تاريخ قادم." if lang == "ar" else "You can’t select a past date. Please choose a future date.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_DATE
            return EngineResult(out, sess, actions)

        sess["date"] = iso
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT
        sess["last_step"] = STATE_BOOK_SLOT
        out = _slot_prompt(lang, iso)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_SLOT:
        if not _is_digit(raw):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_SLOT
            return EngineResult(out, sess, actions)

        idx = _to_int(raw) - 1
        if not (0 <= idx < len(SLOTS)):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_SLOT
            return EngineResult(out, sess, actions)

        sess["slot"] = SLOTS[idx]
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_PATIENT
        sess["last_step"] = STATE_BOOK_PATIENT
        out = _patient_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_PATIENT:
        name, mobile, pid = _extract_name_mobile_id(message_text)

        sess["patient_name"] = name
        sess["patient_mobile"] = mobile
        sess["patient_id"] = pid

        if not sess.get("patient_name") or not sess.get("patient_mobile"):
            msg = ("يرجى إرسال الاسم الكامل ورقم الجوال." if lang == "ar" else "Please send full name and mobile number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _patient_prompt(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_PATIENT
            return EngineResult(out, sess, actions)

        # generate reference BEFORE confirm screen
        if not sess.get("appt_ref"):
            sess["appt_ref"] = _make_reference("SSH")

        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_CONFIRM
        sess["last_step"] = STATE_BOOK_CONFIRM
        out = _confirm_summary(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_BOOK_CONFIRM:
        if _is_thanks(message_text) or _is_greeting(message_text):
            # keep user on confirm, but be polite
            if lang == "ar":
                out = "يرجى تأكيد الموعد باختيار 1 أو تعديل 2 أو إلغاء 3.\n\n" + _confirm_summary(sess, lang)
            else:
                out = "Please confirm by choosing 1, or modify with 2, or cancel with 3.\n\n" + _confirm_summary(sess, lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_BOOK_CONFIRM
            return EngineResult(out, sess, actions)

        if _is_digit(raw):
            c = _to_int(raw)

            if c == 1:
                # confirmed (demo): create receptionist queue request
                sess["status"] = STATUS_COMPLETED
                sess["state"] = STATE_CLOSED
                sess["last_step"] = STATE_CLOSED
                sess["last_closed_at"] = _utcnow_iso()

                ref = sess.get("appt_ref") or _make_reference("SSH")

                if lang == "ar":
                    out = (
                        "تم تأكيد طلب الموعد ✅\n\n"
                        f"المرجع: *{ref}*\n"
                        "سيقوم الاستقبال بتأكيد الموعد قريبًا.\n"
                        "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
                        "لأي تعديل لاحقًا: اكتب 0 ثم اختر تعديل موعد.\n"
                    )
                else:
                    out = (
                        "Appointment request confirmed ✅\n\n"
                        f"Reference: *{ref}*\n"
                        "Reception will confirm the appointment shortly.\n"
                        "Please arrive 15 minutes early.\n\n"
                        "To modify later: reply 0 then choose Reschedule.\n"
                    )

                _set_bot(sess, out)

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
                        "notes": f"ref={ref}",
                    },
                })
                return EngineResult(out, sess, actions)

            if c == 2:
                # modify -> restart booking at dept
                sess["status"] = STATUS_ACTIVE
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                sess["mistakes"] = 0
                out = (_dept_prompt(lang))
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if c == 3:
                # cancel booking request
                sess["status"] = STATUS_ABANDONED
                sess["state"] = STATE_CLOSED
                sess["last_step"] = STATE_CLOSED
                sess["last_closed_at"] = _utcnow_iso()

                if lang == "ar":
                    out = "تم إلغاء الطلب.\n\nإذا رغبت بحجز جديد اكتب 0 لعرض القائمة."
                else:
                    out = "Request cancelled.\n\nIf you’d like a new booking, reply 0 for the main menu."
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

        msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
        out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _confirm_summary(sess, lang)
        _set_bot(sess, out)
        sess["last_step"] = STATE_BOOK_CONFIRM
        return EngineResult(out, sess, actions)

    # ----------------------------
    # RESCHEDULE flow (demo; no real lookup)
    # ----------------------------
    if sess.get("state") == STATE_RESCHEDULE_LOOKUP:
        sess["appt_ref"] = _norm(message_text)[:80]
        sess["state"] = STATE_RESCHEDULE_NEW_DATE
        sess["last_step"] = STATE_RESCHEDULE_NEW_DATE
        sess["mistakes"] = 0
        out = _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_NEW_DATE:
        iso = _parse_user_date(raw)
        if not iso or _is_past_date(iso):
            msg = ("صيغة التاريخ غير صحيحة أو تاريخ سابق." if lang == "ar" else "Invalid date format or past date.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_RESCHEDULE_NEW_DATE
            return EngineResult(out, sess, actions)

        sess["date"] = iso
        sess["state"] = STATE_RESCHEDULE_NEW_SLOT
        sess["last_step"] = STATE_RESCHEDULE_NEW_SLOT
        sess["mistakes"] = 0
        out = _slot_prompt(lang, iso)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_NEW_SLOT:
        if not _is_digit(raw):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_RESCHEDULE_NEW_SLOT
            return EngineResult(out, sess, actions)

        idx = _to_int(raw) - 1
        if not (0 <= idx < len(SLOTS)):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            sess["last_step"] = STATE_RESCHEDULE_NEW_SLOT
            return EngineResult(out, sess, actions)

        sess["slot"] = SLOTS[idx]
        sess["state"] = STATE_RESCHEDULE_CONFIRM
        sess["last_step"] = STATE_RESCHEDULE_CONFIRM

        if lang == "ar":
            out = (
                "يرجى تأكيد تعديل الموعد إلى:\n"
                f"📅 {sess.get('date')}\n"
                f"⏰ {sess.get('slot')}\n\n"
                "1️⃣ تأكيد\n"
                "2️⃣ رجوع للقائمة\n\n"
                "0️⃣ القائمة الرئيسية\n"
                "99️⃣ موظف الاستقبال"
            )
        else:
            out = (
                "Please confirm rescheduling to:\n"
                f"📅 {sess.get('date')}\n"
                f"⏰ {sess.get('slot')}\n\n"
                "1️⃣ Confirm\n"
                "2️⃣ Back to Menu\n\n"
                "0️⃣ Main Menu\n"
                "99️⃣ Reception"
            )
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_RESCHEDULE_CONFIRM:
        if _is_digit(raw) and _to_int(raw) == 1:
            sess["status"] = STATUS_COMPLETED
            sess["state"] = STATE_CLOSED
            sess["last_step"] = STATE_CLOSED
            sess["last_closed_at"] = _utcnow_iso()

            ref = sess.get("appt_ref") or "—"
            if lang == "ar":
                out = f"تم استلام طلب التعديل ✅\nالمرجع: *{ref}*\nسيقوم الاستقبال بتأكيد الموعد الجديد قريبًا.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
            else:
                out = f"Reschedule request received ✅\nReference: *{ref}*\nReception will confirm the new appointment shortly.\n\n0️⃣ Main Menu\n99️⃣ Reception"
            _set_bot(sess, out)

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

        if _is_digit(raw) and _to_int(raw) == 2:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        msg = ("يرجى اختيار 1 للتأكيد أو 2 للقائمة." if lang == "ar" else "Please choose 1 to confirm or 2 for menu.")
        out = _invalid_with_exit(sess, lang, msg)
        _set_bot(sess, out)
        sess["last_step"] = STATE_RESCHEDULE_CONFIRM
        return EngineResult(out, sess, actions)

    # ----------------------------
    # CANCEL flow (demo)
    # ----------------------------
    if sess.get("state") == STATE_CANCEL_LOOKUP:
        sess["appt_ref"] = _norm(message_text)[:80]
        sess["state"] = STATE_CANCEL_CONFIRM
        sess["last_step"] = STATE_CANCEL_CONFIRM
        sess["mistakes"] = 0
        if lang == "ar":
            out = (
                "تم العثور على الطلب (للعرض التجريبي).\n"
                f"المرجع: *{sess.get('appt_ref')}*\n\n"
                "1️⃣ تأكيد الإلغاء\n"
                "2️⃣ الرجوع للقائمة\n\n"
                "0️⃣ القائمة الرئيسية\n"
                "99️⃣ موظف الاستقبال"
            )
        else:
            out = (
                "Request found (demo).\n"
                f"Reference: *{sess.get('appt_ref')}*\n\n"
                "1️⃣ Confirm cancellation\n"
                "2️⃣ Back to Menu\n\n"
                "0️⃣ Main Menu\n"
                "99️⃣ Reception"
            )
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_CANCEL_CONFIRM:
        if _is_digit(raw) and _to_int(raw) == 1:
            sess["status"] = STATUS_COMPLETED
            sess["state"] = STATE_CLOSED
            sess["last_step"] = STATE_CLOSED
            sess["last_closed_at"] = _utcnow_iso()

            if lang == "ar":
                out = "تم إلغاء الموعد ✅\n\nإذا رغبت بخدمة أخرى اكتب 0 لعرض القائمة."
            else:
                out = "Appointment cancelled ✅\n\nIf you need anything else, reply 0 for the main menu."
            _set_bot(sess, out)

            actions.append({
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "CANCEL",
                    "status": "PENDING",
                    "notes": f"appt_ref={sess.get('appt_ref')}",
                },
            })
            return EngineResult(out, sess, actions)

        if _is_digit(raw) and _to_int(raw) == 2:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        msg = ("يرجى اختيار 1 أو 2." if lang == "ar" else "Please choose 1 or 2.")
        out = _invalid_with_exit(sess, lang, msg)
        _set_bot(sess, out)
        sess["last_step"] = STATE_CANCEL_CONFIRM
        return EngineResult(out, sess, actions)

    # Fallback: reset to menu safely
    sess["state"] = STATE_MENU
    sess["last_step"] = STATE_MENU
    out = _main_menu(lang)
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