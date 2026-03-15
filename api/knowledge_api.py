from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from database import AsyncSessionLocal
from core.tenant_schema import ensure_tenant_tables
from core.knowledge_schema import (
    ensure_knowledge_base_table,
    seed_default_knowledge_base,
)

router = APIRouter()

ALLOWED_CATEGORIES = {
    "faq",
    "services",
    "doctors",
    "insurance",
    "location",
    "hours",
    "policies",
    "other",
}

ALLOWED_LANGUAGES = {"en", "ar"}


def require_platform_admin(x_admin_token: str) -> None:
    import os

    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/admin/knowledge/list")
async def admin_list_knowledge(
    tenant_id: str,
    category: str = "",
    language: str = "",
    active_only: bool = False,
    q: str = "",
    limit: int = 500,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    category = (category or "").strip().lower()
    language = (language or "").strip().lower()
    q = (q or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    if category and category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400, detail="invalid category")

    if language and language not in ALLOWED_LANGUAGES:
        raise HTTPException(status_code=400, detail="invalid language")

    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_knowledge_base_table(db)

        res = await db.execute(
            text(
                """
                SELECT
                    id,
                    tenant_id,
                    category,
                    title,
                    content,
                    language,
                    is_active,
                    sort_order,
                    source,
                    created_at,
                    updated_at
                FROM tenant_knowledge_base
                WHERE tenant_id = :tenant_id
                  AND (:category = '' OR category = :category)
                  AND (:language = '' OR language = :language)
                  AND (:active_only = FALSE OR is_active = TRUE)
                  AND (
                        :q = ''
                        OR COALESCE(title, '') ILIKE '%' || :q || '%'
                        OR COALESCE(content, '') ILIKE '%' || :q || '%'
                        OR COALESCE(category, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY
                    language ASC,
                    category ASC,
                    sort_order ASC,
                    id ASC
                LIMIT :limit;
                """
            ),
            {
                "tenant_id": tenant_id,
                "category": category,
                "language": language,
                "active_only": active_only,
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/knowledge/upsert")
async def admin_upsert_knowledge(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    item_id_raw = payload.get("id")
    tenant_id = str(payload.get("tenant_id") or "").strip()
    category = str(payload.get("category") or "").strip().lower()
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    language = str(payload.get("language") or "en").strip().lower()
    is_active = bool(payload.get("is_active", True))
    sort_order = int(payload.get("sort_order") or 0)
    source = str(payload.get("source") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400, detail="invalid category")
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    if language not in ALLOWED_LANGUAGES:
        raise HTTPException(status_code=400, detail="invalid language")

    item_id = None
    if item_id_raw not in (None, ""):
        try:
            item_id = int(item_id_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid id")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_knowledge_base_table(db)

        tenant_chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not tenant_chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        if item_id is None:
            res = await db.execute(
                text(
                    """
                    INSERT INTO tenant_knowledge_base (
                        tenant_id,
                        category,
                        title,
                        content,
                        language,
                        is_active,
                        sort_order,
                        source,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :tenant_id,
                        :category,
                        :title,
                        :content,
                        :language,
                        :is_active,
                        :sort_order,
                        :source,
                        NOW(),
                        NOW()
                    )
                    RETURNING id;
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "category": category,
                    "title": title,
                    "content": content,
                    "language": language,
                    "is_active": is_active,
                    "sort_order": sort_order,
                    "source": source,
                },
            )
            row = res.mappings().first()
            item_id = int(row["id"])
        else:
            chk = await db.execute(
                text(
                    """
                    SELECT id
                    FROM tenant_knowledge_base
                    WHERE id = :id
                      AND tenant_id = :tenant_id
                    LIMIT 1;
                    """
                ),
                {
                    "id": item_id,
                    "tenant_id": tenant_id,
                },
            )
            if not chk.mappings().first():
                raise HTTPException(status_code=404, detail="knowledge_item_not_found")

            await db.execute(
                text(
                    """
                    UPDATE tenant_knowledge_base
                    SET
                        category = :category,
                        title = :title,
                        content = :content,
                        language = :language,
                        is_active = :is_active,
                        sort_order = :sort_order,
                        source = :source,
                        updated_at = NOW()
                    WHERE id = :id
                      AND tenant_id = :tenant_id;
                    """
                ),
                {
                    "id": item_id,
                    "tenant_id": tenant_id,
                    "category": category,
                    "title": title,
                    "content": content,
                    "language": language,
                    "is_active": is_active,
                    "sort_order": sort_order,
                    "source": source,
                },
            )

        await db.commit()

    return {
        "ok": True,
        "id": item_id,
        "tenant_id": tenant_id,
        "category": category,
        "language": language,
        "is_active": is_active,
    }


@router.post("/admin/knowledge/{item_id}/deactivate")
async def admin_deactivate_knowledge(
    item_id: int,
    tenant_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_knowledge_base_table(db)

        res = await db.execute(
            text(
                """
                UPDATE tenant_knowledge_base
                SET
                    is_active = FALSE,
                    updated_at = NOW()
                WHERE id = :id
                  AND tenant_id = :tenant_id
                RETURNING id;
                """
            ),
            {
                "id": item_id,
                "tenant_id": tenant_id,
            },
        )
        row = res.mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="knowledge_item_not_found")

        await db.commit()

    return {
        "ok": True,
        "id": item_id,
        "tenant_id": tenant_id,
        "is_active": False,
    }


@router.post("/admin/knowledge/seed-defaults")
async def admin_seed_knowledge_defaults(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_knowledge_base_table(db)

        tenant_chk = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id},
        )
        if not tenant_chk.mappings().first():
            raise HTTPException(status_code=404, detail="tenant_not_found")

        await seed_default_knowledge_base(db, tenant_id)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "message": "knowledge base defaults seeded",
    }