# api_server.py — Railway-safe, tenant-aware, WhatsApp Cloud webhook
# (Does NOT modify engine.py or whatsapp_controller.py logic)
#
# Smart Slot Picker support
# Engine emits CREATE_APPOINTMENT_REQUEST
# Server persists request and optionally confirms slot hold.

from __future__ import annotations

# ============================================================
# Windows async fix
# ============================================================

import sys
import asyncio

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ============================================================

import os
import re
import uuid
from typing import Any, Dict, List, Optional
from datetime import date

import requests
from sqlalchemy import text
from core.subscription_enforcer import enforce_subscription_status
from fastapi import FastAPI, Request, Query, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# DATABASE
# ============================================================

from database import AsyncSessionLocal

# ============================================================
# CORE TABLES
# ============================================================

from core.patient_schema import ensure_patient_tables
from core.wa_dedupe_store_pg import ensure_wa_dedupe_table, claim_message_once
from core.session_store_pg import ensure_sessions_table
from core.slot_schema import ensure_slot_tables
from services.reminders.appointment_reminders import run_appointment_reminders
from core.doctor_schedule_schema import (
    ensure_doctor_schedule_rules_table,
    ensure_doctor_time_off_table,
    ensure_slot_holds_table,
    ensure_appointments_table,
)

from core.appointment_schema import (
    ensure_appointment_requests_table,
    ensure_reminder_logs_table,
)
from core.internal_billing_schema import ensure_internal_billing_tables
from core.slot_holds_store_pg import confirm_hold_create_appointment
from services.reminders.reminder_service import start_background_reminder_loop
from services.reminders.reminder_scheduler_service import start_reminder_scheduler
from core.auth_schema import ensure_auth_tables, seed_auth_defaults
from core.audit_schema import ensure_audit_logs_table


# ============================================================
# CONTROLLER
# ============================================================

from whatsapp_controller import handle_message

# ============================================================
# ROUTERS
# ============================================================

from admin_ui.tenants_dashboard import router as tenants_router
from admin_ui.reception_dashboard import router as reception_router
from api.slots_api import router as slots_router
from admin_ui.onboarding_dashboard import router as onboarding_router

from api.tenant_admin_api import router as tenant_admin_router
from admin_ui.analytics_dashboard import router as analytics_router
from admin_ui.appointments_dashboard import router as appointments_router
from admin_ui.doctors_dashboard import router as doctors_router
from admin_ui.calendar_dashboard import router as calendar_router
from admin_ui.patients_dashboard import router as patients_router
from billing.stripe_api import router as stripe_billing_router
from admin_ui.billing_dashboard import router as billing_router
from admin_ui.usage_dashboard import router as usage_router
from admin_ui.invoices_dashboard import router as invoices_router
from admin_ui.settings_dashboard import router as settings_router
from admin_ui.templates_dashboard import router as templates_router
from admin_ui.reminders_dashboard import router as reminders_router
from admin_ui.knowledge_dashboard import router as knowledge_router
from api.knowledge_api import router as knowledge_api_router
from api.internal_billing_api import router as internal_billing_router
from admin_ui.internal_billing_dashboard import router as internal_billing_dashboard_router
from api.auth_api import router as auth_router
from admin_ui.login_dashboard import router as login_router

# ============================================================
# ENV CONFIG
# ============================================================

