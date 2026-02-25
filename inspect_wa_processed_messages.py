import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    cols = conn.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='wa_processed_messages'
        ORDER BY ordinal_position;
    """)).fetchall()

    print("wa_processed_messages columns:")
    for c, t in cols:
        print("-", c, ":", t)

    pk = conn.execute(text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_schema='public'
          AND tc.table_name='wa_processed_messages'
          AND tc.constraint_type='PRIMARY KEY'
        ORDER BY kcu.ordinal_position;
    """)).fetchall()

    print("\nPK columns:", [r[0] for r in pk])