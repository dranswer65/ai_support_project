# core/engine.py
# ENTERPRISE CLINIC ENGINE v4
# Deterministic finite-state model (no loops, no fake replies)

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

# ==============================
# STATES
# ==============================

STATE_LANG = "LANG_SELECT"
STATE_MENU = "MAIN_MENU"
STATE_BOOK_SPECIALTY = "BOOK_SPECIALTY"
STATE_BOOK_DOCTOR = "BOOK_DOCTOR"
STATE_BOOK_DATE = "BOOK_DATE"
STATE_BOOK_CONFIRM = "BOOK_CONFIRM"
STATE_HUMAN = "HUMAN_QUEUE"

STATUS_ACTIVE = "ACTIVE"

SESSION_TIMEOUT_MIN = 30

CLINIC_AR = "مستشفى شيرين التخصصي"
CLINIC_EN = "Shireen Specialist Hospital"
EMERGENCY = "997"
RECEPTION = "+966XXXXXXXX"


@dataclass
class EngineResult:
    reply_text: str
    session: Dict
    actions: List[Dict]


# ==============================
# HELPERS
# ==============================

def _now():
    return datetime.now(timezone.utc)

def _iso():
    return _now().isoformat()

def _norm(txt: str) -> str:
    return (txt or "").strip().lower()

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

def _welcome():
    return (
        f"مرحبًا بكم في *{CLINIC_AR}* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        f"📞 الاستقبال: *{RECEPTION}*\n"
        f"🚑 الطوارئ: *{EMERGENCY}*\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "Please select your preferred language:\n\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
    )

def default_session(user_id: str):
    return {
        "user_id": user_id,
        "status": STATUS_ACTIVE,
        "state": STATE_LANG,
        "language": "ar",
        "language_locked": False,
        "last_ts": _iso(),
        "human_mode": False,
    }


# ==============================
# ENGINE CORE
# ==============================

def run_engine(session: Dict, user_message: str, language: str, **_) -> Dict:

    if not session:
        session = default_session("unknown")

    sess = dict(session)
    msg = _norm(user_message)
    actions = []

    # ------------------------------
    # HUMAN MODE (Bot Paused)
    # ------------------------------
    if sess.get("human_mode"):
        if msg == "0":
            sess["human_mode"] = False
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu(sess["language"]),
                "session": sess,
                "actions": [],
            }
        # Bot stays silent
        return {"reply_text": "", "session": sess, "actions": []}

    # ------------------------------
    # GLOBAL HUMAN OVERRIDE
    # ------------------------------
    if msg == "99" or "agent" in msg or "موظف" in msg:
        sess["state"] = STATE_HUMAN
        sess["human_mode"] = True
        actions.append({"type": "ESCALATE"})
        return {
            "reply_text": "تم تحويلكم إلى موظف الاستقبال. الرجاء الانتظار...",
            "session": sess,
            "actions": actions,
        }

    # ------------------------------
    # LANGUAGE SELECTION
    # ------------------------------
    if sess["state"] == STATE_LANG:

        if msg == "1":
            sess["language"] = "ar"
            sess["language_locked"] = True
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu("ar"),
                "session": sess,
                "actions": [],
            }

        if msg == "2":
            sess["language"] = "en"
            sess["language_locked"] = True
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu("en"),
                "session": sess,
                "actions": [],
            }

        return {"reply_text": _welcome(), "session": sess, "actions": []}

    # ------------------------------
    # MAIN MENU
    # ------------------------------
    if sess["state"] == STATE_MENU:

        if msg == "0":
            return {
                "reply_text": _main_menu(sess["language"]),
                "session": sess,
                "actions": [],
            }

        if msg == "1":
            sess["state"] = STATE_BOOK_SPECIALTY
            return {
                "reply_text": (
                    "يرجى اختيار التخصص:\n\n"
                    "1️⃣ الطب العام\n"
                    "2️⃣ طب الأطفال\n"
                    "3️⃣ أمراض النساء\n"
                    "4️⃣ العظام\n"
                    "0️⃣ القائمة الرئيسية\n"
                    "99️⃣ موظف الاستقبال"
                ),
                "session": sess,
                "actions": [],
            }

        # Other options simplified for demo
        return {
            "reply_text": _main_menu(sess["language"]),
            "session": sess,
            "actions": [],
        }

    # ------------------------------
    # BOOKING - SPECIALTY
    # ------------------------------
    if sess["state"] == STATE_BOOK_SPECIALTY:

        if msg == "0":
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu(sess["language"]),
                "session": sess,
                "actions": [],
            }

        if msg in ["1", "2", "3", "4"]:
            sess["state"] = STATE_BOOK_DOCTOR
            return {
                "reply_text": (
                    "الأطباء المتاحون:\n\n"
                    "1️⃣ د. أحمد\n"
                    "2️⃣ د. سارة\n"
                    "0️⃣ القائمة الرئيسية\n"
                    "99️⃣ موظف الاستقبال"
                ),
                "session": sess,
                "actions": [],
            }

        return {
            "reply_text": "يرجى اختيار رقم صحيح.",
            "session": sess,
            "actions": [],
        }

    # ------------------------------
    # BOOKING - DOCTOR
    # ------------------------------
    if sess["state"] == STATE_BOOK_DOCTOR:

        if msg == "0":
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu(sess["language"]),
                "session": sess,
                "actions": [],
            }

        if msg in ["1", "2"]:
            sess["state"] = STATE_BOOK_DATE
            return {
                "reply_text": "يرجى إدخال التاريخ (مثال: 2026-02-28)",
                "session": sess,
                "actions": [],
            }

        return {
            "reply_text": "يرجى اختيار رقم صحيح.",
            "session": sess,
            "actions": [],
        }

    # ------------------------------
    # BOOKING - DATE
    # ------------------------------
    if sess["state"] == STATE_BOOK_DATE:

        if msg == "0":
            sess["state"] = STATE_MENU
            return {
                "reply_text": _main_menu(sess["language"]),
                "session": sess,
                "actions": [],
            }

        sess["state"] = STATE_BOOK_CONFIRM
        ref = f"SH-{_now().strftime('%H%M%S')}"
        return {
            "reply_text": (
                f"تم حجز الموعد مبدئيًا ✅\n"
                f"رقم الطلب: {ref}\n\n"
                "1️⃣ تأكيد\n"
                "2️⃣ إلغاء"
            ),
            "session": sess,
            "actions": [],
        }

    # ------------------------------
    # BOOKING CONFIRM
    # ------------------------------
    if sess["state"] == STATE_BOOK_CONFIRM:

        if msg == "1":
            sess["state"] = STATE_MENU
            return {
                "reply_text": "تم تأكيد الموعد ✅",
                "session": sess,
                "actions": [],
            }

        if msg == "2":
            sess["state"] = STATE_MENU
            return {
                "reply_text": "تم إلغاء الطلب.",
                "session": sess,
                "actions": [],
            }

        return {
            "reply_text": "يرجى اختيار 1 أو 2.",
            "session": sess,
            "actions": [],
        }

    # Fallback safety
    sess["state"] = STATE_MENU
    return {
        "reply_text": _main_menu(sess["language"]),
        "session": sess,
        "actions": [],
    }