# rag_router.py
from __future__ import annotations

import os
import requests
from typing import Dict, Any


SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()


def call_rag_chat(user_id: str, session: Dict[str, Any], user_message: str, language: str) -> str:
    if not SP_API_BASE:
        return "System error: SP_API_BASE not configured"

    url = f"{SP_API_BASE}/chat"
    payload = {
        "client_name": WA_DEFAULT_CLIENT,
        "question": user_message,
        "tone": "formal",
        "language": "ar" if language == "ar" else "en",
    }

    try:
        r = requests.post(url, json=payload, timeout=25)
        if r.status_code != 200:
            try:
                j = r.json()
                return (j.get("detail") or str(j))[:500]
            except Exception:
                return "AI server error"
        data = r.json()
        return (data.get("answer") or "").strip() or "Sorry — I couldn't generate a response."
    except Exception:
        return "System temporarily unavailable"