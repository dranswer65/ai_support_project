# api_server.py (UPDATED) — Part 1/5
from __future__ import annotations

import os
import re
import io
import csv
import json
import math
import time
from pathlib import Path
from typing import Any, List, Tuple
from datetime import datetime, timezone

import bcrypt
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Header, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
import requests

from whatsapp_controller import handle_message

# OpenAI
from openai import OpenAI

# Local modules
from monitoring import log_error, get_errors, clear_errors

from db import ENGINE
from sqlalchemy import text
from sqlalchemy import text
from sqlalchemy import text

def wa_is_duplicate(message_id: str) -> bool:
    """
    Returns True if this WhatsApp message_id was already processed.
    Uses Postgres so it works across Railway instances.
    """
    if not message_id:
        return False

    with ENGINE.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS wa_processed_messages (
                  message_id TEXT PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )

        # Insert first time, ignore duplicates
        result = conn.execute(
            text(
                """
                INSERT INTO wa_processed_messages (message_id)
                VALUES (:mid)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id;
                """
            ),
            {"mid": message_id},
        ).fetchone()

        return result is None

def ensure_wa_dedupe_table():
    with ENGINE.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wa_processed_messages (
            msg_id TEXT PRIMARY KEY,
            wa_from TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """))

def wa_is_duplicate_message(msg_id: str) -> bool:
    """
    Returns True if we've already processed this msg_id.
    """
    if not msg_id:
        return False

    with ENGINE.begin() as conn:
        # Try to insert. If it already exists -> duplicate.
        try:
            conn.execute(
                text("INSERT INTO wa_processed_messages (msg_id) VALUES (:msg_id);"),
                {"msg_id": msg_id},
            )
            return False
        except Exception:
            return True

# ============================================================
# WhatsApp idempotency (prevents duplicate replies)
# ============================================================


_SEEN_WA_MSG: dict[str, float] = {}
_SEEN_TTL_SECONDS = 600  # keep 10 minutes

def _wa_is_duplicate(msg_id: str) -> bool:
    now = time.time()

    # cleanup old ids
    old = [k for k, ts in _SEEN_WA_MSG.items() if (now - ts) > _SEEN_TTL_SECONDS]
    for k in old:
        _SEEN_WA_MSG.pop(k, None)

    if not msg_id:
        return False

    if msg_id in _SEEN_WA_MSG:
        return True

    _SEEN_WA_MSG[msg_id] = now
    return False

from conversation_manager import (
    ensure_tables,
    log_message,
)

# Backup system (used later in file)
from backup_manager import create_backup, list_backups, restore_backup

# Stripe optional (won’t crash if missing)
try:
    import stripe
except Exception:
    stripe = None

APP_START_TS = time.time()

load_dotenv()

# ============================================================
# Data root (Railway volume support)
# ============================================================
def data_root() -> Path:
    p = os.getenv("SP_DATA_DIR", "").strip()
    if p:
        return Path(p)
    return Path(__file__).resolve().parent

BASE_DIR = data_root()
CLIENTS_DIR = BASE_DIR / "clients"
USAGE_DIR = BASE_DIR / "usage"
AUDIT_DIR = BASE_DIR / "audit"
BACKUP_DIR = BASE_DIR / "backups"
ERRORS_DIR = BASE_DIR / "logs"

AUDIT_FILE = AUDIT_DIR / "audit_log.json"
USAGE_FILE = USAGE_DIR / "usage_log.json"
ERRORS_FILE = ERRORS_DIR / "errors.json"
HEALTH_FILE = ERRORS_DIR / "health.json"

# ============================================================
# ENV helpers (supports SP_ and non-SP names)
# ============================================================
def env_any(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n, "")
        if v and str(v).strip():
            return str(v).strip()
    return default

DEFAULT_BRAND = env_any("SP_BRAND_NAME", "BRAND_NAME", default="SupportPilot")
TOKEN_PRICE_PER_1K = float(env_any("SP_TOKEN_PRICE_PER_1K", "TOKEN_PRICE_PER_1K", default="0.002"))

OPENAI_API_KEY = env_any("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Admin token
SP_ADMIN_TOKEN = env_any("SP_ADMIN_TOKEN", "ADMIN_TOKEN")

# Stripe keys (keep BOTH styles)
STRIPE_SECRET_KEY = env_any("SP_STRIPE_SECRET_KEY", "STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env_any("SP_STRIPE_WEBHOOK_SECRET", "STRIPE_WEBHOOK_SECRET")

STRIPE_PRICE_BASIC = env_any("SP_STRIPE_PRICE_BASIC", "STRIPE_PRICE_BASIC")
STRIPE_PRICE_PRO = env_any("SP_STRIPE_PRICE_PRO", "STRIPE_PRICE_PRO")

SP_PUBLIC_BASE_URL = env_any(
    "SP_PUBLIC_BASE_URL",
    "SP_PUBLIC_URL",
    "PUBLIC_BASE_URL",
    default="http://localhost:8000",
)

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# WhatsApp env
WA_VERIFY_TOKEN = env_any("WA_VERIFY_TOKEN")
WA_ACCESS_TOKEN = env_any("WA_ACCESS_TOKEN")
WA_PHONE_NUMBER_ID = env_any("WA_PHONE_NUMBER_ID")

WA_DEFAULT_CLIENT = env_any("WA_DEFAULT_CLIENT", default="supportpilot_demo")
WA_DEFAULT_API_KEY = env_any("WA_DEFAULT_API_KEY", default="")

SP_API_BASE = env_any("SP_API_BASE", default="http://127.0.0.1:8000")

# ============================================================
# Billing manager (Day 46)
# ============================================================
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


# ============================================================
# Debug DB
# ============================================================
@app.get("/debug/db")
def debug_db():
    with ENGINE.begin() as conn:
        v = conn.execute(text("SELECT 1")).scalar()
    return {"db_ok": bool(v == 1)}


@app.get("/debug/tables")
def debug_tables():
    with ENGINE.begin() as conn:
        result = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public'
                ORDER BY table_name;
                """
            )
        ).fetchall()
    return {"tables": [r[0] for r in result]}


