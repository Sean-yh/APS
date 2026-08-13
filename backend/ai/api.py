"""FastAPI backend for the scheduling AI assistant.

This module provides:
1. POST /api/chat - Synchronous chat endpoint
2. POST /api/chat/stream - SSE streaming chat endpoint
3. GET /api/schedule - Get current schedule result
4. GET /api/schedule/gantt - Get Gantt chart HTML
5. GET /health - Health check
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .agent import HELP_TEXT, agenerate_reply, astream_agent_events, generate_reply, seed_conversation_history
from .data import DEFAULT_SCHEDULE_PATH, load_schedule, aggregate_containers
from .calendar_store import (
    VALID_MACHINE_IDS,
    add_holiday as _calendar_add_holiday,
    add_maintenance as _calendar_add_maintenance,
    delete_holiday as _calendar_delete_holiday,
    delete_maintenance as _calendar_delete_maintenance,
    load_calendar as _calendar_load_calendar,
)
from .erp_client import GxErpClient, GxErpConfig
from .db_store import (
    add_chat_message,
    db_enabled,
    ensure_chat_session,
    get_document_payload,
    list_chat_messages,
    upsert_document_payload,
)
from .persistence import bootstrap_process_cache

# Paths
REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESS_DIR = REPO_ROOT / "process"
GANTT_PATH = PROCESS_DIR / "schedule_gantt.html"
ERP_ORDERS_PATH = PROCESS_DIR / "orders_erp.json"
ERP_INVENTORY_PATH = PROCESS_DIR / "inventory_erp.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            tmp_path = tmp.name

        shutil.move(tmp_path, str(path))
        tmp_path = None
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def _safe_load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """App lifespan hook (startup/shutdown).

    We keep startup non-blocking: bootstrap the DB<->process cache in a background
    thread so the server can accept requests immediately.
    """
    if db_enabled():
        try:
            # Ensure core tables exist (first-boot friendliness). Alembic still
            # owns schema migrations; this is best-effort.
            try:
                from .db import ensure_schema

                ensure_schema()
            except Exception:
                pass

            import threading

            def _run() -> None:
                try:
                    bootstrap_process_cache()
                except Exception:
                    pass

            threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass

    yield


# FastAPI app
app = FastAPI(
    title="L2 排产 AI 助手",
    description="基于 LangChain + Gemini 的智能排产交互系统",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
_sessions: dict[str, list[dict[str, Any]]] = {}


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(default=None, description="Session ID for conversation history")


class ChatResponse(BaseModel):
    """Chat response model."""

    session_id: str
    reply: str


class StreamEvent(BaseModel):
    """SSE stream event model."""

    type: str  # "content", "tool_call", "tool_result", "done", "error"
    content: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[str] = None


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


# =============================================================================
# GX ERP APIs (live data pull)
# =============================================================================


def _gx_client() -> GxErpClient:
    cfg = GxErpConfig.from_env()
    if not cfg.api_url:
        raise HTTPException(status_code=500, detail="GX_ERP_API_URL is not configured")
    if not cfg.token:
        raise HTTPException(status_code=500, detail="GX_ERP_TOKEN is not configured")
    return GxErpClient(cfg)


@app.get("/api/erp/orders")
def erp_orders(is_test: Optional[bool] = Query(default=None, alias="isTest")) -> dict[str, Any]:
    """Fetch orders from GX ERP and return in local orders.json format."""
    client = _gx_client()
    try:
        return client.orders_payload(is_test=is_test)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch ERP orders: {type(e).__name__}: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse ERP orders: {type(e).__name__}: {e}")


@app.get("/api/erp/inventory")
def erp_inventory(is_test: Optional[bool] = Query(default=None, alias="isTest")) -> dict[str, Any]:
    """Fetch inventory from GX ERP and return in local inventory.json format."""
    client = _gx_client()
    try:
        return client.inventory_payload(is_test=is_test)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch ERP inventory: {type(e).__name__}: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse ERP inventory: {type(e).__name__}: {e}")


@app.get("/api/erp/demand-history")
def erp_demand_history(is_test: Optional[bool] = Query(default=None, alias="isTest")) -> dict[str, Any]:
    """Fetch demand history from GX ERP."""
    client = _gx_client()
    try:
        rows = client.fetch_demand_history(is_test=is_test)
        return {"timestamp": datetime.now().isoformat(), "data": rows}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch ERP demand history: {type(e).__name__}: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to parse ERP demand history: {type(e).__name__}: {e}")


@app.post("/api/erp/sync")
def erp_sync(
    is_test: Optional[bool] = Query(default=None, alias="isTest"),
    regenerate: bool = Query(default=False, description="After syncing, regenerate schedule/gantt from the new snapshot"),
) -> dict[str, Any]:
    """Fetch orders/inventory from GX ERP and write into process/ for scheduling scripts."""
    client = _gx_client()
    try:
        orders_payload = client.orders_payload(is_test=is_test)
        inventory_payload = client.inventory_payload(is_test=is_test)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to sync from ERP: {type(e).__name__}: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to sync from ERP: {type(e).__name__}: {e}")

    try:
        _atomic_write_json(ERP_ORDERS_PATH, orders_payload)
        _atomic_write_json(ERP_INVENTORY_PATH, inventory_payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write ERP snapshot: {type(e).__name__}: {e}")

    # Persist snapshots to DB as well (Railway FS is ephemeral).
    try:
        upsert_document_payload("erp_orders", orders_payload)
        upsert_document_payload("erp_inventory", inventory_payload)
    except Exception:
        pass

    schedule_info: dict[str, Any] | None = None
    if regenerate:
        # Best-effort: regenerate the combined ALL-lines schedule from the fresh snapshot.
        try:
            from process.multiline import generate_all_lines, write_schedule_artifacts  # type: ignore

            line_schedules, combined = generate_all_lines(apply_downtime=True)
            write_schedule_artifacts(schedule=combined, schedule_path=DEFAULT_SCHEDULE_PATH, gantt_path=GANTT_PATH)
            try:
                upsert_document_payload("schedule_result", combined)
                if GANTT_PATH.exists():
                    upsert_document_payload("schedule_gantt_html", GANTT_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
            schedule_info = {
                "line_schedules": sorted(line_schedules.keys()),
                "orders_total": int((combined.get("kpi") or {}).get("orders_total") or 0),
                "containers_total": int((combined.get("kpi") or {}).get("containers_total") or 0),
                "start_time": (combined.get("meta") or {}).get("start_time"),
            }
        except Exception as e:
            schedule_info = {"error": f"{type(e).__name__}: {e}"}

    return {
        "success": True,
        "orders_path": str(ERP_ORDERS_PATH.relative_to(REPO_ROOT)),
        "inventory_path": str(ERP_INVENTORY_PATH.relative_to(REPO_ROOT)),
        "orders_count": len(orders_payload.get("data") or []),
        "inventory_count": len(inventory_payload.get("data") or []),
        "timestamp": orders_payload.get("timestamp") or datetime.now().isoformat(),
        "schedule": schedule_info,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Synchronous chat endpoint.

    Returns the complete response after processing.
    """
    session_id = req.session_id or str(uuid.uuid4())

    # Restore conversation from DB (if configured) so restarts don't lose memory.
    if db_enabled():
        ensure_chat_session(session_id)
        seed_conversation_history(session_id, list_chat_messages(session_id, limit=200))
        add_chat_message(session_id, role="user", content=req.message)

    _sessions.setdefault(session_id, [])
    _sessions[session_id].append({"role": "user", "content": req.message})

    try:
        reply = await agenerate_reply(req.message, thread_id=session_id)
    except Exception as e:
        reply = f"系统内部错误：{str(e)}"

    if db_enabled():
        add_chat_message(session_id, role="assistant", content=reply)

    _sessions[session_id].append({"role": "assistant", "content": reply})
    return ChatResponse(session_id=session_id, reply=reply)


