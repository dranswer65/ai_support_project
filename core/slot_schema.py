from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _column_exists(db: AsyncSession, table_name: str, column_name: str) -> bool:
    res = await db.execute(
        text("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :t
              AND column_name = :c
            LIMIT 1;
        """),
        {"t": table_name, "c": column_name},
    )
    return res.first() is not None


async def _add_col_if_missing(db: AsyncSession, table: str, col: str, ddl: str) -> None:
    if not await _column_exists(db, table, col):
        await db.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl};"))


async def ensure_slot_tables(db: AsyncSession) -> None:
    """
    Production + migration-safe slot system tables.

    IMPORTANT:
    - appointment_slots is the source of slot availability
    - appointments is the final confirmed booking record
    - compatible with:
        * old slot_date / slot_time style
        * current appt_date / appt_time style
    - supports both:
        * WhatsApp AI booking
        * Reception direct booking
    """

    # =========================================================
    # doctors
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS doctors (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            doctor_key TEXT NOT NULL,
            doctor_name TEXT NOT NULL,
            specialty_key TEXT,
            specialty_label TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, doctor_key)
        );
    """))

    # =========================================================
    # doctor_schedule_rules
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS doctor_schedule_rules (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            doctor_key TEXT NOT NULL,
            day_of_week INT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            slot_minutes INT NOT NULL DEFAULT 15,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    await db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_rule
        ON doctor_schedule_rules (tenant_id, doctor_key, day_of_week, start_time, end_time);
    """))

    # =========================================================
    # doctor_time_off
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS doctor_time_off (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            doctor_key TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_timeoff_range
        ON doctor_time_off (tenant_id, doctor_key, starts_at, ends_at);
    """))

    # =========================================================
    # appointment_slots
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS appointment_slots (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            doctor_key TEXT NOT NULL,
            slot_date DATE NOT NULL,
            slot_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            appointment_id TEXT,
            hold_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    await _add_col_if_missing(db, "appointment_slots", "appointment_id", "appointment_id TEXT")
    await _add_col_if_missing(db, "appointment_slots", "hold_id", "hold_id TEXT")

    await db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_slot
        ON appointment_slots (tenant_id, doctor_key, slot_date, slot_time);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointment_slots_status
        ON appointment_slots (tenant_id, doctor_key, slot_date, status);
    """))

    # =========================================================
    # slot_holds
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS slot_holds (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT,
            hold_id TEXT,
            doctor_key TEXT,
            slot_date DATE,
            slot_time TEXT,
            user_id TEXT,
            status TEXT,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    await _add_col_if_missing(db, "slot_holds", "tenant_id", "tenant_id TEXT")
    await _add_col_if_missing(db, "slot_holds", "hold_id", "hold_id TEXT")
    await _add_col_if_missing(db, "slot_holds", "doctor_key", "doctor_key TEXT")
    await _add_col_if_missing(db, "slot_holds", "slot_date", "slot_date DATE")
    await _add_col_if_missing(db, "slot_holds", "slot_time", "slot_time TEXT")
    await _add_col_if_missing(db, "slot_holds", "user_id", "user_id TEXT")
    await _add_col_if_missing(db, "slot_holds", "status", "status TEXT NOT NULL DEFAULT 'HELD'")
    await _add_col_if_missing(db, "slot_holds", "expires_at", "expires_at TIMESTAMPTZ")

    await db.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname='public' AND indexname='uq_hold'
            ) THEN
                CREATE UNIQUE INDEX uq_hold
                ON slot_holds (tenant_id, hold_id);
            END IF;
        END $$;
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_hold_expires
        ON slot_holds (tenant_id, status, expires_at);
    """))

    await db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_hold_per_slot
        ON slot_holds (tenant_id, doctor_key, slot_date, slot_time)
        WHERE status = 'HELD';
    """))

    # =========================================================
    # appointments
    # =========================================================
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS appointments (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT,
            appointment_id TEXT,
            hold_id TEXT,
            doctor_key TEXT,
            slot_date DATE,
            slot_time TEXT,
            appt_date TEXT,
            appt_time TEXT,
            user_id TEXT,

            patient_name TEXT,
            patient_mobile TEXT,
            patient_id TEXT,
            patient_type TEXT,
            patient_national_id TEXT,
            patient_address TEXT,
            patient_age INT,

            notes TEXT,
            status TEXT,
            booking_source TEXT NOT NULL DEFAULT 'WHATSAPP_AI',

            insurance_provider TEXT,
            insurance_plan TEXT,
            insurance_member_id TEXT,
            insurance_policy_number TEXT,
            insurance_expiry DATE,
            insurance_status TEXT,
            insurance_verified BOOLEAN,

            request_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    # -----------------------
    # core compatibility cols
    # -----------------------
    await _add_col_if_missing(db, "appointments", "tenant_id", "tenant_id TEXT")
    await _add_col_if_missing(db, "appointments", "appointment_id", "appointment_id TEXT")
    await _add_col_if_missing(db, "appointments", "hold_id", "hold_id TEXT")
    await _add_col_if_missing(db, "appointments", "doctor_key", "doctor_key TEXT")
    await _add_col_if_missing(db, "appointments", "slot_date", "slot_date DATE")
    await _add_col_if_missing(db, "appointments", "slot_time", "slot_time TEXT")
    await _add_col_if_missing(db, "appointments", "appt_date", "appt_date TEXT")
    await _add_col_if_missing(db, "appointments", "appt_time", "appt_time TEXT")
    await _add_col_if_missing(db, "appointments", "user_id", "user_id TEXT")

    # -----------------------
    # patient intake fields
    # -----------------------
    await _add_col_if_missing(db, "appointments", "patient_name", "patient_name TEXT")
    await _add_col_if_missing(db, "appointments", "patient_mobile", "patient_mobile TEXT")
    await _add_col_if_missing(db, "appointments", "patient_id", "patient_id TEXT")
    await _add_col_if_missing(db, "appointments", "patient_type", "patient_type TEXT")
    await _add_col_if_missing(db, "appointments", "patient_national_id", "patient_national_id TEXT")
    await _add_col_if_missing(db, "appointments", "patient_address", "patient_address TEXT")
    await _add_col_if_missing(db, "appointments", "patient_age", "patient_age INT")

    # -----------------------
    # booking meta
    # -----------------------
    await _add_col_if_missing(db, "appointments", "notes", "notes TEXT")
    await _add_col_if_missing(db, "appointments", "status", "status TEXT NOT NULL DEFAULT 'CONFIRMED'")
    await _add_col_if_missing(
        db,
        "appointments",
        "booking_source",
        "booking_source TEXT NOT NULL DEFAULT 'WHATSAPP_AI'"
    )
    await _add_col_if_missing(db, "appointments", "request_id", "request_id TEXT")

    # -----------------------
    # insurance fields
    # -----------------------
    await _add_col_if_missing(db, "appointments", "insurance_provider", "insurance_provider TEXT")
    await _add_col_if_missing(db, "appointments", "insurance_plan", "insurance_plan TEXT")
    await _add_col_if_missing(db, "appointments", "insurance_member_id", "insurance_member_id TEXT")
    await _add_col_if_missing(db, "appointments", "insurance_policy_number", "insurance_policy_number TEXT")
    await _add_col_if_missing(db, "appointments", "insurance_expiry", "insurance_expiry DATE")
    await _add_col_if_missing(db, "appointments", "insurance_status", "insurance_status TEXT")
    await _add_col_if_missing(
        db,
        "appointments",
        "insurance_verified",
        "insurance_verified BOOLEAN NOT NULL DEFAULT FALSE"
    )

    # =========================================================
    # backfills / normalization
    # =========================================================
    await db.execute(text("""
        UPDATE appointments
        SET booking_source = 'WHATSAPP_AI'
        WHERE booking_source IS NULL OR booking_source = '';
    """))

    await db.execute(text("""
        UPDATE appointments
        SET insurance_status = 'SELF_PAY'
        WHERE insurance_status IS NULL OR insurance_status = '';
    """))

    await db.execute(text("""
        UPDATE appointments
        SET insurance_verified = FALSE
        WHERE insurance_verified IS NULL;
    """))

    # appt_date/appt_time from legacy slot columns
    await db.execute(text("""
        UPDATE appointments
        SET appt_date = COALESCE(appt_date, slot_date::text)
        WHERE appt_date IS NULL
          AND slot_date IS NOT NULL;
    """))

    await db.execute(text("""
        UPDATE appointments
        SET appt_time = COALESCE(appt_time, slot_time)
        WHERE appt_time IS NULL
          AND slot_time IS NOT NULL;
    """))

    # legacy slot columns from current appt columns
    await db.execute(text("""
        UPDATE appointments
        SET slot_date = CAST(appt_date AS DATE)
        WHERE slot_date IS NULL
          AND appt_date IS NOT NULL
          AND appt_date ~ '^\d{4}-\d{2}-\d{2}$';
    """))

    await db.execute(text("""
        UPDATE appointments
        SET slot_time = appt_time
        WHERE slot_time IS NULL
          AND appt_time IS NOT NULL;
    """))

    # =========================================================
    # indexes
    # =========================================================
    await db.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname='public' AND indexname='uq_appointment'
            ) THEN
                CREATE UNIQUE INDEX uq_appointment
                ON appointments (tenant_id, appointment_id);
            END IF;
        END $$;
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_slot
        ON appointments (tenant_id, doctor_key, slot_date, slot_time);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_appt
        ON appointments (tenant_id, doctor_key, appt_date, appt_time);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_booking_source
        ON appointments (tenant_id, booking_source);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_patient_mobile
        ON appointments (tenant_id, patient_mobile);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_patient_name
        ON appointments (tenant_id, patient_name);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_patient_national_id
        ON appointments (tenant_id, patient_national_id);
    """))

    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appointments_request_id
        ON appointments (tenant_id, request_id);
    """))

    await db.commit()