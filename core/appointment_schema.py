# core/appointment_schema.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_appointment_requests_table(db: AsyncSession) -> None:
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

    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_appt_tenant ON appointment_requests (tenant_id);"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_appt_status ON appointment_requests (status);"))
    await db.execute(text("CREATE INDEX IF NOT EXISTS idx_appt_created ON appointment_requests (created_at DESC);"))
    await db.commit()