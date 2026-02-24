# core/wa_dedupe_store_pg.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "wa_processed_messages"

DEFAULT_TENANT_ID = "supportpilot_demo"


async def ensure_wa_dedupe_table(db: AsyncSession) -> None:
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
    await db.execute(
        text(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_created_at ON {TABLE_NAME} (created_at DESC);")
    )
    await db.commit()


async def claim_message_once(
    db: AsyncSession,
    msg_id: str,
    wa_from: str | None = None,
    phone_number_id: str | None = None,
    tenant_id: str | None = None,
) -> bool:
    """
    True  => first time (process + reply)
    False => duplicate (ignore)

    Backwards compatible:
    - You can call claim_message_once(db, msg_id=..., wa_from=...)
    - Or tenant-safe: claim_message_once(db, tenant_id=..., msg_id=...)
    """
    if not msg_id:
        return True

    tid = (tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID

    res = await db.execute(
        text(f"""
        INSERT INTO {TABLE_NAME} (tenant_id, msg_id, wa_from, phone_number_id)
        VALUES (:tenant_id, :msg_id, :wa_from, :phone_number_id)
        ON CONFLICT (tenant_id, msg_id) DO NOTHING
        RETURNING msg_id;
        """),
        {
            "tenant_id": tid,
            "msg_id": msg_id,
            "wa_from": wa_from,
            "phone_number_id": phone_number_id,
        },
    )
    await db.commit()
    return res.first() is not None