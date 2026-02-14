# --------------------------------------------------
# WhatsApp Controller
# Day 47A — Conversation Versioning & Safe Restart
# Day 49  — Compliance & Audit Logging Layer
# --------------------------------------------------

from datetime import datetime, timedelta

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone

# --------------------------------------------------
# 🔐 Day 49 — Compliance Audit Layer
# --------------------------------------------------
from compliance.audit_logger import log_event
from compliance.audit_events import (
    conversation_restart_event,
    conversation_closed_event,
    escalation_event,
    sla_breach_event,
    incident_mode_event
)

from profiles.user_profile_store import (
    get_preferred_language,
    set_language_preference
)

from vendor_orchestrator import dispatch_ticket
from incident.incident_state import is_incident_mode


# --------------------------------------------------
# In-memory session store
# (Production: Redis / DB with TTL)
# --------------------------------------------------

sessions = {}


# --------------------------------------------------
# Session Management
# ✔ Safe restart
# ✔ Conversation versioning
# ✔ Restart analytics
# ✔ Day 49 — Restart audit logging
# --------------------------------------------------

def get_or_create_session(user_id):
    now = datetime.utcnow()

    if user_id not in sessions:
        sessions[user_id] = {
            "state": "ACTIVE",
            "tries": 0,
            "last_intent": None,
            "last_user_message": None,

            # -----------------------------
            # Day 47A — Versioning
            # -----------------------------
            "conversation_version": 1,
            "restart_count": 0,
            "restart_reason": None,
            "last_closed_at": None,

            # -----------------------------
            # Language handling
            # -----------------------------
            "language": None,
            "text_direction": "ltr",

            "created_at": now.isoformat()
        }
        return sessions[user_id]

    session = sessions[user_id]

    # --------------------------------------------------
    # ✅ SAFE RESTART DETECTION
    # --------------------------------------------------
    if session["state"] == "CLOSED":

        session["conversation_version"] += 1
        session["restart_count"] += 1
        session["restart_reason"] = "user_return"

        session["state"] = "ACTIVE"
        session["tries"] = 0
        session["last_intent"] = None

        # --------------------------------------------------
        # 🔐 Day 49 — Restart Audit Logging
        # --------------------------------------------------
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
# KPI Signal Utilities
# --------------------------------------------------

def collect_restart_kpis(session, kpi_signals):
    """
    Analytics-only KPIs (do NOT affect logic directly)
    """
    if session.get("restart_count", 0) > 0:
        kpi_signals.append("restart_after_close")

        if session["restart_count"] >= 3:
            kpi_signals.append("frequent_restarts")


# --------------------------------------------------
# Priority Engine (Day 43B + Day 47A + Day 49)
# --------------------------------------------------

def get_customer_priority(user_id, session, kpi_signals):
    """
    Returns (priority_level, reason)
    """

    # 🔥 VIP always highest
    if user_id.startswith("vip_"):
        return "P0", "VIP customer"

    # 🔥 SLA breach escalation
    if "sla_breach_detected" in kpi_signals:

        # 🔐 Day 49 — Log SLA breach
        log_event(
            sla_breach_event(
                user_id=user_id,
                conversation_version=session.get("conversation_version")
            )
        )

        return "P0", "SLA breach detected"

    # 🔁 Restart-based boost
    if session.get("restart_count", 0) >= 3:
        return "P1", "Frequent restarts detected"

    # 🚨 Escalation state
    if session.get("state") == "ESCALATION":
        return "P1", "Auto escalation"

    return "P2", "Standard customer"
# --------------------------------------------------
# Handoff Payload Builder
# ✔ Language
# ✔ RTL support
# ✔ Versioning
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
        "user": {
            "user_id": user_id
        },
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
# Language Resolution
# --------------------------------------------------

def resolve_language(user_id, session, message):

    preferred = get_preferred_language(user_id)
    if preferred:
        session["language"] = preferred
        session["text_direction"] = "rtl" if preferred == "ar" else "ltr"
        return preferred

    if session.get("language"):
        return session["language"]

    if detect_arabic_intent(message):
        session["language"] = "ar"
        session["text_direction"] = "rtl"
        set_language_preference(user_id, "ar")
        return "ar"

    detected = detect_language(message)
    session["language"] = detected
    session["text_direction"] = "rtl" if detected == "ar" else "ltr"
    set_language_preference(user_id, detected)
    return detected


# --------------------------------------------------
# Arabic Tone Resolver
# --------------------------------------------------

def resolve_arabic_tone(lang, session):
    if lang != "ar":
        return None

    return select_arabic_tone(
        user_region=session.get("region", "KSA"),
        business_context="support"
    )


# --------------------------------------------------
# Incident Mode Guard (Day 49 audit)
# --------------------------------------------------

def handle_incident_mode(user_id, session, lang):

    log_event(
        incident_mode_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version")
        )
    )

    return (
        "نواجه حالياً ضغطاً عالياً على النظام. تم تسجيل طلبك وسيتم التعامل معه قريباً."
        if lang == "ar"
        else
        "We are currently experiencing high system load. "
        "Your request has been recorded and will be handled shortly."
    )


# --------------------------------------------------
# Conversation Closed Audit (Day 49)
# --------------------------------------------------

def audit_conversation_closed(user_id, session):

    log_event(
        conversation_closed_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version"),
            restart_count=session.get("restart_count", 0)
        )
    )


