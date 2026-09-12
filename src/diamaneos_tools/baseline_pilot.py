"""Target-bound FP6 stock-baseline connected pilot.

This command exercises the connected portions of the FP6-022 protocol before
the declared Android 16 baseline series.  It writes immutable private raw
evidence, omits the ADB serial from its report, and labels every result as pilot
data.  Camera captures and the physically disconnected idle trial remain
separate operator phases and are never implied by this command.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import time

from diamaneos_tools import baseline_protocol
from diamaneos_tools import test_runner


SCHEMA_VERSION = 1
PILOT_LABEL = "PILOT_ONLY_NOT_BASELINE_EVIDENCE"
DEFAULT_TIMEOUT_SECONDS = 20
MAX_TEXT_BYTES = 262_144
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_HARNESS = "HARNESS_ERROR"

THERMAL_RE = re.compile(
    r"Temperature\{mValue=([-+]?[0-9]+(?:\.[0-9]+)?),"
    r"\s*mType=([0-9]+),\s*mName=([^,}]+),\s*mStatus=([0-9]+)\}"
)


class PilotError(Exception):
    """Controlled pilot failure with a stable exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


class CaseFailure(Exception):
    """A measured case failed without losing the report."""

    def __init__(self, status: str, reason: str,
                 raw_evidence_refs: list[str] | None = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.raw_evidence_refs = list(raw_evidence_refs or [])


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _procedure(protocol: dict, procedure_id: str) -> dict:
    return next(item for item in protocol["procedures"]
                if item["id"] == procedure_id)


def parse_am_start(output: str) -> dict:
    """Parse the stable key/value subset emitted by ``am start -W``."""
    fields = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"Status", "LaunchState", "Activity", "TotalTime", "WaitTime"}:
            fields[key] = value.strip()
    if fields.get("Status") != "ok":
        return {"status": STATUS_FAIL, "reason": "am start did not report Status: ok"}
    timing_key = "TotalTime" if "TotalTime" in fields else "WaitTime"
    try:
        timing_ms = int(fields[timing_key])
    except (KeyError, ValueError):
        return {"status": STATUS_FAIL, "reason": "am start omitted a numeric launch time"}
    if timing_ms < 0:
        return {"status": STATUS_FAIL, "reason": "am start reported a negative launch time"}
    return {
        "status": STATUS_PASS,
        "timing_ms": timing_ms,
        "timing_field": timing_key,
        "launch_state": fields.get("LaunchState", "not-reported"),
        "activity": fields.get("Activity", "not-reported"),
    }


def parse_gfxinfo(output: str) -> dict:
    """Parse package-scoped aggregate rendering counters without invention."""
    patterns = {
        "total_frames": r"Total frames rendered:\s*([0-9]+)",
        "janky_frames": r"Janky frames:\s*([0-9]+)\s*\(([0-9.]+)%\)",
        "p50_ms": r"50th percentile:\s*([0-9]+)ms",
        "p90_ms": r"90th percentile:\s*([0-9]+)ms",
        "p95_ms": r"95th percentile:\s*([0-9]+)ms",
        "p99_ms": r"99th percentile:\s*([0-9]+)ms",
    }
    matches = {name: re.search(pattern, output) for name, pattern in patterns.items()}
    if any(match is None for match in matches.values()):
        return {"status": STATUS_FAIL, "reason": "gfxinfo aggregate is incomplete"}
    total = int(matches["total_frames"].group(1))
    if total < 1:
        return {"status": STATUS_FAIL, "reason": "gfxinfo recorded no rendered frames"}
    return {
        "status": STATUS_PASS,
        "total_frames": total,
        "janky_frames": int(matches["janky_frames"].group(1)),
        "janky_percent": float(matches["janky_frames"].group(2)),
        "percentiles_ms": {
            key.removeprefix("p").removesuffix("_ms"): int(match.group(1))
            for key, match in matches.items() if key.startswith("p")
        },
    }


