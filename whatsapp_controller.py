# whatsapp_controller.py — Part 1/3
# WhatsApp Controller
# Day 47A — Conversation Versioning & Safe Restart
# Day 49  — Compliance & Audit Logging Layer
# + Day 50  — Amazon-level workflow: HOLD, AI-first resolution, no-response timers
# --------------------------------------------------

from __future__ import annotations

import os
import re
import requests
from datetime import datetime, timedelta

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone
from escalation_router import route_escalation
from handoff_builder import build_handoff_payload

# --------------------------------------------------
# 🔐 Compliance Audit Layer
# --------------------------------------------------
from compliance.audit_logger import log_event

# --------------------------------------------------
# 🔐 Day 49 — Compliance Audit Events (safe import)
# --------------------------------------------------
try:
    from compliance.audit_events import (
        conversation_restart_event,
        conversation_closed_event,
        escalation_event,
        sla_breach_event,
        incident_mode_event,
    )
except Exception:
    # Fallbacks so WhatsApp never crashes if an event is missing
    def conversation_restart_event(**kwargs): return {"event": "conversation_restart", **kwargs}
    def conversation_closed_event(**kwargs):  return {"event": "conversation_closed", **kwargs}
    def escalation_event(**kwargs):          return {"event": "escalation", **kwargs}
    def sla_breach_event(**kwargs):          return {"event": "sla_breach", **kwargs}
    def incident_mode_event(**kwargs):       return {"event": "incident_mode", **kwargs}

from profiles.user_profile_store import (
    get_preferred_language,
    set_language_preference
)

from vendor_orchestrator import dispatch_ticket
from incident.incident_state import is_incident_mode

# --------------------------------------------------
# Config (safe defaults)
# --------------------------------------------------
SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

# No-response handling (seconds)
NO_REPLY_PING_SECONDS = int(os.getenv("WA_NO_REPLY_PING_SECONDS", "180"))    # 3 min
NO_REPLY_CLOSE_SECONDS = int(os.getenv("WA_NO_REPLY_CLOSE_SECONDS", "420"))  # 7 min

# Escalation guard: don't escalate immediately just because order id exists
MAX_AI_ATTEMPTS_BEFORE_ESCALATION = int(os.getenv("WA_MAX_AI_ATTEMPTS", "2"))

# --------------------------------------------------
# In-memory session store
# (Production later: move to Redis/Postgres)
# --------------------------------------------------
sessions: dict[str, dict] = {}

# --------------------------------------------------
# Session Management
# --------------------------------------------------
def _utcnow() -> datetime:
    return datetime.utcnow()

def get_or_create_session(user_id: str) -> dict:
    now = _utcnow()

    if user_id not in sessions:
        sessions[user_id] = {
            "state": "ACTIVE",  # ACTIVE / WAITING_ORDER_ID / ESCALATION / CLOSED

            "tries": 0,
            "last_intent": None,
            "last_user_message": None,

            # Versioning
            "conversation_version": 1,
            "restart_count": 0,
            "restart_reason": None,
            "last_closed_at": None,

            # Language
            "language": None,
            "text_direction": "ltr",

            # Memory
            "order_id": None,
            "asked_order_id_count": 0,
            "no_count": 0,

            # Day 50 — better UX
            "issue_summary": "",          # stable summary of the issue
            "ai_attempts": 0,             # how many AI attempts in current issue
            "last_bot_message": "",       # last bot output
            "last_user_ts": now.isoformat(),
            "last_bot_ts": None,
            "no_reply_ping_sent": False,

            "created_at": now.isoformat(),
        }
        return sessions[user_id]

    session = sessions[user_id]

    # Safe restart after CLOSED
    if session.get("state") == "CLOSED":
        session["conversation_version"] = int(session.get("conversation_version", 1)) + 1
        session["restart_count"] = int(session.get("restart_count", 0)) + 1
        session["restart_reason"] = "user_return"

        session["state"] = "ACTIVE"
        session["tries"] = 0
        session["last_intent"] = None

        session["order_id"] = None
        session["asked_order_id_count"] = 0
        session["no_count"] = 0

        session["issue_summary"] = ""
        session["ai_attempts"] = 0
        session["no_reply_ping_sent"] = False

        log_event(
            conversation_restart_event(
                user_id=user_id,
                version=session["conversation_version"],
                restart_count=session["restart_count"],
                restart_reason=session["restart_reason"],
            )
        )

    return session

