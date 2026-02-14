# compliance/audit_events.py

from datetime import datetime


def _timestamp():
    return datetime.utcnow().isoformat() + "Z"


def conversation_restart_event(user_id: str, conversation_version: int, restart_count: int):
    return {
        "event_type": "conversation_restart",
        "user_id": user_id,
        "conversation_version": conversation_version,
        "restart_count": restart_count,
        "timestamp": _timestamp(),
    }


def escalation_event(user_id: str, rule: str, priority: str, conversation_version: int):
    return {
        "event_type": "auto_escalation",
        "user_id": user_id,
        "decision_rule": rule,
        "priority": priority,
        "conversation_version": conversation_version,
        "timestamp": _timestamp(),
    }


def agent_language_violation_event(
    user_id: str,
    required_language: str,
    detected_language: str,
    enforcement_level: str,
    conversation_version: int,
):
    return {
        "event_type": "agent_language_violation",
        "user_id": user_id,
        "required_language": required_language,
        "detected_language": detected_language,
        "enforcement_level": enforcement_level,
        "conversation_version": conversation_version,
        "timestamp": _timestamp(),
    }


def incident_mode_event(active: bool):
    return {
        "event_type": "incident_mode_change",
        "active": active,
        "timestamp": _timestamp(),
    }


def sla_breach_event(user_id: str, priority: str, conversation_version: int):
    return {
        "event_type": "sla_breach_detected",
        "user_id": user_id,
        "priority": priority,
        "conversation_version": conversation_version,
        "timestamp": _timestamp(),
    }
