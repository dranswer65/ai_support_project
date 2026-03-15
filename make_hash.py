import bcrypt
import os

key = os.getenv("CLIENT_API_KEY", "")
if not key:
    raise SystemExit("CLIENT_API_KEY is missing. Put it in .env / Railway Variables.")
print(key)

hashed = bcrypt.hashpw(key.encode(), bcrypt.gensalt()).decode()

print("KEY:", key)
print("HASH:", hashed)
