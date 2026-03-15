from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log_usage_event(
    db: AsyncSession,
    tenant_id: str,
    metric_code: str,
    quantity: int = 1,
):
    if not tenant_id or not metric_code:
        return

    try:

        await db.execute(
            text(
                """
                INSERT INTO usage_events (
                    tenant_id,
                    metric_code,
                    quantity,
                    event_date,
                    created_at
                )
                VALUES (
                    :tenant_id,
                    :metric_code,
                    :quantity,
                    CURRENT_DATE,
                    NOW()
                );
                """
            ),
            {
                "tenant_id": tenant_id,
                "metric_code": metric_code,
                "quantity": quantity,
            },
        )

    except Exception as e:
        print("[usage log error]", e)