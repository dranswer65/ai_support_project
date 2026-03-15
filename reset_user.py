import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

USER = "918287920585"   # <-- your WhatsApp test number

async def main():
    async with AsyncSessionLocal() as db:
        # delete session
        await db.execute(
            text("DELETE FROM wa_sessions WHERE user_id=:u"),
            {"u": USER},
        )
        await db.commit()
        print("✅ session deleted for", USER)

        # clear dedupe (important for fresh test)
        await db.execute(
            text("DELETE FROM wa_processed_messages"),
        )
        await db.commit()
        print("✅ dedupe table cleared")

asyncio.run(main())