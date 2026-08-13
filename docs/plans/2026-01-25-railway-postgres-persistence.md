# Railway + Postgres Persistence Plan (Deployable APS)

> Note: You asked to use the “superpowers” skill, but it isn’t available in this Codex session. This doc is the plan we’ll execute next.

## Goal

Make the app **deployable on Railway** with **Postgres as the source of truth** for:
- Chat history (sessions + messages)
- ERP snapshots (orders + inventory)
- Schedules (current + scenario history)
- Constraints (downtime calendar + overrides)
- Production context (last confirmed rotary states)

Keep architecture simple: **FastAPI (Python) owns persistence**, Next.js reads/writes via API only.

## Non-goals (for v1)
- Full auth/roles (we can add a simple API key gate later)
- Perfect data modeling of every order/task row (we’ll store schedule payload JSON first)
- Background workers/cron in-code (Railway cron can hit HTTP endpoints)

## Current Pain Points
- Railway filesystem is ephemeral: current “saved” files in `backend/process/*.json` disappear on redeploy/restart.
- Chat + scenarios are in-memory: they disappear on backend restart.
- Dependencies are not pinned (`backend/requirements.txt` uses `>=`) and Python 3.14 is too new for the LangChain stack.

## What We Implemented (v1)

### Folder structure (monorepo)
- `backend/` contains all Python backend code + migrations:
  - `backend/ai/` (FastAPI app + agent + tools + persistence)
  - `backend/process/` (scheduler algorithms + local cache files)
  - `backend/alembic/` + `backend/alembic.ini`
  - `backend/requirements.txt`
  - `backend/tests/`, `backend/scripts/`
- `frontend/` remains Next.js

### Data model (Postgres)
We implemented a pragmatic schema:
- `chat_sessions` + `chat_messages`: persistent chat history
- `documents`: a simple key/value store for JSON blobs and text blobs
  - Used to persist the existing file-backed artifacts (ERP snapshots, schedules, overrides, downtime calendar, line_config, agent_state, gantt HTML)

This keeps the current scheduling code working (it still reads `backend/process/*.json`) while making Railway deploys safe.

Tables are created via Alembic migration `2b23b1d9669d_init`.

### Persistence behavior
- Writes to these local files also upsert into `documents`:
  - `backend/process/production_calendar.json` (downtime)
  - `backend/process/overrides.json` (priority/due overrides)
  - `backend/process/line_config.json` (AI-editable line config)
  - `backend/process/agent_state.json` (production context check)
  - `backend/process/orders_erp.json` / `inventory_erp.json` (ERP snapshots)
  - `backend/process/schedule_result.json` / `schedule_gantt.html` (current schedule)
- If local files are missing (Railway restart), loaders fall back to DB and restore the Process cache best-effort.
- Schedule/Gantt endpoints also have DB fallbacks, so the API stays usable even if Process cache is temporarily missing.

## Target Runtime / Packaging

### Python version
- Target **Python 3.12** in production.
- Local can remain flexible, but we should dev-test on 3.12 soon.

### Dependency management
- Replace `backend/requirements.txt` with pinned versions (or add a pinned `backend/requirements.lock.txt` for Railway).
- Add a small “compile dependencies” workflow (optional): `pip-tools` or `uv pip compile`.

### Railway start command
- Backend: `PYTHONPATH=backend uvicorn ai.api:app --host 0.0.0.0 --port $PORT`
- Add `/healthz` endpoint for Railway health checks.

## Future (v2) optional normalization

If we outgrow `documents`, we can split into dedicated tables later:
- `erp_snapshots` (history + retention policies)
- `schedules` (current + scenario history)
- `downtime_entries`, `override_entries`, `production_context` (queryable constraints)

## Backend Implementation Checklist

### Phase A — DB foundation (Alembic + SQLAlchemy)
Files to add:
- `backend/ai/db.py` (engine/session)
- `backend/ai/models.py` (ORM models)
- `backend/alembic.ini`, `backend/alembic/` + first migration

Deliverables:
- `DATABASE_URL` supported (Railway style).
- `alembic upgrade head` creates tables.

### Phase B — Replace in-memory/file state with DB (with fallback for local dev)

1) Chat persistence
- Update `/api/chat` and `/api/chat/stream`:
  - Ensure session exists (`chat_sessions`)
  - Store user messages immediately
  - Store assistant message when complete (stream: store at `done`)
- Update agent history:
  - Load last N messages for the session from DB to build the context (instead of `_conversation_history` only in RAM).

2) ERP snapshot persistence
- Update `/api/erp/sync`:
  - Write to `erp_snapshots` (orders + inventory).
  - Keep writing local files in dev only (optional).
- Update schedule generation to prefer DB snapshots, fallback to files.

3) Schedule persistence
- Update `/api/schedule/regenerate`:
  - Read latest ERP snapshots from DB.
  - Run `Process.multiline.generate_all_lines(...)`.
  - Persist schedule to `schedules(kind='current')`.
- Update `/api/schedule` and `/api/schedule/containers/*` to read from DB current schedule first.
- Update gantt:
  - Either store `gantt_html` in DB, OR render from payload on demand.

4) Downtime + overrides persistence
- Replace `backend/process/production_calendar.json` and `backend/process/overrides.json` as the source of truth:
  - Keep file-based store as optional local fallback.
  - Add DB-backed store implementations in `backend/ai/calendar_store.py` and `backend/process/overrides.py` (or new modules).
- Ensure UI “Constraints (Live)” reads DB-backed status via `/api/ui/status`.

5) Production context persistence
- Replace `backend/process/agent_state.json` with `production_context` row:
  - `query_production_context` reads/writes to DB.
  - `/api/ui/status` reads from DB.

Deliverables:
- Restart the backend and nothing “disappears”.
- UI shows constraints/overrides/production context reliably.

### Phase C — Ops hardening (Railway)
- Add `/healthz` endpoint.
- Add structured logging for key actions: ERP sync, regenerate schedule, apply scenario.
- Add optional write-guard:
  - `APS_ADMIN_KEY` header for endpoints that mutate (sync, regenerate, add downtime, overrides).

Deliverables:
- Safe enough for internal use on Railway.

## Frontend Changes (minimal)
- No DB direct access from Next.js.
- Existing `/api/ui/status` stays the single status source.
- (Optional) add “history viewer” pages later; not required for v1.

## Testing Plan

Backend:
- Add unit tests for DB repository functions (insert/read/update).
- Add a small integration test (sqlite) to validate schema + write/read schedule json.

Frontend:
- Keep current UI tests (vitest) for constraints panel/chip.

## Acceptance Criteria (v1)
- Deploy backend+frontend to Railway.
- `DATABASE_URL` set → persistence works:
  - chat history survives restart
  - current schedule survives restart
  - downtime + overrides survive restart
  - ERP snapshot survives restart
- `/api/ui/status` accurately shows:
  - latest ERP snapshot times
  - overrides list
  - downtime calendar counts
  - last production context + last updated time

## Open Questions (need answers before Phase B)
1) Multi-user? If yes, do we separate by “site_id/user_id”, or keep single shared workspace?
2) How much history to retain?
   - schedules: keep last N scenarios?
   - ERP snapshots: keep last N daily pulls?
3) Should “holiday end” be inclusive or exclusive? (affects gantt blocks)
