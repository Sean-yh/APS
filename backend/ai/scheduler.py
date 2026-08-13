from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import process.generate_schedule as gen

from .data import DEFAULT_INVENTORY_PATH, DEFAULT_ORDERS_PATH, DEFAULT_SCHEDULE_PATH, PROCESS_DIR, load_schedule


def _find_order_row(schedule: dict[str, Any], order_id: int) -> dict[str, Any] | None:
    rows = schedule.get("orders")
    if not isinstance(rows, list):
        return None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if int(r.get("c_orderline_id") or -1) == int(order_id):
            return r
    return None


def due_deadline(due: datetime) -> datetime:
    # Same rule as process/generate_schedule.py: due time 向上取整到小时
    if due.minute > 0 or due.second > 0 or due.microsecond > 0:
        return due.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return due.replace(minute=0, second=0, microsecond=0)


def apply_due_override_to_schedule(*, schedule: dict[str, Any], order_id: int, new_due: datetime) -> dict[str, Any]:
    meta = schedule.get("meta") if isinstance(schedule.get("meta"), dict) else {}
    start_time_s = meta.get("start_time")
    if not isinstance(start_time_s, str) or not start_time_s:
        raise ValueError("schedule.meta.start_time is missing")
    start_time = datetime.fromisoformat(start_time_s)

    new_deadline = due_deadline(new_due)
    new_orders: list[dict[str, Any]] = []

    old_orders = schedule.get("orders") if isinstance(schedule.get("orders"), list) else []
    if not old_orders:
        raise ValueError("schedule.orders is missing")

    for row in old_orders:
        if not isinstance(row, dict):
            continue
        if int(row.get("c_orderline_id") or -1) != int(order_id):
            new_orders.append(row)
            continue

        r = dict(row)
        r["due"] = new_due.isoformat()
        r["deadline"] = new_deadline.isoformat()
        end_dt = datetime.fromisoformat(str(r.get("end")))
        lateness_h = max(0.0, (end_dt - new_deadline).total_seconds() / 3600.0)
        expired_before_start = new_deadline <= start_time
        r["expired_before_start"] = expired_before_start
        r["on_time"] = (not expired_before_start) and lateness_h <= 1e-9
        r["lateness_h"] = lateness_h
        new_orders.append(r)

    # Recompute KPI from order rows to keep it consistent with the overridden due date.
    on_time = 0
    expired = 0
    tardiness_h_sum = 0.0
    for r in new_orders:
        if not isinstance(r, dict):
            continue
        on_time += int(bool(r.get("on_time")))
        expired += int(bool(r.get("expired_before_start")))
        tardiness_h_sum += float(r.get("lateness_h") or 0.0)

    # Recompute container KPIs (delivery unit = poreference).
    containers: dict[str, list[dict[str, Any]]] = {}
    for r in new_orders:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("poreference") or "").strip()
        if not cid:
            cid = f"__NO_POREFERENCE__{r.get('c_orderline_id')}"
        containers.setdefault(cid, []).append(r)

    container_rows: list[dict[str, Any]] = []
    containers_on_time = 0
    containers_expired = 0
    containers_tardiness_h_sum = 0.0

    for cid, group in sorted(containers.items(), key=lambda kv: kv[0]):
        order_ids = sorted(int(o.get("c_orderline_id") or -1) for o in group)

        due_vals: list[datetime] = []
        deadline_vals: list[datetime] = []
        start_vals: list[datetime] = []
        end_vals: list[datetime] = []
        qty_sum = 0
        for o in group:
            qty_sum += int(o.get("quantity") or 0)
            due_s = o.get("due")
            if due_s:
                due_vals.append(datetime.fromisoformat(str(due_s)))
            dl_s = o.get("deadline")
            if dl_s:
                deadline_vals.append(datetime.fromisoformat(str(dl_s)))
            st_s = o.get("start")
            if st_s:
                start_vals.append(datetime.fromisoformat(str(st_s)))
            en_s = o.get("end")
            if en_s:
                end_vals.append(datetime.fromisoformat(str(en_s)))

        due_dt = min(due_vals) if due_vals else start_time
        deadline_dt = min(deadline_vals) if deadline_vals else start_time
        start_dt = min(start_vals) if start_vals else start_time
        end_dt = max(end_vals) if end_vals else start_time

        lateness_h = max(0.0, (end_dt - deadline_dt).total_seconds() / 3600.0)
        expired_before_start = deadline_dt <= start_time
        is_on_time = (not expired_before_start) and lateness_h <= 1e-9

        containers_on_time += int(is_on_time)
        containers_expired += int(expired_before_start)
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

    kpi = schedule.get("kpi") if isinstance(schedule.get("kpi"), dict) else {}
    new_kpi = dict(kpi)
    new_kpi["orders_total"] = len(new_orders)
    new_kpi["orders_on_time"] = on_time
    new_kpi["orders_expired_before_start"] = expired
    new_kpi["on_time_rate"] = (on_time / len(new_orders)) if new_orders else 1.0
    new_kpi["total_tardiness_h"] = tardiness_h_sum
    new_kpi["total_tardiness_days"] = tardiness_h_sum / 24.0
    new_kpi["containers_total"] = len(container_rows)
    new_kpi["containers_on_time"] = containers_on_time
    new_kpi["containers_expired_before_start"] = containers_expired
    new_kpi["containers_on_time_rate"] = (containers_on_time / len(container_rows)) if container_rows else 1.0
    new_kpi["total_container_tardiness_h"] = containers_tardiness_h_sum
    new_kpi["total_container_tardiness_days"] = containers_tardiness_h_sum / 24.0

    out = dict(schedule)
    out["orders"] = new_orders
    out["containers"] = container_rows
    out["kpi"] = new_kpi
    return out


