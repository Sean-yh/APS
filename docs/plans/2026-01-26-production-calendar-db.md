# Production Calendar in DB Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Store downtime calendar (holidays + maintenance) in the database only (no `production_calendar.json`), and require DB to be configured.

**Architecture:** Keep using the existing `documents` table (`Document` model) with key `production_calendar` as the canonical source of truth. Remove all file IO / file-path plumbing for production calendar; update scheduler + CLI scripts to read calendar via `ai.calendar_store`.

**Tech Stack:** Python, SQLAlchemy, existing `ai.db_store` Document blob storage, unittest.

## Task 1: Calendar Store: DB-only load/save

**Files:**
- Modify: `backend/ai/calendar_store.py`
- Test: `backend/tests/test_calendar_store_db.py`

**Step 1: Write the failing test**

Create `backend/tests/test_calendar_store_db.py`:
```python
import os
import tempfile
import unittest
from pathlib import Path


class TestCalendarStoreDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

        # Reset cached engine/sessionmaker for this process.
        from ai import db as db_mod
        db_mod._engine = None
        db_mod._SessionLocal = None

        from ai.db import get_engine
        from ai.models import Base

        engine = get_engine()
        self.assertIsNotNone(engine)
        Base.metadata.create_all(engine)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_calendar_defaults_when_missing(self):
        from ai.calendar_store import load_calendar
        cal = load_calendar()
        self.assertEqual(cal.get("holidays"), [])
        self.assertEqual(cal.get("maintenance"), [])

    def test_add_holiday_persists_to_db(self):
        from ai.calendar_store import add_holiday, load_calendar
        add_holiday(name="test", start="2026-01-01", end="2026-01-01")
        cal = load_calendar()
        self.assertEqual(len(cal.get("holidays") or []), 1)
```

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_calendar_store_db -q`

Expected: FAIL (current calendar store still depends on local file path / does not enforce DB-only behavior).

**Step 3: Write minimal implementation**

In `backend/ai/calendar_store.py`:
- Remove `CALENDAR_PATH` and all file read/write code.
- `load_calendar()` reads `production_calendar` from `ai.db_store.get_document_payload`.
- `save_calendar()` upserts via `ai.db_store.upsert_document_payload`.
- If DB not configured, raise `RuntimeError("DATABASE_URL is not configured")`.
- Keep the existing validation / normalization helpers and add/delete functions; they should call the DB-only `load_calendar/save_calendar`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_calendar_store_db -q`

Expected: PASS.

## Task 2: Remove calendar file usage in scheduler + scripts

**Files:**
- Modify: `backend/process/generate_schedule.py`
- Modify: `backend/process/visualize_schedule.py`
- Modify: `backend/ai/scheduler.py`
- Modify: `backend/process/line_scheduler.py` (import stays but `_load_production_calendar` becomes DB-backed)
- Modify: `backend/ai/persistence.py` (remove `production_calendar.json` sync)
- Delete: `backend/process/production_calendar.json`

**Step 1: Write the failing smoke regression**

Update `backend/tests/test_tools_smoke.py` to:
- Stop creating `production_calendar.json` in the sandbox process dir
- Provide a sandbox SQLite `DATABASE_URL`
- Create DB schema and seed an empty `production_calendar` document in setUp or inside the test

Run: `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_tools_smoke -q`

Expected: FAIL until scripts no longer read `production_calendar.json`.

**Step 2: Implement minimal code changes**

- Replace file reads of `production_calendar.json` with `ai.calendar_store.load_calendar()`.
- Keep data shape stable: `{"holidays": [], "maintenance": []}`.
- In API/tool responses, if DB is missing, return a clear error message (but DB is expected to be configured).

**Step 3: Run smoke tests**

Run: `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_tools_smoke -q`

Expected: PASS.

## Task 3: Full test suite

**Step 1: Run all backend tests**

Run: `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -q`

Expected: PASS.

## Task 4: Cleanup + docs

**Files:**
- Modify: `README.md` (remove references to `production_calendar.json` if present; mention DB is required for downtime calendar)

**Step 1: Update docs**
- Ensure no docs instruct editing `backend/process/production_calendar.json`.

**Step 2: Final verification**
- `rg -n "production_calendar\\.json" backend | head`
- Backend tests still green.

