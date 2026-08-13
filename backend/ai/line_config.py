from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LINE_CONFIG_PATH = REPO_ROOT / "process" / "line_config.json"


class LineConfigError(ValueError):
    pass


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


def validate_line_config(cfg: dict[str, Any]) -> None:
    if not isinstance(cfg, dict):
        raise LineConfigError("line_config must be a JSON object")
    if "lines" not in cfg or not isinstance(cfg.get("lines"), dict):
        raise LineConfigError("line_config.lines must be an object")

    lines: dict[str, Any] = cfg["lines"]
    if not lines:
        raise LineConfigError("line_config.lines cannot be empty")

    all_machines: set[str] = set()
    for line_id, line in lines.items():
        if not isinstance(line_id, str) or not line_id.strip():
            raise LineConfigError("line id must be a non-empty string")
        if not isinstance(line, dict):
            raise LineConfigError(f"{line_id}: line config must be an object")

        fm = line.get("forming_machine")
        lm = line.get("labeling_machines")
        if not isinstance(fm, str) or not fm.strip():
            raise LineConfigError(f"{line_id}: forming_machine must be a non-empty string")
        if not isinstance(lm, list) or len(lm) != 2 or not all(isinstance(x, str) and x.strip() for x in lm):
            raise LineConfigError(f"{line_id}: labeling_machines must be a list of 2 machine ids")

        if fm in all_machines:
            raise LineConfigError(f"{line_id}: duplicate machine id: {fm}")
        all_machines.add(fm)
        for m in lm:
            if m in all_machines:
                raise LineConfigError(f"{line_id}: duplicate machine id: {m}")
            all_machines.add(m)

        fr = line.get("forming_rate_per_h")
        lr = line.get("labeling_rate_per_h")
        if not isinstance(fr, (int, float)) or fr <= 0:
            raise LineConfigError(f"{line_id}: forming_rate_per_h must be > 0")
        if not isinstance(lr, (int, float)) or lr <= 0:
            raise LineConfigError(f"{line_id}: labeling_rate_per_h must be > 0")

        prefixes = line.get("sku_prefixes")
        if not isinstance(prefixes, list) or not prefixes or not all(isinstance(p, str) and p.strip() for p in prefixes):
            raise LineConfigError(f"{line_id}: sku_prefixes must be a non-empty list of strings")

        setup = line.get("setup_rules") or {}
        if not isinstance(setup, dict):
            raise LineConfigError(f"{line_id}: setup_rules must be an object")
        cch = setup.get("color_change_h")
        if cch is None or not isinstance(cch, int) or cch < 0:
            raise LineConfigError(f"{line_id}: setup_rules.color_change_h must be an int >= 0")
        mch = setup.get("mold_change_h")
        if mch is not None and (not isinstance(mch, int) or mch < 0):
            raise LineConfigError(f"{line_id}: setup_rules.mold_change_h must be an int >= 0")
        groups = setup.get("mold_change_prefix_groups")
        if groups is not None and not isinstance(groups, dict):
            raise LineConfigError(f"{line_id}: setup_rules.mold_change_prefix_groups must be an object")


def load_line_config(path: Path = LINE_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        # Ephemeral FS (Railway): try DB-backed document, then restore file cache.
        try:
            from .db_store import get_document_payload  # local import

            row = get_document_payload("line_config")
            if row is not None:
                payload, _ts = row
                if isinstance(payload, dict):
                    try:
                        save_line_config(payload, path)
                    except Exception:
                        pass
                    validate_line_config(payload)
                    return payload
        except Exception:
            pass
        raise LineConfigError(f"Missing line config: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise LineConfigError("line config must be a JSON object")
    validate_line_config(cfg)
    return cfg


def save_line_config(cfg: dict[str, Any], path: Path = LINE_CONFIG_PATH) -> None:
    validate_line_config(cfg)
    _atomic_write_json(path, cfg)

    # Persist to DB too so AI-edits survive Railway restarts.
    try:
        from .db_store import upsert_document_payload  # local import

        upsert_document_payload("line_config", cfg)
    except Exception:
        pass
