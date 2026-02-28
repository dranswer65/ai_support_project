# whatsapp_webhook.py (Twilio-style webhook)
#
# This is NOT Meta Cloud API.
# It receives form-data fields like From / Body / MessageSid (Twilio).
# It replies by returning PlainTextResponse.
#
# Uses the SAME core engine/controller (handle_message) + Postgres sessions.

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from database import AsyncSessionLocal
from core.wa_dedupe_store_pg import ensure_wa_dedupe_table, claim_message_once
from core.session_store_pg import ensure_sessions_table
from core.appointment_schema import ensure_appointment_requests_table
from whatsapp_controller import handle_message


app = FastAPI(title="SupportPilot-TwilioWebhook", version="0.1.0")

WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()
TENANT_ID = WA_DEFAULT_CLIENT


def _reply(message: str) -> PlainTextResponse:
    # Twilio can accept plain text; if you later use TwiML, change this.
    return PlainTextResponse((message or "").strip())


@app.on_event("startup")
async def _startup():
    async with AsyncSessionLocal() as db:
        await ensure_wa_dedupe_table(db)
        await ensure_sessions_table(db)
        await ensure_appointment_requests_table(db)


@app.post("/whatsapp")
async def whatsapp_entry(request: Request):
    """
    Twilio WhatsApp inbound webhook typically posts x-www-form-urlencoded.
    Common fields:
      - From: "whatsapp:+15551234567"
      - Body: message text
      - MessageSid: unique id
    """
    form = await request.form()

    from_raw = (form.get("From") or "").strip()
    body = (form.get("Body") or "").strip()
    msg_sid = (form.get("MessageSid") or "").strip()

    if not from_raw:
        return _reply("")

    # Normalize user_id (keep full string to avoid collisions; or strip "whatsapp:" if you want)
    user_id = from_raw

    if not body:
        return _reply("Please send a text message.")

    # Optional dedupe (if MessageSid provided)
    # Twilio MessageSid is unique; we can safely use it as msg_id.
    if msg_sid:
        async with AsyncSessionLocal() as db:
            claimed = await claim_message_once(
                db,
                tenant_id=TENANT_ID,
                msg_id=msg_sid,
                wa_from=user_id,
                phone_number_id="twilio",  # marker
            )
        if not claimed:
            # Duplicate webhook delivery; do not respond again
            return _reply("")

    # Run the SAME engine/controller
    reply_text: str = ""
    meta: Dict[str, Any] = {}
    try:
        async with AsyncSessionLocal() as db:
            reply_text, meta = await handle_message(
                db=db,
                user_id=user_id,
                message_text=body,
                tenant_id=TENANT_ID,
            )
    except Exception as e:
        print("[twilio_webhook] engine error:", repr(e))
        return _reply("")

    # Controller may return "" intentionally during sticky handoff => silence
    reply_clean = (reply_text or "").strip()
    if not reply_clean:
        return _reply("")

    return _reply(reply_clean[:4000])