def default_start_time() -> datetime:
    """返回当前时间，向上取整到小时"""
    now = datetime.now()
    if now.minute > 0 or now.second > 0 or now.microsecond > 0:
        now = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        now = now.replace(minute=0, second=0, microsecond=0)
    return now


def _build_orders(*, orders_path: Path, due_overrides: dict[int, datetime] | None = None) -> list[gen.Order]:
    raw_orders = gen._load_api_list(orders_path)
    orders: list[gen.Order] = []
    for o in raw_orders:
        sku = str(o.get("sku"))
        if sku not in gen.L2_SKUS:
            continue
        order_id = int(o["c_orderline_id"])
        if due_overrides and order_id in due_overrides:
            due = due_overrides[order_id]
        else:
            due = gen._parse_due_datetime(str(o.get("duedate")))
        orders.append(
            gen.Order(
                c_orderline_id=order_id,
                poreference=str(o.get("poreference") or ""),
                sku=sku,
                quantity=int(o["quantity"]),
                due=due,
                deadline=due_deadline(due),
                name=o.get("name"),
                remark=o.get("remark"),
            )
        )
    if not orders:
        raise RuntimeError("No L2 orders found in the provided orders file.")
    return orders


def _build_inventory(*, inventory_path: Path) -> dict[str, int]:
    raw_inv = gen._load_api_list(inventory_path)
    initial_inventory: dict[str, int] = {sku: 0 for sku in gen.L2_SKUS}
    for r in raw_inv:
        code = str(r.get("materialcode"))
        if code in initial_inventory:
            initial_inventory[code] = int(r.get("quantity") or 0)
    return initial_inventory


def _chain_candidates(
    chain_search_days: int,
    rotary_state: str | None = None,
    setup_remaining_h: int | None = None,
) -> list[int]:
    """生成成型链启动点候选值列表。

    Args:
        chain_search_days: 搜索范围（天数）
        rotary_state: 成型机当前状态
            - producing_c/w/v: 正在生产某 SKU
            - setup: 正在换色
            - idle: 空闲
        setup_remaining_h: 换色剩余小时数（仅 rotary_state=setup 时需要）

    Returns:
        chain_start_h 候选值列表
    """
    max_chain_start_h = max(0, int(chain_search_days) * 24)

    if rotary_state == "setup":
        # 正在换色：从换色完成后开始搜索
        remaining = setup_remaining_h if setup_remaining_h is not None else 6
        # 确保在合理范围内
        remaining = max(0, min(12, remaining))
        # 换色完成后开始搜索
        candidates = list(range(remaining, max_chain_start_h + 1, 12))
        if not candidates:
            candidates = [remaining]
        return candidates

    # 其他状态（正在生产或空闲）：搜索所有候选值
    return list(range(0, max_chain_start_h + 1, 12))


