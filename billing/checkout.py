from billing.stripe_client import stripe, APP_BASE_URL, PRICE_BY_PLAN


def create_checkout_session(*, tenant_id: str, clinic_name: str, plan_code: str) -> dict:
    price_id = PRICE_BY_PLAN.get(plan_code, "").strip()
    if not price_id:
        raise ValueError(f"Missing Stripe price for plan: {plan_code}")

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_BASE_URL}/billing/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_BASE_URL}/billing/checkout/cancel",
        client_reference_id=tenant_id,
        metadata={
            "tenant_id": tenant_id,
            "clinic_name": clinic_name,
            "plan_code": plan_code,
        },
        subscription_data={
            "metadata": {
                "tenant_id": tenant_id,
                "clinic_name": clinic_name,
                "plan_code": plan_code,
            }
        },
    )

    return {
        "id": session["id"],
        "url": session["url"],
    }