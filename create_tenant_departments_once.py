import os
from sqlalchemy import create_engine, text

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

SQL = """
-- A) tenants
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id            TEXT PRIMARY KEY,
  name_en              TEXT NOT NULL,
  name_ar              TEXT NOT NULL,
  is_active            BOOLEAN NOT NULL DEFAULT TRUE,

  default_language     TEXT NOT NULL DEFAULT 'ar',
  supported_languages  JSONB NOT NULL DEFAULT '["ar","en"]'::jsonb,

  timezone             TEXT NOT NULL DEFAULT 'Asia/Riyadh',
  location_text_en     TEXT DEFAULT '',
  location_text_ar     TEXT DEFAULT '',
  google_maps_url      TEXT DEFAULT '',

  slot_duration_min    INT NOT NULL DEFAULT 30,

  working_hours        JSONB NOT NULL DEFAULT '{}'::jsonb,
  insurance_list       JSONB NOT NULL DEFAULT '[]'::jsonb,

  wa_phone_number_id   TEXT DEFAULT '',
  wa_verify_token      TEXT DEFAULT '',

  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(is_active);

-- B) tenant_departments
CREATE TABLE IF NOT EXISTS tenant_departments (
  tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  dept_key      TEXT NOT NULL,
  name_en       TEXT NOT NULL,
  name_ar       TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order    INT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, dept_key)
);

CREATE INDEX IF NOT EXISTS idx_depts_tenant_active
  ON tenant_departments(tenant_id, is_active);

-- C) tenant_doctors
CREATE TABLE IF NOT EXISTS tenant_doctors (
  tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  doctor_key    TEXT NOT NULL,
  dept_key      TEXT NOT NULL,
  name_en       TEXT NOT NULL,
  name_ar       TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order    INT NOT NULL DEFAULT 0,

  gender        TEXT DEFAULT '',
  title_en      TEXT DEFAULT '',
  title_ar      TEXT DEFAULT '',

  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, doctor_key)
);

CREATE INDEX IF NOT EXISTS idx_doctors_tenant_dept
  ON tenant_doctors(tenant_id, dept_key);

CREATE INDEX IF NOT EXISTS idx_doctors_tenant_active
  ON tenant_doctors(tenant_id, is_active);
"""

with engine.begin() as conn:
    conn.execute(text(SQL))

print("✅ Created/verified: tenants, tenant_departments, tenant_doctors")