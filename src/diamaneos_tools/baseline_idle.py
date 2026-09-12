"""Staged physical-disconnect idle pilot for FP6-022.

The start stage captures controlled state, performs the one explicitly
authorized batterystats reset, and turns the display off.  A separate stage
observes loss of the ADB transport; the operator's later attestation is what
establishes that the cable was physically removed rather than logically
disabled.  Finish refuses an early reconnect and preserves private evidence
without writing SSID, BSSID, subscriber, cell, or ADB identifiers to reports.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol
from diamaneos_tools import test_runner


SCHEMA_VERSION = 1
PILOT_LABEL = "PILOT_ONLY_NOT_BASELINE_EVIDENCE"
STATUS_ARMED = "ARMED_FOR_PHYSICAL_DISCONNECT"
STATUS_DISCONNECTED = "PHYSICALLY_DISCONNECTED_INTERVAL"
MAX_BATTERYSTATS_BYTES = 8 * 1024 * 1024
DISCONNECT_TIMEOUT_SECONDS = 120
DISCONNECT_CONFIRMATION_SAMPLES = 2
PILOT_FINISH_TOLERANCE_SECONDS = 60


class IdleError(Exception):
    """Controlled idle-run error carrying a stable CLI exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _procedure(protocol: dict, procedure_id: str) -> dict:
    return next(item for item in protocol["procedures"]
                if item["id"] == procedure_id)


def parse_wifi_connection(output: str) -> dict:
    """Return only connection booleans; never return SSID/BSSID text."""
    enabled = baseline_pilot.parse_wifi_status(output)
    connected = bool(re.search(r"(?im)^\s*Wifi is connected(?:\s|$)", output))
    if re.search(r"(?im)^\s*Wifi is not connected(?:\s|$)", output):
        connected = False
    return {"enabled": enabled, "connected": connected if enabled else False}


def parse_telephony_registration(output: str) -> dict:
    """Parse service-state integers without retaining cell/subscriber data."""
    voice = [int(item) for item in re.findall(r"\bmVoiceRegState=([0-9]+)", output)]
    data = [int(item) for item in re.findall(r"\bmDataRegState=([0-9]+)", output)]
    if not voice and not data:
        raise ValueError("telephony registration state is unavailable")
    return {
        "registered": 0 in voice or 0 in data,
        "voice_in_service_observations": sum(item == 0 for item in voice),
        "data_in_service_observations": sum(item == 0 for item in data),
    }


def parse_battery_snapshot(output: str) -> dict:
    parsed = baseline_pilot.parse_battery(output)
    match = re.search(r"(?im)^\s*charge counter:\s*(-?[0-9]+)\s*$", output)
    parsed["charge_counter_uah"] = int(match.group(1)) if match else None
    return parsed


def evaluate_idle_preflight(observed: dict, protocol: dict,
                            ambient_start_c: float,
                            display_50_confirmed: bool,
                            unlocked_confirmed: bool) -> list[str]:
    """Return every mismatch before batterystats may be reset."""
    reasons = []
    controls = protocol["environment_controls"]
    ambient = controls["ambient_temperature"]["full_run_allowed_range"]
    if not ambient["minimum"] <= ambient_start_c <= ambient["maximum"]:
        reasons.append("room temperature is outside the protocol range")
    charging_bound = _procedure(protocol, "thermal")["fixed_parameters"][
        "safety"]["manufacturer_maximum_ambient_charging_degC"]
    if ambient_start_c > charging_bound:
        reasons.append("room temperature exceeds the charging bound")

    display = observed["display"]
    expected_display = controls["display"]
    if not display_50_confirmed:
        reasons.append("50 percent brightness was not operator-confirmed")
    if not unlocked_confirmed or display.get("wakefulness") != "Awake":
        reasons.append("display is not confirmed unlocked and awake")
    if display["adaptive_brightness"] is not expected_display[
            "adaptive_brightness"]:
        reasons.append("adaptive brightness does not match")
    if display["screen_timeout_ms"] != expected_display["screen_timeout_ms"]:
        reasons.append("screen timeout does not match")
    if display["peak_refresh_rate_hz"] != expected_display[
            "peak_refresh_rate_hz"]:
        reasons.append("peak refresh rate does not match")

    network = observed["network"]
    if network["airplane_mode"] is not False:
        reasons.append("airplane mode is not off")
    if not network["wifi_enabled"]:
        reasons.append("Wi-Fi is not enabled")
    if not network["wifi_connected"]:
        reasons.append("Wi-Fi is not connected")
    if not any(item in {"READY", "LOADED"} for item in network["sim_states"]):
        reasons.append("inserted SIM state was not observed")
    if not network["sim_registered"]:
        reasons.append("SIM network registration was not observed")

    battery = observed["battery"]
    bounds = controls["idle_power"]["start_level_percent"]
    if not bounds["minimum"] <= battery["level_percent"] <= bounds["maximum"]:
        reasons.append("battery level is outside the idle-start range")
    if not (battery["ac_powered"] or battery["usb_powered"]):
        reasons.append("phone is not externally powered during idle setup")
    safety = _procedure(protocol, "thermal")["fixed_parameters"]["safety"]
    if battery["temperature_c"] >= safety["abort_battery_degC_at_or_above"]:
        reasons.append("battery temperature reached the protocol stop threshold")
    return reasons


