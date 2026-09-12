"""Strict FP6 baseline protocol validation and comparison helpers.

The protocol is public configuration.  Raw observations and device identifiers
remain private.  This module deliberately contains no ADB execution: capture
and analysis are separate boundaries, and analysis never rewrites raw samples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


MAX_CONFIG_BYTES = 262_144
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
COMPONENT_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+/"
    r"(?:\.[A-Za-z0-9_.$]+|[A-Za-z][A-Za-z0-9_.$]*(?:\.[A-Za-z0-9_.$]+)*)$"
)


class ProtocolError(ValueError):
    """A controlled configuration or comparison-contract failure."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _expect_keys(value, required, allowed, label):
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    missing = set(required) - set(value)
    extra = set(value) - set(allowed)
    if missing:
        raise ProtocolError(f"{label} is missing required fields")
    if extra:
        raise ProtocolError(f"{label} contains unknown fields")


def _number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label} must be numeric")
    if not math.isfinite(value):
        raise ProtocolError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ProtocolError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise ProtocolError(f"{label} exceeds its maximum")
    return value


def _integer(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{label} must be an integer")
    return _number(value, label, minimum, maximum)


def _text(value, label, maximum=1000):
    if not isinstance(value, str) or not (1 <= len(value) <= maximum):
        raise ProtocolError(f"{label} must be non-empty bounded text")
    if any(marker in value.lower() for marker in ("tbd", "todo", "pending")):
        raise ProtocolError(f"{label} contains an unresolved placeholder")
    return value


def _find_procedure(protocol, procedure_id):
    matches = [item for item in protocol["procedures"]
               if item.get("id") == procedure_id]
    if len(matches) != 1:
        raise ProtocolError(f"protocol must contain exactly one {procedure_id} procedure")
    return matches[0]


def _validate_common_procedure(item, procedure_id):
    allowed = {"id", "owner", "units", "method", "fixed_parameters"}
    if procedure_id == "carrier-ims":
        allowed.add("included_in_fp6_022")
    _expect_keys(item, {"id", "owner", "units", "method", "fixed_parameters"},
                 allowed, procedure_id)
    if item["id"] != procedure_id:
        raise ProtocolError("procedure id mismatch")
    _text(item["owner"], f"{procedure_id}.owner", 64)
    _text(item["units"], f"{procedure_id}.units", 300)
    _text(item["method"], f"{procedure_id}.method", 2000)
    if not isinstance(item["fixed_parameters"], dict):
        raise ProtocolError(f"{procedure_id}.fixed_parameters must be an object")


def validate_protocol(protocol):
    """Validate the exact protocol consumed by stock baseline tooling."""
    top = {"schema_version", "protocol_id", "target", "source_references",
           "environment_controls", "pilot", "procedures"}
    _expect_keys(protocol, top, top, "protocol")
    if protocol["schema_version"] != 2:
        raise ProtocolError("unsupported baseline protocol schema version")
    if not isinstance(protocol["protocol_id"], str) or not TOKEN_RE.fullmatch(
            protocol["protocol_id"]):
        raise ProtocolError("invalid protocol id")

    target = protocol["target"]
    _expect_keys(target, {"device", "build_type", "display"},
                 {"device", "build_type", "display"}, "target")
    if target["device"] != "FP6" or target["build_type"] != "user":
        raise ProtocolError("protocol target must be an FP6 user build")
    display = target["display"]
    display_keys = {"physical_width_px", "physical_height_px",
                    "physical_density_dpi", "supported_refresh_range_hz"}
    _expect_keys(display, display_keys, display_keys, "target.display")
    width = _integer(display["physical_width_px"], "display width", 1, 10000)
    height = _integer(display["physical_height_px"], "display height", 1, 10000)
    _integer(display["physical_density_dpi"], "display density", 1, 2000)
    refresh = display["supported_refresh_range_hz"]
    if not isinstance(refresh, list) or len(refresh) != 2:
        raise ProtocolError("refresh range must have two values")
    minimum_refresh = _number(refresh[0], "minimum refresh", 0.1, 1000)
    maximum_refresh = _number(refresh[1], "maximum refresh", 0.1, 1000)
    if minimum_refresh > maximum_refresh:
        raise ProtocolError("refresh range is reversed")

    refs = protocol["source_references"]
    if not isinstance(refs, list) or not (1 <= len(refs) <= 32):
        raise ProtocolError("source references must be a bounded list")
    seen_refs = set()
    for index, ref in enumerate(refs):
        _expect_keys(ref, {"id", "url", "applies_to"},
                     {"id", "url", "applies_to"}, f"source reference {index + 1}")
        if not isinstance(ref["id"], str) or not TOKEN_RE.fullmatch(ref["id"]):
            raise ProtocolError("source reference has invalid id")
        if ref["id"] in seen_refs:
            raise ProtocolError("duplicate source reference id")
        seen_refs.add(ref["id"])
        if not isinstance(ref["url"], str) or not ref["url"].startswith("https://"):
            raise ProtocolError("source reference must use an HTTPS URL")
        _text(ref["applies_to"], "source reference purpose", 500)

    controls = protocol["environment_controls"]
    control_keys = {"ambient_temperature", "display", "performance_network",
                    "idle_network", "connected_power", "idle_power"}
    _expect_keys(controls, control_keys, control_keys, "environment controls")
    ambient = controls["ambient_temperature"]
    ambient_keys = {"unit", "source", "sample_points", "continuous_logging",
                    "full_run_allowed_range", "maximum_within_run_span",
                    "maximum_between_repeat_starts", "comparison_rule"}
    _expect_keys(ambient, ambient_keys, ambient_keys, "ambient control")
    if ambient["unit"] != "degC" or ambient["sample_points"] != ["start", "end"]:
        raise ProtocolError("ambient control must use start/end degC readings")
    if ambient["continuous_logging"] is not False:
        raise ProtocolError("continuous ambient logging availability is misstated")
    allowed = ambient["full_run_allowed_range"]
    _expect_keys(allowed, {"minimum", "maximum"}, {"minimum", "maximum"},
                 "ambient allowed range")
    minimum_ambient = _number(allowed["minimum"], "minimum ambient", -50, 100)
    maximum_ambient = _number(allowed["maximum"], "maximum ambient", -50, 100)
    if minimum_ambient >= maximum_ambient:
        raise ProtocolError("ambient range is invalid")
    _number(ambient["maximum_within_run_span"], "ambient within-run span", 0, 20)
    _number(ambient["maximum_between_repeat_starts"],
            "ambient repeat-start difference", 0, 20)
    _text(ambient["source"], "ambient source", 300)
    _text(ambient["comparison_rule"], "ambient comparison rule", 1000)

    display_control = controls["display"]
    display_control_keys = {"adaptive_brightness", "brightness_slider_percent",
                            "screen_timeout_ms", "peak_refresh_rate_hz",
                            "record_raw_brightness_value", "comparison_rule"}
    _expect_keys(display_control, display_control_keys, display_control_keys,
                 "display control")
    if display_control["adaptive_brightness"] is not False:
        raise ProtocolError("adaptive brightness must be disabled")
    _integer(display_control["brightness_slider_percent"],
             "brightness slider percent", 1, 100)
    _integer(display_control["screen_timeout_ms"], "screen timeout", 30000, 3600000)
    _number(display_control["peak_refresh_rate_hz"], "peak refresh", 1, 1000)
    if display_control["record_raw_brightness_value"] is not True:
        raise ProtocolError("raw brightness value must be recorded")
    _text(display_control["comparison_rule"], "display comparison rule", 1000)

    for key in ("performance_network", "idle_network"):
        network = controls[key]
        network_keys = {"profile", "airplane_mode", "wifi", "sim",
                        "comparison_rule"}
        if key == "performance_network":
            network_keys.add("bluetooth")
        _expect_keys(network, network_keys, network_keys, key)
        if not isinstance(network["profile"], str) or not TOKEN_RE.fullmatch(
                network["profile"]):
            raise ProtocolError(f"{key} has invalid profile")
        if not isinstance(network["airplane_mode"], bool):
            raise ProtocolError(f"{key} airplane mode must be boolean")
        for name in network_keys - {"airplane_mode"}:
            _text(network[name], f"{key}.{name}", 500)

    for key in ("connected_power", "idle_power"):
        power = controls[key]
        required = {"profile", "start_level_percent", "note"}
        allowed_power = required | ({"required_status"} if key == "connected_power"
                                    else {"usb"})
        _expect_keys(power, required | (allowed_power - required), allowed_power, key)
        if not isinstance(power["profile"], str) or not TOKEN_RE.fullmatch(power["profile"]):
            raise ProtocolError(f"{key} has invalid profile")
        level = power["start_level_percent"]
        _expect_keys(level, {"minimum", "maximum"}, {"minimum", "maximum"},
                     f"{key} start level")
        low = _integer(level["minimum"], f"{key} minimum level", 0, 100)
        high = _integer(level["maximum"], f"{key} maximum level", 0, 100)
        if low > high:
            raise ProtocolError(f"{key} start level range is reversed")
        for name in allowed_power - {"start_level_percent"}:
            _text(power[name], f"{key}.{name}", 1000)

    pilot = protocol["pilot"]
    pilot_keys = {"label", "launch_repetitions_per_state", "frame_repetitions",
                  "frame_duration_seconds", "thermal_load_seconds",
                  "thermal_cooldown_seconds", "thermal_sample_interval_seconds",
                  "idle_duration_seconds", "camera_repetitions"}
    _expect_keys(pilot, pilot_keys, pilot_keys, "pilot")
    if pilot["label"] != "PILOT_ONLY_NOT_BASELINE_EVIDENCE":
        raise ProtocolError("pilot must carry the non-baseline evidence label")
    for key in pilot_keys - {"label"}:
        _integer(pilot[key], f"pilot.{key}", 1, 3600)

    procedures = protocol["procedures"]
    if not isinstance(procedures, list) or len(procedures) != 7:
        raise ProtocolError("protocol must contain seven procedures")
    ids = [item.get("id") if isinstance(item, dict) else None for item in procedures]
    expected_ids = {"app-launch", "frame-time", "idle-drain", "thermal",
                    "memory-pressure", "camera-scene", "carrier-ims"}
    if set(ids) != expected_ids or len(ids) != len(set(ids)):
        raise ProtocolError("procedure inventory is incomplete or duplicated")

    for procedure_id in expected_ids:
        _validate_common_procedure(_find_procedure(protocol, procedure_id), procedure_id)

    launch = _find_procedure(protocol, "app-launch")["fixed_parameters"]
    launch_keys = {"cold_repetitions", "warm_repetitions", "settle_seconds",
                   "cold_expected_launch_state", "warm_expected_launch_state", "apps"}
    _expect_keys(launch, launch_keys, launch_keys,
                 "app-launch parameters")
    if launch["cold_repetitions"] != 3 or launch["warm_repetitions"] != 3:
        raise ProtocolError("full launch repetition counts must remain three")
    _integer(launch["settle_seconds"], "launch settle seconds", 1, 30)
    if launch["cold_expected_launch_state"] != "COLD":
        raise ProtocolError("cold launch state must remain COLD")
    if launch["warm_expected_launch_state"] != "WARM":
        raise ProtocolError("warm launch state must remain WARM")
    apps = launch["apps"]
    if not isinstance(apps, list) or len(apps) < 2:
        raise ProtocolError("at least two fixed launch apps are required")
    seen_roles = set()
    for app in apps:
        _expect_keys(app, {"role", "package", "component"},
                     {"role", "package", "component"}, "launch app")
        if not isinstance(app["role"], str) or not TOKEN_RE.fullmatch(app["role"]):
            raise ProtocolError("launch app has invalid role")
        if app["role"] in seen_roles:
            raise ProtocolError("launch app role is duplicated")
        seen_roles.add(app["role"])
        if not PACKAGE_RE.fullmatch(app["package"]):
            raise ProtocolError("launch app package is invalid")
        if not COMPONENT_RE.fullmatch(app["component"]):
            raise ProtocolError("launch app component is invalid")

    frame = _find_procedure(protocol, "frame-time")["fixed_parameters"]
    frame_keys = {"repetitions", "duration_seconds", "package", "component", "interaction"}
    _expect_keys(frame, frame_keys, frame_keys, "frame parameters")
    if frame["repetitions"] != 3 or frame["duration_seconds"] != 60:
        raise ProtocolError("full frame protocol must remain three 60-second runs")
    if not PACKAGE_RE.fullmatch(frame["package"]):
        raise ProtocolError("frame package is invalid")
    if not COMPONENT_RE.fullmatch(frame["component"]):
        raise ProtocolError("frame component is invalid")
    interaction = frame["interaction"]
    interaction_keys = {"kind", "x_px", "top_y_px", "bottom_y_px",
                        "swipe_duration_ms", "interval_seconds",
                        "minimum_frames_per_swipe"}
    _expect_keys(interaction, interaction_keys, interaction_keys, "frame interaction")
    if interaction["kind"] != "alternating-vertical-swipes":
        raise ProtocolError("frame interaction kind is unsupported")
    x = _integer(interaction["x_px"], "swipe x", 0, width - 1)
    top = _integer(interaction["top_y_px"], "swipe top", 0, height - 1)
    bottom = _integer(interaction["bottom_y_px"], "swipe bottom", 0, height - 1)
    if top >= bottom or x <= 0:
        raise ProtocolError("frame swipe geometry is invalid")
    _integer(interaction["swipe_duration_ms"], "swipe duration", 1, 5000)
    _integer(interaction["interval_seconds"], "swipe interval", 1, 30)
    _integer(interaction["minimum_frames_per_swipe"],
             "minimum frames per swipe", 1, 1000)

    idle = _find_procedure(protocol, "idle-drain")["fixed_parameters"]
    idle_keys = {"duration_hours", "repetitions", "screen", "interaction",
                 "network_profile", "batterystats_reset_requires_explicit_operator_step"}
    _expect_keys(idle, idle_keys, idle_keys, "idle parameters")
    if idle["duration_hours"] != 8 or idle["repetitions"] != 2:
        raise ProtocolError("idle protocol must remain two eight-hour runs")
    if idle["screen"] != "off" or idle["interaction"] != "none":
        raise ProtocolError("idle protocol must keep the screen off without interaction")
    if idle["network_profile"] != controls["idle_network"]["profile"]:
        raise ProtocolError("idle network profile does not match its control")
    if idle["batterystats_reset_requires_explicit_operator_step"] is not True:
        raise ProtocolError("batterystats reset must remain explicit")

    thermal = _find_procedure(protocol, "thermal")["fixed_parameters"]
    thermal_keys = {"workload", "observation_sources", "sysfs_maximum_zones",
                    "safety_sensor_source", "workers", "block_size_bytes",
                    "sustained_load_minutes", "cooldown_minutes",
                    "sample_interval_seconds", "safety"}
    _expect_keys(thermal, thermal_keys, thermal_keys, "thermal parameters")
    if thermal["workload"] != "toybox-dd-zero-to-null":
        raise ProtocolError("thermal workload is not allowlisted")
    sources = thermal["observation_sources"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise ProtocolError("thermal observation sources are incomplete")
    for source in sources:
        _text(source, "thermal observation source", 500)
    _integer(thermal["sysfs_maximum_zones"], "thermal sysfs maximum zones", 1, 256)
    _text(thermal["safety_sensor_source"], "thermal safety sensor source", 500)
    _integer(thermal["workers"], "thermal workers", 1, 8)
    _integer(thermal["block_size_bytes"], "thermal block size", 4096, 16 * 1024 * 1024)
    if thermal["sustained_load_minutes"] != 15 or thermal["cooldown_minutes"] != 10:
        raise ProtocolError("full thermal duration must remain 15 plus 10 minutes")
    _integer(thermal["sample_interval_seconds"], "thermal sample interval", 1, 60)
    safety = thermal["safety"]
    safety_keys = {"manufacturer_internal_operating_minimum_degC",
                   "manufacturer_internal_operating_maximum_degC",
                   "manufacturer_maximum_ambient_charging_degC",
                   "abort_android_thermal_status_at_or_above",
                   "abort_battery_degC_at_or_above", "abort_skin_degC_at_or_above"}
    _expect_keys(safety, safety_keys, safety_keys, "thermal safety")
    official_min = _number(safety["manufacturer_internal_operating_minimum_degC"],
                           "manufacturer minimum", -100, 100)
    official_max = _number(safety["manufacturer_internal_operating_maximum_degC"],
                           "manufacturer maximum", -100, 200)
    if official_min != -10.0 or official_max != 55.0:
        raise ProtocolError("manufacturer operating range does not match the bound source")
    if safety["manufacturer_maximum_ambient_charging_degC"] != 40.0:
        raise ProtocolError("manufacturer charging ambient limit does not match the bound source")
    _integer(safety["abort_android_thermal_status_at_or_above"],
             "thermal status abort", 1, 6)
    for key in ("abort_battery_degC_at_or_above", "abort_skin_degC_at_or_above"):
        value = _number(safety[key], key, official_min, official_max)
        if value >= official_max:
            raise ProtocolError("temperature abort must leave margin below the manufacturer maximum")

    memory = _find_procedure(protocol, "memory-pressure")["fixed_parameters"]
    memory_keys = {"summary_source", "kernel_counter_source", "lmkd_event_source",
                   "activity_kill_event_source", "psi_source", "unavailable_rule",
                   "sample_points", "interpretation"}
    _expect_keys(memory, memory_keys, memory_keys, "memory parameters")
    if memory["summary_source"] != "dumpsys meminfo":
        raise ProtocolError("memory summary source changed")
    if memory["kernel_counter_source"] != "/proc/vmstat":
        raise ProtocolError("memory kernel-counter source changed")
    if memory["psi_source"] != "/proc/pressure/memory":
        raise ProtocolError("memory PSI source changed")
    if memory["sample_points"] != ["before", "after"]:
        raise ProtocolError("memory sample points changed")
    for key in ("lmkd_event_source", "activity_kill_event_source",
                "unavailable_rule", "interpretation"):
        _text(memory[key], f"memory.{key}", 1000)

    camera = _find_procedure(protocol, "camera-scene")["fixed_parameters"]
    camera_keys = {"repetitions", "subject", "focus", "lighting_control", "scenes",
                   "standard_photo_matrix", "advertised_mode_survey"}
    _expect_keys(camera, camera_keys, camera_keys, "camera parameters")
    if camera["repetitions"] != 2:
        raise ProtocolError("camera protocol requires two repetitions")
    for key in ("subject", "focus", "lighting_control", "advertised_mode_survey"):
        _text(camera[key], f"camera.{key}", 1000)
    scenes = camera["scenes"]
    if not isinstance(scenes, list) or len(scenes) != 3:
        raise ProtocolError("camera protocol requires exactly three fixed scenes")
    scene_ids = set()
    for scene in scenes:
        _expect_keys(scene, {"id", "color", "brightness"},
                     {"id", "color", "brightness"}, "camera scene")
        if not isinstance(scene["id"], str) or not TOKEN_RE.fullmatch(scene["id"]):
            raise ProtocolError("camera scene has invalid id")
        if scene["id"] in scene_ids:
            raise ProtocolError("camera scene id is duplicated")
        scene_ids.add(scene["id"])
        _text(scene["color"], "camera scene color", 200)
        _text(scene["brightness"], "camera scene brightness", 200)
    matrix = camera["standard_photo_matrix"]
    if not isinstance(matrix, list) or len(matrix) != 3:
        raise ProtocolError("camera matrix requires main, ultrawide and front")
    cameras = set()
    for capture in matrix:
        _expect_keys(capture, {"camera", "mode", "zoom"},
                     {"camera", "mode", "zoom"}, "camera capture")
        for value in capture.values():
            _text(value, "camera capture value", 100)
        cameras.add(capture["camera"])
    if cameras != {"rear-main", "rear-ultrawide", "front"}:
        raise ProtocolError("camera matrix does not cover the three physical camera roles")

    carrier = _find_procedure(protocol, "carrier-ims")
    if carrier.get("included_in_fp6_022") is not False:
        raise ProtocolError("carrier/IMS measurement belongs to its dedicated task")
    return protocol


def load_protocol(path):
    """Load unique-key UTF-8 JSON with a bounded read and return data/hash."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ProtocolError("protocol is unreadable") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ProtocolError("protocol exceeds its byte limit")
    try:
        protocol = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("protocol is not valid unique-key UTF-8 JSON") from exc
    return validate_protocol(protocol), hashlib.sha256(raw).hexdigest()


def summarize_numeric_samples(samples, unit):
    """Summarize successful samples without converting failures into zero."""
    _text(unit, "summary unit", 100)
    if not isinstance(samples, list) or not samples:
        raise ProtocolError("samples must be a non-empty list")
    success = []
    failures = []
    run_ids = []
    for index, sample in enumerate(samples):
        _expect_keys(sample, {"run_id", "status"},
                     {"run_id", "status", "value", "raw_evidence_refs"},
                     f"sample {index + 1}")
        run_id = sample["run_id"]
        if not isinstance(run_id, str) or not (1 <= len(run_id) <= 96):
            raise ProtocolError("sample has invalid run id")
        if run_id in run_ids:
            raise ProtocolError("sample run id is duplicated")
        run_ids.append(run_id)
        status = sample["status"]
        if status == "PASS":
            if "value" not in sample:
                raise ProtocolError("passing sample has no value")
            success.append(float(_number(sample["value"], "sample value")))
        else:
            if status not in {"FAIL", "SKIP", "NOT_RUN", "INCOMPLETE", "HARNESS_ERROR"}:
                raise ProtocolError("sample has invalid status")
            if "value" in sample:
                raise ProtocolError("non-passing sample must not carry a numeric value")
            failures.append({"run_id": run_id, "status": status})
    summary = {
        "status": "COMPLETE" if not failures else "INCOMPLETE",
        "unit": unit,
        "source_run_ids": run_ids,
        "successful_sample_count": len(success),
        "non_passing_samples": failures,
        "statistics": None,
    }
    if success:
        summary["statistics"] = {
            "minimum": min(success),
            "maximum": max(success),
            "mean": statistics.fmean(success),
            "median": statistics.median(success),
        }
    return summary


def assess_comparability(first, second, protocol):
    """Return an explicit repeat-run comparability decision and reasons."""
    validate_protocol(protocol)
    reasons = []
    for name, record in (("first", first), ("second", second)):
        if not isinstance(record, dict):
            raise ProtocolError(f"{name} conditions must be an object")
        _expect_keys(record, {"run_id", "build", "ambient", "display", "network", "power"},
                     {"run_id", "build", "ambient", "display", "network", "power", "camera"},
                     f"{name} conditions")
    if first["build"] != second["build"]:
        reasons.append("stock build differs")

    ambient_control = protocol["environment_controls"]["ambient_temperature"]
    allowed = ambient_control["full_run_allowed_range"]
    for record in (first, second):
        ambient = record["ambient"]
        _expect_keys(ambient, {"start_c", "end_c"}, {"start_c", "end_c"}, "ambient")
        start = _number(ambient["start_c"], "ambient start")
        end = _number(ambient["end_c"], "ambient end")
        if not (allowed["minimum"] <= start <= allowed["maximum"]
                and allowed["minimum"] <= end <= allowed["maximum"]):
            reasons.append(f"{record['run_id']} ambient outside protocol range")
        if abs(end - start) > ambient_control["maximum_within_run_span"]:
            reasons.append(f"{record['run_id']} ambient span exceeds tolerance")
    if abs(first["ambient"]["start_c"] - second["ambient"]["start_c"]) > (
            ambient_control["maximum_between_repeat_starts"]):
        reasons.append("repeat start temperatures differ beyond tolerance")

    for key in ("display", "network", "power"):
        if first[key] != second[key]:
            reasons.append(f"{key} conditions differ")
    if ("camera" in first) != ("camera" in second):
        reasons.append("camera conditions are missing from one run")
    elif first.get("camera") != second.get("camera"):
        reasons.append("camera conditions differ")
    return {
        "status": "COMPARABLE" if not reasons else "NON_COMPARABLE",
        "first_run_id": first["run_id"],
        "second_run_id": second["run_id"],
        "reasons": reasons,
    }


def _default_config_path():
    return Path(__file__).resolve().parents[2] / "config" / "baseline.json"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate the consumed FP6 stock-baseline protocol")
    parser.add_argument("--config", default=str(_default_config_path()))
    args = parser.parse_args(argv)
    try:
        protocol, digest = load_protocol(args.config)
    except ProtocolError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps({
        "schema_version": protocol["schema_version"],
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest,
        "procedure_ids": [item["id"] for item in protocol["procedures"]],
        "status": "VALID",
        "device_commands_executed": 0,
        "writes": "none",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
