# whatsapp_controller.py — Enterprise-ready controller
# Emergency override + urgent window + sticky handoff + language lock + no duplicate tickets
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime, timezone, timedelta

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from language.language_detector import detect_language
from language.arabic_tone_engine import select_arabic_tone

from core.engine import run_engine
from core.session_store_pg import get_session, upsert_session

from incident.incident_state import is_incident_mode

from escalation_router import route_escalation
from handoff_builder import build_handoff_payload
from vendor_orchestrator import dispatch_ticket


WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

# Accept both 99 and 9 as reception shortcuts (enterprise UX)
_RECEPTION_ALIASES = {"99", "9"}

_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال", "موظف استقبال"
]

# ----------------------------
# Emergency keywords (expand over time)
# ----------------------------
_EMERGENCY_KEYS_EN = [
    "chest pain",
    "difficulty breathing",
    "can't breathe",
    "shortness of breath",
    "severe pain",
    "heavy bleeding",
    "bleeding heavily",
    "unconscious",
    "fainted",
    "stroke",
    "heart attack",
    "overdose",
]
_EMERGENCY_KEYS_AR = [
    "ألم في الصدر",
    "الم في الصدر",
    "ضيق تنفس",
    "ما اقدر اتنفس",
    "لا أستطيع التنفس",
    "لا استطيع التنفس",
    "نزيف شديد",
    "نزيف قوي",
    "فقدت الوعي",
    "اغمى عليه",
    "إغماء",
    "جلطة",
    "سكتة",
    "نوبة قلبية",
    "ألم شديد",
    "الم شديد",
    "الم في الكلى",
    "الم في البطن",
    "ألم في البطن",
]

URGENT_WINDOW_MINUTES = 30
HANDOFF_WINDOW_MINUTES = 30


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _wants_agent(text: str) -> bool:
    t = (text or "").strip().lower()
    if t in _RECEPTION_ALIASES:
        return True
    return any(k in t for k in _AGENT_KEYS)


def _handoff_active(session: Dict[str, Any]) -> bool:
    if not isinstance(session, dict):
        return False
    if not bool(session.get("handoff_active")):
        return False
    until = _parse_iso(session.get("handoff_until")) if isinstance(session.get("handoff_until"), str) else None
    if until and _now_utc() <= until:
        return True
    session["handoff_active"] = False
    session["handoff_until"] = None
    return False


def _urgent_active(session: Dict[str, Any]) -> bool:
    if not isinstance(session, dict):
        return False
    until = _parse_iso(session.get("urgent_until")) if isinstance(session.get("urgent_until"), str) else None
    if until and _now_utc() <= until:
        return True
    session["urgent_flag"] = False
    session["urgent_until"] = None
    session["urgent_ack"] = False
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


def _short_ref(ticket_id: Optional[str]) -> Optional[str]:
    if not ticket_id or not isinstance(ticket_id, str):
        return None
    head = ticket_id.split("-")[0].upper()
    return head[:8] if head else None