@app.on_event("startup")
def _startup():
    # Make sure Postgres tables exist for WhatsApp conversation memory
    try:
        ensure_tables()
        ensure_wa_dedupe_table()   # ✅ CREATE dedupe table
        print("DB: wa_conversations + dedupe tables ready")
    except Exception as e:
        print("DB INIT ERROR:", repr(e))


@app.get("/debug/version")
def debug_version():
    return {
        "version": "SP_WA_FIX_2026_02_18_V2",
        "railway_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA", ""),
        "service": "SupportPilot API",
    }

@app.head("/health")
@app.get("/health")
def public_health():
    return {
        "status": "ok",
        "service": "SupportPilot SaaS",
        "mode": os.getenv("ENV", "dev"),
        "time": datetime.utcnow().isoformat()
    }

# ✅ tolerate double slash health checks
@app.head("//health")
@app.get("//health")
def public_health_double_slash():
    return {"status": "ok"}

# # ============================================================
# WhatsApp webhook (STATEFUL) — uses whatsapp_controller.py
# ============================================================
@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    body = await request.json()
    print("WHATSAPP EVENT RECEIVED")

    try:
        entry = (body.get("entry") or [])[0]
        changes = (entry.get("changes") or [])[0]
        value = changes.get("value") or {}

        messages = value.get("messages") or []
        if not messages:
            return {"ok": True}  # statuses etc.

        msg = messages[0]
        msg_id = (msg.get("id") or "").strip()
        if not msg_id:
            return {"ok": True}

        # ✅ ONE dedupe check only (avoid double-dedupe bugs)
        # IMPORTANT: wa_is_duplicate_message should ALSO record msg_id as seen/processed.
        # If your function only "checks" and doesn't "store", then update it to store.
        if wa_is_duplicate_message(msg_id):
            print("WA DUPLICATE IGNORED:", msg_id)
            return {"ok": True, "duplicate": True}

        print("MSG TYPE:", msg.get("type"))
        print("MSG RAW:", json.dumps(msg, ensure_ascii=False)[:1200])

        from_wa = (msg.get("from") or "").strip()  # wa_id
        msg_type = (msg.get("type") or "").strip()

        if not from_wa:
            return {"ok": True}

        if msg_type != "text":
            wa_send_text(from_wa, "Sorry, I can only handle text messages for now.")
            return {"ok": True}

        text_in = ((msg.get("text") or {}).get("body") or "").strip()
        if not text_in:
            wa_send_text(from_wa, "Please send a text message.")
            return {"ok": True}

        # 1) Log incoming (DB)
        log_message(from_wa, "in", text_in)

        # 2) Run Conversation Machine (in-memory sessions + escalation)
        reply_text, meta = handle_message(from_wa, text_in)

        # 3) Send reply
        wa_send_text(from_wa, reply_text)
        log_message(from_wa, "out", reply_text)

        # 4) Return quickly to Meta
        return {"ok": True, "state": (meta or {}).get("state")}

    except Exception as e:
        print("WHATSAPP ERROR:", repr(e))
        return {"ok": True}

