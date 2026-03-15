import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def main():
    engine = create_async_engine(DATABASE_URL)

    async with engine.begin() as conn:

        await conn.execute(text("""
        ALTER TABLE appointment_requests
        ADD CONSTRAINT appointment_requests_unique
        UNIQUE (tenant_id, request_id);
        """))

        print("✅ Unique constraint added: (tenant_id, request_id)")

    await engine.dispose()

asyncio.run(main())