def idle_metrics(start: dict, end: dict, elapsed_seconds: float) -> dict:
    if elapsed_seconds <= 0:
        raise IdleError("idle elapsed time must be positive", 5)
    level_delta = start["level_percent"] - end["level_percent"]
    result = {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "battery_level_start_percent": start["level_percent"],
        "battery_level_end_percent": end["level_percent"],
        "battery_level_delta_percentage_points": level_delta,
        "battery_level_drain_percent_per_hour": round(
            level_delta * 3600.0 / elapsed_seconds, 6),
        "charge_counter_delta_mah": None,
    }
    before = start.get("charge_counter_uah")
    after = end.get("charge_counter_uah")
    if before is not None and after is not None:
        result["charge_counter_delta_mah"] = round((before - after) / 1000.0, 3)
    return result


def evaluate_comparability(report: dict, protocol: dict,
                            ambient_end_c: float, elapsed_seconds: float,
                            end_network: dict,
                            no_known_network_outage: bool) -> list[str]:
    reasons = []
    ambient = protocol["environment_controls"]["ambient_temperature"]
    allowed = ambient["full_run_allowed_range"]
    if not allowed["minimum"] <= ambient_end_c <= allowed["maximum"]:
        reasons.append("ending room temperature is outside the protocol range")
    if abs(ambient_end_c - report["ambient_start_c"]) > ambient[
            "maximum_within_run_span"]:
        reasons.append("room-temperature span exceeds the protocol tolerance")
    duration = report["pilot_duration_seconds"]
    if elapsed_seconds > duration + PILOT_FINISH_TOLERANCE_SECONDS:
        reasons.append("finish capture exceeded the pilot timing tolerance")
    if not end_network["wifi_enabled"] or not end_network["wifi_connected"]:
        reasons.append("Wi-Fi was not connected at finish")
    if not end_network["sim_registered"]:
        reasons.append("SIM was not registered at finish")
    if not no_known_network_outage:
        reasons.append("absence of a known material network outage was not confirmed")
    return reasons


def _owner_controlled_directory(path: Path):
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
            or metadata.st_uid != os.geteuid()):
        raise IdleError("private output must be owner-controlled mode 0750 or stricter", 3)


def _acquire_lock(root: Path, role: str) -> int:
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    fd = os.open(locks / f"{role}.idle-pilot.lock",
                 os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise IdleError("idle target is already locked", 3) from exc
    return fd


def _release_lock(fd: int):
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _atomic_report(path: Path, report: dict, target: str):
    serialized = json.dumps(report, sort_keys=True)
    if target and target in serialized:
        raise IdleError("private target escaped idle report redaction", 5)
    test_runner._atomic_json(path, report)


def _load_report(run_dir: Path, expected_status: str | None = None) -> dict:
    if not run_dir.name.endswith(".partial"):
        raise IdleError("idle run must reference its partial directory")
    _owner_controlled_directory(run_dir)
    value, _ = test_runner._load_unique_json(
        run_dir / "result.json", test_runner.MAX_REPORT_BYTES)
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("operation") != "baseline-idle-pilot"):
        raise IdleError("idle partial report is invalid", 5)
    if expected_status is not None and value.get("status") != expected_status:
        raise IdleError("idle run is not in the required stage", 3)
    return value


