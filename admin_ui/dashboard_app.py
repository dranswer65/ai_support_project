# =========================================================
# IMPORTS
# =========================================================
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
from dotenv import load_dotenv

# 🔐 AUTH (Day 43 secure dashboard ↔ API)
from admin_ui.auth import require_login, logout_button


load_dotenv()

# =========================================================
# PATHS
# =========================================================
BASE_DIR = Path(__file__).resolve().parent.parent
CLIENTS_DIR = BASE_DIR / "clients"
USAGE_FILE = BASE_DIR / "usage" / "usage_log.json"

ADMIN_DIR = BASE_DIR / "admin"
ADMIN_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = ADMIN_DIR / "users.json"
AUDIT_FILE = ADMIN_DIR / "audit_log.json"

# =========================================================
# API CONFIG (Day 43 secure connection)
# =========================================================
API_BASE = os.getenv("SP_API_BASE", "https://web-production-de896d.up.railway.app").strip()
SUPER_ADMIN_TOKEN = os.getenv("SP_ADMIN_TOKEN", "").strip()

# =========================================================
# STREAMLIT PAGE
# =========================================================
st.set_page_config(page_title="SupportPilot SaaS Admin", layout="wide")

# 🔐 REQUIRE LOGIN FIRST (Day 43 security gate)
require_login()

# =========================================================
# SESSION INFO
# =========================================================
role = st.session_state.get("auth_role", "viewer")
username = st.session_state.get("auth_user", "unknown")
client_from_login = st.session_state.get("auth_client", "")

is_admin = role == "admin"
is_owner = role == "owner"

# =========================================================
# SAFE JSON HELPERS
# =========================================================
def load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# =========================================================
# AUDIT LOGGER
# =========================================================
def log_audit(action: str, meta: dict | None = None):
    logs = load_json(AUDIT_FILE, [])
    if not isinstance(logs, list):
        logs = []

    logs.append({
        "ts_utc": utc_now(),
        "actor": username,
        "action": action,
        "meta": meta or {}
    })

    logs = logs[-2000:]
    save_json(AUDIT_FILE, logs)


# =========================================================
# CLIENT HELPERS
# =========================================================
def list_clients():
    if not CLIENTS_DIR.exists():
        return []
    return sorted([p.name for p in CLIENTS_DIR.iterdir() if p.is_dir()])


def client_settings_path(client):
    return CLIENTS_DIR / client / "config" / "settings.json"


def client_key_path(client):
    return CLIENTS_DIR / client / "config" / "api_key.json"


def load_client_settings(client):
    return load_json(client_settings_path(client), {})


def save_client_settings(client, data):
    save_json(client_settings_path(client), data)


def load_client_key(client):
    return load_json(client_key_path(client), {})


def save_client_key(client, data):
    save_json(client_key_path(client), data)


def load_usage():
    return load_json(USAGE_FILE, [])


# =========================================================
# API CALL HELPERS (Day 43)
# =========================================================
def api_headers(client_name=None):
    """
    Returns proper Bearer token header.
    Owner uses super token.
    Client admin uses client token from config.
    """
    if is_owner:
        return {"Authorization": f"Bearer {SUPER_ADMIN_TOKEN}"}

    # client admin token
    cfg = load_json(CLIENTS_DIR / client_name / "config" / "admin_users.json", {})
    token = cfg.get("admin_token", "")
    return {"Authorization": f"Bearer {token}"}


def api_get(path, client=None):
    try:
        url = f"{API_BASE}{path}"
        r = requests.get(url, headers=api_headers(client))
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def api_post(path, payload, client=None):
    try:
        url = f"{API_BASE}{path}"
        r = requests.post(url, json=payload, headers=api_headers(client))
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# =========================================================
# HEADER UI
# =========================================================
top_left, top_right = st.columns([5, 1])

with top_left:
    st.title("🧩 SupportPilot SaaS Admin Dashboard")
    st.caption(f"Logged in as **{username}** (role: **{role}**)")

with top_right:
    logout_button()


# =========================================================
# CLIENT SELECTION
# =========================================================
clients = list_clients()

if not clients:
    st.warning("No clients found in /clients folder.")
    st.stop()

# If client admin → lock to their client
if not is_owner:
    if client_from_login and client_from_login in clients:
        selected_client = client_from_login
    else:
        selected_client = clients[0]
