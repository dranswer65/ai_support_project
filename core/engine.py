# core/engine.py — Enterprise WhatsApp Clinic Engine (V4.2)
# - Arabic-first greeting + language lock + menu + booking flows
# - 0 = show menu
# - 9 = Reception (99 is accepted as alias)
#
# V4.2 Fixes (based on your latest WhatsApp tests):
# ✅ Session-expiry no longer resets language / language_locked (prevents “language menu again”)
# ✅ Session-expiry returns MAIN_MENU immediately if language is locked
# ✅ Input normalization fixes ",0" / "،0" and punctuation around numeric commands (engine-side safety)
# ✅ Greeting detection uses cleaned text
# ✅ Keeps V4.1 improvements: main-menu no redundant "0", reception=9, name parsing removes mobile from name, wording is “Booking Request Summary”

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone
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
STATE_RESCHEDULE_NEW_DATE = "RESCHEDULE_NEW_DATE"
STATE_RESCHEDULE_NEW_SLOT = "RESCHEDULE_NEW_SLOT"
STATE_RESCHEDULE_CONFIRM = "RESCHEDULE_CONFIRM"

STATE_CANCEL_LOOKUP = "CANCEL_LOOKUP"
STATE_CANCEL_CONFIRM = "CANCEL_CONFIRM"

STATE_CLOSED = "CLOSED"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "CLINIC_ENGINE_V4_2"
SESSION_EXPIRE_SECONDS = 60 * 60  # 60 min

CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"

RECEPTION_PHONE = "+966XXXXXXXX"
EMERGENCY_NUMBER = "997"

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


def _normalize_digits(s: str) -> str:
    return (s or "").translate(_ARABIC_DIGITS)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _clean_input(text: str) -> str:
    t = (text or "").strip()
    # normalize common punctuation that breaks numeric commands like ",0" or "،0"
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


def _is_greeting(text: str) -> bool:
    tl = _low(text)
    en = {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}
    ar = {
        "السلام عليكم",
        "السلام عليكم ورحمة الله",
        "السلام عليكم ورحمه الله",
        "مرحبا", "أهلا", "اهلا", "هلا",
        "صباح الخير", "مساء الخير"
    }
    if tl in en:
        return True
    raw = _clean_input(text)  # keep Arabic shapes
    return any(p in raw for p in ar)


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


def _seconds_since(prev_iso: Optional[str]) -> Optional[float]:
    dt = _parse_iso(prev_iso)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _session_expired_from(prev_iso: Optional[str]) -> bool:
    sec = _seconds_since(prev_iso)
    if sec is None:
        return False
    return sec >= SESSION_EXPIRE_SECONDS


def _welcome_text() -> str:
    return (
        f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        f"📞 الاستقبال: *{RECEPTION_PHONE}*\n"
        f"🚑 الطوارئ: *{EMERGENCY_NUMBER}*\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "*(Please select your preferred language)*\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 9 (أو 99)"
    )


def _main_menu(lang: str) -> str:
    # No "0 Main Menu" inside the main menu itself
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
            "9️⃣ موظف الاستقبال"
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
        "9️⃣ Reception"
    )


def _footer(lang: str) -> str:
    if lang == "ar":
        return "\n\n0️⃣ القائمة الرئيسية\n9️⃣ موظف الاستقبال"
    return "\n\n0️⃣ Main Menu\n9️⃣ Reception"


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
        return "يرجى كتابة تاريخ الموعد (مثال: 2026-02-28 أو 28-02-2026 أو 28/02/2026)" + _footer(lang)
    return "Please enter the appointment date (example: 2026-02-28 or 28-02-2026 or 28/02/2026)" + _footer(lang)


def _slot_prompt(lang: str, date_str: str) -> str:
    lines = [f"{i}️⃣ {s}" for i, s in enumerate(SLOTS, start=1)]
    if lang == "ar":
        return f"الأوقات المتاحة بتاريخ {date_str}:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الوقت)" + _footer(lang)
    return f"Available time slots on {date_str}:\n\n" + "\n".join(lines) + "\n\n(Reply with slot number)" + _footer(lang)


def _patient_prompt(lang: str) -> str:
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


def _insurance_text(lang: str) -> str:
    return (INSURANCE_AR if lang == "ar" else INSURANCE_EN) + _footer(lang)


def _timings_text(lang: str) -> str:
    return (CLINIC_TIMINGS_AR if lang == "ar" else CLINIC_TIMINGS_EN) + _footer(lang)


def _location_text(lang: str) -> str:
    return (LOCATION_AR if lang == "ar" else LOCATION_EN) + _footer(lang)


def _contact_text(lang: str) -> str:
    return (CONTACT_AR if lang == "ar" else CONTACT_EN) + _footer(lang)


def _soft_invalid(sess: Dict[str, Any], lang: str, msg: str) -> str:
    sess["mistakes"] = int(sess.get("mistakes", 0)) + 1
    if sess["mistakes"] >= 2:
        if lang == "ar":
            return msg + "\n\nإذا رغبت، يمكنني تحويلك لموظف الاستقبال: 9 (أو 99)"
        return msg + "\n\nIf you prefer, I can connect you to Reception: 9 (or 99)"
    return msg