def parse_battery(output: str) -> dict:
    fields = {}
    for line in output.splitlines():
        match = re.match(r"\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            fields[match.group(1)] = match.group(2)
    required = {"AC powered", "USB powered", "Wireless powered", "status",
                "level", "scale", "voltage", "temperature"}
    if not required.issubset(fields):
        raise ValueError("battery service output is incomplete")
    try:
        return {
            "ac_powered": fields["AC powered"].lower() == "true",
            "usb_powered": fields["USB powered"].lower() == "true",
            "wireless_powered": fields["Wireless powered"].lower() == "true",
            "status_code": int(fields["status"]),
            "level_percent": int(fields["level"]),
            "scale": int(fields["scale"]),
            "voltage_mv": int(fields["voltage"]),
            "temperature_c": int(fields["temperature"]) / 10.0,
        }
    except ValueError as exc:
        raise ValueError("battery service fields are not numeric") from exc


def parse_wifi_status(output: str) -> bool:
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if first == "Wifi is disabled":
        return False
    if first == "Wifi is enabled":
        return True
    raise ValueError("dedicated Wi-Fi status is unavailable")


def parse_package_version(output: str) -> dict:
    version_name = re.search(r"(?m)^\s*versionName=(\S+)\s*$", output)
    version_code = re.search(r"(?m)^\s*versionCode=([0-9]+)", output)
    if not version_name or not version_code:
        raise ValueError("package version fields are incomplete")
    return {"version_name": version_name.group(1),
            "version_code": int(version_code.group(1))}


def parse_meminfo_summary(output: str) -> dict:
    names = {"total_kib": "Total", "free_kib": "Free",
             "used_kib": "Used", "lost_kib": "Lost"}
    values = {}
    for key, label in names.items():
        match = re.search(rf"(?m)^\s*{label} RAM:\s*([0-9,]+)K\b", output)
        if not match:
            raise ValueError(f"meminfo omitted {label.lower()} RAM")
        values[key] = int(match.group(1).replace(",", ""))
    return values


def parse_vmstat_counters(output: str) -> dict:
    selected = {"oom_kill", "pgmajfault", "compact_fail", "compact_success"}
    counters = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in selected:
            try:
                counters[fields[0]] = int(fields[1])
            except ValueError as exc:
                raise ValueError("vmstat counter is not numeric") from exc
    if set(counters) != selected:
        raise ValueError("vmstat omitted required memory counters")
    return counters


def count_logcat_records(output: str) -> int:
    return sum(1 for line in output.splitlines()
               if line.strip() and not line.startswith("---------"))


def parse_thermalservice(output: str) -> dict:
    """Use current HAL readings, never the stale cached section."""
    status_match = re.search(r"(?m)^Thermal Status:\s*([0-9]+)\s*$", output)
    current_match = re.search(
        r"Current temperatures from HAL:\s*(.*?)(?:\nCurrent cooling devices from HAL:|\Z)",
        output, re.DOTALL)
    if not status_match or not current_match:
        raise ValueError("thermal service output is incomplete")
    temperatures = {}
    sensor_statuses = {}
    for match in THERMAL_RE.finditer(current_match.group(1)):
        value, _sensor_type, name, sensor_status = match.groups()
        temperatures[name] = float(value)
        sensor_statuses[name] = int(sensor_status)
    if "battery" not in temperatures or "skin" not in temperatures:
        raise ValueError("current battery or skin thermal sensor is missing")
    return {
        "android_status": int(status_match.group(1)),
        "temperatures_c": temperatures,
        "sensor_statuses": sensor_statuses,
    }


def parse_thermal_sysfs(output: str, maximum_zones: int) -> list[dict]:
    zones = []
    seen = set()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError("thermal sysfs row is malformed")
        zone, zone_type, raw_value = fields
        if (not re.fullmatch(r"thermal_zone[0-9]+", zone)
                or not (1 <= len(zone_type) <= 128)
                or any(ord(character) < 32 for character in zone_type)
                or zone in seen):
            raise ValueError("thermal sysfs identity is invalid")
        try:
            raw_millidegree_c = int(raw_value)
        except ValueError as exc:
            raise ValueError("thermal sysfs value is not numeric") from exc
        if not -1_000_000 <= raw_millidegree_c <= 1_000_000:
            raise ValueError("thermal sysfs value exceeds its bound")
        zones.append({
            "zone": zone,
            "type": zone_type,
            "raw_millidegree_c": raw_millidegree_c,
        })
        seen.add(zone)
        if len(zones) > maximum_zones:
            raise ValueError("thermal sysfs zone count exceeds its bound")
    if not zones:
        raise ValueError("no readable thermal sysfs zones were captured")
    return zones


def _thermal_sysfs_remote() -> str:
    script = (
        'for zone in /sys/class/thermal/thermal_zone*; do '
        'type=""; temp=""; '
        'IFS= read -r type < "$zone/type" 2>/dev/null || continue; '
        'IFS= read -r temp < "$zone/temp" 2>/dev/null || continue; '
        'printf "%s\\t%s\\t%s\\n" "${zone##*/}" "$type" "$temp"; done'
    )
    return "sh -c " + shlex.quote(script)


def thermal_stop_reason(sample: dict, safety: dict) -> str | None:
    if sample["android_status"] >= safety[
            "abort_android_thermal_status_at_or_above"]:
        return "Android thermal status reached the pilot stop threshold"
    if sample["temperatures_c"]["battery"] >= safety[
            "abort_battery_degC_at_or_above"]:
        return "battery sensor reached the pilot stop threshold"
    if sample["temperatures_c"]["skin"] >= safety[
            "abort_skin_degC_at_or_above"]:
        return "skin sensor reached the pilot stop threshold"
    return None


def _parse_setting(value: str, kind):
    normalized = value.strip()
    if kind is int:
        return int(normalized)
    if kind is float:
        return float(normalized)
    return normalized


def evaluate_preflight(observed: dict, protocol: dict,
                       ambient_start_c: float,
                       display_50_confirmed: bool,
                       unlocked_confirmed: bool) -> list[str]:
    """Return every preflight mismatch; an empty list permits workloads."""
    reasons = []
    controls = protocol["environment_controls"]
    expected_display = controls["display"]
    ambient = controls["ambient_temperature"]["full_run_allowed_range"]
    if not ambient["minimum"] <= ambient_start_c <= ambient["maximum"]:
        reasons.append("room temperature is outside the protocol range")
    charging_bound = _procedure(protocol, "thermal")["fixed_parameters"][
        "safety"]["manufacturer_maximum_ambient_charging_degC"]
    if ambient_start_c > charging_bound:
        reasons.append("room temperature exceeds the connected charging bound")
    if not display_50_confirmed:
        reasons.append("50 percent brightness was not operator-confirmed")
    if not unlocked_confirmed:
        reasons.append("unlocked and awake state was not operator-confirmed")
    if observed["display"]["adaptive_brightness"] is not False:
        reasons.append("adaptive brightness is enabled")
    if observed["display"]["screen_timeout_ms"] != expected_display[
            "screen_timeout_ms"]:
        reasons.append("screen timeout does not match the protocol")
    if observed["display"]["peak_refresh_rate_hz"] != expected_display[
            "peak_refresh_rate_hz"]:
        reasons.append("peak refresh rate does not match the protocol")
    if observed["display"]["physical_width_px"] != protocol["target"]["display"][
            "physical_width_px"] or observed["display"]["physical_height_px"] != (
                protocol["target"]["display"]["physical_height_px"]):
        reasons.append("physical display size does not match the protocol")
    if observed["display"]["physical_density_dpi"] != protocol["target"][
            "display"]["physical_density_dpi"]:
        reasons.append("physical display density does not match the protocol")
    network = observed["network"]
    if not network["airplane_mode"]:
        reasons.append("airplane mode is off")
    if network["wifi"]:
        reasons.append("Wi-Fi is on")
    if network["bluetooth"]:
        reasons.append("Bluetooth is on")
    sim_states = network.get("sim_states", [])
    if not any(state in {"READY", "LOADED"} for state in sim_states):
        reasons.append("inserted SIM state was not observed")
    battery = observed["battery"]
    bounds = controls["connected_power"]["start_level_percent"]
    if not bounds["minimum"] <= battery["level_percent"] <= bounds["maximum"]:
        reasons.append("battery level is outside the connected-run range")
    if battery["status_code"] != 5:
        reasons.append("battery status is not FULL")
    if not (battery["ac_powered"] or battery["usb_powered"]):
        reasons.append("ADB cable is not reported as a connected power source")
    return reasons


class Collector:
    def __init__(self, adb: str, target: str, run_dir: Path,
                 executor=test_runner.run_bounded):
        self.adb = adb
        self.target = target
        self.run_dir = run_dir
        self.executor = executor
        self.raw_dir = run_dir / "raw"

    def command(self, name: str, shell_argv: list[str],
                timeout: int = DEFAULT_TIMEOUT_SECONDS,
                required: bool = True) -> tuple[dict, list[str]]:
        result = self.executor(
            [self.adb, "-s", self.target, "shell"] + shell_argv,
            timeout, MAX_TEXT_BYTES)
        refs = []
        for stream in ("stdout", "stderr"):
            filename = f"{name}.{stream}.txt"
            digest = test_runner._write_evidence(
                self.raw_dir / filename, result.get(stream, ""))
            refs.append(f"raw/{filename}@sha256:{digest}")
        if required and result.get("transport") != "ok":
            combined = result.get("stdout", "") + "\n" + result.get("stderr", "")
            status = ("BLOCKED" if test_runner.baseline._is_device_gone(combined)
                      else STATUS_HARNESS)
            raise CaseFailure(status, result.get("reason", "ADB command failed"),
                              refs)
        return result, refs


def _parse_wm_value(output: str, label: str) -> tuple[int, int] | int:
    if label == "size":
        match = re.search(r"Physical size:\s*([0-9]+)x([0-9]+)", output)
        if not match:
            raise ValueError("physical display size is unavailable")
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"Physical density:\s*([0-9]+)", output)
    if not match:
        raise ValueError("physical display density is unavailable")
    return int(match.group(1))


