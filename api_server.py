import os
import sys
import asyncio
from typing import Any, Dict

import requests
from fastapi import FastAPI, Request, BackgroundTasks, Query, HTTPException
from fastapi.responses import PlainTextResponse

from database import AsyncSessionLocal
from core.wa_dedupe_store_pg import ensure_wa_dedupe_table, claim_message_once
from core.session_store_pg import ensure_sessions_table
from whatsapp_controller import handle_message
from conversation_logger import log_event

# ----------------------------
# Windows async fix (must be early)
# ----------------------------
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ----------------------------
# Env
# ----------------------------
WA_ACCESS_TOKEN = (os.getenv("WA_ACCESS_TOKEN", "") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID", "") or "").strip()
WA_VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN", "") or "").strip()

# Tenant (for sellable SaaS)
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()


def wa_send_text(to_wa_id: str, text_: str) -> Dict[str, Any]:
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        raise RuntimeError("Missing WA_ACCESS_TOKEN or WA_PHONE_NUMBER_ID")

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": text_},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"status_code": r.status_code, "text": r.text}


def log_message(user_id: str, direction: str, text_: str) -> None:
    try:
        log_event(
            event_type="wa_message",
            user_id=user_id,
            metadata={"direction": direction, "text": text_},
        )
    except Exception:
        pass


# ----------------------------
# ONE APP ONLY
# ----------------------------
app = FastAPI(title="SupportPilot", version="0.1.0")


@app.get("/")
async def root():
    return {"ok": True, "service": "SupportPilot"}


@app.get("/health")
async def health():
    return {"ok": True}


# Railway sometimes calls //health (double slash). Accept it too.
@app.get("//health")
async def health2():
    return {"ok": True}


@app.on_event("startup")
async def _startup():
    # Optional: log env presence (no secrets)
    try:
        log_event(
            event_type="startup_env",
            user_id="system",
            metadata={
                "has_WA_ACCESS_TOKEN": bool(WA_ACCESS_TOKEN),
                "has_WA_PHONE_NUMBER_ID": bool(WA_PHONE_NUMBER_ID),
                "has_WA_VERIFY_TOKEN": bool(WA_VERIFY_TOKEN),
                "WA_DEFAULT_CLIENT": WA_DEFAULT_CLIENT,
            },
        )
    except Exception:
        pass

    async with AsyncSessionLocal() as db:
        await ensure_wa_dedupe_table(db)
        await ensure_sessions_table(db)


# ----------------------------
# WhatsApp webhook verify (GET)
# ----------------------------
@app.get("/whatsapp/webhook")
async def whatsapp_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


# ----------------------------
# WhatsApp webhook (POST)
# ----------------------------
@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request, background: BackgroundTasks):
    # Always ACK ok=True to avoid Meta retry storms
    try:
        body = await request.json()
    except Exception as e:
        try:
            log_event("wa_webhook_error", "system", {"error": f"bad_json: {repr(e)}"})
        except Exception:
            pass
        return {"ok": True}

    try:
        entries = body.get("entry") or []
        if not entries:
            return {"ok": True}

        for entry in entries:
            changes = entry.get("changes") or []
            for ch in changes:
                value = (ch.get("value") or {})
                messages = value.get("messages") or []
                if not messages:
                    continue  # statuses, etc.

                for msg in messages:
                    msg_id = (msg.get("id") or "").strip()
                    from_wa = (msg.get("from") or "").strip()
                    msg_type = (msg.get("type") or "").strip()

                    if not from_wa or msg_type != "text":
                        continue

                    text_in = ((msg.get("text") or {}).get("body") or "").strip()
                    if not text_in:
                        continue

                    # DB dedupe gate (TENANT-SAFE)
                    if msg_id:
                        async with AsyncSessionLocal() as db:
                            claimed = await claim_message_once(
                                db,
                                tenant_id=WA_DEFAULT_CLIENT,
                                msg_id=msg_id,
                                wa_from=from_wa,
                                phone_number_id=WA_PHONE_NUMBER_ID or None,
                            )
                            if not claimed:
                                continue  # duplicate retry from Meta

                    # Log + enqueue only after claim
                    log_message(from_wa, "in", text_in)
                    background.add_task(_process_wa_message, from_wa, text_in)

        return {"ok": True, "queued": True}

    except Exception as e:
        try:
            log_event("wa_webhook_error", "system", {"error": repr(e)})
        except Exception:
            pass
        return {"ok": True}


async def _process_wa_message(from_wa: str, text_in: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            reply_text, _meta = await handle_message(
                db=db,
                user_id=from_wa,
                message_text=text_in,
                tenant_id=WA_DEFAULT_CLIENT,
            )

        if reply_text:
            try:
                wa_send_text(from_wa, reply_text)
                log_message(from_wa, "out", reply_text)
            except Exception as send_err:
                try:
                    log_event("wa_send_error", "system", {"error": repr(send_err)})
                except Exception:
                    pass

    except Exception as e:
        try:
            log_event("wa_process_error", "system", {"error": repr(e), "from": from_wa, "text": text_in[:300]})
        except Exception:
            pass