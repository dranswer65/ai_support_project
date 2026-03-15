# whatsapp_controller.py — Enterprise-ready controller (V6.4 + Slot Picker Sidecar)
# Fixes:
# ✅ Sticky handoff silence after 99 (including "Thank you")
# ✅ Prevent language reset loops (don’t show language menu once locked)
# ✅ Keep Reception = 99 (not 9)
# ✅ Store last_intent for better handoff payloads
# ✅ Deterministic language resolution (AR chars -> ar, EN chars -> en)
# ✅ Upsert new session immediately to prevent duplicate webhook race
# ✅ Always return meta["actions"] so api_server can persist appointment_requests
#
# NEW (Sidecar Smart Slot Picker — NO engine.py changes):
# ✅ Optional, isolated “slot picker” micro-flow stored in session["meta"]["slot_picker"]
# ✅ Creates a 2–3 minute hold on selected slot (prevents double booking)
# ✅ Maps user choice -> HH:MM only (most compatible)
# ✅ Pagination with next_cursor + page_size ("MORE" to paginate)
# ✅ Designed to NOT affect existing WhatsApp conversation flow unless enabled
#
# IMPORTANT:
# ✅ NO emergency detection / NO symptom triage / NO emergency escalation.
#
# SAFE PHASE 8 INTEGRATION:
# ✅ Loads tenant_settings without changing existing reply/menu flow
# ✅ Uses tenant settings as safe defaults for language/tone/timezone metadata
# ✅ Does NOT inject custom greeting into engine replies yet
# ✅ Keeps tested Arabic booking flow intact

from __future__ import annotations

import os
import re
import json
import base64
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime, timezone, timedelta, date

import anyio
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from profiles.language.language_detector import detect_language
from profiles.language.arabic_tone_engine import select_arabic_tone
from core.engine import run_engine
from core.knowledge_retriever import build_knowledge_answer

from core.session_store_pg import get_session, upsert_session
from core.saas_limits import (
    enforce_subscription_active,
    enforce_ai_limit,
    get_usage_alert_level,
    SaaSLimitExceeded,
)

from incident.incident_state import is_incident_mode

from escalation_router import route_escalation
from handoff_builder import build_handoff_payload

# Slot hold store (already in your project)
from core.slot_holds_store_pg import create_slot_hold, release_slot_hold

WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

# Clinic timezone used for "past times today" and slot window defaults (for picker)
CLINIC_TZ = (os.getenv("CLINIC_TZ") or "Asia/Riyadh").strip()

# Optional: auto-activate picker when engine enters one of these states.
# Leave empty to avoid ANY behavior change in your current flow.
# Example: SLOT_PICKER_TRIGGER_STATES="BOOK_PICK_TIME,APPT_PICK_TIME"
SLOT_PICKER_TRIGGER_STATES = [
    s.strip() for s in (os.getenv("SLOT_PICKER_TRIGGER_STATES") or "").split(",") if s.strip()
]

# Optional: allow a safe manual trigger phrase for internal testing (won't break existing flow)
# Example: SLOT_PICKER_TEST_TRIGGER="picktime"
SLOT_PICKER_TEST_TRIGGER = (os.getenv("SLOT_PICKER_TEST_TRIGGER") or "").strip().lower()

# Default picker settings
SLOT_PICKER_PAGE_SIZE_DEFAULT = int(os.getenv("SLOT_PICKER_PAGE_SIZE", "10") or "10")
SLOT_PICKER_PAGE_SIZE_DEFAULT = max(5, min(25, SLOT_PICKER_PAGE_SIZE_DEFAULT))

SLOT_PICKER_HOLD_MINUTES_DEFAULT = int(os.getenv("SLOT_PICKER_HOLD_MINUTES", "2") or "2")
SLOT_PICKER_HOLD_MINUTES_DEFAULT = max(1, min(10, SLOT_PICKER_HOLD_MINUTES_DEFAULT))

