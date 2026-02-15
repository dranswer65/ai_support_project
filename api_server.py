from __future__ import annotations

import os
import re
import io
import csv
import json
import math
from pathlib import Path
from typing import Any, List, Tuple
from datetime import datetime, timezone

import bcrypt
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from fastapi import Request
from fastapi.responses import PlainTextResponse
import requests

# OpenAI
from openai import OpenAI
import time
from monitoring import log_error, get_errors, clear_errors
from fastapi import Query



# Stripe optional (won’t crash if missing)
try:
    import stripe
except Exception:
    stripe = None

load_dotenv()
def data_root() -> Path:
    # Railway volume mount path you choose (example: /app/data)
    p = os.getenv("SP_DATA_DIR", "").strip()
    if p:
        return Path(p)
    return Path(__file__).resolve().parent
BASE_DIR = data_root()
CLIENTS_DIR = BASE_DIR / "clients"


# Billing manager (Day 46)
from billing_manager import (
    get_subscription,
    set_subscription,
    set_subscription_active,
    log_payment,
    load_json as bm_load_json,
    SUBSCRIPTIONS_FILE,
)

# ============================================================
# App
# ============================================================
app = FastAPI(title="SupportPilot API")

@app.middleware("http")
async def crash_guard(request: Request, call_next):
    try:
        response = await call_next(request)
        # If server returned 500, still log it
        if response.status_code >= 500:
            log_error(
                ERRORS_FILE,
                where="middleware",
                message=f"HTTP {response.status_code}",
                request_path=str(request.url.path),
                method=request.method,
                client_ip=(request.client.host if request.client else ""),
                extra={"query": str(request.url.query)},
            )
        return response

    except Exception as e:
        # Log full crash
        log_error(
            ERRORS_FILE,
            where="unhandled_exception",
            message="Unhandled exception in API",
            request_path=str(request.url.path),
            method=request.method,
            client_ip=(request.client.host if request.client else ""),
            extra={"query": str(request.url.query)},
            exc=e,
        )
        # Return safe JSON (no stacktrace to user)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


# WhatsApp Cloud API Webhook (Meta)
# ============================================================
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

@app.get("/whatsapp/webhook", response_class=PlainTextResponse)
def whatsapp_webhook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """
    Meta webhook verification:
    Must return hub.challenge as plain text with 200 OK.
    """
    if not WHATSAPP_VERIFY_TOKEN:
        raise HTTPException(500, "WHATSAPP_VERIFY_TOKEN not configured.")

    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return hub_challenge or ""

    raise HTTPException(403, "Verification failed")

@app.get("/debug/token")
def debug_token():
    return {"WHATSAPP_VERIFY_TOKEN": os.getenv("WHATSAPP_VERIFY_TOKEN")}

# ============================================================
# ENV helpers (supports SP_ and non-SP names)
# ============================================================
def env_any(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n, "")
        if v and str(v).strip():
            return str(v).strip()
    return default

DEFAULT_BRAND = env_any("SP_BRAND_NAME","BRAND_NAME",default="SupportPilot")
TOKEN_PRICE_PER_1K = float(env_any("SP_TOKEN_PRICE_PER_1K","TOKEN_PRICE_PER_1K",default="0.002"))

OPENAI_API_KEY = env_any("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Admin token
SP_ADMIN_TOKEN = env_any("SP_ADMIN_TOKEN","ADMIN_TOKEN")

# Stripe keys (keep BOTH styles)
STRIPE_SECRET_KEY = env_any("SP_STRIPE_SECRET_KEY","STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env_any("SP_STRIPE_WEBHOOK_SECRET","STRIPE_WEBHOOK_SECRET")

STRIPE_PRICE_BASIC = env_any("SP_STRIPE_PRICE_BASIC","STRIPE_PRICE_BASIC")
STRIPE_PRICE_PRO   = env_any("SP_STRIPE_PRICE_PRO","STRIPE_PRICE_PRO")

SP_PUBLIC_BASE_URL = env_any("SP_PUBLIC_BASE_URL","SP_PUBLIC_URL","PUBLIC_BASE_URL",default="http://localhost:8000")

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "").strip()
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "").strip()


# ============================================================
# Paths
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CLIENTS_DIR = BASE_DIR / "clients"
USAGE_DIR = BASE_DIR / "usage"
AUDIT_DIR = BASE_DIR / "audit"

AUDIT_FILE = AUDIT_DIR / "audit_log.json"
USAGE_FILE = USAGE_DIR / "usage_log.json"
BACKUP_DIR = BASE_DIR / "backups"
ERRORS_DIR = BASE_DIR / "logs"
ERRORS_FILE = ERRORS_DIR / "errors.json"
HEALTH_FILE = ERRORS_DIR / "health.json"


