# -----------------------------------
# Day 43A — SLA Breach Detector
# + Day 44 — Incident Mode Guard
# -----------------------------------

from datetime import datetime
from sla.sla_policies import SLA_POLICIES
from incident.incident_state import is_incident_mode
from sla.gcc_business_hours import is_gcc_business_hours


def detect_sla_breach(sla_record: dict) -> list:
    """
    Returns a list of SLA breach flags.
    During Incident Mode, SLA breaches are suppressed.
    """
    # Pause SLA outside GCC business hours
    if not is_gcc_business_hours():
        return [], []

    # 🚨 Day 44 — Global SLA Freeze
    if is_incident_mode():
        return []

    breaches = []
    now = datetime.utcnow()

    policy = SLA_POLICIES[sla_record["priority"]]

    # -----------------------------------
    # First Response SLA
    # -----------------------------------
    if sla_record["first_response_at"] is None:
        elapsed = (now - sla_record["started_at"]).total_seconds()
        if elapsed > policy["first_response_sec"]:
            breaches.append("first_response_sla_breached")

    # -----------------------------------
    # Resolution SLA
    # -----------------------------------
    if sla_record["resolved_at"] is None:
        elapsed = (now - sla_record["started_at"]).total_seconds()
        if elapsed > policy["resolution_sec"]:
            breaches.append("resolution_sla_breached")

    return breaches
