from typing import Dict, Any


def default_session() -> Dict[str, Any]:
    return {
        "state": None,
        "order_id": None,
        "last_bot_message": None,
        "language": "en",
    }