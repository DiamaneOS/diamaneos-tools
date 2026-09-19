"""Private original-media registrar for stock camera measurements.

The command is deliberately staged because changing the physical lamp and
reversing the phone are operator actions.  A run is started once, each shutter
event is registered in the predeclared order, and finalization refuses missing
captures.  Device identifiers remain in the separate private device map.
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
import shlex
import stat
import subprocess
import time

from diamaneos_tools import baseline_protocol
from diamaneos_tools import baseline_pilot
from diamaneos_tools import rig
from diamaneos_tools import test_runner


SCHEMA_VERSION = 1
PILOT_LABEL = "PILOT_ONLY_NOT_BASELINE_EVIDENCE"
DECLARED_LABEL = "DECLARED_STOCK_BASELINE_EVIDENCE"
MAX_MEDIA_BYTES = 100 * 1024 * 1024
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".dng"}
REMOTE_CAMERA_DIR = "/sdcard/DCIM/Camera"


class CameraError(Exception):
    """Controlled camera-run error with a stable CLI exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _procedure(protocol: dict, procedure_id: str) -> dict:
    return next(item for item in protocol["procedures"]
                if item["id"] == procedure_id)


def camera_profile(protocol: dict, declared: bool) -> dict:
    repetitions = (_procedure(protocol, "camera-scene")["fixed_parameters"][
        "repetitions"] if declared else protocol["pilot"]["camera_repetitions"])
    return {
        "mode": "declared" if declared else "pilot",
        "operation": ("baseline-camera-measurement" if declared
                      else "baseline-camera-pilot"),
        "label": DECLARED_LABEL if declared else PILOT_LABEL,
        "standard_matrix_repetitions": repetitions,
    }


def build_capture_plan(protocol: dict, repetitions: int) -> list[dict]:
    """Expand the camera contract in a movement-minimizing fixed order."""
    if isinstance(repetitions, bool) or not isinstance(repetitions, int):
        raise CameraError("camera repetitions must be an integer")
    declared = _procedure(protocol, "camera-scene")["fixed_parameters"]
    if repetitions < 1 or repetitions > declared["repetitions"]:
        raise CameraError("camera repetitions exceed the declared protocol")
    scenes = [item["id"] for item in declared["scenes"]]
    standard = declared["standard_photo_matrix"]
    additional = declared["advertised_mode_survey"]["additional_still_matrix"]
    captures = []

    def append_capture(repetition: int, scene: str, item: dict,
                       orientation: str, required: bool, source: str):
        token = "-".join((f"r{repetition}", scene, item["camera"],
                          item["mode"], item["zoom"])).lower()
        token = re.sub(r"[^a-z0-9.-]+", "-", token).strip("-")
        captures.append({
            "capture_id": token,
            "repetition": repetition,
            "scene": scene,
            "orientation": orientation,
            "camera": item["camera"],
            "mode": item["mode"],
            "zoom": item["zoom"],
            "required": required,
            "source": source,
        })

    # Keep the rear orientation for all rear captures, changing only the lamp.
    for repetition in range(1, repetitions + 1):
        for scene in scenes:
            for item in standard:
                if item["camera"] != "front":
                    append_capture(repetition, scene, item, "rear-facing",
                                   True, "standard-photo-matrix")
            if repetition == 1:
                for item in additional:
                    if item["scene"] == scene and item["camera"] != "front":
                        append_capture(repetition, scene, item, "rear-facing",
                                       False, "advertised-mode-survey")

    # Reverse the phone only once, then repeat the lamp sequence for the front.
    for repetition in range(1, repetitions + 1):
        for scene in scenes:
            for item in standard:
                if item["camera"] == "front":
                    append_capture(repetition, scene, item, "front-facing",
                                   True, "standard-photo-matrix")
            if repetition == 1:
                for item in additional:
                    if item["scene"] == scene and item["camera"] == "front":
                        append_capture(repetition, scene, item, "front-facing",
                                       False, "advertised-mode-survey")
    if len({item["capture_id"] for item in captures}) != len(captures):
        raise CameraError("camera plan produced duplicate capture ids")
    return captures


def evaluate_camera_preflight(observed: dict, protocol: dict,
                              display_50_confirmed: bool,
                              unlocked_confirmed: bool) -> list[str]:
    """Apply fixed camera controls without the workload-only FULL-state rule."""
    reasons = []
    controls = protocol["environment_controls"]
    display = observed["display"]
    network = observed["network"]
    battery = observed["battery"]
    if not display_50_confirmed:
        reasons.append("50 percent brightness was not operator-confirmed")
    if not unlocked_confirmed or display.get("wakefulness") != "Awake":
        reasons.append("display is not confirmed unlocked and awake")
    expected_display = controls["display"]
    if display["adaptive_brightness"] is not expected_display["adaptive_brightness"]:
        reasons.append("adaptive brightness does not match")
    if display["screen_timeout_ms"] != expected_display["screen_timeout_ms"]:
        reasons.append("screen timeout does not match")
    if display["peak_refresh_rate_hz"] != expected_display["peak_refresh_rate_hz"]:
        reasons.append("peak refresh rate does not match")
    target_display = protocol["target"]["display"]
    if (display["physical_width_px"] != target_display["physical_width_px"]
            or display["physical_height_px"] != target_display["physical_height_px"]
            or display["physical_density_dpi"] != target_display["physical_density_dpi"]):
        reasons.append("physical display geometry does not match")
    expected_network = controls["performance_network"]
    if network["airplane_mode"] is not expected_network["airplane_mode"]:
        reasons.append("airplane mode does not match")
    if network["wifi"] is not False:
        reasons.append("Wi-Fi is not off")
    if network["bluetooth"] is not False:
        reasons.append("Bluetooth is not off")
    if not any(state in {"READY", "LOADED"} for state in network["sim_states"]):
        reasons.append("inserted SIM state was not observed")
    level = controls["connected_power"]["start_level_percent"]
    if not level["minimum"] <= battery["level_percent"] <= level["maximum"]:
        reasons.append("battery level is outside the connected camera range")
    if not (battery["ac_powered"] or battery["usb_powered"]):
        reasons.append("ADB cable is not reported as a connected power source")
    thermal_safety = _procedure(protocol, "thermal")["fixed_parameters"]["safety"]
    if battery["temperature_c"] >= thermal_safety["abort_battery_degC_at_or_above"]:
        reasons.append("battery temperature reached the protocol stop threshold")
    if not any(item["role"] == "camera" for item in observed["apps"]):
        reasons.append("stock camera package version was not captured")
    return reasons