else:
    selected_client = st.selectbox("Select Client", clients)

st.markdown(f"### Client: `{selected_client}`")

# =========================================================
# TABS
# =========================================================
tabs = st.tabs([
    "Clients",
    "Requests Inbox",     # ✅ NEW
    "Usage & Billing",
    "API Keys",
    "Audit Logs"
])


# =========================================================
# TAB 1 — CLIENT SETTINGS
# =========================================================
with tabs[0]:
    st.subheader("Client Settings")

    settings = load_client_settings(selected_client)
    if not settings:
        st.error("settings.json not found")
    else:
        col1, col2 = st.columns([1, 2])

        with col1:
            active_val = bool(settings.get("active", True))
            active_new = st.toggle("Client Active", value=active_val, disabled=not is_admin)
            settings["active"] = active_new

        with col2:
            settings["default_tone"] = st.selectbox(
                "Tone",
                ["formal", "friendly", "premium"],
                index=["formal", "friendly", "premium"].index(settings.get("default_tone", "formal")),
                disabled=not is_admin
            )

            settings["language"] = st.selectbox(
                "Language",
                ["en", "ar"],
                index=["en", "ar"].index(settings.get("language", "en")),
                disabled=not is_admin
            )

            settings["escalation_threshold"] = st.slider(
                "Escalation threshold",
                0.0,
                1.0,
                float(settings.get("escalation_threshold", 0.38)),
                0.01,
                disabled=not is_admin
            )

            settings["sla_hours"] = st.number_input(
                "SLA hours",
                min_value=1,
                max_value=168,
                value=int(settings.get("sla_hours", 24)),
                disabled=not is_admin
            )

            settings["support_email"] = st.text_input(
                "Support email",
                value=str(settings.get("support_email", "")),
                disabled=not is_admin
            )

            settings["legal_notice"] = st.text_area(
                "Legal notice",
                value=str(settings.get("legal_notice", "")),
                height=80,
                disabled=not is_admin
            )

            if is_admin:
                if st.button("💾 Save Settings"):
                    save_client_settings(selected_client, settings)
                    log_audit("client_settings_updated", {"client": selected_client})
                    st.success("Settings saved")
            else:
                st.info("Viewer role: read-only")


# =========================================================
# TAB 2 — REQUESTS INBOX (Reception)
# =========================================================
with tabs[1]:
    st.subheader("Requests Inbox (Reception)")

    # Filters
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        status = st.selectbox("Status", ["PENDING", "APPROVED", "REJECTED", "CONTACTED", "ALL"], index=0)
    with c2:
        auto_refresh = st.toggle("Auto refresh", value=False)
    with c3:
        st.caption("Tip: Use **APPROVED/REJECTED/CONTACTED** to manage the queue like a real clinic inbox.")

    if auto_refresh:
        # lightweight refresh every ~10s
        st.session_state["_sp_inbox_tick"] = st.session_state.get("_sp_inbox_tick", 0) + 1
        st.markdown(
            "<meta http-equiv='refresh' content='10'>",
            unsafe_allow_html=True
        )

    # Fetch inbox
    data = api_get(f"/api/reception/requests?tenant={selected_client}&status={status}", client=selected_client)

    if data.get("error"):
        st.error(f"API error: {data['error']}")
    elif not data.get("ok"):
        st.warning(data)
    else:
        items = data.get("items") or []
        st.caption(f"Tenant: `{data.get('tenant_id')}` • Showing: **{len(items)}** requests")

        if not items:
            st.info("No requests found.")
        else:
            # Show as a table
            st.dataframe(items, use_container_width=True)

            st.divider()
            st.markdown("### Update request (Confirm / Reject / Contacted)")

            req_ids = [it.get("request_id") for it in items if it.get("request_id")]
            chosen = st.selectbox("Select request_id", req_ids)

            colA, colB = st.columns([2, 3])
            with colA:
                new_status = st.selectbox("New status", ["APPROVED", "REJECTED", "CONTACTED", "PENDING"], index=0)
                note = st.text_area("Receptionist note (internal)", height=100)

            with colB:
                send_msg = st.toggle("Send WhatsApp message to patient", value=False)
                msg_text = ""
                if send_msg:
                    st.markdown("**Message text**")
                    msg_text = st.text_area(
                        "",
                        height=140,
                        placeholder="Example: Your appointment request is confirmed. Please arrive 15 minutes early."
                    )

                    # Quick templates
                    tcol1, tcol2, tcol3 = st.columns(3)
                    with tcol1:
                        if st.button("Template: Confirm"):
                            msg_text = "✅ Your appointment request is confirmed. Please arrive 15 minutes early."
                            st.session_state["_sp_msg_text"] = msg_text
                    with tcol2:
                        if st.button("Template: Reject"):
                            msg_text = "❌ Sorry, the selected slot is not available. Please reply with another date/time."
                            st.session_state["_sp_msg_text"] = msg_text
                    with tcol3:
                        if st.button("Template: Need Info"):
                            msg_text = "ℹ️ Please confirm your full name and mobile number to complete your request."
                            st.session_state["_sp_msg_text"] = msg_text

                    # keep in session state so template buttons persist
                    if "_sp_msg_text" in st.session_state and not msg_text:
                        msg_text = st.session_state["_sp_msg_text"]

            if st.button("✅ Apply Update", type="primary"):
                payload = {
                    "status": new_status,
                    "receptionist_note": note,
                    "send_message": bool(send_msg),
                    "message_text": msg_text,
                }

                res = api_post(
                    f"/api/reception/requests/{chosen}/update?tenant={selected_client}",
                    payload,
                    client=selected_client,
                )

                if res.get("ok"):
                    st.success(f"Updated ✅ ({chosen} → {new_status})")
                    log_audit("request_updated", {"client": selected_client, "request_id": chosen, "status": new_status})
                else:
                    st.error(res)


