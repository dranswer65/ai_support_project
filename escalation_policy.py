# Escalation Policy Engine
# Day 41A + 41B
# -----------------------------------
# Responsible ONLY for deciding:
# - whether to escalate
# - severity
# - priority
# - SLA
# - routing queue
# -----------------------------------


def should_escalate(
    state: str,
    retries: int | None,
    kpi_signals: list,
    intent: str,
) -> dict:
    """
    Returns a structured escalation decision.
    This function NEVER performs escalation itself.
    """

    # Default: no escalation
    decision = {
        "escalate": False
    }

    retries = retries or 0
    kpi_signals = kpi_signals or []

    # ------------------------------------------------
    # Rule 1 — Retry exhaustion
    # ------------------------------------------------
    if retries >= 3:
        return {
            "escalate": True,
            "severity": "MEDIUM",
            "priority": "P2",
            "sla_minutes": 240,          # 4 hours
            "queue": "general_support",
            "reason": "retry_limit_exceeded"
        }

    # ------------------------------------------------
    # Rule 2 — Message after conversation closed
    # ------------------------------------------------
    if "message_after_closed" in kpi_signals:
        return {
            "escalate": True,
            "severity": "LOW",
            "priority": "P3",
            "sla_minutes": 1440,         # 24 hours
            "queue": "housekeeping",
            "reason": "message_after_closed"
        }

    # ------------------------------------------------
    # Rule 3 — Abusive / hostile behavior
    # ------------------------------------------------
    if "abusive_language" in kpi_signals:
        return {
            "escalate": True,
            "severity": "HIGH",
            "priority": "P1",
            "sla_minutes": 60,           # 1 hour
            "queue": "trust_and_safety",
            "reason": "abusive_language_detected"
        }

    # ------------------------------------------------
    # Rule 4 — Delivery / fulfillment issue
    # ------------------------------------------------
    if intent == "issue" and "issue_reported" in kpi_signals:
        return {
            "escalate": True,
            "severity": "CRITICAL",
            "priority": "P0",
            "sla_minutes": 15,           # 15 minutes
            "queue": "logistics_support",
            "reason": "delivery_issue"
        }

    # ------------------------------------------------
    # Rule 5 — Escalation state reached
    # ------------------------------------------------
    if state == "ESCALATION":
        return {
            "escalate": True,
            "severity": "MEDIUM",
            "priority": "P2",
            "sla_minutes": 120,
            "queue": "general_support",
            "reason": "forced_escalation_state"
        }

    # ------------------------------------------------
    # No escalation needed
    # ------------------------------------------------
    return decision
