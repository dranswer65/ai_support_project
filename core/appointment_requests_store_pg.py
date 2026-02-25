# core/appointment_requests_store_pg.py
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_appointment_request(
    *,
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> str:
    """
    Inserts a row into appointment_requests (receptionist queue).
    Returns request_id.
    """
    request_id = str(uuid.uuid4())

    # Safe get
    intent = (payload.get("intent") or "BOOK").strip().upper()
    status = (payload.get("status") or "PENDING").strip().upper()

    dept_key = payload.get("dept_key")
    dept_label = payload.get("dept_label")
    doctor_key = payload.get("doctor_key")
    doctor_label = payload.get("doctor_label")
    appt_date = payload.get("appt_date")      # keep as TEXT per your schema
    appt_time = payload.get("appt_time")      # keep as TEXT per your schema

    patient_name = payload.get("patient_name")
    patient_mobile = payload.get("patient_mobile")
    patient_id = payload.get("patient_id")

    notes = (payload.get("notes") or "")[:1000]

    await db.execute(
        text("""
        INSERT INTO appointment_requests (
          tenant_id, request_id, channel, user_id,
          status, intent,
          dept_key, dept_label,
          doctor_key, doctor_label,
          appt_date, appt_time,
          patient_name, patient_mobile, patient_id,
          notes
        )
        VALUES (
          :tenant_id, :request_id, 'whatsapp', :user_id,
          :status, :intent,
          :dept_key, :dept_label,
          :doctor_key, :doctor_label,
          :appt_date, :appt_time,
          :patient_name, :patient_mobile, :patient_id,
          :notes
        )
        """),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
            "user_id": user_id,
            "status": status,
            "intent": intent,
            "dept_key": dept_key,
            "dept_label": dept_label,
            "doctor_key": doctor_key,
            "doctor_label": doctor_label,
            "appt_date": appt_date,
            "appt_time": appt_time,
            "patient_name": patient_name,
            "patient_mobile": patient_mobile,
            "patient_id": patient_id,
            "notes": notes,
        },
    )
    await db.commit()
    return request_id