# seed_tenant_and_request.py
# Creates:
#  1) a tenant row (so FK on appointment_requests.tenant_id passes)
#  2) one appointment_requests row to prove Reception UI works
#
# PowerShell usage:
#   $env:DATABASE_URL="postgresql+asyncpg://USER:PASS@HOST:PORT/DB"
#   py seed_tenant_and_request.py
#
# Optional overrides:
#   $env:WA_DEFAULT_CLIENT="supportpilot_demo"
#   $env:SEED_REQUEST_ID="REQ-TEST-001"

from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

TENANT_ID = (os.getenv("WA_DEFAULT_CLIENT") or "supportpilot_demo").strip() or "supportpilot_demo"
REQUEST_ID = (os.getenv("SEED_REQUEST_ID") or "REQ-TEST-001").strip() or "REQ-TEST-001"

USER_ID = (os.getenv("SEED_USER_ID") or "966500000000").strip() or "966500000000"
PATIENT_NAME = (os.getenv("SEED_PATIENT_NAME") or "Test Patient").strip() or "Test Patient"
PATIENT_MOBILE = (os.getenv("SEED_PATIENT_MOBILE") or "+966500000000").strip() or "+966500000000"

DEPT_LABEL = (os.getenv("SEED_DEPT_LABEL") or "Dermatology").strip() or "Dermatology"
DOCTOR_LABEL = (os.getenv("SEED_DOCTOR_LABEL") or "Dr Test").strip() or "Dr Test"
APPT_TIME = (os.getenv("SEED_APPT_TIME") or "10:30").strip() or "10:30"


async def _get_table_columns(conn, table_name: str) -> List[str]:
    res = await conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = :t
            ORDER BY ordinal_position;
            """
        ),
        {"t": table_name},
    )
    return [r[0] for r in res.all()]


async def _ensure_tenant(conn, tenant_id: str) -> None:
    """
    Your tenants schema (from your output):
      tenant_id text
      name_en text NOT NULL
      name_ar text NOT NULL
      is_active boolean
      default_language text
      supported_languages jsonb
      timezone text
      location_text_en text
      location_text_ar text
      google_maps_url text
      slot_duration_min integer
      working_hours jsonb
      insurance_list jsonb
      wa_phone_number_id text
      wa_verify_token text
      created_at timestamptz
      updated_at timestamptz

    We insert safe defaults for the common NOT NULLs and sensible defaults elsewhere.
    """

    cols = await _get_table_columns(conn, "tenants")
    cols_set = {c.lower() for c in cols}

    insert_cols: List[str] = []
    values_sql: List[str] = []
    params: Dict[str, Any] = {}

    def add(col: str, value_sql: str, param_key: str | None = None, param_val: Any | None = None) -> None:
        insert_cols.append(col)
        values_sql.append(value_sql)
        if param_key is not None:
            params[param_key] = param_val

    # required / core
    if "tenant_id" not in cols_set:
        raise RuntimeError("tenants table missing tenant_id column.")
    add("tenant_id", ":tenant_id", "tenant_id", tenant_id)

    # IMPORTANT: satisfy NOT NULL columns
    if "name_en" in cols_set:
        add("name_en", ":name_en", "name_en", tenant_id)
    if "name_ar" in cols_set:
        add("name_ar", ":name_ar", "name_ar", tenant_id)

    # safe defaults (only if columns exist)
    if "is_active" in cols_set:
        add("is_active", "TRUE")
    if "default_language" in cols_set:
        add("default_language", ":default_language", "default_language", "ar")
    if "supported_languages" in cols_set:
        add("supported_languages", "CAST(:supported_languages AS jsonb)", "supported_languages", '["ar","en"]')
    if "timezone" in cols_set:
        add("timezone", ":timezone", "timezone", "Asia/Riyadh")

    if "location_text_en" in cols_set:
        add("location_text_en", ":location_text_en", "location_text_en", "")
    if "location_text_ar" in cols_set:
        add("location_text_ar", ":location_text_ar", "location_text_ar", "")
    if "google_maps_url" in cols_set:
        add("google_maps_url", ":google_maps_url", "google_maps_url", "")

    if "slot_duration_min" in cols_set:
        add("slot_duration_min", ":slot_duration_min", "slot_duration_min", 30)
    if "working_hours" in cols_set:
        add("working_hours", "CAST(:working_hours AS jsonb)", "working_hours", "{}")
    if "insurance_list" in cols_set:
        add("insurance_list", "CAST(:insurance_list AS jsonb)", "insurance_list", "[]")

    if "wa_phone_number_id" in cols_set:
        add("wa_phone_number_id", ":wa_phone_number_id", "wa_phone_number_id", "")
    if "wa_verify_token" in cols_set:
        add("wa_verify_token", ":wa_verify_token", "wa_verify_token", "")

    if "created_at" in cols_set:
        add("created_at", "NOW()")
    if "updated_at" in cols_set:
        add("updated_at", "NOW()")

    sql = f"""
        INSERT INTO tenants ({", ".join(insert_cols)})
        VALUES ({", ".join(values_sql)})
        ON CONFLICT (tenant_id) DO NOTHING;
    """

    await conn.execute(text(sql), params)


async def _insert_request(conn, tenant_id: str, request_id: str) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO appointment_requests (
              tenant_id, request_id, channel, user_id,
              status, intent,
              dept_label, doctor_label,
              appt_date, appt_time,
              patient_name, patient_mobile,
              notes, created_at, updated_at
            ) VALUES (
              :tenant_id, :request_id, 'whatsapp', :user_id,
              'PENDING', 'BOOK',
              :dept_label, :doctor_label,
              CURRENT_DATE + INTERVAL '1 day', :appt_time,
              :patient_name, :patient_mobile,
              'manual seed test', NOW(), NOW()
            )
            ON CONFLICT (tenant_id, request_id) DO NOTHING;
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
            "user_id": USER_ID,
            "dept_label": DEPT_LABEL,
            "doctor_label": DOCTOR_LABEL,
            "appt_time": APPT_TIME,
            "patient_name": PATIENT_NAME,
            "patient_mobile": PATIENT_MOBILE,
        },
    )


async def main() -> None:
    if not DATABASE_URL:
        print("DATABASE_URL is not set")
        return

    eng = create_async_engine(DATABASE_URL, future=True)

    try:
        async with eng.begin() as conn:
            await _ensure_tenant(conn, TENANT_ID)
            await _insert_request(conn, TENANT_ID, REQUEST_ID)

        print("✅ Seed complete")
        print(f"   tenant_id  = {TENANT_ID}")
        print(f"   request_id = {REQUEST_ID}")
        print("")
        print("Now refresh Reception Dashboard:")
        print("  http://127.0.0.1:8010/reception?token=YOUR_RECEPTION_TOKEN")
        print("")
        print("Or test API (PowerShell):")
        print('  Invoke-RestMethod "http://127.0.0.1:8010/api/reception/requests?status=PENDING" -Headers @{ "X-Reception-Token" = "YOUR_RECEPTION_TOKEN" }')
    finally:
        await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())