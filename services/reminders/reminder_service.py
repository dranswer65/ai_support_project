from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from services.reminders.reminder_worker import send_due_reminders

logger = logging.getLogger(__name__)


REMINDER_INTERVAL_SECONDS = 120   # run every 2 minutes


async def _run_once() -> None:
    """
    Runs one reminder scan cycle.
    """
    async with AsyncSessionLocal() as db:  # type: AsyncSession
        try:
            result = await send_due_reminders(db)

            logger.info(
                "Reminder cycle finished | checked=%s claimed=%s sent=%s failed=%s",
                result.get("checked"),
                result.get("claimed"),
                result.get("sent"),
                result.get("failed"),
            )

        except Exception as e:
            logger.exception("Reminder cycle failed: %s", e)


async def reminder_loop(stop_event: Optional[asyncio.Event] = None) -> None:
    """
    Continuous reminder scheduler loop.

    Runs forever until stop_event is set.
    """
    logger.info("Reminder scheduler started")

    while True:

        if stop_event and stop_event.is_set():
            logger.info("Reminder scheduler stopping")
            break

        await _run_once()

        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)


def start_background_reminder_loop() -> None:
    """
    Starts reminder loop in background task.
    """
    loop = asyncio.get_event_loop()
    loop.create_task(reminder_loop())