from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None


DEFAULT_TZ = "Asia/Riyadh"


@dataclass
class ScheduleRule:
    doctor_key: str
    day_of_week: int  # 0=Mon..6=Sun
    start_time: str   # "10:00"
    end_time: str     # "14:00"
    slot_minutes: int
    is_active: bool


def _parse_hhmm(s: str) -> time:
    """
    Accepts: "10:00" or "10:00:00"
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("empty time")
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid time: {s}")
    hh = int(parts[0])
    mm = int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"invalid time: {s}")
    return time(hour=hh, minute=mm)


def _coerce_slot_minutes(x: Optional[int]) -> int:
    try:
        v = int(x or 15)
    except Exception:
        v = 15
    # production guardrails
    if v < 5:
        v = 5
    if v > 60:
        v = 60
    return v


def _tzinfo(tz_name: str):
    name = (tz_name or DEFAULT_TZ).strip() or DEFAULT_TZ
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


async def _load_rules(db: AsyncSession, tenant_id: str) -> List[ScheduleRule]:
    res = await db.execute(
        text("""
            SELECT doctor_key, day_of_week, start_time, end_time,
                   COALESCE(slot_minutes, 15) AS slot_minutes,
                   COALESCE(is_active, TRUE) AS is_active
            FROM doctor_schedule_rules
            WHERE tenant_id = :tenant_id
              AND COALESCE(is_active, TRUE) = TRUE
            ORDER BY doctor_key, day_of_week, start_time;
        """),
        {"tenant_id": tenant_id},
    )
    rows = res.mappings().all()
    out: List[ScheduleRule] = []
    for r in rows:
        out.append(
            ScheduleRule(
                doctor_key=str(r["doctor_key"]),
                day_of_week=int(r["day_of_week"]),
                start_time=str(r["start_time"]),
                end_time=str(r["end_time"]),
                slot_minutes=_coerce_slot_minutes(r["slot_minutes"]),
                is_active=bool(r["is_active"]),
            )
        )
    return out


async def _load_time_off_ranges(
    db: AsyncSession,
    tenant_id: str,
    doctor_key: str,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> List[Tuple[datetime, datetime]]:
    """
    Fetch time-off blocks that overlap the day window (UTC).
    """
    res = await db.execute(
        text("""
            SELECT starts_at, ends_at
            FROM doctor_time_off
            WHERE tenant_id = :tenant_id
              AND doctor_key = :doctor_key
              AND ends_at   > :day_start
              AND starts_at < :day_end
            ORDER BY starts_at ASC;
        """),
        {
            "tenant_id": tenant_id,
            "doctor_key": doctor_key,
            "day_start": day_start_utc,
            "day_end": day_end_utc,
        },
    )
    rows = res.mappings().all()
    out: List[Tuple[datetime, datetime]] = []
    for r in rows:
        s = r["starts_at"]
        e = r["ends_at"]
        if isinstance(s, datetime) and isinstance(e, datetime):
            out.append((s, e))
    return out


def _overlaps_any(slot_start_utc: datetime, slot_end_utc: datetime, blocks: List[Tuple[datetime, datetime]]) -> bool:
    for b_start, b_end in blocks:
        if slot_end_utc <= b_start:
            continue
        if slot_start_utc >= b_end:
            continue
        return True
    return False


def _iter_slots_for_rule(on_date: date, rule: ScheduleRule) -> List[str]:
    """
    Returns a list of slot_time strings "HH:MM" for that date using rule.
    NOTE: end_time is treated as exclusive (like calendars).
    """
    st = _parse_hhmm(rule.start_time)
    et = _parse_hhmm(rule.end_time)

    start_dt = datetime.combine(on_date, st)
    end_dt = datetime.combine(on_date, et)

    # handle overnight (e.g., 23:00-02:00)
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)

    minutes = rule.slot_minutes
    cur = start_dt
    out: List[str] = []
    while cur + timedelta(minutes=minutes) <= end_dt:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=minutes)
    return out


async def generate_slots(
    *,
    db: AsyncSession,
    tenant_id: str,
    tz_name: str = DEFAULT_TZ,
    days_ahead: int = 14,
    start_from: Optional[date] = None,
) -> Dict[str, int]:
    """
    Production-style idempotent slot generator.

    - Reads doctor_schedule_rules for tenant
    - For each day in [start_from, start_from + days_ahead)
      generates slots into appointment_slots with ON CONFLICT DO NOTHING
    - Skips time-off overlaps (doctor_time_off)

    Returns counters: {"days":..., "doctors":..., "inserted":..., "skipped_timeoff":...}
    """
    tenant_id = (tenant_id or "").strip() or "default"
    tz = _tzinfo(tz_name)

    if days_ahead < 1:
        days_ahead = 1
    if days_ahead > 60:
        days_ahead = 60  # safe cap

    start_from = start_from or datetime.now(tz).date()

    rules = await _load_rules(db, tenant_id)
    if not rules:
        return {"days": 0, "doctors": 0, "inserted": 0, "skipped_timeoff": 0}

    # group by doctor
    rules_by_doc: Dict[str, List[ScheduleRule]] = {}
    for r in rules:
        rules_by_doc.setdefault(r.doctor_key, []).append(r)

    inserted = 0
    skipped_timeoff = 0

    for dkey, drules in rules_by_doc.items():
        for i in range(days_ahead):
            day = start_from + timedelta(days=i)
            dow = day.weekday()  # 0=Mon..6=Sun

            # all rules matching this day
            day_rules = [r for r in drules if r.day_of_week == dow and r.is_active]
            if not day_rules:
                continue

            # day window in UTC for timeoff query
            day_start_local = datetime.combine(day, time(0, 0), tzinfo=tz)
            day_end_local = day_start_local + timedelta(days=1)

            day_start_utc = day_start_local.astimezone(timezone.utc)
            day_end_utc = day_end_local.astimezone(timezone.utc)

            timeoff_blocks = await _load_time_off_ranges(
                db, tenant_id, dkey, day_start_utc, day_end_utc
            )

            # generate slot strings per rule and insert
            for rule in day_rules:
                slot_times = _iter_slots_for_rule(day, rule)

                for t_str in slot_times:
                    # local slot start/end for timeoff overlap check
                    slot_start_local = datetime.fromisoformat(f"{day.isoformat()}T{t_str}:00").replace(tzinfo=tz)
                    slot_end_local = slot_start_local + timedelta(minutes=rule.slot_minutes)

                    # convert to UTC for overlap check
                    slot_start_utc = slot_start_local.astimezone(timezone.utc)
                    slot_end_utc = slot_end_local.astimezone(timezone.utc)

                    if timeoff_blocks and _overlaps_any(slot_start_utc, slot_end_utc, timeoff_blocks):
                        skipped_timeoff += 1
                        continue

                    res = await db.execute(
                        text("""
                            INSERT INTO appointment_slots
                                (tenant_id, doctor_key, slot_date, slot_time, status, created_at, updated_at)
                            VALUES
                                (:tenant_id, :doctor_key, :slot_date, :slot_time, 'OPEN', NOW(), NOW())
                            ON CONFLICT (tenant_id, doctor_key, slot_date, slot_time)
                            DO NOTHING;
                        """),
                        {
                            "tenant_id": tenant_id,
                            "doctor_key": dkey,
                            "slot_date": day,
                            "slot_time": t_str,
                        },
                    )
                    # rowcount is not reliable across all drivers on DO NOTHING,
                    # but in Postgres it usually works.
                    try:
                        if res.rowcount and res.rowcount > 0:
                            inserted += res.rowcount
                    except Exception:
                        pass

    await db.commit()

    return {
        "days": days_ahead,
        "doctors": len(rules_by_doc),
        "inserted": inserted,
        "skipped_timeoff": skipped_timeoff,
    }