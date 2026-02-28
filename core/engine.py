# core/engine.py — Enterprise WhatsApp Clinic Engine (V4.5)
# Fixes:
# ✅ Options 2 and 3 implemented (Reschedule / Cancel) with minimal real flows
# ✅ Single-state routing (no fake “invalid” for menu options)
# ✅ Numeric normalization everywhere (",1" works)
# ✅ Timings formatting improved

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

# ✅ Implemented
STATE_RESCHEDULE_REF = "RESCHEDULE_REF"
STATE_RESCHEDULE_NEW_DATE = "RESCHEDULE_NEW_DATE"
STATE_RESCHEDULE_NEW_SLOT = "RESCHEDULE_NEW_SLOT"
STATE_RESCHEDULE_CONFIRM = "RESCHEDULE_CONFIRM"

# ✅ Implemented
STATE_CANCEL_REF = "CANCEL_REF"
STATE_CANCEL_CONFIRM = "CANCEL_CONFIRM"

STATE_CLOSED = "CLOSED"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "CLINIC_ENGINE_V4_5"
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

TIMINGS_AR = "🗓 السبت – الخميس: 9:00 ص – 9:00 م\n❌ الجمعة: مغلق"
TIMINGS_EN = "🗓 Sat – Thu: 9:00 AM – 9:00 PM\n❌ Friday: Closed"

INSURANCE_AR = "التأمينات المعتمدة: بوبا، التعاونية، ميدغلف (مثال)."
INSURANCE_EN = "Accepted insurance: Bupa, Tawuniya, Medgulf (example)."

LOCATION_AR = "الموقع: (تجريبي) سيتم إضافة رابط خرائط جوجل لاحقًا."
LOCATION_EN = "Location: (demo) Google Maps link will be added later."

CONTACT_AR = f"📞 الاستقبال: {RECEPTION_PHONE}\n🚑 الطوارئ: {EMERGENCY_NUMBER}"
CONTACT_EN = f"📞 Reception: {RECEPTION_PHONE}\n🚑 Emergency: {EMERGENCY_NUMBER}"

_REF_RE = re.compile(r"\bSSH-\d{6}-\d{4}\b", re.IGNORECASE)

@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


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
    for ch in ["،", ",", "٫", ";", "؛", "。"]:
        t = t.replace(ch, "")
    t = t.translate(_ARABIC_DIGITS)
    t = " ".join(t.split())
    return t


def _norm(text: str) -> str:
    return _clean_input(text)


def _low(text: str) -> str:
    return _clean_input(text).lower()


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
    ar = {"السلام عليكم", "مرحبا", "أهلا", "اهلا", "هلا", "صباح الخير", "مساء الخير"}
    if tl in en:
        return True
    raw = _norm(text)
    return any(p in raw for p in ar)


def _is_thanks(text: str) -> bool:
    tl = _low(text)
    return tl in {"thanks", "thank you", "thx", "ok", "okay", "understood", "got it", "شكرا", "شكراً", "شكرًا", "تمام", "مفهوم", "تم"}


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
        "menu_mistakes": 0,
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

        # for reschedule/cancel
        "target_ref": None,
        "new_date": None,
        "new_slot": None,

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
        "target_ref",
        "new_date",
        "new_slot",
    ]:
        sess[k] = None
    sess["mistakes"] = 0
    sess["menu_mistakes"] = 0


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


def _welcome_text(lang_hint: str = "ar") -> str:
    if lang_hint == "en":
        return (
            f"Welcome to *{CLINIC_NAME_EN}* 🏥\n"
            "Official WhatsApp Virtual Assistant.\n\n"
            f"📞 Reception: *{RECEPTION_PHONE}*\n"
            f"🚑 Emergency: *{EMERGENCY_NUMBER}*\n\n"
            "Please select your preferred language:\n"
            "1️⃣ العربية\n"
            "2️⃣ English\n\n"
            "To reach Reception anytime, reply: *Agent* or 9 (or 99)"
        )
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


def _menu_hint(lang: str) -> str:
    if lang == "ar":
        return "فضلاً اختر رقمًا من القائمة (1-9)، أو اكتب 0 لعرض القائمة، أو 9 لموظف الاستقبال."
    return "Please reply with a menu number (1–9), or reply 0 to show the menu, or 9 for Reception."


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
    return (TIMINGS_AR if lang == "ar" else TIMINGS_EN) + _footer(lang)


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
    s = _norm(raw).replace("/", "-")
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

    text = _clean_input(raw0)

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

    return name_candidate or None, mobile, pid


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


