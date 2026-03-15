import time


class SessionManager:
    def __init__(self, timeout=300):
        self.sessions = {}
        self.timeout = timeout

    def _now(self):
        return time.time()

    def get(self, user_id):
        session = self.sessions.get(user_id)

        # Create new session or reset on timeout
        if not session or self._now() - session["last_active"] > self.timeout:
            session = {
                "state": "START",
                "data": {},
                "retries": {},
                "abuse_strikes": 0,
                "last_active": self._now(),
            }
            self.sessions[user_id] = session

        session["last_active"] = self._now()
        return session

    def set_state(self, user_id, state):
        session = self.get(user_id)
        session["state"] = state
        session["retries"][state] = 0

    def increment_retry(self, user_id):
        session = self.get(user_id)
        state = session["state"]
        session["retries"][state] = session["retries"].get(state, 0) + 1
        return session["retries"][state]

    def increment_abuse(self, user_id):
        session = self.get(user_id)
        session["abuse_strikes"] += 1
        return session["abuse_strikes"]

    def set_data(self, user_id, key, value):
        session = self.get(user_id)
        session["data"][key] = value

    def increment_tries(self, user_id):
        self.sessions[user_id]["tries"] += 1

    def reset_tries(self, user_id):
        self.sessions[user_id]["tries"] = 0
