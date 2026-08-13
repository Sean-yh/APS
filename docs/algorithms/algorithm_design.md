# Scheduling Algorithm Design (All Lines)

This document describes the current scheduling algorithm implementation for all 3 production lines.

## Production Lines Overview

| Line | SKU Prefixes | Forming Machine | Forming Rate | Labeling Machines | Labeling Rate (each) |
|------|--------------|-----------------|--------------|-------------------|---------------------|
| L1 | S18B1*, S18G9* | ROTARY-1 | 3,600 units/h | LABEL-1, LABEL-2 | 1,800 units/h |
| L2 | S12G9* | ROTARY-2 | 5,000 units/h | LABEL-3, LABEL-5 | 2,400 units/h |
| L3 | S12G8* | ROTARY-3 | 5,268 units/h | LABEL-4, LABEL-6 | 2,460 units/h |

### Setup Times

| Line | Color Change | Mold Change |
|------|--------------|-------------|
| L1 | 12h | 72h (between S18B1* ↔ S18G9*) |
| L2 | 12h | N/A |
| L3 | 12h | N/A |

**Key difference:** L1 has prefix-based mold groups. Switching between B1 and G9 prefixes incurs 72h mold change (which includes the color change).

## Algorithm Architecture

The algorithm is a **multi-candidate heuristic** with forward simulation. It is NOT an optimization solver (no MIP/CP).

### Stage 1: Candidate Generation

For each line, generate ~8 forming campaign sequences:

```python
def _forming_sequence_candidates(skus, demands, deadlines):
    candidates = [
        sorted(skus, key=lambda s: deadlines[s]),           # by deadline (earliest first)
        sorted(skus, key=lambda s: -demands[s]),            # by demand (highest first)
        sorted(skus, key=lambda s: prefix_group(s)),        # by family (L1: B1 then G9)
        sorted(skus, key=lambda s: -prefix_group(s)),       # reverse family
        list(skus),                                          # raw order
        # ... additional variants
    ]
    return deduplicate(candidates)
```

**Purpose:** Determine the order in which SKUs should be produced through the forming machine.

### Stage 2: Build Forming Plan

For each candidate sequence, pre-compute hour-by-hour forming schedule:

```
Hour 0..N-1:       FORMING SKU1 (hours = ceil(demand / forming_rate))
Hour N..N+setup-1: SETUP (color or mold change)
Hour N+setup..M-1: FORMING SKU2
...
Remaining hours:   IDLE
```

### Stage 3: Forward Simulation

Simulate hour-by-hour for the entire horizon:

```python
for t_h in range(max_hours):
    # 1. Check downtime (holidays/maintenance)
    if is_downtime(t_h):
        continue  # entire line paused

    # 2. Get forming action from pre-computed plan
    forming_action = forming_plan[t_h]

    # 3. Dispatch orders to idle labeling machines (GREEDY)
    for machine in idle_labeling_machines:
        for order in sorted(pending_orders, key=priority_key):
            if _simulate_can_start_order(order, t_h, inventory):
                assign(machine, order)
                break

    # 4. Execute one hour
    if forming_action.type == "forming":
        inventory[forming_action.sku] += forming_rate
    for machine in active_labeling_machines:
        inventory[machine.order.sku] -= labeling_rate
```

### Feasibility Check (`_simulate_can_start_order`)

Before assigning an order, simulate forward to ensure:
- Forming will produce enough material
- Inventory never goes negative during the order

```python
def _simulate_can_start_order(order, start_h, current_inventory):
    inventory = current_inventory[order.sku]
    hours_needed = ceil(order.quantity / labeling_rate)

    for h in range(start_h, start_h + hours_needed):
        # Add forming production if applicable
        if forming_plan[h].sku == order.sku:
            inventory += forming_rate
        # Subtract labeling consumption
        inventory -= labeling_rate
        if inventory < 0:
            return False
    return True
```

### Stage 4: Best Schedule Selection

Compare all feasible schedules using lexicographic key:

```python
key = (
    total_container_tardiness_h,    # PRIMARY: minimize container lateness
    -containers_on_time_rate,       # SECONDARY: maximize on-time rate
    total_order_tardiness_h,        # TERTIARY: minimize order lateness
    -on_time_rate,                  # minimize lateness spread
    horizon_h                       # QUATERNARY: minimize makespan
)
```

Pick the schedule with lexicographically smallest key.

## Constraints

### Hard Constraints (Must Satisfy)

| Constraint | Description |
|------------|-------------|
| Inventory Safety | `inventory[sku][t] >= 0` for all t |
| No Order Splitting | Each order runs continuously on one machine |
| Capacity | Respect forming/labeling rates |
| Machine Eligibility | SKU can only run on its line's machines |
| Downtime | Entire line stops during holidays/maintenance |

### Soft Constraints (Optimize)

| Constraint | Description |
|------------|-------------|
| Setup Minimization | Group same-SKU/same-mold production |
| Priority | Orders with `priority=1` dispatch first |
| Deadline | Minimize tardiness (hours past due) |

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. INPUT                                                    │
│    - orders_erp.json: ERP order data                        │
│    - inventory_erp.json: current stock levels               │
│    - line_config.json: machine specs, rates, setup rules    │
│    - production_calendar (DB): holidays, maintenance        │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. PRE-PROCESSING                                           │
│    - Apply overrides (due_override, priority)               │
│    - Filter orders by line SKU prefixes                     │
│    - Calculate demand per SKU                               │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. SCHEDULING (per line, independent)                       │
│    For L1, L2, L3:                                          │
│      - Generate 8 forming sequences                         │
│      - Simulate each sequence hour-by-hour                  │
│      - Select best by KPI key                               │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. MERGE                                                    │
│    - Combine L1, L2, L3 schedules                           │
│    - Recompute container KPIs (cross-line)                  │
│    - Align inventory series                                 │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. OUTPUT                                                   │
│    - schedule_result.json: combined schedule                │
│    - schedules/L{1,2,3}/schedule_result.json: per-line      │
│    - schedule_gantt.html: visualization                     │
└─────────────────────────────────────────────────────────────┘
```

## Key Implementation Files

| File | Purpose |
|------|---------|
| `backend/process/line_scheduler.py` | Core: `generate_line_schedule()`, candidates, simulation |
| `backend/process/multiline.py` | Multi-line: `generate_all_lines()`, `merge_schedules()` |
| `backend/process/generate_schedule.py` | L2-specific wrapper with chain-start timing |
| `backend/process/line_config.json` | Machine specs, rates, setup rules |
| `backend/ai/scheduler.py` | High-level API for AI agent |
| `backend/ai/calendar_store.py` | Downtime calendar (DB-backed) |

## Algorithm Complexity

- **Time:** O(candidates × horizon × orders) per line
  - candidates ≈ 8
  - horizon ≈ 1000-2000 hours
  - orders ≈ 100
  - Total: ~1M operations per line, runs in seconds

- **Space:** O(horizon × SKUs) for inventory tracking

## Strengths

1. **Fast execution** - Seconds, not minutes
2. **Correct** - Respects all hard constraints
3. **Practical** - Handles holidays, priorities, partial setups
4. **Extensible** - Overrides allow AI/user intervention
5. **Transparent** - Easy to understand and debug

## Limitations

1. **Heuristic, not optimal** - 8 sequences may miss better orderings
2. **Greedy dispatch** - Locally optimal, not globally
3. **Hour granularity** - No sub-hour precision
4. **No batching** - Each order scheduled individually
5. **Independent lines** - No cross-line resource sharing

See `improvements.md` for detailed improvement proposals.
