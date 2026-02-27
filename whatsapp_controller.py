# whatsapp_controller.py (PATCHED)
# Thin WhatsApp Controller (Stable + Tenant-aware)
#
# Patch includes:
# ✅ Greeting override BEFORE engine: greeting -> reset -> show main menu
# ✅ Politeness intent BEFORE engine: thanks -> friendly reply (no errors)
# ✅ Remove controller-forced close (engine owns state)
# ✅ Meta includes status/last_step
# ✅ Keeps your escalation + appointment request actions compatible

from __future__ import annotations

import os
import re
import requests
from typing import Any, Dict, Optional, Tuple, List

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone

from core.appointment_requests_store_pg import create_appointment_request
from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session

from profiles.user_profile_store import get_preferred_language, set_language_preference
from incident.incident_state import is_incident_mode

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

from escalation_router import route_escalation
from handoff_builder import build_handoff_payload
from vendor_orchestrator import dispatch_ticket

from datetime import datetime, timezone, timedelta


# ----------------------------
# Quick intent helpers (controller-level)
# ----------------------------
def _is_greeting_quick(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return (
        t in {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}
        or "السلام عليكم" in t
        or "مرحبا" in t
        or "اهلا" in t
        or "أهلا" in t
        or "هلا" in t
        or "صباح الخير" in t
        or "مساء الخير" in t
    )

def _is_thanks_quick(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"thanks", "thank you", "thx", "شكرا", "شكراً", "شكرًا", "مشكور", "الله يعطيك العافية"}


_END_PATTERNS_EN = [
    r"^\s*no\s*$",
    r"^\s*nope\s*$",
    r"^\s*that's all\s*$",
    r"^\s*thats all\s*$",
    r"^\s*nothing else\s*$",
    r"^\s*all good\s*$",
    r"^\s*ok\s*$",
    r"^\s*thanks\s*$",
    r"^\s*thank you\s*$",
]
_END_PATTERNS_AR = [
    r"^\s*لا\s*$",
    r"^\s*لا شكرا\s*$",
    r"^\s*لا شكرًا\s*$",
    r"^\s*شكرا\s*$",
    r"^\s*شكرًا\s*$",
    r"^\s*تمام\s*$",
    r"^\s*بس\s*$",
    r"^\s*هذا كل شيء\s*$",
]

def _is_end_message(text: str, language: str) -> bool:
    t = (text or "").strip().lower()
    pats = _END_PATTERNS_AR if language == "ar" else _END_PATTERNS_EN
    return any(re.match(p, t, flags=re.IGNORECASE) for p in pats)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(dt: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception:
        return None


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


SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


def _call_supportpilot_chat_sync(*, user_message: str, language: str, tenant_id: str) -> str:
    api_base = (SP_API_BASE or "").strip()
    if not api_base:
        return "System error: SP_API_BASE not configured"

    url = f"{api_base}/chat"
    payload = {
        "client_name": tenant_id,
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
        answer = (data.get("answer") or "").strip()
        if answer:
            return answer
        return "عذرًا، لم أتمكن من الرد الآن." if language == "ar" else "Sorry — I couldn’t generate a response."
    except Exception:
        return "System temporarily unavailable"

async def _call_supportpilot_chat(*, user_message: str, language: str, tenant_id: str) -> str:
    return await anyio.to_thread.run_sync(
        _call_supportpilot_chat_sync,
        user_message=user_message,
        language=language,
        tenant_id=tenant_id,
    )


def _extract_ticket_id(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    if result.get("ticket_id"):
        return result.get("ticket_id")

    inner = result.get("result")
    if isinstance(inner, dict):
        if inner.get("ticket_id"):
            return inner.get("ticket_id")
        if inner.get("id"):
            return inner.get("id")
        ticket_obj = inner.get("ticket")
        if isinstance(ticket_obj, dict):
            return ticket_obj.get("id") or ticket_obj.get("ticket_id") or ticket_obj.get("unique_external_id")
    return None

def _get_customer_priority(user_id: str, session: Dict[str, Any], kpi_signals: List[str]) -> Tuple[str, str]:
    if str(user_id).startswith("vip_"):
        return ("P0", "VIP customer")
    if session.get("state") == "ESCALATION":
        return ("P1", "Auto escalation")
    return ("P2", "Standard customer")


async def _escalate_to_human(
    *,
    tenant_id: str,
    user_id: str,
    session: Dict[str, Any],
    language: str,
    text_direction: str,
    arabic_tone: Optional[str],
    kpi_signals: List[str],
    priority: Tuple[str, str],
    decision_rule: str,
    decision_reason: str,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    extra_context = extra_context or {}

    log_event(
        escalation_event(
            user_id=user_id,
            conversation_version=session.get("conversation_version"),
            reason=decision_reason,
            rule=decision_rule,
            priority=priority[0],
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
    payload["meta"]["priority_level"] = priority[0]
    payload["meta"]["priority_reason"] = priority[1]

    payload.setdefault("conversation", {})
    payload["conversation"].setdefault("context", {})
    payload["conversation"]["context"].update({"arabic_tone": arabic_tone, **extra_context})

    ticket_id = None
    try:
        routing = route_escalation(payload)
        result = await anyio.to_thread.run_sync(dispatch_ticket, payload, routing)
        ticket_id = _extract_ticket_id(result)
    except Exception:
        ticket_id = None

    session["state"] = "ESCALATION"
    session["escalation_flag"] = True  # ✅ helpful signal for SaaS ops

    if language == "ar":
        if ticket_id:
            return (f"شكرًا لكم. تم تحويل طلبكم إلى موظف الاستقبال ✅ رقم التذكرة: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
        return ("شكرًا لكم. تم تحويل طلبكم إلى موظف الاستقبال ✅ وسيتم التواصل معكم قريبًا.", {"state": session["state"], "ticket_id": None})

    if ticket_id:
        return (f"Thanks — I’m transferring you to Reception ✅ Ticket ID: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
    return ("Thanks — I’m transferring you to Reception ✅ A staff member will reply shortly during working hours.", {"state": session["state"], "ticket_id": None})


def _resolve_language_for_turn(message_text: str, session: Dict[str, Any], user_id: str) -> str:
    raw = (message_text or "").strip()

    # If user sends only a number (menu choice), DO NOT re-detect language.
    # Keep the session language stable.
    if raw.isdigit():
        lang = (session.get("language") or "").strip().lower()
        return "ar" if lang.startswith("ar") else "en" if lang.startswith("en") else "ar"

    detected = (detect_language(message_text) or "en").strip().lower()
    preferred = (get_preferred_language(user_id) or "").strip().lower()

    hint = _strong_language_hint(message_text)

    # First greeting / new session: allow detection to win
    if not session.get("has_greeted"):
        lang = hint or detected or preferred or (session.get("language") or "ar")
    else:
        # after greeted: keep stable unless strong hint exists
        lang = hint or preferred or (session.get("language") or "") or detected or "en"

    lang = lang.strip().lower()
    if lang.startswith("ar"):
        return "ar"
    return "en"


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

    if kpi_signals is None:
        kpi_signals = []
    kpi_signals = list(kpi_signals)

    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict):
        session = {
            "user_id": user_id,
            "status": "ACTIVE",         # ✅ new
            "state": "ACTIVE",
            "last_step": "ACTIVE",      # ✅ new
            "language": "ar",
            "text_direction": "rtl",
            "has_greeted": False,
            "last_user_message": None,
            "last_bot_message": "",
            "last_bot_ts": None,
            "last_user_ts": None,
            "conversation_version": 1,
            "escalation_flag": False,   # ✅ new
        }

    session["user_id"] = user_id
    session["last_user_message"] = message_text

    language = _resolve_language_for_turn(message_text, session, user_id)

    if session.get("language") != language:
        session["language"] = language
        try:
            set_language_preference(user_id, language)
        except Exception:
            pass

    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # ---------------------------------------
    # ✅ Greeting override BEFORE engine
    # ---------------------------------------
    if _is_greeting_quick(message_text):
        session["status"] = "ACTIVE"
        session["state"] = "MENU"
        session["last_step"] = "MENU"
        session["timeout_pending"] = False
        session["escalation_flag"] = False

        out = (run_engine(session=session, user_message="0", language=language).get("reply_text") or "").strip()
        if not out:
            out = "0️⃣ القائمة الرئيسية" if language == "ar" else "0️⃣ Main Menu"

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"state": session.get("state"), "status": session.get("status"), "tenant_id": tenant}

    # ---------------------------------------
    # ✅ Politeness intent BEFORE engine
    # ---------------------------------------
    if _is_thanks_quick(message_text):
        if language == "ar":
            out = "العفو 😊\nإذا احتجت أي شيء آخر اكتب 0 لعرض القائمة."
        else:
            out = "You’re welcome 😊\nIf you need anything else, reply 0 for the main menu."
        # No forced state changes; just store session heartbeat
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"state": session.get("state"), "status": session.get("status"), "tenant_id": tenant}

    # Recently closed behavior
    last_closed_at = session.get("last_closed_at")
    closed_dt = _parse_iso(last_closed_at) if isinstance(last_closed_at, str) else None
    recently_closed = False
    if closed_dt is not None:
        recently_closed = (datetime.now(timezone.utc) - closed_dt) < timedelta(minutes=30)

    if session.get("state") == "CLOSED" and recently_closed:
        if _is_end_message(message_text, language=language):
            if language == "ar":
                return ("تم ✅ إذا احتجتم أي مساعدة لاحقًا يمكنكم مراسلتنا في أي وقت.", {"state": "CLOSED", "status": session.get("status"), "tenant_id": tenant})
            return ("All set ✅ If you need help later, message us anytime.", {"state": "CLOSED", "status": session.get("status"), "tenant_id": tenant})
        session["status"] = "ACTIVE"
        session["state"] = "ACTIVE"

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

    reply_text: str = (engine_out.get("reply_text") or "").strip()
    session = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
    actions = engine_out.get("actions") if isinstance(engine_out.get("actions"), list) else []

    # Keep status/last_step in meta for dashboard/debug
    meta: Dict[str, Any] = {
        "state": session.get("state"),
        "status": session.get("status"),
        "last_step": session.get("last_step"),
        "tenant_id": tenant,
    }

    priority = _get_customer_priority(user_id, session, kpi_signals)
    text_direction = session.get("text_direction", "ltr")

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

        elif atype == "CALL_RAG":
            query = (act.get("query") or "").strip()
            if query:
                answer = await _call_supportpilot_chat(
                    user_message=query,
                    language=language,
                    tenant_id=tenant,
                )
                reply_text = f"{reply_text}\n\n{answer}".strip()

        elif atype == "ESCALATE":
            rule = (act.get("rule") or "engine_escalation").strip()
            reason = (act.get("reason") or "Escalation requested by engine").strip()
            esc_reply, esc_meta = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=text_direction,
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
                priority=priority,
                decision_rule=rule,
                decision_reason=reason,
                extra_context={},
            )
            reply_text = esc_reply
            meta.update(esc_meta)

    # ❌ IMPORTANT PATCH:
    # Do NOT force-close sessions in controller based on end-message.
    # Engine owns deterministic state transitions.
    # (We keep _is_end_message only for "recently closed" polite reply above.)

    try:
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
    except Exception as e:
        meta["session_save_error"] = repr(e)

    return reply_text or ("تم" if language == "ar" else "Done"), meta