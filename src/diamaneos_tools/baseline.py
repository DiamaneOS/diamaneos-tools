"""baseline capture read-only baseline collector (stdlib only, no device writes).

Fixture (no hardware):
  python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
  python3 -m unittest discover -s tests/baseline -v
Live (needs hardware; UNRUN until a real FP6 is connected):
  python3 src/diamaneos_tools/baseline.py --target <serial> --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json

Contract: explicit --target; ambiguous/missing target refuses before any adb
command . Missing metrics are 'unsupported', never zero . Capture
status and metric validity are separate: a successful capture of malformed
data is reported, never silently treated as a measurement. Public output
contains ONLY allowlisted extracted fields with identifiers redacted;
complete raw text goes to the private raw bundle (privacy requirements). Every subprocess
is bounded in time AND bytes; tool/setup failures are controlled results,
never uncaught exceptions. Allowlisted read-only adb shell commands only.
No flash/wipe/root/dumps. CLI: `bin/diamaneos baseline capture`.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA_VERSION = 1

# Inner `adb shell ...` allowlist: exact argv only, no shell, no pipes.
ALLOWLIST = {
    ("getprop",),
    ("dumpsys", "carrier_config"),
    ("dumpsys", "telephony.registry"),
    ("dumpsys", "imsservice"),
    ("dumpsys", "battery"),
    ("dumpsys", "gfxinfo"),
}

DEFAULT_TIMEOUT = 20  # seconds per adb call: host safety bound, not a device claim.
MAX_OUTPUT_BYTES = 262144  # per-command bound: duration limits alone do not cap memory.
MAX_KEPT_LINES = 200  # per-case field bound; excess flags truncation, never silently drops.

# Kept-line shapes per command. Anything else is dropped (counted) so broad
# raw dumpsys/getprop text is never treated as safe after a few regexes.
# Identifier-looking content inside kept lines is still redacted below.
SAFE_LINE = {
    ("getprop",): [r"^\[(ro\.build\.|ro\.product\.|ro\.board\.|ro\.hardware\.)"],
    ("dumpsys", "carrier_config"): [r"(?i)\b(mccmnc|version|patch|volte|vowifi|\b5g\b|\blte\b)"],
    ("dumpsys", "telephony.registry"): [r"(?i)\b(mServiceState|mSignalStrength|mDataConnectionState|operator|mccmnc|radioTech|serviceState|dataState|voiceRegState)"],
    ("dumpsys", "imsservice"): [r"(?i)\b(registered|available|enabled|provisioned|voice|video|sms|ut|capable)"],
    ("dumpsys", "battery"): [r"^\s*(level|scale|status|health|temperature|voltage|technology)\s*:"],
    ("dumpsys", "gfxinfo"): [r"."],  # frames data is voluminous but non-identifying; still redacted + capped
}

REDACTIONS = [
    (re.compile(r"\b\d{15,16}\b"), "<redacted:imei-or-imsi>"),
    (re.compile(r"\b\d{19,20}\b"), "<redacted:iccid>"),
    (re.compile(r"\b[0-9A-Fa-f]{32}\b"), "<redacted:eid>"),
    (re.compile(r"\+\d{7,15}\b"), "<redacted:phone>"),
    (re.compile(r"(?i)(msisdn|phone|number|subscriber)[\"':=\s]*\+?\d{7,15}"),
     r"\1<redacted:phone>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
     "<redacted:account>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "<redacted:mac>"),
    (re.compile(r"(?i)(serial[a-z]*[\]\s]*[:=]+[\s\[]*)[A-Za-z0-9_-]+"),
     r"\1<redacted:serial>"),
]


def redact(text):
    for pat, repl in REDACTIONS:
        text = pat.sub(repl, text)
    return text


def _is_unsupported_text(text):
    low = (text or "").lower()
    return ("not found" in low or "can't find" in low or "unknown" in low
            or "no such" in low)


def extract_fields(key, text):
    """Keep only allowlisted line shapes; return (kept_text, kept, dropped)."""
    shapes = [re.compile(p) for p in SAFE_LINE.get(tuple(key), [])]
    kept, dropped = [], 0
    for line in text.splitlines():
        if any(p.search(line) for p in shapes):
            kept.append(line)
        else:
            dropped += 1
    if len(kept) > MAX_KEPT_LINES:
        dropped += len(kept) - MAX_KEPT_LINES
        kept = kept[:MAX_KEPT_LINES]
        truncated = True
    else:
        truncated = False
    return "\n".join(kept), len(kept), dropped, truncated


def validate_metric(key, kept_text):
    """Capture success must not imply a valid measurement."""
    if tuple(key) == ("dumpsys", "battery"):
        m = re.search(r"^\s*level\s*:\s*(\d{1,3})\s*$", kept_text, re.M)
        if not m or not (0 <= int(m.group(1)) <= 100):
            return "malformed"
        return "valid"
    if tuple(key) == ("getprop",):
        return "valid" if kept_text.strip() else "malformed"
    return "unchecked"  # analysis belongs to the harness tasks, not the collector


def classify(key, returncode, stdout, stderr, timeout_hit=False):
    """One shared boundary for fixture and live observations.

    Returns a case dict with distinct capture status, redacted allowlisted
    fields only, metric validity, and evidence refs. Never raises for data.
    """
    key = tuple(key)
    if key not in ALLOWLIST:
        return {"test_id": " ".join(key), "status": "unsupported",
                "expected": "allowlisted read-only output or explicit unsupported",
                "observed": f"not in allowlist: {' '.join(key)}",
                "metric": "unchecked", "evidence_refs": []}
    if timeout_hit:
        return {"test_id": " ".join(key), "status": "error",
                "expected": "bounded collection or explicit timeout",
                "observed": "timeout", "metric": "unchecked", "evidence_refs": []}
    if returncode != 0:
        err = (stderr or "")
        status = "unsupported" if _is_unsupported_text(err) else "error"
        return {"test_id": " ".join(key), "status": status,
                "expected": "bounded collection or explicit unsupported",
                "observed": redact(err.strip())[:500] or f"exit {returncode}",
                "metric": "unchecked", "evidence_refs": []}
    out = stdout or ""
    if not out.strip():
        return {"test_id": " ".join(key), "status": "unsupported",
                "expected": "bounded collection or explicit unsupported",
                "observed": "empty output", "metric": "unchecked",
                "evidence_refs": []}
    if len(out.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
        return {"test_id": " ".join(key), "status": "error",
                "expected": "output within byte bound",
                "observed": "output exceeded byte bound; see private raw",
                "metric": "unchecked", "evidence_refs": []}
    if _is_unsupported_text(out):
        return {"test_id": " ".join(key), "status": "unsupported",
                "expected": "bounded collection or explicit unsupported",
                "observed": redact(out.strip())[:500],
                "metric": "unchecked", "evidence_refs": []}
    if "[TRUNCATED]" in out:
        return {"test_id": " ".join(key), "status": "error",
                "expected": "complete output",
                "observed": "truncated output preserved, not averaged as zero",
                "metric": "unchecked", "evidence_refs": []}
    kept, n_kept, n_dropped, capped = extract_fields(key, out)
    metric = validate_metric(key, kept)
    observed = redact(kept)
    note = "" if not capped else " [kept-lines capped; see private raw]"
    return {"test_id": " ".join(key), "status": "ok",
            "expected": "bounded collection",
            "observed": observed + note, "metric": metric,
            "kept_fields": n_kept, "dropped_lines": n_dropped,
            "evidence_refs": []}


def resolve_target(devices, target):
    """devices: list of serials from `adb devices`. Returns target or raises."""
    if not devices:
        raise RuntimeError("NO_DEVICE: no adb device connected")
    if target:
        if target not in devices:
            raise RuntimeError(f"UNKNOWN_TARGET: {target!r} not in {devices}")
        return target
    if len(devices) > 1:
        raise RuntimeError(f"AMBIGUOUS_TARGET: {devices}, --target required; refusing")
    return devices[0]


def run_cmd(argv, timeout=DEFAULT_TIMEOUT):
    """Bounded subprocess that never raises for tool/setup failures."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"transport": "timeout", "reason": "timeout",
                "timeout_s": timeout}
    except FileNotFoundError:
        return {"transport": "tool-missing",
                "reason": f"executable not found: {argv[0]}"}
    except OSError as exc:
        return {"transport": "error", "reason": f"os error: {exc}"}
    if proc.returncode != 0:
        return {"transport": "error", "reason": (proc.stderr or "").strip()[:200]
                or f"exit {proc.returncode}", "stdout": proc.stdout or ""}
    out = proc.stdout or ""
    if len(out.encode("utf-8", "replace")) > MAX_OUTPUT_BYTES:
        return {"transport": "error", "reason": "output exceeded byte bound",
                "stdout": out}
    return {"transport": "ok", "stdout": out}


