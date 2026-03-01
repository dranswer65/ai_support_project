import os
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Ensure sslmode=require
if "sslmode=" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sessions WHERE tenant_id=%s AND user_id=%s",
            ("supportpilot_demo", "918287920585"),
        )
    conn.commit()

print("✅ reset done")