# Schedule JSON Format

The scheduler output is a JSON dict with these top-level keys:

- `meta`: metadata (line id, start_time, horizon, rates, assumptions)
- `kpi`: computed KPIs (orders/container on-time rates, tardiness, setup counts)
- `machines`: mapping `machine_id -> [tasks...]`
- `orders`: list of per-order schedule rows (start/end/machine/on_time/lateness)
- `containers`: list of per-container schedule rows (delivery end/on_time/lateness)
- `inventory`: optional inventory time series by SKU
- `validation`: optional validation results (e.g. inventory_min)

## machines tasks

Each machine task is a dict with:

- `type`: `"forming" | "label" | "setup" | "idle"`
- `start`, `end`: ISO datetime strings
- `duration_h`: integer hours

Optional fields:

- For forming:
  - `sku`, `quantity`
- For label:
  - `order_id`, `sku`, `quantity`
- For setup:
  - `setup_type` (e.g. `color_change`, `mold_change`, `holiday:...`, `maintenance:...`)
  - `from_sku`, `to_sku`
- For downtime idle:
  - `setup_type` starting with `holiday:` or `maintenance:`

## orders rows

Each order row includes:

- `c_orderline_id`, `poreference`, `sku`, `quantity`, `due`, `deadline`, `name`, `remark`
- `machine`, `start`, `end`
- `on_time`, `expired_before_start`, `lateness_h`
- optional `line` (for merged multi-line schedules)

## containers rows

Each container row includes:

- `container_id` (poreference), `order_ids`, `orders_count`, `total_quantity`
- `due`, `deadline`, `start`, `end`
- `on_time`, `expired_before_start`, `lateness_h`