def _collect_preflight(collector: Collector, protocol: dict,
                       ambient_start_c: float, confirmed_display: bool,
                       confirmed_unlocked: bool) -> dict:
    refs = []
    values = {}
    settings = {
        "brightness_mode": ("system", "screen_brightness_mode", int),
        "brightness_raw": ("system", "screen_brightness", int),
        "screen_timeout_ms": ("system", "screen_off_timeout", int),
        "peak_refresh_rate_hz": ("system", "peak_refresh_rate", float),
        "minimum_refresh_rate_hz": ("system", "min_refresh_rate", float),
        "airplane_mode": ("global", "airplane_mode_on", int),
        "wifi_global_raw": ("global", "wifi_on", int),
        "bluetooth": ("global", "bluetooth_on", int),
    }
    for label, (scope, key, kind) in settings.items():
        result, command_refs = collector.command(
            f"preflight-settings-{label.replace('_', '-')}",
            ["settings", "get", scope, key])
        refs.extend(command_refs)
        try:
            values[label] = _parse_setting(result["stdout"], kind)
        except ValueError as exc:
            raise CaseFailure(STATUS_FAIL, f"invalid {label} setting", refs) from exc

    size_result, size_refs = collector.command("preflight-wm-size", ["wm", "size"])
    density_result, density_refs = collector.command(
        "preflight-wm-density", ["wm", "density"])
    refs.extend(size_refs + density_refs)
    try:
        width, height = _parse_wm_value(size_result["stdout"], "size")
        density = _parse_wm_value(density_result["stdout"], "density")
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc

    battery_result, battery_refs = collector.command(
        "preflight-battery", ["dumpsys", "battery"])
    refs.extend(battery_refs)
    try:
        battery = parse_battery(battery_result["stdout"])
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc

    sim_result, sim_refs = collector.command(
        "preflight-sim-state", ["getprop", "gsm.sim.state"])
    refs.extend(sim_refs)
    sim_states = [state.strip().upper() for state in
                  sim_result["stdout"].strip().split(",") if state.strip()]
    wifi_result, wifi_refs = collector.command(
        "preflight-wifi-status", ["cmd", "wifi", "status"])
    refs.extend(wifi_refs)
    try:
        wifi_enabled = parse_wifi_status(wifi_result["stdout"])
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc

    apps = []
    for app in _procedure(protocol, "app-launch")["fixed_parameters"]["apps"]:
        result, app_refs = collector.command(
            f"preflight-package-{app['role']}", ["dumpsys", "package", app["package"]])
        refs.extend(app_refs)
        try:
            version = parse_package_version(result["stdout"])
        except ValueError as exc:
            raise CaseFailure(
                STATUS_FAIL, f"{app['role']} package version unavailable", refs) from exc
        apps.append({"role": app["role"], "package": app["package"], **version})

    observed = {
        "ambient_start_c": ambient_start_c,
        "display": {
            "adaptive_brightness": values["brightness_mode"] == 1,
            "brightness_raw": values["brightness_raw"],
            "brightness_slider_percent_operator_confirmed": 50 if confirmed_display else None,
            "screen_timeout_ms": values["screen_timeout_ms"],
            "peak_refresh_rate_hz": values["peak_refresh_rate_hz"],
            "minimum_refresh_rate_hz": values["minimum_refresh_rate_hz"],
            "physical_width_px": width,
            "physical_height_px": height,
            "physical_density_dpi": density,
        },
        "network": {
            "profile": protocol["environment_controls"]["performance_network"]["profile"],
            "airplane_mode": values["airplane_mode"] == 1,
            "wifi": wifi_enabled,
            "wifi_global_raw": values["wifi_global_raw"],
            "bluetooth": values["bluetooth"] == 1,
            "sim_states": sim_states,
            "subscriber_identifiers_collected": False,
        },
        "battery": battery,
        "apps": apps,
        "operator_confirmed_unlocked_and_awake": confirmed_unlocked,
    }
    mismatches = evaluate_preflight(
        observed, protocol, ambient_start_c, confirmed_display, confirmed_unlocked)
    return {
        "test_id": "connected-preflight",
        "status": STATUS_PASS if not mismatches else STATUS_FAIL,
        "reason": "all connected controls matched" if not mismatches else "; ".join(mismatches),
        "observed": observed,
        "raw_evidence_refs": refs,
    }


