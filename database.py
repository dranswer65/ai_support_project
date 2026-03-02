from __future__ import annotations

import os
import ssl
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import certifi
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# ✅ do NOT override PowerShell env vars
load_dotenv(override=False)


def _normalize_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set")

    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql://", 1)

    if raw_url.startswith("postgresql+asyncpg://"):
        url2 = raw_url
    elif raw_url.startswith("postgresql+psycopg://"):
        url2 = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://"):
        url2 = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        raise RuntimeError(f"Invalid DATABASE_URL scheme: {raw_url[:40]}")

    # asyncpg cannot accept sslmode in connect args -> remove it
    u = urlparse(url2)
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    q.pop("sslmode", None)
    url2 = urlunparse(u._replace(query=urlencode(q)))

    return url2


def _build_ssl_context():
    db_ssl = (os.getenv("DB_SSL", "") or "").strip().lower()
    if db_ssl not in ("1", "true", "require", "yes", "on"):
        return None

    verify = (os.getenv("DB_SSL_VERIFY", "1") or "").strip().lower() in ("1", "true", "yes", "on")

    ctx = ssl.create_default_context(cafile=certifi.where())
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    return ctx


DATABASE_URL = _normalize_url(os.getenv("DATABASE_URL", ""))
SSL_CTX = _build_ssl_context()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={"ssl": SSL_CTX} if SSL_CTX else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)