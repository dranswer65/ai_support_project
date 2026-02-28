# whatsapp_controller.py — Enterprise-ready controller
# (Emergency override + emergency hold + sticky handoff + language lock + input normalization)
#
# V5 Fixes:
# ✅ Reception is 99 (NOT 9) to avoid conflict with Neurology=9
# ✅ Greeting "Hi/Hello" before language lock shows EN welcome (engine now also supports this)
# ✅ Symptom messages never produce welcome screen; they trigger emergency/urgent escalation or symptom guard
# ✅ Emergency override runs before anything else
# ✅ Sticky handoff: silent unless "0"

from __future__ import annotations

import os
import re
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

# Emergency keywords (expanded)
_EMERGENCY_KEYS_EN = [
    "chest pain", "difficulty breathing", "shortness of breath", "can't breathe", "cannot breathe",
    "severe pain", "heavy bleeding", "bleeding heavily",
    "unconscious", "passed out", "fainted",
    "stroke", "heart attack", "overdose",
    "seizure", "seizures",
    "pregnant bleeding", "pregnancy bleeding", "miscarriage",
    "urinary retention", "urine retention", "unable to urinate", "can't urinate", "cannot urinate", "no urine",
]
_EMERGENCY_KEYS_AR = [
    "ضيق تنفس", "صعوبة تنفس", "اختناق", "لا أستطيع التنفس", "لا استطيع التنفس",
    "نزيف", "نزيف شديد", "نزيف قوي", "نزف", "ينزف",
    "حامل", "حمل", "إجهاض", "اسقاط", "نزيف مع حمل",
    "فقدت الوعي", "فقدان الوعي", "اغمى عليه", "أغمي عليه", "إغماء",
    "تشنج", "تشنجات",
    "ألم في الصدر", "الم في الصدر", "جلطة", "سكتة", "نوبة قلبية",
    "ألم شديد", "الم شديد", "الم في الكلى", "ألم في الكلى", "الم في البطن", "ألم في البطن",
    "احتباس بول", "احتباس البول", "لا أستطيع التبول", "لا استطيع التبول", "ما اقدر اتبول", "انقطاع البول", "توقف البول",
]

_URINE_RETENTION_EN_RE = re.compile(
    r"\b(urinary|urine)\s+retention\b|\b(unable|cant|can't|cannot|not able)\s+to\s+urinate\b|\bno\s+urine\b",
    re.IGNORECASE,
)
_URINE_RETENTION_AR_RE = re.compile(
    r"(احتباس\s*البول|احتباس\s*بول|لا\s*أستطيع\s*التبول|لا\s*استطيع\s*التبول|ما\s*اقدر\s*اتبول|انقطاع\s*البول|توقف\s*البول)",
    re.IGNORECASE,
)

# Symptom guard (non-emergency but should not show welcome)
_SYMPTOM_KEYS = [
    "pain", "hurt", "kidney", "bleeding", "faint", "unconscious", "breathing", "shortness", "retention",
    "ألم", "الم", "يوجع", "كلية", "كلى", "نزيف", "اغمى", "إغماء", "تنفس", "ضيق", "احتباس",
]

EMERGENCY_HOLD_MINUTES = 30
HANDOFF_STICKY_MINUTES = 30

_AR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _looks_arabic(text: str) -> bool:
    return bool(_AR_RE.search(text or ""))


