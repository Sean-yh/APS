# Algorithm Improvements & Best Practices

This document outlines potential improvements to the scheduling algorithm and engineering best practices.

## Current Assessment

**Rating: 7/10** - Good production-ready heuristic, not mathematically optimal.

The algorithm is fast, correct, and handles real-world constraints well. However, there's room for improvement in solution quality without sacrificing simplicity.

---

## High-Priority Improvements

### 1. Smarter L1 Mold-Change Sequencing

**Problem:** L1 has expensive mold changes (72h) between B1↔G9 prefixes. Current heuristics try family grouping but don't optimize the sequence within groups.

**Solution:** Apply TSP-like heuristic for forming sequence:

```python
def optimize_l1_sequence(skus, demands, deadlines):
    """
    Minimize total setup time while respecting deadline ordering within groups.
    """
    # Group by mold (B1 vs G9)
    b1_skus = [s for s in skus if 'B1' in s]
    g9_skus = [s for s in skus if 'G9' in s]

    # Sort within groups by deadline
    b1_sorted = sorted(b1_skus, key=lambda s: deadlines[s])
    g9_sorted = sorted(g9_skus, key=lambda s: deadlines[s])

    # Decide which group goes first based on earliest deadline
    if min(deadlines[s] for s in b1_skus) < min(deadlines[s] for s in g9_skus):
        return b1_sorted + g9_sorted  # B1 first, one mold change
    else:
        return g9_sorted + b1_sorted  # G9 first, one mold change
```

**Impact:** High - Reduces mold changes from potentially 2+ to exactly 1.
**Effort:** Low - Simple grouping logic.

### 2. Order Batching Pre-Pass

**Problem:** Small orders of the same SKU are scheduled individually, potentially causing unnecessary machine idle time or fragmented inventory.

**Solution:** Consolidate orders before scheduling:

```python
def batch_orders(orders, max_batch_gap_h=24):
    """
    Merge orders with same SKU and close deadlines into batches.
    """
    batched = []
    by_sku = groupby(sorted(orders, key=lambda o: (o.sku, o.deadline)))

    for sku, group in by_sku:
        group = list(group)
        current_batch = [group[0]]

        for order in group[1:]:
            if order.deadline - current_batch[-1].deadline <= max_batch_gap_h:
                current_batch.append(order)
            else:
                batched.append(merge_batch(current_batch))
                current_batch = [order]
        batched.append(merge_batch(current_batch))

    return batched
```

**Impact:** Medium - Reduces context switches, improves throughput.
**Effort:** Medium - Need to track batch→original order mapping for reporting.

### 3. Local Search Post-Optimization

**Problem:** Greedy dispatch is myopic. Order A might block Order B unnecessarily.

**Solution:** After initial schedule, apply local search:

```python
def local_search(schedule, max_iterations=100):
    """
    Iteratively swap adjacent orders to reduce tardiness.
    """
    best = schedule
    best_kpi = compute_kpi(schedule)

    for _ in range(max_iterations):
        improved = False
        for machine in schedule.machines:
            for i in range(len(machine.orders) - 1):
                # Try swapping orders i and i+1
                candidate = swap_orders(schedule, machine, i, i+1)
                if is_feasible(candidate):
                    candidate_kpi = compute_kpi(candidate)
                    if candidate_kpi < best_kpi:
                        best = candidate
                        best_kpi = candidate_kpi
                        improved = True
        if not improved:
            break

    return best
```

**Impact:** Medium - Can reduce tardiness 5-15% in constrained scenarios.
**Effort:** Medium - Need to implement swap feasibility check.

---

## Medium-Priority Improvements

### 4. Look-Ahead Dispatch

**Problem:** Greedy dispatch picks the first feasible order without considering impact on future orders.

**Solution:** Score orders by downstream impact:

```python
def dispatch_with_lookahead(idle_machine, pending_orders, inventory, t_h):
    """
    Pick order that leaves best inventory position for subsequent orders.
    """
    candidates = []
    for order in pending_orders:
        if not can_start(order, t_h, inventory):
            continue

        # Simulate completing this order
        future_inventory = simulate_order(order, inventory)

        # Score: how many other orders become feasible?
        feasible_count = sum(1 for o in pending_orders
                           if o != order and can_start(o, t_h + order.duration, future_inventory))

        candidates.append((order, feasible_count))

    # Pick order that enables most future orders
    return max(candidates, key=lambda x: x[1])[0]
```

**Impact:** Medium - Better utilization in constrained scenarios.
**Effort:** High - Computationally expensive, may need caching.

### 5. Dynamic Forming Sequence Adjustment

**Problem:** Forming sequence is fixed before simulation. If labeling falls behind, forming may overproduce one SKU while another is needed.

**Solution:** Allow forming sequence reordering mid-schedule:

```python
def adaptive_forming_plan(t_h, inventory, pending_orders):
    """
    Reorder remaining forming sequence based on current inventory levels.
    """
    remaining_skus = get_remaining_forming_skus(t_h)

    # Prioritize SKUs with low inventory and high pending demand
    urgency = {}
    for sku in remaining_skus:
        pending_demand = sum(o.quantity for o in pending_orders if o.sku == sku)
        urgency[sku] = pending_demand / (inventory[sku] + 1)

    return sorted(remaining_skus, key=lambda s: -urgency[s])
```

**Impact:** Medium - Better responsiveness to demand fluctuations.
**Effort:** High - Changes core algorithm structure.

### 6. Finer Time Granularity

