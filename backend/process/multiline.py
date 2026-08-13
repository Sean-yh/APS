from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any


# Import flexibly so this module works both when imported as `process.multiline`
# (from repo root) and when executed from within `process/`.
try:  # pragma: no cover
    from process.line_scheduler import _ceil_hour, _sku_matches_prefixes, generate_line_schedule  # type: ignore
    from process.visualize_schedule import _render_html as render_gantt_html  # type: ignore
    from process.overrides import apply_overrides_to_orders, load_overrides  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    from line_scheduler import _ceil_hour, _sku_matches_prefixes, generate_line_schedule  # type: ignore
    from visualize_schedule import _render_html as render_gantt_html  # type: ignore
    from overrides import apply_overrides_to_orders, load_overrides  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"

DEFAULT_LINE_CONFIG_PATH = PROCESS_DIR / "line_config.json"
DEFAULT_ORDERS_PATH = PROCESS_DIR / "orders_erp.json"
DEFAULT_INVENTORY_PATH = PROCESS_DIR / "inventory_erp.json"

# Virtual orders for "extra production" (buffer stock). These should be schedulable
# (show up in machine timelines) but excluded from KPI/containers.
BUFFER_POREFERENCE = "__BUFFER__"


def atomic_write_json(path: Path, payload: Any) -> None:
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


def load_api_list(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and "data" in doc:
        data = doc["data"]
        if not isinstance(data, list):
            raise TypeError(f"{path}: expected dict['data'] to be list, got {type(data)}")
        return [r for r in data if isinstance(r, dict)]
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    raise TypeError(f"{path}: expected list or dict with 'data', got {type(doc)}")


def extract_snapshot_start_time(orders_path: Path) -> datetime:
    try:
        doc = json.loads(orders_path.read_text(encoding="utf-8"))
        ts = str(doc.get("timestamp") or "").strip()
        if ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # If ERP returns an explicit timezone (e.g. "Z"), convert to local time
            # so UI/browser and schedule timestamps align in local dev.
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return _ceil_hour(dt)
    except Exception:
        pass
    return _ceil_hour(datetime.now())


def load_line_config(path: Path = DEFAULT_LINE_CONFIG_PATH) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("lines"), dict):
        raise TypeError(f"{path}: invalid line config format")
    return cfg


