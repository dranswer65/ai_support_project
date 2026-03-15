from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_audit_logs_table(db: AsyncSession):

    # Create table
    await db.execute(text("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id BIGSERIAL PRIMARY KEY,

        tenant_id TEXT NOT NULL,

        actor_user_id BIGINT,
        actor_email TEXT,
        role_code TEXT,

        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,

        metadata JSONB,

        created_at TIMESTAMP DEFAULT NOW()
    )
    """))

    # Index 1
    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant
    ON audit_logs (tenant_id)
    """))

    # Index 2
    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_audit_logs_action
    ON audit_logs (action)
    """))

    # Index 3
    await db.execute(text("""
    CREATE INDEX IF NOT EXISTS idx_audit_logs_created
    ON audit_logs (created_at DESC)
    """))

    await db.commit()