from sqlalchemy import create_engine, text
import os

db_url = os.getenv("DATABASE_URL")

engine = create_engine(db_url)

with engine.connect() as conn:

    print("\n📦 Columns:\n")

    cols = conn.execute(text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name='appointment_requests'
        ORDER BY ordinal_position;
    """)).fetchall()

    if not cols:
        print("❌ Table appointment_requests NOT FOUND")
        exit()

    for c in cols:
        print(f"- {c[0]} : {c[1]} (nullable={c[2]})")

    print("\n🔑 Primary Key:\n")

    pk = conn.execute(text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name='appointment_requests'
        AND tc.constraint_type='PRIMARY KEY';
    """)).fetchall()

    for p in pk:
        print("-", p[0])