from __future__ import annotations

import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Load .env early (Railway variables also come via env)
load_dotenv(override=True)


def _normalize_to_psycopg(raw_url: str) -> str:
    """
    Force SQLAlchemy async engine to use psycopg driver:
      postgres://...                 -> postgresql+psycopg://...
      postgresql://...               -> postgresql+psycopg://...
      postgresql+asyncpg://...       -> postgresql+psycopg://...
      postgresql+psycopg://...       -> keep

    Keep sslmode=require if present (psycopg/libpq understands it).
    """
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Normalize scheme prefix
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    # Convert to psycopg dialect for SQLAlchemy
    if raw_url.startswith("postgresql+psycopg://"):
        pass
    elif raw_url.startswith("postgresql+asyncpg://"):
        raw_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    elif raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        raise RuntimeError("Invalid DATABASE_URL scheme (expected postgresql://...)")

    # Ensure sslmode=require if user asked DB_SSL=require (optional)
    db_ssl = (os.getenv("DB_SSL", "") or "").strip().lower()
    force_ssl = db_ssl in ("1", "true", "require", "required", "yes", "on")

    if force_ssl:
        u = urlparse(raw_url)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        q.setdefault("sslmode", "require")
        raw_url = urlunparse(u._replace(query=urlencode(q)))

    return raw_url


DATABASE_URL = _normalize_to_psycopg(os.getenv("DATABASE_URL", ""))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session