def parse_remote_media_listing(output: str) -> set[str]:
    """Accept only stock-camera-style, single-component original filenames."""
    names = set()
    for line in output.splitlines():
        value = line.strip()
        if not value:
            continue
        name = Path(value).name
        if value != f"{REMOTE_CAMERA_DIR}/{name}":
            raise CameraError("camera media listing contains an unexpected path", 5)
        if (not MEDIA_NAME_RE.fullmatch(name)
                or Path(name).suffix.lower() not in MEDIA_EXTENSIONS):
            raise CameraError("camera media listing contains an unsafe filename", 5)
        names.add(name)
    return names


def parse_sha256(output: str, expected_name: str) -> str:
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+?)\s*", output)
    if not match or Path(match.group(2)).name != expected_name:
        raise CameraError("device media checksum output is invalid", 5)
    return match.group(1).lower()


def parse_media_size(output: str) -> int:
    value = output.strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise CameraError("device media size output is invalid", 5)
    size = int(value)
    if size < 1:
        raise CameraError("camera original is still empty", 5)
    if size > MAX_MEDIA_BYTES:
        raise CameraError("camera original exceeds the media byte limit", 5)
    return size


def media_state_is_stable(states: list[tuple[int, str]], samples: int = 3) -> bool:
    if len(states) < samples:
        return False
    tail = states[-samples:]
    return tail[0][0] > 0 and len(set(tail)) == 1


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MEDIA_BYTES:
                raise CameraError("camera original exceeds the media byte limit", 5)
            digest.update(chunk)
    return digest.hexdigest(), size


def _run_ok(argv: list[str], timeout: int = 20,
            cap: int = test_runner.MAX_OUTPUT_BYTES) -> dict:
    result = test_runner.run_bounded(argv, timeout, cap)
    if result.get("transport") != "ok":
        combined = result.get("stdout", "") + "\n" + result.get("stderr", "")
        if "no devices/emulators found" in combined or "device offline" in combined:
            raise CameraError("camera target disconnected", 3)
        raise CameraError("camera device command failed", 5)
    return result


def _media_snapshot(adb: str, target: str) -> tuple[set[str], str]:
    remote = ("find " + REMOTE_CAMERA_DIR
              + " -maxdepth 1 -type f \\( -name '*.jpg' -o -name '*.JPG'"
                " -o -name '*.jpeg' -o -name '*.JPEG' -o -name '*.heic'"
                " -o -name '*.HEIC' -o -name '*.dng' -o -name '*.DNG' \\)"
                " -print 2>/dev/null")
    result = _run_ok([adb, "-s", target, "shell",
                      "sh -c " + shlex.quote(remote)])
    return parse_remote_media_listing(result["stdout"]), result["stdout"]


def _write_text(path: Path, content: str) -> str:
    return test_runner._write_evidence(path, content)


def _capture_ui(adb: str, target: str, raw_dir: Path,
                capture_id: str) -> tuple[str, str]:
    result = _run_ok([adb, "-s", target, "exec-out", "uiautomator", "dump",
                      "/dev/tty"], 30)
    filename = f"{capture_id}.ui.xml"
    digest = _write_text(raw_dir / filename, result["stdout"])
    return f"raw/{filename}@sha256:{digest}", result["stdout"]


