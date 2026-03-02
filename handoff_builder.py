# handoff_builder.py
# -----------------------------------
# Handoff payload builder (Hardened + PHI-safe defaults)
# -----------------------------------

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, List


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_handoff_payload(
    user_id: str,
    current_state: Optional[str],
    last_user_message: Optional[str],
    last_intent: Optional[str],
    decision_rule: str,
    decision_reason: str,
    kpi_signals: List[str],
) -> Dict[str, Any]:
    """
    Builds a unified escalation payload.
    Defensive: tolerates None values to avoid crashes during first-message escalations.
    """
    return {
        "meta": {
            "source": "SupportPilot-AI",
            "generated_at": _utc_iso(),
        },
        "user": {
            "user_id": str(user_id or "unknown"),
        },
        "conversation": {
            "current_state": (current_state or "UNKNOWN"),
            "tries": None,
            "last_user_message": (last_user_message or ""),
            "last_intent": (last_intent or "UNKNOWN"),
        },
        "decision_trace": {
            "rule": (decision_rule or "unknown_rule"),
            "reason": (decision_reason or ""),
        },
        "kpi_flags": list(kpi_signals or []),
    }