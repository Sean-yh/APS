from __future__ import annotations

from datetime import datetime
from typing import Any


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


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


def summarize_schedule_diff(
    *,
    old: dict[str, Any],
    new: dict[str, Any],
    focus_order_id: int | None = None,
    max_affected: int | None = None,
    include_unchanged: bool = False,
) -> str:
    lines: list[str] = []

    old_kpi = old.get("kpi") if isinstance(old.get("kpi"), dict) else {}
    new_kpi = new.get("kpi") if isinstance(new.get("kpi"), dict) else {}
    old_meta = old.get("meta") if isinstance(old.get("meta"), dict) else {}
    new_meta = new.get("meta") if isinstance(new.get("meta"), dict) else {}

    def fnum(v: Any, nd: int = 3) -> str:
        try:
            return f"{float(v):.{nd}f}"
        except Exception:
            return str(v)

    lines.append(
        "KPI: "
        f"on_time_rate {fnum(old_kpi.get('on_time_rate'))} → {fnum(new_kpi.get('on_time_rate'))}, "
        f"total_tardiness_h {fnum(old_kpi.get('total_tardiness_h'), 1)} → {fnum(new_kpi.get('total_tardiness_h'), 1)}, "
        f"setup_count {old_kpi.get('setup_count')} → {new_kpi.get('setup_count')}"
    )
    lines.append(
        "Meta: "
        f"chain_start_h {old_meta.get('chain_start_h')} → {new_meta.get('chain_start_h')}, "
        f"horizon_h {old_meta.get('horizon_h')} → {new_meta.get('horizon_h')}"
    )

    if focus_order_id is not None:
        old_row = _find_order_row(old, int(focus_order_id))
        new_row = _find_order_row(new, int(focus_order_id))
        if old_row and new_row:
            lines.append("")
            lines.append(f"订单 {focus_order_id} 变化：")
            lines.append(f"- due {old_row.get('due')} → {new_row.get('due')}")
            lines.append(f"- machine {old_row.get('machine')} → {new_row.get('machine')}")
            lines.append(f"- start {old_row.get('start')} → {new_row.get('start')}")
            lines.append(f"- 生产完成 {old_row.get('end')} → {new_row.get('end')}")
            lines.append(f"- on_time {old_row.get('on_time')} → {new_row.get('on_time')}")
            lines.append(f"- lateness_h {old_row.get('lateness_h')} → {new_row.get('lateness_h')}")

    # Impacted orders summary.
    old_rows = old.get("orders") if isinstance(old.get("orders"), list) else []
    new_rows = new.get("orders") if isinstance(new.get("orders"), list) else []
    old_map: dict[int, dict[str, Any]] = {}
    new_map: dict[int, dict[str, Any]] = {}
    for r in old_rows:
        if isinstance(r, dict) and "c_orderline_id" in r:
            old_map[int(r["c_orderline_id"])] = r
    for r in new_rows:
        if isinstance(r, dict) and "c_orderline_id" in r:
            new_map[int(r["c_orderline_id"])] = r

    def _fmt_shift(v: float | None) -> str:
        if v is None:
            return "N/A"
        return f"{v:+.1f}"

    impacted: list[tuple[tuple[int, int, float], str]] = []
    for oid, o in old_map.items():
        n = new_map.get(oid)
        if not n:
            continue
        if focus_order_id is not None and oid == int(focus_order_id):
            continue

        machine_changed = int((o.get("machine") or "") != (n.get("machine") or ""))
        on_time_changed = int(bool(o.get("on_time")) != bool(n.get("on_time")))
        o_s, n_s = _dt(o.get("start")), _dt(n.get("start"))
        o_e, n_e = _dt(o.get("end")), _dt(n.get("end"))
        start_shift_h = _hours(o_s, n_s)
        end_shift_h = _hours(o_e, n_e)
        shift_mag = abs(start_shift_h or 0.0) + abs(end_shift_h or 0.0)

        if (not include_unchanged) and (not machine_changed) and (not on_time_changed) and shift_mag <= 1e-9:
            continue

        summary = (
            f"{oid} | {o.get('machine')}→{n.get('machine')} | "
            f"start_shift_h={_fmt_shift(start_shift_h)} end_shift_h={_fmt_shift(end_shift_h)} | "
            f"on_time {o.get('on_time')}→{n.get('on_time')}"
        )
        impacted.append(((on_time_changed, machine_changed, shift_mag), summary))

    impacted.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))
    if impacted:
        limit = len(impacted)
        if isinstance(max_affected, int) and max_affected > 0:
            limit = min(max_affected, len(impacted))
        lines.append("")
        title = "订单对比" if include_unchanged else "受影响订单"
        lines.append(f"{title}（{limit} / {len(impacted)}）：")
        for _, s in impacted[:limit]:
            lines.append(f"- {s}")

    return "\n".join(lines)


