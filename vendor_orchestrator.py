# vendor_orchestrator.py
# -----------------------------------
# Day 42C — Vendor Orchestrator with Health Checks (Hardened)
# + Day 44 — Incident Mode Guard
# + Alignment: unified ticket_id surface
# -----------------------------------

from __future__ import annotations

from typing import Optional, Dict, Any

from vendor_adapters.zendesk_adapter import zendesk_adapter
from vendor_adapters.freshdesk_adapter import freshdesk_adapter
from vendor_health.vendor_health_monitor import (
    can_use_vendor,
    report_vendor_result,
)
from incident.incident_state import is_incident_mode


def _norm_vendor(vendor: str) -> str:
    return (vendor or "").strip().lower()


def _extract_ticket_id(adapter_result: dict) -> Optional[str]:
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
        tid = ticket.get("id") or ticket.get("idempotency_key") or ticket.get("unique_external_id")
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
        try:
            result = zendesk_adapter(payload, routing) or {}
            success = (result.get("status") == "created")
            report_vendor_result("zendesk", success)

            if success:
                return {
                    "final_vendor": "zendesk",
                    "status": "created",
                    "ticket_id": _extract_ticket_id(result),
                    "result": result,
                }
        except Exception as e:
            # count as failure, then fall back
            report_vendor_result("zendesk", False)

    # -----------------------------------
    # 2️⃣ Zendesk failed/unhealthy → Freshdesk (only if healthy)
    # -----------------------------------
    if can_use_vendor("freshdesk"):
        try:
            result = freshdesk_adapter(payload, routing) or {}
            success = (result.get("status") == "created")
            report_vendor_result("freshdesk", success)

            return {
                "final_vendor": "freshdesk",
                "status": "created" if success else "failed",
                "ticket_id": _extract_ticket_id(result),
                "result": result,
                "reason": None if success else "freshdesk_failed",
            }
        except Exception:
            report_vendor_result("freshdesk", False)
            return {
                "final_vendor": "freshdesk",
                "status": "failed",
                "ticket_id": None,
                "result": None,
                "reason": "freshdesk_exception",
            }

    # -----------------------------------
    # 3️⃣ No healthy vendors available
    # -----------------------------------
    return {
        "final_vendor": None,
        "status": "failed",
        "ticket_id": None,
        "result": None,
        "reason": "no_healthy_vendors",
    }