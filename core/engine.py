# core/engine.py — Enterprise WhatsApp Clinic Demo Engine (MVP)
# FIXED:
# ✅ Welcome = Language selection only (no fake menu bullets)
# ✅ After language selection -> show main menu
# ✅ Sticky handoff: if 99/Agent -> lock + never restart greeting
# ✅ Never return empty reply in handoff mode (always “please wait”)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone, date, timedelta
import re
import random

STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ABANDONED = "ABANDONED"

STATE_LANG_SELECT = "LANG_SELECT"
STATE_MENU = "MENU"
STATE_ESCALATION = "ESCALATION"

ENGINE_MARKER = "ENTERPRISE_CLINIC_ENGINE_V3_FIXED"

SESSION_EXPIRE_SECONDS = 60 * 60  # 60 minutes

CLINIC_NAME_AR = "مستشفى شيرين التخصصي"
CLINIC_NAME_EN = "Shireen Specialist Hospital"
EMERGENCY_NUMBER = "997"
RECEPTION_PHONE = "+966XXXXXXXX"
CONTACT_EMAIL = "reception@shireen-hospital.example"
MAPS_LINK = "https://maps.google.com/?q=Shireen+Specialist+Hospital"

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
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _norm(s: str) -> str:
    return (s or "").strip()

def _low(s: str) -> str:
    return _normalize_digits((s or "").strip().lower())

def _lang(lang: str) -> str:
    l = (lang or "").strip().lower()
    return "ar" if l.startswith("ar") else "en"

def _is_digit(s: str) -> bool:
    return _low(s).isdigit()

def _to_int(s: str, default: int = -1) -> int:
    try:
        return int(_low(s))
    except Exception:
        return default

_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال"
]

def _wants_agent(text: str) -> bool:
    t = _low(text)
    return t == "99" or any(k in t for k in _AGENT_KEYS)

def _is_greeting(text: str) -> bool:
    t = _low(text)
    if not t:
        return False
    en = {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}
    if t in en:
        return True
    ar_parts = ["السلام عليكم", "مرحبا", "أهلا", "اهلا", "هلا", "صباح الخير", "مساء الخير"]
    return any(p in t for p in ar_parts)

def default_session(user_id: str) -> Dict[str, Any]:
    return {
        "engine": ENGINE_MARKER,
        "user_id": user_id,
        "status": STATUS_ACTIVE,
        "state": STATE_LANG_SELECT,
        "last_step": STATE_LANG_SELECT,

        "language": "ar",
        "language_locked": False,
        "text_direction": "rtl",

        "has_greeted": False,
        "last_user_ts": None,
        "last_bot_ts": None,
        "last_bot_message": "",

        "escalation_flag": False,
        "handoff_active": False,
        "handoff_until": None,
    }

def _set_bot(sess: Dict[str, Any], msg: str) -> None:
    sess["last_bot_message"] = msg
    sess["last_bot_ts"] = _utcnow_iso()

def _handoff_is_active(sess: Dict[str, Any]) -> bool:
    if not bool(sess.get("handoff_active")):
        return False
    until = _parse_iso(sess.get("handoff_until")) if isinstance(sess.get("handoff_until"), str) else None
    if until and datetime.now(timezone.utc) <= until:
        return True
    sess["handoff_active"] = False
    sess["handoff_until"] = None
    return False

def _handoff_wait_msg(lang: str) -> str:
    if lang == "ar":
        return "تم تحويلكم إلى موظف الاستقبال ✅ الرجاء الانتظار... (للعودة للقائمة اكتب 0)"
    return "You’re being connected to Reception ✅ Please wait... (Reply 0 for the menu)"

