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


@router.get("/admin/invoices", response_class=HTMLResponse)
async def invoices_page(request: Request):
    async with AsyncSessionLocal() as db:
        await require_platform_page_access(request, db)

    return templates.TemplateResponse(
        "invoices.html",
        {
            "request": request,
        },
    )