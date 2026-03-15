from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def validate_tenant_admin_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    admin_token: str,
) -> None:
    tenant_id = (tenant_id or "").strip()
    admin_token = (admin_token or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    if not admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    res = await db.execute(
        text(
            """
            SELECT
                t.tenant_id,
                t.status,
                tt.admin_token
            FROM tenants t
            JOIN tenant_tokens tt
              ON tt.tenant_id = t.tenant_id
            WHERE t.tenant_id = :tenant_id
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )
    row = res.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="tenant_not_found")

    if str(row.get("status") or "").upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="tenant_inactive")

    expected = str(row.get("admin_token") or "").strip()
    if not expected or expected != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


async def resolve_and_validate_tenant_admin(
    db: AsyncSession,
    *,
    x_tenant_id: Optional[str],
    x_admin_token: Optional[str],
    fallback_tenant_id: Optional[str] = None,
) -> str:
    tenant_id = (x_tenant_id or fallback_tenant_id or "").strip()
    admin_token = (x_admin_token or "").strip()

    await validate_tenant_admin_access(
        db,
        tenant_id=tenant_id,
        admin_token=admin_token,
    )
    return tenant_id