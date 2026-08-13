from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _is_success_payload(doc: Any) -> tuple[bool, str]:
    if isinstance(doc, list):
        return True, f"list(count={len(doc)})"
    if isinstance(doc, dict):
        if doc.get("success") is False:
            return False, f"success=false error={doc.get('error')} message={doc.get('message')}"
        if isinstance(doc.get("data"), list):
            return True, f"dict(data_count={len(doc['data'])})"
        return True, "dict"
    return False, f"type={type(doc)}"


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    load_dotenv()

    api_url = str(os.getenv("GX_ERP_API_URL") or "").strip().rstrip("/")
    token = str(os.getenv("GX_ERP_TOKEN") or "").strip()
    if not api_url or not token:
        print("Missing GX_ERP_API_URL or GX_ERP_TOKEN in env/.env")
        return 2

    import requests  # noqa: WPS433

    url = f"{api_url}/orders"
    params = {"isTest": "true"}

    schemes: list[tuple[str, dict[str, str]]] = [
        ("Authorization: Bearer", {"Authorization": f"Bearer {token}"}),
        ("Authorization: raw", {"Authorization": token}),
        ("X-Token", {"X-Token": token}),
        ("X-Access-Token", {"X-Access-Token": token}),
        ("token", {"token": token}),
        ("Authorization: Token", {"Authorization": f"Token {token}"}),
        ("Authorization: token", {"Authorization": f"token {token}"}),
    ]

    any_ok = False
    for name, headers in schemes:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            ok = resp.ok
            try:
                doc = resp.json()
            except Exception:
                doc = resp.text
            payload_ok, summary = _is_success_payload(doc)
            status = f"http={resp.status_code} ok={ok} payload_ok={payload_ok}"
            print(f"{name:22} -> {status} {summary}")
            if payload_ok:
                any_ok = True
        except Exception as e:
            print(f"{name:22} -> error {type(e).__name__}: {e}")

    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

