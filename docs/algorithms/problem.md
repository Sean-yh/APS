# Scheduling Problem (GX APS)

This repo implements an APS scheduler for a two-stage production flow:

- Stage 1: Forming (ROTARY)
- Stage 2: Labeling (LABEL)

Key constraints (from `问题.md`):

- Inventory safety: inventory trajectory must never go negative.
- 100% orders must be scheduled.
- No-split: each order must run continuously on one labeling machine.
- Machine eligibility: each SKU can only run on the machines of its line.
- Setup times (forming only):
  - Color change: 12h when switching SKU within a series (e.g. S12G8A -> S12G8C).
  - Mold change: 72h on ROTARY-1 when switching between S18B1* <-> S18G9* (includes color change).
- Labeling changeover time: 0h.

Delivery unit:

- Container = `poreference`
- Container deliverable time = max(label end time) across all orders in that container
- Container due/deadline aggregation (current implementation): min(order due/deadline) within the container

