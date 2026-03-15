# services/events/pg_bus.py
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Set

import asyncpg
from fastapi import WebSocket

CHANNEL = "reception_events"

# Use your DATABASE_URL env (Railway)
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()


class ReceptionBus:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, event: Dict[str, Any]) -> None:
        data = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        async with self._lock:
            for ws in self.clients:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


bus = ReceptionBus()


async def pg_listen_loop() -> None:
    """
    Long-running LISTEN loop (call this via asyncio.create_task(...) on app startup).
    Reconnects on error.
    """
    if not DATABASE_URL:
        print("[pg_bus] DATABASE_URL missing; LISTEN disabled")
        return

    while True:
        conn = None
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            await conn.add_listener(CHANNEL, _on_notify)
            print("[pg_bus] LISTEN started on", CHANNEL)

            # keep connection alive
            while True:
                await asyncio.sleep(3600)

        except Exception as e:
            print("[pg_bus] listener error:", repr(e))
            print("[pg_bus] reconnecting in 2s...")
            await asyncio.sleep(2)

        finally:
            try:
                if conn:
                    await conn.close()
            except Exception:
                pass


def _on_notify(conn, pid, channel, payload: str) -> None:
    """
    asyncpg calls this callback in the event loop context (not awaited).
    We schedule the async broadcast safely.
    """
    try:
        event = json.loads(payload)
    except Exception:
        event = {"type": "unknown", "raw": payload}

    # ✅ safer than get_event_loop() in modern Python
    asyncio.create_task(bus.broadcast(event))