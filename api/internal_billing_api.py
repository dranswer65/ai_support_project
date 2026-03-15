from __future__ import annotations

import os
from typing import Any, Dict
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from fastapi import Query

from database import AsyncSessionLocal
from core.tenant_schema import ensure_tenant_tables
from core.internal_billing_schema import ensure_internal_billing_tables

router = APIRouter()

ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN") or "").strip()


def require_platform_admin(x_admin_token: str):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not set")
    if (x_admin_token or "").strip() != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


def _norm_money(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def _invoice_number_for_account(account_id: int) -> str:
    return f"BILL-{account_id:06d}"


@router.post("/admin/internal-billing/catalog/upsert")
async def billing_catalog_upsert(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = str(payload.get("tenant_id") or "").strip()
    department_code = str(payload.get("department_code") or "OPD").strip().upper()
    item_code = str(payload.get("item_code") or "").strip().upper()
    item_name = str(payload.get("item_name") or "").strip()
    item_type = str(payload.get("item_type") or "SERVICE").strip().upper()
    base_price = _norm_money(payload.get("base_price"))
    tax_rate = _norm_money(payload.get("tax_rate"))
    is_active = bool(payload.get("is_active", True))
    notes = str(payload.get("notes") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not item_code:
        raise HTTPException(status_code=400, detail="item_code required")
    if not item_name:
        raise HTTPException(status_code=400, detail="item_name required")

    async with AsyncSessionLocal() as db:
        await ensure_tenant_tables(db)
        await ensure_internal_billing_tables(db)

        await db.execute(
            text("""
                INSERT INTO billing_catalog (
                    tenant_id,
                    department_code,
                    item_code,
                    item_name,
                    item_type,
                    base_price,
                    tax_rate,
                    is_active,
                    notes,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :department_code,
                    :item_code,
                    :item_name,
                    :item_type,
                    :base_price,
                    :tax_rate,
                    :is_active,
                    :notes,
                    NOW()
                )
                ON CONFLICT (tenant_id, department_code, item_code)
                DO UPDATE SET
                    item_name = EXCLUDED.item_name,
                    item_type = EXCLUDED.item_type,
                    base_price = EXCLUDED.base_price,
                    tax_rate = EXCLUDED.tax_rate,
                    is_active = EXCLUDED.is_active,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """),
            {
                "tenant_id": tenant_id,
                "department_code": department_code,
                "item_code": item_code,
                "item_name": item_name,
                "item_type": item_type,
                "base_price": base_price,
                "tax_rate": tax_rate,
                "is_active": is_active,
                "notes": notes,
            },
        )

        await db.commit()

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "department_code": department_code,
        "item_code": item_code,
    }


@router.get("/admin/internal-billing/catalog/list")
async def billing_catalog_list(
    tenant_id: str,
    department_code: str = "",
    q: str = "",
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    department_code = (department_code or "").strip().upper()
    q = (q or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        res = await db.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    department_code,
                    item_code,
                    item_name,
                    item_type,
                    base_price,
                    tax_rate,
                    is_active,
                    notes,
                    created_at,
                    updated_at
                FROM billing_catalog
                WHERE tenant_id = :tenant_id
                  AND (:department_code = '' OR department_code = :department_code)
                  AND (
                        :q = ''
                        OR item_code ILIKE '%' || :q || '%'
                        OR item_name ILIKE '%' || :q || '%'
                      )
                ORDER BY department_code ASC, item_name ASC
            """),
            {
                "tenant_id": tenant_id,
                "department_code": department_code,
                "q": q,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.post("/admin/internal-billing/accounts/create")
async def billing_account_create(
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = str(payload.get("tenant_id") or "").strip()
    patient_id = str(payload.get("patient_id") or "").strip() or None
    appointment_id = str(payload.get("appointment_id") or "").strip() or None
    encounter_type = str(payload.get("encounter_type") or "OPD").strip().upper()
    source_module = str(payload.get("source_module") or "APPOINTMENTS").strip().upper()
    source_id = str(payload.get("source_id") or "").strip() or None
    currency = str(payload.get("currency") or "SAR").strip().upper()
    notes = str(payload.get("notes") or "").strip() or None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        res = await db.execute(
            text("""
                INSERT INTO billing_accounts (
                    tenant_id,
                    patient_id,
                    appointment_id,
                    encounter_type,
                    source_module,
                    source_id,
                    status,
                    currency,
                    notes,
                    updated_at
                )
                VALUES (
                    :tenant_id,
                    :patient_id,
                    :appointment_id,
                    :encounter_type,
                    :source_module,
                    :source_id,
                    'OPEN',
                    :currency,
                    :notes,
                    NOW()
                )
                RETURNING id
            """),
            {
                "tenant_id": tenant_id,
                "patient_id": patient_id,
                "appointment_id": appointment_id,
                "encounter_type": encounter_type,
                "source_module": source_module,
                "source_id": source_id,
                "currency": currency,
                "notes": notes,
            },
        )
        row = res.mappings().first()
        account_id = int(row["id"])

        if appointment_id:
            await db.execute(
                text("""
                    UPDATE appointments
                    SET
                        billing_account_id = :billing_account_id,
                        billing_status = 'OPEN',
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND (
                            CAST(id AS TEXT) = :appointment_id
                            OR COALESCE(appointment_id, '') = :appointment_id
                          )
                """),
                {
                    "tenant_id": tenant_id,
                    "billing_account_id": account_id,
                    "appointment_id": appointment_id,
                },
            )

        await db.commit()

    return {
        "ok": True,
        "billing_account_id": account_id,
        "tenant_id": tenant_id,
    }


@router.post("/admin/internal-billing/accounts/{account_id}/items/add")
async def billing_add_item(
    account_id: int,
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    item_code = str(payload.get("item_code") or "").strip().upper()
    quantity = _norm_money(payload.get("quantity") or 1)
    notes = str(payload.get("notes") or "").strip() or None

    if not item_code:
        raise HTTPException(status_code=400, detail="item_code required")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        acc_res = await db.execute(
            text("""
                SELECT id, tenant_id, currency
                FROM billing_accounts
                WHERE id = :id
                LIMIT 1
            """),
            {"id": account_id},
        )
        account = acc_res.mappings().first()
        if not account:
            raise HTTPException(status_code=404, detail="billing_account_not_found")

        cat_res = await db.execute(
            text("""
                SELECT
                    tenant_id,
                    department_code,
                    item_code,
                    item_name,
                    item_type,
                    base_price,
                    tax_rate,
                    is_active
                FROM billing_catalog
                WHERE tenant_id = :tenant_id
                  AND item_code = :item_code
                LIMIT 1
            """),
            {
                "tenant_id": account["tenant_id"],
                "item_code": item_code,
            },
        )
        cat = cat_res.mappings().first()
        if not cat:
            raise HTTPException(status_code=404, detail="billing_catalog_item_not_found")

        if not bool(cat["is_active"]):
            raise HTTPException(status_code=400, detail="billing_catalog_item_inactive")

        unit_price = _norm_money(cat["base_price"])
        gross_amount = round(quantity * unit_price, 2)
        tax_rate = _norm_money(cat["tax_rate"])
        tax_amount = round(gross_amount * (tax_rate / 100.0), 2)
        discount_amount = 0.0
        insurance_amount = 0.0
        patient_amount = round(gross_amount + tax_amount - discount_amount - insurance_amount, 2)

        await db.execute(
            text("""
                INSERT INTO billing_items (
                    billing_account_id,
                    tenant_id,
                    department_code,
                    item_type,
                    item_code,
                    item_name,
                    quantity,
                    unit_price,
                    gross_amount,
                    discount_amount,
                    tax_amount,
                    insurance_amount,
                    patient_amount,
                    status,
                    notes,
                    service_date,
                    updated_at
                )
                VALUES (
                    :billing_account_id,
                    :tenant_id,
                    :department_code,
                    :item_type,
                    :item_code,
                    :item_name,
                    :quantity,
                    :unit_price,
                    :gross_amount,
                    :discount_amount,
                    :tax_amount,
                    :insurance_amount,
                    :patient_amount,
                    'ACTIVE',
                    :notes,
                    NOW(),
                    NOW()
                )
            """),
            {
                "billing_account_id": account_id,
                "tenant_id": account["tenant_id"],
                "department_code": cat["department_code"],
                "item_type": cat["item_type"],
                "item_code": cat["item_code"],
                "item_name": cat["item_name"],
                "quantity": quantity,
                "unit_price": unit_price,
                "gross_amount": gross_amount,
                "discount_amount": discount_amount,
                "tax_amount": tax_amount,
                "insurance_amount": insurance_amount,
                "patient_amount": patient_amount,
                "notes": notes,
            },
        )

        await db.execute(
            text("""
                WITH sums AS (
                    SELECT
                        COALESCE(SUM(gross_amount), 0) AS subtotal,
                        COALESCE(SUM(discount_amount), 0) AS discount_total,
                        COALESCE(SUM(tax_amount), 0) AS tax_total,
                        COALESCE(SUM(insurance_amount), 0) AS insurance_total,
                        COALESCE(SUM(patient_amount), 0) AS patient_due_total
                    FROM billing_items
                    WHERE billing_account_id = :billing_account_id
                      AND status = 'ACTIVE'
                ),
                pays AS (
                    SELECT COALESCE(SUM(amount), 0) AS paid_total
                    FROM billing_payments
                    WHERE billing_account_id = :billing_account_id
                      AND payment_status = 'PAID'
                )
                UPDATE billing_accounts ba
                SET
                    subtotal = sums.subtotal,
                    discount_total = sums.discount_total,
                    tax_total = sums.tax_total,
                    insurance_total = sums.insurance_total,
                    patient_due_total = sums.patient_due_total,
                    paid_total = pays.paid_total,
                    balance_due = sums.patient_due_total - pays.paid_total,
                    updated_at = NOW()
                FROM sums, pays
                WHERE ba.id = :billing_account_id
            """),
            {"billing_account_id": account_id},
        )

        await db.execute(
            text("""
                UPDATE appointments a
                SET
                    billing_status = ba.status,
                    patient_due_total = ba.patient_due_total,
                    paid_total = ba.paid_total,
                    updated_at = NOW()
                FROM billing_accounts ba
                WHERE ba.id = :billing_account_id
                  AND a.billing_account_id = ba.id
            """),
            {"billing_account_id": account_id},
        )

        await db.commit()

    return {"ok": True, "billing_account_id": account_id, "item_code": item_code}


@router.post("/admin/internal-billing/accounts/{account_id}/payments/add")
async def billing_add_payment(
    account_id: int,
    payload: Dict[str, Any],
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    amount = _norm_money(payload.get("amount"))
    payment_method = str(payload.get("payment_method") or "CASH").strip().upper()
    payment_reference = str(payload.get("payment_reference") or "").strip() or None
    collected_by = str(payload.get("collected_by") or "").strip() or None
    notes = str(payload.get("notes") or "").strip() or None

    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        acc_res = await db.execute(
            text("""
                SELECT id, tenant_id, currency
                FROM billing_accounts
                WHERE id = :id
                LIMIT 1
            """),
            {"id": account_id},
        )
        account = acc_res.mappings().first()
        if not account:
            raise HTTPException(status_code=404, detail="billing_account_not_found")

        await db.execute(
            text("""
                INSERT INTO billing_payments (
                    billing_account_id,
                    tenant_id,
                    amount,
                    currency,
                    payment_method,
                    payment_reference,
                    payment_status,
                    collected_by,
                    paid_at,
                    notes
                )
                VALUES (
                    :billing_account_id,
                    :tenant_id,
                    :amount,
                    :currency,
                    :payment_method,
                    :payment_reference,
                    'PAID',
                    :collected_by,
                    NOW(),
                    :notes
                )
            """),
            {
                "billing_account_id": account_id,
                "tenant_id": account["tenant_id"],
                "amount": amount,
                "currency": account["currency"],
                "payment_method": payment_method,
                "payment_reference": payment_reference,
                "collected_by": collected_by,
                "notes": notes,
            },
        )

        await db.execute(
            text("""
                WITH sums AS (
                    SELECT
                        COALESCE(SUM(gross_amount), 0) AS subtotal,
                        COALESCE(SUM(discount_amount), 0) AS discount_total,
                        COALESCE(SUM(tax_amount), 0) AS tax_total,
                        COALESCE(SUM(insurance_amount), 0) AS insurance_total,
                        COALESCE(SUM(patient_amount), 0) AS patient_due_total
                    FROM billing_items
                    WHERE billing_account_id = :billing_account_id
                      AND status = 'ACTIVE'
                ),
                pays AS (
                    SELECT COALESCE(SUM(amount), 0) AS paid_total
                    FROM billing_payments
                    WHERE billing_account_id = :billing_account_id
                      AND payment_status = 'PAID'
                )
                UPDATE billing_accounts ba
                SET
                    subtotal = sums.subtotal,
                    discount_total = sums.discount_total,
                    tax_total = sums.tax_total,
                    insurance_total = sums.insurance_total,
                    patient_due_total = sums.patient_due_total,
                    paid_total = pays.paid_total,
                    balance_due = sums.patient_due_total - pays.paid_total,
                    status = CASE
                        WHEN (sums.patient_due_total - pays.paid_total) <= 0 THEN 'PAID'
                        WHEN pays.paid_total > 0 THEN 'PARTIALLY_PAID'
                        ELSE 'OPEN'
                    END,
                    updated_at = NOW()
                FROM sums, pays
                WHERE ba.id = :billing_account_id
            """),
            {"billing_account_id": account_id},
        )

        await db.execute(
            text("""
                UPDATE appointments a
                SET
                    billing_status = ba.status,
                    patient_due_total = ba.patient_due_total,
                    paid_total = ba.paid_total,
                    updated_at = NOW()
                FROM billing_accounts ba
                WHERE ba.id = :billing_account_id
                  AND a.billing_account_id = ba.id
            """),
            {"billing_account_id": account_id},
        )

        await db.commit()

    return {"ok": True, "billing_account_id": account_id, "amount": amount}


@router.post("/admin/internal-billing/accounts/{account_id}/invoice/create")
async def billing_create_invoice(
    account_id: int,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        acc_res = await db.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    patient_due_total,
                    currency
                FROM billing_accounts
                WHERE id = :id
                LIMIT 1
            """),
            {"id": account_id},
        )
        account = acc_res.mappings().first()
        if not account:
            raise HTTPException(status_code=404, detail="billing_account_not_found")

        invoice_number = _invoice_number_for_account(account_id)

        await db.execute(
            text("""
                INSERT INTO billing_invoices (
                    billing_account_id,
                    tenant_id,
                    invoice_number,
                    invoice_status,
                    issued_at,
                    total_amount,
                    currency,
                    updated_at
                )
                VALUES (
                    :billing_account_id,
                    :tenant_id,
                    :invoice_number,
                    'ISSUED',
                    NOW(),
                    :total_amount,
                    :currency,
                    NOW()
                )
                ON CONFLICT (tenant_id, invoice_number)
                DO UPDATE SET
                    total_amount = EXCLUDED.total_amount,
                    currency = EXCLUDED.currency,
                    updated_at = NOW()
            """),
            {
                "billing_account_id": account_id,
                "tenant_id": account["tenant_id"],
                "invoice_number": invoice_number,
                "total_amount": _norm_money(account["patient_due_total"]),
                "currency": account["currency"],
            },
        )

        await db.commit()

    return {
        "ok": True,
        "billing_account_id": account_id,
        "invoice_number": invoice_number,
    }


@router.get("/admin/internal-billing/accounts/list")
async def billing_accounts_list(
    tenant_id: str = "",
    status: str = "",
    q: str = "",
    limit: int = 200,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    status = (status or "").strip().upper()
    q = (q or "").strip()

    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        res = await db.execute(
            text("""
                SELECT
                    ba.id,
                    ba.tenant_id,
                    t.clinic_name,
                    ba.patient_id,
                    ba.appointment_id,
                    ba.encounter_type,
                    ba.source_module,
                    ba.status,
                    ba.currency,
                    ba.subtotal,
                    ba.discount_total,
                    ba.tax_total,
                    ba.insurance_total,
                    ba.patient_due_total,
                    ba.paid_total,
                    ba.balance_due,
                    ba.created_at,
                    ba.updated_at
                FROM billing_accounts ba
                LEFT JOIN tenants t
                  ON t.tenant_id = ba.tenant_id
                WHERE (:tenant_id = '' OR ba.tenant_id = :tenant_id)
                  AND (:status = '' OR ba.status = :status)
                  AND (
                        :q = ''
                        OR COALESCE(ba.patient_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(ba.appointment_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(ba.tenant_id, '') ILIKE '%' || :q || '%'
                        OR COALESCE(t.clinic_name, '') ILIKE '%' || :q || '%'
                      )
                ORDER BY ba.created_at DESC
                LIMIT :limit
            """),
            {
                "tenant_id": tenant_id,
                "status": status,
                "q": q,
                "limit": limit,
            },
        )

        items = [dict(r) for r in res.mappings().all()]

    return {"ok": True, "items": items}


@router.get("/admin/internal-billing/accounts/{account_id}")
async def billing_account_detail(
    account_id: int,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        acc_res = await db.execute(
            text("""
                SELECT *
                FROM billing_accounts
                WHERE id = :id
                LIMIT 1
            """),
            {"id": account_id},
        )
        account = acc_res.mappings().first()
        if not account:
            raise HTTPException(status_code=404, detail="billing_account_not_found")

        items_res = await db.execute(
            text("""
                SELECT *
                FROM billing_items
                WHERE billing_account_id = :billing_account_id
                ORDER BY id ASC
            """),
            {"billing_account_id": account_id},
        )

        payments_res = await db.execute(
            text("""
                SELECT *
                FROM billing_payments
                WHERE billing_account_id = :billing_account_id
                ORDER BY created_at DESC, id DESC
            """),
            {"billing_account_id": account_id},
        )

        invoices_res = await db.execute(
            text("""
                SELECT *
                FROM billing_invoices
                WHERE billing_account_id = :billing_account_id
                ORDER BY created_at DESC, id DESC
            """),
            {"billing_account_id": account_id},
        )

        items = [dict(r) for r in items_res.mappings().all()]
        payments = [dict(r) for r in payments_res.mappings().all()]
        invoices = [dict(r) for r in invoices_res.mappings().all()]

    return {
        "ok": True,
        "account": dict(account),
        "items": items,
        "payments": payments,
        "invoices": invoices,
    }

@router.get("/admin/internal-billing/accounts/find-by-appointment")
async def billing_find_by_appointment(
    tenant_id: str,
    appointment_id: str,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    require_platform_admin(x_admin_token)

    tenant_id = (tenant_id or "").strip()
    appointment_id = (appointment_id or "").strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if not appointment_id:
        raise HTTPException(status_code=400, detail="appointment_id required")

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        res = await db.execute(
            text("""
                SELECT
                    id,
                    tenant_id,
                    patient_id,
                    appointment_id,
                    encounter_type,
                    status,
                    currency,
                    subtotal,
                    patient_due_total,
                    paid_total,
                    balance_due,
                    created_at,
                    updated_at
                FROM billing_accounts
                WHERE tenant_id = :tenant_id
                  AND appointment_id = :appointment_id
                ORDER BY id DESC
                LIMIT 1
            """),
            {
                "tenant_id": tenant_id,
                "appointment_id": appointment_id,
            },
        )

        row = res.mappings().first()

    return {
        "ok": True,
        "found": bool(row),
        "item": dict(row) if row else None,
    }


from fastapi.responses import HTMLResponse

@router.get("/admin/internal-billing/invoices/{invoice_id}/print", response_class=HTMLResponse)
async def billing_invoice_print(
    invoice_id: int,
    token: str = Query(default=""),
    lang: str = Query(default="en"),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    received = (x_admin_token or token or "").strip()
    require_platform_admin(received)

    async with AsyncSessionLocal() as db:
        await ensure_internal_billing_tables(db)

        inv_res = await db.execute(
            text("""
                SELECT
                    bi.id,
                    bi.billing_account_id,
                    bi.tenant_id,
                    bi.invoice_number,
                    bi.invoice_status,
                    bi.issued_at,
                    bi.due_at,
                    bi.total_amount,
                    bi.currency,
                    ba.patient_id,
                    ba.appointment_id,
                    ba.encounter_type,
                    ba.subtotal,
                    ba.discount_total,
                    ba.tax_total,
                    ba.insurance_total,
                    ba.patient_due_total,
                    ba.paid_total,
                    ba.balance_due,
                    t.clinic_name
                FROM billing_invoices bi
                LEFT JOIN billing_accounts ba
                  ON ba.id = bi.billing_account_id
                LEFT JOIN tenants t
                  ON t.tenant_id = bi.tenant_id
                WHERE bi.id = :invoice_id
                LIMIT 1
            """),
            {"invoice_id": invoice_id},
        )

        invoice = inv_res.mappings().first()
        if not invoice:
            raise HTTPException(status_code=404, detail="billing_invoice_not_found")

        items_res = await db.execute(
            text("""
                SELECT
                    item_code,
                    item_name,
                    department_code,
                    quantity,
                    unit_price,
                    gross_amount,
                    discount_amount,
                    tax_amount,
                    insurance_amount,
                    patient_amount,
                    status
                FROM billing_items
                WHERE billing_account_id = :billing_account_id
                ORDER BY id ASC
            """),
            {"billing_account_id": invoice["billing_account_id"]},
        )
        items = [dict(r) for r in items_res.mappings().all()]

        payments_res = await db.execute(
            text("""
                SELECT
                    amount,
                    currency,
                    payment_method,
                    payment_reference,
                    payment_status,
                    paid_at
                FROM billing_payments
                WHERE billing_account_id = :billing_account_id
                ORDER BY created_at ASC, id ASC
            """),
            {"billing_account_id": invoice["billing_account_id"]},
        )
        payments = [dict(r) for r in payments_res.mappings().all()]

    ui_lang = "ar" if (lang or "").strip().lower() == "ar" else "en"
    is_rtl = ui_lang == "ar"

    TXT = {
        "en": {
            "invoice_title": "Invoice",
            "invoice_number": "Invoice #:",
            "status": "Status:",
            "issued": "Issued:",
            "tenant": "Tenant:",
            "patient_id": "Patient ID:",
            "appointment_id": "Appointment ID:",
            "encounter_type": "Encounter Type:",
            "currency": "Currency:",
            "items": "Items",
            "payments": "Payments",
            "totals": "Totals",
            "code": "Code",
            "name": "Name",
            "dept": "Dept",
            "qty": "Qty",
            "unit_price": "Unit Price",
            "patient_amount": "Patient Amount",
            "amount": "Amount",
            "method": "Method",
            "reference": "Reference",
            "paid_at": "Paid At",
            "subtotal": "Subtotal",
            "discount": "Discount",
            "tax": "Tax",
            "insurance": "Insurance",
            "patient_due": "Patient Due",
            "paid": "Paid",
            "balance": "Balance",
            "total_invoice": "Total Invoice",
            "print_btn": "Print / Save PDF",
            "no_items": "No items",
            "no_payments": "No payments",
        },
        "ar": {
            "invoice_title": "الفاتورة",
            "invoice_number": "رقم الفاتورة:",
            "status": "الحالة:",
            "issued": "تاريخ الإصدار:",
            "tenant": "العيادة:",
            "patient_id": "رقم المريض:",
            "appointment_id": "رقم الموعد:",
            "encounter_type": "نوع الزيارة:",
            "currency": "العملة:",
            "items": "البنود",
            "payments": "المدفوعات",
            "totals": "الإجماليات",
            "code": "الرمز",
            "name": "الاسم",
            "dept": "القسم",
            "qty": "الكمية",
            "unit_price": "سعر الوحدة",
            "patient_amount": "مبلغ المريض",
            "amount": "المبلغ",
            "method": "الطريقة",
            "reference": "المرجع",
            "paid_at": "وقت الدفع",
            "subtotal": "الإجمالي الفرعي",
            "discount": "الخصم",
            "tax": "الضريبة",
            "insurance": "التأمين",
            "patient_due": "المبلغ على المريض",
            "paid": "المدفوع",
            "balance": "المتبقي",
            "total_invoice": "إجمالي الفاتورة",
            "print_btn": "طباعة / حفظ PDF",
            "no_items": "لا توجد بنود",
            "no_payments": "لا توجد مدفوعات",
        }
    }

    T = TXT[ui_lang]

    def esc(v):
        if v is None:
            return "-"
        return str(v)

    items_rows = ""
    if items:
        for it in items:
            items_rows += f"""
            <tr>
              <td>{esc(it.get('item_code'))}</td>
              <td>{esc(it.get('item_name'))}</td>
              <td>{esc(it.get('department_code'))}</td>
              <td>{esc(it.get('quantity'))}</td>
              <td>{esc(it.get('unit_price'))}</td>
              <td>{esc(it.get('patient_amount'))}</td>
            </tr>
            """
    else:
        items_rows = f"""
        <tr><td colspan="6">{T["no_items"]}</td></tr>
        """

    payments_rows = ""
    if payments:
        for p in payments:
            payments_rows += f"""
            <tr>
              <td>{esc(p.get('amount'))}</td>
              <td>{esc(p.get('payment_method'))}</td>
              <td>{esc(p.get('payment_reference'))}</td>
              <td>{esc(p.get('paid_at'))}</td>
            </tr>
            """
    else:
        payments_rows = f"""
        <tr><td colspan="4">{T["no_payments"]}</td></tr>
        """

    html = f"""
    <!doctype html>
    <html lang="{ui_lang}" dir="{"rtl" if is_rtl else "ltr"}">
    <head>
      <meta charset="utf-8"/>
      <title>{T["invoice_title"]} {esc(invoice.get("invoice_number"))}</title>
      <style>
        body {{
          font-family: Arial, sans-serif;
          margin: 30px;
          color: #111;
          direction: {"rtl" if is_rtl else "ltr"};
        }}
        .top {{
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 24px;
        }}
        .box {{
          border: 1px solid #ddd;
          border-radius: 8px;
          padding: 12px;
          margin-bottom: 16px;
        }}
        table {{
          width: 100%;
          border-collapse: collapse;
          margin-top: 8px;
        }}
        th, td {{
          border: 1px solid #ddd;
          padding: 8px;
          text-align: {"right" if is_rtl else "left"};
          vertical-align: top;
        }}
        th {{
          background: #f7f7f7;
        }}
        .muted {{
          color: #666;
          font-size: 12px;
        }}
        .totals td {{
          font-weight: bold;
        }}
        .print-btn {{
          margin-bottom: 18px;
        }}
        @media print {{
          .print-btn {{
            display: none;
          }}
          body {{
            margin: 0;
          }}
        }}
      </style>
    </head>
    <body>
      <div class="print-btn">
        <button onclick="window.print()">{T["print_btn"]}</button>
      </div>

      <div class="top">
        <div>
          <h2>{esc(invoice.get("clinic_name"))}</h2>
          <div class="muted">{T["tenant"]} {esc(invoice.get("tenant_id"))}</div>
        </div>
        <div>
          <h3>{T["invoice_title"]}</h3>
          <div><b>{T["invoice_number"]}</b> {esc(invoice.get("invoice_number"))}</div>
          <div><b>{T["status"]}</b> {esc(invoice.get("invoice_status"))}</div>
          <div><b>{T["issued"]}</b> {esc(invoice.get("issued_at"))}</div>
        </div>
      </div>

      <div class="box">
        <div><b>{T["patient_id"]}</b> {esc(invoice.get("patient_id"))}</div>
        <div><b>{T["appointment_id"]}</b> {esc(invoice.get("appointment_id"))}</div>
        <div><b>{T["encounter_type"]}</b> {esc(invoice.get("encounter_type"))}</div>
        <div><b>{T["currency"]}</b> {esc(invoice.get("currency"))}</div>
      </div>

      <div class="box">
        <h3>{T["items"]}</h3>
        <table>
          <thead>
            <tr>
              <th>{T["code"]}</th>
              <th>{T["name"]}</th>
              <th>{T["dept"]}</th>
              <th>{T["qty"]}</th>
              <th>{T["unit_price"]}</th>
              <th>{T["patient_amount"]}</th>
            </tr>
          </thead>
          <tbody>
            {items_rows}
          </tbody>
        </table>
      </div>

      <div class="box">
        <h3>{T["payments"]}</h3>
        <table>
          <thead>
            <tr>
              <th>{T["amount"]}</th>
              <th>{T["method"]}</th>
              <th>{T["reference"]}</th>
              <th>{T["paid_at"]}</th>
            </tr>
          </thead>
          <tbody>
            {payments_rows}
          </tbody>
        </table>
      </div>

      <div class="box">
        <h3>{T["totals"]}</h3>
        <table class="totals">
          <tbody>
            <tr><td>{T["subtotal"]}</td><td>{esc(invoice.get("subtotal"))}</td></tr>
            <tr><td>{T["discount"]}</td><td>{esc(invoice.get("discount_total"))}</td></tr>
            <tr><td>{T["tax"]}</td><td>{esc(invoice.get("tax_total"))}</td></tr>
            <tr><td>{T["insurance"]}</td><td>{esc(invoice.get("insurance_total"))}</td></tr>
            <tr><td>{T["patient_due"]}</td><td>{esc(invoice.get("patient_due_total"))}</td></tr>
            <tr><td>{T["paid"]}</td><td>{esc(invoice.get("paid_total"))}</td></tr>
            <tr><td>{T["balance"]}</td><td>{esc(invoice.get("balance_due"))}</td></tr>
            <tr><td>{T["total_invoice"]}</td><td>{esc(invoice.get("total_amount"))}</td></tr>
          </tbody>
        </table>
      </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html)