# ============================================================
# Models
# ============================================================
class ChatRequest(BaseModel):
    client_name: str
    question: str
    tone: str = "formal"
    language: str = "en" # en/ar


class ChatResponse(BaseModel):
    answer: str
    tokens: int
    cost: float


class ClientStatusRequest(BaseModel):
    client_name: str
    active: bool
    confirm: str = ""  # Day44 dangerous confirmation


class ClientSettingsUpdateRequest(BaseModel):
    client_name: str
    settings: dict


class CheckoutRequest(BaseModel):
    client_name: str
    plan: str = "basic"  # basic/pro
    customer_email: str | None = None
class BackupCreateRequest(BaseModel):
    client_name: str
    include_chat_logs: bool = True


class BackupRestoreRequest(BaseModel):
    client_name: str
    backup_id: str
    confirm: str = ""   # dangerous confirmation

class BillingManualRequest(BaseModel):
    client_name: str
    plan: str = "basic"
    active: bool = True
    reason: str = "manual_dev"


# ============================================================
# Safe JSON helpers (never crash on empty/corrupt JSON)
# ============================================================
def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Client paths
# ============================================================
def client_settings_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "config" / "settings.json"


def client_key_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "config" / "api_key.json"


def client_admin_path(client_name: str) -> Path:
    # per-client admin token storage (Option B)
    return CLIENTS_DIR / client_name / "config" / "admin_users.json"


def client_embeddings_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "knowledge" / "embeddings.json"


def client_prompt_path(client_name: str) -> Path:
    # per-client override:
    p1 = CLIENTS_DIR / client_name / "prompts" / "support_agent.txt"
    if p1.exists():
        return p1
    # default global prompt:
    return BASE_DIR / "prompts" / "support_agent.txt"


# ============================================================
# Loaders
# ============================================================
def load_client_settings(client_name: str) -> dict:
    path = client_settings_path(client_name)
    if not path.exists():
        raise HTTPException(404, f"settings.json not found for client: {client_name}")
    data = load_json(path, {})
    if not isinstance(data, dict):
        raise HTTPException(500, "Invalid settings.json format (must be JSON object).")
    return data


def load_client_key_data(client_name: str) -> dict:
    path = client_key_path(client_name)
    if not path.exists():
        raise HTTPException(404, f"api_key.json not found for client: {client_name}")
    data = load_json(path, {})
    if not isinstance(data, dict):
        raise HTTPException(500, "Invalid api_key.json format (must be JSON object).")
    return data


def load_client_embeddings(client_name: str) -> list:
    path = client_embeddings_path(client_name)
    if not path.exists():
        raise HTTPException(404, f"embeddings.json not found for client: {client_name}")
    data = load_json(path, [])
    if not isinstance(data, list):
        raise HTTPException(500, "Invalid embeddings.json format (must be JSON list).")
    return data


def load_support_prompt(client_name: str) -> str:
    path = client_prompt_path(client_name)
    if not path.exists():
        return f"You are a professional customer support executive for {DEFAULT_BRAND}."
    return path.read_text(encoding="utf-8")


# ============================================================
# Audit + Usage logging
# ============================================================
def log_audit(event: str, actor: str = "system", meta: dict | None = None) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    logs = load_json(AUDIT_FILE, [])
    if not isinstance(logs, list):
        logs = []
    logs.append(
        {
            "ts_utc": now_utc_iso(),
            "event": event,
            "actor": actor,
            "meta": meta or {},
        }
    )
    save_json(AUDIT_FILE, logs[-5000:])


def log_usage(client_name: str, tokens: int, cost: float) -> None:
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    logs = load_json(USAGE_FILE, [])
    if not isinstance(logs, list):
        logs = []
    logs.append(
        {
            "client": client_name,
            "tokens": int(tokens),
            "cost": float(cost),
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
    )
    save_json(USAGE_FILE, logs[-100000:])
def record_health(ok: bool, note: str = ""):
    ERRORS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "last_check_utc": datetime.now(timezone.utc).isoformat(),
        "ok": bool(ok),
        "note": note,
        "uptime_seconds": int(time.time() - APP_START_TS),
    }
    HEALTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def wa_send_text(to_number: str, text: str):
    if not WA_ACCESS_TOKEN or not WA_PHONE_NUMBER_ID:
        raise HTTPException(500, "WhatsApp env vars missing (WA_ACCESS_TOKEN / WA_PHONE_NUMBER_ID).")

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 400:
        raise HTTPException(500, f"WhatsApp send failed: {r.status_code} {r.text}")
    return r.json()




