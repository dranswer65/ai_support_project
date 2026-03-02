# core/engine.py
# Production-ready booking engine (V8)
# - Clean run_engine() state machine
# - Arabic/English menus
# - Booking flow with specialty -> doctor -> date -> slot -> details -> confirm
# - Doctor inquiry flow
# - Safe, deterministic outputs (numbers or guided text)
# - Controller handles emergency/intent detection; engine focuses on transactional flow

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Clinic configuration (demo)
# -----------------------------
HOSPITAL_NAME_EN = "Shireen Specialist Hospital"
HOSPITAL_NAME_AR = "مستشفى شيرين التخصصي"

RECEPTION_SHORT = "99"
EMERGENCY_NUMBER = "997"


# -----------------------------
# In-memory demo directory
# (Replace later with DB)
# -----------------------------
@dataclass(frozen=True)
class Doctor:
    id: str
    name_en: str
    name_ar: str


SPECIALTIES: Dict[str, Dict[str, Any]] = {
    # key: dept_key
    "internal": {
        "label_en": "Internal Medicine",
        "label_ar": "الباطنية",
        "menu_en": "General Medicine",  # shown in legacy menu mapping if needed
        "menu_ar": "الطب العام",
        "doctors": [
            Doctor(id="internal_ahmed", name_en="Dr. Ahmed", name_ar="د. أحمد"),
            Doctor(id="internal_sara", name_en="Dr. Sara", name_ar="د. سارة"),
        ],
    },
    "dentistry": {
        "label_en": "Dentistry",
        "label_ar": "طب الأسنان",
        "doctors": [
            Doctor(id="dent_laila", name_en="Dr. Laila", name_ar="د. ليلى"),
        ],
    },
    "urology": {
        "label_en": "Urology",
        "label_ar": "المسالك البولية",
        "doctors": [
            Doctor(id="uro_mohamed", name_en="Dr. Mohamed", name_ar="د. محمد"),
        ],
    },
    "ent": {
        "label_en": "ENT",
        "label_ar": "الأنف والأذن والحنجرة",
        "doctors": [
            Doctor(id="ent_faisal", name_en="Dr. Faisal", name_ar="د. فيصل"),
        ],
    },
    "cardio": {
        "label_en": "Cardiology",
        "label_ar": "أمراض القلب",
        "doctors": [
            Doctor(id="cardio_nasser", name_en="Dr. Nasser", name_ar="د. ناصر"),
        ],
    },
    "obgyn": {
        "label_en": "Obstetrics & Gynecology",
        "label_ar": "أمراض النساء والتوليد",
        "doctors": [
            Doctor(id="obgyn_huda", name_en="Dr. Huda", name_ar="د. هدى"),
        ],
    },
}


# Time slots demo
SLOTS = ["10:00", "10:30", "11:00", "11:30", "17:00", "17:30", "18:00", "18:30"]


# -----------------------------
# Helpers
# -----------------------------
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _norm(text: str) -> str:
    t = (text or "").strip()
    t = t.translate(_AR_DIGITS)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _lang(session: Dict[str, Any], fallback: str = "en") -> str:
    l = (session.get("language") or fallback or "en").strip().lower()
    return "ar" if l.startswith("ar") else "en"


def _is_main_menu_cmd(msg: str) -> bool:
    m = _norm(msg).lower()
    return m in {"0", "menu", "القائمة", "القائمة الرئيسية"}


def _make_ref() -> str:
    # deterministic-ish reference for demo; replace with DB sequence/UUID later
    now = datetime.utcnow().strftime("%y%m%d-%H%M%S")
    return f"SSH-{now}"


def _find_doctor_by_number(doctors: List[Doctor], raw: str) -> Optional[Doctor]:
    if not raw.isdigit():
        return None
    idx = int(raw)
    if idx < 1 or idx > len(doctors):
        return None
    return doctors[idx - 1]


