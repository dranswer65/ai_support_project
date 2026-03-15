# services/events/emit.py
from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHANNEL = "reception_events"

async def emit_reception_event(db: AsyncSession, event: Dict[str, Any]) -> None:
    payload = json.dumps(event, ensure_ascii=False)
    await db.execute(text("SELECT pg_notify(:ch, :payload)"), {"ch": CHANNEL, "payload": payload})