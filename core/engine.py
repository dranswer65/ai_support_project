# core/engine.py — Enterprise Clinic Booking Engine V4
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone, date

# ----------------------------
# States
# ----------------------------
STATE_LANG = "LANG_SELECT"
STATE_MENU = "MAIN_MENU"

STATE_BOOK_SPECIALTY = "BOOK_SPECIALTY"
STATE_BOOK_DOCTOR = "BOOK_DOCTOR"
STATE_BOOK_DATE = "BOOK_DATE"
STATE_BOOK_SLOT = "BOOK_SLOT"
STATE_BOOK_PATIENT = "BOOK_PATIENT"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"

STATE_RES_LOOKUP = "RESCHEDULE_LOOKUP"
STATE_CAN_LOOKUP = "CANCEL_LOOKUP"

STATE_ESCALATION = "ESCALATION"
STATE_CLOSED = "CLOSED"

# ----------------------------
# Status (3-level model)
# ----------------------------
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ABANDONED = "ABANDONED"

ENGINE_MARKER = "CLINIC_ENGINE_V4"

# ----------------------------
# Session timing
# ----------------------------
SESSION_EXPIRE_SECONDS = 60 * 60  # 60 min inactivity -> reset to language select (only if session existed before)

# ----------------------------
# Demo catalog
# ----------------------------
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
_ARABIC_DIGITS_1 = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ARABIC_DIGITS_2 = str.maketrans("٠١٢٣٤٥٦٧٨٩١٢٣٤٥٦٧٨٩", "01234567890123456789")

def _normalize_digits(s: str) -> str:
    return (s or "").translate(_ARABIC_DIGITS_1).translate(_ARABIC_DIGITS_2)

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

def _low(t: str) -> str:
    return _normalize_digits((t or "").strip().lower())

def _lang(lang: str) -> str:
    l = (lang or "").strip().lower()
    return "ar" if l.startswith("ar") else "en"

def _is_thanks(text: str) -> bool:
    t = _low(text)
    return t in {"thanks", "thank you", "thx", "شكرا", "شكراً", "شكرًا", "مشكور", "الله يعطيك العافية"}

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
        "99️⃣ Reception"
    )

def _welcome(lang: str) -> str:
    # Requested: add “Please select language” under Arabic line.
    if lang == "ar":
        return (
            f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
            "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
            f"📞 الاستقبال: *{RECEPTION_PHONE}*\n"
            f"🚑 الطوارئ: *{EMERGENCY_NUMBER}*\n\n"
            "يرجى اختيار اللغة المفضلة:\n"
            "Please select language:\n"
            "1️⃣ العربية\n"
            "2️⃣ English\n\n"
            "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
        )
    return (
        f"Welcome to *{CLINIC_NAME_EN}* 🏥\n"
        "Official WhatsApp Virtual Assistant.\n\n"
        f"📞 Reception: *{RECEPTION_PHONE}*\n"
        f"🚑 Emergencies: *{EMERGENCY_NUMBER}*\n\n"
        "Please select your preferred language:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "To speak with Reception anytime, type *Agent* or 99"
    )

def _dept_prompt(lang: str) -> str:
    if lang == "ar":
        lines = [f"{i}️⃣ {d['ar']}" for i, d in enumerate(DEPTS, start=1)]
        return "يرجى اختيار التخصص:\n\n" + "\n".join(lines) + "\n\n(يمكنك إرسال رقم أو اسم التخصص)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    lines = [f"{i}️⃣ {d['en']}" for i, d in enumerate(DEPTS, start=1)]
    return "Please select a specialty:\n\n" + "\n".join(lines) + "\n\n(Reply with number or specialty name)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _doctor_prompt(lang: str, dept_key: str) -> str:
    docs = DOCTORS_BY_DEPT_KEY.get(dept_key, [])
    if lang == "ar":
        lines = [f"{i}️⃣ {d['ar']}" for i, d in enumerate(docs, start=1)]
        return "الأطباء المتاحون:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الطبيب)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    lines = [f"{i}️⃣ {d['en']}" for i, d in enumerate(docs, start=1)]
    return "Available doctors:\n\n" + "\n".join(lines) + "\n\n(Reply with doctor number)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _date_prompt(lang: str) -> str:
    if lang == "ar":
        return "يرجى كتابة تاريخ الموعد (مثال: 2026-02-28 أو 28-02-2026)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Please enter the date (example: 2026-02-28 or 28-02-2026)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _slot_prompt(lang: str, date_str: str) -> str:
    if lang == "ar":
        lines = [f"{i}️⃣ {s}" for i, s in enumerate(SLOTS, start=1)]
        return f"المواعيد المتاحة بتاريخ {date_str} هي:\n\n" + "\n".join(lines) + "\n\n(اكتب رقم الوقت)\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    lines = [f"{i}️⃣ {s}" for i, s in enumerate(SLOTS, start=1)]
    return f"Available slots on {date_str}:\n\n" + "\n".join(lines) + "\n\n(Reply with slot number)\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _patient_prompt(lang: str) -> str:
    if lang == "ar":
        return (
            "لإتمام الحجز، يرجى إرسال (يفضل برسالة واحدة):\n"
            "• الاسم الكامل\n"
            "• رقم الجوال\n"
            "• رقم الهوية/الإقامة (اختياري)\n\n"
            "0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
        )
    return (
        "To proceed, please send (preferably in one message):\n"
        "• Full name\n"
        "• Mobile number\n"
        "• ID/Iqama (optional)\n\n"
        "0️⃣ Main Menu\n99️⃣ Reception"
    )

