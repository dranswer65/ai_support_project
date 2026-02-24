# whatsapp_controller.py
# Thin WhatsApp Controller (Stable + Tenant-aware)
# - Loads session from Postgres (core/session_store_pg.py) by (tenant_id, user_id)
# - Runs engine (core/engine.py)
# - Executes actions: CALL_RAG via internal /chat, ESCALATE via your escalation stack
# - Saves session back to Postgres

from __future__ import annotations
import re
import os
import requests
from typing import Any, Dict, Optional, Tuple, List

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone

from profiles.user_profile_store import get_preferred_language, set_language_preference
from incident.incident_state import is_incident_mode

# Optional audit (won’t crash if audit modules change)
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

from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session
import re
from datetime import datetime, timezone, timedelta
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
        "client_name": tenant_id,  # tenant-safe (sellable SaaS)
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
    return await anyio.to_thread.run_sync(_call_supportpilot_chat_sync, user_message=user_message, language=language, tenant_id=tenant_id)


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
    payload["conversation"]["context"].update(
        {
            "order_id": session.get("order_id"),
            "issue_summary": session.get("issue_summary", ""),
            "arabic_tone": arabic_tone,
            **extra_context,
        }
    )

    ticket_id = None
    try:
        routing = route_escalation(payload)
        result = await anyio.to_thread.run_sync(dispatch_ticket, payload, routing)
        ticket_id = _extract_ticket_id(result)
    except Exception:
        ticket_id = None

    session["state"] = "ESCALATION"

    if language == "ar":
        if ticket_id:
            return (f"شكرًا لك. تم تحويل طلبك للدعم البشري ✅ رقم التذكرة: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
        return ("شكرًا لك. تم تحويل طلبك للدعم البشري ✅ وسيتم التواصل معك قريبًا.", {"state": session["state"], "ticket_id": None})

    if ticket_id:
        return (f"Thanks — I’m escalating this to our support team ✅ Ticket ID: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
    return ("Thanks — I’m escalating this to our support team ✅ They will contact you shortly.", {"state": session["state"], "ticket_id": None})

async def handle_message(
    *,
    db: AsyncSession,
    user_id: str,
    message_text: str,
    tenant_id: Optional[str] = None,
    kpi_signals=None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Main entrypoint called by api_server background worker.
    Tenant-safe (sellable): sessions and RAG are scoped to tenant_id.
    """
    tenant = _norm_tenant(tenant_id)

    if kpi_signals is None:
        kpi_signals = []
    kpi_signals = list(kpi_signals)

    # Detect language from message + pull preferred from profile (if any)
    detected = (detect_language(message_text) or "en").strip().lower()
    preferred = (get_preferred_language(user_id) or "").strip().lower()

    # Load session from Postgres
    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict):
        # ✅ Demo default: Arabic-first
        session = {
            "user_id": user_id,
            "state": "ACTIVE",
            "language": "ar",
            "text_direction": "rtl",
            "has_greeted": False,
            "tries": 0,
            "no_count": 0,
            "asked_order_id_count": 0,
            "ai_attempts": 0,
            "order_id": None,
            "issue_summary": "",
            "last_user_message": None,
            "last_intent": None,
            "last_bot_message": "",
            "last_bot_ts": None,
            "last_user_ts": None,
            "conversation_version": 1,
        }

    session["user_id"] = user_id
    session["last_user_message"] = message_text

    # --------------------------------------------------
    # --------------------------------------------------
    # Language resolution (strong script-aware override)
    hint = _strong_language_hint(message_text)

    # If user clearly switched language, override previous preference
    if hint and hint != (session.get("language") or ""):
        language = hint
    else:
        language = preferred or (session.get("language") or "").strip().lower() or detected or "en"

    if language not in ("en", "ar"):
        language = "en"

    if session.get("language") != language:
        session["language"] = language
        try:
            set_language_preference(user_id, language)
        except Exception:
            pass

    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # -----------------------------
    # Close-state guard (prevents "second No" reopening intake)
    # -----------------------------
    last_closed_at = session.get("last_closed_at")
    closed_dt = _parse_iso(last_closed_at) if isinstance(last_closed_at, str) else None
    recently_closed = False
    if closed_dt is not None:
        recently_closed = (datetime.now(timezone.utc) - closed_dt) < timedelta(minutes=30)

    if session.get("state") == "CLOSED" and recently_closed:
        # If user repeats "no/thanks", do NOT restart intake.
        if _is_end_message(message_text, language=language):
            if language == "ar":
                return ("تم إذا احتجت أي شيء لاحقًا أنا بالخدمة.", {"state": "CLOSED"})
            return ("All set. If you need anything later, just message us.", {"state": "CLOSED"})

        # If they send a real new request, reopen gracefully.
        session["state"] = "ACTIVE"
        session["no_count"] = 0
        session["tries"] = 0

    if is_incident_mode():
        kpi_signals.append("incident_mode")
        log_event(
            incident_mode_event(
                user_id=user_id,
                conversation_version=session.get("conversation_version"),
            )
        )

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

    meta: Dict[str, Any] = {"state": session.get("state"), "tenant_id": tenant}

    priority = _get_customer_priority(user_id, session, kpi_signals)
    text_direction = session.get("text_direction", "ltr")

    for act in actions:
        if not isinstance(act, dict):
            continue
        atype = (act.get("type") or "").strip().upper()

        if atype == "CALL_RAG":
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
                extra_context={
                    "order_id": session.get("order_id"),
                    "issue_summary": session.get("issue_summary", ""),
                },
            )
            reply_text = esc_reply
            meta.update(esc_meta)

    # If user says "No/Thanks" and we already provided a closure or they want to end now -> close session.
    if _is_end_message(message_text, language=language):
        session["state"] = "CLOSED"
        session["last_closed_at"] = _utc_now_iso()

    try:
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
    except Exception:
        pass

    return reply_text or ("تم" if language == "ar" else "Done"), meta


