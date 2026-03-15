from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def enforce_subscription_status(db: AsyncSession):
    """
    Enforces subscription lifecycle rules.

    1. TRIAL -> TRIAL_EXPIRED when trial date passed
    2. ACTIVE -> PAST_DUE when subscription end passed
    """

    # -----------------------------------------------------
    # Expire trials
    # -----------------------------------------------------
    trial_result = await db.execute(
        text(
            """
            UPDATE subscriptions
            SET status = 'TRIAL_EXPIRED',
                updated_at = NOW()
            WHERE status = 'TRIAL'
              AND trial_ends_at IS NOT NULL
              AND trial_ends_at < NOW()
            RETURNING tenant_id
            """
        )
    )

    expired_trials = trial_result.fetchall()

    if expired_trials:
        for row in expired_trials:
            print(f"[subscriptions] trial expired -> {row.tenant_id}")

    # -----------------------------------------------------
    # Expire ACTIVE subscriptions
    # -----------------------------------------------------
    active_result = await db.execute(
        text(
            """
            UPDATE subscriptions
            SET status = 'PAST_DUE',
                updated_at = NOW()
            WHERE status = 'ACTIVE'
              AND ends_at IS NOT NULL
              AND ends_at < NOW()
            RETURNING tenant_id
            """
        )
    )

    expired_active = active_result.fetchall()

    if expired_active:
        for row in expired_active:
            print(f"[subscriptions] subscription expired -> {row.tenant_id}")

    await db.commit()

    return {
        "trial_expired": len(expired_trials),
        "active_expired": len(expired_active),
    }