def merge_schedules(*, start_time: datetime, line_schedules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # Merge machines (machine ids are disjoint across lines).
    machines: dict[str, list[dict[str, Any]]] = {}
    orders: list[dict[str, Any]] = []
    inv_skus: dict[str, list[int]] = {}
    horizon_h = 0
    setup_count = 0

    for line_id, sched in line_schedules.items():
        meta = sched.get("meta") if isinstance(sched.get("meta"), dict) else {}
        horizon_h = max(horizon_h, int(meta.get("horizon_h") or 0))
        kpi = sched.get("kpi") if isinstance(sched.get("kpi"), dict) else {}
        setup_count += int(kpi.get("setup_count") or 0)

        m = sched.get("machines") if isinstance(sched.get("machines"), dict) else {}
        for mid, tasks in m.items():
            if isinstance(tasks, list):
                machines[str(mid)] = tasks

        o = sched.get("orders") if isinstance(sched.get("orders"), list) else []
        for row in o:
            if isinstance(row, dict):
                # Keep buffer tasks in the gantt via machine timelines, but exclude them from
                # KPI and container aggregation (otherwise "extra production" pollutes metrics).
                if str(row.get("poreference") or "").strip() == BUFFER_POREFERENCE:
                    continue
                row2 = dict(row)
                row2["line"] = line_id
                orders.append(row2)

        inv = sched.get("inventory") if isinstance(sched.get("inventory"), dict) else {}
        skus = inv.get("skus") if isinstance(inv.get("skus"), dict) else {}
        for sku, series in skus.items():
            if isinstance(series, list) and series and all(isinstance(x, (int, float)) for x in series):
                inv_skus[str(sku)] = [int(x) for x in series]

    # Align inventory series to max horizon for combined visualization.
    target_len = horizon_h + 1 if horizon_h > 0 else 0
    if target_len > 0:
        for sku, series in list(inv_skus.items()):
            if len(series) < target_len:
                inv_skus[sku] = series + [series[-1]] * (target_len - len(series))
            elif len(series) > target_len:
                inv_skus[sku] = series[:target_len]

    # Recompute containers across all orders (supports cross-line poreference).
    container_groups: dict[str, list[dict[str, Any]]] = {}
    for r in orders:
        cid = str(r.get("poreference") or "").strip()
        if not cid:
            cid = f"__NO_POREFERENCE__{r.get('c_orderline_id')}"
        container_groups.setdefault(cid, []).append(r)

    def _dt(v: Any) -> datetime | None:
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v))
        except Exception:
            return None

    container_rows: list[dict[str, Any]] = []
    containers_on_time = 0
    containers_tardiness_h_sum = 0.0
    containers_expired_before_start_count = 0
    for cid, group in sorted(container_groups.items(), key=lambda kv: kv[0]):
        due_dt = min([d for d in (_dt(o.get("due")) for o in group) if d is not None], default=start_time)
        deadline_dt = min([d for d in (_dt(o.get("deadline")) for o in group) if d is not None], default=start_time)
        start_dt = min([d for d in (_dt(o.get("start")) for o in group) if d is not None], default=start_time)
        end_dt = max([d for d in (_dt(o.get("end")) for o in group) if d is not None], default=start_time)

        qty_sum = sum(int(o.get("quantity") or 0) for o in group)
        order_ids = sorted(int(o.get("c_orderline_id") or -1) for o in group)

        lateness_h = max(0.0, (end_dt - deadline_dt).total_seconds() / 3600.0)
        expired_before_start = deadline_dt <= start_time
        is_on_time = (not expired_before_start) and lateness_h <= 1e-9

        containers_on_time += int(is_on_time)
        containers_expired_before_start_count += int(expired_before_start)
        containers_tardiness_h_sum += lateness_h

        container_rows.append(
            {
                "container_id": cid,
                "order_ids": order_ids,
                "orders_count": len(order_ids),
                "total_quantity": qty_sum,
                "due": due_dt.isoformat(),
                "deadline": deadline_dt.isoformat(),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "on_time": is_on_time,
                "expired_before_start": expired_before_start,
                "lateness_h": lateness_h,
            }
        )

    # Recompute global order KPI from order rows.
    orders_total = len(orders)
    orders_on_time = 0
    orders_expired = 0
    tardiness_h_sum = 0.0
    for r in orders:
        orders_on_time += int(bool(r.get("on_time")))
        orders_expired += int(bool(r.get("expired_before_start")))
        tardiness_h_sum += float(r.get("lateness_h") or 0.0)

    combined = {
        "meta": {
            "line": "ALL",
            "start_time": start_time.isoformat(),
            "time_step_h": 1,
            "horizon_h": horizon_h,
            "lines": {
                lid: {
                    "forming_machine": (line_schedules[lid].get("meta") or {}).get("forming_machine"),
                    "labeling_machines": (line_schedules[lid].get("meta") or {}).get("labeling_machines"),
                }
                for lid in sorted(line_schedules.keys())
            },
            "assumptions": {
                "delivery_unit": "container(poreference)",
                "container_completion_rule": "max_label_end",
                "container_deadline_agg": "min",
            },
        },
        "kpi": {
            "orders_total": orders_total,
            "orders_on_time": orders_on_time,
            "orders_expired_before_start": orders_expired,
            "on_time_rate": (orders_on_time / orders_total) if orders_total else 1.0,
            "total_tardiness_h": tardiness_h_sum,
            "total_tardiness_days": tardiness_h_sum / 24.0,
            "containers_total": len(container_rows),
            "containers_on_time": containers_on_time,
            "containers_expired_before_start": containers_expired_before_start_count,
            "containers_on_time_rate": (containers_on_time / len(container_rows)) if container_rows else 1.0,
            "total_container_tardiness_h": containers_tardiness_h_sum,
            "total_container_tardiness_days": containers_tardiness_h_sum / 24.0,
            "setup_count": setup_count,
        },
        "machines": machines,
        "orders": orders,
        "containers": container_rows,
        "inventory": {"start_time": start_time.isoformat(), "time_step_h": 1, "skus": inv_skus},
        "validation": {},
    }
    return combined


