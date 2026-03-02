# vendor_health/vendor_health_monitor.py
# -----------------------------------
# Day 42C — Vendor Health Monitor (Hardened)
# -----------------------------------

from __future__ import annotations

from vendor_health.vendor_health_store import (
    is_vendor_healthy,
    record_failure,
    record_success,
)


def _norm_vendor(vendor: str) -> str:
    return (vendor or "").strip().lower()


def can_use_vendor(vendor: str) -> bool:
    """
    Returns True if vendor is currently considered healthy enough to use.
    Defensive: if store errors, return False (fail-closed) to avoid crashes.
    """
    v = _norm_vendor(vendor)
    if not v:
        return False

    try:
        return bool(is_vendor_healthy(v))
    except Exception:
        # Fail-closed: do not use vendor if health store is broken
        return False


def report_vendor_result(vendor: str, success: bool) -> None:
    """
    Records vendor success/failure for health tracking.
    Defensive: never raise (webhook must not crash).
    """
    v = _norm_vendor(vendor)
    if not v:
        return

    try:
        if bool(success):
            record_success(v)
        else:
            record_failure(v)
    except Exception:
        # Never crash business logic due to health telemetry issues
        return