def list_devices(adb="adb", timeout=DEFAULT_TIMEOUT):
    """Controlled device enumeration; returns (ok, [serials]) or (error, reason)."""
    res = run_cmd([adb, "devices"], timeout=timeout)
    if res["transport"] != "ok":
        return res["transport"], res["reason"]
    devs = []
    for line in res["stdout"].splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return "ok", devs


def _default_config_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "config",
                                         "baseline.json"))


def load_procedures(path):
    try:
        with open(path) as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"config unreadable: {exc}"
    procs = cfg.get("procedures")
    if not isinstance(procs, list):
        return None, "config has no procedures list"
    return [p.get("id", "?") for p in procs], None


def adb_version(adb="adb", timeout=DEFAULT_TIMEOUT):
    res = run_cmd([adb, "version"], timeout=timeout)
    if res["transport"] != "ok":
        return "unknown (tool query failed)"
    return (res["stdout"].strip().splitlines() or ["unknown"])[0][:120]


def run_fixture(fixture, raw_evidence_location=None):
    """Offline fixture run: {inputs: {key: {returncode, stdout, stderr}},
    tool_versions, environment}. Never touches adb."""
    raw_loc = (raw_evidence_location
               or fixture.get("raw_evidence_location")
               or "synthetic-fixture: no device raw")
    cases = []
    for key, res in fixture.get("inputs", {}).items():
        argv = tuple(key.split(" ", 1))
        case = classify(argv, res.get("returncode", 0), res.get("stdout", ""),
                        res.get("stderr", ""), timeout_hit=bool(res.get("timeout")))
        case["evidence_refs"] = [f"{raw_loc}#{case['test_id']}"]
        cases.append(case)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": fixture.get("run_id", "fixture-run"),
        "utc_time": fixture.get("utc_time", "fixture"),
        "tool_versions": fixture.get("tool_versions", {}),
        "environment": fixture.get("environment", {"source": "synthetic-fixture"}),
        "os_build": fixture.get("os_build", "unsupported: no device"),
        "raw_evidence_location": raw_loc,
        "procedures": fixture.get("procedures", ["fixture-only"]),
        "cases": cases,
    }


