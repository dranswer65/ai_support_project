import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS wa_processed_messages;"))

print("✅ Dropped table: wa_processed_messages")