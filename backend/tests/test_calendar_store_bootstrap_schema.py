import os
import tempfile
import unittest
from pathlib import Path


class TestCalendarStoreBootstrapSchema(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

        # Reset cached engine/sessionmaker so this test's DATABASE_URL is honored.
        from ai import db as db_mod

        db_mod._engine = None  # type: ignore[attr-defined]
        db_mod._SessionLocal = None  # type: ignore[attr-defined]

        # Intentionally do NOT create tables here; calendar_store should bootstrap.

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_holiday_bootstraps_schema_if_missing(self) -> None:
        from ai.calendar_store import add_holiday, load_calendar

        add_holiday(name="boot", start="2026-02-01", end="2026-02-01")
        cal = load_calendar()
        self.assertEqual(len(cal.get("holidays") or []), 1)


if __name__ == "__main__":
    unittest.main()

