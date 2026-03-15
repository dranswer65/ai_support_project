import os
from dotenv import load_dotenv

load_dotenv()

for key in ["DATABASE_URL", "POSTGRES_URL", "DB_URL"]:
    val = os.getenv(key)
    if val:
        print(f"{key} =", val[:60] + ("..." if len(val) > 60 else ""))