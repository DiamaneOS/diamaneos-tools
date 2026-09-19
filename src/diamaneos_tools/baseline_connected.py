"""Declared connected stock-baseline workflow.

A successful measurement is finalized immediately after its postflight capture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol
from diamaneos_tools import test_runner


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Target-bound FP6 declared connected baseline")
    parser.add_argument(
        "--config", default=str(_repo_root() / "config" / "baseline.json"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("dry-run")
    run = sub.add_parser("run")
    run.add_argument("--target", required=True, help="private exact ADB serial")
    run.add_argument("--device-role", required=True)
    run.add_argument("--device-map", required=True)
    run.add_argument("--rig-config")
    run.add_argument("--run-id", required=True)
    run.add_argument("--series-id", required=True)
    run.add_argument("--repeat-index", required=True, type=int, choices=(1, 2))
    run.add_argument("--expected-build", required=True,
                     help="exact ro.build.id expected on the stock target")
    run.add_argument("--output", required=True)
    run.add_argument("--conditions", required=True)
    run.add_argument("--operator-confirmed-display-50", action="store_true")
    run.add_argument("--operator-confirmed-unlocked", action="store_true")
    run.add_argument("--adb", default="adb")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "dry-run":
            print(json.dumps(
                baseline_pilot.dry_run_plan(args.config, declared=True),
                indent=2, sort_keys=True))
            return 0
        code, result = baseline_pilot.execute_connected(
            args, _repo_root(), declared=True)
        print(f"result={result}")
        return code
    except (baseline_pilot.PilotError, baseline_protocol.ProtocolError,
            test_runner.RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
