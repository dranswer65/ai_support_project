from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_knowledge_base_table(db: AsyncSession) -> None:
    """
    Per-tenant clinic knowledge base.

    Stores FAQ / services / doctors / insurance / location / hours
    in a simple structured format that can later evolve into embeddings/RAG.
    """
    await db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_knowledge_base (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INT NOT NULL DEFAULT 0,
                source TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    )

    # Safe migrations for older versions
    await db.execute(
        text(
            """
            ALTER TABLE tenant_knowledge_base
            ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en';
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenant_knowledge_base
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenant_knowledge_base
            ADD COLUMN IF NOT EXISTS sort_order INT NOT NULL DEFAULT 0;
            """
        )
    )

    await db.execute(
        text(
            """
            ALTER TABLE tenant_knowledge_base
            ADD COLUMN IF NOT EXISTS source TEXT;
            """
        )
    )

    # Useful indexes
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_tenant
            ON tenant_knowledge_base (tenant_id);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_tenant_category
            ON tenant_knowledge_base (tenant_id, category);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_tenant_language
            ON tenant_knowledge_base (tenant_id, language);
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_active
            ON tenant_knowledge_base (tenant_id, is_active);
            """
        )
    )

    await db.commit()


async def seed_default_knowledge_base(db: AsyncSession, tenant_id: str) -> None:
    """
    Minimal starter content so every clinic has editable KB rows.
    Safe to run multiple times.
    """
    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        return

    rows = [
        {
            "tenant_id": tenant_id,
            "category": "faq",
            "title": "Opening Hours",
            "content": "Update this with your clinic opening hours.",
            "language": "en",
            "sort_order": 10,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "faq",
            "title": "Location",
            "content": "Update this with your clinic address and location details.",
            "language": "en",
            "sort_order": 20,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "insurance",
            "title": "Accepted Insurance",
            "content": "Update this with the insurance providers accepted by your clinic.",
            "language": "en",
            "sort_order": 30,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "services",
            "title": "Clinic Services",
            "content": "Update this with the services your clinic provides.",
            "language": "en",
            "sort_order": 40,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "faq",
            "title": "ساعات العمل",
            "content": "حدّث هذه الخانة بساعات عمل العيادة.",
            "language": "ar",
            "sort_order": 10,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "faq",
            "title": "الموقع",
            "content": "حدّث هذه الخانة بعنوان العيادة ووصف الموقع.",
            "language": "ar",
            "sort_order": 20,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "insurance",
            "title": "التأمينات المقبولة",
            "content": "حدّث هذه الخانة بأسماء شركات التأمين المقبولة في العيادة.",
            "language": "ar",
            "sort_order": 30,
            "source": "seed",
        },
        {
            "tenant_id": tenant_id,
            "category": "services",
            "title": "خدمات العيادة",
            "content": "حدّث هذه الخانة بالخدمات التي تقدمها العيادة.",
            "language": "ar",
            "sort_order": 40,
            "source": "seed",
        },
    ]

    for row in rows:
        await db.execute(
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
                SELECT
                    :tenant_id,
                    :category,
                    :title,
                    :content,
                    :language,
                    TRUE,
                    :sort_order,
                    :source,
                    NOW(),
                    NOW()
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM tenant_knowledge_base
                    WHERE tenant_id = :tenant_id
                      AND category = :category
                      AND title = :title
                      AND language = :language
                );
                """
            ),
            row,
        )

    await db.commit()