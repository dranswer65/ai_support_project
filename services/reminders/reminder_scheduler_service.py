from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from database import AsyncSessionLocal
from services.reminders.reminder_worker import send_due_reminders


REMINDER_INTERVAL_SECONDS = int(
    os.getenv("REMINDER_INTERVAL_SECONDS") or "300"
)
REMINDER_INTERVAL_SECONDS = max(30, REMINDER_INTERVAL_SECONDS)


async def run_reminder_cycle() -> Dict[str, Any]:
    """
    Runs one reminder scan/send cycle.
    """
    async with AsyncSessionLocal() as db:
        result = await send_due_reminders(
            db,
            include_two_hour_reminder=True,
        )
        return result


async def reminder_loop() -> None:
    """
    Background loop for automatic WhatsApp reminders.
    """
    print(f"[reminders] scheduler started (interval={REMINDER_INTERVAL_SECONDS}s)")

    while True:
        try:
            result = await run_reminder_cycle()

            print(
                "[reminders] cycle:",
                f"checked={result.get('checked', 0)}",
                f"claimed={result.get('claimed', 0)}",
                f"sent={result.get('sent', 0)}",
                f"failed={result.get('failed', 0)}",
            )

        except Exception as e:
            print("[reminders] cycle error:", e)

        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)


def start_reminder_scheduler() -> asyncio.Task:
    """
    Starts the reminder loop as a background asyncio task.
    """
    return asyncio.create_task(reminder_loop())