def generate_best_schedule(
    *,
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    start_time: datetime | None = None,
    chain_search_days: int = 60,
    max_hours: int = 5000,
    due_overrides: dict[int, datetime] | None = None,
    apply_downtime: bool = True,
    rotary_state: str | None = None,
    setup_remaining_h: int | None = None,
) -> dict[str, Any]:
    st = start_time or default_start_time()
    orders = _build_orders(orders_path=orders_path, due_overrides=due_overrides)
    inv = _build_inventory(inventory_path=inventory_path)
    candidates = _chain_candidates(
        chain_search_days,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )
    return gen._pick_best_schedule(
        orders=orders,
        initial_inventory=inv,
        start_time=st,
        chain_start_candidates_h=candidates,
        max_hours=int(max_hours),
        apply_downtime=apply_downtime,
    )


def generate_schedule_with_due_constraint(
    *,
    target_order_ids: list[int] | int,
    target_due: datetime,
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    start_time: datetime | None = None,
    chain_search_days: int = 60,
    max_hours: int = 5000,
    apply_downtime: bool = True,
    rotary_state: str | None = None,
    setup_remaining_h: int | None = None,
) -> dict[str, Any]:
    """为指定订单（或整个货柜的多个订单）设置新的交期约束并重排。

    Args:
        target_order_ids: 目标订单 ID，可以是单个 int 或 int 列表（用于约束整个货柜）
        target_due: 新的交期
        apply_downtime: 是否应用停机计划（假期、维护），默认 True
        rotary_state: 成型机当前状态
        setup_remaining_h: 换色剩余小时数
        其他参数同 generate_schedule

    Returns:
        包含 status、schedule、target 等信息的结果字典
    """
    st = start_time or default_start_time()

    # 统一转换为列表
    if isinstance(target_order_ids, int):
        order_id_list = [target_order_ids]
    else:
        order_id_list = list(target_order_ids)

    order_id_set = set(order_id_list)

    base_orders = _build_orders(orders_path=orders_path, due_overrides=None)
    overridden: list[gen.Order] = []
    for o in base_orders:
        if int(o.c_orderline_id) in order_id_set:
            overridden.append(replace(o, due=target_due, deadline=due_deadline(target_due)))
        else:
            overridden.append(o)

    inv = _build_inventory(inventory_path=inventory_path)
    candidates = _chain_candidates(
        chain_search_days,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )

    best_ok: dict[str, Any] | None = None
    best_ok_key: tuple[float, float, float, float, int] | None = None
    best_effort: dict[str, Any] | None = None
    best_effort_key: tuple[float, float, float, int] | None = None

    # 用于计算所有目标订单的最大延迟
    primary_order_id = order_id_list[0]  # 主要订单（用于返回 target）

    for chain_start_h in candidates:
        sched = gen._build_schedule_for_chain_start(
            orders=overridden,
            initial_inventory=inv,
            start_time=st,
            chain_start_h=int(chain_start_h),
            max_hours=int(max_hours),
            apply_downtime=apply_downtime,
        )
        if sched is None:
            continue

        # 检查所有目标订单是否都满足约束
        all_targets_on_time = True
        max_target_lateness_h = 0.0
        for oid in order_id_list:
            target_row = _find_order_row(sched, oid)
            if not target_row:
                all_targets_on_time = False
                break
            lateness_h = float(target_row.get("lateness_h") or 0.0)
            expired = bool(target_row.get("expired_before_start"))
            on_time = bool(target_row.get("on_time")) and (not expired) and lateness_h <= 1e-9
            max_target_lateness_h = max(max_target_lateness_h, lateness_h)
            if not on_time:
                all_targets_on_time = False

        kpi = sched.get("kpi") if isinstance(sched.get("kpi"), dict) else {}
        total_tardiness_h = float(kpi.get("total_tardiness_h") or 0.0)
        on_time_rate = float(kpi.get("on_time_rate") or 0.0)
        total_container_tardiness_h = float(kpi.get("total_container_tardiness_h") or total_tardiness_h)
        containers_on_time_rate = float(kpi.get("containers_on_time_rate") or on_time_rate)
        horizon_h = int((sched.get("meta") or {}).get("horizon_h") or 0)

        # "Best effort" prioritizes the target orders' max lateness first.
        effort_key = (max_target_lateness_h, total_container_tardiness_h, total_tardiness_h, horizon_h)
        if best_effort_key is None or effort_key < best_effort_key:
            best_effort_key = effort_key
            best_effort = sched

        if not all_targets_on_time:
            continue

        # Same global objective as the original generator (container tardiness first).
        ok_key = (
            total_container_tardiness_h,
            -containers_on_time_rate,
            total_tardiness_h,
            -on_time_rate,
            horizon_h,
        )
        if best_ok_key is None or ok_key < best_ok_key:
            best_ok_key = ok_key
            best_ok = sched

    if best_ok is not None:
        return {
            "status": "ok",
            "schedule": best_ok,
            "best_effort": best_effort,
            "target": _find_order_row(best_ok, primary_order_id),
            "target_order_ids": order_id_list,
        }
    return {
        "status": "infeasible",
        "schedule": best_effort,
        "best_effort": best_effort,
        "target": _find_order_row(best_effort, primary_order_id) if best_effort else None,
        "target_order_ids": order_id_list,
    }


