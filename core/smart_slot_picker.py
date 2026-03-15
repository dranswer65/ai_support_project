# core/smart_slot_picker.py
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CLINIC_TZ = (os.getenv("CLINIC_TZ") or "Asia/Riyadh").strip()

# -------------------------
# Cursor helpers
# -------------------------
def encode_cursor(slot_date: str, slot_time: str) -> str:
    payload = {"d": slot_date, "t": slot_time}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

def decode_cursor(cursor: str) -> Optional[Tuple[str, str]]:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
        obj = json.loads(raw.decode("utf-8"))
        d = str(obj.get("d") or "").strip()
        t = str(obj.get("t") or "").strip()
        if not d or not t:
            return None
        return d, t
    except Exception:
        return None

# -------------------------
# Engine text detection (post-process hook)
# -------------------------
def should_start_picker(reply_text: str) -> bool:
    """
    Heuristic: detect when engine is asking user to choose a time slot.
    Adjust keywords if needed.
    """
    t = (reply_text or "").lower()

    keywords = [
        "choose a time",
        "select a time",
        "available times",
        "available slots",
        "اختر الوقت",
        "اختر موعد",
        "اختار الوقت",
        "المواعيد المتاحة",
        "اختر من المواعيد",
    ]
    return any(k in t for k in keywords)

# -------------------------
# Public: query available slots (PRODUCTION filters)
# -------------------------
async def get_available_slots_page(
    *,
    db: AsyncSession,
    tenant_id: str,
    doctor_key: str,
    tz_name: str,
    days_ahead: int,
    page_size: int,
    next_cursor: Optional[str],
    slot_minutes: int = 15,
) -> Dict[str, Any]:
    """
    Filters:
      - OPEN slots only
      - exclude past times today (clinic tz)
      - exclude active holds
      - exclude overlaps with timeoff
      - paginated by (slot_date, slot_time) cursor
    """
    tenant_id = (tenant_id or "default").strip()
    doctor_key = (doctor_key or "").strip()
    tz_name = (tz_name or DEFAULT_CLINIC_TZ).strip()

    if not doctor_key:
        return {"ok": False, "reason": "doctor_key_required"}

    if days_ahead < 1:
        days_ahead = 1
    if days_ahead > 60:
        days_ahead = 60

    if page_size < 1:
        page_size = 1
    if page_size > 50:
        page_size = 50

    if slot_minutes < 5:
        slot_minutes = 5
    if slot_minutes > 60:
        slot_minutes = 60

    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_local = now_local.date()
    end_date = today_local + timedelta(days=days_ahead)

    # cursor
    cur = decode_cursor(next_cursor or "")
    cursor_date = None
    cursor_time = None
    if cur:
        cursor_date, cursor_time = cur

    # We compare slot_time lexicographically (HH:MM) and slot_date as DATE.
    # NOTE: make sure slot_time stored as TIME or 'HH:MM' compatible.
    sql = """
        SELECT s.slot_date, s.slot_time
        FROM appointment_slots s
        WHERE s.tenant_id = :tenant_id
          AND s.doctor_key = :doctor_key
          AND s.status = 'OPEN'
          AND s.slot_date >= :start_date
          AND s.slot_date <= :end_date

          -- Exclude past times today (clinic local)
          AND (
                s.slot_date > :today
                OR (s.slot_date = :today AND s.slot_time >= :now_time)
              )

          -- Cursor pagination
          AND (
                :cursor_date IS NULL
                OR (s.slot_date > :cursor_date)
                OR (s.slot_date = :cursor_date AND s.slot_time > :cursor_time)
              )

          -- Exclude active holds
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

          -- Exclude time-off overlaps
          AND NOT EXISTS (
                SELECT 1
                FROM doctor_time_off t
                WHERE t.tenant_id = s.tenant_id
                  AND t.doctor_key = s.doctor_key
                  AND (
                        ( (s.slot_date + s.slot_time) AT TIME ZONE :tz_name ) < t.ends_at
                    AND ( ( (s.slot_date + s.slot_time) AT TIME ZONE :tz_name )
                          + (:slot_minutes * INTERVAL '1 minute')
                        ) > t.starts_at
                      )
              )

        ORDER BY s.slot_date ASC, s.slot_time ASC
        LIMIT :limit_plus_one;
    """

    params = {
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "start_date": today_local,
        "end_date": end_date,
        "today": today_local,
        "now_time": now_local.strftime("%H:%M"),
        "tz_name": tz_name,
        "slot_minutes": slot_minutes,
        "cursor_date": cursor_date,
        "cursor_time": cursor_time,
        "limit_plus_one": page_size + 1,
    }

    res = await db.execute(text(sql), params)
    rows = res.mappings().all()

    # Determine next_cursor
    has_more = len(rows) > page_size
    page_rows = rows[:page_size]

    items: List[Dict[str, str]] = []
    for r in page_rows:
        d = r["slot_date"].isoformat() if hasattr(r["slot_date"], "isoformat") else str(r["slot_date"])
        t = str(r["slot_time"])[:5]
        items.append({"slot_date": d, "slot_time": t})

    new_cursor = None
    if has_more and page_rows:
        last = items[-1]
        new_cursor = encode_cursor(last["slot_date"], last["slot_time"])

    # WhatsApp-friendly grouping
    by_date: Dict[str, List[str]] = {}
    for it in items:
        by_date.setdefault(it["slot_date"], []).append(it["slot_time"])

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "doctor_key": doctor_key,
        "tz": tz_name,
        "date_from": today_local.isoformat(),
        "date_to": end_date.isoformat(),
        "page_size": page_size,
        "items": items,
        "by_date": [{"date": d, "times": by_date[d]} for d in sorted(by_date.keys())],
        "next_cursor": new_cursor,
    }

# -------------------------
# WhatsApp message formatting
# -------------------------
def format_slots_message(items, tz_name, has_more):

    if not items:
        return "No available times. Please try another date."

    morning = []
    afternoon = []
    evening = []

    for it in items:

        t = it["slot_time"]
        hour = int(t.split(":")[0])

        if hour < 12:
            morning.append(t)
        elif hour < 17:
            afternoon.append(t)
        else:
            evening.append(t)

    lines = []
    lines.append("Available appointment times")
    lines.append("")

    idx = 1

    if morning:
        lines.append("Morning")
        for t in morning:
            lines.append(f"{idx}️⃣ {t}")
            idx += 1
        lines.append("")

    if afternoon:
        lines.append("Afternoon")
        for t in afternoon:
            lines.append(f"{idx}️⃣ {t}")
            idx += 1
        lines.append("")

    if evening:
        lines.append("Evening")
        for t in evening:
            lines.append(f"{idx}️⃣ {t}")
            idx += 1
        lines.append("")

    lines.append("Reply with the number to choose")

    if has_more:
        lines.append("Type MORE to see more times")

    lines.append("Type 0 to return to menu")

    return "\n".join(lines)