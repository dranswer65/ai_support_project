from __future__ import annotations

import os
import json
import requests
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SupportPilot WhatsApp Cloud Webhook")

# ==========================================================
# ENV
# ==========================================================
VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN", "") or "").strip()
GRAPH_TOKEN = (os.getenv("WA_ACCESS_TOKEN", "") or "").strip()
PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID", "") or "").strip()

GRAPH_VERSION = (os.getenv("WA_GRAPH_VERSION", "v20.0") or "v20.0").strip()

# Where is your SupportPilot API (local OR Railway)
API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()

# Default client routing
DEFAULT_CLIENT = (os.getenv("CLIENT_NAME", "supportpilot_demo") or "supportpilot_demo").strip()
DEFAULT_CLIENT_API_KEY = (os.getenv("CLIENT_API_KEY", "") or "").strip()

# Optional mapping:
# WA_CLIENT_MAP_JSON='{"1036240522898006":"supportpilot_demo","another_phone_id":"client2"}'
WA_CLIENT_MAP_JSON = (os.getenv("WA_CLIENT_MAP_JSON", "") or "").strip()


def load_client_map() -> Dict[str, str]:
    if not WA_CLIENT_MAP_JSON:
        return {}
    try:
        data = json.loads(WA_CLIENT_MAP_JSON)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}
    except Exception:
        return {}


CLIENT_MAP = load_client_map()

# ==========================================================
# HELPERS
# ==========================================================
def _pick_client(phone_id: str) -> str:
    return CLIENT_MAP.get(phone_id, DEFAULT_CLIENT)


def _api_chat(client_name: str, api_key: str, question: str, tone: str = "formal", language: str = "en") -> str:
    """
    Calls your SupportPilot API /chat and returns answer string.
    """
    url = f"{API_BASE}/chat"
    payload = {
        "client_name": client_name,
        "api_key": api_key,
        "question": question,
        "tone": tone,
        "language": language,
    }
    r = requests.post(url, json=payload, timeout=45)
    if r.status_code != 200:
        # Return a user-friendly message instead of crashing
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        return f"Sorry — API error ({r.status_code}). {detail}"
    data = r.json()
    return (data.get("answer") or "").strip() or "Sorry — no answer generated."


def _send_whatsapp_text(to_wa_id: str, message_text: str, phone_number_id: str) -> Dict[str, Any]:
    """
    Sends a text message via Meta Graph API.
    """
    if not GRAPH_TOKEN:
        return {"ok": False, "error": "WA_ACCESS_TOKEN missing in .env"}

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {GRAPH_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": message_text},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    return {"ok": resp.status_code in (200, 201), "status": resp.status_code, "body": body}


def _extract_message(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extract incoming WhatsApp Cloud message safely.
    Returns: {"from": "...", "text": "...", "phone_id": "..."} or None
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        metadata = value.get("metadata") or {}
        phone_id = str(metadata.get("phone_number_id") or "")

        messages = value.get("messages") or []
        if not messages:
            return None

        msg = messages[0]

        # Ignore delivery/read statuses etc
        if msg.get("type") != "text":
            return None

        from_wa = str(msg.get("from") or "")
        text = (msg.get("text") or {}).get("body") or ""

        if not from_wa or not text:
            return None

        return {"from": from_wa, "text": text, "phone_id": phone_id}
    except Exception:
        return None


# ==========================================================
# WEBHOOK VERIFY (GET)
# ==========================================================
@app.get("/whatsapp/webhook", response_class=PlainTextResponse)
def verify_webhook(
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
):
    """
    Meta calls this when you press Verify in webhook settings.
    Must return the challenge if token matches.
    """
    if VERIFY_TOKEN and hub_verify_token == VERIFY_TOKEN and hub_mode == "subscribe":
        return hub_challenge or "OK"
    return PlainTextResponse("Verification failed", status_code=403)


# Meta also sends these as query params like hub.mode, hub.challenge, hub.verify_token
@app.get("/whatsapp/webhook2", response_class=PlainTextResponse)
def verify_webhook_alt(request: Request):
    q = request.query_params
    mode = q.get("hub.mode", "")
    challenge = q.get("hub.challenge", "")
    token = q.get("hub.verify_token", "")

    if VERIFY_TOKEN and token == VERIFY_TOKEN and mode == "subscribe":
        return challenge or "OK"
    return PlainTextResponse("Verification failed", status_code=403)


# ==========================================================
# WEBHOOK RECEIVE (POST)
# ==========================================================
@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    payload = await request.json()

    # Debug print (see your console logs)
    print("WHATSAPP EVENT:", json.dumps(payload, indent=2, ensure_ascii=False))

    msg = _extract_message(payload)
    if not msg:
        return {"ok": True, "note": "No text message to process"}

    from_wa = msg["from"]
    user_text = msg["text"].strip()
    phone_id = msg["phone_id"] or PHONE_NUMBER_ID

    client_name = _pick_client(phone_id)
    if not DEFAULT_CLIENT_API_KEY:
        # If not set, bot cannot call /chat correctly
        out = "Server missing CLIENT_API_KEY. Please set it in .env."
        _send_whatsapp_text(from_wa, out, phone_id)
        return {"ok": False, "error": "CLIENT_API_KEY missing"}

    # Call SupportPilot AI
    answer = _api_chat(client_name, DEFAULT_CLIENT_API_KEY, user_text, tone="formal", language="en")

    # Send back to WhatsApp
    send_result = _send_whatsapp_text(from_wa, answer, phone_id)

    if not send_result.get("ok"):
        print("SEND ERROR:", send_result)
        return JSONResponse({"ok": False, "send_result": send_result}, status_code=500)

    return {"ok": True, "client": client_name, "sent": True}
