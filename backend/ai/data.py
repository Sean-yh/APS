from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict


REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"

DEFAULT_ORDERS_PATH = PROCESS_DIR / "orders_erp.json"
DEFAULT_INVENTORY_PATH = PROCESS_DIR / "inventory_erp.json"
DEFAULT_SCHEDULE_PATH = PROCESS_DIR / "schedule_result.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schedule(path: Path = DEFAULT_SCHEDULE_PATH) -> dict[str, Any]:
    try:
        doc = load_json(path)
        if not isinstance(doc, dict):
            raise TypeError(f"{path}: expected schedule json to be a dict, got {type(doc)}")
        return doc
    except Exception:
        # Railway FS is ephemeral; fall back to DB-backed document for the default schedule.
        try:
            if path.resolve() == DEFAULT_SCHEDULE_PATH.resolve():
                from .db_store import db_enabled, get_document_payload  # local import to avoid hard DB dependency

                if db_enabled():
                    row = get_document_payload("schedule_result")
                    if row is not None:
                        payload, _ts = row
                        if isinstance(payload, dict):
                            return payload
        except Exception:
            pass
        raise


def normalize_query(q: str) -> str:
    return str(q or "").strip()


def match_orders(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = normalize_query(query)
    if not q:
        return []

    if q.isdigit():
        oid = int(q)
        out = [r for r in rows if int(r.get("c_orderline_id") or -1) == oid]
        return out

    q_lower = q.lower()
    out: list[dict[str, Any]] = []
    for r in rows:
        po = str(r.get("poreference") or "")
        name = str(r.get("name") or "")
        if q_lower in po.lower() or q_lower in name.lower():
            out.append(r)
    return out


def order_brief(row: dict[str, Any]) -> str:
    oid = row.get("c_orderline_id")
    po = row.get("poreference") or ""
    sku = row.get("sku") or ""
    qty = row.get("quantity") or ""
    due = row.get("due") or row.get("duedate") or ""
    return f"{oid} | {po} | {sku} | qty={qty} | due={due}"


# =============================================================================
# 客户代码提取
# =============================================================================


def extract_customer_code(row: dict[str, Any]) -> str:
    """从订单 name 字段提取客户代码。

    客户代码定义为 name 字段第一个 '-' 之前的部分。
    例如: "DE#-DSB700c-S12G9C-IL1" -> "DE#"

    Args:
        row: 订单行数据

    Returns:
        客户代码字符串，提取失败时返回空字符串
    """
    name = str(row.get("name") or "")
    if "-" in name:
        return name.split("-", 1)[0].strip()
    return name.strip() if name else ""


def match_orders_by_customer(
    rows: list[dict[str, Any]], customer_code: str
) -> list[dict[str, Any]]:
    """按客户代码筛选订单。

    Args:
        rows: 订单列表
        customer_code: 客户代码（如 "SQ#", "DE#", "SEC"）

    Returns:
        该客户的所有订单列表
    """
    code = normalize_query(customer_code).upper()
    if not code:
        return []
    return [r for r in rows if extract_customer_code(r).upper() == code]


# =============================================================================
# Container 聚合
# =============================================================================


class ContainerInfo(TypedDict):
    """Container（货柜）信息结构。

    重要概念：
    - Container ID = poreference（PO参考号）
    - 同一 poreference 的订单属于同一个 Container
    - latest_end 是该 Container 中最后一个订单的**生产完成时间**（LABEL机台下线时间）
    - 货柜必须等所有订单都生产完成后才能交付
    """
    container_id: str  # poreference
    customer_code: str  # 客户代码
    orders: list[dict[str, Any]]  # 包含的订单列表
    total_quantity: int  # 总数量
    earliest_due: str | None  # 最早交期
    deadline: str | None  # 最早 deadline（用于 Container 交期）
    latest_end: str | None  # 货柜可交付时间（= 最后一个订单的生产完成时间）
    on_time: bool  # 是否准时
    expired_before_start: bool  # 是否在排产开始前已过期（按最早 deadline）
    lateness_h: float  # 延迟小时数（取所有订单中最大值）


def aggregate_containers(orders: list[dict[str, Any]]) -> list[ContainerInfo]:
    """将订单聚合为 Container（货柜）列表。

    Container ID = poreference（PO参考号）
    货柜可交付时间 = 该 Container 中**最后一个订单**的生产完成时间（LABEL机台下线）

    注意：货柜必须等所有订单都生产完成后才能交付，因此货柜的可交付时间
    取决于最慢完成的那个订单。

    Args:
        orders: 排产结果中的订单列表（包含 end、on_time、lateness_h 等字段）

    Returns:
        按 poreference 聚合的 Container 列表
    """
    from datetime import datetime

    def _parse_dt(v: Any) -> datetime | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
        try:
            return datetime.strptime(s, "%d/%m/%Y %H:%M")
        except ValueError:
            return None

    # 按 poreference 分组
    groups: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        po = str(order.get("poreference") or "").strip()
        if not po:
            po = f"__NO_POREFERENCE__{order.get('c_orderline_id')}"
        groups.setdefault(po, []).append(order)

    # 聚合每个 Container
    containers: list[ContainerInfo] = []
    for container_id, group_orders in groups.items():
        total_qty = sum(int(o.get("quantity") or 0) for o in group_orders)

        # 提取客户代码（取第一个订单的）
        customer_code = extract_customer_code(group_orders[0]) if group_orders else ""

        # Container 完成时间 = 该 Container 中最后一个订单的完成时间
        due_vals = [_parse_dt(o.get("due") or o.get("duedate")) for o in group_orders]
        due_vals = [d for d in due_vals if d is not None]
        earliest_due_dt = min(due_vals) if due_vals else None

        deadline_vals = [_parse_dt(o.get("deadline")) for o in group_orders]
        deadline_vals = [d for d in deadline_vals if d is not None]
        min_deadline_dt = min(deadline_vals) if deadline_vals else None

        end_vals = [_parse_dt(o.get("end")) for o in group_orders]
        end_vals = [d for d in end_vals if d is not None]
        latest_end_dt = max(end_vals) if end_vals else None

        # Container 交期规则：取该 Container 内最早 deadline（更保守）。
        container_expired = any(bool(o.get("expired_before_start")) for o in group_orders)

        if latest_end_dt is not None and min_deadline_dt is not None:
            container_lateness_h = max(0.0, (latest_end_dt - min_deadline_dt).total_seconds() / 3600.0)
            container_on_time = (not container_expired) and container_lateness_h <= 1e-9
        else:
            container_on_time = (not container_expired) and all(o.get("on_time", False) for o in group_orders)
            container_lateness_h = max((float(o.get("lateness_h") or 0) for o in group_orders), default=0.0)

        earliest_due = earliest_due_dt.isoformat() if earliest_due_dt is not None else None
        deadline = min_deadline_dt.isoformat() if min_deadline_dt is not None else None
        latest_end = latest_end_dt.isoformat() if latest_end_dt is not None else None

        containers.append(
            ContainerInfo(
                container_id=container_id,
                customer_code=customer_code,
                orders=group_orders,
                total_quantity=total_qty,
                earliest_due=earliest_due,
                deadline=deadline,
                latest_end=latest_end,
                on_time=container_on_time,
                expired_before_start=container_expired,
                lateness_h=container_lateness_h,
            )
        )

    # 按 container_id 排序
    containers.sort(key=lambda c: c["container_id"])
    return containers


def get_container_for_order(
    order_id: int, orders: list[dict[str, Any]]
) -> ContainerInfo | None:
    """根据订单 ID 查找其所属的 Container（货柜）。

    Args:
        order_id: 订单的 c_orderline_id
        orders: 订单列表

    Returns:
        该订单所属的 Container 信息，找不到时返回 None
    """
    # 先找到目标订单的 poreference
    target_poreference = None
    for order in orders:
        if int(order.get("c_orderline_id") or -1) == order_id:
            target_poreference = str(order.get("poreference") or "").strip()
            break

    if not target_poreference:
        return None

    # 聚合所有 Container，找到目标
    containers = aggregate_containers(orders)
    for container in containers:
        if container["container_id"] == target_poreference:
            return container

    return None


def get_orders_in_container(
    container_ref: str, orders: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """根据 Container ID（poreference）获取该货柜内的所有订单。

    Args:
        container_ref: Container ID（即 poreference）
        orders: 订单列表

    Returns:
        该货柜内的所有订单列表
    """
    ref = container_ref.strip()
    return [
        o for o in orders
        if str(o.get("poreference") or "").strip() == ref
    ]
