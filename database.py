import os
import ssl
from dotenv import load_dotenv

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

load_dotenv()  # IMPORTANT: ensure .env is loaded

def _normalize_db_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url

def _ssl_context_from_env():
    mode = (os.getenv("DB_SSL") or "").strip().lower()
    verify = (os.getenv("DB_SSL_VERIFY") or "").strip().lower()

    if mode in ("require", "true", "1", "yes"):
        if verify in ("0", "false", "no"):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return ssl.create_default_context()

    return None

DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))

# ✅ THIS is the block you asked about
connect_args = {}
ssl_ctx = _ssl_context_from_env()
if ssl_ctx is not None:
    connect_args["ssl"] = ssl_ctx

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)