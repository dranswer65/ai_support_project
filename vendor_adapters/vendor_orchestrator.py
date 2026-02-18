# vendor_orchestrator.py
# -----------------------------------
# Day 42C — Vendor Orchestrator with Health Checks
# + Day 44 — Incident Mode Guard
# + Alignment: unified ticket_id surface
# -----------------------------------

from vendor_adapters.zendesk_adapter import zendesk_adapter
from vendor_adapters.freshdesk_adapter import freshdesk_adapter
from vendor_health.vendor_health_monitor import (
    can_use_vendor,
    report_vendor_result,
)
from incident.incident_state import is_incident_mode


def _extract_ticket_id(adapter_result: dict) -> str | None:
    """
    Best-effort extraction of ticket id across adapters.
    With aligned adapters, this will be adapter_result['ticket_id'].
    Kept defensive for safety.
    """
    if not isinstance(adapter_result, dict):
        return None

    tid = adapter_result.get("ticket_id")
    if tid:
        return str(tid)

    ticket = adapter_result.get("ticket") or {}
    if isinstance(ticket, dict):
        # zendesk sim
        tid = ticket.get("id") or ticket.get("idempotency_key")
        if tid:
            return str(tid)
        # freshdesk sim
        tid = ticket.get("unique_external_id")
        if tid:
            return str(tid)

    return None


def dispatch_ticket(payload: dict, routing: dict) -> dict:
    """
    Dispatches escalation tickets with vendor failover.
    During Incident Mode, tickets are queued and NOT sent.

    Returns a unified structure:
    {
      "final_vendor": "zendesk" | "freshdesk" | None,
      "status": "created" | "failed" | "queued",
      "ticket_id": "<id>" | None,
      "result": <adapter_result dict> | None,
      "reason": "...optional..."
    }
    """

    # 🚨 Day 44 — Incident Mode Protection
    if is_incident_mode():
        return {
            "final_vendor": None,
            "status": "queued",
            "ticket_id": None,
            "reason": "incident_mode_active",
            "payload_snapshot": payload,
        }

    # -----------------------------------
    # 1️⃣ Try Zendesk if healthy
    # -----------------------------------
    if can_use_vendor("zendesk"):
        result = zendesk_adapter(payload, routing)

        success = (result.get("status") == "created")
        report_vendor_result("zendesk", success)

        if success:
            return {
                "final_vendor": "zendesk",
                "status": "created",
                "ticket_id": _extract_ticket_id(result),
                "result": result,
            }

    # -----------------------------------
    # 2️⃣ Zendesk failed → Freshdesk
    # -----------------------------------
    result = freshdesk_adapter(payload, routing)
    success = (result.get("status") == "created")
    report_vendor_result("freshdesk", success)

    return {
        "final_vendor": "freshdesk",
        "status": "created" if success else "failed",
        "ticket_id": _extract_ticket_id(result),
        "result": result,
    }
