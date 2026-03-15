from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from database import AsyncSessionLocal
from core.tenant_schema import ensure_tenant_tables, ensure_billing_tables
from billing.stripe_client import stripe


def _ts_to_dt(ts: Any) -> Optional[datetime]:
    if ts in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None


def _normalize_status(status: str | None) -> str:
    s = str(status or "").strip().lower()

    if s == "active":
        return "ACTIVE"
    if s in ("trialing", "trial"):
        return "TRIAL"
    if s in ("past_due", "unpaid", "incomplete", "incomplete_expired"):
        return "PAST_DUE"
    if s in ("canceled", "cancelled"):
        return "CANCELLED"

    return s.upper() if s else "UNKNOWN"


def _plan_code_from_subscription(sub: dict) -> str:
    metadata = sub.get("metadata") or {}
    return str(metadata.get("plan_code") or "").strip().lower()


def _tenant_id_from_subscription(sub: dict) -> str:
    metadata = sub.get("metadata") or {}
    return str(metadata.get("tenant_id") or "").strip()


async def _get_tenant_by_customer_id(db, customer_id: str) -> Optional[str]:
    if not customer_id:
        return None

    res = await db.execute(
        text(
            """
            SELECT tenant_id
            FROM subscriptions
            WHERE stripe_customer_id = :customer_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"customer_id": customer_id},
    )
    row = res.mappings().first()
    return str(row["tenant_id"]) if row else None


async def _get_latest_subscription_row(db, tenant_id: str):
    res = await db.execute(
        text(
            """
            SELECT id, tenant_id
            FROM subscriptions
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id},
    )
    return res.mappings().first()


