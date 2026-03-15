from __future__ import annotations
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/reception", tags=["reception"])

def _norm_tenant(tenant_id: str) -> str:
    t = (tenant_id or "").strip()
    if not t:
        raise HTTPException(status_code=400, detail="tenant_id required")
    return t

@router.get("/requests")
async def list_requests(
    tenant_id: str = Query(...),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    # later: date filters
):
    tenant = _norm_tenant(tenant_id)
    where = "WHERE tenant_id = :tenant_id"
    params: Dict[str, Any] = {"tenant_id": tenant, "limit": limit, "offset": offset}

    if status:
        where += " AND status = :status"
        params["status"] = status.strip().upper()

    q = f"""
    SELECT
      tenant_id, request_id, channel, user_id,
      status, intent,
      dept_key, dept_label,
      doctor_key, doctor_label,
      appt_date, appt_time,
      patient_name, patient_mobile, patient_id,
      notes, receptionist_note,
      created_at, updated_at
    FROM appointment_requests
    {where}
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
    """

    # NOTE: db session is passed in from api_server dependency style later,
    # but for your current codebase we’ll call this using AsyncSessionLocal in api_server.py.
    raise RuntimeError("Wire this router with a db dependency in api_server.py (shown below).")


@router.post("/requests/{request_id}/status")
async def set_status(
    request_id: str,
    tenant_id: str = Query(...),
    new_status: str = Query(..., description="PENDING / CONFIRMED / CANCELLED / DONE"),
):
    tenant = _norm_tenant(tenant_id)
    status = (new_status or "").strip().upper()
    if status not in {"PENDING", "CONFIRMED", "CANCELLED", "DONE"}:
        raise HTTPException(status_code=400, detail="Invalid new_status")

    raise RuntimeError("Wire this router with a db dependency in api_server.py (shown below).")


@router.post("/requests/{request_id}/note")
async def set_reception_note(
    request_id: str,
    tenant_id: str = Query(...),
    note: str = Query(..., max_length=2000),
):
    tenant = _norm_tenant(tenant_id)
    note2 = (note or "").strip()[:2000]
    raise RuntimeError("Wire this router with a db dependency in api_server.py (shown below).")