"""LangGraph-powered Agent for scheduling assistant.

This module builds a LangGraph state machine to orchestrate:
1) LLM conversation
2) Text-based tool calling (via <tool_call>{...}</tool_call>)
3) Tool execution + iterative follow-ups

The public API (`generate_reply` / `agenerate_reply`) stays compatible with the
original implementation, while enabling structured streaming events for the
FastAPI SSE endpoint.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import requests
from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

from .tools import (
    add_holiday,
    add_maintenance,
    analyze_campaign_efficiency,
    apply_campaign_optimization,
    compare_schedules,
    send_erp_export,
    delete_holiday,
    delete_maintenance,
    get_line_config,
    get_overrides,
    get_downtime_plans,
    get_schedule_kpi,
    query_container,
    query_containers_by_customer,
    query_orders,
    query_orders_by_customer,
    query_production_context,
    request_downtime_form,
    reschedule,
    update_line_config,
    set_container_override,
    clear_container_override,
    set_order_override,
    clear_order_override,
)

# Load env explicitly from repo root (avoids find_dotenv() edge cases on Python 3.14).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)

def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


# Allow batch operations (e.g. adding multiple holidays) without prematurely stopping.
# Override via env: AGENT_MAX_TOOL_ITERATIONS=...
MAX_TOOL_ITERATIONS = max(10, _env_int("AGENT_MAX_TOOL_ITERATIONS", 30))

HELP_TEXT = """我是 GX APS 多产线排产助手（L1/L2/L3），可以帮你做以下事情：

1. **查询订单**：例如 "查询 1218288 订单情况" 或 "查询 DE#515894 订单情况"
2. **按客户查询**：例如 "SQ# 客户的订单会不会迟？" 或 "DE# 客户有哪些延迟订单？"
3. **查询货柜**：例如 "SEC515910 这个货柜什么时候能完成？"
4. **按客户查货柜**：例如 "DE# 客户有几个 Container 会延迟？"
5. **修改交期并重排**：例如 "1218288 订单必须在 2026-01-26 之前完成"
6. **对比方案**：重排后可以问 "新方案对其他订单有什么影响"
7. **查看KPI**：例如 "当前排产的KPI是多少"

请告诉我你需要什么帮助？
"""

SYSTEM_PROMPT = """你是 GX APS 多产线排产助手，一个友好、专业的 AI 助手。你可以与用户自然对话，并在需要时使用工具来查询或操作排产系统。

## 产线背景
- 工厂有 3 条独立产线（机器互不共享）：
  - L1：ROTARY-1 + LABEL-1/LABEL-2（SKU: S18B1*, S18G9*）
  - L2：ROTARY-2 + LABEL-3/LABEL-5（SKU: S12G9*）
  - L3：ROTARY-3 + LABEL-4/LABEL-6（SKU: S12G8*）
- 用户通常希望看到 **ALL（9 台机器）** 的合并甘特图
- 开始“开排/全局重排”前，优先确认 3 台成型机（ROTARY-1/2/3）的当前状态（生产/换型/空闲），以贴近现场起始条件
- 贴标机的实时在制状态目前未建模；如需表达不可用时间段，请通过停机计划（holiday/maintenance）录入

## 重要概念

### 客户代码
客户代码是订单 name 字段第一个 `-` 之前的部分。
例如: "DE#-DSB700c-S12G9C-IL1" 的客户代码是 "DE#"

常见客户代码: SQ#, DE#, SEC, AGI, KRS, VPF, EGC 等

### Container（货柜）
- Container ID = poreference（PO参考号）
- 同一 poreference 的订单属于同一个 Container
- **货柜可交付时间** = 该 Container 中**最后一个订单**的生产完成时间（LABEL机台下线）
- **重要**：生产完成时间是订单从 LABEL 机台下线的时间，货柜必须等所有订单都生产完成后才能交付
- 只有所有订单都准时完成，Container 才算准时

### 时间概念区分
- **订单生产完成时间（end）**：单个订单在 LABEL 机台下线的时间
- **货柜可交付时间**：该货柜中最后一个订单的生产完成时间
- **deadline**：系统内部的截止时间（= due time 向上取整到小时）
- 客户关心的是**货柜可交付时间**，而非单个订单的生产完成时间

## 可用工具

你有以下工具可以使用：

1. **query_orders** - 查询单个订单排程状态
   参数：order_ref（订单标识，如 "1218288" 或 "DE#515894"）
   用途：当用户想了解某个特定订单的情况时使用

2. **query_orders_by_customer** - 查询订单
   参数：customer_code（可选，客户代码），status（可选，"all"/"on_time"/"late"/"expired"）
   用途：
   - 指定客户：查询特定客户订单，如 "SQ# 客户有哪些延迟订单？"
   - 不指定客户：查询所有延期订单，如 "列出延期的订单"、"哪些订单会迟"

3. **query_container** - 查询单个 Container 状态
   参数：container_ref（Container ID，即 poreference，如 "SEC515910"）
   用途：当用户问某个货柜/Container 的完成时间时使用
   例如："SEC515910 这个货柜什么时候完成？"

4. **query_containers_by_customer** - 查询 Container
   参数：customer_code（可选，客户代码），status（可选，"all"/"on_time"/"late"）
   用途：
   - 指定客户：查询特定客户货柜，如 "DE# 有几个延迟货柜？"
   - 不指定客户：查询所有延期货柜，如 "哪些货柜会延期"

5. **reschedule** - 重新排产订单或货柜，或执行全局重排
   参数：
   - order_refs: 订单标识，支持逗号分隔多个（如 "DE#515894,SEC515910"）。mode="full" 时可选
   - new_deadline: 新截止日期/时间（格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）
   - mode: 重排模式（"constraint"、"priority" 或 "full"，默认 "constraint"）
   用途：
   - mode="constraint": 交期约束模式，确保在截止日期前完成，最小调整（需要指定 new_deadline）
   - mode="priority": 优先锁定模式，设为最高优先级，其他订单可能被挤后
   - mode="full": 全局重排模式，考虑停机计划，全局优化（添加停机计划后使用）
   **注意**：此工具会自动将约束应用到整个货柜（同一poreference）的所有订单

6. **compare_schedules** - 对比新旧排产方案
   参数：无
   用途：在重排后，用户想看新方案对其他订单的影响时使用

7. **get_schedule_kpi** - 获取当前排产 KPI
   参数：无
   用途：当用户想了解整体排产情况、准时率等指标时使用

8. **request_downtime_form** - 请求显示停机计划表单
   参数：form_type（"maintenance" 或 "holiday"）
   用途：当用户想手动填写单个停机计划时使用
   - "maintenance": 显示设备维护表单（机器、原因、时间）
   - "holiday": 显示假期表单（名称、日期）

9. **add_holiday** - 直接添加假期（无需表单）
   参数：name（假期名称），start（开始日期 YYYY-MM-DD），end（结束日期 YYYY-MM-DD）
   用途：
   - 用户明确说出假期信息时直接添加
   - 批量添加多个假期（如"每周日放假"需多次调用）
   - 例如用户说"1月26日周日放假"，直接调用 add_holiday("周日休息", "2026-01-26", "2026-01-26")

10. **add_maintenance** - 直接添加设备维护（无需表单）
   参数：machine_id, reason, start（YYYY-MM-DDTHH:MM）, end
   用途：用户明确说出维护信息时直接添加

11. **get_downtime_plans** - 查询当前停机计划
   参数：无
   用途：查看已有的假期和维护计划

12. **delete_holiday / delete_maintenance** - 删除停机计划（按索引）
   参数：index（从 get_downtime_plans 列表里的序号）
   用途：当用户要删除/撤销某个假期或维护计划时使用

13. **query_production_context** - 查询当前生产上下文（重排前检查）
   参数：
   - forming_states（可选）：成型机状态（machine_id -> "idle" | "setup" | "producing:<SKU>" | "<SKU>"）
     - 例如：{"ROTARY-1":"producing:S18B1Q","ROTARY-2":"producing:S12G9C","ROTARY-3":"idle"}
   - setup_remaining_by_machine（可选）：换色剩余小时数（machine_id -> hours，仅当 state=setup 时需要）
     - 例如：{"ROTARY-1":10}
   - rotary_state / setup_remaining_h：旧版 L2 简写（仅映射 ROTARY-2，不推荐）
   用途：在执行任何 reschedule 前调用，确认成型机起始状态 + 近期停机计划

