# vendor_health/vendor_health_monitor.py
# -----------------------------------
# Day 42C — Vendor Health Monitor
# -----------------------------------

from vendor_health.vendor_health_store import (
    is_vendor_healthy,
    record_failure,
    record_success,
)


def can_use_vendor(vendor: str) -> bool:
    return is_vendor_healthy(vendor)


def report_vendor_result(vendor: str, success: bool):
    if success:
        record_success(vendor)
    else:
        record_failure(vendor)
