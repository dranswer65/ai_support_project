# database.py
from __future__ import annotations

import os
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Load .env early (Railway env vars still override)
load_dotenv(override=True)


def _normalize_url(raw_url: str) -> str:
    """
    Normalize DATABASE_URL to ALWAYS use psycopg async driver:
      postgres://...                 -> postgresql+psycopg://...
      postgresql://...               -> postgresql+psycopg://...
      postgresql+asyncpg://...       -> postgresql+psycopg://...
      postgresql+psycopg://...       -> keep

    SSL:
      If DB_SSL=require -> add sslmode=require (default)
      If DB_SSL_VERIFY=1 -> set sslmode=verify-full (may fail on some hosted DBs)
    """
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL not set")

    # accept both postgres:// and postgresql://
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    # convert dialect to psycopg
    if raw_url.startswith("postgresql+psycopg://"):
        url = raw_url
    elif raw_url.startswith("postgresql+asyncpg://"):
        url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    elif raw_url.startswith("postgresql://"):
        url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        raise RuntimeError(f"Invalid DATABASE_URL scheme: {raw_url.split(':', 1)[0]}")

    # SSL flags (optional)
    db_ssl = (os.getenv("DB_SSL", "") or "").strip().lower()
    want_ssl = db_ssl in ("1", "true", "require", "required", "yes", "on")

    db_ssl_verify = (os.getenv("DB_SSL_VERIFY", "") or "").strip().lower()
    verify_ssl = db_ssl_verify in ("1", "true", "yes", "on")

    if want_ssl:
        u = urlparse(url)
        q = dict(parse_qsl(u.query, keep_blank_values=True))

        # psycopg/libpq understands sslmode
        if verify_ssl:
            q["sslmode"] = "verify-full"
        else:
            q["sslmode"] = "require"

        url = urlunparse(u._replace(query=urlencode(q)))

    return url


DATABASE_URL = _normalize_url(os.getenv("DATABASE_URL", ""))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,  # Railway-safe
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session