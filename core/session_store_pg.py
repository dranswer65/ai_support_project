# core/session_store_pg.py
# Tenant-aware Postgres-backed session store (async SQLAlchemy)
# One row per (tenant_id, user_id). Session stored as JSONB.

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "wa_sessions"


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or "default").strip()
    return t or "default"


async def ensure_sessions_table(db: AsyncSession) -> None:
    await db.execute(
        text(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            tenant_id  TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            session    JSONB NOT NULL,
            version    BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, user_id)
        );
        """)
    )
    await db.execute(
        text(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_at ON {TABLE_NAME}(updated_at);")
    )
    await db.commit()


async def get_session(db: AsyncSession, *, user_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    tenant = _norm_tenant(tenant_id)
    res = await db.execute(
        text(f"SELECT session FROM {TABLE_NAME} WHERE tenant_id = :tenant_id AND user_id = :user_id"),
        {"tenant_id": tenant, "user_id": user_id},
    )
    row = res.first()
    if not row:
        return None

    sess = row[0]
    if isinstance(sess, dict):
        return sess

    try:
        return json.loads(sess)
    except Exception:
        return None


async def get_session_with_version(
    db: AsyncSession, *, user_id: str, tenant_id: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    tenant = _norm_tenant(tenant_id)
    res = await db.execute(
        text(f"SELECT session, version FROM {TABLE_NAME} WHERE tenant_id = :tenant_id AND user_id = :user_id"),
        {"tenant_id": tenant, "user_id": user_id},
    )
    row = res.first()
    if not row:
        return None, None

    sess = row[0]
    ver = row[1]

    if not isinstance(sess, dict):
        try:
            sess = json.loads(sess)
        except Exception:
            sess = None

    return sess, int(ver) if ver is not None else None


async def upsert_session(
    db: AsyncSession,
    *,
    user_id: str,
    session: Dict[str, Any],
    tenant_id: Optional[str] = None,
) -> None:
    tenant = _norm_tenant(tenant_id)
    await db.execute(
        text(f"""
        INSERT INTO {TABLE_NAME} (tenant_id, user_id, session)
        VALUES (:tenant_id, :user_id, :session::jsonb)
        ON CONFLICT (tenant_id, user_id)
        DO UPDATE SET
            session = EXCLUDED.session,
            version = {TABLE_NAME}.version + 1,
            updated_at = NOW();
        """),
        {"tenant_id": tenant, "user_id": user_id, "session": json.dumps(session, ensure_ascii=False)},
    )
    await db.commit()


async def load_or_create_session(
    db: AsyncSession,
    *,
    user_id: str,
    default_session: Dict[str, Any],
    tenant_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    tenant = _norm_tenant(tenant_id)

    sess, ver = await get_session_with_version(db, user_id=user_id, tenant_id=tenant)
    if sess is not None and ver is not None:
        return sess, ver

    await db.execute(
        text(f"""
        INSERT INTO {TABLE_NAME} (tenant_id, user_id, session)
        VALUES (:tenant_id, :user_id, :session::jsonb)
        ON CONFLICT (tenant_id, user_id) DO NOTHING;
        """),
        {"tenant_id": tenant, "user_id": user_id, "session": json.dumps(default_session, ensure_ascii=False)},
    )
    await db.commit()

    sess2, ver2 = await get_session_with_version(db, user_id=user_id, tenant_id=tenant)
    return (sess2 or default_session), int(ver2 or 1)


async def cleanup_old_sessions(db: AsyncSession, *, tenant_id: Optional[str] = None, older_than_days: int = 30) -> int:
    tenant = _norm_tenant(tenant_id)
    res = await db.execute(
        text(f"""
        DELETE FROM {TABLE_NAME}
        WHERE tenant_id = :tenant_id
          AND updated_at < NOW() - (:days || ' days')::interval
        RETURNING user_id;
        """),
        {"tenant_id": tenant, "days": int(older_than_days)},
    )
    await db.commit()
    rows = res.fetchall() or []
    return len(rows)