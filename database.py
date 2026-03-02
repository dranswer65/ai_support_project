# database.py
from __future__ import annotations

import os
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Load environment variables (.env locally, Railway vars in production)
load_dotenv(override=True)


def _normalize_database_url(raw_url: str) -> str:
    """
    Normalize DATABASE_URL to ALWAYS use psycopg async driver.

    Accepts:
        postgres://
        postgresql://
        postgresql+asyncpg://
        postgresql+psycopg://

    Converts everything to:
        postgresql+psycopg://

    SSL handling:
        DB_SSL=require
        DB_SSL_VERIFY=0  -> sslmode=require
        DB_SSL_VERIFY=1  -> sslmode=verify-full + sslrootcert=system
    """

    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL not set")

    # Normalize scheme
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    if raw_url.startswith("postgresql+psycopg://"):
        url = raw_url
    elif raw_url.startswith("postgresql+asyncpg://"):
        url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    elif raw_url.startswith("postgresql://"):
        url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        raise RuntimeError(
            f"Invalid DATABASE_URL scheme: {raw_url.split(':', 1)[0]}"
        )

    # SSL handling
    db_ssl = (os.getenv("DB_SSL", "") or "").strip().lower()
    db_ssl_verify = (os.getenv("DB_SSL_VERIFY", "") or "").strip().lower()

    want_ssl = db_ssl in ("1", "true", "require", "required", "yes", "on")
    verify_ssl = db_ssl_verify in ("1", "true", "yes", "on")

    if want_ssl:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if verify_ssl:
            # Verify server certificate using system CA store
            query["sslmode"] = "verify-full"
            query["sslrootcert"] = "system"
        else:
            # Encrypted but no cert verification (Railway recommended)
            query["sslmode"] = "require"
            query.pop("sslrootcert", None)

        url = urlunparse(parsed._replace(query=urlencode(query)))

    return url


# Build final DATABASE_URL
DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", ""))


# Create async engine (Railway-safe)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,  # important for serverless / Railway
)


# Session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Dependency (FastAPI)
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session