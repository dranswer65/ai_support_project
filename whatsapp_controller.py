# whatsapp_controller.py — Enterprise-ready controller (V5.1)
# Fixes:
# ✅ Proper bilingual welcome greeting (AR/EN) based on detected message language
# ✅ If greeting or "book intent" arrives early, show welcome (not menu hint)
# ✅ Emergency still intercepts FIRST
# ✅ Tenant-aware session saving stays same

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

# ---------------------------
# Hard emergency list (🚨)
# ---------------------------
_HARD_EMERGENCY_KEYS_EN = [
    "chest pain",
    "pain in chest",
    "shortness of breath",
    "difficulty breathing",
    "can't breathe",
    "cannot breathe",
    "severe abdominal pain",
    "heavy bleeding",
    "bleeding heavily",
    "unconscious",
    "passed out",
    "fainted",
]

_HARD_EMERGENCY_KEYS_AR = [
    "ألم في الصدر",
    "الم في الصدر",
    "ضيق تنفس",
    "ضيق في التنفس",
    "صعوبة تنفس",
    "صعوبة في التنفس",
    "اختناق",
    "لا أستطيع التنفس",
    "لا استطيع التنفس",
    "نزيف شديد",
    "نزيف قوي",
    "نزيف",
    "ينزف",
    "فقدت الوعي",
    "فقدان الوعي",
    "اغمى عليه",
    "أغمي عليه",
    "إغماء",
    "ألم شديد في البطن",
    "الم شديد في البطن",
    "ألم في البطن شديد",
    "الم في البطن شديد",
]

_CHEST_EN_RE = re.compile(r"\b(chest)\s+(pain|hurts|tightness)\b", re.IGNORECASE)
_SOB_EN_RE = re.compile(r"\b(shortness\s+of\s+breath|difficulty\s+breathing|can't\s+breathe|cannot\s+breathe)\b", re.IGNORECASE)
_ABD_EN_RE = re.compile(r"\b(severe)\s+(abdominal|belly|stomach)\s+pain\b", re.IGNORECASE)
_BLEED_EN_RE = re.compile(r"\b(heavy|severe)\s+bleeding\b|\bbleeding\s+heavily\b", re.IGNORECASE)
_UNCON_EN_RE = re.compile(r"\b(unconscious|passed\s+out|fainted)\b", re.IGNORECASE)

