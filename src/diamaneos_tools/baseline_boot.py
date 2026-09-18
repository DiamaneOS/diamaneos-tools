"""Declared FP6-022 stock restart-time measurement.

This runner measures an ordinary software restart from one host-monotonic
trigger to several separately reported Android milestones.  It deliberately
does not call that result a cold power-on time: the physical power-button
event belongs to the manual source-media procedure in ``baseline.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol
from diamaneos_tools import rig
from diamaneos_tools import test_runner


SCHEMA_VERSION = 1
OPERATION = "baseline-boot-restart-measurement"
LABEL = baseline_pilot.DECLARED_LABEL
MAX_TEXT_BYTES = 262_144


class BootError(Exception):
    """Controlled boot-measurement failure with a stable exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _procedure(protocol: dict) -> dict:
    return next(item for item in protocol["procedures"]
                if item["id"] == "boot-time")


def _owner_controlled_directory(path: Path):
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
            or metadata.st_uid != os.geteuid()):
        raise BootError(
            "private output must be owner-controlled mode 0750 or stricter", 3)


def _evidence(raw_dir: Path, name: str, content: str) -> str:
    digest = test_runner._write_evidence(raw_dir / name, content)
    return f"raw/{name}@sha256:{digest}"


def _scrub(value, target: str):
    if isinstance(value, dict):
        return {key: _scrub(item, target) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, target) for item in value]
    if isinstance(value, str):
        return value.replace(target, "<redacted-device-id>")
    return value


