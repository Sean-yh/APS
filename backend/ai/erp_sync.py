"""ERP数据同步模块，供排产工具调用。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .data import PROCESS_DIR
from .erp_client import GxErpClient, GxErpConfig

ERP_ORDERS_PATH = PROCESS_DIR / "orders_erp.json"
ERP_INVENTORY_PATH = PROCESS_DIR / "inventory_erp.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    """原子写入JSON文件。"""
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


def sync_erp_data(is_test: bool | None = None) -> dict[str, Any]:
    """从ERP拉取最新订单和库存数据并保存到本地。

    Args:
        is_test: 是否使用测试模式。None则使用环境变量配置。

    Returns:
        包含同步结果的字典：success, orders_count, inventory_count, timestamp

    Raises:
        ValueError: ERP配置缺失
        RuntimeError: ERP请求失败
    """
    cfg = GxErpConfig.from_env()
    if not cfg.api_url:
        raise ValueError("GX_ERP_API_URL 未配置")
    if not cfg.token:
        raise ValueError("GX_ERP_TOKEN 未配置")

    client = GxErpClient(cfg)

    # 拉取数据
    orders_payload = client.orders_payload(is_test=is_test)
    inventory_payload = client.inventory_payload(is_test=is_test)

    # 写入文件
    _atomic_write_json(ERP_ORDERS_PATH, orders_payload)
    _atomic_write_json(ERP_INVENTORY_PATH, inventory_payload)

    # Persist to DB as well (Railway FS is ephemeral).
    try:
        from .db_store import upsert_document_payload

        upsert_document_payload("erp_orders", orders_payload)
        upsert_document_payload("erp_inventory", inventory_payload)
    except Exception:
        pass

    return {
        "success": True,
        "orders_count": len(orders_payload.get("data") or []),
        "inventory_count": len(inventory_payload.get("data") or []),
        "timestamp": orders_payload.get("timestamp"),
    }
