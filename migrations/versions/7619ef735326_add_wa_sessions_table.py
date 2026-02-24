from alembic import op

# revision identifiers
revision = "7619ef735326"
down_revision = "737f37d473aa"

branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS wa_sessions (
        user_id TEXT PRIMARY KEY,
        session JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS wa_sessions;")



