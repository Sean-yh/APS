#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal


LINE = "L2"
FORMING_MACHINE = "ROTARY-2"
LABELING_MACHINES = ("LABEL-3", "LABEL-5")

FORMING_RATE_PER_H = 5000
LABELING_RATE_PER_H = 2400
COLOR_CHANGE_H = 12

L2_SKUS = ("S12G9C", "S12G9V", "S12G9W")
DEFAULT_START_TIME = "2026-01-19 00:00"


@dataclass
class FrozenSlot:
    """冻结订单槽位信息"""
    order_id: int
    machine: str
    start_h: int
    end_h: int
    sku: str
    quantity: int
    due: datetime
    deadline: datetime


@dataclass
class FrozenRotarySlot:
    """冻结的 ROTARY 任务"""
    mode: str  # "forming" | "setup" | "idle"
    sku: str | None  # 生产的 SKU（forming 时）
    start_h: int
    end_h: int
    quantity: int  # 产出数量


def _load_production_calendar() -> dict[str, Any]:
    """加载生产日历配置（假期、维护等停机计划）。

    DB-only: the calendar is stored in the `documents` table under key
    `production_calendar`. Local JSON files are not used anymore.
    """
    from ai.calendar_store import load_calendar

    cal = load_calendar()
    if not isinstance(cal, dict):
        return {"holidays": [], "maintenance": []}
    if not isinstance(cal.get("holidays"), list):
        cal["holidays"] = []
    if not isinstance(cal.get("maintenance"), list):
        cal["maintenance"] = []
    return cal


def _is_machine_down(
    machine_id: str, current_time: datetime, calendar: dict[str, Any]
) -> tuple[bool, str | None]:
    """
    检查指定机器在指定时间是否停机。

    Args:
        machine_id: 机器ID（如 "ROTARY-2", "LABEL-3"）
        current_time: 要检查的时间点
        calendar: 生产日历配置

    Returns:
        (是否停机, 停机原因)
        停机原因格式: "holiday:春节" 或 "maintenance:年度保养"
    """
    # 检查假期（全厂停机）
    for holiday in calendar.get("holidays", []):
        start = datetime.fromisoformat(holiday["start"])
        # end 日期是包含的，所以加 1 天
        end = datetime.fromisoformat(holiday["end"]) + timedelta(days=1)
        if start <= current_time < end:
            return True, f"holiday:{holiday['name']}"

    # 检查维护（可能针对特定机器）
    for maint in calendar.get("maintenance", []):
        maint_machine = maint.get("machine_id")
        # 如果指定了 machine_id，则只影响该机器；否则影响所有机器
        if maint_machine and maint_machine != machine_id:
            continue
        start = datetime.fromisoformat(maint["start"])
        end = datetime.fromisoformat(maint["end"])
        if start <= current_time < end:
            return True, f"maintenance:{maint['reason']}"

    return False, None


