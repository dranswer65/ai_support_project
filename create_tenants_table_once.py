import os
from sqlalchemy import create_engine, text

# Get DB from environment
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

SQL = """
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
"""

with engine.begin() as conn:
    conn.execute(text(SQL))

print("✅ tenants table created successfully")