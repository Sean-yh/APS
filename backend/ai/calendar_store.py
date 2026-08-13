from __future__ import annotations

import re
from datetime import datetime
from typing import Any, TypedDict

CALENDAR_KEY = "production_calendar"

# All machines across 3 independent lines (see 问题.md).
VALID_MACHINE_IDS = [
    "ROTARY-1",
    "LABEL-1",
    "LABEL-2",
    "ROTARY-2",
    "LABEL-3",
    "LABEL-5",
    "ROTARY-3",
    "LABEL-4",
    "LABEL-6",
]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}$")


class HolidayEntry(TypedDict):
    name: str
    start: str  # YYYY-MM-DD (inclusive)
    end: str  # YYYY-MM-DD (inclusive)


class MaintenanceEntry(TypedDict):
    machine_id: str
    reason: str
    start: str  # YYYY-MM-DDTHH:MM
    end: str  # YYYY-MM-DDTHH:MM


def _require_db() -> None:
    # The user explicitly wants downtime calendar to be DB-only (no local JSON).
    from .db_store import db_enabled

    if not db_enabled():
        raise RuntimeError("DATABASE_URL 未配置：停机/假期日历已改为仅数据库存储，必须配置数据库。")


def load_calendar() -> dict[str, Any]:
    """Load downtime calendar from DB only."""
    _require_db()

    from .db_store import get_document_payload

    default: dict[str, Any] = {"holidays": [], "maintenance": []}
    row = get_document_payload(CALENDAR_KEY)
    if row is None:
        return dict(default)

    payload, _ts = row
    if not isinstance(payload, dict):
        return dict(default)

    data = dict(payload)
    if not isinstance(data.get("holidays"), list):
        data["holidays"] = []
    if not isinstance(data.get("maintenance"), list):
        data["maintenance"] = []
    return data


def save_calendar(data: dict[str, Any]) -> None:
    """Persist downtime calendar to DB only."""
    _require_db()

    from .db_store import upsert_document_payload

    ok = upsert_document_payload(CALENDAR_KEY, data)
    if not ok:
        # Most common cause: DB is configured but schema isn't migrated yet.
        # Best-effort: create missing tables then retry once.
        try:
            from .db import ensure_schema

            ensure_schema()
        except Exception:
            pass

        ok2 = upsert_document_payload(CALENDAR_KEY, data)
        if not ok2:
            raise RuntimeError(
                "写入数据库失败：production_calendar 未能保存。"
                "请检查 DATABASE_URL、数据库权限（可写/可建表）、并确保已执行 Alembic 迁移（`alembic upgrade head`）。"
            )


def _normalize_date(s: str) -> str:
    s = str(s).strip()
    if not _DATE_RE.match(s):
        raise ValueError(f"Invalid date format: {s!r}, expected YYYY-MM-DD")
    dt = datetime.strptime(s, "%Y-%m-%d")
    return dt.date().isoformat()


def _normalize_datetime(s: str) -> str:
    s = str(s).strip()
    if not _DATETIME_RE.match(s):
        raise ValueError(f"Invalid datetime format: {s!r}, expected YYYY-MM-DDTHH:MM")
    s = s.replace(" ", "T")
    dt = datetime.fromisoformat(s)
    return dt.strftime("%Y-%m-%dT%H:%M")


