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

@router.get("/admin/internal-billing", response_class=HTMLResponse)
async def internal_billing_page(request: Request):
    async with AsyncSessionLocal() as db:
        await require_platform_page_access(request, db)

    return templates.TemplateResponse(
        "internal_billing.html",
        {
            "request": request,
        },
    )