def _write_json_evidence(path: Path, value: dict) -> str:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return test_runner._write_evidence(path, text)


def _verify_evidence_refs(report_path: Path, refs: list[str]):
    """Verify private idle evidence, including bounded multi-megabyte stats."""
    root = report_path.parent.resolve()
    for ref in refs:
        if not isinstance(ref, str) or "@sha256:" not in ref:
            raise IdleError("idle report has an invalid evidence reference", 5)
        relative, expected = ref.rsplit("@sha256:", 1)
        relpath = Path(relative)
        if (relpath.is_absolute() or ".." in relpath.parts
                or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise IdleError("idle report has an invalid evidence reference", 5)
        path = (root / relpath).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise IdleError("idle evidence escapes its run directory", 5) from exc
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_BATTERYSTATS_BYTES + 1)
        except OSError as exc:
            raise IdleError("idle evidence is unreadable", 5) from exc
        if len(data) > MAX_BATTERYSTATS_BYTES:
            raise IdleError("idle evidence exceeds its byte limit", 5)
        if hashlib.sha256(data).hexdigest() != expected:
            raise IdleError("idle evidence hash mismatch", 5)


def _run_required(adb: str, target: str, argv: list[str], timeout: int = 20,
                  cap: int = test_runner.MAX_OUTPUT_BYTES) -> dict:
    result = test_runner.run_bounded(
        [adb, "-s", target, "shell"] + argv, timeout, cap)
    if result.get("transport") != "ok":
        combined = result.get("stdout", "") + "\n" + result.get("stderr", "")
        if test_runner.baseline._is_device_gone(combined):
            raise IdleError("idle target disconnected unexpectedly", 3)
        raise IdleError("idle device command failed", 5)
    return result


def _privacy_minimized_network(adb: str, target: str, raw_dir: Path,
                               prefix: str) -> tuple[dict, list[str]]:
    """Parse sensitive service output in memory and persist booleans only."""
    wifi_raw = _run_required(adb, target, ["cmd", "wifi", "status"])["stdout"]
    telephony_raw = _run_required(
        adb, target, ["dumpsys", "telephony.registry"], cap=2 * 1024 * 1024)[
            "stdout"]
    try:
        wifi = parse_wifi_connection(wifi_raw)
        registration = parse_telephony_registration(telephony_raw)
    except ValueError as exc:
        raise IdleError(str(exc), 4) from exc
    minimized = {
        "privacy_minimized": True,
        "ssid_bssid_subscriber_and_cell_identifiers_persisted": False,
        "wifi_enabled": wifi["enabled"],
        "wifi_connected": wifi["connected"],
        "sim_registered": registration["registered"],
        "voice_in_service_observations": registration[
            "voice_in_service_observations"],
        "data_in_service_observations": registration[
            "data_in_service_observations"],
    }
    filename = f"{prefix}-network-minimized.json"
    digest = _write_json_evidence(raw_dir / filename, minimized)
    return minimized, [f"raw/{filename}@sha256:{digest}"]