# =========================================================
# TAB 3 — USAGE + BILLING
# =========================================================
with tabs[2]:
    st.subheader("Usage & Billing")

    usage = load_usage()
    if not usage:
        st.info("No usage yet")
    else:
        rows = [u for u in usage if u.get("client") == selected_client]
        st.dataframe(rows, use_container_width=True)

    st.markdown("### Billing status")

    billing = api_get(f"/admin/billing/status?client_name={selected_client}", client=selected_client)
    if billing.get("error"):
        st.error(f"API error: {billing['error']}")
    elif billing.get("detail"):
        st.warning(billing["detail"])
    else:
        st.json(billing)

    if is_admin:
        st.markdown("### Export Billing CSV")
        if st.button("⬇️ Export CSV (API)", key="export_csv_btn"):
            try:
                url = f"{API_BASE}/admin/billing/export?client_name={selected_client}"
                r = requests.get(url, headers=api_headers(selected_client))
                if r.status_code != 200:
                    st.error(r.text)
                else:
                    st.download_button(
                        "Download billing.csv",
                        data=r.text,
                        file_name=f"{selected_client}_billing.csv",
                        mime="text/csv",
                        key="download_csv_btn",
                    )
                    log_audit("billing_exported", {"client": selected_client})
            except Exception as e:
                st.error(str(e))


# =========================================================
# TAB 4 — API KEYS
# =========================================================
with tabs[3]:
    st.subheader("API Keys (Per Client)")

    key_data = load_client_key(selected_client)
    st.markdown("### Current api_key.json")
    st.json(key_data)

    st.markdown("### Update api_key.json")
    api_key_hash = st.text_input(
        "api_key_hash (bcrypt)",
        value=str(key_data.get("api_key_hash", "")),
        disabled=not is_admin,
        key=f"api_hash_{selected_client}",
    )

    if is_admin:
        if st.button("💾 Save api_key.json", key=f"save_api_key_{selected_client}"):
            payload = {"client_name": selected_client, "api_key_hash": api_key_hash.strip()}
            save_client_key(selected_client, payload)
            log_audit("api_key_updated", {"client": selected_client})
            st.success("Saved ✅")
    else:
        st.info("Viewer role: read-only")


# =========================================================
# TAB 5 — AUDIT LOGS
# =========================================================
with tabs[4]:
    st.subheader("Audit Logs")

    logs = load_json(AUDIT_FILE, [])
    if not logs:
        st.info("No audit logs yet.")
    else:
        st.dataframe(list(reversed(logs))[:300], use_container_width=True)

    if is_admin:
        if st.button("🧹 Clear audit logs", key="clear_audit_btn"):
            save_json(AUDIT_FILE, [])
            log_audit("audit_cleared", {})
            st.success("Audit cleared ✅")
    else:
        st.info("Viewer role: cannot clear logs.")

