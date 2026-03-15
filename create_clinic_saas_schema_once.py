import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        print("✅ Creating clinic SaaS schema...")

        # =========================
        # tenants
        # =========================
        print("1️⃣ tenants...")
        await db.execute(text("""
        CREATE TABLE IF NOT EXISTS tenants (
          tenant_id        TEXT PRIMARY KEY,
          name_en          TEXT NOT NULL,
          name_ar          TEXT NOT NULL,
          default_language TEXT NOT NULL DEFAULT 'ar',
          timezone         TEXT NOT NULL DEFAULT 'Asia/Riyadh',
          is_active        BOOLEAN NOT NULL DEFAULT TRUE,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))

        # =========================
        # tenant_departments
        # =========================
        print("2️⃣ tenant_departments...")
        await db.execute(text("""
        CREATE TABLE IF NOT EXISTS tenant_departments (
          tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          dept_key     TEXT NOT NULL,
          name_en      TEXT NOT NULL,
          name_ar      TEXT NOT NULL,
          is_active    BOOLEAN NOT NULL DEFAULT TRUE,
          sort_order   INT NOT NULL DEFAULT 0,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (tenant_id, dept_key)
        );
        """))

        await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_tenant_departments_active
          ON tenant_departments(tenant_id, is_active, sort_order);
        """))

        # =========================
        # tenant_doctors
        # =========================
        print("3️⃣ tenant_doctors...")
        await db.execute(text("""
        CREATE TABLE IF NOT EXISTS tenant_doctors (
          tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          doctor_key   TEXT NOT NULL,
          dept_key     TEXT NOT NULL,
          name_en      TEXT NOT NULL,
          name_ar      TEXT NOT NULL,
          is_active    BOOLEAN NOT NULL DEFAULT TRUE,
          sort_order   INT NOT NULL DEFAULT 0,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (tenant_id, doctor_key),
          FOREIGN KEY (tenant_id, dept_key)
            REFERENCES tenant_departments(tenant_id, dept_key)
            ON DELETE RESTRICT
        );
        """))

        await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_tenant_doctors_dept_active
          ON tenant_doctors(tenant_id, dept_key, is_active, sort_order);
        """))

        # =========================
        # appointment_requests
        # =========================
        print("4️⃣ appointment_requests...")
        await db.execute(text("""
        CREATE TABLE IF NOT EXISTS appointment_requests (
          id              BIGSERIAL PRIMARY KEY,
          tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          user_id         TEXT NOT NULL,
          channel         TEXT NOT NULL DEFAULT 'whatsapp',

          kind            TEXT NOT NULL,
          status          TEXT NOT NULL DEFAULT 'new',

          dept_key        TEXT NULL,
          dept_label      TEXT NULL,
          doctor_key      TEXT NULL,
          doctor_label    TEXT NULL,

          appt_ref        TEXT NULL,
          requested_date  TEXT NULL,
          requested_slot  TEXT NULL,

          patient_name    TEXT NULL,
          patient_mobile  TEXT NULL,
          patient_id      TEXT NULL,

          payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """))

        await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appt_requests_tenant_status
          ON appointment_requests(tenant_id, status, created_at DESC);
        """))

        await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_appt_requests_user
          ON appointment_requests(tenant_id, user_id, created_at DESC);
        """))

        await db.commit()

    print("\n✅ DONE: tenants, tenant_departments, tenant_doctors, appointment_requests are ready.")

asyncio.run(main())