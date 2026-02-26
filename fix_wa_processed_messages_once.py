import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

TENANT = "supportpilot_demo"   # change later if needed

async def main():
    async with AsyncSessionLocal() as db:

        print("1️⃣ Adding tenant_id column if missing...")
        await db.execute(text("""
            ALTER TABLE wa_processed_messages
            ADD COLUMN IF NOT EXISTS tenant_id TEXT;
        """))

        print("2️⃣ Backfilling tenant_id...")
        await db.execute(text("""
            UPDATE wa_processed_messages
            SET tenant_id = COALESCE(tenant_id, :t)
            WHERE tenant_id IS NULL;
        """), {"t": TENANT})

        print("3️⃣ Enforcing NOT NULL...")
        await db.execute(text("""
            ALTER TABLE wa_processed_messages
            ALTER COLUMN tenant_id SET NOT NULL;
        """))

        print("4️⃣ Dropping old primary key if exists...")
        await db.execute(text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'wa_processed_messages_pkey'
            ) THEN
                ALTER TABLE wa_processed_messages
                DROP CONSTRAINT wa_processed_messages_pkey;
            END IF;
        END $$;
        """))

        print("5️⃣ Creating new composite PK (tenant_id + msg_id)...")
        await db.execute(text("""
            ALTER TABLE wa_processed_messages
            ADD PRIMARY KEY (tenant_id, msg_id);
        """))

        print("6️⃣ Creating index...")
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_wa_processed_created
            ON wa_processed_messages(created_at);
        """))

        await db.commit()

    print("\n✅ DONE: wa_processed_messages fixed for multi-tenant")

asyncio.run(main())