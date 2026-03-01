# core/engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List

from core.nlu import detect_intent


# =========================================================
# ENGINE RESULT
# =========================================================

@dataclass
class EngineResult:
    reply_text: str
    session: Dict[str, Any]
    actions: List[Dict[str, Any]]


# =========================================================
# MENUS
# =========================================================

AR_MAIN_MENU = """القائمة الرئيسية:

1️⃣ حجز موعد
2️⃣ تعديل موعد
3️⃣ إلغاء موعد
4️⃣ البحث عن طبيب
5️⃣ أوقات العمل
6️⃣ التأمينات
7️⃣ الموقع والاتجاهات
8️⃣ معلومات التواصل
99️⃣ موظف الاستقبال
"""

EN_MAIN_MENU = """Main Menu:

1️⃣ Book Appointment
2️⃣ Reschedule Appointment
3️⃣ Cancel Appointment
4️⃣ Find a Doctor
5️⃣ Hospital Timings
6️⃣ Accepted Insurance
7️⃣ Location & Directions
8️⃣ Contact Information
99️⃣ Reception
"""


# =========================================================
# HELPERS
# =========================================================

def _menu(lang: str):
    return AR_MAIN_MENU if lang == "ar" else EN_MAIN_MENU


def _dept_name(dept: str, lang: str):

    mapping = {
        "PEDIATRICS": ("طب الأطفال 👶", "Pediatrics 👶"),
        "DENTAL": ("طب الأسنان 🦷", "Dental 🦷"),
        "CARDIOLOGY": ("القلب ❤️", "Cardiology ❤️"),
        "NEUROLOGY": ("الأعصاب 🧠", "Neurology 🧠"),
        "UROLOGY": ("المسالك البولية 🚹", "Urology 🚹"),
        "GENERAL": ("الطب العام 🩺", "General Medicine 🩺"),
    }

    ar, en = mapping.get(dept, (dept, dept))
    return ar if lang == "ar" else en


# =========================================================
# ENGINE
# =========================================================

def run_engine(
    message_text: str,
    session: Dict[str, Any],
) -> EngineResult:

    actions: List[Dict[str, Any]] = []

    lang = session.get("language", "en")

    text = (message_text or "").strip().lower()

    intent, dept = detect_intent(message_text)

    # =====================================================
    # THANK YOU
    # =====================================================
    if intent == "THANKS":

        reply = (
            "العفو ✅ إذا احتجت أي مساعدة اكتب 0 لعرض القائمة."
            if lang == "ar"
            else "You're welcome ✅ Type 0 anytime for the menu."
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # RECEPTION REQUEST
    # =====================================================
    if intent == "RECEPTION":

        actions.append({"type": "ESCALATE_RECEPTION"})

        reply = (
            "✅ يتم تحويلك الآن إلى موظف الاستقبال."
            if lang == "ar"
            else "✅ Connecting you to Reception."
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # EMERGENCY TRIAGE (SAFE GCC STYLE)
    # =====================================================
    if intent == "EMERGENCY":

        actions.append({"type": "ESCALATE_RECEPTION"})

        reply = (
            "⚠️ قد تحتاج حالتك إلى تقييم طبي سريع.\n"
            "إذا كانت الحالة طارئة يرجى الاتصال على 997 فورًا.\n\n"
            "هل ترغب بالتحويل لموظف الاستقبال؟"
            if lang == "ar"
            else
            "⚠️ Your symptoms may require urgent medical attention.\n"
            "If this is an emergency please call 997 immediately.\n\n"
            "Would you like me to connect you to Reception?"
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # SPECIALTY AUTO JUMP
    # =====================================================
    if intent == "SPECIALTY":

        session["department"] = dept
        session["state"] = "BOOKING"

        dname = _dept_name(dept, lang)

        reply = (
            f"تم اختيار قسم {dname} ✅\n"
            "هل ترغب بأقرب موعد متاح؟\n\n"
            "1️⃣ أقرب موعد\n"
            "2️⃣ اختيار طبيب\n"
            "0️⃣ القائمة الرئيسية"
            if lang == "ar"
            else
            f"{dname} selected ✅\n"
            "Would you like the earliest available appointment?\n\n"
            "1️⃣ Earliest appointment\n"
            "2️⃣ Choose doctor\n"
            "0️⃣ Main Menu"
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # BOOKING INTENT
    # =====================================================
    if intent == "BOOK":

        session["state"] = "BOOKING"

        reply = (
            "بكل سرور ✅\nيرجى اختيار القسم:"
            if lang == "ar"
            else "Sure ✅ Please choose a department:"
        )

        reply += "\n\n" + _menu(lang)

        return EngineResult(reply, session, actions)

    # =====================================================
    # CANCEL
    # =====================================================
    if intent == "CANCEL":

        reply = (
            "يرجى تزويدي برقم الموعد لإلغاء الحجز."
            if lang == "ar"
            else "Please provide appointment number to cancel."
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # RESCHEDULE
    # =====================================================
    if intent == "RESCHEDULE":

        reply = (
            "يرجى تزويدي برقم الموعد لتعديله."
            if lang == "ar"
            else "Please provide appointment number to reschedule."
        )

        return EngineResult(reply, session, actions)

    # =====================================================
    # MENU COMMAND
    # =====================================================
    if text in ["0", "menu"]:
        return EngineResult(_menu(lang), session, actions)

    # =====================================================
    # DEFAULT SAFE FALLBACK
    # =====================================================
    reply = (
        "لم أفهم الطلب بالكامل.\nاكتب 0 لعرض القائمة الرئيسية."
        if lang == "ar"
        else
        "I didn't fully understand.\nType 0 to view the main menu."
    )

    return EngineResult(reply, session, actions)