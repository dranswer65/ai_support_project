# vendor_health/vendor_health_store.py
# -----------------------------------
# Day 42C — Vendor Health Store
# -----------------------------------

from datetime import datetime, timedelta

# Simple in-memory store
_VENDOR_HEALTH = {
    "zendesk": {
        "healthy": True,
        "failures": 0,
        "disabled_until": None,
    },
    "freshdesk": {
        "healthy": True,
        "failures": 0,
        "disabled_until": None,
    },
}

FAILURE_THRESHOLD = 3          # failures before disabling
DISABLE_DURATION_MIN = 10      # cooldown window


def is_vendor_healthy(vendor: str) -> bool:
    record = _VENDOR_HEALTH.get(vendor)

    if not record:
        return False

    if record["disabled_until"]:
        if datetime.utcnow() < record["disabled_until"]:
            return False
        else:
            # Auto-recover
            record["disabled_until"] = None
            record["failures"] = 0
            record["healthy"] = True

    return record["healthy"]


def record_failure(vendor: str):
    record = _VENDOR_HEALTH[vendor]
    record["failures"] += 1

    if record["failures"] >= FAILURE_THRESHOLD:
        record["healthy"] = False
        record["disabled_until"] = datetime.utcnow() + timedelta(
            minutes=DISABLE_DURATION_MIN
        )


def record_success(vendor: str):
    record = _VENDOR_HEALTH[vendor]
    record["failures"] = 0
    record["healthy"] = True
    record["disabled_until"] = None


def snapshot():
    """For debugging / admin dashboards later"""
    return _VENDOR_HEALTH
