import os
from sqlalchemy import create_engine, text

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_engine(DATABASE_URL)

SQL = """
CREATE TABLE IF NOT EXISTS appointment_requests (
  tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  request_id       TEXT PRIMARY KEY,
  channel          TEXT NOT NULL DEFAULT 'whatsapp',
  user_id          TEXT NOT NULL,

  status           TEXT NOT NULL DEFAULT 'PENDING',
  -- PENDING | CONFIRMED | REJECTED | CANCELLED | RESCHEDULED | CLOSED

  intent           TEXT NOT NULL,

  dept_key         TEXT,
  dept_label       TEXT,
  doctor_key       TEXT,
  doctor_label     TEXT,
  appt_date        TEXT,
  appt_time        TEXT,

  patient_name     TEXT,
  patient_mobile   TEXT,
  patient_id       TEXT,

  notes            TEXT DEFAULT '',
  receptionist_note TEXT DEFAULT '',

  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apptreq_tenant_status 
ON appointment_requests(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_apptreq_tenant_user 
ON appointment_requests(tenant_id, user_id);
"""

with engine.begin() as conn:
    conn.execute(text(SQL))

print("✅ appointment_requests table created/verified")