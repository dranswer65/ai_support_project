from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy import text
from database import AsyncSessionLocal

TENANT_ID = "alnoor_clinic"
DOCTOR_KEY = "derma_1"
DEPT_LABEL = "Dermatology"
DOCTOR_LABEL = "Dr Huda"

PATIENT_NAME = "PS Test Insurance Patient"
PATIENT_MOBILE = "+966500000099"
USER_ID = PATIENT_MOBILE

INSURANCE_PROVIDER = "Bupa"
INSURANCE_PLAN = "Gold"
INSURANCE_MEMBER_ID = "BU99887766"
INSURANCE_POLICY_NUMBER = None
INSURANCE_EXPIRY = date(2028, 5, 4)
INSURANCE_STATUS = "SELF_PAY"
INSURANCE_VERIFIED = False


async def main():
    async with AsyncSessionLocal() as db:
        # 1) find first OPEN slot for this doctor
        res = await db.execute(
            text(
                """
                SELECT slot_date, slot_time
                FROM appointment_slots
                WHERE tenant_id = :tenant_id
                  AND doctor_key = :doctor_key
                  AND status = 'OPEN'
                ORDER BY slot_date ASC, slot_time ASC
                LIMIT 1;
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "doctor_key": DOCTOR_KEY,
            },
        )
        slot = res.mappings().first()

        if not slot:
            print("NO_OPEN_SLOT_FOUND")
            return

        request_id = f"REQ-PS-{uuid.uuid4().hex[:8].upper()}"
        appt_date = str(slot["slot_date"])
        appt_time = str(slot["slot_time"])[:5]

        await db.execute(
            text(
                """
                INSERT INTO appointment_requests (
                    tenant_id,
                    request_id,
                    channel,
                    user_id,
                    status,
                    intent,
                    dept_key,
                    dept_label,
                    doctor_key,
                    doctor_label,
                    appt_date,
                    appt_time,
                    patient_name,
                    patient_mobile,
                    patient_id,
                    notes,
                    receptionist_note,
                    insurance_provider,
                    insurance_plan,
                    insurance_member_id,
                    insurance_policy_number,
                    insurance_expiry,
                    insurance_status,
                    insurance_verified,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :request_id,
                    'whatsapp',
                    :user_id,
                    'PENDING',
                    'BOOK',
                    :dept_key,
                    :dept_label,
                    :doctor_key,
                    :doctor_label,
                    :appt_date,
                    :appt_time,
                    :patient_name,
                    :patient_mobile,
                    NULL,
                    :notes,
                    '',
                    :insurance_provider,
                    :insurance_plan,
                    :insurance_member_id,
                    :insurance_policy_number,
                    :insurance_expiry,
                    :insurance_status,
                    :insurance_verified,
                    NOW(),
                    NOW()
                );
                """
            ),
            {
                "tenant_id": TENANT_ID,
                "request_id": request_id,
                "user_id": USER_ID,
                "dept_key": "dermatology",
                "dept_label": DEPT_LABEL,
                "doctor_key": DOCTOR_KEY,
                "doctor_label": DOCTOR_LABEL,
                "appt_date": appt_date,
                "appt_time": appt_time,
                "patient_name": PATIENT_NAME,
                "patient_mobile": PATIENT_MOBILE,
                "notes": "PowerShell test request for reception insurance flow",
                "insurance_provider": INSURANCE_PROVIDER,
                "insurance_plan": INSURANCE_PLAN,
                "insurance_member_id": INSURANCE_MEMBER_ID,
                "insurance_policy_number": INSURANCE_POLICY_NUMBER,
                "insurance_expiry": INSURANCE_EXPIRY,
                "insurance_status": INSURANCE_STATUS,
                "insurance_verified": INSURANCE_VERIFIED,
            },
        )

        await db.commit()

        print("CREATED_OK")
        print(f"request_id={request_id}")
        print(f"appt_date={appt_date}")
        print(f"appt_time={appt_time}")
        print(f"doctor_key={DOCTOR_KEY}")


if __name__ == "__main__":
    asyncio.run(main())
