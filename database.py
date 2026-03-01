# database.py
# Railway + Postgres (asyncpg) — Windows-safe + SSL fix for self-signed chain
#
# Fixes:
# ✅ Forces SQLAlchemy to use asyncpg driver
# ✅ Removes libpq-only params (sslmode, sslrootcert, etc.)
# ✅ Sets asyncpg SSL context to avoid: SSLCertVerificationError (self-signed chain)
# ✅ Windows selector event loop policy (stable)
#
# Notes:
# - For DEV/local: this disables certificate verification (still encrypted).
# - For PROD/verified SSL: see the "PROD SSL" section below.

from __future__ import annotations

import os
import sys
import asyncio
import ssl
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

    Also removes libpq-only params like sslmode/sslrootcert/etc.
    We DO NOT pass sslmode to asyncpg.
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
        if "://" not in raw_url:
            raise RuntimeError("DATABASE_URL looks invalid (missing scheme like postgresql://)")

    u = urlparse(raw_url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))

    # Remove libpq-only params (asyncpg doesn't accept them)
    q.pop("sslmode", None)
    q.pop("sslrootcert", None)
    q.pop("sslcert", None)
    q.pop("sslkey", None)

    normalized = u._replace(query=urlencode(q))
    return urlunparse(normalized), {}


def _make_dev_ssl_context() -> ssl.SSLContext:
    """
    DEV-friendly SSL context:
    - Encrypted TLS
    - No cert verification (fixes self-signed chain errors on Windows)
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ----------------------------
# Database URL
# ----------------------------
DATABASE_URL_RAW = os.getenv("DATABASE_URL", "")
DATABASE_URL, _ = _force_asyncpg_url(DATABASE_URL_RAW)

# ----------------------------
# SSL handling
# ----------------------------
# Optional overrides:
# - DB_SSL=require  -> force SSL (default)
# - DB_SSL=disable  -> no SSL (NOT recommended, may fail on Railway)
# - DB_SSL=verify   -> require verification using DB_SSL_CA_FILE (production)
DB_SSL = (os.getenv("DB_SSL", "require") or "").strip().lower()
DB_SSL_CA_FILE = (os.getenv("DB_SSL_CA_FILE", "") or "").strip()

connect_args: dict = {}

if DB_SSL in ("0", "false", "off", "disable", "disabled", "no"):
    # Not recommended for Railway, but allowed for local non-ssl DBs.
    connect_args = {}
elif DB_SSL in ("verify", "verify-full", "verify_ca", "verify-ca"):
    # PROD SSL (verified):
    # Provide a CA bundle path in DB_SSL_CA_FILE (e.g., railway-ca.pem)
    if not DB_SSL_CA_FILE:
        raise RuntimeError("DB_SSL=verify requires DB_SSL_CA_FILE to point to a CA PEM file")
    ssl_ctx = ssl.create_default_context(cafile=DB_SSL_CA_FILE)
    connect_args["ssl"] = ssl_ctx
else:
    # Default: require SSL but skip verification (DEV-friendly)
    connect_args["ssl"] = _make_dev_ssl_context()

# ----------------------------
# Engine + Session
# ----------------------------
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