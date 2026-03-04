# api_server.py — Railway-safe, tenant-aware, WhatsApp Cloud webhook

# ============================================================
# 🔴 CRITICAL: Windows async fix MUST be first import
# ============================================================
import sys
import asyncio

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ============================================================

import os
from typing import Any, Dict, List, Optional
import re
import uuid

import requests
from sqlalchemy import text
from fastapi import FastAPI, Request, Query, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse
from admin_ui.reception_dashboard import router as reception_router
from database import AsyncSessionLocal
from core.wa_dedupe_store_pg import ensure_wa_dedupe_table, claim_message_once
from core.session_store_pg import ensure_sessions_table
from core.appointment_schema import ensure_appointment_requests_table
from whatsapp_controller import handle_message

WA_ACCESS_TOKEN = (os.getenv("WA_ACCESS_TOKEN", "") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID", "") or "").strip()
WA_VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN", "") or "").strip()

WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()
TENANT_ID = WA_DEFAULT_CLIENT

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN", "") or "").strip()


def wa_send_text(to_wa_id: str, text_: str) -> Dict[str, Any]:
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        raise RuntimeError("Missing WA_ACCESS_TOKEN or WA_PHONE_NUMBER_ID")

    body = (text_ or "").strip()
    if not body:
        return {"ok": False, "note": "empty_body"}

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": body[:4000]},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        j = r.json()
    except Exception:
        j = {"status_code": r.status_code, "text": r.text}

    if r.status_code >= 400:
        raise RuntimeError(f"WhatsApp send failed: {r.status_code} {j}")

    return j


app = FastAPI(title="SupportPilot", version="0.1.0")

app.include_router(reception_router)
@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"ok": True, "service": "SupportPilot"}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"ok": True}


@app.on_event("startup")
async def _startup():
    print(
        "[startup] env:",
        "has_token=",
        bool(WA_ACCESS_TOKEN),
        "has_phone_id=",
        bool(WA_PHONE_NUMBER_ID),
        "has_verify_token=",
        bool(WA_VERIFY_TOKEN),
        "tenant=",
        WA_DEFAULT_CLIENT,
        "has_admin_token=",
        bool(ADMIN_TOKEN),
    )

    async with AsyncSessionLocal() as db:
        await ensure_wa_dedupe_table(db)
        await ensure_sessions_table(db)
        await ensure_appointment_requests_table(db)

    print("[startup] tables ensured")


# ✅ Admin reset — protected by ADMIN_TOKEN
@app.get("/admin/reset-sessions")
async def admin_reset_sessions(
    token: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    expected = (os.getenv("ADMIN_TOKEN", "") or "").strip()
    received = (x_admin_token or token or "").strip()

    print(f"[admin] reset-sessions expected_set={bool(expected)} received_len={len(received)}")

    if not expected or received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    tenant = TENANT_ID
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM sessions WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant},
        )
        await db.commit()

    return {"ok": True, "tenant": tenant, "deleted": "sessions"}


@app.get("/whatsapp/webhook")
async def whatsapp_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


def _extract_text_messages(body: Dict[str, Any]) -> list[Dict[str, str]]:
    out: list[Dict[str, str]] = []
    entries = body.get("entry") or []
    if not isinstance(entries, list):
        return out

    for entry in entries:
        changes = (entry or {}).get("changes") or []
        if not isinstance(changes, list):
            continue

        for ch in changes:
            value = (ch or {}).get("value") or {}
            if not isinstance(value, dict):
                continue

            messages = value.get("messages") or []
            if not isinstance(messages, list) or not messages:
                continue

            for msg in messages:
                if not isinstance(msg, dict):
                    continue

                msg_id = (msg.get("id") or "").strip()
                from_wa = (msg.get("from") or "").strip()
                msg_type = (msg.get("type") or "").strip().lower()

                if not from_wa or msg_type != "text":
                    continue

                text_in = ((msg.get("text") or {}).get("body") or "").strip()
                if not text_in:
                    continue

                out.append({"msg_id": msg_id, "from_wa": from_wa, "text": text_in})

    return out


