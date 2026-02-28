# whatsapp_controller.py — Enterprise-ready controller (Clinic SaaS)
# Fixes:
# ✅ Real reschedule/cancel menu flows handled in engine (see engine V4.5)
# ✅ Emergency lock UX: "thank you/ok" does NOT trigger guard question
# ✅ Emergency escalation reuses existing handoff ticket ref (no duplicate refs)
# ✅ Single active mode rule: EMERGENCY overrides everything
# ✅ Emergency message is symptom-specific (chest pain / SOB / bleeding / unconscious / severe abdominal pain)
# ✅ Input normalization: ",1" / "،0" / Arabic digits

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

EMERGENCY_HOLD_MINUTES = 30
HANDOFF_STICKY_MINUTES = 30

_AR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Emergency patterns
_CHEST_EN_RE = re.compile(r"\b(chest)\s+(pain|hurts|aching)\b", re.IGNORECASE)
_SOB_EN_RE = re.compile(r"\b(shortness\s+of\s+breath|difficulty\s+breathing|can't\s+breathe|cannot\s+breathe)\b", re.IGNORECASE)
_BLEED_EN_RE = re.compile(r"\b(heavy\s+bleeding|bleeding\s+heavily)\b", re.IGNORECASE)
_UNCON_EN_RE = re.compile(r"\b(unconscious|passed\s+out|fainted)\b", re.IGNORECASE)
_ABD_EN_RE = re.compile(r"\b(severe)\s+(abdominal|stomach|belly)\s+pain\b", re.IGNORECASE)

_CHEST_AR_RE = re.compile(r"(ألم\s*في\s*الصدر|الم\s*في\s*الصدر|وجع\s*في\s*الصدر)", re.IGNORECASE)
_SOB_AR_RE = re.compile(r"(ضيق\s*تنفس|ضيق\s*في\s*التنفس|صعوبة\s*تنفس|لا\s*أستطيع\s*التنفس|لا\s*استطيع\s*التنفس|ما\s*اقدر\s*اتنفس)", re.IGNORECASE)
_BLEED_AR_RE = re.compile(r"(نزيف\s*شديد|نزيف\s*قوي|ينزف\s*بشدة)", re.IGNORECASE)
_UNCON_AR_RE = re.compile(r"(فقدت\s*الوعي|فقدان\s*الوعي|إغماء|اغمى\s*عليه|أغمي\s*عليه)", re.IGNORECASE)
_ABD_AR_RE = re.compile(r"(ألم\s*شديد\s*في\s*البطن|الم\s*شديد\s*في\s*البطن|ألم\s*قوي\s*في\s*البطن|الم\s*قوي\s*في\s*البطن)", re.IGNORECASE)

