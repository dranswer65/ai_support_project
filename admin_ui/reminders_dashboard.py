from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from database import AsyncSessionLocal

router = APIRouter()

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def require_platform_admin(request: Request) -> None:
    expected = ADMIN_TOKEN
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")

    header_token = (request.headers.get("X-Admin-Token") or "").strip()
    query_token = (request.query_params.get("token") or "").strip()

    auth_header = (request.headers.get("Authorization") or "").strip()
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()

    received = header_token or query_token or bearer_token

    if received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/reminders", response_class=HTMLResponse)
async def admin_reminders_page(request: Request):
    require_platform_admin(request)
    return templates.TemplateResponse(
        "reminders.html",
        {
            "request": request,
        },
    )


@router.post("/admin/reminders/test")
async def test_reminder(
    request: Request,
    tenant_id: str,
    appointment_id: int,
):
    require_platform_admin(request)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                """
                INSERT INTO reminder_logs (
                    tenant_id,
                    appointment_id,
                    reminder_type,
                    status
                )
                VALUES (
                    :tenant_id,
                    :appointment_id,
                    'REMINDER_MAIN',
                    'SENT'
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "appointment_id": appointment_id,
            },
        )
        await db.commit()

    return {"ok": True}