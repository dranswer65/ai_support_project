# core/session_store_pg.py
# Postgres-backed session store (async SQLAlchemy)
# Stores session dict as JSONB in one row per (tenant_id, user_id).
# Adds: tenant support + versioning + safe upsert + optional cleanup.

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TABLE_NAME = "wa_sessions"


async def ensure_sessions_table(db: AsyncSession) -> None:
    """
    Ensures tenant-aware schema.

    If you previously had:
      wa_sessions(user_id PRIMARY KEY, session JSONB, ...)
    this function will:
      - add tenant_id column (default 'default')
      - convert PK to (tenant_id, user_id)
    """
    # 1) Create if missing (already tenant-aware)
    await db.execute(
        text(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            tenant_id  TEXT NOT NULL DEFAULT 'default',
            user_id    TEXT NOT NULL,
            session    JSONB NOT NULL,
            version    BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, user_id)
        );
        """)
    )
    await db.commit()

    # 2) If table existed with old schema, migrate safely (idempotent)
    #    - add tenant_id if missing
    #    - drop old PK on user_id if exists
    #    - add new PK (tenant_id, user_id)
    await db.execute(
        text(f"""
        DO $$
        DECLARE
            pk_name text;
            has_tenant boolean;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = '{TABLE_NAME}'
                  AND column_name = 'tenant_id'
            ) INTO has_tenant;

            IF NOT has_tenant THEN
                ALTER TABLE {TABLE_NAME} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
            END IF;

            -- find primary key constraint name
            SELECT conname INTO pk_name
            FROM pg_constraint
            WHERE conrelid = '{TABLE_NAME}'::regclass
              AND contype = 'p'
            LIMIT 1;

            -- If PK exists but isn't (tenant_id, user_id), replace it
            IF pk_name IS NOT NULL THEN
                -- Drop it (safe even if it's already the correct one; we'll re-add)
                EXECUTE format('ALTER TABLE {TABLE_NAME} DROP CONSTRAINT %I', pk_name);
            END IF;

            -- Re-add desired PK
            BEGIN
                ALTER TABLE {TABLE_NAME} ADD PRIMARY KEY (tenant_id, user_id);
            EXCEPTION
                WHEN duplicate_object THEN
                    -- already has correct PK
                    NULL;
            END;
        END $$;
        """)
    )
    await db.commit()

    # Index for ops/cleanup
    await db.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_updated_at ON {TABLE_NAME}(updated_at DESC);"))
    await db.commit()


def _norm_tenant(tenant_id: Optional[str]) -> str:
    t = (tenant_id or "default").strip()
    return t or "default"


async def get_session(db: AsyncSession, user_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
    db: AsyncSession,
    user_id: str,
    tenant_id: Optional[str] = None,
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


async def upsert_session_expected_version(
    db: AsyncSession,
    user_id: str,
    session: Dict[str, Any],
    expected_version: int,
    tenant_id: Optional[str] = None,
) -> bool:
    tenant = _norm_tenant(tenant_id)
    res = await db.execute(
        text(f"""
        UPDATE {TABLE_NAME}
        SET session = :session::jsonb,
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = :tenant_id
          AND user_id = :user_id
          AND version = :expected_version
        RETURNING version;
        """),
        {
            "tenant_id": tenant,
            "user_id": user_id,
            "expected_version": int(expected_version),
            "session": json.dumps(session, ensure_ascii=False),
        },
    )
    await db.commit()
    return res.first() is not None


async def load_or_create_session(
    db: AsyncSession,
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


async def cleanup_old_sessions(
    db: AsyncSession,
    older_than_days: int = 30,
    tenant_id: Optional[str] = None,
) -> int:
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