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
    Hardened adapter that converts internal payload into a Freshdesk-safe ticket object.

    Return contract (aligned with Zendesk):
    - On success: {status, vendor, ticket_id, ticket}
    - On failure: {status, vendor, error_type, details, action, created_at}
    """
    try:
        # 1) Schema validation
        required_keys = {"user", "conversation", "decision_trace"}
        missing = required_keys - set(payload.keys())
        if missing:
            raise FreshdeskAdapterError(f"Missing required payload fields: {missing}")

        # 2) ticket id (idempotency)
        ticket_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat() + "Z"

        # 3) Agent constraints
        agent_constraints = payload.get("agent_constraints", {}) or {}
        reply_language = agent_constraints.get("reply_language", "en")
        language_lock = bool(agent_constraints.get("language_lock", False))
        rtl_required = bool(agent_constraints.get("rtl_required", False))

        # 4) Build Freshdesk ticket (safe shape)
        ticket = {
            "unique_external_id": ticket_id,
            "created_at": created_at,
            "requester_id": payload["user"]["user_id"],
            "subject": (
                f"AI Escalation — {routing.get('team')} "
                f"({routing.get('priority')}) "
                f"[LANG={str(reply_language).upper()}]"
            ),
            "description": _build_ticket_body(payload, routing),
            "group": routing.get("team"),
            "priority": routing.get("priority"),
            "tags": payload.get("kpi_flags", []),
            "custom_fields": {
                # routing
                "tier": routing.get("tier"),
                "region": routing.get("region"),
                # decision
                "decision_rule": (payload.get("decision_trace", {}) or {}).get("rule"),
                # language enforcement
                "reply_language": reply_language,
                "language_lock": language_lock,
                "rtl_required": rtl_required,
            },
        }

        # 5) Simulated send (safe)
        return {
            "status": "created",
            "vendor": "freshdesk",
            "ticket_id": ticket_id,
            "ticket": ticket,
        }

    except FreshdeskAdapterError as e:
        return _safe_failure("schema_error", str(e))
    except Exception as e:
        return _safe_failure("unknown_error", str(e))


def _build_ticket_body(payload: dict, routing: dict) -> str:
    """
    Human-readable escalation summary for Freshdesk agents.
    Must never throw.
    """
    try:
        convo = payload.get("conversation", {}) or {}
        trace = payload.get("decision_trace", {}) or {}
        agent_constraints = payload.get("agent_constraints", {}) or {}

        return f"""
AI Escalation Summary
--------------------
User ID: {payload.get('user', {}).get('user_id')}
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
""".strip()
    except Exception:
        return "AI Escalation Summary (failed to render details safely)."


def _safe_failure(error_type: str, details: str) -> dict:
    """
    NEVER throw. ALWAYS return a safe object.
    """
    return {
        "status": "failed",
        "vendor": "freshdesk",
        "error_type": error_type,
        "details": str(details)[:500],
        "action": "manual_followup_required",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