async def _stream_agent_response(message: str, session_id: str) -> AsyncIterator[dict]:
    """Stream agent response as SSE events.

    使用 LangGraph agent 的结构化事件（content/tool_call/tool_result），
    并对 content 做轻量分段以模拟打字效果。
    """
    try:
        import asyncio
        import re

        assistant_acc = ""
        buffer = ""

        async for event in astream_agent_events(message, thread_id=session_id):
            event_type = event.get("type")

            if event_type == "content":
                chunk = str(event.get("content") or "")
                if not chunk:
                    continue

                buffer += chunk
                segments = re.split(r"(\n|。|！|？|；)", buffer)
                buffer = segments.pop() or ""

                acc = ""
                for seg in segments:
                    acc += seg
                    if seg in ("\n", "。", "！", "？", "；") and acc.strip():
                        assistant_acc += acc
                        yield {
                            "event": "message",
                            "data": json.dumps({"type": "content", "content": acc}, ensure_ascii=False),
                        }
                        acc = ""
                        await asyncio.sleep(0.05)
                if acc:
                    buffer = acc + buffer
                continue

            # Flush any pending content before emitting non-content events (tool_call/tool_result/done/error)
            if buffer.strip():
                assistant_acc += buffer
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "content", "content": buffer}, ensure_ascii=False),
                }
                buffer = ""

            # Check for form_card special marker in tool_result
            if event_type == "tool_result":
                tool_output = str(event.get("tool_output") or "")
                if tool_output.startswith("__FORM_CARD__:"):
                    form_type = tool_output.split(":", 1)[1] if ":" in tool_output else ""
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "type": "form_card",
                            "form_type": form_type,
                            "form_data": {},
                        }, ensure_ascii=False),
                    }
                    # Skip emitting the raw tool_result for form_card
                    continue

                # Check for schedule_card special marker in tool_result
                if "__SCHEDULE_CARD__:" in tool_output:
                    # Extract the marker and parse the JSON data
                    marker_idx = tool_output.find("__SCHEDULE_CARD__:")
                    marker_data = tool_output[marker_idx + len("__SCHEDULE_CARD__:"):]
                    # Clean tool_output by removing the marker
                    clean_output = tool_output[:marker_idx].strip()

                    try:
                        card_data = json.loads(marker_data.strip())
                        # Emit the schedule_card event
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "type": "schedule_card",
                                "schedule_id": card_data.get("schedule_id"),
                                "schedule_type": card_data.get("schedule_type"),
                                "label": card_data.get("label"),
                                "timestamp": card_data.get("timestamp"),
                                "gantt_url": f"http://localhost:8000/api/schedule/gantt/comparison/{card_data.get('schedule_id')}"
                                    if card_data.get("schedule_type") == "comparison"
                                    else "http://localhost:8000/api/schedule/gantt",
                                "constraint": card_data.get("constraint"),
                            }, ensure_ascii=False),
                        }
                    except json.JSONDecodeError:
                        pass  # If parsing fails, just continue with the original output

                    # Update event with cleaned output and continue to emit it
                    if clean_output:
                        event = {**event, "tool_output": clean_output}
                    else:
                        # If no other content, skip emitting tool_result
                        continue

                # Check for erp_export_card special marker in tool_result
                if "__ERP_EXPORT_CARD__:" in tool_output:
                    # Extract the marker and parse the JSON data
                    marker_idx = tool_output.find("__ERP_EXPORT_CARD__:")
                    marker_data = tool_output[marker_idx + len("__ERP_EXPORT_CARD__:"):]
                    # Clean tool_output by removing the marker
                    clean_output = tool_output[:marker_idx].strip()

                    try:
                        card_data = json.loads(marker_data.strip())
                        # Emit the erp_export_card event
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "type": "erp_export_card",
                                "status": card_data.get("status"),
                                "orderCount": card_data.get("orderCount"),
                                "totalQuantity": card_data.get("totalQuantity"),
                                "dateRange": card_data.get("dateRange"),
                                "timestamp": card_data.get("timestamp"),
                            }, ensure_ascii=False),
                        }
                    except json.JSONDecodeError:
                        pass  # If parsing fails, just continue with the original output

                    # Update event with cleaned output and continue to emit it
                    if clean_output:
                        event = {**event, "tool_output": clean_output}
                    else:
                        # If no other content, skip emitting tool_result
                        continue

            yield {"event": "message", "data": json.dumps(event, ensure_ascii=False)}

        # Persist assistant content at end of stream (best-effort).
        if db_enabled() and assistant_acc.strip():
            add_chat_message(session_id, role="assistant", content=assistant_acc.strip())

    except Exception as e:
        yield {"event": "message", "data": json.dumps({
            "type": "error",
            "content": f"错误: {str(e)}",
        }, ensure_ascii=False)}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming chat endpoint.

    Returns a stream of events as the agent processes the request.
    Event types:
    - content: Partial text content from the LLM
    - tool_call: Tool invocation with name and input
    - tool_result: Tool execution result
    - done: Stream completed
    - error: Error occurred
    """
    session_id = req.session_id or str(uuid.uuid4())

    # Restore conversation from DB (if configured) so restarts don't lose memory.
    if db_enabled():
        ensure_chat_session(session_id)
        seed_conversation_history(session_id, list_chat_messages(session_id, limit=200))
        add_chat_message(session_id, role="user", content=req.message)

    _sessions.setdefault(session_id, [])
    _sessions[session_id].append({"role": "user", "content": req.message})

    # Handle help request without streaming
    if not req.message or req.message.strip().lower() in ("help", "帮助", "?", "？"):
        async def help_stream():
            yield {"event": "message", "data": json.dumps({"type": "content", "content": HELP_TEXT}, ensure_ascii=False)}
            yield {"event": "message", "data": json.dumps({"type": "done"}, ensure_ascii=False)}
        return EventSourceResponse(help_stream())

    return EventSourceResponse(
        _stream_agent_response(req.message, session_id),
        media_type="text/event-stream",
    )


@app.post("/api/reset", response_model=ChatResponse)
def reset() -> ChatResponse:
    """Reset conversation and create a new session."""
    session_id = str(uuid.uuid4())
    _sessions[session_id] = []
    return ChatResponse(session_id=session_id, reply="已创建新会话。")


@app.get("/api/schedule")
def get_schedule() -> dict:
    """Get the current schedule result."""
    try:
        return load_schedule(DEFAULT_SCHEDULE_PATH)
    except Exception as e:
        # Fallback to DB-backed document (Railway FS is ephemeral).
        if db_enabled():
            row = get_document_payload("schedule_result")
            if row is not None:
                payload, _ts = row
                if isinstance(payload, dict):
                    return payload
        raise HTTPException(status_code=500, detail=f"Failed to load schedule: {str(e)}")


@app.post("/api/schedule/regenerate")
def regenerate_schedule(
    max_hours: int = Query(default=8000, ge=1, le=20000),
    px_per_day: int = Query(default=120, ge=30, le=400),
    apply_downtime: bool = Query(default=True),
    also_write_per_line: bool = Query(default=True),
) -> dict[str, Any]:
    """Regenerate schedule_result.json + schedule_gantt.html from current ERP snapshot files.

    This is intended for local development flows where ERP sync is done periodically (e.g. daily),
    and scheduling runs off the last snapshot.
    """
    try:
        from process.multiline import PROCESS_DIR as _P, generate_all_lines, write_schedule_artifacts  # type: ignore

        line_schedules, combined = generate_all_lines(max_hours=int(max_hours), apply_downtime=bool(apply_downtime))

        if also_write_per_line:
            for line_id, sched in line_schedules.items():
                out_dir = Path(_P) / "schedules" / line_id
                out_dir.mkdir(parents=True, exist_ok=True)
                write_schedule_artifacts(
                    schedule=sched,
                    schedule_path=out_dir / "schedule_result.json",
                    gantt_path=out_dir / "schedule_gantt.html",
                    px_per_day=int(px_per_day),
                )

        write_schedule_artifacts(
            schedule=combined,
            schedule_path=DEFAULT_SCHEDULE_PATH,
            gantt_path=GANTT_PATH,
            px_per_day=int(px_per_day),
        )

        # Persist schedule artifacts to DB so they survive Railway restarts.
        try:
            upsert_document_payload("schedule_result", combined)
            if GANTT_PATH.exists():
                upsert_document_payload("schedule_gantt_html", GANTT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

        kpi = combined.get("kpi") if isinstance(combined.get("kpi"), dict) else {}
        meta = combined.get("meta") if isinstance(combined.get("meta"), dict) else {}
        return {
            "success": True,
            "lines": sorted(line_schedules.keys()),
            "meta": {"start_time": meta.get("start_time"), "horizon_h": meta.get("horizon_h")},
            "kpi": {
                "orders_total": kpi.get("orders_total"),
                "containers_total": kpi.get("containers_total"),
                "containers_on_time_rate": kpi.get("containers_on_time_rate"),
                "total_container_tardiness_h": kpi.get("total_container_tardiness_h"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate schedule: {type(e).__name__}: {e}")


@app.head("/api/schedule/gantt")
def check_gantt():
    """检查甘特图是否存在（支持 HEAD 请求，避免下载整个 HTML）"""
    if GANTT_PATH.exists():
        stat = GANTT_PATH.stat()
        # st_mtime is in local time; Last-Modified must be GMT per RFC.
        last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return Response(
            headers={
                "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "Content-Type": "text/html",
                "Content-Length": str(stat.st_size),
            }
        )

    # Fallback to DB-backed document.
    if db_enabled():
        row = get_document_payload("schedule_gantt_html")
        if row is not None:
            payload, ts = row
            html = str(payload or "")
            last_modified = ts.astimezone(timezone.utc)
            return Response(
                headers={
                    "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                    "Content-Type": "text/html",
                    "Content-Length": str(len(html.encode("utf-8"))),
                }
            )

    raise HTTPException(status_code=404, detail="Gantt chart not found")


@app.get("/api/schedule/gantt")
def get_gantt():
    """Get the Gantt chart HTML file."""
    html: str | None = None
    if GANTT_PATH.exists():
        html = GANTT_PATH.read_text(encoding="utf-8")
    elif db_enabled():
        row = get_document_payload("schedule_gantt_html")
        if row is not None:
            payload, _ts = row
            html = str(payload or "")

    if not html:
        raise HTTPException(status_code=404, detail="Gantt chart not found")
    from fastapi.responses import Response

    # Backwards-compatible: older generated HTML may not include the viewDays postMessage handler yet.
    if "gantt:set-view" not in html:
        html = _inject_gantt_view_controls(html)
    return Response(content=html, media_type="text/html")


@app.get("/api/schedule/gantt/comparison")
def get_comparison_gantt():
    """获取最新重排后的甘特图 HTML（用于对比显示）。

    只有在调用 reschedule_with_constraint 或 reschedule_with_priority_lock 后才有数据。
    """
    from fastapi.responses import Response
    from .tools import _last_reschedule_state

    html = _last_reschedule_state.get("new_schedule_gantt_html")
    if not html:
        raise HTTPException(status_code=404, detail="No comparison schedule available")

    if "gantt:set-view" not in html:
        html = _inject_gantt_view_controls(html)
    return Response(content=html, media_type="text/html")


@app.get("/api/schedule/gantt/comparison/{schedule_id}")
def get_comparison_gantt_by_id(schedule_id: str):
    """获取指定重排方案的甘特图 HTML。

    Args:
        schedule_id: 方案 ID（如 comparison_1）
    """
    from fastapi.responses import Response
    from .tools import get_comparison_schedule_by_id

    schedule = get_comparison_schedule_by_id(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Comparison schedule '{schedule_id}' not found")

    html = schedule.get("gantt_html")
    if not html:
        raise HTTPException(status_code=404, detail="Gantt chart not available for this schedule")

    if "gantt:set-view" not in html:
        html = _inject_gantt_view_controls(html)
    return Response(content=html, media_type="text/html")


def _inject_gantt_view_controls(html: str) -> str:
    """
    Injects a small script that adds "viewDays" behavior (3D/5D/7D style)
    via query params + postMessage, without requiring regeneration of the HTML.
    """
    marker = "</body>"
    if marker not in html:
        return html

    script = r"""
