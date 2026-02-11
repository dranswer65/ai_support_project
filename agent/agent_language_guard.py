# agent/agent_language_guard.py

def validate_agent_reply(agent_language, customer_language):
    if agent_language != customer_language:
        return False
    return True
