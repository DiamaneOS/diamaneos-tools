"""Fail-closed argument boundary for passwordless test-host workflows.

The ordinary ``bin/diamaneos`` CLI intentionally keeps development overrides
such as ``--adb`` and explicit fixture/configuration paths.  Those controls are
not suitable for a sudoers wildcard.  The deployed sudo policy therefore
delegates only this wrapper, which accepts reviewed routes, fixes executable
and configuration identities, and confines private file access to the test
account's state root before executing the ordinary CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys


DEPLOY_ROOT = Path("/opt/diamaneos/tools")
DIAMANEOS = DEPLOY_ROOT / "bin/diamaneos"
PYTHON = Path("/usr/bin/python3")
PRIVATE_ROOT = Path("/var/lib/diamaneos-test")
DEVICE_MAP = PRIVATE_ROOT / "devices/test-host.json"
RIG_CONFIG = Path("/etc/diamaneos/rig.json")
BASELINE_CONFIG = DEPLOY_ROOT / "config/baseline.json"
CARRIER_MATRIX = DEPLOY_ROOT / "config/carrier-matrix.json"
ENDPOINT_INVENTORY = DEPLOY_ROOT / "config/endpoints.json"

MAX_ARGUMENTS = 128
MAX_ARGUMENT_BYTES = 65_536
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DelegationError(ValueError):
    """The requested invocation is outside the delegated boundary."""


# The first tuple is consumed by bin/diamaneos.  Remaining bare tokens must be
# one of the exact action sequences listed for that route.  In particular the
# internal idle-series worker is never exposed through sudo.
ROUTES = {
    ("baseline", "capture"): {()},
    ("baseline", "pilot"): {()},
    ("baseline", "boot"): {("dry-run",), ("restart",)},
    ("baseline", "connected"): {("dry-run",), ("run",)},
    ("baseline", "camera"): {
        ("dry-run",), ("start",), ("capture",), ("finalize",),
        ("quarantine",),
    },
    ("baseline", "idle"): {
        ("dry-run",), ("start",), ("observe-disconnect",), ("status",),
        ("finish",), ("quarantine",), ("series", "launch"),
        ("series", "status"),
    },
    ("baseline", "protocol", "validate"): {()},
    ("carrier", "matrix", "validate"): {()},
    ("endpoints", "validate"): {()},
    ("test", "run"): {()},
    ("rig",): {
        ("validate",), ("dry-run",), ("status",), ("power",),
        ("maintain",), ("inhibit", "list"),
        ("inhibit", "acquire"), ("inhibit", "release"),
    },
}

# Every accepted long option is spelled in full so argparse abbreviation can
# never turn an apparently different argument into a privileged override.
FLAG_OPTIONS = {
    "--dry-run",
    "--declared",
    "--wait-for-reconnect",
    "--operator-authorized-batterystats-reset",
    "--operator-authorized-reboots",
    "--operator-authorized-repeat-1-batterystats-reset",
    "--operator-authorized-repeat-2-batterystats-reset",
    "--operator-committed-no-interaction",
    "--operator-confirmed-box-closed",
    "--operator-confirmed-defaults",
    "--operator-confirmed-display-50",
    "--operator-confirmed-fixture",
    "--operator-confirmed-lamp",
    "--operator-confirmed-no-interaction",
    "--operator-confirmed-no-known-network-outage",
    "--operator-confirmed-permanent-state",
    "--operator-confirmed-physical-disconnect",
    "--operator-confirmed-ui",
    "--operator-confirmed-unlocked",
    "--operator-declared-no-planned-network-outage",
}

VALUE_OPTIONS = {
    "--action",
    "--allowed-run-id",
    "--conditions",
    "--declared-repeat-index",
    "--device-role",
    "--disconnect-method",
    "--evidence-kind",
    "--expected-build",
    "--expected-spec-sha256",
    "--focus-x",
    "--focus-y",
    "--lease-id",
    "--reason",
    "--reconnect-timeout-seconds",
    "--repeat-index",
    "--role",
    "--run-id",
    "--series-id",
    "--stage",
    "--status",
    "--suite",
    "--target",
    "--timeout",
    "--timeout-seconds",
}

PRIVATE_PATH_OPTIONS = {
    "--candidate",
    "--job-dir",
    "--job-root",
    "--output",
    "--raw-dir",
    "--rerun-from",
    "--run-dir",
}

FIXED_PATH_OPTIONS = {
    "--device-map": {DEVICE_MAP},
    "--rig-config": {RIG_CONFIG},
    "--config": {BASELINE_CONFIG, RIG_CONFIG},
    "--matrix": {CARRIER_MATRIX},
    "--inventory": {ENDPOINT_INVENTORY},
}

# These development controls must remain available only through an ordinary,
# password-gated administrator invocation, never the NOPASSWD wrapper.
FORBIDDEN_OPTIONS = {"--adb", "--destructive", "--fixture", "--services"}


def _route(argv: list[str]) -> tuple[tuple[str, ...], int]:
    for prefix in sorted(ROUTES, key=len, reverse=True):
        if tuple(argv[:len(prefix)]) == prefix:
            return prefix, len(prefix)
    raise DelegationError("command is not a reviewed delegated route")


def _private_path(value: str) -> None:
    path = Path(value)
    if not path.is_absolute():
        raise DelegationError("delegated private paths must be absolute")
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(PRIVATE_ROOT.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise DelegationError(
            "delegated private path escapes /var/lib/diamaneos-test") from exc


def _fixed_path(option: str, value: str, route: tuple[str, ...]) -> None:
    path = Path(value)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise DelegationError(f"{option} is not a trusted fixed path") from exc
    configured = FIXED_PATH_OPTIONS[option]
    if option == "--config":
        configured = {RIG_CONFIG} if route == ("rig",) else {BASELINE_CONFIG}
    allowed = {item.resolve(strict=False) for item in configured}
    if resolved not in allowed:
        raise DelegationError(f"{option} is not a trusted fixed path")


def validate_argv(argv: list[str]) -> list[str]:
    """Return a validated copy or raise before any delegated program runs."""
    argv = list(argv)
    if (not argv or len(argv) > MAX_ARGUMENTS
            or sum(len(item.encode("utf-8")) for item in argv)
            > MAX_ARGUMENT_BYTES):
        raise DelegationError("delegated argument list is empty or too large")
    if any("\x00" in item for item in argv):
        raise DelegationError("delegated argument contains NUL")

    route, index = _route(argv)
    actions: list[str] = []
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            index += 1
            continue
        if not item.startswith("--"):
            actions.append(item)
            index += 1
            continue

        option, separator, inline_value = item.partition("=")
        if option in FORBIDDEN_OPTIONS:
            raise DelegationError(
                f"{option} is not available through passwordless delegation")
        if option in FLAG_OPTIONS:
            if separator:
                raise DelegationError(f"{option} does not take a value")
            index += 1
            continue
        if (option not in VALUE_OPTIONS
                and option not in PRIVATE_PATH_OPTIONS
                and option not in FIXED_PATH_OPTIONS):
            raise DelegationError(
                f"{option} is not a reviewed delegated option")

        if separator:
            value = inline_value
        else:
            index += 1
            if index >= len(argv) or argv[index].startswith("--"):
                raise DelegationError(f"{option} requires a value")
            value = argv[index]
        if not value:
            raise DelegationError(f"{option} requires a non-empty value")
        if option in PRIVATE_PATH_OPTIONS:
            _private_path(value)
        elif option in FIXED_PATH_OPTIONS:
            _fixed_path(option, value, route)
        elif option == "--suite" and not TOKEN_RE.fullmatch(value):
            raise DelegationError(
                "delegated suites must be reviewed names, not paths")
        elif option == "--expected-spec-sha256" and not SHA256_RE.fullmatch(value):
            raise DelegationError("expected series specification hash is invalid")
        index += 1

    if tuple(actions) not in ROUTES[route]:
        raise DelegationError("command action is not delegated")
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        validated = validate_argv(argv)
    except DelegationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    environment = {
        "HOME": str(PRIVATE_ROOT),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "USER": "diamaneos-test",
        "LOGNAME": "diamaneos-test",
    }
    os.execve(
        str(PYTHON),
        [str(PYTHON), str(DIAMANEOS), *validated],
        environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