def _load_api_list(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and "data" in doc:
        data = doc["data"]
        if not isinstance(data, list):
            raise TypeError(f"{path}: expected dict['data'] to be list, got {type(data)}")
        return data
    if isinstance(doc, list):
        return doc
    raise TypeError(f"{path}: expected list or dict with 'data', got {type(doc)}")


def _parse_due_datetime(s: str) -> datetime:
    s = str(s).strip().replace("：", ":")
    if not s:
        raise ValueError("Missing duedate")

    # Common legacy format (day-first)
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass

    # Date-only: interpret as "due by 24:00 of that day" => next day 00:00
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", s):
        d = datetime.strptime(s, "%d/%m/%Y")
        return datetime(d.year, d.month, d.day) + timedelta(days=1)
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        d = datetime.strptime(s, "%Y-%m-%d")
        return datetime(d.year, d.month, d.day) + timedelta(days=1)

    # Common ISO-ish formats
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass

    # ISO 8601 with optional timezone (drop tzinfo for internal consistency)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except Exception:
        pass

    raise ValueError(
        f"Unsupported duedate format: {s!r}. Expected one of: "
        "DD/MM/YYYY HH:MM, YYYY-MM-DD HH:MM, ISO 8601, or date-only (DD/MM/YYYY or YYYY-MM-DD)."
    )


def _due_deadline(due: datetime) -> datetime:
    # deadline = due time 向上取整到小时
    if due.minute > 0 or due.second > 0 or due.microsecond > 0:
        return due.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return due.replace(minute=0, second=0, microsecond=0)


def _parse_start_datetime(s: str) -> datetime:
    normalized = str(s).strip().replace("：", ":")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported --start datetime format: {s!r}")


def _extract_start_from_orders(orders_path: Path) -> datetime | None:
    """从订单文件的 timestamp 字段提取开始时间，向上取整到小时"""
    try:
        doc = json.loads(orders_path.read_text(encoding="utf-8"))
        ts_str = doc.get("timestamp")
        if not ts_str:
            return None
        # 解析 ISO 8601 格式，处理 Z 后缀
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # If ERP returns timezone-aware timestamps, convert to local time so the schedule
        # aligns with the browser time zone in local dev.
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        else:
            ts = ts.replace(tzinfo=None)
        # 向上取整到小时
        if ts.minute > 0 or ts.second > 0 or ts.microsecond > 0:
            ts = ts.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return ts
    except Exception:
        return None


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
    priority: int = 0  # 0=普通, 1=优先锁定

    @property
    def proc_hours(self) -> int:
        return int(math.ceil(self.quantity / LABELING_RATE_PER_H))


@dataclass
class LabelMachineState:
    machine: str
    order: Order | None = None
    remaining_qty: int = 0
    order_start_h: int | None = None

    def is_idle(self) -> bool:
        return self.order is None


FormingMode = Literal[
    "forming",
    "setup",
    "idle",
]


@dataclass(frozen=True)
class FormingHour:
    mode: FormingMode
    sku: str | None = None
    qty: int = 0
    setup_type: str | None = None
    from_sku: str | None = None
    to_sku: str | None = None


def _forming_plan_mode(t_h: int, chain_start_h: int, w_prod_h: int, v_prod_h: int) -> FormingHour:
    if t_h < chain_start_h:
        return FormingHour(mode="forming", sku="S12G9C")

    t = t_h - chain_start_h
    if 0 <= t < COLOR_CHANGE_H:
        return FormingHour(
            mode="setup",
            setup_type="color_change",
            from_sku="S12G9C",
            to_sku="S12G9W",
        )
    if COLOR_CHANGE_H <= t < COLOR_CHANGE_H + w_prod_h:
        return FormingHour(mode="forming", sku="S12G9W")

    t -= COLOR_CHANGE_H + w_prod_h
    if 0 <= t < COLOR_CHANGE_H:
        return FormingHour(
            mode="setup",
            setup_type="color_change",
            from_sku="S12G9W",
            to_sku="S12G9V",
        )
    if COLOR_CHANGE_H <= t < COLOR_CHANGE_H + v_prod_h:
        return FormingHour(mode="forming", sku="S12G9V")

    t -= COLOR_CHANGE_H + v_prod_h
    if 0 <= t < COLOR_CHANGE_H:
        return FormingHour(
            mode="setup",
            setup_type="color_change",
            from_sku="S12G9V",
            to_sku="S12G9C",
        )
    return FormingHour(mode="forming", sku="S12G9C")


def _allowed_label_skus(forming_hour: FormingHour) -> tuple[str, ...]:
    # While ROTARY-2 is not producing C (setup/W/V), prefer consuming W/V to avoid starving C.
    if forming_hour.mode == "forming" and forming_hour.sku == "S12G9C":
        return L2_SKUS
    return ("S12G9W", "S12G9V")


def _simulate_can_start_order(
    *,
    now_h: int,
    start_order: Order,
    start_machine: str,
    label_states: list[LabelMachineState],
    inventories: dict[str, int],
    forming_remaining: dict[str, int],
    chain_start_h: int,
    w_prod_h: int,
    v_prod_h: int,
) -> bool:
    sim_inventories = dict(inventories)
    sim_forming_remaining = dict(forming_remaining)

    # Copy label states; only in-progress orders stay, plus the hypothetical started one.
    sim_label = []
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
        t_h = now_h + step

        forming_hour = _forming_plan_mode(t_h, chain_start_h, w_prod_h, v_prod_h)
        produced: dict[str, int] = {sku: 0 for sku in L2_SKUS}
        if forming_hour.mode == "forming" and forming_hour.sku in sim_forming_remaining:
            sku = forming_hour.sku
            if sim_forming_remaining[sku] > 0:
                qty = FORMING_RATE_PER_H
                sim_forming_remaining[sku] = max(0, sim_forming_remaining[sku] - qty)
                produced[sku] = qty

        consumed: dict[str, int] = {sku: 0 for sku in L2_SKUS}
        for st in sim_label:
            if st.order is None:
                continue
            sku = st.order.sku
            qty = min(LABELING_RATE_PER_H, st.remaining_qty)
            st.remaining_qty -= qty
            consumed[sku] += qty
            if st.remaining_qty == 0:
                st.order = None
                st.order_start_h = None

        for sku in L2_SKUS:
            sim_inventories[sku] = sim_inventories.get(sku, 0) + produced[sku] - consumed[sku]
            if sim_inventories[sku] < 0:
                return False
    return True


def _build_schedule_for_chain_start(
    *,
    orders: list[Order],
    initial_inventory: dict[str, int],
    start_time: datetime,
    chain_start_h: int,
    max_hours: int,
    frozen_slots: list[FrozenSlot] | None = None,
    frozen_rotary_slots: list[FrozenRotarySlot] | None = None,
    apply_downtime: bool = True,
) -> dict[str, Any] | None:
    # 加载生产日历（假期、维护计划）
    calendar = _load_production_calendar()

    # 如果不应用停机计划，清空 holidays 和 maintenance
    if not apply_downtime:
        calendar = {**calendar, "holidays": [], "maintenance": []}

    # 处理冻结订单
    frozen_order_ids: set[int] = set()
    frozen_by_machine: dict[str, list[FrozenSlot]] = {m: [] for m in LABELING_MACHINES}
    if frozen_slots:
        for slot in frozen_slots:
            frozen_order_ids.add(slot.order_id)
            if slot.machine in frozen_by_machine:
                frozen_by_machine[slot.machine].append(slot)
        # 按开始时间排序
        for m in LABELING_MACHINES:
            frozen_by_machine[m].sort(key=lambda s: s.start_h)

    orders_left = [o for o in orders if o.c_orderline_id not in frozen_order_ids]
    # Sort by priority (descending), deadline, then id for deterministic tie-breaker.
    orders_left.sort(key=lambda o: (-o.priority, o.deadline, o.c_orderline_id))

    # 计算需求时包含冻结订单
    demand_by_sku: dict[str, int] = {sku: 0 for sku in L2_SKUS}
    for o in orders:  # 使用原始 orders 列表
        demand_by_sku[o.sku] += o.quantity

    forming_remaining: dict[str, int] = {}
    for sku in L2_SKUS:
        forming_remaining[sku] = max(0, demand_by_sku[sku] - int(initial_inventory.get(sku, 0)))

    w_prod_h = int(math.ceil(forming_remaining["S12G9W"] / FORMING_RATE_PER_H)) if forming_remaining["S12G9W"] else 0
    v_prod_h = int(math.ceil(forming_remaining["S12G9V"] / FORMING_RATE_PER_H)) if forming_remaining["S12G9V"] else 0

    inventories: dict[str, int] = {sku: int(initial_inventory.get(sku, 0)) for sku in L2_SKUS}
    inventory_series: dict[str, list[int]] = {sku: [inventories[sku]] for sku in L2_SKUS}

    label_states = [LabelMachineState(machine=m) for m in LABELING_MACHINES]
    label_tasks: dict[str, list[dict[str, Any]]] = {m: [] for m in LABELING_MACHINES}
    forming_hours: list[FormingHour] = []

    order_schedule: dict[int, dict[str, Any]] = {}

    # 预填充冻结订单的 schedule 信息
    if frozen_slots:
        for slot in frozen_slots:
            start_dt = start_time + timedelta(hours=slot.start_h)
            end_dt = start_time + timedelta(hours=slot.end_h)
            order_schedule[slot.order_id] = {
                "machine": slot.machine,
                "sku": slot.sku,
                "quantity": slot.quantity,
                "start_h": slot.start_h,
                "due": slot.due.isoformat(),
                "deadline": slot.deadline.isoformat(),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "end_h": slot.end_h,
                "duration_h": slot.end_h - slot.start_h,
                "frozen": True,  # 标记为冻结订单
            }

    # 跟踪每台机器的下一个冻结槽位索引
    frozen_slot_idx: dict[str, int] = {m: 0 for m in LABELING_MACHINES}

    # 构建冻结的 ROTARY 小时映射
    frozen_rotary_by_hour: dict[int, FrozenRotarySlot] = {}
    if frozen_rotary_slots:
        for slot in frozen_rotary_slots:
            for h in range(slot.start_h, slot.end_h):
                frozen_rotary_by_hour[h] = slot

    t_h = 0
    while t_h < max_hours:
        # 检查是否还有订单需要处理（包含冻结订单）
        all_frozen_done = all(
            frozen_slot_idx[m] >= len(frozen_by_machine[m])
            for m in LABELING_MACHINES
        )
        if not orders_left and all(st.is_idle() for st in label_states) and all_frozen_done:
            break

        current_time = start_time + timedelta(hours=t_h)

        # 检查成型机是否停机（假期或维护）
        forming_down, forming_down_reason = _is_machine_down(FORMING_MACHINE, current_time, calendar)

        # 如果成型机停机（假期时全厂停），贴标机也停
        if forming_down:
            # 记录成型机停机时段
            forming_hours.append(FormingHour(mode="idle", setup_type=forming_down_reason))
            # 记录贴标机停机任务（用于甘特图显示）
            down_start = current_time
            down_end = current_time + timedelta(hours=1)
            for m in LABELING_MACHINES:
                label_tasks[m].append({
                    "type": "idle",
                    "setup_type": forming_down_reason,
                    "start": down_start.isoformat(),
                    "end": down_end.isoformat(),
                    "duration_h": 1,
                })
            # 库存保持不变
            for sku in L2_SKUS:
                inventory_series[sku].append(inventories[sku])
            t_h += 1
            continue

        forming_hour_base = _forming_plan_mode(t_h, chain_start_h, w_prod_h, v_prod_h)

        # 如果当前小时在冻结期内，使用冻结的 ROTARY 状态
        if t_h in frozen_rotary_by_hour:
            frozen_slot = frozen_rotary_by_hour[t_h]
            if frozen_slot.mode == "forming" and frozen_slot.sku:
                forming_hour_base = FormingHour(mode="forming", sku=frozen_slot.sku)
            elif frozen_slot.mode == "setup":
                forming_hour_base = FormingHour(mode="setup", setup_type="color_change")
            else:
                forming_hour_base = FormingHour(mode="idle")

        # 优先启动冻结订单
        for st in label_states:
            if not st.is_idle():
                continue

            # 检查该机器是否有冻结订单需要在此时刻启动
            idx = frozen_slot_idx[st.machine]
            frozen_list = frozen_by_machine[st.machine]
            if idx < len(frozen_list):
                slot = frozen_list[idx]
                if slot.start_h == t_h:
                    # 启动冻结订单
                    # 创建一个伪 Order 对象来兼容现有逻辑
                    frozen_order = Order(
                        c_orderline_id=slot.order_id,
                        poreference="",
                        sku=slot.sku,
                        quantity=slot.quantity,
                        due=slot.due,
                        deadline=slot.deadline,
                        name=None,
                        remark=None,
                        priority=999,  # 最高优先级
                    )
                    st.order = frozen_order
                    st.remaining_qty = slot.quantity
                    st.order_start_h = t_h
                    frozen_slot_idx[st.machine] = idx + 1

        # Dispatch label orders at hour boundary for idle machines.
        for st in label_states:
            # 检查该贴标机是否在维护中
            label_down, label_down_reason = _is_machine_down(st.machine, current_time, calendar)
            if label_down:
                # 如果贴标机在维护中，始终记录维护任务
                maint_start = current_time
                maint_end = current_time + timedelta(hours=1)
                label_tasks[st.machine].append({
                    "type": "idle",
                    "setup_type": label_down_reason,
                    "start": maint_start.isoformat(),
                    "end": maint_end.isoformat(),
                    "duration_h": 1,
                })
                continue  # 跳过该机器的订单分配

            if not st.is_idle():
                continue

            # 检查该机器是否有冻结订单即将开始（不要分配普通订单）
            idx = frozen_slot_idx[st.machine]
            frozen_list = frozen_by_machine[st.machine]
            next_frozen_start: int | None = None
            if idx < len(frozen_list):
                next_frozen_start = frozen_list[idx].start_h

            allowed_skus = _allowed_label_skus(forming_hour_base)

            chosen: Order | None = None
            chosen_key: tuple[Any, ...] | None = None
            for o in orders_left:
                if o.sku not in allowed_skus:
                    continue
                # 检查是否会与冻结订单冲突
                if next_frozen_start is not None:
                    order_end_h = t_h + o.proc_hours
                    if order_end_h > next_frozen_start:
                        continue  # 跳过会冲突的订单
                key = (-o.priority, o.deadline, o.c_orderline_id)
                if chosen_key is not None and key >= chosen_key:
                    continue
                if not _simulate_can_start_order(
                    now_h=t_h,
                    start_order=o,
                    start_machine=st.machine,
                    label_states=label_states,
                    inventories=inventories,
                    forming_remaining=forming_remaining,
                    chain_start_h=chain_start_h,
                    w_prod_h=w_prod_h,
                    v_prod_h=v_prod_h,
                ):
                    continue
                chosen = o
                chosen_key = key

            # If nothing feasible in allowed SKUs, relax to any SKU.
            if chosen is None:
                for o in orders_left:
                    # 检查是否会与冻结订单冲突
                    if next_frozen_start is not None:
                        order_end_h = t_h + o.proc_hours
                        if order_end_h > next_frozen_start:
                            continue  # 跳过会冲突的订单
                    key = (-o.priority, o.deadline, o.c_orderline_id)
                    if chosen_key is not None and key >= chosen_key:
                        continue
                    if not _simulate_can_start_order(
                        now_h=t_h,
                        start_order=o,
                        start_machine=st.machine,
                        label_states=label_states,
                        inventories=inventories,
                        forming_remaining=forming_remaining,
                        chain_start_h=chain_start_h,
                        w_prod_h=w_prod_h,
                        v_prod_h=v_prod_h,
                    ):
                        continue
                    chosen = o
                    chosen_key = key

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
            }

        # Execute one hour: forming + labeling.
        forming_qty_by_sku: dict[str, int] = {sku: 0 for sku in L2_SKUS}
        forming_hour = forming_hour_base
        if forming_hour.mode == "forming" and forming_hour.sku in forming_remaining:
            sku = forming_hour.sku
            if forming_remaining[sku] > 0:
                qty = FORMING_RATE_PER_H
                forming_remaining[sku] = max(0, forming_remaining[sku] - qty)
                forming_hour = FormingHour(mode="forming", sku=sku, qty=qty)
                forming_qty_by_sku[sku] = qty
            else:
                forming_hour = FormingHour(mode="idle")
        else:
            forming_hour = forming_hour_base

        consumed_qty_by_sku: dict[str, int] = {sku: 0 for sku in L2_SKUS}
        for st in label_states:
            if st.order is None:
                continue
            # 检查该贴标机是否在维护中，如果是则该小时不处理订单
            label_down, _ = _is_machine_down(st.machine, current_time, calendar)
            if label_down:
                continue  # 维护期间不处理订单
            sku = st.order.sku
            qty = min(LABELING_RATE_PER_H, st.remaining_qty)
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
                        "duration_h": st.order.proc_hours,
                    }
                )
                order_schedule[st.order.c_orderline_id].update(
                    {
                        "start": start_dt.isoformat(),
                        "end": end_dt.isoformat(),
                        "end_h": t_h + 1,
                        "duration_h": st.order.proc_hours,
                    }
                )
                st.order = None
                st.order_start_h = None

        for sku in L2_SKUS:
            inventories[sku] = inventories.get(sku, 0) + forming_qty_by_sku[sku] - consumed_qty_by_sku[sku]
            if inventories[sku] < 0:
                return None
            inventory_series[sku].append(inventories[sku])

        forming_hours.append(forming_hour)
        t_h += 1

    if orders_left:
        return None
    if any(not st.is_idle() for st in label_states):
        return None

    makespan_h = t_h

    # Add explicit idle gaps for labelers (helps visualization).
    for m in LABELING_MACHINES:
        tasks = label_tasks[m]
        if not tasks:
            continue
        filled: list[dict[str, Any]] = []
        cur = start_time
        for task in tasks:
            ts = datetime.fromisoformat(task["start"])
            te = datetime.fromisoformat(task["end"])
            if ts > cur:
                filled.append(
                    {
                        "type": "idle",
                        "start": cur.isoformat(),
                        "end": ts.isoformat(),
                        "duration_h": int((ts - cur).total_seconds() // 3600),
                    }
                )
            filled.append(task)
            cur = te
        label_tasks[m] = filled

    # Compress forming hours to tasks.
    forming_tasks: list[dict[str, Any]] = []
    cur_task: dict[str, Any] | None = None
    cur_start_h = 0
    for idx, fh in enumerate(forming_hours):
        def _task_key(x: FormingHour) -> tuple[Any, ...]:
            if x.mode == "forming":
                return ("forming", x.sku)
            if x.mode == "setup":
                return ("setup", x.setup_type, x.from_sku, x.to_sku)
            # idle: 区分不同类型的停机（假期、维护、普通空闲）
            return ("idle", x.setup_type)

        if cur_task is None:
            cur_start_h = idx
            cur_task = fh.__dict__.copy()
            cur_task["_key"] = _task_key(fh)
            cur_task["_qty_sum"] = fh.qty
            continue

        if _task_key(fh) != cur_task["_key"]:
            start_dt = start_time + timedelta(hours=cur_start_h)
            end_dt = start_time + timedelta(hours=idx)
            out = {
                "type": cur_task["mode"],
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration_h": idx - cur_start_h,
            }
            if cur_task["_key"][0] == "forming":
                out.update({"sku": cur_task["sku"], "quantity": int(cur_task["_qty_sum"])})
            elif cur_task["_key"][0] == "setup":
                out.update(
                    {
                        "setup_type": cur_task["setup_type"],
                        "from_sku": cur_task["from_sku"],
                        "to_sku": cur_task["to_sku"],
                    }
                )
            elif cur_task["_key"][0] == "idle" and cur_task.get("setup_type"):
                out.update({"setup_type": cur_task["setup_type"]})
            forming_tasks.append(out)
            cur_start_h = idx
            cur_task = fh.__dict__.copy()
            cur_task["_key"] = _task_key(fh)
            cur_task["_qty_sum"] = fh.qty
        else:
            cur_task["_qty_sum"] += fh.qty

    if cur_task is not None:
        start_dt = start_time + timedelta(hours=cur_start_h)
        end_dt = start_time + timedelta(hours=len(forming_hours))
        out = {
            "type": cur_task["mode"],
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "duration_h": len(forming_hours) - cur_start_h,
        }
        if cur_task["_key"][0] == "forming":
            out.update({"sku": cur_task["sku"], "quantity": int(cur_task["_qty_sum"])})
        elif cur_task["_key"][0] == "setup":
            out.update(
                {
                    "setup_type": cur_task["setup_type"],
                    "from_sku": cur_task["from_sku"],
                    "to_sku": cur_task["to_sku"],
                }
            )
        elif cur_task["_key"][0] == "idle" and cur_task.get("setup_type"):
            out.update({"setup_type": cur_task["setup_type"]})
        forming_tasks.append(out)

    # Order KPIs.
    on_time = 0
    tardiness_h_sum = 0.0
    expired_before_start_count = 0
    order_rows: list[dict[str, Any]] = []
    for o in orders:
        s = order_schedule[o.c_orderline_id]
        end_dt = datetime.fromisoformat(s["end"])
        deadline = o.deadline
        lateness_h = max(0.0, (end_dt - deadline).total_seconds() / 3600.0)
        expired_before_start = deadline <= start_time
        expired_before_start_count += int(expired_before_start)
        is_on_time = (not expired_before_start) and lateness_h <= 1e-9
        on_time += int(is_on_time)
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
            }
        )

    # Container KPIs (delivery unit = poreference).
    container_groups: dict[str, list[Order]] = {}
    for o in orders:
        container_id = str(o.poreference or "").strip()
        if not container_id:
            container_id = f"__NO_POREFERENCE__{o.c_orderline_id}"
        container_groups.setdefault(container_id, []).append(o)

    containers_on_time = 0
    containers_tardiness_h_sum = 0.0
    containers_expired_before_start_count = 0
    container_rows: list[dict[str, Any]] = []
    for container_id, group in sorted(container_groups.items(), key=lambda kv: kv[0]):
        due = min(o.due for o in group)
        deadline = min(o.deadline for o in group)
        qty_sum = sum(int(o.quantity) for o in group)
        order_ids = sorted(int(o.c_orderline_id) for o in group)

        starts: list[datetime] = []
        ends: list[datetime] = []
        for o in group:
            s = order_schedule[o.c_orderline_id]
            starts.append(datetime.fromisoformat(s["start"]))
            ends.append(datetime.fromisoformat(s["end"]))

        start_dt = min(starts) if starts else start_time
        end_dt = max(ends) if ends else start_time

        container_lateness_h = max(0.0, (end_dt - deadline).total_seconds() / 3600.0)
        expired_before_start = deadline <= start_time
        is_on_time = (not expired_before_start) and container_lateness_h <= 1e-9
        containers_on_time += int(is_on_time)
        containers_tardiness_h_sum += container_lateness_h
        containers_expired_before_start_count += int(expired_before_start)

        container_rows.append(
            {
                "container_id": container_id,
                "order_ids": order_ids,
                "orders_count": len(order_ids),
                "total_quantity": qty_sum,
                "due": due.isoformat(),
                "deadline": deadline.isoformat(),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "on_time": is_on_time,
                "expired_before_start": expired_before_start,
                "lateness_h": container_lateness_h,
            }
        )

    setup_count = sum(1 for t in forming_tasks if t["type"] == "setup")
    campaigns = [t for t in forming_tasks if t["type"] == "forming"]
    avg_campaign_h = (sum(t["duration_h"] for t in campaigns) / len(campaigns)) if campaigns else 0.0

    # 计算停机时长（假期、维护）
    holiday_hours = sum(
        t["duration_h"] for t in forming_tasks
        if t["type"] == "idle" and str(t.get("setup_type") or "").startswith("holiday:")
    )
    maintenance_hours = sum(
        t["duration_h"] for t in forming_tasks
        if t["type"] == "idle" and str(t.get("setup_type") or "").startswith("maintenance:")
    )
    total_downtime_hours = holiday_hours + maintenance_hours
    effective_hours = makespan_h - total_downtime_hours
    effective_utilization = (effective_hours / makespan_h) if makespan_h > 0 else 1.0

    inv_mins = {sku: min(series) for sku, series in inventory_series.items()}

    return {
        "meta": {
            "line": LINE,
            "start_time": start_time.isoformat(),
            "time_step_h": 1,
            "assumptions": {
                "continuous_24x7": True,
                "due_deadline_rule": "finish_before_due_time_ceil_hour",
                "label_order_no_split": True,
                "delivery_unit": "container(poreference)",
                "container_completion_rule": "max_label_end",
                "container_due_agg": "min",
                "container_deadline_agg": "min",
                "label_changeover_h": 0,
                "forming_color_change_h": COLOR_CHANGE_H,
            },
            "rates_per_h": {
                "forming": FORMING_RATE_PER_H,
                "labeling_each": LABELING_RATE_PER_H,
            },
            "chain_start_h": chain_start_h,
            "horizon_h": makespan_h,
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
            "avg_campaign_h": avg_campaign_h,
            "total_downtime_hours": total_downtime_hours,
            "holiday_hours": holiday_hours,
            "maintenance_hours": maintenance_hours,
            "effective_utilization": effective_utilization,
        },
        "validation": {
            "inventory_min": inv_mins,
            "inventory_nonnegative": all(v >= 0 for v in inv_mins.values()),
        },
        "machines": {
            FORMING_MACHINE: forming_tasks,
            LABELING_MACHINES[0]: label_tasks[LABELING_MACHINES[0]],
            LABELING_MACHINES[1]: label_tasks[LABELING_MACHINES[1]],
        },
        "orders": order_rows,
        "containers": container_rows,
        "inventory": {
            "start_time": start_time.isoformat(),
            "time_step_h": 1,
            "skus": {sku: series for sku, series in inventory_series.items()},
        },
    }


