# whatsapp_controller.py
from __future__ import annotations

import re
import time
from typing import Dict, Tuple, Any

from core.engine import run_engine

STATE_MAIN_MENU = "MAIN_MENU"
STATE_HANDOFF = "HUMAN_HANDOFF"

# =========================================================
# LANGUAGE DETECTION
# =========================================================

def detect_language(text: str) -> str:
    t = text or ""

    arabic = sum(1 for c in t if '\u0600' <= c <= '\u06FF')
    latin = sum(1 for c in t if c.isascii())

    return "ar" if arabic > latin else "en"


# =========================================================
# SPECIALTY INTENT DETECTION
# =========================================================

SPECIALTY_KEYWORDS = {
    "pediatrics": ["children", "pediatric", "اطفال", "الأطفال"],
    "cardiology": ["heart", "cardio", "قلب"],
    "dermatology": ["skin", "جلدية"],
    "ophthalmology": ["eye", "عيون"],
    "urology": ["urinate", "بول", "تبول"],
}

def detect_specialty(text: str):
    t = (text or "").lower()

    for spec, words in SPECIALTY_KEYWORDS.items():
        if any(w in t for w in words):
            return spec
    return None


# =========================================================
# URGENCY CLASSIFICATION
# =========================================================

SEVERE_SIGNS = [
    "chest pain",
    "cannot breathe",
    "unconscious",
]

URGENT_SIGNS = [
    "urinate",
    "تبول",
    "pain",
    "fever",
]

def classify_medical(text: str):
    t = text.lower()

    if any(w in t for w in SEVERE_SIGNS):
        return "emergency"

    if any(w in t for w in URGENT_SIGNS):
        return "urgent"

    return None


THANK_WORDS = ["thanks", "thank you", "شكرا", "شكراً"]


# =========================================================
# MAIN HANDLER
# =========================================================

async def handle_whatsapp_message(
    tenant_id: str,
    user_id: str,
    message_text: str,
    session: Dict[str, Any],
) -> Tuple[str, Dict]:

    sess = session or {}

    text = (message_text or "").strip()

    # -----------------------------------------------------
    # THANK YOU HANDLING
    # -----------------------------------------------------
    if any(w in text.lower() for w in THANK_WORDS):
        lang = sess.get("language", "en")
        reply = (
            "العفو ✅ اكتب 0 لعرض القائمة."
            if lang == "ar"
            else "You're welcome ✅ Reply 0 for menu."
        )
        return reply, sess

    # -----------------------------------------------------
    # AUTO LANGUAGE LOCK
    # -----------------------------------------------------
    if not sess.get("language_locked"):
        lang = detect_language(text)

        sess["language"] = lang
        sess["language_locked"] = True
        sess["state"] = STATE_MAIN_MENU

    # -----------------------------------------------------
    # MEDICAL TRIAGE
    # -----------------------------------------------------
    severity = classify_medical(text)

    if severity == "emergency":
        sess["state"] = STATE_HANDOFF
        return (
            "🚨 Please call 997 immediately.\n"
            "Connecting you to Reception.",
            sess,
        )

    if severity == "urgent":
        lang = sess["language"]

        if lang == "ar":
            return (
                "⚠️ قد تحتاج حالتك لتقييم طبي.\n"
                "1️⃣ حجز أقرب موعد\n"
                "99️⃣ موظف الاستقبال\n"
                "🚑 للحالات الطارئة اتصل 997",
                sess,
            )
        else:
            return (
                "⚠️ Your symptoms may require medical evaluation.\n"
                "1️⃣ Book earliest appointment\n"
                "99️⃣ Reception\n"
                "🚑 For emergencies call 997",
                sess,
            )

    # -----------------------------------------------------
    # SPECIALTY SHORTCUT
    # -----------------------------------------------------
    spec = detect_specialty(text)

    if spec and sess.get("state") == STATE_MAIN_MENU:
        sess["selected_specialty"] = spec
        sess["state"] = "SELECT_DOCTOR"

    # -----------------------------------------------------
    # HANDOFF LOCK
    # -----------------------------------------------------
    if sess.get("state") == STATE_HANDOFF:
        if text != "0":
            return (
                "✅ Reception will assist you shortly.",
                sess,
            )
        sess["state"] = STATE_MAIN_MENU

    # -----------------------------------------------------
    # BOOKING EXPIRY CHECK
    # -----------------------------------------------------
    exp = sess.get("booking_expires_at")
    if exp and time.time() > exp:
        sess.pop("booking_expires_at", None)
        return (
            "⏳ Booking session expired. Please start again.",
            sess,
        )

    # -----------------------------------------------------
    # ENGINE CALL
    # -----------------------------------------------------
    reply, new_sess, meta = run_engine(
        tenant_id,
        user_id,
        text,
        sess,
    )

    return reply, new_sess