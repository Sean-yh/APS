# Current L2 Algorithm (v0)

The current single-line algorithm for L2 lives in `backend/process/generate_schedule.py`.

High-level structure:

- Filter orders to L2 SKUs only (`S12G9C`, `S12G9W`, `S12G9V`).
- Forming plan is a fixed cycle across the 3 SKUs with 12h color-change blocks.
- A single degree of freedom is searched: `chain_start_h` in 12-hour steps
  (i.e. when to start the non-default part of the forming cycle).
- For each candidate `chain_start_h`, the scheduler simulates hour-by-hour:
  - ROTARY produces at a fixed rate when in "forming" mode for a SKU.
  - 2 label machines dispatch orders greedily by priority and deadline,
    with an inventory-safety feasibility check before starting an order.
- Objective for choosing the best schedule:
  1) minimize container tardiness (sum of hours late)
  2) maximize container on-time rate
  3) minimize order tardiness
  4) maximize order on-time rate
  5) minimize makespan

Output format matches `docs/algorithms/schedule_format.md`.