def _parse_date(raw: str) -> Optional[str]:
    """
    Accept:
      2026-03-03
      03-03-2026
      03/03/2026
    Return ISO date: YYYY-MM-DD
    """
    s = _norm(raw)
    if not s:
        return None

    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}"

    # DD-MM-YYYY or DD/MM/YYYY
    m = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"

    return None


def _slot_by_number(raw: str) -> Optional[str]:
    if not raw.isdigit():
        return None
    idx = int(raw)
    if 1 <= idx <= len(SLOTS):
        return SLOTS[idx - 1]
    return None


def _extract_name_mobile(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Very light extraction:
      "Ahmed Ali 0561222212"
      "Ahmed Ali, 0561222212"
    """
    t = _norm(raw)
    if not t:
        return None, None

    # mobile: 9-15 digits
    mob_m = re.search(r"(\d{9,15})", t)
    mobile = mob_m.group(1) if mob_m else None

    name = t
    if mobile:
        name = (t.replace(mobile, "")).strip(" ,.-")
    name = name.strip()
    if len(name) < 2:
        name = None
    return name, mobile


# -----------------------------
# Responses
# -----------------------------
def _welcome(language: str) -> str:
    if language == "ar":
        return (
            f"مرحبًا بكم في *{HOSPITAL_NAME_AR}* 🏥\n"
            "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
            f"📞 الاستقبال: *+966XXXXXXXX*\n"
            f"🚑 الطوارئ: *{EMERGENCY_NUMBER}*\n\n"
            "يرجى اختيار اللغة المفضلة:\n"
            "1️⃣ العربية\n"
            "2️⃣ English\n\n"
            f"للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو {RECEPTION_SHORT}"
        )
    return (
        f"Welcome to *{HOSPITAL_NAME_EN}* 🏥\n"
        "Official WhatsApp virtual assistant.\n\n"
        "📞 Reception: *+966XXXXXXXX*\n"
        f"🚑 Emergency: *{EMERGENCY_NUMBER}*\n\n"
        "Please select your preferred language:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        f"To reach Reception anytime, type *Agent* or {RECEPTION_SHORT}"
    )


def _main_menu(language: str) -> str:
    if language == "ar":
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
            f"{RECEPTION_SHORT}️⃣ موظف الاستقبال"
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
        f"{RECEPTION_SHORT}️⃣ Reception"
    )


def _specialty_menu(language: str) -> str:
    # Keep your old numbering style if you want; now engine supports dept_key too
    if language == "ar":
        return (
            "يرجى اختيار التخصص:\n\n"
            "1️⃣ الباطنية\n"
            "2️⃣ طب الأسنان\n"
            "3️⃣ المسالك البولية\n"
            "4️⃣ الأنف والأذن والحنجرة\n"
            "5️⃣ أمراض القلب\n"
            "6️⃣ أمراض النساء والتوليد\n\n"
            "(يمكنك إرسال رقم أو اسم التخصص)\n\n"
            "0️⃣ القائمة الرئيسية\n"
            f"{RECEPTION_SHORT}️⃣ موظف الاستقبال"
        )
    return (
        "Please select a specialty:\n\n"
        "1️⃣ Internal Medicine\n"
        "2️⃣ Dentistry\n"
        "3️⃣ Urology\n"
        "4️⃣ ENT\n"
        "5️⃣ Cardiology\n"
        "6️⃣ Obstetrics & Gynecology\n\n"
        "(Reply with number or type the specialty)\n\n"
        "0️⃣ Main Menu\n"
        f"{RECEPTION_SHORT}️⃣ Reception"
    )


def _doctors_list(language: str, dept_key: str) -> str:
    spec = SPECIALTIES.get(dept_key) or {}
    doctors: List[Doctor] = list(spec.get("doctors") or [])
    if language == "ar":
        lines = ["الأطباء المتاحون:\n"]
        for i, d in enumerate(doctors, start=1):
            lines.append(f"{i}️⃣ {d.name_ar}")
        lines.append("\n(اكتب رقم الطبيب)\n\n0️⃣ القائمة الرئيسية\n" + f"{RECEPTION_SHORT}️⃣ موظف الاستقبال")
        return "\n".join(lines)

    lines = ["Available doctors:\n"]
    for i, d in enumerate(doctors, start=1):
        lines.append(f"{i}️⃣ {d.name_en}")
    lines.append("\n(Reply with doctor number)\n\n0️⃣ Main Menu\n" + f"{RECEPTION_SHORT}️⃣ Reception")
    return "\n".join(lines)


def _ask_date(language: str) -> str:
    if language == "ar":
        return (
            "يرجى كتابة تاريخ الموعد (مثال: 2026-03-03 أو 03-03-2026 أو 03/03/2026)\n\n"
            "0️⃣ القائمة الرئيسية\n"
            f"{RECEPTION_SHORT}️⃣ موظف الاستقبال"
        )
    return (
        "Please enter the appointment date (example: 2026-03-03 or 03-03-2026 or 03/03/2026)\n\n"
        "0️⃣ Main Menu\n"
        f"{RECEPTION_SHORT}️⃣ Reception"
    )


def _slots(language: str, iso_date: str) -> str:
    if language == "ar":
        lines = [f"الأوقات المتاحة بتاريخ {iso_date}:\n"]
        for i, s in enumerate(SLOTS, start=1):
            lines.append(f"{i}️⃣ {s}")
        lines.append("\n(اكتب رقم الوقت)\n\n0️⃣ القائمة الرئيسية\n" + f"{RECEPTION_SHORT}️⃣ موظف الاستقبال")
        return "\n".join(lines)

    lines = [f"Available time slots on {iso_date}:\n"]
    for i, s in enumerate(SLOTS, start=1):
        lines.append(f"{i}️⃣ {s}")
    lines.append("\n(Reply with slot number)\n\n0️⃣ Main Menu\n" + f"{RECEPTION_SHORT}️⃣ Reception")
    return "\n".join(lines)


def _ask_details(language: str) -> str:
    if language == "ar":
        return (
            "لإكمال الحجز، يرجى إرسال (ويُفضل في رسالة واحدة):\n"
            "• الاسم الكامل\n"
            "• رقم الجوال\n"
            "• رقم الهوية/الإقامة (اختياري)\n\n"
            "تنبيه: هذه الخدمة ليست للطوارئ الطبية.\n\n"
            "0️⃣ القائمة الرئيسية\n"
            f"{RECEPTION_SHORT}️⃣ موظف الاستقبال"
        )
    return (
        "To complete the booking, please send (preferably in one message):\n"
        "• Full Name\n"
        "• Mobile Number\n"
        "• National ID/Iqama (optional)\n\n"
        "Note: This service is not for medical emergencies.\n\n"
        "0️⃣ Main Menu\n"
        f"{RECEPTION_SHORT}️⃣ Reception"
    )


def _confirm(language: str, session: Dict[str, Any]) -> str:
    ref = session.get("ref") or ""
    name = session.get("patient_name") or ""
    mobile = session.get("patient_mobile") or ""
    dept_key = session.get("dept_key") or ""
    doctor_id = session.get("doctor_id") or ""
    date = session.get("date") or ""
    time = session.get("time") or ""

    spec = SPECIALTIES.get(dept_key) or {}
    dept_label = spec.get("label_ar") if language == "ar" else spec.get("label_en")

    doc_name = doctor_id
    for d in (spec.get("doctors") or []):
        if isinstance(d, Doctor) and d.id == doctor_id:
            doc_name = d.name_ar if language == "ar" else d.name_en
            break

    if language == "ar":
        return (
            "ملخص طلب الحجز ✅\n\n"
            f"📌 المرجع: *{ref}*\n"
            f"👤 الاسم: {name}\n"
            f"📱 الجوال: {mobile}\n"
            f"👨‍⚕️ الطبيب: {doc_name}\n"
            f"🏥 التخصص: {dept_label}\n"
            f"📅 التاريخ: {date}\n"
            f"⏰ الوقت: {time}\n\n"
            "يرجى الرد:\n"
            "1️⃣ إرسال الطلب لموظف الاستقبال\n"
            "2️⃣ تعديل\n"
            "3️⃣ إلغاء\n\n"
            "0️⃣ القائمة الرئيسية\n"
            f"{RECEPTION_SHORT}️⃣ موظف الاستقبال"
        )

    return (
        "Booking Request Summary ✅\n\n"
        f"📌 Reference: *{ref}*\n"
        f"👤 Name: {name}\n"
        f"📱 Mobile: {mobile}\n"
        f"👨‍⚕️ Doctor: {doc_name}\n"
        f"🏥 Specialty: {dept_label}\n"
        f"📅 Date: {date}\n"
        f"⏰ Time: {time}\n\n"
        "Please reply:\n"
        "1️⃣ Send request to Reception\n"
        "2️⃣ Modify\n"
        "3️⃣ Cancel\n\n"
        "0️⃣ Main Menu\n"
        f"{RECEPTION_SHORT}️⃣ Reception"
    )


def _received(language: str, ref: str) -> str:
    if language == "ar":
        return (
            "تم استلام طلب الحجز ✅\n"
            f"📌 المرجع: *{ref}*\n"
            "سيقوم موظف الاستقبال بتأكيد الموعد خلال ساعات العمل.\n"
            "يرجى الحضور قبل الموعد بـ 15 دقيقة.\n\n"
            "ساعات العمل: يوميًا من 9 صباحًا إلى 9 مساءً (عدا الجمعة).\n"
            f"🚑 الطوارئ: {EMERGENCY_NUMBER}\n"
            f"الاستقبال: {RECEPTION_SHORT}\n\n"
            "للعودة للقائمة اكتب 0"
        )
    return (
        "Booking request received ✅\n"
        f"📌 Reference: *{ref}*\n"
        "Reception will confirm your appointment during working hours.\n"
        "Please arrive 15 minutes early.\n\n"
        "Hospital hours: daily 9:00 AM to 9:00 PM (except Friday).\n"
        f"🚑 Emergency: {EMERGENCY_NUMBER}\n"
        f"Reception: {RECEPTION_SHORT}\n\n"
        "Reply 0 for the main menu."
    )


# -----------------------------
# Engine core
# -----------------------------
def run_engine(
    *,
    session: Dict[str, Any],
    user_message: str,
    language: str,
    arabic_tone: Optional[str] = None,  # kept for compatibility; not used here
    kpi_signals: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Deterministic transactional engine.
    The controller should:
      - handle emergency
      - handle high-level intent detection and possibly set state/dept_key
      - enforce timeouts/handoff
    """
    msg = _norm(user_message)
    lang = "ar" if (language or "").startswith("ar") else "en"

    # Ensure required session fields
    session.setdefault("status", "ACTIVE")
    session.setdefault("state", "LANG_SELECT")
    session.setdefault("last_step", session.get("state"))
    session["language"] = lang

    # Global main menu command
    if _is_main_menu_cmd(msg):
        session["state"] = "MAIN_MENU"
        session["last_step"] = "MAIN_MENU"
        return {"reply_text": _main_menu(lang), "session": session, "actions": []}

    state = (session.get("state") or "LANG_SELECT").strip().upper()

    # -------------------------
    # Language selection (optional)
    # -------------------------
    if state == "LANG_SELECT":
        # If user explicitly chooses
        if msg == "1":
            session["language"] = "ar"
            session["language_locked"] = True
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
            return {"reply_text": _main_menu("ar"), "session": session, "actions": []}
        if msg == "2":
            session["language"] = "en"
            session["language_locked"] = True
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
            return {"reply_text": _main_menu("en"), "session": session, "actions": []}

        # Otherwise show welcome (controller should auto-lock language on Arabic text)
        return {"reply_text": _welcome(lang), "session": session, "actions": []}

    # -------------------------
    # Main menu
    # -------------------------
    if state == "MAIN_MENU":
        if msg == "1":
            session["state"] = "BOOK_SPECIALTY"
            session["last_step"] = "BOOK_SPECIALTY"
            return {"reply_text": _specialty_menu(lang), "session": session, "actions": []}

        if msg == "4":
            session["state"] = "FIND_SPECIALTY"
            session["last_step"] = "FIND_SPECIALTY"
            return {"reply_text": _specialty_menu(lang), "session": session, "actions": []}

        # Keep other menu items simple for now
        if lang == "ar":
            return {"reply_text": _main_menu("ar"), "session": session, "actions": []}
        return {"reply_text": _main_menu("en"), "session": session, "actions": []}

    # -------------------------
    # Specialty selection (booking)
    # -------------------------
    if state in {"BOOK_SPECIALTY", "FIND_SPECIALTY"}:
        # dept_key might be pre-set by controller (free-text intent)
        dept_key = session.get("dept_key")
        if not dept_key:
            dept_key = _dept_key_from_specialty_input(msg)

        if not dept_key or dept_key not in SPECIALTIES:
            # reprompt
            return {"reply_text": _specialty_menu(lang), "session": session, "actions": []}

        session["dept_key"] = dept_key
        session["state"] = "BOOK_DOCTOR" if state == "BOOK_SPECIALTY" else "FIND_DOCTOR"
        session["last_step"] = session["state"]
        return {"reply_text": _doctors_list(lang, dept_key), "session": session, "actions": []}

    # -------------------------
    # Doctor list
    # -------------------------
    if state in {"BOOK_DOCTOR", "FIND_DOCTOR"}:
        dept_key = session.get("dept_key")
        if not dept_key or dept_key not in SPECIALTIES:
            session["state"] = "BOOK_SPECIALTY"
            session["last_step"] = "BOOK_SPECIALTY"
            return {"reply_text": _specialty_menu(lang), "session": session, "actions": []}

        doctors: List[Doctor] = list(SPECIALTIES[dept_key]["doctors"])
        doc = _find_doctor_by_number(doctors, msg)
        if not doc:
            return {"reply_text": _doctors_list(lang, dept_key), "session": session, "actions": []}

        session["doctor_id"] = doc.id

        # If doctor inquiry only, stop here
        if state == "FIND_DOCTOR":
            if lang == "ar":
                return {
                    "reply_text": f"نعم، متوفر {doc.name_ar} ✅\n\nهل ترغب في حجز موعد؟ اكتب 1 للحجز أو 0 للقائمة.",
                    "session": session,
                    "actions": [],
                }
            return {
                "reply_text": f"Yes, {doc.name_en} is available ✅\n\nDo you want to book an appointment? Reply 1 to book or 0 for the menu.",
                "session": session,
                "actions": [],
            }

        # booking continues
        session["state"] = "BOOK_DATE"
        session["last_step"] = "BOOK_DATE"
        return {"reply_text": _ask_date(lang), "session": session, "actions": []}

    # -------------------------
    # Date
    # -------------------------
    if state == "BOOK_DATE":
        iso = _parse_date(msg)
        if not iso:
            return {"reply_text": _ask_date(lang), "session": session, "actions": []}

        session["date"] = iso
        session["state"] = "BOOK_SLOT"
        session["last_step"] = "BOOK_SLOT"
        return {"reply_text": _slots(lang, iso), "session": session, "actions": []}

    # -------------------------
    # Slot
    # -------------------------
    if state == "BOOK_SLOT":
        slot = _slot_by_number(msg)
        if not slot:
            iso = session.get("date") or ""
            return {"reply_text": _slots(lang, iso), "session": session, "actions": []}

        session["time"] = slot
        session["state"] = "BOOK_DETAILS"
        session["last_step"] = "BOOK_DETAILS"
        return {"reply_text": _ask_details(lang), "session": session, "actions": []}

    # -------------------------
    # Details
    # -------------------------
    if state == "BOOK_DETAILS":
        name, mobile = _extract_name_mobile(msg)
        if not name or not mobile:
            return {"reply_text": _ask_details(lang), "session": session, "actions": []}

        session["patient_name"] = name
        session["patient_mobile"] = mobile

        session["ref"] = session.get("ref") or _make_ref()
        session["state"] = "BOOK_CONFIRM"
        session["last_step"] = "BOOK_CONFIRM"
        return {"reply_text": _confirm(lang, session), "session": session, "actions": []}

    # -------------------------
    # Confirm
    # -------------------------
    if state == "BOOK_CONFIRM":
        if msg == "1":
            ref = session.get("ref") or _make_ref()
            session["ref"] = ref
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"

            # Here you can emit an action to your reception/handoff pipeline if you want:
            actions = [
                {
                    "type": "CREATE_APPOINTMENT_REQUEST",
                    "intent": "BOOK",
                    "reference": ref,
                    "payload": {
                        "dept_key": session.get("dept_key"),
                        "doctor_id": session.get("doctor_id"),
                        "date": session.get("date"),
                        "time": session.get("time"),
                        "name": session.get("patient_name"),
                        "mobile": session.get("patient_mobile"),
                    },
                }
            ]
            return {"reply_text": _received(lang, ref), "session": session, "actions": actions}

        if msg == "2":
            # Modify = restart booking at specialty
            session.pop("doctor_id", None)
            session.pop("date", None)
            session.pop("time", None)
            session.pop("patient_name", None)
            session.pop("patient_mobile", None)
            session["state"] = "BOOK_SPECIALTY"
            session["last_step"] = "BOOK_SPECIALTY"
            return {"reply_text": _specialty_menu(lang), "session": session, "actions": []}

        if msg == "3":
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
            if lang == "ar":
                return {"reply_text": "تم إلغاء الطلب ✅\n\n0️⃣ للقائمة الرئيسية", "session": session, "actions": []}
            return {"reply_text": "Request cancelled ✅\n\nReply 0 for the main menu.", "session": session, "actions": []}

        return {"reply_text": _confirm(lang, session), "session": session, "actions": []}

    # Fallback
    session["state"] = "MAIN_MENU"
    session["last_step"] = "MAIN_MENU"
    return {"reply_text": _main_menu(lang), "session": session, "actions": []}


def _dept_key_from_specialty_input(msg: str) -> Optional[str]:
    t = _norm(msg).lower()

    # numbers
    if t == "1":
        return "internal"
    if t == "2":
        return "dentistry"
    if t == "3":
        return "urology"
    if t == "4":
        return "ent"
    if t == "5":
        return "cardio"
    if t == "6":
        return "obgyn"

    # english keywords
    if "internal" in t or "medicine" in t:
        return "internal"
    if "dent" in t:
        return "dentistry"
    if "urolog" in t:
        return "urology"
    if t == "ent" or "ear" in t or "throat" in t or "sinus" in t:
        return "ent"
    if "cardio" in t or "heart" in t:
        return "cardio"
    if "ob" in t or "gyn" in t or "pregnan" in t:
        return "obgyn"

    # arabic keywords
    raw = msg
    if "باطن" in raw or "الباطنيه" in raw or "الباطنية" in raw:
        return "internal"
    if "اسنان" in raw or "أسنان" in raw or "سنان" in raw:
        return "dentistry"
    if "مسالك" in raw or "بولية" in raw or "بوليه" in raw:
        return "urology"
    if "أنف" in raw or "اذن" in raw or "أذن" in raw or "حنجرة" in raw:
        return "ent"
    if "قلب" in raw or "خفقان" in raw:
        return "cardio"
    if "نساء" in raw or "ولادة" in raw or "توليد" in raw:
        return "obgyn"

    return None