def _parse_date_any(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Accept:
      YYYY-MM-DD, YYYY/MM/DD
      DD-MM-YYYY, DD/MM/YYYY
    Return (normalized YYYY-MM-DD, error_reason) where error_reason in {None, "format", "invalid_date"}.
    """
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


def _is_past_date(ymd: str) -> bool:
    try:
        dt = datetime.strptime(ymd, "%Y-%m-%d").date()
        return dt < datetime.now(timezone.utc).date()
    except Exception:
        return False


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

    return name or None, mobile, pid


def _make_reference(prefix: str = "SSH") -> str:
    today = datetime.now(timezone.utc).strftime("%y%m%d")
    rnd = random.randint(1000, 9999)
    return f"{prefix}-{today}-{rnd}"


def _confirmation(sess: Dict[str, Any], lang: str) -> str:
    ref = sess.get("appt_ref") or ""
    pid = sess.get("patient_id")
    pid_line = ""
    if pid:
        pid_line = (f"🪪 الهوية/الإقامة: {pid}\n" if lang == "ar" else f"🪪 ID/Iqama: {pid}\n")

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
            + f"⏰ الوقت: {sess.get('slot')}\n\n"
            "يرجى الرد:\n"
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
        + f"⏰ Time: {sess.get('slot')}\n\n"
        "Please reply:\n"
        "1️⃣ Send request to Reception\n"
        "2️⃣ Modify\n"
        "3️⃣ Cancel"
        + _footer(lang)
    )


def handle_turn(
    user_id: str,
    message_text: str,
    language: str,
    session_in: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    # Always work with cleaned input inside engine
    raw = _norm(message_text)
    low = _low(message_text)

    # Respect existing session language; controller may already lock it
    lang = _lang(sess.get("language") or language or "ar")
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    prev_last = sess.get("last_user_ts")
    sess["last_user_ts"] = _utcnow_iso()

    # ----------------------------
    # Session expiry handling (V4.2 FIX)
    # - Expire flow fields, but DO NOT reset language/language_locked
    # - If language is locked, go straight to MAIN_MENU (prevents language menu restart)
    # ----------------------------
    if prev_last and _session_expired_from(prev_last) and sess.get("state") != STATE_ESCALATION:
        locked = bool(sess.get("language_locked"))
        keep_lang = sess.get("language") or lang

        _reset_flow_fields(sess)
        sess["status"] = STATUS_ABANDONED

        sess["language"] = keep_lang
        sess["text_direction"] = "rtl" if keep_lang == "ar" else "ltr"
        # keep lock as-is (do NOT force False)
        sess["language_locked"] = locked
        sess["has_greeted"] = False
        sess["mistakes"] = 0

        if locked:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu(keep_lang)
        else:
            sess["state"] = STATE_LANG
            sess["last_step"] = STATE_LANG
            out = _welcome_text()

        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # 0 = show main menu (if language locked) or language selection (if not)
    if low in {"0", "٠"}:
        if not bool(sess.get("language_locked")):
            sess["state"] = STATE_LANG
            sess["last_step"] = STATE_LANG
            out = _welcome_text()
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Reception shortcuts (9 primary, 99 alias)
    if low in {"9", "99"}:
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

    if _is_greeting(raw) and not bool(sess.get("language_locked")):
        sess["state"] = STATE_LANG
        sess["last_step"] = STATE_LANG
        out = _welcome_text()
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Language selection
    if sess.get("state") == STATE_LANG or not bool(sess.get("language_locked")):
        if _is_digit_choice(raw):
            c = _to_int(raw)
            if c == 1:
                sess["language"] = "ar"
                sess["text_direction"] = "rtl"
                sess["language_locked"] = True
                sess["status"] = STATUS_ACTIVE
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("ar")
                _set_bot(sess, out)
                return EngineResult(out, sess, [])
            if c == 2:
                sess["language"] = "en"
                sess["text_direction"] = "ltr"
                sess["language_locked"] = True
                sess["status"] = STATUS_ACTIVE
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("en")
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

        sess["state"] = STATE_LANG
        sess["last_step"] = STATE_LANG
        out = _welcome_text()
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_CLOSED:
        sess["status"] = STATUS_ACTIVE
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # MAIN MENU routing
    if sess.get("state") == STATE_MENU:
        if not raw:
            out = _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

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

            if choice == 2:
                _reset_flow_fields(sess)
                sess["intent"] = "RESCHEDULE"
                sess["state"] = STATE_RESCHEDULE_LOOKUP
                sess["last_step"] = STATE_RESCHEDULE_LOOKUP
                out = ("يرجى إدخال رقم المرجع أو رقم الجوال المسجل." if lang == "ar"
                       else "Please enter your reference number or registered mobile.") + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 3:
                _reset_flow_fields(sess)
                sess["intent"] = "CANCEL"
                sess["state"] = STATE_CANCEL_LOOKUP
                sess["last_step"] = STATE_CANCEL_LOOKUP
                out = ("يرجى إدخال رقم المرجع أو رقم الجوال المسجل لإتمام الإلغاء." if lang == "ar"
                       else "Please enter your reference number or registered mobile to cancel.") + _footer(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 4:
                _reset_flow_fields(sess)
                sess["intent"] = "DOCTOR_INFO"
                sess["state"] = STATE_BOOK_DEPT
                sess["last_step"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 5:
                out = _timings_text(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 6:
                out = _insurance_text(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 7:
                out = _location_text(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 8:
                out = _contact_text(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 9:
                sess["state"] = STATE_ESCALATION
                sess["last_step"] = STATE_ESCALATION
                sess["escalation_flag"] = True
                out = ("تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
                       if lang == "ar" else "Connecting you to Reception ✅ Please wait... (Reply 0 for menu)")
                _set_bot(sess, out)
                return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

            msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid menu number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # BOOKING FLOWS
    if sess.get("state") == STATE_BOOK_DEPT:
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        dept_key = None
        dept_label = None

        if 0 <= idx < len(DEPTS):
            dept_key = DEPTS[idx]["key"]
            dept_label = DEPTS[idx]["ar"] if lang == "ar" else DEPTS[idx]["en"]
        else:
            for d in DEPTS:
                if _low(d["ar"]) in low or _low(d["en"]) in low:
                    dept_key = d["key"]
                    dept_label = d["ar"] if lang == "ar" else d["en"]
                    break

        if not dept_key:
            msg = ("يرجى اختيار تخصص صحيح." if lang == "ar" else "Please choose a valid specialty.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["dept_key"] = dept_key
        sess["dept_label"] = dept_label
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_DOCTOR
        sess["last_step"] = STATE_BOOK_DOCTOR
        out = _doctor_prompt(lang, dept_key)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

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
                if _low(doc["ar"]) in low or _low(doc["en"]) in low:
                    chosen_key = doc.get("key")
                    chosen_label = doc["ar"] if lang == "ar" else doc["en"]
                    break

        if not chosen_label:
            msg = ("يرجى اختيار طبيب صحيح." if lang == "ar" else "Please choose a valid doctor.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _doctor_prompt(lang, sess.get("dept_key") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["doctor_key"] = chosen_key
        sess["doctor_label"] = chosen_label
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_DATE
        sess["last_step"] = STATE_BOOK_DATE
        out = _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_DATE:
        norm_ymd, err = _parse_date_any(message_text)
        if not norm_ymd:
            if lang == "ar":
                msg = "صيغة التاريخ غير صحيحة. مثال: 2026-02-28 أو 28-02-2026 أو 28/02/2026" if err == "format" else "التاريخ غير صالح. يرجى إدخال تاريخ صحيح."
            else:
                msg = "Date format is invalid. Example: 2026-02-28 or 28-02-2026 or 28/02/2026" if err == "format" else "That date is not valid. Please enter a valid date."
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if _is_past_date(norm_ymd):
            msg = ("لا يمكن اختيار تاريخ سابق. يرجى اختيار تاريخ قادم." if lang == "ar"
                   else "Past dates are not allowed. Please choose a future date.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["date"] = norm_ymd
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT
        sess["last_step"] = STATE_BOOK_SLOT
        out = _slot_prompt(lang, norm_ymd)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_SLOT:
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        if not (0 <= idx < len(SLOTS)):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["slot"] = SLOTS[idx]
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_PATIENT
        sess["last_step"] = STATE_BOOK_PATIENT
        out = _patient_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_PATIENT:
        name, mobile, pid = _extract_name_mobile_id(message_text)
        sess["patient_name"] = name
        sess["patient_mobile"] = mobile
        sess["patient_id"] = pid

        if not sess.get("patient_name") or not sess.get("patient_mobile"):
            msg = ("فضلاً أرسل الاسم الكامل ورقم الجوال." if lang == "ar" else "Please send full name and mobile number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _patient_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["mistakes"] = 0
        sess["appt_ref"] = sess.get("appt_ref") or _make_reference("SSH")
        sess["state"] = STATE_BOOK_CONFIRM
        sess["last_step"] = STATE_BOOK_CONFIRM
        out = _confirmation(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_CONFIRM:
        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _confirmation(sess, lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        c = _to_int(raw)
        if c == 1:
            sess["status"] = STATUS_COMPLETED
            sess["state"] = STATE_CLOSED
            sess["last_step"] = STATE_CLOSED

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
                    "للتواصل مع الاستقبال: 9 (أو 99)\n\n"
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
                    "Reception: 9 (or 99)\n\n"
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
            sess["state"] = STATE_BOOK_DEPT
            sess["last_step"] = STATE_BOOK_DEPT
            sess["mistakes"] = 0
            out = ("تمام. لنعد لاختيار التخصص.\n\n" if lang == "ar" else "Okay. Let's choose the specialty again.\n\n")
            out += _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if c == 3:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            sess["mistakes"] = 0
            out = ("تم إلغاء الطلب. للمتابعة اختر من القائمة.\n\n" if lang == "ar" else "Request cancelled. Please choose from the menu.\n\n")
            out += _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
        out = _soft_invalid(sess, lang, msg) + "\n\n" + _confirmation(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

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