def _pick_best_schedule(
    *,
    orders: list[Order],
    initial_inventory: dict[str, int],
    start_time: datetime,
    chain_start_candidates_h: list[int],
    max_hours: int,
    apply_downtime: bool = True,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float, float, int] | None = None

    for chain_start_h in chain_start_candidates_h:
        sched = _build_schedule_for_chain_start(
            orders=orders,
            initial_inventory=initial_inventory,
            start_time=start_time,
            chain_start_h=chain_start_h,
            max_hours=max_hours,
            apply_downtime=apply_downtime,
        )
        if sched is None:
            continue
        kpi = sched["kpi"]
        container_tardiness = float(kpi.get("total_container_tardiness_h") or kpi.get("total_tardiness_h") or 0.0)
        containers_on_time_rate = float(kpi.get("containers_on_time_rate") or kpi.get("on_time_rate") or 0.0)
        order_tardiness = float(kpi.get("total_tardiness_h") or 0.0)
        order_on_time_rate = float(kpi.get("on_time_rate") or 0.0)
        key = (
            container_tardiness,
            -containers_on_time_rate,
            order_tardiness,
            -order_on_time_rate,
            int(sched["meta"]["horizon_h"]),
        )
        if best_key is None or key < best_key:
            best_key = key
            best = sched

    if best is None:
        raise RuntimeError("Failed to produce any feasible schedule from the provided candidates.")
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate feasible APS schedule for L2 line (S12G9*).")
    default_dir = Path(__file__).resolve().parent
    parser.add_argument("--orders", type=Path, default=default_dir / "orders_erp.json")
    parser.add_argument("--inventory", type=Path, default=default_dir / "inventory_erp.json")
    parser.add_argument("--out", type=Path, default=default_dir / "schedule_result.json")
    parser.add_argument("--start", type=str, default=DEFAULT_START_TIME, help="Schedule start time (YYYY-mm-dd HH:MM)")
    parser.add_argument(
        "--chain-search-days",
        type=int,
        default=60,
        help="Search window for chain start (0..N days, step 12h).",
    )
    parser.add_argument("--max-hours", type=int, default=5000, help="Safety cap for simulation horizon.")
    args = parser.parse_args()

    if not args.orders.exists() or not args.inventory.exists():
        missing: list[str] = []
        if not args.orders.exists():
            missing.append(f"--orders {args.orders}")
        if not args.inventory.exists():
            missing.append(f"--inventory {args.inventory}")
        missing_s = ", ".join(missing)
        raise SystemExit(
            "Missing required ERP snapshot file(s): "
            f"{missing_s}\n"
            "Fetch fresh snapshots first (backend must be running):\n"
            '  curl -X POST "http://localhost:8000/api/erp/sync?isTest=true"\n'
        )

    if args.start != DEFAULT_START_TIME:
        # 用户明确指定了 --start 参数
        start_time = _parse_start_datetime(args.start)
    else:
        # 尝试从订单文件提取时间戳
        extracted = _extract_start_from_orders(args.orders)
        start_time = extracted if extracted else _parse_start_datetime(args.start)

    raw_orders = _load_api_list(args.orders)
    orders: list[Order] = []
    for o in raw_orders:
        sku = str(o.get("sku"))
        if sku not in L2_SKUS:
            continue
        due = _parse_due_datetime(str(o.get("duedate")))
        orders.append(
            Order(
                c_orderline_id=int(o["c_orderline_id"]),
                poreference=str(o.get("poreference") or ""),
                sku=sku,
                quantity=int(o["quantity"]),
                due=due,
                deadline=_due_deadline(due),
                name=o.get("name"),
                remark=o.get("remark"),
            )
        )

    raw_inv = _load_api_list(args.inventory)
    initial_inventory: dict[str, int] = {sku: 0 for sku in L2_SKUS}
    for r in raw_inv:
        code = str(r.get("materialcode"))
        if code in initial_inventory:
            initial_inventory[code] = int(r.get("quantity") or 0)

    if not orders:
        raise RuntimeError("No L2 orders found in the provided orders file.")

    # Candidates: every 12 hours within the window.
    max_chain_start_h = max(0, int(args.chain_search_days) * 24)
    candidates = list(range(0, max_chain_start_h + 1, 12))

    sched = _pick_best_schedule(
        orders=orders,
        initial_inventory=initial_inventory,
        start_time=start_time,
        chain_start_candidates_h=candidates,
        max_hours=int(args.max_hours),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {args.out}")
    print(
        "KPI: "
        f"containers_on_time_rate={sched['kpi'].get('containers_on_time_rate', 0.0):.3f}, "
        f"total_container_tardiness_h={sched['kpi'].get('total_container_tardiness_h', 0.0):.1f}, "
        f"orders_on_time_rate={sched['kpi'].get('on_time_rate', 0.0):.3f}, "
        f"total_tardiness_h={sched['kpi'].get('total_tardiness_h', 0.0):.1f}, "
        f"chain_start_h={sched['meta'].get('chain_start_h')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