_CHEST_AR_RE = re.compile(r"(ألم\s*في\s*الصدر|الم\s*في\s*الصدر)", re.IGNORECASE)
_SOB_AR_RE = re.compile(r"(ضيق\s*تنفس|صعوبة\s*تنفس|اختناق|لا\s*أستطيع\s*التنفس|لا\s*استطيع\s*التنفس)", re.IGNORECASE)
_ABD_AR_RE = re.compile(r"(ألم\s*شديد(\s*في)?\s*البطن|الم\s*شديد(\s*في)?\s*البطن|ألم\s*في\s*البطن\s*شديد|الم\s*في\s*البطن\s*شديد)", re.IGNORECASE)
_BLEED_AR_RE = re.compile(r"(نزيف\s*شديد|نزيف\s*قوي|ينزف|نزيف)", re.IGNORECASE)
_UNCON_AR_RE = re.compile(r"(فقدت\s*الوعي|فقدان\s*الوعي|اغمى\s*عليه|أغمي\s*عليه|إغماء)", re.IGNORECASE)

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

    raw = (message_text or "").strip()
    if raw.isdigit():
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _emergency_language(message_text: str) -> str:
    if _looks_arabic(message_text or ""):
        return "ar"
    detected = (detect_language(message_text or "") or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _is_hard_emergency(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False

    if any(k in t for k in _HARD_EMERGENCY_KEYS_EN):
        return True
    if _CHEST_EN_RE.search(text or "") or _SOB_EN_RE.search(text or "") or _ABD_EN_RE.search(text or "") or _BLEED_EN_RE.search(text or "") or _UNCON_EN_RE.search(text or ""):
        return True

    raw = text or ""
    if any(k in raw for k in _HARD_EMERGENCY_KEYS_AR):
        return True
    if _CHEST_AR_RE.search(raw) or _SOB_AR_RE.search(raw) or _ABD_AR_RE.search(raw) or _BLEED_AR_RE.search(raw) or _UNCON_AR_RE.search(raw):
        return True

    return False


def _is_greeting_like(text: str) -> bool:
    t = (text or "").strip().lower()
    if t in {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}:
        return True
    raw = text or ""
    for k in ["السلام عليكم", "مرحبا", "أهلا", "اهلا", "هلا", "صباح الخير", "مساء الخير"]:
        if k in raw:
            return True
    return False


# NEW: detect "book intent" early (English + Arabic)
_BOOK_INTENT_EN = re.compile(r"\b(book|booking|appointment|reserve|schedule)\b", re.IGNORECASE)
_BOOK_INTENT_AR = re.compile(r"(حجز|موعد|احجز|أحجز|حجز موعد)", re.IGNORECASE)


def _wants_booking(text: str) -> bool:
    raw = text or ""
    return bool(_BOOK_INTENT_EN.search(raw)) or bool(_BOOK_INTENT_AR.search(raw))


def _welcome_text(lang: str) -> str:
    # This matches your old greeting format (AR/EN variants)
    if lang == "en":
        return (
            "Welcome to *Shireen Specialist Hospital* 🏥\n"
            "Official WhatsApp Virtual Assistant.\n\n"
            "📞 Reception: *+966XXXXXXXX*\n"
            "🚑 Emergency: *997*\n\n"
            "Please select your preferred language:\n"
            "1️⃣ العربية\n"
            "2️⃣ English\n\n"
            "To reach Reception anytime, reply: *Agent* or 99"
        )

    return (
        "مرحبًا بكم في *مستشفى شيرين التخصصي* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        "📞 الاستقبال: *+966XXXXXXXX*\n"
        "🚑 الطوارئ: *997*\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "*(Please select your preferred language)*\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 99"
    )


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
            "🚨 ذكرت أعراضًا قد تكون طارئة.\n"
            "هل أنت الآن بأمان وتريد المتابعة بالحجز؟\n\n"
            "1️⃣ نعم، متابعة الحجز\n"
            "9️⃣ موظف الاستقبال\n"
            "🚑 للطوارئ اتصل على 997\n\n"
            "0️⃣ القائمة الرئيسية"
        )
    return (
        "🚨 You mentioned symptoms that may be an emergency.\n"
        "Are you safe now and want to continue booking?\n\n"
        "1️⃣ Yes, continue booking\n"
        "9️⃣ Reception\n"
        "🚑 For emergencies call 997\n\n"
        "0️⃣ Main Menu"
    )


def _handoff_wait_message(language: str) -> str:
    if language == "ar":
        return "تم تحويلك إلى الاستقبال ✅ الرجاء الانتظار.\nللعودة للقائمة اكتب 0"
    return "You are connected to Reception ✅ Please wait.\nReply 0 for the menu"


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
    # 0) Emergency always first
    # ---------------------------------------------------------
    if _is_hard_emergency(cleaned):
        emergency_lang = _emergency_language(cleaned)
        session["emergency_language"] = emergency_lang
        session["urgent_flag"] = True
        session["emergency_hold_until"] = (_utcnow() + timedelta(minutes=EMERGENCY_HOLD_MINUTES)).isoformat()
        kpi_signals.append("hard_emergency_detected")

        if not session.get("last_ticket_id"):
            ticket_id, extra = await _escalate_to_human(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=emergency_lang,
                text_direction=("rtl" if emergency_lang == "ar" else "ltr"),
                arabic_tone=select_arabic_tone(cleaned) if emergency_lang == "ar" else None,
                kpi_signals=kpi_signals,
                decision_rule="controller_hard_emergency_override",
                decision_reason="Hard emergency keywords detected",
                urgent=True,
            )
            short_ref = _short_ref(ticket_id)
            out = _emergency_protocol_message(emergency_lang, short_ref)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}
            meta.update(extra)
            return out, meta

        short_ref = _short_ref(session.get("last_ticket_id"))
        out = _emergency_protocol_message(emergency_lang, short_ref)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "urgent": True, "emergency_hold": True}

    # ---------------------------------------------------------
    # A) Emergency hold guard
    # ---------------------------------------------------------
    if _emergency_hold_active(session):
        hold_lang = session.get("emergency_language") or ("ar" if str(session.get("language") or "ar").startswith("ar") else "en")
        hold_lang = "ar" if str(hold_lang).startswith("ar") else "en"

        if raw == "0":
            out = _emergency_guard_prompt(hold_lang)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "emergency_hold": True}

        if raw in {"9", "99"} or _wants_agent(raw):
            out = _handoff_wait_message(hold_lang)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "emergency_hold": True}

        if raw == "1":
            continue_lang = session.get("language") or "ar"
            continue_lang = "ar" if str(continue_lang).startswith("ar") else "en"

            session["emergency_hold_until"] = None
            session["emergency_language"] = None
            session["urgent_flag"] = False
            session["handoff_active"] = False
            session["handoff_until"] = None

            session["language"] = continue_lang
            session["language_locked"] = True
            session["text_direction"] = "rtl" if continue_lang == "ar" else "ltr"
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"

            engine_out = run_engine(
                session=session,
                user_message="0",
                language=continue_lang,
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
    # B) NEW: Welcome greeting / early booking intent
    # If user greets OR says "book appointment" early, show welcome in detected language
    # This is what you want.
    # ---------------------------------------------------------
    detected_lang = "ar" if _looks_arabic(cleaned) else ("ar" if (detect_language(cleaned) or "en").startswith("ar") else "en")

    if (_is_greeting_like(cleaned) or _wants_booking(cleaned)) and not bool(session.get("has_greeted")):
        session["has_greeted"] = True
        session["language_locked"] = False
        session["state"] = "LANG_SELECT"
        session["last_step"] = "LANG_SELECT"
        # Do NOT force save language yet; just greet in detected language
        out = _welcome_text(detected_lang)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "state": session.get("state"), "welcome": True}

    # ---------------------------------------------------------
    # Normal language resolution
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(cleaned, session)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(cleaned) if language == "ar" else None

    # Sticky handoff (don’t stay silent)
    if _handoff_active(session):
        if raw == "0":
            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
        else:
            out = _handoff_wait_message(language)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True}

    # Agent override
    if _wants_agent(cleaned):
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
        reply = (
            f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0"
            if language == "ar" and short_ref else
            "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
            if language == "ar" else
            f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu"
            if short_ref else
            "Connecting you to Reception ✅\nReply 0 for the menu"
        )
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
        "emergency_hold": False,
    }