import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS wa_sessions;"))

    conn.execute(text("""
        CREATE TABLE wa_sessions (
          tenant_id   TEXT NOT NULL,
          user_id     TEXT NOT NULL,
          session     JSONB NOT NULL,
          version     BIGINT NOT NULL DEFAULT 1,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (tenant_id, user_id)
        );
    """))

    conn.execute(text("""
        CREATE INDEX idx_wa_sessions_updated_at
        ON wa_sessions(updated_at);
    """))

    conn.execute(text("""
        CREATE INDEX idx_wa_sessions_tenant
        ON wa_sessions(tenant_id);
    """))

print("✅ wa_sessions rebuilt correctly (tenant-aware + JSONB)")