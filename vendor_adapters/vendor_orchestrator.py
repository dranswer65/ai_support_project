# -----------------------------------
# Day 42C — Vendor Orchestrator with Health Checks
# + Day 44 — Incident Mode Guard
# -----------------------------------

from vendor_adapters.zendesk_adapter import zendesk_adapter
from vendor_adapters.freshdesk_adapter import freshdesk_adapter
from vendor_health.vendor_health_monitor import (
    can_use_vendor,
    report_vendor_result,
)
from incident.incident_state import is_incident_mode


def dispatch_ticket(payload: dict, routing: dict) -> dict:
    """
    Dispatches escalation tickets with vendor failover.
    During Incident Mode, tickets are queued and NOT sent.
    """

    # 🚨 Day 44 — Incident Mode Protection
    if is_incident_mode():
        return {
            "final_vendor": None,
            "status": "queued",
            "reason": "incident_mode_active",
            "payload_snapshot": payload,
        }

    # -----------------------------------
    # 1️⃣ Try Zendesk if healthy
    # -----------------------------------
    if can_use_vendor("zendesk"):
        result = zendesk_adapter(payload, routing)

        success = result.get("status") == "created"
        report_vendor_result("zendesk", success)

        if success:
            return {
                "final_vendor": "zendesk",
                "result": result,
            }

    # -----------------------------------
    # 2️⃣ Zendesk failed → Freshdesk
    # -----------------------------------
    result = freshdesk_adapter(payload, routing)
    success = result.get("status") == "created"
    report_vendor_result("freshdesk", success)

    return {
        "final_vendor": "freshdesk",
        "result": result,
    }
