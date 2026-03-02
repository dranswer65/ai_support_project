# reset_session.py
from __future__ import annotations

import os
import sys
import ssl
import asyncio
from dotenv import load_dotenv
load_dotenv()

import certifi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool


# ----------------------------
# Windows async fix (MUST be first for scripts too)
# ----------------------------
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


TENANT_ID = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip() or "supportpilot_demo"


def _build_asyncpg_url() -> str:
    """
    Accept DATABASE_URL like:
      postgresql://user:pass@host:port/db?sslmode=require
    Convert to:
      postgresql+asyncpg://...
    And REMOVE sslmode (asyncpg doesn't support sslmode keyword).
    """
    raw = (os.getenv("DATABASE_URL", "") or "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL not set")

    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)

    if raw.startswith("postgresql+asyncpg://"):
        url = raw
    elif raw.startswith("postgresql://"):
        url = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        raise RuntimeError(f"Invalid DATABASE_URL scheme: {raw[:40]}")

    # strip sslmode if present
    if "sslmode=" in url:
        # quick safe remove of sslmode param (handles &sslmode=require or ?sslmode=require)
        parts = url.split("?", 1)
        if len(parts) == 2:
            base, qs = parts
            qs2 = "&".join([p for p in qs.split("&") if not p.lower().startswith("sslmode=")])
            url = base if not qs2 else base + "?" + qs2

    return url


def _ssl_context() -> ssl.SSLContext | None:
    """
    If DB_SSL=require => enable SSL.
    If DB_SSL_VERIFY=1 => verify using certifi.
    If DB_SSL_VERIFY=0 => no verification (dev only).
    """
    db_ssl = (os.getenv("DB_SSL", "") or "").strip().lower()
    if db_ssl not in ("1", "true", "require", "yes", "on"):
        return None

    verify = (os.getenv("DB_SSL_VERIFY", "1") or "").strip().lower() in ("1", "true", "yes", "on")

    if verify:
        return ssl.create_default_context(cafile=certifi.where())

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def main() -> None:
    url = _build_asyncpg_url()
    ssl_ctx = _ssl_context()

    # IMPORTANT: statement_timeout/connect timeout
    # asyncpg respects server_settings + timeout in connect_args via SQLAlchemy.
    engine = create_async_engine(
        url,
        echo=False,
        poolclass=NullPool,
        connect_args={
            "timeout": 10,  # seconds (prevents hanging forever)
            **({"ssl": ssl_ctx} if ssl_ctx else {}),
        },
    )

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionLocal() as db:
            await db.execute(
                text("DELETE FROM sessions WHERE tenant_id = :tenant_id"),
                {"tenant_id": TENANT_ID},
            )
            await db.commit()
        print(f"✅ sessions cleared for tenant={TENANT_ID}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())