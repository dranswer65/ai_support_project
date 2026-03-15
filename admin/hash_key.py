import bcrypt

import os

key = os.getenv("CLIENT_API_KEY", "")
if not key:
    raise SystemExit("CLIENT_API_KEY missing. Put it in .env (NOT in git).")

hashed = bcrypt.hashpw(key.encode(), bcrypt.gensalt())

print(hashed.decode())