# --------------------------------------------------
# Escalation Audit (Day 49)
# --------------------------------------------------

def audit_escalation_created(user_id, session, priority):

    log_event(
        escalation_created_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version"),
            priority_level=priority[0]
        )
    )
# --------------------------------------------------
# Main WhatsApp Message Handler
# Day 47A + Day 48B + Day 49
# --------------------------------------------------

def handle_message(user_id, message):

    session = get_or_create_session(user_id)
    session["last_user_message"] = message

    text = message.strip()
    text_lower = text.lower()

    # --------------------------------------------------
    # KPI Signals
    # --------------------------------------------------

    kpi_signals = []
    collect_restart_kpis(session, kpi_signals)

    # --------------------------------------------------
    # Language Resolution
    # --------------------------------------------------

    lang = resolve_language(user_id, session, text)
    arabic_tone = resolve_arabic_tone(lang, session)

    # --------------------------------------------------
    # Incident Mode Guard (Audited)
    # --------------------------------------------------

    if is_incident_mode():

        log_event(
            event_type="incident_mode_triggered",
            user_id=user_id,
            metadata={
                "conversation_version": session["conversation_version"],
                "language": lang
            }
        )

        return RESPONSES["incident"][lang]

    # --------------------------------------------------
    # Greetings
    # --------------------------------------------------

    if text_lower in ["hi", "hello", "hey", "مرحبا", "السلام عليكم"]:
        session["last_intent"] = "greeting"
        return RESPONSES["greeting"][lang]

    # --------------------------------------------------
    # Delivery Issue Intent
    # --------------------------------------------------

    if "delivery" in text_lower or "توصيل" in text_lower:
        session["last_intent"] = "delivery_issue"
        return RESPONSES["ask_order"][lang]

    # --------------------------------------------------
    # Order ID Provided
    # --------------------------------------------------

    if text_lower.startswith("order") or "طلب" in text_lower:
        session["last_intent"] = "order_id_provided"
        return RESPONSES["order_received"][lang]

    # --------------------------------------------------
    # Acknowledgment
    # --------------------------------------------------

    if text_lower in ["ok", "okay", "yes", "نعم", "تمام"]:
        return RESPONSES["acknowledged"][lang]

    # --------------------------------------------------
    # User Ends Conversation
    # --------------------------------------------------

    if text_lower in ["no", "nothing", "thanks", "thank you", "لا", "شكرا"]:

        session["state"] = "CLOSED"
        session["last_closed_at"] = datetime.utcnow().isoformat()

        kpi_signals.append("conversation_closed")

        priority = get_customer_priority(user_id, session, kpi_signals)

        agent_constraints = {
            "reply_language": lang,
            "language_lock": True,
            "arabic_tone": arabic_tone if lang == "ar" else None,
            "rtl_required": session["text_direction"] == "rtl"
        }

        payload = build_handoff_payload(
            user_id=user_id,
            current_state="CLOSED",
            last_user_message="redacted_for_privacy",
            last_intent=session.get("last_intent"),
            decision_rule="USER_DONE",
            decision_reason="User confirmed no further assistance needed",
            kpi_signals=kpi_signals,
            priority=priority,
            language=lang,
            text_direction=session["text_direction"],
            arabic_tone=arabic_tone,
            agent_constraints=agent_constraints
        )

        # Audit conversation closed (no raw text logged)
        log_event(
            event_type="conversation_closed",
            user_id=user_id,
            metadata={
                "conversation_version": session["conversation_version"],
                "priority": priority[0],
                "language": lang
            }
        )

        return RESPONSES["closed"][lang]

    # --------------------------------------------------
    # Retry → Escalation Logic
    # --------------------------------------------------

    session["tries"] += 1

    if session["tries"] >= 3:

        session["state"] = "ESCALATION"
        kpi_signals.extend(["auto_escalation", "sla_breach_detected"])

        priority = get_customer_priority(user_id, session, kpi_signals)

        agent_constraints = {
            "reply_language": lang,
            "language_lock": True,
            "arabic_tone": arabic_tone if lang == "ar" else None,
            "rtl_required": session["text_direction"] == "rtl"
        }

        payload = build_handoff_payload(
            user_id=user_id,
            current_state="ESCALATION",
            last_user_message="redacted_for_privacy",
            last_intent=session.get("last_intent"),
            decision_rule="MAX_RETRIES",
            decision_reason="User stuck after multiple attempts",
            kpi_signals=kpi_signals,
            priority=priority,
            language=lang,
            text_direction=session["text_direction"],
            arabic_tone=arabic_tone,
            agent_constraints=agent_constraints
        )

        routing = {
            "team": "tier_2",
            "region": "GCC",
            "priority": priority[0],
            "language": lang,
            "business_hours_only": True
        }

        dispatch_result = dispatch_ticket(payload, routing)

        # -----------------------------
        # Compliance Audit (NO RAW TEXT)
        # -----------------------------

        log_event(
            event_type="ticket_escalated",
            user_id=user_id,
            metadata={
                "conversation_version": session["conversation_version"],
                "priority": priority[0],
                "language": lang,
                "vendor_status": dispatch_result.get("status")
            }
        )

        return RESPONSES["handoff"][lang]

    # --------------------------------------------------
    # Safe Fallback
    # --------------------------------------------------

    return RESPONSES["fallback"][lang]

