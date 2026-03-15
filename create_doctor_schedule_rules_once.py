import os
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

TENANT_ID = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()

async def main():
    async with AsyncSessionLocal() as db:
        # Example: Dr Ahmed Monday 10:00-14:00 and 17:00-21:00, 15-min slots
        await db.execute(text("""
            INSERT INTO doctor_schedule_rules
              (tenant_id, doctor_key, day_of_week, start_time, end_time, slot_minutes, is_active)
            VALUES
              (:t,'dr_ahmed',0,'10:00','14:00',15,TRUE),
              (:t,'dr_ahmed',0,'17:00','21:00',15,TRUE)
            ON CONFLICT DO NOTHING;
        """), {"t": TENANT_ID})
        await db.commit()

    print("Inserted schedule rules ✅")

if __name__ == "__main__":
    asyncio.run(main())