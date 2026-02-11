# sla/sla_tracker.py
# -----------------------------------
# Day 43A — SLA Tracker
# -----------------------------------

from datetime import datetime

_SLA_TRACKER = {}


def start_sla(user_id: str, priority: str):
    _SLA_TRACKER[user_id] = {
        "priority": priority,
        "started_at": datetime.utcnow(),
        "first_response_at": None,
        "resolved_at": None,
    }


def mark_first_response(user_id: str):
    record = _SLA_TRACKER.get(user_id)
    if record and record["first_response_at"] is None:
        record["first_response_at"] = datetime.utcnow()


def mark_resolved(user_id: str):
    record = _SLA_TRACKER.get(user_id)
    if record:
        record["resolved_at"] = datetime.utcnow()


def get_sla_record(user_id: str):
    return _SLA_TRACKER.get(user_id)
