# reset_one_session.py
# Tenant-aware reset for WhatsApp testing (fixes stuck menu/greeting issues)
#
# Usage (PowerShell):
#   py .\reset_one_session.py 918287920585 supportpilot_demo
# If tenant not provided, uses WA_DEFAULT_CLIENT or "default".

import os
import sys
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

def _tenant_from_args() -> str:
    if len(sys.argv) >= 3 and sys.argv[2].strip():
        return sys.argv[2].strip()
    env_t = (os.getenv("WA_DEFAULT_CLIENT", "") or "").strip()
    return env_t or "default"

def _user_from_args() -> str:
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return sys.argv[1].strip()
    raise SystemExit("Usage: py reset_one_session.py <user_id> [tenant_id]")

async def main():
    user_id = _user_from_args()
    tenant_id = _tenant_from_args()

    async with AsyncSessionLocal() as db:
        # ✅ Correct table used by controller: sessions (tenant_id, user_id)
        await db.execute(
            text("DELETE FROM sessions WHERE tenant_id=:t AND user_id=:u"),
            {"t": tenant_id, "u": user_id},
        )

        # ✅ Optional: clear dedupe rows so Meta retries don't confuse testing
        # (Only if your wa_processed_messages stores wa_from=user_id; if not, it's harmless)
        await db.execute(
            text("DELETE FROM wa_processed_messages WHERE tenant_id=:t AND wa_from=:u"),
            {"t": tenant_id, "u": user_id},
        )

        await db.commit()

    print(f"✅ deleted sessions row for user_id={user_id} tenant_id={tenant_id}")

if __name__ == "__main__":
    asyncio.run(main())