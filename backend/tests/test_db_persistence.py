import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


# Force a sandbox SQLite DB for tests (do not rely on developer's .env / Railway).
_ORIG_DATABASE_URL = os.environ.get("DATABASE_URL")


class TestDbPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls._tmp.name) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

        # Ensure tables exist.
        # Tests patch DATABASE_URL at import time, but the app caches the SQLAlchemy engine
        # globally; reset it here so this test always uses its sandbox SQLite DB.
        from ai import db as db_mod

        db_mod._engine = None  # type: ignore[attr-defined]
        db_mod._SessionLocal = None  # type: ignore[attr-defined]

        from ai.db import get_engine
        from ai.models import Base

        engine = get_engine()
        assert engine is not None
        cls._engine = engine
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    @classmethod
    def tearDownClass(cls):
        try:
            engine = getattr(cls, "_engine", None)
            if engine is not None:
                engine.dispose()
        except Exception:
            pass
        # Reset global engine/sessionmaker so unittest doesn't keep SQLite connections
        # alive until interpreter shutdown (avoids ResourceWarning noise).
        try:
            from ai import db as db_mod

            db_mod._engine = None  # type: ignore[attr-defined]
            db_mod._SessionLocal = None  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            tmp = getattr(cls, "_tmp", None)
            if tmp is not None:
                tmp.cleanup()
        except Exception:
            pass
        try:
            if _ORIG_DATABASE_URL is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = _ORIG_DATABASE_URL
        except Exception:
            pass

    def test_chat_persists_messages(self):
        from fastapi.testclient import TestClient
        from ai.api import app
        from ai.db_store import list_chat_messages

        with patch("ai.api.agenerate_reply", return_value="hello"):
            with TestClient(app) as client:
                session_id = "test_session_sync"
                r = client.post("/api/chat", json={"message": "hi", "session_id": session_id})
                self.assertEqual(r.status_code, 200)

        msgs = list_chat_messages("test_session_sync", limit=50)
        self.assertGreaterEqual(len(msgs), 2)
        self.assertEqual(msgs[-2]["role"], "user")
        self.assertEqual(msgs[-2]["content"], "hi")
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertEqual(msgs[-1]["content"], "hello")

    def test_chat_stream_persists_assistant(self):
        from fastapi.testclient import TestClient
        from ai.api import app
        from ai.db_store import list_chat_messages

        async def fake_events(_message: str, thread_id: str = "default"):
            # Simulate the agent yielding incremental content then done.
            yield {"type": "content", "content": "A"}
            yield {"type": "content", "content": "B"}
            yield {"type": "done"}

        with patch("ai.api.astream_agent_events", side_effect=fake_events):
            with TestClient(app) as client:
                session_id = "test_session_stream"
                with client.stream("POST", "/api/chat/stream", json={"message": "hi", "session_id": session_id}) as resp:
                    self.assertEqual(resp.status_code, 200)
                    # Consume stream fully so the server-side generator can persist.
                    _ = list(resp.iter_text())

        msgs = list_chat_messages("test_session_stream", limit=50)
        self.assertGreaterEqual(len(msgs), 2)
        self.assertEqual(msgs[-2]["role"], "user")
        self.assertEqual(msgs[-2]["content"], "hi")
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertIn("AB", msgs[-1]["content"].replace("\n", ""))

    def test_schedule_persists_and_bootstraps_process_cache(self):
        from fastapi.testclient import TestClient
        from ai.api import app
        from ai.db_store import get_document_payload
        from ai.api import PROCESS_DIR as PROCESS_DIR_PATH

        import process.multiline as ml

        # Back up current artifacts (local dev files; gitignored).
        schedule_path = PROCESS_DIR_PATH / "schedule_result.json"
        gantt_path = PROCESS_DIR_PATH / "schedule_gantt.html"
        old_schedule = schedule_path.read_text(encoding="utf-8") if schedule_path.exists() else None
        old_gantt = gantt_path.read_text(encoding="utf-8") if gantt_path.exists() else None

        def fake_generate_all_lines(*args, **kwargs):
            combined = {
                "meta": {"line": "ALL", "start_time": "2026-01-25T00:00:00", "horizon_h": 1},
                "kpi": {"orders_total": 0, "containers_total": 0},
                "machines": {},
                "orders": [],
            }
            return {"L1": combined}, combined

        def fake_write_schedule_artifacts(*, schedule, schedule_path, gantt_path, px_per_day=120):
            schedule_path.parent.mkdir(parents=True, exist_ok=True)
            schedule_path.write_text(__import__("json").dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")
            gantt_path.write_text("<html>ok</html>", encoding="utf-8")

        try:
            with patch.object(ml, "generate_all_lines", side_effect=fake_generate_all_lines), patch.object(
                ml, "write_schedule_artifacts", side_effect=fake_write_schedule_artifacts
            ):
                with TestClient(app) as client:
                    r = client.post("/api/schedule/regenerate?also_write_per_line=false")
                    self.assertEqual(r.status_code, 200)
                    self.assertTrue(r.json().get("success"))

            row = get_document_payload("schedule_result")
            self.assertIsNotNone(row)
            payload, _ts = row
            self.assertEqual((payload.get("meta") or {}).get("line"), "ALL")

            # Remove files to simulate Railway restart, then start a new app instance
            # (TestClient triggers FastAPI startup events).
            if schedule_path.exists():
                schedule_path.unlink()
            if gantt_path.exists():
                gantt_path.unlink()

            with TestClient(app):
                # Startup restores process cache from DB in a background thread.
                import time

                deadline = time.time() + 2.0
                while time.time() < deadline:
                    if schedule_path.exists() and gantt_path.exists():
                        break
                    time.sleep(0.05)

                self.assertTrue(schedule_path.exists())
                self.assertTrue(gantt_path.exists())
        finally:
            # Restore original dev artifacts.
            if old_schedule is not None:
                schedule_path.write_text(old_schedule, encoding="utf-8")
            if old_gantt is not None:
                gantt_path.write_text(old_gantt, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
