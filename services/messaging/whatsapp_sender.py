from __future__ import annotations

import os
from typing import Any, Dict

import requests


def _get_wa_config() -> tuple[str, str, str]:
    wa_access_token = (os.getenv("WA_ACCESS_TOKEN") or "").strip()
    wa_phone_number_id = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
    graph_api_version = (os.getenv("WA_GRAPH_VERSION") or "v20.0").strip()

    return wa_access_token, wa_phone_number_id, graph_api_version


def _build_url(wa_phone_number_id: str, graph_api_version: str) -> str:
    if not wa_phone_number_id:
        raise RuntimeError("WA_PHONE_NUMBER_ID not configured")
    return f"https://graph.facebook.com/{graph_api_version}/{wa_phone_number_id}/messages"

def wa_send_text(to_wa_id: str, text_: str) -> Dict[str, Any]:
    """
    Sends a WhatsApp text message via WhatsApp Cloud API.
    Reads env config at runtime so refreshed tokens are always used.
    """
    wa_access_token, wa_phone_number_id, graph_api_version = _get_wa_config()

    if not wa_access_token:
        raise RuntimeError("WA_ACCESS_TOKEN not configured")

    to_wa_id = str(to_wa_id or "").strip()
    if not to_wa_id:
        raise RuntimeError("recipient_whatsapp_id_missing")

    body = str(text_ or "").strip()
    if not body:
        return {"ok": False, "error": "empty_body"}

    url = _build_url(wa_phone_number_id, graph_api_version)

    headers = {
        "Authorization": f"Bearer {wa_access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {
            "body": body[:4000],
        },
    }

    print("WA URL =", url)
    print("WA TOKEN PREFIX =", wa_access_token[:20])
    print("WA TOKEN LEN =", len(wa_access_token))
    print("WA PHONE NUMBER ID =", wa_phone_number_id)

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
    except Exception as e:
        raise RuntimeError(f"WhatsApp request failed: {e}")

    try:
        data = r.json()
    except Exception:
        data = {"status_code": r.status_code, "raw": r.text}

    if r.status_code >= 400:
        raise RuntimeError(f"WhatsApp API error {r.status_code}: {data}")

    return {
        "ok": True,
        "response": data,
    }