import asyncio
import asyncpg

async def main():
    try:
        conn = await asyncpg.connect(
            host="shuttle.proxy.rlwy.net",
            port=46770,
            user="postgres",
            password="QJfoNHXhcvctGKqtnvXaEkYURTJyWYjb",  # paste railway password here
            database="railway",
            ssl="require",
        )

        val = await conn.fetchval("select 1")
        print("✅ SUCCESS: DB connected ->", val)

        await conn.close()

    except Exception as e:
        print("❌ FAILED:", str(e))

asyncio.run(main())