def validate_camera_ui(xml: str, expected: dict) -> list[str]:
    """Reject stale launcher/incorrect lens state before touching the shutter."""
    reasons = []
    if 'package="com.fps.camera"' not in xml:
        return ["stock camera is not the foreground UI"]
    if 'content-desc="Shutter"' not in xml:
        reasons.append("camera shutter control is unavailable")
    switch = ("Switch to front camera" if expected["orientation"] == "rear-facing"
              else "Switch to back camera")
    switch_matches = f'content-desc="{switch}"' in xml
    rear_only_mode = (expected["orientation"] == "rear-facing"
                      and expected["mode"] == "pro"
                      and 'content-desc="PRO,Selected"' in xml)
    if not switch_matches and not rear_only_mode:
        reasons.append("active camera orientation does not match the plan")
    selected_mode = expected["mode"]
    if expected["orientation"] == "front-facing":
        if ('text="PHOTO"' not in xml or selected_mode != "photo"
                or 'content-desc="PORTRAIT,Selected"' in xml
                or 'content-desc="PRO,Selected"' in xml
                or 'content-desc="SUPER NIGHT,Selected"' in xml):
            reasons.append("Photo mode is not selected")
        if (expected["zoom"] not in {"1x", "multi-person"}
                or 'resource-id="com.fps.camera:id/single_person"' not in xml
                or 'resource-id="com.fps.camera:id/multi_person"' not in xml):
            reasons.append("planned front field of view is unavailable")
    elif selected_mode == "portrait":
        if 'content-desc="PORTRAIT,Selected"' not in xml:
            reasons.append("Portrait mode is not selected")
    elif selected_mode == "pro":
        if 'content-desc="PRO,Selected"' not in xml:
            reasons.append("Pro mode is not selected")
    elif selected_mode == "super-night":
        if 'content-desc="SUPER NIGHT,Selected"' not in xml:
            reasons.append("Super Night mode is not selected")
    elif selected_mode in {"photo", "super-macro"}:
        if ('text="PHOTO"' not in xml or 'content-desc="PORTRAIT,Selected"' in xml
                or 'content-desc="PRO,Selected"' in xml
                or 'content-desc="SUPER NIGHT,Selected"' in xml):
            reasons.append("Photo mode is not selected")
    else:
        reasons.append("camera plan contains an unsupported still mode")
    if expected["orientation"] == "front-facing":
        pass
    elif selected_mode == "portrait":
        portrait_zoom = {"1x": "zoom_1x", "2x": "zoom_2x"}.get(expected["zoom"])
        if (not portrait_zoom or not re.search(
                r'<node[^>]*resource-id="com\.fps\.camera:id/'
                + portrait_zoom + r'"[^>]*selected="true"', xml)):
            reasons.append("selected Portrait zoom does not match the plan")
    else:
        zoom_description = {
            "0.6x": "Zoom value is 0.6",
            "1x": "Zoom value is 1.0",
            "2x": "Zoom value is 2.0",
        }.get(expected["zoom"])
        if zoom_description:
            pattern = (r'<node[^>]*content-desc="' + re.escape(zoom_description)
                       + r'"[^>]*selected="true"')
            if not re.search(pattern, xml):
                reasons.append("selected camera zoom does not match the plan")
        elif expected["zoom"] == "macro":
            if not re.search(
                    r'<node[^>]*content-desc="SUPER MACRO"[^>]*selected="true"', xml):
                reasons.append("Super Macro is not selected")
        else:
            reasons.append("camera plan contains an unsupported zoom token")
    return reasons


def camera_ui_observations(xml: str) -> list[str]:
    observations = []
    for value in ("Move farther for better effects.",):
        if f'text="{value}"' in xml or f'content-desc="{value}"' in xml:
            observations.append(value)
    if ('resource-id="com.fps.camera:id/single_person"' in xml
            and 'resource-id="com.fps.camera:id/multi_person"' in xml):
        observations.append(
            "Front single-person and multi-person field-of-view controls are exposed.")
    if 'content-desc="Face beauty"' in xml:
        observations.append("Front Face beauty control is exposed.")
    return observations


def face_beauty_level(xml: str) -> int:
    """Read the stock front-camera Face Beauty value from its open panel."""
    matches = []
    for node in re.findall(r"<node\b[^>]*>", xml):
        if 'resource-id="com.fps.camera:id/face_beauty_info"' not in node:
            continue
        text = re.search(r'text="([0-9]+)"', node)
        description = re.search(r'content-desc="([0-9]+)"', node)
        if text and description and text.group(1) == description.group(1):
            matches.append(int(text.group(1)))
    if len(matches) != 1 or not 0 <= matches[0] <= 100:
        raise CameraError("front Face Beauty level is unavailable", 5)
    return matches[0]


def current_camera_orientation(xml: str) -> str:
    if 'content-desc="Switch to front camera"' in xml:
        return "rear-facing"
    if 'content-desc="Switch to rear camera"' in xml:
        return "front-facing"
    if 'content-desc="Switch to back camera"' in xml:
        return "front-facing"
    if 'content-desc="PRO,Selected"' in xml:
        return "rear-facing"
    raise CameraError("active camera orientation is unavailable", 5)


def camera_node_bounds(xml: str, attribute: str,
                       value: str) -> tuple[int, int, int, int]:
    """Locate one accessibility node by its current live bounds."""
    matches = []
    marker = f'{attribute}="{value}"'
    for node in re.findall(r"<node\b[^>]*>", xml):
        if marker not in node:
            continue
        bounds = re.search(r'bounds="\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]"',
                           node)
        if bounds:
            left, top, right, bottom = map(int, bounds.groups())
            if right > left and bottom > top:
                matches.append({
                    "bounds": (left, top, right, bottom),
                    "clickable": 'clickable="true"' in node,
                })
    if len(matches) > 1:
        actionable = [item for item in matches if item["clickable"]]
        if len(actionable) == 1:
            matches = actionable
    if len(matches) != 1:
        raise CameraError("camera control is missing or ambiguous in the live UI", 5)
    return matches[0]["bounds"]


def camera_node_center(xml: str, attribute: str, value: str) -> tuple[int, int]:
    """Locate one actionable accessibility node by its current live bounds."""
    left, top, right, bottom = camera_node_bounds(xml, attribute, value)
    return (left + right) // 2, (top + bottom) // 2


def face_beauty_dismiss_point(xml: str) -> tuple[int, int]:
    """Return a preview point that dismisses the stock Face Beauty panel."""
    left, top, right, bottom = camera_node_bounds(
        xml, "resource-id", "com.fps.camera:id/face_beauty_root")
    _, seekbar_top, _, _ = camera_node_bounds(
        xml, "resource-id", "com.fps.camera:id/face_beauty_seekbar_layout")
    x = (left + right) // 2
    y = (top + seekbar_top) // 2
    if not (left <= x < right and top <= y < min(bottom, seekbar_top)):
        raise CameraError("front Face Beauty panel has no safe preview area", 5)
    return x, y


def _live_camera_xml(adb: str, target: str) -> str:
    return _run_ok([adb, "-s", target, "exec-out", "uiautomator", "dump",
                    "/dev/tty"], 30)["stdout"]


