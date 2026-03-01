import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM sessions WHERE tenant_id=:t AND user_id=:u"),
            {"t": "supportpilot_demo", "u": "918287920585"},
        )
        await db.commit()

    print("✅ reset done")

asyncio.run(main())



