# WhatsApp Controller
# Day 47A — Conversation Versioning & Safe Restart
# Day 49  — Compliance & Audit Logging Layer
# --------------------------------------------------

from datetime import datetime
import re

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone
from escalation_router import route_escalation

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
# In-memory session store
# (Production later: move to Redis/Postgres)
# --------------------------------------------------
sessions = {}

# --------------------------------------------------
# Session Management
# --------------------------------------------------
def get_or_create_session(user_id):
    now = datetime.utcnow()

    if user_id not in sessions:
        sessions[user_id] = {
            "state": "ACTIVE",
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

            "created_at": now.isoformat()
        }
        return sessions[user_id]

    session = sessions[user_id]

    # Safe restart after CLOSED
    if session["state"] == "CLOSED":
        session["conversation_version"] += 1
        session["restart_count"] += 1
        session["restart_reason"] = "user_return"

        session["state"] = "ACTIVE"
        session["tries"] = 0
        session["last_intent"] = None
        session["order_id"] = None
        session["asked_order_id_count"] = 0
        session["no_count"] = 0

        log_event(
            conversation_restart_event(
                user_id=user_id,
                version=session["conversation_version"],
                restart_count=session["restart_count"],
                restart_reason=session["restart_reason"]
            )
        )

    return session

# --------------------------------------------------
# KPI utilities
# --------------------------------------------------
def collect_restart_kpis(session, kpi_signals):
    if session.get("restart_count", 0) > 0:
        kpi_signals.append("restart_after_close")

        if session["restart_count"] >= 3:
            kpi_signals.append("frequent_restarts")

# --------------------------------------------------
# Priority Engine
# --------------------------------------------------
def get_customer_priority(user_id, session, kpi_signals):

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

    if session.get("restart_count", 0) >= 3:
        return "P1", "Frequent restarts detected"

    if session.get("state") == "ESCALATION":
        return "P1", "Auto escalation"

    return "P2", "Standard customer"

# --------------------------------------------------
# Handoff payload builder
# --------------------------------------------------
def build_handoff_payload(
    user_id,
    current_state,
    last_user_message,
    last_intent,
    decision_rule,
    decision_reason,
    kpi_signals,
    priority,
    language,
    text_direction,
    arabic_tone,
    agent_constraints
):
    return {
        "meta": {
            "source": "SupportPilot-AI",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "language": language,
            "text_direction": text_direction,
            "conversation_version": sessions[user_id]["conversation_version"]
        },
        "agent_constraints": agent_constraints,
        "user": {"user_id": user_id},
        "conversation": {
            "current_state": current_state,
            "last_user_message": last_user_message,
            "last_intent": last_intent
        },
        "decision_trace": {
            "rule": decision_rule,
            "reason": decision_reason
        },
        "kpi_flags": kpi_signals,
        "priority": {
            "level": priority[0],
            "reason": priority[1]
        },
        "language_context": {
            "language": language,
            "text_direction": text_direction,
            "arabic_tone": arabic_tone
        }
    }

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

