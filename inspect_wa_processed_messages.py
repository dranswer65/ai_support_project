from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("\n📦 Columns:\n")

    cols = conn.execute(text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema='public'
          AND table_name='wa_processed_messages'
        ORDER BY ordinal_position;
    """)).fetchall()

    for c in cols:
        print(f"- {c[0]} : {c[1]} (nullable={c[2]})")

    print("\n🔑 Primary Key:\n")

    pk = conn.execute(text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema='public'
          AND tc.table_name='wa_processed_messages'
          AND tc.constraint_type='PRIMARY KEY'
        ORDER BY kcu.ordinal_position;
    """)).fetchall()

    for p in pk:
        print(f"- {p[0]}")