14. **analyze_campaign_efficiency** - 分析换色周期效率
   参数：min_campaign_ratio（可选，默认 0.2）
   用途：当用户发现"换色后生产太少"、"换色效率低"时使用
   返回：各换色周期效率分析 + 三个优化选项

15. **apply_campaign_optimization** - 应用换色周期优化方案
   参数：option（"1"=保守方案, "2"=平衡方案, "3"=最大化方案）
   用途：用户选择优化方案后，应用该方案重新排产

16. **send_erp_export** - 将未来 N 天的排产计划直接发送到 ERP（单步）
   参数：days（导出天数，默认 3 天）
   用途：当用户说"导出到ERP/发回ERP/发送到ERP/把未来X天发到ERP"时直接使用
   返回：发送结果，成功时触发前端显示成功动画

17. **get_line_config** - 读取多产线配置（backend/process/line_config.json）
   参数：无
   用途：当需要确认/审阅产线配置时使用

18. **update_line_config** - 更新或删除某条产线配置（写入 backend/process/line_config.json）
   参数：line_id（如 "L1"）, updates（字段合并）, delete（是否删除）
   用途：当需要调整产线映射、产能、SKU 前缀、换型规则时使用（AI 可操作，但要谨慎）

## 如何使用工具

当你需要使用工具时，请按以下格式输出：

<tool_call>
{"tool": "工具名称", "args": {"参数名": "参数值"}}
</tool_call>

例如：
- 查询订单：<tool_call>{"tool": "query_orders", "args": {"order_ref": "1218288"}}</tool_call>
- 查所有延期订单：<tool_call>{"tool": "query_orders_by_customer", "args": {"status": "late"}}</tool_call>
- 按客户查延期订单：<tool_call>{"tool": "query_orders_by_customer", "args": {"customer_code": "SQ#", "status": "late"}}</tool_call>
- 查询货柜：<tool_call>{"tool": "query_container", "args": {"container_ref": "SEC515910"}}</tool_call>
- 查所有延期货柜：<tool_call>{"tool": "query_containers_by_customer", "args": {"status": "late"}}</tool_call>
- 按客户查延期货柜：<tool_call>{"tool": "query_containers_by_customer", "args": {"customer_code": "DE#", "status": "late"}}</tool_call>
- 重排订单（交期约束，先重排前检查）：<tool_call>{"tool": "query_production_context", "args": {"rotary_state": "producing_c"}}</tool_call><tool_call>{"tool": "reschedule", "args": {"order_refs": "1218288", "new_deadline": "2026-01-26"}}</tool_call>
- 重排订单（优先锁定，先重排前检查）：<tool_call>{"tool": "query_production_context", "args": {"rotary_state": "producing_c"}}</tool_call><tool_call>{"tool": "reschedule", "args": {"order_refs": "DE#515894,SEC515910", "mode": "priority"}}</tool_call>
- 对比方案：<tool_call>{"tool": "compare_schedules", "args": {}}</tool_call>
- 查看KPI：<tool_call>{"tool": "get_schedule_kpi", "args": {}}</tool_call>
- 弹出维护表单：<tool_call>{"tool": "request_downtime_form", "args": {"form_type": "maintenance"}}</tool_call>
- 直接添加假期：<tool_call>{"tool": "add_holiday", "args": {"name": "周日休息", "start": "2026-01-26", "end": "2026-01-26"}}</tool_call>
- 直接添加维护：<tool_call>{"tool": "add_maintenance", "args": {"machine_id": "ROTARY-2", "reason": "换模", "start": "2026-01-25T08:00", "end": "2026-01-25T12:00"}}</tool_call>
- 查看停机计划：<tool_call>{"tool": "get_downtime_plans", "args": {}}</tool_call>
- 删除假期（按索引）：<tool_call>{"tool": "delete_holiday", "args": {"index": 0}}</tool_call>
- 删除维护（按索引）：<tool_call>{"tool": "delete_maintenance", "args": {"index": 0}}</tool_call>
- 查询生产上下文：<tool_call>{"tool": "query_production_context", "args": {}}</tool_call>
- 带状态的生产上下文（推荐多产线）：<tool_call>{"tool": "query_production_context", "args": {"forming_states": {"ROTARY-1":"setup","ROTARY-2":"producing:S12G9C","ROTARY-3":"idle"}, "setup_remaining_by_machine": {"ROTARY-1": 10}}}</tool_call>
- 执行全局重排（ALL；先重排前检查）：<tool_call>{"tool": "query_production_context", "args": {"forming_states": {"ROTARY-1":"producing:S18B1Q","ROTARY-2":"producing:S12G9C","ROTARY-3":"idle"}}}</tool_call><tool_call>{"tool": "reschedule", "args": {"mode": "full"}}</tool_call>
- 带状态的重排（先重排前检查）：<tool_call>{"tool": "query_production_context", "args": {"rotary_state": "producing_c"}}</tool_call><tool_call>{"tool": "reschedule", "args": {"order_refs": "DE#515894", "new_deadline": "2026-01-25", "rotary_state": "producing_c"}}</tool_call>
- 分析换色效率：<tool_call>{"tool": "analyze_campaign_efficiency", "args": {}}</tool_call>
- 应用换色优化方案（先重排前检查）：<tool_call>{"tool": "query_production_context", "args": {"rotary_state": "producing_c"}}</tool_call><tool_call>{"tool": "apply_campaign_optimization", "args": {"option": "2"}}</tool_call>
- 发送到ERP：<tool_call>{"tool": "send_erp_export", "args": {"days": 3}}</tool_call>
- 查看产线配置：<tool_call>{"tool": "get_line_config", "args": {}}</tool_call>
- 更新产线配置：<tool_call>{"tool": "update_line_config", "args": {"line_id": "L3", "updates": {"forming_rate_per_h": 5200}}}</tool_call>

## 回复规范

1. 用友好的中文回复用户
2. 数量使用千分位格式（如 5,000）
3. 时间使用 YYYY-MM-DD HH:MM 格式
4. 对延期订单标注延迟时长
5. 如果用户的问题不需要使用工具（如打招呼、闲聊），直接回复即可
6. 如果用户问的问题需要数据支撑，先调用工具获取数据，再基于数据回复
7. **禁止使用 Markdown 表格格式**（如 `| 列1 | 列2 |`），聊天界面不支持表格渲染
8. 使用列表格式（如 `- 订单1: ...`）或自然语言段落来呈现数据

## 重要提示

- 不要编造数据，必须通过工具获取真实数据
- 如果用户提供的订单号不完整或有歧义，先询问确认
- 保持对话的自然和连贯性
- 当用户说"锁定"、"确保完成"、"必须优先"等关键词时，使用 reschedule(..., mode="priority")
- 普通的交期修改使用 reschedule(..., mode="constraint")（默认模式）
- 当用户问"列出延期订单"、"哪些订单会迟"时，使用 query_orders_by_customer(status="late")
- 当用户问"哪些货柜会延期"时，使用 query_containers_by_customer(status="late")
- 当用户问某个特定客户的情况时，传入 customer_code 参数
- **停机计划处理策略**：
  - 用户说出具体信息（如"1月26日周日放假"、"每周日放假"）时，直接用 add_holiday/add_maintenance 添加
  - 用户说"两台贴标机"需要停机维护时，默认指 LABEL-3 和 LABEL-5，必须分别调用 add_maintenance 录入
  - 用户说"我要添加一个假期"但没给具体信息时，用 request_downtime_form 弹出表单
  - 批量添加（如"每周日放假"）时，计算出所有日期后多次调用 add_holiday
  - **添加完停机计划后，必须先调用 query_production_context 完成重排前检查，再调用 reschedule(mode="full") 执行全局重排**，这样才能在右侧工作区显示新旧排产甘特图对比
  - **重要**：如果用户没有给出具体日期（如只说"春节放假"但没说几号到几号），必须先询问确认，不要自己假设日期
  - **严禁**在未调用 reschedule(mode="full") 前就汇报"排产已更新/KPI/停机已生效"；必须以工具返回结果为准