def _ask_reference(lang: str, action: str, last_ref: Optional[str]) -> str:
    if lang == "ar":
        hint = f"\n(آخر مرجع لديك: {last_ref})" if last_ref else ""
        return (
            f"يرجى إدخال رقم المرجع لإتمام {action} (مثال: SSH-260228-3997)."
            f"{hint}"
            + _footer(lang)
        )
    hint = f"\n(Your last reference: {last_ref})" if last_ref else ""
    return (
        f"Please enter your booking reference to {action} (example: SSH-260228-3997)."
        f"{hint}"
        + _footer(lang)
    )


def _reschedule_confirm(sess: Dict[str, Any], lang: str) -> str:
    ref = sess.get("target_ref")
    nd = sess.get("new_date")
    ns = sess.get("new_slot")
    if lang == "ar":
        return (
            "تأكيد تعديل الموعد ✅\n\n"
            f"📌 المرجع: *{ref}*\n"
            f"📅 التاريخ الجديد: {nd}\n"
            f"⏰ الوقت الجديد: {ns}\n\n"
            "يرجى الرد:\n"
            "1️⃣ إرسال الطلب إلى الاستقبال\n"
            "2️⃣ تعديل مرة أخرى\n"
            "3️⃣ إلغاء العملية"
            + _footer(lang)
        )
    return (
        "Reschedule Request Summary ✅\n\n"
        f"📌 Reference: *{ref}*\n"
        f"📅 New date: {nd}\n"
        f"⏰ New time: {ns}\n\n"
        "Please reply:\n"
        "1️⃣ Send request to Reception\n"
        "2️⃣ Modify again\n"
        "3️⃣ Cancel"
        + _footer(lang)
    )


def _cancel_confirm(ref: str, lang: str) -> str:
    if lang == "ar":
        return (
            "تأكيد إلغاء الموعد ✅\n\n"
            f"📌 المرجع: *{ref}*\n\n"
            "يرجى الرد:\n"
            "1️⃣ إرسال طلب الإلغاء إلى الاستقبال\n"
            "2️⃣ رجوع"
            + _footer(lang)
        )
    return (
        "Cancel Confirmation ✅\n\n"
        f"📌 Reference: *{ref}*\n\n"
        "Please reply:\n"
        "1️⃣ Send cancellation to Reception\n"
        "2️⃣ Back"
        + _footer(lang)
    )