def _welcome_language_only() -> str:
    # ✅ IMPORTANT: no service bullets here (prevents “where are numbers?” confusion)
    return (
        f"مرحبًا بكم في *{CLINIC_NAME_AR}* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        f"📞 الاستقبال: *{RECEPTION_PHONE}*\n"
        f"🚑 الطوارئ: *{EMERGENCY_NUMBER}*\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
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

def handle_turn(
    user_id: str,
    message_text: str,
    language: str,
    session_in: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    sess = dict(session_in or default_session(user_id))
    sess["user_id"] = user_id
    actions: List[Dict[str, Any]] = []

    # Language lock if already selected
    lang = _lang(language or sess.get("language") or "ar")
    if bool(sess.get("language_locked")):
        lang = _lang(sess.get("language") or lang)
    sess["language"] = lang
    sess["text_direction"] = "rtl" if lang == "ar" else "ltr"

    raw = _norm(message_text)
    tlow = _low(message_text)

    # ✅ If handoff active, NEVER restart greeting/menu. Just “please wait”.
    if _handoff_is_active(sess):
        if tlow == "0":
            sess["handoff_active"] = False
            sess["handoff_until"] = None
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = _main_menu(sess["language"])
            _set_bot(sess, out)
            sess["last_user_ts"] = _utcnow_iso()
            return EngineResult(out, sess, actions)

        out = _handoff_wait_msg(sess["language"])
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # ✅ Agent override ALWAYS
    if _wants_agent(message_text):
        sess["state"] = STATE_ESCALATION
        sess["status"] = STATUS_ACTIVE
        sess["escalation_flag"] = True
        sess["last_step"] = STATE_ESCALATION
        sess["handoff_active"] = True
        sess["handoff_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

        out = ("جاري تحويلكم إلى موظف الاستقبال. يرجى الانتظار..."
               if sess["language"] == "ar"
               else "Connecting you to a reception officer. Please wait...")
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        actions.append({"type": "ESCALATE", "reason": "user_requested_agent"})
        return EngineResult(out, sess, actions)

    # ✅ FIRST MESSAGE behavior:
    # If not greeted yet -> show welcome (language only), not expiry
    if not bool(sess.get("has_greeted")):
        sess["has_greeted"] = True
        sess["state"] = STATE_LANG_SELECT
        sess["last_step"] = STATE_LANG_SELECT
        out = _welcome_language_only()
        _set_bot(sess, out)
        sess["last_user_ts"] = _utcnow_iso()
        return EngineResult(out, sess, actions)

    # Session expiry (only after greeted)
    prev = _parse_iso(session_in.get("last_user_ts")) if isinstance(session_in, dict) else None
    if prev:
        sec = (datetime.now(timezone.utc) - prev).total_seconds()
        if sec >= SESSION_EXPIRE_SECONDS:
            sess["state"] = STATE_MENU
            sess["last_step"] = STATE_MENU
            out = ("انتهت الجلسة بسبب عدم النشاط. تفضل اختر من القائمة للمتابعة:\n\n" + _main_menu(sess["language"])
                   if sess["language"] == "ar"
                   else "Your session expired due to inactivity. Please choose from the menu:\n\n" + _main_menu(sess["language"]))
            _set_bot(sess, out)
            sess["last_user_ts"] = _utcnow_iso()
            return EngineResult(out, sess, actions)

    sess["last_user_ts"] = _utcnow_iso()

    # 0 always menu
    if tlow == "0":
        sess["state"] = STATE_MENU
        sess["last_step"] = STATE_MENU
        out = _main_menu(sess["language"])
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ✅ Language selection
    if sess.get("state") == STATE_LANG_SELECT:
        if _is_digit(raw):
            c = _to_int(raw)
            if c == 1:
                sess["language"] = "ar"
                sess["language_locked"] = True
                sess["text_direction"] = "rtl"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("ar")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)
            if c == 2:
                sess["language"] = "en"
                sess["language_locked"] = True
                sess["text_direction"] = "ltr"
                sess["state"] = STATE_MENU
                sess["last_step"] = STATE_MENU
                out = _main_menu("en")
                _set_bot(sess, out)
                return EngineResult(out, sess, actions)

        # If user didn’t choose 1/2, repeat welcome language prompt
        out = _welcome_language_only()
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # ✅ Menu state (your full flows continue from here)
    if sess.get("state") == STATE_MENU:
        out = _main_menu(sess["language"])
        _set_bot(sess, out)
        return EngineResult(out, sess, actions)

    # Fallback
    sess["state"] = STATE_MENU
    sess["last_step"] = STATE_MENU
    out = _main_menu(sess["language"])
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