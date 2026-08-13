# Multi-line Plan (L1/L2/L3)

Goal: support 3 independent lines (9 machines total) while:

- generating schedules per line,
- merging them for a single combined 9-lane Gantt view,
- computing container deliverability even when a container (`poreference`) spans multiple lines.

Key idea:

- Scheduling is done per line because machines are disjoint.
- The combined "ALL" schedule is a merge artifact used for display and cross-line container KPIs.

Container handling:

- A container may contain orders across multiple lines.
- Container deliverable time = max(order end) across all its orders.
- Container deadline (current rule) = min(order deadline) across all its orders.

Future rescheduling behavior (not required for first cut):

- For a container deadline constraint, split the container's order ids by line,
  reschedule each line subset with the same container deadline, then merge results.

