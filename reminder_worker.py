# reminder_worker.py
from __future__ import annotations

import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
TABLE_NAME = os.getenv("SESSIONS_TABLE", "sessions").strip() or "sessions"

POLL_SECONDS = int(os.getenv("REMINDER_POLL_SECONDS", "60"))  # check every 60s

# ---------------------------------------------------------------------
# Reminder templates (match your engine copy)
# ---------------------------------------------------------------------
REMINDER_AR = (
    "هل ما زلت بحاجة إلى مساعدة؟ 😊\n\n"
    "يسعدنا خدمتك في أي وقت.\n"
    "يمكنك كتابة سؤالك أو اختيار أحد الخيارات من القائمة.\n\n"
    "0️⃣ القائمة الرئيسية\n"
    "99️⃣ موظف الاستقبال"
)

REMINDER_EN = (
    "Are you still there? 😊\n\n"
    "I’m here to help whenever you're ready.\n"
    "You can type your question or choose an option from the menu.\n\n"
    "0️⃣ Main Menu\n"
    "99️⃣ Reception"
)

# ---------------------------------------------------------------------
# TODO: Replace with your real WhatsApp sender
# ---------------------------------------------------------------------
async def send_whatsapp_text(*, tenant_id: str, user_id: str, text_message: str) -> None:
    """
    Replace this with your actual WA sender.
    You likely already have something like:
      - send_wa_message(phone_number_id, user_id, text)
    inside api_server.py or whatsapp sender module.
    """
    # Example placeholder:
    print(f"[REMINDER] tenant={tenant_id} user={user_id} text={text_message[:60]}...")


def _pick_reminder_text(session_json: Dict[str, Any]) -> str:
    lang = str(session_json.get("language") or "en").lower()
    if lang.startswith("ar"):
        return REMINDER_AR
    return REMINDER_EN


async def _fetch_due_sessions(db: AsyncSession, limit: int = 200) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    Returns list of (tenant_id, user_id, session_json)
    that are due for reminder and not sent yet.
    """
    q = text(f"""
        SELECT tenant_id, user_id, session_json
        FROM {TABLE_NAME}
        WHERE
            COALESCE((session_json->>'reminder_sent')::boolean, false) = false
            AND (session_json->>'reminder_due_at') IS NOT NULL
            AND (session_json->>'reminder_due_at')::timestamptz <= NOW()
            AND COALESCE(session_json->>'status', 'ACTIVE') = 'ACTIVE'
            AND COALESCE(session_json->>'state', '') <> 'ESCALATION'
        ORDER BY updated_at ASC
        LIMIT :limit;
    """)
    res = await db.execute(q, {"limit": limit})
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


async def _mark_reminder_sent(db: AsyncSession, tenant_id: str, user_id: str) -> None:
    """
    Atomically mark reminder_sent = true.
    """
    q = text(f"""
        UPDATE {TABLE_NAME}
        SET session_json =
            jsonb_set(
                jsonb_set(session_json, '{{reminder_sent}}', 'true'::jsonb, true),
                '{{reminder_sent_at}}',
                to_jsonb(NOW()::text),
                true
            ),
            updated_at = NOW()
        WHERE tenant_id = :tenant_id AND user_id = :user_id;
    """)
    await db.execute(q, {"tenant_id": tenant_id, "user_id": user_id})


async def run_once(session_maker: async_sessionmaker[AsyncSession]) -> int:
    async with session_maker() as db:
        due = await _fetch_due_sessions(db)
        if not due:
            return 0

        sent = 0
        for tenant_id, user_id, sess in due:
            try:
                msg = _pick_reminder_text(sess)
                await send_whatsapp_text(tenant_id=tenant_id, user_id=user_id, text_message=msg)
                await _mark_reminder_sent(db, tenant_id, user_id)
                sent += 1
            except Exception:
                # Do NOT mark sent if sending failed
                continue

        await db.commit()
        return sent


async def main() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    print("[reminder_worker] started")
    while True:
        try:
            n = await run_once(session_maker)
            if n:
                print(f"[reminder_worker] reminders_sent={n}")
        except Exception as e:
            print(f"[reminder_worker] error: {e}")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())