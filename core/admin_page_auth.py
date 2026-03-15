from __future__ import annotations

import os

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth_rbac import get_current_user_context

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()


async def require_platform_page_access(request: Request, db: AsyncSession):
    # 1) allow old admin token for backward compatibility
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
            "auth_mode": "legacy_token",
            "is_platform_admin": True,
            "role_code": "PLATFORM_ADMIN",
            "tenant_id": None,
        }

    # 2) otherwise require JWT login
    ctx = await get_current_user_context(request, db)

    if not ctx.get("is_platform_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    return ctx