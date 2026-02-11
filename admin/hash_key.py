import bcrypt

key = "hb_9f38sk29dj293kd9"

hashed = bcrypt.hashpw(key.encode(), bcrypt.gensalt())

print(hashed.decode())