def generate_schedule_with_priority_lock(
    *,
    priority_order_ids: list[int],
    due_overrides: dict[int, datetime] | None = None,
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    start_time: datetime | None = None,
    chain_search_days: int = 60,
    max_hours: int = 5000,
    apply_downtime: bool = True,
    rotary_state: str | None = None,
    setup_remaining_h: int | None = None,
) -> dict[str, Any]:
    """优先锁定指定订单的产能，然后排产其他订单。

    与普通排产不同，此函数会：
    1. 将指定订单设为最高优先级（priority=1）
    2. 排产算法会优先为这些订单分配产能时间段
    3. 其他订单在剩余产能中排产

    Args:
        priority_order_ids: 需要优先锁定的订单 ID 列表
        due_overrides: 可选的交期修改（订单ID -> 新交期）
        orders_path: 订单数据文件路径
        inventory_path: 库存数据文件路径
        start_time: 排产开始时间
        chain_search_days: 成型链启动搜索天数
        max_hours: 最大排产小时数
        apply_downtime: 是否应用停机计划（假期、维护），默认 True
        rotary_state: 成型机当前状态
        setup_remaining_h: 换色剩余小时数

    Returns:
        排产结果字典，包含:
        - status: "ok", "partial" 或 "infeasible"
        - schedule: 排产方案
        - priority_orders: 优先订单的排产结果
    """
    st = start_time or default_start_time()
    priority_set = set(priority_order_ids)

    # Build orders with priority and optional due overrides.
    base_orders = _build_orders(orders_path=orders_path, due_overrides=due_overrides)
    orders_with_priority: list[gen.Order] = []
    for o in base_orders:
        if o.c_orderline_id in priority_set:
            orders_with_priority.append(replace(o, priority=1))
        else:
            orders_with_priority.append(o)

    inv = _build_inventory(inventory_path=inventory_path)
    candidates = _chain_candidates(
        chain_search_days,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )

    best: dict[str, Any] | None = None
    best_key: tuple[Any, ...] | None = None

    for chain_start_h in candidates:
        sched = gen._build_schedule_for_chain_start(
            orders=orders_with_priority,
            initial_inventory=inv,
            start_time=st,
            chain_start_h=int(chain_start_h),
            max_hours=int(max_hours),
            apply_downtime=apply_downtime,
        )
        if sched is None:
            continue

        # Check if all priority orders meet their deadlines.
        all_priority_on_time = True
        priority_lateness_sum = 0.0
        for oid in priority_set:
            row = _find_order_row(sched, oid)
            if not row:
                all_priority_on_time = False
                break
            lateness_h = float(row.get("lateness_h") or 0.0)
            expired = bool(row.get("expired_before_start"))
            if expired or lateness_h > 1e-9:
                all_priority_on_time = False
            priority_lateness_sum += lateness_h

        kpi = sched.get("kpi") if isinstance(sched.get("kpi"), dict) else {}
        total_tardiness_h = float(kpi.get("total_tardiness_h") or 0.0)
        on_time_rate = float(kpi.get("on_time_rate") or 0.0)
        total_container_tardiness_h = float(kpi.get("total_container_tardiness_h") or total_tardiness_h)
        containers_on_time_rate = float(kpi.get("containers_on_time_rate") or on_time_rate)
        horizon_h = int((sched.get("meta") or {}).get("horizon_h") or 0)

        # Prioritize: all priority orders on time, then priority lateness, then global tardiness.
        key = (
            0 if all_priority_on_time else 1,
            priority_lateness_sum,
            total_container_tardiness_h,
            -containers_on_time_rate,
            total_tardiness_h,
            -on_time_rate,
            horizon_h,
        )
        if best_key is None or key < best_key:
            best_key = key
            best = sched

    if best is None:
        return {
            "status": "infeasible",
            "schedule": None,
            "priority_orders": [],
        }

    # Extract priority order results.
    priority_results = []
    for oid in priority_order_ids:
        row = _find_order_row(best, oid)
        if row:
            priority_results.append(row)

    all_on_time = all(
        r.get("on_time") and not r.get("expired_before_start")
        for r in priority_results
    )

    return {
        "status": "ok" if all_on_time else "partial",
        "schedule": best,
        "priority_orders": priority_results,
    }