_THANKS_EN = {"thanks", "thank you", "thx", "ok", "okay", "understood", "got it"}
_THANKS_AR = {"شكرا", "شكراً", "شكرًا", "تمام", "حسنا", "حسنًا", "مفهوم", "تم"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _looks_arabic(text: str) -> bool:
    return bool(_AR_RE.search(text or ""))


def _normalize_input(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("،", "").replace(",", "").replace("٫", "").replace("؛", "").replace(";", "")
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
    txt = message_text or ""
    if _looks_arabic(txt):
        return "ar"
    detected = (detect_language(txt) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"


def _emergency_kind(text: str) -> Optional[str]:
    raw = text or ""
    if _CHEST_EN_RE.search(raw) or _CHEST_AR_RE.search(raw):
        return "chest"
    if _SOB_EN_RE.search(raw) or _SOB_AR_RE.search(raw):
        return "breathing"
    if _BLEED_EN_RE.search(raw) or _BLEED_AR_RE.search(raw):
        return "bleeding"
    if _UNCON_EN_RE.search(raw) or _UNCON_AR_RE.search(raw):
        return "unconscious"
    if _ABD_EN_RE.search(raw) or _ABD_AR_RE.search(raw):
        return "abdomen"
    return None


def _is_emergency(text: str) -> bool:
    return _emergency_kind(text) is not None


def _is_thanks(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in _THANKS_EN:
        return True
    raw = text or ""
    return any(w in raw for w in _THANKS_AR)


def _welcome_text_ar() -> str:
    return (
        "مرحبًا بكم في *مستشفى شيرين التخصصي* 🏥\n"
        "المساعد الافتراضي الرسمي عبر واتساب.\n\n"
        "📞 الاستقبال: *+966XXXXXXXX*\n"
        "🚑 الطوارئ: *997*\n\n"
        "يرجى اختيار اللغة المفضلة:\n"
        "*(Please select your preferred language)*\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "للتحدث مع الاستقبال في أي وقت اكتب: *Agent* أو 9 (أو 99)"
    )


def _welcome_text_en() -> str:
    return (
        "Welcome to *Shireen Specialist Hospital* 🏥\n"
        "Official WhatsApp Virtual Assistant.\n\n"
        "📞 Reception: *+966XXXXXXXX*\n"
        "🚑 Emergency: *997*\n\n"
        "Please select your preferred language:\n"
        "1️⃣ العربية\n"
        "2️⃣ English\n\n"
        "To reach Reception anytime, reply: *Agent* or 9 (or 99)"
    )


def _emergency_protocol_message(language: str, kind: Optional[str], ticket_short: Optional[str]) -> str:
    if language == "ar":
        headline = "🚨 قد تكون هذه حالة طارئة."
        if kind == "chest":
            headline = "🚨 ألم الصدر قد يدل على حالة خطيرة."
        elif kind == "breathing":
            headline = "🚨 ضيق التنفس قد يدل على حالة خطيرة."
        elif kind == "bleeding":
            headline = "🚨 النزيف الشديد قد يكون خطيرًا."
        elif kind == "unconscious":
            headline = "🚨 فقدان الوعي حالة طارئة."
        elif kind == "abdomen":
            headline = "🚨 ألم البطن الشديد قد يدل على حالة خطيرة."

        base = (
            f"{headline}\n"
            "يرجى الاتصال فورًا على 997 أو التوجه إلى أقرب قسم طوارئ.\n"
            "✅ تم تمرير طلبكم إلى فريق الاستقبال للمساعدة."
        )
        if ticket_short:
            base += f"\nرقم الطلب: #{ticket_short}"
        base += "\nللعودة للقائمة اكتب 0"
        return base

    headline = "🚨 Severe symptoms can be serious."
    if kind == "chest":
        headline = "🚨 Chest pain can indicate a serious condition."
    elif kind == "breathing":
        headline = "🚨 Breathing difficulty can be serious."
    elif kind == "bleeding":
        headline = "🚨 Heavy bleeding can be dangerous."
    elif kind == "unconscious":
        headline = "🚨 Loss of consciousness is an emergency."
    elif kind == "abdomen":
        headline = "🚨 Severe abdominal pain can be serious."

    base = (
        f"{headline}\n"
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
            "🚨 ذكرت أعراضًا طارئة.\n"
            "هل أنت الآن بأمان وتريد المتابعة بالحجز؟\n\n"
            "1️⃣ نعم، متابعة الحجز\n"
            "9️⃣ موظف الاستقبال\n"
            "🚑 للطوارئ اتصل على 997\n\n"
            "0️⃣ القائمة الرئيسية"
        )
    return (
        "🚨 You mentioned emergency symptoms.\n"
        "Are you safe now and want to continue booking?\n\n"
        "1️⃣ Yes, continue booking\n"
        "9️⃣ Reception\n"
        "🚑 For emergencies call 997\n\n"
        "0️⃣ Main Menu"
    )


def _emergency_polite_after_thanks(language: str) -> str:
    if language == "ar":
        return (
            "العفو ✅\n"
            "إذا استمرت الأعراض أو ساءت، يرجى الاتصال فورًا على 997.\n"
            "عندما تكون جاهزًا، اكتب 0 لعرض القائمة."
        )
    return (
        "You’re welcome ✅\n"
        "If symptoms continue or worsen, please call 997 immediately.\n"
        "When you’re ready, reply 0 for the main menu."
    )


async def _dispatch_ticket(
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
    urgent: bool,
) -> Optional[str]:
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

    try:
        routing = route_escalation(payload)
        result = await anyio.to_thread.run_sync(dispatch_ticket, payload, routing)
        return _extract_ticket_id(result)
    except Exception:
        return None


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
            "handoff_active": False,
            "handoff_until": None,
            "last_ticket_id": None,
            "urgent_flag": False,
            "mode": "NORMAL",  # ✅ single active mode
            "emergency_hold_until": None,
            "emergency_language": None,
        }

    cleaned = _normalize_input(message_text)
    raw = cleaned
    session["last_user_message"] = cleaned

    # ---------------------------------------------------------
    # 0) Ensure greeting works even if session got stuck in MAIN_MENU
    # ---------------------------------------------------------
    if not bool(session.get("has_greeted")) and session.get("state") == "MAIN_MENU":
        session["state"] = "LANG_SELECT"
        session["last_step"] = "LANG_SELECT"
        session["language_locked"] = False

    # ---------------------------------------------------------
    # 1) Emergency Interceptor ALWAYS FIRST (even during handoff)
    # Reuse existing ticket ref if already in handoff window.
    # ---------------------------------------------------------
    if _is_emergency(message_text):
        kind = _emergency_kind(message_text)
        emergency_lang = _emergency_language(message_text)
        session["mode"] = "EMERGENCY"
        session["urgent_flag"] = True
        session["emergency_language"] = emergency_lang
        session["emergency_hold_until"] = (_utcnow() + timedelta(minutes=EMERGENCY_HOLD_MINUTES)).isoformat()

        # ✅ If already escalated and still in handoff, reuse ticket (no duplicate refs)
        existing_ticket = session.get("last_ticket_id") if _handoff_active(session) else None
        ticket_id = existing_ticket

        if not ticket_id:
            ticket_id = await _dispatch_ticket(
                tenant_id=tenant,
                user_id=user_id,
                session=session,
                language=emergency_lang,
                text_direction=("rtl" if emergency_lang == "ar" else "ltr"),
                arabic_tone=select_arabic_tone(message_text) if emergency_lang == "ar" else None,
                kpi_signals=kpi_signals + ["emergency_detected"],
                decision_rule="controller_emergency_override",
                decision_reason="Emergency keywords detected",
                urgent=True,
            )
            session["last_ticket_id"] = ticket_id

            # start sticky handoff
            session["handoff_active"] = True
            session["handoff_until"] = (_utcnow() + timedelta(minutes=HANDOFF_STICKY_MINUTES)).isoformat()

        short_ref = _short_ref(ticket_id)
        out = _emergency_protocol_message(emergency_lang, kind, short_ref)

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {
            "tenant_id": tenant,
            "mode": "EMERGENCY",
            "state": session.get("state"),
            "handoff_active": True,
            "emergency_hold": True,
            "ticket_id": ticket_id,
        }

    # ---------------------------------------------------------
    # 2) Emergency hold behavior (UX polish)
    # - "Thanks/OK" => polite + reminder (no guard prompt)
    # - 0 or 1 or booking/menu intent => show guard prompt
    # ---------------------------------------------------------
    if _emergency_hold_active(session):
        hold_lang = session.get("emergency_language") or ("ar" if str(session.get("language") or "ar").startswith("ar") else "en")
        hold_lang = "ar" if str(hold_lang).startswith("ar") else "en"

        if _is_thanks(message_text):
            out = _emergency_polite_after_thanks(hold_lang)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "mode": "EMERGENCY", "emergency_hold": True}

        if raw in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "99"}:
            out = _emergency_guard_prompt(hold_lang)
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "mode": "EMERGENCY", "emergency_hold": True}

        # any other text -> gentle guard
        out = _emergency_guard_prompt(hold_lang)
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return out, {"tenant_id": tenant, "mode": "EMERGENCY", "emergency_hold": True}

    # ---------------------------------------------------------
    # 3) Normal greeting (first contact)
    # ---------------------------------------------------------
    if not bool(session.get("has_greeted")) and cleaned:
        # If user starts with Hello/Hi/السلام, show welcome in detected language hint
        t = cleaned.lower()
        if t in {"hi", "hello", "hey"} or _looks_arabic(message_text):
            session["has_greeted"] = True
            out = _welcome_text_ar() if _looks_arabic(message_text) else _welcome_text_en()
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
            return out, {"tenant_id": tenant, "state": session.get("state"), "welcome": True}

    # ---------------------------------------------------------
    # 4) Normal language resolution
    # ---------------------------------------------------------
    language = _resolve_language_for_turn(cleaned, session)
    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"
    arabic_tone = select_arabic_tone(message_text) if language == "ar" else None

    # ---------------------------------------------------------
    # 5) Sticky handoff silence (non-emergency only)
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

    # Agent override
    if _wants_agent(cleaned):
        ticket_id = await _dispatch_ticket(
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

        session["handoff_active"] = True
        session["handoff_until"] = (_utcnow() + timedelta(minutes=HANDOFF_STICKY_MINUTES)).isoformat()
        session["state"] = "ESCALATION"
        session["last_step"] = "ESCALATION"
        session["last_ticket_id"] = ticket_id

        short_ref = _short_ref(ticket_id)
        if language == "ar":
            reply = f"تم تحويلكم إلى موظف الاستقبال ✅ رقم الطلب: #{short_ref}\nللعودة للقائمة اكتب 0" if short_ref else "تم تحويلكم إلى موظف الاستقبال ✅\nللعودة للقائمة اكتب 0"
        else:
            reply = f"Connecting you to Reception ✅ Ref: #{short_ref}\nReply 0 for the menu" if short_ref else "Connecting you to Reception ✅\nReply 0 for the menu"

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return reply, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "ticket_id": ticket_id}

    if is_incident_mode():
        kpi_signals.append("incident_mode")

    # ---------------------------------------------------------
    # 6) Engine routing
    # ---------------------------------------------------------
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
        "mode": session2.get("mode", "NORMAL"),
        "state": session2.get("state"),
        "status": session2.get("status"),
        "last_step": session2.get("last_step"),
        "language": session2.get("language"),
        "language_locked": session2.get("language_locked"),
        "handoff_active": session2.get("handoff_active"),
        "urgent_flag": bool(session2.get("urgent_flag")),
        "emergency_hold": bool(_emergency_hold_active(session2)),
        "ticket_id": session2.get("last_ticket_id"),
    }