def _collect_state(adb: str, target: str, run_dir: Path, protocol: dict,
                   prefix: str, include_apps: bool) -> tuple[dict, list[str]]:
    collector = baseline_pilot.Collector(adb, target, run_dir)
    refs = []
    values = {}
    settings = {
        "brightness_mode": ("system", "screen_brightness_mode", int),
        "brightness_raw": ("system", "screen_brightness", int),
        "screen_timeout_ms": ("system", "screen_off_timeout", int),
        "peak_refresh_rate_hz": ("system", "peak_refresh_rate", float),
        "minimum_refresh_rate_hz": ("system", "min_refresh_rate", float),
        "airplane_mode": ("global", "airplane_mode_on", int),
        "bluetooth": ("global", "bluetooth_on", int),
    }
    for label, (scope, key, kind) in settings.items():
        result, command_refs = collector.command(
            f"{prefix}-settings-{label.replace('_', '-')}",
            ["settings", "get", scope, key])
        refs.extend(command_refs)
        try:
            values[label] = baseline_pilot._parse_setting(result["stdout"], kind)
        except ValueError as exc:
            raise IdleError(f"invalid {label} setting", 4) from exc

    battery_result, battery_refs = collector.command(
        f"{prefix}-battery", ["dumpsys", "battery"])
    refs.extend(battery_refs)
    power_result, power_refs = collector.command(
        f"{prefix}-power", [baseline_pilot._power_wakefulness_remote()])
    refs.extend(power_refs)
    sim_result, sim_refs = collector.command(
        f"{prefix}-sim-state", ["getprop", "gsm.sim.state"])
    refs.extend(sim_refs)
    try:
        battery = parse_battery_snapshot(battery_result["stdout"])
        wakefulness = baseline_pilot.parse_power_wakefulness(
            power_result["stdout"])
    except ValueError as exc:
        raise IdleError(str(exc), 4) from exc
    sim_states = [item.strip().upper() for item in
                  sim_result["stdout"].strip().split(",") if item.strip()]
    private_network, private_refs = _privacy_minimized_network(
        adb, target, collector.raw_dir, prefix)
    refs.extend(private_refs)

    apps = []
    if include_apps:
        for app in _procedure(protocol, "app-launch")["fixed_parameters"]["apps"]:
            result, app_refs = collector.command(
                f"{prefix}-package-{app['role']}",
                ["dumpsys", "package", app["package"]])
            refs.extend(app_refs)
            try:
                version = baseline_pilot.parse_package_version(result["stdout"])
            except ValueError as exc:
                raise IdleError(f"{app['role']} package version unavailable", 4) from exc
            apps.append({"role": app["role"], "package": app["package"], **version})

    return {
        "display": {
            "adaptive_brightness": values["brightness_mode"] == 1,
            "brightness_raw": values["brightness_raw"],
            "screen_timeout_ms": values["screen_timeout_ms"],
            "peak_refresh_rate_hz": values["peak_refresh_rate_hz"],
            "minimum_refresh_rate_hz": values["minimum_refresh_rate_hz"],
            "wakefulness": wakefulness,
        },
        "network": {
            "profile": protocol["environment_controls"]["idle_network"]["profile"],
            "airplane_mode": values["airplane_mode"] == 1,
            "bluetooth": values["bluetooth"] == 1,
            "sim_states": sim_states,
            "wifi_enabled": private_network["wifi_enabled"],
            "wifi_connected": private_network["wifi_connected"],
            "sim_registered": private_network["sim_registered"],
            "sensitive_network_identifiers_persisted": False,
        },
        "battery": battery,
        "apps": apps,
    }, refs


def _write_command_evidence(run_dir: Path, name: str, result: dict) -> list[str]:
    refs = []
    for stream in ("stdout", "stderr"):
        filename = f"{name}.{stream}.txt"
        digest = test_runner._write_evidence(
            run_dir / "raw" / filename, result.get(stream, ""))
        refs.append(f"raw/{filename}@sha256:{digest}")
    return refs


def _host_clock_sample() -> dict:
    """Use Linux boot-relative time so wall-clock corrections cannot shorten a run."""
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip()
    except (OSError, ValueError, IndexError) as exc:
        raise IdleError("Linux boot-relative clock is unavailable", 5) from exc
    if uptime < 0 or not boot_id:
        raise IdleError("Linux boot-relative clock is invalid", 5)
    return {
        "host_boottime_seconds": uptime,
        "host_boot_id_sha256": hashlib.sha256(boot_id.encode("ascii")).hexdigest(),
        "utc": _utc_now(),
    }


