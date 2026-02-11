import json
from pathlib import Path


# --------------------------------
# Base Paths
# --------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

CLIENTS_DIR = BASE_DIR / "clients"
ADMIN_DIR = BASE_DIR / "admin"
USAGE_FILE = BASE_DIR / "usage" / "usage_log.json"
API_KEYS_FILE = ADMIN_DIR / "api_key.json"


# --------------------------------
# Loaders
# --------------------------------

def load_usage():

    if not USAGE_FILE.exists():
        return []

    with open(USAGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_keys():

    if not API_KEYS_FILE.exists():
        return {}

    with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_api_keys(data):

    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_client_config(client):

    path = CLIENTS_DIR / client / "config" / "settings.json"

    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_client_config(client, config):

    path = CLIENTS_DIR / client / "config" / "settings.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# --------------------------------
# Client Management
# --------------------------------

def list_clients():

    clients = []

    if CLIENTS_DIR.exists():

        for folder in CLIENTS_DIR.iterdir():
            if folder.is_dir():
                clients.append(folder.name)

    return clients


def show_clients():

    clients = list_clients()
    keys = load_api_keys()

    print("\n===== CLIENTS =====\n")

    if not clients:
        print("No clients found.")
        return


    for c in clients:

        config = load_client_config(c)

        if not config:
            continue

        status = "ACTIVE" if config.get("active", True) else "DISABLED"
        key = keys.get(c, "No key")

        print(f"Client: {c}")
        print(f" Status : {status}")
        print(f" API Key: {key}")
        print("-" * 30)


def set_client_status(client, active=True):

    config = load_client_config(client)

    if not config:
        print("❌ Client config not found")
        return

    config["active"] = active

    save_client_config(client, config)

    status = "ENABLED" if active else "DISABLED"

    print(f"✅ {client} is now {status}")


# --------------------------------
# Usage / Billing
# --------------------------------

def show_usage():

    data = load_usage()

    if not data:
        print("\nNo usage data yet.\n")
        return


    summary = {}

    for record in data:

        name = record["client"]

        summary.setdefault(name, {
            "tokens": 0,
            "cost": 0
        })

        summary[name]["tokens"] += record["tokens"]
        summary[name]["cost"] += record["cost"]


    print("\n===== BILLING REPORT =====\n")

    for client, info in summary.items():

        print(f"Client: {client}")
        print(f" Total Tokens: {info['tokens']}")
        print(f" Total Cost  : ${round(info['cost'], 4)}")
        print("-" * 30)


# --------------------------------
# API Key Management
# --------------------------------

def create_api_key():

    name = input("Client name: ").strip().lower()

    key = input("New API key: ").strip()

    if not name or not key:
        print("❌ Invalid input")
        return


    keys = load_api_keys()

    keys[name] = key

    save_api_keys(keys)

    print(f"✅ API key saved for {name}")


def delete_api_key():

    name = input("Client name: ").strip().lower()

    keys = load_api_keys()

    if name not in keys:
        print("❌ Client not found")
        return

    del keys[name]

    save_api_keys(keys)

    print(f"✅ API key removed for {name}")


# --------------------------------
# Admin Menu
# --------------------------------

def menu():

    while True:

        print("""
===== ADMIN DASHBOARD =====

1. List Clients
2. Show Usage / Billing
3. Disable Client
4. Enable Client
5. Create / Update API Key
6. Delete API Key
7. Exit
""")

        choice = input("Select option: ").strip()


        if choice == "1":
            show_clients()


        elif choice == "2":
            show_usage()


        elif choice == "3":

            name = input("Client name: ").strip().lower()
            set_client_status(name, False)


        elif choice == "4":

            name = input("Client name: ").strip().lower()
            set_client_status(name, True)


        elif choice == "5":
            create_api_key()


        elif choice == "6":
            delete_api_key()


        elif choice == "7":

            print("Goodbye.")
            break


        else:

            print("Invalid option.")


# --------------------------------
# Run
# --------------------------------

if __name__ == "__main__":

    menu()

