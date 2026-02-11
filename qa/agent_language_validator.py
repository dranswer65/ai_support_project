def validate_agent_reply(reply_text, agent_constraints):
    detected_lang = detect_language(reply_text)

    if agent_constraints["language_lock"]:
        if not is_override_active(ticket):
            if detected_lang != agent_constraints["reply_language"]:
                return False, "Language violation (lock active)"

    return True, "OK"
