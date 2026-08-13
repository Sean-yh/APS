#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

try:  # pragma: no cover
    from process.multiline import (  # type: ignore
        PROCESS_DIR,
        generate_all_lines,
        write_schedule_artifacts,
    )
except ModuleNotFoundError:  # pragma: no cover
    from multiline import (  # type: ignore
        PROCESS_DIR,
        generate_all_lines,
        write_schedule_artifacts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate combined APS schedule for L1/L2/L3 and render a 9-machine gantt.")
    parser.add_argument("--orders", type=Path, default=PROCESS_DIR / "orders_erp.json")
    parser.add_argument("--inventory", type=Path, default=PROCESS_DIR / "inventory_erp.json")
    parser.add_argument("--out", type=Path, default=PROCESS_DIR / "schedule_result.json")
    parser.add_argument("--gantt-out", type=Path, default=PROCESS_DIR / "schedule_gantt.html")
    parser.add_argument("--max-hours", type=int, default=8000)
    parser.add_argument("--px-per-day", type=int, default=120)
    parser.add_argument("--apply-downtime", action="store_true", default=True)
    parser.add_argument("--no-downtime", action="store_false", dest="apply_downtime")
    parser.add_argument("--also-write-per-line", action="store_true", default=True)
    parser.add_argument("--no-per-line", action="store_false", dest="also_write_per_line")
    args = parser.parse_args()

    if not args.orders.exists() or not args.inventory.exists():
        raise SystemExit("Missing required ERP snapshot files: process/orders_erp.json and process/inventory_erp.json")
    line_schedules, combined = generate_all_lines(
        orders_path=args.orders,
        inventory_path=args.inventory,
        max_hours=int(args.max_hours),
        apply_downtime=bool(args.apply_downtime),
    )

    if args.also_write_per_line:
        for line_id, sched in line_schedules.items():
            out_dir = PROCESS_DIR / "schedules" / line_id
            out_dir.mkdir(parents=True, exist_ok=True)
            write_schedule_artifacts(
                schedule=sched,
                schedule_path=out_dir / "schedule_result.json",
                gantt_path=out_dir / "schedule_gantt.html",
                px_per_day=int(args.px_per_day),
            )

    write_schedule_artifacts(
        schedule=combined,
        schedule_path=args.out,
        gantt_path=args.gantt_out,
        px_per_day=int(args.px_per_day),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
