import os
import requests

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()

WA_ACCESS_TOKEN = _env("WA_ACCESS_TOKEN")
WA_PHONE_NUMBER_ID = _env("WA_PHONE_NUMBER_ID")

WA_DEFAULT_CLIENT = _env("WA_DEFAULT_CLIENT", "supportpilot_demo")
WA_DEFAULT_API_KEY = _env("WA_DEFAULT_API_KEY")  # must be the REAL client api key (plain) OR match your auth logic

SP_API_BASE = _env("SP_API_BASE", "http://127.0.0.1:8000")

def wa_send_text(to_wa_id: str, text: str) -> dict:
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        raise RuntimeError("Missing WA_ACCESS_TOKEN or WA_PHONE_NUMBER_ID")

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": (text or "")[:4000]},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"WhatsApp send failed: {r.status_code} {r.text}")

    return r.json()

def call_supportpilot_chat(message_text: str) -> str:
    """
    Calls your /chat endpoint and returns the assistant answer text.
    """
    if not WA_DEFAULT_API_KEY:
        return "⚠️ WA_DEFAULT_API_KEY is missing on server."

    url = f"{SP_API_BASE}/chat"
    payload = {
        "client_name": WA_DEFAULT_CLIENT,
        "api_key": WA_DEFAULT_API_KEY,
        "question": message_text,
        "tone": "formal",
        "language": "en",
    }

    r = requests.post(url, json=payload, timeout=60)
    if r.status_code >= 400:
        try:
            j = r.json()
            detail = j.get("detail", r.text)
        except Exception:
            detail = r.text
        return f"⚠️ SupportPilot error: {detail}"

    data = r.json()
    return (data.get("answer") or "").strip() or "Sorry — I couldn't generate a response."

def handle_whatsapp_event(payload: dict) -> dict:
    """
    Parses WhatsApp Cloud webhook payload, replies with AI answer.
    Returns quickly (Meta expects fast 200).
    """
    try:
        entry = (payload.get("entry") or [])[0]
        changes = (entry.get("changes") or [])[0]
        value = changes.get("value") or {}

        # statuses callbacks have no "messages"
        messages = value.get("messages") or []
        if not messages:
            return {"ok": True, "note": "no_messages"}

        msg = messages[0]
        from_wa = msg.get("from", "")
        msg_type = msg.get("type", "")

        if not from_wa:
            return {"ok": True, "note": "missing_from"}

        if msg_type != "text":
            wa_send_text(from_wa, "Sorry, I can only handle text messages right now.")
            return {"ok": True, "note": "non_text"}

        text = ((msg.get("text") or {}).get("body") or "").strip()
        if not text:
            wa_send_text(from_wa, "Please send a text message.")
            return {"ok": True, "note": "empty_text"}

        answer = call_supportpilot_chat(text)
        wa_send_text(from_wa, answer)

        return {"ok": True, "note": "replied"}

    except Exception as e:
        # Never crash the webhook (Meta will retry)
        print("WHATSAPP BOT ERROR:", repr(e))
        return {"ok": True, "note": "error"}
