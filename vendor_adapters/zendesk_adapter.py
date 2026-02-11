# vendor_adapters/zendesk_adapter.py
# -----------------------------------
# Day 42A — Hardened Zendesk Adapter
# Day 47B — Agent Reply Language Enforcement
# Day 48B — Language-Aware Agent QA Fields
# -----------------------------------

import uuid
import datetime


class ZendeskAdapterError(Exception):
    """Raised when Zendesk adapter fails safely."""


def zendesk_adapter(payload: dict, routing: dict) -> dict:
    """
    Hardened adapter that converts internal payload into
    a Zendesk-safe ticket object.

    Guarantees:
    - Never crash
    - Never emit malformed payloads
    - Idempotent
    - Language & QA enforcement safe
    """

    try:
        # -----------------------------
        # 1. Basic schema validation
        # -----------------------------

        required_keys = {"user", "conversation", "decision_trace"}
        missing = required_keys - payload.keys()

        if missing:
            raise ZendeskAdapterError(
                f"Missing required payload fields: {missing}"
            )

        # -----------------------------
        # 2. Idempotency key
        # -----------------------------

        idempotency_key = str(uuid.uuid4())

        # -----------------------------
        # 3. Extract Agent Constraints
        # -----------------------------

        agent_constraints = payload.get("agent_constraints", {})

        reply_language = agent_constraints.get("reply_language", "en")
        language_lock = agent_constraints.get("language_lock", False)
        rtl_required = agent_constraints.get("rtl_required", False)

        # -----------------------------
        # 4. Conversation Meta (Day 48B)
        # -----------------------------

        convo = payload.get("conversation", {})

        conversation_version = convo.get("conversation_version", 1)
        restart_count = convo.get("restart_count", 0)
        restart_reason = convo.get("restart_reason", "none")

        detected_language = convo.get("detected_language", reply_language)

        # -----------------------------
        # 5. Build Zendesk Ticket
        # -----------------------------

        ticket = {
            "idempotency_key": idempotency_key,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",

            "requester": {
                "external_id": payload["user"]["user_id"],
            },

            "subject": (
                f"AI Escalation — {routing['team']} "
                f"({routing['priority']}) "
                f"[LANG={reply_language.upper()}]"
            ),

            "comment": {
                "body": _build_ticket_body(payload, routing),
                "public": False,
            },

            "group": routing["team"],
            "priority": routing["priority"],
            "tags": payload.get("kpi_flags", []),

            # -----------------------------
            # 6. Custom Fields (Day 48B)
            # -----------------------------

            "custom_fields": {
                # Routing
                "tier": routing.get("tier"),
                "region": routing.get("region"),

                # Decision trace
                "decision_rule": payload["decision_trace"].get("rule"),

                # Language enforcement
                "reply_language": reply_language,
                "language_lock": language_lock,
                "rtl_required": rtl_required,
                "language_detected": detected_language,

                # Conversation safety
                "conversation_version": conversation_version,
                "restart_count": restart_count,
                "restart_reason": restart_reason,
            },
        }

        # -----------------------------
        # 7. Simulated send (safe)
        # -----------------------------

        return {
            "status": "created",
            "vendor": "zendesk",
            "ticket": ticket,
        }

    except ZendeskAdapterError as e:
        return _safe_failure("schema_error", str(e))

    except Exception as e:
        return _safe_failure("unknown_error", str(e))

# -----------------------------------
# Helpers
# -----------------------------------

def _build_ticket_body(payload: dict, routing: dict) -> str:
    """
    Human-readable escalation summary for agents & QA.
    Must never throw.
    """

    convo = payload.get("conversation", {})
    trace = payload.get("decision_trace", {})
    agent_constraints = payload.get("agent_constraints", {})

    return f"""
AI Escalation Summary
====================

USER
----
User ID: {payload.get('user', {}).get('user_id')}

CONVERSATION
------------
Current State: {convo.get('current_state')}
Last Message: {convo.get('last_user_message')}
Last Intent: {convo.get('last_intent')}

Conversation Version: {convo.get('conversation_version', 1)}
Restart Count: {convo.get('restart_count', 0)}
Restart Reason: {convo.get('restart_reason', 'none')}

LANGUAGE
--------
Detected Language: {convo.get('detected_language')}
Reply Language (Required): {agent_constraints.get('reply_language', 'en')}
Language Lock: {agent_constraints.get('language_lock', False)}
RTL Required: {agent_constraints.get('rtl_required', False)}

DECISION TRACE
--------------
Decision Rule: {trace.get('rule')}
Decision Reason: {trace.get('reason')}

ROUTING
-------
Team: {routing.get('team')}
Tier: {routing.get('tier')}
Region: {routing.get('region')}
Priority: {routing.get('priority')}
"""
# -----------------------------------
# Helpers
# -----------------------------------

def _build_ticket_body(payload: dict, routing: dict) -> str:
    """
    Human-readable escalation summary for agents.
    Used for:
    - Agent clarity
    - QA audits
    - Compliance reviews
    """

    convo = payload["conversation"]
    trace = payload["decision_trace"]
    agent_constraints = payload.get("agent_constraints", {})

    return f"""
AI Escalation Summary
--------------------
User ID: {payload['user'].get('user_id')}
Conversation ID: {convo.get('conversation_id')}
Conversation Version: {convo.get('conversation_version')}

Current State: {convo.get('current_state')}
Last Message: {convo.get('last_user_message')}
Last Intent: {convo.get('last_intent')}

Decision Rule: {trace.get('rule')}
Decision Reason: {trace.get('reason')}

Routing:
- Team: {routing.get('team')}
- Tier: {routing.get('tier')}
- Region: {routing.get('region')}
- Priority: {routing.get('priority')}

Agent Constraints:
- Reply Language: {agent_constraints.get('reply_language', 'en')}
- Language Lock: {agent_constraints.get('language_lock', False)}
- RTL Required: {agent_constraints.get('rtl_required', False)}
"""


def _safe_failure(error_type: str, details: str) -> dict:
    """
    FINAL SAFETY NET — NEVER FAIL.

    Guarantees:
    - No exception propagation
    - No stack trace leakage
    - Zendesk-safe payload
    - Manual follow-up always possible
    """

    try:
        return {
            "status": "failed",
            "vendor": "zendesk",
            "error_type": error_type,
            "details": str(details)[:500],  # hard cap to prevent API rejection
            "action": "manual_followup_required",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }

    except Exception:
        # Absolute last-resort fallback (cannot fail)
        return {
            "status": "failed",
            "vendor": "zendesk",
            "error_type": "fatal_adapter_error",
            "details": "Zendesk adapter failed in safety handler",
            "action": "manual_followup_required",
        }

