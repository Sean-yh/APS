#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from string import Template
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _days_since(t0: datetime, t: datetime) -> float:
    return (t - t0).total_seconds() / 86400.0


def _esc_attr(v: Any) -> str:
    return escape(str(v), quote=True)


def _json_script_tag(tag_id: str, obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    raw = raw.replace("</", "<\\/")  # avoid closing <script> tags in embedded JSON.
    return f'<script type="application/json" id="{_esc_attr(tag_id)}">{raw}</script>'


def _task_color(task: dict[str, Any]) -> str:
    sku_colors = {
        "S12G9C": "#E67E5A",  # 主橙红 - 应用强调色
        "S12G9V": "#F59E0B",  # 琥珀黄 (Tailwind amber-500)
        "S12G9W": "#EA580C",  # 深橙 (Tailwind orange-600)
    }
    t = str(task.get("type") or "")
    setup_type = task.get("setup_type") or ""

    # 处理停机类型（假期、维护）
    if setup_type:
        if str(setup_type).startswith("holiday:"):
            return "#E0E0E0"  # 浅灰色 - 假期
        if str(setup_type).startswith("maintenance:"):
            return "#FFA726"  # 橙色 - 维护

    if t == "setup":
        return "#666666"
    if t == "idle":
        return "#DDDDDD"
    sku = task.get("sku")
    if isinstance(sku, str) and sku in sku_colors:
        return sku_colors[sku]
    return "#999999"


def _task_label(task: dict[str, Any]) -> str:
    t = str(task.get("type") or "")
    setup_type = task.get("setup_type") or ""

    # 处理停机类型（假期、维护）的标签
    if setup_type:
        if str(setup_type).startswith("holiday:"):
            return str(setup_type).replace("holiday:", "")
        if str(setup_type).startswith("maintenance:"):
            return str(setup_type).replace("maintenance:", "")

    if t == "forming":
        sku = task.get("sku")
        if isinstance(sku, str):
            return sku
    if t == "label":
        order_id = task.get("order_id")
        if isinstance(order_id, int):
            return str(order_id)
        sku = task.get("sku")
        if isinstance(sku, str):
            return sku
    if t == "setup":
        from_sku = task.get("from_sku")
        to_sku = task.get("to_sku")
        if isinstance(from_sku, str) and isinstance(to_sku, str):
            return f"{from_sku}→{to_sku}"
        return "setup"
    return ""


def _task_tooltip(machine: str, task: dict[str, Any]) -> str:
    parts: list[str] = [f"machine={machine}", f"type={task.get('type')}"]
    for key in ("sku", "order_id", "quantity", "setup_type", "from_sku", "to_sku", "start", "end", "duration_h"):
        if key in task and task.get(key) not in (None, ""):
            parts.append(f"{key}={task.get(key)}")
    return "\n".join(parts)


def _load_production_calendar() -> dict[str, Any]:
    """加载生产日历配置（DB-only）。"""
    from ai.calendar_store import load_calendar

    cal = load_calendar()
    if not isinstance(cal, dict):
        return {"holidays": [], "maintenance": []}
    if not isinstance(cal.get("holidays"), list):
        cal["holidays"] = []
    if not isinstance(cal.get("maintenance"), list):
        cal["maintenance"] = []
    return cal


def _get_holidays(calendar: dict[str, Any]) -> list[tuple[datetime, datetime, str]]:
    """从日历中提取假期列表，返回 [(start, end, name), ...]"""
    holidays = []
    for h in calendar.get("holidays", []):
        start = datetime.fromisoformat(h["start"])
        end = datetime.fromisoformat(h["end"]) + timedelta(days=1)  # end is inclusive
        name = h.get("name", "假期")
        holidays.append((start, end, name))
    return sorted(holidays, key=lambda x: x[0])


def _split_tasks_by_holidays(
    tasks: list[dict[str, Any]],
    holidays: list[tuple[datetime, datetime, str]],
) -> list[dict[str, Any]]:
    """
    将跨假期的任务拆分为多个显示段。

    对于 type="label" 的任务，如果时间范围跨越假期：
    1. 将任务拆分为：工作段 + 假期段 + 工作段 + ...
    2. 假期段使用 type="idle", setup_type="holiday:假期名"
    3. 工作段保留原始任务的所有属性，但更新 start/end

    其他类型的任务不拆分。
    """
    if not holidays:
        return tasks

    result: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("type") != "label":
            result.append(task)
            continue

        task_start = _dt(task["start"])
        task_end = _dt(task["end"])

        # 找出与任务时间范围重叠的假期
        overlapping_holidays: list[tuple[datetime, datetime, str]] = []
        for h_start, h_end, h_name in holidays:
            if h_start < task_end and h_end > task_start:
                overlapping_holidays.append((
                    max(h_start, task_start),  # 假期段开始
                    min(h_end, task_end),       # 假期段结束
                    h_name,
                ))

        if not overlapping_holidays:
            result.append(task)
            continue

        # 按时间排序
        overlapping_holidays.sort(key=lambda x: x[0])

        # 拆分任务
        current = task_start

        for h_start, h_end, h_name in overlapping_holidays:
            # 假期前的工作段
            if current < h_start:
                work_segment = task.copy()
                work_segment["start"] = current.isoformat()
                work_segment["end"] = h_start.isoformat()
                work_segment["duration_h"] = int((h_start - current).total_seconds() / 3600)
                result.append(work_segment)

            # 假期段
            holiday_segment = {
                "type": "idle",
                "setup_type": f"holiday:{h_name}",
                "start": h_start.isoformat(),
                "end": h_end.isoformat(),
                "duration_h": int((h_end - h_start).total_seconds() / 3600),
            }
            result.append(holiday_segment)

            current = max(current, h_end)  # 只向前移动，防止嵌套假期回退

        # 假期后的工作段
        if current < task_end:
            work_segment = task.copy()
            work_segment["start"] = current.isoformat()
            work_segment["end"] = task_end.isoformat()
            work_segment["duration_h"] = int((task_end - current).total_seconds() / 3600)
            result.append(work_segment)

    return result


def _merge_consecutive_idle_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并连续的同类型 idle 任务。"""
    if not tasks:
        return tasks

    result: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("type") != "idle":
            result.append(task)
            continue

        if result and result[-1].get("type") == "idle":
            prev = result[-1]
            if (prev.get("setup_type") == task.get("setup_type") and
                prev.get("end") == task.get("start")):
                prev["end"] = task["end"]
                prev["duration_h"] = prev.get("duration_h", 0) + task.get("duration_h", 0)
                continue

        result.append(task.copy())
    return result


def _render_html(data: dict[str, Any], px_per_day: int) -> str:
    machines: dict[str, list[dict[str, Any]]] = data["machines"]

    # 加载假期配置并拆分跨假期的任务
    calendar = _load_production_calendar()
    holidays = _get_holidays(calendar)
    for machine_id in machines:
        machines[machine_id] = _split_tasks_by_holidays(machines[machine_id], holidays)

    # 合并连续的 idle 任务
    for machine_id in machines:
        machines[machine_id] = _merge_consecutive_idle_tasks(machines[machine_id])
    meta = data.get("meta") or {}
    kpi = data.get("kpi") or {}
    inv = data.get("inventory") or {}
    orders = data.get("orders") if isinstance(data.get("orders"), list) else []

    inv_skus: dict[str, list[int]] = {}
    if isinstance(inv, dict):
        raw = inv.get("skus")
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                    inv_skus[str(k)] = [int(x) for x in v]

    start_time = _dt(str(meta["start_time"]))
    max_end = start_time
    for tasks in machines.values():
        for task in tasks:
            try:
                max_end = max(max_end, _dt(str(task["end"])))
            except Exception:
                continue

    inv_time_step_h = 1.0
    if isinstance(inv, dict) and isinstance(inv.get("time_step_h"), (int, float)):
        inv_time_step_h = float(inv["time_step_h"]) or 1.0
    elif isinstance(meta.get("time_step_h"), (int, float)):
        inv_time_step_h = float(meta["time_step_h"]) or 1.0

    if inv_skus:
        series_len = max((len(v) for v in inv_skus.values()), default=0)
        if series_len >= 2:
            inv_end = start_time + timedelta(hours=(series_len - 1) * inv_time_step_h)
            max_end = max(max_end, inv_end)

    # 以 start_time 所在日的 00:00 为基准，使日期刻度对齐到午夜
    timeline_base = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon_days = max(0.0, _days_since(timeline_base, max_end))
    day_count = max(1, int(math.ceil(horizon_days)))
    timeline_width_px = day_count * int(px_per_day)

    axis_ticks: list[str] = []
    for d in range(day_count + 1):
        tick_dt = timeline_base + timedelta(days=d)  # 从午夜开始
        left = d * int(px_per_day)
        full_date = tick_dt.strftime("%Y-%m-%d")
        short_date = tick_dt.strftime("%m-%d")
        axis_ticks.append(
            '<div class="tick" data-day="'
            + str(d)
            + '" data-date="'
            + _esc_attr(full_date)
            + '" style="left:'
            + str(left)
            + 'px">'
            + '<div class="tick-line"></div>'
            + '<div class="tick-label">'
            + escape(short_date)
            + "</div></div>"
        )

    def _data_attrs(attrs: dict[str, Any]) -> str:
        out: list[str] = []
        for k, v in attrs.items():
            if v is None:
                continue
            out.append(f' data-{k}="{_esc_attr(v)}"')
        return "".join(out)

    grid_cells: list[str] = []
    grid_cells.append('<div class="cell cell--label cell--header">Machine</div>')
    grid_cells.append(
        '<div class="cell cell--header cell--axis">'
        + '<div id="axis" class="axis timeline-bg" style="width:'
        + str(timeline_width_px)
        + 'px">'
        + "".join(axis_ticks)
        + "</div></div>"
    )

    for machine, tasks in machines.items():
        grid_cells.append(
            '<div class="cell cell--label" title="'
            + _esc_attr(machine)
            + '">'
            + escape(str(machine))
            + "</div>"
        )

        bars: list[str] = []
        for task in tasks:
            try:
                ts = _dt(str(task["start"]))
                te = _dt(str(task["end"]))
            except Exception:
                continue
            if te <= ts:
                continue

            left_days = _days_since(timeline_base, ts)  # 相对于午夜计算
            duration_days = _days_since(ts, te)
            left_px = int(round(left_days * px_per_day))
            width_px = max(1, int(round(duration_days * px_per_day)))

            task_type = str(task.get("type") or "")
            color = _task_color(task)
            label = _task_label(task)
            tooltip = _task_tooltip(machine, task)

            attrs = _data_attrs(
                {
                    "machine": machine,
                    "type": task_type,
                    "sku": task.get("sku"),
                    "order-id": task.get("order_id"),
                    "quantity": task.get("quantity"),
                    "start": task.get("start"),
                    "end": task.get("end"),
                    "duration-h": task.get("duration_h"),
                    "left-days": f"{left_days:.10f}",
                    "duration-days": f"{duration_days:.10f}",
                }
            )

            bars.append(
                '<div class="bar" style="left:'
                + str(left_px)
                + "px;width:"
                + str(width_px)
                + "px;--bar-color:"
                + _esc_attr(color)
                + '"'
                + attrs
                + '><span class="bar-label">'
                + escape(label)
                + "</span></div>"
            )

        grid_cells.append(
            '<div class="cell lane timeline-bg" data-machine="'
            + _esc_attr(machine)
            + '" style="width:'
            + str(timeline_width_px)
            + 'px">'
            + "".join(bars)
            + "</div>"
        )

    inv_height = 220
    inv_payload: dict[str, Any] | None = None
    if inv_skus:
        vals = [x for series in inv_skus.values() for x in series]
        y_min = min(vals) if vals else 0
        y_max = max(vals) if vals else 1
        if y_max <= y_min:
            y_max = y_min + 1

        pad_top = 14
        pad_bottom = 22
        plot_h = max(1, inv_height - pad_top - pad_bottom)

        sku_colors = {
            "S12G9C": "#E67E5A",  # 主橙红 - 应用强调色
            "S12G9V": "#F59E0B",  # 琥珀黄 (Tailwind amber-500)
            "S12G9W": "#EA580C",  # 深橙 (Tailwind orange-600)
        }

        h_lines: list[str] = []
        for frac in (0.0, 0.5, 1.0):
            y = pad_top + int(round(frac * plot_h))
            h_lines.append(
                f'<line class="inv-hline" x1="0" y1="{y}" x2="{timeline_width_px}" y2="{y}" stroke="#E5E7EB" stroke-width="1" />'
            )

        polylines: list[str] = []
        # 库存数据从 start_time 开始，需要加上相对于 timeline_base 的偏移
        inv_offset_days = _days_since(timeline_base, start_time)
        for sku, series in inv_skus.items():
            pts: list[str] = []
            for i, val in enumerate(series):
                x = int(round((inv_offset_days + (i * inv_time_step_h) / 24.0) * px_per_day))
                y = pad_top + int(round(((y_max - val) / (y_max - y_min)) * plot_h))
                pts.append(f"{x},{y}")
            stroke = sku_colors.get(sku, "#999999")
            polylines.append(
                f'<polyline class="inv-line" data-sku="{_esc_attr(sku)}" fill="none" stroke="{escape(stroke)}" stroke-width="2" points="{" ".join(pts)}" />'
            )

        svg = (
            '<svg id="invSvg" width="'
            + str(timeline_width_px)
            + '" height="'
            + str(inv_height)
            + '" viewBox="0 0 '
            + str(timeline_width_px)
            + " "
            + str(inv_height)
            + '" xmlns="http://www.w3.org/2000/svg">'
            + "".join(h_lines)
            + "".join(polylines)
            + '<line id="invCursor" x1="0" y1="'
            + str(pad_top)
            + '" x2="0" y2="'
            + str(pad_top + plot_h)
            + '" stroke="#111827" stroke-opacity="0.25" stroke-width="1" style="display:none" />'
        )
        for sku in inv_skus.keys():
            stroke = sku_colors.get(sku, "#999999")
            svg += (
                '<circle class="inv-dot" data-sku="'
                + _esc_attr(sku)
                + '" cx="0" cy="0" r="3.5" fill="'
                + _esc_attr(stroke)
                + '" stroke="#fff" stroke-width="1.5" style="display:none" />'
            )
        svg += "</svg>"

        inv_payload = {
            "start_time": str(inv.get("start_time") or meta.get("start_time")),
            "time_step_h": inv_time_step_h,
            "offset_days": inv_offset_days,  # 库存数据相对于时间线基准的偏移
            "y_min": y_min,
            "y_max": y_max,
            "height": inv_height,
            "pad_top": pad_top,
            "pad_bottom": pad_bottom,
            "skus": inv_skus,
        }


    title_line = escape(str(meta.get("line") or "Schedule"))
    start_str = escape(start_time.strftime("%Y-%m-%d %H:%M"))
    chain_start_h = escape("" if meta.get("chain_start_h") is None else str(meta.get("chain_start_h")))
    on_time_rate = float(kpi.get("containers_on_time_rate", 0.0) or 0.0)
    total_tardiness_days = float(kpi.get("total_container_tardiness_days", 0.0) or 0.0)

    orders_json = _json_script_tag("ordersData", orders)
    inv_json = _json_script_tag("invData", inv_payload) if inv_payload is not None else ""

    tpl = Template(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>$title Schedule</title>
  <style>
    :root {
      --bg: #F5F0EB;
      --card: #FFFFFF;
      --text: #1a1a1a;
      --muted: #64748b;
      --border: #E5E0DB;
      --shadow: 0 1px 2px rgba(0,0,0,0.06);
      --accent: #E67E5A;
      --axis-h: 44px;
      --row-h: 36px;
      --bar-h: 22px;
      --inv-h: $inv_h;
      --label-w: 160px;
      --grid: $grid_px;
      --timeline-width: $timeline_w;
    }
    body {
      margin: 16px;
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, "Noto Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .top {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 12px 16px;
      margin: 0 0 12px 0;
    }
    .title {
      display: grid;
      gap: 2px;
    }
    .title h1 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0.2px;
    }
    .title .sub {
      font-size: 13px;
      color: var(--muted);
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: var(--shadow);
    }
    .kpis {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 10px;
      justify-content: flex-end;
    }
    .kpi {
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 13px;
      color: var(--muted);
      background: rgba(148,163,184,0.08);
    }
    .kpi b {
      color: var(--text);
      font-weight: 600;
    }
    .controls {
      padding: 10px 12px;
      margin-bottom: 12px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 14px;
      font-size: 13px;
      color: var(--muted);
    }
    .controls .group {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px 10px;
    }
    .controls input[type="text"] {
      width: min(340px, 72vw);
      padding: 7px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: rgba(148,163,184,0.06);
      color: var(--text);
      outline: none;
    }
    .controls input[type="text"]::placeholder {
      color: rgba(100,116,139,0.9);
    }
    .controls input[type="range"] {
      width: 180px;
    }
    .btn {
      padding: 6px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: rgba(148,163,184,0.08);
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
    }
    .btn:hover {
      border-color: rgba(37,99,235,0.45);
    }
    .controls label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      user-select: none;
    }
    .gantt {
      overflow: hidden;
    }
    .gantt-scroll {
      /* Fill the viewport (works well inside the frontend iframe too). */
      height: 100vh;
      max-height: none;
      min-height: 360px;
      overflow: auto;
      overscroll-behavior: contain;
      cursor: grab;
    }
    .gantt-scroll.dragging {
      cursor: grabbing;
    }
    .gantt-grid {
      display: grid;
      grid-template-columns: var(--label-w) var(--timeline-width);
      width: calc(var(--label-w) + var(--timeline-width));
    }
    .cell {
      box-sizing: border-box;
    }
    .cell--label {
      position: sticky;
      left: 0;
      z-index: 5;
      background: var(--card);
      border-right: 1px solid var(--border);
      padding: 0 10px;
      display: flex;
      align-items: center;
      height: var(--row-h);
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .cell--header {
      position: sticky;
      top: 0;
      z-index: 7;
      background: var(--card);
    }
    .cell--header.cell--label {
      z-index: 9;
      height: var(--axis-h);
      font-weight: 600;
      color: var(--text);
    }
    .cell--axis {
      height: var(--axis-h);
      border-bottom: 1px solid var(--border);
    }
    .timeline-bg {
      background-image: repeating-linear-gradient(to right, rgba(180,120,90,0.12) 0 1px, transparent 1px var(--grid));
    }
    .axis {
      position: relative;
      height: var(--axis-h);
    }
    .tick {
      position: absolute;
      top: 0;
      bottom: 0;
    }
    .tick-line {
      position: absolute;
      top: 0;
      bottom: 0;
      left: 0;
      width: 1px;
      background: rgba(148,163,184,0.55);
    }
    .tick-label {
      position: absolute;
      top: 12px;
      left: 0;
      transform: translateX(8px);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .tick-label.is-hidden {
      display: none;
    }
    .lane {
      position: relative;
      height: var(--row-h);
      border-bottom: 1px solid rgba(148,163,184,0.18);
    }
    .inv {
      height: var(--inv-h);
      border-bottom: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.60);
    }
    .inv-label {
      height: var(--inv-h);
      align-items: flex-start;
      padding-top: 10px;
      color: var(--muted);
    }
    .inv-wrap {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: stretch;
    }
    .inv svg {
      display: block;
    }
    .inv.is-hidden {
      display: none;
    }
    .cell--label.is-hidden {
      display: none;
    }
    .bar {
      position: absolute;
      top: calc((var(--row-h) - var(--bar-h)) / 2);
      height: var(--bar-h);
      border-radius: 10px;
      padding: 0 8px;
      display: flex;
      align-items: center;
      font-size: 12px;
      color: rgba(15, 23, 42, 0.92);
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      box-sizing: border-box;
      border: 1px solid rgba(15, 23, 42, 0.12);
      background: var(--bar-color);
      box-shadow: 0 1px 1px rgba(15,23,42,0.10);
      cursor: pointer;
      user-select: none;
    }
    .bar:hover {
      filter: brightness(1.03);
    }
    .bar[data-type="setup"] {
      color: rgba(255,255,255,0.95);
      background-image: repeating-linear-gradient(45deg, rgba(255,255,255,0.18) 0 6px, rgba(255,255,255,0.0) 6px 12px);
    }
    .bar[data-type="idle"] {
      --bar-color: rgba(148,163,184,0.35);
      color: rgba(15,23,42,0.75);
      border-style: dashed;
      box-shadow: none;
      background-image: none;
    }
    .bar.is-hidden {
      display: none;
    }
    .bar.is-dim {
      opacity: 0.18;
    }
    .bar.is-match {
      outline: 2px solid rgba(37, 99, 235, 0.65);
      outline-offset: 1px;
      opacity: 1;
    }
    .bar.is-selected {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .bar-label {
      pointer-events: none;
    }
    .legend {
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      font-size: 13px;
      color: var(--muted);
    }
    .legend .swatch {
      width: 12px;
      height: 12px;
      border-radius: 4px;
      border: 1px solid rgba(15,23,42,0.15);
      display: inline-block;
      margin-right: 6px;
      vertical-align: -2px;
    }
    .tooltip {
      position: fixed;
      z-index: 1000;
      max-width: 420px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid rgba(148,163,184,0.30);
      background: rgba(15,23,42,0.92);
      color: #f8fafc;
      box-shadow: 0 10px 24px rgba(15,23,42,0.25);
      font-size: 12px;
      line-height: 1.35;
      pointer-events: none;
      opacity: 0;
      transform: translateY(6px);
      transition: opacity 90ms ease, transform 90ms ease;
    }
    .tooltip.show {
      opacity: 1;
      transform: translateY(0);
    }
    .tooltip .kv {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 4px 10px;
    }
    .tooltip .k {
      color: rgba(226,232,240,0.82);
      white-space: nowrap;
    }
    .tooltip .v {
      color: #f8fafc;
      overflow-wrap: anywhere;
    }
    .details {
      margin-top: 12px;
      padding: 12px;
      display: none;
      gap: 8px 12px;
    }
    .details.show {
      display: grid;
    }
    .details .hdr {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .details .hdr .t {
      font-size: 14px;
      font-weight: 650;
      color: var(--text);
    }
    .details .grid {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px 12px;
      font-size: 13px;
    }
    .details .row {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 8px;
      align-items: baseline;
    }
    .details .row .k {
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .details .row .v {
      color: var(--text);
      overflow-wrap: anywhere;
    }
    @media (max-width: 720px) {
      .details .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="gantt card" style="margin:0">
    <div id="ganttScroll" class="gantt-scroll" data-day-count="$day_count">
      <div id="ganttGrid" class="gantt-grid">
        $grid_cells
      </div>
    </div>
  </div>

  <div id="tooltip" class="tooltip"></div>

  $orders_json
  $inv_json

  <script>
    const ganttScroll = document.getElementById("ganttScroll");
    const tooltip = document.getElementById("tooltip");
    const dayCount = Number(ganttScroll.dataset.dayCount || "1");
    const dayMs = 24 * 60 * 60 * 1000;

    function parseJsonScript(id) {
      const el = document.getElementById(id);
      if (!el) return null;
      try { return JSON.parse(el.textContent || "null"); } catch (e) { return null; }
    }
    const orders = parseJsonScript("ordersData") || [];
    const ordersById = new Map();
    for (const o of orders) {
      if (o && typeof o.c_orderline_id === "number") ordersById.set(String(o.c_orderline_id), o);
    }
    const invData = parseJsonScript("invData");

    function setCssVar(name, value) { document.documentElement.style.setProperty(name, value); }

    // Keep current pixels-per-day in JS so zoom actions are incremental.
    let currentPpd = $px_per_day;
    function getPpd() { return currentPpd; }

    function getLabelWidthPx() {
      const raw = getComputedStyle(document.documentElement).getPropertyValue("--label-w").trim();
      const parsed = parseInt(raw, 10);
      return Number.isFinite(parsed) ? parsed : 160;
    }

    function parseYmdToLocalMidnight(ymd) {
      // Expect "YYYY-MM-DD" (from tick labels). Treat as local date.
      // Escape dollar sign for Python's string.Template (use $$ to render a literal dollar sign in the HTML/JS).
      const m = /^(\\d{4})-(\\d{2})-(\\d{2})$$/.exec(String(ymd || "").trim());
      if (!m) return null;
      const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
      const dt = new Date(y, mo - 1, d, 0, 0, 0, 0);
      return Number.isNaN(dt.getTime()) ? null : dt;
    }

    function getScheduleStartDate() {
      const firstTick = document.querySelector(".tick");
      const fromData = firstTick && firstTick.dataset ? firstTick.dataset.date : null;
      if (fromData) return parseYmdToLocalMidnight(fromData);
      const fallback = document.querySelector(".tick-label");
      return fallback ? parseYmdToLocalMidnight(fallback.textContent) : null;
    }

    function getTodayIndex() {
      const start = getScheduleStartDate();
      if (!start) return 0;
      const now = new Date();
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
      return Math.floor((today.getTime() - start.getTime()) / dayMs);
    }

    function applyPpd(newPpd) {
      currentPpd = newPpd;
      setCssVar("--grid", currentPpd + "px");
      setCssVar("--timeline-width", (dayCount * currentPpd) + "px");

      const w = (dayCount * currentPpd) + "px";
      for (const lane of document.querySelectorAll(".lane, .inv, #axis")) lane.style.width = w;
      for (const tick of document.querySelectorAll(".tick")) tick.style.left = (Number(tick.dataset.day || 0) * currentPpd) + "px";
      for (const bar of document.querySelectorAll(".bar")) {
        bar.style.left = (Number(bar.dataset.leftDays || 0) * currentPpd) + "px";
        bar.style.width = Math.max(1, Number(bar.dataset.durationDays || 0) * currentPpd) + "px";
      }
    }

    function setViewDays(viewDays, anchor) {
      const days = Math.max(1, Number(viewDays) || 7);
      const labelW = getLabelWidthPx();
      const viewportW = Math.max(240, ganttScroll.clientWidth || 0);
      const timelineW = Math.max(80, viewportW - labelW);
      const targetPpd = Math.max(40, Math.min(320, Math.floor(timelineW / days)));

      // Preserve the currently-leftmost day unless we explicitly anchor to today.
      const oldPpd = getPpd();
      const oldLeftDay = oldPpd > 0 ? (ganttScroll.scrollLeft / oldPpd) : 0;

      applyPpd(targetPpd);

      let leftDay = oldLeftDay;
      if (anchor === "today") {
        leftDay = getTodayIndex();
      }
      // Clamp so we don't scroll past the end.
      const maxLeft = Math.max(0, dayCount - days);
      leftDay = Math.max(0, Math.min(maxLeft, leftDay));
      ganttScroll.scrollLeft = leftDay * targetPpd;
    }

    function barDetails(bar) {
      const t = {
        machine: bar.dataset.machine || "", type: bar.dataset.type || "", sku: bar.dataset.sku || "",
        order_id: bar.dataset.orderId || "", quantity: bar.dataset.quantity || "",
        start: bar.dataset.start || "", end: bar.dataset.end || "", duration_h: bar.dataset.durationH || "",
      };
      const o = t.order_id ? ordersById.get(String(t.order_id)) : null;
      return { task: t, order: o };
    }

    function showTooltip(e, rows) {
      tooltip.innerHTML = "";
      const kv = document.createElement("div"); kv.className = "kv";
      for (const [k, v] of rows) {
        const kk = document.createElement("div"); kk.className = "k"; kk.textContent = k;
        const vv = document.createElement("div"); vv.className = "v"; vv.textContent = v;
        kv.appendChild(kk); kv.appendChild(vv);
      }
      tooltip.appendChild(kv); tooltip.classList.add("show"); moveTooltip(e);
    }
    function moveTooltip(e) {
      const pad = 12; const rect = tooltip.getBoundingClientRect();
      let x = e.clientX + 14, y = e.clientY + 14;
      if (x + rect.width + pad > window.innerWidth) x = e.clientX - rect.width - 14;
      if (y + rect.height + pad > window.innerHeight) y = e.clientY - rect.height - 14;
      tooltip.style.left = Math.max(8, x) + "px"; tooltip.style.top = Math.max(8, y) + "px";
    }
    function hideTooltip() { tooltip.classList.remove("show"); }

    // Pan with mouse drag
    let dragging = false, dragX = 0, dragY = 0, dragLeft = 0, dragTop = 0;
    ganttScroll.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || e.target.closest(".bar")) return;
      dragging = true; dragX = e.clientX; dragY = e.clientY;
      dragLeft = ganttScroll.scrollLeft; dragTop = ganttScroll.scrollTop;
      ganttScroll.classList.add("dragging");
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      ganttScroll.scrollLeft = dragLeft - (e.clientX - dragX);
      ganttScroll.scrollTop = dragTop - (e.clientY - dragY);
    });
    window.addEventListener("mouseup", () => { dragging = false; ganttScroll.classList.remove("dragging"); });

    // Tooltip on bars
    for (const bar of document.querySelectorAll(".bar")) {
      bar.addEventListener("mouseenter", (e) => {
        const payload = barDetails(bar); const t = payload.task; const o = payload.order;
        const rows = [["machine", t.machine], ["type", t.type], ["sku", t.sku],
          ["order_id", t.order_id], ["quantity", t.quantity], ["start", t.start], ["end", t.end]];
        if (o) { rows.push(["PO", o.poreference || ""]); rows.push(["deadline", o.deadline || ""]); rows.push(["on_time", String(!!o.on_time)]); }
        showTooltip(e, rows);
      });
      bar.addEventListener("mousemove", (e) => moveTooltip(e));
      bar.addEventListener("mouseleave", () => hideTooltip());
    }

    // === Task selection and postMessage communication ===
    let selectedBar = null;

    function selectBar(bar) {
      // Deselect previous
      if (selectedBar) {
        selectedBar.classList.remove("is-selected");
      }

      // Select new task
      if (bar && bar !== selectedBar) {
        bar.classList.add("is-selected");
        selectedBar = bar;

        // Build task data from data attributes
        const taskData = {
          machine: bar.dataset.machine || "",
          type: bar.dataset.type || "",
          sku: bar.dataset.sku || null,
          orderId: bar.dataset.orderId ? parseInt(bar.dataset.orderId, 10) : null,
          quantity: bar.dataset.quantity ? parseInt(bar.dataset.quantity, 10) : null,
          start: bar.dataset.start || "",
          end: bar.dataset.end || "",
          durationH: bar.dataset.durationH ? parseFloat(bar.dataset.durationH) : null,
        };

        // Send selection message to parent window
        if (window.parent !== window) {
          window.parent.postMessage({
            type: "gantt:task:select",
            payload: taskData
          }, "*");
        }
      } else {
        // Clicking same task deselects it
        selectedBar = null;
        if (window.parent !== window) {
          window.parent.postMessage({
            type: "gantt:task:deselect",
            payload: null
          }, "*");
        }
      }
    }

    // Add click event to all task bars
    for (const bar of document.querySelectorAll(".bar")) {
      bar.addEventListener("click", (e) => {
        e.stopPropagation();
        selectBar(bar);
      });
    }

    // Click on empty area to deselect
    ganttScroll.addEventListener("click", (e) => {
      if (!e.target.closest(".bar")) {
        selectBar(null);
      }
    });

    // ESC key to deselect
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && selectedBar) {
        selectBar(null);
      }
    });

    // Listen for clear selection message from parent
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "gantt:clear-selection") {
        if (selectedBar) {
          selectedBar.classList.remove("is-selected");
          selectedBar = null;
        }
        return;
      }

      // Parent-driven view controls (e.g. 3D/5D/7D buttons in the app).
      if (e.data && e.data.type === "gantt:set-view") {
        const payload = e.data.payload || {};
        setViewDays(payload.viewDays, payload.anchor || "today");
        return;
      }
    });

    // Initialize from query params (supports opening in a new tab with viewDays preset).
    try {
      const params = new URLSearchParams(window.location.search);
      const viewDays = params.get("viewDays");
      const anchor = params.get("anchor") || "today";
      if (viewDays) {
        requestAnimationFrame(() => setViewDays(viewDays, anchor));
      }
    } catch {}

    // Ctrl+wheel zoom
    ganttScroll.addEventListener("wheel", (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const oldPpd = getPpd();
      const delta = e.deltaY > 0 ? -10 : 10;
      const newPpd = Math.max(40, Math.min(320, oldPpd + delta));
      applyPpd(newPpd);
    }, { passive: false });
  </script>
</body>
</html>
"""
    )

    return tpl.substitute(
        {
            "title": title_line,
            "start_str": start_str,
            "chain_start_h": chain_start_h,
            "on_time_rate": f"{on_time_rate:.3f}",
            "total_tardiness_days": f"{total_tardiness_days:.2f}",
            "px_per_day": str(int(px_per_day)),
            "day_count": str(int(day_count)),
            "grid_cells": "".join(grid_cells),
            "orders_json": orders_json,
            "inv_json": inv_json,
            "timeline_w": f"{timeline_width_px}px",
            "grid_px": f"{int(px_per_day)}px",
            "inv_h": f"{inv_height}px",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize schedule_result.json as an HTML Gantt chart (interactive).")
    default_dir = Path(__file__).resolve().parent
    parser.add_argument("--schedule", type=Path, default=default_dir / "schedule_result.json")
    parser.add_argument("--out", type=Path, default=default_dir / "schedule_gantt.html", help="Output html path.")
    parser.add_argument("--px-per-day", type=int, default=120, help="Initial horizontal scale (pixels per day).")
    args = parser.parse_args()

    data = _load_json(args.schedule)
    html = _render_html(data, px_per_day=int(args.px_per_day))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
