"""Identity-bound USB-rig control and battery-maintenance policy.

The deployment configuration is private because it binds local USB topology to
private ADB identities through the existing device map.  Reports produced by
this module contain roles and topology only; they never contain ADB serials.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Callable

SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 128 * 1024
MAX_STATE_BYTES = 1024 * 1024
LOCATION_RE = re.compile(r"[0-9]+-[0-9]+(?:\.[0-9]+)*")
HEX_ID_RE = re.compile(r"[0-9a-f]{4}")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
REASON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
POWER_ACTIONS = {"on", "off"}
POLICY_MODES = {"native-limit", "host-hysteresis"}


class RigError(Exception):
    """Controlled rig failure carrying a stable process exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sync_directory(path: Path) -> None:
    """Best-effort directory durability on filesystems that support it."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _expect_keys(value: dict, required: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise RigError(f"{label} has unexpected or missing fields")


def _absolute_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise RigError(f"{label} must be an absolute path")
    return value


def _read_bounded_regular(path: Path, cap: int, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RigError(f"{label} is unreadable") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RigError(f"{label} is not a regular file")
        chunks = []
        remaining = cap + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > cap:
            raise RigError(f"{label} exceeds its byte limit")
        return raw, metadata
    finally:
        os.close(fd)


def validate_config(document: dict) -> dict:
    top = {
        "schema_version", "adb", "uhubctl", "device_map", "state_root",
        "hub", "roles", "inhibit_roots",
    }
    _expect_keys(document, top, "rig config")
    if document["schema_version"] != SCHEMA_VERSION:
        raise RigError("unsupported rig-config schema version")
    for field in ("adb", "uhubctl", "device_map", "state_root"):
        _absolute_path(document[field], field)

    hub_fields = {
        "model", "control_location", "control_port_count", "usb2", "usb3",
    }
    hub = document["hub"]
    _expect_keys(hub, hub_fields, "rig hub")
    if not isinstance(hub["model"], str) or not (1 <= len(hub["model"]) <= 120):
        raise RigError("rig hub has an invalid model label")
    if not isinstance(hub["control_location"], str) or not LOCATION_RE.fullmatch(
            hub["control_location"]):
        raise RigError("rig hub has an invalid control location")
    if (isinstance(hub["control_port_count"], bool)
            or not isinstance(hub["control_port_count"], int)
            or not 1 <= hub["control_port_count"] <= 32):
        raise RigError("rig hub has an invalid port count")
    for generation in ("usb2", "usb3"):
        component = hub[generation]
        _expect_keys(component, {"sysfs_path", "vendor_id", "product_id"},
                     f"rig {generation} hub")
        if (not isinstance(component["sysfs_path"], str)
                or not LOCATION_RE.fullmatch(component["sysfs_path"])):
            raise RigError(f"rig {generation} hub has an invalid sysfs path")
        for field in ("vendor_id", "product_id"):
            value = component[field]
            if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
                raise RigError(f"rig {generation} hub has an invalid {field}")
    if hub["usb2"]["sysfs_path"] != hub["control_location"]:
        raise RigError("rig control location must name its USB2 hub")

    roots = document["inhibit_roots"]
    if (not isinstance(roots, list) or len(roots) > 32
            or len(set(roots)) != len(roots)):
        raise RigError("rig config has invalid inhibit roots")
    for root in roots:
        _absolute_path(root, "inhibit root")

    roles = document["roles"]
    if not isinstance(roles, list) or not 1 <= len(roles) <= 16:
        raise RigError("rig config has invalid roles")
    seen_roles: set[str] = set()
    seen_ports: set[int] = set()
    seen_paths: set[str] = set()
    role_fields = {"role", "logical_port", "usb_path", "battery"}
    battery_fields = {
        "mode", "low_percent", "high_percent", "hard_ceiling_percent",
        "maximum_temperature_c", "resume_temperature_c",
        "off_probe_interval_seconds",
    }
    for entry in roles:
        _expect_keys(entry, role_fields, "rig role")
        role = entry["role"]
        if not isinstance(role, str) or not TOKEN_RE.fullmatch(role):
            raise RigError("rig config has an invalid role")
        port = entry["logical_port"]
        if (isinstance(port, bool) or not isinstance(port, int)
                or not 1 <= port <= hub["control_port_count"]):
            raise RigError("rig role has an invalid logical port")
        expected_path = f"usb:{hub['control_location']}.{port}"
        if entry["usb_path"] != expected_path:
            raise RigError("rig role USB path does not match its hub port")
        if role in seen_roles or port in seen_ports or entry["usb_path"] in seen_paths:
            raise RigError("rig roles duplicate a role, port, or USB path")
        seen_roles.add(role)
        seen_ports.add(port)
        seen_paths.add(entry["usb_path"])

        policy = entry["battery"]
        _expect_keys(policy, battery_fields, "rig battery policy")
        if policy["mode"] not in POLICY_MODES:
            raise RigError("rig battery policy has an invalid mode")
        numeric_percent = (
            policy["low_percent"], policy["high_percent"],
            policy["hard_ceiling_percent"],
        )
        if any(isinstance(value, bool) or not isinstance(value, int)
               for value in numeric_percent):
            raise RigError("rig battery policy percentages must be integers")
        low, high, hard = numeric_percent
        if not 5 <= low < high <= hard <= 100:
            raise RigError("rig battery policy percentage order is invalid")
        for field in ("maximum_temperature_c", "resume_temperature_c"):
            value = policy[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RigError("rig battery policy temperature is invalid")
        if not 0 <= policy["resume_temperature_c"] < policy[
                "maximum_temperature_c"] <= 50:
            raise RigError("rig battery policy temperature bounds are invalid")
        interval = policy["off_probe_interval_seconds"]
        if (isinstance(interval, bool) or not isinstance(interval, int)
                or not 300 <= interval <= 86400):
            raise RigError("rig battery policy probe interval is invalid")
    return document


def load_config(path: Path, *, runtime: bool = False) -> dict:
    raw, metadata = _read_bounded_regular(path, MAX_CONFIG_BYTES, "rig config")
    if runtime and (metadata.st_uid != 0 or metadata.st_mode & 0o037):
        raise RigError(
            "runtime rig config must be root-owned mode 0640 or stricter")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigError("rig config is not valid JSON") from exc
    return validate_config(document)


def role_config(config: dict, role: str) -> dict:
    matches = [entry for entry in config["roles"] if entry["role"] == role]
    if len(matches) != 1:
        raise RigError("selected role is not configured for this rig", 3)
    return matches[0]


def mapped_serial(device_map: Path, role: str) -> str:
    raw, metadata = _read_bounded_regular(
        device_map, MAX_CONFIG_BYTES, "private device map")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o037:
        raise RigError(
            "private device map must be runner-owned mode 0640 or stricter")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RigError("private device map is invalid") from exc
    if (not isinstance(document, dict) or set(document) != {"schema_version", "devices"}
            or document.get("schema_version") != 1
            or not isinstance(document.get("devices"), list)):
        raise RigError("private device map is invalid")
    matches = []
    seen_roles: set[str] = set()
    seen_serials: set[str] = set()
    for entry in document["devices"]:
        if (not isinstance(entry, dict)
                or set(entry) != {"role", "adb_serial", "disposable"}):
            raise RigError("private device map is invalid")
        mapped_role = entry["role"]
        serial = entry["adb_serial"]
        if (not isinstance(mapped_role, str)
                or not TOKEN_RE.fullmatch(mapped_role)
                or not isinstance(serial, str) or not serial
                or any(ch.isspace() for ch in serial)
                or not isinstance(entry["disposable"], bool)
                or mapped_role in seen_roles or serial in seen_serials):
            raise RigError("private device map is invalid")
        seen_roles.add(mapped_role)
        seen_serials.add(serial)
        if mapped_role == role:
            matches.append(serial)
    if len(matches) != 1:
        raise RigError("selected private role is not mapped exactly once", 3)
    return matches[0]


def parse_battery(output: str) -> dict:
    fields = {}
    for line in output.splitlines():
        match = re.match(r"\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            fields[match.group(1)] = match.group(2)
    required = {
        "AC powered", "USB powered", "Wireless powered", "status", "level",
        "temperature",
    }
    if not required.issubset(fields):
        raise RigError("Android battery state is incomplete", 4)
    try:
        level = int(fields["level"])
        status_code = int(fields["status"])
        temperature_c = int(fields["temperature"]) / 10.0
    except ValueError as exc:
        raise RigError("Android battery state is incomplete", 4) from exc
    if not 0 <= level <= 100 or not -50 <= temperature_c <= 100:
        raise RigError("Android battery state is implausible", 4)
    return {
        "level_percent": level,
        "status_code": status_code,
        "externally_powered": bool(
            fields["AC powered"].lower() == "true"
            or fields["USB powered"].lower() == "true"
            or fields["Wireless powered"].lower() == "true"),
        "temperature_c": temperature_c,
    }


def battery_decision(policy: dict, observation: dict, intended_on: bool) -> dict:
    """Return a pure policy decision; callers perform and verify effects."""
    level = observation["level_percent"]
    temperature = observation["temperature_c"]
    if temperature >= policy["maximum_temperature_c"]:
        return {"action": "off", "reason": "battery-temperature-stop"}
    if policy["mode"] == "native-limit":
        if level > policy["hard_ceiling_percent"]:
            return {"action": "off", "reason": "native-limit-hard-ceiling"}
        if not intended_on and (level <= policy["high_percent"]
                                and temperature <= policy["resume_temperature_c"]):
            return {"action": "on", "reason": "native-limit-recovery"}
        return {"action": "hold", "reason": "native-limit-monitor"}
    if intended_on and level >= policy["high_percent"]:
        return {"action": "off", "reason": "hysteresis-high"}
    if (not intended_on and level <= policy["low_percent"]
            and temperature <= policy["resume_temperature_c"]):
        return {"action": "on", "reason": "hysteresis-low"}
    return {"action": "hold", "reason": "hysteresis-band"}


class RigController:
    def __init__(self, config: dict,
                 executor: Callable[..., subprocess.CompletedProcess[str]] | None = None,
                 sysfs_root: Path = Path("/sys/bus/usb/devices")):
        self.config = validate_config(config)
        self.executor = executor or subprocess.run
        self.sysfs_root = sysfs_root

    def _run(self, argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        try:
            return self.executor(
                argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RigError("required rig command did not complete", 4) from exc

    def verify_hub(self) -> dict:
        observed = {}
        for generation in ("usb2", "usb3"):
            expected = self.config["hub"][generation]
            base = self.sysfs_root / expected["sysfs_path"]
            try:
                vendor = (base / "idVendor").read_text(encoding="ascii").strip()
                product = (base / "idProduct").read_text(encoding="ascii").strip()
            except OSError as exc:
                raise RigError("configured USB hub is not present", 3) from exc
            if vendor != expected["vendor_id"] or product != expected["product_id"]:
                raise RigError("configured USB hub identity changed", 3)
            observed[generation] = {
                "sysfs_path": expected["sysfs_path"],
                "vendor_id": vendor,
                "product_id": product,
            }
        return observed

    def _device(self, role: str) -> dict:
        entry = role_config(self.config, role)
        serial = mapped_serial(Path(self.config["device_map"]), role)
        state_call = self._run([self.config["adb"], "-s", serial, "get-state"], 10)
        if state_call.returncode != 0 or state_call.stdout.strip() != "device":
            return {"role": role, "adb_state": "absent", "usb_path": None,
                    "path_matches": False, "battery": None}
        path_call = self._run([self.config["adb"], "-s", serial, "get-devpath"], 10)
        if path_call.returncode != 0:
            raise RigError("authorized device path is unavailable", 4)
        path = path_call.stdout.strip()
        battery_call = self._run(
            [self.config["adb"], "-s", serial, "shell", "dumpsys", "battery"], 15)
        if battery_call.returncode != 0:
            raise RigError("Android battery capture failed", 4)
        return {
            "role": role,
            "adb_state": "device",
            "usb_path": path,
            "path_matches": path == entry["usb_path"],
            "battery": parse_battery(battery_call.stdout),
        }

    def status(self) -> dict:
        roles = []
        for entry in self.config["roles"]:
            observed = self._device(entry["role"])
            observed["port_powered"] = self._hub_port_powered(
                entry["logical_port"])
            roles.append(observed)
        return {
            "schema_version": SCHEMA_VERSION,
            "captured_at_utc": _utc_now(),
            "hub": self.verify_hub(),
            "roles": roles,
        }

    def _inhibitors(self, role: str, allowed_run_id: str | None = None) -> list[str]:
        found = ["lease:" + item["lease_id"]
                 for item in self.list_inhibitors(role)]
        for root_value in self.config["inhibit_roots"]:
            root = Path(root_value)
            if not root.is_dir():
                continue
            for candidate in sorted(root.glob("*.partial")):
                result = candidate / "result.json"
                try:
                    if result.stat().st_size > MAX_STATE_BYTES:
                        raise ValueError
                    report = json.loads(result.read_text(encoding="utf-8"))
                    target = report.get("target")
                    report_role = target.get("role") if isinstance(target, dict) else None
                    run_id = report.get("run_id")
                except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                        AttributeError, ValueError):
                    found.append("unreadable-active-run")
                    continue
                if report_role == role and run_id != allowed_run_id:
                    found.append("active-run:" + str(run_id or "unknown"))
        return found

    def _inhibitor_root(self, role: str) -> Path:
        role_config(self.config, role)
        root = self._state_root() / "inhibitors" / role
        root.mkdir(parents=True, mode=0o750, exist_ok=True)
        if root.is_symlink():
            raise RigError("rig inhibitor root must not be a symbolic link", 3)
        metadata = root.stat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
                or metadata.st_uid != os.geteuid()):
            raise RigError("rig inhibitor root must be owner-controlled", 3)
        return root

    def list_inhibitors(self, role: str) -> list[dict]:
        root = self._inhibitor_root(role)
        records = []
        for path in sorted(root.glob("*.json")):
            try:
                raw, metadata = _read_bounded_regular(
                    path, MAX_STATE_BYTES, "rig inhibitor")
                value = json.loads(raw)
            except (RigError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RigError("rig inhibitor state is invalid", 5) from exc
            expected = {"schema_version", "role", "lease_id", "reason",
                        "created_at_utc"}
            if (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o037
                    or not isinstance(value, dict) or set(value) != expected
                    or value.get("schema_version") != SCHEMA_VERSION
                    or value.get("role") != role
                    or not isinstance(value.get("lease_id"), str)
                    or not REASON_RE.fullmatch(value["lease_id"])
                    or path.name != value["lease_id"] + ".json"
                    or not isinstance(value.get("reason"), str)
                    or not REASON_RE.fullmatch(value["reason"])
                    or not isinstance(value.get("created_at_utc"), str)):
                raise RigError("rig inhibitor state is invalid", 5)
            records.append(value)
        return records

    def acquire_inhibitor(self, role: str, lease_id: str, reason: str) -> dict:
        if (not isinstance(lease_id, str) or not REASON_RE.fullmatch(lease_id)
                or not isinstance(reason, str) or not REASON_RE.fullmatch(reason)):
            raise RigError("rig inhibitor requires stable lease and reason tokens")
        lock_fd = self._lock(role)
        try:
            path = self._inhibitor_root(role) / f"{lease_id}.json"
            value = {
                "schema_version": SCHEMA_VERSION,
                "role": role,
                "lease_id": lease_id,
                "reason": reason,
                "created_at_utc": _utc_now(),
            }
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_CLOEXEC", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            try:
                fd = os.open(path, flags, 0o640)
            except FileExistsError as exc:
                raise RigError("rig inhibitor lease already exists", 3) from exc
            try:
                payload = (json.dumps(value, sort_keys=True, indent=2)
                           + "\n").encode("utf-8")
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written < 1:
                        raise RigError("rig inhibitor write did not complete", 5)
                    remaining = remaining[written:]
                os.fsync(fd)
                os.fchmod(fd, 0o640)
            finally:
                os.close(fd)
            _sync_directory(path.parent)
            return {"status": "ACQUIRED", **value}
        finally:
            self._unlock(lock_fd)

    def release_inhibitor(self, role: str, lease_id: str) -> dict:
        if not isinstance(lease_id, str) or not REASON_RE.fullmatch(lease_id):
            raise RigError("rig inhibitor requires a stable lease token")
        lock_fd = self._lock(role)
        try:
            matches = [item for item in self.list_inhibitors(role)
                       if item["lease_id"] == lease_id]
            if len(matches) != 1:
                raise RigError("rig inhibitor lease is not active", 3)
            root = self._inhibitor_root(role)
            (root / f"{lease_id}.json").unlink()
            _sync_directory(root)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "RELEASED",
                "role": role,
                "lease_id": lease_id,
                "released_at_utc": _utc_now(),
            }
        finally:
            self._unlock(lock_fd)

    def _state_root(self) -> Path:
        root = Path(self.config["state_root"])
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if root.is_symlink():
            raise RigError("rig state root must not be a symbolic link", 3)
        metadata = root.stat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o027
                or metadata.st_uid != os.geteuid()):
            raise RigError("rig state root must be owner-controlled", 3)
        return root

    def _lock(self, role: str) -> int:
        lock_root = self._state_root() / ".locks"
        lock_root.mkdir(mode=0o750, exist_ok=True)
        fd = os.open(lock_root / f"{role}.lock", os.O_RDWR | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RigError("selected rig role is already locked", 3) from exc
        return fd

    @staticmethod
    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _intent_path(self, role: str) -> Path:
        ports = self._state_root() / "ports"
        ports.mkdir(mode=0o750, exist_ok=True)
        return ports / f"{role}.json"

    def _read_intent(self, role: str) -> dict | None:
        path = self._intent_path(role)
        if not path.exists():
            return None
        try:
            raw, metadata = _read_bounded_regular(
                path, MAX_STATE_BYTES, "rig port intent state")
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o037:
                raise RigError("rig port intent state is not owner-controlled", 5)
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RigError("rig port intent state is invalid", 5) from exc
        if (not isinstance(value, dict) or value.get("role") != role
                or not isinstance(value.get("intended_on"), bool)):
            raise RigError("rig port intent state is invalid", 5)
        return value

    def _write_intent(self, role: str, intended_on: bool, reason: str) -> dict:
        value = {
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "intended_on": intended_on,
            "reason": reason,
            "updated_at_utc": _utc_now(),
            "next_probe_not_before_epoch": (
                None if intended_on else time.time() + role_config(
                    self.config, role)["battery"]["off_probe_interval_seconds"]),
        }
        path = self._intent_path(role)
        temp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.chmod(0o640)
        temp.replace(path)
        _sync_directory(path.parent)
        return value

    @staticmethod
    def _parse_port_states(output: str, port: int) -> list[bool]:
        pattern = re.compile(rf"^\s*Port\s+{port}:\s+(.+?)\s*$")
        states = []
        for line in output.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            words = set(match.group(1).lower().split())
            if "power" in words:
                states.append(True)
            elif "off" in words:
                states.append(False)
            else:
                raise RigError("USB hub returned an ambiguous port state", 4)
        if not states:
            raise RigError("USB hub did not report the selected port", 4)
        return states

    def _hub_port_powered(self, port: int) -> bool:
        result = self._run([
            self.config["uhubctl"], "-N", "-l",
            self.config["hub"]["control_location"], "-p", str(port),
        ], 20)
        if result.returncode != 0:
            raise RigError("USB hub status query failed", 4)
        states = self._parse_port_states(result.stdout, port)
        if len(set(states)) != 1:
            raise RigError("USB2 and USB3 hub port power states disagree", 4)
        return states[0]

    def _hub_action(self, role: str, action: str) -> None:
        if action not in POWER_ACTIONS:
            raise RigError("unsupported rig power action")
        entry = role_config(self.config, role)
        result = self._run([
            self.config["uhubctl"], "-N", "-l",
            self.config["hub"]["control_location"], "-p",
            str(entry["logical_port"]), "-a", action,
        ], 20)
        if result.returncode != 0:
            raise RigError("USB hub rejected the requested port action", 4)
        observed = self._hub_port_powered(entry["logical_port"])
        if observed != (action == "on"):
            raise RigError("USB hub did not reach the requested power state", 4)

    def _wait_role(self, role: str, present: bool, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self._device(role)
            if (last["adb_state"] == "device") is present:
                return last
            time.sleep(0.25)
        raise RigError("USB port transition did not reach its requested state", 4)

    def set_power(self, role: str, action: str, reason: str,
                  *, allowed_run_id: str | None = None) -> dict:
        if action not in POWER_ACTIONS:
            raise RigError("unsupported rig power action")
        if not isinstance(reason, str) or not REASON_RE.fullmatch(reason):
            raise RigError("rig power action requires a stable reason token")
        if (allowed_run_id is not None
                and (not isinstance(allowed_run_id, str)
                     or not RUN_ID_RE.fullmatch(allowed_run_id))):
            raise RigError("allowed run id is invalid")
        role_config(self.config, role)
        lock_fd = self._lock(role)
        try:
            self.verify_hub()
            inhibitors = self._inhibitors(role, allowed_run_id)
            if inhibitors:
                raise RigError("active test state inhibits USB power changes", 3)
            before = {entry["role"]: self._device(entry["role"])
                      for entry in self.config["roles"]}
            target_before = before[role]
            if action == "off":
                if (target_before["adb_state"] != "device"
                        or not target_before["path_matches"]):
                    raise RigError("selected role is not authorized on its configured port", 3)
                self._hub_action(role, action)
                self._write_intent(role, False, reason)
                target_after = self._wait_role(role, False, 15)
            else:
                self._hub_action(role, action)
                self._write_intent(role, True, reason)
                target_after = self._wait_role(role, True, 30)
                if not target_after["path_matches"]:
                    raise RigError("selected role returned on an unexpected USB path", 4)
                if not target_after["battery"]["externally_powered"]:
                    raise RigError("selected role returned without verified external power", 4)
            for other_role, other_before in before.items():
                if other_role == role or other_before["adb_state"] != "device":
                    continue
                other_after = self._device(other_role)
                if (other_after["adb_state"] != "device"
                        or other_after["usb_path"] != other_before["usb_path"]):
                    raise RigError("USB action disturbed another configured role", 4)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "PASS",
                "role": role,
                "action": action,
                "reason": reason,
                "completed_at_utc": _utc_now(),
                "before": target_before,
                "after": target_after,
            }
        finally:
            self._unlock(lock_fd)

    def maintain(self, role: str) -> dict:
        entry = role_config(self.config, role)
        lock_fd = self._lock(role)
        try:
            self.verify_hub()
            if self._inhibitors(role):
                return {"status": "INHIBITED", "role": role,
                        "reason": "active-test-state"}
            intent = self._read_intent(role)
            port_powered = self._hub_port_powered(entry["logical_port"])
            observation = self._device(role)
            if not port_powered and observation["adb_state"] == "device":
                raise RigError("USB hub and ADB power observations disagree", 4)
            intended_on = port_powered if intent is None else intent["intended_on"]
            probing_off_hold = False
            if not port_powered and intended_on:
                self._hub_action(role, "on")
                self._write_intent(role, True, "intended-power-recovery")
                observation = self._wait_role(role, True, 30)
            elif not port_powered:
                due = intent is None or intent.get(
                    "next_probe_not_before_epoch") is None or time.time() >= intent[
                        "next_probe_not_before_epoch"]
                if not due:
                    return {"status": "HELD", "role": role,
                            "reason": "off-probe-not-due"}
                self._hub_action(role, "on")
                probing_off_hold = True
                try:
                    observation = self._wait_role(role, True, 30)
                except RigError:
                    self._hub_action(role, "off")
                    self._write_intent(role, False, "probe-target-unavailable")
                    return {"status": "HELD", "role": role,
                            "reason": "probe-target-unavailable"}
            if observation["adb_state"] != "device" or not observation["path_matches"]:
                if probing_off_hold:
                    self._hub_action(role, "off")
                    self._write_intent(role, False, "probe-identity-mismatch")
                raise RigError("powered role is unavailable on its configured path", 4)
            if not observation["battery"]["externally_powered"]:
                if probing_off_hold:
                    self._hub_action(role, "off")
                    self._write_intent(role, False, "probe-power-mismatch")
                raise RigError("powered role lacks verified external power", 4)
            decision = battery_decision(
                entry["battery"], observation["battery"], intended_on)
            if decision["action"] == "off":
                self._hub_action(role, "off")
                self._write_intent(role, False, decision["reason"])
                self._wait_role(role, False, 15)
            elif decision["action"] == "on":
                self._write_intent(role, True, decision["reason"])
            elif not intended_on:
                self._hub_action(role, "off")
                self._write_intent(role, False, "probe-complete-above-low-threshold")
                self._wait_role(role, False, 15)
            return {"status": "PASS", "role": role, "decision": decision,
                    "battery": observation["battery"]}
        finally:
            self._unlock(lock_fd)

    def acquire_test_start_lock(self, role: str) -> int:
        """Prepare one role for a test and retain its lock for partial creation."""
        entry = role_config(self.config, role)
        lock_fd = self._lock(role)
        try:
            self.verify_hub()
            if self._inhibitors(role):
                raise RigError(
                    "active test or operation inhibits test start", 3)
            before = {item["role"]: self._device(item["role"])
                      for item in self.config["roles"]}
            port_powered = self._hub_port_powered(entry["logical_port"])
            target = before[role]
            if not port_powered:
                if target["adb_state"] == "device":
                    raise RigError(
                        "USB hub and ADB power observations disagree", 4)
                self._hub_action(role, "on")
                self._write_intent(role, True, "test-start")
                target = self._wait_role(role, True, 30)
            if (target["adb_state"] != "device" or not target["path_matches"]
                    or not target["battery"]["externally_powered"]):
                raise RigError(
                    "selected test role is unavailable on its configured port", 3)
            for other_role, other_before in before.items():
                if other_role == role or other_before["adb_state"] != "device":
                    continue
                other_after = self._device(other_role)
                if (other_after["adb_state"] != "device"
                        or other_after["usb_path"] != other_before["usb_path"]):
                    raise RigError(
                        "test-start power recovery disturbed another configured role",
                        4)
            return lock_fd
        except Exception:
            self._unlock(lock_fd)
            raise


class TestStartGuard:
    """Short role lock held until a test has created its partial state."""

    def __init__(self, controller: RigController | None = None,
                 lock_fd: int | None = None):
        self.controller = controller
        self.lock_fd = lock_fd

    def release(self) -> None:
        if self.controller is not None and self.lock_fd is not None:
            self.controller._unlock(self.lock_fd)
            self.lock_fd = None


def controller_for_target(config_path: str, role: str,
                          device_map: str, target: str) -> RigController:
    """Load a runtime config and bind it to the test's private role target."""
    config = load_config(Path(config_path), runtime=True)
    role_config(config, role)
    try:
        configured_map = Path(config["device_map"]).resolve(strict=True)
        selected_map = Path(device_map).resolve(strict=True)
    except OSError as exc:
        raise RigError(
            "rig test guard cannot resolve its private device map", 3) from exc
    if configured_map != selected_map:
        raise RigError("rig and test device maps do not match", 3)
    if mapped_serial(configured_map, role) != target:
        raise RigError("rig role does not match the selected private target", 3)
    return RigController(config)