def _run_launches(collector: Collector, protocol: dict) -> dict:
    params = _procedure(protocol, "app-launch")["fixed_parameters"]
    samples = []
    refs = []
    for app in params["apps"]:
        package = app["package"]
        component = app["component"]
        for state in ("cold", "warm"):
            _, step_refs = collector.command(
                f"launch-{app['role']}-{state}-home", ["input", "keyevent", "KEYCODE_HOME"])
            refs.extend(step_refs)
            if state == "cold":
                _, step_refs = collector.command(
                    f"launch-{app['role']}-{state}-force-stop", ["am", "force-stop", package])
                refs.extend(step_refs)
            time.sleep(params["settle_seconds"])
            result, step_refs = collector.command(
                f"launch-{app['role']}-{state}-start",
                ["am", "start", "-W", "-n", component])
            refs.extend(step_refs)
            parsed = parse_am_start(result["stdout"])
            sample = {"role": app["role"], "state": state, **parsed}
            samples.append(sample)
            if parsed["status"] != STATUS_PASS:
                return {"test_id": "app-launch", "status": STATUS_FAIL,
                        "reason": f"{app['role']} {state} launch failed",
                        "samples": samples, "raw_evidence_refs": refs}
    return {"test_id": "app-launch", "status": STATUS_PASS,
            "reason": "all one-per-state pilot launches completed",
            "samples": samples, "raw_evidence_refs": refs}