# ============================================================
# Internal WA send (keeps api_server.py self-contained)
# ============================================================
def wa_send_text(to_wa_id: str, text_out: str) -> None:
    if not (WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN):
        print("WA ENV missing")
        return

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": (text_out or "")[:4000]},
    }
    print("WA SEND ->", to_wa_id, "TEXT=", (text_out or "")[:80])
    print("WA URL ->", url)
    print("WA PHONE OK?", bool(WA_PHONE_NUMBER_ID), "TOKEN OK?", bool(WA_ACCESS_TOKEN))


    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code >= 400:
            print("WA SEND ERROR:", r.status_code, r.text)
    except Exception as e:
        print("WA SEND EXC:", repr(e))

# api_server.py (UPDATED) — Part 2/5
# ============================================================
# INTERNAL AI CALL (optional utility, not required by WA controller)
# ============================================================
def _call_supportpilot_chat(message_text: str) -> str:
    """
    Calls internal /chat endpoint.
    Keeps WhatsApp fully server-side (no client API key exposure).
    """
    try:
        api_base = SP_API_BASE.strip()
        if not api_base:
            return "System error: SP_API_BASE not configured"

        url = f"{api_base}/chat"

        payload = {
            "client_name": WA_DEFAULT_CLIENT,
            "question": message_text,
            "tone": "formal",
            "language": "language",
        }

        r = requests.post(url, json=payload, timeout=25)

        if r.status_code != 200:
            try:
                return f"Error: {r.json()}"
            except Exception:
                return "AI server error"

        data = r.json()
        return (data.get("answer") or "No response").strip()

    except Exception as e:
        print("AI CALL ERROR:", repr(e))
        return "System temporarily unavailable"


# ============================================================
# JSON helpers
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
# Models
# ============================================================
class ChatRequest(BaseModel):
    client_name: str
    question: str
    tone: str = "formal"
    language: str = "en"  # en/ar
    api_key: str | None = None  # optional (kept for backward compat)


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
# Client paths
# ============================================================
def client_settings_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "config" / "settings.json"


def client_key_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "config" / "api_key.json"


def client_admin_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "config" / "admin_users.json"


def client_embeddings_path(client_name: str) -> Path:
    return CLIENTS_DIR / client_name / "knowledge" / "embeddings.json"