def start(args, repo_root: Path) -> tuple[int, Path]:
    if not args.run_id or not test_runner.RUN_ID_RE.fullmatch(args.run_id):
        raise IdleError("idle start requires a valid --run-id")
    if not all((args.target, args.device_role, args.device_map, args.output,
                args.expected_build, args.conditions)):
        raise IdleError("idle start is missing required target or run metadata")
    if args.target in args.run_id or args.target in args.conditions:
        raise IdleError("idle metadata must not contain the private target")
    if args.ambient_start_c is None or not (-50 <= args.ambient_start_c <= 100):
        raise IdleError("idle start requires a plausible ambient temperature")
    if not (args.operator_confirmed_display_50
            and args.operator_confirmed_unlocked
            and args.operator_authorized_batterystats_reset):
        raise IdleError(
            "idle start requires display, unlocked, and batterystats-reset authorization", 3)

    protocol, protocol_hash = baseline_protocol.load_protocol(args.config)
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    if args.target not in test_runner._authorized_devices(args.adb):
        raise IdleError("selected idle target is not an authorized USB device", 3)
    root = Path(args.output).resolve()
    root.mkdir(parents=True, mode=0o750, exist_ok=True)
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    partial = root / f"{args.run_id}.partial"
    final = root / args.run_id
    rejected = root / f"{args.run_id}.preflight-rejected"
    try:
        if partial.exists() or final.exists() or rejected.exists():
            raise IdleError("immutable idle output collision", 3)
        partial.mkdir(mode=0o750)
        (partial / "raw").mkdir(mode=0o750)
        identity, identity_refs = test_runner._capture_identity(
            args.adb, args.target, partial, 20)
        observed, condition_refs = _collect_state(
            args.adb, args.target, partial, protocol, "start", True)
        mismatches = evaluate_idle_preflight(
            observed, protocol, args.ambient_start_c,
            args.operator_confirmed_display_50,
            args.operator_confirmed_unlocked)
        if identity["build_id"] != args.expected_build:
            mismatches.append("target build does not match the expected stock build")
        report = {
            "schema_version": SCHEMA_VERSION,
            "operation": "baseline-idle-pilot",
            "label": PILOT_LABEL,
            "run_id": args.run_id,
            "protocol": {"id": protocol["protocol_id"], "sha256": protocol_hash},
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
            "conditions": args.conditions.strip(),
            "ambient_start_c": args.ambient_start_c,
            "ambient_end_c": None,
            "ambient_span_c": None,
            "pilot_duration_seconds": protocol["pilot"]["idle_duration_seconds"],
            "finish_tolerance_seconds": PILOT_FINISH_TOLERANCE_SECONDS,
            "started_at_utc": _utc_now(),
            "finished_at_utc": None,
            "status": "PREFLIGHT" if not mismatches else "PREFLIGHT_REJECTED",
            "preflight_mismatches": mismatches,
            "start_state": observed,
            "end_state": None,
            "metrics": None,
            "reset": None,
            "screen_off": None,
            "disconnect": None,
            "operator_attestations": {
                "display_50_percent": args.operator_confirmed_display_50,
                "unlocked_and_awake_at_start": args.operator_confirmed_unlocked,
                "batterystats_reset_authorized":
                    args.operator_authorized_batterystats_reset,
                "physical_usb_disconnect": None,
                "no_interaction_during_interval": None,
                "no_known_material_network_outage": None,
            },
            "identity_evidence_refs": identity_refs,
            "condition_evidence_refs": condition_refs,
            "reset_evidence_refs": [],
            "finish_evidence_refs": [],
            "comparability_exclusions": [],
            "limitations": [
                "Pilot only; this is not declared baseline evidence.",
                "Ambient temperature is sampled manually only at start and finish.",
                "Network service output is privacy-minimized before persistence; SSID, BSSID, subscriber and cell identifiers are not retained.",
                "Physical VBUS removal depends on operator attestation in addition to observed ADB loss.",
                "Reconnect precedes the ending ADB capture and can begin charging; reconnect time is recorded before capture.",
            ],
            "errors": [],
        }
        _atomic_report(partial / "result.json", report, args.target)
        if mismatches:
            report["finished_at_utc"] = _utc_now()
            _atomic_report(partial / "result.json", report, args.target)
            os.rename(partial, rejected)
            test_runner._sync_directory(root)
            return 4, rejected / "result.json"

        reset_result = _run_required(
            args.adb, args.target, ["dumpsys", "batterystats", "--reset"])
        reset_refs = _write_command_evidence(
            partial, "batterystats-reset", reset_result)
        if not re.search(r"(?i)battery stats.*reset", reset_result["stdout"]):
            raise IdleError("batterystats reset did not report confirmation", 5)
        reset_check = _run_required(
            args.adb, args.target, ["dumpsys", "batterystats", "--checkin"],
            timeout=30, cap=MAX_BATTERYSTATS_BYTES)
        reset_refs.extend(_write_command_evidence(
            partial, "batterystats-after-reset", reset_check))
        sleep_result = _run_required(
            args.adb, args.target, ["input", "keyevent", "KEYCODE_SLEEP"])
        reset_refs.extend(_write_command_evidence(
            partial, "screen-sleep", sleep_result))
        power_result = _run_required(
            args.adb, args.target, [baseline_pilot._power_wakefulness_remote()])
        reset_refs.extend(_write_command_evidence(
            partial, "screen-sleep-verification", power_result))
        try:
            wakefulness = baseline_pilot.parse_power_wakefulness(
                power_result["stdout"])
        except ValueError as exc:
            raise IdleError(str(exc), 5) from exc
        if wakefulness != "Asleep":
            raise IdleError("display did not enter the required asleep state", 4)
        report["reset"] = {
            "authorized": True,
            "completed_at_utc": _utc_now(),
            "confirmation_observed": True,
        }
        report["screen_off"] = {
            "method": "ADB KEYCODE_SLEEP after the authorized reset",
            "verified_wakefulness": wakefulness,
            "verified_at_utc": _utc_now(),
        }
        report["reset_evidence_refs"] = reset_refs
        report["status"] = STATUS_ARMED
        _atomic_report(partial / "result.json", report, args.target)
        return 0, partial / "result.json"
    except Exception as exc:
        if partial.exists() and (partial / "result.json").exists():
            try:
                report = _load_report(partial)
                report["status"] = "HARNESS_ERROR"
                report["finished_at_utc"] = _utc_now()
                report["errors"].append(str(exc)[:500])
                _atomic_report(partial / "result.json", report, args.target)
            except Exception:
                pass
        raise
    finally:
        _release_lock(lock_fd)


