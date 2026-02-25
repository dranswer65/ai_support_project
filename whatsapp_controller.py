# whatsapp_controller.py
# Stable WhatsApp Controller for Clinic SaaS (Production Safe)

from __future__ import annotations
import re
import os
import requests
from typing import Any, Dict, Optional, Tuple, List

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone
from core.appointment_requests_store_pg import create_appointment_request

from profiles.user_profile_store import get_preferred_language, set_language_preference
from incident.incident_state import is_incident_mode

# Optional audit safe import
try:
    from compliance.audit_logger import log_event
    from compliance.audit_events import escalation_event, incident_mode_event
except Exception:
    def log_event(*args, **kwargs): return None
    def escalation_event(**kwargs): return {"event": "escalation", **kwargs}
    def incident_mode_event(**kwargs): return {"event": "incident_mode", **kwargs}

from escalation_router import route_escalation
from handoff_builder import build_handoff_payload
from vendor_orchestrator import dispatch_ticket

from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session

from datetime import datetime, timezone, timedelta

# -----------------------------
# End detection
# -----------------------------
_END_PATTERNS_EN = [
    r"^\s*no\s*$",
    r"^\s*thanks\s*$",
    r"^\s*thank you\s*$",
    r"^\s*ok\s*$",
]

_END_PATTERNS_AR = [
    r"^\s*لا\s*$",
    r"^\s*شكرا\s*$",
    r"^\s*شكرًا\s*$",
    r"^\s*تمام\s*$",
]

def _is_end_message(text: str, language: str) -> bool:
    t = (text or "").strip().lower()
    pats = _END_PATTERNS_AR if language == "ar" else _END_PATTERNS_EN
    return any(re.match(p, t, flags=re.IGNORECASE) for p in pats)

# -----------------------------
# Language hint detection
# -----------------------------
_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

def _strong_language_hint(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    latin = len(_LATIN_RE.findall(t))
    arab  = len(_ARABIC_RE.findall(t))
    if arab >= 3 and arab > latin:
        return "ar"
    if latin >= 3 and latin > arab:
        return "en"
    return None

# -----------------------------
# ENV
# -----------------------------
SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"

# -----------------------------
# RAG CALL (optional future)
# -----------------------------
def _call_supportpilot_chat_sync(*, user_message: str, language: str, tenant_id: str) -> str:
    url = f"{SP_API_BASE}/chat"
    payload = {
        "client_name": tenant_id,
        "question": user_message,
        "tone": "formal",
        "language": "ar" if language == "ar" else "en",
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            return "AI error"
        data = r.json()
        return (data.get("answer") or "").strip()
    except Exception:
        return "System temporarily unavailable"

async def _call_supportpilot_chat(*, user_message: str, language: str, tenant_id: str) -> str:
    return await anyio.to_thread.run_sync(
        _call_supportpilot_chat_sync,
        user_message=user_message,
        language=language,
        tenant_id=tenant_id,
    )

# -----------------------------
# Escalation
# -----------------------------
def _extract_ticket_id(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    return result.get("ticket_id")

def _get_customer_priority(user_id: str, session: Dict[str, Any]) -> Tuple[str, str]:
    if str(user_id).startswith("vip_"):
        return ("P0", "VIP")
    if session.get("state") == "ESCALATION":
        return ("P1", "Escalation")
    return ("P2", "Normal")

async def _escalate_to_human(
    *,
    tenant_id: str,
    user_id: str,
    session: Dict[str, Any],
    language: str,
    text_direction: str,
) -> Tuple[str, Dict[str, Any]]:

    payload = build_handoff_payload(
        user_id=user_id,
        current_state=session.get("state"),
        last_user_message=session.get("last_user_message"),
        last_intent=session.get("intent"),
        decision_rule="user_request",
        decision_reason="user_requested_reception",
        kpi_signals=[],
    )

    payload.setdefault("meta", {})
    payload["meta"]["tenant_id"] = tenant_id
    payload["meta"]["language"] = language
    payload["meta"]["text_direction"] = text_direction

    ticket_id = None
    try:
        routing = route_escalation(payload)
        result = await anyio.to_thread.run_sync(dispatch_ticket, payload, routing)
        ticket_id = _extract_ticket_id(result)
    except Exception:
        pass

    session["state"] = "ESCALATION"

    if language == "ar":
        msg = "يتم تحويلكم الآن إلى موظف الاستقبال. سيتم الرد قريبًا."
    else:
        msg = "Transferring you to reception. A staff member will reply shortly."

    return msg, {"ticket_id": ticket_id}

# -----------------------------
# MAIN HANDLER
# -----------------------------
async def handle_message(
    *,
    db: AsyncSession,
    user_id: str,
    message_text: str,
    tenant_id: Optional[str] = None,
    kpi_signals=None,
) -> Tuple[str, Dict[str, Any]]:

    tenant = _norm_tenant(tenant_id)

    # -------------------------
    # Load session
    # -------------------------
    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict):
        session = {
            "user_id": user_id,
            "state": "ACTIVE",
            "language": "ar",
            "text_direction": "rtl",
            "has_greeted": False,
        }

    session["user_id"] = user_id
    session["last_user_message"] = message_text

    # -------------------------
    # Language resolution
    # -------------------------
    detected = (detect_language(message_text) or "en").lower()
    preferred = (get_preferred_language(user_id) or "").lower()
    hint = _strong_language_hint(message_text)

    language = hint or preferred or session.get("language") or detected or "en"
    if language not in ("ar", "en"):
        language = "en"

    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"

    try:
        set_language_preference(user_id, language)
    except Exception:
        pass

    # -------------------------
    # Run engine safely
    # -------------------------
    try:
        engine_out = run_engine(
            session=session,
            user_message=message_text,
            language=language,
        )
    except Exception as e:
        print("[ENGINE ERROR]", repr(e))
        return ("System error. Please try again." if language=="en" else "حدث خطأ تقني، حاول مرة أخرى"), {}

    reply_text: str = (engine_out.get("reply_text") or "").strip()
    session = engine_out.get("session") or session
    actions = engine_out.get("actions") or []

    meta: Dict[str, Any] = {"state": session.get("state"), "tenant_id": tenant}

    # -------------------------
    # Execute actions
    # -------------------------
    for act in actions:
        atype = (act.get("type") or "").upper()

        # SAVE APPOINTMENT
        if atype == "CREATE_APPOINTMENT_REQUEST":
            payload = act.get("payload") or {}
            try:
                req_id = await create_appointment_request(
                    db=db,
                    tenant_id=tenant,
                    user_id=user_id,
                    payload=payload,
                )
                meta["appointment_request_id"] = req_id
            except Exception as e:
                print("[APPOINTMENT SAVE ERROR]", repr(e))

        # ESCALATE
        elif atype == "ESCALATE":
            esc_reply, esc_meta = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
            )
            reply_text = esc_reply
            meta.update(esc_meta)

    # -------------------------
    # Close detection
    # -------------------------
    if _is_end_message(message_text, language):
        session["state"] = "CLOSED"
        session["last_closed_at"] = datetime.now(timezone.utc).isoformat()

    # -------------------------
    # Save session
    # -------------------------
    try:
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
    except Exception as e:
        print("[SESSION SAVE ERROR]", repr(e))

    return reply_text or ("تم" if language=="ar" else "Done"), meta