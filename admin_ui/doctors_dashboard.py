from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from database import AsyncSessionLocal
from core.auth_rbac import get_current_user_context

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def _resolve_doctors_page_access(request: Request) -> Dict[str, Any]:
    """
    Access model for /admin/doctors

    Allowed:
    1) Legacy platform token
    2) Platform Admin via JWT session
    3) Doctor via JWT session -> scoped to own tenant + doctor_key
    """

    # ---- 1) Legacy platform token still supported ----
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

    if ADMIN_TOKEN and received == ADMIN_TOKEN:
        return {
            "access_mode": "platform_admin",
            "is_platform_admin": True,
            "tenant_id": "",
            "doctor_key": "",
            "role_code": "PLATFORM_ADMIN",
            "full_name": "",
            "email": "",
        }

    # ---- 2) JWT session access ----
    async with AsyncSessionLocal() as db:
        ctx = await get_current_user_context(request, db)

        role_code = str(ctx.get("role_code") or "").strip().upper()
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        is_platform_admin = bool(ctx.get("is_platform_admin"))

        # Platform admin through login session
        if is_platform_admin:
            return {
                "access_mode": "platform_admin",
                "is_platform_admin": True,
                "tenant_id": "",
                "doctor_key": "",
                "role_code": "PLATFORM_ADMIN",
                "full_name": str(ctx.get("full_name") or ""),
                "email": str(ctx.get("email") or ""),
            }

        # Doctor-scoped access
        if role_code == "DOCTOR":
            if not tenant_id:
                raise HTTPException(status_code=403, detail="tenant_missing_in_token")

            res = await db.execute(
                text(
                    """
                    SELECT
                        tu.tenant_id,
                        tu.doctor_key,
                        u.full_name,
                        u.email
                    FROM tenant_users tu
                    JOIN users u
                      ON u.id = tu.user_id
                    JOIN roles r
                      ON r.id = tu.role_id
                    WHERE tu.user_id = :user_id
                      AND tu.tenant_id = :tenant_id
                      AND tu.is_active = TRUE
                      AND r.role_code = 'DOCTOR'
                    ORDER BY tu.id ASC
                    LIMIT 1
                    """
                ),
                {
                    "user_id": int(ctx["user_id"]),
                    "tenant_id": tenant_id,
                },
            )
            row = res.mappings().first()

            if not row:
                raise HTTPException(status_code=403, detail="doctor_scope_not_found")

            doctor_key = str(row.get("doctor_key") or "").strip()
            if not doctor_key:
                raise HTTPException(status_code=403, detail="doctor_key_not_assigned")

            return {
                "access_mode": "doctor_scoped",
                "is_platform_admin": False,
                "tenant_id": tenant_id,
                "doctor_key": doctor_key,
                "role_code": "DOCTOR",
                "full_name": str(row.get("full_name") or ""),
                "email": str(row.get("email") or ""),
            }

        # Optional future extension:
        # if role_code == "CLINIC_ADMIN": ...
        # allow tenant-scoped doctor management if you decide later

    raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/doctors", response_class=HTMLResponse)
async def doctors_page(request: Request):
    access = await _resolve_doctors_page_access(request)

    return templates.TemplateResponse(
        "doctors.html",
        {
            "request": request,
            "access_mode": access["access_mode"],           # platform_admin | doctor_scoped
            "is_platform_admin": access["is_platform_admin"],
            "scope_tenant_id": access["tenant_id"],
            "scope_doctor_key": access["doctor_key"],
            "scope_role_code": access["role_code"],
            "scope_full_name": access["full_name"],
            "scope_email": access["email"],
        },
    )