import os
import asyncio

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


async def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")

    engine = create_async_engine(DATABASE_URL)

    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO appointment_requests (
                    tenant_id, request_id, channel, user_id,
                    status, intent,
                    dept_label, doctor_label,
                    appt_date, appt_time,
                    patient_name, patient_mobile,
                    notes, created_at, updated_at
                ) VALUES (
                    'supportpilot_demo', 'REQ-TEST-001', 'whatsapp', '966500000000',
                    'PENDING', 'BOOK',
                    'Dermatology', 'Dr Test',
                    CURRENT_DATE + INTERVAL '1 day', '10:30',
                    'Test Patient', '+966500000000',
                    'manual test', NOW(), NOW()
                )
            """)
        )

    await engine.dispose()
    print("Inserted REQ-TEST-001")


if __name__ == "__main__":
    asyncio.run(main())