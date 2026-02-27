# whatsapp_controller.py
# Thin WhatsApp Controller (Stable + Tenant-aware)
# Fixes:
# - CLOSED + thanks/ack -> polite response (no engine call)
# - Script-aware language switch
# - Arabic digit normalization support (engine also supports)
# - Removes randomness caused by missing session fields

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
# Normalizers
# ----------------------------
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩١٢٣٤٥٦٧٨٩", "01234567890123456789")
def _normalize_digits(s: str) -> str:
    return (s or "").translate(_ARABIC_DIGITS)

# ----------------------------
# End-message patterns (close guard)
# ----------------------------
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
    r"^\s*شكراً\s*$",
    r"^\s*شكرًا\s*$",
    r"^\s*تمام\s*$",
    r"^\s*بس\s*$",
    r"^\s*هذا كل شيء\s*$",
]

def _is_end_message(text: str, language: str) -> bool:
    t = _normalize_digits((text or "").strip().lower())
    pats = _END_PATTERNS_AR if language == "ar" else _END_PATTERNS_EN
    return any(re.match(p, t, flags=re.IGNORECASE) for p in pats)

def _is_thanks_or_ack(text: str) -> bool:
    t = _normalize_digits((text or "").strip().lower())
    return t in {
        "thanks", "thank you", "thx", "ok", "okay", "done",
        "شكرا", "شكراً", "شكرًا", "تمام", "تم", "اوكي", "حسنًا", "حسنا"
    }

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(dt: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception:
        return None


# ----------------------------
# Strong language hint (script based)
# ----------------------------
_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

def _strong_language_hint(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    latin = len(_LATIN_RE.findall(t))
    arab = len(_ARABIC_RE.findall(t))
    if arab >= 3 and arab > latin:
        return "ar"
    if latin >= 3 and latin > arab:
        return "en"
    return None


# ----------------------------
# Env
# ----------------------------
SP_API_BASE = (os.getenv("SP_API_BASE", "http://127.0.0.1:8000") or "").strip()
WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


# ----------------------------
# Optional RAG helper
# ----------------------------
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
        return answer or ("عذرًا، لم أتمكن من الرد الآن." if language == "ar" else "Sorry — I couldn’t generate a response.")
    except Exception:
        return "System temporarily unavailable"

async def _call_supportpilot_chat(*, user_message: str, language: str, tenant_id: str) -> str:
    return await anyio.to_thread.run_sync(
        _call_supportpilot_chat_sync,
        user_message=user_message,
        language=language,
        tenant_id=tenant_id,
    )


# ----------------------------
# Escalation helper
# ----------------------------
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

    if language == "ar":
        if ticket_id:
            return (f"شكرًا لكم. تم تحويل طلبكم إلى موظف الاستقبال ✅ رقم التذكرة: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
        return ("شكرًا لكم. تم تحويل طلبكم إلى موظف الاستقبال ✅ وسيتم التواصل معكم قريبًا.", {"state": session["state"], "ticket_id": None})

    if ticket_id:
        return (f"Thanks — I’m transferring you to Reception ✅ Ticket ID: {ticket_id}", {"state": session["state"], "ticket_id": ticket_id})
    return ("Thanks — I’m transferring you to Reception ✅ A staff member will reply shortly during working hours.", {"state": session["state"], "ticket_id": None})


# ----------------------------
# MAIN entrypoint
# ----------------------------
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

    # normalize digits early (helps Arabic "١" etc.)
    message_text = _normalize_digits((message_text or "").strip())

    detected = (detect_language(message_text) or "en").strip().lower()
    preferred = (get_preferred_language(user_id) or "").strip().lower()

    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    if not isinstance(session, dict):
        session = {
            "user_id": user_id,
            "state": "ACTIVE",
            "language": "ar",
            "text_direction": "rtl",
            "has_greeted": False,
            "menu_shown": False,
            "timeout_pending": False,
            "last_user_message": None,
            "last_bot_message": "",
            "last_bot_ts": None,
            "last_user_ts": None,
            "last_closed_at": None,
            "conversation_version": 1,
        }

    # Ensure keys exist even for older sessions
    session.setdefault("has_greeted", False)
    session.setdefault("menu_shown", False)
    session.setdefault("timeout_pending", False)
    session.setdefault("last_closed_at", None)

    session["user_id"] = user_id
    session["last_user_message"] = message_text

    # Language resolution
    hint = _strong_language_hint(message_text)
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

    meta: Dict[str, Any] = {"state": session.get("state"), "tenant_id": tenant}

    # CLOSED + thanks/no -> do not call engine (prevents "choose 1/2/3")
    if session.get("state") == "CLOSED":
        if _is_thanks_or_ack(message_text) or _is_end_message(message_text, language=language):
            if language == "ar":
                reply = "يسعدنا خدمتك دائماً ✅ إذا رغبت بحجز جديد اكتب 0 لعرض القائمة."
            else:
                reply = "Happy to help ✅ If you’d like a new booking, reply 0 to see the menu."
            return reply, meta

    # Close-state guard (prevents “second No” reopening menu)
    last_closed_at = session.get("last_closed_at")
    closed_dt = _parse_iso(last_closed_at) if isinstance(last_closed_at, str) else None
    if session.get("state") == "CLOSED" and closed_dt is not None:
        recently_closed = (datetime.now(timezone.utc) - closed_dt) < timedelta(minutes=30)
        if recently_closed and _is_end_message(message_text, language=language):
            if language == "ar":
                return ("تم ✅ إذا احتجتم أي مساعدة لاحقًا يمكنكم مراسلتنا في أي وقت.", {"state": "CLOSED", "tenant_id": tenant})
            return ("All set ✅ If you need help later, message us anytime.", {"state": "CLOSED", "tenant_id": tenant})
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

    meta = {"state": session.get("state"), "tenant_id": tenant}

    priority = _get_customer_priority(user_id, session, kpi_signals)
    text_direction = session.get("text_direction", "ltr")

    for act in actions:
        if not isinstance(act, dict):
            continue

        atype = (act.get("type") or "").strip().upper()

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

    if _is_end_message(message_text, language=language):
        session["state"] = "CLOSED"
        session["last_closed_at"] = _utc_now_iso()

    try:
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
    except Exception:
        pass

    return reply_text or ("تم" if language == "ar" else "Done"), meta