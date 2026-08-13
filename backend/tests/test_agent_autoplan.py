import unittest


class TestAgentAutoPlan(unittest.TestCase):
    def test_confirm_reply_after_reschedule_intent_triggers_full_reschedule(self):
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule(
            [
                HumanMessage(content="全局重排"),
                HumanMessage(content="确认"),
            ]
        )
        self.assertEqual(calls, [("reschedule", {"mode": "full"})])

        calls2 = _auto_tool_calls_for_downtime_and_schedule(
            [
                HumanMessage(content="开始排产"),
                HumanMessage(content="好的，数据已确认！"),
            ]
        )
        self.assertEqual(calls2, [("reschedule", {"mode": "full"})])

    def test_confirm_reply_does_not_override_order_query(self):
        # Guardrail: don't interpret "确认 ..." as confirmation if it includes an order id.
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule(
            [
                HumanMessage(content="全局重排"),
                HumanMessage(content="确认 1218288"),
            ]
        )
        self.assertEqual(calls, [])

    def test_forming_sku_shorthand_triggers_context_check_and_reschedule(self):
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule(
            [HumanMessage(content="弄错了，是18g9c, 12g9w,12g8q")]
        )

        # Should deterministically: confirm production context, then run full reschedule.
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[-2][0], "query_production_context")
        self.assertEqual(calls[-1][0], "reschedule")
        self.assertEqual(calls[-1][1], {"mode": "full"})

        qc_args = calls[-2][1]
        self.assertIn("forming_states", qc_args)
        forming_states = qc_args["forming_states"]
        self.assertEqual(forming_states.get("ROTARY-1"), "producing:S18G9C")
        self.assertEqual(forming_states.get("ROTARY-2"), "producing:S12G9W")
        self.assertEqual(forming_states.get("ROTARY-3"), "producing:S12G8Q")

    def test_forming_three_tokens_shorthand_maps_by_order(self):
        # KISS UX: allow replying with 3 tokens (one per line) and map them to ROTARY-1/2/3.
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule(
            [HumanMessage(content="18G9C\n12G9W\n12G8Q")]
        )
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[-2][0], "query_production_context")
        qc_args = calls[-2][1]
        fs = qc_args.get("forming_states") or {}
        self.assertEqual(fs.get("ROTARY-1"), "producing:S18G9C")
        self.assertEqual(fs.get("ROTARY-2"), "producing:S12G9W")
        self.assertEqual(fs.get("ROTARY-3"), "producing:S12G8Q")
        self.assertEqual(calls[-1], ("reschedule", {"mode": "full"}))

    def test_parse_single_day_holiday_and_cny_range(self):
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule(
            [
                HumanMessage(
                    content=(
                        "S18G9C\n\nS12G9W\n\nS12G8Q\n\n"
                        "2-1 全天转班休息，春节放假 2-13 到 2-23"
                    )
                )
            ]
        )

        holiday_calls = [(name, args) for name, args in calls if name == "add_holiday"]
        self.assertTrue(any(h[1].get("start") == "2026-02-01" and h[1].get("end") == "2026-02-01" for h in holiday_calls))
        self.assertTrue(any(h[1].get("start") == "2026-02-13" and h[1].get("end") == "2026-02-23" for h in holiday_calls))

    def test_global_reschedule_request_triggers_context_check_first(self):
        from langchain_core.messages import HumanMessage

        from ai.agent import _auto_tool_calls_for_downtime_and_schedule

        calls = _auto_tool_calls_for_downtime_and_schedule([HumanMessage(content="全局重排")])
        self.assertEqual(calls[0][0], "query_production_context")
        self.assertEqual(calls[0][1], {})
        self.assertTrue(all(name != "reschedule" for name, _ in calls))

    def test_parse_fallback_distinguishes_invalid_tool_call_from_iteration_cap(self):
        from langchain_core.messages import AIMessage, HumanMessage

        from ai.agent import _node_parse_and_route

        out = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="hello"),
                    # Not valid JSON; should be treated as a parse failure, not "limit reached".
                    AIMessage(content="<tool_call>{'tool':'query_orders','args':{}}</tool_call>"),
                ],
                "rendered": "",
                "tool_iterations": 0,
                "max_tool_iterations": 30,
            }
        )
        self.assertIn("工具调用解析失败", out.get("rendered") or "")

        out2 = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="hello"),
                    AIMessage(content=""),
                ],
                "rendered": "",
                "tool_iterations": 30,
                "max_tool_iterations": 30,
            }
        )
        self.assertIn("工具调用次数已达上限", out2.get("rendered") or "")

    def test_autoplan_ignores_llm_reschedule_until_precheck_is_done(self):
        # Regression guard: When the deterministic "更新排产" auto-plan triggers, we must not
        # execute an LLM-emitted `reschedule` in the same turn; the UX should prompt for
        # latest starting status first (no tool calls on first click).
        from langchain_core.messages import AIMessage, HumanMessage

        from ai.agent import _node_parse_and_route

        out = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="更新排产"),
                    AIMessage(
                        content=(
                            '<tool_call>{"tool":"reschedule","args":{"mode":"full"}}</tool_call>'
                        )
                    ),
                ],
                "rendered": "",
                "tool_iterations": 0,
                "max_tool_iterations": 30,
            }
        )
        self.assertIsNone(out.get("pending_tool_name"))
        pending = out.get("pending_tool_calls") or []
        self.assertEqual(pending, [])
        self.assertIn("更新排产前", out.get("rendered") or "")

    def test_update_schedule_first_turn_shows_prompt_and_does_not_call_tools(self):
        # KISS UX: clicking "更新排产" should prompt for rotary statuses + downtime/holidays,
        # and should not run tools (which could reuse previous confirmed state).
        from langchain_core.messages import AIMessage, HumanMessage

        from ai.agent import _node_parse_and_route

        out = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="更新排产"),
                    AIMessage(content=""),
                ],
                "rendered": "",
                "tool_iterations": 0,
                "max_tool_iterations": 30,
            }
        )
        self.assertIsNone(out.get("pending_tool_name"))
        self.assertIn("更新排产前", out.get("rendered") or "")

    def test_query_production_context_no_args_uses_previous_state(self):
        from datetime import datetime

        from ai import tools as tools_mod

        prev = dict(tools_mod._production_context_check)
        try:
            tools_mod._production_context_check.update(
                {
                    "confirmed": True,
                    "forming_states": {
                        "ROTARY-1": "producing:S18G9C",
                        "ROTARY-2": "producing:S12G9W",
                        "ROTARY-3": "producing:S12G8Q",
                    },
                    "setup_remaining_by_machine": {},
                    "timestamp": datetime.now(),
                }
            )
            out = tools_mod.query_production_context.invoke({})
            self.assertIn("ROTARY-1", out)
            self.assertTrue(bool(tools_mod._production_context_check.get("confirmed")))
            fs = tools_mod._production_context_check.get("forming_states") or {}
            self.assertEqual(fs.get("ROTARY-2"), "producing:S12G9W")
        finally:
            tools_mod._production_context_check.clear()
            tools_mod._production_context_check.update(prev)

    def test_erp_export_request_triggers_send_tool_immediately(self):
        from langchain_core.messages import AIMessage, HumanMessage

        from ai.agent import _node_parse_and_route

        out = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="把未来3天导出到ERP"),
                    AIMessage(content=""),
                ],
                "rendered": "",
                "tool_iterations": 0,
                "max_tool_iterations": 30,
            }
        )
        self.assertEqual(out.get("pending_tool_name"), "send_erp_export")
        self.assertEqual(out.get("pending_tool_args"), {"days": 3})

    def test_erp_export_request_english_parses_days(self):
        from langchain_core.messages import AIMessage, HumanMessage

        from ai.agent import _node_parse_and_route

        out = _node_parse_and_route(
            {
                "messages": [
                    HumanMessage(content="send next 5 days to ERP"),
                    AIMessage(content=""),
                ],
                "rendered": "",
                "tool_iterations": 0,
                "max_tool_iterations": 30,
            }
        )
        self.assertEqual(out.get("pending_tool_name"), "send_erp_export")
        self.assertEqual(out.get("pending_tool_args"), {"days": 5})


if __name__ == "__main__":
    unittest.main()
