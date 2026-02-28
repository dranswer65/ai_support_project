# whatsapp_controller.py — Enterprise-ready controller (Emergency override + emergency hold + sticky handoff + language lock)
# FINAL FIXED VERSION
#
# Fixes included:
# ✅ Emergency detection runs BEFORE anything (works even after idle/session reset)
# ✅ Arabic emergency lexicon expanded (includes ضيق في التنفس variants, etc.)
# ✅ Input normalization fixes ",0" and "،0" and whitespace noise
# ✅ Emergency HOLD guard always replies (never silent)
# ✅ Pressing "1" in emergency guard goes to MAIN MENU directly (no language menu, no reset)
# ✅ Reception escalation during hold does not silence incorrectly
# ✅ Sticky handoff silence remains for non-emergency handoff only
# ✅ Language stays sticky (locked) once chosen or once emergency detected

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

# Emergency keywords (expand over time)
_EMERGENCY_KEYS_EN = [
    "chest pain",
    "difficulty breathing",
    "can't breathe",
    "cannot breathe",
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

_EMERGENCY_KEYS_AR = [
    # Cardiac / chest
    "ألم في الصدر", "الم في الصدر", "وجع في الصدر",

    # Breathing (IMPORTANT)
    "ضيق تنفس", "ضيق في التنفس", "ضيق بالتنفس",
    "صعوبة تنفس", "صعوبه تنفس",
    "اختناق", "مخنوق",
    "ما اقدر اتنفس", "لا اقدر اتنفس",
    "لا أستطيع التنفس", "لا استطيع التنفس",

    # Bleeding
    "نزيف", "ينزف", "نزف", "نزيف شديد", "نزيف قوي",

    # Pregnancy-related
    "حامل", "حمل",
    "إجهاض", "اجهاض",
    "اسقاط", "إسقاط",
    "نزيف مع حمل", "نزيف والحمل", "نزيف مع الحمل",

    # Neuro / consciousness
    "فقدت الوعي", "فقدان الوعي", "فاقد الوعي",
    "اغمى عليه", "إغماء", "اغماء",
    "تشنج", "تشنجات",

    # Stroke / heart attack terms
    "جلطة", "سكتة", "نوبة قلبية",

    # Severe pain / abdomen / kidney
    "ألم شديد", "الم شديد", "ألم حاد", "الم حاد",
    "الم في الكلى", "ألم في الكلى",
    "الم في البطن", "ألم في البطن",
]

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


def _clean_input(text: str) -> str:
    t = (text or "").strip()
    # Normalize Arabic/English commas and common punctuation around numeric commands
    for ch in ["،", ",", "٫", ";", "؛", "。"]:
        t = t.replace(ch, "")
    # Collapse whitespace
    t = " ".join(t.split())
    return t


def _wants_agent(text: str) -> bool:
    t = (_clean_input(text) or "").strip().lower()
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
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    raw = _clean_input(message_text)
    if raw.isdigit():
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _is_emergency(text: str) -> bool:
    raw = _clean_input(text)
    t = raw.lower()
    if not t:
        return False
    if any(k in t for k in _EMERGENCY_KEYS_EN):
        return True
    # Arabic contains check on cleaned raw
    if any(k in raw for k in _EMERGENCY_KEYS_AR):
        return True
    return False


def _emergency_protocol_message(language: str, ticket_short: Optional[str]) -> str:
    if language == "ar":
        base = (
            "🚨 قد تكون هذه حالة طارئة.\n"
            "يرجى الاتصال فورًا على 997 أو التوجه إلى أقرب قسم طوارئ.\n"
            "تم تمرير طلبكم إلى فريق الاستقبال الآن للمساعدة."
        )
        if ticket_short:
            base += f"\nرقم الطلب: #{ticket_short}"
        base += "\nللعودة للقائمة اكتب 0"
        return base

    base = (
        "🚨 Severe symptoms can be serious.\n"
        "Please call 997 immediately or go to the nearest emergency department.\n"
        "✅ Your request was forwarded to Reception."
    )
    if ticket_short:
        base += f" Ref: #{ticket_short}"
    base += "\nReply 0 for the menu"
    return base


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
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Creates a reception ticket and activates sticky handoff.
    Returns ticket_id + meta. Caller decides the final UX message.
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
    session["handoff_until"] = (_utcnow() + timedelta(minutes=HANDOFF_STICKY_MINUTES)).isoformat()
    session["state"] = "ESCALATION"
    session["last_step"] = "ESCALATION"
    session["escalation_flag"] = True
    if urgent:
        session["urgent_flag"] = True
    if ticket_id:
        session["last_ticket_id"] = ticket_id

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

    # Normalize input FIRST (fix ",0" / "،0" and whitespace issues)
    cleaned = _clean_input(message_text)

    # ---------------------------------------------------------
    # 0) EMERGENCY DETECTION MUST RUN FIRST (even after idle / expired session)
    # ---------------------------------------------------------
    if _is_emergency(cleaned):
        # Get existing session if any (but do not depend on it)
        session = await get_session(db, user_id=user_id, tenant_id=tenant)
        if not isinstance(session, dict):
            session = {
                "user_id": user_id,
                "status": "ACTIVE",
                "state": "ESCALATION",
                "last_step": "ESCALATION",
                "language": "ar",
                "language_locked": False,
                "text_direction": "rtl",
                "has_greeted": False,
                "conversation_version": 4,
                "escalation_flag": False,
                "urgent_flag": True,
                "handoff_active": False,
                "handoff_until": None,
                "last_ticket_id": None,
                "emergency_hold_until": None,
            }

        session["last_user_message"] = cleaned

        detected_lang = "ar" if (detect_language(cleaned) or "").lower().startswith("ar") else "en"
        language = detected_lang

        # Lock language during emergency path (avoid language menu appearing after)
        session["language"] = language
        session["language_locked"] = True
        session["text_direction"] = "rtl" if language == "ar" else "ltr"

        session["urgent_flag"] = True
        session["emergency_hold_until"] = (_utcnow() + timedelta(minutes=EMERGENCY_HOLD_MINUTES)).isoformat()
        kpi_signals.append("emergency_detected")

        ticket_id, extra = await _escalate_to_human(
            tenant_id=tenant,
            user_id=user_id,
            session=session,
            language=language,
            text_direction=session.get("text_direction", "ltr"),
            arabic_tone=select_arabic_tone(cleaned) if language == "ar" else None,
            kpi_signals=kpi_signals,
            decision_rule="controller_emergency_override",
            decision_reason="Emergency keywords detected",
            urgent=True,
        )

        short_ref = _short_ref(ticket_id)
        out = _emergency_protocol_message(language, short_ref)

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {
            "tenant_id": tenant,
            "state": session.get("state"),
            "handoff_active": True,
            "urgent": True,
            "emergency_hold": True,
        }
        meta.update(extra)
        return out, meta

    # ---------------------------------------------------------
    # Load session (non-emergency path)
    # ---------------------------------------------------------
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

    session["last_user_message"] = cleaned
    raw = cleaned  # always use cleaned from now on

    # ---------------------------------------------------------
    # A) Emergency HOLD guard (highest priority while active)
    # Must always reply (never silent)
    # ---------------------------------------------------------
    if _emergency_hold_active(session):
        # Keep last known language (do NOT restart language selection)
        language = "ar" if str(session.get("language") or "ar").startswith("ar") else "en"
        session["language"] = language
        session["text_direction"] = "rtl" if language == "ar" else "ltr"
        session["language_locked"] = True

        # 0 shows guard again
        if raw in {"0", "٠"}:
            out = _emergency_guard_prompt(language)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

        # 9/99/agent -> reception
        if raw in {"9", "99"} or _wants_agent(raw):
            kpi_signals.append("emergency_guard_reception")

            ticket_id, extra = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=language,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=select_arabic_tone(raw) if language == "ar" else None,
                kpi_signals=kpi_signals,
                decision_rule="controller_emergency_guard_reception",
                decision_reason="Emergency hold: user requested reception",
                urgent=True,
            )

            short_ref = _short_ref(ticket_id)
            if language == "ar":
                msg = (
                    f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0"
                    if short_ref else
                    "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
                )
            else:
                msg = (
                    f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu"
                    if short_ref else
                    "Connecting you to Reception ✅\nReply 0 for the menu"
                )

            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}
            meta.update(extra)
            return msg, meta

        # ✅ KEY FIX: user presses 1 -> continue booking (NO language restart, NO silence)
        if raw in {"1", "١"}:
            session["emergency_hold_until"] = None

            # IMPORTANT: allow bot to speak again (avoid "no reply")
            session["handoff_active"] = False
            session["handoff_until"] = None

            # Jump directly to main menu (no language menu)
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"

            # Ask engine to render menu in same language
            engine_out = run_engine(
                session=session,
                user_message="0",
                language=language,
                arabic_tone=None,
                kpi_signals=kpi_signals,
            )
            reply_text = (engine_out.get("reply_text") or "").strip()
            session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

            await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)
            return reply_text, {"tenant_id": tenant, "state": session2.get("state"), "emergency_hold": False}

        # Anything else -> guard prompt (never silent)
        out = _emergency_guard_prompt(language)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

    # ---------------------------------------------------------
    # Normal language resolution (non-emergency, non-hold)
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(raw, session)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(raw) if language == "ar" else None

    # ---------------------------------------------------------
    # Sticky handoff silence (non-emergency only)
    # ---------------------------------------------------------
    if _handoff_active(session):
        if raw in {"0", "٠"}:
            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
        else:
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return "", {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}

    # ---------------------------------------------------------
    # Agent override (non-emergency)
    # ---------------------------------------------------------
    if _wants_agent(raw):
        ticket_id, extra = await _escalate_to_human(
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

        short_ref = _short_ref(ticket_id)
        if language == "ar":
            reply = (
                f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0"
                if short_ref else
                "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
            )
        else:
            reply = (
                f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu"
                if short_ref else
                "Connecting you to Reception ✅\nReply 0 for the menu"
            )

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}
        meta.update(extra)
        return reply, meta

    # ---------------------------------------------------------
    # Incident mode flag (optional)
    # ---------------------------------------------------------
    if is_incident_mode():
        kpi_signals.append("incident_mode")

    # ---------------------------------------------------------
    # Engine routing
    # ---------------------------------------------------------
    engine_out = run_engine(
        session=session,
        user_message=raw,  # IMPORTANT: pass cleaned
        language=language,
        arabic_tone=arabic_tone,
        kpi_signals=kpi_signals,
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

    await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

    # Compute emergency_hold without mutating session2 (do NOT call _emergency_hold_active here)
    hold_until = _parse_iso(session2.get("emergency_hold_until")) if isinstance(session2, dict) else None
    hold_active = bool(hold_until and _utcnow() <= hold_until)

    return reply_text, {
        "tenant_id": tenant,
        "state": session2.get("state"),
        "status": session2.get("status"),
        "last_step": session2.get("last_step"),
        "language": session2.get("language"),
        "language_locked": session2.get("language_locked"),
        "handoff_active": session2.get("handoff_active"),
        "urgent_flag": bool(session2.get("urgent_flag")),
        "emergency_hold": hold_active,
    }