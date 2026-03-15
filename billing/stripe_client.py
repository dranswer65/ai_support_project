import os
import stripe

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

APP_BASE_URL = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8010").strip()

PRICE_BY_PLAN = {
    "starter": (os.getenv("STRIPE_PRICE_STARTER") or "").strip(),
    "growth": (os.getenv("STRIPE_PRICE_GROWTH") or "").strip(),
    "enterprise": (os.getenv("STRIPE_PRICE_ENTERPRISE") or "").strip(),
}