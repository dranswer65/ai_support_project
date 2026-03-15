from __future__ import annotations

import os

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth_rbac import get_current_user_context

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()


async def require_platform_api_access(
    request: Request,
    db: AsyncSession,
    x_admin_token: str = "",
):
    # 1) old admin token still works
    received = (x_admin_token or "").strip()
    if ADMIN_TOKEN and received == ADMIN_TOKEN:
        return {
            "auth_mode": "legacy_token",
            "is_platform_admin": True,
            "role_code": "PLATFORM_ADMIN",
            "tenant_id": None,
        }

    # 2) otherwise use JWT cookie / bearer token
    ctx = await get_current_user_context(request, db)

    if not ctx.get("is_platform_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")

    return ctx