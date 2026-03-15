import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

# Use ONE connection for everything
with engine.connect() as conn:
    cols = conn.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='wa_sessions'
        ORDER BY ordinal_position;
    """)).fetchall()

    print("wa_sessions columns:")
    for c in cols:
        print("-", c[0], ":", c[1])

    pk_cols = conn.execute(text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema='public'
          AND tc.table_name='wa_sessions'
          AND tc.constraint_type='PRIMARY KEY'
        ORDER BY kcu.ordinal_position;
    """)).fetchall()

    print("\nwa_sessions PK columns:")
    if not pk_cols:
        print("- (none)")
    else:
        for r in pk_cols:
            print("-", r[0])