def observe_disconnect(args) -> Path:
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir, STATUS_ARMED)
        if report["target"]["role"] != args.device_role:
            raise IdleError("idle role does not match the partial report", 3)
        power_result = _run_required(
            args.adb, args.target, [baseline_pilot._power_wakefulness_remote()])
        try:
            wakefulness = baseline_pilot.parse_power_wakefulness(
                power_result["stdout"])
        except ValueError as exc:
            raise IdleError(str(exc), 5) from exc
        if wakefulness != "Asleep":
            raise IdleError("display is not asleep immediately before disconnect", 4)
        refs = _write_command_evidence(
            run_dir, "pre-disconnect-screen-verification", power_result)
        wait_started = _utc_now()
        print("READY_TO_DISCONNECT: physically unplug the phone USB-C cable now",
              flush=True)
        absent = 0
        first_absent = None
        deadline = time.monotonic() + args.timeout_seconds
        while time.monotonic() < deadline:
            devices = test_runner._authorized_devices(args.adb)
            if args.target not in devices:
                if first_absent is None:
                    first_absent = _host_clock_sample()
                absent += 1
                if absent >= DISCONNECT_CONFIRMATION_SAMPLES:
                    break
            else:
                absent = 0
                first_absent = None
            time.sleep(1)
        if absent < DISCONNECT_CONFIRMATION_SAMPLES or first_absent is None:
            raise IdleError("ADB disconnect was not observed before the timeout", 3)
        report["disconnect"] = {
            "wait_started_at_utc": wait_started,
            "first_absent_at_utc": first_absent["utc"],
            "confirmed_absent_samples": absent,
            "host_boottime_seconds": first_absent["host_boottime_seconds"],
            "host_boot_id_sha256": first_absent["host_boot_id_sha256"],
            "finish_not_before_boottime_seconds": round(
                first_absent["host_boottime_seconds"]
                + report["pilot_duration_seconds"], 3),
            "reconnected_at_utc": None,
            "observed_transport_loss": True,
            "physical_disconnect_requires_finish_attestation": True,
            "raw_evidence_refs": refs,
        }
        report["status"] = STATUS_DISCONNECTED
        _atomic_report(run_dir / "result.json", report, args.target)
        return run_dir / "result.json"
    finally:
        _release_lock(lock_fd)


def status(args) -> dict:
    run_dir = Path(args.run_dir).resolve()
    report = _load_report(run_dir)
    result = {"run_id": report["run_id"], "status": report["status"]}
    if report["status"] == STATUS_DISCONNECTED:
        now = _host_clock_sample()
        disconnect = report["disconnect"]
        if now["host_boot_id_sha256"] != disconnect["host_boot_id_sha256"]:
            result["host_rebooted"] = True
            result["ready_to_reconnect"] = False
        else:
            remaining = max(0.0, disconnect[
                "finish_not_before_boottime_seconds"] - now[
                    "host_boottime_seconds"])
            result["host_rebooted"] = False
            result["remaining_seconds"] = round(remaining, 1)
            result["ready_to_reconnect"] = remaining <= 0
    return result


