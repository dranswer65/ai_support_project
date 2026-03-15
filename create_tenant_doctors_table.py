import os
from sqlalchemy import create_engine, text

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_engine(DATABASE_URL)

SQL = """
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

print("✅ tenant_doctors table created/verified")