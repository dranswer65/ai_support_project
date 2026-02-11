from datetime import datetime, timedelta

def request_language_override(ticket_id, agent_id, reason):
    return {
        "language_override_requested": True,
        "language_override_reason": reason,
        "requested_by": agent_id,
        "requested_at": datetime.utcnow().isoformat()
    }


def approve_language_override(ticket_id, supervisor_id, duration_minutes=30):
    return {
        "language_lock": False,
        "language_override_approved": True,
        "language_override_by": supervisor_id,
        "language_override_until": (
            datetime.utcnow() + timedelta(minutes=duration_minutes)
        ).isoformat()
    }


def is_override_active(ticket):
    until = ticket.get("language_override_until")
    if not until:
        return False
    return datetime.utcnow() < datetime.fromisoformat(until)
