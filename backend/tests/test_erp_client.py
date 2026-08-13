import unittest

from ai.erp_client import (
    _as_list_payload,
    _join_url,
    _normalize_inventory,
    _normalize_orders,
)


class TestErpClientHelpers(unittest.TestCase):
    def test_join_url(self):
        self.assertEqual(
            _join_url("http://example.com/api/v1/aps-gx", "orders"),
            "http://example.com/api/v1/aps-gx/orders",
        )
        self.assertEqual(
            _join_url("http://example.com/api/v1/aps-gx/", "/inventory"),
            "http://example.com/api/v1/aps-gx/inventory",
        )

    def test_as_list_payload_accepts_list(self):
        rows = _as_list_payload([{"a": 1}, {"b": 2}])
        self.assertEqual(rows, [{"a": 1}, {"b": 2}])

    def test_as_list_payload_accepts_dict_data(self):
        rows = _as_list_payload({"data": [{"a": 1}]})
        self.assertEqual(rows, [{"a": 1}])

    def test_as_list_payload_raises_on_success_false(self):
        with self.assertRaises(RuntimeError):
            _as_list_payload(
                {
                    "success": False,
                    "timestamp": "2026-01-24T00:00:00Z",
                    "error": "Unauthorized",
                    "message": "Authorization is required",
                }
            )

    def test_normalize_orders(self):
        rows = [
            {
                "c_orderline_id": "123",
                "poreference": "DE#515476",
                "sku": "S18G9C",
                "quantity": "1300",
                "duedate": "23/09/2025 13:39",
                "name": "DE#-WDB900b-S18G9C-IL1",
                "remark": "xxx",
            }
        ]
        normalized = _normalize_orders(rows)
        self.assertEqual(normalized[0]["c_orderline_id"], 123)
        self.assertEqual(normalized[0]["quantity"], 1300)
        self.assertEqual(normalized[0]["poreference"], "DE#515476")

    def test_normalize_inventory(self):
        rows = [{"materialcode": "S12G8C", "quantity": "113855"}]
        normalized = _normalize_inventory(rows)
        self.assertEqual(normalized, [{"materialcode": "S12G8C", "quantity": 113855}])


if __name__ == "__main__":
    unittest.main()
