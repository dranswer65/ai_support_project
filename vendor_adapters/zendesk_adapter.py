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

    Return contract (aligned):
    - On success: {status, vendor, ticket_id, ticket}
    - On failure: {status, vendor, error_type, details, action, created_at}
    """
    try:
        # 1) Basic schema validation
        required_keys = {"user", "conversation", "decision_trace"}
        missing = required_keys - set(payload.keys())
        if missing:
            raise ZendeskAdapterError(f"Missing required payload fields: {missing}")

        # 2) ticket id (idempotency)
        ticket_id = str(uuid.uuid4())
        created_at = datetime.datetime.utcnow().isoformat() + "Z"

        # 3) Agent constraints
        agent_constraints = payload.get("agent_constraints", {}) or {}
        reply_language = agent_constraints.get("reply_language", "en")
        language_lock = bool(agent_constraints.get("language_lock", False))
        rtl_required = bool(agent_constraints.get("rtl_required", False))

        # 4) Conversation meta
        convo = payload.get("conversation", {}) or {}
        conversation_version = convo.get("conversation_version", 1)
        restart_count = convo.get("restart_count", 0)
        restart_reason = convo.get("restart_reason", "none")
        detected_language = convo.get("detected_language", reply_language)

        # 5) Ticket
        ticket = {
            "idempotency_key": ticket_id,
            "created_at": created_at,
            "requester": {"external_id": payload["user"]["user_id"]},
            "subject": (
                f"AI Escalation — {routing.get('team')} "
                f"({routing.get('priority')}) "
                f"[LANG={str(reply_language).upper()}]"
            ),
            "comment": {
                "body": _build_ticket_body(payload, routing),
                "public": False,
            },
            "group": routing.get("team"),
            "priority": routing.get("priority"),
            "tags": payload.get("kpi_flags", []),
            "custom_fields": {
                # Routing
                "tier": routing.get("tier"),
                "region": routing.get("region"),
                # Decision trace
                "decision_rule": (payload.get("decision_trace", {}) or {}).get("rule"),
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

        # 6) Simulated send
        return {
            "status": "created",
            "vendor": "zendesk",
            "ticket_id": ticket_id,
            "ticket": ticket,
        }

    except ZendeskAdapterError as e:
        return _safe_failure("schema_error", str(e))
    except Exception as e:
        return _safe_failure("unknown_error", str(e))


def _build_ticket_body(payload: dict, routing: dict) -> str:
    """
    Human-readable escalation summary for agents & QA.
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

Conversation Version: {convo.get('conversation_version', 1)}
Restart Count: {convo.get('restart_count', 0)}
Restart Reason: {convo.get('restart_reason', 'none')}

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
    FINAL SAFETY NET — NEVER FAIL.
    """
    try:
        return {
            "status": "failed",
            "vendor": "zendesk",
            "error_type": error_type,
            "details": str(details)[:500],
            "action": "manual_followup_required",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
    except Exception:
        return {
            "status": "failed",
            "vendor": "zendesk",
            "error_type": "fatal_adapter_error",
            "details": "Zendesk adapter failed in safety handler",
            "action": "manual_followup_required",
        }