def _prepare_output(args) -> tuple[Path, Path, int]:
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    _owner_controlled_directory(root)
    partial = root / f"{args.run_id}.partial"
    final = root / args.run_id
    alternatives = [
        partial, final, final.with_name(final.name + ".non-comparable"),
        final.with_name(final.name + ".failed"),
        final.with_name(final.name + ".harness-error"),
    ]
    if any(path.exists() for path in alternatives):
        raise BootError("immutable boot output collision", 3)
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    lock_fd = os.open(locks / f"{args.device_role}.baseline-boot.lock",
                      os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        raise BootError("physical target is already locked", 3) from exc
    try:
        guard = rig.acquire_test_start_guard(
            args.rig_config, args.device_role, args.device_map, args.target)
        try:
            devices = test_runner._authorized_devices(args.adb)
            if args.target not in devices:
                raise BootError(
                    "selected private target is not an authorized USB device", 3)
            partial.mkdir(mode=0o750)
            (partial / "raw").mkdir(mode=0o750)
        finally:
            guard.release()
    except rig.RigError as exc:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        raise BootError(str(exc), exc.exit_code) from exc
    except Exception:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        raise
    return partial, final, lock_fd


def _adb_state(adb: str, target: str, executor) -> str:
    result = executor([adb, "-s", target, "get-state"], 3, 4096)
    if (result.get("transport") == "ok"
            and result.get("stdout", "").strip() == "device"):
        return "device"
    return "unavailable"


def _property(adb: str, target: str, name: str, executor) -> str | None:
    result = executor(
        [adb, "-s", target, "shell", "getprop", name], 3, 4096)
    if result.get("transport") != "ok":
        return None
    return result.get("stdout", "").strip()


def observe_restart(adb: str, target: str, parameters: dict, raw_dir: Path,
                    repetition: int, executor=test_runner.run_bounded,
                    clock=time.monotonic, sleeper=time.sleep) -> dict:
    """Run one restart and retain bounded milestone evidence.

    The returned structure never includes the private target.  Poll output is
    intentionally reduced to states and counters rather than copying a serial
    or an unbounded diagnostic stream into the report.
    """
    prefix = f"restart-r{repetition}"
    refs = []
    started = clock()
    reboot = executor([adb, "-s", target, "reboot"], 20, MAX_TEXT_BYTES)
    refs.append(_evidence(raw_dir, f"{prefix}.reboot.stdout.txt",
                          reboot.get("stdout", "")))
    refs.append(_evidence(raw_dir, f"{prefix}.reboot.stderr.txt",
                          reboot.get("stderr", "")))
    if reboot.get("transport") != "ok":
        return {
            "repetition": repetition,
            "status": "FAIL",
            "reason": "adb reboot did not complete successfully",
            "milestones_seconds": {},
            "raw_evidence_refs": refs,
        }

    poll_seconds = parameters["poll_interval_ms"] / 1000.0
    milestones = {}
    state_polls = 0
    property_polls = 0
    disconnect_deadline = started + parameters["disconnect_timeout_seconds"]
    while clock() <= disconnect_deadline:
        state_polls += 1
        if _adb_state(adb, target, executor) != "device":
            milestones["adb-unavailable"] = round(clock() - started, 3)
            break
        sleeper(poll_seconds)
    if "adb-unavailable" not in milestones:
        observation = {
            "schema_version": 1, "repetition": repetition,
            "state_polls": state_polls, "property_polls": property_polls,
            "milestones_seconds": milestones,
            "failure": "mapped device never became unavailable",
        }
        refs.append(_evidence(
            raw_dir, f"{prefix}.observer.json",
            json.dumps(observation, indent=2, sort_keys=True) + "\n"))
        return {
            "repetition": repetition, "status": "FAIL",
            "reason": "restart did not produce the required ADB-unavailable milestone",
            "milestones_seconds": milestones, "raw_evidence_refs": refs,
        }

    completion_deadline = started + parameters["completion_timeout_seconds"]
    while clock() <= completion_deadline:
        state_polls += 1
        if _adb_state(adb, target, executor) == "device":
            milestones["adb-authorized"] = round(clock() - started, 3)
            break
        sleeper(poll_seconds)
    if "adb-authorized" in milestones:
        while clock() <= completion_deadline:
            property_polls += 1
            if ("sys.boot_completed" not in milestones
                    and _property(adb, target, "sys.boot_completed", executor) == "1"):
                milestones["sys.boot_completed"] = round(clock() - started, 3)
            if ("service.bootanim.exit" not in milestones
                    and _property(adb, target, "service.bootanim.exit", executor) == "1"):
                milestones["service.bootanim.exit"] = round(clock() - started, 3)
            if all(name in milestones for name in parameters["required_milestones"]):
                break
            sleeper(poll_seconds)

    missing = [name for name in parameters["required_milestones"]
               if name not in milestones]
    observation = {
        "schema_version": 1,
        "repetition": repetition,
        "state_polls": state_polls,
        "property_polls": property_polls,
        "milestones_seconds": milestones,
        "missing_milestones": missing,
    }
    refs.append(_evidence(
        raw_dir, f"{prefix}.observer.json",
        json.dumps(observation, indent=2, sort_keys=True) + "\n"))
    if missing:
        return {
            "repetition": repetition, "status": "FAIL",
            "reason": "restart completion timed out before every required milestone",
            "milestones_seconds": milestones,
            "missing_milestones": missing,
            "raw_evidence_refs": refs,
        }
    ready = max(milestones["sys.boot_completed"],
                milestones["service.bootanim.exit"])
    return {
        "repetition": repetition,
        "status": "PASS",
        "reason": "all declared restart milestones were observed",
        "milestones_seconds": milestones,
        "ready_seconds": round(ready, 3),
        "raw_evidence_refs": refs,
    }


def _battery_preflight(adb: str, target: str, raw_dir: Path,
                       protocol: dict) -> tuple[dict, list[str], list[str]]:
    result = test_runner.run_bounded(
        [adb, "-s", target, "shell", "dumpsys", "battery"], 20,
        MAX_TEXT_BYTES)
    refs = [
        _evidence(raw_dir, "preflight-battery.stdout.txt",
                  result.get("stdout", "")),
        _evidence(raw_dir, "preflight-battery.stderr.txt",
                  result.get("stderr", "")),
    ]
    if result.get("transport") != "ok":
        return {}, refs, ["battery preflight command failed"]
    try:
        battery = baseline_pilot.parse_battery(result.get("stdout", ""))
    except ValueError:
        return {}, refs, ["battery preflight output was incomplete"]
    limits = protocol["environment_controls"]["connected_power"][
        "start_level_percent"]
    problems = []
    if not limits["minimum"] <= battery["level_percent"] <= limits["maximum"]:
        problems.append("battery level is outside the connected-run range")
    if battery["status_code"] != 5:
        problems.append("battery status is not FULL")
    if not (battery["ac_powered"] or battery["usb_powered"]):
        problems.append("ADB cable is not reported as a connected power source")
    return battery, refs, problems


def _network_preflight(adb: str, target: str,
                       raw_dir: Path) -> tuple[dict, list[str], list[str]]:
    commands = {
        "airplane": ["settings", "get", "global", "airplane_mode_on"],
        "bluetooth": ["settings", "get", "global", "bluetooth_on"],
        "wifi": ["cmd", "wifi", "status"],
        "sim-state": ["getprop", "gsm.sim.state"],
    }
    results = {}
    refs = []
    problems = []
    for label, shell_argv in commands.items():
        result = test_runner.run_bounded(
            [adb, "-s", target, "shell"] + shell_argv, 20, MAX_TEXT_BYTES)
        results[label] = result
        for stream in ("stdout", "stderr"):
            refs.append(_evidence(
                raw_dir, f"preflight-{label}.{stream}.txt",
                result.get(stream, "")))
        if result.get("transport") != "ok":
            problems.append(f"{label} preflight command failed")
    if problems:
        return {}, refs, problems
    try:
        airplane = int(results["airplane"]["stdout"].strip()) == 1
        bluetooth = int(results["bluetooth"]["stdout"].strip()) == 1
        wifi = baseline_pilot.parse_wifi_status(results["wifi"]["stdout"])
    except (ValueError, KeyError):
        return {}, refs, ["network preflight output was incomplete"]
    states = [value.strip().upper() for value in
              results["sim-state"]["stdout"].strip().split(",")
              if value.strip()]
    ready_count = sum(value in {"READY", "LOADED"} for value in states)
    observed = {
        "airplane_mode": airplane,
        "wifi_enabled": wifi,
        "bluetooth_enabled": bluetooth,
        "reported_sim_slot_count": len(states),
        "ready_or_loaded_sim_slot_count": ready_count,
    }
    if airplane:
        problems.append("airplane mode is enabled")
    if not wifi:
        problems.append("Wi-Fi is disabled")
    if ready_count < 1:
        problems.append("no ready or loaded SIM state was observed")
    return observed, refs, problems


def _report(args, protocol: dict, protocol_hash: str, repo_root: Path,
            identity: dict, identity_refs: list[str], battery: dict,
            network: dict, preflight_refs: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": OPERATION,
        "label": LABEL,
        "status": "INCOMPLETE",
        "run_id": args.run_id,
        "series_id": args.series_id,
        "repeat_index": args.repeat_index,
        "protocol": {
            "id": protocol["protocol_id"],
            "sha256": protocol_hash,
            "procedure": "boot-time.restart",
        },
        "tool": {
            "revision": test_runner._git_revision(repo_root),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "adb": test_runner._adb_version(args.adb),
        },
        "target": {
            "role": args.device_role,
            "model": identity["model"],
            "device": identity["device"],
            "build": identity["build_id"],
            "incremental": identity["incremental"],
            "firmware": identity["firmware"],
            "build_type": identity["build_type"],
            "security_patch": identity["security_patch"],
            "evidence_label": identity["evidence_label"],
        },
        "conditions": args.conditions,
        "ambient_start_c": args.ambient_start_c,
        "ambient_end_c": None,
        "ambient_span_c": None,
        "operator_attestations": {
            "ordinary_reboots_authorized": args.operator_authorized_reboots,
            "permanent_network_and_sim_state_confirmed":
                args.operator_confirmed_permanent_state,
        },
        "battery_preflight": battery,
        "network_preflight": network,
        "started_at_utc": _utc_now(),
        "finished_at_utc": None,
        "identity_evidence_refs": identity_refs,
        "preflight_evidence_refs": preflight_refs,
        "case": {
            "test_id": "restart-boot-time",
            "status": "NOT_RUN",
            "reason": "not started",
            "samples": [],
        },
        "comparability_exclusions": [],
        "limitations": [
            "This measures ordinary software restart from the host adb reboot trigger; it is not physical cold power-on time.",
            "Host polling resolution and ADB/property availability bound each reported milestone.",
            "Physical cold power-on remains a separate manual source-media measurement.",
        ],
        "errors": [],
    }


def execute_restart(args, repo_root: Path) -> tuple[int, Path]:
    if not test_runner.RUN_ID_RE.fullmatch(args.run_id or ""):
        raise BootError("execution requires a valid --run-id")
    if not test_runner.RUN_ID_RE.fullmatch(args.series_id or ""):
        raise BootError("execution requires a valid --series-id")
    if args.repeat_index not in {1, 2}:
        raise BootError("execution requires --repeat-index 1 or 2")
    if not args.target or not args.device_role or not args.device_map:
        raise BootError("execution requires target, role and private device map")
    if not test_runner.TOKEN_RE.fullmatch(args.device_role):
        raise BootError("invalid device role")
    if (not args.conditions or not args.conditions.strip()
            or len(args.conditions) > 4000):
        raise BootError("execution requires bounded non-empty --conditions")
    if args.target in args.run_id or args.target in args.series_id or args.target in args.conditions:
        raise BootError("run metadata must not contain the private target")
    if not -50.0 <= args.ambient_start_c <= 100.0:
        raise BootError("execution requires a plausible --ambient-start-c")
    if not args.operator_authorized_reboots:
        raise BootError("ordinary device reboots require explicit operator authorization", 3)
    if not args.operator_confirmed_permanent_state:
        raise BootError("permanent network and SIM state was not operator-confirmed", 3)
    protocol, protocol_hash = baseline_protocol.load_protocol(args.config)
    ambient = protocol["environment_controls"]["ambient_temperature"]
    if not (ambient["full_run_allowed_range"]["minimum"]
            <= args.ambient_start_c
            <= ambient["full_run_allowed_range"]["maximum"]):
        raise BootError("room temperature is outside the protocol range", 3)
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)

    partial, final, lock_fd = _prepare_output(args)
    report = None
    try:
        identity, identity_refs = test_runner._capture_identity(
            args.adb, args.target, partial, 20)
        battery, battery_refs, problems = _battery_preflight(
            args.adb, args.target, partial / "raw", protocol)
        network, network_refs, network_problems = _network_preflight(
            args.adb, args.target, partial / "raw")
        problems.extend(network_problems)
        if identity["build_id"] != args.expected_build:
            problems.append("target build does not match the expected stock build")
        if identity["build_type"] != "user" or identity["device"] != "FP6":
            problems.append("target is not the declared FP6 user build")
        report = _report(
            args, protocol, protocol_hash, repo_root, identity, identity_refs,
            battery, network, battery_refs + network_refs)
        result_path = partial / "result.json"
        if problems:
            report["status"] = "BLOCKED"
            report["case"]["status"] = "BLOCKED"
            report["case"]["reason"] = "; ".join(problems)
            report["errors"].extend(problems)
        else:
            params = _procedure(protocol)["fixed_parameters"]["restart"]
            for repetition in range(1, params["repetitions"] + 1):
                sample = observe_restart(
                    args.adb, args.target, params, partial / "raw", repetition)
                report["case"]["samples"].append(sample)
                report = _scrub(report, args.target)
                test_runner._atomic_json(result_path, report)
                if sample["status"] != "PASS":
                    report["status"] = "FAIL"
                    report["case"]["status"] = "FAIL"
                    report["case"]["reason"] = sample["reason"]
                    report["errors"].append(sample["reason"])
                    break
                if repetition < params["repetitions"]:
                    time.sleep(params["settle_seconds_between_repetitions"])
            else:
                report["case"]["status"] = "PASS"
                report["case"]["reason"] = "all three declared restart samples passed"
                report["status"] = "AWAITING_AMBIENT_END"

        report = _scrub(report, args.target)
        if report["status"] != "AWAITING_AMBIENT_END":
            report["finished_at_utc"] = _utc_now()
        test_runner._atomic_json(result_path, report)
        refs = report["identity_evidence_refs"] + report["preflight_evidence_refs"]
        for sample in report["case"]["samples"]:
            refs.extend(sample.get("raw_evidence_refs", []))
        test_runner._verify_evidence_refs(result_path, refs)
        if args.target in result_path.read_text(encoding="utf-8"):
            raise BootError("private target escaped report redaction", 5)
        if report["status"] == "AWAITING_AMBIENT_END":
            test_runner._sync_directory(partial)
            return 0, result_path
        suffix = ".failed" if report["status"] in {"FAIL", "BLOCKED"} else ".harness-error"
        destination = final.with_name(final.name + suffix)
        os.rename(partial, destination)
        test_runner._sync_directory(destination.parent)
        return 4 if report["status"] == "FAIL" else 3, destination / "result.json"
    except Exception:
        if report is not None and partial.exists():
            try:
                report["status"] = "HARNESS_ERROR"
                report["finished_at_utc"] = _utc_now()
                report["errors"].append(
                    "unexpected harness failure; inspect private raw evidence")
                result_path = partial / "result.json"
                test_runner._atomic_json(result_path, _scrub(report, args.target))
                destination = final.with_name(final.name + ".harness-error")
                if not destination.exists():
                    os.rename(partial, destination)
                    test_runner._sync_directory(destination.parent)
            except Exception:
                pass
        raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _load_partial(run_dir: Path) -> dict:
    if not run_dir.name.endswith(".partial"):
        raise BootError("boot finalization requires a partial run directory")
    _owner_controlled_directory(run_dir.parent)
    _owner_controlled_directory(run_dir)
    report, _ = test_runner._load_unique_json(
        run_dir / "result.json", test_runner.MAX_REPORT_BYTES)
    if (not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION
            or report.get("operation") != OPERATION or report.get("label") != LABEL
            or report.get("status") != "AWAITING_AMBIENT_END"):
        raise BootError("declared boot partial report is invalid", 5)
    return report