def client_prompt_path(client_name: str) -> Path:
    p1 = CLIENTS_DIR / client_name / "prompts" / "support_agent.txt"
    if p1.exists():
        return p1
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
    logs.append({"ts_utc": now_utc_iso(), "event": event, "actor": actor, "meta": meta or {}})
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


# ============================================================
# API key verification (bcrypt hash)
# NOTE: WhatsApp uses server-side calls, so /chat can remain subscription-gated.
# ============================================================
def verify_api_key(client_name: str, user_key: str) -> bool:
    data = load_client_key_data(client_name)

    plain = (data.get("api_key") or "").strip()
    if plain:
        return user_key == plain

    hashed = (data.get("api_key_hash") or "").strip()
    if hashed:
        try:
            return bcrypt.checkpw(user_key.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False

    raise HTTPException(500, "Client API key not configured on server.")


# ============================================================
# Admin auth helpers
# ============================================================
def parse_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()


def load_client_admin_token(client_name: str) -> str:
    data = load_json(client_admin_path(client_name), {})
    if not isinstance(data, dict):
        return ""
    return (data.get("admin_token") or "").strip()


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
# Subscription gate (Day 46 billing)
# ============================================================
def require_active_subscription(client_name: str):
    sub = get_subscription(client_name) or {}
    if not sub.get("active", False):
        raise HTTPException(402, "Subscription inactive. Please activate billing for this client.")
# api_server.py (UPDATED) — Part 3/5
# ============================================================
# Text helpers (anti-loop basics + Arabic-friendly)
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

        # ✅ Day 46 gate (only allows paid/active clients)
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

# api_server.py (UPDATED) — Part 4/5
# ============================================================
# Debug WA
# ============================================================
@app.get("/debug/wa")
def debug_wa():
    return {
        "running": "WA_WEBHOOK_V2",
        "WA_PHONE_NUMBER_ID": bool(os.getenv("WA_PHONE_NUMBER_ID", "").strip()),
        "WA_ACCESS_TOKEN": bool(os.getenv("WA_ACCESS_TOKEN", "").strip()),
        "WA_VERIFY_TOKEN": bool(os.getenv("WA_VERIFY_TOKEN", "").strip()),
        "WA_DEFAULT_CLIENT": os.getenv("WA_DEFAULT_CLIENT", ""),
        "SP_API_BASE": os.getenv("SP_API_BASE", ""),
    }


# ============================================================
# STRIPE CHECKOUT
# ============================================================
@app.post("/billing/checkout")
def billing_checkout(payload: CheckoutRequest, authorization: str | None = Header(default=None)):
    if stripe is None:
        raise HTTPException(500, "stripe package not installed. Run: pip install stripe")

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
        raise HTTPException(500, f"Stripe price ID missing for plan: {plan}")

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

    set_subscription(
        payload.client_name,
        {
            "active": False,
            "plan": plan,
            "stripe_checkout_session_id": sess.get("id", ""),
            "reason": "pending_checkout",
        },
    )

    log_payment("checkout_created", payload.client_name, {"plan": plan})
    return {"checkout_url": sess.get("url", ""), "session_id": sess.get("id", "")}


# ============================================================
# STRIPE WEBHOOK
# ============================================================
@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    if stripe is None:
        return JSONResponse({"error": "stripe package not installed"}, status_code=500)

    secret_key = (os.getenv("SP_STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY") or "").strip()
    webhook_secret = (os.getenv("SP_STRIPE_WEBHOOK_SECRET") or os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()

    if not secret_key:
        return JSONResponse({"error": "STRIPE_SECRET_KEY missing"}, status_code=500)
    if not webhook_secret:
        return JSONResponse({"error": "STRIPE_WEBHOOK_SECRET missing"}, status_code=500)

    stripe.api_key = secret_key

    payload_bytes = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload_bytes, sig, webhook_secret)
    except Exception as e:
        return JSONResponse({"error": f"Invalid signature: {e}"}, status_code=400)

    etype = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

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
                    "stripe_subscription_id": data.get("subscription", ""),
                    "reason": "active",
                },
            )
            log_payment("checkout_completed", client_name, {})

    if etype in {"customer.subscription.deleted", "invoice.payment_failed"}:
        sub_id = data.get("subscription") or data.get("id")
        from billing_manager import load_json as _lz, SUBSCRIPTIONS_FILE as _sf

        allsubs = _lz(_sf, {})
        for c, s in (allsubs or {}).items():
            if (s or {}).get("stripe_subscription_id") == sub_id:
                set_subscription_active(c, False, reason=etype)
                break

    return {"ok": True}