# =============================================================================
# 换色周期优化：额外生产半成品
# =============================================================================

# 虚拟订单标识常量
BUFFER_POREFERENCE = "__BUFFER__"


def generate_schedule_with_extra_production(
    *,
    extra_production: dict[str, int],
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    chain_search_days: int = 60,
    max_hours: int = 5000,
    apply_downtime: bool = True,
    rotary_state: str | None = None,
    setup_remaining_h: int | None = None,
) -> dict[str, Any]:
    """带额外半成品生产的排产函数。

    将用户选择的额外生产量转化为低优先级虚拟订单，利用空闲产能进行生产。
    虚拟订单具有最低优先级(priority=-1)和最晚交期，确保不影响真实订单的准时率。

    Args:
        extra_production: 额外生产计划，格式为 {SKU: 数量}，如 {"S12G9C": 5000, "S12G9W": 3000}
        orders_path: 订单文件路径
        inventory_path: 库存文件路径
        schedule_path: 当前排产结果路径（用于获取排产参数）
        chain_search_days: 成型链搜索范围（天数）
        max_hours: 排产最大小时数
        apply_downtime: 是否应用停机计划

    Returns:
        包含排产结果的字典:
        - status: "ok" 或 "partial"
        - schedule: 排产结果（已过滤虚拟订单、重新计算KPI）
        - buffer_orders: 额外生产的虚拟订单列表
    """
    # 1. 加载当前排产获取参数
    current_schedule = load_schedule(schedule_path)
    meta = current_schedule.get("meta", {})

    # 从当前排产获取开始时间
    start_time_str = meta.get("start_time")
    if start_time_str:
        start_time = datetime.fromisoformat(start_time_str)
    else:
        start_time = default_start_time()

    # 获取排产时间范围（用于设置虚拟订单交期）
    horizon_h = int(meta.get("horizon_h") or 2000)
    # 虚拟订单交期设在排产末尾之后
    buffer_due = start_time + timedelta(hours=horizon_h + 100)

    # 2. 构建订单列表（真实订单）
    real_orders = _build_orders(orders_path=orders_path, due_overrides=None)
    inv = _build_inventory(inventory_path=inventory_path)

    # 3. 创建虚拟订单（额外生产的半成品）
    buffer_orders: list[gen.Order] = []
    buffer_id = -1  # 从 -1 开始递减

    for sku, qty in extra_production.items():
        if qty <= 0:
            continue
        if sku not in gen.L2_SKUS:
            continue

        buffer_order = gen.Order(
            c_orderline_id=buffer_id,
            poreference=BUFFER_POREFERENCE,
            sku=sku,
            quantity=qty,
            due=buffer_due,
            deadline=buffer_due,
            name=f"BUFFER-{sku}",
            remark="额外生产半成品库存",
            priority=-1,  # 最低优先级，确保最后处理
        )
        buffer_orders.append(buffer_order)
        buffer_id -= 1

    # 4. 合并订单列表
    all_orders = real_orders + buffer_orders

    # 5. 获取成型链候选
    candidates = _chain_candidates(
        chain_search_days,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )

    # 6. 执行排产
    best_schedule = gen._pick_best_schedule(
        orders=all_orders,
        initial_inventory=inv,
        start_time=start_time,
        chain_start_candidates_h=candidates,
        max_hours=int(max_hours),
        apply_downtime=apply_downtime,
    )

    # 7. 后处理：过滤虚拟订单，重新计算 KPI
    filtered_schedule = _filter_buffer_orders_and_recalc_kpi(
        schedule=best_schedule,
        buffer_order_ids={bo.c_orderline_id for bo in buffer_orders},
    )

    # 8. 提取虚拟订单的排产信息
    buffer_order_rows = []
    all_order_rows = best_schedule.get("orders", [])
    for row in all_order_rows:
        oid = int(row.get("c_orderline_id") or 0)
        if oid < 0:  # 虚拟订单 ID 为负数
            buffer_order_rows.append(row)

    return {
        "status": "ok",
        "schedule": filtered_schedule,
        "buffer_orders": buffer_order_rows,
    }


