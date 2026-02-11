def log_language_override(event):
    """
    event = {
        type: requested | approved | expired
        ticket_id
        actor_id
        reason
        timestamp
    }
    """
    # Persist to immutable audit store
    pass