def live_capture(target, adb="adb", timeout=DEFAULT_TIMEOUT, run_id=None,
                 conditions=None, raw_dir=None, config_path=None):
    """Target-bound live collection. Returns (exit_code, report)."""
    run_id = run_id or ("live-" + datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    procs, cfg_err = load_procedures(config_path or _default_config_path())
    status, devs = list_devices(adb=adb, timeout=timeout)
    if status != "ok":
        return 3, {"schema_version": SCHEMA_VERSION, "run_id": run_id,
                   "status": "error",
                   "observed": f"device enumeration failed: {devs}",
                   "cases": []}
    try:
        target = resolve_target(devs, target)
    except RuntimeError as exc:
        return 3, {"schema_version": SCHEMA_VERSION, "run_id": run_id,
                   "status": "error", "observed": str(exc), "cases": []}
    raws = {}
    cases = []
    for shell_argv in sorted(ALLOWLIST):
        res = run_cmd([adb, "-s", target, "shell"] + list(shell_argv),
                      timeout=timeout)
        if res["transport"] != "ok":
            case = classify(shell_argv, 0, "", "", timeout_hit=(
                res["transport"] == "timeout"))
            if res["transport"] == "tool-missing":
                case = {"test_id": " ".join(shell_argv), "status": "error",
                        "expected": "adb available",
                        "observed": res["reason"], "metric": "unchecked",
                        "evidence_refs": []}
            case["evidence_refs"] = []
            cases.append(case)
            continue
        raw = res["stdout"]
        raws[" ".join(shell_argv)] = raw
        case = classify(shell_argv, 0, raw, "")
        case["evidence_refs"] = []
        cases.append(case)
    raw_refs = {}
    if raw_dir is not None:
        os.makedirs(raw_dir, exist_ok=True)
        for name, raw in raws.items():
            dest = os.path.join(raw_dir, name.replace(" ", "_") + ".txt")
            with open(dest, "w") as fh:
                fh.write(raw)
            raw_refs[name] = dest
    else:
        for name, raw in raws.items():
            digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
            raw_refs[name] = (f"sha256:{digest} (raw not persisted; "
                              "pass --raw-dir under PRIVATE_ROOT)")
    for case in cases:
        name = case["test_id"]
        if name in raw_refs:
            case["evidence_refs"] = [f"{raw_refs[name]}#{name}"]
    builds = [c["observed"] for c in cases
              if c["test_id"] == "getprop" and c["status"] == "ok"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tool_versions": {"adb": adb_version(adb=adb, timeout=timeout)},
        "environment": {"source": "live-device", "target": target,
                        "conditions": conditions or
                        "unrecorded (repeat with --conditions)",
                        "repetitions": {"pass": 1, "note": "single pass; "
                                         "repetitions controlled by the hardware harness"}},
        "os_build": builds[0] if builds else "unsupported: no device",
        "raw_evidence_location": raw_dir or "not persisted (see per-case refs)",
        "procedures": procs if procs is not None else [],
        "config_error": cfg_err,
        "cases": cases,
    }
    return 0, report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only FP6 baseline collector")
    ap.add_argument("--fixture", help="offline fixture JSON (no adb)")
    ap.add_argument("--target", help="explicit adb serial (required for live)")
    ap.add_argument("--adb", default="adb", help="adb executable")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan only, run no adb commands")
    ap.add_argument("--output", help="write report JSON here (default: stdout)")
    ap.add_argument("--raw-dir", help="persist private raw outputs here")
    ap.add_argument("--run-id", help="run identity")
    ap.add_argument("--conditions", help="controlled conditions record")
    ap.add_argument("--config", help="procedures config (default: config/baseline.json)")
    args = ap.parse_args(argv)

    if args.fixture:
        with open(args.fixture) as fh:
            report = run_fixture(json.load(fh),
                                 raw_evidence_location=f"fixture:{args.fixture}")
        report["label"] = f"FIXTURE {args.fixture}: no hardware claim"
        text = json.dumps(report, indent=2) + "\n"
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        return 0

    if args.dry_run:
        plan = {"schema_version": SCHEMA_VERSION, "plan": "allowlisted read-only captures",
                "allowlist": sorted(" ".join(a) for a in ALLOWLIST),
                "target_required": True, "timeout_s": args.timeout,
                "byte_cap": MAX_OUTPUT_BYTES,
                "writes": "none (dry-run runs no adb commands)"}
        sys.stdout.write(json.dumps(plan, indent=2) + "\n")
        return 0

    if not args.target:
        print("error: --target required (refusing before any adb command)",
              file=sys.stderr)
        return 2
    code, report = live_capture(args.target, adb=args.adb,
                                timeout=args.timeout, run_id=args.run_id,
                                conditions=args.conditions,
                                raw_dir=args.raw_dir,
                                config_path=args.config)
    if code == 0:
        text = json.dumps(report, indent=2) + "\n"
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
    else:
        print(f"error: {report.get('observed')}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
