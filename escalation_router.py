# escalation_router.py
# -----------------------------------
# Day 41C — Escalation Routing Engine
# -----------------------------------

def route_escalation(payload: dict) -> dict:
    """
    Decide which team, tier, and region should handle the escalation.
    Returns routing metadata only.
    """

    kpi_flags = payload.get("kpi_flags", [])
    decision_rule = payload.get("decision_trace", {}).get("rule")
    intent = payload.get("conversation", {}).get("last_intent")
    user_id = payload.get("user", {}).get("user_id")

    # -------------------------------
    # Default routing
    # -------------------------------

    routing = {
        "team": "general_support",
        "tier": "T1",
        "region": "GLOBAL",
        "priority": "normal",
    }

    # -------------------------------
    # Team routing
    # -------------------------------

    if intent in {"delivery_issue", "order_id"}:
        routing["team"] = "delivery_support"

    if intent in {"billing_issue", "refund"}:
        routing["team"] = "billing_support"

    if "abuse_detected" in kpi_flags:
        routing["team"] = "trust_and_safety"
        routing["tier"] = "T4"
        routing["priority"] = "urgent"

    # -------------------------------
    # Tier escalation
    # -------------------------------

    if "retry_limit_exceeded" in kpi_flags:
        routing["tier"] = "T2"

    if "sla_breach_risk" in kpi_flags:
        routing["tier"] = "T3"
        routing["priority"] = "high"

    if decision_rule in {"ESCALATE_CRITICAL", "ESCALATE_ABUSE"}:
        routing["tier"] = "T4"
        routing["priority"] = "urgent"

    # -------------------------------
    # Region routing (simple example)
    # -------------------------------

    if user_id and user_id.startswith("IN_"):
        routing["region"] = "IN"

    elif user_id and user_id.startswith("EU_"):
        routing["region"] = "EU"

    else:
        routing["region"] = "GLOBAL"

    return routing
