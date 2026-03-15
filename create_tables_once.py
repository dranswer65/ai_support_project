import os
from sqlalchemy import create_engine, text

DATABASE_URL = (os.getenv("DATABASE_URL", "") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_engine(DATABASE_URL)

SQLS = [
    """
    CREATE TABLE IF NOT EXISTS wa_inbound_dedupe (
      msg_id TEXT PRIMARY KEY,
      wa_id  TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
      user_id TEXT PRIMARY KEY,
      channel TEXT NOT NULL DEFAULT 'whatsapp',

      state TEXT NOT NULL DEFAULT 'ACTIVE',
      conversation_version INT NOT NULL DEFAULT 1,

      language TEXT,
      text_direction TEXT NOT NULL DEFAULT 'ltr',

      order_id TEXT,
      issue_summary TEXT NOT NULL DEFAULT '',
      asked_order_id_count INT NOT NULL DEFAULT 0,
      no_count INT NOT NULL DEFAULT 0,
      tries INT NOT NULL DEFAULT 0,
      ai_attempts INT NOT NULL DEFAULT 0,

      last_intent TEXT,
      last_user_message TEXT,
      last_bot_message TEXT,
      last_user_ts TIMESTAMPTZ,
      last_bot_ts TIMESTAMPTZ,

      no_reply_ping_sent BOOLEAN NOT NULL DEFAULT FALSE,
      last_closed_at TIMESTAMPTZ,

      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
]

with engine.begin() as conn:
    for s in SQLS:
        conn.execute(text(s))

print("✅ Tables created/verified: wa_inbound_dedupe, conversation_sessions")