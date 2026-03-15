# conversation_manager.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

import psycopg


def _db_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL not configured.")

    # Railway sometimes provides postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    # Railway/SQLAlchemy sometimes provides driver-prefixed URLs:
    # postgresql+psycopg://...  -> postgresql://...
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://") :]

    # Some setups might use async driver:
    # postgresql+asyncpg://... -> postgresql://...
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]

    return url



@dataclass
class WAState:
    wa_id: str
    state: str = "NEW"
    order_id: str = ""
    order_attempts: int = 0
    last_intent: str = ""
    last_user_msg: str = ""
    issue_text: str = ""


def ensure_tables() -> None:
    sql1 = """
    CREATE TABLE IF NOT EXISTS wa_conversations (
      wa_id TEXT PRIMARY KEY,
      state TEXT NOT NULL DEFAULT 'NEW',
      order_id TEXT DEFAULT '',
      order_attempts INT NOT NULL DEFAULT 0,
      last_intent TEXT DEFAULT '',
      last_user_msg TEXT DEFAULT '',
      issue_text TEXT DEFAULT '',
      updated_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    sql2 = """
    CREATE TABLE IF NOT EXISTS wa_messages (
      id BIGSERIAL PRIMARY KEY,
      wa_id TEXT NOT NULL,
      direction TEXT NOT NULL,
      body TEXT NOT NULL,
      ts_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    sql3 = "CREATE INDEX IF NOT EXISTS idx_wa_messages_wa_id_ts ON wa_messages (wa_id, ts_utc DESC);"

    with psycopg.connect(_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql1)
            cur.execute(sql2)
            cur.execute(sql3)

            # Safe migration for older databases
            cur.execute(
                "ALTER TABLE wa_conversations "
                "ADD COLUMN IF NOT EXISTS issue_text TEXT DEFAULT '';"
            )

        conn.commit()


def get_state(wa_id: str) -> WAState:
    with psycopg.connect(_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wa_id, state, order_id, order_attempts,
                       last_intent, last_user_msg, issue_text
                FROM wa_conversations
                WHERE wa_id=%s
                """,
                (wa_id,),
            )

            row = cur.fetchone()

            if not row:
                cur.execute(
                    "INSERT INTO wa_conversations (wa_id, state) VALUES (%s, 'NEW')",
                    (wa_id,),
                )
                conn.commit()
                return WAState(wa_id=wa_id)

            return WAState(
                wa_id=row[0],
                state=row[1] or "NEW",
                order_id=row[2] or "",
                order_attempts=int(row[3] or 0),
                last_intent=row[4] or "",
                last_user_msg=row[5] or "",
                issue_text=row[6] or "",
            )


def save_state(s: WAState) -> None:
    with psycopg.connect(_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wa_conversations
                SET state=%s,
                    order_id=%s,
                    order_attempts=%s,
                    last_intent=%s,
                    last_user_msg=%s,
                    issue_text=%s,
                    updated_utc=NOW()
                WHERE wa_id=%s
                """,
                (
                    s.state,
                    s.order_id,
                    int(s.order_attempts),
                    s.last_intent,
                    s.last_user_msg,
                    s.issue_text,
                    s.wa_id,
                ),
            )
        conn.commit()


def log_message(wa_id: str, direction: str, body: str) -> None:
    direction = (direction or "").strip().lower()
    if direction not in {"in", "out"}:
        direction = "in"

    with psycopg.connect(_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wa_messages (wa_id, direction, body) VALUES (%s, %s, %s)",
                (wa_id, direction, body),
            )
        conn.commit()


# ---------------------------
# Simple intent detection
# ---------------------------
_ORDER_RE = re.compile(r"\b([A-Z0-9]{6,20})\b", re.I)


def detect_intent(text: str) -> str:
    t = (text or "").strip().lower()

    if not t:
        return "empty"
    if t in {"hi", "hello", "hey", "salam", "assalam"}:
        return "greeting"
    if "reset" in t or "start over" in t:
        return "reset"
    if t in {"thanks", "thank you", "thx"}:
        return "thanks"
    if "agent" in t or "human" in t or "call me" in t:
        return "handoff"
    if _ORDER_RE.search(text or ""):
        return "order_id"
    if any(k in t for k in [
        "not working", "refund", "late",
        "complain", "angry", "bad",
        "problem", "issue"
    ]):
        return "complaint"
    return "general"


def extract_order_id(text: str) -> str:
    m = _ORDER_RE.search(text or "")
    return (m.group(1) if m else "").strip()
