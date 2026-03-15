from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()


def require_platform_admin(request: Request):
    token = (
        request.headers.get("X-Admin-Token")
        or request.query_params.get("token")
        or ""
    ).strip()

    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured")

    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/knowledge", response_class=HTMLResponse)
async def knowledge_dashboard(request: Request):

    require_platform_admin(request)

    return templates.TemplateResponse(
        "knowledge.html",
        {
            "request": request,
        },
    )