from __future__ import annotations

import os
import json
import time
import base64
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import smtplib

import streamlit as st
from dotenv import load_dotenv
SESSION_TIMEOUT_SECONDS = 15 * 60  # 15 minutes 
# bcrypt optional (recommended)
try:
    import bcrypt
except Exception:
    bcrypt = None

load_dotenv()

# ============================================================
# Paths
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_DIR = BASE_DIR / "admin"
ADMIN_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = ADMIN_DIR / "users.json"
AUDIT_FILE = ADMIN_DIR / "audit_log.json"

# ============================================================
# Security settings
# ============================================================
LOCK_MINUTES = int(os.getenv("SP_LOCK_MINUTES", "10"))
MAX_PASS_FAILS = int(os.getenv("SP_MAX_PASS_FAILS", "5"))
MAX_OTP_FAILS = int(os.getenv("SP_MAX_OTP_FAILS", "5"))
OTP_TTL_MINUTES = int(os.getenv("SP_OTP_TTL_MINUTES", "5"))

SP_SMTP_HOST = os.getenv("SP_SMTP_HOST", "").strip()
SP_SMTP_PORT = int(os.getenv("SP_SMTP_PORT", "587") or "587")
SP_SMTP_USER = os.getenv("SP_SMTP_USER", "").strip()
SP_SMTP_PASS = os.getenv("SP_SMTP_PASS", "").strip()
SP_FROM_EMAIL = os.getenv("SP_FROM_EMAIL", "SupportPilot Security <no-reply@supportpilot.ai>").strip()

OTP_ENABLED = bool(SP_SMTP_HOST and SP_SMTP_USER and SP_SMTP_PASS)

