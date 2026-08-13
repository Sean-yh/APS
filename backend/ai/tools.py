"""LangChain Tools for scheduling operations.

This module provides tools that can be used by LangChain agents to:
1. Query order scheduling status
2. Reschedule with deadline constraints
3. Compare scheduling plans
4. Get KPI summary
"""
from __future__ import annotations

import re
import sys
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import tool

# 添加 backend 目录到 Python 路径以导入 process/* 调度模块
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from process.visualize_schedule import _render_html as render_gantt_html
from process.overrides import load_overrides as load_local_overrides, save_overrides as save_local_overrides

from . import diff
from .state_store import load_production_context_check as _load_pcc, save_production_context_check as _save_pcc
from .data import DEFAULT_INVENTORY_PATH, DEFAULT_ORDERS_PATH, DEFAULT_SCHEDULE_PATH, load_json, load_schedule, match_orders, order_brief
from .data import aggregate_containers, extract_customer_code, match_orders_by_customer
from .data import get_container_for_order, get_orders_in_container
from .erp_sync import sync_erp_data
from .scheduler import (
    apply_due_override_to_schedule,
    due_deadline,
    generate_best_schedule,
    generate_schedule_with_due_constraint,
    generate_schedule_with_priority_lock,
)


# Module-level state to store multiple rescheduling results for comparison
MAX_COMPARISONS = 5  # 最多保存5个方案

# 保留单个方案状态（用于 compare_schedules 工具的向后兼容）
_last_reschedule_state: dict[str, Any] = {
    "old_schedule": None,
    "new_schedule": None,
    "constraint": None,
    "new_schedule_gantt_html": None,  # 重排后的甘特图 HTML
    "timestamp": None,  # 更新时间戳
}

# 多方案列表存储
_comparison_schedules: list[dict[str, Any]] = []
_comparison_id_counter: int = 0