def _filter_buffer_orders_and_recalc_kpi(
    *,
    schedule: dict[str, Any],
    buffer_order_ids: set[int],
) -> dict[str, Any]:
    """从排产结果中过滤虚拟订单，并重新计算 KPI。

    Args:
        schedule: 原始排产结果（包含虚拟订单）
        buffer_order_ids: 虚拟订单 ID 集合

    Returns:
        过滤后的排产结果（KPI 只统计真实订单）
    """
    import copy

    result = copy.deepcopy(schedule)

    # 过滤订单列表
    all_orders = result.get("orders", [])
    real_orders = [
        o for o in all_orders
        if int(o.get("c_orderline_id") or 0) not in buffer_order_ids
        and str(o.get("poreference") or "") != BUFFER_POREFERENCE
    ]
    result["orders"] = real_orders

    # 过滤货柜列表（虚拟订单的 poreference 是 __BUFFER__）
    all_containers = result.get("containers", [])
    real_containers = [
        c for c in all_containers
        if str(c.get("container_id") or "") != BUFFER_POREFERENCE
    ]
    result["containers"] = real_containers

    # 重新计算 KPI（只统计真实订单）
    orders_total = len(real_orders)
    orders_on_time = sum(1 for o in real_orders if o.get("on_time") and not o.get("expired_before_start"))
    orders_expired = sum(1 for o in real_orders if o.get("expired_before_start"))
    orders_tardiness_h = sum(float(o.get("lateness_h") or 0) for o in real_orders)

    containers_total = len(real_containers)
    containers_on_time = sum(1 for c in real_containers if c.get("on_time") and not c.get("expired_before_start"))
    containers_expired = sum(1 for c in real_containers if c.get("expired_before_start"))
    containers_tardiness_h = sum(float(c.get("lateness_h") or 0) for c in real_containers)

    # 更新 KPI
    kpi = result.get("kpi", {})
    kpi["orders_total"] = orders_total
    kpi["orders_on_time"] = orders_on_time
    kpi["orders_expired_before_start"] = orders_expired
    kpi["on_time_rate"] = (orders_on_time / orders_total) if orders_total > 0 else 1.0
    kpi["total_tardiness_h"] = orders_tardiness_h
    kpi["total_tardiness_days"] = orders_tardiness_h / 24.0

    kpi["containers_total"] = containers_total
    kpi["containers_on_time"] = containers_on_time
    kpi["containers_expired_before_start"] = containers_expired
    kpi["containers_on_time_rate"] = (containers_on_time / containers_total) if containers_total > 0 else 1.0
    kpi["total_container_tardiness_h"] = containers_tardiness_h
    kpi["total_container_tardiness_days"] = containers_tardiness_h / 24.0

    result["kpi"] = kpi

    # 注：机器时间线（label_tasks、forming_tasks）保留虚拟订单的任务，用于可视化
    return result