def _detect_intent(text: str):
    """
    Minimal intent detection for the order flow.
    (Your Day 57 intent engine can replace this later.)
    """
    t = _norm(text)
    if _is_greeting(t):
        return "greeting"
    if _is_thanks(t):
        return "thanks"
    if _is_goodbye(t):
        return "goodbye"

    # delivery/order delay (English)
    if any(k in t for k in ["delivery", "late", "delayed", "where is my order", "order delayed", "my order delayed"]):
        return "delivery_delay"

    # delivery/order delay (Arabic)
    if any(k in t for k in ["طلب", "طلبي", "متأخر", "تأخير", "وين الطلب", "توصيل", "الشحنة", "تأخر التوصيل"]):
        return "delivery_delay"

    return "other"

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
    # Loop protection on repeated "No"
    # --------------------------------------------------
    if _is_no(message_text):
        session["no_count"] = int(session.get("no_count", 0)) + 1
        if session["no_count"] >= 2:
            session["state"] = "CLOSED"
            session["last_closed_at"] = datetime.utcnow().isoformat()

            log_event(
                conversation_closed_event(
                    user_id=user_id,
                    version=session.get("conversation_version"),
                    reason="user_declined_help"
                )
            )

            if language == "ar":
                return "شكرًا لك. إذا احتجت أي مساعدة لاحقًا أنا موجود. 🌟", {"state": session["state"]}
            return "Thank you. If you need any help later, I’m here. 🌟", {"state": session["state"]}
    else:
        session["no_count"] = 0

    # --------------------------------------------------
    # Greeting / Thanks / Goodbye
    # --------------------------------------------------
    if intent == "greeting":
        if language == "ar":
            return "مرحبًا! شكرًا لتواصلك مع SupportPilot. كيف يمكنني مساعدتك اليوم؟", {"state": session["state"]}
        return "Hello! Thank you for contacting SupportPilot. How may I assist you today?", {"state": session["state"]}

    if intent == "thanks":
        if language == "ar":
            return "على الرحب والسعة. هل هناك أي شيء آخر يمكنني مساعدتك به اليوم؟", {"state": session["state"]}
        return "You’re most welcome. Is there anything else I can help you with today?", {"state": session["state"]}

    if intent == "goodbye":
        session["state"] = "CLOSED"
        session["last_closed_at"] = datetime.utcnow().isoformat()

        log_event(
            conversation_closed_event(
                user_id=user_id,
                version=session.get("conversation_version"),
                reason="user_goodbye"
            )
        )

        if language == "ar":
            return "مع السلامة! إذا احتجت أي شيء، أنا موجود. ✅", {"state": session["state"]}
        return "Goodbye! If you need anything else, I’m here. ✅", {"state": session["state"]}

    # --------------------------------------------------
    # Order / Delivery delay flow
    # --------------------------------------------------
    maybe_order_id = _extract_order_id(message_text)
    if maybe_order_id:
        session["order_id"] = maybe_order_id

    if intent == "delivery_delay" or session.get("state") == "WAITING_ORDER_ID":
        if not session.get("order_id"):
            session["state"] = "WAITING_ORDER_ID"
            session["asked_order_id_count"] = int(session.get("asked_order_id_count", 0)) + 1

            # Ask order ID at most twice, then escalate
            if session["asked_order_id_count"] <= 2:
                if language == "ar":
                    return "أقدر ذلك. هل يمكنك تزويدي برقم الطلب (Order ID) حتى أتحقق من حالة الطلب؟", {"state": session["state"]}
                return "I can help you with that. Could you please provide your Order ID so I can check the status?", {"state": session["state"]}

            # Escalate after 2 fails
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
                decision_reason="Order ID not provided after 2 requests",
            )
            return reply, meta

        # We have order id now
        session["state"] = "ACTIVE"
        oid = session.get("order_id")

        reply, meta = _escalate_to_human(
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            priority=priority,
            decision_rule="order_delay_with_order_id",
            decision_reason=f"Delivery delay reported, order_id captured: {oid}",
            extra_context={"order_id": oid},
        )
        return reply, meta

    # --------------------------------------------------
    # Fallback: do NOT loop endlessly
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
        )
        return reply, meta

    if language == "ar":
        return "شكرًا لرسالتك. لتقديم المساعدة بشكل أفضل، هل يمكنك توضيح التفاصيل أكثر؟", {"state": session["state"]}
    return "Thank you. To assist you properly, could you please provide a little more information about your request?", {"state": session["state"]}

# --------------------------------------------------
# Escalation / Ticket Dispatch
# --------------------------------------------------

def _extract_ticket_id(result: dict | None):
    """
    Tries to extract a usable ticket id from different adapter formats.
    """
    if not isinstance(result, dict):
        return None

    # If vendor_orchestrator returns top-level ticket_id
    if result.get("ticket_id"):
        return result.get("ticket_id")

    # If it returns { "result": { "ticket": {...}} } or { "result": {...} }
    inner = result.get("result")
    if isinstance(inner, dict):
        if inner.get("ticket_id"):
            return inner.get("ticket_id")
        if inner.get("id"):
            return inner.get("id")

        # adapters you shared return: {"status":"created","vendor":"...","ticket":{...}}
        ticket_obj = inner.get("ticket")
        if isinstance(ticket_obj, dict):
            return ticket_obj.get("id") or ticket_obj.get("ticket_id") or ticket_obj.get("unique_external_id")

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

    agent_constraints = {
        "no_sensitive_data": True,
        "max_questions": 2,

        # keep both keys (some adapters use reply_language)
        "language": language,
        "reply_language": language,

        "language_lock": False,
        "rtl_required": (text_direction == "rtl"),
        "text_direction": text_direction,
    }

    payload = build_handoff_payload(
        user_id=user_id,
        current_state=session.get("state"),
        last_user_message=session.get("last_user_message"),
        last_intent=session.get("last_intent"),
        decision_rule=decision_rule,
        decision_reason=decision_reason,
        kpi_signals=kpi_signals,
        priority=priority,
        language=language,
        text_direction=text_direction,
        arabic_tone=arabic_tone,
        agent_constraints=agent_constraints
    )

    # attach extra info like order_id
    if extra_context:
        payload.setdefault("conversation", {})
        payload["conversation"]["context"] = extra_context

    ticket_id = None

    try:
        # ✅ Routing metadata
        routing = route_escalation(payload)

        # ✅ Send to vendor orchestrator
        result = dispatch_ticket(payload, routing)

        # ✅ Extract ticket id (works across your adapter shapes)
        ticket_id = _extract_ticket_id(result)

    except Exception as e:
        print("ESCALATION ERROR:", repr(e))
        ticket_id = None

    # After escalation, keep state as ESCALATION (prevents loops)
    session["state"] = "ESCALATION"

    if language == "ar":
        if ticket_id:
            return f"تم رفع طلبك للدعم البشري ✅ رقم التذكرة: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id}
        return "تم رفع طلبك للدعم البشري ✅ وسيتم التواصل معك قريبًا.", {"state": session["state"], "ticket_id": None}

    if ticket_id:
        return f"I’ve escalated this to a human agent ✅ Ticket ID: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id}
    return "I’ve escalated this to a human agent ✅ They will contact you shortly.", {"state": session["state"], "ticket_id": None}