def _normalize_input(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("،", "").replace(",", "").replace("٫", "")
    t = t.translate(_ARABIC_DIGITS)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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
    if t in {"99"}:
        return True
    if t in {"9"}:
        # ✅ 9 is not reception anymore; it's allowed as text "agent" only
        return False
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
    raw = (message_text or "").strip()
    if raw.isdigit():
        return "en"
    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _emergency_language(message_text: str) -> str:
    if _looks_arabic(message_text):
        return "ar"
    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _looks_like_symptom(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(k in t for k in _SYMPTOM_KEYS)


def _is_emergency(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(k in t for k in _EMERGENCY_KEYS_EN):
        return True
    if _URINE_RETENTION_EN_RE.search(text or ""):
        return True
    raw = text or ""
    if any(k in raw for k in _EMERGENCY_KEYS_AR):
        return True
    if _URINE_RETENTION_AR_RE.search(raw):
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


def _symptom_guard(language: str) -> str:
    if language == "ar":
        return (
            "⚠️ يبدو أنك تذكر أعراضًا صحية.\n"
            "إذا كانت الحالة شديدة أو طارئة، اتصل على 997 فورًا.\n"
            "يمكنني تحويلك لموظف الاستقبال للمساعدة: 99\n\n"
            "0️⃣ القائمة الرئيسية"
        )
    return (
        "⚠️ It sounds like you’re describing a medical symptom.\n"
        "If it is severe or urgent, please call 997 immediately.\n"
        "I can connect you to Reception for help: 99\n\n"
        "0️⃣ Main Menu"
    )


def _emergency_guard_prompt(language: str) -> str:
    if language == "ar":
        return (
            "⚠️ ذكرت أعراضًا قد تحتاج رعاية عاجلة.\n"
            "هل أنت الآن بأمان وتريد المتابعة بالحجز؟\n\n"
            "1️⃣ نعم، متابعة الحجز\n"
            "99️⃣ موظف الاستقبال\n"
            "🚑 للطوارئ اتصل على 997\n\n"
            "0️⃣ القائمة الرئيسية"
        )
    return (
        "⚠️ You mentioned symptoms that may need urgent care.\n"
        "Are you safe now and want to continue booking?\n\n"
        "1️⃣ Yes, continue booking\n"
        "99️⃣ Reception\n"
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
            "conversation_version": 5,
            "escalation_flag": False,
            "urgent_flag": False,
            "handoff_active": False,
            "handoff_until": None,
            "last_ticket_id": None,
            "emergency_hold_until": None,
            "emergency_language": None,
        }

    cleaned = _normalize_input(message_text)
    raw = cleaned
    session["last_user_message"] = cleaned

    # ---------------------------------------------------------
    # Emergency hold guard (highest while active)
    # ---------------------------------------------------------
    if _emergency_hold_active(session):
        hold_lang = session.get("emergency_language") or ("ar" if str(session.get("language") or "ar").startswith("ar") else "en")
        hold_lang = "ar" if str(hold_lang).startswith("ar") else "en"

        if raw == "0":
            out = _emergency_guard_prompt(hold_lang)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

        if raw == "99" or _wants_agent(raw):
            session["language"] = hold_lang
            session["text_direction"] = "rtl" if hold_lang == "ar" else "ltr"
            session["language_locked"] = True
            kpi_signals.append("emergency_guard_reception")

            ticket_id, extra = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=hold_lang,
                text_direction=session.get("text_direction", "ltr"),
                arabic_tone=select_arabic_tone(cleaned) if hold_lang == "ar" else None,
                kpi_signals=kpi_signals,
                decision_rule="controller_emergency_guard_reception",
                decision_reason="Emergency hold: user requested reception",
                urgent=True,
            )
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

            short_ref = _short_ref(ticket_id)
            if hold_lang == "ar":
                msg = f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0" if short_ref else "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
            else:
                msg = f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu" if short_ref else "Connecting you to Reception ✅\nReply 0 for the menu"

            meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}
            meta.update(extra)
            return msg, meta

        if raw == "1":
            sess_lang = "ar" if str(session.get("language") or "ar").startswith("ar") else "en"
            session["emergency_hold_until"] = None
            session["emergency_language"] = None

            session["language"] = sess_lang
            session["language_locked"] = True
            session["text_direction"] = "rtl" if sess_lang == "ar" else "ltr"

            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"

            engine_out = run_engine(
                session=session,
                user_message="0",
                language=sess_lang,
                arabic_tone=None,
                kpi_signals=kpi_signals,
            )
            reply_text = (engine_out.get("reply_text") or "").strip()
            session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session

            await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)
            return reply_text, {"tenant_id": tenant, "state": session2.get("state"), "emergency_hold": False}

        out = _emergency_guard_prompt(hold_lang)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

    # ---------------------------------------------------------
    # EMERGENCY OVERRIDE (must run BEFORE anything else)
    # ---------------------------------------------------------
    if _is_emergency(cleaned):
        emergency_lang = _emergency_language(cleaned)
        session["emergency_language"] = emergency_lang

        session["urgent_flag"] = True
        session["emergency_hold_until"] = (_utcnow() + timedelta(minutes=EMERGENCY_HOLD_MINUTES)).isoformat()
        kpi_signals.append("emergency_detected")

        ticket_id, extra = await _escalate_to_human(
            tenant_id=tenant,
            user_id=user_id,
            session=session,
            language=emergency_lang,
            text_direction=("rtl" if emergency_lang == "ar" else "ltr"),
            arabic_tone=select_arabic_tone(cleaned) if emergency_lang == "ar" else None,
            kpi_signals=kpi_signals,
            decision_rule="controller_emergency_override",
            decision_reason="Emergency keywords detected",
            urgent=True,
        )

        short_ref = _short_ref(ticket_id)
        out = _emergency_protocol_message(emergency_lang, short_ref)

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True, "emergency_language": emergency_lang}
        meta.update(extra)
        return out, meta

    # ---------------------------------------------------------
    # Symptom guard (non-emergency) — never show welcome screen
    # ---------------------------------------------------------
    if _looks_like_symptom(cleaned) and not bool(session.get("language_locked")):
        lang = "ar" if _looks_arabic(cleaned) else "en"
        session["language"] = lang
        session["language_locked"] = True
        session["text_direction"] = "rtl" if lang == "ar" else "ltr"
        session["state"] = "MAIN_MENU"
        session["last_step"] = "MAIN_MENU"
        out = _symptom_guard(lang)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "symptom_guard": True, "language": lang}

    # ---------------------------------------------------------
    # Normal language resolution (non-emergency)
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(cleaned, session)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(cleaned) if language == "ar" else None

    # Sticky handoff silence (non-emergency)
    if _handoff_active(session):
        if raw == "0":
            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
        else:
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return "", {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}

    # Agent override (99)
    if raw == "99" or _wants_agent(cleaned):
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
            reply = f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0" if short_ref else "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
        else:
            reply = f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu" if short_ref else "Connecting you to Reception ✅\nReply 0 for the menu"

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}
        meta.update(extra)
        return reply, meta

    if is_incident_mode():
        kpi_signals.append("incident_mode")

    # Engine routing
    engine_out = run_engine(
        session=session,
        user_message=cleaned,
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
        "emergency_language": session2.get("emergency_language"),
    }