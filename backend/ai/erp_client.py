from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None  # type: ignore


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _join_url(base: str, suffix: str) -> str:
    base = str(base or "").strip()
    suffix = str(suffix or "").strip()
    if not base:
        return suffix
    if not suffix:
        return base
    return base.rstrip("/") + "/" + suffix.lstrip("/")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_list_payload(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, dict):
        # Common ERP error envelope (observed: HTTP 200 with success=false)
        if doc.get("success") is False:
            err = str(doc.get("error") or "ERP_ERROR")
            msg = str(doc.get("message") or "").strip()
            ts = str(doc.get("timestamp") or "").strip()
            extra = f" ({ts})" if ts else ""
            raise RuntimeError(f"ERP error: {err}: {msg}{extra}".strip())

    if isinstance(doc, list):
        rows = doc
    elif isinstance(doc, dict) and isinstance(doc.get("data"), list):
        rows = doc["data"]
    else:
        raise TypeError(f"ERP response must be list or dict{{data:list}}, got {type(doc)}")

    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def _normalize_int(v: Any, *, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _normalize_orders(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "c_orderline_id": _normalize_int(row.get("c_orderline_id")),
                "poreference": str(row.get("poreference") or "").strip(),
                "sku": str(row.get("sku") or "").strip(),
                "quantity": _normalize_int(row.get("quantity")),
                "duedate": str(row.get("duedate") or "").strip(),
                "name": str(row.get("name") or "").strip() or None,
                "remark": str(row.get("remark") or "").strip() or None,
            }
        )
    return normalized


def _normalize_inventory(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "materialcode": str(row.get("materialcode") or "").strip(),
                "quantity": _normalize_int(row.get("quantity")),
            }
        )
    return normalized


@dataclass(frozen=True)
class GxErpConfig:
    api_url: str
    token: str | None
    is_test: bool = False
    timeout_s: float = 60.0

    @staticmethod
    def from_env() -> "GxErpConfig":
        return GxErpConfig(
            api_url=str(os.getenv("GX_ERP_API_URL") or "").strip(),
            token=str(os.getenv("GX_ERP_TOKEN") or "").strip() or None,
            is_test=_bool_env("GX_ERP_IS_TEST", False),
            timeout_s=_float_env("GX_ERP_TIMEOUT_S", 60.0),
        )


class GxErpClient:
    def __init__(self, config: GxErpConfig, *, session: requests.Session | None = None) -> None:
        if requests is None:  # pragma: no cover
            raise RuntimeError("Missing dependency: requests. Install with `pip install -r backend/requirements.txt`.")
        self.config = config
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        token = self.config.token
        if not token:
            return {}
        # GX ERP expects raw token in Authorization header (no "Bearer " prefix).
        return {
            "Authorization": token,
        }

    def _get(self, path: str, *, is_test: bool | None = None) -> Any:
        if not self.config.api_url:
            raise ValueError("GX_ERP_API_URL is not configured")

        params: dict[str, Any] = {}
        if is_test is None:
            is_test = self.config.is_test
        if is_test:
            params["isTest"] = "true"

        url = _join_url(self.config.api_url, path)
        resp = self._session.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=self.config.timeout_s,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_orders(self, *, is_test: bool | None = None) -> list[dict[str, Any]]:
        doc = self._get("orders", is_test=is_test)
        rows = _as_list_payload(doc)
        return _normalize_orders(rows)

    def fetch_inventory(self, *, is_test: bool | None = None) -> list[dict[str, Any]]:
        doc = self._get("inventory", is_test=is_test)
        rows = _as_list_payload(doc)
        return _normalize_inventory(rows)

    def fetch_demand_history(self, *, is_test: bool | None = None) -> list[dict[str, Any]]:
        doc = self._get("demand-history", is_test=is_test)
        return _as_list_payload(doc)

    def orders_payload(self, *, is_test: bool | None = None) -> dict[str, Any]:
        return {"timestamp": _utc_iso(), "data": self.fetch_orders(is_test=is_test)}

    def inventory_payload(self, *, is_test: bool | None = None) -> dict[str, Any]:
        return {"timestamp": _utc_iso(), "data": self.fetch_inventory(is_test=is_test)}