def _tap_node(adb: str, target: str, xml: str,
              attribute: str, value: str, label: str, delay: float) -> str:
    x, y = camera_node_center(xml, attribute, value)
    _run_ok([adb, "-s", target, "shell", "input", "tap", str(x), str(y)])
    time.sleep(delay)
    return label


def _prepare_camera_ui(adb: str, target: str,
                       expected: dict) -> tuple[list[str], list[str]]:
    actions = []
    observations = []
    xml = _live_camera_xml(adb, target)
    orientation = current_camera_orientation(xml)
    if orientation != expected["orientation"]:
        switch = ("Switch to back camera" if orientation == "front-facing"
                  else "Switch to front camera")
        actions.append(_tap_node(
            adb, target, xml, "content-desc", switch, "switch-camera", 2))
        xml = _live_camera_xml(adb, target)
    mode_text = {
        "photo": "PHOTO",
        "super-macro": "PHOTO",
        "portrait": "PORTRAIT",
        "pro": "PRO",
        "super-night": "SUPER NIGHT",
    }.get(expected["mode"])
    if not mode_text:
        raise CameraError("camera plan contains an unsupported still mode", 5)
    try:
        camera_node_center(xml, "text", mode_text)
    except CameraError:
        if expected["mode"] != "super-night":
            raise
        pano_x, pano_y = camera_node_center(xml, "text", "PANO")
        _run_ok([adb, "-s", target, "shell", "input", "swipe",
                 str(min(pano_x, 1000)), str(pano_y),
                 str(max(100, pano_x - 650)), str(pano_y), "350"])
        time.sleep(2)
        actions.append("scroll-mode-strip")
        xml = _live_camera_xml(adb, target)
    actions.append(_tap_node(adb, target, xml, "text", mode_text,
                             f"mode-{expected['mode']}", 2))
    xml = _live_camera_xml(adb, target)
    observations.extend(camera_ui_observations(xml))
    if expected["orientation"] == "front-facing":
        front_control = {
            "1x": ("com.fps.camera:id/single_person",
                   "front-field-of-view-1x-single-person"),
            "multi-person": ("com.fps.camera:id/multi_person",
                             "front-field-of-view-multi-person"),
        }.get(expected["zoom"])
        if expected["mode"] != "photo" or not front_control:
            raise CameraError("camera plan contains an unsupported front view", 5)
        actions.append(_tap_node(
            adb, target, xml, "resource-id",
            front_control[0], front_control[1], 1))
        xml = _live_camera_xml(adb, target)
        actions.append(_tap_node(
            adb, target, xml, "content-desc", "Face beauty",
            "inspect-face-beauty", 1))
        beauty_xml = _live_camera_xml(adb, target)
        beauty_level = face_beauty_level(beauty_xml)
        dismiss_x, dismiss_y = face_beauty_dismiss_point(beauty_xml)
        _run_ok([adb, "-s", target, "shell", "input", "tap",
                 str(dismiss_x), str(dismiss_y)])
        time.sleep(1)
        xml = _live_camera_xml(adb, target)
        if ('package="com.fps.camera"' not in xml
                or 'com.fps.camera:id/face_beauty_seekbar' in xml
                or 'content-desc="Face beauty"' not in xml):
            raise CameraError("front Face Beauty panel did not dismiss safely", 3)
        if beauty_level != 0:
            raise CameraError("front Face Beauty is not disabled", 3)
        observations.append("Front Face Beauty level 0 (disabled) was verified.")
        return actions, list(dict.fromkeys(observations))
    if expected["mode"] == "portrait":
        resource = {"1x": "com.fps.camera:id/zoom_1x",
                    "2x": "com.fps.camera:id/zoom_2x"}.get(expected["zoom"])
        if not resource:
            raise CameraError("camera plan contains an unsupported Portrait zoom", 5)
        actions.append(_tap_node(
            adb, target, xml, "resource-id", resource,
            f"zoom-{expected['zoom']}", 1))
    else:
        description = {
            "macro": "SUPER MACRO",
            "0.6x": "Zoom value is 0.6",
            "1x": "Zoom value is 1.0",
            "2x": "Zoom value is 2.0",
        }.get(expected["zoom"])
        if not description:
            raise CameraError("camera plan contains an unsupported zoom control", 5)
        actions.append(_tap_node(
            adb, target, xml, "content-desc", description,
            f"zoom-{expected['zoom']}", 1))
    return actions, list(dict.fromkeys(observations))


def _pull_original(adb: str, target: str, run_dir: Path,
                   capture_id: str, name: str) -> dict:
    media_dir = run_dir / "media" / capture_id
    media_dir.mkdir(parents=True, mode=0o750)
    destination = media_dir / name
    temporary = media_dir / (name + ".partial")
    if destination.exists() or temporary.exists():
        raise CameraError("camera original destination already exists", 5)
    remote = f"{REMOTE_CAMERA_DIR}/{name}"
    states = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        size_output = _run_ok(
            [adb, "-s", target, "shell", "stat", "-c", "%s", remote],
            20)["stdout"]
        if size_output.strip() == "0":
            states.clear()
            time.sleep(0.5)
            continue
        size = parse_media_size(size_output)
        remote_hash = parse_sha256(
            _run_ok([adb, "-s", target, "shell", "sha256sum", remote], 30)["stdout"],
            name)
        states.append((size, remote_hash))
        if media_state_is_stable(states):
            break
        time.sleep(0.5)
    else:
        raise CameraError("camera original did not reach a stable non-empty state", 5)
    _run_ok([adb, "-s", target, "pull", remote, str(temporary)], 60)
    local_hash, local_size = _sha256_file(temporary)
    final_size = parse_media_size(
        _run_ok([adb, "-s", target, "shell", "stat", "-c", "%s", remote],
                20)["stdout"])
    final_remote_hash = parse_sha256(
        _run_ok([adb, "-s", target, "shell", "sha256sum", remote], 30)["stdout"],
        name)
    if (local_hash != remote_hash or local_size != size
            or final_remote_hash != remote_hash or final_size != size):
        raise CameraError("camera original checksum changed during transfer", 5)
    os.chmod(temporary, 0o640)
    os.replace(temporary, destination)
    test_runner._sync_directory(media_dir)
    return {
        "original_filename": name,
        "media_type": destination.suffix.lower().removeprefix("."),
        "byte_size": local_size,
        "sha256": local_hash,
        "original_ref": f"media/{capture_id}/{name}@sha256:{local_hash}",
    }


