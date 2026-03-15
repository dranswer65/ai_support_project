import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS wa_processed_messages;"))

    conn.execute(text("""
        CREATE TABLE wa_processed_messages (
          tenant_id        TEXT NOT NULL,
          msg_id           TEXT NOT NULL,
          wa_from          TEXT,
          phone_number_id  TEXT,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (tenant_id, msg_id)
        );
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_wa_processed_messages_created_at
        ON wa_processed_messages(created_at);
    """))

print("✅ wa_processed_messages rebuilt correctly (tenant_id + composite PK)")