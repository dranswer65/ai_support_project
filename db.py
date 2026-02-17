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
        # SQLAlchemy can't execute multiple statements in one text() on some setups
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))
