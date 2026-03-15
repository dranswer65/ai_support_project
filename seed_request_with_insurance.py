from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

TENANT_ID = "alnoor_clinic"
REQUEST_ID = "REQ-INS-004"
USER_ID = "966500000001"

async def main() -> None:
    if not DATABASE_URL:
        print("DATABASE_URL is not set")
        return

    engine = create_async_engine(DATABASE_URL, future=True)

    async with engine.begin() as conn:
        # Check which columns exist so the script works even if insurance columns
        # were not added yet.
        col_res = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'appointment_requests';
                """
            )
        )
        cols = {row[0] for row in col_res.fetchall()}

        # remove previous test row if exists
        await conn.execute(
            text(
                """
                DELETE FROM appointment_requests
                WHERE tenant_id = :tenant_id
                  AND request_id = :request_id;
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "request_id": REQUEST_ID,
            },
        )

        appt_date = (date.today() + timedelta(days=1)).isoformat()

        base_columns = [
            "tenant_id",
            "request_id",
            "channel",
            "user_id",
            "status",
            "intent",
            "dept_label",
            "doctor_key",
            "doctor_label",
            "appt_date",
            "appt_time",
            "patient_name",
            "patient_mobile",
            "notes",
            "created_at",
            "updated_at",
        ]

        base_values = {
            "tenant_id": TENANT_ID,
            "request_id": REQUEST_ID,
            "channel": "whatsapp",
            "user_id": USER_ID,
            "status": "PENDING",
            "intent": "BOOK",
            "dept_label": "Dermatology",
            "doctor_key": "derma_1",
            "doctor_label": "Dr Huda",
            "appt_date": appt_date,
            "appt_time": "10:30",
            "patient_name": "Test Insurance Patient",
            "patient_mobile": "+966500000001",
            "notes": "manual insurance flow test",
        }

        optional_columns = []
        optional_values = {}

        if "insurance_provider" in cols:
            optional_columns.append("insurance_provider")
            optional_values["insurance_provider"] = "Bupa"

        if "insurance_member_id" in cols:
            optional_columns.append("insurance_member_id")
            optional_values["insurance_member_id"] = "BU12345678"

        if "insurance_status" in cols:
            optional_columns.append("insurance_status")
            optional_values["insurance_status"] = "DECLARED"

        if "insurance_plan" in cols:
            optional_columns.append("insurance_plan")
            optional_values["insurance_plan"] = "Gold"

        if "insurance_class" in cols:
            optional_columns.append("insurance_class")
            optional_values["insurance_class"] = "A"

        all_columns = base_columns + optional_columns

        sql = f"""
            INSERT INTO appointment_requests (
                {", ".join(all_columns)}
            )
            VALUES (
                :tenant_id,
                :request_id,
                :channel,
                :user_id,
                :status,
                :intent,
                :dept_label,
                :doctor_key,
                :doctor_label,
                :appt_date,
                :appt_time,
                :patient_name,
                :patient_mobile,
                :notes,
                NOW(),
                NOW()
                {", " + ", ".join(f":{c}" for c in optional_columns) if optional_columns else ""}
            );
        """

        values = {}
        values.update(base_values)
        values.update(optional_values)

        await conn.execute(text(sql), values)

    await engine.dispose()
    print("Inserted test request:", REQUEST_ID)

if __name__ == "__main__":
    asyncio.run(main())