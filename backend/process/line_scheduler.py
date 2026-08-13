#!/usr/bin/env python3
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Reuse due parsing + downtime calendar semantics from the current implementation.
# This module may be imported either from repo root (namespace package) or executed
# as a script from within `process/`, so keep imports flexible.
try:  # pragma: no cover
    from process.generate_schedule import (  # type: ignore
        _due_deadline as due_deadline,
        _is_machine_down,
        _load_production_calendar,
        _parse_due_datetime,
    )
except ModuleNotFoundError:  # pragma: no cover
    from generate_schedule import (  # type: ignore
        _due_deadline as due_deadline,
        _is_machine_down,
        _load_production_calendar,
        _parse_due_datetime,
    )


@dataclass(frozen=True)
class Order:
    c_orderline_id: int
    poreference: str
    sku: str
    quantity: int
    due: datetime
    deadline: datetime
    name: str | None
    remark: str | None
    priority: int = 0
    labeling_rate_per_h: int = 1

    @property
    def proc_hours(self) -> int:
        return int(math.ceil(self.quantity / self.labeling_rate_per_h))


@dataclass
class LabelMachineState:
    machine: str
    order: Order | None = None
    remaining_qty: int = 0
    order_start_h: int | None = None

    def is_idle(self) -> bool:
        return self.order is None


@dataclass(frozen=True)
class FormingHour:
    mode: str  # "forming" | "setup" | "idle"
    sku: str | None = None
    setup_type: str | None = None
    from_sku: str | None = None
    to_sku: str | None = None


def _ceil_hour(dt: datetime) -> datetime:
    if dt.minute > 0 or dt.second > 0 or dt.microsecond > 0:
        return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dt.replace(minute=0, second=0, microsecond=0)


def _sku_matches_prefixes(sku: str, prefixes: list[str]) -> bool:
    s = str(sku or "").strip().upper()
    for p in prefixes:
        if s.startswith(str(p).strip().upper()):
            return True
    return False


def _mold_group(sku: str, groups: dict[str, str] | None) -> str | None:
    if not groups:
        return None
    s = str(sku or "").strip().upper()
    for prefix, g in groups.items():
        if s.startswith(str(prefix).strip().upper()):
            return str(g)
    return None


def _setup_hours(from_sku: str, to_sku: str, setup_rules: dict[str, Any]) -> tuple[int, str]:
    """Return (hours, setup_type) for switching from->to."""
    if from_sku == to_sku:
        return 0, ""
    color_h = int(setup_rules.get("color_change_h") or 0)
    mold_h = setup_rules.get("mold_change_h")
    groups = setup_rules.get("mold_change_prefix_groups")
    if isinstance(mold_h, int) and mold_h > 0 and isinstance(groups, dict):
        g1 = _mold_group(from_sku, groups)
        g2 = _mold_group(to_sku, groups)
        if g1 and g2 and g1 != g2:
            return int(mold_h), "mold_change"
    return color_h, "color_change"


