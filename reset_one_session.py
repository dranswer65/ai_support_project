import sys
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

# Usage:
#   py reset_one_session.py 918287920585 supportpilot_demo
USER = sys.argv[1] if len(sys.argv) > 1 else "918287920585"
TENANT = sys.argv[2] if len(sys.argv) > 2 else "supportpilot_demo"

async def main():
    async with AsyncSessionLocal() as db:
        # ✅ correct sessions table
        await db.execute(
            text("DELETE FROM sessions WHERE tenant_id=:t AND user_id=:u"),
            {"t": TENANT, "u": USER},
        )
        # ✅ also clear dedupe so Meta test messages don't get ignored
        await db.execute(
            text("DELETE FROM wa_processed_messages WHERE tenant_id=:t AND wa_from=:u"),
            {"t": TENANT, "u": USER},
        )
        await db.commit()

    print("✅ reset ok for user", USER, "tenant", TENANT)

asyncio.run(main())