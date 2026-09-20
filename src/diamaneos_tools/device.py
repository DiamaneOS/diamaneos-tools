"""USB identity capture without workflow orchestration."""
from pathlib import Path
import re
from .errors import RunnerError
from .evidence import write_evidence
from .process import run_bounded
DEFAULT_TIMEOUT_SECONDS = 20

IDENTITY_PROPERTIES = {
    "model": "ro.product.model",
    "device": "ro.product.device",
    "build_id": "ro.build.id",
    "incremental": "ro.build.version.incremental",
    "build_type": "ro.build.type",
    "security_patch": "ro.build.version.security_patch",
    "firmware": "gsm.version.baseband",
}

# The quoted-target form (adb: device '<serial>' not found) must match even
# though the serial interrupts the contiguous phrase.
DEVICE_GONE = [
    r"device\s+('[^']*'\s+)?not found",
    r"no devices?\b",
    r"device\s+offline",
    r"unauthorized device",
    r"no permissions",
]

def is_device_gone(text):
    low = (text or "").lower()
    return any(re.search(p, low) for p in DEVICE_GONE)


def evidence_label(build_type: str) -> str:
    if build_type == "user":
        return "USER_BUILD_EVIDENCE"
    if build_type == "userdebug":
        return "USERDEBUG_DIAGNOSTIC_EVIDENCE"
    return "NON_USER_DIAGNOSTIC_EVIDENCE"

def adb_version(adb: str, executor=run_bounded) -> str:
    result = executor([adb, "version"], 10)
    if result.get("transport") != "ok":
        return "unknown"
    lines = result.get("stdout", "").splitlines()
    return " | ".join(lines[:3])[:500] or "unknown"

def authorized_devices(adb: str, executor=run_bounded) -> list[str]:
    result = executor([adb, "devices"], DEFAULT_TIMEOUT_SECONDS)
    if result.get("transport") != "ok":
        raise RunnerError("unable to enumerate authorized USB devices", 3)
    devices = []
    for line in result.get("stdout", "").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            devices.append(fields[0])
    return devices

def capture_identity(adb: str, target: str, run_dir: Path,
                      timeout: int, executor=run_bounded) -> tuple[dict, list[str]]:
    identity = {}
    refs = []
    identity_dir = run_dir / "raw" / "identity"
    identity_dir.mkdir(parents=True, mode=0o750)
    for field, prop in IDENTITY_PROPERTIES.items():
        result = executor([adb, "-s", target, "shell", "getprop", prop], timeout)
        if result.get("transport") != "ok":
            combined = result.get("stdout", "") + "\n" + result.get("stderr", "")
            if is_device_gone(combined):
                raise RunnerError("device became unavailable during identity capture", 3)
            raise RunnerError("device/build identity capture failed", 5)
        value = result.get("stdout", "").strip()
        if not value and field != "firmware":
            raise RunnerError("required device/build identity is empty", 5)
        identity[field] = value or "not-reported"
        filename = f"{field}.stdout.txt"
        digest = write_evidence(identity_dir / filename, result.get("stdout", ""))
        refs.append(f"raw/identity/{filename}@sha256:{digest}")
    identity["evidence_label"] = evidence_label(identity["build_type"])
    return identity, refs
