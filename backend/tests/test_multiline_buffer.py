import unittest
from datetime import datetime


class TestMultilineBufferFiltering(unittest.TestCase):
    def test_generate_all_lines_includes_lines_even_without_matching_orders(self):
        import json
        import tempfile
        from pathlib import Path

        from process.multiline import generate_all_lines

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "line_config.json"
            orders_path = root / "orders.json"
            inv_path = root / "inventory.json"

            cfg = {
                "lines": {
                    "L1": {
                        "forming_machine": "ROTARY-1",
                        "labeling_machines": ["LABEL-1", "LABEL-2"],
                        "sku_prefixes": ["S18"],
                        "forming_rate_per_h": 1000,
                        "labeling_rate_per_h": 1000,
                        "setup_rules": {},
                    },
                    "L2": {
                        "forming_machine": "ROTARY-2",
                        "labeling_machines": ["LABEL-3", "LABEL-5"],
                        "sku_prefixes": ["S12"],
                        "forming_rate_per_h": 1000,
                        "labeling_rate_per_h": 1000,
                        "setup_rules": {},
                    },
                }
            }
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            orders = {
                "timestamp": "2026-01-25T00:00:00Z",
                "data": [
                    {
                        "c_orderline_id": 1,
                        "poreference": "PO1",
                        "sku": "S18G9C",
                        "quantity": 10,
                        "duedate": "2026-01-26T00:00:00",
                        "priority": 0,
                    }
                ],
            }
            orders_path.write_text(json.dumps(orders), encoding="utf-8")
            inv_path.write_text(json.dumps({"data": []}), encoding="utf-8")

            line_schedules, combined = generate_all_lines(
                line_config_path=cfg_path,
                orders_path=orders_path,
                inventory_path=inv_path,
                max_hours=48,
                apply_downtime=False,
            )

            # Even if a line has no matching orders, we still include it so the UI can show all machines.
            self.assertIn("L1", line_schedules)
            self.assertIn("L2", line_schedules)
            self.assertIn("ROTARY-2", combined["machines"])

    def test_generate_all_lines_supports_extra_production_without_polluting_kpi(self):
        import json
        import tempfile
        from pathlib import Path

        from process.multiline import generate_all_lines

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_path = root / "line_config.json"
            orders_path = root / "orders.json"
            inv_path = root / "inventory.json"

            cfg = {
                "lines": {
                    "L1": {
                        "forming_machine": "ROTARY-1",
                        "labeling_machines": ["LABEL-1", "LABEL-2"],
                        "sku_prefixes": ["S18"],
                        "forming_rate_per_h": 1000,
                        "labeling_rate_per_h": 1000,
                        "setup_rules": {},
                    },
                    "L2": {
                        "forming_machine": "ROTARY-2",
                        "labeling_machines": ["LABEL-3", "LABEL-5"],
                        "sku_prefixes": ["S12"],
                        "forming_rate_per_h": 1000,
                        "labeling_rate_per_h": 1000,
                        "setup_rules": {},
                    },
                }
            }
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            orders = {
                "timestamp": "2026-01-25T00:00:00Z",
                "data": [
                    {
                        "c_orderline_id": 1,
                        "poreference": "PO1",
                        "sku": "S18G9C",
                        "quantity": 10,
                        "duedate": "2026-01-26T00:00:00",
                        "priority": 0,
                    }
                ],
            }
            orders_path.write_text(json.dumps(orders), encoding="utf-8")
            inv_path.write_text(json.dumps({"data": []}), encoding="utf-8")

            line_schedules, combined = generate_all_lines(
                line_config_path=cfg_path,
                orders_path=orders_path,
                inventory_path=inv_path,
                max_hours=48,
                apply_downtime=False,
                extra_production={"S12G9W": 100},
            )

            # Extra production should cause L2 to be scheduled even if it had no real orders.
            self.assertIn("L2", line_schedules)

            # Buffer orders must not pollute the KPI.
            self.assertEqual(combined["kpi"]["orders_total"], 1)

    def test_merge_schedules_filters_buffer_orders_from_kpi_and_containers(self):
        # Buffer orders should still be schedulable (appear in machine tasks),
        # but they must not pollute KPI/containers (those should be based on real demand).
        from process.multiline import merge_schedules

        start = datetime(2026, 1, 25, 0, 0, 0)

        real_order = {
            "c_orderline_id": 1,
            "poreference": "PO1",
            "sku": "S18G9C",
            "quantity": 100,
            "due": start.isoformat(),
            "deadline": start.isoformat(),
            "start": start.isoformat(),
            "end": start.isoformat(),
            "on_time": True,
            "expired_before_start": False,
            "lateness_h": 0,
        }
        buffer_order = {
            "c_orderline_id": -1,
            "poreference": "__BUFFER__",
            "sku": "S12G9W",
            "quantity": 999,
            "due": start.isoformat(),
            "deadline": start.isoformat(),
            "start": start.isoformat(),
            "end": start.isoformat(),
            "on_time": True,
            "expired_before_start": False,
            "lateness_h": 0,
        }

        line_schedules = {
            "L1": {
                "meta": {"line": "L1", "start_time": start.isoformat(), "horizon_h": 1},
                "kpi": {"setup_count": 0},
                "machines": {"M1": [{"type": "forming", "sku": "S18G9C"}]},
                "orders": [real_order],
            },
            "L2": {
                "meta": {"line": "L2", "start_time": start.isoformat(), "horizon_h": 1},
                "kpi": {"setup_count": 0},
                # Buffer task should remain visible in machines.
                "machines": {"M2": [{"type": "forming", "sku": "S12G9W"}]},
                "orders": [buffer_order],
            },
        }

        combined = merge_schedules(start_time=start, line_schedules=line_schedules)

        # Only real orders count.
        self.assertEqual(combined["kpi"]["orders_total"], 1)
        self.assertEqual(len(combined["orders"]), 1)
        self.assertEqual(combined["orders"][0]["poreference"], "PO1")

        # Containers should not include the buffer pseudo-container.
        container_ids = [c["container_id"] for c in combined["containers"]]
        self.assertIn("PO1", container_ids)
        self.assertNotIn("__BUFFER__", container_ids)


if __name__ == "__main__":
    unittest.main()
