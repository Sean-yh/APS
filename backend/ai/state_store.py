from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"
DEFAULT_AGENT_STATE_PATH = PROCESS_DIR / "agent_state.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
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
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
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


def load_agent_state(path: Path = DEFAULT_AGENT_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        # Ephemeral FS (Railway): fall back to DB-backed doc.
        try:
            from .db_store import get_document_payload  # local import

            row = get_document_payload("agent_state")
            if row is not None:
                payload, _ts = row
                if isinstance(payload, dict):
                    try:
                        save_agent_state(payload, path)
                    except Exception:
                        pass
                    return dict(payload)
        except Exception:
            pass
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save_agent_state(state: dict[str, Any], path: Path = DEFAULT_AGENT_STATE_PATH) -> None:
    payload = state if isinstance(state, dict) else {}
    _atomic_write_json(path, payload)

    # Persist to DB as well so the last confirmed production-context survives restarts.
    try:
        from .db_store import upsert_document_payload  # local import

        upsert_document_payload("agent_state", payload)
    except Exception:
        pass


def load_production_context_check(path: Path = DEFAULT_AGENT_STATE_PATH) -> dict[str, Any] | None:
    st = load_agent_state(path)
    ctx = st.get("production_context_check")
    if not isinstance(ctx, dict):
        return None
    out = dict(ctx)
    ts = out.get("timestamp")
    if isinstance(ts, str) and ts:
        try:
            out["timestamp"] = datetime.fromisoformat(ts)
        except Exception:
            out["timestamp"] = None
    return out


def save_production_context_check(ctx: dict[str, Any], path: Path = DEFAULT_AGENT_STATE_PATH) -> None:
    st = load_agent_state(path)
    out = dict(ctx) if isinstance(ctx, dict) else {}
    ts = out.get("timestamp")
    if isinstance(ts, datetime):
        out["timestamp"] = ts.isoformat()
    st["production_context_check"] = out
    save_agent_state(st, path)
