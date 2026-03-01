from __future__ import annotations

import os
import ssl
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Load .env early
load_dotenv(override=True)


def _force_asyncpg_url(raw_url: str) -> tuple[str, dict]:
    """
    Ensures SQLAlchemy async engine uses asyncpg:
      postgres://...                 -> postgresql+asyncpg://...
      postgresql://...               -> postgresql+asyncpg://...
      postgresql+psycopg://...       -> postgresql+asyncpg://...
      postgresql+asyncpg://...       -> keep

    Removes libpq-only params like sslmode/sslrootcert/etc.
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

    # Keep sslmode as a hint, but remove from query (asyncpg doesn't accept sslmode)
    sslmode_hint = (q.pop("sslmode", "") or "").strip().lower()

    # Remove other libpq-only params
    q.pop("sslrootcert", None)
    q.pop("sslcert", None)
    q.pop("sslkey", None)

    normalized = u._replace(query=urlencode(q))
    return urlunparse(normalized), {"sslmode_hint": sslmode_hint}


def _env_flag(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on", "require", "required")


def _env_off(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on", "off", "disable", "disabled", "0", "false", "no")


def _build_ssl_context() -> ssl.SSLContext | None:
    """
    Railway proxy requires TLS. On Windows dev you can disable verification safely (still encrypted)
    by setting:
      DB_SSL_VERIFY=0

    Controls:
      DB_SSL=require   -> force TLS
      DB_SSL=off       -> no TLS (NOT recommended for Railway)
      DB_SSL_VERIFY=1  -> verify cert chain (secure)
      DB_SSL_VERIFY=0  -> do NOT verify (fixes Railway proxy self-signed chain on Windows)
    """
    db_ssl = (os.getenv("DB_SSL", "require") or "").strip().lower()
    verify_raw = (os.getenv("DB_SSL_VERIFY", "1") or "").strip().lower()

    # Allow turning TLS off explicitly (not recommended for Railway)
    if db_ssl in ("0", "false", "no", "off", "disable", "disabled"):
        return None

    verify_ssl = verify_raw not in ("0", "false", "no", "off")

    if verify_ssl:
        # Verify using system trust store (or certifi if you install it)
        ctx = ssl.create_default_context()
        try:
            import certifi  # pip install certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    # ✅ DEV FIX: encrypted but unverified (prevents CERTIFICATE_VERIFY_FAILED)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


DATABASE_URL_RAW = os.getenv("DATABASE_URL", "")
DATABASE_URL, hints = _force_asyncpg_url(DATABASE_URL_RAW)

sslmode_hint = (hints.get("sslmode_hint") or "").strip().lower()

# Build SSL context
ssl_ctx = _build_ssl_context()

connect_args: dict = {}
if ssl_ctx is not None:
    connect_args["ssl"] = ssl_ctx
else:
    # If ssl_ctx is None, we only proceed without ssl if user explicitly disabled it.
    # Railway usually needs SSL, so leaving ssl None may fail if remote requires TLS.
    pass

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