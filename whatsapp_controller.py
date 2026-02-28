# whatsapp_controller.py — Enterprise-ready controller (Emergency override + emergency hold + sticky handoff + language lock)

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

_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال", "موظف استقبال"
]

# ----------------------------
# Emergency keywords (expanded)
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
    "seizure",
    "seizures",
    "pregnant bleeding",
    "pregnancy bleeding",
    "miscarriage",
]

# NOTE: include single "نزيف" and pregnancy words. This fixes: "زوجتي حامل لديها نزيف"
_EMERGENCY_KEYS_AR = [
    "ألم في الصدر",
    "الم في الصدر",
    "ضيق تنفس",
    "صعوبة تنفس",
    "ما اقدر اتنفس",
    "لا أستطيع التنفس",
    "لا استطيع التنفس",

    "نزيف",
    "نزيف شديد",
    "نزيف قوي",
    "نزف",

    "حامل",
    "حمل",
    "إجهاض",
    "اسقاط",
    "نزيف مع حمل",

    "فقدت الوعي",
    "فقدان الوعي",
    "اغمى عليه",
    "إغماء",
    "تشنج",
    "تشنجات",

    "جلطة",
    "سكتة",
    "نوبة قلبية",

    "ألم شديد",
    "الم شديد",
    "الم في الكلى",
    "ألم في الكلى",
    "الم في البطن",
    "ألم في البطن",
]

# How long we keep a safety guard after emergency
EMERGENCY_HOLD_MINUTES = 30
HANDOFF_STICKY_MINUTES = 30


def _utcnow() -> datetime:
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
    # primary is 9, but accept 99 too
    if t in {"9", "99"}:
        return True
    return any(k in t for k in _AGENT_KEYS)


def _handoff_active(session: Dict[str, Any]) -> bool:
    if not isinstance(session, dict):
        return False
    if not bool(session.get("handoff_active")):
        return False
    until = _parse_iso(session.get("handoff_until")) if isinstance(session.get("handoff_until"), str) else None
    if until and _utcnow() <= until:
        return True
    session["handoff_active"] = False
    session["handoff_until"] = None
    return False


def _emergency_hold_active(session: Dict[str, Any]) -> bool:
    until = _parse_iso(session.get("emergency_hold_until")) if isinstance(session.get("emergency_hold_until"), str) else None
    if until and _utcnow() <= until:
        return True
    session["emergency_hold_until"] = None
    return False


def _short_ref(ticket_id: Optional[str]) -> Optional[str]:
    if not ticket_id or not isinstance(ticket_id, str):
        return None
    head = ticket_id.split("-")[0].upper()
    return head[:8] if head else None