# ============================================================
# API key verification (bcrypt hash)
# ============================================================
def verify_api_key(client_name: str, user_key: str) -> bool:
    data = load_client_key_data(client_name)  # must read clients/<client>/config/api_key.json

    plain = (data.get("api_key") or "").strip()
    if plain:
        return user_key == plain

    hashed = (data.get("api_key_hash") or "").strip()
    if hashed:
        try:
            return bcrypt.checkpw(user_key.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False

    # If neither exists, server is not configured for this client
    raise HTTPException(500, "Client API key not configured on server.")

# ============================================================
# Admin auth helpers (Option B + super token)
# ============================================================
def parse_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()


def load_client_admin_token(client_name: str) -> str:
    """
    Reads: clients/<client>/config/admin_users.json
    Expected shape:
      { "admin_token": "spdemo_admin_token_2026", ... }
    """
    data = load_json(client_admin_path(client_name), {})
    if not isinstance(data, dict):
        return ""
    return (data.get("admin_token") or "").strip()


def require_admin_token_any(authorization: str | None):
    """
    Super-admin token (global):
      SP_ADMIN_TOKEN in .env
    """
    token = os.getenv("SP_ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(500, "Admin token not configured on server (.env SP_ADMIN_TOKEN).")

    provided = parse_bearer(authorization)
    if not provided:
        raise HTTPException(401, "Missing Authorization header. Use: Bearer <token>")

    if provided != token:
        raise HTTPException(403, "Invalid admin token.")


def require_client_admin_token(client_name: str, authorization: str | None):
    """
    Accept either:
    - per-client token from clients/<client>/config/admin_users.json
    - OR super-admin token from .env SP_ADMIN_TOKEN (override)
    """
    provided = parse_bearer(authorization)
    if not provided:
        raise HTTPException(401, "Missing Authorization header. Use: Bearer <token>")

    # Super-admin override
    super_token = os.getenv("SP_ADMIN_TOKEN", "").strip()
    if super_token and provided == super_token:
        return

    # Client token
    client_token = load_client_admin_token(client_name)
    if not client_token:
        raise HTTPException(500, f"Client admin token not configured for {client_name} (admin_users.json).")

    if provided != client_token:
        raise HTTPException(403, "Invalid client admin token.")


# ============================================================
# Subscription gate (Day 46 billing)
# ============================================================
def require_active_subscription(client_name: str):
    sub = get_subscription(client_name) or {}
    if not sub.get("active", False):
        raise HTTPException(
            402,
            "Subscription inactive. Please activate billing for this client.",
        )


# ============================================================
# Text helpers (anti-loop basics + Arabic friendly)
# ============================================================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_greeting(text: str) -> bool:
    t = normalize(text)
    return t in {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}


def is_thanks(text: str) -> bool:
    t = normalize(text)
    return t in {"thanks", "thank you", "thx", "thanks a lot", "thank you very much"}


def looks_meaningful(text: str) -> bool:
    # English OR Arabic letters
    return bool(re.search(r"[A-Za-z\u0600-\u06FF]{2,}", text or ""))


# ============================================================
# RAG helpers
# ============================================================
def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search_knowledge(query: str, items: list, top_k: int = 3) -> List[Tuple[float, dict]]:
    if client is None:
        raise HTTPException(500, "OPENAI_API_KEY not configured on server.")

    q_emb = client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    ).data[0].embedding

    scores: List[Tuple[float, dict]] = []
    for it in items:
        emb = it.get("embedding")
        if not isinstance(emb, list):
            continue
        s = cosine_similarity(q_emb, emb)
        scores.append((s, it))

    scores.sort(key=lambda x: x[0], reverse=True)
    return scores[:top_k]


def build_system_prompt(base_prompt: str, brand: str, client_name: str, language: str, context_bullets: str) -> str:
    lang_line = "Answer in Arabic." if language == "ar" else "Answer in English."
    return f"""
{base_prompt}

---
BRAND = {brand}
CLIENT = {client_name}
{lang_line}

Rules:
- Do NOT greet twice in the same conversation.
- Do NOT loop repeating the same request forever.
- If asking for Order ID, ask at most twice. If still not provided, offer escalation.

Context:
{context_bullets}
""".strip()

# ============================================================
# (CONTINUE PART 3) Admin auth helpers
# ============================================================
def require_admin_token_any(authorization: str | None):
    token = os.getenv("SP_ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(500, "Admin token not configured on server (.env SP_ADMIN_TOKEN).")

    provided = parse_bearer(authorization)
    if not provided:
        raise HTTPException(401, "Missing Authorization header. Use: Bearer <token>")
    if provided != token:
        raise HTTPException(403, "Invalid admin token.")


def require_client_admin_token(client_name: str, authorization: str | None):
    """
    Accept either:
    - per-client token from clients/<client>/config/admin_users.json
    - OR super-admin token from .env SP_ADMIN_TOKEN (override)
    """
    provided = parse_bearer(authorization)
    if not provided:
        raise HTTPException(401, "Missing Authorization header. Use: Bearer <token>")

    super_token = os.getenv("SP_ADMIN_TOKEN", "").strip()
    if super_token and provided == super_token:
        return

    client_token = load_client_admin_token(client_name)
    if not client_token:
        raise HTTPException(500, f"Client admin token not configured for {client_name} (admin_users.json).")

    if provided != client_token:
        raise HTTPException(403, "Invalid client admin token.")


# ============================================================
# Subscription gate (Day 46)
# ============================================================
def require_active_subscription(client_name: str):
    sub = get_subscription(client_name) or {}
    if not sub.get("active", False):
        raise HTTPException(402, "Subscription inactive. Please activate billing for this client.")


# ============================================================
# Text helpers (Arabic preserved)
# ============================================================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_greeting(text: str) -> bool:
    t = normalize(text)
    return t in {"hi", "hello", "hey", "good morning", "good evening", "good afternoon"}


def is_thanks(text: str) -> bool:
    t = normalize(text)
    return t in {"thanks", "thank you", "thx", "thanks a lot", "thank you very much"}


def looks_meaningful(text: str) -> bool:
    return bool(re.search(r"[A-Za-z\u0600-\u06FF]{2,}", text or ""))


# ============================================================
# RAG helpers
# ============================================================
def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search_knowledge(query: str, items: list, top_k: int = 3) -> List[Tuple[float, dict]]:
    if client is None:
        raise HTTPException(500, "OPENAI_API_KEY not configured on server.")

    q_emb = client.embeddings.create(model="text-embedding-3-small", input=query).data[0].embedding

    scores: List[Tuple[float, dict]] = []
    for it in items:
        emb = it.get("embedding")
        if not isinstance(emb, list):
            continue
        s = cosine_similarity(q_emb, emb)
        scores.append((s, it))

    scores.sort(key=lambda x: x[0], reverse=True)
    return scores[:top_k]


def build_system_prompt(base_prompt: str, brand: str, client_name: str, language: str, context_bullets: str) -> str:
    lang_line = "Answer in Arabic." if language == "ar" else "Answer in English."
    return f"""
{base_prompt}

---
BRAND = {brand}
CLIENT = {client_name}
{lang_line}

Rules:
- Do NOT greet twice in the same conversation.
- Do NOT loop repeating the same request forever.
- If asking for Order ID, ask at most twice. If still not provided, offer escalation.

Context:
{context_bullets}
""".strip()


# ============================================================
# CHAT endpoint (subscription-gated)
# ============================================================
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        settings = load_client_settings(req.client_name)

        if not bool(settings.get("active", True)):
            raise HTTPException(403, "Client disabled")
        # Load stored client API key (secure)
        key_data = load_client_key_data(req.client_name)

        stored_hash = (key_data.get("api_key_hash") or "").strip()
        if not stored_hash:
            raise HTTPException(500, "Client API key not configured on server.")


        # Day 46 gate (only allows paid/active clients)
        require_active_subscription(req.client_name)

        brand = (settings.get("brand_name") or DEFAULT_BRAND).strip() or DEFAULT_BRAND
        language = (req.language or settings.get("language") or "en").strip().lower()
        if language not in {"en", "ar"}:
            language = "en"

        # Fast replies (no OpenAI)
        if is_greeting(req.question):
            opening = (
                f"مرحبًا! شكرًا لتواصلك مع {brand}. كيف يمكنني مساعدتك اليوم؟"
                if language == "ar"
                else f"Hello! Thank you for contacting {brand}. How may I assist you today?"
            )
            return ChatResponse(answer=opening, tokens=0, cost=0.0)

        if is_thanks(req.question):
            closing = (
                "على الرحب والسعة. هل هناك أي شيء آخر يمكنني مساعدتك به اليوم؟"
                if language == "ar"
                else "You’re most welcome. Is there anything else I can help you with today?"
            )
            return ChatResponse(answer=closing, tokens=0, cost=0.0)

        if not looks_meaningful(req.question):
            msg = (
                "شكرًا لرسالتك. لتقديم المساعدة بشكل أفضل، هل يمكنك توضيح التفاصيل أكثر؟"
                if language == "ar"
                else "Thanks for your message. To assist you better, could you please share a bit more detail?"
            )
            return ChatResponse(answer=msg, tokens=0, cost=0.0)

        # RAG
        items = load_client_embeddings(req.client_name)
        results = search_knowledge(req.question, items, top_k=3)

        threshold = float(settings.get("escalation_threshold", 0.38))
        filtered = [(s, it) for s, it in results if s >= threshold]

        if not filtered:
            msg = (
                "شكرًا لك. لتقديم المساعدة بشكل صحيح، هل يمكنك مشاركة مزيد من التفاصيل؟"
                if language == "ar"
                else "Thank you. To assist you properly, could you please provide a little more information about your request?"
            )
            return ChatResponse(answer=msg, tokens=0, cost=0.0)

        bullets = ""
        for s, it in filtered:
            t = (it.get("text") or "").strip()
            if t:
                bullets += f"- {t}\n"

        base_prompt = load_support_prompt(req.client_name)
        system_prompt = build_system_prompt(base_prompt, brand, req.client_name, language, bullets)

        resp = client.chat.completions.create(
            model=os.getenv("SP_CHAT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.question},
            ],
            temperature=0.2,
        )

        answer = (resp.choices[0].message.content or "").strip()
        tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
        cost = round((tokens / 1000.0) * TOKEN_PRICE_PER_1K, 6)

        log_usage(req.client_name, tokens=tokens, cost=cost)

        if not answer:
            answer = (
                "عذرًا، لم أتمكن من إنشاء رد الآن. حاول مرة أخرى بعد قليل."
                if language == "ar"
                else "Sorry, I couldn't generate a response right now. Please try again in a moment."
            )

        return ChatResponse(answer=answer, tokens=tokens, cost=cost)

    except HTTPException:
        raise
    except Exception as e:
        print("SERVER ERROR:", repr(e))
        raise HTTPException(500, "Internal Server Error")

def send_whatsapp_message(to_number: str, message: str):
    try:
        phone_id = os.getenv("WA_PHONE_NUMBER_ID")
        token = os.getenv("WA_ACCESS_TOKEN")

        url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message}
        }

        r = requests.post(url, headers=headers, json=payload)
        print("WhatsApp send:", r.text)

    except Exception as e:
        print("WhatsApp send error:", e)