WA_ACCESS_TOKEN = (os.getenv("WA_ACCESS_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
WA_VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "").strip()

WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT") or "supportpilot_demo").strip()
TENANT_ID = WA_DEFAULT_CLIENT

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()

AUTO_CONFIRM_HOLDS = (os.getenv("AUTO_CONFIRM_HOLDS") or "1").lower() in ("1", "true", "yes")

AUTO_MATCH_HOLD_IF_MISSING_ID = (
    os.getenv("AUTO_MATCH_HOLD_IF_MISSING_ID") or "0"
).lower() in ("1", "true", "yes")

# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="SupportPilot", version="0.1.0")

app.include_router(reception_router)
app.include_router(slots_router)
app.include_router(tenant_admin_router)

print(
    "[api_server] app routes with audit:",
    [r.path for r in app.routes if "audit" in r.path]
)

app.include_router(onboarding_router)
app.include_router(tenants_router)
app.include_router(analytics_router)
app.include_router(appointments_router)
app.include_router(doctors_router)
app.include_router(calendar_router)
app.include_router(patients_router)
app.include_router(stripe_billing_router)
app.include_router(billing_router)
app.include_router(usage_router)
app.include_router(invoices_router)
app.include_router(settings_router)
app.include_router(templates_router)
app.include_router(reminders_router)
app.include_router(knowledge_router)
app.include_router(knowledge_api_router)
app.include_router(internal_billing_router)
app.include_router(internal_billing_dashboard_router)
app.include_router(auth_router)
app.include_router(login_router)

# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True}


# ============================================================
# DEV: RESET TENANT TABLES (ONLY FOR LOCAL DEVELOPMENT)
# ============================================================

@app.get("/admin/dev/reset-tenants")
async def reset_tenants(
    token: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    expected = ADMIN_TOKEN
    received = (x_admin_token or token or "").strip()

    if not expected or received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    async with AsyncSessionLocal() as db:
        await db.execute(text("DROP TABLE IF EXISTS tenant_tokens CASCADE"))
        await db.execute(text("DROP TABLE IF EXISTS tenants CASCADE"))
        await db.commit()

    return {"ok": True, "message": "tenant tables dropped"}


# ============================================================
# WhatsApp Sender
# ============================================================

def wa_send_text(to_wa_id: str, text_: str):
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        raise RuntimeError("Missing WA config")

    body = (text_ or "").strip()
    if not body:
        return

    to_wa_id = (
        str(to_wa_id or "")
        .strip()
        .replace("+", "")
        .replace(" ", "")
        .replace("-", "")
    )

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": body[:4000]},
    }

    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    print("[wa_send_text] to =", to_wa_id)
    print("[wa_send_text] phone_number_id =", WA_PHONE_NUMBER_ID)

    resp = requests.post(url, headers=headers, json=payload, timeout=30)

    print("[wa_send_text] status =", resp.status_code)
    print("[wa_send_text] response =", resp.text)

    resp.raise_for_status()
    return resp.json()
# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():
    from core.tenant_schema import ensure_tenant_tables

    print(
        "[startup]",
        "tenant=", WA_DEFAULT_CLIENT,
        "auto_confirm_holds=", AUTO_CONFIRM_HOLDS,
        "auto_match_hold_if_missing_id=", AUTO_MATCH_HOLD_IF_MISSING_ID,
    )

    async with AsyncSessionLocal() as db:
        await ensure_wa_dedupe_table(db)
        await ensure_sessions_table(db)
        await ensure_tenant_tables(db)
        await ensure_patient_tables(db)
        await ensure_appointment_requests_table(db)
        await ensure_slot_tables(db)
        await ensure_doctor_schedule_rules_table(db)
        await ensure_doctor_time_off_table(db)

        await ensure_slot_holds_table(db)
        await ensure_appointments_table(db)
        await ensure_reminder_logs_table(db)
        await ensure_internal_billing_tables(db)
        await ensure_auth_tables(db)
        await seed_auth_defaults(db)
        await ensure_audit_logs_table(db)

    print("[startup] tables ensured")

    start_reminder_scheduler()


async def reminder_loop():
    while True:
        print("[reminder] checking appointments")

        try:
            await run_appointment_reminders()
        except Exception as e:
            print("[reminder error]", e)

        await asyncio.sleep(300)   # run every 5 minutes


async def subscription_loop():
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await enforce_subscription_status(db)

            print("[subscriptions] status check done")

        except Exception as e:
            print("[subscription error]", e)

        await asyncio.sleep(600)


# VERIFY WEBHOOK
# ============================================================

@app.get("/whatsapp/webhook")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)

    raise HTTPException(status_code=403)


# ============================================================
# MESSAGE EXTRACTOR
# ============================================================

def _extract_text_messages(body):
    out = []

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue

                out.append(
                    {
                        "msg_id": msg.get("id"),
                        "from_wa": msg.get("from"),
                        "text": msg.get("text", {}).get("body", ""),
                    }
                )

    return out


# ============================================================
# HELPERS
# ============================================================

