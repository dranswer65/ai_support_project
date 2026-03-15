import os
from sqlalchemy import create_engine, text

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_engine(DATABASE_URL)

TARGET = "993d5b14f077"

with engine.begin() as conn:
    # Ensure the table exists
    conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL);"))

    # If multiple rows exist, wipe and reinsert cleanly
    conn.execute(text("DELETE FROM alembic_version;"))
    conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v);"), {"v": TARGET})

print("✅ alembic_version fixed ->", TARGET)