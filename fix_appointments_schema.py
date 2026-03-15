from __future__ import annotations
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:

        print("Adding appt_date if missing...")
        await db.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS appt_date TEXT;
        """))

        print("Adding appt_time if missing...")
        await db.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS appt_time TEXT;
        """))

        print("Adding request_id if missing...")
        await db.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS request_id TEXT;
        """))

        print("Adding notes if missing...")
        await db.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS notes TEXT;
        """))

        print("Adding insurance_expiry if missing...")
        await db.execute(text("""
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS insurance_expiry DATE;
        """))

        await db.commit()
        print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(main())
