# admin_ui/reception_dashboard.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal

RECEPTION_TOKEN = (os.getenv("RECEPTION_TOKEN") or "").strip()

router = APIRouter()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as db:
        yield db


def require_reception_token(request: Request) -> None:
    expected = RECEPTION_TOKEN
    if not expected:
        raise HTTPException(status_code=500, detail="RECEPTION_TOKEN is not set")

    received = (request.headers.get("X-Reception-Token") or request.query_params.get("token") or "").strip()
    if received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


def _norm_tenant(request: Request) -> str:
    # MVP: default tenant comes from env WA_DEFAULT_CLIENT
    # Later: receptionist login -> tenant mapping.
    t = (os.getenv("WA_DEFAULT_CLIENT") or "default").strip()
    return t or "default"


@router.get("/reception", response_class=HTMLResponse)
async def reception_page(request: Request):
    require_reception_token(request)

    # Simple HTML served inline (or you can load from templates later)
    # Here we will read the HTML from templates/reception.html
    try:
        with open("templates/reception.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="Missing templates/reception.html")

    return HTMLResponse(html)


@router.get("/api/reception/requests")
async def list_requests(request: Request, status: str = "PENDING", db: AsyncSession = Depends(get_db)):
    require_reception_token(request)
    tenant_id = _norm_tenant(request)

    status = (status or "PENDING").strip().upper()

    res = await db.execute(
        text("""
            SELECT
              request_id, status, intent,
              dept_label, doctor_label,
              appt_date, appt_time,
              patient_name, patient_mobile,
              created_at, updated_at
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND (:status = 'ALL' OR status = :status)
            ORDER BY created_at DESC
            LIMIT 300;
        """),
        {"tenant_id": tenant_id, "status": status},
    )
    rows = res.mappings().all()
    return {"ok": True, "tenant_id": tenant_id, "items": [dict(r) for r in rows]}


@router.get("/api/reception/requests/{request_id}")
async def get_request(request: Request, request_id: str, db: AsyncSession = Depends(get_db)):
    require_reception_token(request)
    tenant_id = _norm_tenant(request)

    res = await db.execute(
        text("""
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id AND request_id = :request_id
            LIMIT 1;
        """),
        {"tenant_id": tenant_id, "request_id": request_id},
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    return {"ok": True, "tenant_id": tenant_id, "item": dict(row)}


@router.post("/api/reception/requests/{request_id}/update")
async def update_request(
    request: Request,
    request_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """
    payload:
      {
        "status": "APPROVED" | "REJECTED" | "CONTACTED" | "PENDING",
        "receptionist_note": "...",
        "send_message": true/false,
        "message_text": "..."
      }
    """
    require_reception_token(request)
    tenant_id = _norm_tenant(request)

    new_status = str(payload.get("status") or "").strip().upper()
    note = str(payload.get("receptionist_note") or "").strip()
    send_message = bool(payload.get("send_message") or False)
    message_text = str(payload.get("message_text") or "").strip()

    if new_status not in {"PENDING", "APPROVED", "REJECTED", "CONTACTED"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    # Fetch request (need user_id / patient_mobile)
    res = await db.execute(
        text("""
            SELECT user_id, patient_mobile, patient_name, dept_label, doctor_label, appt_date, appt_time
            FROM appointment_requests
            WHERE tenant_id = :tenant_id AND request_id = :request_id
            LIMIT 1;
        """),
        {"tenant_id": tenant_id, "request_id": request_id},
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    await db.execute(
        text("""
            UPDATE appointment_requests
            SET status = :status,
                receptionist_note = :note,
                updated_at = NOW()
            WHERE tenant_id = :tenant_id AND request_id = :request_id;
        """),
        {"status": new_status, "note": note, "tenant_id": tenant_id, "request_id": request_id},
    )
    await db.commit()

    # Optional: send WhatsApp message via your existing sender in api_server
    if send_message:
        if not message_text:
            raise HTTPException(status_code=400, detail="message_text required when send_message=true")

        # Import here to avoid circular import at module load time
        from api_server import wa_send_text  # uses WA_ACCESS_TOKEN / WA_PHONE_NUMBER_ID

        to_user = (row.get("user_id") or "").strip()
        if not to_user:
            raise HTTPException(status_code=400, detail="Missing user_id in request row")

        wa_send_text(to_user, message_text)

    return {"ok": True, "tenant_id": tenant_id, "request_id": request_id, "status": new_status}