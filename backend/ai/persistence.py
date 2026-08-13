from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .db_store import db_enabled, get_document_payload, upsert_document_payload


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"


DocKind = Literal["json", "text"]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            tmp_path = tmp.name
        shutil.move(tmp_path, str(path))
        tmp_path = None
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _file_updated_at(path: Path) -> datetime | None:
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return ts
    except Exception:
        return None


def _sync_one(*, key: str, path: Path, kind: DocKind) -> dict[str, Any]:
    """Bidirectional sync between DB(document) and a process file.

    Policy:
    - If only DB exists: write DB -> file
    - If only file exists: write file -> DB
    - If both exist: pick the newer by timestamp (DB updated_at vs file mtime)
    """
    if not db_enabled():
        return {"key": key, "path": str(path), "action": "skip", "reason": "db_disabled"}

    db_row = get_document_payload(key)
    file_exists = path.exists()

    db_payload: Any | None = None
    db_ts: datetime | None = None
    if db_row is not None:
        db_payload, db_ts = db_row

    file_ts = _file_updated_at(path) if file_exists else None

    # If both exist and the payload is identical, do nothing (avoid noisy rewrites).
    if db_row is not None and file_exists:
        try:
            file_payload = _read_json(path) if kind == "json" else _read_text(path)
            if file_payload is not None and file_payload == db_payload:
                return {"key": key, "path": str(path), "action": "skip", "reason": "in_sync"}
        except Exception:
            pass

    # DB only -> file
    if db_row is not None and not file_exists:
        if kind == "json":
            _atomic_write_json(path, db_payload)
        else:
            _atomic_write_text(path, str(db_payload or ""))
        return {"key": key, "path": str(path), "action": "db->file"}

    # File only -> DB
    if db_row is None and file_exists:
        payload = _read_json(path) if kind == "json" else _read_text(path)
        if payload is None:
            return {"key": key, "path": str(path), "action": "skip", "reason": "file_unreadable"}
        upsert_document_payload(key, payload)
        return {"key": key, "path": str(path), "action": "file->db"}

    # Neither exists
    if db_row is None and not file_exists:
        return {"key": key, "path": str(path), "action": "skip", "reason": "missing_both"}

    # Both exist: decide by timestamp
    if db_ts is None or file_ts is None:
        # Fallback: trust DB as canonical when timestamps are missing.
        if kind == "json":
            _atomic_write_json(path, db_payload)
        else:
            _atomic_write_text(path, str(db_payload or ""))
        return {"key": key, "path": str(path), "action": "db->file", "reason": "missing_ts"}

    if file_ts > db_ts:
        payload = _read_json(path) if kind == "json" else _read_text(path)
        if payload is None:
            return {"key": key, "path": str(path), "action": "skip", "reason": "file_unreadable"}
        upsert_document_payload(key, payload)
        return {
            "key": key,
            "path": str(path),
            "action": "file->db",
            "reason": "file_newer",
            "file_ts": file_ts.isoformat(),
            "db_ts": db_ts.isoformat(),
        }

    # DB newer (or equal) -> file
    if kind == "json":
        _atomic_write_json(path, db_payload)
    else:
        _atomic_write_text(path, str(db_payload or ""))
    return {
        "key": key,
        "path": str(path),
        "action": "db->file",
        "reason": "db_newer_or_equal",
        "file_ts": file_ts.isoformat(),
        "db_ts": db_ts.isoformat(),
    }


def bootstrap_process_cache() -> dict[str, Any]:
    """Sync DB <-> process/ files so scheduling stays compatible on Railway."""
    if not db_enabled():
        return {"enabled": False, "actions": []}

    # Keep keys stable; these are referenced in store modules too.
    items: list[tuple[str, Path, DocKind]] = [
        ("overrides", PROCESS_DIR / "overrides.json", "json"),
        ("line_config", PROCESS_DIR / "line_config.json", "json"),
        ("agent_state", PROCESS_DIR / "agent_state.json", "json"),
        ("erp_orders", PROCESS_DIR / "orders_erp.json", "json"),
        ("erp_inventory", PROCESS_DIR / "inventory_erp.json", "json"),
        ("schedule_result", PROCESS_DIR / "schedule_result.json", "json"),
        ("schedule_gantt_html", PROCESS_DIR / "schedule_gantt.html", "text"),
    ]

    actions: list[dict[str, Any]] = []
    for key, path, kind in items:
        try:
            actions.append(_sync_one(key=key, path=path, kind=kind))
        except Exception as e:
            actions.append({"key": key, "path": str(path), "action": "error", "error": f"{type(e).__name__}: {e}"})

    return {"enabled": True, "actions": actions}
