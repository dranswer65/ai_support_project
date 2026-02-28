# reset_session_pg.py
import asyncio
import os
from sqlalchemy import text
from database import AsyncSessionLocal

# Usage:
#   py reset_session_pg.py 918287920585 supportpilot_demo
# If tenant not provided -> default

async def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: py reset_session_pg.py <user_id> [tenant_id]")
        raise SystemExit(2)

    user_id = sys.argv[1].strip()
    tenant_id = (sys.argv[2].strip() if len(sys.argv) >= 3 else "default") or "default"

    async with AsyncSessionLocal() as db:
        # sessions table (core/session_store_pg.py)
        r1 = await db.execute(
            text("DELETE FROM sessions WHERE tenant_id=:t AND user_id=:u"),
            {"t": tenant_id, "u": user_id},
        )

        # wa_processed_messages table (dedupe) optional cleanup for test loops
        r2 = await db.execute(
            text("DELETE FROM wa_processed_messages WHERE tenant_id=:t AND wa_from=:u"),
            {"t": tenant_id, "u": user_id},
        )

        await db.commit()

    print(f"✅ deleted sessions row for user_id={user_id} tenant_id={tenant_id}")

if __name__ == "__main__":
    asyncio.run(main())