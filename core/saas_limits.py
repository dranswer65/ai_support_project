from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SaaSLimitExceeded(Exception):
    pass


async def get_tenant_plan(db: AsyncSession, tenant_id: str):
    res = await db.execute(
        text(
            """
            SELECT
                p.code,
                p.name,
                p.max_doctors,
                p.max_monthly_appointments,
                p.max_ai_conversations
            FROM subscriptions s
            JOIN plans p
              ON p.code = s.plan_code
            WHERE s.tenant_id = :tenant_id
            ORDER BY s.created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )

    return res.mappings().first()


async def get_usage_this_month(db: AsyncSession, tenant_id: str, metric_code: str):
    res = await db.execute(
        text(
            """
            SELECT COALESCE(SUM(quantity), 0) AS total
            FROM usage_events
            WHERE tenant_id = :tenant_id
              AND metric_code = :metric
              AND date_trunc('month', event_date) = date_trunc('month', CURRENT_DATE)
            """
        ),
        {
            "tenant_id": tenant_id,
            "metric": metric_code,
        },
    )

    row = res.mappings().first()
    return row["total"] if row else 0


async def enforce_subscription_active(db: AsyncSession, tenant_id: str):
    res = await db.execute(
        text(
            """
            SELECT status
            FROM subscriptions
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )

    row = res.mappings().first()

    if not row:
        raise SaaSLimitExceeded(
            "Your clinic does not have an active subscription."
        )

    status = str(row["status"] or "").strip().upper()

    if status in ("TRIAL", "ACTIVE"):
        return

    if status in ("TRIAL_EXPIRED", "PAST_DUE", "CANCELLED"):
        raise SaaSLimitExceeded(
            "Your clinic subscription is inactive. Please upgrade to continue."
        )

    raise SaaSLimitExceeded(
        f"Subscription status '{status or 'UNKNOWN'}' is not allowed."
    )


async def get_usage_alert_level(db: AsyncSession, tenant_id: str):
    plan = await get_tenant_plan(db, tenant_id)

    if not plan:
        return {
            "level": "NONE",
            "plan_code": None,
            "plan_name": None,
            "used": 0,
            "limit": 0,
            "percent": 0,
        }

    limit = int(plan["max_ai_conversations"] or 0)
    used = int(await get_usage_this_month(db, tenant_id, "ai_conversations"))
    pct = int((used / limit) * 100) if limit > 0 else 0

    if limit <= 0:
        return {
            "level": "NONE",
            "plan_code": plan["code"],
            "plan_name": plan["name"],
            "used": used,
            "limit": limit,
            "percent": 0,
        }

    if pct >= 100:
        level = "OVER"
    elif pct >= 80:
        level = "NEAR"
    else:
        level = "OK"

    return {
        "level": level,
        "plan_code": plan["code"],
        "plan_name": plan["name"],
        "used": used,
        "limit": limit,
        "percent": pct,
    }


async def enforce_ai_limit(db: AsyncSession, tenant_id: str):
    plan = await get_tenant_plan(db, tenant_id)
    if not plan:
        return

    limit = plan["max_ai_conversations"] or 0
    if limit <= 0:
        return

    used = await get_usage_this_month(db, tenant_id, "ai_conversations")

    if used >= limit:
        raise SaaSLimitExceeded(
            f"AI usage limit reached ({used}/{limit})"
        )


async def enforce_appointment_limit(db: AsyncSession, tenant_id: str):
    plan = await get_tenant_plan(db, tenant_id)
    if not plan:
        return

    limit = plan["max_monthly_appointments"] or 0
    if limit <= 0:
        return

    used = await get_usage_this_month(
        db,
        tenant_id,
        "appointments_created"
    )

    if used >= limit:
        raise SaaSLimitExceeded(
            f"Appointment limit reached ({used}/{limit})"
        )


async def enforce_doctor_limit(db: AsyncSession, tenant_id: str):
    plan = await get_tenant_plan(db, tenant_id)
    if not plan:
        return

    limit = plan["max_doctors"] or 0
    if limit <= 0:
        return

    res = await db.execute(
        text(
            """
            SELECT COUNT(*) AS total
            FROM doctors
            WHERE tenant_id = :tenant_id
              AND is_active = TRUE
            """
        ),
        {"tenant_id": tenant_id},
    )

    row = res.mappings().first()
    doctors = row["total"] if row else 0

    if doctors >= limit:
        raise SaaSLimitExceeded(
            f"Doctor limit reached ({doctors}/{limit})"
        )