def _add_comparison_schedule(
    schedule: dict[str, Any],
    gantt_html: str,
    constraint: dict[str, Any] | None = None,
    label: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """添加一个新的对比方案到列表。

    Args:
        schedule: 排产结果
        gantt_html: 甘特图 HTML
        constraint: 约束条件
        label: 方案标签（可选，自动生成）

    Returns:
        (新方案的 ID, 方案元信息字典)
    """
    global _comparison_id_counter, _comparison_schedules

    _comparison_id_counter += 1
    schedule_id = f"comparison_{_comparison_id_counter}"

    # 自动生成标签
    if not label:
        label = f"方案 {len(_comparison_schedules) + 1}"
        if constraint:
            porefs = constraint.get("porefs", [])
            if porefs:
                label = f"加急 {', '.join(porefs[:2])}"
                if len(porefs) > 2:
                    label += f" 等 {len(porefs)} 个"

    timestamp = datetime.now().isoformat()

    comparison = {
        "id": schedule_id,
        "label": label,
        "schedule": schedule,
        "gantt_html": gantt_html,
        "constraint": constraint,
        "timestamp": timestamp,
    }

    _comparison_schedules.append(comparison)

    # 限制保存数量，删除最旧的
    while len(_comparison_schedules) > MAX_COMPARISONS:
        _comparison_schedules.pop(0)

    # 返回方案元信息（不包含 schedule 和 gantt_html 以减少数据量）
    meta = {
        "id": schedule_id,
        "label": label,
        "timestamp": timestamp,
        "constraint": constraint,
    }
    return schedule_id, meta


def get_comparison_schedules() -> list[dict[str, Any]]:
    """获取所有对比方案的摘要信息。"""
    return [
        {
            "id": s["id"],
            "label": s["label"],
            "timestamp": s["timestamp"],
            "constraint": s.get("constraint"),
        }
        for s in _comparison_schedules
    ]


def _make_schedule_card_marker(
    schedule_id: str,
    schedule_type: str,
    label: str,
    timestamp: str,
    constraint: dict[str, Any] | None = None,
) -> str:
    """生成 schedule_card 标记，用于触发前端显示预览卡片。

    Args:
        schedule_id: 方案 ID（'current' 或 comparison_xxx）
        schedule_type: 类型（'current' 或 'comparison'）
        label: 显示标签
        timestamp: 时间戳
        constraint: 约束条件

    Returns:
        特殊标记字符串，格式为 __SCHEDULE_CARD__:json
    """
    import json
    card_data = {
        "schedule_id": schedule_id,
        "schedule_type": schedule_type,
        "label": label,
        "timestamp": timestamp,
        "constraint": constraint,
    }
    return f"\n\n__SCHEDULE_CARD__:{json.dumps(card_data, ensure_ascii=False)}"


def get_comparison_schedule_by_id(schedule_id: str) -> dict[str, Any] | None:
    """根据 ID 获取对比方案。"""
    for s in _comparison_schedules:
        if s["id"] == schedule_id:
            return s
    return None


def delete_comparison_schedule(schedule_id: str) -> bool:
    """删除指定的对比方案。"""
    global _comparison_schedules
    for i, s in enumerate(_comparison_schedules):
        if s["id"] == schedule_id:
            _comparison_schedules.pop(i)
            return True
    return False


def clear_comparison_schedules() -> None:
    """清空所有对比方案。"""
    global _comparison_schedules
    _comparison_schedules = []


def _parse_due_datetime(s: str) -> tuple[datetime, bool]:
    """Parse due date/datetime string.

    Supports:
    - "YYYY-MM-DD" -> (date at 00:00, has_time=False)
    - "YYYY-MM-DD HH:MM" -> (datetime, has_time=True)

    Returns:
        (datetime, has_time): has_time=True if time was specified
    """
    s = str(s).strip()
    # Try datetime format first: YYYY-MM-DD HH:MM
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$", s)
    if m:
        y, mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        try:
            return datetime(y, mo, d, h, mi, 0), True
        except ValueError as e:
            raise ValueError(f"Invalid due datetime value: {s!r}") from e

    # Try date-only format: YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, 0, 0, 0), False
        except ValueError as e:
            raise ValueError(f"Invalid due date value: {s!r}") from e

    raise ValueError(f"Invalid due date format: {s!r}, expected YYYY-MM-DD or YYYY-MM-DD HH:MM")


def _load_raw_orders(path=DEFAULT_ORDERS_PATH) -> list[dict[str, Any]]:
    """Load raw order data from JSON file."""
    doc = load_json(path)
    if isinstance(doc, dict) and isinstance(doc.get("data"), list):
        return doc["data"]
    if isinstance(doc, list):
        return doc
    raise TypeError(f"{path}: expected list or dict with 'data'")


@tool
def query_orders(
    order_ref: str,
    status: Optional[Literal["all", "on_time", "late", "expired"]] = "all",
) -> str:
    """查询订单的排程状态。

    Args:
        order_ref: 订单标识，可以是:
            - c_orderline_id (纯数字，如 "1218288")
            - poreference (如 "DE#515894")
            - name 关键字
        status: 状态筛选
            - "all": 全部订单
            - "on_time": 准时完成的订单
            - "late": 延期的订单
            - "expired": 排产开始前已过期的订单

    Returns:
        订单信息，包含排程时间、是否准时、延迟时长等
    """
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
    matches = match_orders([r for r in rows if isinstance(r, dict)], order_ref)

    if not matches:
        return "没有找到匹配的订单。请提供 `c_orderline_id`（纯数字）或 `poreference`/`name` 的关键字。"

    # Apply status filter
    if status and status != "all":
        filtered = []
        for r in matches:
            if status == "on_time" and r.get("on_time"):
                filtered.append(r)
            elif status == "late" and not r.get("on_time") and not r.get("expired_before_start"):
                filtered.append(r)
            elif status == "expired" and r.get("expired_before_start"):
                filtered.append(r)
        matches = filtered

    if not matches:
        return f"没有找到状态为 '{status}' 的匹配订单。"

    if len(matches) > 1:
        top = "\n".join(f"- {order_brief(r)}" for r in matches[:10])
        return f"匹配到 {len(matches)} 个订单，请更具体一些（建议直接给 `c_orderline_id`）：\n" + top

    r = matches[0]
    order_id = int(r.get("c_orderline_id") or 0)

    # 获取该订单所属货柜的信息
    all_orders = [o for o in rows if isinstance(o, dict)]
    container = get_container_for_order(order_id, all_orders)

    fields = [
        ("c_orderline_id", r.get("c_orderline_id")),
        ("poreference", r.get("poreference")),
        ("name", r.get("name")),
        ("sku", r.get("sku")),
        ("quantity", f"{r.get('quantity'):,}" if r.get("quantity") else None),
        ("due", r.get("due")),
        ("deadline", r.get("deadline")),
        ("machine", r.get("machine")),
        ("start", r.get("start")),
        ("end (订单生产完成)", r.get("end")),
        ("on_time (订单)", "✅ 准时" if r.get("on_time") else "❌ 延期"),
        ("expired_before_start", "是" if r.get("expired_before_start") else "否"),
        ("lateness_h", f"{r.get('lateness_h'):.1f} 小时" if r.get("lateness_h") else "0"),
    ]
    out = ["## 订单信息"]
    for k, v in fields:
        out.append(f"- {k}: {v}")

    # 添加货柜信息
    if container:
        out.append("")
        out.append("## 货柜交付信息")
        out.append(f"- 货柜 (poreference): {container['container_id']}")
        out.append(f"- 货柜内订单数: {len(container['orders'])}")
        out.append(f"- 货柜可交付时间: {container['latest_end'] or 'N/A'}")
        out.append(f"- 货柜状态: {'✅ 准时' if container['on_time'] else '❌ 延期'}")
        if container['lateness_h'] > 0:
            out.append(f"- 货柜延迟: {container['lateness_h']:.1f} 小时")
        if len(container['orders']) > 1:
            out.append("")
            out.append(f"💡 提示：该订单属于货柜 {container['container_id']}，")
            out.append(f"   货柜包含 {len(container['orders'])} 个订单，全部完成后才能交付。")
            out.append(f"   货柜可交付时间取决于最后完成的订单。")

    return "\n".join(out)


@tool
def reschedule(
    order_refs: Optional[str] = None,
    new_deadline: Optional[str] = None,
    mode: Literal["constraint", "priority", "full"] = "constraint",
    rotary_state: Optional[Literal["producing_c", "producing_w", "producing_v", "setup", "idle"]] = None,
    setup_remaining_h: Optional[int] = None,
) -> str:
    """重新排产指定订单或货柜，或进行全局重排。

    Args:
        order_refs: 订单或货柜标识，支持逗号分隔多个（如 "DE#515894,SEC515910"）
                   mode="full" 时可选，其他模式必填
        new_deadline: 新截止日期/时间，格式:
            - "YYYY-MM-DD": 默认当日 24:00 前可交付
            - "YYYY-MM-DD HH:MM": 精确到指定时间
        mode: 重排模式
            - "constraint": 交期约束模式（默认）- 确保在截止日期前完成，最小调整
            - "priority": 优先锁定模式 - 设为最高优先级，其他订单可能被挤后
            - "full": 全局重排模式 - 考虑停机计划，全局优化（原 run_schedule）
        rotary_state: 成型机当前状态（可选，用于优化搜索范围）
            - producing_c: 正在生产 S12G9C
            - producing_w: 正在生产 S12G9W
            - producing_v: 正在生产 S12G9V
            - setup: 正在换色
            - idle: 空闲
        setup_remaining_h: 换色剩余小时数（仅 rotary_state=setup 时需要，0-12）

    Returns:
        排产结果和差异分析报告
    """
    global _last_reschedule_state

    # === ERP 快照同步（可选） ===
    #
    # 默认不在每次重排时都拉 ERP（ERP 拉取可能较慢），依赖本地快照文件：
    # - process/orders_erp.json
    # - process/inventory_erp.json
    #
    # 如需自动同步，设置环境变量 APS_AUTO_SYNC_ERP=true。
    auto_sync = str(os.getenv("APS_AUTO_SYNC_ERP") or "").strip().lower() in ("1", "true", "yes", "y")
    if auto_sync or (not DEFAULT_ORDERS_PATH.exists()) or (not DEFAULT_INVENTORY_PATH.exists()):
        try:
            sync_erp_data(is_test=None)
        except ValueError as e:
            return f"❌ ERP配置错误: {str(e)}\n\n请检查 .env 文件中的 GX_ERP_API_URL 和 GX_ERP_TOKEN"
        except Exception as e:
            return f"❌ ERP数据同步失败: {str(e)}\n\n请检查网络连接或ERP服务状态"
    # === 同步结束 ===

    # mode == "full" 时进行全局重排，不需要 order_refs
    if mode == "full":
        # Full reschedule is still a reschedule: require a fresh "production context check" first.
        _checked_state, _checked_setup_remaining_h, err = _require_production_context_check(
            rotary_state=None,
            setup_remaining_h=None,
        )
        if err:
            return err
        return _reschedule_full_mode(
            rotary_state=rotary_state,
            setup_remaining_h=setup_remaining_h,
        )

    # For constraint/priority modes, require the production-context confirmation (legacy: ROTARY-2).
    checked_state, checked_setup_remaining_h, err = _require_production_context_check(
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )
    if err:
        return err

    rotary_state = checked_state
    setup_remaining_h = checked_setup_remaining_h

    # 其他模式需要 order_refs
    if not order_refs:
        return "请提供至少一个订单或货柜标识（mode=constraint 或 mode=priority 时必填）。"

    # 解析多个 order_refs（逗号分隔）
    refs = [r.strip() for r in order_refs.split(",") if r.strip()]
    if not refs:
        return "请提供至少一个订单或货柜标识。"

    raw_orders = _load_raw_orders(DEFAULT_ORDERS_PATH)

    # 收集所有货柜的订单
    all_order_ids: list[int] = []
    all_porefs: list[str] = []
    not_found: list[str] = []

    for ref in refs:
        matches = match_orders(raw_orders, ref)
        if not matches:
            not_found.append(ref)
            continue

        # 获取匹配订单的 poreference（货柜号）
        poreference = str(matches[0].get("poreference") or "")
        if not poreference:
            # 无 poreference 的单独订单
            oid = int(matches[0]["c_orderline_id"])
            if oid not in all_order_ids:
                all_order_ids.append(oid)
            continue

        # 避免重复添加同一货柜
        if poreference in all_porefs:
            continue

        all_porefs.append(poreference)
        container_orders = get_orders_in_container(poreference, raw_orders)
        for o in container_orders:
            oid = int(o["c_orderline_id"])
            if oid not in all_order_ids:
                all_order_ids.append(oid)

    if not all_order_ids:
        return f"未找到任何匹配的订单或货柜：{', '.join(not_found)}"

    # 警告未找到的部分
    warning = ""
    if not_found:
        warning = f"⚠️ 以下标识未找到，已跳过：{', '.join(not_found)}\n\n"

    # 解析可选的新交期
    new_due: datetime | None = None
    has_time = False
    if new_deadline:
        try:
            new_due, has_time = _parse_due_datetime(new_deadline)
        except ValueError:
            return "due date 格式不正确，请使用 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`。"

    try:
        old = load_schedule(DEFAULT_SCHEDULE_PATH)
    except FileNotFoundError:
        old = None  # 首次排产，无旧数据可对比

    # 根据 mode 选择处理逻辑
    if mode == "priority":
        return _reschedule_priority_mode(
            all_order_ids=all_order_ids,
            all_porefs=all_porefs,
            new_due=new_due,
            has_time=has_time,
            new_deadline=new_deadline,
            old_schedule=old,
            warning=warning,
            rotary_state=rotary_state,
            setup_remaining_h=setup_remaining_h,
        )
    else:
        # mode == "constraint"
        # constraint 模式要求必须指定 new_deadline
        if not new_deadline or new_due is None:
            return "交期约束模式（mode=constraint）需要指定 new_deadline 参数。"
        return _reschedule_constraint_mode(
            all_order_ids=all_order_ids,
            all_porefs=all_porefs,
            new_due=new_due,
            has_time=has_time,
            new_deadline=new_deadline,
            old_schedule=old,
            warning=warning,
            rotary_state=rotary_state,
            setup_remaining_h=setup_remaining_h,
        )


def _reschedule_constraint_mode(
    all_order_ids: list[int],
    all_porefs: list[str],
    new_due: datetime,
    has_time: bool,
    new_deadline: str,
    old_schedule: dict[str, Any],
    warning: str,
    rotary_state: Optional[str] = None,
    setup_remaining_h: Optional[int] = None,
) -> str:
    """交期约束模式的重排实现。"""
    global _last_reschedule_state

    old = old_schedule
    # 使用第一个订单 ID 作为 focus_order_id
    focus_order_id = all_order_ids[0] if all_order_ids else None
    poreference = all_porefs[0] if all_porefs else ""

    # Multi-line (ALL) path: express "due constraint" as an override patch and re-run the multi-line scheduler.
    try:
        from process.multiline import generate_all_lines  # type: ignore

        base_overrides = load_local_overrides()
        patch = {"containers": {}, "orders": {}}

        # If only date is provided, interpret as "due by 24:00 of that day" => next day 00:00
        eff_due = new_due
        if not has_time:
            eff_due = datetime(new_due.year, new_due.month, new_due.day) + timedelta(days=1)
        eff_due_str = eff_due.isoformat()
        eff_deadline_str = due_deadline(eff_due).isoformat()

        if all_porefs:
            for po in all_porefs:
                cid = str(po or "").strip().upper()
                if not cid:
                    continue
                patch["containers"][cid] = {
                    "priority": 100,
                    "due_override": eff_due_str,
                    "deadline_override": eff_deadline_str,
                }
        else:
            for oid in all_order_ids:
                patch["orders"][str(int(oid))] = {
                    "priority": 100,
                    "due_override": eff_due_str,
                    "deadline_override": eff_deadline_str,
                }

        merged = {
            "containers": dict((base_overrides.get("containers") or {}) if isinstance(base_overrides.get("containers"), dict) else {}),
            "orders": dict((base_overrides.get("orders") or {}) if isinstance(base_overrides.get("orders"), dict) else {}),
        }
        for cid, cfg in (patch.get("containers") or {}).items():
            prev = merged["containers"].get(cid) if isinstance(merged["containers"].get(cid), dict) else {}
            merged["containers"][cid] = {**dict(prev), **dict(cfg)}
        for oid, cfg in (patch.get("orders") or {}).items():
            prev = merged["orders"].get(oid) if isinstance(merged["orders"].get(oid), dict) else {}
            merged["orders"][oid] = {**dict(prev), **dict(cfg)}

        forming_states = _production_context_check.get("forming_states")
        setup_remaining_by_machine = _production_context_check.get("setup_remaining_by_machine")
        if not isinstance(forming_states, dict):
            forming_states = None
        if not isinstance(setup_remaining_by_machine, dict):
            setup_remaining_by_machine = None

        _line_schedules, new = generate_all_lines(
            max_hours=8000,
            apply_downtime=True,
            forming_states_by_machine=forming_states,
            setup_remaining_by_machine=setup_remaining_by_machine,
            overrides=merged,
        )

        constraint_info = {
            "order_ids": all_order_ids,
            "porefs": all_porefs,
            "new_deadline": new_deadline,
            "overrides_patch": patch,
        }
        gantt_html = render_gantt_html(new, px_per_day=120)

        _last_reschedule_state = {
            "old_schedule": old,
            "new_schedule": new,
            "constraint": constraint_info,
            "new_schedule_gantt_html": gantt_html,
            "timestamp": datetime.now().isoformat(),
        }

        schedule_id, meta = _add_comparison_schedule(schedule=new, gantt_html=gantt_html, constraint=constraint_info)

        # Check whether the target containers are satisfied in the new plan.
        cid_status = []
        try:
            rows = new.get("containers") if isinstance(new.get("containers"), list) else []
            for po in all_porefs:
                for r in rows:
                    if isinstance(r, dict) and str(r.get("container_id") or "").strip().upper() == str(po).strip().upper():
                        cid_status.append(
                            f"- {po}: {'✅ 准时' if r.get('on_time') else '❌ 延期'} (end={r.get('end')}, deadline={r.get('deadline')}, lateness_h={r.get('lateness_h')})"
                        )
                        break
        except Exception:
            pass

        deadline_desc = due_deadline(eff_due).strftime("%Y-%m-%d %H:%M") if has_time else f"{eff_due.date().isoformat()} 24:00"
        porefs_str = ", ".join(all_porefs) if all_porefs else f"{len(all_order_ids)} 个订单"
        head = f"✅ 已生成交期约束方案（ALL 3 线）: {porefs_str}（deadline={deadline_desc}）"
        if cid_status:
            head += "\n\n货柜状态：\n" + "\n".join(cid_status)

        old_for_diff = old if isinstance(old, dict) else {"meta": {}, "kpi": {}, "orders": [], "machines": {}}
        final_result = warning + head + "\n\n" + diff.summarize_schedule_diff(
            old=old_for_diff,
            new=new,
            focus_order_id=focus_order_id,
        )
        final_result += _make_schedule_card_marker(
            schedule_id=schedule_id,
            schedule_type="comparison",
            label=meta["label"],
            timestamp=meta["timestamp"],
            constraint=constraint_info,
        )
        return final_result
    except Exception:
        # Fall back to legacy L2-only algorithm if multi-line scheduler is not available.
        pass

    # Check if current schedule already satisfies the new due date
    if isinstance(old.get("meta"), dict) and isinstance(old["meta"].get("start_time"), str):
        start_time = datetime.fromisoformat(old["meta"]["start_time"])
        # 如果指定了精确时间，使用 due_deadline 向上取整；否则默认当日 24:00（即下一天 00:00）
        if has_time:
            deadline = due_deadline(new_due)
        else:
            deadline = datetime(new_due.year, new_due.month, new_due.day) + timedelta(days=1)

        if deadline <= start_time:
            return warning + f"该 due date（{new_due.date().isoformat()}）早于排产 start_time（{start_time.isoformat()}），无法满足。"

        # 检查所有订单是否都满足新交期
        old_orders = old.get("orders") if isinstance(old.get("orders"), list) else []
        all_satisfy = True
        latest_end_dt = None

        for oid in all_order_ids:
            old_row = None
            for r in old_orders:
                if isinstance(r, dict) and int(r.get("c_orderline_id") or -1) == oid:
                    old_row = r
                    break
            if old_row:
                try:
                    end_dt = datetime.fromisoformat(str(old_row.get("end")))
                    if latest_end_dt is None or end_dt > latest_end_dt:
                        latest_end_dt = end_dt
                    if end_dt > deadline:
                        all_satisfy = False
                except Exception:
                    all_satisfy = False
            else:
                all_satisfy = False

        if all_satisfy and latest_end_dt:
            # 当前排产已满足，只需更新 due/deadline
            new_sched = deepcopy(old)
            for oid in all_order_ids:
                new_sched = apply_due_override_to_schedule(schedule=new_sched, order_id=oid, new_due=new_due)

            constraint_info = {
                "order_ids": all_order_ids,
                "porefs": all_porefs,
                "new_deadline": new_deadline,
            }
            gantt_html = render_gantt_html(new_sched, px_per_day=120)

            _last_reschedule_state = {
                "old_schedule": old,
                "new_schedule": new_sched,
                "constraint": constraint_info,
                "new_schedule_gantt_html": gantt_html,
                "timestamp": datetime.now().isoformat(),
            }

            # 添加到多方案列表
            schedule_id, meta = _add_comparison_schedule(
                schedule=new_sched,
                gantt_html=gantt_html,
                constraint=constraint_info,
            )

            deadline_desc = deadline.strftime("%Y-%m-%d %H:%M") if has_time else f"{new_due.date().isoformat()} 24:00"
            porefs_str = ", ".join(all_porefs) if all_porefs else f"{len(all_order_ids)} 个订单"
            head = (
                f"当前排产已满足 {porefs_str} 的新交期（deadline={deadline_desc} 前交付）。\n"
                f"共 {len(all_order_ids)} 个订单，全部在 deadline 前完成。\n"
                f"无需调整生产顺序；已更新所有订单的 due/deadline 并重新计算 KPI。"
            )
            result = warning + head + "\n\n" + diff.summarize_schedule_diff(old=old, new=new_sched, focus_order_id=focus_order_id)
            # 添加 schedule_card 标记
            result += _make_schedule_card_marker(
                schedule_id=schedule_id,
                schedule_type="comparison",
                label=meta["label"],
                timestamp=meta["timestamp"],
                constraint=constraint_info,
            )
            return result

    # Need to reschedule - 约束所有订单
    result = generate_schedule_with_due_constraint(
        target_order_ids=all_order_ids,
        target_due=new_due,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )

    new = result.get("schedule")

    if not isinstance(new, dict):
        return warning + "重排失败：在给定搜索窗口内无法生成可行排产（库存约束导致不可行）。"

    # Store for later comparison
    constraint_info = {
        "order_ids": all_order_ids,
        "porefs": all_porefs,
        "new_deadline": new_deadline,
    }
    gantt_html = render_gantt_html(new, px_per_day=120)

    _last_reschedule_state = {
        "old_schedule": old,
        "new_schedule": new,
        "constraint": constraint_info,
        "new_schedule_gantt_html": gantt_html,
        "timestamp": datetime.now().isoformat(),
    }

    # 添加到多方案列表
    schedule_id, meta = _add_comparison_schedule(
        schedule=new,
        gantt_html=gantt_html,
        constraint=constraint_info,
    )

    status = str(result.get("status") or "")
    target_row = result.get("target") if isinstance(result.get("target"), dict) else None

    # 计算 deadline 描述
    if has_time:
        dl = due_deadline(new_due)
        deadline_desc = dl.strftime("%Y-%m-%d %H:%M")
    else:
        deadline_desc = f"{new_due.date().isoformat()} 24:00"

    porefs_str = ", ".join(all_porefs) if all_porefs else f"{len(all_order_ids)} 个订单"

    if status == "ok":
        head = (
            f"✅ 已按要求将 {porefs_str} 的交期调整为 {deadline_desc} 前可交付。\n"
            f"共 {len(all_order_ids)} 个订单，排产已确保所有订单在 deadline 前完成。"
        )
    else:
        head = (
            f"⚠️ 无法在 {deadline_desc} 前完成 {porefs_str}（在当前产能/库存约束下不可行）。\n"
            f"共 {len(all_order_ids)} 个订单。下面给出'最优努力'的重排结果与差异。"
        )
        if target_row:
            head += f"\n最优努力：end={target_row.get('end')}，lateness_h={target_row.get('lateness_h')}。"

    final_result = warning + head + "\n\n" + diff.summarize_schedule_diff(old=old, new=new, focus_order_id=focus_order_id)
    # 添加 schedule_card 标记
    final_result += _make_schedule_card_marker(
        schedule_id=schedule_id,
        schedule_type="comparison",
        label=meta["label"],
        timestamp=meta["timestamp"],
        constraint=constraint_info,
    )
    return final_result


def _reschedule_priority_mode(
    all_order_ids: list[int],
    all_porefs: list[str],
    new_due: datetime | None,
    has_time: bool,
    new_deadline: str | None,
    old_schedule: dict[str, Any],
    warning: str,
    rotary_state: Optional[str] = None,
    setup_remaining_h: Optional[int] = None,
) -> str:
    """优先锁定模式的重排实现。"""
    global _last_reschedule_state

    old = old_schedule

    # Multi-line (ALL) path: express "priority lock" as an override patch and re-run the multi-line scheduler.
    # This keeps the UI consistent (always 9 machines) and avoids applying an L2-only schedule as current.
    try:
        from process.multiline import generate_all_lines  # type: ignore

        # Load current overrides (persisted) and build a candidate patch for this scenario.
        base_overrides = load_local_overrides()
        patch = {"containers": {}, "orders": {}}

        # Optional new due override: apply to all locked orders (container-level if possible).
        eff_due_str: str | None = None
        if new_due:
            eff_due = new_due
            if not has_time:
                eff_due = datetime(new_due.year, new_due.month, new_due.day) + timedelta(days=1)
            eff_due_str = eff_due.isoformat()

        if all_porefs:
            for po in all_porefs:
                cid = str(po or "").strip().upper()
                if not cid:
                    continue
                cfg: dict[str, Any] = {"priority": 100}
                if eff_due_str:
                    cfg["due_override"] = eff_due_str
                patch["containers"][cid] = cfg
        else:
            for oid in all_order_ids:
                patch["orders"][str(int(oid))] = {"priority": 100, **({"due_override": eff_due_str} if eff_due_str else {})}

        # Merge patch onto base for this scenario (do NOT persist until user applies the comparison plan).
        merged = {
            "containers": dict((base_overrides.get("containers") or {}) if isinstance(base_overrides.get("containers"), dict) else {}),
            "orders": dict((base_overrides.get("orders") or {}) if isinstance(base_overrides.get("orders"), dict) else {}),
        }
        for cid, cfg in (patch.get("containers") or {}).items():
            prev = merged["containers"].get(cid) if isinstance(merged["containers"].get(cid), dict) else {}
            merged["containers"][cid] = {**dict(prev), **dict(cfg)}
        for oid, cfg in (patch.get("orders") or {}).items():
            prev = merged["orders"].get(oid) if isinstance(merged["orders"].get(oid), dict) else {}
            merged["orders"][oid] = {**dict(prev), **dict(cfg)}

        # Use the most recent production-context check (if any) to seed multi-line scheduling.
        forming_states = _production_context_check.get("forming_states")
        setup_remaining_by_machine = _production_context_check.get("setup_remaining_by_machine")
        if not isinstance(forming_states, dict):
            forming_states = None
        if not isinstance(setup_remaining_by_machine, dict):
            setup_remaining_by_machine = None

        _line_schedules, new = generate_all_lines(
            max_hours=8000,
            apply_downtime=True,
            forming_states_by_machine=forming_states,
            setup_remaining_by_machine=setup_remaining_by_machine,
            overrides=merged,
        )

        # Store for later comparison
        constraint_info = {
            "order_ids": all_order_ids,
            "porefs": all_porefs,
            "new_deadline": new_deadline,
            "priority_lock": True,
            "overrides_patch": patch,
        }
        gantt_html = render_gantt_html(new, px_per_day=120)
        _last_reschedule_state = {
            "old_schedule": old,
            "new_schedule": new,
            "constraint": constraint_info,
            "new_schedule_gantt_html": gantt_html,
            "timestamp": datetime.now().isoformat(),
        }

        schedule_id, meta = _add_comparison_schedule(schedule=new, gantt_html=gantt_html, constraint=constraint_info)

        # Show on-time status for the requested containers (if any)
        cid_status = []
        try:
            rows = new.get("containers") if isinstance(new.get("containers"), list) else []
            for po in all_porefs:
                for r in rows:
                    if isinstance(r, dict) and str(r.get("container_id") or "").strip().upper() == str(po).strip().upper():
                        cid_status.append(
                            f"- {po}: {'✅ 准时' if r.get('on_time') else '❌ 延期'} (end={r.get('end')}, deadline={r.get('deadline')}, lateness_h={r.get('lateness_h')})"
                        )
                        break
        except Exception:
            pass

        porefs_str = ", ".join(all_porefs) if all_porefs else f"{len(all_order_ids)} 个订单"
        head = f"✅ 已生成加急方案（ALL 3 线）: {porefs_str}"
        if cid_status:
            head += "\n\n货柜状态：\n" + "\n".join(cid_status)

        old_for_diff = old if isinstance(old, dict) else {"meta": {}, "kpi": {}, "orders": [], "machines": {}}
        final_result = warning + head + "\n\n" + diff.summarize_schedule_diff(
            old=old_for_diff,
            new=new,
            focus_order_id=(all_order_ids[0] if all_order_ids else None),
        )
        final_result += _make_schedule_card_marker(
            schedule_id=schedule_id,
            schedule_type="comparison",
            label=meta["label"],
            timestamp=meta["timestamp"],
            constraint=constraint_info,
        )
        return final_result
    except Exception:
        # Fall back to legacy L2-only algorithm if multi-line scheduler is not available.
        pass

    # 解析可选的新交期 - 应用到所有锁定订单
    due_overrides: dict[int, datetime] | None = None
    if new_due:
        # 如果只指定日期，设置 due 为当日 24:00（即下一天 00:00）
        effective_due = new_due
        if not has_time:
            effective_due = datetime(new_due.year, new_due.month, new_due.day) + timedelta(days=1)
        due_overrides = {oid: effective_due for oid in all_order_ids}

    # Run priority lock scheduling - 锁定所有指定订单
    result = generate_schedule_with_priority_lock(
        priority_order_ids=all_order_ids,
        due_overrides=due_overrides,
        rotary_state=rotary_state,
        setup_remaining_h=setup_remaining_h,
    )

    new = result.get("schedule")
    if not isinstance(new, dict):
        return warning + "重排失败：在给定搜索窗口内无法生成可行排产（库存约束导致不可行）。"

    # Store for later comparison
    constraint_info = {
        "order_ids": all_order_ids,
        "porefs": all_porefs,
        "new_deadline": new_deadline,
        "priority_lock": True,
    }
    gantt_html = render_gantt_html(new, px_per_day=120)

    _last_reschedule_state = {
        "old_schedule": old,
        "new_schedule": new,
        "constraint": constraint_info,
        "new_schedule_gantt_html": gantt_html,
        "timestamp": datetime.now().isoformat(),
    }

    # 添加到多方案列表
    schedule_id, meta = _add_comparison_schedule(
        schedule=new,
        gantt_html=gantt_html,
        constraint=constraint_info,
    )

    status = str(result.get("status") or "")
    priority_orders = result.get("priority_orders", [])

    # Build impact analysis report
    report = diff.analyze_priority_lock_impact(
        old_schedule=old,
        new_schedule=new,
        priority_order_ids=all_order_ids,
    )

    # 构建输出头部
    porefs_str = ", ".join(all_porefs) if all_porefs else f"{len(all_order_ids)} 个订单"

    if status == "ok":
        head = f"✅ 已优先锁定 {len(all_porefs)} 个货柜的产能：{porefs_str}"
        head += f"\n共 {len(all_order_ids)} 个订单设为最高优先级。"
        if new_deadline:
            head += f"\n新交期: {new_deadline}"
    else:
        head = f"⚠️ 已将 {porefs_str} 设为优先锁定，但在当前产能/库存约束下仍有延迟风险。"
        head += f"\n共 {len(all_order_ids)} 个订单。"
        if priority_orders:
            # 显示第一个优先订单的状态作为参考
            target_row = priority_orders[0]
            head += f"\n参考：end={target_row.get('end')}，lateness_h={target_row.get('lateness_h')}"

    final_result = warning + head + "\n\n" + report
    # 添加 schedule_card 标记
    final_result += _make_schedule_card_marker(
        schedule_id=schedule_id,
        schedule_type="comparison",
        label=meta["label"],
        timestamp=meta["timestamp"],
        constraint=constraint_info,
    )
    return final_result


def _reschedule_full_mode(
    rotary_state: Optional[str] = None,
    setup_remaining_h: Optional[int] = None,
) -> str:
    """全局重排模式 - 原 run_schedule 逻辑。

    考虑停机计划，全局优化排产方案。

    Args:
        rotary_state: 成型机当前状态
        setup_remaining_h: 换色剩余小时数

    Returns:
        排产结果摘要，包括 KPI 指标
    """
    global _last_reschedule_state

    # 加载当前排产（用于对比）
    try:
        old = load_schedule(DEFAULT_SCHEDULE_PATH)
    except FileNotFoundError:
        old = None  # 首次排产，无旧数据可对比

    # 执行全局重排（多产线：L1/L2/L3 合并为 ALL）
    #
    # NOTE: rotary_state/setup_remaining_h 当前仅对旧版 L2 算法生效；多产线版本暂不使用。
    try:
        from process.multiline import generate_all_lines  # type: ignore

        # Use the most recent production-context check (if any) to seed multi-line scheduling.
        forming_states = _production_context_check.get("forming_states")
        setup_remaining_by_machine = _production_context_check.get("setup_remaining_by_machine")
        if not isinstance(forming_states, dict):
            forming_states = None
        if not isinstance(setup_remaining_by_machine, dict):
            setup_remaining_by_machine = None

        _line_schedules, new_schedule = generate_all_lines(
            max_hours=8000,
            apply_downtime=True,
            forming_states_by_machine=forming_states,
            setup_remaining_by_machine=setup_remaining_by_machine,
        )
    except Exception as e:
        return f"全局重排失败：{type(e).__name__}: {str(e)}"

    if not isinstance(new_schedule, dict):
        return "全局重排失败：无法生成可行排产方案。"

    # 生成甘特图 HTML
    gantt_html = render_gantt_html(new_schedule, px_per_day=120)

    # 更新状态
    _last_reschedule_state = {
        "old_schedule": old,
        "new_schedule": new_schedule,
        "constraint": {"type": "full_reschedule"},
        "new_schedule_gantt_html": gantt_html,
        "timestamp": datetime.now().isoformat(),
    }

    # 添加到多方案列表
    schedule_id, meta = _add_comparison_schedule(
        schedule=new_schedule,
        gantt_html=gantt_html,
        constraint={"type": "full_reschedule"},
        label="全局重排 (ALL)",
    )

    # 解析 KPI
    kpi = new_schedule.get("kpi", {})
    on_time_rate = kpi.get("containers_on_time_rate", 0) * 100
    total_containers = int(kpi.get("containers_total") or 0)
    total_orders = int(kpi.get("orders_total") or 0)

    result_text = f"""## 已生成全局重排方案

**KPI 指标：**
- 货柜准时率：{on_time_rate:.1f}%
- 货柜总数：{total_containers}
- 订单总数：{total_orders}
- 货柜延迟：{kpi.get('total_container_tardiness_h', 0):.1f} 小时
- 订单延迟：{kpi.get('total_tardiness_h', 0):.1f} 小时

新方案已添加到右侧"重排方案"列表。请预览后点击 ✓ 确认应用，或点击 ✕ 删除。"""

    # 添加 schedule_card 标记
    result_text += _make_schedule_card_marker(
        schedule_id=schedule_id,
        schedule_type="comparison",
        label=meta["label"],
        timestamp=meta["timestamp"],
        constraint={"type": "full_reschedule"},
    )
    return result_text


@tool
def compare_schedules(include_unchanged: bool = False) -> str:
    """对比当前方案（原方案）和最近一次重排生成的新方案。

    必须先调用 reschedule 工具生成新方案后才能使用此工具。

    Args:
        include_unchanged: 是否包含未变化的订单，默认 False

    Returns:
        详细的对比报告，包括 KPI 对比和受影响订单列表
    """
    global _last_reschedule_state

    old = _last_reschedule_state.get("old_schedule")
    new = _last_reschedule_state.get("new_schedule")
    constraint = _last_reschedule_state.get("constraint")

    if old is None or new is None:
        return "没有可对比的方案，请先使用 reschedule 工具生成新方案。"

    constraint = constraint if isinstance(constraint, dict) else {}

    focus_order_id: int | None = None
    raw_focus = constraint.get("order_id")
    if isinstance(raw_focus, (int, str)) and str(raw_focus).strip().isdigit():
        focus_order_id = int(raw_focus)
    else:
        order_ids = constraint.get("order_ids")
        if isinstance(order_ids, list) and order_ids:
            try:
                focus_order_id = int(order_ids[0])
            except Exception:
                focus_order_id = None

    result = diff.summarize_schedule_diff(
        old=old,
        new=new,
        focus_order_id=focus_order_id,
        max_affected=None,
        include_unchanged=include_unchanged,
    )

    header = "## 新旧方案对比\n\n"
    if constraint:
        new_deadline = constraint.get("new_deadline")
        porefs = constraint.get("porefs") if isinstance(constraint.get("porefs"), list) else []
        order_ids = constraint.get("order_ids") if isinstance(constraint.get("order_ids"), list) else []

        if constraint.get("priority_lock"):
            if porefs:
                header += f"优先锁定：{', '.join(porefs)}\n\n"
            else:
                header += f"优先锁定：{len(order_ids)} 个订单\n\n"
        elif new_deadline:
            if porefs:
                header += f"约束：货柜 {', '.join(porefs)} 必须在 {new_deadline} 前可交付\n\n"
            elif order_ids:
                header += f"约束：订单 {', '.join(str(x) for x in order_ids[:5])} 必须在 {new_deadline} 前完成\n\n"

    return header + result


@tool
def get_schedule_kpi() -> str:
    """获取当前排产方案的 KPI 概览。

    Returns:
        当前排产的关键指标，包括订单总数、准时率、总延迟时长、换色次数、停机时长等
    """
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    kpi = sched.get("kpi") if isinstance(sched.get("kpi"), dict) else {}
    meta = sched.get("meta") if isinstance(sched.get("meta"), dict) else {}

    lines = [
        "## 当前排产 KPI 概览",
        "",
        "### 排产信息",
        f"- 产线: {meta.get('line', 'L2')}",
        f"- 排产开始时间: {meta.get('start_time')}",
        f"- 排产周期: {meta.get('horizon_h')} 小时 ({meta.get('horizon_h', 0) / 24:.1f} 天)",
        f"- 成型链启动点: {meta.get('chain_start_h')} 小时",
        "",
        "### KPI 指标",
        f"- Container 总数: {kpi.get('containers_total')}",
        f"- 准时 Container: {kpi.get('containers_on_time')}",
        f"- 开始前已过期(Container): {kpi.get('containers_expired_before_start')}",
        f"- Container 准时率: {kpi.get('containers_on_time_rate', 0) * 100:.1f}%",
        f"- Container 总延迟: {kpi.get('total_container_tardiness_h', 0):.1f} 小时 ({kpi.get('total_container_tardiness_days', 0):.1f} 天)",
        "",
        f"- 订单总数: {kpi.get('orders_total')}",
        f"- 准时订单: {kpi.get('orders_on_time')}",
        f"- 开始前已过期: {kpi.get('orders_expired_before_start')}",
        f"- 准时率: {kpi.get('on_time_rate', 0) * 100:.1f}%",
        f"- 总延迟: {kpi.get('total_tardiness_h', 0):.1f} 小时 ({kpi.get('total_tardiness_days', 0):.1f} 天)",
        f"- 换色次数: {kpi.get('setup_count')}",
        f"- 平均生产周期: {kpi.get('avg_campaign_h', 0):.1f} 小时",
        "",
        "### 停机统计",
        f"- 总停机时长: {kpi.get('total_downtime_hours', 0):.1f} 小时 ({kpi.get('total_downtime_hours', 0) / 24:.1f} 天)",
        f"- 假期停机: {kpi.get('holiday_hours', 0):.1f} 小时",
        f"- 维护停机: {kpi.get('maintenance_hours', 0):.1f} 小时",
        f"- 有效利用率: {kpi.get('effective_utilization', 1.0) * 100:.1f}%",
        "",
        "### 产能参数",
    ]

    rates = meta.get("rates_per_h") if isinstance(meta.get("rates_per_h"), dict) else {}
    for k, v in rates.items():
        lines.append(f"- {k}: {v:,}/小时")

    return "\n".join(lines)


# =============================================================================
# 客户订单查询工具
# =============================================================================


@tool
def query_orders_by_customer(
    customer_code: Optional[str] = None,
    status: Optional[Literal["all", "on_time", "late", "expired"]] = "all",
) -> str:
    """查询订单的延迟情况。

    Args:
        customer_code: 客户代码（如 "SQ#", "DE#", "SEC" 等）。不传则查询所有客户。
        status: 状态筛选
            - "all": 全部订单（默认）
            - "on_time": 准时完成的订单
            - "late": 延期的订单
            - "expired": 排产开始前已过期的订单

    Returns:
        订单汇总（总数、准时数、延迟数、延迟详情）
    """
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
    rows = [r for r in rows if isinstance(r, dict)]

    # 按客户筛选（如果指定了客户代码）
    if customer_code:
        matches = match_orders_by_customer(rows, customer_code)
        if not matches:
            return f"没有找到客户代码为 '{customer_code}' 的订单。请检查客户代码是否正确（如 SQ#, DE#, SEC 等）。"
        title = f"## 客户 {customer_code.upper()} 订单汇总"
    else:
        matches = rows
        title = "## 所有订单汇总"

    # 统计
    total = len(matches)
    on_time_count = sum(1 for r in matches if r.get("on_time"))
    expired_count = sum(1 for r in matches if r.get("expired_before_start"))
    late_count = total - on_time_count - expired_count

    # 按状态筛选显示
    if status and status != "all":
        if status == "on_time":
            matches = [r for r in matches if r.get("on_time")]
        elif status == "late":
            matches = [r for r in matches if not r.get("on_time") and not r.get("expired_before_start")]
        elif status == "expired":
            matches = [r for r in matches if r.get("expired_before_start")]

    # 构建输出
    lines = [
        title,
        "",
        f"- 订单总数: {total}",
        f"- 准时订单: {on_time_count}",
        f"- 延期订单: {late_count}",
        f"- 开始前已过期: {expired_count}",
        f"- 准时率: {on_time_count / total * 100:.1f}%" if total > 0 else "- 准时率: N/A",
        "",
    ]

    if matches:
        lines.append(f"### 筛选结果（状态: {status}，共 {len(matches)} 个）")
        lines.append("")

        # 按延迟时间排序（延迟最大的在前）
        matches_sorted = sorted(matches, key=lambda r: -(float(r.get("lateness_h") or 0)))

        for r in matches_sorted:
            oid = r.get("c_orderline_id")
            po = r.get("poreference") or ""
            name = r.get("name") or ""
            qty = r.get("quantity") or 0
            due = r.get("due") or ""
            end = r.get("end") or ""
            lateness = float(r.get("lateness_h") or 0)

            status_icon = "✅" if r.get("on_time") else ("⏰" if r.get("expired_before_start") else "❌")
            lateness_str = f"延迟 {lateness:.1f}h" if lateness > 0 else "准时"

            lines.append(f"- {status_icon} {oid} | {po} | qty={qty:,} | due={due} | 生产完成={end} | {lateness_str}")

    return "\n".join(lines)


# =============================================================================
# Container 查询工具
# =============================================================================


@tool
def query_container(container_ref: str) -> str:
    """查询单个 Container（货柜）的状态。

    Container ID = poreference（PO参考号）
    货柜可交付时间 = 该 Container 中最后一个订单的生产完成时间（LABEL机台下线）

    Args:
        container_ref: Container 标识（即 poreference，如 "SEC515910", "DE#515894"）

    Returns:
        该 Container 的详细信息，包括包含的订单、总数量、可交付时间等
    """
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
    rows = [r for r in rows if isinstance(r, dict)]

    containers = aggregate_containers(rows)

    # 查找匹配的 Container
    ref = container_ref.strip().upper()
    target = None
    for c in containers:
        if c["container_id"].upper() == ref:
            target = c
            break

    if not target:
        # 模糊匹配
        partial_matches = [c for c in containers if ref in c["container_id"].upper()]
        if partial_matches:
            if len(partial_matches) == 1:
                target = partial_matches[0]
            else:
                top = "\n".join(f"- {c['container_id']} ({c['customer_code']}, {len(c['orders'])} 个订单)" for c in partial_matches[:10])
                return f"找到 {len(partial_matches)} 个匹配的 Container：\n{top}\n\n请提供完整的 Container ID。"

    if not target:
        return f"没有找到 Container '{container_ref}'。Container ID 即 poreference（如 SEC515910, DE#515894）。"

    # 输出详情
    lines = [
        f"## Container: {target['container_id']}",
        "",
        f"- 客户: {target['customer_code']}",
        f"- 订单数: {len(target['orders'])}",
        f"- 总数量: {target['total_quantity']:,}",
        f"- 最早交期: {target['earliest_due'] or 'N/A'}",
        f"- 货柜可交付时间: {target['latest_end'] or 'N/A'}",
        f"- 状态: {'✅ 准时' if target['on_time'] else '❌ 延期'}",
    ]

    if target['lateness_h'] > 0:
        lines.append(f"- 延迟: {target['lateness_h']:.1f} 小时")

    lines.append("")
    lines.append("### 包含的订单")
    lines.append("")

    for o in target['orders']:
        oid = o.get("c_orderline_id")
        name = o.get("name") or ""
        qty = o.get("quantity") or 0
        end = o.get("end") or ""
        on_time = o.get("on_time", False)
        status_icon = "✅" if on_time else "❌"
        lines.append(f"- {status_icon} {oid} | {name} | qty={qty:,} | 生产完成={end}")

    return "\n".join(lines)


@tool
def query_containers_by_customer(
    customer_code: Optional[str] = None,
    status: Optional[Literal["all", "on_time", "late"]] = "all",
) -> str:
    """查询 Container（货柜）的状态。

    Args:
        customer_code: 客户代码（如 "SQ#", "DE#", "SEC" 等）。不传则查询所有客户。
        status: 状态筛选
            - "all": 全部 Container（默认）
            - "on_time": 准时完成的 Container
            - "late": 延期的 Container

    Returns:
        Container 汇总（总数、准时数、延迟数、各 Container 详情）
    """
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
    rows = [r for r in rows if isinstance(r, dict)]

    containers = aggregate_containers(rows)

    # 按客户筛选（如果指定了客户代码）
    if customer_code:
        code = customer_code.strip().upper()
        matches = [c for c in containers if c["customer_code"].upper() == code]
        if not matches:
            return f"没有找到客户代码为 '{customer_code}' 的 Container。请检查客户代码是否正确（如 SQ#, DE#, SEC 等）。"
        title = f"## 客户 {code} Container 汇总"
    else:
        matches = containers
        title = "## 所有 Container 汇总"

    # 统计
    total = len(matches)
    on_time_count = sum(1 for c in matches if c["on_time"])
    late_count = total - on_time_count

    # 按状态筛选
    if status == "on_time":
        matches = [c for c in matches if c["on_time"]]
    elif status == "late":
        matches = [c for c in matches if not c["on_time"]]

    # 构建输出
    lines = [
        title,
        "",
        f"- Container 总数: {total}",
        f"- 准时 Container: {on_time_count}",
        f"- 延期 Container: {late_count}",
        f"- 准时率: {on_time_count / total * 100:.1f}%" if total > 0 else "- 准时率: N/A",
        "",
    ]

    if matches:
        lines.append(f"### Container 列表（状态: {status}，共 {len(matches)} 个）")
        lines.append("")

        # 按延迟时间排序
        matches_sorted = sorted(matches, key=lambda c: -c["lateness_h"])

        for c in matches_sorted:
            status_icon = "✅" if c["on_time"] else "❌"
            lateness_str = f"延迟 {c['lateness_h']:.1f}h" if c["lateness_h"] > 0 else "准时"
            lines.append(
                f"- {status_icon} {c['container_id']} | {len(c['orders'])} 订单 | "
                f"qty={c['total_quantity']:,} | 可交付={c['latest_end'] or 'N/A'} | {lateness_str}"
            )

    return "\n".join(lines)


# =============================================================================
# 停机计划表单工具
# =============================================================================

import json as _json
from pathlib import Path as _Path

from .calendar_store import (
    VALID_MACHINE_IDS as _VALID_MACHINE_IDS,
    add_holiday as _calendar_add_holiday,
    add_maintenance as _calendar_add_maintenance,
    delete_holiday as _calendar_delete_holiday,
    delete_maintenance as _calendar_delete_maintenance,
    load_calendar as _load_calendar,
)


@tool
def request_downtime_form(form_type: Literal["maintenance", "holiday"]) -> str:
    """请求用户填写停机计划表单。

    当用户想要添加设备维护或假期停机计划时调用此工具。
    工具会触发前端显示交互式表单卡片，用户填写后会自动提交。

    Args:
        form_type: 表单类型
            - "maintenance": 设备维护表单（需要填写机器、原因、开始/结束时间）
            - "holiday": 假期表单（需要填写假期名称、开始/结束日期）

    Returns:
        特殊标记，用于触发前端显示表单卡片
    """
    # 返回特殊标记，让 SSE 流处理器识别并发送 form_card 事件
    return f"__FORM_CARD__:{form_type}"


@tool
def add_holiday(name: str, start: str, end: str) -> str:
    """直接添加假期停机计划。

    适用于：
    - 用户明确告知假期名称和日期范围
    - 批量添加多个假期（如每周日放假，需多次调用此工具）
    - 用户不想通过表单填写

    Args:
        name: 假期名称，如 "春节"、"周日休息"、"国庆节"
        start: 开始日期，格式 YYYY-MM-DD
        end: 结束日期，格式 YYYY-MM-DD（包含该日）

    Returns:
        添加结果
    """
    try:
        entry, _, existed = _calendar_add_holiday(name=name, start=start, end=end)
    except ValueError as e:
        return f"添加假期失败：{e}"

    if existed:
        return f"假期已存在：{entry['name']}（{entry['start']} ~ {entry['end']}），无需重复添加"
    return f"已添加假期：{entry['name']}（{entry['start']} ~ {entry['end']}）"


@tool
def add_maintenance(machine_id: str, reason: str, start: str, end: str) -> str:
    """直接添加设备维护停机计划。

    适用于：
    - 用户明确告知机器、原因和时间
    - 批量添加多个维护计划
    - 用户不想通过表单填写

    Args:
        machine_id: 机器ID，可选值: ROTARY-2, LABEL-3, LABEL-5
        reason: 维护原因，如 "年度保养"、"换模"、"维修"
        start: 开始时间，格式 YYYY-MM-DDTHH:MM 或 YYYY-MM-DD HH:MM
        end: 结束时间，格式 YYYY-MM-DDTHH:MM 或 YYYY-MM-DD HH:MM

    Returns:
        添加结果
    """
    try:
        entry, _, existed = _calendar_add_maintenance(
            machine_id=machine_id,
            reason=reason,
            start=start,
            end=end,
        )
    except ValueError as e:
        return f"添加维护计划失败：{e}"

    if existed:
        return (
            f"维护计划已存在：{entry['machine_id']} - {entry['reason']}（{entry['start']} ~ {entry['end']}），无需重复添加"
        )
    return f"已添加维护计划：{entry['machine_id']} - {entry['reason']}（{entry['start']} ~ {entry['end']}）"


@tool
def delete_holiday(index: int) -> str:
    """删除假期（按 get_downtime_plans 输出的索引）。"""
    try:
        deleted = _calendar_delete_holiday(index=int(index))
        return f"已删除假期：[{index}] {deleted.get('name', '')}（{deleted.get('start', '')} ~ {deleted.get('end', '')}）"
    except Exception as e:
        return f"删除假期失败：{type(e).__name__}: {e}"


@tool
def delete_maintenance(index: int) -> str:
    """删除维护计划（按 get_downtime_plans 输出的索引）。"""
    try:
        deleted = _calendar_delete_maintenance(index=int(index))
        return (
            f"已删除维护：[{index}] {deleted.get('machine_id', '')} - {deleted.get('reason', '')}"
            f"（{deleted.get('start', '')} ~ {deleted.get('end', '')}）"
        )
    except Exception as e:
        return f"删除维护计划失败：{type(e).__name__}: {e}"


@tool
def get_downtime_plans() -> str:
    """查询当前的停机计划（假期和设备维护）。

    Returns:
        当前所有停机计划的列表
    """
    calendar = _load_calendar()
    holidays = calendar.get("holidays", [])
    maintenance = calendar.get("maintenance", [])

    lines = ["## 当前停机计划", ""]

    if holidays:
        lines.append("### 假期")
        for i, h in enumerate(holidays):
            if isinstance(h, dict):
                lines.append(f"- [{i}] {h.get('name', '')}: {h.get('start', '')} ~ {h.get('end', '')}")
            else:
                lines.append(f"- [{i}] {h}")
    else:
        lines.append("### 假期\n暂无假期计划")

    lines.append("")

    if maintenance:
        lines.append("### 设备维护")
        for i, m in enumerate(maintenance):
            if isinstance(m, dict):
                lines.append(
                    f"- [{i}] {m.get('machine_id', '')} - {m.get('reason', '')}: {m.get('start', '')} ~ {m.get('end', '')}"
                )
            else:
                lines.append(f"- [{i}] {m}")
    else:
        lines.append("### 设备维护\n暂无维护计划")

    return "\n".join(lines)


# =============================================================================
# 重排前置检查工具
# =============================================================================


# Disable TTL by default: keep the last confirmed context until the user updates it.
# (The UI can still show "last checked at" and ask for reconfirmation when needed.)
_PRODUCTION_CONTEXT_CHECK_TTL_S: int | None = None
_production_context_check: dict[str, Any] = {
    "confirmed": False,
    "rotary_state": None,
    "setup_remaining_h": None,
    # Multi-line state (machine_id -> state / remaining hours), used by ALL-lines scheduling.
    "forming_states": None,
    "setup_remaining_by_machine": None,
    "timestamp": None,
}

# Restore last confirmed context across server reloads (still TTL-gated for safety).
try:
    loaded = _load_pcc()
    if isinstance(loaded, dict):
        _production_context_check.update({k: loaded.get(k) for k in _production_context_check.keys()})
except Exception:
    pass


def _is_production_context_confirmed(
    rotary_state: Optional[str],
    setup_remaining_h: Optional[int],
) -> bool:
    if not rotary_state:
        return False
    if rotary_state == "setup":
        if setup_remaining_h is None:
            return False
        try:
            v = int(setup_remaining_h)
        except Exception:
            return False
        if v < 0 or v > 12:
            return False
    return True


def _require_production_context_check(
    *,
    rotary_state: Optional[str],
    setup_remaining_h: Optional[int],
) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """Return (checked_rotary_state, checked_setup_remaining_h, error_text)."""
    now = datetime.now()
    checked = _production_context_check
    ts = checked.get("timestamp")

    # Accept a confirmed check (optionally TTL-gated).
    if checked.get("confirmed") and isinstance(ts, datetime):
        if _PRODUCTION_CONTEXT_CHECK_TTL_S is None or (now - ts).total_seconds() <= _PRODUCTION_CONTEXT_CHECK_TTL_S:
            checked_state = checked.get("rotary_state")
            checked_setup = checked.get("setup_remaining_h")

            # If caller passed a state, it must match the last confirmed check.
            if rotary_state and rotary_state != checked_state:
                return None, None, (
                    "⚠️ 成型机状态与最近一次重排前检查不一致。\n"
                    f"- 最近确认: rotary_state={checked_state}\n"
                    f"- 本次请求: rotary_state={rotary_state}\n\n"
                    "请先重新调用 `query_production_context` 确认最新状态后再重排。"
                )
            if rotary_state == "setup" and setup_remaining_h is not None and checked_setup is not None:
                try:
                    passed_setup = max(0, min(12, int(setup_remaining_h)))
                except Exception:
                    passed_setup = None
                if passed_setup is not None and int(passed_setup) != int(checked_setup):
                    return None, None, (
                        "⚠️ 换色剩余时间与最近一次重排前检查不一致。\n"
                        f"- 最近确认: setup_remaining_h={checked_setup}\n"
                        f"- 本次请求: setup_remaining_h={setup_remaining_h}\n\n"
                        "请先重新调用 `query_production_context` 确认最新状态后再重排。"
                    )

            return checked_state, checked_setup, None

    # If caller supplied a complete context, ask them to confirm via the check tool first.
    if _is_production_context_confirmed(rotary_state, setup_remaining_h):
        return None, None, (
            "⚠️ 在执行任何重排前，必须先完成“重排前检查”（成型机状态 + 近期停机计划）。\n"
            "请先调用：\n"
            f'- `query_production_context(rotary_state="{rotary_state}"{", setup_remaining_h=" + str(setup_remaining_h) if setup_remaining_h is not None else ""})`\n'
            "确认无误后，再执行重排。"
        )

    return None, None, (
        "⚠️ 在执行任何重排前，必须先完成“重排前检查”（成型机状态 + 近期停机计划）。\n"
        "请先调用：`query_production_context()`，确认成型机状态与停机计划后再重排。"
    )


@tool
def query_production_context(
    rotary_state: Optional[Literal["producing_c", "producing_w", "producing_v", "setup", "idle"]] = None,
    setup_remaining_h: Optional[int] = None,
    forming_states: Optional[dict[str, str]] = None,
    setup_remaining_by_machine: Optional[dict[str, int]] = None,
) -> str:
    """查询当前生产上下文，用于重排前的状态确认。

    Multi-line note:
    - When `backend/process/line_config.json` is available, this tool will ask for / display
      forming-machine states for ALL lines (ROTARY-1/2/3).
    - Labeling machine in-progress state is not modeled yet; use downtime calendar
      (holiday/maintenance) to represent unavailable periods.

    Args:
        rotary_state/setup_remaining_h: legacy L2-only shorthand (maps to ROTARY-2).
        forming_states: machine_id -> state ("idle" | "setup" | "producing:<SKU>" | "<SKU>")
        setup_remaining_by_machine: machine_id -> remaining hours (for state="setup")

    Returns:
        生产上下文汇总，包括成型机状态、停机计划和建议
    """
    global _production_context_check

    lines = ["## 当前生产上下文", ""]

    # Load multi-line config (best-effort).
    try:
        from .line_config import load_line_config  # type: ignore

        cfg = load_line_config()
        lines_cfg = cfg.get("lines") if isinstance(cfg.get("lines"), dict) else {}
    except Exception:
        lines_cfg = {}

    # If the caller didn't provide any new context, show the last stored check (if any)
    # instead of wiping it out.
    used_prev = False
    if (
        rotary_state is None
        and setup_remaining_h is None
        and forming_states is None
        and setup_remaining_by_machine is None
    ):
        prev_states = _production_context_check.get("forming_states")
        prev_setup = _production_context_check.get("setup_remaining_by_machine")
        if isinstance(prev_states, dict) or isinstance(prev_setup, dict):
            used_prev = True
            if isinstance(prev_states, dict):
                forming_states = prev_states  # type: ignore[assignment]
            if isinstance(prev_setup, dict):
                setup_remaining_by_machine = prev_setup  # type: ignore[assignment]
            # Legacy fields (ROTARY-2 only), kept for backwards compatibility.
            if rotary_state is None and isinstance(_production_context_check.get("rotary_state"), str):
                rotary_state = _production_context_check.get("rotary_state")  # type: ignore[assignment]
            if setup_remaining_h is None and _production_context_check.get("setup_remaining_h") is not None:
                try:
                    setup_remaining_h = int(_production_context_check.get("setup_remaining_h"))
                except Exception:
                    setup_remaining_h = None

    if used_prev and isinstance(_production_context_check.get("timestamp"), datetime):
        ts = _production_context_check.get("timestamp")
        try:
            ts_s = ts.isoformat(timespec="seconds")  # type: ignore[union-attr]
        except Exception:
            ts_s = str(ts)
        lines.append(f"(已使用上次确认的状态：{ts_s})")
        lines.append("")

    # Normalize machine states.
    normalized_states: dict[str, str] = {}
    if isinstance(forming_states, dict):
        for k, v in forming_states.items():
            mk = str(k or "").strip().upper()
            sv = str(v or "").strip()
            if mk and sv:
                normalized_states[mk] = sv

    normalized_setup: dict[str, int] = {}
    if isinstance(setup_remaining_by_machine, dict):
        for k, v in setup_remaining_by_machine.items():
            mk = str(k or "").strip().upper()
            try:
                hv = int(v)
            except Exception:
                continue
            if mk:
                normalized_setup[mk] = max(0, hv)

    # Legacy ROTARY-2 mapping.
    if rotary_state:
        if rotary_state == "producing_c":
            normalized_states.setdefault("ROTARY-2", "producing:S12G9C")
        elif rotary_state == "producing_w":
            normalized_states.setdefault("ROTARY-2", "producing:S12G9W")
        elif rotary_state == "producing_v":
            normalized_states.setdefault("ROTARY-2", "producing:S12G9V")
        elif rotary_state in ("setup", "idle"):
            normalized_states.setdefault("ROTARY-2", rotary_state)
        if rotary_state == "setup" and setup_remaining_h is not None:
            try:
                normalized_setup.setdefault("ROTARY-2", max(0, min(12, int(setup_remaining_h))))
            except Exception:
                pass

    def parse_state(raw: str | None) -> tuple[str, str | None]:
        s = str(raw or "").strip()
        if not s:
            return "unknown", None
        sl = s.lower()
        if sl in ("idle", "空闲"):
            return "idle", None
        if sl in ("setup", "换色", "换模"):
            return "setup", None
        if sl.startswith("producing:"):
            sku = s.split(":", 1)[1].strip().upper()
            return "producing", sku or None
        if s.upper().startswith("S"):
            return "producing", s.strip().upper()
        if sl.startswith("producing"):
            return "producing", None
        return "unknown", None

    # 1. Forming machine states.
    if lines_cfg:
        lines.append("### 成型机状态（ROTARY-1/2/3）")
        required: list[str] = []
        for line_id, c in sorted(lines_cfg.items(), key=lambda kv: kv[0]):
            if not isinstance(c, dict):
                continue
            fm = str(c.get("forming_machine") or "").strip().upper()
            if not fm:
                continue
            required.append(fm)
            prefixes = c.get("sku_prefixes") if isinstance(c.get("sku_prefixes"), list) else []
            prefixes_s = "/".join(str(p) for p in prefixes if p)

            mode, sku = parse_state(normalized_states.get(fm))
            if mode == "idle":
                lines.append(f"- {fm} ({line_id}, {prefixes_s}*): ✅ 空闲")
            elif mode == "setup":
                rem = normalized_setup.get(fm)
                rem_s = f"{rem}h" if rem is not None else "?h"
                lines.append(f"- {fm} ({line_id}, {prefixes_s}*): ⚠️ 正在换型（剩余 {rem_s}）")
            elif mode == "producing":
                lines.append(f"- {fm} ({line_id}, {prefixes_s}*): ✅ 正在生产 {sku or '(请补充SKU)'}")
            else:
                lines.append(f"- {fm} ({line_id}, {prefixes_s}*): ⚠️ 未确认（idle / setup / producing:SKU）")
        lines.append("")
    else:
        lines.append("### 成型机状态（ROTARY-2）")
        mode, sku = parse_state(normalized_states.get("ROTARY-2"))
        if mode == "idle":
            lines.append("- ROTARY-2: ✅ 空闲")
        elif mode == "setup":
            rem = normalized_setup.get("ROTARY-2")
            lines.append(f"- ROTARY-2: ⚠️ 正在换色（剩余 {rem if rem is not None else '?'}h）")
        elif mode == "producing":
            lines.append(f"- ROTARY-2: ✅ 正在生产 {sku or '(请补充SKU)'}")
        else:
            lines.append("- ROTARY-2: ⚠️ 未确认（idle / setup / producing:S12G9C/W/V）")
        lines.append("")

    # 2. 当前排产信息
    lines.append("### 当前排产参数")
    try:
        sched = load_schedule(DEFAULT_SCHEDULE_PATH)
        meta = sched.get("meta") if isinstance(sched.get("meta"), dict) else {}
        kpi = sched.get("kpi") if isinstance(sched.get("kpi"), dict) else {}

        start_time = meta.get("start_time", "未知")
        chain_start_h = meta.get("chain_start_h", "N/A")
        horizon_h = meta.get("horizon_h", 0)
        orders_total = kpi.get("orders_total", 0)
        on_time_rate = kpi.get("containers_on_time_rate", 0) * 100

        lines.append(f"- 排产开始时间: {start_time}")
        if chain_start_h != "N/A":
            lines.append(f"- 成型链启动点: {chain_start_h} 小时")
        lines.append(f"- 排产周期: {horizon_h} 小时（{horizon_h / 24:.1f} 天）")
        lines.append(f"- 订单总数: {orders_total}")
        lines.append(f"- 货柜准时率: {on_time_rate:.1f}%")
    except Exception:
        lines.append("- 无法读取当前排产信息")
    lines.append("")

    # 3. 近期停机计划（7天内）
    lines.append("### 近期停机计划（7天内）")
    calendar = _load_calendar()
    holidays = calendar.get("holidays", [])
    maintenance = calendar.get("maintenance", [])

    # 计算 7 天范围
    from datetime import datetime as dt, timedelta as td
    now = dt.now()
    week_later = now + td(days=7)

    upcoming_items = []

    # 检查假期
    for h in holidays:
        if not isinstance(h, dict):
            continue
        try:
            start_str = h.get("start", "")
            start_dt = dt.fromisoformat(start_str) if start_str else None
            if start_dt and now.date() <= start_dt.date() <= week_later.date():
                upcoming_items.append(f"- 🏖️ {h.get('name', '假期')}: {h.get('start')} ~ {h.get('end')}")
        except Exception:
            continue

    # 检查维护
    for m in maintenance:
        if not isinstance(m, dict):
            continue
        try:
            start_str = m.get("start", "").replace(" ", "T")
            start_dt = dt.fromisoformat(start_str) if start_str else None
            if start_dt and now <= start_dt <= week_later:
                upcoming_items.append(f"- 🔧 {m.get('machine_id', '')} {m.get('reason', '')}: {m.get('start')} ~ {m.get('end')}")
        except Exception:
            continue

    if upcoming_items:
        lines.extend(upcoming_items)
    else:
        lines.append("- 暂无近期停机计划")
    lines.append("")

    # 4. 建议
    lines.append("### 重排建议")
    confirmed = False
    missing: list[str] = []
    if lines_cfg:
        required = []
        for _, c in sorted(lines_cfg.items(), key=lambda kv: kv[0]):
            if isinstance(c, dict) and c.get("forming_machine"):
                required.append(str(c["forming_machine"]).strip().upper())
        for fm in required:
            mode, _sku = parse_state(normalized_states.get(fm))
            if mode == "unknown":
                missing.append(fm)
            elif mode == "setup" and fm not in normalized_setup:
                missing.append(f"{fm}(setup_remaining_h)")
        confirmed = not missing
        if not confirmed:
            lines.append("⚠️ 开排前建议先确认 3 台成型机状态：")
            lines.append(f"- 缺少: {', '.join(missing)}")
            lines.append("")
            lines.append("示例：")
            lines.append("- forming_states={\"ROTARY-1\":\"setup\",\"ROTARY-2\":\"producing:S12G9C\",\"ROTARY-3\":\"idle\"}")
            lines.append("- setup_remaining_by_machine={\"ROTARY-1\":10}")
        else:
            lines.append("✅ 3 台成型机状态已确认，可以执行全局重排（ALL）。")
    else:
        mode, _sku = parse_state(normalized_states.get("ROTARY-2"))
        needs_setup_remaining = mode == "setup" and "ROTARY-2" not in normalized_setup
        if mode == "unknown" or needs_setup_remaining:
            lines.append("⚠️ 请先确认成型机状态后再执行重排。")
        else:
            confirmed = True
            lines.append("✅ 成型机状态已确认，可以执行重排。")

    # Update check state (used to gate all reschedules)
    _production_context_check = {
        "confirmed": bool(confirmed) if lines_cfg else _is_production_context_confirmed(rotary_state, normalized_setup.get("ROTARY-2")),
        "rotary_state": rotary_state,
        "setup_remaining_h": normalized_setup.get("ROTARY-2"),
        "forming_states": normalized_states,
        "setup_remaining_by_machine": normalized_setup,
        "timestamp": datetime.now(),
    }
    # Persist across reloads so constraints don’t "disappear" in local dev.
    try:
        _save_pcc(_production_context_check)
    except Exception:
        pass

    return "\n".join(lines)


# =============================================================================
# 产线配置（AI 可编辑）
# =============================================================================


@tool
def get_line_config() -> str:
    """读取当前多产线配置（backend/process/line_config.json）。"""
    try:
        from .line_config import load_line_config  # type: ignore

        cfg = load_line_config()
        import json as _json  # local import to avoid polluting module namespace

        return _json.dumps(cfg, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"读取 line_config 失败：{type(e).__name__}: {e}"


@tool
def update_line_config(
    line_id: str,
    updates: dict[str, Any] | None = None,
    delete: bool = False,
) -> str:
    """更新（或删除）某条产线配置，写入 backend/process/line_config.json。

    - 默认是“合并更新”（不会覆盖未提及字段）
    - `updates.setup_rules` 会做 dict 合并

    Args:
        line_id: 产线 ID（如 "L1"/"L2"/"L3"）
        updates: 需要更新的字段（forming_machine/labeling_machines/...）
        delete: 是否删除该产线

    Returns:
        更新结果摘要
    """
    line_id = str(line_id or "").strip()
    if not line_id:
        return "line_id 不能为空。"

    try:
        from .line_config import load_line_config, save_line_config  # type: ignore
    except Exception as e:
        return f"更新 line_config 失败：无法导入配置模块：{type(e).__name__}: {e}"

    try:
        cfg = load_line_config()
        lines_cfg = cfg.get("lines") if isinstance(cfg.get("lines"), dict) else {}
        if not isinstance(lines_cfg, dict):
            lines_cfg = {}
            cfg["lines"] = lines_cfg

        if delete:
            if line_id not in lines_cfg:
                return f"产线 {line_id} 不存在，无需删除。"
            del lines_cfg[line_id]
            save_line_config(cfg)
            return f"✅ 已删除产线配置：{line_id}"

        if updates is None:
            updates = {}
        if not isinstance(updates, dict):
            return "updates 必须是一个 JSON object。"

        cur = lines_cfg.get(line_id)
        if not isinstance(cur, dict):
            cur = {}

        # Merge update fields.
        next_line = dict(cur)
        for k, v in updates.items():
            if k == "setup_rules" and isinstance(v, dict):
                sr = next_line.get("setup_rules")
                sr2 = dict(sr) if isinstance(sr, dict) else {}
                sr2.update(v)
                next_line["setup_rules"] = sr2
            else:
                next_line[k] = v

        lines_cfg[line_id] = next_line
        save_line_config(cfg)

        return f"✅ 已更新产线配置：{line_id}"
    except Exception as e:
        return f"更新 line_config 失败：{type(e).__name__}: {e}"


# =============================================================================
# 本地排产覆盖（Overrides）
# =============================================================================


@tool
def get_overrides() -> str:
    """查看当前本地 overrides（backend/process/overrides.json）。"""
    try:
        doc = load_local_overrides()
        import json as _json

        return _json.dumps(doc, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"读取 overrides 失败：{type(e).__name__}: {e}"


@tool
def set_container_override(
    container_ref: str,
    priority: int = 100,
    due_override: Optional[str] = None,
    deadline_override: Optional[str] = None,
) -> str:
    """对一个货柜（poreference）设置本地覆盖（优先级/交期/截止时间）。

    这不会修改 ERP 快照，只会写入 `backend/process/overrides.json`，下一次 `regenerate`/`full` 重排会生效。
    """
    cid = str(container_ref or "").strip().upper()
    if not cid:
        return "container_ref 不能为空。"

    try:
        doc = load_local_overrides()
        containers = doc.get("containers") if isinstance(doc.get("containers"), dict) else {}
        cur = containers.get(cid) if isinstance(containers.get(cid), dict) else {}
        next_cfg = dict(cur)
        next_cfg["priority"] = int(priority)
        if due_override:
            next_cfg["due_override"] = str(due_override).strip()
        if deadline_override:
            next_cfg["deadline_override"] = str(deadline_override).strip()
        containers[cid] = next_cfg
        doc["containers"] = containers
        save_local_overrides(doc)
        return f"✅ 已设置货柜覆盖：{cid}（priority={int(priority)}）"
    except Exception as e:
        return f"设置货柜覆盖失败：{type(e).__name__}: {e}"


@tool
def clear_container_override(container_ref: str) -> str:
    """删除一个货柜（poreference）的本地覆盖。"""
    cid = str(container_ref or "").strip().upper()
    if not cid:
        return "container_ref 不能为空。"
    try:
        doc = load_local_overrides()
        containers = doc.get("containers") if isinstance(doc.get("containers"), dict) else {}
        if cid not in containers:
            return f"货柜 {cid} 没有覆盖配置。"
        del containers[cid]
        doc["containers"] = containers
        save_local_overrides(doc)
        return f"✅ 已删除货柜覆盖：{cid}"
    except Exception as e:
        return f"删除货柜覆盖失败：{type(e).__name__}: {e}"


@tool
def set_order_override(
    order_id: int,
    priority: int = 100,
    due_override: Optional[str] = None,
    deadline_override: Optional[str] = None,
) -> str:
    """对单个订单（c_orderline_id）设置本地覆盖（优先级/交期/截止时间）。"""
    try:
        oid = str(int(order_id))
    except Exception:
        return "order_id 必须是数字。"

    try:
        doc = load_local_overrides()
        orders = doc.get("orders") if isinstance(doc.get("orders"), dict) else {}
        cur = orders.get(oid) if isinstance(orders.get(oid), dict) else {}
        next_cfg = dict(cur)
        next_cfg["priority"] = int(priority)
        if due_override:
            next_cfg["due_override"] = str(due_override).strip()
        if deadline_override:
            next_cfg["deadline_override"] = str(deadline_override).strip()
        orders[oid] = next_cfg
        doc["orders"] = orders
        save_local_overrides(doc)
        return f"✅ 已设置订单覆盖：{oid}（priority={int(priority)}）"
    except Exception as e:
        return f"设置订单覆盖失败：{type(e).__name__}: {e}"


@tool
def clear_order_override(order_id: int) -> str:
    """删除单个订单（c_orderline_id）的本地覆盖。"""
    try:
        oid = str(int(order_id))
    except Exception:
        return "order_id 必须是数字。"
    try:
        doc = load_local_overrides()
        orders = doc.get("orders") if isinstance(doc.get("orders"), dict) else {}
        if oid not in orders:
            return f"订单 {oid} 没有覆盖配置。"
        del orders[oid]
        doc["orders"] = orders
        save_local_overrides(doc)
        return f"✅ 已删除订单覆盖：{oid}"
    except Exception as e:
        return f"删除订单覆盖失败：{type(e).__name__}: {e}"


# =============================================================================
# 换色周期效率优化工具
# =============================================================================

# 模块状态：保存换色周期优化分析结果，供 apply_campaign_optimization 使用
_campaign_optimization_state: dict[str, Any] = {
    "analysis": None,
    "options": None,
    "timestamp": None,
}


def _analyze_forming_campaigns(schedule: dict[str, Any]) -> dict[str, Any]:
    """分析成型机的换色周期效率。

    Args:
        schedule: 排产结果字典

    Returns:
        分析结果字典，包含:
        - campaigns: 各换色周期列表
        - idle_total_h: 总空闲时间
        - setup_total_h: 总换色时间
        - avg_campaign_h: 平均生产时长
        - inefficient_campaigns: 效率偏低的周期
    """
    machines = schedule.get("machines", {})
    rotary_blocks = machines.get("ROTARY-2", [])

    campaigns: list[dict[str, Any]] = []
    idle_total_h = 0
    setup_total_h = 0

    for block in rotary_blocks:
        block_type = block.get("type")
        duration_h = float(block.get("duration_h", 0))

        if block_type == "forming":
            campaigns.append({
                "sku": block.get("sku"),
                "duration_h": duration_h,
                "quantity": int(block.get("quantity", 0)),
                "start": block.get("start"),
                "end": block.get("end"),
            })
        elif block_type == "idle":
            idle_total_h += duration_h
        elif block_type == "setup":
            setup_total_h += duration_h

    # 计算平均生产时长
    total_forming_h = sum(c["duration_h"] for c in campaigns)
    avg_campaign_h = total_forming_h / len(campaigns) if campaigns else 0

    # 判断效率偏低的周期：时长 < 平均值 * 20%
    threshold_h = avg_campaign_h * 0.2
    inefficient_campaigns = []
    for c in campaigns:
        if c["duration_h"] < threshold_h:
            efficiency_pct = (c["duration_h"] / avg_campaign_h * 100) if avg_campaign_h > 0 else 0
            inefficient_campaigns.append({
                **c,
                "efficiency_pct": efficiency_pct,
                "threshold_h": threshold_h,
            })

    return {
        "campaigns": campaigns,
        "idle_total_h": idle_total_h,
        "setup_total_h": setup_total_h,
        "avg_campaign_h": avg_campaign_h,
        "inefficient_campaigns": inefficient_campaigns,
        "total_forming_h": total_forming_h,
    }


def _calculate_extra_capacity(
    analysis: dict[str, Any],
    idle_usage_ratio: float = 0.6,
) -> dict[str, int]:
    """计算可额外生产的产能。

    Args:
        analysis: _analyze_forming_campaigns 的返回结果
        idle_usage_ratio: 利用空闲时间的比例 (0.0 ~ 1.0)

    Returns:
        各 SKU 可额外生产的数量字典
    """
    forming_rate = 5000  # 件/小时
    labeling_rate = 4800  # 贴标机瓶颈（2台合计）

    # 可用空闲时间
    idle_h = analysis.get("idle_total_h", 0)
    available_h = idle_h * idle_usage_ratio

    # 以贴标机为瓶颈计算最大产能
    max_extra_qty = int(available_h * min(forming_rate, labeling_rate))

    # 按效率偏低的 SKU 分配额外产能
    inefficient = analysis.get("inefficient_campaigns", [])
    extra_by_sku: dict[str, int] = {}
    remaining_qty = max_extra_qty

    for c in inefficient:
        sku = c.get("sku")
        if not sku or remaining_qty <= 0:
            continue

        # 计算该 SKU 需要多少额外产量才能达到平均水平
        avg_h = analysis.get("avg_campaign_h", 0)
        current_h = c.get("duration_h", 0)
        gap_h = max(0, avg_h * 0.5 - current_h)  # 目标达到平均值的 50%
        needed_qty = int(gap_h * forming_rate)

        allocated = min(needed_qty, remaining_qty)
        if allocated > 0:
            extra_by_sku[sku] = extra_by_sku.get(sku, 0) + allocated
            remaining_qty -= allocated

    return extra_by_sku


def _generate_optimization_options(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """生成三个优化选项。

    Args:
        analysis: _analyze_forming_campaigns 的返回结果

    Returns:
        三个选项的列表
    """
    idle_h = analysis.get("idle_total_h", 0)
    forming_rate = 5000
    labeling_rate = 4800
    effective_rate = min(forming_rate, labeling_rate)

    options = []

    # 选项 1：保守方案（利用 30% idle 时间）
    extra_1 = _calculate_extra_capacity(analysis, idle_usage_ratio=0.3)
    total_extra_1 = sum(extra_1.values())
    time_used_1 = total_extra_1 / effective_rate if effective_rate > 0 else 0
    options.append({
        "id": "1",
        "name": "保守方案",
        "description": f"利用 30% 空闲时间（约 {time_used_1:.1f}h）",
        "idle_usage_ratio": 0.3,
        "extra_production": extra_1,
        "total_extra_qty": total_extra_1,
    })

    # 选项 2：平衡方案（利用 60% idle 时间）
    extra_2 = _calculate_extra_capacity(analysis, idle_usage_ratio=0.6)
    total_extra_2 = sum(extra_2.values())
    time_used_2 = total_extra_2 / effective_rate if effective_rate > 0 else 0
    options.append({
        "id": "2",
        "name": "平衡方案",
        "description": f"利用 60% 空闲时间（约 {time_used_2:.1f}h）",
        "idle_usage_ratio": 0.6,
        "extra_production": extra_2,
        "total_extra_qty": total_extra_2,
    })

    # 选项 3：最大化方案（利用全部 idle 时间）
    extra_3 = _calculate_extra_capacity(analysis, idle_usage_ratio=1.0)
    total_extra_3 = sum(extra_3.values())
    time_used_3 = total_extra_3 / effective_rate if effective_rate > 0 else 0
    options.append({
        "id": "3",
        "name": "最大化方案",
        "description": f"利用全部空闲时间（约 {time_used_3:.1f}h）",
        "idle_usage_ratio": 1.0,
        "extra_production": extra_3,
        "total_extra_qty": total_extra_3,
    })

    return options


@tool
def analyze_campaign_efficiency(
    min_campaign_ratio: float = 0.2,
) -> str:
    """分析换色周期效率，识别生产时间太短的周期。

    当发现某次换色之间生产的数量太少时，此工具可以：
    1. 分析各换色周期的效率
    2. 识别效率偏低的周期（生产时长 < 平均值 × min_campaign_ratio）
    3. 计算可利用的空闲时间
    4. 提供三个优化选项供用户选择

    Args:
        min_campaign_ratio: 最小效率比例，默认 0.2（低于平均值的 20% 视为效率偏低）

    Returns:
        换色周期效率分析报告和三个优化选项
    """
    global _campaign_optimization_state

    # 加载当前排产
    sched = load_schedule(DEFAULT_SCHEDULE_PATH)

    # 分析换色周期
    analysis = _analyze_forming_campaigns(sched)
    campaigns = analysis.get("campaigns", [])
    inefficient = analysis.get("inefficient_campaigns", [])
    idle_h = analysis.get("idle_total_h", 0)
    avg_h = analysis.get("avg_campaign_h", 0)

    # 生成优化选项
    options = _generate_optimization_options(analysis)

    # 保存状态
    _campaign_optimization_state = {
        "analysis": analysis,
        "options": options,
        "timestamp": datetime.now().isoformat(),
    }

    # 构建报告
    lines = ["## 换色周期效率分析", ""]

    # 总体统计
    lines.append("### 成型机（ROTARY-2）生产统计")
    lines.append(f"- 换色周期数: {len(campaigns)}")
    lines.append(f"- 平均生产时长: {avg_h:.1f} 小时")
    lines.append(f"- 总空闲时间: {idle_h:.1f} 小时")
    lines.append(f"- 换色次数: {int(analysis.get('setup_total_h', 0) / 12)}")
    lines.append("")

    # 各周期详情
    lines.append("### 各换色周期详情")
    for i, c in enumerate(campaigns, 1):
        sku = c.get("sku", "?")
        duration_h = c.get("duration_h", 0)
        qty = c.get("quantity", 0)
        pct_of_avg = (duration_h / avg_h * 100) if avg_h > 0 else 0

        # 判断状态
        if duration_h < avg_h * min_campaign_ratio:
            status = "⚠️ 极端太少"
        elif duration_h < avg_h * 0.5:
            status = "⚠️ 偏低"
        else:
            status = "✅ 正常"

        lines.append(f"- {sku}: {duration_h:.0f}h, {qty:,} 件 ({pct_of_avg:.1f}% of avg) {status}")
    lines.append("")

    # 效率偏低周期分析
    if inefficient:
        lines.append("### ⚠️ 效率偏低的周期")
        for c in inefficient:
            sku = c.get("sku", "?")
            duration_h = c.get("duration_h", 0)
            lines.append(f"- **{sku}** 周期仅 {duration_h:.0f}h（12h 换色 → {duration_h:.0f}h 生产）")
            efficiency = duration_h / (12 + duration_h) * 100
            lines.append(f"  效率比: {efficiency:.1f}%（生产时间 / 总耗时）")
        lines.append("")

    # 优化选项
    lines.append("### 优化选项")
    lines.append("")
    lines.append("请选择一个方案，系统将在不影响 PO 达成率的情况下额外生产半成品库存：")
    lines.append("")

    for opt in options:
        opt_id = opt.get("id")
        name = opt.get("name")
        desc = opt.get("description")
        extra = opt.get("extra_production", {})
        total_qty = opt.get("total_extra_qty", 0)

        lines.append(f"**选项 {opt_id}: {name}**")
        lines.append(f"- {desc}")
        if extra:
            for sku, qty in extra.items():
                lines.append(f"- 额外生产 {sku}: +{qty:,} 件")
        lines.append(f"- 总额外产量: +{total_qty:,} 件")
        lines.append("")

    lines.append("请回复 `1`、`2` 或 `3` 选择方案，或调用 `apply_campaign_optimization(option=\"1\")` 工具。")

    return "\n".join(lines)


# =============================================================================
# ERP 导出工具
# =============================================================================

# ERP 导出状态存储
_erp_export_state: dict[str, Any] = {
    "pending_orders": None,
    "days": None,
    "date_range": None,
    "timestamp": None,
}


def _filter_orders_by_days(schedule: dict[str, Any], days: int) -> tuple[list[dict[str, Any]], str]:
    """筛选前 N 天的订单。

    Args:
        schedule: 排产结果
        days: 天数

    Returns:
        (订单列表, 日期范围描述)
    """
    meta = schedule.get("meta", {})
    orders = schedule.get("orders", [])

    start_time_str = meta.get("start_time")
    if not start_time_str:
        return [], ""

    start_time = datetime.fromisoformat(start_time_str)
    end_time = start_time + timedelta(days=days)

    filtered = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        order_start_str = order.get("start")
        if not order_start_str:
            continue
        try:
            order_start = datetime.fromisoformat(order_start_str)
            if start_time <= order_start < end_time:
                filtered.append(order)
        except (ValueError, TypeError):
            continue

    date_range = f"{start_time.strftime('%Y-%m-%d')} 至 {(end_time - timedelta(days=1)).strftime('%Y-%m-%d')}"
    return filtered, date_range


@tool
def prepare_erp_export(days: int = 3) -> str:
    """准备将排产计划发送到 ERP。

    提取指定天数内的订单数据，返回摘要信息，等待用户确认物料是否已备好。

    Args:
        days: 导出天数（默认 3 天）

    Returns:
        导出数据摘要，包含订单数量和总数量
    """
    global _erp_export_state

    # 加载当前排产
    schedule = load_schedule(DEFAULT_SCHEDULE_PATH)

    # 筛选订单
    filtered_orders, date_range = _filter_orders_by_days(schedule, days)

    if not filtered_orders:
        return f"未找到前 {days} 天内的订单，无法导出。"

    # 计算统计信息
    order_count = len(filtered_orders)
    total_quantity = sum(int(o.get("quantity", 0)) for o in filtered_orders)

    # 存储到状态
    _erp_export_state = {
        "pending_orders": filtered_orders,
        "days": days,
        "date_range": date_range,
        "order_count": order_count,
        "total_quantity": total_quantity,
        "timestamp": datetime.now().isoformat(),
    }

    return f"准备导出 {date_range} 的排产：共 {order_count} 个订单，{total_quantity:,} 件"


@tool
def confirm_erp_export() -> str:
    """用户确认后发送排产计划到 ERP。

    检查是否有待发送的数据（5分钟内有效），然后模拟发送到 ERP。

    Returns:
        发送结果，成功时触发前端显示成功动画
    """
    global _erp_export_state
    import json as _json

    # 检查是否有待发送数据
    pending_orders = _erp_export_state.get("pending_orders")
    timestamp_str = _erp_export_state.get("timestamp")

    if not pending_orders or not timestamp_str:
        return "没有待发送的数据，请先调用 prepare_erp_export 准备导出数据。"

    # 检查是否过期（5分钟）
    try:
        timestamp = datetime.fromisoformat(timestamp_str)
        if datetime.now() - timestamp > timedelta(minutes=5):
            _erp_export_state = {
                "pending_orders": None,
                "days": None,
                "date_range": None,
                "timestamp": None,
            }
            return "准备的导出数据已过期（超过5分钟），请重新调用 prepare_erp_export。"
    except (ValueError, TypeError):
        return "导出状态异常，请重新调用 prepare_erp_export。"

    # 获取导出信息
    order_count = _erp_export_state.get("order_count", len(pending_orders))
    total_quantity = _erp_export_state.get("total_quantity", 0)
    date_range = _erp_export_state.get("date_range", "")

    # 模拟发送到 ERP（暂不实现实际接口）
    # TODO: 实际调用 ERP API
    # response = requests.post(ERP_API_URL, json={"orders": pending_orders})

    # 清空状态
    _erp_export_state = {
        "pending_orders": None,
        "days": None,
        "date_range": None,
        "timestamp": None,
    }

    # 构建返回数据，包含特殊标记触发前端动画
    card_data = {
        "status": "success",
        "orderCount": order_count,
        "totalQuantity": total_quantity,
        "dateRange": date_range,
        "timestamp": datetime.now().isoformat(),
    }

    return f"排产计划已成功发送到 ERP。\n\n__ERP_EXPORT_CARD__:{_json.dumps(card_data, ensure_ascii=False)}"


@tool
def send_erp_export(days: int = 3) -> str:
    """将未来 N 天的排产计划直接发送到 ERP（单步）。

    这是一个无状态的“单步发送”工具：每次调用都会从当前 schedule_result.json
    读取排产、筛选未来 N 天订单并发送（目前仍为模拟发送）。

    Args:
        days: 导出天数（默认 3 天）

    Returns:
        发送结果（包含 __ERP_EXPORT_CARD__ 标记触发前端成功提示）
    """
    import json as _json

    days = int(days or 0)
    if days <= 0:
        return "days 必须是 >= 1 的整数。"
    if days > 30:
        days = 30

    schedule = load_schedule(DEFAULT_SCHEDULE_PATH)
    filtered_orders, date_range = _filter_orders_by_days(schedule, days)
    if not filtered_orders:
        return f"未找到未来 {days} 天内的订单，无法导出。"

    order_count = len(filtered_orders)
    total_quantity = sum(int(o.get("quantity", 0)) for o in filtered_orders if isinstance(o, dict))

    # TODO: 实际调用 ERP API
    # response = requests.post(ERP_API_URL, json={"orders": filtered_orders})

    card_data = {
        "status": "success",
        "orderCount": order_count,
        "totalQuantity": total_quantity,
        "dateRange": date_range,
        "timestamp": datetime.now().isoformat(),
    }
    return (
        f"排产计划已成功发送到 ERP（{date_range}）。\n"
        f"- 订单数：{order_count}\n"
        f"- 总数量：{total_quantity:,} 件\n\n"
        f"__ERP_EXPORT_CARD__:{_json.dumps(card_data, ensure_ascii=False)}"
    )


@tool
def apply_campaign_optimization(
    option: Literal["1", "2", "3"],
) -> str:
    """应用换色周期优化方案。

    根据用户选择的选项，增加效率偏低 SKU 的额外生产量，
    然后重新排产。

    Args:
        option: 用户选择的选项（"1"=保守方案, "2"=平衡方案, "3"=最大化方案）

    Returns:
        优化应用结果，包括新方案的 KPI 和变化
    """
    global _campaign_optimization_state, _last_reschedule_state

    checked_state, checked_setup_remaining_h, err = _require_production_context_check(
        rotary_state=None,
        setup_remaining_h=None,
    )
    if err:
        return err

    # 检查是否有分析结果
    analysis = _campaign_optimization_state.get("analysis")
    options = _campaign_optimization_state.get("options")

    if not analysis or not options:
        return "请先调用 `analyze_campaign_efficiency` 工具分析换色周期效率。"

    # 找到用户选择的选项
    selected_option = None
    for opt in options:
        if opt.get("id") == option:
            selected_option = opt
            break

    if not selected_option:
        return f"无效的选项: {option}。请选择 1、2 或 3。"

    extra_production = selected_option.get("extra_production", {})
    if not extra_production:
        return "该选项没有额外生产计划（可能空闲时间不足）。"

    # Multi-line (ALL) schedule generation: keep other lines visible and only add extra
    # production as low-priority buffer demand on the relevant line.
    try:
        from process.multiline import generate_all_lines  # type: ignore

        forming_states = _production_context_check.get("forming_states")
        setup_remaining_by_machine = _production_context_check.get("setup_remaining_by_machine")
        if not isinstance(forming_states, dict):
            forming_states = None
        if not isinstance(setup_remaining_by_machine, dict):
            setup_remaining_by_machine = None

        _line_schedules, new_schedule = generate_all_lines(
            max_hours=8000,
            apply_downtime=True,
            forming_states_by_machine=forming_states,
            setup_remaining_by_machine=setup_remaining_by_machine,
            extra_production=extra_production,
        )
    except Exception as e:
        return f"重排失败: {e}"

    # 加载旧排产进行对比
    old_schedule = load_schedule(DEFAULT_SCHEDULE_PATH)

    # 生成甘特图
    gantt_html = render_gantt_html(new_schedule, px_per_day=120)

    # 更新状态
    constraint_info = {
        "type": "campaign_optimization",
        "option": option,
        "option_name": selected_option.get("name"),
        "extra_production": extra_production,
    }

    _last_reschedule_state = {
        "old_schedule": old_schedule,
        "new_schedule": new_schedule,
        "constraint": constraint_info,
        "new_schedule_gantt_html": gantt_html,
        "timestamp": datetime.now().isoformat(),
    }

    # 添加到多方案列表
    schedule_id, meta = _add_comparison_schedule(
        schedule=new_schedule,
        gantt_html=gantt_html,
        constraint=constraint_info,
        label=f"换色优化({selected_option.get('name')})",
    )

    # 计算 KPI 变化
    old_kpi = old_schedule.get("kpi", {})
    new_kpi = new_schedule.get("kpi", {})

    old_on_time_rate = old_kpi.get("containers_on_time_rate", 0) * 100
    new_on_time_rate = new_kpi.get("containers_on_time_rate", 0) * 100

    # 构建结果报告
    lines = [f"## 已应用 {selected_option.get('name')}", ""]

    lines.append("### 额外生产计划")
    total_extra = 0
    for sku, qty in extra_production.items():
        lines.append(f"- {sku}: +{qty:,} 件")
        total_extra += qty
    lines.append(f"- **总额外产量**: +{total_extra:,} 件")
    lines.append("")

    lines.append("### KPI 对比")
    lines.append(f"- 货柜准时率: {old_on_time_rate:.1f}% → {new_on_time_rate:.1f}%")
    if new_on_time_rate >= old_on_time_rate:
        lines.append("  ✅ 准时率未降低")
    else:
        lines.append(f"  ⚠️ 准时率降低 {old_on_time_rate - new_on_time_rate:.1f}%")
    lines.append("")

    # 分析新的换色周期
    new_analysis = _analyze_forming_campaigns(new_schedule)
    lines.append("### 优化后换色周期")
    for c in new_analysis.get("campaigns", []):
        sku = c.get("sku", "?")
        duration_h = c.get("duration_h", 0)
        qty = c.get("quantity", 0)
        lines.append(f"- {sku}: {duration_h:.0f}h, {qty:,} 件")
    lines.append("")

    lines.append("新方案已添加到右侧「重排方案」列表。请预览后点击 ✓ 确认应用，或点击 ✕ 删除。")

    # 添加 schedule_card 标记
    result_text = "\n".join(lines)
    result_text += _make_schedule_card_marker(
        schedule_id=schedule_id,
        schedule_type="comparison",
        label=meta["label"],
        timestamp=meta["timestamp"],
        constraint=constraint_info,
    )

    return result_text
