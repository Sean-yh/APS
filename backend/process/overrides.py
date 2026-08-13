from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"
DEFAULT_OVERRIDES_PATH = PROCESS_DIR / "overrides.json"


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


def load_overrides(path: Path = DEFAULT_OVERRIDES_PATH) -> dict[str, Any]:
    if not path.exists():
        # Railway FS is ephemeral; fall back to DB-backed document when available.
        try:
            from ai.db_store import get_document_payload  # type: ignore

            row = get_document_payload("overrides")
            if row is not None:
                payload, _ts = row
                if isinstance(payload, dict):
                    try:
                        save_overrides(payload, path)
                    except Exception:
                        pass
                    containers = payload.get("containers") if isinstance(payload.get("containers"), dict) else {}
                    orders = payload.get("orders") if isinstance(payload.get("orders"), dict) else {}
                    return {"containers": dict(containers), "orders": dict(orders)}
        except Exception:
            pass
        return {"containers": {}, "orders": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return {"containers": {}, "orders": {}}
    containers = doc.get("containers") if isinstance(doc.get("containers"), dict) else {}
    orders = doc.get("orders") if isinstance(doc.get("orders"), dict) else {}
    return {"containers": dict(containers), "orders": dict(orders)}


def save_overrides(payload: dict[str, Any], path: Path = DEFAULT_OVERRIDES_PATH) -> None:
    containers = payload.get("containers") if isinstance(payload.get("containers"), dict) else {}
    orders = payload.get("orders") if isinstance(payload.get("orders"), dict) else {}
    out = {"containers": containers, "orders": orders}
    _atomic_write_json(path, out)

    # Persist to DB too so constraints survive Railway restarts.
    try:
        from ai.db_store import upsert_document_payload  # type: ignore

        upsert_document_payload("overrides", out)
    except Exception:
        pass


def apply_overrides_to_orders(raw_orders: list[dict[str, Any]], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply overrides (priority / due_override / deadline_override) to raw ERP order rows.

    The scheduler reads these fields:
    - priority: int (higher scheduled earlier)
    - due_override: ISO datetime or YYYY-MM-DD(/HH:MM) string; replaces duedate for sorting/KPI
    - deadline_override: ISO datetime string; replaces computed deadline (ceil-hour)
    """
    containers = overrides.get("containers") if isinstance(overrides.get("containers"), dict) else {}
    orders = overrides.get("orders") if isinstance(overrides.get("orders"), dict) else {}

    out: list[dict[str, Any]] = []
    for row in raw_orders:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        try:
            oid = str(int(r.get("c_orderline_id")))
        except Exception:
            oid = ""
        cid = str(r.get("poreference") or "").strip()

        # Apply container-level overrides first…
        if cid and cid in containers and isinstance(containers[cid], dict):
            cfg = containers[cid]
            if "priority" in cfg:
                try:
                    r["priority"] = max(int(r.get("priority") or 0), int(cfg.get("priority") or 0))
                except Exception:
                    pass
            if cfg.get("due_override"):
                r["due_override"] = str(cfg.get("due_override"))
            if cfg.get("deadline_override"):
                r["deadline_override"] = str(cfg.get("deadline_override"))

        # …then order-level overrides win.
        if oid and oid in orders and isinstance(orders[oid], dict):
            cfg = orders[oid]
            if "priority" in cfg:
                try:
                    r["priority"] = max(int(r.get("priority") or 0), int(cfg.get("priority") or 0))
                except Exception:
                    pass
            if cfg.get("due_override"):
                r["due_override"] = str(cfg.get("due_override"))
            if cfg.get("deadline_override"):
                r["deadline_override"] = str(cfg.get("deadline_override"))

        out.append(r)
    return out
