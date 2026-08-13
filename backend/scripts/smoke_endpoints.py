from __future__ import annotations

import json
from pathlib import Path
import sys

from dotenv import load_dotenv
from fastapi.testclient import TestClient


def _short(obj) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:240] + ("…" if len(s) > 240 else "")


def _redact_payload(path: str, payload: object) -> object:
    if not path.startswith("/api/erp/"):
        return payload
    if isinstance(payload, dict):
        # /api/erp/sync response is already safe (counts + paths).
        if "orders_count" in payload or "inventory_count" in payload:
            return {
                "success": payload.get("success"),
                "orders_count": payload.get("orders_count"),
                "inventory_count": payload.get("inventory_count"),
                "orders_path": payload.get("orders_path"),
                "inventory_path": payload.get("inventory_path"),
                "timestamp": payload.get("timestamp"),
            }
        data = payload.get("data")
        if isinstance(data, list):
            return {"timestamp": payload.get("timestamp"), "count": len(data)}
        return {"detail": payload.get("detail") or payload.get("message") or payload.get("error") or "unknown"}
    return {"detail": str(payload)[:120]}


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    load_dotenv()

    from ai.api import app, REPO_ROOT, ERP_INVENTORY_PATH, ERP_ORDERS_PATH  # noqa: WPS433

    client = TestClient(app)

    def check(method: str, path: str) -> tuple[int, object]:
        resp = client.request(method, path)
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, resp.text
        return resp.status_code, {"content_type": resp.headers.get("content-type"), "bytes": len(resp.content)}

    checks = [
        ("GET", "/health"),
        ("GET", "/api/schedule"),
        ("GET", "/api/schedule/kpi"),
        ("HEAD", "/api/schedule/gantt"),
        ("GET", "/api/schedule/gantt"),
        ("GET", "/api/schedule/containers"),
        ("GET", "/api/calendar/downtime"),
        ("GET", "/api/erp/orders?isTest=true"),
        ("GET", "/api/erp/inventory?isTest=true"),
        ("GET", "/api/erp/demand-history?isTest=true"),
        ("POST", "/api/erp/sync?isTest=true"),
    ]

    ok = True
    for method, path in checks:
        status, payload = check(method, path)
        print(f"{method:4} {path:35} -> {status} {_short(_redact_payload(path, payload))}")
        if method == "POST" and path.startswith("/api/erp/sync") and status == 200:
            # Best-effort check that snapshots were written.
            repo_root = Path(REPO_ROOT)
            expected = [
                repo_root / Path(str(ERP_ORDERS_PATH)).relative_to(repo_root),
                repo_root / Path(str(ERP_INVENTORY_PATH)).relative_to(repo_root),
            ]
            for p in expected:
                print(f"  wrote: {p} exists={p.exists()}")

        if status >= 500:
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
