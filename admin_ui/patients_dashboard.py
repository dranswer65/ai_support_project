from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from database import AsyncSessionLocal
from core.admin_page_auth import require_platform_page_access

router = APIRouter()

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

@router.get("/admin/patients", response_class=HTMLResponse)
async def patients_page(request: Request):
    async with AsyncSessionLocal() as db:
        await require_platform_page_access(request, db)

    return templates.TemplateResponse(
        "patients.html",
        {
            "request": request,
        },
    )