# ============================================================
# Helpers
# ============================================================
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _to_utc_str(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""

def _parse_utc(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default

def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def _log_audit(action: str, actor: str, meta: dict | None = None):
    logs = _load_json(AUDIT_FILE, [])
    if not isinstance(logs, list):
        logs = []
    logs.append({
        "ts_utc": _utc_now().isoformat(),
        "action": action,
        "actor": actor,
        "meta": meta or {},
    })
    _save_json(AUDIT_FILE, logs[-2000:])

def _load_users_doc():
    doc = _load_json(USERS_FILE, {"users": []})
    if not isinstance(doc, dict):
        doc = {"users": []}
    if "users" not in doc or not isinstance(doc["users"], list):
        doc["users"] = []
    return doc

def _save_users_doc(doc):
    _save_json(USERS_FILE, doc)

def _find_user(username: str):
    doc = _load_users_doc()
    for u in doc["users"]:
        if (u.get("username") or "").lower() == (username or "").lower():
            return u, doc
    return None, doc

def _is_locked(user: dict) -> bool:
    locked_until = _parse_utc(user.get("locked_until_utc", ""))
    return locked_until is not None and _utc_now() < locked_until

def _lock_user(user: dict, minutes: int = LOCK_MINUTES):
    user["locked_until_utc"] = _to_utc_str(_utc_now() + timedelta(minutes=minutes))

def _bcrypt_check(password: str, password_hash: str) -> bool:
    if not bcrypt:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

def _verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    if password_hash.startswith("$2"):
        return _bcrypt_check(password, password_hash)
    return False

def _make_otp_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"

def _hash_otp(otp: str) -> str:
    if bcrypt:
        return bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return base64.b64encode(otp.encode("utf-8")).decode("utf-8")

def _check_otp(otp: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("$2") and bcrypt:
        try:
            return bcrypt.checkpw(otp.encode("utf-8"), stored.encode("utf-8"))
        except Exception:
            return False
    try:
        return base64.b64decode(stored.encode("utf-8")).decode("utf-8") == otp
    except Exception:
        return False

def _send_otp_email(to_email: str, otp_code: str):
    msg = EmailMessage()
    msg["Subject"] = "SupportPilot Admin OTP"
    msg["From"] = SP_FROM_EMAIL
    msg["To"] = to_email
    msg.set_content(
        f"Your SupportPilot OTP is: {otp_code}\n\nThis code expires in {OTP_TTL_MINUTES} minutes."
    )

    with smtplib.SMTP(SP_SMTP_HOST, SP_SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(SP_SMTP_USER, SP_SMTP_PASS)
        s.send_message(msg)

# ============================================================
# SESSION INIT (single source of truth)
# ============================================================
def ensure_session():
    # Core auth flags
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False
    if "auth_user" not in st.session_state:
        st.session_state.auth_user = None
    if "auth_role" not in st.session_state:
        st.session_state.auth_role = None
    if "auth_client" not in st.session_state:
        st.session_state.auth_client = None

    # ✅ OTP / stage flags (THIS FIXES YOUR ERROR)
    if "auth_stage" not in st.session_state:
        st.session_state.auth_stage = "login"   # "login" | "otp" | "ok"
    if "otp_username" not in st.session_state:
        st.session_state.otp_username = None
    if "otp_sent_at" not in st.session_state:
        st.session_state.otp_sent_at = 0.0

    # Session timeout tracking
    if "last_seen" not in st.session_state:
        st.session_state.last_seen = time.time()
def touch_session():
    st.session_state.last_seen = time.time()

def is_session_expired() -> bool:
    return (time.time() - float(st.session_state.get("last_seen", 0))) > SESSION_TIMEOUT_SECONDS

def force_logout(reason: str = "Session expired. Please login again."):
    st.session_state.auth_ok = False
    st.session_state.auth_user = None
    st.session_state.auth_role = None
    st.session_state.auth_client = None
    st.session_state.auth_stage = "login"
    st.session_state.otp_username = None
    st.session_state.otp_sent_at = 0.0
    st.error(reason)
    st.stop()



# ============================================================
# LOGIN UI
# ============================================================
def login_ui():
    ensure_session()

    st.markdown("# 🔐 SupportPilot Login")

    with st.form(key="login_form_unique"):
        username = st.text_input("Username", key="login_username_unique")
        password = st.text_input("Password", type="password", key="login_password_unique")
        submitted = st.form_submit_button("Login", use_container_width=True)

    if not submitted:
        return

    username = (username or "").strip()
    password = (password or "").strip()

    user, doc = _find_user(username)
    if not user:
        st.error("Incorrect username or password.")
        _log_audit("login_failed", actor=username or "unknown", meta={"reason": "user_not_found"})
        return

    if not user.get("active", True):
        st.error("This account is disabled.")
        _log_audit("login_failed", actor=username, meta={"reason": "disabled"})
        return

    if _is_locked(user):
        st.error("Account temporarily locked. Please try again later.")
        _log_audit("login_failed", actor=username, meta={"reason": "locked"})
        return

    if not _verify_password(password, str(user.get("password_hash", ""))):
        user["failed_password_attempts"] = int(user.get("failed_password_attempts", 0)) + 1
        if user["failed_password_attempts"] >= MAX_PASS_FAILS:
            _lock_user(user)
        _save_users_doc(doc)

        st.error("Incorrect username or password.")
        _log_audit("login_failed", actor=username, meta={"reason": "bad_password"})
        return

    # password ok
    user["failed_password_attempts"] = 0
    _save_users_doc(doc)

    # OTP path
    if OTP_ENABLED:
        otp = _make_otp_code()
        user["otp_hash"] = _hash_otp(otp)
        user["otp_expires_utc"] = _to_utc_str(_utc_now() + timedelta(minutes=OTP_TTL_MINUTES))
        user["failed_otp_attempts"] = 0
        _save_users_doc(doc)

        try:
            _send_otp_email(str(user.get("email", "")), otp)
        except Exception as e:
            st.error(f"Could not send OTP email. Check SMTP settings. ({e})")
            _log_audit("otp_send_failed", actor=username, meta={"error": str(e)})
            return

        st.session_state.auth_stage = "otp"
        st.session_state.pending_user = username
        _log_audit("otp_sent", actor=username, meta={"to": user.get("email", "")})

        st.success("OTP sent. Please enter it below.")
        st.rerun()
        return

    # No OTP mode => logged in
    st.session_state.auth_ok = True
    st.session_state.auth_user = username
    st.session_state.auth_role = user.get("role", "viewer")
    st.session_state.auth_client = user.get("client_name") or user.get("client") or None
    st.session_state.auth_stage = "login"
    st.session_state.pending_user = None

    _log_audit("login_success", actor=username, meta={"role": st.session_state.auth_role})
    st.rerun()

def otp_ui():
    ensure_session()
    username = st.session_state.pending_user

    if not username:
        st.session_state.auth_stage = "login"
        st.warning("OTP session expired. Please login again.")
        return

    user, doc = _find_user(username)
    if not user:
        st.session_state.auth_stage = "login"
        st.error("User not found. Please login again.")
        return

    if _is_locked(user):
        st.error("Account temporarily locked. Please try again later.")
        return

    st.subheader("✅ Enter OTP")

    # VERY IMPORTANT: unique form key (only ONE otp form exists)
    with st.form(key="otp_form_unique"):
        otp = st.text_input("6-digit OTP", key="otp_input_unique")
        ok = st.form_submit_button("Verify OTP", use_container_width=True)

    if not ok:
        return

    expires = _parse_utc(str(user.get("otp_expires_utc", "")))
    if not expires or _utc_now() > expires:
        st.error("OTP expired. Please login again.")
        _log_audit("otp_failed", actor=username, meta={"reason": "expired"})
        st.session_state.auth_stage = "login"
        st.session_state.pending_user = None
        return

    if not _check_otp((otp or "").strip(), str(user.get("otp_hash", ""))):
        user["failed_otp_attempts"] = int(user.get("failed_otp_attempts", 0)) + 1
        if user["failed_otp_attempts"] >= MAX_OTP_FAILS:
            _lock_user(user)
        _save_users_doc(doc)

        st.error("Incorrect OTP.")
        _log_audit("otp_failed", actor=username, meta={"reason": "bad_otp"})
        return

    # OTP success => LOGGED IN
    user["failed_otp_attempts"] = 0
    user["otp_hash"] = ""
    user["otp_expires_utc"] = ""
    _save_users_doc(doc)

    st.session_state.auth_ok = True
    st.session_state.auth_user = username
    st.session_state.auth_role = user.get("role", "viewer")
    st.session_state.auth_client = user.get("client_name") or user.get("client") or None
    st.session_state.auth_stage = "login"
    st.session_state.pending_user = None

    _log_audit("login_success_otp", actor=username, meta={"role": st.session_state.auth_role})
    st.rerun()

# ============================================================
# GATE + ROLE
# ============================================================
def require_login():
    ensure_session()

    # If already logged in, enforce timeout
    if st.session_state.auth_ok:
        if is_session_expired():
            force_logout("Session expired (idle). Please login again.")
        touch_session()
        return

    # Not logged in yet:
    if st.session_state.auth_stage == "otp":
        # show OTP screen only
        otp_ui()   # your existing otp function
        st.stop()

    # otherwise show login screen
    login_ui()    # your existing login function
    st.stop()

def require_role(roles: list[str]):
    ensure_session()
    role = st.session_state.get("auth_role") or ""
    if role not in roles:
        st.error("You do not have permission for this section.")
        st.stop()

def logout_button():
    if st.button("Logout", key="logout_btn_unique"):
        st.session_state.auth_ok = False
        st.session_state.auth_user = None
        st.session_state.auth_role = None
        st.session_state.auth_client = None
        st.session_state.auth_stage = "login"
        st.session_state.pending_user = None
        st.rerun()
