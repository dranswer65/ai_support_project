# profiles/user_profile_store.py

# In production: DB / Redis
_user_profiles = {}

def get_user_profile(user_id):
    return _user_profiles.get(user_id)

def set_language_preference(user_id, language, locked=True):
    _user_profiles[user_id] = {
        "language": language,
        "locked": locked
    }

def get_preferred_language(user_id):
    profile = _user_profiles.get(user_id)
    if profile:
        return profile["language"]
    return None