def _contact_info(lang: str) -> str:
    if lang == "ar":
        return f"معلومات التواصل:\n📞 الاستقبال: {RECEPTION_PHONE}\n🚑 الطوارئ: {EMERGENCY_NUMBER}\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return f"Contact Info:\n📞 Reception: {RECEPTION_PHONE}\n🚑 Emergencies: {EMERGENCY_NUMBER}\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _timings(lang: str) -> str:
    if lang == "ar":
        return "مواعيد العمل: يوميًا 9:00 صباحًا إلى 9:00 مساءً (عدا الجمعة).\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Hospital hours: daily 9:00 AM to 9:00 PM (except Friday).\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _insurance(lang: str) -> str:
    if lang == "ar":
        return "التأمينات المعتمدة (مثال): بوبا، التعاونية، ميدغلف.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Accepted insurance (example): Bupa, Tawuniya, Medgulf.\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _location(lang: str) -> str:
    # Demo-friendly (simple, not complex): keep as text for now
    if lang == "ar":
        return "الموقع والاتجاهات:\nسيتم إضافة رابط خرائط Google لاحقًا ضمن نسخة التكامل.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
    return "Locations & Directions:\nA Google Maps link will be added in the integration phase.\n\n0️⃣ Main Menu\n99️⃣ Reception"

def _extract_name_mobile_id(raw: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw0 = (raw or "").strip()
    if not raw0:
        return None, None, None
    rawN = _normalize_digits(raw0)

    lines = [ln.strip() for ln in rawN.splitlines() if ln.strip()]
    name = lines[0] if lines else rawN

    # find sequences 8+ digits
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

    return name, mobile, pid

def _parse_date_flexible(raw: str) -> Optional[str]:
    """
    Accept:
    - YYYY-MM-DD
    - DD-MM-YYYY
    Normalize to YYYY-MM-DD
    """
    t = _normalize_digits(_norm(raw))
    if len(t) < 8:
        return None

    # YYYY-MM-DD
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        y, m, d = t[:4], t[5:7], t[8:10]
    # DD-MM-YYYY
    elif len(t) >= 10 and t[2] == "-" and t[5] == "-":
        d, m, y = t[:2], t[3:5], t[6:10]
    else:
        return None

    try:
        yy = int(y); mm = int(m); dd = int(d)
        _ = date(yy, mm, dd)
        return f"{yy:04d}-{mm:02d}-{dd:02d}"
    except Exception:
        return None

def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "engine": ENGINE_MARKER,
        "user_id": user_id,
        "status": STATUS_ACTIVE,
        "state": STATE_LANG,
        "last_step": STATE_LANG,
        "language": "ar",
        "language_locked": False,
        "has_greeted": False,
        "mistakes": 0,
        "last_user_ts": _utcnow_iso(),
        "dept_key": None,
        "dept_label": None,
        "doctor_key": None,
        "doctor_label": None,
        "date": None,
        "slot": None,
        "patient_name": None,
        "patient_mobile": None,
        "patient_id": None,
        "last_closed_at": None,
    }

def _seconds_since_last_user(sess: Dict[str, Any]) -> Optional[float]:
    last = _parse_iso(sess.get("last_user_ts"))
    if not last:
        return None
    return (datetime.now(timezone.utc) - last).total_seconds()

def _session_expired(sess: Dict[str, Any]) -> bool:
    sec = _seconds_since_last_user(sess)
    if sec is None:
        return False
    return sec >= SESSION_EXPIRE_SECONDS

