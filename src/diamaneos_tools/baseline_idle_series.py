"""Detached two-repeat controller for declared rig-backed FP6 idle runs.

The operator authorizes each batterystats reset separately before launch.  The
worker then owns both eight-hour timing boundaries on the tester, recharges and
rechecks the mapped phone between repetitions, and never depends on an SSH
session or another computer remaining awake.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import types

from diamaneos_tools import baseline_idle
from diamaneos_tools import rig
from diamaneos_tools import test_runner


SCHEMA_VERSION = 1
MAX_SPEC_BYTES = 64 * 1024
RECHARGE_TIMEOUT_SECONDS = 4 * 3600
RECHARGE_POLL_SECONDS = 30
WAIT_POLL_SECONDS = 30


class SeriesError(Exception):
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _atomic_json(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o640)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written < 1:
                raise SeriesError("idle-series state write did not complete", 5)
            remaining = remaining[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o640)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    test_runner._sync_directory(path.parent)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o750, exist_ok=True)
    if path.is_symlink():
        raise SeriesError("idle-series storage must not be a symbolic link", 3)
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o027):
        raise SeriesError(
            "idle-series storage must be owner-controlled mode 0750 or stricter",
            3)


def _read_json(path: Path, expected_hash: str | None = None) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SeriesError("idle-series state is unreadable", 3) from exc
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o037):
            raise SeriesError(
                "idle-series state must be owner-controlled mode 0640 or stricter",
                3)
        chunks = []
        remaining = MAX_SPEC_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > MAX_SPEC_BYTES:
        raise SeriesError("idle-series state exceeds its byte limit", 3)
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        raise SeriesError("idle-series authorization record changed", 3)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeriesError("idle-series state is invalid", 3) from exc
    if not isinstance(value, dict):
        raise SeriesError("idle-series state is invalid", 3)
    return value


def _validate_spec(spec: dict) -> dict:
    expected = {
        "schema_version", "series_id", "target", "device_role",
        "device_map", "rig_config", "adb", "config", "output",
        "expected_build", "conditions", "run_ids", "authorizations",
        "prepared_at_utc",
    }
    if not isinstance(spec, dict) or set(spec) != expected:
        raise SeriesError("idle-series authorization record is invalid", 3)
    if (spec.get("schema_version") != SCHEMA_VERSION
            or not isinstance(spec.get("series_id"), str)
            or not test_runner.RUN_ID_RE.fullmatch(spec["series_id"])
            or not isinstance(spec.get("target"), str)
            or not spec["target"]
            or any(ch.isspace() for ch in spec["target"])
            or not isinstance(spec.get("device_role"), str)
            or not isinstance(spec.get("expected_build"), str)
            or not isinstance(spec.get("conditions"), str)
            or not spec["conditions"]):
        raise SeriesError("idle-series authorization record is invalid", 3)
    for field in ("device_map", "rig_config", "config", "output"):
        value = spec.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise SeriesError("idle-series authorization record is invalid", 3)
    run_ids = spec.get("run_ids")
    if (not isinstance(run_ids, dict) or set(run_ids) != {"1", "2"}
            or any(not isinstance(value, str)
                   or not test_runner.RUN_ID_RE.fullmatch(value)
                   for value in run_ids.values())
            or len(set(run_ids.values())) != 2):
        raise SeriesError("idle-series authorization record is invalid", 3)
    authorizations = spec.get("authorizations")
    expected_authorizations = {
        "repeat_1_batterystats_reset", "repeat_2_batterystats_reset",
        "display_50_percent", "unlocked_no_screen_lock",
        "no_phone_interaction_for_series",
        "no_material_network_outage_planned",
    }
    if (not isinstance(authorizations, dict)
            or set(authorizations) != expected_authorizations
            or any(value is not True for value in authorizations.values())):
        raise SeriesError("idle-series authorization record is invalid", 3)
    return spec


def _event(state: dict, phase: str, **details) -> None:
    state["phase"] = phase
    state["updated_at_utc"] = baseline_idle._utc_now()
    state.setdefault("events", []).append({
        "at_utc": state["updated_at_utc"], "phase": phase, **details})


def _worker_lock(job: Path) -> int:
    fd = os.open(
        job / ".worker.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise SeriesError("idle-series worker is already active", 3) from exc
    return fd


def _write_state(job: Path, state: dict) -> None:
    _atomic_json(job / "state.json", state)


def _timer_is_inactive() -> bool:
    active = subprocess.run(
        ["systemctl", "is-active", "diamaneos-rig-maintenance.timer"],
        capture_output=True, text=True, check=False)
    enabled = subprocess.run(
        ["systemctl", "is-enabled", "diamaneos-rig-maintenance.timer"],
        capture_output=True, text=True, check=False)
    return (active.stdout.strip() == "inactive"
            and enabled.stdout.strip() == "disabled")


def _wake_phone(adb: str, target: str) -> None:
    for command in (
            ["input", "keyevent", "KEYCODE_WAKEUP"],
            ["wm", "dismiss-keyguard"],
            ["input", "keyevent", "KEYCODE_HOME"]):
        baseline_idle._run_required(adb, target, command)
    time.sleep(2)


def _run_args(spec: dict, repeat_index: int, run_id: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        config=spec["config"], target=spec["target"],
        device_role=spec["device_role"], device_map=spec["device_map"],
        rig_config=spec["rig_config"],
        disconnect_method=baseline_idle.DISCONNECT_METHOD_RIG,
        adb=spec["adb"], run_id=run_id, output=spec["output"],
        expected_build=spec["expected_build"], conditions=spec["conditions"],
        declared_repeat_index=repeat_index, series_id=spec["series_id"],
        operator_confirmed_display_50=True,
        operator_confirmed_unlocked=True,
        operator_authorized_batterystats_reset=True,
        operator_committed_no_interaction=True,
        operator_declared_no_planned_network_outage=True)


def _disconnect_args(spec: dict, run_dir: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        target=spec["target"], device_role=spec["device_role"],
        device_map=spec["device_map"], rig_config=spec["rig_config"],
        adb=spec["adb"], run_dir=str(run_dir), timeout_seconds=120)


def _finish_args(spec: dict, run_dir: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        config=spec["config"], target=spec["target"],
        device_role=spec["device_role"], device_map=spec["device_map"],
        rig_config=spec["rig_config"], adb=spec["adb"],
        run_dir=str(run_dir), wait_for_reconnect=False,
        reconnect_timeout_seconds=120,
        operator_confirmed_physical_disconnect=False,
        operator_confirmed_no_interaction=False,
        operator_confirmed_no_known_network_outage=False,
        automated_rig_series=True)


def _quarantine_args(spec: dict, run_dir: Path, reason: str):
    return types.SimpleNamespace(
        target=spec["target"], device_role=spec["device_role"],
        device_map=spec["device_map"], rig_config=spec["rig_config"],
        adb=spec["adb"], run_dir=str(run_dir),
        reason=reason[:500], status="HARNESS_ERROR")


def _wait_until_finish(run_dir: Path, state: dict, job: Path) -> None:
    while True:
        status = baseline_idle.status(types.SimpleNamespace(run_dir=str(run_dir)))
        if status.get("host_rebooted"):
            raise SeriesError("tester rebooted during the idle interval", 4)
        remaining = status.get("remaining_seconds")
        ready = status.get("ready_to_reconnect")
        if (status.get("status") != baseline_idle.STATUS_DISCONNECTED_RIG
                or status.get("host_rebooted") is not False
                or type(ready) is not bool
                or type(remaining) not in (int, float)
                or not math.isfinite(remaining) or remaining < 0):
            raise SeriesError("idle interval status is missing or invalid", 5)
        state["remaining_seconds"] = remaining
        _write_state(job, state)
        # The display countdown can round to zero before the finish gate opens.
        if ready:
            return
        time.sleep(min(WAIT_POLL_SECONDS, max(0.1, remaining)))


def _wait_for_full(spec: dict, state: dict, job: Path) -> None:
    controller = rig.controller_for_target(
        spec["rig_config"], spec["device_role"],
        spec["device_map"], spec["target"])
    deadline = time.monotonic() + RECHARGE_TIMEOUT_SECONDS
    while True:
        if not _timer_is_inactive():
            raise SeriesError(
                "scheduled rig maintenance became active during recharge", 3)
        report = controller.status()
        observed = next(
            item for item in report["roles"]
            if item["role"] == spec["device_role"])
        battery = observed.get("battery") or {}
        state["recharge"] = {
            "level_percent": battery.get("level_percent"),
            "externally_powered": battery.get("externally_powered"),
            "status_code": battery.get("status_code"),
            "observed_at_utc": report["captured_at_utc"],
        }
        _write_state(job, state)
        if (observed.get("adb_state") == "device"
                and observed.get("path_matches") is True
                and observed.get("port_powered") is True
                and battery.get("externally_powered") is True
                and battery.get("level_percent") == 100
                and battery.get("status_code") == 5):
            return
        if time.monotonic() >= deadline:
            raise SeriesError(
                "phone did not return to a verified full state before repeat 2",
                4)
        time.sleep(RECHARGE_POLL_SECONDS)


def _recover(spec: dict, active_run: Path | None, message: str) -> None:
    if active_run is not None and active_run.exists():
        try:
            baseline_idle.quarantine(
                _quarantine_args(spec, active_run, message))
            return
        except Exception:
            pass
    try:
        controller = rig.controller_for_target(
            spec["rig_config"], spec["device_role"],
            spec["device_map"], spec["target"])
        if spec["target"] not in test_runner._authorized_devices(spec["adb"]):
            controller.set_power(
                spec["device_role"], "on", "idle-series-recovery",
                allowed_run_id=(
                    active_run.name.removesuffix(".partial")
                    if active_run is not None else None))
    except Exception:
        pass


def run(job: Path, expected_spec_hash: str, repo_root: Path) -> int:
    if (not isinstance(expected_spec_hash, str)
            or len(expected_spec_hash) != 64
            or not all(character in "0123456789abcdef"
                       for character in expected_spec_hash)):
        raise SeriesError("idle-series authorization hash is invalid", 3)
    lock_fd = _worker_lock(job)
    spec = None
    state = None
    active_run = None
    try:
        spec = _validate_spec(_read_json(job / "spec.json", expected_spec_hash))
        state = _read_json(job / "state.json")
        if state.get("phase") != "PREPARED":
            raise SeriesError("idle series is not prepared", 3)
        if not _timer_is_inactive():
            raise SeriesError(
                "scheduled rig maintenance became active before worker start", 3)
        controller = rig.controller_for_target(
            spec["rig_config"], spec["device_role"],
            spec["device_map"], spec["target"])
        controller.verify_hub()
        _event(state, "RUNNING")
        _write_state(job, state)
        results = []
        for repeat_index in (1, 2):
            if repeat_index == 2:
                _event(state, "RECHARGING_FOR_REPEAT_2")
                _write_state(job, state)
                _wait_for_full(spec, state, job)
            _wake_phone(spec["adb"], spec["target"])
            run_id = spec["run_ids"][str(repeat_index)]
            _event(state, f"STARTING_REPEAT_{repeat_index}", run_id=run_id)
            _write_state(job, state)
            active_run = Path(spec["output"]).resolve() / f"{run_id}.partial"
            code, start_path = baseline_idle.start(
                _run_args(spec, repeat_index, run_id), repo_root)
            if code != 0:
                raise SeriesError(
                    f"repeat {repeat_index} preflight was rejected", code)
            active_run = start_path.parent
            state["reset_authorizations_consumed"][str(repeat_index)] = True
            _event(state, f"DISCONNECTING_REPEAT_{repeat_index}", run_id=run_id)
            _write_state(job, state)
            baseline_idle.observe_disconnect(
                _disconnect_args(spec, active_run))
            _event(state, f"MEASURING_REPEAT_{repeat_index}", run_id=run_id)
            _write_state(job, state)
            _wait_until_finish(active_run, state, job)
            if not _timer_is_inactive():
                raise SeriesError(
                    "scheduled rig maintenance became active during idle series",
                    3)
            _event(state, f"FINALIZING_REPEAT_{repeat_index}", run_id=run_id)
            _write_state(job, state)
            code, result_path = baseline_idle.finish(
                _finish_args(spec, active_run))
            active_run = None
            if code != 0:
                raise SeriesError(
                    f"repeat {repeat_index} was not comparable", code)
            result_hash = hashlib.sha256(result_path.read_bytes()).hexdigest()
            results.append({
                "repeat_index": repeat_index,
                "run_id": run_id,
                "result": str(result_path),
                "result_sha256": result_hash,
            })
            state["results"] = results
            _write_state(job, state)
        state.pop("remaining_seconds", None)
        _event(state, "PASS")
        _write_state(job, state)
        return 0
    except Exception as exc:
        message = str(exc)[:500]
        if spec is not None:
            _recover(spec, active_run, message)
        if state is not None:
            _event(state, "FAILED", error=message)
            _write_state(job, state)
        return getattr(exc, "exit_code", 5)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def launch(args, repo_root: Path) -> dict:
    required = (
        args.operator_confirmed_display_50,
        args.operator_confirmed_unlocked,
        args.operator_authorized_repeat_1_batterystats_reset,
        args.operator_authorized_repeat_2_batterystats_reset,
        args.operator_committed_no_interaction,
        args.operator_declared_no_planned_network_outage,
    )
    if not all(required):
        raise SeriesError(
            "launch requires both named reset authorizations and all unattended commitments",
            3)
    if not test_runner.RUN_ID_RE.fullmatch(args.series_id):
        raise SeriesError("idle series ID is invalid", 3)
    if not _timer_is_inactive():
        raise SeriesError(
            "scheduled rig maintenance must be disabled before idle-series launch",
            3)
    config = rig.load_config(Path(args.rig_config), runtime=True)
    if Path(config["device_map"]).resolve() != Path(args.device_map).resolve():
        raise SeriesError("idle-series device map does not match rig config", 3)
    target = rig.mapped_serial(Path(args.device_map), args.device_role)
    test_runner.load_device_map(Path(args.device_map), args.device_role, target)
    if target not in test_runner._authorized_devices(args.adb):
        raise SeriesError("mapped idle-series target is not authorized", 3)
    if target in args.conditions:
        raise SeriesError("idle-series conditions must not contain the private target", 3)
    baseline_idle.dry_run(
        args.config, declared_repeat_index=1, series_id=args.series_id,
        disconnect_method=baseline_idle.DISCONNECT_METHOD_RIG)
    root = Path(args.job_root).resolve()
    _private_directory(root)
    for candidate in root.iterdir():
        if not candidate.is_dir() or not (candidate / "state.json").is_file():
            continue
        prior = _read_json(candidate / "state.json")
        if prior.get("phase") not in {"PASS", "FAILED"}:
            raise SeriesError("another idle series is active", 3)
    job = root / args.series_id
    if job.exists():
        raise SeriesError("immutable idle-series output collision", 3)
    job.mkdir(mode=0o750)
    stamp = baseline_idle._utc_now().replace("-", "").replace(":", "")
    stamp = stamp.split(".")[0].replace("T", "T") + "Z"
    spec = {
        "schema_version": SCHEMA_VERSION,
        "series_id": args.series_id,
        "target": target,
        "device_role": args.device_role,
        "device_map": str(Path(args.device_map).resolve()),
        "rig_config": str(Path(args.rig_config).resolve()),
        "adb": args.adb,
        "config": args.config,
        "output": str(Path(args.output).resolve()),
        "expected_build": args.expected_build,
        "conditions": args.conditions,
        "run_ids": {
            "1": f"fp6-022-idle-stock16-final-r1-{stamp}",
            "2": f"fp6-022-idle-stock16-final-r2-{stamp}",
        },
        "authorizations": {
            "repeat_1_batterystats_reset": True,
            "repeat_2_batterystats_reset": True,
            "display_50_percent": True,
            "unlocked_no_screen_lock": True,
            "no_phone_interaction_for_series": True,
            "no_material_network_outage_planned": True,
        },
        "prepared_at_utc": baseline_idle._utc_now(),
    }
    _atomic_json(job / "spec.json", spec)
    spec_hash = hashlib.sha256((job / "spec.json").read_bytes()).hexdigest()
    state = {
        "schema_version": SCHEMA_VERSION,
        "series_id": args.series_id,
        "phase": "PREPARED",
        "prepared_at_utc": spec["prepared_at_utc"],
        "updated_at_utc": spec["prepared_at_utc"],
        "reset_authorizations_consumed": {"1": False, "2": False},
        "results": [],
        "events": [],
    }
    _event(state, "PREPARED")
    _write_state(job, state)
    log = (job / "worker.log").open("ab", buffering=0)
    command = [
        str(repo_root / "bin" / "diamaneos"),
        "baseline", "idle", "series", "run",
        "--job-dir", str(job),
        "--expected-spec-sha256", spec_hash,
    ]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            cwd="/", start_new_session=True, close_fds=True,
            env={**os.environ, "HOME": os.environ.get(
                "HOME", "/var/lib/diamaneos-test")})
    finally:
        log.close()
    return {
        "status": "LAUNCHED",
        "series_id": args.series_id,
        "worker_pid": process.pid,
        "state": str(job / "state.json"),
        "log": str(job / "worker.log"),
    }


def status(args) -> dict:
    if not test_runner.RUN_ID_RE.fullmatch(args.series_id):
        raise SeriesError("idle series ID is invalid", 3)
    job = Path(args.job_root).resolve() / args.series_id
    state = _read_json(job / "state.json")
    return state


def dispatch(args, repo_root: Path) -> int:
    try:
        if args.series_action == "launch":
            print(json.dumps(launch(args, repo_root), indent=2, sort_keys=True))
            return 0
        if args.series_action == "status":
            print(json.dumps(status(args), indent=2, sort_keys=True))
            return 0
        return run(
            Path(args.job_dir).resolve(),
            args.expected_spec_sha256, repo_root)
    except (SeriesError, baseline_idle.IdleError, rig.RigError,
            test_runner.RunnerError) as exc:
        print(str(exc), file=os.sys.stderr)
        return getattr(exc, "exit_code", 2)
