from __future__ import annotations

# ---------------------------------------------------------
# FIX: allow imports from project root
# ---------------------------------------------------------
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))
# ---------------------------------------------------------

import os
import asyncio

from database import AsyncSessionLocal
from core.slot_generator import generate_slots, DEFAULT_TZ


WA_DEFAULT_CLIENT = (os.getenv("WA_DEFAULT_CLIENT", "supportpilot_demo") or "").strip()
TENANT_ID = WA_DEFAULT_CLIENT


async def main():

    async with AsyncSessionLocal() as db:

        stats = await generate_slots(
            db=db,
            tenant_id=TENANT_ID,
            tz_name=os.getenv("CLINIC_TZ", DEFAULT_TZ),
            days_ahead=int(os.getenv("SLOTS_DAYS_AHEAD", "14")),
        )

    print("[slot_generator] done:", stats)


if __name__ == "__main__":
    asyncio.run(main())