<script>
(function () {
  if (window.__ganttViewControlsInstalled) return;
  window.__ganttViewControlsInstalled = true;

  const ganttScroll = document.getElementById("ganttScroll");
  if (!ganttScroll) return;
  const dayCount = Number(ganttScroll.dataset.dayCount || "1");
  const dayMs = 24 * 60 * 60 * 1000;

  function setCssVar(name, value) { document.documentElement.style.setProperty(name, value); }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function getLabelWidthPx() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--label-w").trim();
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : 160;
  }

  function getGridPx() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--grid").trim();
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : 120;
  }

  function parseYmdToLocalMidnight(ymd) {
    const m = /^(\\d{4})-(\\d{2})-(\\d{2})$/.exec(String(ymd || "").trim());
    if (!m) return null;
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    const dt = new Date(y, mo - 1, d, 0, 0, 0, 0);
    return Number.isNaN(dt.getTime()) ? null : dt;
  }

  function getScheduleStartDate() {
    const firstTick = document.querySelector(".tick");
    const fromData = firstTick && firstTick.dataset ? firstTick.dataset.date : null;
    if (fromData) return parseYmdToLocalMidnight(fromData);
    const fallback = document.querySelector(".tick-label");
    return fallback ? parseYmdToLocalMidnight(fallback.textContent) : null;
  }

  function getTodayIndex() {
    const start = getScheduleStartDate();
    if (!start) return 0;
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
    return Math.floor((today.getTime() - start.getTime()) / dayMs);
  }

  let currentPpd = getGridPx();

  function applyPpd(newPpd) {
    currentPpd = newPpd;
    setCssVar("--grid", currentPpd + "px");
    setCssVar("--timeline-width", (dayCount * currentPpd) + "px");

    const w = (dayCount * currentPpd) + "px";
    for (const lane of document.querySelectorAll(".lane, .inv, #axis")) lane.style.width = w;
    for (const tick of document.querySelectorAll(".tick")) tick.style.left = (Number(tick.dataset.day || 0) * currentPpd) + "px";
    for (const bar of document.querySelectorAll(".bar")) {
      bar.style.left = (Number(bar.dataset.leftDays || 0) * currentPpd) + "px";
      bar.style.width = Math.max(1, Number(bar.dataset.durationDays || 0) * currentPpd) + "px";
    }
  }

  function setViewDays(viewDays, anchor) {
    const days = Math.max(1, Number(viewDays) || 7);
    const labelW = getLabelWidthPx();
    const viewportW = Math.max(240, ganttScroll.clientWidth || 0);
    const timelineW = Math.max(80, viewportW - labelW);
    const targetPpd = clamp(Math.floor(timelineW / days), 40, 320);

    const oldPpd = currentPpd || targetPpd;
    const oldLeftDay = oldPpd > 0 ? (ganttScroll.scrollLeft / oldPpd) : 0;

    applyPpd(targetPpd);

    let leftDay = oldLeftDay;
    if (anchor === "today") leftDay = getTodayIndex();
    const maxLeft = Math.max(0, dayCount - days);
    leftDay = clamp(leftDay, 0, maxLeft);
    ganttScroll.scrollLeft = leftDay * targetPpd;
  }

  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "gantt:set-view") {
      const payload = e.data.payload || {};
      setViewDays(payload.viewDays, payload.anchor || "today");
    }
  });

  try {
    const params = new URLSearchParams(window.location.search);
    const viewDays = params.get("viewDays");
    const anchor = params.get("anchor") || "today";
    if (viewDays) requestAnimationFrame(() => setViewDays(viewDays, anchor));
  } catch {}
})();
</script>
"""

    return html.replace(marker, script + "\n" + marker)


@app.get("/api/schedule/comparison/status")
def get_comparison_status():
    """获取对比排产的状态信息。

    返回是否有可用的对比排产、方案列表等。
    """
    from .tools import _last_reschedule_state, get_comparison_schedules

    schedules = get_comparison_schedules()

    return {
        "available": _last_reschedule_state.get("new_schedule_gantt_html") is not None,
        "timestamp": _last_reschedule_state.get("timestamp"),
        "constraint": _last_reschedule_state.get("constraint"),
        "count": len(schedules),
        "schedules": schedules,
    }


@app.delete("/api/schedule/comparison/{schedule_id}")
def delete_comparison(schedule_id: str):
    """删除指定的对比方案。

    Args:
        schedule_id: 方案 ID（如 comparison_1）
    """
    from .tools import delete_comparison_schedule

    if delete_comparison_schedule(schedule_id):
        return {"success": True, "message": f"Deleted schedule '{schedule_id}'"}
    else:
        raise HTTPException(status_code=404, detail=f"Comparison schedule '{schedule_id}' not found")


@app.post("/api/schedule/comparison/{schedule_id}/apply")
def apply_comparison(schedule_id: str):
    """应用指定的对比方案为当前排产。

    这会将选中的重排方案保存为正式排产结果。

    Args:
        schedule_id: 方案 ID（如 comparison_1）
    """
    import json
    from .tools import get_comparison_schedule_by_id, clear_comparison_schedules
    from .data import DEFAULT_SCHEDULE_PATH

    schedule = get_comparison_schedule_by_id(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Comparison schedule '{schedule_id}' not found")

    schedule_data = schedule.get("schedule")
    if not schedule_data:
        raise HTTPException(status_code=400, detail="Invalid schedule data")

    # Persist which constraints generated the currently-applied schedule (so UI can show "live constraints").
    try:
        meta = schedule_data.get("meta") if isinstance(schedule_data.get("meta"), dict) else {}
        meta = dict(meta)
        meta["applied_constraints"] = {
            "source_schedule_id": schedule_id,
            "label": schedule.get("label"),
            "constraint": schedule.get("constraint"),
            "applied_at": datetime.now().isoformat(),
        }
        schedule_data["meta"] = meta
    except Exception:
        pass

    # Persist overrides patch (if this scenario was generated via overrides) so future
    # regenerations keep the same "hard constraints" without touching ERP snapshots.
    try:
        constraint = schedule.get("constraint") if isinstance(schedule.get("constraint"), dict) else {}
        overrides_patch = constraint.get("overrides_patch") if isinstance(constraint.get("overrides_patch"), dict) else None
        if overrides_patch:
            from process.overrides import load_overrides as _load_overrides, save_overrides as _save_overrides  # type: ignore

            base = _load_overrides()
            base_cont = base.get("containers") if isinstance(base.get("containers"), dict) else {}
            base_ord = base.get("orders") if isinstance(base.get("orders"), dict) else {}

            patch_cont = overrides_patch.get("containers") if isinstance(overrides_patch.get("containers"), dict) else {}
            patch_ord = overrides_patch.get("orders") if isinstance(overrides_patch.get("orders"), dict) else {}

            for k, v in patch_cont.items():
                key = str(k or "").strip().upper()
                if not key:
                    continue
                if v is None:
                    base_cont.pop(key, None)
                elif isinstance(v, dict):
                    prev = base_cont.get(key) if isinstance(base_cont.get(key), dict) else {}
                    base_cont[key] = {**dict(prev), **dict(v)}

            for k, v in patch_ord.items():
                key = str(k or "").strip()
                if not key:
                    continue
                if v is None:
                    base_ord.pop(key, None)
                elif isinstance(v, dict):
                    prev = base_ord.get(key) if isinstance(base_ord.get(key), dict) else {}
                    base_ord[key] = {**dict(prev), **dict(v)}

            base["containers"] = base_cont
            base["orders"] = base_ord
            _save_overrides(base)
    except Exception:
        pass

    # 保存为当前排产
    try:
        with open(DEFAULT_SCHEDULE_PATH, "w", encoding="utf-8") as f:
            json.dump(schedule_data, f, ensure_ascii=False, indent=2)

        # 更新甘特图文件
        gantt_html = schedule.get("gantt_html")
        if gantt_html:
            with open(GANTT_PATH, "w", encoding="utf-8") as f:
                f.write(gantt_html)

        # Persist to DB as well (Railway FS is ephemeral).
        try:
            upsert_document_payload("schedule_result", schedule_data)
            if gantt_html:
                upsert_document_payload("schedule_gantt_html", gantt_html)
        except Exception:
            pass

        # 清空对比方案列表
        clear_comparison_schedules()

        return {
            "success": True,
            "message": f"Applied schedule '{schedule_id}' as current schedule",
            "label": schedule.get("label"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to apply schedule: {str(e)}")


@app.delete("/api/schedule/comparison")
def clear_all_comparisons():
    """清空所有对比方案。"""
    from .tools import clear_comparison_schedules

    clear_comparison_schedules()
    return {"success": True, "message": "Cleared all comparison schedules"}


@app.get("/api/ui/status")
def ui_status() -> dict[str, Any]:
    """UI-friendly status summary of constraints + snapshots + comparison scenarios.

    Intended for the frontend RightPanel to show "what constraints are live right now".
    """
    from .calendar_store import load_calendar
    from .tools import _production_context_check, get_comparison_schedules  # type: ignore

    now = datetime.now()

    # ERP snapshot info (no secrets).
    orders_doc = _safe_load_json(ERP_ORDERS_PATH)
    inv_doc = _safe_load_json(ERP_INVENTORY_PATH)

    def _snapshot_summary(doc: Any, path: Path) -> dict[str, Any]:
        if not isinstance(doc, dict):
            return {"path": str(path.relative_to(REPO_ROOT)), "exists": path.exists()}
        data = doc.get("data") if isinstance(doc.get("data"), list) else []
        return {
            "path": str(path.relative_to(REPO_ROOT)),
            "exists": path.exists(),
            "timestamp": doc.get("timestamp"),
            "count": len(data),
        }

    # Current schedule summary (including whether downtime blocks are present in the gantt).
    sched: dict[str, Any] | None = None
    try:
        sched = load_schedule(DEFAULT_SCHEDULE_PATH)
    except Exception:
        sched = None

    applied_downtime = False
    downtime_block_counts: dict[str, int] = {"holiday": 0, "maintenance": 0}
    meta: dict[str, Any] = {}
    if isinstance(sched, dict):
        meta = sched.get("meta") if isinstance(sched.get("meta"), dict) else {}
        machines = sched.get("machines") if isinstance(sched.get("machines"), dict) else {}
        for _mid, tasks in machines.items():
            if not isinstance(tasks, list):
                continue
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                st = t.get("setup_type")
                if not isinstance(st, str) or not st:
                    continue
                if st.startswith("holiday:"):
                    applied_downtime = True
                    downtime_block_counts["holiday"] += 1
                elif st.startswith("maintenance:"):
                    applied_downtime = True
                    downtime_block_counts["maintenance"] += 1

    # Production-context check (gates reschedule flows).
    check = dict(_production_context_check or {})
    ts = check.get("timestamp")

    # Local overrides (priority/due overrides) that act like hard constraints.
    overrides_summary: dict[str, Any] = {"containers": [], "orders": []}
    try:
        from process.overrides import load_overrides as _load_overrides  # type: ignore

        ov = _load_overrides()
        overrides_summary = {
            "containers": sorted(list((ov.get("containers") or {}).keys()))[:200],
            "orders": sorted(list((ov.get("orders") or {}).keys()))[:200],
        }
    except Exception:
        pass

    return {
        "timestamp": now.isoformat(),
        "erp_snapshot": {
            "orders": _snapshot_summary(orders_doc, ERP_ORDERS_PATH),
            "inventory": _snapshot_summary(inv_doc, ERP_INVENTORY_PATH),
        },
        "overrides": overrides_summary,
        "downtime_calendar": load_calendar(),
        "production_context": {
            "confirmed": bool(check.get("confirmed")),
            "forming_states": check.get("forming_states"),
            "setup_remaining_by_machine": check.get("setup_remaining_by_machine"),
            "checked_at": ts.isoformat() if isinstance(ts, datetime) else None,
        },
        "current_schedule": {
            "exists": isinstance(sched, dict),
            "meta": {
                "line": meta.get("line"),
                "start_time": meta.get("start_time"),
                "horizon_h": meta.get("horizon_h"),
                "applied_constraints": meta.get("applied_constraints"),
            },
            "applied_downtime": applied_downtime,
            "downtime_block_counts": downtime_block_counts,
        },
        "comparisons": {
            "count": len(get_comparison_schedules()),
            "schedules": get_comparison_schedules(),
        },
    }


@app.get("/api/schedule/kpi")
def get_kpi() -> dict:
    """Get the KPI summary from the current schedule."""
    try:
        sched = load_schedule(DEFAULT_SCHEDULE_PATH)
        return {
            "kpi": sched.get("kpi", {}),
            "meta": sched.get("meta", {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load KPI: {str(e)}")


# =============================================================================
# Container APIs
# =============================================================================


@app.get("/api/schedule/containers")
def get_containers(customer: Optional[str] = None, status: Optional[str] = None) -> dict:
    """Get Container list.

    Args:
        customer: Optional customer code filter (e.g., "SQ#", "DE#", "SEC")
        status: Optional status filter ("all", "on_time", "late")

    Returns:
        Container list with summary statistics
    """
    try:
        sched = load_schedule(DEFAULT_SCHEDULE_PATH)
        rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
        rows = [r for r in rows if isinstance(r, dict)]

        containers = aggregate_containers(rows)

        # Filter by customer
        if customer:
            code = customer.strip().upper()
            containers = [c for c in containers if c["customer_code"].upper() == code]

        # Filter by status
        if status == "on_time":
            containers = [c for c in containers if c["on_time"]]
        elif status == "late":
            containers = [c for c in containers if not c["on_time"]]

        # Summary
        total = len(containers)
        on_time_count = sum(1 for c in containers if c["on_time"])
        late_count = total - on_time_count

        return {
            "summary": {
                "total": total,
                "on_time": on_time_count,
                "late": late_count,
                "on_time_rate": on_time_count / total if total > 0 else 0,
            },
            "containers": [
                {
                    "container_id": c["container_id"],
                    "customer_code": c["customer_code"],
                    "order_count": len(c["orders"]),
                    "total_quantity": c["total_quantity"],
                    "earliest_due": c["earliest_due"],
                    "latest_end": c["latest_end"],
                    "on_time": c["on_time"],
                    "lateness_h": c["lateness_h"],
                }
                for c in containers
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load containers: {str(e)}")


@app.get("/api/schedule/containers/{container_id}")
def get_container_detail(container_id: str) -> dict:
    """Get Container detail by ID (poreference).

    Args:
        container_id: Container ID (poreference)

    Returns:
        Container detail including all orders
    """
    try:
        sched = load_schedule(DEFAULT_SCHEDULE_PATH)
        rows = sched.get("orders") if isinstance(sched.get("orders"), list) else []
        rows = [r for r in rows if isinstance(r, dict)]

        containers = aggregate_containers(rows)

        # Find container
        ref = container_id.strip().upper()
        target = None
        for c in containers:
            if c["container_id"].upper() == ref:
                target = c
                break

        if not target:
            # Partial match
            partial_matches = [c for c in containers if ref in c["container_id"].upper()]
            if len(partial_matches) == 1:
                target = partial_matches[0]
            elif partial_matches:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Multiple matches found",
                        "matches": [c["container_id"] for c in partial_matches[:10]],
                    },
                )

        if not target:
            raise HTTPException(status_code=404, detail=f"Container '{container_id}' not found")

        return {
            "container_id": target["container_id"],
            "customer_code": target["customer_code"],
            "total_quantity": target["total_quantity"],
            "earliest_due": target["earliest_due"],
            "latest_end": target["latest_end"],
            "on_time": target["on_time"],
            "lateness_h": target["lateness_h"],
            "orders": target["orders"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load container: {str(e)}")


# =============================================================================
# Calendar / Downtime APIs
# =============================================================================


class MaintenanceEntry(BaseModel):
    """Maintenance entry model."""

    machine_id: str = Field(..., description=f"Machine ID, valid: {VALID_MACHINE_IDS}")
    reason: str = Field(..., description="Maintenance reason")
    start: str = Field(..., description="Start datetime (ISO format: YYYY-MM-DDTHH:MM)")
    end: str = Field(..., description="End datetime (ISO format: YYYY-MM-DDTHH:MM)")


class HolidayEntry(BaseModel):
    """Holiday entry model."""

    name: str = Field(..., description="Holiday name")
    start: str = Field(..., description="Start date (YYYY-MM-DD)")
    end: str = Field(..., description="End date (YYYY-MM-DD)")


@app.get("/api/calendar/downtime")
def get_downtime() -> dict:
    """Get all downtime plans (holidays and maintenance)."""
    try:
        calendar = _calendar_load_calendar()
        return {
            "holidays": calendar.get("holidays", []),
            "maintenance": calendar.get("maintenance", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load calendar: {str(e)}")


@app.post("/api/calendar/maintenance")
def add_maintenance(entry: MaintenanceEntry) -> dict:
    """Add a maintenance entry."""
    try:
        new_entry, index, existed = _calendar_add_maintenance(
            machine_id=entry.machine_id,
            reason=entry.reason,
            start=entry.start,
            end=entry.end,
        )
        return {
            "success": True,
            "message": "Maintenance entry already exists" if existed else "Maintenance entry added",
            "entry": new_entry,
            "index": index,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add maintenance: {str(e)}")


@app.post("/api/calendar/holiday")
def add_holiday(entry: HolidayEntry) -> dict:
    """Add a holiday entry."""
    try:
        new_entry, index, existed = _calendar_add_holiday(
            name=entry.name,
            start=entry.start,
            end=entry.end,
        )
        return {
            "success": True,
            "message": "Holiday entry already exists" if existed else "Holiday entry added",
            "entry": new_entry,
            "index": index,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add holiday: {str(e)}")


@app.delete("/api/calendar/maintenance/{index}")
def delete_maintenance(index: int) -> dict:
    """Delete a maintenance entry by index."""
    try:
        deleted = _calendar_delete_maintenance(index=index)
        return {
            "success": True,
            "message": f"Deleted maintenance entry at index {index}",
            "deleted": deleted,
        }
    except IndexError:
        raise HTTPException(status_code=404, detail=f"Maintenance entry at index {index} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete maintenance: {str(e)}")


@app.delete("/api/calendar/holiday/{index}")
def delete_holiday(index: int) -> dict:
    """Delete a holiday entry by index."""
    try:
        deleted = _calendar_delete_holiday(index=index)
        return {
            "success": True,
            "message": f"Deleted holiday entry at index {index}",
            "deleted": deleted,
        }
    except IndexError:
        raise HTTPException(status_code=404, detail=f"Holiday entry at index {index} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete holiday: {str(e)}")
