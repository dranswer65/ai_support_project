# jobs/inactivity_reminder_worker.py
# Enterprise-ready one-time inactivity reminder worker (V1.3.1)
#
# Fixes:
# ✅ Fix SQL syntax errors (remove accidental text like "(optional but good)")
# ✅ NO repeat spam even if multiple Railway workers are running
# ✅ Atomic "claim + mark" in ONE SQL statement using FOR UPDATE SKIP LOCKED
# ✅ Writes 2 fields inside session_json (no DB schema change):
#    - inactivity_nudge_sent: true
#    - inactivity_nudged_at: ISO datetime
# ✅ Resets flags ONLY when user becomes active AFTER the nudge (updated_at > nudged_at)
# ✅ Avoids nudging during handoff/escalation

from __future__ import annotations

import os
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
TABLE_NAME = (os.getenv("SESSIONS_TABLE", "sessions") or "sessions").strip()

INACTIVITY_MINUTES = int(os.getenv("INACTIVITY_MINUTES", "10"))       # 10 minutes
POLL_SECONDS = int(os.getenv("INACTIVITY_POLL_SECONDS", "60"))        # check every 60 sec

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
# DB operations (atomic)
# -----------------------------
async def claim_and_mark_candidates(engine: AsyncEngine) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    ATOMIC:
      1) Select candidates FOR UPDATE SKIP LOCKED
      2) Mark them as nudged in the same statement
      3) Return (tenant_id, user_id, session_json)

    This prevents duplicates even if multiple worker instances run.
    """
    cutoff = utcnow() - timedelta(minutes=INACTIVITY_MINUTES)
    now_iso = utcnow().isoformat()

    stmt = text(f"""
        WITH candidates AS (
            SELECT tenant_id, user_id
            FROM {TABLE_NAME}
            WHERE updated_at <= :cutoff
              AND COALESCE(session_json->>'status', 'ACTIVE') = 'ACTIVE'
              AND COALESCE((session_json->>'handoff_active')::boolean, false) = false
              AND COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = false
            ORDER BY updated_at ASC
            LIMIT 200
            FOR UPDATE SKIP LOCKED
        )
        UPDATE {TABLE_NAME} AS s
        SET session_json =
            jsonb_set(
              jsonb_set(
                s.session_json,
                '{{inactivity_nudge_sent}}',
                'true'::jsonb,
                true
              ),
              '{{inactivity_nudged_at}}',
              to_jsonb(:now_iso::text),
              true
            )
        FROM candidates AS c
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


async def reset_nudge_when_user_active(engine: AsyncEngine) -> None:
    """
    Reset nudge flags ONLY if the user became active AFTER we nudged them:
      updated_at > inactivity_nudged_at

    This avoids mistakenly clearing the flag and re-nudging every minute.
    """
    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            session_json
            - 'inactivity_nudge_sent'
            - 'inactivity_nudged_at'
        WHERE COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = true
          AND (session_json->>'inactivity_nudged_at') IS NOT NULL
          AND updated_at >
              (session_json->>'inactivity_nudged_at')::timestamptz;
    """)

    async with engine.begin() as conn:
        await conn.execute(stmt)


async def unmark_if_send_failed(engine: AsyncEngine, tenant_id: str, user_id: str) -> None:
    """
    If WhatsApp send fails, remove the flags so it can retry later.
    """
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
    # 1) Reset flags when user becomes active again
    try:
        await reset_nudge_when_user_active(engine)
    except Exception as e:
        print(f"[reminder] reset_nudge_when_user_active error: {e}")

    # 2) Claim + mark candidates atomically
    candidates = await claim_and_mark_candidates(engine)
    print(f"[reminder] claimed={len(candidates)} cutoff={INACTIVITY_MINUTES}min")

    # 3) Send message for claimed sessions
    for tenant_id, user_id, sess in candidates:
        lang = str(sess.get("language") or "en")
        msg = inactivity_message(lang)

        try:
            wa_send_text(user_id, msg)
            print(f"[reminder] sent tenant={tenant_id} user={user_id} lang={lang}")
        except Exception as e:
            print(f"[reminder] send failed tenant={tenant_id} user={user_id}: {e}")
            try:
                await unmark_if_send_failed(engine, tenant_id, user_id)
            except Exception as e2:
                print(f"[reminder] unmark failed tenant={tenant_id} user={user_id}: {e2}")


async def main() -> None:
    db_url = to_async_db_url(DATABASE_URL)
    engine = create_async_engine(db_url, pool_pre_ping=True)

    print(
        f"[reminder-worker] started inactivity={INACTIVITY_MINUTES}min "
        f"poll={POLL_SECONDS}s table={TABLE_NAME}"
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