def _extract_ticket_id(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    if result.get("ticket_id"):
        return result.get("ticket_id")
    inner = result.get("result")
    if isinstance(inner, dict):
        return inner.get("ticket_id") or inner.get("id")
    return None


def _resolve_language_for_turn(message_text: str, session: Dict[str, Any]) -> str:
    # If locked, never change
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    raw = (message_text or "").strip()

    # Numeric inputs shouldn't trigger re-detection
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
    # Arabic contains (keep original text)
    if any(k in (text or "") for k in _EMERGENCY_KEYS_AR):
        return True
    return False


def _emergency_protocol_message(language: str) -> str:
    if language == "ar":
        return (
            "🚨 قد تكون هذه حالة طارئة.\n"
            "يرجى الاتصال فورًا على 997 أو التوجه إلى أقرب قسم طوارئ.\n"
            "تم تمرير طلبكم إلى فريق الاستقبال الآن للمساعدة."
        )
    return (
        "🚨 Severe symptoms can be serious.\n"
        "Please call 997 immediately or go to the nearest emergency department.\n"
        "✅ Your request was forwarded to Reception."
    )


def _emergency_guard_prompt(language: str) -> str:
    if language == "ar":
        return (
            "⚠️ ذكرت أعراضًا قد تحتاج رعاية عاجلة.\n"
            "هل أنت الآن بأمان وتريد المتابعة بالحجز؟\n\n"
            "1️⃣ نعم، متابعة الحجز\n"
            "9️⃣ موظف الاستقبال\n"
            "🚑 للطوارئ اتصل على 997\n\n"
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
) -> Tuple[str, Dict[str, Any]]:
    """
    Creates (or reuses) a reception ticket and activates sticky handoff.
    Reuse rule: if we already have a ticket_id and handoff is active, do NOT create a new one.
    """
    existing_ticket = session.get("last_ticket_id")
    if existing_ticket and _handoff_active(session):
        short_ref = _short_ref(existing_ticket)
        if language == "ar":
            msg = f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0" if short_ref else "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
        else:
            msg = f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu" if short_ref else "Connecting you to Reception ✅\nReply 0 for the menu"
        return msg, {"ticket_id": existing_ticket, "urgent": urgent, "reused": True}

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
    session["handoff_until"] = (_utcnow() + timedelta(minutes=HANDOFF_STICKY_MINUTES)).isoformat()
    session["state"] = "ESCALATION"
    session["last_step"] = "ESCALATION"
    session["escalation_flag"] = True
    if urgent:
        session["urgent_flag"] = True
    if ticket_id:
        session["last_ticket_id"] = ticket_id

    short_ref = _short_ref(ticket_id)

    if language == "ar":
        if short_ref:
            return (f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0", {"ticket_id": ticket_id, "urgent": urgent, "reused": False})
        return ("تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0", {"ticket_id": None, "urgent": urgent, "reused": False})

    if short_ref:
        return (f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu", {"ticket_id": ticket_id, "urgent": urgent, "reused": False})
    return ("Connecting you to Reception ✅\nReply 0 for the menu", {"ticket_id": None, "urgent": urgent, "reused": False})


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
            "handoff_active": False,
            "handoff_until": None,
            "last_ticket_id": None,
            "emergency_hold_until": None,
        }

    session["last_user_message"] = message_text
    raw = (message_text or "").strip()

    # ---------------------------------------------------------
    # A) Emergency hold guard (if active) — BEFORE anything else
    # ---------------------------------------------------------
    if _emergency_hold_active(session):
        # Keep last known language (never reset to Arabic greeting)
        language = "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

        # Allow: 1 = continue, 9/99 = reception, 0 = show guard prompt again
        if raw in {"1", "١"}:
            # Continue booking safely: keep language, lock it, return to main menu (engine will handle)
            session["language"] = language
            session["language_locked"] = True
            session["emergency_hold_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

            # Let engine produce proper menu in the same language (no language menu)
            engine_out = run_engine(
                session=session,
                user_message="0",  # ask engine for menu
                language=language,
                arabic_tone=None,
                kpi_signals=kpi_signals,
            )
            reply_text = (engine_out.get("reply_text") or "").strip()
            session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
            await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)
            return reply_text, {"tenant_id": tenant, "state": session2.get("state"), "emergency_hold": False}

        if raw in {"9", "99"} or _wants_agent(raw):
            # Send/Reuse reception ticket
            language = "ar" if str(session.get("language") or "ar").startswith("ar") else "en"
            session["language"] = language
            session["text_direction"] = "rtl" if language == "ar" else "ltr"
            arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

            reply, extra = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
                decision_rule="controller_emergency_guard_reception",
                decision_reason="Emergency hold: user requested reception",
                urgent=True,
            )
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}
            meta.update(extra)
            return reply, meta

        # Default during emergency hold: show safety guard prompt (don’t show language menu)
        out = _emergency_guard_prompt(language)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

    # ---------------------------------------------------------
    # 1) EMERGENCY OVERRIDE (highest priority)
    # - Must happen BEFORE handoff silence and BEFORE engine
    # ---------------------------------------------------------
    if _is_emergency(message_text):
        detected_lang = "ar" if (detect_language(message_text) or "").lower().startswith("ar") else "en"
        language = detected_lang

        session["language"] = language
        session["text_direction"] = "rtl" if language == "ar" else "ltr"

        # Important: keep language sticky after emergency
        session["language_locked"] = True

        # Set emergency hold window (prevents immediate booking without confirmation)
        session["emergency_hold_until"] = (_utcnow() + timedelta(minutes=EMERGENCY_HOLD_MINUTES)).isoformat()

        session["urgent_flag"] = True
        kpi_signals.append("emergency_detected")

        arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

        # Create/Reuse escalation ticket
        esc_reply, esc_meta = await _escalate_to_human(
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

        # SINGLE clean message (no double “I will transfer” then “transferred”)
        safety_msg = _emergency_protocol_message(language)
        short_ref = _short_ref(session.get("last_ticket_id"))
        if language == "ar":
            transfer_line = f"رقم الطلب: #{short_ref}" if short_ref else ""
            out = f"{safety_msg}\n{transfer_line}\nللعودة للقائمة اكتب 0".strip()
        else:
            transfer_line = f"Ref: #{short_ref}" if short_ref else ""
            out = f"{safety_msg} {transfer_line}\nReply 0 for the menu".strip()

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}
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
    # 2) Sticky handoff: bot must STOP while handoff active
    # Only allow "0" to exit handoff back to menu.
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
    # 3) Agent override before engine
    # ---------------------------------------------------------
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
            urgent=False,
        )
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}
        meta.update(extra)
        return reply, meta

    # ---------------------------------------------------------
    # 4) Incident mode flag (optional)
    # ---------------------------------------------------------
    if is_incident_mode():
        kpi_signals.append("incident_mode")

    # ---------------------------------------------------------
    # 5) Normal engine routing
    # ---------------------------------------------------------
    engine_out = run_engine(
        session=session,
        user_message=message_text,
        language=language,
        arabic_tone=arabic_tone,
        kpi_signals=kpi_signals,
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

    await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

    return reply_text, {
        "tenant_id": tenant,
        "state": session2.get("state"),
        "status": session2.get("status"),
        "last_step": session2.get("last_step"),
        "language": session2.get("language"),
        "language_locked": session2.get("language_locked"),
        "handoff_active": session2.get("handoff_active"),
        "urgent_flag": bool(session2.get("urgent_flag")),
        "emergency_hold": bool(_emergency_hold_active(session2)),
    }