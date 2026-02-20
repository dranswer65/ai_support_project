import os
from sqlalchemy import create_engine, text


def get_db_url() -> str:
    # Railway provides DATABASE_URL.
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        # local fallback if you use sqlite for dev
        url = os.getenv("SP_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    # Railway sometimes gives postgres:// but SQLAlchemy prefers postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


ENGINE = create_engine(
    get_db_url(),
    pool_pre_ping=True,
    future=True,
)


# -----------------------------------------------------------
# SupportPilot core tables
# -----------------------------------------------------------
def create_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS conversations (
      id SERIAL PRIMARY KEY,
      client_name TEXT NOT NULL,
      channel TEXT NOT NULL DEFAULT 'whatsapp',
      user_id TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open',
      topic TEXT DEFAULT '',
      last_intent TEXT DEFAULT '',
      missing_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
      last_user_message TEXT DEFAULT '',
      last_bot_message TEXT DEFAULT '',
      turns INT NOT NULL DEFAULT 0,
      updated_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      created_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (client_name, channel, user_id)
    );

    CREATE TABLE IF NOT EXISTS conversation_messages (
      id SERIAL PRIMARY KEY,
      conversation_id INT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      text TEXT NOT NULL,
      created_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_utc DESC);
    CREATE INDEX IF NOT EXISTS idx_msg_conv_id ON conversation_messages(conversation_id, created_utc DESC);
    """
    with ENGINE.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))


# -----------------------------------------------------------
# WhatsApp SaaS tables (NEW)
# -----------------------------------------------------------
def create_wa_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS wa_conversations (
      wa_id TEXT PRIMARY KEY,
      state TEXT NOT NULL DEFAULT 'NEW',
      order_id TEXT DEFAULT '',
      order_attempts INT NOT NULL DEFAULT 0,
      last_intent TEXT DEFAULT '',
      last_user_msg TEXT DEFAULT '',
      updated_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS wa_messages (
      id BIGSERIAL PRIMARY KEY,
      wa_id TEXT NOT NULL,
      direction TEXT NOT NULL,
      body TEXT NOT NULL,
      ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_wa_messages_wa_id_ts
    ON wa_messages (wa_id, ts_utc DESC);
    """

    with ENGINE.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))


# -----------------------------------------------------------
# WhatsApp inbound dedupe + state machine sessions (YOU NEED THESE)
# -----------------------------------------------------------
def create_wa_inbound_dedupe_table():
    """
    Prevents duplicate replies when Meta retries the same webhook message.
    """
    sql = """
    CREATE TABLE IF NOT EXISTS wa_inbound_dedupe (
      msg_id TEXT PRIMARY KEY,
      wa_id  TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_wa_inbound_dedupe_created_at
    ON wa_inbound_dedupe (created_at DESC);
    """
    with ENGINE.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))


def create_conversation_sessions_table():
    """
    Persists whatsapp_controller.py session state (so you don't lose state on restart).
    """
    sql = """
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

    CREATE INDEX IF NOT EXISTS idx_conversation_sessions_updated_at
    ON conversation_sessions (updated_at DESC);
    """
    with ENGINE.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))


def create_all_tables():
    """
    One function to call on startup.
    Safe to run on every deploy.
    """
    create_tables()
    create_wa_tables()
    create_wa_inbound_dedupe_table()
    create_conversation_sessions_table()