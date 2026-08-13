import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestToolsSmoke(unittest.TestCase):
    """Agent-level smoke coverage for every registered tool.

    Goal: each tool name in `ai.agent._TOOLS` is invokable with reasonable args
    without crashing, and stateful flows (context-check -> reschedule -> compare)
    work end-to-end.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.process_dir = self.tmp_root / "process"

        repo_backend = Path(__file__).resolve().parents[1]
        shutil.copytree(repo_backend / "process", self.process_dir)

        # Keep test runs hermetic: reset mutable process documents.

        self.overrides_path = self.process_dir / "overrides.json"
        self.overrides_path.write_text(json.dumps({"containers": {}, "orders": {}}, ensure_ascii=False, indent=2), encoding="utf-8")

        self.agent_state_path = self.process_dir / "agent_state.json"
        self.agent_state_path.write_text("{}", encoding="utf-8")

        self.line_config_path = self.process_dir / "line_config.json"
        self.orders_path = self.process_dir / "orders_erp.json"
        self.inventory_path = self.process_dir / "inventory_erp.json"
        self.schedule_path = self.process_dir / "schedule_result.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load_schedule_sample(self) -> dict:
        sched = json.loads(self.schedule_path.read_text(encoding="utf-8"))
        self.assertIsInstance(sched, dict)
        orders = sched.get("orders")
        self.assertIsInstance(orders, list)
        self.assertTrue(orders, "schedule_result.json must contain at least 1 order")
        first = orders[0]
        self.assertIsInstance(first, dict)
        return first

    def test_all_tools_invokable(self) -> None:
        import process.multiline as multiline_mod
        from ai import line_config as line_config_mod
        from ai import state_store as state_store_mod
        from ai import tools as tools_mod
        from ai.agent import _TOOLS, _execute_tool
        from process import overrides as overrides_mod

        sample = self._load_schedule_sample()
        container_ref = str(sample.get("poreference") or "").strip()
        self.assertTrue(container_ref)
        order_id = int(sample.get("c_orderline_id"))
        customer_code = str(sample.get("name") or "").split("-", 1)[0].strip()
        self.assertTrue(customer_code)

        real_generate_all_lines = multiline_mod.generate_all_lines
        real_load_line_config = line_config_mod.load_line_config
        real_save_line_config = line_config_mod.save_line_config

        def generate_all_lines_sandboxed(*, overrides=None, **kwargs):
            # Keep scheduling reading from the sandbox process dir.
            kwargs.setdefault("line_config_path", self.line_config_path)
            kwargs.setdefault("orders_path", self.orders_path)
            kwargs.setdefault("inventory_path", self.inventory_path)
            if overrides is None:
                overrides = overrides_mod.load_overrides(path=self.overrides_path)
            return real_generate_all_lines(overrides=overrides, **kwargs)

        # Patch tool module stateful dependencies to sandbox files (DB-backed calendar is
        # configured via a sandbox SQLite DATABASE_URL below).
        def load_overrides_sandboxed():
            return overrides_mod.load_overrides(path=self.overrides_path)

        def save_overrides_sandboxed(payload):
            return overrides_mod.save_overrides(payload, path=self.overrides_path)

        def load_pcc_sandboxed():
            return state_store_mod.load_production_context_check(path=self.agent_state_path)

        def save_pcc_sandboxed(payload):
            return state_store_mod.save_production_context_check(payload, path=self.agent_state_path)

        def load_line_config_sandboxed():
            return real_load_line_config(path=self.line_config_path)

        def save_line_config_sandboxed(cfg):
            return real_save_line_config(cfg, path=self.line_config_path)

        with (
            patch.object(tools_mod, "DEFAULT_ORDERS_PATH", self.orders_path),
            patch.object(tools_mod, "DEFAULT_INVENTORY_PATH", self.inventory_path),
            patch.object(tools_mod, "DEFAULT_SCHEDULE_PATH", self.schedule_path),
            patch.object(tools_mod, "load_local_overrides", side_effect=load_overrides_sandboxed),
            patch.object(tools_mod, "save_local_overrides", side_effect=save_overrides_sandboxed),
            patch.object(tools_mod, "_load_pcc", side_effect=load_pcc_sandboxed),
            patch.object(tools_mod, "_save_pcc", side_effect=save_pcc_sandboxed),
            patch.object(multiline_mod, "generate_all_lines", side_effect=generate_all_lines_sandboxed),
            patch.object(line_config_mod, "load_line_config", side_effect=load_line_config_sandboxed),
            patch.object(line_config_mod, "save_line_config", side_effect=save_line_config_sandboxed),
            patch.dict(
                os.environ,
                {
                    "APS_AUTO_SYNC_ERP": "0",
                    # Make sure tool smoke tests never hit a developer's real DB.
                    "DATABASE_URL": f"sqlite:///{self.tmp_root / 'test.db'}",
                    "PGHOST": "",
                    "PGPORT": "",
                    "PGUSER": "",
                    "PGPASSWORD": "",
                    "PGDATABASE": "",
                    "POSTGRES_USER": "",
                    "POSTGRES_PASSWORD": "",
                    "POSTGRES_DB": "",
                },
                clear=False,
            ),
        ):
            # Initialize sandbox DB schema + seed empty downtime calendar.
            from ai import db as db_mod

            db_mod._engine = None
            db_mod._SessionLocal = None

            from ai.db import get_engine
            from ai.db_store import upsert_document_payload
            from ai.models import Base

            engine = get_engine()
            self.assertIsNotNone(engine)
            Base.metadata.create_all(engine)
            upsert_document_payload("production_calendar", {"holidays": [], "maintenance": []})

            # Ensure tool registry itself stays stable.
            tool_names = [t.name for t in _TOOLS]
            self.assertEqual(len(tool_names), len(set(tool_names)), "tool names must be unique")

            # 1) Stateless read tools
            out = _execute_tool("query_orders", {"order_ref": str(order_id)})
            self.assertIsInstance(out, str)

            out = _execute_tool("query_orders_by_customer", {"customer_code": customer_code, "status": "all"})
            self.assertIsInstance(out, str)

            out = _execute_tool("query_container", {"container_ref": container_ref})
            self.assertIsInstance(out, str)

            out = _execute_tool("query_containers_by_customer", {"customer_code": customer_code, "status": "all"})
            self.assertIsInstance(out, str)

            out = _execute_tool("get_schedule_kpi", {})
            self.assertIsInstance(out, str)

            out = _execute_tool("request_downtime_form", {"form_type": "holiday"})
            self.assertIsInstance(out, str)
            self.assertIn("__FORM_CARD__", out)

            # 2) Calendar write path (sandboxed file)
            out = _execute_tool("add_holiday", {"name": "test-holiday", "start": "2099-01-01", "end": "2099-01-02"})
            self.assertIsInstance(out, str)

            out = _execute_tool(
                "add_maintenance",
                {
                    "machine_id": "ROTARY-2",
                    "reason": "test-maint",
                    "start": "2099-01-03 10:00",
                    "end": "2099-01-03 12:00",
                },
            )
            self.assertIsInstance(out, str)

            out = _execute_tool("get_downtime_plans", {})
            self.assertIsInstance(out, str)
            self.assertIn("test-holiday", out)
            self.assertIn("test-maint", out)

            out = _execute_tool("delete_holiday", {"index": 0})
            self.assertIsInstance(out, str)

            out = _execute_tool("delete_maintenance", {"index": 0})
            self.assertIsInstance(out, str)

            # 3) Overrides (sandboxed file)
            out = _execute_tool("get_overrides", {})
            self.assertIsInstance(out, str)

            out = _execute_tool("set_container_override", {"container_ref": container_ref, "priority": 123})
            self.assertIsInstance(out, str)

            out = _execute_tool("set_order_override", {"order_id": order_id, "priority": 124})
            self.assertIsInstance(out, str)

            out = _execute_tool("clear_container_override", {"container_ref": container_ref})
            self.assertIsInstance(out, str)

            out = _execute_tool("clear_order_override", {"order_id": order_id})
            self.assertIsInstance(out, str)

            # 4) Line config edit (sandboxed file)
            out = _execute_tool("get_line_config", {})
            self.assertIsInstance(out, str)

            out = _execute_tool("update_line_config", {"line_id": "L2", "updates": {"forming_rate_per_h": 5001}})
            self.assertIsInstance(out, str)

            # 5) Context-check -> full reschedule -> compare (core flow)
            out = _execute_tool(
                "query_production_context",
                {
                    "forming_states": {"ROTARY-1": "idle", "ROTARY-2": "idle", "ROTARY-3": "idle"},
                    "setup_remaining_by_machine": {},
                },
            )
            self.assertIsInstance(out, str)

            out = _execute_tool("reschedule", {"mode": "full"})
            self.assertIsInstance(out, str)
            self.assertIn("全局重排", out)

            out = _execute_tool("compare_schedules", {"include_unchanged": False})
            self.assertIsInstance(out, str)

            # 6) Campaign analysis (read-only) + apply path (no-op option to avoid re-running scheduler)
            out = _execute_tool("analyze_campaign_efficiency", {"min_campaign_ratio": 0.2})
            self.assertIsInstance(out, str)

            prev_state = dict(getattr(tools_mod, "_campaign_optimization_state", {}) or {})
            try:
                tools_mod._campaign_optimization_state = {
                    "analysis": {"idle_total_h": 0.0, "avg_campaign_h": 1.0},
                    "options": [{"id": "1", "name": "noop", "extra_production": {}}],
                    "timestamp": "2099-01-01T00:00:00",
                }
                out = _execute_tool("apply_campaign_optimization", {"option": "1"})
                self.assertIsInstance(out, str)
            finally:
                tools_mod._campaign_optimization_state = prev_state

            # 7) ERP export flow (purely simulated; single-step send)
            out = _execute_tool("send_erp_export", {"days": 1})
            self.assertIsInstance(out, str)
            self.assertIn("__ERP_EXPORT_CARD__", out)

            # Avoid leaking SQLite connections across the whole unittest run.
            try:
                engine.dispose()
            except Exception:
                pass
            db_mod._engine = None
            db_mod._SessionLocal = None
