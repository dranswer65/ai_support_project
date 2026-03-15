import os, asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    print("DATABASE_URL is not set")
    raise SystemExit(1)

async def main():
    eng = create_async_engine(DATABASE_URL, future=True)
    async with eng.connect() as conn:
        res = await conn.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name='tenants'
            ORDER BY ordinal_position;
        """))
        rows = res.all()
        for r in rows:
            print(r[0], "-", r[1])
    await eng.dispose()

asyncio.run(main())