# Vendor Adapter Layer
# Day 40C
# -----------------------------------

def zendesk_adapter(handoff_payload: dict) -> dict:
    """
    Converts internal payload to Zendesk ticket format
    """
    return {
        "ticket": {
            "subject": "AI Escalation – Customer Support",
            "comment": {
                "body": _format_conversation(handoff_payload)
            },
            "priority": _priority_from_kpi(handoff_payload),
            "tags": handoff_payload.get("kpi_flags", []),
        }
    }


def freshdesk_adapter(handoff_payload: dict) -> dict:
    """
    Converts internal payload to Freshdesk ticket format
    """
    return {
        "subject": "AI Escalation – SupportPilot",
        "description": _format_conversation(handoff_payload),
        "priority": _priority_from_kpi(handoff_payload),
        "source": 2,  # API
    }


def intercom_adapter(handoff_payload: dict) -> dict:
    """
    Converts internal payload to Intercom conversation format
    """
    return {
        "from": {
            "type": "user",
            "id": handoff_payload["user"]["user_id"],
        },
        "body": _format_conversation(handoff_payload),
        "tags": handoff_payload.get("kpi_flags", []),
    }


# -----------------------------------
# Internal helpers
# -----------------------------------

def _format_conversation(payload: dict) -> str:
    convo = payload["conversation"]
    decision = payload["decision_trace"]

    return (
        f"Last message: {convo['last_user_message']}\n"
        f"Last intent: {convo['last_intent']}\n\n"
        f"Decision rule: {decision['rule']}\n"
        f"Reason: {decision['reason']}\n"
    )


def _priority_from_kpi(payload: dict) -> str:
    flags = payload.get("kpi_flags", [])

    if "retry_exhausted" in flags or "escalated" in flags:
        return "high"
    if "conversation_closed" in flags:
        return "low"
    return "normal"