def acquire_test_start_guard(config_path: str | None, role: str,
                             device_map: str, target: str) -> TestStartGuard:
    """Serialize a test's transition into detectable ``.partial`` state."""
    if config_path is None:
        return TestStartGuard()
    controller = controller_for_target(config_path, role, device_map, target)
    lock_fd = controller.acquire_test_start_lock(role)
    return TestStartGuard(controller, lock_fd)


def dry_run(config: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "operations": [
            "validate", "status", "power", "maintain", "inhibit",
        ],
        "power_actions": sorted(POWER_ACTIONS),
        "cycle_action_supported": False,
        "identity_source": "private role-to-ADB map",
        "configured_roles": [entry["role"] for entry in config["roles"]],
        "writes": "none",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diamaneos rig",
        description="identity-bound USB test-rig control")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "dry-run", "status"):
        item = sub.add_parser(name)
        item.add_argument("--config", required=True)
    power = sub.add_parser("power")
    power.add_argument("--config", required=True)
    power.add_argument("--role", required=True)
    power.add_argument(
        "--action", dest="power_action", required=True,
        choices=sorted(POWER_ACTIONS))
    power.add_argument("--reason", required=True)
    power.add_argument("--allowed-run-id")
    maintain = sub.add_parser("maintain")
    maintain.add_argument("--config", required=True)
    maintain.add_argument("--role")
    inhibit = sub.add_parser("inhibit")
    inhibit_sub = inhibit.add_subparsers(dest="inhibit_action", required=True)
    for name in ("list", "release"):
        item = inhibit_sub.add_parser(name)
        item.add_argument("--config", required=True)
        item.add_argument("--role", required=True)
        if name == "release":
            item.add_argument("--lease-id", required=True)
    acquire = inhibit_sub.add_parser("acquire")
    acquire.add_argument("--config", required=True)
    acquire.add_argument("--role", required=True)
    acquire.add_argument("--lease-id", required=True)
    acquire.add_argument("--reason", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(Path(args.config), runtime=args.action not in {
            "validate", "dry-run"})
        if args.action == "validate":
            print("VALID rig config: " + str(len(config["roles"])) + " roles")
            return 0
        if args.action == "dry-run":
            print(json.dumps(dry_run(config), indent=2, sort_keys=True))
            return 0
        controller = RigController(config)
        if args.action == "status":
            result = controller.status()
        elif args.action == "power":
            result = controller.set_power(
                args.role, args.power_action, args.reason,
                allowed_run_id=args.allowed_run_id)
        elif args.action == "inhibit":
            if args.inhibit_action == "list":
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "role": args.role,
                    "inhibitors": controller.list_inhibitors(args.role),
                }
            elif args.inhibit_action == "acquire":
                result = controller.acquire_inhibitor(
                    args.role, args.lease_id, args.reason)
            else:
                result = controller.release_inhibitor(
                    args.role, args.lease_id)
        else:
            roles = [args.role] if args.role else [
                entry["role"] for entry in config["roles"]]
            result = {"schema_version": SCHEMA_VERSION,
                      "results": [controller.maintain(role) for role in roles]}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except RigError as exc:
        print(str(exc), file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
