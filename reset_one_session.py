import os
import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

USER = "918287920585"

async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM wa_sessions WHERE user_id=:u"), {"u": USER})
        await db.commit()
    print("✅ deleted session for", USER)

asyncio.run(main())