# ============================================================
# WhatsApp Webhook
# ============================================================
@app.get("/whatsapp/webhook", response_class=PlainTextResponse)
def whatsapp_webhook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if not WHATSAPP_VERIFY_TOKEN:
        raise HTTPException(500, "WHATSAPP_VERIFY_TOKEN not configured.")

    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return hub_challenge or ""

    raise HTTPException(403, "Verification failed")

# ============================================================
# WhatsApp Webhook — RECEIVE messages (POST)
# ============================================================
@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request):

    try:
        body = await request.json()
        print("WHATSAPP EVENT:", json.dumps(body, indent=2))



        # Extract message safely
        entry = body.get("entry", [])
        if not entry:
            return {"ok": True}

        changes = entry[0].get("changes", [])
        if not changes:
            return {"ok": True}

        value = changes[0].get("value", {})
        messages = value.get("messages")

        if not messages:
            return {"ok": True}

        message = messages[0]
        from_number = message.get("from")
        text = message.get("text", {}).get("body", "")

        print(f"Incoming WhatsApp message from {from_number}: {text}")

        # Simple reply test
        if text:
            wa_send_text(from_number, "Hello 👋 message received!")

        return {"status": "received"}

    except Exception as e:
        print("Webhook error:", e)
        return {"ok": False}

