# whatsapp_controller.py — Enterprise-aligned controller with HARD handoff lock

from __future__ import annotations

import os
import re
import requests
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime, timezone, timedelta

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone

from core.appointment_requests_store_pg import create_appointment_request
from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session

from profiles.user_profile_store import get_preferred_language, set_language_preference
from incident.incident_state import is_incident_mode

from escalation_router import route_escalation
from handoff_builder import build_handoff_payload
from vendor_orchestrator import dispatch_ticket

try:
    from compliance.audit_logger import log_event
    from compliance.audit_events import escalation_event, incident_mode_event
except Exception:
    def log_event(*args, **kwargs):  # type: ignore
        return None
    def escalation_event(**kwargs):  # type: ignore
        return {"event": "escalation", **kwargs}
    def incident_mode_event(**kwargs):  # type: ignore
        return {"event": "incident_mode", **kwargs}

SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال"
]

def _wants_agent(text: str) -> bool:
    t = (text or "").strip().lower()
    if t == "99":
        return True
    return any(k in t for k in _AGENT_KEYS)

def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _handoff_active(session: Dict[str, Any]) -> bool:
    if not isinstance(session, dict):
        return False
    if not bool(session.get("handoff_active")):
        return False
    until = _parse_iso(session.get("handoff_until")) if isinstance(session.get("handoff_until"), str) else None
    if until and datetime.now(timezone.utc) <= until:
        return True
    session["handoff_active"] = False
    session["handoff_until"] = None
    return False


def _extract_ticket_id(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    if result.get("ticket_id"):
        return result.get("ticket_id")
    inner = result.get("result")
    if isinstance(inner, dict):
        return inner.get("ticket_id") or inner.get("id")
    return None


async def _escalate_to_human(
    *,
    tenant_id: str,
    user_id: str,
    session: Dict[str, Any],
    language: str,
    text_direction: str,
    arabic_tone: Optional[str],
    kpi_signals: List[str],
    decision_rule: str,
    decision_reason: str,
) -> Tuple[str, Dict[str, Any]]:
    log_event(
        escalation_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version"),
            reason=decision_reason,
            rule=decision_rule,
            priority="P2",
        )
    )

    payload = build_handoff_payload(
        user_id=user_id,
        current_state=session.get("state"),
        last_user_message=session.get("last_user_message"),
        last_intent=session.get("last_intent"),
        decision_rule=decision_rule,
        decision_reason=decision_reason,
        kpi_signals=kpi_signals,
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
        ticket_id = None

    # HARD LOCK after transfer
    session["handoff_active"] = True
    session["handoff_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    session["state"] = "ESCALATION"
    session["escalation_flag"] = True

    if language == "ar":
        if ticket_id:
            return (f"تم تحويلكم إلى موظف الاستقبال ✅ رقم التذكرة: {ticket_id}", {"ticket_id": ticket_id})
        return ("تم تحويلكم إلى موظف الاستقبال ✅ يرجى الانتظار...", {"ticket_id": None})

    if ticket_id:
        return (f"Connecting you to Reception ✅ Ticket ID: {ticket_id}", {"ticket_id": ticket_id})
    return ("Connecting you to Reception ✅ Please wait...", {"ticket_id": None})


def _resolve_language_for_turn(message_text: str, session: Dict[str, Any], user_id: str) -> str:
    if bool(session.get("language_locked")):
        lang = (session.get("language") or "ar").strip().lower()
        return "ar" if lang.startswith("ar") else "en"

    raw = (message_text or "").strip()
    if raw.isdigit():
        lang = (session.get("language") or "").strip().lower()
        return "ar" if lang.startswith("ar") else "en" if lang.startswith("en") else "ar"

    preferred = (get_preferred_language(user_id) or "").strip().lower()
    detected = (detect_language(message_text) or "en").strip().lower()
    lang = preferred or (session.get("language") or "") or detected or "ar"
    return "ar" if lang.startswith("ar") else "en"


def _normalize_appointment_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    p = dict(payload or {})
    if "appt_date" not in p and "date" in p:
        p["appt_date"] = p.get("date")
    if "appt_time" not in p and "slot" in p:
        p["appt_time"] = p.get("slot")
    p["intent"] = (p.get("intent") or "BOOK").strip().upper()
    p["status"] = (p.get("status") or "PENDING").strip().upper()
    return p


async def handle_message(
    *,
    db: AsyncSession,
    user_id: str,
    message_text: str,
    tenant_id: Optional[str] = None,
    kpi_signals=None,
) -> Tuple[str, Dict[str, Any]]:
    tenant = _norm_tenant(tenant_id)
    kpi_signals = list(kpi_signals or [])

    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict):
        session = {
            "user_id": user_id,
            "status": "ACTIVE",
            "state": "LANG_SELECT",
            "last_step": "LANG_SELECT",
            "language": "ar",
            "language_locked": False,
            "text_direction": "rtl",
            "has_greeted": False,
            "conversation_version": 1,
            "escalation_flag": False,
            "handoff_active": False,
            "handoff_until": None,
        }

    session["last_user_message"] = message_text

    # ✅ If already handed off, IGNORE everything (enterprise behavior)
    if _handoff_active(session):
        # optional: allow “0” to return to menu if you want
        if (message_text or "").strip() == "0":
            session["handoff_active"] = False
            session["handoff_until"] = None
        else:
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return "", {"state": session.get("state"), "handoff_active": True, "tenant_id": tenant}

    language = _resolve_language_for_turn(message_text, session, user_id)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # ✅ HARD PRIORITY: agent request BEFORE engine (never miss it)
    if _wants_agent(message_text):
        reply, extra = await _escalate_to_human(
            tenant_id=tenant,
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            decision_rule="controller_agent_override",
            decision_reason="User requested reception",
        )
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}
        meta.update(extra)
        return reply, meta

    if is_incident_mode():
        kpi_signals.append("incident_mode")
        log_event(incident_mode_event(user_id=user_id, conversation_version=session.get("conversation_version")))

    engine_out = run_engine(
        session=session,
        user_message=message_text,
        language=language,
        arabic_tone=arabic_tone,
        kpi_signals=kpi_signals,
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    session = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
    actions = engine_out.get("actions") if isinstance(engine_out.get("actions"), list) else []

    meta: Dict[str, Any] = {
        "tenant_id": tenant,
        "state": session.get("state"),
        "status": session.get("status"),
        "last_step": session.get("last_step"),
        "language": session.get("language"),
        "language_locked": session.get("language_locked"),
        "handoff_active": session.get("handoff_active"),
    }

    for act in actions:
        if not isinstance(act, dict):
            continue
        atype = (act.get("type") or "").strip().upper()

        if atype == "CREATE_APPOINTMENT_REQUEST":
            payload = _normalize_appointment_payload(act.get("payload") or {})
            try:
                req_id = await create_appointment_request(
                    db=db,
                    tenant_id=tenant,
                    user_id=user_id,
                    payload=payload,
                )
                meta["appointment_request_id"] = req_id
            except Exception as e:
                meta["appointment_request_error"] = repr(e)

        elif atype == "ESCALATE":
            # engine escalation also locks
            reply, extra = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
                decision_rule="engine_escalation",
                decision_reason=(act.get("reason") or "Escalation requested"),
            )
            reply_text = reply
            meta.update(extra)

    await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
    return reply_text, meta