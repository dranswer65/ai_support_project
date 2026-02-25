# core/wa_dedupe_store_pg.py
# Tenant-aware WhatsApp webhook dedupe store (async SQLAlchemy)
# One row per (tenant_id, msg_id) to prevent Meta retry loops.

from __future__ import annotations

from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "wa_processed_messages"


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or "default").strip()
    return t or "default"


async def ensure_wa_dedupe_table(db: AsyncSession) -> None:
    """
    Ensures the dedupe table exists and is tenant-aware.

    IMPORTANT:
    - If you previously created wa_processed_messages without tenant_id,
      this will auto-migrate by adding tenant_id and recreating the primary key.
    """
    # 1) Create table (fresh installs)
    await db.execute(
        text(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            tenant_id TEXT NOT NULL,
            msg_id TEXT NOT NULL,
            wa_from TEXT,
            phone_number_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, msg_id)
        );
        """)
    )

    # 2) Migrate older table versions that missed tenant_id / wrong PK
    # Add tenant_id if missing
    await db.execute(
        text(f"""
        ALTER TABLE {TABLE_NAME}
        ADD COLUMN IF NOT EXISTS tenant_id TEXT;
        """)
    )

    # Backfill tenant_id if null (old rows)
    await db.execute(
        text(f"""
        UPDATE {TABLE_NAME}
        SET tenant_id = 'default'
        WHERE tenant_id IS NULL;
        """)
    )

    # Enforce NOT NULL on tenant_id (safe after backfill)
    await db.execute(
        text(f"""
        ALTER TABLE {TABLE_NAME}
        ALTER COLUMN tenant_id SET NOT NULL;
        """)
    )

    # Drop any existing primary key constraint (name varies; try common patterns)
    # We can safely attempt and ignore failures using DO blocks.
    await db.execute(
        text(f"""
        DO $$
        BEGIN
            -- drop PK if exists (unknown name)
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = '{TABLE_NAME}'::regclass
                  AND contype = 'p'
            ) THEN
                EXECUTE (
                    SELECT 'ALTER TABLE {TABLE_NAME} DROP CONSTRAINT ' || quote_ident(conname)
                    FROM pg_constraint
                    WHERE conrelid = '{TABLE_NAME}'::regclass
                      AND contype = 'p'
                    LIMIT 1
                );
            END IF;
        END $$;
        """)
    )

    # Recreate correct composite primary key
    await db.execute(
        text(f"""
        ALTER TABLE {TABLE_NAME}
        ADD PRIMARY KEY (tenant_id, msg_id);
        """)
    )

    # Helpful index for ops/debugging (optional)
    await db.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_created_at ON {TABLE_NAME}(created_at);"))

    await db.commit()


async def claim_message_once(
    db: AsyncSession,
    *,
    tenant_id: str,
    msg_id: str,
    wa_from: str | None = None,
    phone_number_id: str | None = None,
) -> bool:
    """
    True  => first time (process + reply)
    False => duplicate (ignore)
    """
    tenant = _norm_tenant(tenant_id)

    if not msg_id:
        return True

    res = await db.execute(
        text(f"""
        INSERT INTO {TABLE_NAME} (tenant_id, msg_id, wa_from, phone_number_id)
        VALUES (:tenant_id, :msg_id, :wa_from, :phone_number_id)
        ON CONFLICT (tenant_id, msg_id) DO NOTHING
        RETURNING msg_id;
        """),
        {
            "tenant_id": tenant,
            "msg_id": msg_id,
            "wa_from": wa_from,
            "phone_number_id": phone_number_id,
        },
    )
    await db.commit()
    return res.first() is not None