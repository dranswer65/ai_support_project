from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def _now_stamp() -> str:
    # e.g. 20260210_114500Z
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _safe_load_json(path: Path, default: Any):
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def _safe_write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _add_path_to_zip(z: zipfile.ZipFile, src_path: Path, arc_prefix: str):
    """
    Adds file/dir into zip under arc_prefix/<relative>.
    """
    src_path = src_path.resolve()
    if src_path.is_file():
        arcname = f"{arc_prefix}/{src_path.name}"
        z.write(src_path, arcname=arcname)
        return

    if src_path.is_dir():
        for p in src_path.rglob("*"):
            if p.is_file():
                rel = p.relative_to(src_path).as_posix()
                arcname = f"{arc_prefix}/{rel}"
                z.write(p, arcname=arcname)


def _ensure_within(base: Path, target: Path) -> None:
    base = base.resolve()
    target = target.resolve()
    if base not in target.parents and base != target:
        raise ValueError("Unsafe path (outside base).")


def create_backup(
    base_dir: Path,
    backup_dir: Path,
    client_name: str,
    include_chat_logs: bool = True,
) -> dict:
    """
    Creates a ZIP backup:
      backups/<client>/<stamp>__<client>.zip
    Includes:
      - clients/<client>/**
      - usage/usage_log.json (if exists)
      - audit/audit_log.json (if exists)
      - admin/users.json + admin/audit_log.json (if exists)
      - billing/** (if exists)
      - logs/** (optional)
    """
    base_dir = base_dir.resolve()
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = _now_stamp()
    safe_client = client_name.strip()
    if not safe_client:
        raise ValueError("client_name is required")

    # Paths
    clients_dir = base_dir / "clients" / safe_client
    usage_file = base_dir / "usage" / "usage_log.json"
    audit_file = base_dir / "audit" / "audit_log.json"
    admin_users = base_dir / "admin" / "users.json"
    admin_audit = base_dir / "admin" / "audit_log.json"
    billing_dir = base_dir / "billing"
    logs_dir = base_dir / "logs"

    # ZIP target
    client_backup_dir = backup_dir / safe_client
    client_backup_dir.mkdir(parents=True, exist_ok=True)
    zip_path = client_backup_dir / f"{stamp}__{safe_client}.zip"

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "client_name": safe_client,
        "includes": [],
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # client workspace
        if clients_dir.exists():
            _add_path_to_zip(z, clients_dir, f"clients/{safe_client}")
            manifest["includes"].append(f"clients/{safe_client}/**")

        # global logs
        if usage_file.exists():
            _add_path_to_zip(z, usage_file, "usage")
            manifest["includes"].append("usage/usage_log.json")

        if audit_file.exists():
            _add_path_to_zip(z, audit_file, "audit")
            manifest["includes"].append("audit/audit_log.json")

        # admin store
        if admin_users.exists():
            _add_path_to_zip(z, admin_users, "admin")
            manifest["includes"].append("admin/users.json")

        if admin_audit.exists():
            _add_path_to_zip(z, admin_audit, "admin")
            manifest["includes"].append("admin/audit_log.json")

        # billing store
        if billing_dir.exists() and billing_dir.is_dir():
            _add_path_to_zip(z, billing_dir, "billing")
            manifest["includes"].append("billing/**")

        # optional: logs
        if include_chat_logs and logs_dir.exists() and logs_dir.is_dir():
            _add_path_to_zip(z, logs_dir, "logs")
            manifest["includes"].append("logs/**")

        # embed manifest inside zip
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return {
        "ok": True,
        "client": safe_client,
        "backup_id": zip_path.stem,  # filename without .zip
        "zip_path": str(zip_path),
        "created_utc": manifest["created_utc"],
    }


def list_backups(backup_dir: Path, client_name: str) -> list[dict]:
    backup_dir = backup_dir.resolve()
    safe_client = client_name.strip()
    client_backup_dir = backup_dir / safe_client
    if not client_backup_dir.exists():
        return []

    items = []
    for z in sorted(client_backup_dir.glob("*.zip"), reverse=True):
        items.append({
            "backup_id": z.stem,
            "zip_path": str(z),
            "size_bytes": z.stat().st_size,
            "modified_utc": datetime.fromtimestamp(z.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return items


def restore_backup(
    base_dir: Path,
    backup_dir: Path,
    client_name: str,
    backup_id: str,
    allow_overwrite: bool = True,
) -> dict:
    """
    Restores from backups/<client>/<backup_id>.zip

    Safe restore rules:
    - Only writes into base_dir (clients/, usage/, audit/, admin/, billing/, logs/)
    - Rejects zip path traversal.
    """
    base_dir = base_dir.resolve()
    backup_dir = backup_dir.resolve()
    safe_client = client_name.strip()
    if not safe_client:
        raise ValueError("client_name is required")

    zip_path = (backup_dir / safe_client / f"{backup_id}.zip").resolve()
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    _ensure_within(backup_dir, zip_path)

    allowed_prefixes = {
        "clients/",
        "usage/",
        "audit/",
        "admin/",
        "billing/",
        "logs/",
        "manifest.json",
    }

    restored = []

    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.infolist():
            name = member.filename.replace("\\", "/")

            # allow manifest
            if name == "manifest.json":
                continue

            if not any(name.startswith(p) for p in allowed_prefixes if p != "manifest.json"):
                # ignore unknown stuff
                continue

            # protect against zip slip
            if ".." in Path(name).parts:
                raise ValueError("Unsafe zip content (path traversal).")

            target = (base_dir / name).resolve()
            _ensure_within(base_dir, target)

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not allow_overwrite:
                continue

            with z.open(member, "r") as src, open(target, "wb") as dst:
                dst.write(src.read())

            restored.append(name)

    return {
        "ok": True,
        "client": safe_client,
        "backup_id": backup_id,
        "restored_count": len(restored),
        "restored_paths": restored[:200],  # keep response small
    }
