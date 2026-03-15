from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# =========================================================
# A) doctor_schedule_rules
# Weekly schedule for doctors
# =========================================================
async def ensure_doctor_schedule_rules_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS doctor_schedule_rules (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                doctor_key TEXT NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                slot_minutes INTEGER DEFAULT 15,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
    )
    await db.commit()


# =========================================================
# B) doctor_time_off
# Doctor vacation / holidays
# =========================================================
async def ensure_doctor_time_off_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS doctor_time_off (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                doctor_key TEXT NOT NULL,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                reason TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
    )
    await db.commit()


# =========================================================
# C) appointment_slots
# Generated slots per doctor
# =========================================================
async def ensure_appointment_slots_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS appointment_slots (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                doctor_key TEXT NOT NULL,
                slot_date DATE NOT NULL,
                slot_time TEXT NOT NULL,
                status TEXT DEFAULT 'OPEN',
                hold_id TEXT,
                appointment_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (tenant_id, doctor_key, slot_date, slot_time)
            );
            """
        )
    )
    await db.commit()


# =========================================================
# D) slot_holds
# Temporary reservation
# =========================================================
async def ensure_slot_holds_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS slot_holds (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                hold_id TEXT NOT NULL,
                doctor_key TEXT NOT NULL,
                slot_id BIGINT NOT NULL,
                user_id TEXT,
                request_id TEXT,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (tenant_id, hold_id),
                UNIQUE (tenant_id, slot_id)
            );
            """
        )
    )
    await db.commit()


# =========================================================
# E) appointments
# Final confirmed appointments
# =========================================================
async def ensure_appointments_table(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                appointment_id TEXT NOT NULL,
                doctor_key TEXT NOT NULL,
                appt_date TEXT,
                appt_time TEXT,
                patient_name TEXT,
                patient_mobile TEXT,
                patient_id TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                request_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (tenant_id, doctor_key, appt_date, appt_time)
            );
            """
        )
    )

    # ---------------------------------------------------------
    # Safe auto-migrations for older DBs / newer code paths
    # ---------------------------------------------------------
    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS hold_id TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS user_id TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS notes TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS booking_source TEXT;
            """
        )
    )

    # ---------------------------------------------------------
    # Insurance MVP columns
    # ---------------------------------------------------------
    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_provider TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_plan TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_member_id TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_policy_number TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_expiry DATE;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_status TEXT;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS insurance_verified BOOLEAN;
            """
        )
    )

    # ---------------------------------------------------------
    # Backfill defaults for existing rows
    # ---------------------------------------------------------
    await db.execute(
        text(
            """
            UPDATE appointments
            SET insurance_status = 'SELF_PAY'
            WHERE insurance_status IS NULL;
            """
        )
    )

    await db.execute(
        text(
            """
            UPDATE appointments
            SET insurance_verified = FALSE
            WHERE insurance_verified IS NULL;
            """
        )
    )

    await db.execute(
        text(
            """
            UPDATE appointments
            SET booking_source = 'WHATSAPP_AI'
            WHERE booking_source IS NULL
              AND request_id IS NOT NULL;
            """
        )
    )

    await db.commit()