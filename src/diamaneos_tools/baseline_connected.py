"""Declared FP6-022 connected stock-baseline workflow.

The workload runner leaves a successful measurement in a partial directory so
the operator can record the end thermometer reading immediately afterwards.
Finalization applies the protocol ambient limits before assigning PASS.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol
from diamaneos_tools import test_runner


class ConnectedError(Exception):
    """Controlled declared-connected error with a stable exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _owner_controlled_directory(path: Path):
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
            or metadata.st_uid != os.geteuid()):
        raise ConnectedError(
            "private output must be owner-controlled mode 0750 or stricter", 3)


def _load_partial(run_dir: Path) -> dict:
    if not run_dir.name.endswith(".partial"):
        raise ConnectedError("connected finalization requires a partial run directory")
    _owner_controlled_directory(run_dir.parent)
    _owner_controlled_directory(run_dir)
    report, _ = test_runner._load_unique_json(
        run_dir / "result.json", test_runner.MAX_REPORT_BYTES)
    if (not isinstance(report, dict)
            or report.get("schema_version") != baseline_pilot.SCHEMA_VERSION
            or report.get("operation") != "baseline-connected-measurement"
            or report.get("label") != baseline_pilot.DECLARED_LABEL
            or report.get("status") != "AWAITING_AMBIENT_END"):
        raise ConnectedError("declared connected partial report is invalid", 5)
    return report


def _acquire_lock(root: Path, role: str) -> int:
    if not isinstance(role, str) or not test_runner.TOKEN_RE.fullmatch(role):
        raise ConnectedError("declared connected report has an invalid device role", 5)
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    fd = os.open(locks / f"{role}.baseline-pilot.lock",
                 os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ConnectedError("physical target is already locked", 3) from exc
    return fd


def finalize(run_dir_value: str, ambient_end_c: float, config: str) -> tuple[int, Path]:
    if not -50.0 <= ambient_end_c <= 100.0:
        raise ConnectedError("finalization requires a plausible --ambient-end-c")
    run_dir = Path(run_dir_value).resolve()
    report = _load_partial(run_dir)
    role = report.get("target", {}).get("role")
    lock_fd = _acquire_lock(run_dir.parent, role)
    try:
        # Re-read after acquiring the lock so a concurrent writer cannot race
        # the validation above.
        report = _load_partial(run_dir)
        protocol, digest = baseline_protocol.load_protocol(config)
        if digest != report.get("protocol", {}).get("sha256"):
            raise ConnectedError("baseline protocol changed during the connected run", 5)
        expected_profile = baseline_pilot.connected_profile(protocol, True)
        if report.get("run_profile") != expected_profile:
            raise ConnectedError("declared connected run profile is invalid", 5)
        cases = report.get("cases")
        series_id = report.get("series_id")
        if (not isinstance(series_id, str)
                or not test_runner.RUN_ID_RE.fullmatch(series_id)
                or report.get("repeat_index") not in {1, 2}):
            raise ConnectedError("declared connected series identity is invalid", 5)
        expected_cases = [
            "connected-preflight", "app-launch", "frame-time", "thermal",
            "memory-pressure", "connected-postflight",
        ]
        if (not isinstance(cases, list)
                or report.get("run_order") != expected_cases
                or any(not isinstance(case, dict) for case in cases)
                or [case.get("test_id") for case in cases]
                != expected_cases
                or any(case.get("status") != "PASS"
                       for case in cases)):
            raise ConnectedError("declared connected case inventory is incomplete", 5)
        report_path = run_dir / "result.json"
        test_runner._verify_evidence_refs(
            report_path, report.get("identity_evidence_refs", []))
        for case in report.get("cases", []):
            test_runner._verify_evidence_refs(
                report_path, case.get("raw_evidence_refs", []))

        controls = protocol["environment_controls"]["ambient_temperature"]
        allowed = controls["full_run_allowed_range"]
        span = round(abs(ambient_end_c - report["ambient_start_c"]), 2)
        exclusions = []
        if not allowed["minimum"] <= ambient_end_c <= allowed["maximum"]:
            exclusions.append("end ambient temperature is outside the protocol range")
        if span > controls["maximum_within_run_span"]:
            exclusions.append("ambient temperature span exceeds the protocol tolerance")

        base = run_dir.with_name(run_dir.name.removesuffix(".partial"))
        suffix = ".non-comparable" if exclusions else ""
        final = base.with_name(base.name + suffix)
        if (base.exists()
                or base.with_name(base.name + ".non-comparable").exists()
                or base.with_name(base.name + ".harness-error").exists()):
            raise ConnectedError("immutable connected final output collision", 3)

        report["ambient_end_c"] = ambient_end_c
        report["ambient_span_c"] = span
        report["comparability_exclusions"] = exclusions
        report["finished_at_utc"] = baseline_pilot._utc_now()
        report["tool"]["finalizer_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()
        report["status"] = "NON_COMPARABLE" if exclusions else "PASS"
        test_runner._atomic_json(report_path, report)
        os.rename(run_dir, final)
        test_runner._sync_directory(final.parent)
        return (4 if exclusions else 0), final / "result.json"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Target-bound FP6 declared connected baseline")
    parser.add_argument("--config", default=str(_repo_root() / "config" / "baseline.json"))
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
    run.add_argument("--ambient-start-c", required=True, type=float)
    run.add_argument("--operator-confirmed-display-50", action="store_true")
    run.add_argument("--operator-confirmed-unlocked", action="store_true")
    run.add_argument("--adb", default="adb")
    finish = sub.add_parser("finalize")
    finish.add_argument("--run-dir", required=True)
    finish.add_argument("--ambient-end-c", required=True, type=float)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "dry-run":
            print(json.dumps(
                baseline_pilot.dry_run_plan(args.config, declared=True),
                indent=2, sort_keys=True))
            return 0
        if args.action == "run":
            code, result = baseline_pilot.execute_connected(
                args, _repo_root(), declared=True)
        else:
            code, result = finalize(
                args.run_dir, args.ambient_end_c, args.config)
        print(f"result={result}")
        return code
    except (ConnectedError, baseline_pilot.PilotError,
            baseline_protocol.ProtocolError, test_runner.RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