- **换色周期效率优化策略**：
  - 当用户说"换色后生产太少"、"换色效率低"、"能不能多生产一些"时，先调用 analyze_campaign_efficiency() 分析
  - 分析结果会展示三个选项：保守方案（30% idle）、平衡方案（60% idle）、最大化方案（100% idle）
  - 用户选择后，调用 apply_campaign_optimization(option="1/2/3") 应用方案
  - 此优化不会影响现有订单的准时率，只是利用空闲时间额外生产半成品库存
- **ERP 导出流程（单步）**：
  - 当用户说"将排产计划发回ERP"、"导出到ERP"、"把未来X天的排产发给ERP"时：
    1. 直接调用 send_erp_export(days=X) 执行发送（未给 X 时默认 3 天）
    2. 发送成功后会在前端显示发送动画和成功提示

## 重排前检查流程

在执行 reschedule 之前，**必须**先调用 `query_production_context` 确认：
1. **成型机当前状态**：正在生产哪个 SKU / 正在换色 / 空闲
2. **近期停机计划**：假期和维护计划是否正确

用户交互流程示例：
```
用户: "把 DE#515894 提前到 1月25日"
       ↓
Agent: 调用 query_production_context()
       ↓
展示: "在执行重排前，请确认 3 台成型机状态：
       - ROTARY-1 / ROTARY-2 / ROTARY-3：生产哪个 SKU / 换色 / 空闲？
       近期停机计划：..."
       ↓
用户: "ROTARY-1 在换色还剩 10 小时，ROTARY-2 正在生产 S12G9C，ROTARY-3 空闲"
       ↓
Agent: 调用 query_production_context(forming_states={...}, setup_remaining_by_machine={...})
       ↓
Agent: 调用 reschedule(order_refs="DE#515894",
                      new_deadline="2026-01-25")
```

如果用户直接指定了成型机状态，也需要调用 query_production_context(forming_states=...) 完成检查后再重排。

## 排产初始化流程

当用户请求**新一轮排产**（如"开始排产"、"生成排产计划"、"初始化排产"、"更新排产"等）时，应主动询问：
1. **停机计划**："在开始排产前，请问近期是否有停机计划需要考虑？例如：
   - 设备维护保养（某台机器在某时间段停机）
   - 假期安排（全厂放假时间）"
2. 如果用户说"有"，则收集停机信息：
   - **必须确认所有日期**：如果用户说的信息不完整（如"春节放假"但没说具体日期），必须追问
   - 使用 add_holiday/add_maintenance 逐个添加
3. 如果用户说"没有"或"跳过"，继续进入重排前检查
4. **调用 query_production_context 确认成型机状态与近期停机计划**
5. **收集完所有停机计划后，必须调用 reschedule(mode="full") 执行全局重排**
6. 排产完成后，新旧排产甘特图会自动显示在右侧工作区供用户对比
"""

# Global state
_conversation_history: dict[str, list[dict[str, str]]] = {}


def seed_conversation_history(thread_id: str, history: list[dict[str, str]]) -> None:
    """Replace in-memory history for a thread (used when restoring from DB)."""
    global _conversation_history
    tid = str(thread_id or "default")
    cleaned: list[dict[str, str]] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        if role not in ("user", "assistant", "system"):
            continue
        content = str(msg.get("content") or "")
        cleaned.append({"role": role, "content": content})
    _conversation_history[tid] = cleaned

_TOOLS = [
    query_orders,
    query_orders_by_customer,
    query_container,
    query_containers_by_customer,
    reschedule,
    compare_schedules,
    get_schedule_kpi,
    get_overrides,
    set_container_override,
    clear_container_override,
    set_order_override,
    clear_order_override,
    request_downtime_form,
    add_holiday,
    add_maintenance,
    get_downtime_plans,
    delete_holiday,
    delete_maintenance,
    query_production_context,
    analyze_campaign_efficiency,
    apply_campaign_optimization,
    send_erp_export,
    get_line_config,
    update_line_config,
]
_TOOLS_BY_NAME = {t.name: t for t in _TOOLS}

# =============================================================================
# Auto tool-calling fallback (rule-based)
# =============================================================================

_UPDATE_KEYWORDS = (
    "更新排产",
    "排产更新",
    "开始排产",
    "生成排产",
    "执行排产",
    "初始化排产",
    "更新 排产",
    # Common phrases users actually type
    "重排",
    "全局重排",
    "重新排产",
    "重新排程",
    "replan",
    "reschedule",
)

_DOWNTIME_KEYWORDS = (
    "停机",
    "维护",
    "保养",
    "检修",
    "假期",
    "放假",
    "周日",
    "春节",
)

_ERP_KEYWORDS = (
    "ERP",
    "erp",
    "导出",
    "发回",
    "发送",
    "send",
    "export",
)

_ERP_CONFIRM_RE = re.compile(
    r"^\s*(?:"
    r"准备好了|已准备好|备好了|已备好|"
    r"物料(?:已)?备好|"
    r"发送|发吧|发出|确认发送|可以发送|"
    r"ok|okay"
    r")\s*[!！。.\s]*$",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEDULE_PATH = _REPO_ROOT / "process" / "schedule_result.json"

_MAINT_WINDOW_RE = re.compile(
    r"(?:(?P<y>\d{4})[./-])?(?P<m>\d{1,2})[月./-](?P<d>\d{1,2})(?:[日号])?\s*"
    r"(?P<sh>\d{1,2})[:：](?P<sm>\d{2})\s*[-~至到]\s*(?P<eh>\d{1,2})[:：](?P<em>\d{2})"
)

_FEST_RANGE_RE = re.compile(
    r"春节[^\d]{0,12}"
    r"(?:(?P<y>\d{4})[./-])?(?P<m1>\d{1,2})[./-](?P<d1>\d{1,2})\s*[-~至到]\s*"
    r"(?:(?P<y2>\d{4})[./-])?(?P<m2>\d{1,2})[./-](?P<d2>\d{1,2})"
)

_SUNDAY_START_RE = re.compile(
    r"(?:最近的)?(?:放假)?周日[^\d]{0,12}(?:(?P<y>\d{4})[./-])?(?P<m>\d{1,2})[月./-](?P<d>\d{1,2})"
)

_SHIFT_REST_RE = re.compile(
    r"(?:(?P<y>\d{4})[./-])?(?P<m>\d{1,2})[月./-](?P<d>\d{1,2})(?:[日号])?"
    r"[^\d]{0,16}(?:转班|倒班)"
)


def _load_schedule_window() -> tuple[int, datetime | None]:
    """Return (base_year, schedule_end_time) from current schedule_result.json if possible."""
    base_year = datetime.now().year
    end_time: datetime | None = None

    try:
        if _SCHEDULE_PATH.exists():
            doc = json.loads(_SCHEDULE_PATH.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
                st = meta.get("start_time")
                if isinstance(st, str) and st:
                    start_dt = datetime.fromisoformat(st)
                    base_year = start_dt.year
                    horizon_h = int(meta.get("horizon_h") or 0)
                    if horizon_h > 0:
                        end_time = start_dt + timedelta(hours=horizon_h)
    except Exception:
        pass

    return base_year, end_time


def _iter_recent_user_texts(messages: list[BaseMessage], limit: int = 8) -> list[str]:
    texts: list[str] = []
    for m in reversed(messages):
        if not isinstance(m, HumanMessage):
            continue
        c = str(getattr(m, "content", "") or "").strip()
        if not c:
            continue
        # Tool results are injected as HumanMessage; ignore them for intent parsing.
        if c.startswith("工具 ") and "返回结果" in c:
            continue
        texts.append(c)
        if len(texts) >= limit:
            break
    return list(reversed(texts))


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def _date_ymd(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


_CONTAINER_ID_RE = re.compile(r"\b([A-Z]{3}\d{6})\b")
_ORDER_ID_RE = re.compile(r"\b(\d{6,10})\b")

# Avoid `\b` here: Chinese characters are "word chars" in Python regex, so "\b" won't
# match boundaries like "...是18g9c" where the SKU touches a CJK character.
_SKU_L1_RE = re.compile(r"(?<![A-Za-z0-9])S?18(?:B1|G9)[A-Z0-9]{1,6}(?![A-Za-z0-9])", re.IGNORECASE)
_SKU_L2_RE = re.compile(r"(?<![A-Za-z0-9])S?12G9[A-Z0-9]{1,6}(?![A-Za-z0-9])", re.IGNORECASE)
_SKU_L3_RE = re.compile(r"(?<![A-Za-z0-9])S?12G8[A-Z0-9]{1,6}(?![A-Za-z0-9])", re.IGNORECASE)

# Confirmation replies in the reschedule flow (keep conservative to avoid surprising tool calls).
_CONFIRM_REPLY_RE = re.compile(
    r"^\s*(?:"
    r"确认(?:无误)?|已确认|"
    r"没问题|可以(?:了)?|"
    r"开始(?:吧)?|继续|执行|"
    r"ok|okay"
    r")\s*[!！。.\s]*$",
    re.IGNORECASE,
)


def _normalize_sku_token(token: str) -> str:
    """Normalize user-entered SKU fragments like '18g9c' into 'S18G9C'."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(token or "")).upper()
    if not s:
        return ""
    return s if s.startswith("S") else ("S" + s)