# ============================================================
# ADMIN CLIENT LIST
# ============================================================
@app.get("/admin/clients")
def admin_list_clients(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)

    if not CLIENTS_DIR.exists():
        return {"clients": []}

    clients = [p.name for p in CLIENTS_DIR.iterdir() if p.is_dir()]
    return {"clients": clients}


# ============================================================
# ADMIN CLIENT STATUS
# ============================================================
@app.post("/admin/client/status")
def admin_set_client_status(payload: ClientStatusRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    path = client_settings_path(payload.client_name)
    if not path.exists():
        raise HTTPException(404, "Client settings.json not found")

    settings = load_json(path, {})
    settings["active"] = bool(payload.active)
    save_json(path, settings)

    log_audit("client_status_change", meta={"client": payload.client_name, "active": payload.active})
    return {"ok": True}


# ============================================================
# ADMIN BILLING STATUS
# ============================================================
@app.get("/admin/billing/status")
def admin_billing_status(client_name: str, authorization: str | None = Header(default=None)):
    require_client_admin_token(client_name, authorization)
    sub = get_subscription(client_name) or {}
    return {"client": client_name, "subscription": sub}


# ============================================================
# BACKUP CREATE
# ============================================================
@app.post("/admin/backup/create")
def admin_backup_create(payload: BackupCreateRequest, authorization: str | None = Header(default=None)):
    require_client_admin_token(payload.client_name, authorization)

    result = create_backup(
        base_dir=BASE_DIR,
        backup_dir=BACKUP_DIR,
        client_name=payload.client_name,
        include_chat_logs=bool(payload.include_chat_logs),
    )
    return result


# ============================================================
# BACKUP LIST
# ============================================================
@app.get("/admin/backup/list")
def admin_backup_list(client_name: str, authorization: str | None = Header(default=None)):
    require_client_admin_token(client_name, authorization)
    items = list_backups(BACKUP_DIR, client_name)
    return {"client": client_name, "backups": items}


# ============================================================
# MONITOR ERRORS
# ============================================================
@app.get("/admin/monitor/errors")
def admin_get_errors(limit: int = 200, authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)
    return {"errors": get_errors(ERRORS_FILE, limit=limit)}


@app.post("/admin/monitor/errors/clear")
def admin_clear_errors(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)
    clear_errors(ERRORS_FILE)
    return {"ok": True}


# ============================================================
# HEALTH
# ============================================================
@app.get("/admin/monitor/health")
def admin_health(authorization: str | None = Header(default=None)):
    require_admin_token_any(authorization)

    ok = True
    notes = []

    if not os.getenv("OPENAI_API_KEY", "").strip():
        ok = False
        notes.append("OPENAI_API_KEY missing")

    record_health(ok, "; ".join(notes))

    try:
        if HEALTH_FILE.exists():
            return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

    return {"ok": ok, "note": "; ".join(notes)}


@app.get("/health")
def public_health():
    return {
        "status": "ok",
        "service": "SupportPilot SaaS",
        "mode": os.getenv("ENV", "dev"),
        "time": datetime.utcnow().isoformat(),
    }

# api_server.py (UPDATED) — Part 5/5
# ============================================================
# ROOT
# ============================================================
@app.get("/")
def root():
    return {"status": "SupportPilot API Running"}