def finish(args) -> tuple[int, Path]:
    if args.ambient_end_c is None or not (-50 <= args.ambient_end_c <= 100):
        raise IdleError("idle finish requires a plausible ambient temperature")
    if not (args.operator_confirmed_physical_disconnect
            and args.operator_confirmed_no_interaction
            and args.operator_confirmed_no_known_network_outage):
        raise IdleError(
            "idle finish requires physical-disconnect, no-interaction, and network-outage attestations", 3)
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir, STATUS_DISCONNECTED)
        now = _host_clock_sample()
        disconnect = report["disconnect"]
        if now["host_boot_id_sha256"] != disconnect["host_boot_id_sha256"]:
            raise IdleError("tester rebooted during the idle interval", 4)
        elapsed = now["host_boottime_seconds"] - disconnect[
            "host_boottime_seconds"]
        if elapsed < report["pilot_duration_seconds"]:
            raise IdleError("idle interval has not reached its declared duration", 3)
        if args.target not in test_runner._authorized_devices(args.adb):
            raise IdleError("selected idle target is not reconnected and authorized", 3)
        reconnected_at = now["utc"]
        protocol, digest = baseline_protocol.load_protocol(args.config)
        if digest != report["protocol"]["sha256"]:
            raise IdleError("baseline protocol changed during the idle run", 5)
        end_state, finish_refs = _collect_state(
            args.adb, args.target, run_dir, protocol, "finish", False)
        batterystats = _run_required(
            args.adb, args.target, ["dumpsys", "batterystats"], timeout=30,
            cap=MAX_BATTERYSTATS_BYTES)
        finish_refs.extend(_write_command_evidence(
            run_dir, "finish-batterystats", batterystats))
        exclusions = evaluate_comparability(
            report, protocol, args.ambient_end_c, elapsed,
            end_state["network"],
            args.operator_confirmed_no_known_network_outage)
        report["ambient_end_c"] = args.ambient_end_c
        report["ambient_span_c"] = round(
            abs(args.ambient_end_c - report["ambient_start_c"]), 2)
        report["end_state"] = end_state
        report["metrics"] = idle_metrics(
            report["start_state"]["battery"], end_state["battery"], elapsed)
        report["finish_evidence_refs"] = finish_refs
        report["disconnect"]["reconnected_at_utc"] = reconnected_at
        report["disconnect"]["elapsed_seconds"] = round(elapsed, 3)
        report["operator_attestations"].update({
            "physical_usb_disconnect": True,
            "no_interaction_during_interval": True,
            "no_known_material_network_outage": True,
        })
        report["comparability_exclusions"] = exclusions
        report["finished_at_utc"] = _utc_now()
        report["tool"]["finalizer_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()
        report["status"] = "NON_COMPARABLE" if exclusions else "PASS"
        report_path = run_dir / "result.json"
        for ref in report["identity_evidence_refs"]:
            _verify_evidence_refs(report_path, [ref])
        for group in (report["condition_evidence_refs"],
                      report["reset_evidence_refs"], finish_refs,
                      report["disconnect"]["raw_evidence_refs"]):
            _verify_evidence_refs(report_path, group)
        _atomic_report(report_path, report, args.target)
        suffix = ".non-comparable" if exclusions else ""
        final = run_dir.with_name(run_dir.name.removesuffix(".partial") + suffix)
        if final.exists():
            raise IdleError("immutable idle final output collision", 3)
        os.rename(run_dir, final)
        test_runner._sync_directory(root)
        return (4 if exclusions else 0), final / "result.json"
    finally:
        _release_lock(lock_fd)


def quarantine(args) -> Path:
    if not args.reason or len(args.reason) > 500:
        raise IdleError("idle quarantine requires a bounded reason")
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir)
        report["status"] = args.status
        report["finished_at_utc"] = _utc_now()
        if args.status == "HARNESS_ERROR":
            report["errors"].append(args.reason)
        else:
            report["comparability_exclusions"].append(args.reason)
        _atomic_report(run_dir / "result.json", report, args.target)
        suffix = (".harness-error" if args.status == "HARNESS_ERROR"
                  else ".non-comparable")
        final = run_dir.with_name(run_dir.name.removesuffix(".partial") + suffix)
        if final.exists():
            raise IdleError("immutable idle quarantine output collision", 3)
        os.rename(run_dir, final)
        test_runner._sync_directory(root)
        return final / "result.json"
    finally:
        _release_lock(lock_fd)


