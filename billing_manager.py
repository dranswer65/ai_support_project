import json
import os
from pathlib import Path
from datetime import datetime, timezone

# -----------------------------
# Files
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent
BILLING_DIR = BASE_DIR / "billing"
BILLING_DIR.mkdir(parents=True, exist_ok=True)

SUBSCRIPTIONS_FILE = BILLING_DIR / "subscriptions.json"
PAYMENTS_FILE = BILLING_DIR / "payments.json"


# -----------------------------
# Safe JSON helpers
# -----------------------------
def load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


# -----------------------------
# Subscription store
# -----------------------------
def get_subscription(client_name: str) -> dict:
    data = load_json(SUBSCRIPTIONS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    return data.get(client_name, {})


def set_subscription(client_name: str, sub: dict) -> None:
    data = load_json(SUBSCRIPTIONS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    data[client_name] = {
        **sub,
        "updated_utc": now_utc_iso(),
    }
    save_json(SUBSCRIPTIONS_FILE, data)


def set_subscription_active(client_name: str, active: bool, reason: str = "") -> None:
    cur = get_subscription(client_name) or {}
    cur["active"] = bool(active)
    cur["reason"] = reason
    set_subscription(client_name, cur)


# -----------------------------
# Payment audit trail (optional)
# -----------------------------
def log_payment(event: str, client_name: str, meta: dict | None = None) -> None:
    payments = load_json(PAYMENTS_FILE, [])
    if not isinstance(payments, list):
        payments = []

    payments.append({
        "ts_utc": now_utc_iso(),
        "event": event,
        "client": client_name,
        "meta": meta or {},
    })

    # keep last 5000
    payments = payments[-5000:]
    save_json(PAYMENTS_FILE, payments)
