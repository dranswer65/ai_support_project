from __future__ import annotations

import os
import sys
import asyncio
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Load .env early (important for uvicorn import order)
load_dotenv(override=True)

# Windows fix: use Selector loop (asyncpg is fine, but keep it stable)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Put it in .env or set $env:DATABASE_URL")

# Optional SSL control for asyncpg
# DB_SSL=require  -> uses TLS without certificate validation errors
DB_SSL = os.getenv("DB_SSL", "").strip().lower()

connect_args = {}
if DB_SSL in ("1", "true", "require", "required", "yes", "on"):
    # asyncpg expects ssl as string 'require' or an SSLContext
    connect_args["ssl"] = "require"

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,   # good for serverless / Railway proxies
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