def _forming_sequence_candidates(
    *,
    skus: list[str],
    orders: list[Order],
    demand_by_sku: dict[str, int],
    setup_rules: dict[str, Any],
) -> list[list[str]]:
    """Generate a small set of candidate forming campaign orders."""
    skus = [s for s in skus if demand_by_sku.get(s, 0) > 0]
    if not skus:
        return [[]]

    earliest_deadline: dict[str, datetime] = {}
    max_priority: dict[str, int] = {}
    for o in orders:
        d = earliest_deadline.get(o.sku)
        earliest_deadline[o.sku] = o.deadline if d is None else min(d, o.deadline)
        max_priority[o.sku] = max(int(max_priority.get(o.sku, 0)), int(o.priority or 0))

    # Priority impacts forming campaign order too; otherwise "加急" only affects labeling dispatch
    # and large-qty SKUs can still be formed too late.
    seq_by_due = sorted(skus, key=lambda s: (-int(max_priority.get(s, 0)), earliest_deadline.get(s) or datetime.max, s))
    seq_by_demand = sorted(
        skus,
        key=lambda s: (
            -int(max_priority.get(s, 0)),
            -int(demand_by_sku.get(s, 0)),
            earliest_deadline.get(s) or datetime.max,
            s,
        ),
    )

    # Family-grouped sequences (mainly for L1 to avoid extra mold changes).
    groups = setup_rules.get("mold_change_prefix_groups")
    seq_family_a: list[str] = []
    seq_family_b: list[str] = []
    if isinstance(groups, dict) and groups:
        # Split by mold group name (only supports 2 groups for now).
        group_names: list[str] = []
        sku_group: dict[str, str | None] = {}
        for s in skus:
            g = _mold_group(s, groups)
            sku_group[s] = g
            if g and g not in group_names:
                group_names.append(g)
        if len(group_names) >= 2:
            g0, g1 = group_names[0], group_names[1]
            seq_family_a = sorted(
                [s for s in skus if sku_group.get(s) == g0],
                key=lambda s: (-int(max_priority.get(s, 0)), earliest_deadline.get(s) or datetime.max, s),
            )
            seq_family_b = sorted(
                [s for s in skus if sku_group.get(s) == g1],
                key=lambda s: (-int(max_priority.get(s, 0)), earliest_deadline.get(s) or datetime.max, s),
            )

    out: list[list[str]] = []
    for cand in (seq_by_due, seq_by_demand, skus):
        if cand and cand not in out:
            out.append(list(cand))

    if seq_family_a and seq_family_b:
        for cand in (seq_family_a + seq_family_b, seq_family_b + seq_family_a):
            if cand and cand not in out:
                out.append(cand)

    return out[:8]


def _build_forming_plan(
    *,
    sequence: list[str],
    required_by_sku: dict[str, int],
    forming_rate_per_h: int,
    setup_rules: dict[str, Any],
    max_hours: int,
    initial_sku: str | None = None,
) -> list[FormingHour]:
    plan: list[FormingHour] = []
    current: str | None = str(initial_sku).strip().upper() if initial_sku else None

    for sku in sequence:
        sku = str(sku).strip().upper()
        need = int(required_by_sku.get(sku, 0))
        if need <= 0:
            continue
        if current is None:
            current = sku
        if current != sku:
            setup_h, setup_type = _setup_hours(current, sku, setup_rules)
            for _ in range(setup_h):
                plan.append(FormingHour(mode="setup", setup_type=setup_type, from_sku=current, to_sku=sku))
            current = sku

        hours_needed = int(math.ceil(need / forming_rate_per_h))
        for _ in range(hours_needed):
            plan.append(FormingHour(mode="forming", sku=sku))

    # Pad to max_hours with idle.
    if len(plan) < max_hours:
        plan.extend([FormingHour(mode="idle")] * (max_hours - len(plan)))
    else:
        plan = plan[:max_hours]
    return plan


def _simulate_can_start_order(
    *,
    now_h: int,
    start_order: Order,
    start_machine: str,
    label_states: list[LabelMachineState],
    inventories: dict[str, int],
    forming_remaining: dict[str, int],
    forming_plan: list[FormingHour],
    forming_plan_idx: int,
    forming_rate_per_h: int,
    labeling_rate_per_h: int,
    line_skus: list[str],
) -> bool:
    sim_inventories = dict(inventories)
    sim_forming_remaining = dict(forming_remaining)

    # Copy label states; keep in-progress orders and start the candidate on start_machine.
    sim_label: list[LabelMachineState] = []
    for st in label_states:
        sim_label.append(
            LabelMachineState(
                machine=st.machine,
                order=st.order,
                remaining_qty=st.remaining_qty,
                order_start_h=st.order_start_h,
            )
        )
    for st in sim_label:
        if st.machine == start_machine:
            if not st.is_idle():
                return False
            st.order = start_order
            st.remaining_qty = start_order.quantity
            st.order_start_h = now_h
            break

    horizon_h = start_order.proc_hours
    for step in range(horizon_h):
        plan_h = int(forming_plan_idx) + step
        if plan_h >= len(forming_plan):
            forming_hour = FormingHour(mode="idle")
        else:
            forming_hour = forming_plan[plan_h]

        produced: dict[str, int] = {sku: 0 for sku in line_skus}
        if forming_hour.mode == "forming" and forming_hour.sku in sim_forming_remaining:
            sku = str(forming_hour.sku)
            if sim_forming_remaining.get(sku, 0) > 0:
                qty = int(forming_rate_per_h)
                sim_forming_remaining[sku] = max(0, sim_forming_remaining[sku] - qty)
                produced[sku] = qty

        consumed: dict[str, int] = {sku: 0 for sku in line_skus}
        for st in sim_label:
            if st.order is None:
                continue
            sku = st.order.sku
            qty = min(int(labeling_rate_per_h), st.remaining_qty)
            st.remaining_qty -= qty
            consumed[sku] += qty
            if st.remaining_qty == 0:
                st.order = None
                st.order_start_h = None

        for sku in line_skus:
            sim_inventories[sku] = sim_inventories.get(sku, 0) + produced[sku] - consumed[sku]
            if sim_inventories[sku] < 0:
                return False
    return True