def _owner_controlled_directory(path: Path):
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
            or metadata.st_uid != os.geteuid()):
        raise CameraError("private output must be owner-controlled mode 0750 or stricter", 3)


def _acquire_lock(root: Path, role: str) -> int:
    locks = root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    fd = os.open(locks / f"{role}.camera-pilot.lock",
                 os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise CameraError("camera target is already locked", 3) from exc
    return fd


def _release_lock(fd: int):
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _validate_target(args):
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    if args.target not in test_runner._authorized_devices(args.adb):
        raise CameraError("selected camera target is not an authorized USB device", 3)


def _load_report(run_dir: Path) -> dict:
    if not run_dir.name.endswith(".partial"):
        raise CameraError("camera run must reference its partial directory")
    _owner_controlled_directory(run_dir)
    value, _ = test_runner._load_unique_json(
        run_dir / "result.json", test_runner.MAX_REPORT_BYTES)
    operation_labels = {
        "baseline-camera-pilot": PILOT_LABEL,
        "baseline-camera-measurement": DECLARED_LABEL,
    }
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("operation") not in operation_labels
            or value.get("label") != operation_labels.get(value.get("operation"))
            or value.get("status") != "INCOMPLETE"):
        raise CameraError("camera partial report is invalid", 5)
    return value


def _register_runner_version(report: dict, repo_root: Path) -> str:
    """Bind every capture implementation used by a staged camera run."""
    tool = report["tool"]
    versions = tool.setdefault("runner_versions", [{
        "revision": tool["revision"],
        "sha256": tool["runner_sha256"],
    }])
    current = {
        "revision": test_runner._git_revision(repo_root),
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if current not in versions:
        versions.append(current)
    return current["sha256"]


def _next_attempt_number(raw_dir: Path, capture_id: str, report: dict) -> int:
    """Choose a fresh attempt suffix without deleting prior partial evidence."""
    occupied = set()
    prefix = re.compile(
        re.escape(capture_id) + r"\.attempt-([1-9][0-9]*)\..+")
    for path in raw_dir.iterdir():
        match = prefix.fullmatch(path.name)
        if match:
            occupied.add(int(match.group(1)))
    attempt_prefix = re.compile(
        re.escape(capture_id) + r"\.attempt-([1-9][0-9]*)")
    for item in report.get("attempts", []):
        match = attempt_prefix.fullmatch(str(item.get("attempt_id", "")))
        if match:
            occupied.add(int(match.group(1)))
    return max(occupied, default=0) + 1


def _verify_runner_provenance(report: dict):
    versions = report.get("tool", {}).get("runner_versions", [])
    if not versions:
        return
    declared = {item.get("sha256") for item in versions}
    records = report.get("captures", []) + report.get("attempts", [])
    if (None in declared or any(
            item.get("runner_sha256") not in declared for item in records)):
        raise CameraError("camera capture runner provenance is incomplete", 5)


def _atomic_report(path: Path, report: dict, target: str):
    serialized = json.dumps(report, sort_keys=True)
    if target in serialized:
        raise CameraError("private target escaped camera report redaction", 5)
    test_runner._atomic_json(path, report)


def verify_private_refs(report_path: Path, refs: list[str]):
    """Verify bounded binary or text evidence within one private run."""
    root = report_path.parent.resolve()
    for ref in refs:
        if not isinstance(ref, str) or "@sha256:" not in ref:
            raise CameraError("camera report has an invalid evidence reference", 5)
        relative, expected_digest = ref.rsplit("@sha256:", 1)
        relpath = Path(relative)
        if (relpath.is_absolute() or ".." in relpath.parts
                or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)):
            raise CameraError("camera report has an invalid evidence reference", 5)
        evidence_path = (root / relpath).resolve()
        try:
            evidence_path.relative_to(root)
        except ValueError as exc:
            raise CameraError("camera evidence escapes its run directory", 5) from exc
        digest, _ = _sha256_file(evidence_path)
        if digest != expected_digest:
            raise CameraError("camera evidence hash mismatch", 5)