# ----------------------------
# Main handler
# ----------------------------
def handle_turn(user_id: str, message_text: str, language: str, session_in: Optional[Dict[str, Any]] = None) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id

    lang = _lang(language or sess.get("language") or "ar")
    sess["language"] = lang

    raw = _norm(message_text)
    low = _low(message_text)

    # Update timestamp
    prev_last = sess.get("last_user_ts")
    sess["last_user_ts"] = _utcnow_iso()

    # Session expiry only if we had a previous timestamp (i.e., not first-ever message)
    if prev_last and _session_expired(sess) and sess.get("state") not in {STATE_ESCALATION}:
        # Reset to language selection (gentle)
        sess["state"] = STATE_LANG
        sess["last_step"] = STATE_LANG
        sess["status"] = STATUS_ACTIVE
        sess["language_locked"] = False
        sess["has_greeted"] = False
        sess["mistakes"] = 0
        out = _welcome("ar")  # show bilingual welcome by default
        return EngineResult(out, sess, [])

    # Global quick commands
    if low == "0":
        sess["state"] = STATE_MENU if sess.get("language_locked") else STATE_LANG
        sess["last_step"] = sess["state"]
        out = _main_menu(lang) if sess["state"] == STATE_MENU else _welcome(lang)
        return EngineResult(out, sess, [])

    if low == "99":
        sess["state"] = STATE_ESCALATION
        sess["last_step"] = STATE_ESCALATION
        return EngineResult(("تم تحويلكم إلى موظف الاستقبال ✅" if lang == "ar" else "Connecting you to Reception ✅"), sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

    # Politeness intent (never error)
    if _is_thanks(raw):
        out = ("العفو 😊\nإذا احتجت أي شيء آخر اكتب 0 لعرض القائمة." if lang == "ar"
               else "You’re welcome 😊\nIf you need anything else, reply 0 for the menu.")
        return EngineResult(out, sess, [])

    # ----------------------------
    # Language selection gate (must happen before menu)
    # ----------------------------
    if sess.get("state") == STATE_LANG:
        # Always show welcome unless user selects 1/2
        if low == "1":
            sess["language"] = "ar"
            sess["language_locked"] = True
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu("ar")
            return EngineResult(out, sess, [])

        if low == "2":
            sess["language"] = "en"
            sess["language_locked"] = True
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu("en")
            return EngineResult(out, sess, [])

        # Show bilingual welcome (Arabic default + English line requested)
        out = _welcome("ar")  # Arabic message includes "Please select language"
        return EngineResult(out, sess, [])

    # ----------------------------
    # MAIN MENU routing
    # ----------------------------
    if sess.get("state") == STATE_MENU:
        # numeric selection
        if low.isdigit():
            c = int(low)

            if c == 1:
                sess["state"] = STATE_BOOK_SPECIALTY
                sess["last_step"] = STATE_BOOK_SPECIALTY
                return EngineResult(_dept_prompt(lang), sess, [])

            if c == 2:
                sess["state"] = STATE_RES_LOOKUP
                sess["last_step"] = STATE_RES_LOOKUP
                msg = ("يرجى إدخال رقم الموعد أو رقم الجوال لإعادة الجدولة.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                       if lang == "ar" else
                       "Please enter appointment reference or registered mobile to reschedule.\n\n0️⃣ Main Menu\n99️⃣ Reception")
                return EngineResult(msg, sess, [])

            if c == 3:
                sess["state"] = STATE_CAN_LOOKUP
                sess["last_step"] = STATE_CAN_LOOKUP
                msg = ("يرجى إدخال رقم الموعد أو رقم الجوال لإلغاء الموعد.\n\n0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
                       if lang == "ar" else
                       "Please enter appointment reference or registered mobile to cancel.\n\n0️⃣ Main Menu\n99️⃣ Reception")
                return EngineResult(msg, sess, [])

            if c == 4:
                # reuse specialty->doctor browsing
                sess["state"] = STATE_BOOK_SPECIALTY
                sess["last_step"] = STATE_BOOK_SPECIALTY
                return EngineResult(_dept_prompt(lang), sess, [])

            if c == 5:
                return EngineResult(_timings(lang), sess, [])

            if c == 6:
                return EngineResult(_insurance(lang), sess, [])

            if c == 7:
                return EngineResult(_location(lang), sess, [])

            if c == 8:
                return EngineResult(_contact_info(lang), sess, [])

            if c == 99:
                sess["state"] = STATE_ESCALATION
                sess["last_step"] = STATE_ESCALATION
                return EngineResult(("تم تحويلكم إلى موظف الاستقبال ✅" if lang == "ar" else "Connecting you to Reception ✅"),
                                    sess, [{"type": "ESCALATE", "reason": "user_requested_reception"}])

            return EngineResult(_main_menu(lang), sess, [])

        # fallback: show menu
        return EngineResult(_main_menu(lang), sess, [])

    # ----------------------------
    # Booking flow
    # ----------------------------
    if sess.get("state") == STATE_BOOK_SPECIALTY:
        dept_key = None
        dept_label = None

        if low.isdigit():
            idx = int(low) - 1
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
            msg = ("يرجى اختيار تخصص صحيح.\n\n" if lang == "ar" else "Please choose a valid specialty.\n\n")
            return EngineResult(msg + _dept_prompt(lang), sess, [])

        sess["dept_key"] = dept_key
        sess["dept_label"] = dept_label
        sess["state"] = STATE_BOOK_DOCTOR
        sess["last_step"] = STATE_BOOK_DOCTOR
        return EngineResult(_doctor_prompt(lang, dept_key), sess, [])

    if sess.get("state") == STATE_BOOK_DOCTOR:
        docs = DOCTORS_BY_DEPT_KEY.get(sess.get("dept_key") or "", [])
        chosen = None

        if low.isdigit():
            idx = int(low) - 1
            if 0 <= idx < len(docs):
                chosen = docs[idx]
        else:
            for d in docs:
                if _low(d["ar"]) in low or _low(d["en"]) in low:
                    chosen = d
                    break

        if not chosen:
            msg = ("يرجى اختيار طبيب صحيح.\n\n" if lang == "ar" else "Please choose a valid doctor.\n\n")
            return EngineResult(msg + _doctor_prompt(lang, sess.get("dept_key") or ""), sess, [])

        sess["doctor_key"] = chosen.get("key")
        sess["doctor_label"] = chosen["ar"] if lang == "ar" else chosen["en"]
        sess["state"] = STATE_BOOK_DATE
        sess["last_step"] = STATE_BOOK_DATE
        return EngineResult(_date_prompt(lang), sess, [])

    if sess.get("state") == STATE_BOOK_DATE:
        norm_date = _parse_date_flexible(raw)
        if not norm_date:
            msg = ("صيغة التاريخ غير صحيحة. مثال: 2026-02-28 أو 28-02-2026\n\n" if lang == "ar"
                   else "Invalid date format. Example: 2026-02-28 or 28-02-2026\n\n")
            return EngineResult(msg + _date_prompt(lang), sess, [])

        # Reject past dates (server date 기준)
        try:
            y, m, d = int(norm_date[:4]), int(norm_date[5:7]), int(norm_date[8:10])
            picked = date(y, m, d)
            if picked < datetime.now(timezone.utc).date():
                msg = ("لا يمكن اختيار تاريخ سابق. يرجى اختيار تاريخ قادم.\n\n" if lang == "ar"
                       else "Past dates are not allowed. Please choose a future date.\n\n")
                return EngineResult(msg + _date_prompt(lang), sess, [])
        except Exception:
            return EngineResult(_date_prompt(lang), sess, [])

        sess["date"] = norm_date
        sess["state"] = STATE_BOOK_SLOT
        sess["last_step"] = STATE_BOOK_SLOT
        return EngineResult(_slot_prompt(lang, norm_date), sess, [])

    if sess.get("state") == STATE_BOOK_SLOT:
        if not low.isdigit():
            msg = ("يرجى اختيار رقم الوقت.\n\n" if lang == "ar" else "Please choose a slot number.\n\n")
            return EngineResult(msg + _slot_prompt(lang, sess.get("date") or ""), sess, [])

        idx = int(low) - 1
        if not (0 <= idx < len(SLOTS)):
            msg = ("يرجى اختيار رقم وقت صحيح.\n\n" if lang == "ar" else "Please choose a valid slot number.\n\n")
            return EngineResult(msg + _slot_prompt(lang, sess.get("date") or ""), sess, [])

        sess["slot"] = SLOTS[idx]
        sess["state"] = STATE_BOOK_PATIENT
        sess["last_step"] = STATE_BOOK_PATIENT
        return EngineResult(_patient_prompt(lang), sess, [])

    if sess.get("state") == STATE_BOOK_PATIENT:
        name, mobile, pid = _extract_name_mobile_id(raw)
        sess["patient_name"] = name
        sess["patient_mobile"] = mobile
        sess["patient_id"] = pid

        if not name or not mobile or len("".join(c for c in mobile if c.isdigit())) < 9:
            msg = ("يرجى إرسال الاسم ورقم جوال صحيح (9 أرقام على الأقل).\n\n" if lang == "ar"
                   else "Please send a valid name and mobile (at least 9 digits).\n\n")
            return EngineResult(msg + _patient_prompt(lang), sess, [])

        sess["state"] = STATE_BOOK_CONFIRM
        sess["last_step"] = STATE_BOOK_CONFIRM

        if lang == "ar":
            conf = (
                "ملخص الموعد:\n\n"
                f"👤 الاسم: {sess.get('patient_name')}\n"
                f"📱 الجوال: {sess.get('patient_mobile')}\n"
                f"🏥 التخصص: {sess.get('dept_label')}\n"
                f"👨‍⚕️ الطبيب: {sess.get('doctor_label')}\n"
                f"📅 التاريخ: {sess.get('date')}\n"
                f"⏰ الوقت: {sess.get('slot')}\n\n"
                "يرجى الرد:\n"
                "1️⃣ تأكيد الموعد\n"
                "2️⃣ تعديل\n"
                "3️⃣ إلغاء\n\n"
                "0️⃣ القائمة الرئيسية\n99️⃣ موظف الاستقبال"
            )
        else:
            conf = (
                "Appointment Summary:\n\n"
                f"👤 Name: {sess.get('patient_name')}\n"
                f"📱 Mobile: {sess.get('patient_mobile')}\n"
                f"🏥 Specialty: {sess.get('dept_label')}\n"
                f"👨‍⚕️ Doctor: {sess.get('doctor_label')}\n"
                f"📅 Date: {sess.get('date')}\n"
                f"⏰ Time: {sess.get('slot')}\n\n"
                "Please reply:\n"
                "1️⃣ Confirm\n"
                "2️⃣ Modify\n"
                "3️⃣ Cancel\n\n"
                "0️⃣ Main Menu\n99️⃣ Reception"
            )
        return EngineResult(conf, sess, [])

    if sess.get("state") == STATE_BOOK_CONFIRM:
        if not low.isdigit():
            msg = ("يرجى اختيار 1 أو 2 أو 3.\n\n" if lang == "ar" else "Please choose 1, 2, or 3.\n\n")
            return EngineResult(msg, sess, [])

        c = int(low)
        if c == 1:
            # Create receptionist request
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
                    "notes": "",
                }
            }]
            sess["state"] = STATE_CLOSED
            sess["last_step"] = STATE_CLOSED
            sess["status"] = STATUS_COMPLETED
            sess["last_closed_at"] = _utcnow_iso()

            out = ("تم استلام طلبكم ✅ سيتم تأكيد الموعد من الاستقبال قريبًا.\nللعودة للقائمة اكتب 0"
                   if lang == "ar" else
                   "Request received ✅ Reception will confirm shortly.\nReply 0 for the menu")
            return EngineResult(out, sess, actions)

        if c == 2:
            sess["state"] = STATE_BOOK_SPECIALTY
            sess["last_step"] = STATE_BOOK_SPECIALTY
            return EngineResult(_dept_prompt(lang), sess, [])

        if c == 3:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            return EngineResult(_main_menu(lang), sess, [])

        return EngineResult(_main_menu(lang), sess, [])

    # ----------------------------
    # Reschedule / Cancel (minimal demo)
    # ----------------------------
    if sess.get("state") == STATE_RES_LOOKUP:
        # For demo: route to reception via request
        sess["state"] = STATE_CLOSED
        sess["last_step"] = STATE_CLOSED
        actions = [{
            "type": "CREATE_APPOINTMENT_REQUEST",
            "payload": {"intent": "RESCHEDULE", "status": "PENDING", "notes": f"lookup={raw[:80]}"},
        }]
        out = ("تم استلام طلب التعديل ✅ سيتم التواصل معكم قريبًا.\nللعودة للقائمة اكتب 0"
               if lang == "ar" else
               "Reschedule request received ✅ We will contact you shortly.\nReply 0 for the menu")
        return EngineResult(out, sess, actions)

    if sess.get("state") == STATE_CAN_LOOKUP:
        sess["state"] = STATE_CLOSED
        sess["last_step"] = STATE_CLOSED
        actions = [{
            "type": "CREATE_APPOINTMENT_REQUEST",
            "payload": {"intent": "CANCEL", "status": "PENDING", "notes": f"lookup={raw[:80]}"},
        }]
        out = ("تم استلام طلب الإلغاء ✅ سيتم تأكيد الإلغاء قريبًا.\nللعودة للقائمة اكتب 0"
               if lang == "ar" else
               "Cancellation request received ✅ We will confirm shortly.\nReply 0 for the menu")
        return EngineResult(out, sess, actions)

    # Fallback
    sess["state"] = STATE_MENU
    sess["last_step"] = STATE_MENU
    return EngineResult(_main_menu(lang), sess, [])

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