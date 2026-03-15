from __future__ import annotations

import json
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def _safe_save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def log_error(
    errors_file: Path,
    *,
    where: str,
    message: str,
    request_path: str = "",
    method: str = "",
    client_ip: str = "",
    actor: str = "system",
    extra: dict | None = None,
    exc: Exception | None = None,
) -> None:
    logs = _safe_load_json(errors_file, [])
    if not isinstance(logs, list):
        logs = []

    entry = {
        "ts_utc": _utc_now_iso(),
        "where": where,
        "message": message,
        "request_path": request_path,
        "method": method,
        "client_ip": client_ip,
        "actor": actor,
        "extra": extra or {},
    }

    if exc is not None:
        entry["exception_type"] = type(exc).__name__
        entry["traceback"] = traceback.format_exc()[:20000]  # prevent huge file

    logs.append(entry)
    logs = logs[-2000:]  # keep last 2000 errors
    _safe_save_json(errors_file, logs)


def get_errors(errors_file: Path, limit: int = 200) -> list[dict]:
    logs = _safe_load_json(errors_file, [])
    if not isinstance(logs, list):
        return []
    return list(reversed(logs[-max(1, min(limit, 2000)):]))


def clear_errors(errors_file: Path) -> None:
    _safe_save_json(errors_file, [])
