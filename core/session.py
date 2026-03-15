import time
from core.state import ConversationState

class Session:
    def __init__(self):
        self.state = ConversationState.START
        self.intent = None
        self.language = "en"
        self.last_seen = time.time()
        self.waiting_since = None
        self.verified = False
        self.closed = False
