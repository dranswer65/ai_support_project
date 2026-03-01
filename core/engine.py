# core/engine.py
from __future__ import annotations

from typing import Dict, Tuple, Any
import time

STATE_MAIN_MENU = "MAIN_MENU"
STATE_SELECT_DOCTOR = "SELECT_DOCTOR"


def _menu(lang: str):

    if lang == "ar":
        return (
            "القائمة الرئيسية:\n\n"
            "1️⃣ حجز موعد\n"
            "2️⃣ تعديل موعد\n"
            "3️⃣ إلغاء موعد\n"
            "4️⃣ البحث عن طبيب\n"
            "8️⃣ معلومات التواصل\n"
            "99️⃣ الاستقبال"
        )

    return (
        "Main Menu:\n\n"
        "1️⃣ Book Appointment\n"
        "2️⃣ Reschedule\n"
        "3️⃣ Cancel\n"
        "4️⃣ Find Doctor\n"
        "8️⃣ Contact Info\n"
        "99️⃣ Reception"
    )


def run_engine(
    tenant_id: str,
    user_id: str,
    message: str,
    sess: Dict[str, Any],
) -> Tuple[str, Dict, Dict]:

    lang = sess.get("language", "en")

    # =====================================================
    # MAIN MENU
    # =====================================================
    if message == "0":
        sess["state"] = STATE_MAIN_MENU
        return _menu(lang), sess, {}

    if sess.get("state") == STATE_MAIN_MENU:

        if message == "1":
            sess["state"] = STATE_SELECT_DOCTOR

            if lang == "ar":
                return (
                    "يرجى اختيار التخصص.",
                    sess,
                    {},
                )
            else:
                return (
                    "Please select specialty.",
                    sess,
                    {},
                )

        if message == "8":
            return (
                "📞 Reception: +966XXXXXXXX\n🚑 Emergency: 997",
                sess,
                {},
            )

    # =====================================================
    # DOCTOR SELECTION
    # =====================================================
    if sess.get("state") == STATE_SELECT_DOCTOR:

        sess["booking_expires_at"] = time.time() + 300

        if lang == "ar":
            return (
                "الأطباء المتاحون:\n"
                "1️⃣ د. رنا\n"
                "⏳ تنتهي جلسة الحجز خلال 5 دقائق",
                sess,
                {},
            )
        else:
            return (
                "Available doctors:\n"
                "1️⃣ Dr. Rana\n"
                "⏳ Booking expires in 5 minutes",
                sess,
                {},
            )

    return _menu(lang), sess, {}