SLOT_PICKER_DAYS_AHEAD_DEFAULT = int(os.getenv("SLOT_PICKER_DAYS_AHEAD", "14") or "14")
SLOT_PICKER_DAYS_AHEAD_DEFAULT = max(1, min(60, SLOT_PICKER_DAYS_AHEAD_DEFAULT))

_AGENT_KEYS = [
    "agent", "reception", "human", "representative", "help", "support",
    "موظف", "الاستقبال", "استقبال", "إنسان", "موظف الاستقبال", "موظف استقبال"
]

HANDOFF_STICKY_MINUTES = 30

_AR_RE = re.compile(r"[\u0600-\u06FF]")
_EN_RE = re.compile(r"[A-Za-z]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# =========================================================
# BASIC HELPERS
# =========================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _looks_arabic(text: str) -> bool:
    return bool(_AR_RE.search(text or ""))


def _looks_english(text: str) -> bool:
    return bool(_EN_RE.search(text or ""))


def _normalize_input(text: str) -> str:
    t = (text or "").strip()
    for ch in ["،", ",", "٫", ";", "؛", "。"]:
        t = t.replace(ch, "")
    t = t.translate(_ARABIC_DIGITS)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or WA_DEFAULT_CLIENT or "default").strip()
    return t or "default"


async def _load_tenant_settings(db: AsyncSession, tenant_id: str) -> Dict[str, Any]:
    tenant_id = _norm_tenant(tenant_id)

    try:
        res = await db.execute(
            text(
                """
                SELECT
                    ts.tenant_id,
                    ts.clinic_name,
                    ts.logo_url,
                    ts.brand_color,
                    ts.timezone,
                    ts.language,
                    ts.whatsapp_greeting,
                    ts.ai_tone,
                    ts.reminder_hours_before
                FROM tenant_settings ts
                WHERE ts.tenant_id = :tenant_id
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = res.mappings().first()
        if row:
            return dict(row)
    except Exception:
        pass

    return {
        "tenant_id": tenant_id,
        "clinic_name": None,
        "logo_url": None,
        "brand_color": None,
        "timezone": CLINIC_TZ,
        "language": "en",
        "whatsapp_greeting": None,
        "ai_tone": "formal",
        "reminder_hours_before": 24,
    }


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


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


def _short_ref(ticket_id: Optional[str]) -> Optional[str]:
    if not ticket_id or not isinstance(ticket_id, str):
        return None
    head = ticket_id.split("-")[0].upper()
    return head[:8] if head else None


def _extract_ticket_id(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    if result.get("ticket_id"):
        return str(result.get("ticket_id"))
    inner = result.get("result")
    if isinstance(inner, dict):
        tid = inner.get("ticket_id") or inner.get("id")
        return str(tid) if tid else None
    return None


def _wants_agent(text: str) -> bool:
    t = (text or "").strip().lower()
    if t == "99":
        return True
    if t == "9":  # keep 9 NOT reception
        return False
    return any(k in t for k in _AGENT_KEYS)


def _resolve_language_for_turn(message_text: str, session: Dict[str, Any]) -> str:
    if bool(session.get("language_locked")):
        return "ar" if str(session.get("language") or "ar").startswith("ar") else "en"

    if _looks_arabic(message_text):
        return "ar"
    if _looks_english(message_text):
        return "en"

    raw = (message_text or "").strip()
    if raw.isdigit():
        return "en"

    detected = (detect_language(message_text) or "en").strip().lower()
    return "ar" if detected.startswith("ar") else "en"

# =========================================================
# SLOT PICKER SIDECAR (NO ENGINE CHANGES)
# =========================================================

def _hhmm(x: str) -> str:
    s = (x or "").strip()
    return s[:5]  # "10:00:00" -> "10:00"


def _parse_int(s: str) -> Optional[int]:
    try:
        return int((s or "").strip())
    except Exception:
        return None


def _is_more(text_in: str) -> bool:
    t = (text_in or "").strip().lower()
    return t in {"more", "m", "more times", "next", "التالي", "المزيد", "التالي>"}


def _parse_date_yyyy_mm_dd(s: str) -> Optional[date]:
    try:
        return date.fromisoformat((s or "").strip())
    except Exception:
        return None


def _cursor_encode(slot_date: str, slot_time: str) -> str:
    payload = {"d": slot_date, "t": slot_time}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _cursor_decode(cur: Optional[str]) -> Optional[Dict[str, str]]:
    if not cur:
        return None
    try:
        pad = "=" * (-len(cur) % 4)
        raw = base64.urlsafe_b64decode((cur + pad).encode("utf-8"))
        obj = json.loads(raw.decode("utf-8"))
        d = str(obj.get("d") or "").strip()
        t = str(obj.get("t") or "").strip()
        if d and t:
            return {"d": d, "t": t}
        return None
    except Exception:
        return None


def _picker_get(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    meta = session.get("meta")
    if not isinstance(meta, dict):
        return None
    picker = meta.get("slot_picker")
    return picker if isinstance(picker, dict) else None


def _picker_set(session: Dict[str, Any], picker: Dict[str, Any]) -> None:
    meta = session.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta["slot_picker"] = picker
    session["meta"] = meta


def _picker_message(items: List[Dict[str, str]], has_more: bool, language: str) -> str:
    if not items:
        if language == "ar":
            return "لا توجد مواعيد متاحة حالياً. جرّب تاريخاً آخر أو اكتب 0 للعودة للقائمة."
        return "No available times right now. Try another date, or reply 0 to return to menu."

    lines: List[str] = []
    lines.append("المواعيد المتاحة:" if language == "ar" else "Available appointment times:")
    lines.append("")

    for i, it in enumerate(items, start=1):
        lines.append(f"{i}) {it['slot_date']} {it['slot_time']}")

    lines.append("")
    if language == "ar":
        lines.append("اكتب رقم الموعد للاختيار.")
        if has_more:
            lines.append("اكتب MORE لعرض المزيد.")
        lines.append("للعودة للقائمة اكتب 0.")
    else:
        lines.append("Reply with the number to choose.")
        if has_more:
            lines.append("Reply MORE to see more times.")
        lines.append("Reply 0 to return to menu.")

    return "\n".join(lines)


async def _fetch_slots_page(
    db: AsyncSession,
    *,
    tenant_id: str,
    doctor_key: str,
    date_from: str,
    date_to: str,
    tz_name: str,
    page_size: int,
    next_cursor: Optional[str],
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    doctor_key = (doctor_key or "").strip()
    tz_name = (tz_name or CLINIC_TZ or "Asia/Riyadh").strip()
    tz = ZoneInfo(tz_name)

    now_local = datetime.now(tz)
    today_local = now_local.date()
    now_hhmm = now_local.strftime("%H:%M")

    cur = _cursor_decode(next_cursor)
    after_d = cur["d"] if cur else None
    after_t = cur["t"] if cur else None

    slot_minutes = 15

    sql = """
        SELECT
            s.slot_date::text AS slot_date,
            to_char(s.slot_time, 'HH24:MI') AS slot_time
        FROM appointment_slots s
        WHERE s.tenant_id = :tenant_id
          AND s.doctor_key = :doctor_key
          AND s.status = 'OPEN'
          AND s.slot_date >= :date_from::date
          AND s.slot_date <= :date_to::date

          AND (
                s.slot_date::date > :today_local::date
                OR (s.slot_date::date = :today_local::date AND to_char(s.slot_time,'HH24:MI') >= :now_hhmm)
              )

          AND NOT EXISTS (
                SELECT 1
                FROM slot_holds h
                WHERE h.tenant_id = s.tenant_id
                  AND h.doctor_key = s.doctor_key
                  AND h.slot_date = s.slot_date
                  AND h.slot_time = s.slot_time
                  AND h.status = 'HELD'
                  AND h.expires_at > NOW()
              )

          AND NOT EXISTS (
                SELECT 1
                FROM appointments a
                WHERE a.tenant_id = s.tenant_id
                  AND a.doctor_key = s.doctor_key
                  AND a.slot_date = s.slot_date
                  AND a.slot_time = s.slot_time
                  AND a.status IN ('CONFIRMED','BOOKED')
              )

          AND NOT EXISTS (
                SELECT 1
                FROM doctor_time_off t
                WHERE t.tenant_id = s.tenant_id
                  AND t.doctor_key = s.doctor_key
                  AND ((s.slot_date + s.slot_time) AT TIME ZONE :tz_name) < t.ends_at
                  AND (((s.slot_date + s.slot_time) AT TIME ZONE :tz_name) + (:slot_minutes * INTERVAL '1 minute')) > t.starts_at
              )
    """

    params: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "date_from": date_from,
        "date_to": date_to,
        "tz_name": tz_name,
        "slot_minutes": slot_minutes,
        "today_local": str(today_local),
        "now_hhmm": now_hhmm,
        "page_size": int(page_size),
    }

    if after_d and after_t:
        sql += """
          AND (
                s.slot_date::text > :after_d
                OR (s.slot_date::text = :after_d AND to_char(s.slot_time,'HH24:MI') > :after_t)
              )
        """
        params["after_d"] = after_d
        params["after_t"] = after_t

    sql += """
        ORDER BY s.slot_date ASC, s.slot_time ASC
        LIMIT :page_size;
    """

    res = await db.execute(text(sql), params)
    items = [dict(r) for r in res.mappings().all()]

    new_cur = None
    if items:
        last = items[-1]
        new_cur = _cursor_encode(last["slot_date"], last["slot_time"])

    return items, new_cur


async def _picker_activate(
    *,
    db: AsyncSession,
    session: Dict[str, Any],
    tenant_id: str,
    user_id: str,
    doctor_key: str,
    language: str,
    tz_name: Optional[str] = None,
    days_ahead: int = SLOT_PICKER_DAYS_AHEAD_DEFAULT,
    page_size: int = SLOT_PICKER_PAGE_SIZE_DEFAULT,
) -> str:
    tz_name = (tz_name or CLINIC_TZ or "Asia/Riyadh").strip()
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()

    df = today.isoformat()
    dt = (today + timedelta(days=days_ahead)).isoformat()

    picker: Dict[str, Any] = {
        "active": True,
        "doctor_key": (doctor_key or "").strip(),
        "tz": tz_name,
        "date_from": df,
        "date_to": dt,
        "page_size": int(page_size),
        "hold_minutes": int(SLOT_PICKER_HOLD_MINUTES_DEFAULT),
        "next_cursor": None,
        "last_items": [],
        "selected": None,
        "hold_id": None,
    }

    items, cur = await _fetch_slots_page(
        db,
        tenant_id=tenant_id,
        doctor_key=picker["doctor_key"],
        date_from=picker["date_from"],
        date_to=picker["date_to"],
        tz_name=picker["tz"],
        page_size=picker["page_size"],
        next_cursor=None,
    )

    picker["last_items"] = items
    picker["next_cursor"] = cur

    _picker_set(session, picker)
    await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant_id)

    return _picker_message(items, has_more=bool(cur), language=language)

async def _picker_handle_if_active(
    *,
    db: AsyncSession,
    session: Dict[str, Any],
    tenant_id: str,
    user_id: str,
    text_in: str,
    language: str,
) -> Tuple[bool, Optional[str], Dict[str, Any], Optional[str]]:
    picker = _picker_get(session)
    if not isinstance(picker, dict) or not picker.get("active"):
        return False, None, session, None

    raw = (text_in or "").strip()

    if raw == "0":
        picker["active"] = False
        _picker_set(session, picker)
        return True, "0", session, "0"

    if _is_more(raw):
        items, cur = await _fetch_slots_page(
            db,
            tenant_id=tenant_id,
            doctor_key=str(picker.get("doctor_key") or ""),
            date_from=str(picker.get("date_from") or ""),
            date_to=str(picker.get("date_to") or ""),
            tz_name=str(picker.get("tz") or CLINIC_TZ),
            page_size=int(picker.get("page_size") or SLOT_PICKER_PAGE_SIZE_DEFAULT),
            next_cursor=str(picker.get("next_cursor") or "") or None,
        )
        picker["last_items"] = items
        picker["next_cursor"] = cur
        _picker_set(session, picker)
        return True, _picker_message(items, has_more=bool(cur), language=language), session, None

    n = _parse_int(raw)
    items = list(picker.get("last_items") or [])

    if n is None:
        return True, _picker_message(items, has_more=bool(picker.get("next_cursor")), language=language), session, None

    if n < 1 or n > len(items):
        if language == "ar":
            return True, "اختيار غير صحيح. اكتب رقم من القائمة أو MORE للمزيد.", session, None
        return True, "Invalid choice. Reply with a number from the list, or MORE.", session, None

    chosen = items[n - 1]
    slot_date = str(chosen.get("slot_date") or "").strip()
    slot_time = _hhmm(str(chosen.get("slot_time") or ""))

    if not slot_date or not slot_time:
        if language == "ar":
            return True, "حدث خطأ في قراءة الموعد. اكتب MORE لعرض القائمة مرة أخرى.", session, None
        return True, "Failed to read that slot. Reply MORE to reload times.", session, None

    old_hold = str(picker.get("hold_id") or "").strip()
    if old_hold:
        try:
            await release_slot_hold(db=db, tenant_id=tenant_id, hold_id=old_hold)
        except Exception:
            pass
        picker["hold_id"] = None

    expires_at = session.get("slot_hold_expires")
    if expires_at:
        now_ts = datetime.utcnow().timestamp()
        try:
            exp_ts = float(expires_at)
        except Exception:
            exp_ts = 0.0

        if exp_ts and now_ts > exp_ts:
            session["slot_hold_id"] = None
            session["slot_hold_expires"] = None
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant_id)

            if language == "ar":
                msg = "انتهت صلاحية الموعد السابق. اختر موعداً آخر من القائمة."
            else:
                msg = "That slot expired. Please choose another time."

            return True, msg + "\n\n" + _picker_message(items, has_more=bool(picker.get("next_cursor")), language=language), session, None

    out = await create_slot_hold(
        db=db,
        tenant_id=tenant_id,
        doctor_key=str(picker.get("doctor_key") or ""),
        slot_date=slot_date,
        slot_time=slot_time,
        user_id=user_id,
        hold_minutes=int(picker.get("hold_minutes") or SLOT_PICKER_HOLD_MINUTES_DEFAULT),
    )

    if not out.get("ok"):
        items2, cur2 = await _fetch_slots_page(
            db,
            tenant_id=tenant_id,
            doctor_key=str(picker.get("doctor_key") or ""),
            date_from=str(picker.get("date_from") or ""),
            date_to=str(picker.get("date_to") or ""),
            tz_name=str(picker.get("tz") or CLINIC_TZ),
            page_size=int(picker.get("page_size") or SLOT_PICKER_PAGE_SIZE_DEFAULT),
            next_cursor=None,
        )
        picker["last_items"] = items2
        picker["next_cursor"] = cur2
        _picker_set(session, picker)

        if language == "ar":
            msg = "هذا الموعد تم حجزه للتو. اختر موعداً آخر:\n\n"
        else:
            msg = "That time was just taken. Please pick another:\n\n"

        return True, msg + _picker_message(items2, has_more=bool(cur2), language=language), session, None

    hold_id = str(out.get("hold_id") or "").strip()

    picker["selected"] = {"slot_date": slot_date, "slot_time": slot_time}
    picker["hold_id"] = hold_id
    picker["active"] = False
    _picker_set(session, picker)

    session["slot_hold_id"] = hold_id
    session["slot_hold_expires"] = datetime.utcnow().timestamp() + (
        60 * int(picker.get("hold_minutes") or SLOT_PICKER_HOLD_MINUTES_DEFAULT)
    )

    injected = slot_time

    if language == "ar":
        reply = f"تم اختيار الموعد ✅ {slot_date} {slot_time}"
    else:
        reply = f"Selected ✅ {slot_date} {slot_time}"

    return True, reply, session, injected


def _should_autostart_picker(session2: Dict[str, Any]) -> bool:
    if not SLOT_PICKER_TRIGGER_STATES:
        return False
    st = str(session2.get("state") or "").strip()
    return bool(st) and st in SLOT_PICKER_TRIGGER_STATES


def _try_extract_doctor_key(session2: Dict[str, Any]) -> Optional[str]:
    for k in ["doctor_key", "selected_doctor_key", "appt_doctor_key"]:
        v = session2.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    ctx = session2.get("context")
    if isinstance(ctx, dict):
        v = ctx.get("doctor_key") or ctx.get("selected_doctor_key")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# =========================================================
# ESCALATION (AGENT OVERRIDE ONLY)
# =========================================================

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


# =========================================================
# MAIN CONTROLLER
# =========================================================

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

    tenant_settings = await _load_tenant_settings(db, tenant)

    settings_language = str(tenant_settings.get("language") or "en").strip().lower()
    if settings_language not in {"en", "ar"}:
        settings_language = "en"

    settings_tone = str(tenant_settings.get("ai_tone") or "formal").strip().lower()
    if settings_tone not in {"formal", "friendly"}:
        settings_tone = "formal"

    settings_timezone = str(tenant_settings.get("timezone") or CLINIC_TZ or "Asia/Riyadh").strip()

    cleaned = _normalize_input(message_text)
    raw = cleaned

    session = await get_session(db, user_id=user_id, tenant_id=tenant)
    is_new_session = not isinstance(session, dict) or not session

    if is_new_session:
        session = {
            "user_id": user_id,
            "status": "ACTIVE",
            "state": "LANG_SELECT",
            "last_step": "LANG_SELECT",
            "language": settings_language,
            "language_locked": False,
            "text_direction": "rtl" if settings_language == "ar" else "ltr",
            "has_greeted": False,
            "conversation_version": 6,
            "escalation_flag": False,
            "urgent_flag": False,
            "handoff_active": False,
            "handoff_until": None,
            "last_ticket_id": None,
            "last_intent": None,
            "meta": {
                "tenant_settings": {
                    "tenant_id": tenant,
                    "clinic_name": tenant_settings.get("clinic_name"),
                    "timezone": settings_timezone,
                    "language": settings_language,
                    "ai_tone": settings_tone,
                    "reminder_hours_before": int(tenant_settings.get("reminder_hours_before") or 24),
                }
            },
            "slot_hold_id": None,
            "slot_hold_expires": None,
        }
        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

    session["last_user_message"] = cleaned
    session["last_intent"] = session.get("intent") or session.get("last_intent")

    meta = session.get("meta")
    if not isinstance(meta, dict):
        meta = {}

    tenant_meta = meta.get("tenant_settings")
    if not isinstance(tenant_meta, dict):
        tenant_meta = {}

    tenant_meta["clinic_name"] = tenant_settings.get("clinic_name")
    tenant_meta["timezone"] = settings_timezone
    tenant_meta["language"] = settings_language
    tenant_meta["ai_tone"] = settings_tone
    tenant_meta["tenant_id"] = tenant
    tenant_meta["reminder_hours_before"] = int(tenant_settings.get("reminder_hours_before") or 24)

    meta["tenant_settings"] = tenant_meta
    session["meta"] = meta

    if _handoff_active(session):
        if raw == "0":
            session["handoff_active"] = False
            session["handoff_until"] = None
            session["state"] = "MAIN_MENU"
            session["last_step"] = "MAIN_MENU"
            await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)

            engine_out = run_engine(
                session=session,
                user_message="0",
                language=_resolve_language_for_turn("0", session),
            )
            reply_text = (engine_out.get("reply_text") or "").strip()
            session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
            session2["last_intent"] = session2.get("intent") or session2.get("last_intent")

            actions = engine_out.get("actions") or []
            await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

            return reply_text, {
                "tenant_id": tenant,
                "state": session2.get("state"),
                "handoff_active": False,
                "actions": actions,
            }

        await upsert_session(db, user_id=user_id, session=session, tenant_id=tenant)
        return "", {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "actions": []}

    language = _resolve_language_for_turn(cleaned, session)

    if not cleaned.strip() and settings_language in {"en", "ar"}:
        language = settings_language

    session["language"] = language
    session["text_direction"] = "rtl" if language == "ar" else "ltr"

    arabic_tone = select_arabic_tone(cleaned) if language == "ar" else None
    if language == "ar" and not arabic_tone:
        arabic_tone = settings_tone

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
        meta = {"tenant_id": tenant, "state": session.get("state"), "handoff_active": True, "actions": []}
        meta.update(extra)
        return reply, meta

    if is_incident_mode():
        kpi_signals.append("incident_mode")

    handled, picker_reply, session_after_picker, injected = await _picker_handle_if_active(
        db=db,
        session=session,
        tenant_id=tenant,
        user_id=user_id,
        text_in=cleaned,
        language=language,
    )

    if handled:
        await upsert_session(db, user_id=user_id, session=session_after_picker, tenant_id=tenant)

        if injected:
            engine_out = run_engine(
                session=session_after_picker,
                user_message=injected,
                language=language,
                arabic_tone=arabic_tone,
                kpi_signals=kpi_signals,
            )

            from core.usage_logger import log_usage_event

            await log_usage_event(
                db,
                tenant,
                "ai_conversations",
                1
            )

            reply_text = (engine_out.get("reply_text") or "").strip()
            actions = engine_out.get("actions") or []
            session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session_after_picker
            session2["last_intent"] = session2.get("intent") or session2.get("last_intent")
            await upsert_session(db, user_id=user_id, session=session2, tenant_id=tenant)

            combined = reply_text
            if picker_reply:
                combined = (picker_reply + "\n\n" + reply_text).strip()

            return combined, {
                "tenant_id": tenant,
                "state": session2.get("state"),
                "status": session2.get("status"),
                "last_step": session2.get("last_step"),
                "language": session2.get("language"),
                "language_locked": session2.get("language_locked"),
                "handoff_active": session2.get("handoff_active"),
                "urgent_flag": bool(session2.get("urgent_flag")),
                "actions": actions,
            }

        return (picker_reply or ""), {
            "tenant_id": tenant,
            "state": session_after_picker.get("state"),
            "status": session_after_picker.get("status"),
            "last_step": session_after_picker.get("last_step"),
            "language": session_after_picker.get("language"),
            "language_locked": session_after_picker.get("language_locked"),
            "handoff_active": session_after_picker.get("handoff_active"),
            "urgent_flag": bool(session_after_picker.get("urgent_flag")),
            "actions": [],
        }

    if SLOT_PICKER_TEST_TRIGGER and cleaned.lower() == SLOT_PICKER_TEST_TRIGGER:
        dk = _try_extract_doctor_key(session)
        if not dk:
            msg = "Missing doctor_key in session. Select a doctor first." if language == "en" else "لم يتم تحديد الطبيب بعد. اختر الطبيب أولاً."
            return msg, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": False, "actions": []}

        reply = await _picker_activate(
            db=db,
            session=session,
            tenant_id=tenant,
            user_id=user_id,
            doctor_key=dk,
            language=language,
        )
        return reply, {"tenant_id": tenant, "state": session.get("state"), "handoff_active": False, "actions": []}


    # NORMAL FLOW: subscription + SaaS AI usage enforcement
    # ---------------------------------------------------------
    try:
        await enforce_subscription_active(db, tenant)
    except SaaSLimitExceeded as e:
        return (
            f"⚠️ {str(e)}",
            {
                "tenant_id": tenant,
                "state": session.get("state"),
                "handoff_active": False,
                "actions": [],
            },
        )

    try:
        await enforce_ai_limit(db, tenant)
    except SaaSLimitExceeded:
        alert = await get_usage_alert_level(db, tenant)

        plan_name = (alert.get("plan_name") or alert.get("plan_code") or "current").strip()
        used = int(alert.get("used") or 0)
        limit = int(alert.get("limit") or 0)

        return (
            f"⚠️ Your clinic has reached the {plan_name} AI usage limit ({used}/{limit}).\n"
            f"Please upgrade your subscription to continue using the assistant.",
            {
                "tenant_id": tenant,
                "state": session.get("state"),
                "handoff_active": False,
                "actions": [],
            },
        )

    current_state = str(session.get("state") or "")

    transaction_states = {
        "BOOK_DEPT",
        "BOOK_DOCTOR",
        "BOOK_DATE",
        "BOOK_SLOT",
        "BOOK_PATIENT",
        "BOOK_CONFIRM",
        "RESCHEDULE_LOOKUP",
        "RESCHEDULE_NEW_DATE",
        "RESCHEDULE_NEW_SLOT",
        "RESCHEDULE_CONFIRM",
        "CANCEL_LOOKUP",
        "CANCEL_CONFIRM",
    }

    if current_state not in transaction_states:
        try:
            tenant_meta = (session.get("meta") or {}).get("tenant_settings", {}) or {}
            clinic_name = str(tenant_meta.get("clinic_name") or "").strip()

            kb_answer = await build_knowledge_answer(
                db=db,
                tenant_id=tenant,
                user_message=cleaned,
                language=language,
                clinic_name=clinic_name,
            )

            if kb_answer:
                reply_text = kb_answer + (
                    "\n\n0️⃣ القائمة الرئيسية\n99️⃣ التحدث مع موظف الاستقبال"
                    if language == "ar"
                    else "\n\n0️⃣ Main Menu\n99️⃣ Speak to Reception"
                )

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
                    "actions": [],
                }
        except Exception:
            pass

    engine_out = run_engine(
        session=session,
        user_message=cleaned,
        language=language,
        arabic_tone=arabic_tone,
        kpi_signals=kpi_signals,
    )

    reply_text = (engine_out.get("reply_text") or "").strip()
    actions = engine_out.get("actions") or []
    session2 = engine_out.get("session") if isinstance(engine_out.get("session"), dict) else session
    session2["last_intent"] = session2.get("intent") or session2.get("last_intent")

    session2_meta = session2.get("meta")
    if not isinstance(session2_meta, dict):
        session2_meta = {}

    session2_tenant_meta = session2_meta.get("tenant_settings")
    if not isinstance(session2_tenant_meta, dict):
        session2_tenant_meta = {}

    session2_tenant_meta["clinic_name"] = tenant_settings.get("clinic_name")
    session2_tenant_meta["timezone"] = settings_timezone
    session2_tenant_meta["language"] = settings_language
    session2_tenant_meta["ai_tone"] = settings_tone
    session2_tenant_meta["reminder_hours_before"] = int(tenant_settings.get("reminder_hours_before") or 24)
    session2_tenant_meta["tenant_id"] = tenant

    session2_meta["tenant_settings"] = session2_tenant_meta
    session2["meta"] = session2_meta

    if _should_autostart_picker(session2):
        doctor_key = _try_extract_doctor_key(session2)
        if doctor_key:
            try:
                picker_text = await _picker_activate(
                    db=db,
                    session=session2,
                    tenant_id=tenant,
                    user_id=user_id,
                    doctor_key=doctor_key,
                    language=language,
                    tz_name=settings_timezone,
                )
                reply_text = picker_text
                actions = []
            except Exception:
                pass

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
        "actions": actions,
    }

    