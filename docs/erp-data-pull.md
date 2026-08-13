# GX ERP Data Pull

This project can pull **live orders / inventory / demand history** from GX ERP and convert it into the local JSON formats used by the scheduler.

## Configuration

Set these in `.env` (repo already gitignores `.env`):

```bash
GX_ERP_API_URL=http://<host>:<port>/api/v1/aps-gx
GX_ERP_TOKEN=...
GX_ERP_IS_TEST=true
GX_ERP_TIMEOUT_S=60
```

### Auth header (important)

GX ERP expects the token as a **raw** `Authorization` header value (no `Bearer` prefix):

```http
Authorization: <GX_ERP_TOKEN>
```

The backend implements this in `backend/ai/erp_client.py`.

## Backend endpoints

All endpoints support `?isTest=true` (or omit it to use `GX_ERP_IS_TEST` default).

- `GET /api/erp/orders`  
  Returns `{ "timestamp": "...", "data": [ ...orders ] }` where `data` is compatible with `backend/process/generate_schedule.py` order input format.

- `GET /api/erp/inventory`  
  Returns `{ "timestamp": "...", "data": [ ...inventory ] }` where `data` is compatible with the inventory input format.

- `GET /api/erp/demand-history`  
  Returns `{ "timestamp": "...", "data": [ ... ] }` (pass-through rows from ERP).

- `POST /api/erp/sync`  
  Pulls `orders` + `inventory` and writes snapshots:
  - `backend/process/orders_erp.json`
  - `backend/process/inventory_erp.json`

  Response includes counts + paths.

## Typical flow

1. Start backend:

```bash
PYTHONPATH=backend uvicorn ai.api:app --reload --port 8000
```

2. Pull snapshots:

```bash
curl -X POST "http://localhost:8000/api/erp/sync?isTest=true"
```

3. Run scheduler against snapshots:

```bash
PYTHONPATH=backend python backend/process/generate_schedule.py \
  --start "2026-01-19 00:00" \
  --out backend/process/schedule_result.json \
  --chain-search-days 60
```

## Debugging / troubleshooting

- If you see `success:false` with `Unauthorized`, confirm:
  - `.env` has correct `GX_ERP_TOKEN`
  - Your environment is loading `.env` (backend uses `dotenv` in `ai/agent.py`; if running other scripts, load `.env` explicitly)
- If you see timeouts:
  - Increase `GX_ERP_TIMEOUT_S` (e.g. `60`, `120`)
- If ERP returns HTTP 200 but still errors:
  - The client treats `{ "success": false, ... }` as an error and the backend returns `502`.

## Production notes

For production hardening:

- Store `GX_ERP_TOKEN` in a secret manager (not `.env` on disk).
- Add auth to `/api/erp/*` endpoints and restrict CORS to your frontend origin.
- Avoid writing snapshots to local disk if you plan to run multiple backend replicas; use shared storage or a DB (this repo persists snapshots to Postgres via the `documents` table).