def generate_all_lines(
    *,
    line_config_path: Path = DEFAULT_LINE_CONFIG_PATH,
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    max_hours: int = 8000,
    apply_downtime: bool = True,
    forming_states_by_machine: dict[str, str] | None = None,
    setup_remaining_by_machine: dict[str, int] | None = None,
    overrides: dict[str, Any] | None = None,
    extra_production: dict[str, int] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    cfg = load_line_config(line_config_path)
    lines = cfg.get("lines") if isinstance(cfg.get("lines"), dict) else {}
    if not lines:
        raise RuntimeError("line_config.json: missing lines")

    raw_orders = load_api_list(orders_path)
    raw_inventory = load_api_list(inventory_path)
    start_time = extract_snapshot_start_time(orders_path)
    # Apply overrides (priority / due_override / deadline_override) on top of ERP snapshot.
    # This keeps ERP snapshots immutable while letting users/AI "nudge" scheduling decisions.
    if overrides is None:
        try:
            overrides = load_overrides()
        except Exception:
            overrides = {"containers": {}, "orders": {}}
    try:
        raw_orders = apply_overrides_to_orders(raw_orders, overrides)
    except Exception:
        raw_orders = list(raw_orders)

    # Optionally inject "buffer" orders that represent extra production of semi-finished goods.
    # These should be lowest priority and due after the scheduling horizon.
    if isinstance(extra_production, dict) and extra_production:
        # Compute a safe starting id for virtual orders.
        min_id = 0
        for r in raw_orders:
            if not isinstance(r, dict):
                continue
            try:
                min_id = min(min_id, int(r.get("c_orderline_id") or 0))
            except Exception:
                continue
        next_id = min(-1, min_id - 1) if min_id <= 0 else -1

        # Precompute all sku prefixes that belong to some configured line.
        all_prefixes: list[str] = []
        for _lid, c in sorted(lines.items(), key=lambda kv: kv[0]):
            if not isinstance(c, dict):
                continue
            pfx = c.get("sku_prefixes") if isinstance(c.get("sku_prefixes"), list) else []
            all_prefixes.extend([str(x) for x in pfx if str(x)])

        buffer_due = start_time + timedelta(hours=int(max_hours) + 100)
        injected: list[dict[str, Any]] = []
        for sku_raw, qty_raw in extra_production.items():
            sku = str(sku_raw or "").strip().upper()
            try:
                qty = int(qty_raw)
            except Exception:
                qty = 0
            if not sku or qty <= 0:
                continue
            if all_prefixes and not _sku_matches_prefixes(sku, all_prefixes):
                continue
            injected.append(
                {
                    "c_orderline_id": next_id,
                    "poreference": BUFFER_POREFERENCE,
                    "sku": sku,
                    "quantity": qty,
                    "duedate": buffer_due.isoformat(),
                    "name": f"BUFFER-{sku}",
                    "remark": "额外生产半成品库存",
                    "priority": -1,
                }
            )
            next_id -= 1

        if injected:
            raw_orders = list(raw_orders) + injected

    line_schedules: dict[str, dict[str, Any]] = {}
    for line_id, line_cfg in sorted(lines.items(), key=lambda kv: kv[0]):
        prefixes = list((line_cfg or {}).get("sku_prefixes") or [])
        if not prefixes:
            continue
        fm = str((line_cfg or {}).get("forming_machine") or "").strip().upper()

        # Optional real-time context for forming machines (best-effort):
        # - if machine is in setup, delay forming by N hours
        # - if machine is currently producing a SKU, treat it as the current SKU to reduce immediate setup
        init_sku: str | None = None
        init_setup_h: int | None = None
        if fm and isinstance(forming_states_by_machine, dict):
            raw_state = str(forming_states_by_machine.get(fm) or "").strip()
            if raw_state:
                s = raw_state.strip()
                if s.lower() == "setup":
                    if isinstance(setup_remaining_by_machine, dict) and fm in setup_remaining_by_machine:
                        try:
                            init_setup_h = max(0, int(setup_remaining_by_machine[fm]))
                        except Exception:
                            init_setup_h = None
                elif s.lower() == "idle":
                    pass
                else:
                    # Accept: producing:SKU, SKU, producing
                    if s.lower().startswith("producing:"):
                        init_sku = s.split(":", 1)[1].strip().upper() or None
                    elif s.upper().startswith("S"):
                        init_sku = s.strip().upper()

        line_schedules[line_id] = generate_line_schedule(
            line_id=line_id,
            line_cfg=line_cfg,
            raw_orders=raw_orders,
            raw_inventory=raw_inventory,
            start_time=start_time,
            max_hours=int(max_hours),
            apply_downtime=bool(apply_downtime),
            initial_forming_sku=init_sku,
            initial_setup_remaining_h=init_setup_h,
        )

    if not line_schedules:
        raise RuntimeError("No line schedules generated (no matching orders?)")

    combined = merge_schedules(start_time=start_time, line_schedules=line_schedules)
    # Expose overrides in meta for debugging / UI (best-effort; keep it small).
    try:
        meta = combined.get("meta") if isinstance(combined.get("meta"), dict) else {}
        meta["overrides"] = {
            "containers": sorted(list((overrides.get("containers") or {}).keys()))[:50],
            "orders": sorted(list((overrides.get("orders") or {}).keys()))[:50],
        }
        combined["meta"] = meta
    except Exception:
        pass
    return line_schedules, combined


def write_schedule_artifacts(
    *,
    schedule: dict[str, Any],
    schedule_path: Path,
    gantt_path: Path,
    px_per_day: int = 120,
) -> None:
    atomic_write_json(schedule_path, schedule)
    gantt_path.write_text(render_gantt_html(schedule, px_per_day=int(px_per_day)), encoding="utf-8")
