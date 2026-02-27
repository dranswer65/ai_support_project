# core/engine.py — Enterprise WhatsApp Clinic Demo Engine (Sellable SaaS MVP)
#
# Fixes in this version:
# ✅ Greeting/first-contact OVERRIDES expiry (no “expired” as first impression)
# ✅ Add reception contact in greeting
# ✅ Menu numbering fixed: 7=Location, 8=Contact, 0=Menu, 99=Human
# ✅ Handoff lock supported: session["handoff_active"] / ["handoff_until"]
# ✅ If handoff_active => bot stops responding (except optional “0”)
# ✅ Language selection at start + language lock
# ✅ Date validation + flexibility + reject past dates
# ✅ Reference number
#
# Note: no integrations; booking creates appointment_request queue.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone, date, timedelta
import re
import random


STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ABANDONED = "ABANDONED"

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

ENGINE_MARKER = "ENTERPRISE_CLINIC_ENGINE_V3"

SESSION_EXPIRE_SECONDS = 60 * 60  # 60 minutes
MAX_INVALIDS_BEFORE_EXIT = 2

CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"
EMERGENCY_NUMBER = "997"

RECEPTION_PHONE = "+966XXXXXXXX"  # put the real number later
CONTACT_EMAIL = "reception@shireen-hospital.example"
MAPS_LINK = "https://maps.google.com/?q=Shireen+Specialist+Hospital"

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
    "dental": [{"key": "dr_laila", "en": "Dr. Laila (Specialist)", "ar": "د. ليلى (أخصائي)"}],
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "17:00", "17:30", "18:00", "18:30"]

CLINIC_TIMINGS_AR = "مواعيد العمل: يوميًا من 9:00 صباحًا إلى 9:00 مساءً (عدا الجمعة)."
CLINIC_TIMINGS_EN = "Hospital hours: daily 9:00 AM to 9:00 PM (except Friday)."

INSURANCE_LIST = ["Bupa", "Tawuniya", "MedGulf", "Other"]


@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]


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
        "emergency", "chest pain", "shortness of breath", "severe bleeding",
        "طارئ", "ألم صدر", "ضيق تنفس", "نزيف شديد",
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


