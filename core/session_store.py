class SessionStore:
    def __init__(self):
        self.sessions = {}

    def get(self, user_id):
        if user_id not in self.sessions:
            self.sessions[user_id] = Session()
        return self.sessions[user_id]