_REF_RE = re.compile(r"appt_ref=([A-Z0-9\-]+)", re.I)


def _extract_or_make_request_id(payload):
    notes = payload.get("notes", "")
    m = _REF_RE.search(notes)

    if m:
        return m.group(1)

    return f"REQ-{uuid.uuid4().hex[:12].upper()}"


def _hhmm(x):
    if not x:
        return None

    s = str(x).strip()

    if not s:
        return None

    return s[:5]


def _parse_optional_date(value: Any) -> Optional[date]:
    s = str(value or "").strip()
    if not s:
        return None

    try:
        return date.fromisoformat(s)
    except Exception:
        return None


# ============================================================
# MATCH HOLD IF MISSING
# ============================================================

async def _maybe_match_hold_if_missing_id(db, tenant_id, user_id, payload):
    if not AUTO_MATCH_HOLD_IF_MISSING_ID:
        return None

    doctor_key = payload.get("doctor_key")
    appt_date = payload.get("appt_date")
    appt_time = _hhmm(payload.get("appt_time"))

    if not doctor_key or not appt_date or not appt_time:
        return None

    r = await db.execute(
        text(
            """
            SELECT hold_id
            FROM slot_holds
            WHERE tenant_id=:tenant
            AND user_id=:user
            AND doctor_key=:doctor
            AND slot_date=:date
            AND to_char(slot_time,'HH24:MI')=:time
            AND status='HELD'
            AND expires_at>NOW()
            LIMIT 1
            """
        ),
        dict(
            tenant=tenant_id,
            user=user_id,
            doctor=doctor_key,
            date=appt_date,
            time=appt_time,
        ),
    )

    row = r.mappings().first()

    return row["hold_id"] if row else None


# ============================================================
# CONFIRM HOLD
# ============================================================

async def _maybe_confirm_hold_for_action(db, tenant_id, user_id, payload):
    if not AUTO_CONFIRM_HOLDS:
        return None

    hold_id = payload.get("hold_id")

    if not hold_id:
        return None

    try:
        out = await confirm_hold_create_appointment(
            db=db,
            tenant_id=tenant_id,
            hold_id=hold_id,
            patient_name=payload.get("patient_name"),
            patient_mobile=payload.get("patient_mobile"),
            notes=payload.get("notes"),
        )

        return {"ok": True, "hold_id": hold_id, "result": out}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
# PERSIST ENGINE ACTIONS
# ============================================================

