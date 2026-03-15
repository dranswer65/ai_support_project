from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_appointment_requests_table(db: AsyncSession) -> None:
    # Base table
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS appointment_requests (
            tenant_id TEXT NOT NULL,
            request_id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            intent TEXT NOT NULL,
            dept_key TEXT,
            dept_label TEXT,
            doctor_key TEXT,
            doctor_label TEXT,
            appt_date TEXT,
            appt_time TEXT,
            patient_name TEXT,
            patient_mobile TEXT,
            patient_id TEXT,
            notes TEXT,
            receptionist_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    # Existing indexes
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appt_tenant
        ON appointment_requests (tenant_id);
    """))
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appt_status
        ON appointment_requests (status);
    """))
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appt_created
        ON appointment_requests (created_at DESC);
    """))

    # ---------------------------------------------------------
    # Insurance MVP columns (auto-migration, safe on restart)
    # ---------------------------------------------------------
    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_provider TEXT;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_plan TEXT;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_member_id TEXT;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_policy_number TEXT;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_expiry DATE;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_status TEXT;
    """))

    await db.execute(text("""
        ALTER TABLE appointment_requests
        ADD COLUMN IF NOT EXISTS insurance_verified BOOLEAN;
    """))

    # Backfill nulls safely for old rows
    await db.execute(text("""
        UPDATE appointment_requests
        SET insurance_status = 'SELF_PAY'
        WHERE insurance_status IS NULL;
    """))

    await db.execute(text("""
        UPDATE appointment_requests
        SET insurance_verified = FALSE
        WHERE insurance_verified IS NULL;
    """))

    await db.commit()


# =========================================================
# Reminder Logs (prevents duplicate reminder messages)
# =========================================================

async def ensure_reminder_logs_table(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS reminder_logs (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            appointment_id BIGINT NOT NULL,
            reminder_type TEXT NOT NULL,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'SENT',
            error_text TEXT
        );
    """))

    # Prevent duplicate reminder sends
    await db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_unique
        ON reminder_logs (appointment_id, reminder_type);
    """))

    # Tenant filtering
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_reminder_tenant
        ON reminder_logs (tenant_id);
    """))

    # Dashboard sorting
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_reminder_sent
        ON reminder_logs (sent_at DESC);
    """))

    # Reminder type filter
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_reminder_type
        ON reminder_logs (reminder_type);
    """))

    await db.commit()