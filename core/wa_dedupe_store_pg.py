# core/wa_dedupe_store_pg.py
from __future__ import annotations

from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "wa_processed_messages"


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or "default").strip()
    return t or "default"


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