# =============================================================================
# 初始化排产引导流程
# =============================================================================


def check_schedule_status(
    *,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    orders_path: Path = DEFAULT_ORDERS_PATH,
) -> dict[str, Any]:
    """检查当前排产状态，判断是否需要初始化。

    Returns:
        状态字典，包含:
        - has_schedule: 是否有有效排产结果
        - needs_init: 是否需要初始化
        - schedule_info: 排产基本信息（如果有）
        - message: 状态说明
    """
    # 检查是否有订单数据
    if not orders_path.exists():
        return {
            "has_schedule": False,
            "needs_init": False,
            "schedule_info": None,
            "message": "未找到订单数据文件，请先导入订单。",
        }

    # 检查是否有排产结果
    if not schedule_path.exists():
        return {
            "has_schedule": False,
            "needs_init": True,
            "schedule_info": None,
            "message": "未找到排产结果，需要初始化排产。",
        }

    try:
        schedule = load_schedule(schedule_path)
        meta = schedule.get("meta", {})
        kpi = schedule.get("kpi", {})

        return {
            "has_schedule": True,
            "needs_init": False,
            "schedule_info": {
                "start_time": meta.get("start_time"),
                "chain_start_h": meta.get("chain_start_h"),
                "horizon_h": meta.get("horizon_h"),
                "orders_total": kpi.get("orders_total"),
                "on_time_rate": kpi.get("on_time_rate"),
            },
            "message": "已有排产结果。",
        }
    except Exception as e:
        return {
            "has_schedule": False,
            "needs_init": True,
            "schedule_info": None,
            "message": f"排产结果文件损坏，需要重新初始化：{e}",
        }


def get_init_params_template() -> dict[str, Any]:
    """获取初始化排产需要确认的参数模板。

    Returns:
        参数模板字典，供用户确认/修改
    """
    # 加载生产日历（DB-only）
    from .calendar_store import load_calendar

    calendar = load_calendar()

    # 默认开始时间（当前时间向上取整到小时）
    now = datetime.now()
    default_start = now.replace(minute=0, second=0, microsecond=0)
    if now.minute > 0:
        default_start += timedelta(hours=1)

    return {
        "start_time": {
            "description": "排产开始时间",
            "default": default_start.strftime("%Y-%m-%d %H:%M"),
            "format": "YYYY-MM-DD HH:MM",
        },
        "rotary_state": {
            "description": "转鼓机（ROTARY-2）当前状态",
            "options": [
                {"value": "producing_c", "label": "正在生产 S12G9C"},
                {"value": "producing_w", "label": "正在生产 S12G9W"},
                {"value": "producing_v", "label": "正在生产 S12G9V"},
                {"value": "setup", "label": "正在换色"},
                {"value": "idle", "label": "空闲"},
            ],
            "default": "producing_c",
        },
        "setup_remaining_h": {
            "description": "换色还需要多少小时完成",
            "default": 6,
            "note": "换色总时长 12 小时",
            "condition": "仅当 rotary_state == setup 时需要回答",
        },
        "downtime": {
            "description": "停机计划",
            "type": "multi_select",
            "options": [
                {
                    "id": "holiday_cny",
                    "label": "春节假期",
                    "detail": calendar.get("holidays", [{}])[0] if calendar.get("holidays") else None,
                    "selected": False,
                },
                {
                    "id": "maintenance_annual",
                    "label": "年度保养",
                    "detail": calendar.get("maintenance", [{}])[0] if calendar.get("maintenance") else None,
                    "selected": False,
                },
                {
                    "id": "custom",
                    "label": "添加自定义停机",
                    "detail": None,
                    "selected": False,
                },
            ],
            "note": "选择要应用的停机计划，可多选；不选则不应用任何停机",
        },
    }