def _extract_forming_states_from_text(text: str) -> tuple[dict[str, str], dict[str, int]]:
    """Best-effort parse of forming-machine states from free text.

    Currently supports the common shorthand where users provide just the current
    SKU for each line (e.g. '18g9c, 12g9w, 12g8q').
    """
    ctx = str(text or "")
    states: dict[str, str] = {}
    setup_remaining: dict[str, int] = {}

    # 1) Explicit mapping: "ROTARY-1: 18g9c" etc (accepts ':'/'：'/'-')
    for mid in ("ROTARY-1", "ROTARY-2", "ROTARY-3"):
        m = re.search(rf"{mid}\\s*[:：\\-]\\s*([A-Za-z0-9]+)", ctx, re.IGNORECASE)
        if m:
            sku = _normalize_sku_token(m.group(1))
            if sku:
                states[mid] = f"producing:{sku}"

    # 2) Heuristic: detect known SKU families and map to their owning lines.
    m1 = _SKU_L1_RE.search(ctx)
    if m1:
        sku = _normalize_sku_token(m1.group(0))
        if sku:
            states["ROTARY-1"] = f"producing:{sku}"

    m2 = _SKU_L2_RE.search(ctx)
    if m2:
        sku = _normalize_sku_token(m2.group(0))
        if sku:
            states["ROTARY-2"] = f"producing:{sku}"

    m3 = _SKU_L3_RE.search(ctx)
    if m3:
        sku = _normalize_sku_token(m3.group(0))
        if sku:
            states["ROTARY-3"] = f"producing:{sku}"

    # 3) Fallback: if the user replies with exactly 3 SKU-like tokens on separate lines
    # (or comma/space separated), map them to ROTARY-1/2/3 by order.
    #
    # This keeps UX simple ("18G9C\n12G9W\n12G8Q") without requiring ROTARY prefixes.
    if not {"ROTARY-1", "ROTARY-2", "ROTARY-3"}.issubset(set(states)):
        tokens = re.findall(
            r"(?<![A-Za-z0-9])S?\d{2}[A-Za-z0-9]{2,10}(?![A-Za-z0-9])",
            ctx,
            flags=re.IGNORECASE,
        )
        # Remove duplicates while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for t in tokens:
            tt = t.strip()
            if not tt:
                continue
            # Avoid mistaking container IDs like "SEC515910" for SKUs.
            if _CONTAINER_ID_RE.fullmatch(tt.upper()):
                continue
            if tt.upper() in seen:
                continue
            seen.add(tt.upper())
            ordered.append(tt)
            if len(ordered) >= 3:
                break

        if len(ordered) == 3:
            for mid, tok in zip(("ROTARY-1", "ROTARY-2", "ROTARY-3"), ordered, strict=True):
                if mid in states:
                    continue
                sku = _normalize_sku_token(tok)
                if sku:
                    states[mid] = f"producing:{sku}"

    return states, setup_remaining


def _auto_tool_calls_for_common_queries(messages: list[BaseMessage]) -> list[tuple[str, dict[str, Any]]]:
    """Rule-based fallback for common read-only queries.

    This is intentionally conservative: it triggers only on clear identifiers
    to avoid surprising tool calls.
    """
    user_texts = _iter_recent_user_texts(messages, limit=3)
    if not user_texts:
        return []

    latest = user_texts[-1].strip()
    if not latest:
        return []

    # Container lookup: e.g. "EGC515529 什么时候完成？"
    m = _CONTAINER_ID_RE.search(latest.upper())
    if m:
        return [("query_container", {"container_ref": m.group(1)})]

    # Order lookup: c_orderline_id is numeric.
    m = _ORDER_ID_RE.search(latest)
    if m and ("订单" in latest or "order" in latest.lower() or len(latest.strip()) == len(m.group(1))):
        return [("query_orders", {"order_ref": m.group(1)})]

    # Production context / status
    if _contains_any(latest, ("生产上下文", "现场状态", "rotary", "ROTARY", "成型机")):
        return [("query_production_context", {})]

    # KPI quick check
    if _contains_any(latest, ("kpi", "KPI", "准时率", "延期", "迟交率")) and _contains_any(latest, ("当前", "现在", "这个")):
        return [("get_schedule_kpi", {})]

    return []


def _auto_tool_calls_for_erp_export(messages: list[BaseMessage]) -> list[tuple[str, dict[str, Any]]]:
    """Rule-based helper for ERP export flow (single-step send).

    If the latest user message explicitly asks to export/send to ERP, send immediately.
    """
    user_texts = _iter_recent_user_texts(messages, limit=10)
    if not user_texts:
        return []
    latest = user_texts[-1].strip()
    ctx = "\n".join(user_texts)

    if ("ERP" not in ctx) and ("erp" not in ctx.lower()):
        return []
    if (
        ("导出" not in ctx)
        and ("发回" not in ctx)
        and ("发送" not in ctx)
        and ("send" not in ctx.lower())
        and ("export" not in ctx.lower())
        and ("导出" not in latest)
        and ("发回" not in latest)
        and ("发送" not in latest)
        and ("send" not in latest.lower())
        and ("export" not in latest.lower())
    ):
        return []

    m = re.findall(r"(\d{1,2})\s*(?:天|days?)", latest, flags=re.IGNORECASE) or re.findall(
        r"(\d{1,2})\s*(?:天|days?)", ctx, flags=re.IGNORECASE
    )
    days = int(m[-1]) if m else 3
    days = max(1, min(30, days))
    return [("send_erp_export", {"days": days})]


