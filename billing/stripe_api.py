from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy import text

from database import AsyncSessionLocal
from core.tenant_schema import ensure_tenant_tables, ensure_billing_tables
from billing.checkout import create_checkout_session
from billing.webhook_handler import process_stripe_event
from billing.stripe_client import stripe, APP_BASE_URL

router = APIRouter()

WEBHOOK_SECRET = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()


def require_platform_admin(x_admin_token: str) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/billing/checkout/session")
async def billing_checkout_session(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = str(payload.get("tenant_id") or "").strip()
    plan_code = str(payload.get("plan_code") or "").strip().lower()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not plan_code:
        raise HTTPException(status_code=400, detail="plan_code required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        tenant_res = await db.execute(
            text(
                """
                SELECT tenant_id, clinic_name
                FROM tenants
                WHERE tenant_id = :tenant_id
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id},
        )
        tenant = tenant_res.mappings().first()

        if not tenant:
            raise HTTPException(status_code=404, detail="tenant_not_found")

        plan_res = await db.execute(
            text(
                """
                SELECT code, name
                FROM plans
                WHERE code = :code
                LIMIT 1;
                """
            ),
            {"code": plan_code},
        )
        plan = plan_res.mappings().first()

        if not plan:
            raise HTTPException(status_code=404, detail="plan_not_found")

    try:
        session = create_checkout_session(
            tenant_id=tenant["tenant_id"],
            clinic_name=tenant["clinic_name"],
            plan_code=plan_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"stripe_checkout_failed: {e}")

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "plan_code": plan_code,
        "checkout_url": session["url"],
        "session_id": session["id"],
    }


@router.post("/billing/portal/session")
async def billing_portal_session(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = str(payload.get("tenant_id") or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        res = await db.execute(
            text(
                """
                SELECT
                    s.tenant_id,
                    s.status,
                    s.stripe_customer_id,
                    s.stripe_subscription_id,
                    t.clinic_name
                FROM subscriptions s
                LEFT JOIN tenants t
                  ON t.tenant_id = s.tenant_id
                WHERE s.tenant_id = :tenant_id
                LIMIT 1;
                """
            ),
            {"tenant_id": tenant_id},
        )
        row = res.mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="subscription_not_found")

        stripe_customer_id = str(row.get("stripe_customer_id") or "").strip()

        if not stripe_customer_id:
            raise HTTPException(
                status_code=400,
                detail="stripe_customer_id_missing_for_tenant"
            )

    try:
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{APP_BASE_URL}/admin/billing?token={(os.getenv('ADMIN_TOKEN') or '').strip()}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"stripe_portal_failed: {e}")

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "portal_url": session["url"],
    }


@router.get("/admin/invoices/list")
async def admin_list_invoices(
    tenant_id: str = "",
    status: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    status = (status or "").strip().lower()

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_billing_tables(db)

        sub_res = await db.execute(
            text(
                """
                SELECT
                    s.tenant_id,
                    t.clinic_name,
                    s.plan_code,
                    s.stripe_customer_id
                FROM subscriptions s
                LEFT JOIN tenants t
                  ON t.tenant_id = s.tenant_id
                WHERE (:tenant_id = '' OR s.tenant_id = :tenant_id)
                ORDER BY t.clinic_name ASC, s.tenant_id ASC;
                """
            ),
            {"tenant_id": tenant_id},
        )

        subscriptions = [dict(r) for r in sub_res.mappings().all()]

    items = []

    for sub in subscriptions:
        stripe_customer_id = str(sub.get("stripe_customer_id") or "").strip()
        if not stripe_customer_id:
            continue

        try:
            invoices = stripe.Invoice.list(
                customer=stripe_customer_id,
                limit=20,
            )
        except Exception:
            continue

        for inv in invoices.auto_paging_iter():
            inv_status = str(inv.get("status") or "").lower()

            if status and inv_status != status:
                continue

            amount_paid = float((inv.get("amount_paid") or 0) / 100.0)
            amount_due = float((inv.get("amount_due") or 0) / 100.0)
            amount_total = amount_paid if amount_paid > 0 else amount_due

            created_ts = inv.get("created")
            invoice_date = "-"
            if created_ts:
                from datetime import datetime
                invoice_date = datetime.utcfromtimestamp(created_ts).isoformat()

            items.append({
                "tenant_id": sub.get("tenant_id"),
                "clinic_name": sub.get("clinic_name"),
                "plan_code": sub.get("plan_code"),
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("number"),
                "invoice_date": invoice_date,
                "amount": amount_total,
                "currency": str(inv.get("currency") or "usd").upper(),
                "status": str(inv.get("status") or "").upper(),
                "hosted_invoice_url": inv.get("hosted_invoice_url"),
                "invoice_pdf": inv.get("invoice_pdf"),
            })

    items.sort(
        key=lambda x: (
            str(x.get("invoice_date") or ""),
            str(x.get("tenant_id") or ""),
        ),
        reverse=True,
    )

    return {"ok": True, "items": items}



@router.get("/billing/checkout/success", response_class=HTMLResponse)
async def billing_checkout_success(session_id: str = ""):
    return f"""
    <html>
      <head><title>Payment Success</title></head>
      <body style="font-family:Arial,sans-serif;padding:40px;">
        <h2>Payment received</h2>
        <p>Your checkout completed successfully.</p>
        <p>Session ID: {session_id or "-"}</p>
        <p>You can close this window and return to SupportPilot.</p>
      </body>
    </html>
    """


@router.get("/billing/checkout/cancel", response_class=HTMLResponse)
async def billing_checkout_cancel():
    return """
    <html>
      <head><title>Checkout Cancelled</title></head>
      <body style="font-family:Arial,sans-serif;padding:40px;">
        <h2>Checkout cancelled</h2>
        <p>No payment was completed.</p>
        <p>You can close this window and return to SupportPilot.</p>
      </body>
    </html>
    """


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    await process_stripe_event(event)

    return {"ok": True}