def handle_turn(user_id: str, message_text: str, language: str, session_in: Optional[Dict[str, Any]] = None) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    raw = _norm(message_text)
    low = _low(message_text)

    lang = _lang(sess.get("language") or language or "ar")
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    prev_last = sess.get("last_user_ts")
    sess["last_user_ts"] = _utcnow_iso()

    if prev_last and _session_expired_from(prev_last) and sess.get("state") != STATE_ESCALATION:
        locked = bool(sess.get("language_locked"))
        keep_lang = sess.get("language") or lang

        _reset_flow_fields(sess)
        sess["status"] = STATUS_ABANDONED
        sess["language"] = keep_lang
        sess["text_direction"] = "rtl" if keep_lang == "ar" else "ltr"
        sess["language_locked"] = locked
        sess["has_greeted"] = False

        if locked:
            sess["state"] = STATE_MENU
            out = _main_menu(keep_lang)
        else:
            sess["state"] = STATE_LANG
            out = _welcome_text("en" if keep_lang == "en" else "ar")

        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if low == "0":
        if not bool(sess.get("language_locked")):
            sess["state"] = STATE_LANG
            out = _welcome_text("en" if lang == "en" else "ar")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        sess["state"] = STATE_MENU
        sess["menu_mistakes"] = 0
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if low in {"9", "99"}:
        sess["state"] = STATE_ESCALATION
        sess["escalation_flag"] = True
        out = ("تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
               if lang == "ar" else "Connecting you to Reception ✅ Please wait... (Reply 0 for menu)")
        _set_bot(sess, out)
        return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

    if _is_thanks(raw):
        out = ("العفو ✅ إذا احتجت أي شيء آخر اكتب 0 لعرض القائمة."
               if lang == "ar" else "You’re welcome ✅ If you need anything else, reply 0 for the menu.")
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if _is_greeting(raw) and not bool(sess.get("has_greeted")):
        sess["has_greeted"] = True
        sess["state"] = STATE_LANG
        out = _welcome_text("en" if lang == "en" else "ar")
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # Language selection only in STATE_LANG
    if sess.get("state") == STATE_LANG:
        if _is_digit_choice(raw):
            c = _to_int(raw)
            if c == 1:
                sess["language"] = "ar"
                sess["text_direction"] = "rtl"
                sess["language_locked"] = True
                sess["has_greeted"] = True
                sess["status"] = STATUS_ACTIVE
                sess["state"] = STATE_MENU
                out = _main_menu("ar")
                _set_bot(sess, out)
                return EngineResult(out, sess, [])
            if c == 2:
                sess["language"] = "en"
                sess["text_direction"] = "ltr"
                sess["language_locked"] = True
                sess["has_greeted"] = True
                sess["status"] = STATUS_ACTIVE
                sess["state"] = STATE_MENU
                out = _main_menu("en")
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

        out = _welcome_text("en" if lang == "en" else "ar")
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_CLOSED:
        sess["status"] = STATUS_ACTIVE
        sess["state"] = STATE_MENU
        sess["menu_mistakes"] = 0
        out = _main_menu(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # MAIN MENU
    if sess.get("state") == STATE_MENU:
        if not raw:
            out = _menu_hint(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if _is_digit_choice(raw):
            sess["menu_mistakes"] = 0
            choice = _to_int(raw)

            if choice == 1:
                _reset_flow_fields(sess)
                sess["intent"] = "BOOK"
                sess["state"] = STATE_BOOK_DEPT
                out = _dept_prompt(lang)
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            # ✅ RESCHEDULE implemented
            if choice == 2:
                sess["intent"] = "RESCHEDULE"
                sess["state"] = STATE_RESCHEDULE_REF
                sess["mistakes"] = 0
                out = _ask_reference(lang, ("تعديل الموعد" if lang == "ar" else "reschedule"), sess.get("appt_ref"))
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            # ✅ CANCEL implemented
            if choice == 3:
                sess["intent"] = "CANCEL"
                sess["state"] = STATE_CANCEL_REF
                sess["mistakes"] = 0
                out = _ask_reference(lang, ("إلغاء الموعد" if lang == "ar" else "cancel"), sess.get("appt_ref"))
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

            if choice == 4:
                _reset_flow_fields(sess)
                sess["intent"] = "DOCTOR_INFO"
                sess["state"] = STATE_BOOK_DEPT
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
                sess["escalation_flag"] = True
                out = ("تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
                       if lang == "ar" else "Connecting you to Reception ✅ Please wait... (Reply 0 for menu)")
                _set_bot(sess, out)
                return EngineResult(out, sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

            msg = ("يرجى اختيار رقم صحيح من القائمة." if lang == "ar" else "Please choose a valid menu number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["menu_mistakes"] = int(sess.get("menu_mistakes", 0)) + 1
        out = _main_menu(lang) if int(sess["menu_mistakes"]) >= 2 else _menu_hint(lang)
        if int(sess["menu_mistakes"]) >= 2:
            sess["menu_mistakes"] = 0
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    # -------------------------
    # RESCHEDULE FLOW
    # -------------------------
    if sess.get("state") == STATE_RESCHEDULE_REF:
        ref = raw.upper()
        if not _REF_RE.search(ref):
            # allow last ref keyword
            if ref in {"LAST", "LASTREF"} and sess.get("appt_ref"):
                ref = str(sess.get("appt_ref")).upper()
            else:
                msg = ("رقم المرجع غير صحيح." if lang == "ar" else "That reference looks invalid.")
                out = _soft_invalid(sess, lang, msg) + "\n\n" + _ask_reference(lang, ("تعديل الموعد" if lang == "ar" else "reschedule"), sess.get("appt_ref"))
                _set_bot(sess, out)
                return EngineResult(out, sess, [])

        sess["target_ref"] = ref
        sess["state"] = STATE_RESCHEDULE_NEW_DATE
        sess["mistakes"] = 0
        out = ("يرجى إدخال التاريخ الجديد." if lang == "ar" else "Please enter the new appointment date.") + "\n" + _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_RESCHEDULE_NEW_DATE:
        norm_ymd, err = _parse_date_any(message_text)
        if not norm_ymd or _is_past_date(norm_ymd):
            msg = ("يرجى إدخال تاريخ صحيح ومستقبلي." if lang == "ar" else "Please enter a valid future date.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        sess["new_date"] = norm_ymd
        sess["state"] = STATE_RESCHEDULE_NEW_SLOT
        out = _slot_prompt(lang, norm_ymd)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_RESCHEDULE_NEW_SLOT:
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        if not (0 <= idx < len(SLOTS)):
            msg = ("يرجى اختيار رقم وقت صحيح." if lang == "ar" else "Please choose a valid slot number.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("new_date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        sess["new_slot"] = SLOTS[idx]
        sess["state"] = STATE_RESCHEDULE_CONFIRM
        out = _reschedule_confirm(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_RESCHEDULE_CONFIRM:
        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار 1 أو 2 أو 3." if lang == "ar" else "Please choose 1, 2, or 3.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _reschedule_confirm(sess, lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        c = _to_int(raw)
        if c == 1:
            ref = sess.get("target_ref")
            nd = sess.get("new_date")
            ns = sess.get("new_slot")
            out = ("تم استلام طلب تعديل الموعد ✅ سيتم التواصل معك خلال ساعات العمل.\n\nاكتب 0 للقائمة."
                   if lang == "ar" else "Reschedule request received ✅ Reception will confirm during working hours.\n\nReply 0 for the main menu.")
            actions = [{
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "RESCHEDULE",
                    "status": "PENDING",
                    "appt_ref": ref,
                    "new_date": nd,
                    "new_time": ns,
                    "notes": f"reschedule_ref={ref} new_date={nd} new_time={ns}",
                },
            }]
            sess["state"] = STATE_CLOSED
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        if c == 2:
            sess["state"] = STATE_RESCHEDULE_NEW_DATE
            out = ("تمام. أدخل التاريخ الجديد.\n" if lang == "ar" else "Okay. Enter the new date.\n") + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        if c == 3:
            sess["state"] = STATE_MENU
            out = ("تم إلغاء العملية.\n\n" if lang == "ar" else "Cancelled.\n\n") + _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

    # -------------------------
    # CANCEL FLOW
    # -------------------------
    if sess.get("state") == STATE_CANCEL_REF:
        ref = raw.upper()
        if not _REF_RE.search(ref):
            if ref in {"LAST", "LASTREF"} and sess.get("appt_ref"):
                ref = str(sess.get("appt_ref")).upper()
            else:
                msg = ("رقم المرجع غير صحيح." if lang == "ar" else "That reference looks invalid.")
                out = _soft_invalid(sess, lang, msg) + "\n\n" + _ask_reference(lang, ("إلغاء الموعد" if lang == "ar" else "cancel"), sess.get("appt_ref"))
                _set_bot(sess, out)
                return EngineResult(out, sess, [])
        sess["target_ref"] = ref
        sess["state"] = STATE_CANCEL_CONFIRM
        out = _cancel_confirm(ref, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_CANCEL_CONFIRM:
        if not _is_digit_choice(raw):
            msg = ("يرجى اختيار 1 أو 2." if lang == "ar" else "Please choose 1 or 2.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _cancel_confirm(sess.get("target_ref") or "", lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])
        c = _to_int(raw)
        if c == 1:
            ref = sess.get("target_ref")
            out = ("تم استلام طلب الإلغاء ✅ سيتم التأكيد خلال ساعات العمل.\n\nاكتب 0 للقائمة."
                   if lang == "ar" else "Cancellation request received ✅ Reception will confirm during working hours.\n\nReply 0 for the main menu.")
            actions = [{
                "type": "CREATE_APPOINTMENT_REQUEST",
                "payload": {
                    "intent": "CANCEL",
                    "status": "PENDING",
                    "appt_ref": ref,
                    "notes": f"cancel_ref={ref}",
                },
            }]
            sess["state"] = STATE_CLOSED
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        if c == 2:
            sess["state"] = STATE_MENU
            out = _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

    # -------------------------
    # BOOKING FLOW (same as before)
    # -------------------------
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
        out = _date_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_DATE:
        norm_ymd, err = _parse_date_any(message_text)
        if not norm_ymd:
            msg = ("Date format is invalid. Example: 2026-02-28 or 28-02-2026 or 28/02/2026"
                   if lang == "en" else "صيغة التاريخ غير صحيحة. مثال: 2026-02-28 أو 28-02-2026 أو 28/02/2026")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if _is_past_date(norm_ymd):
            msg = ("Past dates are not allowed. Please choose a future date." if lang == "en"
                   else "لا يمكن اختيار تاريخ سابق. يرجى اختيار تاريخ قادم.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _date_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["date"] = norm_ymd
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_SLOT
        out = _slot_prompt(lang, norm_ymd)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_SLOT:
        idx = _to_int(raw, -1) - 1 if _is_digit_choice(raw) else -1
        if not (0 <= idx < len(SLOTS)):
            msg = ("Please choose a valid slot number." if lang == "en" else "يرجى اختيار رقم وقت صحيح.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _slot_prompt(lang, sess.get("date") or "")
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["slot"] = SLOTS[idx]
        sess["mistakes"] = 0
        sess["state"] = STATE_BOOK_PATIENT
        out = _patient_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_PATIENT:
        name, mobile, pid = _extract_name_mobile_id(message_text)
        sess["patient_name"] = name
        sess["patient_mobile"] = mobile
        sess["patient_id"] = pid

        if not sess.get("patient_name") or not sess.get("patient_mobile"):
            msg = ("Please send full name and mobile number." if lang == "en" else "فضلاً أرسل الاسم الكامل ورقم الجوال.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _patient_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        sess["mistakes"] = 0
        sess["appt_ref"] = sess.get("appt_ref") or _make_reference("SSH")
        sess["state"] = STATE_BOOK_CONFIRM
        out = _confirmation(sess, lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, [])

    if sess.get("state") == STATE_BOOK_CONFIRM:
        if not _is_digit_choice(raw):
            msg = ("Please choose 1, 2, or 3." if lang == "en" else "يرجى اختيار 1 أو 2 أو 3.")
            out = _soft_invalid(sess, lang, msg) + "\n\n" + _confirmation(sess, lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        c = _to_int(raw)
        if c == 1:
            sess["status"] = STATUS_COMPLETED
            sess["state"] = STATE_CLOSED

            ref = sess.get("appt_ref") or _make_reference("SSH")
            sess["appt_ref"] = ref

            out = (
                f"Booking request received ✅\n📌 Reference: *{ref}*\n"
                "Reception will confirm your appointment during working hours.\n"
                "Please arrive 15 minutes early.\n\n"
                f"{TIMINGS_EN}\n"
                f"🚑 Emergency: {EMERGENCY_NUMBER}\n"
                "Reception: 9 (or 99)\n\n"
                "Reply 0 for the main menu."
                if lang == "en" else
                f"تم استلام طلب الحجز ✅\n📌 رقم المرجع: *{ref}*\n"
                "سيقوم موظف الاستقبال بتأكيد الموعد خلال ساعات العمل.\n"
                "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
                f"{TIMINGS_AR}\n"
                f"🚑 الطوارئ: {EMERGENCY_NUMBER}\n"
                "للتواصل مع الاستقبال: 9 (أو 99)\n\n"
                "للعودة للقائمة الرئيسية اكتب 0."
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
            sess["mistakes"] = 0
            out = ("Okay. Let's choose the specialty again.\n\n" if lang == "en" else "تمام. لنعد لاختيار التخصص.\n\n") + _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

        if c == 3:
            sess["state"] = STATE_MENU
            sess["mistakes"] = 0
            out = ("Request cancelled. Please choose from the menu.\n\n" if lang == "en" else "تم إلغاء الطلب. للمتابعة اختر من القائمة.\n\n") + _main_menu(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, [])

    sess["state"] = STATE_MENU
    out = _main_menu(lang)
    _set_bot(sess, out)
    return EngineResult(out, sess, [])


def run_engine(session: Dict[str, Any], user_message: str, language: str, arabic_tone: Optional[str] = None, kpi_signals: Optional[list] = None) -> Dict[str, Any]:
    user_id = (session or {}).get("user_id") or "unknown"
    res = handle_turn(user_id=user_id, message_text=user_message, language=language, session_in=session)
    return {"reply_text": res.reply_text, "session": res.session, "actions": res.actions}