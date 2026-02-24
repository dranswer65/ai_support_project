# core/engine.py — CLINIC BOOKING DEMO ENGINE (Arabic-first UX supported)
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List
from datetime import datetime

STATE_ACTIVE = "ACTIVE"
STATE_BOOK_DEPT = "BOOK_DEPT"
STATE_BOOK_DOCTOR = "BOOK_DOCTOR"
STATE_BOOK_DATE = "BOOK_DATE"
STATE_BOOK_SLOT = "BOOK_SLOT"
STATE_BOOK_PATIENT = "BOOK_PATIENT"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"
STATE_CONFIRMED = "CONFIRMED"
STATE_CLOSED = "CLOSED"
ENGINE_MARKER = "BOOKING_ENGINE_V1"

def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _norm(t: str) -> str:
    return (t or "").strip().lower()

def _is_goodbye(t: str) -> bool:
    t=_norm(t)
    return t in {"bye","goodbye","see you","مع السلامة","سلام","الى اللقاء","إلى اللقاء","باي"}

def _is_no(t: str) -> bool:
    t=_norm(t)
    return t in {"no","nope","nah","لا","لا شكرا","لا شكرًا","ليس الآن","مو","مش"}

def _is_yes(t: str) -> bool:
    t=_norm(t)
    return t in {"yes","y","yeah","sure","ok","okay","تمام","نعم","اي","أكيد","اوكي"}

def _is_thanks(t: str) -> bool:
    t=_norm(t)
    return t in {"thanks","thank you","thx","شكرا","شكرًا","مشكور","الله يعطيك العافية"}

def _looks_like_booking(t: str) -> bool:
    t=_norm(t)
    return any(k in t for k in [
        "appointment","book","booking","schedule","doctor","clinic",
        "موعد","حجز","احجز","عيادة","دكتور","طبيب","زيارة"
    ])

# Demo catalog (later we’ll load from tenant settings)
DEPTS_AR = ["باطنة", "أطفال", "جلدية", "أسنان"]
DEPTS_EN = ["Internal Medicine", "Pediatrics", "Dermatology", "Dental"]

DOCTORS = {
    "Internal Medicine": ["Dr. Ahmed", "Dr. Sara"],
    "Pediatrics": ["Dr. Mona"],
    "Dermatology": ["Dr. Ali"],
    "Dental": ["Dr. Khaled"],
    "باطنة": ["د. أحمد", "د. سارة"],
    "أطفال": ["د. منى"],
    "جلدية": ["د. علي"],
    "أسنان": ["د. خالد"],
}

SLOTS = ["10:00", "10:30", "11:00", "11:30", "17:00", "17:30"]

@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]

def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "state": STATE_ACTIVE,
        "language": "ar",  # Arabic-first for GCC demo
        "text_direction": "rtl",
        "has_greeted": False,
        "no_count": 0,
        "dept": None,
        "doctor": None,
        "date": None,
        "slot": None,
        "patient_name": None,
        "last_user_message": None,
        "last_bot_message": "",
        "last_bot_ts": None,
        "last_user_ts": _utcnow_iso(),
    }

def _greet(sess: Dict[str, Any], lang: str, msg: str) -> str:
    if sess.get("has_greeted"):
        return msg
    sess["has_greeted"] = True
    return (f"مرحبًا! 👋\n{msg}" if lang == "ar" else f"Hello! 👋\n{msg}")

def _set_bot(sess: Dict[str, Any], msg: str) -> None:
    sess["last_bot_message"] = msg
    sess["last_bot_ts"] = _utcnow_iso()

def _dept_prompt(lang: str) -> str:
    if lang == "ar":
        items = "\n".join([f"{i+1}) {d}" for i,d in enumerate(DEPTS_AR)])
        return f"أكيد ✅\nاختر القسم:\n{items}\n(اكتب رقم الخيار)"
    items = "\n".join([f"{i+1}) {d}" for i,d in enumerate(DEPTS_EN)])
    return f"Sure ✅\nChoose department:\n{items}\n(Reply with number)"

def _slots_prompt(lang: str) -> str:
    if lang == "ar":
        return "اختر الوقت المناسب:\n" + "\n".join([f"{i+1}) {s}" for i,s in enumerate(SLOTS)]) + "\n(اكتب رقم الخيار)"
    return "Choose a time:\n" + "\n".join([f"{i+1}) {s}" for i,s in enumerate(SLOTS)]) + "\n(Reply with number)"