def analyze_priority_lock_impact(
    old_schedule: dict[str, Any],
    new_schedule: dict[str, Any],
    priority_order_ids: list[int],
) -> str:
    """分析优先锁定排产对其他订单的影响。

    返回报告包括：
    1. 优先订单的排产情况
    2. 受影响的订单列表（被推迟的）
    3. KPI 变化（准时率、总延迟）
    4. 建议（是否接受此方案）
    """
    lines: list[str] = []
    priority_set = set(priority_order_ids)

    old_kpi = old_schedule.get("kpi") if isinstance(old_schedule.get("kpi"), dict) else {}
    new_kpi = new_schedule.get("kpi") if isinstance(new_schedule.get("kpi"), dict) else {}

    old_rows = old_schedule.get("orders") if isinstance(old_schedule.get("orders"), list) else []
    new_rows = new_schedule.get("orders") if isinstance(new_schedule.get("orders"), list) else []

    old_map: dict[int, dict[str, Any]] = {}
    new_map: dict[int, dict[str, Any]] = {}
    for r in old_rows:
        if isinstance(r, dict) and "c_orderline_id" in r:
            old_map[int(r["c_orderline_id"])] = r
    for r in new_rows:
        if isinstance(r, dict) and "c_orderline_id" in r:
            new_map[int(r["c_orderline_id"])] = r

    # 1. Priority order status
    lines.append("## 优先锁定排产报告")
    lines.append("")
    lines.append("### 锁定订单")

    for oid in priority_order_ids:
        new_row = new_map.get(oid)
        if not new_row:
            lines.append(f"- 订单 {oid}: 未找到")
            continue

        on_time = new_row.get("on_time") and not new_row.get("expired_before_start")
        status = "✅ 可按时完成" if on_time else "⚠️ 仍有延迟"

        lines.append(f"- 订单号: {oid} ({new_row.get('poreference', '')})")
        lines.append(f"  - 产品: {new_row.get('sku')}, 数量: {new_row.get('quantity', 0):,}")
        lines.append(f"  - 交期: {new_row.get('deadline', '')[:10] if new_row.get('deadline') else ''}")
        lines.append(f"  - 排产时间: {new_row.get('start', '')[:16]} ~ {new_row.get('end', '')[:16]}")
        lines.append(f"  - 机台: {new_row.get('machine')}")
        lines.append(f"  - 状态: {status}")
        if not on_time:
            lines.append(f"  - 延迟: {new_row.get('lateness_h', 0):.1f} 小时")

    # 2. Impacted orders (delayed)
    lines.append("")
    lines.append("### 受影响订单（被推迟）")

    delayed_orders: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    # 从准时变为延误的订单
    newly_late_orders: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    # 仍然准时但完成时间被推迟的订单
    still_on_time_delayed: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    # 本已延误且进一步推迟的订单
    already_late_delayed: list[tuple[float, dict[str, Any], dict[str, Any]]] = []

    for oid, old_row in old_map.items():
        if oid in priority_set:
            continue
        new_row = new_map.get(oid)
        if not new_row:
            continue

        old_end = _dt(old_row.get("end"))
        new_end = _dt(new_row.get("end"))
        delay_h = _hours(old_end, new_end)

        # 检查是否从准时变为延误
        old_on_time = old_row.get("on_time") and not old_row.get("expired_before_start")
        new_on_time = new_row.get("on_time") and not new_row.get("expired_before_start")

        if old_on_time and not new_on_time:
            # 从准时变为延误
            new_lateness = float(new_row.get("lateness_h") or 0.0)
            newly_late_orders.append((new_lateness, old_row, new_row))

        if delay_h is not None and delay_h > 0.5:  # At least 0.5h delay
            delayed_orders.append((delay_h, old_row, new_row))
            # 细分：仍然准时但被推迟 vs 本已延误且进一步推迟
            if old_on_time and new_on_time:
                still_on_time_delayed.append((delay_h, old_row, new_row))
            elif not old_on_time:
                already_late_delayed.append((delay_h, old_row, new_row))

    if delayed_orders:
        delayed_orders.sort(key=lambda x: -x[0])
        lines.append("")
        lines.append(f"共 {len(delayed_orders)} 个订单生产完成时间被推迟：")
        lines.append("")

        for delay_h, old_row, new_row in delayed_orders:
            oid = old_row.get("c_orderline_id")
            sku = old_row.get("sku", "")
            old_end = str(old_row.get("end", ""))[:16].replace("T", " ")
            new_end = str(new_row.get("end", ""))[:16].replace("T", " ")
            lines.append(f"- {oid} | {sku} | 原完成={old_end} | 新完成={new_end} | 推迟=+{delay_h:.1f}h")
    else:
        lines.append("无订单被推迟。")

    # 3. Newly late orders (on_time: True → False) - 重点关注
    lines.append("")
    lines.append("### ⚠️ 新增延误订单（原本准时，现在延误）")

    if newly_late_orders:
        newly_late_orders.sort(key=lambda x: -x[0])  # 按延误时长降序
        lines.append("")
        lines.append("以下订单原本可以准时交付，但因优先锁定导致延误：")
        lines.append("")

        for lateness_h, old_row, new_row in newly_late_orders:
            oid = old_row.get("c_orderline_id")
            poref = old_row.get("poreference", "")
            sku = old_row.get("sku", "")
            due = str(new_row.get("due", ""))[:10]
            deadline = str(new_row.get("deadline", ""))[:16].replace("T", " ")
            new_end = str(new_row.get("end", ""))[:16].replace("T", " ")
            lines.append(
                f"- {oid} | {poref} | {sku} | due={due} | deadline={deadline} | 完成={new_end} | 延误={lateness_h:.1f}h"
            )

        lines.append("")
        lines.append(f"**共 {len(newly_late_orders)} 个订单从准时变为延误，需要特别关注！**")
    else:
        lines.append("无订单从准时变为延误。")

    # 3. KPI comparison
    lines.append("")
    lines.append("### KPI 变化")
    lines.append("")

    # 货柜准时率（主要指标）
    old_container_rate = float(old_kpi.get("containers_on_time_rate", 0)) * 100
    new_container_rate = float(new_kpi.get("containers_on_time_rate", 0)) * 100
    container_rate_change = new_container_rate - old_container_rate
    container_rate_sign = "+" if container_rate_change >= 0 else ""
    lines.append(
        f"- 货柜准时率: {old_container_rate:.1f}% → {new_container_rate:.1f}% ({container_rate_sign}{container_rate_change:.1f}%)"
    )

    # 货柜总延迟
    old_container_tardiness = float(old_kpi.get("total_container_tardiness_h", 0))
    new_container_tardiness = float(new_kpi.get("total_container_tardiness_h", 0))
    container_tardiness_change = new_container_tardiness - old_container_tardiness
    container_tardiness_sign = "+" if container_tardiness_change >= 0 else ""
    lines.append(
        f"- 货柜总延迟: {old_container_tardiness:.1f}h → {new_container_tardiness:.1f}h ({container_tardiness_sign}{container_tardiness_change:.1f}h)"
    )

    # 订单准时率
    old_on_time_rate = float(old_kpi.get("on_time_rate", 0)) * 100
    new_on_time_rate = float(new_kpi.get("on_time_rate", 0)) * 100
    rate_change = new_on_time_rate - old_on_time_rate
    rate_sign = "+" if rate_change >= 0 else ""
    lines.append(f"- 订单准时率: {old_on_time_rate:.1f}% → {new_on_time_rate:.1f}% ({rate_sign}{rate_change:.1f}%)")

    # 订单总延迟
    old_tardiness = float(old_kpi.get("total_tardiness_h", 0))
    new_tardiness = float(new_kpi.get("total_tardiness_h", 0))
    tardiness_change = new_tardiness - old_tardiness
    tardiness_sign = "+" if tardiness_change >= 0 else ""
    lines.append(
        f"- 订单总延迟: {old_tardiness:.1f}h → {new_tardiness:.1f}h ({tardiness_sign}{tardiness_change:.1f}h)"
    )

    # 5. Recommendation
    lines.append("")
    lines.append("### 建议")

    if not delayed_orders and not newly_late_orders:
        lines.append("✅ 此方案不会导致其他订单延迟，建议采纳。")
    elif newly_late_orders:
        lines.append(f"❌ 此方案会导致 **{len(newly_late_orders)} 个原本准时的订单变为延误**，需要与相关客户沟通确认。")
        if still_on_time_delayed:
            lines.append(f"   另有 {len(still_on_time_delayed)} 个订单虽仍准时，但完成时间被推迟。")
        if already_late_delayed:
            lines.append(f"   另有 {len(already_late_delayed)} 个本已延误的订单会进一步推迟。")
    elif container_rate_change >= -1.0 and container_tardiness_change <= 10:
        lines.append(f"⚠️ 此方案会导致 {len(delayed_orders)} 个订单延迟，但整体影响较小。建议与相关客户确认后采纳。")
    else:
        lines.append(f"⚠️ 此方案会导致 {len(delayed_orders)} 个订单延迟，整体KPI有所下降。建议评估后决定。")

    return "\n".join(lines)
