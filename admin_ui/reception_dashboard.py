# admin_ui/reception_dashboard.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal

# =========================================================
# CONFIG
# =========================================================

RECEPTION_TOKEN = (os.getenv("RECEPTION_TOKEN") or "").strip()

router = APIRouter()

# Point templates to: admin_ui/templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# =========================================================
# DB DEPENDENCY
# =========================================================

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as db:
        yield db


# =========================================================
# SECURITY
# =========================================================

def require_reception_token(request: Request) -> None:
    expected = RECEPTION_TOKEN

    if not expected:
        raise HTTPException(status_code=500, detail="RECEPTION_TOKEN is not set")

    received = (
        request.headers.get("X-Reception-Token")
        or request.query_params.get("token")
        or ""
    ).strip()

    if received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


# =========================================================
# TENANT RESOLUTION
# =========================================================

def _norm_tenant(request: Request) -> str:
    # For now we use default tenant
    t = (os.getenv("WA_DEFAULT_CLIENT") or "default").strip()
    return t or "default"


# =========================================================
# DASHBOARD PAGE
# =========================================================

@router.get("/reception", response_class=HTMLResponse)
async def reception_page(request: Request):
    """
    Serves Reception Dashboard UI
    """
    require_reception_token(request)

    return templates.TemplateResponse(
        "reception.html",
        {"request": request},
    )


# =========================================================
# LIST APPOINTMENT REQUESTS
# =========================================================

@router.get("/api/reception/requests")
async def list_requests(
    request: Request,
    status: str = "PENDING",
    db: AsyncSession = Depends(get_db),
):
    require_reception_token(request)

    tenant_id = _norm_tenant(request)

    status = (status or "PENDING").strip().upper()

    result = await db.execute(
        text(
            """
            SELECT
                request_id,
                status,
                intent,
                dept_label,
                doctor_label,
                appt_date,
                appt_time,
                patient_name,
                patient_mobile,
                created_at,
                updated_at
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND (:status = 'ALL' OR status = :status)
            ORDER BY created_at DESC
            LIMIT 300
            """
        ),
        {
            "tenant_id": tenant_id,
            "status": status,
        },
    )

    rows = result.mappings().all()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [dict(r) for r in rows],
    }


# =========================================================
# GET SINGLE REQUEST
# =========================================================

@router.get("/api/reception/requests/{request_id}")
async def get_request(
    request: Request,
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    require_reception_token(request)

    tenant_id = _norm_tenant(request)

    result = await db.execute(
        text(
            """
            SELECT *
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )

    row = result.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "item": dict(row),
    }


# =========================================================
# UPDATE REQUEST STATUS
# =========================================================

@router.post("/api/reception/requests/{request_id}/update")
async def update_request(
    request: Request,
    request_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """
    Payload example:

    {
        "status": "APPROVED",
        "receptionist_note": "Confirmed with doctor",
        "send_message": true,
        "message_text": "Your appointment is confirmed"
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

    # -----------------------------------------------------
    # Load appointment request
    # -----------------------------------------------------

    result = await db.execute(
        text(
            """
            SELECT
                user_id,
                patient_mobile,
                patient_name,
                dept_label,
                doctor_label,
                appt_date,
                appt_time
            FROM appointment_requests
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )

    row = result.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    # -----------------------------------------------------
    # Update DB
    # -----------------------------------------------------

    await db.execute(
        text(
            """
            UPDATE appointment_requests
            SET
                status = :status,
                receptionist_note = :note,
                updated_at = NOW()
            WHERE tenant_id = :tenant_id
              AND request_id = :request_id
            """
        ),
        {
            "status": new_status,
            "note": note,
            "tenant_id": tenant_id,
            "request_id": request_id,
        },
    )

    await db.commit()

    # -----------------------------------------------------
    # Optional WhatsApp message
    # -----------------------------------------------------

    if send_message:

        if not message_text:
            raise HTTPException(
                status_code=400,
                detail="message_text required when send_message=true",
            )

        # Import here to avoid circular imports
        from api_server import wa_send_text

        to_user = (row.get("user_id") or "").strip()

        if not to_user:
            raise HTTPException(
                status_code=400,
                detail="Missing user_id for WhatsApp reply",
            )

        wa_send_text(to_user, message_text)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "status": new_status,
    }