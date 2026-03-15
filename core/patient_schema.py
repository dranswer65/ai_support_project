from __future__ import annotations

import uuid
from typing import Optional, Dict, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _make_patient_id() -> str:
    return f"PAT-{uuid.uuid4().hex[:12].upper()}"


async def ensure_patient_tables(db: AsyncSession) -> None:
    """
    Patient registry foundation.

    This table is intentionally lightweight for now:
    - supports reception/front desk
    - supports future EMR/LIS/pharmacy expansion
    - supports search by phone / national id / patient id
    """

    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                patient_id TEXT NOT NULL,
                full_name TEXT NOT NULL,
                mobile TEXT,
                national_id TEXT,
                date_of_birth DATE,
                gender TEXT,
                address TEXT,

                insurance_provider TEXT,
                insurance_plan TEXT,
                insurance_member_id TEXT,
                insurance_policy_number TEXT,
                insurance_expiry DATE,
                insurance_status TEXT NOT NULL DEFAULT 'SELF_PAY',
                insurance_verified BOOLEAN NOT NULL DEFAULT FALSE,

                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                UNIQUE (tenant_id, patient_id)
            );
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_patients_tenant_mobile
            ON patients (tenant_id, mobile);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_patients_tenant_national_id
            ON patients (tenant_id, national_id);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_patients_tenant_name
            ON patients (tenant_id, full_name);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_patients_tenant_created
            ON patients (tenant_id, created_at DESC);
            """
        )
    )

    await db.commit()


async def find_existing_patient(
    db: AsyncSession,
    *,
    tenant_id: str,
    patient_id: Optional[str] = None,
    mobile: Optional[str] = None,
    national_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Matching priority:
    1) patient_id
    2) national_id
    3) mobile
    """

    patient_id = (patient_id or "").strip()
    mobile = (mobile or "").strip()
    national_id = (national_id or "").strip()

    if patient_id:
        res = await db.execute(
            text(
                """
                SELECT *
                FROM patients
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
            },
        )
        row = res.mappings().first()
        if row:
            return dict(row)

    if national_id:
        res = await db.execute(
            text(
                """
                SELECT *
                FROM patients
                WHERE tenant_id = :tenant_id
                  AND national_id = :national_id
                ORDER BY updated_at DESC
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "national_id": national_id,
            },
        )
        row = res.mappings().first()
        if row:
            return dict(row)

    if mobile:
        res = await db.execute(
            text(
                """
                SELECT *
                FROM patients
                WHERE tenant_id = :tenant_id
                  AND mobile = :mobile
                ORDER BY updated_at DESC
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "mobile": mobile,
            },
        )
        row = res.mappings().first()
        if row:
            return dict(row)

    return None


async def upsert_patient_registry(
    db: AsyncSession,
    *,
    tenant_id: str,
    full_name: str,
    mobile: Optional[str] = None,
    national_id: Optional[str] = None,
    date_of_birth=None,
    gender: Optional[str] = None,
    address: Optional[str] = None,
    insurance_provider: Optional[str] = None,
    insurance_plan: Optional[str] = None,
    insurance_member_id: Optional[str] = None,
    insurance_policy_number: Optional[str] = None,
    insurance_expiry=None,
    insurance_status: str = "SELF_PAY",
    insurance_verified: bool = False,
    patient_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If patient exists, update latest profile data.
    Else create a new patient record.
    """

    existing = await find_existing_patient(
        db,
        tenant_id=tenant_id,
        patient_id=patient_id,
        mobile=mobile,
        national_id=national_id,
    )

    if existing:
        registry_patient_id = str(existing["patient_id"])

        await db.execute(
            text(
                """
                UPDATE patients
                SET
                    full_name = COALESCE(:full_name, full_name),
                    mobile = COALESCE(:mobile, mobile),
                    national_id = COALESCE(:national_id, national_id),
                    date_of_birth = COALESCE(:date_of_birth, date_of_birth),
                    gender = COALESCE(:gender, gender),
                    address = COALESCE(:address, address),
                    insurance_provider = COALESCE(:insurance_provider, insurance_provider),
                    insurance_plan = COALESCE(:insurance_plan, insurance_plan),
                    insurance_member_id = COALESCE(:insurance_member_id, insurance_member_id),
                    insurance_policy_number = COALESCE(:insurance_policy_number, insurance_policy_number),
                    insurance_expiry = COALESCE(:insurance_expiry, insurance_expiry),
                    insurance_status = COALESCE(:insurance_status, insurance_status),
                    insurance_verified = COALESCE(:insurance_verified, insurance_verified),
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id;
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": registry_patient_id,
                "full_name": full_name or None,
                "mobile": mobile or None,
                "national_id": national_id or None,
                "date_of_birth": date_of_birth,
                "gender": gender or None,
                "address": address or None,
                "insurance_provider": insurance_provider,
                "insurance_plan": insurance_plan,
                "insurance_member_id": insurance_member_id,
                "insurance_policy_number": insurance_policy_number,
                "insurance_expiry": insurance_expiry,
                "insurance_status": insurance_status or "SELF_PAY",
                "insurance_verified": bool(insurance_verified),
            },
        )

        res = await db.execute(
            text(
                """
                SELECT *
                FROM patients
                WHERE tenant_id = :tenant_id
                  AND patient_id = :patient_id
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "patient_id": registry_patient_id,
            },
        )
        row = res.mappings().first()
        return {
            "created": False,
            "patient": dict(row) if row else existing,
        }

    registry_patient_id = patient_id or _make_patient_id()

    await db.execute(
        text(
            """
            INSERT INTO patients
            (
                tenant_id,
                patient_id,
                full_name,
                mobile,
                national_id,
                date_of_birth,
                gender,
                address,
                insurance_provider,
                insurance_plan,
                insurance_member_id,
                insurance_policy_number,
                insurance_expiry,
                insurance_status,
                insurance_verified,
                is_active,
                created_at,
                updated_at
            )
            VALUES
            (
                :tenant_id,
                :patient_id,
                :full_name,
                :mobile,
                :national_id,
                :date_of_birth,
                :gender,
                :address,
                :insurance_provider,
                :insurance_plan,
                :insurance_member_id,
                :insurance_policy_number,
                :insurance_expiry,
                :insurance_status,
                :insurance_verified,
                TRUE,
                NOW(),
                NOW()
            );
            """
        ),
        {
            "tenant_id": tenant_id,
            "patient_id": registry_patient_id,
            "full_name": full_name,
            "mobile": mobile or None,
            "national_id": national_id or None,
            "date_of_birth": date_of_birth,
            "gender": gender or None,
            "address": address or None,
            "insurance_provider": insurance_provider,
            "insurance_plan": insurance_plan,
            "insurance_member_id": insurance_member_id,
            "insurance_policy_number": insurance_policy_number,
            "insurance_expiry": insurance_expiry,
            "insurance_status": insurance_status or "SELF_PAY",
            "insurance_verified": bool(insurance_verified),
        },
    )

    res = await db.execute(
        text(
            """
            SELECT *
            FROM patients
            WHERE tenant_id = :tenant_id
              AND patient_id = :patient_id
            LIMIT 1;
            """
        ),
        {
            "tenant_id": tenant_id,
            "patient_id": registry_patient_id,
        },
    )
    row = res.mappings().first()

    return {
        "created": True,
        "patient": dict(row) if row else {
            "tenant_id": tenant_id,
            "patient_id": registry_patient_id,
            "full_name": full_name,
            "mobile": mobile,
            "national_id": national_id,
        },
    }