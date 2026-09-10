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
# Dropped-first shapes: location-bearing or identifier-bearing lines never reach
# public output even when they match a kept shape below.
DROP_LINE = {
    ("dumpsys", "telephony.registry"): [
        # camelCase key=value first: \b never matches inside mTac/mCi.
        r"(?i)[a-z]*(tac|cell_?id|cgi|pci|arfcn|lac|ci|nid|bid|sid)\s*=",
        r"(?i)\b(tac|pci|arfcn|lac|cgi|cellinfo|nid|bid|sid|ci)\b",
        r"(?i)(tracking.area|cell.identit|location.area|routing.area|location.info)"],
    ("dumpsys", "gfxinfo"): [r"(?i)\b(package|applicationId)\b\s*[:=]"],
}

SAFE_LINE = {
    ("getprop",): [r"^\[(ro\.build\.|ro\.product\.|ro\.board\.|ro\.hardware\.)"],
    ("dumpsys", "carrier_config"): [r"(?i)\b(mccmnc|version|patch|volte|vowifi|\b5g\b|\blte\b)"],
    ("dumpsys", "telephony.registry"): [r"(?i)\b(mServiceState|mSignalStrength|mDataConnectionState|operator|mccmnc|radioTech|serviceState|dataState|voiceRegState)"],
    ("dumpsys", "imsservice"): [r"(?i)\b(registered|available|enabled|provisioned|voice|video|sms|ut|capable)"],
    ("dumpsys", "battery"): [r"^\s*(level|scale|status|health|temperature|voltage|technology)\s*:"],
    # Graphics stats only: a kept line must carry a digit (package-name lines drop).
    ("dumpsys", "gfxinfo"): [r"^\s*[\w ./-]+:\s*[-+.\w%]*\d"],
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


# Only genuine service-error shapes count as unavailable. A bare "unknown"
# (e.g. an operator value inside valid service state) must never flip a case.
MISSING_SERVICE = [
    "can't find service", "unknown service", "service not found",
    "service unknown", "no such service", "not found",
    "no such file or directory", "unknown command",
]


def _is_unsupported_text(text):
    low = (text or "").lower()
    return any(p in low for p in MISSING_SERVICE)


def extract_fields(key, text):
    """Keep only allowlisted line shapes; return (kept_text, kept, dropped, sensitive, capped)."""
    drops = [re.compile(p) for p in DROP_LINE.get(tuple(key), [])]
    shapes = [re.compile(p) for p in SAFE_LINE.get(tuple(key), [])]
    kept, dropped, sensitive = [], 0, 0
    for line in text.splitlines():
        if any(p.search(line) for p in drops):
            sensitive += 1
        elif any(p.search(line) for p in shapes):
            kept.append(line)
        else:
            dropped += 1
    if len(kept) > MAX_KEPT_LINES:
        dropped += len(kept) - MAX_KEPT_LINES
        kept = kept[:MAX_KEPT_LINES]
        truncated = True
    else:
        truncated = False
    return "\n".join(kept), len(kept), dropped, sensitive, truncated


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
    kept, n_kept, n_dropped, sensitive, capped = extract_fields(key, out)
    metric = validate_metric(key, kept)
    observed = redact(kept)
    note = "" if not capped else " [kept-lines capped; see private raw]"
    return {"test_id": " ".join(key), "status": "ok",
            "expected": "bounded collection",
            "observed": observed + note, "metric": metric,
            "kept_fields": n_kept, "dropped_lines": n_dropped,
            "dropped_sensitive": sensitive,
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


def _pump(stream, acc, cap):
    """Drain stream to EOF, keeping only the first cap chars.

    Must keep reading after the cap (discarding) so the child never blocks
    on a full pipe; acc['over'] records that the bound was exceeded.
    Daemon thread target. Never raises.
    """
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            if acc["total"] <= cap:
                acc["chunks"].append(chunk)
                acc["total"] += len(chunk)
                if acc["total"] > cap:
                    acc["over"] = True
    except ValueError:
        pass  # stream closed under us during kill


def run_cmd(argv, timeout=DEFAULT_TIMEOUT):
    """Bounded subprocess: time AND bytes capped WHILE reading.

    Never raises for tool/setup failures. Returns transport ok (with bounded
    stdout/stderr) or timeout/overflow/tool-missing/error with a sanitized
    reason. A killed over-producer counts as overflow, never success.
    """
    import threading
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return {"transport": "tool-missing",
                "reason": "executable not found: "
                          + os.path.basename(argv[0]),
                "stdout": "", "stderr": ""}
    except OSError as exc:
        return {"transport": "error", "reason": f"os error: {exc}",
                "stdout": "", "stderr": ""}
    out_acc = {"chunks": [], "total": 0, "over": False}
    err_acc = {"chunks": [], "total": 0, "over": False}
    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_acc,
                                                 MAX_OUTPUT_BYTES),
                             daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_acc,
                                                 MAX_OUTPUT_BYTES),
                             daemon=True)
    t_out.start()
    t_err.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        try:
            proc.stdout.close()
            proc.stderr.close()
        except ValueError:
            pass
        proc.wait()
        return {"transport": "timeout",
                "reason": "timeout", "timeout_s": timeout,
                "stdout": "".join(out_acc["chunks"])[:MAX_OUTPUT_BYTES],
                "stderr": "".join(err_acc["chunks"])[:MAX_OUTPUT_BYTES],
                "partial": True}
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    out = "".join(out_acc["chunks"])
    err = "".join(err_acc["chunks"])
    overflow = out_acc["over"] or err_acc["over"]
    out, err = out[:MAX_OUTPUT_BYTES], err[:MAX_OUTPUT_BYTES]
    if overflow:
        return {"transport": "overflow",
                "reason": "output exceeded byte bound; child terminated",
                "stdout": out, "stderr": err, "partial": True}
    if proc.returncode != 0:
        return {"transport": "error",
                "reason": redact(err.strip())[:200] or f"exit {proc.returncode}",
                "returncode": proc.returncode, "stdout": out, "stderr": err}
    return {"transport": "ok", "stdout": out, "stderr": err}


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
    # Full banner: the release/build line distinguishes platform-tools
    # releases that share the same first line.
    return res["stdout"].strip()[:500] or "unknown"


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
        if res["transport"] == "timeout":
            case = {"test_id": " ".join(shell_argv), "status": "error",
                    "expected": "bounded collection or explicit timeout",
                    "observed": "timeout", "metric": "unchecked",
                    "transport": "timeout", "evidence_refs": []}
        elif res["transport"] == "tool-missing":
            case = {"test_id": " ".join(shell_argv), "status": "error",
                    "expected": "adb available",
                    "observed": res["reason"], "metric": "unchecked",
                    "transport": "tool-missing", "evidence_refs": []}
        elif res["transport"] == "overflow":
            raws[" ".join(shell_argv)] = res.get("stdout", "")
            case = {"test_id": " ".join(shell_argv), "status": "error",
                    "expected": "output within byte bound",
                    "observed": ("output overflow beyond bound; partial "
                                 "preserved privately"), "metric": "unchecked",
                    "transport": "overflow", "evidence_refs": []}
        elif res["transport"] == "error" and "stdout" not in res:
            case = {"test_id": " ".join(shell_argv), "status": "error",
                    "expected": "bounded collection",
                    "observed": res["reason"], "metric": "unchecked",
                    "transport": "error", "evidence_refs": []}
        else:
            raw = res.get("stdout", "")
            raws[" ".join(shell_argv)] = raw
            case = classify(shell_argv, res.get("returncode", 0), raw,
                            res.get("stderr", ""))
            case["transport"] = "process"
        cases.append(case)
    if raw_dir is not None:
        run_dir = os.path.join(raw_dir, run_id)
        if os.path.exists(run_dir) and os.listdir(run_dir):
            return 3, {"schema_version": SCHEMA_VERSION, "run_id": run_id,
                       "status": "error",
                       "observed": f"refusing: raw directory not empty: {run_dir}",
                       "cases": []}
        os.makedirs(run_dir, exist_ok=True)
        raw_refs = {}
        for name, raw in raws.items():
            dest = os.path.join(run_dir, name.replace(" ", "_") + ".txt")
            with open(dest, "w") as fh:
                fh.write(raw)
            digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
            raw_refs[name] = f"{dest}#{name}@sha256:{digest}"
        raw_location = run_dir
    else:
        raw_location = ("ephemeral: observations not persisted — "
                        "not accepted evidence (pass --raw-dir under PRIVATE_ROOT)")
        raw_refs = {}
        for name, raw in raws.items():
            digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
            raw_refs[name] = (f"sha256:{digest} (raw not persisted)")
    for case in cases:
        name = case["test_id"]
        if name in raw_refs:
            case["evidence_refs"] = [f"{raw_refs[name]}"]
    builds = [c["observed"] for c in cases
              if c["test_id"] == "getprop" and c["status"] == "ok"]
    if any(c["status"] == "error" for c in cases):
        completeness = "partial"
    else:
        completeness = "complete"
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "collection_status": completeness,
        "tool_versions": {"adb": adb_version(adb=adb, timeout=timeout)},
        # Public envelope carries a run-local alias only. The real serial
        # lives in the private raw bundle and is never published (R1).
        "environment": {"source": "live-device", "device_alias": "target-1",
                        "device_role": "unassigned",
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
