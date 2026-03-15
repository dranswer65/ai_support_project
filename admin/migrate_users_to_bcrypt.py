import json
from pathlib import Path
import getpass
import bcrypt

BASE_DIR = Path(__file__).resolve().parent.parent
USERS_FILE = BASE_DIR / "admin" / "users.json"

def load_users():
    if not USERS_FILE.exists():
        raise FileNotFoundError(f"{USERS_FILE} not found")
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(data):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def main():
    data = load_users()
    users = data.get("users", [])
    if not users:
        print("No users found in users.json")
        return

    print("\nUsers found:")
    for u in users:
        print(" -", u.get("username"))

    print("\nThis will RESET passwords to new bcrypt hashes.")
    print("You will enter a NEW password for each user.\n")

    for u in users:
        username = u.get("username", "")
        if not username:
            continue

        while True:
            pw1 = getpass.getpass(f"New password for '{username}': ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw1 != pw2:
                print("❌ Passwords do not match. Try again.\n")
                continue
            if len(pw1) < 8:
                print("❌ Use at least 8 characters.\n")
                continue
            break

        hashed = bcrypt.hashpw(pw1.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        u["password_hash"] = hashed
        u["failed_password_attempts"] = 0
        u["failed_otp_attempts"] = 0
        u["locked_until_utc"] = ""
        u["otp_hash"] = ""
        u["otp_expires_utc"] = ""
        u["otp_last_sent_utc"] = ""

    save_users({"users": users})
    print("\n✅ Done. users.json now uses bcrypt password hashes.\n")

if __name__ == "__main__":
    main()
