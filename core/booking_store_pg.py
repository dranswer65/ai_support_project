# core/booking_store_pg.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================
# CREATE ALL BOOKING TABLES (SAFE — WILL NOT DELETE DATA)
# ============================================================

async def ensure_booking_tables(db: AsyncSession) -> None:

    # ---------------- CLINICS ----------------
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS clinics (
      clinic_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      country TEXT DEFAULT '',
      timezone TEXT NOT NULL DEFAULT 'Asia/Riyadh',
      default_language TEXT NOT NULL DEFAULT 'ar',
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """))

    # ---------------- DEPARTMENTS ----------------
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS departments (
      id BIGSERIAL PRIMARY KEY,
      clinic_id TEXT NOT NULL REFERENCES clinics(clinic_id) ON DELETE CASCADE,
      name_ar TEXT NOT NULL,
      name_en TEXT NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_departments_clinic 
    ON departments(clinic_id);
    """))

    # ---------------- DOCTORS ----------------
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS doctors (
      doctor_id BIGSERIAL PRIMARY KEY,
      clinic_id TEXT NOT NULL REFERENCES clinics(clinic_id) ON DELETE CASCADE,
      department_id BIGINT REFERENCES departments(id) ON DELETE SET NULL,
      name TEXT NOT NULL,
      languages TEXT[] NOT NULL DEFAULT ARRAY['ar'],
      slot_minutes INT NOT NULL DEFAULT 30,
      location TEXT DEFAULT '',
      active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_doctors_clinic 
    ON doctors(clinic_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_doctors_dept 
    ON doctors(department_id);
    """))

    # ---------------- SCHEDULES ----------------
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS doctor_schedules (
      id BIGSERIAL PRIMARY KEY,
      doctor_id BIGINT NOT NULL REFERENCES doctors(doctor_id) ON DELETE CASCADE,
      day_of_week INT NOT NULL,
      start_time TIME NOT NULL,
      end_time TIME NOT NULL,
      active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_sched_doc 
    ON doctor_schedules(doctor_id);
    """))

    # ---------------- APPOINTMENTS ----------------
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS appointments (
      appt_id BIGSERIAL PRIMARY KEY,
      clinic_id TEXT NOT NULL REFERENCES clinics(clinic_id) ON DELETE CASCADE,
      doctor_id BIGINT NOT NULL REFERENCES doctors(doctor_id) ON DELETE CASCADE,
      patient_name TEXT NOT NULL,
      phone TEXT NOT NULL,
      appt_date DATE NOT NULL,
      appt_time TIME NOT NULL,
      status TEXT NOT NULL DEFAULT 'PENDING',
      notes TEXT DEFAULT '',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (doctor_id, appt_date, appt_time)
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_appt_clinic_date 
    ON appointments(clinic_id, appt_date);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_appt_status 
    ON appointments(status);
    """))

    await db.commit()


# ============================================================
# SLOT GENERATION
# ============================================================

def _daterange_slots(start: datetime, end: datetime, slot_minutes: int):
    slots = []
    step = timedelta(minutes=max(5, int(slot_minutes or 30)))
    cur = start

    while cur + step <= end:
        slots.append(cur)
        cur = cur + step

    return slots


# ============================================================
# DOCTOR LIST
# ============================================================

async def list_doctors_by_department(
    db: AsyncSession,
    clinic_id: str,
    department_id: Optional[int]
) -> List[Dict[str, Any]]:

    q = """
    SELECT doctor_id, name, slot_minutes, languages, location
    FROM doctors
    WHERE clinic_id=:clinic_id AND active=TRUE
    """

    params = {"clinic_id": clinic_id}

    if department_id:
        q += " AND department_id=:department_id"
        params["department_id"] = int(department_id)

    q += " ORDER BY name ASC"

    res = await db.execute(text(q), params)
    rows = res.fetchall() or []

    return [
        {
            "doctor_id": int(r[0]),
            "name": r[1],
            "slot_minutes": int(r[2]),
            "languages": list(r[3] or []),
            "location": r[4] or "",
        }
        for r in rows
    ]


# ============================================================
# SLOT LIST
# ============================================================

async def get_doctor_slot_minutes(db: AsyncSession, doctor_id: int) -> int:
    res = await db.execute(
        text("SELECT slot_minutes FROM doctors WHERE doctor_id=:id"),
        {"id": int(doctor_id)},
    )
    row = res.first()
    return int(row[0]) if row and row[0] else 30


async def list_available_slots(
    db: AsyncSession,
    clinic_id: str,
    doctor_id: int,
    appt_date: date,
) -> List[str]:

    dow = appt_date.weekday()

    res = await db.execute(text("""
        SELECT start_time, end_time
        FROM doctor_schedules
        WHERE doctor_id=:doctor_id 
        AND day_of_week=:dow 
        AND active=TRUE
        ORDER BY start_time ASC
    """), {"doctor_id": int(doctor_id), "dow": int(dow)})

    sched_rows = res.fetchall() or []
    if not sched_rows:
        return []

    res2 = await db.execute(text("""
        SELECT appt_time
        FROM appointments
        WHERE doctor_id=:doctor_id 
        AND appt_date=:d 
        AND status IN ('PENDING','CONFIRMED')
    """), {"doctor_id": int(doctor_id), "d": appt_date})

    booked = {str(r[0]) for r in (res2.fetchall() or [])}

    slot_minutes = await get_doctor_slot_minutes(db, doctor_id)

    slots = []

    for (start_t, end_t) in sched_rows:
        start_dt = datetime.combine(appt_date, start_t)
        end_dt = datetime.combine(appt_date, end_t)

        for sdt in _daterange_slots(start_dt, end_dt, slot_minutes):
            t_str = sdt.time().strftime("%H:%M:%S")
            if t_str not in booked:
                slots.append(sdt.time().strftime("%H:%M"))

    return slots


# ============================================================
# CREATE APPOINTMENT
# ============================================================

async def create_pending_appointment(
    db: AsyncSession,
    clinic_id: str,
    doctor_id: int,
    patient_name: str,
    phone: str,
    appt_date: date,
    appt_time_hhmm: str,
    notes: str = "",
) -> int:

    res = await db.execute(text("""
        INSERT INTO appointments 
        (clinic_id, doctor_id, patient_name, phone, appt_date, appt_time, status, notes)
        VALUES (:clinic_id, :doctor_id, :patient_name, :phone, :appt_date, :appt_time::time, 'PENDING', :notes)
        RETURNING appt_id
    """), {
        "clinic_id": clinic_id,
        "doctor_id": int(doctor_id),
        "patient_name": patient_name.strip(),
        "phone": phone.strip(),
        "appt_date": appt_date,
        "appt_time": appt_time_hhmm.strip() + ":00",
        "notes": notes[:500],
    })

    await db.commit()
    row = res.first()
    return int(row[0]) if row else 0


# ============================================================
# RECEPTIONIST UPDATE
# ============================================================

async def receptionist_set_status(
    db: AsyncSession,
    appt_id: int,
    new_status: str,
    new_time_hhmm: Optional[str] = None,
) -> bool:

    new_status = (new_status or "").upper().strip()

    if new_status not in ("CONFIRMED", "REJECTED", "CANCELLED"):
        return False

    if new_time_hhmm:
        res = await db.execute(text("""
            UPDATE appointments
            SET status=:st, appt_time=:t::time, updated_at=NOW()
            WHERE appt_id=:id
            RETURNING appt_id
        """), {
            "st": new_status,
            "t": new_time_hhmm.strip() + ":00",
            "id": int(appt_id)
        })
    else:
        res = await db.execute(text("""
            UPDATE appointments
            SET status=:st, updated_at=NOW()
            WHERE appt_id=:id
            RETURNING appt_id
        """), {
            "st": new_status,
            "id": int(appt_id)
        })

    await db.commit()
    return res.first() is not None