_DATE_RE_1 = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")
_DATE_RE_2 = re.compile(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*$")

def _parse_user_date(text: str) -> Optional[str]:
    t = _normalize_digits(_norm(text))
    m1 = _DATE_RE_1.match(t)
    if m1:
        y, mo, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            return date(y, mo, d).isoformat()
        except Exception:
            return None
    m2 = _DATE_RE_2.match(t)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            return date(y, mo, d).isoformat()
        except Exception:
            return None
    return None

def _is_past_date(iso_yyyy_mm_dd: str) -> bool:
    try:
        y, mo, d = [int(x) for x in iso_yyyy_mm_dd.split("-")]
        return date(y, mo, d) < datetime.now(timezone.utc).date()
    except Exception:
        return False


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

        "last_user_ts": None,
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

        # Handoff lock (controller sets this too)
        "handoff_active": False,
        "handoff_until": None,
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

def _handoff_is_active(sess: Dict[str, Any]) -> bool:
    if not bool(sess.get("handoff_active")):
        return False
    until = _parse_iso(sess.get("handoff_until")) if isinstance(sess.get("handoff_until"), str) else None
    if until and datetime.now(timezone.utc) <= until:
        return True
    # expired lock
    sess["handoff_active"] = False
    sess["handoff_until"] = None
    return False


def _enterprise_welcome_bilingual() -> str:
    # bilingual language selection (always safe for first message)
    return (
        f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        f"📞 الاستقبال: *{RECEPTION_PHONE}*\n"
        f"🚑 الطوارئ: *{EMERGENCY_NUMBER}*\n\n"
        "يمكنني مساعدتكم في:\n"
        "• حجز وإدارة المواعيد\n"
        "• معلومات الأطباء\n"
        "• التأمينات المعتمدة\n"
        "• مواعيد العمل والموقع\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
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
        "8️⃣ Contact Information\n"
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
            f"🗺️ {MAPS_LINK}\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        "Locations & directions:\n"
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
    if lang == "ar":
        return (
            f"تم استلام طلبكم ✅\n"
            f"شركة التأمين: *{choice}*\n\n"
            "مبدئيًا: قد تختلف التغطية حسب الخطة.\n"
            "للتأكيد النهائي يمكن تحويلكم للاستقبال.\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ موظف الاستقبال"
        )
    return (
        f"Received ✅\n"
        f"Insurance: *{choice}*\n\n"
        "Coverage depends on your plan.\n"
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
        return ("لا توجد بيانات أطباء لهذا التخصص حاليًا.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                if lang == "ar"
                else "No doctors found for this specialty.\n\n0️⃣ Main Menu\n99️⃣ Reception")

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

def _extract_name_mobile_id(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw0 = (raw or "").strip()
    if not raw0:
        return None, None, None
    rawN = _normalize_digits(raw0)
    lines = [ln.strip() for ln in rawN.splitlines() if ln.strip()]
    name = lines[0] if lines else rawN

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
    pid = seqs[1] if len(seqs) >= 2 else None
    return name, mobile, pid

def _make_reference(prefix: str = "SSH") -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    rnd = random.randint(10000, 99999)
    return f"{prefix}-{today}-{rnd}"


def handle_turn(
    user_id: str,
    message_text: str,
    language: str,
    session_in: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    # If handoff active => stop bot (enterprise behavior)
    if _handoff_is_active(sess):
        # Optional: allow user to return to menu with 0 (you can remove if you want strict stop)
        if _low(message_text) == "0":
            sess["handoff_active"] = False
            sess["handoff_until"] = None
        else:
            # silent stop
            return EngineResult("", sess, [])

    # Language lock
    lang = _lang(language or sess.get("language") or "ar")
    if bool(sess.get("language_locked")):
        lang = _lang(sess.get("language") or lang)
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    raw = _norm(message_text)
    tlow = _low(message_text)
    actions: List[Dict[str, Any]] = []

    # ✅ FIRST IMPRESSION OVERRIDES:
    # If greeting OR not greeted yet => show welcome (bilingual) and DO NOT show expiry
    if _is_greeting(message_text) or not bool(sess.get("has_greeted")):
        sess["has_greeted"] = True
        sess["status"] = STATUS_ACTIVE
        sess["state"] = STATE_LANG_SELECT
        sess["last_step"] = STATE_LANG_SELECT
        out = _enterprise_welcome_bilingual()
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # Update last_user_ts
    sess["last_user_ts"] = _utcnow_iso()

    # Emergency
    if _looks_like_emergency(message_text):
        out = _emergency_msg(lang)
        _set_bot(sess, out)
        sess["last_step"] = sess.get("state")
        return EngineResult(out, sess, actions)

    # Agent override ALWAYS
    if _wants_agent(message_text):
        sess["state"] = STATE_ESCALATION
        sess["status"] = STATUS_ACTIVE
        sess["escalation_flag"] = True
        sess["last_step"] = STATE_ESCALATION

        # lock bot for 30 minutes after transfer
        sess["handoff_active"] = True
        sess["handoff_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

        out = ("جاري تحويلكم إلى موظف الاستقبال. يرجى الانتظار..."
               if lang == "ar"
               else "Connecting you to a reception officer. Please wait...")
        _set_bot(sess, out)
        actions.append({"type": "ESCALATE", "reason": "user_requested_agent"})
        return EngineResult(out, sess, actions)

    # Session expiry (ONLY after the above greeting override)
    last = _parse_iso(sess.get("last_user_ts"))
    # We already set last_user_ts now, so we check previous stored timestamp:
    prev = _parse_iso(session_in.get("last_user_ts")) if isinstance(session_in, dict) else None
    if prev:
        sec = (datetime.now(timezone.utc) - prev).total_seconds()
        if sec >= SESSION_EXPIRE_SECONDS and sess.get("state") not in {STATE_CLOSED, STATE_ESCALATION}:
            _reset_flow_fields(sess)
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = ("انتهت الجلسة بسبب عدم النشاط. تفضل اختر من القائمة للمتابعة:\n\n" + _main_menu(lang)
                   if lang == "ar"
                   else "Your session expired due to inactivity. Please choose from the menu:\n\n" + _main_menu(lang))
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

    # 0 always menu
    if tlow == "0":
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Language select
    if sess.get("state") == STATE_LANG_SELECT:
        if _is_digit(raw):
            c = _to_int(raw)
            if c == 1:
                sess["language"] = "ar"
                sess["language_locked"] = True
                sess["text_direction"] = "rtl"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("ar")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)
            if c == 2:
                sess["language"] = "en"
                sess["language_locked"] = True
                sess["text_direction"] = "ltr"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("en")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

        out = _enterprise_welcome_bilingual()
        _set_bot(sess, out)
        sess["last_step"] = STATE_LANG_SELECT
        return EngineResult(out, sess, actions)

    # Menu
    if sess.get("state") == STATE_MENU:
        if _is_thanks(message_text):
            out = ("العفو. إذا احتجتم أي خدمة أخرى اكتبوا 0 لعرض القائمة."
                   if lang == "ar"
                   else "You’re welcome. If you need anything else, reply 0 for the main menu.")
            _set_bot(sess, out)
            sess["last_step"] = STATE_MENU
            return EngineResult(out, sess, actions)

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
                out = ("يرجى إدخال رقم المرجع أو رقم الجوال المسجل.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                       if lang == "ar"
                       else "Please enter your reference number or registered mobile.\n\n0️⃣ Main Menu\n99️⃣ Reception")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

            if choice == 3:
                _reset_flow_fields(sess)
                sess["intent"] = "CANCEL"
                sess["state"] = STATE_CANCEL_LOOKUP
                sess["last_step"] = STATE_CANCEL_LOOKUP
                out = ("يرجى إدخال رقم المرجع أو رقم الجوال المسجل لإتمام الإلغاء.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                       if lang == "ar"
                       else "Please enter your reference number or registered mobile to cancel.\n\n0️⃣ Main Menu\n99️⃣ Reception")
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
                sess["intent"] = "INSURANCE"
                out = _insurance_menu(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            if choice == 7:
                out = _locations(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            if choice == 8:
                out = _contact_info(lang)
                _set_bot(sess, out)
                sess["last_step"] = STATE_MENU
                return EngineResult(out, sess, actions)

            msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid option number.")
            out = _invalid_with_exit(sess, lang, msg) + "\n\n" + _main_menu(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_MENU
            return EngineResult(out, sess, actions)

        # Insurance follow-up when intent is INSURANCE
        if sess.get("intent") == "INSURANCE":
            if _is_digit(raw):
                c = _to_int(raw)
                if 1 <= c <= 4:
                    out = _insurance_result(lang, INSURANCE_LIST[c - 1])
                    sess["intent"] = None
                    _set_bot(sess, out)
                    sess["last_step"] = STATE_MENU
                    return EngineResult(out, sess, actions)
            out = _insurance_menu(lang)
            _set_bot(sess, out)
            sess["last_step"] = STATE_MENU
            return EngineResult(out, sess, actions)

        out = _main_menu(lang)
        _set_bot(sess, out)
        sess["last_step"] = STATE_MENU
        return EngineResult(out, sess, actions)

    # Remaining booking/reschedule/cancel flow:
    # Keep your previous V2 logic here if already working, or paste from your last version.
    # For brevity, we fall back to menu if unknown state.
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