def _run_frame(collector: Collector, protocol: dict) -> dict:
    params = _procedure(protocol, "frame-time")["fixed_parameters"]
    interaction = params["interaction"]
    refs = []
    for name, argv in (
            ("frame-reset", ["dumpsys", "gfxinfo", params["package"], "reset"]),
            ("frame-force-stop", ["am", "force-stop", params["package"]]),
            ("frame-start", ["am", "start", "-W", "-n", params["component"]])):
        _, step_refs = collector.command(name, argv)
        refs.extend(step_refs)
    duration = protocol["pilot"]["frame_duration_seconds"]
    deadline = time.monotonic() + duration
    direction_up = True
    swipe_count = 0
    while time.monotonic() < deadline:
        start_y = interaction["bottom_y_px"] if direction_up else interaction["top_y_px"]
        end_y = interaction["top_y_px"] if direction_up else interaction["bottom_y_px"]
        _, step_refs = collector.command(
            f"frame-swipe-{swipe_count + 1:02d}",
            ["input", "swipe", str(interaction["x_px"]), str(start_y),
             str(interaction["x_px"]), str(end_y),
             str(interaction["swipe_duration_ms"])])
        refs.extend(step_refs)
        swipe_count += 1
        direction_up = not direction_up
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interaction["interval_seconds"], remaining))
    result, step_refs = collector.command(
        "frame-gfxinfo", ["dumpsys", "gfxinfo", params["package"]])
    refs.extend(step_refs)
    parsed = parse_gfxinfo(result["stdout"])
    return {"test_id": "frame-time", **parsed,
            "reason": ("package-scoped frame aggregate parsed" if parsed["status"] == STATUS_PASS
                       else parsed["reason"]),
            "swipe_count": swipe_count, "raw_evidence_refs": refs}


def _thermal_workload_argv(adb: str, target: str, pidfile: str,
                           workers: int, seconds: int, block_size: int) -> list[str]:
    script = (
        'pidfile="$1"; workers="$2"; seconds="$3"; block="$4"; '
        ': > "$pidfile" || exit 70; pids=""; '
        'cleanup(){ for pid in $pids; do kill "$pid" 2>/dev/null || true; done; '
        'rm -f "$pidfile"; }; trap cleanup EXIT HUP INT TERM; i=0; '
        'while [ "$i" -lt "$workers" ]; do '
        'timeout "$seconds" dd if=/dev/zero of=/dev/null bs="$block" & '
        'pid=$!; pids="$pids $pid"; printf "%s\\n" "$pid" >> "$pidfile"; '
        'i=$((i+1)); done; wait'
    )
    # ADB's remote shell joins its post-``shell`` argv.  Send one deliberately
    # quoted command string so the script remains the single ``sh -c``
    # operand.  Every data operand is either internally generated or numeric.
    operands = ["diamaneos-thermal", pidfile, str(workers), str(seconds),
                str(block_size)]
    remote = "sh -c " + shlex.quote(script) + " " + " ".join(
        shlex.quote(value) for value in operands)
    return [adb, "-s", target, "shell", remote]


def _stop_remote_thermal(collector: Collector, pidfile: str) -> list[str]:
    refs = []
    result, step_refs = collector.command(
        "thermal-abort-read-pids", ["cat", pidfile], required=False)
    refs.extend(step_refs)
    pids = [line.strip() for line in result.get("stdout", "").splitlines()]
    if pids and all(re.fullmatch(r"[1-9][0-9]{0,9}", pid) for pid in pids):
        _, step_refs = collector.command(
            "thermal-abort-kill", ["kill", "-TERM"] + pids, required=False)
        refs.extend(step_refs)
    _, step_refs = collector.command(
        "thermal-abort-remove-pidfile", ["rm", "-f", pidfile], required=False)
    refs.extend(step_refs)
    return refs


def _run_thermal(collector: Collector, protocol: dict, run_id: str) -> dict:
    params = _procedure(protocol, "thermal")["fixed_parameters"]
    safety = params["safety"]
    load_seconds = protocol["pilot"]["thermal_load_seconds"]
    cooldown_seconds = protocol["pilot"]["thermal_cooldown_seconds"]
    sample_interval = protocol["pilot"]["thermal_sample_interval_seconds"]
    token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]
    pidfile = f"/data/local/tmp/diamaneos-thermal-{token}.pids"
    refs = []
    samples = []
    stop_reason = None

    _, step_refs = collector.command(
        "thermal-remove-stale-pidfile", ["rm", "-f", pidfile])
    refs.extend(step_refs)
    workload_argv = _thermal_workload_argv(
        collector.adb, collector.target, pidfile, params["workers"],
        load_seconds, params["block_size_bytes"])

    def sample(phase: str, elapsed: int):
        nonlocal stop_reason
        result, sample_refs = collector.command(
            f"thermal-{phase}-{elapsed:04d}s", ["dumpsys", "thermalservice"])
        refs.extend(sample_refs)
        try:
            parsed = parse_thermalservice(result["stdout"])
        except ValueError as exc:
            raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc
        sysfs_result, sysfs_refs = collector.command(
            f"thermal-{phase}-{elapsed:04d}s-sysfs", [_thermal_sysfs_remote()])
        refs.extend(sysfs_refs)
        try:
            sysfs_zones = parse_thermal_sysfs(
                sysfs_result["stdout"], params["sysfs_maximum_zones"])
        except ValueError as exc:
            raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc
        samples.append({"phase": phase, "elapsed_seconds": elapsed, **parsed,
                        "sysfs_zones": sysfs_zones})
        stop_reason = thermal_stop_reason(parsed, safety)

    sample("preload", 0)
    if stop_reason:
        return {"test_id": "thermal", "status": STATUS_FAIL,
                "reason": stop_reason, "samples": samples,
                "raw_evidence_refs": refs}

    workload_elapsed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(test_runner.run_bounded, workload_argv,
                             load_seconds + 10, 65_536)
        started = time.monotonic()
        try:
            while True:
                elapsed = min(load_seconds, round(time.monotonic() - started))
                sample("load", elapsed)
                if stop_reason or elapsed >= load_seconds or future.done():
                    break
                time.sleep(min(sample_interval, max(0, load_seconds - elapsed)))
        except BaseException:
            refs.extend(_stop_remote_thermal(collector, pidfile))
            future.result()
            raise
        workload_elapsed = round(time.monotonic() - started)
        if stop_reason:
            refs.extend(_stop_remote_thermal(collector, pidfile))
        workload = future.result()

    for stream in ("stdout", "stderr"):
        filename = f"thermal-workload.{stream}.txt"
        digest = test_runner._write_evidence(
            collector.raw_dir / filename, workload.get(stream, ""))
        refs.append(f"raw/{filename}@sha256:{digest}")
    if workload.get("transport") not in {"ok", "error"}:
        return {"test_id": "thermal", "status": STATUS_HARNESS,
                "reason": workload.get("reason", "thermal workload transport failed"),
                "samples": samples, "raw_evidence_refs": refs}
    if stop_reason:
        return {"test_id": "thermal", "status": STATUS_FAIL,
                "reason": stop_reason, "samples": samples,
                "raw_evidence_refs": refs}
    if workload_elapsed < load_seconds - 2:
        return {"test_id": "thermal", "status": STATUS_HARNESS,
                "reason": "thermal workload exited before its bounded duration",
                "samples": samples, "raw_evidence_refs": refs}

    cooldown_started = time.monotonic()
    while True:
        elapsed = min(cooldown_seconds, round(time.monotonic() - cooldown_started))
        sample("cooldown", elapsed)
        if stop_reason or elapsed >= cooldown_seconds:
            break
        time.sleep(min(sample_interval, max(0, cooldown_seconds - elapsed)))
    return {"test_id": "thermal",
            "status": STATUS_FAIL if stop_reason else STATUS_PASS,
            "reason": stop_reason or "bounded load and cooldown completed below stop thresholds",
            "samples": samples, "raw_evidence_refs": refs}


