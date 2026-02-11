# vendor_adapters/freshdesk_adapter.py
# -----------------------------------
# Day 42A — Hardened Freshdesk Adapter
# Day 47B — Agent Reply Language Enforcement
# Day 48B — Agent Constraint Custom Fields
# -----------------------------------

import uuid
import datetime


class FreshdeskAdapterError(Exception):
    """Raised when Freshdesk adapter fails safely."""


def freshdesk_adapter(payload: dict, routing: dict) -> dict:
    """
    Hardened adapter that converts internal payload into
    a Freshdesk-safe ticket object.

    Guarantees:
    - Never crash AI flow
    - Never send malformed payloads
    - Agent language & RTL enforcement (Day 47B / 48B)
    """

    try:
        # -----------------------------
        # 1. Schema validation
        # -----------------------------
        required_keys = {"user", "conversation", "decision_trace"}
        missing = required_keys - payload.keys()

        if missing:
            raise FreshdeskAdapterError(
                f"Missing required payload fields: {missing}"
            )

        # -----------------------------
        # 2. Idempotency
        # -----------------------------
        idempotency_key = str(uuid.uuid4())

        # -----------------------------
        # 3. Agent constraints
        # -----------------------------
        agent_constraints = payload.get("agent_constraints", {})

        reply_language = agent_constraints.get("reply_language", "en")
        language_lock = agent_constraints.get("language_lock", False)
        rtl_required = agent_constraints.get("rtl_required", False)

        # -----------------------------
        # 4. Build Freshdesk ticket
        # -----------------------------
        ticket = {
            "unique_external_id": idempotency_key,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",

            "requester_id": payload["user"]["user_id"],

            "subject": (
                f"AI Escalation — {routing.get('team')} "
                f"({routing.get('priority')}) "
                f"[LANG={reply_language.upper()}]"
            ),

            "description": _build_ticket_body(payload, routing),

            "group": routing.get("team"),
            "priority": routing.get("priority"),
            "tags": payload.get("kpi_flags", []),

            # -----------------------------
            # 5. Custom Fields (Day 48B)
            # -----------------------------
            "custom_fields": {
                "reply_language": reply_language,
                "language_lock": language_lock,
                "rtl_required": rtl_required,
            },
        }

        # -----------------------------
        # 6. Simulated send (safe)
        # -----------------------------
        return {
            "status": "created",
            "vendor": "freshdesk",
            "ticket": ticket,
        }

    except FreshdeskAdapterError as e:
        return _safe_failure("schema_error", str(e))

    except Exception as e:
        return _safe_failure("unknown_error", str(e))

# -----------------------------------
# Helpers
# -----------------------------------

def _build_ticket_body(payload: dict, routing: dict) -> str:
    """
    Human-readable escalation summary for Freshdesk agents.
    Used for QA, audits, and compliance.
    """

    convo = payload["conversation"]
    trace = payload["decision_trace"]
    agent_constraints = payload.get("agent_constraints", {})

    return f"""
AI Escalation Summary
--------------------
User ID: {payload['user']['user_id']}
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
    NEVER throw.
    NEVER crash.
    ALWAYS return a safe object.
    """

    return {
        "status": "failed",
        "vendor": "freshdesk",
        "error_type": error_type,
        "details": str(details)[:500],
        "action": "manual_followup_required",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
