import os
import tempfile
import unittest
from pathlib import Path


class TestCalendarStoreDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

        # Reset cached engine/sessionmaker so this test's DATABASE_URL is honored.
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

    def test_load_calendar_defaults_when_missing(self) -> None:
        from ai.calendar_store import load_calendar

        cal = load_calendar()
        self.assertEqual(cal.get("holidays"), [])
        self.assertEqual(cal.get("maintenance"), [])

    def test_add_holiday_persists_to_db(self) -> None:
        from ai.calendar_store import add_holiday, load_calendar

        add_holiday(name="test", start="2026-01-01", end="2026-01-01")
        cal = load_calendar()
        self.assertEqual(len(cal.get("holidays") or []), 1)


if __name__ == "__main__":
    unittest.main()

