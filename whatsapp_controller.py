# whatsapp_controller.py — Enterprise-ready controller (Emergency override + sticky handoff + language lock)
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
# Emergency keywords (demo-safe but hospital-grade)
# You can expand these over time.
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
    "suicidal",
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

def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"

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

def _short_ref(ticket_id: Optional[str]) -> Optional[str]:
    if not ticket_id or not isinstance(ticket_id, str):
        return None
    # patient-friendly: show only first block / first 6-8 chars
    head = ticket_id.split("-")[0].upper()
    return head[:8] if head else None

def _resolve_language_for_turn(message_text: str, session: Dict[str, Any], user_id: str) -> str:
    # If locked, never change
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    raw = (message_text or "").strip()

    # numeric inputs during flows should not trigger re-detection
    if raw.isdigit():
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"

def _is_emergency(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    # quick contains checks
    if any(k in t for k in _EMERGENCY_KEYS_EN):
        return True
    # Arabic checks (don’t lower-case Arabic; contains still works)
    if any(k in (text or "") for k in _EMERGENCY_KEYS_AR):
        return True
    return False

def _emergency_message(language: str) -> str:
    if language == "ar":
        return (
            "🚨 قد تكون هذه حالة طارئة.\n"
            "يرجى الاتصال فورًا على 997 أو التوجه إلى أقرب قسم طوارئ.\n"
            "سأقوم الآن بتحويلكم إلى فريق الاستقبال للمساعدة."
        )
    return (
        "🚨 Chest pain (or severe symptoms) can be serious.\n"
        "Please call 997 immediately or go to the nearest emergency department.\n"
        "I am also connecting you to our reception team now."
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

    # Sticky handoff window: 30 min
    session["handoff_active"] = True
    session["handoff_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    session["state"] = "ESCALATION"
    session["last_step"] = "ESCALATION"
    session["escalation_flag"] = True
    if urgent:
        session["urgent_flag"] = True

    short_ref = _short_ref(ticket_id)

    if language == "ar":
        if short_ref:
            return (f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0", {"ticket_id": ticket_id, "urgent": urgent})
        return ("تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0", {"ticket_id": None, "urgent": urgent})

    if short_ref:
        return (f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu", {"ticket_id": ticket_id, "urgent": urgent})
    return ("Connecting you to Reception ✅\nReply 0 for the menu", {"ticket_id": None, "urgent": urgent})

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
        }

    session["last_user_message"] = message_text

    # ---------------------------------------------------------
    # 1) EMERGENCY OVERRIDE (highest priority)
    # - Must happen BEFORE handoff silence
    # - Must happen BEFORE engine / language menu
    # ---------------------------------------------------------
    if _is_emergency(message_text):
        # choose language from the message itself (do not show language menu)
        detected_lang = "ar" if (detect_language(message_text) or "").lower().startswith("ar") else "en"
        language = detected_lang
        session["language"] = language
        session["text_direction"] = "rtl" if language == "ar" else "ltr"
        arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

        # ensure urgent logging
        session["urgent_flag"] = True
        kpi_signals.append("emergency_detected")

        # emergency protocol + immediate escalation
        safety_msg = _emergency_message(language)

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

        # combine: safety first, then transfer confirmation
        out = f"{safety_msg}\n\n{esc_reply}".strip()

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {
            "tenant_id": tenant,
            "state": session.get("state"),
            "handoff_active": True,
            "urgent": True,
        }
        meta.update(esc_meta)
        return out, meta

    # ---------------------------------------------------------
    # Resolve language normally (non-emergency)
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(message_text, session, user_id)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # ---------------------------------------------------------
    # 2) Sticky handoff: bot must STOP while handoff active
    # Only allow "0" to exit handoff back to menu
    # ---------------------------------------------------------
    if _handoff_active(session):
        if (message_text or "").strip() == "0":
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
    }