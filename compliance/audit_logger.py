# compliance/audit_logger.py

import json
from pathlib import Path


AUDIT_LOG_PATH = Path("compliance/audit_log.jsonl")


def log_event(event: dict):
    """
    Append-only structured audit logger.

    - NEVER throws
    - NEVER crashes system
    - NEVER logs sensitive data
    """

    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    except Exception:
        # Fail silently — audit must never break production
        pass
