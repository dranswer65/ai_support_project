import bcrypt

raw_key = "supportpilot_secret_123"  # ← this will be your real API key

hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt())

print(hashed.decode())