def _acquire_finalize_lock(root: Path, role: str) -> int:
    if not isinstance(role, str) or not test_runner.TOKEN_RE.fullmatch(role):
        raise BootError("declared boot report has an invalid device role", 5)
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    fd = os.open(locks / f"{role}.baseline-boot.lock",
                 os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise BootError("physical target is already locked", 3) from exc
    return fd


def _finalize_locked(run_dir: Path, ambient_end_c: float,
                     config: str) -> tuple[int, Path]:
    if not -50.0 <= ambient_end_c <= 100.0:
        raise BootError("finalization requires a plausible --ambient-end-c")
    report = _load_partial(run_dir)
    protocol, digest = baseline_protocol.load_protocol(config)
    if digest != report.get("protocol", {}).get("sha256"):
        raise BootError("baseline protocol changed during the boot run", 5)
    if (report.get("repeat_index") not in {1, 2}
            or not test_runner.RUN_ID_RE.fullmatch(report.get("series_id", ""))):
        raise BootError("declared boot series identity is invalid", 5)
    case = report.get("case")
    params = _procedure(protocol)["fixed_parameters"]["restart"]
    if (not isinstance(case, dict) or case.get("status") != "PASS"
            or len(case.get("samples", [])) != params["repetitions"]
            or any(sample.get("status") != "PASS"
                   for sample in case.get("samples", []))):
        raise BootError("declared boot sample inventory is incomplete", 5)
    report_path = run_dir / "result.json"
    refs = report.get("identity_evidence_refs", []) + report.get(
        "preflight_evidence_refs", [])
    for sample in case["samples"]:
        refs.extend(sample.get("raw_evidence_refs", []))
    test_runner._verify_evidence_refs(report_path, refs)

    controls = protocol["environment_controls"]["ambient_temperature"]
    allowed = controls["full_run_allowed_range"]
    span = round(abs(ambient_end_c - report["ambient_start_c"]), 2)
    exclusions = []
    if not allowed["minimum"] <= ambient_end_c <= allowed["maximum"]:
        exclusions.append("end ambient temperature is outside the protocol range")
    if span > controls["maximum_within_run_span"]:
        exclusions.append("ambient temperature span exceeds the protocol tolerance")
    base = run_dir.with_name(run_dir.name.removesuffix(".partial"))
    final = base.with_name(base.name + (".non-comparable" if exclusions else ""))
    if (base.exists() or base.with_name(base.name + ".non-comparable").exists()
            or base.with_name(base.name + ".failed").exists()
            or base.with_name(base.name + ".harness-error").exists()):
        raise BootError("immutable boot final output collision", 3)
    report["ambient_end_c"] = ambient_end_c
    report["ambient_span_c"] = span
    report["comparability_exclusions"] = exclusions
    report["finished_at_utc"] = _utc_now()
    report["tool"]["finalizer_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    report["status"] = "NON_COMPARABLE" if exclusions else "PASS"
    test_runner._atomic_json(report_path, report)
    os.rename(run_dir, final)
    test_runner._sync_directory(final.parent)
    return (4 if exclusions else 0), final / "result.json"


def finalize(run_dir_value: str, ambient_end_c: float,
             config: str) -> tuple[int, Path]:
    run_dir = Path(run_dir_value).resolve()
    report = _load_partial(run_dir)
    lock_fd = _acquire_finalize_lock(
        run_dir.parent, report.get("target", {}).get("role"))
    try:
        # Re-read and validate only after the role lock is held so a competing
        # finalizer cannot race the first inspection.
        return _finalize_locked(run_dir, ambient_end_c, config)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def dry_run_plan(config: str) -> dict:
    protocol, digest = baseline_protocol.load_protocol(config)
    params = _procedure(protocol)["fixed_parameters"]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": OPERATION + "-dry-run",
        "label": LABEL,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest,
        "device_commands_executed": 0,
        "output_directories_created": 0,
        "restart": params["restart"],
        "cold_power_on": params["cold_power_on"],
        "device_state_changes": [
            "three explicitly authorized ordinary adb reboots per repetition",
            "no wipe, package-data clear, radio reconfiguration or bootloader action",
        ],
        "measurement_boundary": (
            "restart and physical cold power-on are separate metrics; the latter "
            "requires manual source media"),
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Target-bound FP6 declared stock boot baseline")
    parser.add_argument(
        "--config", default=str(_repo_root() / "config" / "baseline.json"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("dry-run")
    run = sub.add_parser("restart")
    run.add_argument("--target", required=True, help="private exact ADB serial")
    run.add_argument("--device-role", required=True)
    run.add_argument("--device-map", required=True)
    run.add_argument("--rig-config")
    run.add_argument("--run-id", required=True)
    run.add_argument("--series-id", required=True)
    run.add_argument("--repeat-index", required=True, type=int, choices=(1, 2))
    run.add_argument("--expected-build", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--conditions", required=True)
    run.add_argument("--ambient-start-c", required=True, type=float)
    run.add_argument("--operator-authorized-reboots", action="store_true")
    run.add_argument("--operator-confirmed-permanent-state", action="store_true")
    run.add_argument("--adb", default="adb")
    finish = sub.add_parser("finalize")
    finish.add_argument("--run-dir", required=True)
    finish.add_argument("--ambient-end-c", required=True, type=float)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "dry-run":
            print(json.dumps(dry_run_plan(args.config), indent=2, sort_keys=True))
            return 0
        if args.action == "restart":
            code, result = execute_restart(args, _repo_root())
        else:
            code, result = finalize(args.run_dir, args.ambient_end_c, args.config)
        print(f"result={result}")
        return code
    except (BootError, baseline_protocol.ProtocolError,
            test_runner.RunnerError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