# ============================================================
# WhatsApp Webhook (REAL AI reply)
# ============================================================

@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):

    data = await request.json()
    print("WHATSAPP EVENT:", data)

    try:
        entry = data.get("entry",[{}])[0]
        changes = entry.get("changes",[{}])[0]
        value = changes.get("value",{})

        if "messages" not in value:
            return {"ok":True}

        msg = value["messages"][0]
        from_number = msg["from"]
        text = msg.get("text",{}).get("body","")

        print("MESSAGE:", text)

        # ===== AI reply =====
        reply = "AI working..."

        try:
            payload = ChatRequest(
                client_name="supportpilot_demo",
                api_key=os.getenv("WHATSAPP_CLIENT_API_KEY",""),
                question=text,
                tone="formal",
                language="en"
            )
            res = chat(payload)
            reply = res.answer

        except Exception as e:
            print("AI ERROR:", e)
            reply = "AI temporarily unavailable."

        send_whatsapp_message(from_number, reply)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return {"ok":True}



# ============================================================
# Stripe Checkout (create session)
# ============================================================
@app.post("/billing/checkout")
def billing_checkout(payload: CheckoutRequest, authorization: str | None = Header(default=None)):
    """
    Client admin creates a checkout session for their own client.
    Uses env aliases:
      SP_STRIPE_SECRET_KEY or STRIPE_SECRET_KEY
      SP_STRIPE_PRICE_BASIC/PRO or STRIPE_PRICE_BASIC/PRO
    """
    secret_key = (os.getenv("SP_STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not secret_key:
        raise HTTPException(500, "STRIPE_SECRET_KEY not configured.")
    stripe.api_key = secret_key

    require_client_admin_token(payload.client_name, authorization)

    plan = (payload.plan or "basic").lower().strip()
    if plan not in {"basic", "pro"}:
        raise HTTPException(400, "Invalid plan. Use basic or pro.")

    price_basic = (os.getenv("SP_STRIPE_PRICE_BASIC") or os.getenv("STRIPE_PRICE_BASIC") or "").strip()
    price_pro = (os.getenv("SP_STRIPE_PRICE_PRO") or os.getenv("STRIPE_PRICE_PRO") or "").strip()
    price_id = price_basic if plan == "basic" else price_pro

    if not price_id:
        raise HTTPException(500, f"Stripe price ID missing for plan: {plan} (set STRIPE_PRICE_BASIC/PRO).")

    public_base = (os.getenv("SP_PUBLIC_BASE_URL") or os.getenv("SP_PUBLIC_URL") or "").strip()
    if not public_base:
        public_base = "http://localhost:8000"

    try:
        sess = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{public_base}/billing/success?client={payload.client_name}",
            cancel_url=f"{public_base}/billing/cancel?client={payload.client_name}",
            customer_email=payload.customer_email or None,
            metadata={"client_name": payload.client_name, "plan": plan},
        )
    except Exception as e:
        raise HTTPException(500, f"Stripe checkout error: {e}")

    # Save pending subscription record
    set_subscription(
        payload.client_name,
        {
            "active": False,
            "plan": plan,
            "stripe_checkout_session_id": sess.get("id", ""),
            "stripe_customer_id": "",
            "stripe_subscription_id": "",
            "reason": "pending_checkout",
            "updated_utc": now_utc_iso(),
        },
    )

    log_payment("checkout_created", payload.client_name, {"plan": plan, "session_id": sess.get("id", "")})

    return {"checkout_url": sess.get("url", ""), "session_id": sess.get("id", "")}


# ============================================================
# Stripe Webhook (activate/deactivate)
# ============================================================
@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    secret_key = (os.getenv("SP_STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY") or "").strip()
    webhook_secret = (os.getenv("SP_STRIPE_WEBHOOK_SECRET") or os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret_key:
        raise HTTPException(500, "STRIPE_SECRET_KEY not configured.")
    if not webhook_secret:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET not configured.")

    stripe.api_key = secret_key

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as e:
        return JSONResponse({"error": f"Invalid signature: {e}"}, status_code=400)

    etype = event["type"]
    data = event["data"]["object"]

    # Checkout completed => active
    if etype == "checkout.session.completed":
        meta = data.get("metadata") or {}
        client_name = (meta.get("client_name") or "").strip()
        plan = (meta.get("plan") or "basic").strip()
        if client_name:
            set_subscription(
                client_name,
                {
                    "active": True,
                    "plan": plan,
                    "stripe_checkout_session_id": data.get("id", ""),
                    "stripe_customer_id": data.get("customer", ""),
                    "stripe_subscription_id": data.get("subscription", ""),
                    "reason": "active",
                    "updated_utc": now_utc_iso(),
                },
            )
            log_payment("checkout_completed", client_name, {"event": etype})

    # Subscription canceled/unpaid => inactive
    if etype in {"customer.subscription.deleted", "invoice.payment_failed"}:
        client_name = ""
        meta = data.get("metadata") or {}
        if meta.get("client_name"):
            client_name = meta["client_name"]

        if not client_name:
            sub_id = data.get("id") or data.get("subscription")
            if sub_id:
                # brute search local store
                from billing_manager import load_json as _lz, SUBSCRIPTIONS_FILE as _sf

                allsubs = _lz(_sf, {})
                if isinstance(allsubs, dict):
                    for c, s in allsubs.items():
                        if (s or {}).get("stripe_subscription_id") == sub_id:
                            client_name = c
                            break

        if client_name:
            set_subscription_active(client_name, False, reason=etype)
            log_payment("subscription_deactivated", client_name, {"event": etype})

    return {"ok": True}


# ============================================================
# Admin endpoints (Option B SaaS + super-admin)
# ============================================================
@app.get("/admin/clients")
def admin_list_clients(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)
    if not CLIENTS_DIR.exists():
        return {"clients": []}

    clients = [p.name for p in CLIENTS_DIR.iterdir() if p.is_dir()]
    log_audit("admin_list_clients", actor="admin_token", meta={"count": len(clients)})
    return {"clients": clients}


@app.post("/admin/client/status")
def admin_set_client_status(payload: ClientStatusRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    path = client_settings_path(payload.client_name)
    if not path.exists():
        raise HTTPException(404, "Client settings.json not found")

    expected = f"{'DISABLE' if not payload.active else 'ENABLE'} {payload.client_name}"
    if (payload.confirm or "").strip().upper() != expected.upper():
        raise HTTPException(400, f"Confirmation required. Set confirm to: {expected}")

    settings = load_json(path, {})
    if not isinstance(settings, dict):
        raise HTTPException(500, "Invalid settings.json format.")

    settings["active"] = bool(payload.active)
    save_json(path, settings)

    log_audit("admin_set_client_status", actor="admin_token", meta={"client": payload.client_name, "active": payload.active})
    return {"ok": True, "client": payload.client_name, "active": payload.active}


@app.post("/admin/client/update")
def admin_update_client_settings(payload: ClientSettingsUpdateRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    path = client_settings_path(payload.client_name)
    if not path.exists():
        raise HTTPException(404, "Client settings.json not found")

    if not isinstance(payload.settings, dict):
        raise HTTPException(400, "settings must be a JSON object")

    save_json(path, payload.settings)
    log_audit("admin_update_client_settings", actor="admin_token", meta={"client": payload.client_name})
    return {"ok": True}


@app.get("/admin/billing/status")
def admin_billing_status(client_name: str, authorization: str | None = Header(default=None)):
    require_client_admin_token(client_name, authorization)
    sub = get_subscription(client_name) or {}
    return {"client": client_name, "subscription": sub}


@app.get("/admin/billing/export", response_class=PlainTextResponse)
def admin_export_billing(client_name: str, authorization: str | None = Header(default=None)):
    require_client_admin_token(client_name, authorization)

    usage = load_json(USAGE_FILE, [])
    if not isinstance(usage, list):
        usage = []

    rows = [r for r in usage if (str(r.get("client", "")).lower() == client_name.lower())]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date", "client", "tokens", "cost"])
    writer.writeheader()

    for r in rows:
        writer.writerow(
            {
                "date": r.get("date", ""),
                "client": r.get("client", ""),
                "tokens": r.get("tokens", 0),
                "cost": r.get("cost", 0),
            }
        )
   

    log_audit("admin_export_billing", actor="admin_token", meta={"client": client_name, "rows": len(rows)})
    return output.getvalue()
# ============================================================
# Day 46/47 helper — Manual billing control (DEV / Admin)
# ============================================================

@app.post("/admin/billing/manual")
def admin_billing_manual(payload: BillingManualRequest, authorization: str | None = Header(default=None)):
    """
    DEV helper: manually activate/deactivate subscription.
    Uses client admin token OR super admin token (via require_client_admin_token).
    """
    require_client_admin_token(payload.client_name, authorization)

    plan = (payload.plan or "basic").strip().lower()
    if plan not in {"basic", "pro"}:
        raise HTTPException(400, "plan must be 'basic' or 'pro'")

    set_subscription(
        payload.client_name,
        {
            "active": bool(payload.active),
            "plan": plan,
            "reason": payload.reason,
            "stripe_checkout_session_id": "",
            "stripe_customer_id": "",
            "stripe_subscription_id": "",
        },
    )

    log_payment(
        "manual_subscription_change",
        payload.client_name,
        {"active": bool(payload.active), "plan": plan, "reason": payload.reason},
    )

    return {"ok": True, "client": payload.client_name, "active": bool(payload.active), "plan": plan, "reason": payload.reason}


@app.post("/admin/backup/create")
def admin_backup_create(payload: BackupCreateRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    try:
        result = create_backup(
            base_dir=BASE_DIR,
            backup_dir=BACKUP_DIR,
            client_name=payload.client_name,
            include_chat_logs=bool(payload.include_chat_logs),
        )
        log_audit("backup_create", actor="admin_token", meta={"client": payload.client_name, "backup_id": result["backup_id"]})
        return result
    except Exception as e:
        raise HTTPException(500, f"Backup create failed: {e}")


@app.get("/admin/backup/list")
def admin_backup_list(client_name: str, authorization: str | None = Header(default=None)):
    require_client_admin_token(client_name, authorization)

    try:
        items = list_backups(BACKUP_DIR, client_name)
        return {"client": client_name, "backups": items}
    except Exception as e:
        raise HTTPException(500, f"Backup list failed: {e}")


@app.post("/admin/backup/restore")
def admin_backup_restore(payload: BackupRestoreRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    # Dangerous confirmation
    expected = f"RESTORE {payload.client_name} {payload.backup_id}"
    if (payload.confirm or "").strip().upper() != expected.upper():
        raise HTTPException(400, f"Confirmation required. Set confirm to exactly: {expected}")

    try:
        result = restore_backup(
            base_dir=BASE_DIR,
            backup_dir=BACKUP_DIR,
            client_name=payload.client_name,
            backup_id=payload.backup_id,
            allow_overwrite=True,
        )
        log_audit("backup_restore", actor="admin_token", meta={"client": payload.client_name, "backup_id": payload.backup_id, "restored": result.get("restored_count", 0)})
        return result
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Backup restore failed: {e}")

@app.get("/admin/monitor/errors")
def admin_get_errors(limit: int = 200, authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)  # super token only
    return {"errors": get_errors(ERRORS_FILE, limit=limit)}


@app.post("/admin/monitor/errors/clear")
def admin_clear_errors(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)
    clear_errors(ERRORS_FILE)
    log_audit("monitor_errors_cleared", actor="admin_token", meta={})
    return {"ok": True}


@app.get("/admin/monitor/health")
def admin_health(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)

    # Build live status
    ok = True
    notes = []

    # Check OpenAI key presence (doesn't call OpenAI, only checks config)
    if not os.getenv("OPENAI_API_KEY", "").strip():
        ok = False
        notes.append("OPENAI_API_KEY missing")

    # Stripe optional — only warn if billing endpoints used
    # (you can keep it as info)
    if not (os.getenv("SP_STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY")):
        notes.append("Stripe key missing (billing/checkout will fail)")

    record_health(ok, "; ".join(notes))

    # Return the health file content
    try:
        if HEALTH_FILE.exists():
            return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

    return {
        "ok": ok,
        "note": "; ".join(notes),
        "uptime_seconds": int(time.time() - APP_START_TS),
    }
@app.get("/health")
def public_health():
    return {
        "status": "ok",
        "service": "SupportPilot SaaS",
        "mode": os.getenv("ENV","dev"),
        "time": datetime.utcnow().isoformat()
    }


WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "").strip()
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "").strip()

WA_DEFAULT_CLIENT = os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo").strip()
WA_DEFAULT_API_KEY = os.getenv("WA_DEFAULT_API_KEY", "").strip()

SP_API_BASE = os.getenv("SP_API_BASE", "http://127.0.0.1:8000").strip()

def wa_send_text(to_wa_id: str, text: str):
    if not (WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN):
        raise RuntimeError("WhatsApp env not configured (WA_PHONE_NUMBER_ID / WA_ACCESS_TOKEN).")

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": text[:4000]},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"WhatsApp send failed: {r.status_code} {r.text}")

def call_supportpilot_chat(message_text: str) -> str:
    if not WA_DEFAULT_API_KEY:
        return "Client API key is not configured on server."

    url = f"{SP_API_BASE}/chat"
    payload = {
        "client_name": WA_DEFAULT_CLIENT,
        "api_key": WA_DEFAULT_API_KEY,
        "question": message_text,
        "tone": "formal",
        "language": "en",
    }

    r = requests.post(url, json=payload, timeout=60)
    if r.status_code >= 400:
        # Show short readable error to you (don’t expose internal stack traces)
        try:
            j = r.json()
            detail = j.get("detail", r.text)
        except Exception:
            detail = r.text
        return f"Sorry — system error: {detail}"

    data = r.json()
    return (data.get("answer") or "").strip() or "Sorry — I couldn't generate a response."

@app.get("/whatsapp/webhook")
def whatsapp_verify(hub_mode: str | None = None,
                    hub_challenge: str | None = None,
                    hub_verify_token: str | None = None):
    # Meta sends: hub.mode, hub.challenge, hub.verify_token
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    raise HTTPException(403, "Verification failed")

@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    body = await request.json()
    print("WHATSAPP EVENT:", body)

    try:
        entry = (body.get("entry") or [])[0]
        changes = (entry.get("changes") or [])[0]
        value = changes.get("value") or {}
        messages = value.get("messages") or []
        if not messages:
            return {"ok": True}  # status updates etc

        msg = messages[0]
        from_wa = msg.get("from")  # wa_id
        msg_type = msg.get("type")

        if msg_type != "text":
            wa_send_text(from_wa, "Sorry, I can only handle text messages right now.")
            return {"ok": True}

        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            wa_send_text(from_wa, "Please send a text message.")
            return {"ok": True}

        # ✅ ROUTE TO AI
        answer = call_supportpilot_chat(text)

        # ✅ SEND BACK TO WHATSAPP
        wa_send_text(from_wa, answer)

        return {"ok": True}

    except Exception as e:
        print("WHATSAPP ERROR:", repr(e))
        return {"ok": True}



# ============================================================
# Health
# ============================================================
@app.get("/")
def root():
    return {"status": "SupportPilot API Running"}
