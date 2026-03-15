from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def require_platform_admin(request: Request) -> None:
    expected = ADMIN_TOKEN
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")

    auth = (request.headers.get("Authorization") or "").strip()
    bearer = ""
    if auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()

    received = (
        request.headers.get("X-Admin-Token")
        or request.query_params.get("token")
        or bearer
        or ""
    ).strip()

    if received != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    require_platform_admin(request)
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
        },
    )