_REF_RE = re.compile(r"appt_ref=([A-Z]{2,6}-\d{6}-\d{3,6})", re.IGNORECASE)


def _extract_or_make_request_id(payload: Dict[str, Any]) -> str:
    """
    Prefer engine appt_ref (stable id), else fallback to UUID.
    """
    notes = str(payload.get("notes") or "")
    m = _REF_RE.search(notes)
    if m:
        return m.group(1).upper()
    # fallback - unique but not human-friendly
    return f"REQ-{uuid.uuid4().hex[:12].upper()}"


async def _persist_engine_actions(
    *,
    db,
    tenant_id: str,
    user_id: str,
    actions: List[Dict[str, Any]],
) -> None:
    """
    Persist engine actions to DB.

    Handles:
      - CREATE_APPOINTMENT_REQUEST -> inserts into appointment_requests
    """
    if not actions:
        return

    for a in actions:
        if not isinstance(a, dict):
            continue

        if (a.get("type") or "").strip() != "CREATE_APPOINTMENT_REQUEST":
            continue

        payload = a.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        request_id = _extract_or_make_request_id(payload)
        channel = "whatsapp"

        await db.execute(
            text("""
                INSERT INTO appointment_requests (
                    tenant_id,
                    request_id,
                    channel,
                    user_id,
                    status,
                    intent,
                    dept_key,
                    dept_label,
                    doctor_key,
                    doctor_label,
                    appt_date,
                    appt_time,
                    patient_name,
                    patient_mobile,
                    patient_id,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :request_id,
                    :channel,
                    :user_id,
                    :status,
                    :intent,
                    :dept_key,
                    :dept_label,
                    :doctor_key,
                    :doctor_label,
                    :appt_date,
                    :appt_time,
                    :patient_name,
                    :patient_mobile,
                    :patient_id,
                    :notes,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (request_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = NOW();
            """),
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "channel": channel,
                "user_id": user_id,
                "status": str(payload.get("status") or "PENDING"),
                "intent": str(payload.get("intent") or "BOOK"),
                "dept_key": payload.get("dept_key"),
                "dept_label": payload.get("dept_label"),
                "doctor_key": payload.get("doctor_key"),
                "doctor_label": payload.get("doctor_label"),
                "appt_date": payload.get("appt_date"),
                "appt_time": payload.get("appt_time"),
                "patient_name": payload.get("patient_name"),
                "patient_mobile": payload.get("patient_mobile"),
                "patient_id": payload.get("patient_id"),
                "notes": payload.get("notes"),
            },
        )


@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    messages = _extract_text_messages(body)
    if not messages:
        return JSONResponse({"ok": True})

    for m in messages:
        msg_id = m["msg_id"]
        from_wa = m["from_wa"]
        text_in = m["text"]

        print(f"[webhook] incoming from={from_wa} msg_id={msg_id} text={text_in!r}")

        # dedupe
        if msg_id:
            async with AsyncSessionLocal() as db:
                claimed = await claim_message_once(
                    db,
                    tenant_id=TENANT_ID,
                    msg_id=msg_id,
                    wa_from=from_wa,
                    phone_number_id=WA_PHONE_NUMBER_ID,
                )
            if not claimed:
                print(f"[webhook] duplicate ignored msg_id={msg_id}")
                continue

        # run engine
        try:
            async with AsyncSessionLocal() as db:
                reply_text, meta = await handle_message(
                    db=db,
                    user_id=from_wa,
                    message_text=text_in,
                    tenant_id=TENANT_ID,
                )

                # ✅ Persist actions if present (requires whatsapp_controller to include meta["actions"])
                actions = meta.get("actions") if isinstance(meta, dict) else None
                if isinstance(actions, list) and actions:
                    await _persist_engine_actions(
                        db=db,
                        tenant_id=TENANT_ID,
                        user_id=from_wa,
                        actions=actions,
                    )
                    await db.commit()

        except Exception as e:
            print("[engine] error:", repr(e))
            continue

        reply_clean = (reply_text or "").strip()
        if reply_clean:
            try:
                wa_send_text(from_wa, reply_clean)
            except Exception as e:
                print("[wa_send] error:", repr(e))

    return JSONResponse({"ok": True})