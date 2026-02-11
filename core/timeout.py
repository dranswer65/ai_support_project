import time
from core.state import ConversationState

def check_timeout(session):
    if session.waiting_since:
        elapsed = time.time() - session.waiting_since

        if elapsed > 120:
            session.state = ConversationState.NO_RESPONSE_CLOSED
            return (
                "I haven’t heard back from you, so I’ll close this chat for now.\n"
                "You can contact us anytime if you need help."
            )
    return None