def add_holiday(
    *,
    name: str,
    start: str,
    end: str,
) -> tuple[HolidayEntry, int, bool]:
    """Add a holiday entry (all machines down) with simple de-duplication.

    Returns:
        (entry, index, existed)
    """
    name = str(name).strip()
    if not name:
        raise ValueError("Holiday name cannot be empty")

    start_norm = _normalize_date(start)
    end_norm = _normalize_date(end)
    if start_norm > end_norm:
        raise ValueError(f"Holiday start must be <= end, got {start_norm} > {end_norm}")

    entry: HolidayEntry = {"name": name, "start": start_norm, "end": end_norm}

    calendar = load_calendar()
    holidays = calendar.setdefault("holidays", [])
    if not isinstance(holidays, list):
        holidays = []
        calendar["holidays"] = holidays

    for i, existing in enumerate(holidays):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("start", "")).strip() == start_norm and str(existing.get("end", "")).strip() == end_norm:
            return (
                HolidayEntry(
                    name=str(existing.get("name") or name),
                    start=start_norm,
                    end=end_norm,
                ),
                i,
                True,
            )

    holidays.append(entry)
    save_calendar(calendar)
    return entry, len(holidays) - 1, False


def add_maintenance(
    *,
    machine_id: str,
    reason: str,
    start: str,
    end: str,
) -> tuple[MaintenanceEntry, int, bool]:
    """Add a maintenance entry with simple de-duplication.

    Returns:
        (entry, index, existed)
    """
    machine_id = str(machine_id).strip()
    if machine_id not in VALID_MACHINE_IDS:
        raise ValueError(f"Invalid machine_id: {machine_id!r}, valid: {', '.join(VALID_MACHINE_IDS)}")

    reason = str(reason).strip()
    if not reason:
        raise ValueError("Maintenance reason cannot be empty")

    start_norm = _normalize_datetime(start)
    end_norm = _normalize_datetime(end)
    if start_norm >= end_norm:
        raise ValueError(f"Maintenance start must be < end, got {start_norm} >= {end_norm}")

    entry: MaintenanceEntry = {
        "machine_id": machine_id,
        "reason": reason,
        "start": start_norm,
        "end": end_norm,
    }

    calendar = load_calendar()
    maintenance = calendar.setdefault("maintenance", [])
    if not isinstance(maintenance, list):
        maintenance = []
        calendar["maintenance"] = maintenance

    for i, existing in enumerate(maintenance):
        if not isinstance(existing, dict):
            continue
        if (
            str(existing.get("machine_id", "")).strip() == machine_id
            and str(existing.get("start", "")).strip().replace(" ", "T") == start_norm
            and str(existing.get("end", "")).strip().replace(" ", "T") == end_norm
        ):
            return (
                MaintenanceEntry(
                    machine_id=machine_id,
                    reason=str(existing.get("reason") or reason),
                    start=start_norm,
                    end=end_norm,
                ),
                i,
                True,
            )

    maintenance.append(entry)
    save_calendar(calendar)
    return entry, len(maintenance) - 1, False


def delete_holiday(*, index: int) -> HolidayEntry:
    calendar = load_calendar()
    holidays = calendar.get("holidays", [])
    if not isinstance(holidays, list):
        raise IndexError("Holiday list is missing")
    if index < 0 or index >= len(holidays):
        raise IndexError(f"Holiday index out of range: {index}")
    deleted = holidays.pop(index)
    save_calendar(calendar)
    if isinstance(deleted, dict):
        return HolidayEntry(
            name=str(deleted.get("name") or ""),
            start=str(deleted.get("start") or ""),
            end=str(deleted.get("end") or ""),
        )
    return HolidayEntry(name=str(deleted), start="", end="")


def delete_maintenance(*, index: int) -> MaintenanceEntry:
    calendar = load_calendar()
    maintenance = calendar.get("maintenance", [])
    if not isinstance(maintenance, list):
        raise IndexError("Maintenance list is missing")
    if index < 0 or index >= len(maintenance):
        raise IndexError(f"Maintenance index out of range: {index}")
    deleted = maintenance.pop(index)
    save_calendar(calendar)
    if isinstance(deleted, dict):
        return MaintenanceEntry(
            machine_id=str(deleted.get("machine_id") or ""),
            reason=str(deleted.get("reason") or ""),
            start=str(deleted.get("start") or ""),
            end=str(deleted.get("end") or ""),
        )
    return MaintenanceEntry(machine_id="", reason=str(deleted), start="", end="")