# --------------------------------------------------
# KPI utilities
# --------------------------------------------------
def collect_restart_kpis(session: dict, kpi_signals: list) -> None:
    if int(session.get("restart_count", 0)) > 0:
        kpi_signals.append("restart_after_close")
        if int(session.get("restart_count", 0)) >= 3:
            kpi_signals.append("frequent_restarts")

# --------------------------------------------------
# Priority Engine
# --------------------------------------------------
def get_customer_priority(user_id: str, session: dict, kpi_signals: list):
    if user_id.startswith("vip_"):
        return "P0", "VIP customer"

    if "sla_breach_detected" in kpi_signals:
        log_event(
            sla_breach_event(
                user_id=user_id,
                conversation_version=session.get("conversation_version")
            )
        )
        return "P0", "SLA breach detected"

    if int(session.get("restart_count", 0)) >= 3:
        return "P1", "Frequent restarts detected"

    if session.get("state") == "ESCALATION":
        return "P1", "Auto escalation"

    return "P2", "Standard customer"

# --------------------------------------------------
# Basic NLP / Pattern Helpers (lightweight)
# --------------------------------------------------
_ORDER_ID_RE = re.compile(r"\b([A-Z]{2,5}\d{4,12}|\d{6,12})\b", re.IGNORECASE)

def _norm(text: str) -> str:
    return (text or "").strip().lower()

def _extract_order_id(text: str):
    m = _ORDER_ID_RE.search(text or "")
    return m.group(1).strip() if m else None

def _is_greeting(text: str) -> bool:
    t = _norm(text)
    return t in {"hi", "hello", "hey", "السلام عليكم", "مرحبا", "أهلاً", "اهلا"}

def _is_thanks(text: str) -> bool:
    t = _norm(text)
    return t in {"thanks", "thank you", "thx", "شكرا", "شكرًا", "جزاك الله خير"}

def _is_goodbye(text: str) -> bool:
    t = _norm(text)
    return t in {"bye", "goodbye", "see you", "مع السلامة", "سلام", "الى اللقاء", "إلى اللقاء"}

def _is_no(text: str) -> bool:
    t = _norm(text)
    return t in {"no", "nope", "nah", "لا", "لا شكرا", "لا شكرًا", "ليس الآن", "مو", "مش"}

def _is_ack(text: str) -> bool:
    t = _norm(text)
    return t in {"ok", "okay", "okey", "k", "sure", "alright", "done", "تمام", "تم", "اوكي", "حسنًا", "حسنا"}

def _is_yes(text: str) -> bool:
    t = _norm(text)
    return t in {"yes", "yeah", "yep", "ya", "نعم", "اي", "أجل", "تمام"}


