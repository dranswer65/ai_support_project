# core/session_store_pg.py
# Tenant-aware Postgres session store (async SQLAlchemy)
# Fixes: PostgresSyntaxError near ":" by using CAST(:param AS JSONB)

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "sessions"


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or "default").strip()
    return t or "default"


async def ensure_sessions_table(db: AsyncSession) -> None:
    await db.execute(
        text(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            tenant_id   TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            session_json JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, user_id)
        );
        """)
    )
    await db.commit()


async def get_session(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    tenant = _norm_tenant(tenant_id)

    res = await db.execute(
        text(f"""
        SELECT session_json
        FROM {TABLE_NAME}
        WHERE tenant_id = :tenant_id AND user_id = :user_id
        LIMIT 1;
        """),
        {"tenant_id": tenant, "user_id": user_id},
    )
    row = res.first()
    if not row:
        return None

    val = row[0]
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        return None


async def upsert_session(
    db: AsyncSession,
    *,
    user_id: str,
    session: Dict[str, Any],
    tenant_id: Optional[str] = None,
) -> None:
    tenant = _norm_tenant(tenant_id)
    session_json = json.dumps(session, ensure_ascii=False)

    # IMPORTANT:
    # Do NOT do ":session_json::jsonb" because SQLAlchemy will treat it as a bindparam name and Postgres errors near ":"
    # Use CAST(:session_json AS JSONB)
    await db.execute(
        text(f"""
        INSERT INTO {TABLE_NAME} (tenant_id, user_id, session_json)
        VALUES (:tenant_id, :user_id, CAST(:session_json AS JSONB))
        ON CONFLICT (tenant_id, user_id)
        DO UPDATE SET
            session_json = CAST(:session_json AS JSONB),
            updated_at = NOW();
        """),
        {"tenant_id": tenant, "user_id": user_id, "session_json": session_json},
    )
    await db.commit()