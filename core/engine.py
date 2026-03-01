# core/engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List
import re

# =====================================================
# STATES
# =====================================================

STATE_MAIN_MENU = "MAIN_MENU"
STATE_BOOK_OPTIONS = "BOOK_OPTIONS"

# =====================================================
# RESULT OBJECT
# =====================================================

@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]

# =====================================================
# LANGUAGE DETECTION
# =====================================================

_AR_RE = re.compile(r"[\u0600-\u06FF]")

def detect_language(text: str) -> str:
    if _AR_RE.search(text):
        return "ar"
    return "en"

# =====================================================
# KEYWORDS
# =====================================================

BOOKING_WORDS = [
    "book","booking","appointment","موعد","احجز",
    "حجز","ابغى","أبي","عايز","اريد"
]

THANK_WORDS = [
    "thanks","thank you","thx",
    "شكرا","شكراً","مشكور","يعطيك العافية"
]

AGENT_WORDS = [
    "agent","human","reception",
    "موظف","استقبال","اكلم احد"
]

EMERGENCY_WORDS = [
    "can't breathe",
    "unable to breathe",
    "اختناق",
    "ما اقدر اتنفس"
]

DEPARTMENTS = {
    "pediatric":"Pediatrics",
    "children":"Pediatrics",
    "اطفال":"Pediatrics",
    "أسنان":"Dental",
    "dentist":"Dental",
    "eye":"Ophthalmology",
    "ophthalmology":"Ophthalmology",
    "قلب":"Cardiology",
    "heart":"Cardiology",
    "مسالك":"Urology",
    "urinate":"Urology",
    "تبول":"Urology",
}

# =====================================================
# MENUS
# =====================================================

def main_menu(lang: str):
    if lang == "ar":
        return (
            "القائمة الرئيسية:\n\n"
            "1️⃣ حجز موعد\n"
            "2️⃣ تعديل موعد\n"
            "3️⃣ إلغاء موعد\n"
            "4️⃣ البحث عن طبيب\n"
            "5️⃣ مواعيد العمل\n"
            "6️⃣ التأمينات\n"
            "7️⃣ الموقع\n"
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

def dept_selected(lang: str, dept: str):
    if lang == "ar":
        return (
            f"تم اختيار قسم {dept} ✅\n\n"
            "هل ترغب بأقرب موعد؟\n\n"
            "1️⃣ أقرب موعد\n"
            "2️⃣ اختيار طبيب\n"
            "0️⃣ القائمة الرئيسية"
        )

    return (
        f"{dept} selected ✅\n\n"
        "Would you like the earliest appointment?\n\n"
        "1️⃣ Earliest appointment\n"
        "2️⃣ Choose doctor\n"
        "0️⃣ Main Menu"
    )

# =====================================================
# HELPERS
# =====================================================

def contains(text, words):
    t = text.lower()
    return any(w in t for w in words)

def detect_department(text: str):
    t = text.lower()
    for k, v in DEPARTMENTS.items():
        if k in t:
            return v
    return None

# =====================================================
# ENGINE ENTRY
# =====================================================

def run_engine(
    session: Dict[str, Any],
    message_text: str,
) -> EngineResult:

    actions: List[Dict[str, Any]] = []
    text = (message_text or "").strip()

    # ---------------------------------------------
    # LANGUAGE LOCK
    # ---------------------------------------------
    if "language" not in session:
        session["language"] = detect_language(text)

    lang = session["language"]

    # ---------------------------------------------
    # GLOBAL RESET
    # ---------------------------------------------
    if text in {"0","٠"}:

        session.clear()
        session["language"] = lang
        session["state"] = STATE_MAIN_MENU

        reply = main_menu(lang)
        return EngineResult(reply, session, actions)

    # ---------------------------------------------
    # THANK YOU
    # ---------------------------------------------
    if contains(text, THANK_WORDS):
        if lang == "ar":
            return EngineResult(
                "العفو ✅ إذا احتجت أي شيء اكتب 0 للقائمة.",
                session,
                actions,
            )
        return EngineResult(
            "You're welcome ✅ Type 0 anytime for menu.",
            session,
            actions,
        )

    # ---------------------------------------------
    # AGENT REQUEST
    # ---------------------------------------------
    if contains(text, AGENT_WORDS) or text == "99":
        actions.append({"type": "ESCALATE"})
        if lang == "ar":
            reply = "جارٍ تحويلك لموظف الاستقبال ✅"
        else:
            reply = "Connecting you to Reception ✅"
        return EngineResult(reply, session, actions)

    # ---------------------------------------------
    # EMERGENCY SAFE CHECK
    # ---------------------------------------------
    if contains(text, EMERGENCY_WORDS):

        if lang == "ar":
            reply = (
                "⚠️ قد تحتاج هذه الحالة لتقييم عاجل.\n"
                "إذا كنت لا تستطيع التنفس اتصل 997 فورًا.\n\n"
                "هل ترغب بالتحويل للاستقبال؟\n99️⃣"
            )
        else:
            reply = (
                "⚠️ This may require urgent medical attention.\n"
                "If breathing difficulty continues call 997.\n\n"
                "Reply 99 for Reception."
            )

        return EngineResult(reply, session, actions)

    # ---------------------------------------------
    # DEPARTMENT DETECTION (ANYTIME)
    # ---------------------------------------------
    dept = detect_department(text)
    if dept:
        session["department"] = dept
        session["state"] = STATE_BOOK_OPTIONS
        reply = dept_selected(lang, dept)
        return EngineResult(reply, session, actions)

    # ---------------------------------------------
    # BOOKING INTENT
    # ---------------------------------------------
    if contains(text, BOOKING_WORDS):
        session["state"] = STATE_MAIN_MENU
        return EngineResult(main_menu(lang), session, actions)

    # ---------------------------------------------
    # MAIN MENU STATE
    # ---------------------------------------------
    if session.get("state") == STATE_MAIN_MENU:

        if text == "1":
            return EngineResult(
                "Please type specialty (Pediatrics, Dental, Eye...)"
                if lang == "en"
                else "يرجى كتابة التخصص المطلوب",
                session,
                actions,
            )

        return EngineResult(main_menu(lang), session, actions)

    # ---------------------------------------------
    # FALLBACK
    # ---------------------------------------------
    if lang == "ar":
        reply = "لم أفهم بالكامل. اكتب 0 للقائمة."
    else:
        reply = "I didn't fully understand. Type 0 for menu."

    return EngineResult(reply, session, actions)