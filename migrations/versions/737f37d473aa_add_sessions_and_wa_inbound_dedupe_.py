"""add sessions and wa inbound dedupe tables

Revision ID: 737f37d473aa
Revises: 17204194f197
Create Date: 2026-02-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "737f37d473aa"
down_revision = "17204194f197"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS wa_inbound_dedupe (
      msg_id TEXT PRIMARY KEY,
      wa_id  TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    op.execute("""
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
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS conversation_sessions;")
    op.execute("DROP TABLE IF EXISTS wa_inbound_dedupe;")