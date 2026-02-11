import streamlit as st
import requests
import os
from dotenv import load_dotenv

load_dotenv()

API = "http://127.0.0.1:8000"
SUPER_TOKEN = os.getenv("SP_ADMIN_TOKEN")

st.set_page_config(page_title="SupportPilot Owner", layout="wide")

# ===============================
# LOGIN
# ===============================
if "owner_ok" not in st.session_state:
    st.session_state.owner_ok = False

if not st.session_state.owner_ok:
    st.title("🔐 SupportPilot Owner Login")

    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        if u == os.getenv("ADMIN_USERNAME") and p == os.getenv("ADMIN_PASSWORD"):
            st.session_state.owner_ok = True
            st.rerun()
        else:
            st.error("Invalid login")

    st.stop()

# ===============================
# DASHBOARD
# ===============================
st.title("🧠 SupportPilot SaaS Control Center")

headers = {"Authorization": f"Bearer {SUPER_TOKEN}"}

# ===============================
# CLIENT LIST
# ===============================
st.subheader("📊 All Clients")

try:
    r = requests.get(f"{API}/admin/clients", headers=headers)
    data = r.json()
    clients = data.get("clients", [])
except:
    clients = []

st.write("Total clients:", len(clients))

for c in clients:
    col1, col2, col3 = st.columns([3,2,2])

    col1.write(f"**{c}**")

    if col2.button(f"Billing {c}"):
        r = requests.get(
            f"{API}/admin/billing/status?client_name={c}",
            headers=headers
        )
        st.json(r.json())

    if col3.button(f"Disable {c}"):
        requests.post(
            f"{API}/admin/client/status",
            headers=headers,
            json={
                "client_name": c,
                "active": False,
                "confirm": f"DISABLE {c}"
            }
        )
        st.warning(f"{c} disabled")