async def _persist_engine_actions(db, tenant_id, user_id, actions):
    side_effects = []

    for a in actions:
        if a.get("type") != "CREATE_APPOINTMENT_REQUEST":
            continue

        payload = a.get("payload") or {}

        request_id = _extract_or_make_request_id(payload)
        appt_time = _hhmm(payload.get("appt_time"))
        insurance_expiry = _parse_optional_date(payload.get("insurance_expiry"))

        await db.execute(
            text(
                """
                INSERT INTO appointment_requests (
                    tenant_id,request_id,channel,user_id,
                    status,intent,
                    dept_key,dept_label,
                    doctor_key,doctor_label,
                    appt_date,appt_time,
                    patient_name,patient_mobile,
                    notes,
                    insurance_provider,
                    insurance_plan,
                    insurance_member_id,
                    insurance_policy_number,
                    insurance_expiry,
                    insurance_status,
                    insurance_verified,
                    created_at,updated_at
                )
                VALUES(
                    :tenant,:rid,'whatsapp',:user,
                    :status,:intent,
                    :dept_key,:dept_label,
                    :doctor_key,:doctor_label,
                    :date,:time,
                    :name,:mobile,
                    :notes,
                    :insurance_provider,
                    :insurance_plan,
                    :insurance_member_id,
                    :insurance_policy_number,
                    :insurance_expiry,
                    :insurance_status,
                    :insurance_verified,
                    NOW(),NOW()
                )
                ON CONFLICT (request_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    dept_key = EXCLUDED.dept_key,
                    dept_label = EXCLUDED.dept_label,
                    doctor_key = EXCLUDED.doctor_key,
                    doctor_label = EXCLUDED.doctor_label,
                    appt_date = EXCLUDED.appt_date,
                    appt_time = EXCLUDED.appt_time,
                    patient_name = EXCLUDED.patient_name,
                    patient_mobile = EXCLUDED.patient_mobile,
                    notes = EXCLUDED.notes,
                    insurance_provider = COALESCE(EXCLUDED.insurance_provider, appointment_requests.insurance_provider),
                    insurance_plan = COALESCE(EXCLUDED.insurance_plan, appointment_requests.insurance_plan),
                    insurance_member_id = COALESCE(EXCLUDED.insurance_member_id, appointment_requests.insurance_member_id),
                    insurance_policy_number = COALESCE(EXCLUDED.insurance_policy_number, appointment_requests.insurance_policy_number),
                    insurance_expiry = COALESCE(EXCLUDED.insurance_expiry, appointment_requests.insurance_expiry),
                    insurance_status = COALESCE(EXCLUDED.insurance_status, appointment_requests.insurance_status),
                    insurance_verified = COALESCE(EXCLUDED.insurance_verified, appointment_requests.insurance_verified),
                    updated_at = NOW()
                """
            ),
            dict(
                tenant=tenant_id,
                rid=request_id,
                user=user_id,
                status=payload.get("status", "PENDING"),
                intent=payload.get("intent", "BOOK"),
                dept_key=payload.get("dept_key"),
                dept_label=payload.get("dept_label"),
                doctor_key=payload.get("doctor_key"),
                doctor_label=payload.get("doctor_label"),
                date=payload.get("appt_date"),
                time=appt_time,
                name=payload.get("patient_name"),
                mobile=payload.get("patient_mobile"),
                notes=payload.get("notes"),
                insurance_provider=payload.get("insurance_provider"),
                insurance_plan=payload.get("insurance_plan"),
                insurance_member_id=payload.get("insurance_member_id"),
                insurance_policy_number=payload.get("insurance_policy_number"),
                insurance_expiry=insurance_expiry,
                insurance_status=payload.get("insurance_status") or "SELF_PAY",
                insurance_verified=payload.get("insurance_verified", False),
            ),
        )

        if AUTO_MATCH_HOLD_IF_MISSING_ID and not payload.get("hold_id"):
            hold_id = await _maybe_match_hold_if_missing_id(
                db, tenant_id, user_id, payload
            )

            if hold_id:
                payload["hold_id"] = hold_id
                side_effects.append({"type": "hold_matched", "hold_id": hold_id})

        hold_effect = await _maybe_confirm_hold_for_action(
            db, tenant_id, user_id, payload
        )

        if hold_effect:
            side_effects.append(hold_effect)

    return side_effects


# ============================================================
# WHATSAPP WEBHOOK
# ============================================================

@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    messages = _extract_text_messages(body)

    for m in messages:
        msg_id = m["msg_id"]
        from_wa = m["from_wa"]
        text_in = m["text"]

        print("[LOCAL WEBHOOK HIT]", TENANT_ID, from_wa, text_in)
        print("[webhook]", from_wa, text_in)

        async with AsyncSessionLocal() as db:
            claimed = await claim_message_once(
                db,
                tenant_id=TENANT_ID,
                msg_id=msg_id,
                wa_from=from_wa,
                phone_number_id=WA_PHONE_NUMBER_ID,
            )

            if not claimed:
                continue

            from core.usage_logger import log_usage_event

            await log_usage_event(
                db,
                TENANT_ID,
                "whatsapp_messages",
                1
            )

        reply_text = ""

        try:
            async with AsyncSessionLocal() as db:
                reply_text, meta = await handle_message(
                    db=db,
                    user_id=from_wa,
                    message_text=text_in,
                    tenant_id=TENANT_ID,
                )

                actions = meta.get("actions") if isinstance(meta, dict) else None

                if actions:
                    effects = await _persist_engine_actions(
                        db, TENANT_ID, from_wa, actions
                    )

                    await db.commit()

                    if effects:
                        print("[effects]", effects)

        except Exception as e:
            print("[engine error]", e)

        if reply_text:
            try:
                wa_send_text(from_wa, reply_text)
            except Exception as e:
                print("[wa_send error]", e)

    return JSONResponse({"ok": True})