def _memory_sample(collector: Collector, label: str) -> tuple[dict, list[str]]:
    refs = []
    meminfo_result, step_refs = collector.command(
        f"memory-{label}-meminfo", ["dumpsys", "meminfo"])
    refs.extend(step_refs)
    vmstat_result, step_refs = collector.command(
        f"memory-{label}-vmstat", ["cat", "/proc/vmstat"])
    refs.extend(step_refs)
    psi_result, step_refs = collector.command(
        f"memory-{label}-psi", ["cat", "/proc/pressure/memory"],
        required=False)
    refs.extend(step_refs)
    try:
        meminfo = parse_meminfo_summary(meminfo_result["stdout"])
        vmstat = parse_vmstat_counters(vmstat_result["stdout"])
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc
    if psi_result.get("transport") == "ok":
        psi_lines = [line for line in psi_result["stdout"].splitlines()
                     if line.startswith("some ") or line.startswith("full ")]
        if len(psi_lines) != 2 or any("total=" not in line for line in psi_lines):
            raise CaseFailure(STATUS_FAIL, "memory PSI output is malformed", refs)
        psi = {"status": STATUS_PASS, "lines": psi_lines}
    else:
        psi = {
            "status": "UNSUPPORTED",
            "reason": "locked stock shell cannot read the PSI source",
        }
    return {"point": label, "meminfo": meminfo, "vmstat": vmstat,
            "psi": psi}, refs


def _memory_start(collector: Collector) -> tuple[dict, list[str]]:
    result, refs = collector.command("memory-run-start-epoch", ["date", "+%s"])
    try:
        epoch = int(result["stdout"].strip())
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, "device run-start epoch is invalid", refs) from exc
    if epoch < 1:
        raise CaseFailure(STATUS_FAIL, "device run-start epoch is invalid", refs)
    sample, sample_refs = _memory_sample(collector, "before")
    return {"start_epoch": epoch, "sample": sample}, refs + sample_refs


def _memory_finish(collector: Collector, start: dict,
                   start_refs: list[str]) -> dict:
    after, refs = _memory_sample(collector, "after")
    timestamp = f"{start['start_epoch']}.000"
    lmkd_result, step_refs = collector.command(
        "memory-lmkd-events",
        ["logcat", "-b", "all", "-d", "-v", "epoch", "-T", timestamp,
         "-s", "lmkd:I", "lowmemorykiller:I", "*:S"])
    refs.extend(step_refs)
    activity_result, step_refs = collector.command(
        "memory-activity-kill-events",
        ["logcat", "-b", "events", "-d", "-v", "epoch", "-T", timestamp,
         "-s", "am_kill:I", "am_low_memory:I", "*:S"])
    refs.extend(step_refs)
    before = start["sample"]
    deltas = {}
    for name, value in after["vmstat"].items():
        delta = value - before["vmstat"][name]
        if delta < 0:
            raise CaseFailure(STATUS_FAIL, "memory counter decreased during the run",
                              start_refs + refs)
        deltas[name] = delta
    return {
        "test_id": "memory-pressure",
        "status": STATUS_PASS,
        "reason": "bounded memory observations captured; unsupported PSI remains explicit",
        "samples": [before, after],
        "kernel_counter_deltas": deltas,
        "bounded_event_records": {
            "lmkd_or_lowmemorykiller_tag_lines": count_logcat_records(
                lmkd_result["stdout"]),
            "activity_manager_kill_or_low_memory_lines": count_logcat_records(
                activity_result["stdout"]),
            "interpretation": "Activity-manager events are not counted as LMKD kills without corroboration.",
        },
        "raw_evidence_refs": start_refs + refs,
    }


