import streamlit as st
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENTS_DIR = BASE_DIR / "clients"
USAGE_FILE = BASE_DIR / "usage" / "usage_log.json"

st.set_page_config(page_title="Client Dashboard", layout="wide")

# -------------------------
# helpers
# -------------------------
def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return default

def client_admin_file(client):
    return CLIENTS_DIR / client / "config" / "admin_users.json"

def list_clients():
    if not CLIENTS_DIR.exists():
        return []
    return [p.name for p in CLIENTS_DIR.iterdir() if p.is_dir()]

# -------------------------
# LOGIN
# -------------------------
if "client_logged" not in st.session_state:
    st.session_state.client_logged = False

if not st.session_state.client_logged:

    st.title("🔐 Client Login")

    clients = list_clients()
    if not clients:
        st.error("No clients found")
        st.stop()

    client = st.selectbox("Select your company", clients)
    token = st.text_input("Client Admin Token", type="password")

    if st.button("Login"):

        data = load_json(client_admin_file(client), {})

        if token == data.get("admin_token"):
            st.session_state.client_logged = True
            st.session_state.client_name = client
            st.success("Login successful")
            st.rerun()
        else:
            st.error("Invalid token")

    st.stop()

# -------------------------
# DASHBOARD
# -------------------------
client = st.session_state.client_name
st.title(f"🏢 Client Dashboard — {client}")

if st.button("Logout"):
    st.session_state.client_logged = False
    st.rerun()

# -------------------------
# Usage
# -------------------------
st.header("📊 Usage")

usage = load_json(USAGE_FILE, [])
rows = [r for r in usage if r.get("client")==client]

total_tokens = sum(r.get("tokens",0) for r in rows)
total_cost = sum(r.get("cost",0) for r in rows)

st.metric("Total tokens", total_tokens)
st.metric("Total cost $", round(total_cost,4))

st.dataframe(rows, use_container_width=True)

# -------------------------
# Settings view
# -------------------------
st.header("⚙️ Client Settings")

settings_path = CLIENTS_DIR / client / "config" / "settings.json"
settings = load_json(settings_path, {})

st.json(settings)