def _auto_tool_calls_for_downtime_and_schedule(messages: list[BaseMessage]) -> list[tuple[str, dict[str, Any]]]:
    """Rule-based fallback: add downtime entries + run_schedule when users provide concrete dates."""
    user_texts = _iter_recent_user_texts(messages, limit=10)
    if not user_texts:
        return []

    latest = user_texts[-1]
    ctx = "\n".join(user_texts)
    # Users often reply to the assistant's "please confirm forming-machine states" prompt
    # with just the 3 current SKUs. Treat that as part of the reschedule flow.
    # Parse forming states primarily from the latest user reply to avoid accidentally
    # picking up older SKUs mentioned earlier in the conversation.
    forming_states, setup_remaining_by_machine = _extract_forming_states_from_text(latest)
    update_requested = _contains_any(ctx, _UPDATE_KEYWORDS) or bool(forming_states)
    if not update_requested:
        return []

    no_downtime_phrases = ("没有", "无", "不用", "跳过", "不需要")
    is_no_downtime_reply = _contains_any(latest, no_downtime_phrases) and (
        _contains_any(latest, _DOWNTIME_KEYWORDS) or len(latest) <= 8
    )
    is_confirm_reply = bool(_CONFIRM_REPLY_RE.match(latest.strip())) or (
        # Soft match: allow short confirmations like "好的，数据已确认！"
        (len(latest.strip()) <= 32)
        and ("确认" in latest or "已确认" in latest)
        and not _ORDER_ID_RE.search(latest)
        and not _CONTAINER_ID_RE.search(latest.upper())
    )

    # If the latest message is unrelated to update/downtime (e.g. asking about orders),
    # do not trigger the fallback.
    if not (
        _contains_any(latest, _UPDATE_KEYWORDS)
        or _contains_any(latest, _DOWNTIME_KEYWORDS)
        or is_no_downtime_reply
        or forming_states
        or is_confirm_reply
    ):
        return []

    base_year, sched_end = _load_schedule_window()

    # ----------------------------
    # Rotary state precheck (required before reschedule)
    # ----------------------------
    rotary_state: str | None = None
    setup_remaining_h: int | None = None
    ctx_upper = ctx.upper()

    if "producing_c" in ctx or "PRODUCING_C" in ctx_upper:
        rotary_state = "producing_c"
    elif "producing_w" in ctx or "PRODUCING_W" in ctx_upper:
        rotary_state = "producing_w"
    elif "producing_v" in ctx or "PRODUCING_V" in ctx_upper:
        rotary_state = "producing_v"
    elif "换色" in ctx or "SETUP" in ctx_upper:
        rotary_state = "setup"
    elif "空闲" in ctx or "IDLE" in ctx_upper:
        rotary_state = "idle"
    else:
        if re.search(r"(生产|正在生产)\s*(S12G9C|C)\b", ctx, re.IGNORECASE):
            rotary_state = "producing_c"
        elif re.search(r"(生产|正在生产)\s*(S12G9W|W)\b", ctx, re.IGNORECASE):
            rotary_state = "producing_w"
        elif re.search(r"(生产|正在生产)\s*(S12G9V|V)\b", ctx, re.IGNORECASE):
            rotary_state = "producing_v"

    if rotary_state == "setup":
        m = re.search(r"(?:剩余|还要|还有|需)\D{0,6}(\d{1,2})\s*(?:h|小时)", ctx, re.IGNORECASE)
        if m:
            try:
                setup_remaining_h = int(m.group(1))
            except Exception:
                setup_remaining_h = None

    # ----------------------------
    # Maintenance (LABEL-3/LABEL-5)
    # ----------------------------
    maint_requested = ("维护" in ctx) or ("停机" in ctx and ("贴标" in ctx or "LABEL" in ctx))
    maint_match = _MAINT_WINDOW_RE.search(ctx)
    maint_start: str | None = None
    maint_end: str | None = None
    if maint_match:
        y = int(maint_match.group("y") or base_year)
        m = int(maint_match.group("m"))
        d = int(maint_match.group("d"))
        sh = int(maint_match.group("sh"))
        sm = int(maint_match.group("sm"))
        eh = int(maint_match.group("eh"))
        em = int(maint_match.group("em"))
        maint_start = f"{y:04d}-{m:02d}-{d:02d}T{sh:02d}:{sm:02d}"
        maint_end = f"{y:04d}-{m:02d}-{d:02d}T{eh:02d}:{em:02d}"

    machines: list[str] = []
    if "LABEL-3" in ctx:
        machines.append("LABEL-3")
    if "LABEL-5" in ctx:
        machines.append("LABEL-5")
    if (not machines) and ("两台贴标" in ctx or "两台贴标机" in ctx or "两台贴标" in latest):
        machines = ["LABEL-3", "LABEL-5"]

    # ----------------------------
    # Spring Festival holiday
    # ----------------------------
    fest_requested = "春节" in ctx
    fest_start: str | None = None
    fest_end: str | None = None
    fest_match = _FEST_RANGE_RE.search(ctx)
    if fest_match:
        y1 = int(fest_match.group("y") or base_year)
        y2 = int(fest_match.group("y2") or y1)
        m1 = int(fest_match.group("m1"))
        d1 = int(fest_match.group("d1"))
        m2 = int(fest_match.group("m2"))
        d2 = int(fest_match.group("d2"))
        fest_start = _date_ymd(y1, m1, d1)
        fest_end = _date_ymd(y2, m2, d2)

    # ----------------------------
    # One-off shift-change rest day (e.g. "2-1 全天转班休息")
    # ----------------------------
    shift_rest_day: str | None = None
    shift_match = _SHIFT_REST_RE.search(ctx)
    if shift_match:
        y = int(shift_match.group("y") or base_year)
        m = int(shift_match.group("m"))
        d = int(shift_match.group("d"))
        shift_rest_day = _date_ymd(y, m, d)

    # ----------------------------
    # Bi-weekly Sundays
    # ----------------------------
    biweekly_requested = ("每两周" in ctx and "周日" in ctx) or ("隔周" in ctx and "周日" in ctx)
    sunday_start: str | None = None
    sunday_match = _SUNDAY_START_RE.search(ctx)
    if sunday_match:
        y = int(sunday_match.group("y") or base_year)
        m = int(sunday_match.group("m"))
        d = int(sunday_match.group("d"))
        sunday_start = _date_ymd(y, m, d)

    # Decide whether we can run_schedule now.
    missing = False
    if maint_requested and (not maint_match or not machines):
        missing = True
    if biweekly_requested and not sunday_start:
        missing = True
    if fest_requested and not (fest_start and fest_end):
        missing = True

    # If the user is explicitly confirming, attempt the full reschedule using the last
    # confirmed production-context check (persisted by query_production_context).
    # NOTE: Don't call query_production_context here; calling it with empty args can
    # overwrite the stored check.
    if is_confirm_reply and not missing and not forming_states:
        return [("reschedule", {"mode": "full"})]

    tool_calls: list[tuple[str, dict[str, Any]]] = []

    # Apply maintenance if we have concrete window + machine list.
    if maint_start and maint_end and machines:
        for mid in machines:
            tool_calls.append((
                "add_maintenance",
                {
                    "machine_id": mid,
                    "reason": "停机维护",
                    "start": maint_start,
                    "end": maint_end,
                },
            ))

    # Apply Spring Festival holiday if we have concrete range.
    if fest_start and fest_end:
        tool_calls.append(("add_holiday", {"name": "春节", "start": fest_start, "end": fest_end}))

    if shift_rest_day:
        tool_calls.append(("add_holiday", {"name": "转班休息", "start": shift_rest_day, "end": shift_rest_day}))

    # Apply bi-weekly Sunday holidays within current schedule horizon (if available), default 90 days.
    if biweekly_requested and sunday_start:
        try:
            start_dt = datetime.fromisoformat(sunday_start)
            end_dt = sched_end or (start_dt + timedelta(days=90))

            fest_start_dt = datetime.fromisoformat(fest_start) if fest_start else None
            fest_end_dt = datetime.fromisoformat(fest_end) if fest_end else None

            d = start_dt
            while d.date() <= end_dt.date():
                # Skip Sundays fully covered by Spring Festival range (avoid redundant entries like 2/22).
                if fest_start_dt and fest_end_dt and fest_start_dt.date() <= d.date() <= fest_end_dt.date():
                    d += timedelta(days=14)
                    continue
                tool_calls.append((
                    "add_holiday",
                    {
                        "name": "隔周周日休息",
                        "start": d.date().isoformat(),
                        "end": d.date().isoformat(),
                    },
                ))
                d += timedelta(days=14)
        except Exception:
            # If parsing fails, let LLM handle clarification.
            pass

    # Run schedule when requested items are complete:
    # - after we applied any downtime changes, OR
    # - user explicitly indicates "no downtime" and wants to proceed.
    # For any "update/reschedule" intent, start by surfacing the production-context check.
    # This reduces hallucinated confirmations because reschedule tools are gated on this check.
    if not missing:
        qc_args: dict[str, Any] = {}
        if rotary_state:
            qc_args["rotary_state"] = rotary_state
            if rotary_state == "setup" and setup_remaining_h is not None:
                qc_args["setup_remaining_h"] = setup_remaining_h
        if forming_states:
            qc_args["forming_states"] = forming_states
        if setup_remaining_by_machine:
            qc_args["setup_remaining_by_machine"] = setup_remaining_by_machine
        tool_calls.append(("query_production_context", qc_args))

        # Only run reschedule when forming state is plausibly complete.
        # (The reschedule tool itself will still gate on an explicit production-context check.)
        has_all_rotaries = {"ROTARY-1", "ROTARY-2", "ROTARY-3"}.issubset(set(forming_states))
        # Legacy: only run reschedule when rotary_state is confirmed (setup requires remaining hours).
        legacy_ok = bool(rotary_state) and (rotary_state != "setup" or setup_remaining_h is not None)
        if has_all_rotaries or legacy_ok:
            tool_calls.append(("reschedule", {"mode": "full"}))

    # Safety cap to avoid runaway batches, but try hard not to drop the final
    # "query_production_context" / "reschedule" steps (those make the flow deterministic).
    max_batch = 12
    if len(tool_calls) <= max_batch:
        return tool_calls

    tail: list[tuple[str, dict[str, Any]]] = []
    while tool_calls and tool_calls[-1][0] in ("reschedule", "query_production_context"):
        tail.insert(0, tool_calls.pop())

    head_cap = max(0, max_batch - len(tail))
    return tool_calls[:head_cap] + tail