async def _upsert_subscription(
    db,
    *,
    tenant_id: str,
    plan_code: str,
    status: str,
    started_at: Optional[datetime],
    ends_at: Optional[datetime],
    trial_ends_at: Optional[datetime],
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str],
) -> None:
    existing = await _get_latest_subscription_row(db, tenant_id)

    if existing:
        await db.execute(
            text(
                """
                UPDATE subscriptions
                SET
                    plan_code = COALESCE(:plan_code, plan_code),
                    status = :status,
                    started_at = COALESCE(:started_at, started_at),
                    ends_at = COALESCE(:ends_at, ends_at),
                    trial_ends_at = COALESCE(:trial_ends_at, trial_ends_at),
                    stripe_customer_id = COALESCE(:stripe_customer_id, stripe_customer_id),
                    stripe_subscription_id = COALESCE(:stripe_subscription_id, stripe_subscription_id),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": existing["id"],
                "plan_code": plan_code or None,
                "status": status,
                "started_at": started_at,
                "ends_at": ends_at,
                "trial_ends_at": trial_ends_at,
                "stripe_customer_id": stripe_customer_id or None,
                "stripe_subscription_id": stripe_subscription_id or None,
            },
        )
    else:
        await db.execute(
            text(
                """
                INSERT INTO subscriptions (
                    tenant_id,
                    plan_code,
                    status,
                    started_at,
                    ends_at,
                    trial_ends_at,
                    stripe_customer_id,
                    stripe_subscription_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :plan_code,
                    :status,
                    :started_at,
                    :ends_at,
                    :trial_ends_at,
                    :stripe_customer_id,
                    :stripe_subscription_id,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "plan_code": plan_code or None,
                "status": status,
                "started_at": started_at,
                "ends_at": ends_at,
                "trial_ends_at": trial_ends_at,
                "stripe_customer_id": stripe_customer_id or None,
                "stripe_subscription_id": stripe_subscription_id or None,
            },
        )


async def process_stripe_event(event):
    event_type = str(event.get("type") or "").strip()
    obj = ((event.get("data") or {}).get("object") or {})

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        try:
            # ---------------------------------------------------------
            # Checkout completed
            # ---------------------------------------------------------
            if event_type == "checkout.session.completed":
                tenant_id = str(obj.get("client_reference_id") or "").strip()
                customer_id = str(obj.get("customer") or "").strip()
                subscription_id = str(obj.get("subscription") or "").strip()
                metadata = obj.get("metadata") or {}
                plan_code = str(metadata.get("plan_code") or "").strip().lower() or "starter"

                if not tenant_id and customer_id:
                    tenant_id = await _get_tenant_by_customer_id(db, customer_id) or ""

                if tenant_id and subscription_id:
                    sub = stripe.Subscription.retrieve(subscription_id)

                    started_at = _ts_to_dt(sub.get("current_period_start"))
                    ends_at = _ts_to_dt(sub.get("current_period_end"))
                    trial_ends_at = _ts_to_dt(sub.get("trial_end"))
                    status = _normalize_status(sub.get("status"))

                    sub_plan_code = _plan_code_from_subscription(sub)
                    if sub_plan_code:
                        plan_code = sub_plan_code

                    await _upsert_subscription(
                        db,
                        tenant_id=tenant_id,
                        plan_code=plan_code,
                        status=status,
                        started_at=started_at,
                        ends_at=ends_at,
                        trial_ends_at=trial_ends_at,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=subscription_id,
                    )

                    print(f"[stripe] checkout completed -> {status} {tenant_id}")

            # ---------------------------------------------------------
            # Invoice paid (monthly renewal success too)
            # ---------------------------------------------------------
            elif event_type == "invoice.paid":
                customer_id = str(obj.get("customer") or "").strip()
                subscription_id = str(obj.get("subscription") or "").strip()

                if subscription_id:
                    sub = stripe.Subscription.retrieve(subscription_id)

                    tenant_id = _tenant_id_from_subscription(sub)
                    if not tenant_id and customer_id:
                        tenant_id = await _get_tenant_by_customer_id(db, customer_id) or ""

                    if tenant_id:
                        await _upsert_subscription(
                            db,
                            tenant_id=tenant_id,
                            plan_code=_plan_code_from_subscription(sub),
                            status="ACTIVE",
                            started_at=_ts_to_dt(sub.get("current_period_start")),
                            ends_at=_ts_to_dt(sub.get("current_period_end")),
                            trial_ends_at=_ts_to_dt(sub.get("trial_end")),
                            stripe_customer_id=customer_id or str(sub.get("customer") or "").strip(),
                            stripe_subscription_id=subscription_id,
                        )

                        print(f"[stripe] invoice paid -> ACTIVE {tenant_id}")

            # ---------------------------------------------------------
            # Invoice payment failed
            # ---------------------------------------------------------
            elif event_type == "invoice.payment_failed":
                customer_id = str(obj.get("customer") or "").strip()
                subscription_id = str(obj.get("subscription") or "").strip()

                tenant_id = None
                if customer_id:
                    tenant_id = await _get_tenant_by_customer_id(db, customer_id)

                if not tenant_id and subscription_id:
                    sub = stripe.Subscription.retrieve(subscription_id)
                    tenant_id = _tenant_id_from_subscription(sub)

                if tenant_id:
                    await _upsert_subscription(
                        db,
                        tenant_id=tenant_id,
                        plan_code="",
                        status="PAST_DUE",
                        started_at=None,
                        ends_at=None,
                        trial_ends_at=None,
                        stripe_customer_id=customer_id or None,
                        stripe_subscription_id=subscription_id or None,
                    )

                    print(f"[stripe] invoice payment failed -> PAST_DUE {tenant_id}")

            # ---------------------------------------------------------
            # Subscription updated
            # ---------------------------------------------------------
            elif event_type == "customer.subscription.updated":
                subscription_id = str(obj.get("id") or "").strip()
                customer_id = str(obj.get("customer") or "").strip()

                tenant_id = _tenant_id_from_subscription(obj)
                if not tenant_id and customer_id:
                    tenant_id = await _get_tenant_by_customer_id(db, customer_id) or ""

                if tenant_id:
                    await _upsert_subscription(
                        db,
                        tenant_id=tenant_id,
                        plan_code=_plan_code_from_subscription(obj),
                        status=_normalize_status(obj.get("status")),
                        started_at=_ts_to_dt(obj.get("current_period_start")),
                        ends_at=_ts_to_dt(obj.get("current_period_end")),
                        trial_ends_at=_ts_to_dt(obj.get("trial_end")),
                        stripe_customer_id=customer_id or None,
                        stripe_subscription_id=subscription_id or None,
                    )

                    print(f"[stripe] subscription updated -> {tenant_id}")

            # ---------------------------------------------------------
            # Subscription deleted / cancelled
            # ---------------------------------------------------------
            elif event_type == "customer.subscription.deleted":
                subscription_id = str(obj.get("id") or "").strip()
                customer_id = str(obj.get("customer") or "").strip()

                tenant_id = _tenant_id_from_subscription(obj)
                if not tenant_id and customer_id:
                    tenant_id = await _get_tenant_by_customer_id(db, customer_id) or ""

                if tenant_id:
                    await _upsert_subscription(
                        db,
                        tenant_id=tenant_id,
                        plan_code=_plan_code_from_subscription(obj),
                        status="CANCELLED",
                        started_at=None,
                        ends_at=_ts_to_dt(obj.get("current_period_end")),
                        trial_ends_at=_ts_to_dt(obj.get("trial_end")),
                        stripe_customer_id=customer_id or None,
                        stripe_subscription_id=subscription_id or None,
                    )

                    print(f"[stripe] subscription deleted -> CANCELLED {tenant_id}")

            else:
                print(f"[stripe] ignored event -> {event_type}")

            await db.commit()

        except Exception:
            await db.rollback()
            raise