def start(args, repo_root: Path) -> Path:
    if not args.run_id or not test_runner.RUN_ID_RE.fullmatch(args.run_id):
        raise CameraError("camera start requires a valid --run-id")
    if not all((args.target, args.device_role, args.device_map, args.output,
                args.expected_build, args.conditions)):
        raise CameraError("camera start is missing required target or run metadata")
    if not (args.operator_confirmed_fixture and args.operator_confirmed_defaults
            and args.operator_confirmed_display_50
            and args.operator_confirmed_unlocked):
        raise CameraError("camera start requires fixture, camera-default, display and unlocked attestations", 3)
    protocol, protocol_hash = baseline_protocol.load_protocol(args.config)
    profile = camera_profile(protocol, getattr(args, "declared", False))
    repetitions = profile["standard_matrix_repetitions"]
    plan = build_capture_plan(protocol, repetitions)
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        partial = root / f"{args.run_id}.partial"
        final = root / args.run_id
        if (partial.exists() or final.exists()
                or final.with_name(final.name + ".non-comparable").exists()
                or final.with_name(final.name + ".harness-error").exists()):
            raise CameraError("immutable camera output collision", 3)
        try:
            guard = rig.acquire_test_start_guard(
                getattr(args, "rig_config", None), args.device_role,
                args.device_map, args.target)
            try:
                partial.mkdir(mode=0o750)
                (partial / "raw").mkdir(mode=0o750)
                (partial / "media").mkdir(mode=0o750)
            finally:
                guard.release()
        except rig.RigError as exc:
            raise CameraError(str(exc), exc.exit_code) from exc
        _validate_target(args)
        identity, refs = test_runner._capture_identity(
            args.adb, args.target, partial, 20)
        if identity["build_id"] != args.expected_build:
            raise CameraError("camera target build does not match the expected stock build", 3)
        try:
            preflight = baseline_pilot._collect_preflight(
                baseline_pilot.Collector(args.adb, args.target, partial),
                protocol,
                args.operator_confirmed_display_50,
                args.operator_confirmed_unlocked)
        except baseline_pilot.CaseFailure as exc:
            raise CameraError("camera condition capture failed", 5) from exc
        mismatches = evaluate_camera_preflight(
            preflight["observed"], protocol,
            args.operator_confirmed_display_50,
            args.operator_confirmed_unlocked)
        if mismatches:
            raise CameraError(
                "camera preflight controls do not match the protocol: "
                + "; ".join(mismatches), 3)
        camera = _procedure(protocol, "camera-scene")["fixed_parameters"]
        report = {
            "schema_version": SCHEMA_VERSION,
            "operation": profile["operation"],
            "label": profile["label"],
            "run_id": args.run_id,
            "protocol": {"id": protocol["protocol_id"], "sha256": protocol_hash},
            "run_profile": profile,
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
            "fixture": camera["fixture"],
            "capture_defaults": camera["capture_defaults"],
            "conditions": args.conditions.strip(),
            "observed_conditions": preflight["observed"],
            "condition_evidence_refs": preflight["raw_evidence_refs"],
            "started_at_utc": _utc_now(),
            "finished_at_utc": None,
            "status": "INCOMPLETE",
            "expected_captures": plan,
            "captures": [],
            "attempts": [],
            "identity_evidence_refs": refs,
            "mode_inventory": camera["advertised_mode_survey"],
            "limitations": ([] if profile["mode"] == "declared" else [
                "Pilot only; source media is not declared baseline evidence.",
            ]) + [
                "Fixture registration is repeatable to its stated tolerance, not pixel-exact.",
                "Lamp colour descriptions are operator descriptions, not measured colour temperatures.",
            ],
            "comparability_exclusions": [],
            "errors": [],
        }
        _atomic_report(partial / "result.json", report, args.target)
        return partial
    finally:
        _release_lock(lock_fd)