def _compress_forming_hours(
    *,
    forming_hours: list[dict[str, Any]],
    start_time: datetime,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cur_start_h = 0

    def _key(h: dict[str, Any]) -> tuple[Any, ...]:
        mode = h.get("mode")
        if mode == "forming":
            return ("forming", h.get("sku"))
        if mode == "setup":
            return ("setup", h.get("setup_type"), h.get("from_sku"), h.get("to_sku"))
        return ("idle", h.get("setup_type"))

    for idx, h in enumerate(forming_hours):
        if cur is None:
            cur = dict(h)
            cur["_key"] = _key(h)
            cur["_qty_sum"] = int(h.get("qty") or 0)
            cur_start_h = idx
            continue

        if _key(h) != cur["_key"]:
            start_dt = start_time + timedelta(hours=cur_start_h)
            end_dt = start_time + timedelta(hours=idx)
            out: dict[str, Any] = {
                "type": cur.get("mode"),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration_h": idx - cur_start_h,
            }
            if cur["_key"][0] == "forming":
                out.update({"sku": cur.get("sku"), "quantity": int(cur.get("_qty_sum") or 0)})
            elif cur["_key"][0] == "setup":
                out.update(
                    {
                        "setup_type": cur.get("setup_type"),
                        "from_sku": cur.get("from_sku"),
                        "to_sku": cur.get("to_sku"),
                    }
                )
            elif cur["_key"][0] == "idle" and cur.get("setup_type"):
                out.update({"setup_type": cur.get("setup_type")})
            tasks.append(out)

            cur = dict(h)
            cur["_key"] = _key(h)
            cur["_qty_sum"] = int(h.get("qty") or 0)
            cur_start_h = idx
        else:
            cur["_qty_sum"] = int(cur.get("_qty_sum") or 0) + int(h.get("qty") or 0)

    if cur is not None:
        start_dt = start_time + timedelta(hours=cur_start_h)
        end_dt = start_time + timedelta(hours=len(forming_hours))
        out = {
            "type": cur.get("mode"),
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "duration_h": len(forming_hours) - cur_start_h,
        }
        if cur["_key"][0] == "forming":
            out.update({"sku": cur.get("sku"), "quantity": int(cur.get("_qty_sum") or 0)})
        elif cur["_key"][0] == "setup":
            out.update(
                {
                    "setup_type": cur.get("setup_type"),
                    "from_sku": cur.get("from_sku"),
                    "to_sku": cur.get("to_sku"),
                }
            )
        elif cur["_key"][0] == "idle" and cur.get("setup_type"):
            out.update({"setup_type": cur.get("setup_type")})
        tasks.append(out)
    return tasks


def generate_line_schedule(
    *,
    line_id: str,
    line_cfg: dict[str, Any],
    raw_orders: list[dict[str, Any]],
    raw_inventory: list[dict[str, Any]],
    start_time: datetime,
    max_hours: int = 8000,
    apply_downtime: bool = True,
    initial_forming_sku: str | None = None,
    initial_setup_remaining_h: int | None = None,
) -> dict[str, Any]:
    """Generate a schedule for a single independent line.

    This is a pragmatic heuristic, designed to:
    - satisfy inventory safety via a forward simulation feasibility check
    - minimize tardiness by earliest-deadline-first dispatch on labeling machines
    - respect forming setup times via a prebuilt forming campaign plan
    """
    forming_machine = str(line_cfg["forming_machine"])
    labeling_machines = tuple(str(x) for x in line_cfg["labeling_machines"])
    forming_rate = int(line_cfg["forming_rate_per_h"])
    labeling_rate = int(line_cfg["labeling_rate_per_h"])
    sku_prefixes = list(line_cfg["sku_prefixes"])
    setup_rules = dict(line_cfg.get("setup_rules") or {})

    # Filter orders + inventory by SKU prefixes.
    matched_orders = [o for o in raw_orders if _sku_matches_prefixes(str(o.get("sku") or ""), sku_prefixes)]
    matched_inventory = [r for r in raw_inventory if _sku_matches_prefixes(str(r.get("materialcode") or ""), sku_prefixes)]

    line_skus = sorted({str(o.get("sku") or "").strip() for o in matched_orders if str(o.get("sku") or "").strip()})
    if not line_skus:
        return {
            "meta": {"line": line_id, "start_time": start_time.isoformat(), "time_step_h": 1, "horizon_h": 0},
            "kpi": {"orders_total": 0, "containers_total": 0, "setup_count": 0},
            "machines": {forming_machine: [], labeling_machines[0]: [], labeling_machines[1]: []},
            "orders": [],
            "containers": [],
            "inventory": {"start_time": start_time.isoformat(), "time_step_h": 1, "skus": {}},
            "validation": {"inventory_nonnegative": True, "inventory_min": {}},
        }

    # Build Orders
    orders: list[Order] = []
    for o in matched_orders:
        sku = str(o.get("sku") or "").strip()
        if not sku:
            continue
        due_raw = str(o.get("due_override") or o.get("duedate") or "")
        due = _parse_due_datetime(due_raw)
        deadline_override = str(o.get("deadline_override") or "").strip()
        if deadline_override:
            # Use override directly (ceil-hour) to avoid forcing users to reason about our rounding.
            try:
                deadline = _ceil_hour(_parse_due_datetime(deadline_override))
            except Exception:
                deadline = due_deadline(due)
        else:
            deadline = due_deadline(due)
        orders.append(
            Order(
                c_orderline_id=int(o["c_orderline_id"]),
                poreference=str(o.get("poreference") or ""),
                sku=sku,
                quantity=int(o.get("quantity") or 0),
                due=due,
                deadline=deadline,
                name=o.get("name"),
                remark=o.get("remark"),
                priority=int(o.get("priority") or 0),
                labeling_rate_per_h=labeling_rate,
            )
        )
    if not orders:
        raise RuntimeError(f"{line_id}: no orders found after filtering")

    # Inventory
    inventories: dict[str, int] = {sku: 0 for sku in line_skus}
    for r in matched_inventory:
        code = str(r.get("materialcode") or "").strip()
        if code in inventories:
            inventories[code] = int(r.get("quantity") or 0)
    inventory_series: dict[str, list[int]] = {sku: [inventories[sku]] for sku in line_skus}

    # Demand + required forming
    demand_by_sku: dict[str, int] = {sku: 0 for sku in line_skus}
    for o in orders:
        demand_by_sku[o.sku] += int(o.quantity)
    required_by_sku: dict[str, int] = {sku: max(0, demand_by_sku[sku] - inventories.get(sku, 0)) for sku in line_skus}
    forming_remaining: dict[str, int] = dict(required_by_sku)

    # Candidates: small set of forming campaign sequences.
    candidates = _forming_sequence_candidates(
        skus=line_skus,
        orders=orders,
        demand_by_sku=demand_by_sku,
        setup_rules=setup_rules,
    )

    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float, float, int] | None = None

    # Only load downtime calendar when it is actually used. This keeps pure
    # scheduling tests (apply_downtime=False) independent from DB configuration.
    if apply_downtime:
        calendar = _load_production_calendar()
    else:
        calendar = {"holidays": [], "maintenance": []}

    for sequence in candidates:
        forming_plan = _build_forming_plan(
            sequence=sequence,
            required_by_sku=required_by_sku,
            forming_rate_per_h=forming_rate,
            setup_rules=setup_rules,
            max_hours=max_hours,
            initial_sku=initial_forming_sku,
        )

        # Initialize simulation state per candidate
        inv = dict(inventories)
        inv_series = {sku: [inv[sku]] for sku in line_skus}
        rem = dict(forming_remaining)

        label_states = [LabelMachineState(machine=m) for m in labeling_machines]
        label_tasks: dict[str, list[dict[str, Any]]] = {m: [] for m in labeling_machines}
        forming_hours_out: list[dict[str, Any]] = []
        forming_plan_idx = 0  # advances only when forming machine is working (not in downtime)
        initial_setup_left = int(initial_setup_remaining_h or 0)
        initial_setup_left = max(0, initial_setup_left)

        # Orders dispatch state
        orders_left = list(orders)
        orders_left.sort(key=lambda o: (-o.priority, o.deadline, o.c_orderline_id))
        order_schedule: dict[int, dict[str, Any]] = {}

        t_h = 0
        while t_h < max_hours:
            if not orders_left and all(st.is_idle() for st in label_states):
                break

            current_time = start_time + timedelta(hours=t_h)

            forming_down, forming_down_reason = _is_machine_down(forming_machine, current_time, calendar)
            if forming_down:
                # Whole line down if forming is down (holidays are encoded this way).
                forming_hours_out.append({"mode": "idle", "setup_type": forming_down_reason, "qty": 0})
                down_start = current_time
                down_end = current_time + timedelta(hours=1)
                for m in labeling_machines:
                    label_tasks[m].append(
                        {
                            "type": "idle",
                            "setup_type": forming_down_reason,
                            "start": down_start.isoformat(),
                            "end": down_end.isoformat(),
                            "duration_h": 1,
                        }
                    )
                for sku in line_skus:
                    inv_series[sku].append(inv[sku])
                t_h += 1
                continue

            plan_advance = True
            if initial_setup_left > 0:
                # Initial in-progress setup: do not advance the forming campaign plan yet.
                forming_hour_base = FormingHour(mode="setup", setup_type="setup_in_progress")
                plan_advance = False
                initial_setup_left -= 1
            else:
                forming_hour_base = (
                    forming_plan[forming_plan_idx] if forming_plan_idx < len(forming_plan) else FormingHour(mode="idle")
                )

            # Dispatch labeling orders for idle machines.
            for st in label_states:
                label_down, label_down_reason = _is_machine_down(st.machine, current_time, calendar)
                if label_down:
                    # Record downtime as idle blocks for the gantt.
                    label_tasks[st.machine].append(
                        {
                            "type": "idle",
                            "setup_type": label_down_reason,
                            "start": current_time.isoformat(),
                            "end": (current_time + timedelta(hours=1)).isoformat(),
                            "duration_h": 1,
                        }
                    )
                    continue

                if not st.is_idle():
                    continue

                chosen: Order | None = None
                for o in orders_left:
                    if not _simulate_can_start_order(
                        now_h=t_h,
                        start_order=o,
                        start_machine=st.machine,
                        label_states=label_states,
                        inventories=inv,
                        forming_remaining=rem,
                        forming_plan=forming_plan,
                        forming_plan_idx=forming_plan_idx,
                        forming_rate_per_h=forming_rate,
                        labeling_rate_per_h=labeling_rate,
                        line_skus=line_skus,
                    ):
                        continue
                    chosen = o
                    break
                if chosen is None:
                    continue

                st.order = chosen
                st.remaining_qty = chosen.quantity
                st.order_start_h = t_h
                orders_left.remove(chosen)
                order_schedule[chosen.c_orderline_id] = {
                    "machine": st.machine,
                    "sku": chosen.sku,
                    "quantity": chosen.quantity,
                    "start_h": t_h,
                    "due": chosen.due.isoformat(),
                    "deadline": chosen.deadline.isoformat(),
                    "line": line_id,
                }

            # Execute one hour: forming + labeling consumption.
            forming_qty_by_sku: dict[str, int] = {sku: 0 for sku in line_skus}
            forming_hour = forming_hour_base
            if forming_hour.mode == "forming" and forming_hour.sku in rem:
                sku = str(forming_hour.sku)
                if rem.get(sku, 0) > 0:
                    qty = forming_rate
                    rem[sku] = max(0, rem[sku] - qty)
                    forming_qty_by_sku[sku] = qty
                    forming_hours_out.append({"mode": "forming", "sku": sku, "qty": qty})
                else:
                    forming_hours_out.append({"mode": "idle", "qty": 0})
            elif forming_hour.mode == "setup":
                forming_hours_out.append(
                    {
                        "mode": "setup",
                        "setup_type": forming_hour.setup_type,
                        "from_sku": forming_hour.from_sku,
                        "to_sku": forming_hour.to_sku,
                        "qty": 0,
                    }
                )
            else:
                forming_hours_out.append({"mode": "idle", "qty": 0})

            consumed_qty_by_sku: dict[str, int] = {sku: 0 for sku in line_skus}
            for st in label_states:
                if st.order is None:
                    continue
                label_down, _ = _is_machine_down(st.machine, current_time, calendar)
                if label_down:
                    continue
                sku = st.order.sku
                qty = min(labeling_rate, st.remaining_qty)
                st.remaining_qty -= qty
                consumed_qty_by_sku[sku] += qty
                if st.remaining_qty == 0:
                    start_dt = start_time + timedelta(hours=int(st.order_start_h or 0))
                    end_dt = start_time + timedelta(hours=t_h + 1)
                    label_tasks[st.machine].append(
                        {
                            "type": "label",
                            "order_id": st.order.c_orderline_id,
                            "sku": st.order.sku,
                            "quantity": st.order.quantity,
                            "start": start_dt.isoformat(),
                            "end": end_dt.isoformat(),
                            "duration_h": int(t_h + 1 - int(st.order_start_h or 0)),
                        }
                    )
                    s = order_schedule.get(st.order.c_orderline_id) or {}
                    s.update(
                        {
                            "start": start_dt.isoformat(),
                            "end": end_dt.isoformat(),
                            "end_h": t_h + 1,
                            "duration_h": int(t_h + 1 - int(st.order_start_h or 0)),
                        }
                    )
                    order_schedule[st.order.c_orderline_id] = s
                    st.order = None
                    st.order_start_h = None

            for sku in line_skus:
                inv[sku] = inv.get(sku, 0) + forming_qty_by_sku[sku] - consumed_qty_by_sku[sku]
                inv_series[sku].append(inv[sku])

            if plan_advance:
                forming_plan_idx += 1
            t_h += 1

        # Candidate must schedule all orders.
        if len(order_schedule) != len(orders):
            continue

        makespan_h = 0
        if order_schedule:
            makespan_h = max(int(v.get("end_h") or 0) for v in order_schedule.values())

        # Build order rows + KPI.
        order_rows: list[dict[str, Any]] = []
        on_time = 0
        tardiness_h_sum = 0.0
        expired_before_start_count = 0
        for o in orders:
            s = order_schedule[o.c_orderline_id]
            end_dt = datetime.fromisoformat(str(s["end"]))
            lateness_h = max(0.0, (end_dt - o.deadline).total_seconds() / 3600.0)
            expired_before_start = o.deadline <= start_time
            is_on_time = (not expired_before_start) and lateness_h <= 1e-9
            on_time += int(is_on_time)
            expired_before_start_count += int(expired_before_start)
            tardiness_h_sum += lateness_h
            order_rows.append(
                {
                    "c_orderline_id": o.c_orderline_id,
                    "poreference": o.poreference,
                    "name": o.name,
                    "remark": o.remark,
                    "sku": o.sku,
                    "quantity": o.quantity,
                    "due": o.due.isoformat(),
                    "deadline": o.deadline.isoformat(),
                    "machine": s["machine"],
                    "start": s["start"],
                    "end": s["end"],
                    "on_time": is_on_time,
                    "expired_before_start": expired_before_start,
                    "lateness_h": lateness_h,
                    "line": line_id,
                }
            )

        # Container KPI (within this line only; cross-line containers are recomputed in the merged schedule).
        container_groups: dict[str, list[Order]] = {}
        for o in orders:
            cid = str(o.poreference or "").strip() or f"__NO_POREFERENCE__{o.c_orderline_id}"
            container_groups.setdefault(cid, []).append(o)

        containers_on_time = 0
        containers_tardiness_h_sum = 0.0
        containers_expired_before_start_count = 0
        container_rows: list[dict[str, Any]] = []
        for cid, group in sorted(container_groups.items(), key=lambda kv: kv[0]):
            due = min(o.due for o in group)
            deadline = min(o.deadline for o in group)
            qty_sum = sum(int(o.quantity) for o in group)
            order_ids = sorted(int(o.c_orderline_id) for o in group)
            starts = [datetime.fromisoformat(order_schedule[o.c_orderline_id]["start"]) for o in group]
            ends = [datetime.fromisoformat(order_schedule[o.c_orderline_id]["end"]) for o in group]
            start_dt = min(starts) if starts else start_time
            end_dt = max(ends) if ends else start_time
            lateness_h = max(0.0, (end_dt - deadline).total_seconds() / 3600.0)
            expired_before_start = deadline <= start_time
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
                    "due": due.isoformat(),
                    "deadline": deadline.isoformat(),
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "on_time": is_on_time,
                    "expired_before_start": expired_before_start,
                    "lateness_h": lateness_h,
                }
            )

        forming_tasks = _compress_forming_hours(forming_hours=forming_hours_out[:makespan_h], start_time=start_time)
        setup_count = sum(1 for t in forming_tasks if t.get("type") == "setup")
        inv_mins = {sku: min(series) for sku, series in inv_series.items()}

        schedule = {
            "meta": {
                "line": line_id,
                "start_time": start_time.isoformat(),
                "time_step_h": 1,
                "forming_machine": forming_machine,
                "labeling_machines": list(labeling_machines),
                "rates_per_h": {"forming": forming_rate, "labeling_each": labeling_rate},
                "horizon_h": makespan_h,
                "forming_sequence": sequence,
                "assumptions": {
                    "continuous_24x7": True,
                    "due_deadline_rule": "finish_before_due_time_ceil_hour",
                    "label_order_no_split": True,
                    "delivery_unit": "container(poreference)",
                    "container_completion_rule": "max_label_end",
                    "container_deadline_agg": "min",
                    "label_changeover_h": 0,
                },
            },
            "kpi": {
                "orders_total": len(orders),
                "orders_on_time": on_time,
                "orders_expired_before_start": expired_before_start_count,
                "on_time_rate": (on_time / len(orders)) if orders else 1.0,
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
            "validation": {
                "inventory_min": inv_mins,
                "inventory_nonnegative": all(v >= 0 for v in inv_mins.values()),
            },
            "machines": {
                forming_machine: forming_tasks,
                labeling_machines[0]: label_tasks[labeling_machines[0]],
                labeling_machines[1]: label_tasks[labeling_machines[1]],
            },
            "orders": order_rows,
            "containers": container_rows,
            "inventory": {"start_time": start_time.isoformat(), "time_step_h": 1, "skus": inv_series},
        }

        kpi = schedule.get("kpi") or {}
        key = (
            float(kpi.get("total_container_tardiness_h") or kpi.get("total_tardiness_h") or 0.0),
            -float(kpi.get("containers_on_time_rate") or kpi.get("on_time_rate") or 0.0),
            float(kpi.get("total_tardiness_h") or 0.0),
            -float(kpi.get("on_time_rate") or 0.0),
            int(schedule.get("meta", {}).get("horizon_h") or 0),
        )
        if best_key is None or key < best_key:
            best_key = key
            best = schedule

    if best is None:
        raise RuntimeError(f"{line_id}: failed to produce a feasible schedule from candidate sequences")
    return best
