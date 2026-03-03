# jobs/inactivity_reminder_worker.py
# Enterprise-ready one-time inactivity reminder worker (V1.2)
#
# Fixes:
# ✅ Sends reminder ONLY ONCE per inactivity window (no spam every minute)
# ✅ Stores 2 flags inside session_json (no DB schema change):
#    - inactivity_nudge_sent: true/false
#    - inactivity_nudged_at: ISO datetime
# ✅ Resets the flags automatically when the user becomes active again
# ✅ Avoids nudging during handoff/escalation
#
# How it works (practical):
# - Every POLL_SECONDS (default 60s), we scan sessions with updated_at older than 10 mins.
# - We only send if inactivity_nudge_sent != true
# - After sending, we write inactivity_nudge_sent=true and inactivity_nudged_at=now
# - If user sends ANY message, your main app updates updated_at; on the next scan,
#   we auto-clear inactivity_nudge_sent so a future 10-min inactivity can nudge again.

from __future__ import annotations

import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

import requests
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine


# -----------------------------
# Config
# -----------------------------
TABLE_NAME = (os.getenv("SESSIONS_TABLE", "sessions") or "sessions").strip()

INACTIVITY_MINUTES = int(os.getenv("INACTIVITY_MINUTES", "10"))      # 10 minutes
POLL_SECONDS = int(os.getenv("INACTIVITY_POLL_SECONDS", "60"))       # check every 60 sec

WA_TOKEN = (os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
WA_API_VERSION = (os.getenv("WA_API_VERSION") or "v20.0").strip()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not WA_TOKEN or not WA_PHONE_NUMBER_ID:
    raise RuntimeError("WA_TOKEN / WA_PHONE_NUMBER_ID are missing")


# -----------------------------
# Reminder messages
# -----------------------------
def inactivity_message(lang: str) -> str:
    lang = (lang or "en").lower()
    if lang.startswith("ar"):
        return (
            "هل ما زلت بحاجة إلى مساعدة؟ 😊\n\n"
            "يسعدنا خدمتك في أي وقت.\n"
            "يمكنك كتابة سؤالك أو اختيار أحد الخيارات من القائمة.\n\n"
            "0️⃣ القائمة الرئيسية\n"
            "99️⃣ التحدث مع موظف الاستقبال"
        )
    return (
        "Are you still there? 😊\n\n"
        "I’m here to help whenever you're ready.\n"
        "You can type your question or choose an option from the menu.\n\n"
        "0️⃣ Main Menu\n"
        "99️⃣ Speak to Reception"
    )


# -----------------------------
# Helpers
# -----------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_async_db_url(url: str) -> str:
    """
    Railway often gives: postgres://...
    SQLAlchemy async wants: postgresql+asyncpg://...
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def wa_send_text(to_user: str, body: str) -> None:
    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_user,
        "type": "text",
        "text": {"body": body},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp send failed {r.status_code}: {r.text}")


def _safe_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return False


# -----------------------------
# DB operations
# -----------------------------
async def fetch_candidates(engine: AsyncEngine) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    Candidates:
      - updated_at <= now - INACTIVITY_MINUTES
      - session_json status ACTIVE (or missing)
      - NOT nudged already (inactivity_nudge_sent != true)
      - NOT handoff_active == true
    """
    cutoff = utcnow() - timedelta(minutes=INACTIVITY_MINUTES)

    stmt = text(f"""
        SELECT tenant_id, user_id, session_json
        FROM {TABLE_NAME}
        WHERE updated_at <= :cutoff
          AND COALESCE(session_json->>'status', 'ACTIVE') = 'ACTIVE'
          AND COALESCE((session_json->>'handoff_active')::boolean, false) = false
          AND COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = false
        ORDER BY updated_at ASC
        LIMIT 200;
    """)

    async with engine.begin() as conn:
        res = await conn.execute(stmt, {"cutoff": cutoff})
        rows = res.fetchall()

    out: List[Tuple[str, str, Dict[str, Any]]] = []
    for tenant_id, user_id, session_json in rows:
        if isinstance(session_json, dict):
            out.append((tenant_id, user_id, session_json))
        else:
            try:
                out.append((tenant_id, user_id, json.loads(session_json)))
            except Exception:
                continue
    return out


async def mark_nudged(engine: AsyncEngine, tenant_id: str, user_id: str) -> None:
    """
    Sets:
      inactivity_nudge_sent = true
      inactivity_nudged_at  = now_iso
    """
    now_iso = utcnow().isoformat()

    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            jsonb_set(
              jsonb_set(
                session_json,
                '{{inactivity_nudge_sent}}',
                'true'::jsonb,
                true
              ),
              '{{inactivity_nudged_at}}',
              to_jsonb(:now_iso::text),
              true
            ),
            updated_at = updated_at
        WHERE tenant_id = :tenant_id AND user_id = :user_id;
    """)

    async with engine.begin() as conn:
        await conn.execute(stmt, {"now_iso": now_iso, "tenant_id": tenant_id, "user_id": user_id})


async def reset_nudge_if_active(engine: AsyncEngine) -> None:
    """
    If a user becomes active again (updated_at is recent), clear nudge flags
    so that a future inactivity period can trigger again.

    Condition:
      updated_at > now - INACTIVITY_MINUTES
      AND inactivity_nudge_sent == true
    """
    cutoff_recent = utcnow() - timedelta(minutes=INACTIVITY_MINUTES)

    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            session_json
            - 'inactivity_nudge_sent'
            - 'inactivity_nudged_at'
        WHERE updated_at > :cutoff_recent
          AND COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = true;
    """)

    async with engine.begin() as conn:
        await conn.execute(stmt, {"cutoff_recent": cutoff_recent})


# -----------------------------
# Worker loop
# -----------------------------
async def run_once(engine: AsyncEngine) -> None:
    # 1) Clear nudge flags for sessions that became active again
    #    (prevents "nudged forever" state)
    try:
        await reset_nudge_if_active(engine)
    except Exception as e:
        print(f"[reminder] reset_nudge_if_active error: {e}")

    # 2) Fetch candidates to nudge
    candidates = await fetch_candidates(engine)

    for tenant_id, user_id, sess in candidates:
        # extra safety in case JSON types are strange
        if _safe_bool(sess.get("handoff_active")):
            continue
        if _safe_bool(sess.get("inactivity_nudge_sent")):
            continue

        lang = str(sess.get("language") or "en")
        msg = inactivity_message(lang)

        try:
            wa_send_text(user_id, msg)
        except Exception as e:
            print(f"[reminder] send failed tenant={tenant_id} user={user_id}: {e}")
            continue

        try:
            await mark_nudged(engine, tenant_id, user_id)
            print(f"[reminder] nudged tenant={tenant_id} user={user_id} lang={lang}")
        except Exception as e:
            # message sent but DB failed; might resend next loop (rare)
            print(f"[reminder] DB mark failed tenant={tenant_id} user={user_id}: {e}")


async def main() -> None:
    db_url = to_async_db_url(DATABASE_URL)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    print(
        f"[reminder-worker] started "
        f"inactivity={INACTIVITY_MINUTES}min poll={POLL_SECONDS}s table={TABLE_NAME}"
    )

    try:
        while True:
            try:
                await run_once(engine)
            except Exception as e:
                print(f"[reminder-worker] loop error: {e}")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())