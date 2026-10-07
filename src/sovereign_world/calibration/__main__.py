"""``python -m sovereign_world.calibration run|report`` (see ``batch.py``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sovereign_world.calibration.batch import QUICK, run_batch, seeds_of, specs_of, workers_of
from sovereign_world.calibration.report import write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sovereign_world.calibration")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="play scripted histories")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--seeds", default="0-249")
    run.add_argument("--size", type=int, default=32)
    run.add_argument("--days", type=int, default=365)
    run.add_argument("--rotations", default="all")
    run.add_argument("--assignments", default="builders,mixed")
    run.add_argument(
        "--council-interval",
        type=int,
        default=28,
        choices=(7, 14, 21, 28, 30),
        help="days between councils, as in the world to be launched",
    )
    run.add_argument("--workers", default="auto")
    run.add_argument("--quick", action="store_true", help="a small preset, for a quick check")
    report = commands.add_parser("report", help="write the fairness report")
    report.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.quick:
            for key, value in QUICK.items():
                setattr(args, key, value)
        specs = specs_of(
            seeds_of(args.seeds),
            args.size,
            args.days,
            args.rotations,
            args.assignments,
            args.council_interval,
        )
        summary = run_batch(args.out, specs, workers_of(args.workers))
        print(
            f"{summary['histories']} histories ({summary['played_now']} played now) in"
            f" {summary['elapsed_seconds']} s on {summary['workers']} workers: {args.out}"
        )
        failed = summary["failed"]
        if isinstance(failed, list) and failed:
            print(f"{len(failed)} histories failed (engine bugs); see {args.out / 'failures.txt'}")
            return 1
        return 0
    result = write_report(args.directory)
    print(f"{'passed' if result['passed'] else 'FAILED'}: {args.directory / 'report.md'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
