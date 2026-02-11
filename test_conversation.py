import time
from whatsapp_controller import handle_message
from session_manager import SessionManager

USER_ID = "user_123"

def send(msg):
    print("\nUSER:", msg)
    reply = handle_message(USER_ID, msg)
    print("BOT:", reply)

# ---- Normal Flow ----
send("Hi")
send("I have a delivery issue")
send("ORDER12345")
send("ok")
send("no")

# ---- Timeout Test ----
print("\n--- TIMEOUT TEST ---")
time.sleep(2)
send("Hi")

time.sleep(65)  # exceeds timeout
send("Hi")
