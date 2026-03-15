# jobs/inactivity_reminder_worker.py
# Enterprise-ready one-time inactivity reminder worker (V1.5.1)
#
# Fixes:
# ✅ Fixes Postgres syntax error (no :now_iso::text)
# ✅ Uses activity_ts = COALESCE(last_user_ts, updated_at)
# ✅ Sanitizes TABLE_NAME hard
# ✅ One-time reminder via atomic claim+mark (SKIP LOCKED)
# ✅ Resets flags only when user becomes active AFTER the nudge
# ✅ Avoids nudging during handoff/escalation
# ✅ Debug mode prints claimed count

from __future__ import annotations

import os
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple, Optional

import requests
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine


# -----------------------------
# Config
# -----------------------------
_RAW_TABLE = (os.getenv("SESSIONS_TABLE", "sessions") or "sessions").strip()
_first = _RAW_TABLE.split()[0].strip()
TABLE_NAME = re.sub(r"[^A-Za-z0-9_]", "", _first) or "sessions"

INACTIVITY_MINUTES = int(os.getenv("INACTIVITY_MINUTES", "10"))
POLL_SECONDS = int(os.getenv("INACTIVITY_POLL_SECONDS", "60"))
DEBUG = (os.getenv("INACTIVITY_DEBUG", "false") or "false").strip().lower() in {"1", "true", "yes", "y"}

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


def _as_dict(val: Any) -> Optional[Dict[str, Any]]:
    if isinstance(val, dict):
        return val
    if isinstance(val, (str, bytes)):
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


# -----------------------------
# DB operations
# -----------------------------
async def reset_nudge_when_user_active(engine: AsyncEngine) -> None:
    """
    Reset nudge flags ONLY if user became active AFTER we nudged them.
    activity_ts = COALESCE(last_user_ts, updated_at)
    """
    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            session_json
            - 'inactivity_nudge_sent'
            - 'inactivity_nudged_at'
        WHERE COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = true
          AND (session_json->>'inactivity_nudged_at') IS NOT NULL
          AND COALESCE(
                (session_json->>'last_user_ts')::timestamptz,
                updated_at
              ) >
              (session_json->>'inactivity_nudged_at')::timestamptz;
    """)
    async with engine.begin() as conn:
        await conn.execute(stmt)


async def claim_and_mark_candidates(engine: AsyncEngine) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    Claim candidates atomically and mark them nudged.
    activity_ts = COALESCE(last_user_ts, updated_at)
    """
    cutoff = utcnow() - timedelta(minutes=INACTIVITY_MINUTES)
    now_iso = utcnow().isoformat()

    stmt = text(f"""
        WITH candidates AS (
            SELECT tenant_id, user_id
            FROM {TABLE_NAME}
            WHERE COALESCE(session_json->>'status', 'ACTIVE') = 'ACTIVE'
              AND COALESCE((session_json->>'handoff_active')::boolean, false) = false
              AND COALESCE((session_json->>'escalation_flag')::boolean, false) = false
              AND COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = false
              AND COALESCE(
                    (session_json->>'last_user_ts')::timestamptz,
                    updated_at
                  ) <= :cutoff
            ORDER BY COALESCE(
                    (session_json->>'last_user_ts')::timestamptz,
                    updated_at
                  ) ASC
            LIMIT 200
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {TABLE_NAME} s
        SET session_json =
            jsonb_set(
              jsonb_set(
                s.session_json,
                '{{inactivity_nudge_sent}}',
                'true'::jsonb,
                true
              ),
              '{{inactivity_nudged_at}}',
              to_jsonb(CAST(:now_iso AS text)),
              true
            )
        FROM candidates c
        WHERE s.tenant_id = c.tenant_id AND s.user_id = c.user_id
        RETURNING s.tenant_id, s.user_id, s.session_json;
    """)

    async with engine.begin() as conn:
        res = await conn.execute(stmt, {"cutoff": cutoff, "now_iso": now_iso})
        rows = res.fetchall()

    out: List[Tuple[str, str, Dict[str, Any]]] = []
    for tenant_id, user_id, session_json in rows:
        d = _as_dict(session_json)
        if d is not None:
            out.append((tenant_id, user_id, d))
    return out


async def unmark_if_send_failed(engine: AsyncEngine, tenant_id: str, user_id: str) -> None:
    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            session_json
            - 'inactivity_nudge_sent'
            - 'inactivity_nudged_at'
        WHERE tenant_id = :tenant_id AND user_id = :user_id;
    """)
    async with engine.begin() as conn:
        await conn.execute(stmt, {"tenant_id": tenant_id, "user_id": user_id})


# -----------------------------
# Worker loop
# -----------------------------
async def run_once(engine: AsyncEngine) -> None:
    try:
        await reset_nudge_when_user_active(engine)
    except Exception as e:
        print(f"[reminder] reset_nudge_when_user_active error: {e}")

    candidates = await claim_and_mark_candidates(engine)

    if DEBUG:
        print(f"[reminder] candidates_claimed={len(candidates)} inactivity={INACTIVITY_MINUTES}min table={TABLE_NAME}")

    for tenant_id, user_id, sess in candidates:
        lang = str(sess.get("language") or "en")
        msg = inactivity_message(lang)

        try:
            wa_send_text(user_id, msg)
            print(f"[reminder] nudged tenant={tenant_id} user={user_id} lang={lang}")
        except Exception as e:
            print(f"[reminder] send failed tenant={tenant_id} user={user_id}: {e}")
            await unmark_if_send_failed(engine, tenant_id, user_id)


async def main() -> None:
    db_url = to_async_db_url(DATABASE_URL)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    print(f"[reminder-worker] started inactivity={INACTIVITY_MINUTES}min poll={POLL_SECONDS}s table={TABLE_NAME} debug={DEBUG}")

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