import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    ddl = conn.execute(text("""
        SELECT pg_get_tabledef('public.wa_sessions'::regclass);
    """)).fetchone()

print(ddl[0] if ddl else "No DDL returned")