def initialize_schedule(
    *,
    start_time: datetime | str,
    rotary_state: str = "producing_c",
    setup_remaining_h: int | None = None,
    apply_downtime: bool = False,
    orders_path: Path = DEFAULT_ORDERS_PATH,
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    chain_search_days: int = 60,
    max_hours: int = 5000,
) -> dict[str, Any]:
    """执行初始化排产。

    Args:
        start_time: 排产开始时间
        rotary_state: 转鼓机当前状态
            - "producing_c": 正在生产 S12G9C
            - "producing_w": 正在生产 S12G9W
            - "producing_v": 正在生产 S12G9V
            - "setup": 正在换色
            - "idle": 空闲
        setup_remaining_h: 换色剩余时间（仅当 rotary_state="setup" 时需要）
        apply_downtime: 是否应用停机计划
        其他参数同 generate_best_schedule

    Returns:
        排产结果字典
    """
    # 解析开始时间
    if isinstance(start_time, str):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                start_time = datetime.strptime(start_time.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return {
                "status": "error",
                "message": f"无法解析开始时间: {start_time}",
                "schedule": None,
            }

    # 构建订单
    orders = _build_orders(orders_path=orders_path, due_overrides=None)
    if not orders:
        return {
            "status": "error",
            "message": "未找到有效订单",
            "schedule": None,
        }

    inv = _build_inventory(inventory_path=inventory_path)

    # 根据转鼓机状态确定搜索范围
    max_chain_start_h = max(0, chain_search_days * 24)

    if rotary_state == "setup":
        # 正在换色：从换色完成后开始搜索
        remaining = setup_remaining_h if setup_remaining_h is not None else 6
        # 换色完成后，下一个换色点从 remaining + 12h 步长开始
        candidates = list(range(remaining, max_chain_start_h + 1, 12))
        if not candidates:
            candidates = [remaining]
    else:
        # 正在生产或空闲：搜索所有候选值，让算法找最优解
        candidates = list(range(0, max_chain_start_h + 1, 12))

    # 执行排产
    try:
        schedule = gen._pick_best_schedule(
            orders=orders,
            initial_inventory=inv,
            start_time=start_time,
            chain_start_candidates_h=candidates,
            max_hours=max_hours,
            apply_downtime=apply_downtime,
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"排产失败: {e}",
            "schedule": None,
        }

    # 保存结果
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成甘特图
    try:
        from process.visualize_schedule import _render_html
        gantt_html = _render_html(schedule, px_per_day=120)
        gantt_path = schedule_path.parent / "schedule_gantt.html"
        gantt_path.write_text(gantt_html, encoding="utf-8")
    except Exception:
        pass

    kpi = schedule.get("kpi", {})
    meta = schedule.get("meta", {})

    return {
        "status": "ok",
        "message": "初始化排产完成",
        "schedule": schedule,
        "summary": {
            "start_time": meta.get("start_time"),
            "chain_start_h": meta.get("chain_start_h"),
            "horizon_h": meta.get("horizon_h"),
            "orders_total": kpi.get("orders_total"),
            "orders_on_time": kpi.get("orders_on_time"),
            "on_time_rate": kpi.get("on_time_rate"),
            "containers_total": kpi.get("containers_total"),
            "containers_on_time": kpi.get("containers_on_time"),
            "total_downtime_hours": kpi.get("total_downtime_hours", 0),
        },
    }