def _looks_like_order_issue(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    # English
    if any(k in t for k in ["order", "delivery", "shipment", "tracking", "late", "delayed", "where is my order"]):
        return True
    # Arabic
    if any(k in t for k in ["طلب", "طلبي", "توصيل", "الشحنة", "تتبع", "متأخر", "تأخير", "وين الطلب", "تأخر التوصيل"]):
        return True
    return False

def _detect_intent(text: str):
    """
    Minimal intent detection for the order flow.
    """
    t = _norm(text)
    if _is_greeting(t):
        return "greeting"
    if _is_thanks(t):
        return "thanks"
    if _is_goodbye(t):
        return "goodbye"

    if any(k in t for k in ["reset", "start over"]):
        return "reset"
    if any(k in t for k in ["agent", "human", "call me", "representative"]):
        return "handoff"

    # delivery/order delay (English)
    if any(k in t for k in ["delivery", "late", "delayed", "where is my order", "order delayed", "my order delayed"]):
        return "delivery_delay"

    # delivery/order delay (Arabic)
    if any(k in t for k in ["طلب", "طلبي", "متأخر", "تأخير", "وين الطلب", "توصيل", "الشحنة", "تأخر التوصيل"]):
        return "delivery_delay"

    # order id detection
    if _extract_order_id(text):
        return "order_id"

    return "other"

# --------------------------------------------------
# AI call (internal /chat) — uses server-side WA client
# --------------------------------------------------
def _call_supportpilot_chat(user_message: str, language: str) -> str:
    """
    Calls internal /chat endpoint.
    Keeps WhatsApp fully server-side.
    """
    api_base = (SP_API_BASE or "").strip()
    if not api_base:
        return "System error: SP_API_BASE not configured"

    url = f"{api_base}/chat"
    payload = {
        "client_name": WA_DEFAULT_CLIENT,
        "question": user_message,
        "tone": "formal",
        "language": "ar" if language == "ar" else "en",
    }

    try:
        r = requests.post(url, json=payload, timeout=25)
        if r.status_code != 200:
            try:
                j = r.json()
                return (j.get("detail") or str(j))[:500]
            except Exception:
                return "AI server error"
        data = r.json()
        return (data.get("answer") or "").strip() or "Sorry — I couldn't generate a response."
    except Exception as e:
        print("AI CALL ERROR:", repr(e))
        return "System temporarily unavailable"


def _safe_set_issue_summary(session: dict, intent: str, message_text: str) -> None:
    """
    Keep issue_summary stable; do NOT overwrite with OrderID-only message.
    """
    t = (message_text or "").strip()
    if not t:
        return

    if intent in {"order_id", "greeting", "thanks"}:
        return

    # overwrite only if it's meaningful (not too short)
    if len(t) >= 8:
        session["issue_summary"] = t

def _no_response_check(session: dict, language: str):
    """
    If user is silent after bot asked something, we can ping/close.
    (This function is here for completeness, but needs a scheduler/automation
     to actually run without new inbound messages.)
    You CAN trigger it from your WhatsApp webhook when you receive delivery statuses,
    or via a small cron endpoint later.
    """
    try:
        last_bot_ts = session.get("last_bot_ts")
        if not last_bot_ts:
            return None

        last_bot = datetime.fromisoformat(last_bot_ts)
        delta = (_utcnow() - last_bot).total_seconds()

        if delta >= NO_REPLY_CLOSE_SECONDS:
            session["state"] = "CLOSED"
            session["last_closed_at"] = _utcnow().isoformat()
            log_event(
                conversation_closed_event(
                    user_id=session.get("user_id"),
                    version=session.get("conversation_version"),
                    reason="no_response_timeout"
                )
            )
            if language == "ar":
                return "شكرًا لتواصلك معنا. يبدو أنك غير متصل الآن. يمكنك مراسلتنا في أي وقت وسنكون سعداء بمساعدتك. 🌟"
            return "Thanks for reaching out. It looks like you’re not available right now. Feel free to message us anytime — we’ll be happy to help. 🌟"

        if delta >= NO_REPLY_PING_SECONDS and not session.get("no_reply_ping_sent", False):
            session["no_reply_ping_sent"] = True
            if language == "ar":
                return "هل ما زلت متصلاً؟ أنا هنا لمساعدتك. ✅"
            return "Are you still connected? I’m here to help. ✅"

    except Exception:
        return None

    return None

# whatsapp_controller.py — Part 2/3

# --------------------------------------------------
# Core Conversation Router
# --------------------------------------------------
def handle_message(user_id: str, message_text: str, kpi_signals=None):
    """
    Main entry point:
    returns: (reply_text, meta_dict)
    """
    if kpi_signals is None:
        kpi_signals = []

    session = get_or_create_session(user_id)
    session["user_id"] = user_id  # for no-response logging if needed
    session["last_user_ts"] = _utcnow().isoformat()
    session["no_reply_ping_sent"] = False

    collect_restart_kpis(session, kpi_signals)

    # --- Incident mode KPI ---
    if is_incident_mode():
        kpi_signals.append("incident_mode")
        log_event(
            incident_mode_event(
                user_id=user_id,
                conversation_version=session.get("conversation_version")
            )
        )

    # --- language ---
    detected = detect_language(message_text)
    preferred = get_preferred_language(user_id)

    language = preferred or session.get("language") or detected or "en"
    language = (language or "en").strip().lower()
    if language not in ("en", "ar"):
        language = "en"

    if session.get("language") != language:
        session["language"] = language
        set_language_preference(user_id, language)

    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # store last user message
    session["last_user_message"] = message_text

    # intent
    intent = _detect_intent(message_text)
    session["last_intent"] = intent

    # priority
    priority = get_customer_priority(user_id, session, kpi_signals)

    # --------------------------------------------------
    # Reset / Handoff direct
    # --------------------------------------------------
    if intent == "reset":
        session["state"] = "ACTIVE"
        session["tries"] = 0
        session["order_id"] = None
        session["asked_order_id_count"] = 0
        session["no_count"] = 0
        session["issue_summary"] = ""
        session["ai_attempts"] = 0

        if language == "ar":
            out = "✅ تم إعادة ضبط المحادثة. كيف يمكنني مساعدتك اليوم؟"
        else:
            out = "✅ Reset done. How may I help you today?"
        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

    if intent == "handoff":
        session["state"] = "ESCALATION"
        reply, meta = _escalate_to_human(
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            priority=priority,
            decision_rule="user_requested_handoff",
            decision_reason="Customer explicitly requested human support",
            extra_context={"order_id": session.get("order_id"), "issue_summary": session.get("issue_summary", "")},
        )
        session["last_bot_message"] = reply
        session["last_bot_ts"] = _utcnow().isoformat()
        return reply, meta

    # --------------------------------------------------
    # Loop protection on repeated "No"
    # --------------------------------------------------
    if _is_no(message_text):
        session["no_count"] = int(session.get("no_count", 0)) + 1
        if session["no_count"] >= 2:
            session["state"] = "CLOSED"
            session["last_closed_at"] = _utcnow().isoformat()

            log_event(
                conversation_closed_event(
                    user_id=user_id,
                    version=session.get("conversation_version"),
                    reason="user_declined_help"
                )
            )

            if language == "ar":
                out = "شكرًا لك. إذا احتجت أي مساعدة لاحقًا أنا موجود. 🌟"
            else:
                out = "Thank you. If you need any help later, I’m here. 🌟"

            session["last_bot_message"] = out
            session["last_bot_ts"] = _utcnow().isoformat()
            return out, {"state": session["state"]}
    else:
        session["no_count"] = 0

    # --------------------------------------------------
    # Greeting / Thanks / Goodbye
    # --------------------------------------------------
    if intent == "greeting":
        if language == "ar":
            out = "مرحبًا! شكرًا لتواصلك مع SupportPilot. كيف يمكنني مساعدتك اليوم؟"
        else:
            out = "Hello! Thank you for contacting SupportPilot. How may I assist you today?"

        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

    if intent == "thanks":
        if language == "ar":
            out = "على الرحب والسعة. هل هناك أي شيء آخر يمكنني مساعدتك به اليوم؟"
        else:
            out = "You’re most welcome. Is there anything else I can help you with today?"

        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

    if intent == "goodbye":
        session["state"] = "CLOSED"
        session["last_closed_at"] = _utcnow().isoformat()

        log_event(
            conversation_closed_event(
                user_id=user_id,
                version=session.get("conversation_version"),
                reason="user_goodbye"
            )
        )

        if language == "ar":
            out = "مع السلامة! إذا احتجت أي شيء، أنا موجود. ✅"
        else:
            out = "Goodbye! If you need anything else, I’m here. ✅"

        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

# --------------------------------------------------
# Post-resolution confirmation state (prevents looping)
# --------------------------------------------------
if session.get("state") == "AWAITING_CONFIRMATION":
    if _is_thanks(message_text) or _is_ack(message_text) or _is_yes(message_text):
        if language == "ar":
            out = "على الرحب والسعة ✅ هل هناك أي شيء آخر يمكنني مساعدتك به اليوم؟"
        else:
            out = "You’re welcome ✅ Is there anything else I can help you with today?"
        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

    if _is_no(message_text):
        session["state"] = "CLOSED"
        session["last_closed_at"] = _utcnow().isoformat()
        if language == "ar":
            out = "شكرًا لتواصلك معنا. يومك سعيد 🌟"
        else:
            out = "Thank you for contacting us. Have a great day 🌟"
        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}

    # If user wrote a new issue, continue normally:
    session["state"] = "ACTIVE"
    session["tries"] = 0
    session["ai_attempts"] = 0


    # --------------------------------------------------
    # Capture Order ID anytime
    # --------------------------------------------------
    maybe_order_id = _extract_order_id(message_text)
    if maybe_order_id:
        session["order_id"] = maybe_order_id

    # Update issue summary safely
    _safe_set_issue_summary(session, intent, message_text)

    # --------------------------------------------------
    # Order / Delivery delay flow (AI-first, HOLD language)
    # --------------------------------------------------
    if intent == "delivery_delay" or _looks_like_order_issue(session.get("issue_summary", "")):

        # If missing order id, ask (max twice) then escalate
        if not session.get("order_id"):
            session["state"] = "WAITING_ORDER_ID"
            session["asked_order_id_count"] = int(session.get("asked_order_id_count", 0)) + 1

            if session["asked_order_id_count"] <= 2:
                if language == "ar":
                    out = "شكرًا لتوضيح المشكلة. هل يمكنك تزويدي برقم الطلب (Order ID) حتى أتحقق من حالة الطلب؟\nإذا لم يكن متوفرًا، يمكنك مشاركة رقم الجوال أو البريد المسجل."
                else:
                    out = "Thanks for sharing that. Could you please provide your Order ID so I can check the order status?\nIf you don’t have it, you may share your registered phone number or email."

                session["last_bot_message"] = out
                session["last_bot_ts"] = _utcnow().isoformat()
                return out, {"state": session["state"]}

            # after 2 attempts -> escalate
            session["state"] = "ESCALATION"
            reply, meta = _escalate_to_human(
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
                priority=priority,
                decision_rule="order_id_missing_after_2_asks",
                decision_reason="Order-related issue but Order ID not provided after 2 requests",
                extra_context={"issue_summary": session.get("issue_summary", "")},
            )
            session["last_bot_message"] = reply
            session["last_bot_ts"] = _utcnow().isoformat()
            return reply, meta

        # We DO have order id -> DO NOT escalate immediately.
        # Use HOLD language + AI-first resolution from policy docs.
        session["state"] = "ACTIVE"

        if language == "ar":
            hold = f"شكرًا لمشاركة رقم الطلب ({session['order_id']}). يرجى الانتظار لحظة — أنا أراجع التفاصيل الآن."
        else:
            hold = f"Thanks for sharing the Order ID ({session['order_id']}). Please allow me a moment — I’m checking the details now."

        # Build message for internal AI (RAG)
        issue = (session.get("issue_summary") or "").strip()
        user_msg = f"Order ID: {session.get('order_id')}\nIssue: {issue or message_text}\nCustomer message: {message_text}"

        answer = _call_supportpilot_chat(user_msg, language=language)

        # AI returned "need more details" too often -> escalate after a couple tries
        session["ai_attempts"] = int(session.get("ai_attempts", 0)) + 1

        # If answer looks like uncertainty, try one clarifying question before escalation
        uncertain_phrases = [
            "provide a little more information",
            "share a bit more detail",
            "could you please clarify",
            "cannot confidently",
            "not enough information",
        ]
        is_uncertain = any(p in (answer or "").lower() for p in uncertain_phrases)

        if is_uncertain and session["ai_attempts"] <= MAX_AI_ATTEMPTS_BEFORE_ESCALATION:
            if language == "ar":
                out = (
                    f"{hold}\n\n"
                    "حتى أساعدك بشكل أدق، هل المشكلة هي:\n"
                    "1) تأخر في التوصيل\n"
                    "2) تحديث حالة الشحنة\n"
                    "3) مشكلة في المنتج\n"
                    "اختر رقمًا (1/2/3) أو اكتب التفاصيل."
                )
            else:
                out = (
                    f"{hold}\n\n"
                    "To help you accurately, is this about:\n"
                    "1) Late delivery\n"
                    "2) Shipment status update\n"
                    "3) Product issue\n"
                    "Reply with 1/2/3 or share details."
                )

            session["last_bot_message"] = out
            session["last_bot_ts"] = _utcnow().isoformat()
            return out, {"state": session["state"]}

        if is_uncertain and session["ai_attempts"] > MAX_AI_ATTEMPTS_BEFORE_ESCALATION:
            session["state"] = "ESCALATION"
            reply, meta = _escalate_to_human(
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
                priority=priority,
                decision_rule="ai_uncertain_after_attempts",
                decision_reason="AI could not resolve confidently after multiple attempts",
                extra_context={
                    "order_id": session.get("order_id"),
                    "issue_summary": session.get("issue_summary", ""),
                    "last_ai_answer": (answer or "")[:800],
                },
            )
            session["last_bot_message"] = reply
            session["last_bot_ts"] = _utcnow().isoformat()
            return reply, meta

        # Normal: send hold + helpful answer
        out = f"{hold}\n\n{answer}".strip()

        # ✅ prevent "ok/thanks/no" from re-triggering the same order flow
        session["state"] = "AWAITING_CONFIRMATION"

        session["last_bot_message"] = out
        session["last_bot_ts"] = _utcnow().isoformat()
        return out, {"state": session["state"]}


    # --------------------------------------------------
    # Generic fallback (AI-first) with anti-loop
    # --------------------------------------------------
    session["tries"] = int(session.get("tries", 0)) + 1

    if session["tries"] >= 3:
        session["state"] = "ESCALATION"
        reply, meta = _escalate_to_human(
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            priority=priority,
            decision_rule="unclear_after_3_tries",
            decision_reason="User message unclear after 3 attempts",
            extra_context={"last_message": message_text, "issue_summary": session.get("issue_summary", "")},
        )
        session["last_bot_message"] = reply
        session["last_bot_ts"] = _utcnow().isoformat()
        return reply, meta

    # One polite clarification
    if language == "ar":
        out = "شكرًا لرسالتك. لتقديم المساعدة بشكل أفضل، هل يمكنك توضيح التفاصيل أكثر؟ هل الموضوع متعلق بطلب/توصيل/استرجاع/منتج؟"
    else:
        out = "Thank you for your message. To assist you properly, could you please share a bit more detail — is this about an order, delivery, refund/return, or a product issue?"

    session["last_bot_message"] = out
    session["last_bot_ts"] = _utcnow().isoformat()
    return out, {"state": session["state"]}

