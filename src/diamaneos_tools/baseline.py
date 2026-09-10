"""baseline capture read-only baseline collector (stdlib only, no device writes).

Usage (fixture, no hardware):
  python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
  python3 -m unittest discover -s tests/baseline -v

Live capture (requires hardware; stays UNRUN until a real FP6 is connected):
  python3 src/diamaneos_tools/baseline.py --target <serial> [--timeout 20]

Rules: explicit --target required; ambiguous/missing target refuses before any
adb command . Missing metrics are 'unsupported', never zero .
Identifiers are redacted in public output; raw stays at its private location .
Allowlisted read-only adb shell commands only. No flash/wipe/root/dumps.
"""
import argparse
import datetime
import json
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

REDACTIONS = [
    (re.compile(r"\b\d{15}\b"), "<redacted:imei>"),
    (re.compile(r"\b\d{19,20}\b"), "<redacted:iccid>"),
    (re.compile(r"(?i)(serial[a-z]*[\]\s]*[:=]+[\s\[]*)[A-Za-z0-9_-]+"),
     r"\1<redacted:serial>"),
]

DEFAULT_TIMEOUT = 20  # seconds per adb call: host safety bound, not a device claim.


def _is_unsupported_error(err):
    low = err.lower()
    return ("not found" in low or "can't find" in low or "unknown" in low
            or "no such" in low)


def redact(text):
    for pat, repl in REDACTIONS:
        text = pat.sub(repl, text)
    return text


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


def list_devices(adb="adb", timeout=DEFAULT_TIMEOUT):
    out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                         timeout=timeout)
    out.check_returncode()
    devs = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs


def run_capture(target, shell_argv, adb="adb", timeout=DEFAULT_TIMEOUT):
    """Run one allowlisted capture. Returns dict with status/result."""
    if tuple(shell_argv) not in ALLOWLIST:
        return {"status": "unsupported",
                "reason": f"not in read-only allowlist: {shell_argv}"}
    try:
        proc = subprocess.run([adb, "-s", target, "shell"] + list(shell_argv),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "timeout", "timeout_s": timeout}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if _is_unsupported_error(err):
            return {"status": "unsupported", "reason": err[:200]}
        return {"status": "error", "reason": err[:200] or f"exit {proc.returncode}"}
    if not proc.stdout.strip():
        return {"status": "unsupported", "reason": "empty output"}
    return {"status": "ok", "observed": proc.stdout}


def run_fixture(fixture):
    """Offline fixture run: {inputs: {key: {returncode, stdout, stderr}},
    tool_versions, environment}. Never touches adb."""
    cases = []
    for key, res in fixture.get("inputs", {}).items():
        argv = tuple(key.split(" ", 1))
        if argv not in ALLOWLIST:
            cases.append({"test_id": key, "status": "unsupported",
                          "expected": "allowlisted read-only output or explicit unsupported",
                          "observed": f"not in allowlist: {key}", "evidence_refs": []})
            continue
        if res.get("timeout"):
            cases.append({"test_id": key, "status": "error",
                          "expected": "bounded collection or explicit timeout",
                          "observed": "timeout", "evidence_refs": []})
            continue
        if res.get("returncode", 0) != 0:
            err = (res.get("stderr") or "")
            status = "unsupported" if _is_unsupported_error(err) else "error"
            cases.append({"test_id": key, "status": status,
                          "expected": "bounded collection or explicit unsupported",
                          "observed": err[:200] or f"exit {res.get('returncode')}",
                          "evidence_refs": []})
            continue
        out = res.get("stdout", "")
        if not out.strip():
            cases.append({"test_id": key, "status": "unsupported",
                          "expected": "bounded collection or explicit unsupported",
                          "observed": "empty output", "evidence_refs": []})
            continue
        if "[TRUNCATED]" in out:
            cases.append({"test_id": key, "status": "error",
                          "expected": "complete output",
                          "observed": "truncated output preserved, not averaged as zero",
                          "evidence_refs": []})
            continue
        cases.append({"test_id": key, "status": "ok",
                      "expected": "bounded collection",
                      "observed": redact(out)[:500], "evidence_refs": []})
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": fixture.get("run_id", "fixture-run"),
        "utc_time": fixture.get("utc_time", "fixture"),
        "tool_versions": fixture.get("tool_versions", {}),
        "environment": fixture.get("environment", {"source": "synthetic-fixture"}),
        "os_build": fixture.get("os_build", "unsupported: no device"),
        "cases": cases,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only FP6 baseline collector")
    ap.add_argument("--fixture", help="offline fixture JSON (no adb)")
    ap.add_argument("--target", help="explicit adb serial (required for live)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan only, run no adb commands")
    ap.add_argument("--output", help="write report JSON here (default: stdout)")
    args = ap.parse_args(argv)

    if args.fixture:
        with open(args.fixture) as fh:
            report = run_fixture(json.load(fh))
        label = f"FIXTURE {args.fixture}: no hardware claim"
        report["label"] = label
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
                "writes": "none (dry-run runs no adb commands)"}
        sys.stdout.write(json.dumps(plan, indent=2) + "\n")
        return 0

    if not args.target:
        print("error: --target required (refusing before any adb command)",
              file=sys.stderr)
        return 2
    try:
        devs = list_devices(timeout=args.timeout)
        target = resolve_target(devs, args.target)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"live capture on {target}: not run in FP6-010 (no hardware yet)",
          file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
