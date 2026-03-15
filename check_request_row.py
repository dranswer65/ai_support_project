from __future__ import annotations
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

REQUEST_ID = "REQ-PS-2CF10702"
TENANT_ID = "alnoor_clinic"

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            text("""
                SELECT
                    tenant_id,
                    request_id,
                    LENGTH(request_id) AS request_id_len,
                    status,
                    patient_name,
                    appt_date,
                    appt_time
                FROM appointment_requests
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
                LIMIT 20;
            """),
            {"tenant_id": TENANT_ID},
        )
        rows = res.mappings().all()
        for r in rows:
            print(dict(r))

        print("----- EXACT MATCH -----")

        res2 = await db.execute(
            text("""
                SELECT
                    tenant_id,
                    request_id,
                    LENGTH(request_id) AS request_id_len,
                    status,
                    patient_name,
                    appt_date,
                    appt_time
                FROM appointment_requests
                WHERE tenant_id = :tenant_id
                  AND request_id = :request_id
                LIMIT 1;
            """),
            {"tenant_id": TENANT_ID, "request_id": REQUEST_ID},
        )
        row = res2.mappings().first()
        print(dict(row) if row else "NOT_FOUND")

if __name__ == "__main__":
    asyncio.run(main())
