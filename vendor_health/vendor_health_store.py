# vendor_health/vendor_health_store.py
# -----------------------------------
# Day 42C — Vendor Health Store (Hardened, in-memory)
# -----------------------------------

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, Optional


def _utcnow() -> datetime:
    # naive UTC is fine for this use-case (consistent with your original code)
    return datetime.utcnow()


def _norm_vendor(vendor: str) -> str:
    return (vendor or "").strip().lower()


# Simple in-memory store (demo-safe; replace with DB later if needed)
_VENDOR_HEALTH: Dict[str, Dict[str, Any]] = {
    "zendesk": {"healthy": True, "failures": 0, "disabled_until": None},
    "freshdesk": {"healthy": True, "failures": 0, "disabled_until": None},
}

FAILURE_THRESHOLD = 3          # failures before disabling
DISABLE_DURATION_MIN = 10      # cooldown window


def _get_record(vendor: str) -> Optional[Dict[str, Any]]:
    v = _norm_vendor(vendor)
    if not v:
        return None
    rec = _VENDOR_HEALTH.get(v)
    if rec is None:
        # unknown vendor is treated as unhealthy
        return None
    return rec


def is_vendor_healthy(vendor: str) -> bool:
    record = _get_record(vendor)
    if not record:
        return False

    try:
        disabled_until = record.get("disabled_until")
        if disabled_until:
            if _utcnow() < disabled_until:
                return False
            # Auto-recover after cooldown
            record["disabled_until"] = None
            record["failures"] = 0
            record["healthy"] = True

        return bool(record.get("healthy"))
    except Exception:
        # Fail-closed if store record is malformed
        return False


def record_failure(vendor: str) -> None:
    record = _get_record(vendor)
    if not record:
        return

    try:
        record["failures"] = int(record.get("failures", 0)) + 1

        if record["failures"] >= FAILURE_THRESHOLD:
            record["healthy"] = False
            record["disabled_until"] = _utcnow() + timedelta(minutes=DISABLE_DURATION_MIN)
    except Exception:
        return


def record_success(vendor: str) -> None:
    record = _get_record(vendor)
    if not record:
        return

    try:
        record["failures"] = 0
        record["healthy"] = True
        record["disabled_until"] = None
    except Exception:
        return


def snapshot() -> Dict[str, Dict[str, Any]]:
    """
    For debugging / admin dashboards later.
    Returns a copy so external callers can't mutate internal state.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in _VENDOR_HEALTH.items():
        out[k] = dict(v)
    return out