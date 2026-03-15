from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from database import AsyncSessionLocal
from core.auth_rbac import get_current_user_context

router = APIRouter()

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def require_platform_admin_access(
    request: Request,
    x_admin_token: str = "",
) -> None:
    # 1) Legacy platform admin token still allowed
    expected = ADMIN_TOKEN
    received = (x_admin_token or "").strip()

    auth_header = (request.headers.get("Authorization") or "").strip()
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header.split(" ", 1)[1].strip()

    query_token = (request.query_params.get("token") or "").strip()

    if expected and (
        received == expected
        or bearer_token == expected
        or query_token == expected
    ):
        return

    # 2) JWT session / cookie access
    async with AsyncSessionLocal() as db:
        ctx = await get_current_user_context(request, db)

    if not bool(ctx.get("is_platform_admin")):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(
    request: Request,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    await require_platform_admin_access(request, x_admin_token)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
        },
    )