def dry_run(config: str) -> dict:
    protocol, digest = baseline_protocol.load_protocol(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "baseline-idle-pilot-dry-run",
        "label": PILOT_LABEL,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest,
        "device_commands_executed": 0,
        "output_directories_created": 0,
        "duration_seconds": protocol["pilot"]["idle_duration_seconds"],
        "finish_tolerance_seconds": PILOT_FINISH_TOLERANCE_SECONDS,
        "stages": ["start", "observe-disconnect", "status", "finish"],
        "device_state_changes": [
            "explicitly authorized dumpsys batterystats --reset",
            "KEYCODE_SLEEP to turn the display off before disconnect",
        ],
        "physical_actions": [
            "physically unplug USB only after start reports ARMED",
            "leave the screen off and do not interact",
            "physically reconnect once only after status reports ready",
        ],
        "privacy": "network output is reduced to booleans before persistence",
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FP6-022 idle pilot registrar")
    parser.add_argument("--config", default=str(_repo_root() / "config" / "baseline.json"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("dry-run")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--target", required=True)
    start_parser.add_argument("--device-role", required=True)
    start_parser.add_argument("--device-map", required=True)
    start_parser.add_argument("--adb", default="adb")
    start_parser.add_argument("--run-id", required=True)
    start_parser.add_argument("--output", required=True)
    start_parser.add_argument("--expected-build", required=True)
    start_parser.add_argument("--conditions", required=True)
    start_parser.add_argument("--ambient-start-c", required=True, type=float)
    start_parser.add_argument("--operator-confirmed-display-50", action="store_true")
    start_parser.add_argument("--operator-confirmed-unlocked", action="store_true")
    start_parser.add_argument(
        "--operator-authorized-batterystats-reset", action="store_true")
    disconnect_parser = sub.add_parser("observe-disconnect")
    disconnect_parser.add_argument("--target", required=True)
    disconnect_parser.add_argument("--device-role", required=True)
    disconnect_parser.add_argument("--device-map", required=True)
    disconnect_parser.add_argument("--adb", default="adb")
    disconnect_parser.add_argument("--run-dir", required=True)
    disconnect_parser.add_argument(
        "--timeout-seconds", type=int, default=DISCONNECT_TIMEOUT_SECONDS,
        choices=range(10, 301), metavar="10..300")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--run-dir", required=True)
    finish_parser = sub.add_parser("finish")
    finish_parser.add_argument("--target", required=True)
    finish_parser.add_argument("--device-role", required=True)
    finish_parser.add_argument("--device-map", required=True)
    finish_parser.add_argument("--adb", default="adb")
    finish_parser.add_argument("--run-dir", required=True)
    finish_parser.add_argument("--ambient-end-c", required=True, type=float)
    finish_parser.add_argument(
        "--operator-confirmed-physical-disconnect", action="store_true")
    finish_parser.add_argument(
        "--operator-confirmed-no-interaction", action="store_true")
    finish_parser.add_argument(
        "--operator-confirmed-no-known-network-outage", action="store_true")
    quarantine_parser = sub.add_parser("quarantine")
    quarantine_parser.add_argument("--target", required=True)
    quarantine_parser.add_argument("--device-role", required=True)
    quarantine_parser.add_argument("--device-map", required=True)
    quarantine_parser.add_argument("--run-dir", required=True)
    quarantine_parser.add_argument("--reason", required=True)
    quarantine_parser.add_argument(
        "--status", choices=("HARNESS_ERROR", "NON_COMPARABLE"),
        default="HARNESS_ERROR")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "dry-run":
            print(json.dumps(dry_run(args.config), indent=2, sort_keys=True))
            return 0
        if args.action == "start":
            code, path = start(args, _repo_root())
            print(f"result={path}")
            return code
        if args.action == "observe-disconnect":
            print(f"result={observe_disconnect(args)}")
            return 0
        if args.action == "status":
            print(json.dumps(status(args), indent=2, sort_keys=True))
            return 0
        if args.action == "finish":
            code, path = finish(args)
            print(f"result={path}")
            return code
        print(f"result={quarantine(args)}")
        return 0
    except (IdleError, baseline_protocol.ProtocolError,
            test_runner.RunnerError) as exc:
        print(str(exc), file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