# whatsapp_controller.py — Part 3/3 (remainder)

# --------------------------------------------------
# Escalation / Ticket Dispatch
# --------------------------------------------------
def _extract_ticket_id(result):
    """
    Tries to extract a usable ticket id from different adapter formats.
    """
    if not isinstance(result, dict):
        return None

    # If vendor_orchestrator returns top-level ticket_id
    if result.get("ticket_id"):
        return result.get("ticket_id")

    # If it returns { "result": {...} }
    inner = result.get("result")
    if isinstance(inner, dict):
        if inner.get("ticket_id"):
            return inner.get("ticket_id")
        if inner.get("id"):
            return inner.get("id")

        # adapters shape: {"status":"created","vendor":"...","ticket":{...}}
        ticket_obj = inner.get("ticket")
        if isinstance(ticket_obj, dict):
            return (
                ticket_obj.get("id")
                or ticket_obj.get("ticket_id")
                or ticket_obj.get("unique_external_id")
            )

    return None


def _escalate_to_human(
    user_id,
    session,
    language,
    text_direction,
    arabic_tone,
    kpi_signals,
    priority,
    decision_rule,
    decision_reason,
    extra_context=None,
):
    if extra_context is None:
        extra_context = {}

    # 🔐 Day 49 — escalation audit
    log_event(
        escalation_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version"),
            reason=decision_reason,
            rule=decision_rule,
            priority=priority[0],
        )
    )

    # Keep both keys (some adapters use reply_language)
    agent_constraints = {
        "no_sensitive_data": True,
        "max_questions": 2,
        "language": language,
        "reply_language": language,
        "language_lock": False,
        "rtl_required": (text_direction == "rtl"),
        "text_direction": text_direction,
    }

    # Base schema from your handoff_builder (do NOT break)
    payload = build_handoff_payload(
        user_id=user_id,
        current_state=session.get("state"),
        last_user_message=session.get("last_user_message"),
        last_intent=session.get("last_intent"),
        decision_rule=decision_rule,
        decision_reason=decision_reason,
        kpi_signals=kpi_signals,
    )

    # Enrich safely (adds fields; doesn't remove existing ones)
    payload.setdefault("meta", {})
    payload["meta"]["language"] = language
    payload["meta"]["text_direction"] = text_direction
    payload["meta"]["conversation_version"] = session.get("conversation_version", 1)
    payload["meta"]["priority_level"] = priority[0]
    payload["meta"]["priority_reason"] = priority[1]

    payload["agent_constraints"] = agent_constraints

    payload.setdefault("conversation", {})
    payload["conversation"].setdefault("context", {})
    payload["conversation"]["context"].update(
        {
            "order_id": session.get("order_id"),
            "issue_summary": session.get("issue_summary", ""),
            "arabic_tone": arabic_tone,
            **(extra_context or {}),
        }
    )

    ticket_id = None
    try:
        routing = route_escalation(payload)
        result = dispatch_ticket(payload, routing)
        ticket_id = _extract_ticket_id(result)

    except Exception as e:
        print("ESCALATION ERROR:", repr(e))
        ticket_id = None

    # After escalation, keep state as ESCALATION (prevents loops)
    session["state"] = "ESCALATION"

    if language == "ar":
        if ticket_id:
            return (
                f"شكرًا لك. سأقوم برفع الطلب للدعم البشري للمراجعة ✅ رقم التذكرة: {ticket_id}",
                {"state": session["state"], "ticket_id": ticket_id},
            )
        return (
            "شكرًا لك. سأقوم برفع الطلب للدعم البشري للمراجعة ✅ وسيتم التواصل معك قريبًا.",
            {"state": session["state"], "ticket_id": None},
        )

    if ticket_id:
        return (
            f"Thanks — I’m escalating this to our support team for further review ✅ Ticket ID: {ticket_id}",
            {"state": session["state"], "ticket_id": ticket_id},
        )

    return (
        "Thanks — I’m escalating this to our support team for further review ✅ They will contact you shortly.",
        {"state": session["state"], "ticket_id": None},
    )

