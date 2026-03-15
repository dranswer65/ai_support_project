"""add wa_message_dedupe table

Revision ID: 993d5b14f077
Revises: 7619ef735326
Create Date: 2026-02-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "993d5b14f077"
down_revision = "7619ef735326"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS wa_message_dedupe (
        message_id TEXT PRIMARY KEY,
        wa_id TEXT,
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wa_message_dedupe;")