**Problem:** 1-hour buckets cause artificial delays and imprecise scheduling.

**Solution:** Use 15-minute or 6-minute buckets:

```python
TIME_STEP_MINUTES = 15  # Instead of 60
forming_rate_per_step = forming_rate_per_h / (60 / TIME_STEP_MINUTES)
```

**Impact:** Low - More precise timing, but minimal KPI improvement.
**Effort:** Low - Multiply/divide rates, but increases simulation steps 4x.

---

## Low-Priority Improvements (Future)

### 7. Constraint Programming Solver

For truly optimal solutions, replace heuristic with CP solver:

```python
from ortools.sat.python import cp_model

def solve_with_cp(orders, machines, constraints):
    model = cp_model.CpModel()

    # Decision variables
    starts = {o.id: model.NewIntVar(0, horizon, f'start_{o.id}') for o in orders}
    machine_assignment = {o.id: model.NewIntVar(0, len(machines)-1, f'machine_{o.id}')
                         for o in orders}

    # Constraints
    for o in orders:
        # Deadline constraint
        model.Add(starts[o.id] + o.duration <= o.deadline)

        # No overlap on same machine
        # ... (interval variables, no_overlap constraint)

    # Objective: minimize total tardiness
    tardiness = [model.NewIntVar(0, horizon, f'tard_{o.id}') for o in orders]
    model.Minimize(sum(tardiness))

    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    return extract_solution(solver, starts, machine_assignment)
```

**Impact:** High - Provably optimal solutions.
**Effort:** Very High - Complete rewrite, solver tuning, licensing.
**Recommendation:** Only pursue if heuristic proves insufficient for business needs.

### 8. Machine Learning for Sequence Prediction

Train a model to predict good forming sequences:

```python
# Training data: (orders, constraints) → best_sequence from exhaustive search
# Model: sequence-to-sequence transformer

def predict_sequence(orders, constraints):
    features = encode_orders(orders)
    predicted = model.predict(features)
    return decode_sequence(predicted)
```

**Impact:** Potentially high if patterns exist.
**Effort:** Very High - Data collection, model training, validation.
**Recommendation:** Research project, not immediate priority.

---

## Engineering Best Practices

### Code Organization

```
backend/process/
├── scheduling/
│   ├── __init__.py
│   ├── models.py          # Data classes: Order, Machine, Schedule
│   ├── constraints.py     # Constraint checking functions
│   ├── candidates.py      # Forming sequence generation
│   ├── simulation.py      # Forward simulation engine
│   ├── dispatch.py        # Order dispatch strategies
│   ├── optimization.py    # Local search, post-processing
│   └── kpi.py             # KPI calculation
├── line_scheduler.py      # Main entry point (thin wrapper)
└── multiline.py           # Multi-line coordination
```

### Testing Strategy

1. **Unit tests** for each component:
   ```python
   def test_feasibility_check_respects_inventory():
       inventory = {'SKU1': 100}
       order = Order(sku='SKU1', quantity=200)
       assert not can_start(order, t_h=0, inventory=inventory)
   ```

2. **Property-based tests** for invariants:
   ```python
   @given(st.lists(orders()))
   def test_schedule_never_negative_inventory(orders):
       schedule = generate_schedule(orders)
       for t in range(schedule.horizon):
           for sku in schedule.inventory:
               assert schedule.inventory[sku][t] >= 0
   ```

3. **Regression tests** with known-good schedules:
   ```python
   def test_baseline_scenario():
       schedule = generate_schedule(BASELINE_ORDERS)
       assert schedule.kpi.on_time_rate >= 0.95  # Known achievable
   ```

### Performance Monitoring

Track scheduling performance over time:

```python
@dataclass
class SchedulingMetrics:
    execution_time_ms: float
    candidates_explored: int
    best_candidate_index: int
    kpi: ScheduleKPI

def log_scheduling_run(metrics: SchedulingMetrics):
    logger.info(f"Scheduled in {metrics.execution_time_ms}ms, "
                f"explored {metrics.candidates_explored} candidates, "
                f"best was #{metrics.best_candidate_index}, "
                f"on_time_rate={metrics.kpi.on_time_rate:.2%}")
```

### Configuration Management

Externalize algorithm parameters:

```json
// algorithm_config.json
{
  "max_candidates": 8,
  "simulation_horizon_h": 2000,
  "local_search_iterations": 100,
  "dispatch_strategy": "greedy",  // or "lookahead"
  "time_step_minutes": 60
}
```

### Documentation

1. **Docstrings** with examples for all public functions
2. **Type hints** for all function signatures
3. **Algorithm comments** explaining non-obvious logic
4. **Change log** for algorithm modifications

---

## Recommended Roadmap

| Phase | Improvements | Expected Gain |
|-------|--------------|---------------|
| **Phase 1** | L1 mold-change sequencing | 5-10% fewer late orders on L1 |
| **Phase 2** | Order batching pre-pass | 3-5% throughput improvement |
| **Phase 3** | Local search post-optimization | 5-10% tardiness reduction |
| **Phase 4** | Code refactoring (best practices) | Maintainability, testability |
| **Future** | CP solver (if needed) | Optimal solutions |

---

## References

- [Job Shop Scheduling](https://en.wikipedia.org/wiki/Job-shop_scheduling) - Classic problem formulation
- [Google OR-Tools](https://developers.google.com/optimization) - Open-source optimization library
- [Flexible Job Shop Survey](https://doi.org/10.1016/j.ejor.2015.10.057) - Academic survey of approaches