def _resolve_language_for_turn(message_text: str, session: Dict[str, Any]) -> str:
    # If locked, never change
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    raw = (message_text or "").strip()
    # numeric inputs should not trigger re-detection
    if raw.isdigit():
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _is_emergency(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(k in t for k in _EMERGENCY_KEYS_EN):
        return True
    # Arabic contains checks
    if any(k in (text or "") for k in _EMERGENCY_KEYS_AR):
        return True
    return False


def _emergency_compact(language: str, short_ref: Optional[str]) -> str:
    """
    ONE clean merged message: safety + forwarded + ref + return option.
    """
    if language == "ar":
        ref_line = f"✅ تم تحويل طلبكم إلى موظف الاستقبال. رقم الطلب: #{short_ref}\n" if short_ref else "✅ تم تحويل طلبكم إلى موظف الاستقبال.\n"
        return (
            "🚨 قد تكون هذه حالة طارئة.\n"
            "يرجى الاتصال فورًا على 997 أو التوجه إلى أقرب قسم طوارئ.\n\n"
            + ref_line +
            "للعودة للقائمة اكتب 0"
        )

    ref_line = f"✅ Your request was forwarded to Reception. Ref: #{short_ref}\n" if short_ref else "✅ Your request was forwarded to Reception.\n"
    return (
        "🚨 Severe symptoms can be serious.\n"
        "Please call 997 immediately or go to the nearest emergency department.\n\n"
        + ref_line +
        "Reply 0 for the menu"
    )


def _handoff_message(language: str, short_ref: Optional[str]) -> str:
    if language == "ar":
        if short_ref:
            return f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0"
        return "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
    if short_ref:
        return f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu"
    return "Connecting you to Reception ✅\nReply 0 for the menu"


def _urgent_gate_menu(language: str) -> str:
    """
    Shown when user tries to proceed while urgent window is active (enterprise healthcare safety).
    """
    if language == "ar":
        return (
            "⚠️ لاحظنا أنك ذكرت أعراضًا قد تتطلب رعاية عاجلة.\n"
            "هل أنت بخير الآن وتريد المتابعة بالحجز؟\n\n"
            "1️⃣ نعم، متابعة الحجز\n"
            "9️⃣ موظف الاستقبال\n"
            "🚑 للطوارئ اتصل 997\n\n"
            "0️⃣ القائمة الرئيسية"
        )
    return (
        "⚠️ You mentioned symptoms that may need urgent care.\n"
        "Are you safe now and want to continue booking?\n\n"
        "1️⃣ Yes, continue booking\n"
        "9️⃣ Reception\n"
        "🚑 For emergencies call 997\n\n"
        "0️⃣ Main Menu"
    )


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
    urgent: bool = False,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Returns (ticket_id, meta). Does NOT compose user-facing text (so we can merge messages cleanly).
    """
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
    payload["meta"]["urgent"] = bool(urgent)

    ticket_id = None
    try:
        routing = route_escalation(payload)
        result = await anyio.to_thread.run_sync(dispatch_ticket, payload, routing)
        ticket_id = _extract_ticket_id(result)
    except Exception:
        ticket_id = None

    # Sticky handoff window
    session["handoff_active"] = True
    session["handoff_until"] = (_now_utc() + timedelta(minutes=HANDOFF_WINDOW_MINUTES)).isoformat()
    session["state"] = "ESCALATION"
    session["last_step"] = "ESCALATION"
    session["escalation_flag"] = True
    if urgent:
        session["urgent_flag"] = True
        session["urgent_until"] = (_now_utc() + timedelta(minutes=URGENT_WINDOW_MINUTES)).isoformat()
        session["urgent_ack"] = False

    return ticket_id, {"ticket_id": ticket_id, "urgent": urgent}


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
            "conversation_version": 4,
            "escalation_flag": False,
            "urgent_flag": False,
            "urgent_until": None,
            "urgent_ack": False,
            "handoff_active": False,
            "handoff_until": None,
        }

    session["last_user_message"] = message_text
    raw = (message_text or "").strip()

    # ---------------------------------------------------------
    # 1) EMERGENCY OVERRIDE (highest priority)
    # ---------------------------------------------------------
    if _is_emergency(message_text):
        language = "ar" if (detect_language(message_text) or "").lower().startswith("ar") else "en"
        session["language"] = language
        session["text_direction"] = "rtl" if language == "ar" else "ltr"
        arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

        session["urgent_flag"] = True
        session["urgent_until"] = (_now_utc() + timedelta(minutes=URGENT_WINDOW_MINUTES)).isoformat()
        session["urgent_ack"] = False
        kpi_signals.append("emergency_detected")

        # If already handed off, do NOT create a new ticket; just send safety + "already forwarded"
        if _handoff_active(session):
            # keep urgent window active
            short_ref = _short_ref(session.get("handoff_ticket_id"))
            out = _emergency_compact(language, short_ref)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True}

        ticket_id, esc_meta = await _escalate_to_human(
            tenant_id=tenant,
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            decision_rule="controller_emergency_override",
            decision_reason="Emergency keywords detected",
            urgent=True,
        )
        session["handoff_ticket_id"] = ticket_id
        out = _emergency_compact(language, _short_ref(ticket_id))

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True}
        meta.update(esc_meta)
        return out, meta

    # ---------------------------------------------------------
    # Resolve language normally (non-emergency)
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(message_text, session)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # ---------------------------------------------------------
    # 2) Sticky handoff silence (enterprise)
    # ---------------------------------------------------------
    if _handoff_active(session):
        if raw == "0":
            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
        else:
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return "", {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}

    # ---------------------------------------------------------
    # 3) Urgent safety gate: block booking/menu until acknowledged
    # ---------------------------------------------------------
    if _urgent_active(session) and not bool(session.get("urgent_ack")):
        # allow direct reception shortcut
        if raw in _RECEPTION_ALIASES or _wants_agent(message_text):
            # do NOT create new ticket if user just exited handoff and presses again quickly:
            # (if you WANT a new ticket, remove this condition)
            if session.get("handoff_ticket_id"):
                out = _handoff_message(language, _short_ref(session.get("handoff_ticket_id")))
                await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
                return out, {"tenant_id": tenant, "state": "ESCALATION", "handoff_active": True, "urgent": True}

            ticket_id, esc_meta = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals + ["urgent_gate_to_reception"],
                decision_rule="controller_urgent_gate",
                decision_reason="User requested reception during urgent window",
                urgent=True,
            )
            session["handoff_ticket_id"] = ticket_id
            out = _handoff_message(language, _short_ref(ticket_id))
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True}
            meta.update(esc_meta)
            return out, meta

        # allow confirm continue booking only
        if raw == "1":
            session["urgent_ack"] = True
            # continue to engine below
        else:
            out = _urgent_gate_menu(language)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": False, "urgent": True}

    # ---------------------------------------------------------
    # 4) Agent override (non-emergency)
    # ---------------------------------------------------------
    if _wants_agent(message_text):
        # if ticket already exists recently, avoid making another
        if session.get("handoff_ticket_id"):
            out = _handoff_message(language, _short_ref(session.get("handoff_ticket_id")))
            session["handoff_active"] = True
            session["handoff_until"] = (_now_utc() + timedelta(minutes=HANDOFF_WINDOW_MINUTES)).isoformat()
            session["state"] = "ESCALATION"
            session["last_step"] = "ESCALATION"
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}

        ticket_id, esc_meta = await _escalate_to_human(
            tenant_id=tenant,
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=arabic_tone,
            kpi_signals=kpi_signals,
            decision_rule="controller_agent_override",
            decision_reason="User requested reception",
            urgent=False,
        )
        session["handoff_ticket_id"] = ticket_id
        out = _handoff_message(language, _short_ref(ticket_id))

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}
        meta.update(esc_meta)
        return out, meta

    # ---------------------------------------------------------
    # 5) Incident mode flag
    # ---------------------------------------------------------
    if is_incident_mode():
        kpi_signals.append("incident_mode")

    # ---------------------------------------------------------
    # 6) Normal engine routing
    # ---------------------------------------------------------
    engine_out = run_engine(
        session=session,
        user_message=message_text,
        language=language,
        arabic_tone=arabic_tone,
        kpi_signals=kpi_signals,
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    session = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

    await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

    return reply_text, {
        "tenant_id": tenant,
        "state": session.get("state"),
        "status": session.get("status"),
        "last_step": session.get("last_step"),
        "language": session.get("language"),
        "language_locked": session.get("language_locked"),
        "handoff_active": session.get("handoff_active"),
        "urgent_flag": bool(session.get("urgent_flag")),
        "urgent_ack": bool(session.get("urgent_ack")),
    }