from __future__ import annotations

import os
import sys
import asyncio
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Load .env early
load_dotenv(override=True)

# Windows fix: ensure Selector policy (helps many libs)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _force_asyncpg_url(raw_url: str) -> tuple[str, dict]:
    """
    Ensures SQLAlchemy async engine uses asyncpg:
      postgres://...                 -> postgresql+asyncpg://...
      postgresql://...               -> postgresql+asyncpg://...
      postgresql+psycopg://...       -> postgresql+asyncpg://...
      postgresql+asyncpg://...       -> keep

    Also removes libpq-only params like sslmode and translates sslmode=require -> connect_args['ssl']='require'
    """
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set. Put it in .env or set $env:DATABASE_URL")

    # Normalize scheme to asyncpg
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql+psycopg://"):
        raw_url = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql+asyncpg://"):
        pass
    else:
        # If it's some other scheme, leave it, but it likely won't work.
        # Better to fail loudly:
        if "://" not in raw_url:
            raise RuntimeError("DATABASE_URL looks invalid (missing scheme like postgresql://)")

    u = urlparse(raw_url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))

    connect_args: dict = {}

    # asyncpg does NOT accept sslmode, translate it
    sslmode = (q.pop("sslmode", "") or "").strip().lower()
    if sslmode in ("require", "required", "verify-ca", "verify-full"):
        connect_args["ssl"] = "require"

    # Remove other libpq-only params if present
    q.pop("sslrootcert", None)
    q.pop("sslcert", None)
    q.pop("sslkey", None)

    normalized = u._replace(query=urlencode(q))
    return urlunparse(normalized), connect_args


DATABASE_URL_RAW = os.getenv("DATABASE_URL", "")
DATABASE_URL, connect_args_from_url = _force_asyncpg_url(DATABASE_URL_RAW)

# Optional override: DB_SSL=require (forces SSL even if url had no sslmode)
DB_SSL = (os.getenv("DB_SSL", "") or "").strip().lower()
connect_args = dict(connect_args_from_url)
if DB_SSL in ("1", "true", "require", "required", "yes", "on"):
    connect_args["ssl"] = "require"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args=connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session