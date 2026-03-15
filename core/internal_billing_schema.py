from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# =========================================================
# A) billing_accounts
# One financial container per visit / appointment / encounter
# =========================================================
async def ensure_billing_accounts_table(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS billing_accounts (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        patient_id TEXT,
        appointment_id TEXT,
        encounter_type TEXT NOT NULL DEFAULT 'OPD',
        source_module TEXT NOT NULL DEFAULT 'APPOINTMENTS',
        source_id TEXT,
        status TEXT NOT NULL DEFAULT 'OPEN',

        currency TEXT NOT NULL DEFAULT 'SAR',

        subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
        discount_total NUMERIC(12,2) NOT NULL DEFAULT 0,
        tax_total NUMERIC(12,2) NOT NULL DEFAULT 0,
        insurance_total NUMERIC(12,2) NOT NULL DEFAULT 0,
        patient_due_total NUMERIC(12,2) NOT NULL DEFAULT 0,
        paid_total NUMERIC(12,2) NOT NULL DEFAULT 0,
        balance_due NUMERIC(12,2) NOT NULL DEFAULT 0,

        notes TEXT,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_accounts_tenant
    ON billing_accounts (tenant_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_accounts_patient
    ON billing_accounts (tenant_id, patient_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_accounts_appointment
    ON billing_accounts (tenant_id, appointment_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_accounts_status
    ON billing_accounts (tenant_id, status);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_accounts_created
    ON billing_accounts (tenant_id, created_at DESC);
    """))

    await db.commit()


# =========================================================
# B) billing_items
# Charge lines inside one billing account
# =========================================================
async def ensure_billing_items_table(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS billing_items (
        id BIGSERIAL PRIMARY KEY,
        billing_account_id BIGINT NOT NULL REFERENCES billing_accounts(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,

        department_code TEXT NOT NULL DEFAULT 'OPD',
        item_type TEXT NOT NULL DEFAULT 'SERVICE',
        item_code TEXT NOT NULL,
        item_name TEXT NOT NULL,

        quantity NUMERIC(12,2) NOT NULL DEFAULT 1,
        unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,

        gross_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        tax_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        insurance_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        patient_amount NUMERIC(12,2) NOT NULL DEFAULT 0,

        status TEXT NOT NULL DEFAULT 'ACTIVE',
        service_date TIMESTAMPTZ,
        notes TEXT,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_items_account
    ON billing_items (billing_account_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_items_tenant
    ON billing_items (tenant_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_items_dept
    ON billing_items (tenant_id, department_code);
    """))

    await db.commit()


# =========================================================
# C) billing_payments
# Actual payments collected against billing account
# =========================================================
async def ensure_billing_payments_table(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS billing_payments (
        id BIGSERIAL PRIMARY KEY,
        billing_account_id BIGINT NOT NULL REFERENCES billing_accounts(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,

        amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'SAR',
        payment_method TEXT NOT NULL DEFAULT 'CASH',
        payment_reference TEXT,
        payment_status TEXT NOT NULL DEFAULT 'PAID',
        collected_by TEXT,
        paid_at TIMESTAMPTZ,
        notes TEXT,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_payments_account
    ON billing_payments (billing_account_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_payments_tenant
    ON billing_payments (tenant_id);
    """))

    await db.commit()


# =========================================================
# D) billing_invoices
# Financial document linked to billing account
# =========================================================
async def ensure_billing_invoices_table(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS billing_invoices (
        id BIGSERIAL PRIMARY KEY,
        billing_account_id BIGINT NOT NULL REFERENCES billing_accounts(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,

        invoice_number TEXT NOT NULL,
        invoice_status TEXT NOT NULL DEFAULT 'ISSUED',
        issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        due_at TIMESTAMPTZ,
        total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'SAR',
        pdf_url TEXT,
        notes TEXT,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        UNIQUE (tenant_id, invoice_number)
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_invoices_account
    ON billing_invoices (billing_account_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant
    ON billing_invoices (tenant_id);
    """))

    await db.commit()


# =========================================================
# E) billing_catalog
# Master catalog of billable services/items
# =========================================================
async def ensure_billing_catalog_table(db: AsyncSession):
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS billing_catalog (
        id BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL,

        department_code TEXT NOT NULL DEFAULT 'OPD',
        item_code TEXT NOT NULL,
        item_name TEXT NOT NULL,
        item_type TEXT NOT NULL DEFAULT 'SERVICE',

        base_price NUMERIC(12,2) NOT NULL DEFAULT 0,
        tax_rate NUMERIC(8,4) NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        notes TEXT,
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        UNIQUE (tenant_id, department_code, item_code)
    );
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_catalog_tenant
    ON billing_catalog (tenant_id);
    """))

    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_billing_catalog_dept
    ON billing_catalog (tenant_id, department_code);
    """))

    await db.commit()


# =========================================================
# F) appointments lightweight linkage (future-safe summary only)
# =========================================================
async def ensure_appointments_billing_columns(db: AsyncSession):
    await db.execute(text("""
    ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS billing_account_id BIGINT;
    """))

    await db.execute(text("""
    ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS billing_status TEXT;
    """))

    await db.execute(text("""
    ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS patient_due_total NUMERIC(12,2);
    """))

    await db.execute(text("""
    ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS paid_total NUMERIC(12,2);
    """))

    await db.execute(text("""
    UPDATE appointments
    SET
        billing_status = COALESCE(NULLIF(billing_status, ''), 'UNBILLED'),
        patient_due_total = COALESCE(patient_due_total, 0),
        paid_total = COALESCE(paid_total, 0)
    WHERE TRUE;
    """))

    await db.commit()


# =========================================================
# G) all internal billing schema
# =========================================================
async def ensure_internal_billing_tables(db: AsyncSession):
    await ensure_billing_accounts_table(db)
    await ensure_billing_items_table(db)
    await ensure_billing_payments_table(db)
    await ensure_billing_invoices_table(db)
    await ensure_billing_catalog_table(db)
    await ensure_appointments_billing_columns(db)