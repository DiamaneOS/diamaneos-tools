"""Bounded, target-bound device test runner.

Suites are validated data, never shell programs.  The v1 executor supports a
small allowlist of read-only ADB shell commands and one controlled temporary
file round trip with mandatory cleanup.  Destructive cases name a reviewed
installer/runbook boundary; this module gates and reports them but does not
contain flash or wipe recipes.
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
import signal
import stat
import sys
import time

from diamaneos_tools import baseline
from diamaneos_tools import rig

from .errors import RunnerError, CommandInterrupted
from .process import run_bounded, terminate_group as _terminate_group
from .device import IDENTITY_PROPERTIES
from .evidence import sha256_bytes as sha256_bytes
from .evidence import read_bounded as _read_bounded
from .evidence import load_unique_json as _load_unique_json
from .evidence import atomic_json as _atomic_json
from .evidence import sync_directory as _sync_directory
from .evidence import write_evidence as _write_evidence
from .evidence import verify_evidence_refs as _verify_evidence_refs
from .evidence import git_revision as _git_revision
from .device import evidence_label as _evidence_label
from .device import adb_version as _adb_version
from .device import authorized_devices as _authorized_devices
from .device import capture_identity as _capture_identity


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 262_144
MAX_CASE_OUTPUT_BYTES = 1_048_576
MAX_SUITE_BYTES = 262_144
MAX_MAP_BYTES = 65_536
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,95}$")
STAGES = ("inspect", "smoke", "security", "destructive")
TERMINAL_CASE_STATUSES = {
    "PASS", "FAIL", "SKIP", "BLOCKED", "NOT_RUN", "HARNESS_ERROR",
    "INCOMPLETE", "NOT_APPLICABLE",
}

# Exact argv after ``adb -s <private target> shell``.  Adding an operation is
# a source review, not something a suite file can request dynamically.
READ_ONLY_ADB_ALLOWLIST = {
    ("getprop", "ro.product.model"),
    ("getprop", "ro.product.device"),
    ("getprop", "ro.build.id"),
    ("getprop", "ro.build.version.incremental"),
    ("getprop", "ro.build.type"),
    ("getprop", "ro.build.version.security_patch"),
    ("getprop", "gsm.version.baseband"),
    ("getprop", "sys.boot_completed"),
    ("dumpsys", "battery"),
    ("dumpsys", "carrier_config"),
    ("dumpsys", "imsservice"),
    ("dumpsys", "phone"),
    ("dumpsys", "telephony.registry"),
    ("getenforce",),
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _expect_keys(value: dict, required: set[str], allowed: set[str], label: str):
    if not isinstance(value, dict):
        raise RunnerError(f"{label} must be an object", 2)
    missing = required - set(value)
    extra = set(value) - allowed
    if missing:
        raise RunnerError(f"{label} is missing required fields", 2)
    if extra:
        raise RunnerError(f"{label} contains unknown fields", 2)


def validate_suite(suite: object) -> dict:
    """Fail-closed stdlib validation for the consumed suite contract."""
    required = {"schema_version", "suite_id", "description", "cases"}
    _expect_keys(suite, required, required, "suite")
    if suite["schema_version"] != 1:
        raise RunnerError("unsupported suite schema version", 2)
    if not isinstance(suite["suite_id"], str) or not TOKEN_RE.fullmatch(suite["suite_id"]):
        raise RunnerError("invalid suite id", 2)
    if not isinstance(suite["description"], str) or not (1 <= len(suite["description"]) <= 1000):
        raise RunnerError("invalid suite description", 2)
    if not isinstance(suite["cases"], list) or not (1 <= len(suite["cases"]) <= 256):
        raise RunnerError("suite cases must contain 1 to 256 entries", 2)

    seen = set()
    for index, case in enumerate(suite["cases"]):
        label = f"case {index + 1}"
        common = {
            "test_id", "requirement_ids", "stage", "adapter",
            "preconditions", "expected", "applicability", "timeout_seconds",
        }
        _expect_keys(
            case, common,
            common | {"argv", "runbook_ref", "output_limit_bytes"}, label)
        case_id = case["test_id"]
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise RunnerError(f"{label} has an invalid test id", 2)
        if case_id in seen:
            raise RunnerError("suite has duplicate test ids", 2)
        seen.add(case_id)
        reqs = case["requirement_ids"]
        if (not isinstance(reqs, list) or not (1 <= len(reqs) <= 32)
                or any(not isinstance(item, str) or not CASE_ID_RE.fullmatch(item)
                       for item in reqs) or len(set(reqs)) != len(reqs)):
            raise RunnerError(f"{label} has invalid requirement ids", 2)
        if case["stage"] not in STAGES:
            raise RunnerError(f"{label} has an invalid stage", 2)
        if (not isinstance(case["preconditions"], list)
                or not (1 <= len(case["preconditions"]) <= 32)
                or any(not isinstance(item, str) or not (1 <= len(item) <= 500)
                       for item in case["preconditions"])):
            raise RunnerError(f"{label} has invalid preconditions", 2)
        expected = case["expected"]
        _expect_keys(expected, {"oracle", "description"},
                     {"oracle", "description", "value"}, f"{label} expected")
        if expected["oracle"] not in {"exit-zero", "nonempty", "equals", "contains"}:
            raise RunnerError(f"{label} has an invalid oracle", 2)
        if (not isinstance(expected["description"], str)
                or not (1 <= len(expected["description"]) <= 500)):
            raise RunnerError(f"{label} has an invalid expected description", 2)
        if expected["oracle"] in {"equals", "contains"}:
            if not isinstance(expected.get("value"), str) or len(expected["value"]) > 500:
                raise RunnerError(f"{label} oracle requires a bounded value", 2)
        elif "value" in expected:
            raise RunnerError(f"{label} oracle does not accept a value", 2)
        applicability = case["applicability"]
        _expect_keys(applicability, {"kind"}, {"kind", "skip_reason"},
                     f"{label} applicability")
        if applicability["kind"] not in {"required", "optional"}:
            raise RunnerError(f"{label} has invalid applicability", 2)
        if applicability["kind"] == "optional":
            if (not isinstance(applicability.get("skip_reason"), str)
                    or not (1 <= len(applicability["skip_reason"]) <= 500)):
                raise RunnerError(f"{label} optional case requires a skip reason", 2)
        elif "skip_reason" in applicability:
            raise RunnerError(f"{label} required case cannot declare a skip reason", 2)
        timeout = case["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not (1 <= timeout <= 300):
            raise RunnerError(f"{label} has an invalid timeout", 2)

        adapter = case["adapter"]
        if adapter == "adb-shell-read-only":
            if case["stage"] == "destructive" or "runbook_ref" in case:
                raise RunnerError("read-only adapter cannot be a destructive case", 2)
            argv = case.get("argv")
            if (not isinstance(argv, list) or not argv
                    or any(not isinstance(arg, str) or not (1 <= len(arg) <= 128)
                           for arg in argv)):
                raise RunnerError(f"{label} has invalid argv", 2)
            if tuple(argv) not in READ_ONLY_ADB_ALLOWLIST:
                raise RunnerError(f"{label} command is not allowlisted", 2)
            output_limit = case.get("output_limit_bytes", MAX_OUTPUT_BYTES)
            if (isinstance(output_limit, bool) or not isinstance(output_limit, int)
                    or not (1 <= output_limit <= MAX_CASE_OUTPUT_BYTES)):
                raise RunnerError(f"{label} has an invalid output limit", 2)
        elif adapter == "adb-temp-file-roundtrip":
            if (case["stage"] != "smoke" or "argv" in case
                    or "output_limit_bytes" in case
                    or "runbook_ref" in case
                    or case["expected"]["oracle"] != "exit-zero"):
                raise RunnerError("temporary-file adapter requires a smoke exit-zero case", 2)
        elif adapter == "installer-runbook":
            if (case["stage"] != "destructive" or "argv" in case
                    or "output_limit_bytes" in case):
                raise RunnerError("installer-runbook is restricted to destructive cases", 2)
            ref = case.get("runbook_ref")
            if (not isinstance(ref, str) or not (1 <= len(ref) <= 500)
                    or ref.startswith("/") or ".." in Path(ref).parts):
                raise RunnerError(f"{label} has an invalid runbook reference", 2)
        else:
            raise RunnerError(f"{label} has an invalid adapter", 2)
    return suite


def load_suite(value: str, repo_root: Path) -> tuple[dict, str, Path]:
    if TOKEN_RE.fullmatch(value):
        path = repo_root / "tests" / "device" / "suites" / f"{value}.json"
    else:
        path = Path(value)
    suite, raw = _load_unique_json(path, MAX_SUITE_BYTES)
    return validate_suite(suite), sha256_bytes(raw), path


def load_device_map(path: Path, role: str, target: str) -> dict:
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise RunnerError("private device map is unreadable", 2) from exc
    if not stat.S_ISREG(mode) or mode & 0o037:
        raise RunnerError("private device map permissions must be 0640 or stricter", 2)
    mapping, _ = _load_unique_json(path, MAX_MAP_BYTES)
    required = {"schema_version", "devices"}
    _expect_keys(mapping, required, required, "device map")
    if mapping["schema_version"] != 1:
        raise RunnerError("unsupported device-map schema version", 2)
    devices = mapping["devices"]
    if not isinstance(devices, list) or not (1 <= len(devices) <= 32):
        raise RunnerError("device map has invalid devices", 2)
    matches = []
    seen_roles = set()
    seen_targets = set()
    for item in devices:
        fields = {"role", "adb_serial", "disposable"}
        _expect_keys(item, fields, fields, "device-map entry")
        if not isinstance(item["role"], str) or not TOKEN_RE.fullmatch(item["role"]):
            raise RunnerError("device map has an invalid role", 2)
        if (not isinstance(item["adb_serial"], str)
                or not (4 <= len(item["adb_serial"]) <= 256)
                or any(ch.isspace() for ch in item["adb_serial"])):
            raise RunnerError("device map has an invalid target", 2)
        if not isinstance(item["disposable"], bool):
            raise RunnerError("device map has an invalid disposable flag", 2)
        if item["role"] in seen_roles or item["adb_serial"] in seen_targets:
            raise RunnerError("device map has duplicate roles or targets", 2)
        seen_roles.add(item["role"])
        seen_targets.add(item["adb_serial"])
        if item["role"] == role:
            matches.append(item)
    if len(matches) != 1 or matches[0]["adb_serial"] != target:
        # Never echo a private target or mapped serial in an error.
        raise RunnerError("explicit target does not match the selected private role", 3)
    return matches[0]


def _safe_observation(case: dict, result: dict) -> str:
    transport = result["transport"]
    if transport == "ok":
        argv = tuple(case.get("argv", []))
        if argv and argv[0] == "dumpsys":
            kept, kept_count, dropped, sensitive, capped = baseline.extract_fields(
                argv, result.get("stdout", ""))
            detail = baseline.redact(kept).strip()
            suffix = (f" [kept={kept_count}, dropped={dropped}, "
                      f"sensitive-dropped={sensitive}, capped={str(capped).lower()}]")
            return ((detail or "command completed with no public-safe fields")
                    + suffix)[:500]
        detail = baseline.redact(result.get("stdout", "")).strip()
        return (detail or "command completed with empty output")[:500]
    if baseline._is_device_gone(result.get("stdout", "") + "\n" + result.get("stderr", "")):
        return "device became unavailable during the case"
    return result.get("reason", "command did not complete")


def _oracle_passed(expected: dict, stdout: str) -> bool:
    oracle = expected["oracle"]
    normalized = stdout.strip()
    if oracle == "exit-zero":
        return True
    if oracle == "nonempty":
        return bool(normalized)
    if oracle == "equals":
        return normalized == expected["value"]
    if oracle == "contains":
        return expected["value"] in stdout
    return False


def _case_base(case: dict, identity: dict, evidence_kind: str) -> dict:
    return {
        "test_id": case["test_id"],
        "requirement_ids": case["requirement_ids"],
        "stage": case["stage"],
        "preconditions": case["preconditions"],
        "expected": case["expected"]["description"],
        "observed": "not run",
        "status": "NOT_RUN",
        "reason": "not started",
        "build": identity["incremental"],
        "firmware": identity["firmware"],
        "build_type": identity["build_type"],
        "evidence_kind": evidence_kind,
        "duration_ms": 0,
        "redacted_evidence_refs": [],
        "raw_evidence_refs": [],
    }


def _temporary_roundtrip(case: dict, run_id: str, run_dir: Path, adb: str,
                         target: str, executor=run_bounded) -> tuple[dict, dict, bool]:
    """Create, verify and always attempt to remove one controlled test file."""
    payload = b"DiamaneOS temporary harness payload\n"
    raw_dir = run_dir / "raw"
    input_name = f"{case['test_id']}.synthetic-input.txt"
    input_path = raw_dir / input_name
    input_hash = _write_evidence(input_path, payload.decode("ascii"))
    token = hashlib.sha256(f"{run_id}:{case['test_id']}".encode()).hexdigest()[:24]
    remote_path = f"/data/local/tmp/diamaneos-harness-{token}"
    streams = {input_name: payload.decode("ascii")}
    total_ms = 0
    interrupted = False
    primary = None

    try:
        push = executor([adb, "-s", target, "push", str(input_path), remote_path],
                        case["timeout_seconds"])
        total_ms += push.get("duration_ms", 0)
        streams[f"{case['test_id']}.push.stdout.txt"] = push.get("stdout", "")
        streams[f"{case['test_id']}.push.stderr.txt"] = push.get("stderr", "")
        if push.get("transport") != "ok":
            primary = push
        else:
            readback = executor([adb, "-s", target, "shell", "cat", remote_path],
                                case["timeout_seconds"])
            total_ms += readback.get("duration_ms", 0)
            streams[f"{case['test_id']}.read.stdout.txt"] = readback.get("stdout", "")
            streams[f"{case['test_id']}.read.stderr.txt"] = readback.get("stderr", "")
            if (readback.get("transport") == "ok"
                    and readback.get("stdout", "").encode("utf-8", "replace") != payload):
                primary = dict(readback)
                primary.update(transport="error",
                               reason="temporary-file round trip changed the payload")
            else:
                primary = readback
    except CommandInterrupted as exc:
        primary = exc.result
        interrupted = True
    finally:
        cleanup_results = []
        for label, command in (
                ("remove", [adb, "-s", target, "shell", "rm", "-f", remote_path]),
                ("verify-removed", [adb, "-s", target, "shell", "test", "!", "-e",
                                    remote_path])):
            try:
                cleanup = executor(command, case["timeout_seconds"])
            except CommandInterrupted as exc:
                cleanup = exc.result
                interrupted = True
            total_ms += cleanup.get("duration_ms", 0)
            streams[f"{case['test_id']}.{label}.stdout.txt"] = cleanup.get("stdout", "")
            streams[f"{case['test_id']}.{label}.stderr.txt"] = cleanup.get("stderr", "")
            cleanup_results.append(cleanup)
        if any(item.get("transport") != "ok" for item in cleanup_results):
            combined = "\n".join(item.get("stdout", "") + item.get("stderr", "")
                                 for item in cleanup_results)
            primary = {
                "transport": "cleanup-error",
                "stdout": combined,
                "stderr": "",
                "reason": "temporary device data cleanup could not be verified",
                "duration_ms": total_ms,
            }

    if primary is None:
        primary = {"transport": "error", "stdout": "", "stderr": "",
                   "reason": "temporary-file adapter produced no result"}
    primary["duration_ms"] = total_ms
    primary["synthetic_input_ref"] = f"raw/{input_name}@sha256:{input_hash}"
    return primary, streams, interrupted


def _run_case(case: dict, identity: dict, run_dir: Path, adb: str,
              target: str, index: int, evidence_kind: str, run_id: str,
              executor=run_bounded) -> dict:
    result_case = _case_base(case, identity, evidence_kind)
    if case["adapter"] == "installer-runbook":
        result_case.update({
            "status": "BLOCKED",
            "observed": "destructive execution is outside this runner",
            "reason": "use the reviewed installer/runbook as a separate operator action",
            "redacted_evidence_refs": [f"result.json#/cases/{index}"],
        })
        return result_case

    extra_streams = {}
    if case["adapter"] == "adb-temp-file-roundtrip":
        transport, extra_streams, interrupted = _temporary_roundtrip(
            case, run_id, run_dir, adb, target, executor)
    else:
        command = [adb, "-s", target, "shell"] + case["argv"]
        try:
            transport = executor(
                command, case["timeout_seconds"],
                case.get("output_limit_bytes", MAX_OUTPUT_BYTES))
        except CommandInterrupted as exc:
            transport = exc.result
            interrupted = True
        else:
            interrupted = False

    raw_dir = run_dir / "raw"
    stdout_name = f"{case['test_id']}.stdout.txt"
    stderr_name = f"{case['test_id']}.stderr.txt"
    out_hash = _write_evidence(raw_dir / stdout_name, transport.get("stdout", ""))
    err_hash = _write_evidence(raw_dir / stderr_name, transport.get("stderr", ""))
    result_case["raw_evidence_refs"] = [
        f"raw/{stdout_name}@sha256:{out_hash}",
        f"raw/{stderr_name}@sha256:{err_hash}",
    ]
    if transport.get("synthetic_input_ref"):
        result_case["raw_evidence_refs"].append(transport["synthetic_input_ref"])
    for filename, content in extra_streams.items():
        # The synthetic input was already written before the ADB push.
        if filename == f"{case['test_id']}.synthetic-input.txt":
            continue
        digest = _write_evidence(raw_dir / filename, content)
        result_case["raw_evidence_refs"].append(
            f"raw/{filename}@sha256:{digest}")
    result_case["redacted_evidence_refs"] = [f"result.json#/cases/{index}"]
    result_case["duration_ms"] = transport.get("duration_ms", 0)
    result_case["observed"] = _safe_observation(case, transport)

    combined = transport.get("stdout", "") + "\n" + transport.get("stderr", "")
    if interrupted:
        result_case.update(status="INCOMPLETE",
                           reason="run interrupted; partial raw streams retained")
        raise CommandInterrupted({"case": result_case})
    if baseline._is_device_gone(combined):
        result_case.update(status="BLOCKED", reason="target disconnected")
    elif transport["transport"] in {
            "timeout", "overflow", "tool-missing", "cleanup-error"}:
        result_case.update(status="HARNESS_ERROR", reason=transport["reason"])
    elif transport["transport"] != "ok":
        if (case["applicability"]["kind"] == "optional"
                and baseline._is_unsupported_text(combined)):
            result_case.update(status="SKIP",
                               observed="test capability is unavailable",
                               reason=case["applicability"]["skip_reason"])
        else:
            result_case.update(status="FAIL", reason="command failed")
    elif (case["applicability"]["kind"] == "optional"
          and (baseline._is_unsupported_text(combined)
               or not transport.get("stdout", "").strip())):
        result_case.update(status="SKIP", observed="test capability is unavailable",
                           reason=case["applicability"]["skip_reason"])
    elif _oracle_passed(case["expected"], transport.get("stdout", "")):
        result_case.update(status="PASS", reason="oracle satisfied")
    else:
        result_case.update(status="FAIL", reason="oracle rejected the observation")
    return result_case


def _candidate_record(path_value: str | None) -> dict:
    if not path_value:
        return {
            "kind": "installed-device-build",
            "manifest_name": None,
            "manifest_sha256": None,
            "reason": "no candidate manifest supplied; device properties identify the installed build",
        }
    path = Path(path_value)
    data = _read_bounded(path, MAX_CANDIDATE_BYTES)
    return {
        "kind": "candidate-manifest",
        "manifest_name": path.name,
        "manifest_sha256": sha256_bytes(data),
        "reason": None,
    }


def _load_retry(path_value: str | None, suite: dict, suite_sha256: str,
                candidate: dict, role: str, evidence_kind: str,
                run_id: str) -> tuple[str | None, set[str] | None, dict | None]:
    if not path_value:
        return None, None, None
    report_path = Path(path_value)
    previous, _ = _load_unique_json(report_path, MAX_REPORT_BYTES)
    expected = [case["test_id"] for case in suite["cases"]]
    if (not isinstance(previous, dict)
            or previous.get("schema_version") != SCHEMA_VERSION
            or previous.get("operation") != "device-test-run"
            or previous.get("suite", {}).get("sha256") != suite_sha256
            or previous.get("expected_case_ids") != expected
            or previous.get("candidate") != candidate
            or previous.get("evidence_kind") != evidence_kind
            or previous.get("target", {}).get("role") != role):
        raise RunnerError("retry report does not match this suite input", 2)
    parent = previous.get("run_id")
    if (not isinstance(parent, str) or not RUN_ID_RE.fullmatch(parent)
            or parent == run_id):
        raise RunnerError("retry requires a distinct valid parent run", 2)
    cases = previous.get("cases")
    if (not isinstance(cases, list)
            or [case.get("test_id") if isinstance(case, dict) else None
                for case in cases] != expected
            or any(case.get("status") not in TERMINAL_CASE_STATUSES for case in cases)):
        raise RunnerError("retry report case inventory is invalid", 2)
    selected = previous.get("rerun_case_ids")
    if (not isinstance(selected, list) or not selected
            or len(selected) != len(set(selected))
            or any(item not in expected for item in selected)):
        raise RunnerError("retry report has no explicit rerun selection", 2)
    selected_before = previous.get("selected_case_ids")
    resolved = {case["test_id"] for case in cases
                if case["status"] in {"PASS", "SKIP", "NOT_APPLICABLE"}}
    derived = [item for item in selected_before or [] if item not in resolved]
    if (not isinstance(selected_before, list)
            or any(item not in expected for item in selected_before)
            or selected != derived
            or previous.get("unresolved_checks") != selected):
        raise RunnerError("retry report rerun selection is inconsistent", 2)
    refs = previous.get("identity_evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise RunnerError("retry report lacks identity evidence", 2)
    _verify_evidence_refs(report_path, refs)
    for case in cases:
        raw_refs = case.get("raw_evidence_refs")
        if not isinstance(raw_refs, list):
            raise RunnerError("retry report has invalid case evidence", 2)
        _verify_evidence_refs(report_path, raw_refs)
    return parent, set(selected), previous.get("target")


def _counts(cases: list[dict]) -> dict:
    return {status: sum(case["status"] == status for case in cases)
            for status in sorted(TERMINAL_CASE_STATUSES)}


def _scrub_report(report: dict, target: str) -> dict:
    """Remove the private ADB serial from every structured-report field."""
    protected_keys = {
        "sha256", "runner_sha256", "manifest_sha256",
        "identity_evidence_refs", "raw_evidence_refs",
        "redacted_evidence_refs",
    }

    def scrub(value, parent_key=None):
        if isinstance(value, dict):
            return {key: scrub(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item, parent_key) for item in value]
        if isinstance(value, str) and parent_key not in protected_keys:
            return value.replace(target, "<redacted:device-target>")
        return value

    return scrub(report)


def _validate_final_report(report: dict, result_path: Path) -> None:
    expected = report.get("expected_case_ids")
    cases = report.get("cases")
    if (not isinstance(expected, list) or not isinstance(cases, list)
            or [case.get("test_id") for case in cases] != expected
            or len(expected) != len(set(expected))):
        raise RunnerError("generated report case inventory is inconsistent", 5)
    if (report.get("completed_case_count")
            != sum(case.get("status") != "NOT_RUN" for case in cases)):
        raise RunnerError("generated report completion count is inconsistent", 5)
    if report.get("counts") != _counts(cases):
        raise RunnerError("generated report status counts are inconsistent", 5)
    if any(case.get("evidence_kind") != report.get("evidence_kind")
           for case in cases):
        raise RunnerError("generated report evidence labels are inconsistent", 5)
    try:
        _verify_evidence_refs(result_path, report.get("identity_evidence_refs", []))
        for case in cases:
            _verify_evidence_refs(result_path, case.get("raw_evidence_refs", []))
    except RunnerError as exc:
        raise RunnerError("generated report evidence did not verify", 5) from exc


def _overall(cases: list[dict], selected_ids: list[str]) -> tuple[str, int]:
    selected = [case for case in cases if case["test_id"] in selected_ids]
    statuses = {case["status"] for case in selected}
    if "INCOMPLETE" in statuses:
        return "INCOMPLETE", 5
    if "HARNESS_ERROR" in statuses:
        return "HARNESS_ERROR", 5
    if "BLOCKED" in statuses:
        return "BLOCKED", 3
    if "FAIL" in statuses:
        return "FAIL", 4
    if not selected or any(status == "NOT_RUN" for status in statuses):
        return "INCOMPLETE", 5
    return "PASS", 0


def _report_template(args, suite: dict, suite_hash: str, suite_path: Path,
                     repo_root: Path, identity: dict, identity_refs: list[str],
                     selected_ids: list[str], retry_parent: str | None,
                     candidate: dict,
                     executor=run_bounded) -> dict:
    expected_ids = [case["test_id"] for case in suite["cases"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "device-test-run",
        "run_id": args.run_id,
        "suite": {
            "id": suite["suite_id"],
            "source_name": suite_path.name,
            "sha256": suite_hash,
        },
        "tool": {
            "revision": _git_revision(repo_root, executor),
            "runner_sha256": sha256_bytes(Path(__file__).read_bytes()),
            "adb": _adb_version(args.adb, executor),
        },
        "candidate": candidate,
        "evidence_kind": args.evidence_kind,
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
        "started_at_utc": utc_now(),
        "finished_at_utc": None,
        "duration_ms": 0,
        "status": "INCOMPLETE",
        "completeness": "INCOMPLETE",
        "expected_case_ids": expected_ids,
        "selected_case_ids": selected_ids,
        "completed_case_count": 0,
        "counts": _counts([]),
        "identity_evidence_refs": identity_refs,
        "cases": [],
        "errors": [],
        "retry_parent": retry_parent,
        "rerun_case_ids": selected_ids,
        "unresolved_checks": selected_ids,
    }


def execute_run(args, suite: dict, suite_hash: str, suite_path: Path,
                repo_root: Path, executor=run_bounded) -> tuple[int, Path]:
    """Execute one immutable run and return ``(exit_code, result_path)``."""
    if not args.run_id or not RUN_ID_RE.fullmatch(args.run_id):
        raise RunnerError("execution requires a valid --run-id", 2)
    if not args.target or not args.device_role or not args.device_map:
        raise RunnerError("execution requires --target, --device-role and --device-map", 2)
    if args.evidence_kind not in {
            "real-device", "emulator", "synthetic-fixture", "static-review"}:
        raise RunnerError("execution requires an explicit valid --evidence-kind", 2)
    if not TOKEN_RE.fullmatch(args.device_role):
        raise RunnerError("invalid device role", 2)
    if args.target in args.run_id:
        raise RunnerError("run id must not contain the private target", 2)
    if not args.output:
        raise RunnerError("execution requires an --output root", 2)
    if not args.conditions or not args.conditions.strip():
        raise RunnerError("execution requires non-empty --conditions", 2)

    map_entry = load_device_map(Path(args.device_map), args.device_role, args.target)
    candidate = _candidate_record(args.candidate)
    chosen_stages = set(args.stage or STAGES)
    selected_ids = [case["test_id"] for case in suite["cases"]
                    if case["stage"] in chosen_stages]
    retry_parent, retry_ids, retry_target = _load_retry(
        args.rerun_from, suite, suite_hash, candidate, args.device_role,
        args.evidence_kind, args.run_id)
    if retry_ids is not None:
        selected_ids = [case_id for case_id in selected_ids if case_id in retry_ids]
        unknown = retry_ids - {case["test_id"] for case in suite["cases"]}
        if unknown or not selected_ids:
            raise RunnerError("retry selection is not present in the selected stages", 2)
    selected_cases = [case for case in suite["cases"]
                      if case["test_id"] in selected_ids]
    if not selected_cases:
        raise RunnerError("stage selection contains no runnable cases", 2)
    has_destructive = any(case["stage"] == "destructive" for case in selected_cases)
    if args.destructive and not has_destructive:
        raise RunnerError("--destructive was supplied without a selected destructive case", 2)
    if has_destructive and (not args.destructive or not map_entry["disposable"]):
        raise RunnerError("destructive stage requires explicit mode and an approved disposable role", 3)

    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    output_stat = output_root.stat()
    if (not stat.S_ISDIR(output_stat.st_mode) or output_stat.st_mode & 0o027
            or output_stat.st_uid != os.geteuid()):
        raise RunnerError("private output root must be owner-controlled mode 0750 or stricter", 3)
    partial_dir = output_root / f"{args.run_id}.partial"
    final_dir = output_root / args.run_id
    if partial_dir.exists() or final_dir.exists():
        raise RunnerError("immutable output collision", 3)
    locks = output_root / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    lock_path = locks / f"{args.device_role}.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("physical target is already locked", 3) from exc
        try:
            guard = rig.acquire_test_start_guard(
                getattr(args, "rig_config", None), args.device_role,
                args.device_map, args.target)
            try:
                # Scheduled maintenance may deliberately hold an otherwise
                # idle role off.  The start guard restores and verifies that
                # exact mapped role, so authorization must be checked only
                # after the guard has completed its restoration work.
                devices = _authorized_devices(args.adb, executor)
                if args.target not in devices:
                    raise RunnerError(
                        "selected private target is not an authorized USB device", 3)
                partial_dir.mkdir(mode=0o750)
                (partial_dir / "raw").mkdir(mode=0o750)
            finally:
                guard.release()
        except rig.RigError as exc:
            raise RunnerError(str(exc), exc.exit_code) from exc
        started = time.monotonic()
        try:
            identity, identity_refs = _capture_identity(
                args.adb, args.target, partial_dir,
                min(args.timeout, DEFAULT_TIMEOUT_SECONDS), executor)
            if retry_target is not None:
                comparable = {
                    "role": args.device_role,
                    "model": identity["model"],
                    "device": identity["device"],
                    "build": identity["build_id"],
                    "incremental": identity["incremental"],
                    "firmware": identity["firmware"],
                    "build_type": identity["build_type"],
                    "security_patch": identity["security_patch"],
                    "evidence_label": identity["evidence_label"],
                }
                if retry_target != comparable:
                    raise RunnerError("retry target/build identity changed", 3)
            report = _report_template(
                args, suite, suite_hash, suite_path, repo_root, identity,
                identity_refs, selected_ids, retry_parent, candidate, executor)
            report = _scrub_report(report, args.target)
            result_path = partial_dir / "result.json"
            _atomic_json(result_path, report)

            stop_reason = None
            interrupted = False
            for case in suite["cases"]:
                if case["test_id"] not in selected_ids:
                    omitted = _case_base(case, identity, args.evidence_kind)
                    omitted["reason"] = ("not selected for retry" if retry_ids is not None
                                         else "stage not selected")
                    report["cases"].append(omitted)
                    continue
                if stop_reason is not None:
                    omitted = _case_base(case, identity, args.evidence_kind)
                    omitted["reason"] = stop_reason
                    report["cases"].append(omitted)
                    continue
                index = len(report["cases"])
                try:
                    outcome = _run_case(case, identity, partial_dir, args.adb,
                                        args.target, index, args.evidence_kind,
                                        args.run_id, executor)
                except CommandInterrupted as exc:
                    outcome = exc.result["case"]
                    interrupted = True
                report["cases"].append(outcome)
                report["completed_case_count"] = sum(
                    item["status"] != "NOT_RUN" for item in report["cases"])
                report["counts"] = _counts(report["cases"])
                report["rerun_case_ids"] = [
                    item for item in selected_ids
                    if item not in {done["test_id"] for done in report["cases"]
                                    if done["status"] in {"PASS", "SKIP", "NOT_APPLICABLE"}}
                ]
                report["unresolved_checks"] = list(report["rerun_case_ids"])
                report["duration_ms"] = round((time.monotonic() - started) * 1000)
                report = _scrub_report(report, args.target)
                _atomic_json(result_path, report)
                if outcome["status"] not in {"PASS", "SKIP", "NOT_APPLICABLE"}:
                    stop_reason = f"fail-stop after {outcome['test_id']}"
                if interrupted:
                    break

            # Preserve explicit inventory even when interruption stopped iteration.
            present = {case["test_id"] for case in report["cases"]}
            for case in suite["cases"]:
                if case["test_id"] not in present:
                    omitted = _case_base(case, identity, args.evidence_kind)
                    omitted["reason"] = "run interrupted before this case"
                    report["cases"].append(omitted)

            status, exit_code = _overall(report["cases"], selected_ids)
            report["status"] = status
            report["completeness"] = (
                "COMPLETE" if len(selected_ids) == len(report["expected_case_ids"])
                and all(case["status"] != "NOT_RUN" for case in report["cases"])
                else "SELECTED" if status == "PASS" else "INCOMPLETE"
            )
            report["finished_at_utc"] = utc_now()
            report["duration_ms"] = round((time.monotonic() - started) * 1000)
            report["completed_case_count"] = sum(
                case["status"] != "NOT_RUN" for case in report["cases"])
            report["counts"] = _counts(report["cases"])
            resolved = {case["test_id"] for case in report["cases"]
                        if case["status"] in {"PASS", "SKIP", "NOT_APPLICABLE"}}
            report["rerun_case_ids"] = [item for item in selected_ids if item not in resolved]
            report["unresolved_checks"] = list(report["rerun_case_ids"])
            if status != "PASS":
                report["errors"] = [f"run ended with {status}"]
            report = _scrub_report(report, args.target)
            _atomic_json(result_path, report)
            _validate_final_report(report, result_path)
            os.rename(partial_dir, final_dir)
            _sync_directory(output_root)
            return exit_code, final_dir / "result.json"
        except RunnerError:
            # Identity/setup failures have no schema-valid report yet, but their
            # empty partial directory cannot be mistaken for an accepted run.
            raise
    finally:
        os.close(lock_fd)


def dry_run_plan(args, suite: dict, suite_hash: str, suite_path: Path) -> dict:
    stages = list(dict.fromkeys(args.stage or STAGES))
    selected = [case for case in suite["cases"] if case["stage"] in stages]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "device-test-run",
        "status": "DRY_RUN",
        "suite": {"id": suite["suite_id"], "source_name": suite_path.name,
                  "sha256": suite_hash},
        "stages": stages,
        "selected_case_ids": [case["test_id"] for case in selected],
        "contains_destructive_stage": any(case["stage"] == "destructive"
                                           for case in selected),
        "execution_requires": ["--run-id", "--target", "--device-role",
                               "--device-map", "--evidence-kind", "--conditions",
                               "--output"],
        "destructive_requires": ["--destructive", "device map disposable=true"],
        "device_commands_executed": 0,
        "device_state_changes": "none",
        "writes": "none",
        "timeout_cap_seconds": args.timeout,
        "output_cap_bytes": max(
            (case.get("output_limit_bytes", MAX_OUTPUT_BYTES)
             for case in selected if case["adapter"] == "adb-shell-read-only"),
            default=MAX_OUTPUT_BYTES),
        "case_output_caps_bytes": {
            case["test_id"]: case.get("output_limit_bytes", MAX_OUTPUT_BYTES)
            for case in selected if case["adapter"] == "adb-shell-read-only"
        },
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Target-bound staged device test runner")
    parser.add_argument("--suite", required=True,
                        help="suite name or explicit suite JSON path")
    parser.add_argument("--stage", action="append", choices=STAGES,
                        help="run only this stage (repeatable; default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print a plan; run no adb command and write nothing")
    parser.add_argument("--target", help="exact private ADB serial")
    parser.add_argument("--device-role", help="non-identifying role from the private map")
    parser.add_argument("--device-map", help="private target-role JSON")
    parser.add_argument(
        "--rig-config",
        help="optional private rig config providing a race-free start guard")
    parser.add_argument("--evidence-kind",
                        choices=("real-device", "emulator", "synthetic-fixture",
                                 "static-review"),
                        help="explicit source class for every case result")
    parser.add_argument("--destructive", action="store_true",
                        help="explicitly select destructive mode (map must mark role disposable)")
    parser.add_argument("--run-id", help="immutable run identity")
    parser.add_argument("--conditions", help="setup, connection and environment record")
    parser.add_argument("--output", help="private root receiving one immutable run directory")
    parser.add_argument("--candidate", help="optional candidate manifest to hash")
    parser.add_argument("--rerun-from", help="prior result.json selecting unresolved cases")
    parser.add_argument("--adb", default="adb", help="ADB executable")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                        help="identity/preflight timeout, 1..300 seconds")
    return parser


def _raise_keyboard_interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if isinstance(args.timeout, bool) or not (1 <= args.timeout <= 300):
        parser.error("--timeout must be between 1 and 300 seconds")
    try:
        repo_root = _repo_root()
        suite, suite_hash, suite_path = load_suite(args.suite, repo_root)
        if args.dry_run:
            sys.stdout.write(json.dumps(
                dry_run_plan(args, suite, suite_hash, suite_path), indent=2) + "\n")
            return 0
        old_umask = os.umask(0o027)
        old_term = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
        try:
            code, result_path = execute_run(
                args, suite, suite_hash, suite_path, repo_root)
        finally:
            signal.signal(signal.SIGTERM, old_term)
            os.umask(old_umask)
        sys.stdout.write(f"result={result_path}\n")
        return code
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except (KeyboardInterrupt, CommandInterrupted):
        print("error: run interrupted; any initialized partial checkpoint was retained",
              file=sys.stderr)
        return 5
    except OSError:
        print("error: runner filesystem operation failed", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