def capture(args) -> tuple[int, Path]:
    if not all((args.target, args.device_role, args.device_map, args.run_dir)):
        raise CameraError("camera capture is missing its target or partial run")
    if not (args.operator_confirmed_lamp and args.operator_confirmed_ui
            and args.operator_confirmed_box_closed):
        raise CameraError("camera capture requires lamp, UI and closed-box attestations", 3)
    if (args.focus_x is None or args.focus_y is None
            or not (0 <= args.focus_x < 1116) or not (0 <= args.focus_y < 2484)):
        raise CameraError("camera capture requires an in-bounds focus coordinate")
    _validate_target(args)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir)
        runner_sha256 = _register_runner_version(report, _repo_root())
        index = len(report["captures"])
        if index >= len(report["expected_captures"]):
            raise CameraError("camera plan has no remaining captures", 3)
        expected = report["expected_captures"][index]
        raw_dir = run_dir / "raw"
        refs = []
        attempt_number = _next_attempt_number(
            raw_dir, expected["capture_id"], report)
        attempt_prefix = f"{expected['capture_id']}.attempt-{attempt_number}"
        launch = _run_ok([
            args.adb, "-s", args.target, "shell", "am", "start", "-W", "-n",
            report["capture_defaults"]["camera_component"],
        ], 30)
        for stream in ("stdout", "stderr"):
            filename = f"{attempt_prefix}.launch.{stream}.txt"
            digest = _write_text(raw_dir / filename, launch.get(stream, ""))
            refs.append(f"raw/{filename}@sha256:{digest}")
        time.sleep(2)
        try:
            preparation_actions, preparation_observations = _prepare_camera_ui(
                args.adb, args.target, expected)
        except CameraError as exc:
            report.setdefault("attempts", []).append({
                "attempt_id": attempt_prefix,
                "capture_id": expected["capture_id"],
                "status": "BLOCKED" if exc.exit_code == 3 else "HARNESS_ERROR",
                "reason": str(exc),
                "shutter_triggered": False,
                "ui_preparation_actions": [],
                "ui_observations": [],
                "recorded_at_utc": _utc_now(),
                "raw_evidence_refs": refs,
                "runner_sha256": runner_sha256,
            })
            _atomic_report(run_dir / "result.json", report, args.target)
            return exc.exit_code, run_dir / "result.json"
        time.sleep(1)
        ui_ref, ui_xml = _capture_ui(
            args.adb, args.target, raw_dir, attempt_prefix + ".before")
        refs.append(ui_ref)
        ui_reasons = validate_camera_ui(ui_xml, expected)
        ui_observations = list(dict.fromkeys(
            preparation_observations + camera_ui_observations(ui_xml)))
        if ui_reasons:
            report.setdefault("attempts", []).append({
                "attempt_id": attempt_prefix,
                "capture_id": expected["capture_id"],
                "status": "BLOCKED",
                "reason": "; ".join(ui_reasons),
                "shutter_triggered": False,
                "ui_preparation_actions": preparation_actions,
                "ui_observations": ui_observations,
                "recorded_at_utc": _utc_now(),
                "raw_evidence_refs": refs,
                "runner_sha256": runner_sha256,
            })
            _atomic_report(run_dir / "result.json", report, args.target)
            return 3, run_dir / "result.json"
        before, before_text = _media_snapshot(args.adb, args.target)
        before_name = f"{attempt_prefix}.media-before.txt"
        refs.append(f"raw/{before_name}@sha256:{_write_text(raw_dir / before_name, before_text)}")
        _run_ok([args.adb, "-s", args.target, "shell", "input", "tap",
                 str(args.focus_x), str(args.focus_y)])
        time.sleep(report["capture_defaults"]["focus_settle_seconds"])
        _run_ok([args.adb, "-s", args.target, "shell", "input", "tap",
                 "557", "2060"])
        deadline = time.monotonic() + 15
        after = before
        after_text = before_text
        while time.monotonic() < deadline:
            time.sleep(0.5)
            after, after_text = _media_snapshot(args.adb, args.target)
            if after - before:
                break
        after_name = f"{attempt_prefix}.media-after.txt"
        refs.append(f"raw/{after_name}@sha256:{_write_text(raw_dir / after_name, after_text)}")
        created = sorted(after - before)
        if len(created) != 1:
            report.setdefault("attempts", []).append({
                "attempt_id": attempt_prefix,
                "capture_id": expected["capture_id"],
                "status": "FAIL",
                "reason": "shutter did not produce exactly one new original",
                "shutter_triggered": True,
                "ui_preparation_actions": preparation_actions,
                "ui_observations": ui_observations,
                "recorded_at_utc": _utc_now(),
                "focus_coordinate_px": [args.focus_x, args.focus_y],
                "raw_evidence_refs": refs,
                "runner_sha256": runner_sha256,
            })
            report["errors"].append("shutter did not produce exactly one new original")
            _atomic_report(run_dir / "result.json", report, args.target)
            return 4, run_dir / "result.json"
        try:
            media = _pull_original(args.adb, args.target, run_dir,
                                   expected["capture_id"], created[0])
        except CameraError as exc:
            report.setdefault("attempts", []).append({
                "attempt_id": attempt_prefix,
                "capture_id": expected["capture_id"],
                "status": "HARNESS_ERROR",
                "reason": str(exc),
                "shutter_triggered": True,
                "ui_preparation_actions": preparation_actions,
                "ui_observations": ui_observations,
                "recorded_at_utc": _utc_now(),
                "focus_coordinate_px": [args.focus_x, args.focus_y],
                "new_original_filename": created[0],
                "raw_evidence_refs": refs,
                "runner_sha256": runner_sha256,
            })
            report["errors"].append(str(exc))
            _atomic_report(run_dir / "result.json", report, args.target)
            return 5, run_dir / "result.json"
        record = dict(expected)
        record.update({
            "status": "PASS",
            "reason": "exactly one new original was transferred with matching checksums",
            "captured_at_utc": _utc_now(),
            "focus_coordinate_px": [args.focus_x, args.focus_y],
            "lamp_and_ui_operator_confirmed": True,
            "ui_preparation_actions": preparation_actions,
            "ui_observations": ui_observations,
            "raw_evidence_refs": refs,
            "runner_sha256": runner_sha256,
            **media,
        })
        report["captures"].append(record)
        _atomic_report(run_dir / "result.json", report, args.target)
        return 0, run_dir / "result.json"
    finally:
        _release_lock(lock_fd)