def _get_llm_config() -> dict[str, Any]:
    """Get LLM configuration from environment."""
    # Prefer explicit LLM_* settings. Fall back to legacy env vars for compatibility.
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY (preferred) or GOOGLE_API_KEY/OPENAI_API_KEY environment variable is not set")

    model_name = os.getenv("LLM_MODEL", "gemini-3-flash-preview")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    base_url = os.getenv("LLM_API_BASE") or os.getenv("GOOGLE_API_BASE") or os.getenv("OPENAI_API_BASE") or ""

    # If the user explicitly set OPENAI_API_KEY but didn't set any base, default to OpenAI.
    if not base_url and os.getenv("OPENAI_API_KEY") and not os.getenv("LLM_API_KEY"):
        base_url = "https://api.openai.com/v1"
    if not base_url:
        raise ValueError("LLM_API_BASE (preferred) or GOOGLE_API_BASE/OPENAI_API_BASE environment variable is not set")

    if base_url and not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    return {
        "api_key": api_key,
        "model": model_name,
        "temperature": temperature,
        "base_url": base_url,
    }


def _call_llm(messages: list[dict[str, str]]) -> str:
    """Call the LLM API directly with requests."""
    config = _get_llm_config()

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": config["temperature"],
    }

    url = f"{config['base_url']}/chat/completions"

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        # Try to include provider error payload to help debug auth/base_url issues.
        detail = ""
        try:
            if "response" in locals() and response is not None:  # type: ignore[name-defined]
                ct = response.headers.get("content-type", "")
                if ct.startswith("application/json"):
                    detail = json.dumps(response.json(), ensure_ascii=False)[:400]  # type: ignore[name-defined]
                else:
                    detail = (response.text or "")[:400]  # type: ignore[name-defined]
        except Exception:
            detail = ""
        suffix = f" | detail={detail}" if detail else ""
        raise RuntimeError(f"LLM API 调用失败: {str(e)}{suffix}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"LLM 响应解析失败: {str(e)}")


def _execute_tool(tool_name: str, args: dict[str, Any]) -> str:
    """Execute a tool and return the result."""
    try:
        tool = _TOOLS_BY_NAME.get(tool_name)
        if not tool:
            return f"未知工具: {tool_name}"
        return tool.invoke(args)
    except Exception as e:
        return f"工具执行错误: {str(e)}"


def _extract_tool_call(text: str) -> tuple[Optional[str], Optional[dict[str, Any]], str]:
    """Extract tool call from LLM response.

    Returns:
        (tool_name, args, text_before_tool_call)
    """
    # Match <tool_call>...</tool_call>
    pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
    match = re.search(pattern, text, re.DOTALL)

    if not match:
        return None, None, text

    text_before = text[:match.start()].strip()

    try:
        call_data = json.loads(match.group(1))
        tool_name = call_data.get("tool")
        args = call_data.get("args", {})
        return tool_name, args, text_before
    except json.JSONDecodeError:
        return None, None, text


def _extract_all_tool_calls(text: str) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """Extract ALL tool calls from LLM response.

    Returns:
        (list of (tool_name, args), text_before_first_tool_call)
    """
    pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
    matches = list(re.finditer(pattern, text, re.DOTALL))

    if not matches:
        return [], text

    text_before = text[:matches[0].start()].strip()
    tool_calls = []

    for match in matches:
        try:
            call_data = json.loads(match.group(1))
            tool_name = call_data.get("tool")
            args = call_data.get("args", {})
            if tool_name:
                tool_calls.append((tool_name, args))
        except json.JSONDecodeError:
            continue

    return tool_calls, text_before


def _history_to_langchain_messages(history: list[dict[str, str]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for msg in history[-10:]:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        else:
            out.append(HumanMessage(content=str(content)))
    return out


class _OpenAICompatChatModel(BaseChatModel):
    """Minimal ChatModel wrapper around an OpenAI-compatible /chat/completions API."""

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat_completions"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        api_messages: list[dict[str, str]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, HumanMessage):
                role = "user"
            elif isinstance(m, AIMessage):
                role = "assistant"
            else:
                role = "user"
            api_messages.append({"role": role, "content": str(m.content)})

        content = _call_llm(api_messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await asyncio.to_thread(self._generate, messages, stop, run_manager, **kwargs)


_CHAT_MODEL: BaseChatModel = _OpenAICompatChatModel()


def _append_rendered(rendered: str, addition: str) -> str:
    if not addition:
        return rendered
    if not rendered:
        return addition
    return rendered + "\n" + addition


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    rendered: str
    pending_tool_name: Optional[str]
    pending_tool_args: Optional[dict[str, Any]]
    last_tool_result: Optional[str]
    tool_iterations: int
    max_tool_iterations: int
    auto_plan_used: bool
    executed_tool_calls: list[str]


def _node_init(state: AgentState) -> dict[str, Any]:
    return {
        "rendered": "",
        "pending_tool_name": None,
        "pending_tool_args": None,
        "last_tool_result": None,
        "tool_iterations": 0,
        "max_tool_iterations": state.get("max_tool_iterations", MAX_TOOL_ITERATIONS),
        "auto_plan_used": False,
        # Guardrail: prevent infinite loops where the model repeats the same tool call
        # (same tool name + same args) within a single request.
        "executed_tool_calls": [],
    }


def _dedupe_tool_calls(tool_calls: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Remove duplicate (tool,args) pairs while preserving order."""
    seen: set[str] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    for name, args in tool_calls:
        try:
            key = json.dumps({"tool": name, "args": args or {}}, sort_keys=True, ensure_ascii=False)
        except Exception:
            key = f"{name}:{str(args)}"
        if key in seen:
            continue
        seen.add(key)
        out.append((name, args or {}))
    return out


def _node_call_model(state: AgentState) -> dict[str, Any]:
    history = list(state.get("messages") or [])
    response = _CHAT_MODEL.invoke([SystemMessage(content=SYSTEM_PROMPT), *history])
    return {"messages": [response]}


def _node_parse_and_route(state: AgentState) -> dict[str, Any]:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    content = str(getattr(last, "content", "") or "")

    tool_calls, text_before = _extract_all_tool_calls(content)
    rendered = state.get("rendered") or ""

    # Deterministic ERP export flow (KISS, single tool):
    # - User asks "把未来X天导出/发送到 ERP" -> call send_erp_export(days=X) immediately
    if (
        (state.get("tool_iterations", 0) < state.get("max_tool_iterations", MAX_TOOL_ITERATIONS))
        and (not state.get("auto_plan_used", False))
    ):
        user_texts = _iter_recent_user_texts(messages, limit=10)
        latest_user = (user_texts or [""])[-1].strip()
        ctx = "\n".join(user_texts)
        erp_in_latest = ("ERP" in latest_user) or ("erp" in latest_user.lower())
        latest_lower = latest_user.lower()
        has_erp_intent = erp_in_latest and (
            ("导出" in latest_user)
            or ("发回" in latest_user)
            or ("发送" in latest_user)
            or ("send" in latest_lower)
            or ("export" in latest_lower)
        )

        if has_erp_intent:
            erp_calls = _dedupe_tool_calls(_auto_tool_calls_for_erp_export(messages))
            if erp_calls:
                if text_before:
                    rendered = _append_rendered(rendered, text_before)
                first_tool_name, first_tool_args = erp_calls[0]
                return {
                    "rendered": rendered,
                    "pending_tool_name": first_tool_name,
                    "pending_tool_args": first_tool_args or {},
                    "pending_tool_calls": erp_calls[1:] if len(erp_calls) > 1 else [],
                    "last_tool_result": None,
                    "auto_plan_used": True,
                }

    # Deterministic fallback for common "read" queries (avoid hallucinations when the model
    # answers without tools, and reduce tool-call storms).
    if (not tool_calls) and (state.get("tool_iterations", 0) < state.get("max_tool_iterations", MAX_TOOL_ITERATIONS)):
        auto_calls = _auto_tool_calls_for_common_queries(messages)
        if auto_calls:
            if text_before:
                rendered = _append_rendered(rendered, text_before)
            auto_calls = _dedupe_tool_calls(auto_calls)
            first_tool_name, first_tool_args = auto_calls[0]
            return {
                "rendered": rendered,
                "pending_tool_name": first_tool_name,
                "pending_tool_args": first_tool_args or {},
                "pending_tool_calls": auto_calls[1:] if len(auto_calls) > 1 else [],
                "last_tool_result": None,
            }

    # Deterministic override for "更新排产/停机计划" flow:
    # Prefer a rule-based plan over the model's tool calls to avoid tool-call storms and hallucinated confirmations.
    if (state.get("tool_iterations", 0) < state.get("max_tool_iterations", MAX_TOOL_ITERATIONS)) and (not state.get("auto_plan_used", False)):
        auto_calls = _auto_tool_calls_for_downtime_and_schedule(messages)
        if auto_calls:
            # Deterministic UX copy for the scheduling update flow (KISS: plain text prompt).
            # Only show this on the initial "更新排产" request to avoid repeating it on follow-up replies.
            latest_user = (_iter_recent_user_texts(messages, limit=1) or [""])[-1]
            if _contains_any(latest_user, _UPDATE_KEYWORDS):
                rendered = _append_rendered(
                    rendered,
                    (
                        "更新排产前请先确认这些信息（直接回复一条消息即可）：\n"
                        "1) 三台成型机当前状态（正在生产哪个 SKU / 换型(剩余X小时) / 空闲 / 停机维护）\n"
                        "   - ROTARY-1：？\n"
                        "   - ROTARY-2：？\n"
                        "   - ROTARY-3：？\n"
                        "2) 近期停机/维护计划（如果有）：设备 + 开始时间 + 结束时间；没有就回复“无”\n"
                        "3) 近期假期安排（如果有）：假期名称 + 开始日期 + 结束日期；没有就回复“无”\n"
                        "确认后我会执行重排。"
                    ),
                )
                # IMPORTANT: don't execute any tools on the initial "更新排产" click.
                # Otherwise we'd call `query_production_context()` with empty args, which may reuse a
                # previously-confirmed state and allow an immediate `reschedule` without user input.
                # If the user included concrete statuses/dates in the same message, allow the normal tool flow.
                has_any_details = (
                    bool(_extract_forming_states_from_text(latest_user)[0])
                    or _contains_any(latest_user, _DOWNTIME_KEYWORDS)
                    or bool(_MAINT_WINDOW_RE.search(latest_user))
                    or bool(_FEST_RANGE_RE.search(latest_user))
                    or bool(_SUNDAY_START_RE.search(latest_user))
                )
                if not has_any_details:
                    return {
                        "rendered": rendered,
                        "pending_tool_name": None,
                        "pending_tool_args": None,
                        "pending_tool_calls": [],
                        "last_tool_result": None,
                        "auto_plan_used": True,
                    }
            # For scheduling/downtime intents, do NOT merge in LLM-emitted tool calls.
            # The rule-based plan already handles the required prechecks and confirmation gating.
            # Merging can let the LLM queue `reschedule` in the same turn.
            auto_calls = _dedupe_tool_calls(auto_calls)
            first_tool_name, first_tool_args = auto_calls[0]

            return {
                "rendered": rendered,
                "pending_tool_name": first_tool_name,
                "pending_tool_args": first_tool_args or {},
                "pending_tool_calls": auto_calls[1:] if len(auto_calls) > 1 else [],
                "last_tool_result": None,
                "auto_plan_used": True,
            }

    if tool_calls and (state.get("tool_iterations", 0) < state.get("max_tool_iterations", MAX_TOOL_ITERATIONS)):
        if text_before:
            rendered = _append_rendered(rendered, text_before)
        # Take the first tool call, store remaining for later
        tool_calls = _dedupe_tool_calls(tool_calls)
        first_tool_name, first_tool_args = tool_calls[0]
        return {
            "rendered": rendered,
            "pending_tool_name": first_tool_name,
            "pending_tool_args": first_tool_args or {},
            "pending_tool_calls": tool_calls[1:] if len(tool_calls) > 1 else [],
            "last_tool_result": None,
        }

    # Fallback: if the model didn't emit tool_call tags, try a small rule-based planner
    # for downtime + schedule update requests to avoid "hallucinated" confirmations.
    if (not tool_calls) and (state.get("tool_iterations", 0) < state.get("max_tool_iterations", MAX_TOOL_ITERATIONS)) and (not state.get("auto_plan_used", False)):
        auto_calls = _auto_tool_calls_for_downtime_and_schedule(messages)
        if auto_calls:
            auto_calls = _dedupe_tool_calls(auto_calls)
            first_tool_name, first_tool_args = auto_calls[0]
            return {
                "rendered": rendered,
                "pending_tool_name": first_tool_name,
                "pending_tool_args": first_tool_args or {},
                "pending_tool_calls": auto_calls[1:] if len(auto_calls) > 1 else [],
                "last_tool_result": None,
                "auto_plan_used": True,
            }

    # No tool calls or exceeded iteration limit
    # Strip tool_call tags from content to avoid showing raw tags to user
    clean_content = re.sub(r'<tool_call>\s*\{.*?\}\s*</tool_call>', '', content, flags=re.DOTALL).strip()
    tool_iterations = int(state.get("tool_iterations", 0))
    max_tool_iterations = int(state.get("max_tool_iterations", MAX_TOOL_ITERATIONS))
    limit_reached = tool_iterations >= max_tool_iterations
    has_tool_call_tags = ("<tool_call>" in content) and ("</tool_call>" in content)
    fallback = (
        "(工具调用次数已达上限)"
        if limit_reached
        else "(工具调用解析失败，请重试。)"
        if has_tool_call_tags
        else "(未生成有效回复)"
    )
    rendered = _append_rendered(rendered, clean_content or text_before or fallback)
    return {
        "rendered": rendered,
        "pending_tool_name": None,
        "pending_tool_args": None,
        "pending_tool_calls": [],
        "last_tool_result": None,
    }


def _route_after_parse(state: AgentState) -> str:
    return "tool" if state.get("pending_tool_name") else "end"


def _node_call_tool(state: AgentState) -> dict[str, Any]:
    tool_name = state.get("pending_tool_name") or ""
    tool_args = state.get("pending_tool_args") or {}
    pending_tool_calls = state.get("pending_tool_calls") or []

    # Enforce iteration cap even when the model emitted a long batch of tool calls.
    tool_iterations = int(state.get("tool_iterations", 0))
    max_tool_iterations = int(state.get("max_tool_iterations", MAX_TOOL_ITERATIONS))
    if tool_iterations >= max_tool_iterations:
        stop_msg = HumanMessage(content="(工具调用次数已达上限，已停止执行剩余工具。)")
        return {
            "messages": [stop_msg],
            "pending_tool_name": None,
            "pending_tool_args": None,
            "pending_tool_calls": [],
            "last_tool_result": None,
            "tool_iterations": tool_iterations,
        }

    # Skip repeated tool calls within the same request to avoid infinite loops.
    try:
        sig = json.dumps({"tool": tool_name, "args": tool_args or {}}, sort_keys=True, ensure_ascii=False)
    except Exception:
        sig = f"{tool_name}:{str(tool_args)}"
    executed = list(state.get("executed_tool_calls") or [])
    if sig in executed:
        tool_msg = HumanMessage(content=f"工具 {tool_name} 返回结果：\n(已跳过重复工具调用，避免循环。)")
        if pending_tool_calls:
            next_tool_name, next_tool_args = pending_tool_calls[0]
            remaining_calls = pending_tool_calls[1:] if len(pending_tool_calls) > 1 else []
            return {
                "messages": [tool_msg],
                "pending_tool_name": next_tool_name,
                "pending_tool_args": next_tool_args or {},
                "pending_tool_calls": remaining_calls,
                "last_tool_result": "(skipped_duplicate)",
                "tool_iterations": (state.get("tool_iterations") or 0) + 1,
                "executed_tool_calls": executed,
            }
        return {
            "messages": [tool_msg],
            "pending_tool_name": None,
            "pending_tool_args": None,
            "pending_tool_calls": [],
            "last_tool_result": "(skipped_duplicate)",
            "tool_iterations": int(state.get("tool_iterations", 0)) + 1,
            "executed_tool_calls": executed,
        }

    tool_result = _execute_tool(tool_name, tool_args)
    tool_msg = HumanMessage(content=f"工具 {tool_name} 返回结果：\n{tool_result}")
    executed.append(sig)

    # Check if there are more tools to execute
    if pending_tool_calls:
        next_tool_name, next_tool_args = pending_tool_calls[0]
        remaining_calls = pending_tool_calls[1:] if len(pending_tool_calls) > 1 else []

        # Keep tool results in messages so the model can see what was applied (reduces duplicate tool calls).
        return {
            "messages": [tool_msg],
            "pending_tool_name": next_tool_name,
            "pending_tool_args": next_tool_args or {},
            "pending_tool_calls": remaining_calls,
            "last_tool_result": tool_result,
            "tool_iterations": (state.get("tool_iterations") or 0) + 1,
            "executed_tool_calls": executed,
        }

    return {
        "messages": [tool_msg],
        "pending_tool_name": None,
        "pending_tool_args": None,
        "pending_tool_calls": [],
        "last_tool_result": tool_result,
        "tool_iterations": int(state.get("tool_iterations", 0)) + 1,
        "auto_plan_used": state.get("auto_plan_used", False),  # 保持状态传递，防止规则引擎重复触发
        "executed_tool_calls": executed,
    }


def _build_agent_graph():
    g = StateGraph(AgentState)
    g.add_node("init", _node_init)
    g.add_node("llm", _node_call_model)
    g.add_node("parse", _node_parse_and_route)
    g.add_node("tool", _node_call_tool)
    g.set_entry_point("init")
    g.add_edge("init", "llm")
    g.add_edge("llm", "parse")
    g.add_conditional_edges("parse", _route_after_parse, {"tool": "tool", "end": END})
    # After tool execution, check if more tools are pending
    g.add_conditional_edges(
        "tool",
        lambda s: "tool" if s.get("pending_tool_name") else "llm",
        {"tool": "tool", "llm": "llm"},
    )
    return g.compile()


_AGENT_GRAPH = _build_agent_graph()


def generate_reply(message: str, thread_id: str = "default") -> str:
    """Generate a reply for the given message.

    Args:
        message: User message
        thread_id: Conversation thread ID for maintaining context

    Returns:
        Assistant's reply text
    """
    global _conversation_history

    # Initialize conversation history for this thread
    if thread_id not in _conversation_history:
        _conversation_history[thread_id] = []

    # Add user message to history
    _conversation_history[thread_id].append({"role": "user", "content": message})

    try:
        # Build LangChain messages with history (keep last 10)
        lc_messages = _history_to_langchain_messages(_conversation_history[thread_id])

        # Run LangGraph agent once (internal tool loop handled by graph)
        final_state: AgentState = _AGENT_GRAPH.invoke(
            {"messages": lc_messages, "max_tool_iterations": MAX_TOOL_ITERATIONS}
        )

        final_response = str(final_state.get("rendered") or "").strip()
        if not final_response:
            # Fallback: use the last AI message content if graph didn't render anything.
            msgs = final_state.get("messages") or []
            last = msgs[-1] if msgs else None
            final_response = str(getattr(last, "content", "") or "").strip()

        # Add assistant response to history
        _conversation_history[thread_id].append({"role": "assistant", "content": final_response})

        return final_response

    except Exception as e:
        error_msg = str(e)
        if "API_KEY" in error_msg.upper():
            return "系统配置错误：未设置 API KEY 环境变量。请联系管理员。"
        return f"抱歉，处理您的请求时出现了问题：{error_msg}"


async def agenerate_reply(message: str, thread_id: str = "default") -> str:
    """Async version of generate_reply."""
    return await asyncio.to_thread(generate_reply, message, thread_id)


async def astream_agent_events(message: str, thread_id: str = "default") -> AsyncIterator[dict[str, Any]]:
    """Stream agent execution as structured events.

    Yields dict payloads compatible with the FastAPI SSE endpoint schema:
    - {type: "content", content: "..."}
    - {type: "tool_call", tool_name, tool_input}
    - {type: "tool_result", tool_output}
    - {type: "done"}
    - {type: "error", content}
    """
    global _conversation_history

    if thread_id not in _conversation_history:
        _conversation_history[thread_id] = []
    _conversation_history[thread_id].append({"role": "user", "content": message})

    rendered_sent = ""

    try:
        lc_messages = _history_to_langchain_messages(_conversation_history[thread_id])

        async for update in _AGENT_GRAPH.astream(
            {"messages": lc_messages, "max_tool_iterations": MAX_TOOL_ITERATIONS},
            stream_mode="updates",
        ):
            if not isinstance(update, dict):
                continue
            if "parse" in update and isinstance(update["parse"], dict):
                parse_update = update["parse"]
                rendered = str(parse_update.get("rendered") or "")
                if rendered and rendered != rendered_sent:
                    delta = rendered[len(rendered_sent) :] if rendered.startswith(rendered_sent) else rendered
                    if delta:
                        yield {"type": "content", "content": delta}
                    rendered_sent = rendered

                tool_name = parse_update.get("pending_tool_name")
                if tool_name:
                    yield {
                        "type": "tool_call",
                        "tool_name": tool_name,
                        "tool_input": parse_update.get("pending_tool_args") or {},
                    }

            if "tool" in update and isinstance(update["tool"], dict):
                tool_update = update["tool"]
                if tool_update.get("last_tool_result") is not None:
                    yield {"type": "tool_result", "tool_output": str(tool_update.get("last_tool_result") or "")}

                # Check if there's another tool call queued
                next_tool_name = tool_update.get("pending_tool_name")
                if next_tool_name:
                    yield {
                        "type": "tool_call",
                        "tool_name": next_tool_name,
                        "tool_input": tool_update.get("pending_tool_args") or {},
                    }

        # Persist assistant reply to history
        final_reply = rendered_sent.strip()
        if not final_reply:
            final_reply = "(未生成有效回复)"
        _conversation_history[thread_id].append({"role": "assistant", "content": final_reply})

        yield {"type": "done"}

    except Exception as e:
        yield {"type": "error", "content": f"错误: {str(e)}"}


def clear_conversation(thread_id: str = "default") -> None:
    """Clear conversation history for a thread."""
    global _conversation_history
    if thread_id in _conversation_history:
        _conversation_history[thread_id] = []
