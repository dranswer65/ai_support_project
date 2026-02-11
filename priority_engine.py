def get_customer_priority(user_id, session, kpi_signals):
    """
    Returns priority level and reason
    """

    # Example VIP rules (mock)
    if user_id.startswith("vip_"):
        return "P0", "VIP customer"

    if "sla_breach_detected" in kpi_signals:
        return "P0", "SLA breach"

    if session.get("state") == "ESCALATION":
        return "P1", "Auto escalation"

    return "P2", "Standard customer"