def finalize(args) -> Path:
    if not all((args.target, args.device_role, args.device_map, args.run_dir)):
        raise CameraError("camera finalize is missing its target or partial run")
    _validate_target(args)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir)
        expected = report["expected_captures"]
        captures = report["captures"]
        if len(captures) != len(expected):
            raise CameraError("camera run is incomplete and cannot be finalized", 3)
        report_path = run_dir / "result.json"
        final = run_dir.with_name(run_dir.name.removesuffix(".partial"))
        if final.exists():
            raise CameraError("immutable camera final output collision", 3)
        for ref in report["identity_evidence_refs"]:
            test_runner._verify_evidence_refs(report_path, [ref])
        test_runner._verify_evidence_refs(
            report_path, report.get("condition_evidence_refs", []))
        for item in captures:
            test_runner._verify_evidence_refs(
                report_path, item.get("raw_evidence_refs", []))
            if item.get("status") != "PASS":
                continue
            original = run_dir / item["original_ref"].rsplit("@sha256:", 1)[0]
            digest, _ = _sha256_file(original)
            if digest != item["sha256"]:
                raise CameraError("camera original failed final checksum verification", 5)
        attempts = report.get("attempts", [])
        for item in attempts:
            test_runner._verify_evidence_refs(
                report_path, item.get("raw_evidence_refs", []))
        for discovery in report.get("pilot_discoveries", []):
            verify_private_refs(
                report_path, discovery.get("raw_evidence_refs", []))
        _verify_runner_provenance(report)
        report["finished_at_utc"] = _utc_now()
        report["tool"]["finalizer_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()
        if report["operation"] == "baseline-camera-measurement":
            _, digest = baseline_protocol.load_protocol(args.config)
            if digest != report["protocol"]["sha256"]:
                raise CameraError("baseline protocol changed during the camera run", 5)
        report["comparability_exclusions"] = []
        if any(item.get("status") != "PASS" for item in captures):
            report["status"] = "FAIL"
        else:
            report["status"] = "PASS"
        _atomic_report(report_path, report, args.target)
        os.rename(run_dir, final)
        test_runner._sync_directory(final.parent)
        return final / "result.json"
    finally:
        _release_lock(lock_fd)


def quarantine(args) -> Path:
    if not all((args.target, args.device_role, args.device_map,
                args.run_dir, args.reason)):
        raise CameraError("camera quarantine is missing required metadata")
    if len(args.reason) > 500:
        raise CameraError("camera quarantine reason exceeds its limit")
    test_runner.load_device_map(Path(args.device_map), args.device_role, args.target)
    run_dir = Path(args.run_dir).resolve()
    root = run_dir.parent
    _owner_controlled_directory(root)
    lock_fd = _acquire_lock(root, args.device_role)
    try:
        report = _load_report(run_dir)
        status = getattr(args, "status", "HARNESS_ERROR")
        if status not in {"HARNESS_ERROR", "NON_COMPARABLE"}:
            raise CameraError("camera quarantine status is invalid")
        report["status"] = status
        report["finished_at_utc"] = _utc_now()
        if status == "HARNESS_ERROR":
            report["errors"].append(args.reason)
            for item in report["captures"]:
                if item.get("status") == "PASS" and item.get("byte_size", 0) < 1:
                    item["status"] = "HARNESS_ERROR"
                    item["reason"] = args.reason
        else:
            report.setdefault("comparability_exclusions", []).append(args.reason)
        _atomic_report(run_dir / "result.json", report, args.target)
        suffix = (".harness-error" if status == "HARNESS_ERROR"
                  else ".non-comparable")
        final = run_dir.with_name(run_dir.name.removesuffix(".partial")
                                  + suffix)
        if final.exists():
            raise CameraError("camera quarantine output collision", 3)
        os.rename(run_dir, final)
        test_runner._sync_directory(final.parent)
        return final / "result.json"
    finally:
        _release_lock(lock_fd)


def dry_run(config: str, declared: bool = False) -> dict:
    protocol, digest = baseline_protocol.load_protocol(config)
    profile = camera_profile(protocol, declared)
    plan = build_capture_plan(protocol, profile["standard_matrix_repetitions"])
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": profile["operation"] + "-dry-run",
        "label": profile["label"],
        "protocol_sha256": digest,
        "run_profile": profile,
        "device_commands_executed": 0,
        "output_directories_created": 0,
        "capture_count": len(plan),
        "capture_order": plan,
        "physical_interventions": [
            "set the recorded lamp scene and wait its declared settle interval",
            "keep the box closed for each capture",
            "reverse the phone once before the front-camera group",
        ],
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock camera measurement registrar")
    parser.add_argument("--config", default=str(_repo_root() / "config" / "baseline.json"))
    sub = parser.add_subparsers(dest="action", required=True)
    dry_parser = sub.add_parser("dry-run")
    dry_parser.add_argument("--declared", action="store_true")
    start_parser = sub.add_parser("start")
    for target in (start_parser,):
        target.add_argument("--target", required=True)
        target.add_argument("--device-role", required=True)
        target.add_argument("--device-map", required=True)
        target.add_argument("--rig-config")
        target.add_argument("--adb", default="adb")
    start_parser.add_argument("--run-id", required=True)
    start_parser.add_argument("--output", required=True)
    start_parser.add_argument("--expected-build", required=True,
                              help="exact ro.build.id expected on the stock target")
    start_parser.add_argument("--conditions", required=True)
    start_parser.add_argument("--operator-confirmed-fixture", action="store_true")
    start_parser.add_argument("--operator-confirmed-defaults", action="store_true")
    start_parser.add_argument("--operator-confirmed-display-50", action="store_true")
    start_parser.add_argument("--operator-confirmed-unlocked", action="store_true")
    start_parser.add_argument("--declared", action="store_true")
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--target", required=True)
    capture_parser.add_argument("--device-role", required=True)
    capture_parser.add_argument("--device-map", required=True)
    capture_parser.add_argument("--adb", default="adb")
    capture_parser.add_argument("--run-dir", required=True)
    capture_parser.add_argument("--focus-x", required=True, type=int)
    capture_parser.add_argument("--focus-y", required=True, type=int)
    capture_parser.add_argument("--operator-confirmed-lamp", action="store_true")
    capture_parser.add_argument("--operator-confirmed-ui", action="store_true")
    capture_parser.add_argument("--operator-confirmed-box-closed", action="store_true")
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--target", required=True)
    finalize_parser.add_argument("--device-role", required=True)
    finalize_parser.add_argument("--device-map", required=True)
    finalize_parser.add_argument("--adb", default="adb")
    finalize_parser.add_argument("--run-dir", required=True)
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
            print(json.dumps(dry_run(args.config, args.declared), indent=2, sort_keys=True))
            return 0
        if args.action == "start":
            print(f"run_dir={start(args, _repo_root())}")
            return 0
        if args.action == "capture":
            code, result = capture(args)
            print(f"result={result}")
            return code
        if args.action == "finalize":
            result = finalize(args)
            print(f"result={result}")
            report, _ = test_runner._load_unique_json(
                result, test_runner.MAX_REPORT_BYTES)
            return 0
        print(f"result={quarantine(args)}")
        return 0
    except (CameraError, baseline_protocol.ProtocolError,
            test_runner.RunnerError) as exc:
        print(str(exc), file=os.sys.stderr)
        return getattr(exc, "exit_code", 2)
