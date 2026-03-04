# jobs/inactivity_reminder_worker.py
# Enterprise-ready one-time inactivity reminder worker (V1.4)
#
# Fixes:
# ✅ Uses session_json->>'last_user_ts' (REAL user activity clock) instead of updated_at
# ✅ One-time reminder only (inactivity_nudge_sent / inactivity_nudged_at)
# ✅ Atomic claim+mark (FOR UPDATE SKIP LOCKED) prevents duplicates across multiple workers
# ✅ Resets flags ONLY when user becomes active again (last_user_ts > nudged_at)
# ✅ Avoids nudging during handoff/escalation
# ✅ Does NOT depend on having a SQL editor; logs show exactly what's happening

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

INACTIVITY_MINUTES = int(os.getenv("INACTIVITY_MINUTES", "10"))      # 10 minutes
POLL_SECONDS = int(os.getenv("INACTIVITY_POLL_SECONDS", "60"))       # check every 60 sec

WA_TOKEN = (os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("WA_PHONE_NUMBER_ID") or "").strip()
WA_API_VERSION = (os.getenv("WA_API_VERSION") or "v20.0").strip()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

DEBUG = (os.getenv("INACTIVITY_DEBUG", "0").strip() in {"1", "true", "True", "YES", "yes"})


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
# DB operations
# -----------------------------
async def reset_nudge_when_user_active(engine: AsyncEngine) -> int:
    """
    Reset flags ONLY if user became active AFTER the nudge:
      (session_json->>'last_user_ts')::timestamptz > (session_json->>'inactivity_nudged_at')::timestamptz
    """
    stmt = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            session_json
            - 'inactivity_nudge_sent'
            - 'inactivity_nudged_at'
        WHERE COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = true
          AND (session_json->>'inactivity_nudged_at') IS NOT NULL
          AND (session_json->>'last_user_ts') IS NOT NULL
          AND (session_json->>'last_user_ts')::timestamptz >
              (session_json->>'inactivity_nudged_at')::timestamptz;
    """)

    async with engine.begin() as conn:
        res = await conn.execute(stmt)
        return int(getattr(res, "rowcount", 0) or 0)


async def claim_and_mark_candidates(engine: AsyncEngine) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    ATOMIC claim+mark:
    - Choose sessions whose last_user_ts <= cutoff (10 minutes)
    - Not nudged yet
    - Not in handoff/escalation
    - status ACTIVE
    - Mark inactivity_nudge_sent=true and inactivity_nudged_at=now
    - Return rows to send messages
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
              AND (session_json->>'last_user_ts') IS NOT NULL
              AND (session_json->>'last_user_ts')::timestamptz <= :cutoff
            ORDER BY (session_json->>'last_user_ts')::timestamptz ASC
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
              to_jsonb(:now_iso::text),
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
    """
    If send fails, remove flags so it can retry later.
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


async def debug_counts(engine: AsyncEngine) -> None:
    """
    Prints why worker may be claiming 0 candidates.
    """
    cutoff = utcnow() - timedelta(minutes=INACTIVITY_MINUTES)

    stmt = text(f"""
        SELECT
          COUNT(*) FILTER (WHERE (session_json->>'last_user_ts') IS NULL) AS missing_last_user_ts,
          COUNT(*) FILTER (
            WHERE (session_json->>'last_user_ts') IS NOT NULL
              AND (session_json->>'last_user_ts')::timestamptz <= :cutoff
          ) AS older_than_cutoff,
          COUNT(*) FILTER (
            WHERE COALESCE((session_json->>'inactivity_nudge_sent')::boolean, false) = true
          ) AS already_nudged,
          COUNT(*) AS total
        FROM {TABLE_NAME};
    """)

    async with engine.begin() as conn:
        res = await conn.execute(stmt, {"cutoff": cutoff})
        row = res.first()
        if row:
            print(f"[debug] total={row.total} missing_last_user_ts={row.missing_last_user_ts} older_than_cutoff={row.older_than_cutoff} already_nudged={row.already_nudged}")


# -----------------------------
# Worker loop
# -----------------------------
async def run_once(engine: AsyncEngine) -> None:
    reset_count = 0
    try:
        reset_count = await reset_nudge_when_user_active(engine)
    except Exception as e:
        print(f"[reminder] reset_nudge_when_user_active error: {e}")

    candidates = await claim_and_mark_candidates(engine)

    if DEBUG:
        print(f"[debug] reset_count={reset_count} claimed={len(candidates)} inactivity={INACTIVITY_MINUTES}min")
        await debug_counts(engine)

    for tenant_id, user_id, sess in candidates:
        lang = str(sess.get("language") or "en")
        msg = inactivity_message(lang)

        try:
            wa_send_text(user_id, msg)
            print(f"[reminder] nudged tenant={tenant_id} user={user_id} lang={lang}")
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
        f"poll={POLL_SECONDS}s table={TABLE_NAME} debug={DEBUG}"
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