def _collect_postflight(collector: Collector) -> dict:
    result, refs = collector.command(
        "postflight-battery", ["dumpsys", "battery"])
    try:
        battery = parse_battery(result["stdout"])
    except ValueError as exc:
        raise CaseFailure(STATUS_FAIL, str(exc), refs) from exc
    return {
        "test_id": "connected-postflight",
        "status": STATUS_PASS,
        "reason": "ending connected battery and charging state recorded",
        "observed": {"battery": battery},
        "raw_evidence_refs": refs,
    }


def _scrub(value, target: str):
    serialized = json.dumps(value, sort_keys=True)
    scrubbed = serialized.replace(target, "<redacted:device-target>")
    return json.loads(scrubbed)


def _git_revision(repo_root: Path) -> str:
    return test_runner._git_revision(repo_root)


def _report_template(args, protocol: dict, protocol_hash: str,
                     repo_root: Path, identity: dict, identity_refs: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "baseline-connected-pilot",
        "label": PILOT_LABEL,
        "run_id": args.run_id,
        "protocol": {"id": protocol["protocol_id"], "sha256": protocol_hash},
        "tool": {
            "revision": _git_revision(repo_root),
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
        "started_at_utc": _utc_now(),
        "finished_at_utc": None,
        "status": "INCOMPLETE",
        "cases": [],
        "run_order": [],
        "identity_evidence_refs": identity_refs,
        "deferred_phases": [
            {"id": "idle-drain", "status": "NOT_RUN",
             "reason": "requires a separately supervised physical USB disconnect"},
            {"id": "camera-scene", "status": "NOT_RUN",
             "reason": "requires the fixed physical scene and original media registration"},
        ],
        "limitations": [
            "Connected pilot only; it is not baseline evidence.",
            "The manual end temperature is recorded after the run outside this immutable connected result.",
            "A visible 50 percent brightness setting is operator-attested because this build exposes no normalized brightness value through settings.",
        ],
        "errors": [],
    }


def _prepare_output(args) -> tuple[Path, Path, int]:
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    metadata = root.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
            or metadata.st_uid != os.geteuid()):
        raise PilotError("private output root must be owner-controlled mode 0750 or stricter", 3)
    partial = root / f"{args.run_id}.partial"
    final = root / args.run_id
    if partial.exists() or final.exists():
        raise PilotError("immutable output collision", 3)
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    lock_fd = os.open(locks / f"{args.device_role}.baseline-pilot.lock",
                      os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        raise PilotError("physical target is already locked", 3) from exc
    partial.mkdir(mode=0o750)
    (partial / "raw").mkdir(mode=0o750)
    return partial, final, lock_fd


def execute_connected(args, repo_root: Path) -> tuple[int, Path]:
    if not args.run_id or not test_runner.RUN_ID_RE.fullmatch(args.run_id):
        raise PilotError("execution requires a valid --run-id")
    if not args.target or not args.device_role or not args.device_map:
        raise PilotError("execution requires --target, --device-role and --device-map")
    if not args.output:
        raise PilotError("execution requires an --output root")
    if not test_runner.TOKEN_RE.fullmatch(args.device_role):
        raise PilotError("invalid device role")
    if not args.conditions or not args.conditions.strip() or len(args.conditions) > 4000:
        raise PilotError("execution requires bounded non-empty --conditions")
    if args.target in args.run_id or args.target in args.conditions:
        raise PilotError("run metadata must not contain the private target")
    if args.ambient_start_c is None or not (-50.0 <= args.ambient_start_c <= 100.0):
        raise PilotError("execution requires a plausible --ambient-start-c")

    try:
        protocol, protocol_hash = baseline_protocol.load_protocol(args.config)
    except baseline_protocol.ProtocolError as exc:
        raise PilotError(f"invalid baseline protocol: {exc}") from exc
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    devices = test_runner._authorized_devices(args.adb)
    if args.target not in devices:
        raise PilotError("selected private target is not an authorized USB device", 3)

    partial, final, lock_fd = _prepare_output(args)
    report = None
    exit_code = 5
    try:
        identity, identity_refs = test_runner._capture_identity(
            args.adb, args.target, partial, DEFAULT_TIMEOUT_SECONDS)
        report = _report_template(
            args, protocol, protocol_hash, repo_root, identity, identity_refs)
        report_path = partial / "result.json"
        report = _scrub(report, args.target)
        test_runner._atomic_json(report_path, report)
        collector = Collector(args.adb, args.target, partial)

        try:
            preflight = _collect_preflight(
                collector, protocol, args.ambient_start_c,
                args.operator_confirmed_display_50,
                args.operator_confirmed_unlocked)
            report["cases"].append(preflight)
            report = _scrub(report, args.target)
            test_runner._atomic_json(report_path, report)
            if preflight["status"] != STATUS_PASS:
                raise CaseFailure(STATUS_FAIL, preflight["reason"])

            memory_start, memory_refs = _memory_start(collector)
            launches = _run_launches(collector, protocol)
            report["cases"].append(launches)
            if launches["status"] != STATUS_PASS:
                raise CaseFailure(STATUS_FAIL, launches["reason"])
            frame = _run_frame(collector, protocol)
            report["cases"].append(frame)
            if frame["status"] != STATUS_PASS:
                raise CaseFailure(STATUS_FAIL, frame["reason"])
            thermal = _run_thermal(collector, protocol, args.run_id)
            report["cases"].append(thermal)
            if thermal["status"] != STATUS_PASS:
                raise CaseFailure(thermal["status"], thermal["reason"])
            report["cases"].append(_memory_finish(
                collector, memory_start, memory_refs))
            report["cases"].append(_collect_postflight(collector))
            report["status"] = STATUS_PASS
            exit_code = 0
        except CaseFailure as exc:
            if not report["cases"] or report["cases"][-1].get("status") != exc.status:
                report["cases"].append({
                    "test_id": "connected-pilot-harness", "status": exc.status,
                    "reason": exc.reason,
                    "raw_evidence_refs": exc.raw_evidence_refs,
                })
            report["status"] = exc.status
            report["errors"].append(exc.reason)
            exit_code = 3 if exc.status == "BLOCKED" else 5 if (
                exc.status == STATUS_HARNESS) else 4
        report["finished_at_utc"] = _utc_now()
        report["run_order"] = [case["test_id"] for case in report["cases"]]
        report = _scrub(report, args.target)
        test_runner._atomic_json(report_path, report)
        test_runner._verify_evidence_refs(
            report_path, report["identity_evidence_refs"])
        for case in report["cases"]:
            test_runner._verify_evidence_refs(
                report_path, case.get("raw_evidence_refs", []))
        if args.target in report_path.read_text(encoding="utf-8"):
            raise PilotError("private target escaped report redaction", 5)
        os.rename(partial, final)
        test_runner._sync_directory(final.parent)
        return exit_code, final / "result.json"
    except Exception:
        if report is not None:
            try:
                report["finished_at_utc"] = _utc_now()
                report["status"] = STATUS_HARNESS
                report["errors"].append("unexpected harness failure; inspect private partial evidence")
                test_runner._atomic_json(partial / "result.json", _scrub(report, args.target))
            except Exception:
                pass
        raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def dry_run_plan(config: str) -> dict:
    protocol, digest = baseline_protocol.load_protocol(config)
    launch = _procedure(protocol, "app-launch")["fixed_parameters"]
    thermal = _procedure(protocol, "thermal")["fixed_parameters"]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "baseline-connected-pilot-dry-run",
        "label": PILOT_LABEL,
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest,
        "device_commands_executed": 0,
        "output_directories_created": 0,
        "connected_cases": ["preflight", "app-launch", "frame-time",
                            "thermal", "memory-pressure", "postflight"],
        "launch_components": [item["component"] for item in launch["apps"]],
        "pilot_bounds": {
            "frame_seconds": protocol["pilot"]["frame_duration_seconds"],
            "thermal_load_seconds": protocol["pilot"]["thermal_load_seconds"],
            "thermal_cooldown_seconds": protocol["pilot"]["thermal_cooldown_seconds"],
            "thermal_workers": thermal["workers"],
        },
        "device_state_changes": [
            "HOME key and deterministic Settings swipes",
            "force-stop exact benchmark packages; app data is not cleared",
            "reset package-scoped Settings gfxinfo counters",
            "bounded CPU load with temporary PID file and mandatory cleanup",
        ],
        "deferred": ["physical-disconnect idle pilot", "fixed-scene camera pilot"],
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Target-bound FP6 baseline connected pilot")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the plan; contact no device and write nothing")
    parser.add_argument("--config", default=str(_repo_root() / "config" / "baseline.json"))
    parser.add_argument("--target", help="private exact ADB serial")
    parser.add_argument("--device-role", help="private device-map role")
    parser.add_argument("--device-map", help="private role-to-target map")
    parser.add_argument("--run-id", help="immutable private run ID")
    parser.add_argument("--output", help="owner-controlled private output root")
    parser.add_argument("--conditions", help="bounded operator conditions record")
    parser.add_argument("--ambient-start-c", type=float,
                        help="manual room thermometer reading at start")
    parser.add_argument("--operator-confirmed-display-50", action="store_true",
                        help="attest the visible brightness slider is exactly 50 percent")
    parser.add_argument("--operator-confirmed-unlocked", action="store_true",
                        help="attest the phone is unlocked and awake")
    parser.add_argument("--adb", default="adb", help="ADB executable")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.dry_run:
            print(json.dumps(dry_run_plan(args.config), indent=2, sort_keys=True))
            return 0
        code, result_path = execute_connected(args, _repo_root())
        print(f"result={result_path}")
        return code
    except (PilotError, baseline_protocol.ProtocolError,
            test_runner.RunnerError) as exc:
        code = getattr(exc, "exit_code", 2)
        print(f"error: {exc}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