def handle_turn(user_id: str, message_text: str, language: str, session_in: Optional[Dict[str, Any]]=None) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id
    sess["last_user_message"] = message_text
    sess["last_user_ts"] = _utcnow_iso()

    lang = (language or sess.get("language") or "ar").lower()
    sess["language"] = "ar" if lang.startswith("ar") else "en"
    lang = sess["language"]
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    t = _norm(message_text)
    actions: List[Dict[str, Any]] = []

    if _is_goodbye(t):
        sess["state"] = STATE_CLOSED
        out = "مع السلامة! ✅" if lang=="ar" else "Goodbye! ✅"
        out = _greet(sess, lang, out)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Global thanks
    if _is_thanks(t):
        out = "على الرحب والسعة ✅ هل تريد حجز موعد؟" if lang=="ar" else "You’re welcome ✅ Do you want to book an appointment?"
        out = _greet(sess, lang, out)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Global NO handling (never close)
    if _is_no(t):
        sess["no_count"] = int(sess.get("no_count", 0)) + 1
        if sess["no_count"] == 1:
            out = "تمام 👍 إذا احتجت حجز موعد لاحقًا أنا جاهز." if lang=="ar" else "Okay 👍 If you need to book later, I’m here."
        else:
            out = "تحب تحجز موعد؟ اكتب: حجز موعد" if lang=="ar" else "Want to book? Type: book appointment"
        out = _greet(sess, lang, out)
        _set_bot(sess, out)
        sess["state"] = STATE_ACTIVE
        return EngineResult(out, sess, actions)

    # Start booking intent
    if sess.get("state") == STATE_ACTIVE:
        if _looks_like_booking(t) or t in {"1","book","appointment","حجز","موعد","احجز"}:
            sess["state"] = STATE_BOOK_DEPT
            out = _dept_prompt(lang)
            out = _greet(sess, lang, out)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        out = _greet(sess, lang, ("هل تريد حجز موعد؟ اكتب: حجز موعد" if lang=="ar" else "Do you want to book an appointment? Type: book appointment"))
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Department
    if sess.get("state") == STATE_BOOK_DEPT:
        idx = int(t) - 1 if t.isdigit() else -1
        dept = None
        if lang=="ar" and 0 <= idx < len(DEPTS_AR): dept = DEPTS_AR[idx]
        if lang=="en" and 0 <= idx < len(DEPTS_EN): dept = DEPTS_EN[idx]
        if not dept:
            out = _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        sess["dept"] = dept
        sess["state"] = STATE_BOOK_DOCTOR
        docs = DOCTORS.get(dept, [])
        if lang=="ar":
            out = "اختر الطبيب:\n" + "\n".join([f"{i+1}) {d}" for i,d in enumerate(docs)]) + "\n(اكتب رقم الخيار)"
        else:
            out = "Choose doctor:\n" + "\n".join([f"{i+1}) {d}" for i,d in enumerate(docs)]) + "\n(Reply with number)"
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Doctor
    if sess.get("state") == STATE_BOOK_DOCTOR:
        docs = DOCTORS.get(sess.get("dept"), [])
        idx = int(t) - 1 if t.isdigit() else -1
        if not (0 <= idx < len(docs)):
            out = ("اختر رقم الطبيب من القائمة." if lang=="ar" else "Please choose a doctor number from the list.")
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["doctor"] = docs[idx]
        sess["state"] = STATE_BOOK_DATE
        out = "اكتب التاريخ (مثال: 2026-02-24)" if lang=="ar" else "Enter date (example: 2026-02-24)"
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Date (light validation)
    if sess.get("state") == STATE_BOOK_DATE:
        if len(t) < 8:
            out = "من فضلك اكتب التاريخ بهذا الشكل: 2026-02-24" if lang=="ar" else "Please enter date like: 2026-02-24"
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["date"] = message_text.strip()
        sess["state"] = STATE_BOOK_SLOT
        out = _slots_prompt(lang)
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Slot
    if sess.get("state") == STATE_BOOK_SLOT:
        idx = int(t) - 1 if t.isdigit() else -1
        if not (0 <= idx < len(SLOTS)):
            out = _slots_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["slot"] = SLOTS[idx]
        sess["state"] = STATE_BOOK_PATIENT
        out = "ما اسم المريض؟" if lang=="ar" else "Patient name?"
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Patient name
    if sess.get("state") == STATE_BOOK_PATIENT:
        name = (message_text or "").strip()
        if len(name) < 2:
            out = "من فضلك اكتب الاسم الكامل." if lang=="ar" else "Please enter full name."
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)
        sess["patient_name"] = name
        sess["state"] = STATE_BOOK_CONFIRM
        if lang=="ar":
            out = (
                "تأكيد الحجز ✅\n"
                f"- القسم: {sess.get('dept')}\n"
                f"- الطبيب: {sess.get('doctor')}\n"
                f"- التاريخ: {sess.get('date')}\n"
                f"- الوقت: {sess.get('slot')}\n"
                f"- الاسم: {sess.get('patient_name')}\n\n"
                "اكتب نعم للتأكيد أو لا للتعديل."
            )
        else:
            out = (
                "Confirm booking ✅\n"
                f"- Dept: {sess.get('dept')}\n"
                f"- Doctor: {sess.get('doctor')}\n"
                f"- Date: {sess.get('date')}\n"
                f"- Time: {sess.get('slot')}\n"
                f"- Name: {sess.get('patient_name')}\n\n"
                "Reply YES to confirm or NO to change."
            )
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Confirm
    if sess.get("state") == STATE_BOOK_CONFIRM:
        if _is_yes(t):
            sess["state"] = STATE_CONFIRMED
            out = "تم ✅ تم إرسال الطلب للاستقبال لتأكيد الموعد. ستصلك رسالة تأكيد قريبًا." if lang=="ar" else "Booked ✅ Sent to receptionist for confirmation. You’ll receive confirmation shortly."
            _set_bot(sess, out)
            # (later: action to create receptionist task/ticket)
            return EngineResult(out, sess, actions)

        if _is_no(t):
            # Don’t close — ask what to change and jump back to dept
            sess["state"] = STATE_BOOK_DEPT
            out = ("تمام 👍 لنعد من البداية. " if lang=="ar" else "Okay 👍 Let’s restart. ") + _dept_prompt(lang)
            _set_bot(sess, out)
            return EngineResult(out, sess, actions)

        out = "اكتب نعم للتأكيد أو لا للتعديل." if lang=="ar" else "Reply YES to confirm or NO to change."
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Fallback
    sess["state"] = STATE_ACTIVE
    out = "هل تريد حجز موعد؟ اكتب: حجز موعد" if lang=="ar" else "Do you want to book an appointment? Type: book appointment"
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