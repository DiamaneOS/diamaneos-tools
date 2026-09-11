"""Read-only baseline collector (stdlib only, no device writes).

Fixture (no hardware):
  python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
  python3 -m unittest discover -s tests/baseline -v
Live (needs hardware; UNRUN until a real FP6 is connected):
  python3 src/diamaneos_tools/baseline.py --target <serial> --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json

Contract: explicit --target; ambiguous/missing target refuses before any adb
command. Missing metrics are 'unsupported', never zero. Capture
status and metric validity are separate: a successful capture of malformed
data is reported, never silently treated as a measurement. Public output
contains ONLY allowlisted extracted fields with identifiers redacted;
complete raw text goes to the private raw bundle in caller-selected protected storage. Every subprocess
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
GFXINFO_COMMAND = ("dumpsys", "gfxinfo", "com.android.systemui")

# Inner `adb shell ...` allowlist: exact argv only, no shell, no pipes.
ALLOWLIST = {
    ("getprop",),
    ("dumpsys", "carrier_config"),
    ("dumpsys", "telephony.registry"),
    ("dumpsys", "imsservice"),
    ("dumpsys", "battery"),
    GFXINFO_COMMAND,
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
    GFXINFO_COMMAND: [r"(?i)\b(package|applicationId)\b\s*[:=]"],
}

SAFE_LINE = {
    ("getprop",): [r"^\[(ro\.build\.|ro\.product\.|ro\.board\.|ro\.hardware\.)"],
    ("dumpsys", "carrier_config"): [r"(?i)\b(mccmnc|version|patch|volte|vowifi|\b5g\b|\blte\b)"],
    ("dumpsys", "telephony.registry"): [r"(?i)\b(mServiceState|mSignalStrength|mDataConnectionState|operator|mccmnc|radioTech|serviceState|dataState|voiceRegState)"],
    ("dumpsys", "imsservice"): [r"(?i)\b(registered|available|enabled|provisioned|voice|video|sms|ut|capable)"],
    ("dumpsys", "battery"): [r"^\s*(level|scale|status|health|temperature|voltage|technology)\s*:"],
    # Graphics stats only: a kept line must carry a digit (package-name lines drop).
    GFXINFO_COMMAND: [r"^\s*[\w ./-]+:\s*[-+.\w%]*\d"],
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
# Device-gone shapes are connection failures, never missing services.
# The quoted-target form (adb: device '<serial>' not found) must match even
# though the serial interrupts the contiguous phrase.
DEVICE_GONE = [
    r"device\s+('[^']*'\s+)?not found",
    r"no devices?\b",
    r"device\s+offline",
    r"unauthorized device",
    r"no permissions",
]

MISSING_SERVICE = [
    "can't find service", "unknown service", "service not found",
    "service unknown", "no such service", "not found",
    "no such file or directory", "unknown command",
]


def _is_unsupported_text(text):
    low = (text or "").lower()
    return any(p in low for p in MISSING_SERVICE)


def _is_device_gone(text):
    low = (text or "").lower()
    return any(re.search(p, low) for p in DEVICE_GONE)


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
        if _is_device_gone(err) or _is_device_gone(stdout):
            return {"test_id": " ".join(key), "status": "error",
                    "expected": "device present for the whole capture",
                    "observed": "device unavailable during capture",
                    "metric": "unchecked", "evidence_refs": []}
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
    if _is_device_gone(out):
        return {"test_id": " ".join(key), "status": "error",
                "expected": "device present for the whole capture",
                "observed": "device unavailable during capture",
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


def run_cmd(argv, timeout=DEFAULT_TIMEOUT):
    """Bounded subprocess: time AND bytes capped WHILE reading.

    Both pipes are non-blocking, serviced in one deterministic loop with no
    threads: the first excess byte is detected within milliseconds and the
    child terminated at once (no buffered read can wait out a slow
    continuation marker, and no thread timing can reorder the result).
    Counts are BYTES on the raw stream, so multi-byte UTF-8 cannot slip past
    a character count; decoding uses errors="replace" so invalid input stays
    visible instead of vanishing. A killed over-producer counts as overflow,
    never success. Never raises for tool/setup failures.
    """
    import time
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except FileNotFoundError:
        return {"transport": "tool-missing",
                "reason": "executable not found: "
                          + os.path.basename(argv[0]),
                "stdout": "", "stderr": ""}
    except OSError as exc:
        return {"transport": "error", "reason": f"os error: {exc}",
                "stdout": "", "stderr": ""}
    for stream in (proc.stdout, proc.stderr):
        os.set_blocking(stream.fileno(), False)
    out, err = bytearray(), bytearray()
    over = False
    eof_out = eof_err = False
    exited = False
    deadline = time.monotonic() + timeout
    result = None
    while True:
        for fd, buf, done in ((proc.stdout.fileno(), out, eof_out),
                              (proc.stderr.fileno(), err, eof_err)):
            if done:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                chunk = b""
            if chunk == b"":
                if fd == proc.stdout.fileno():
                    eof_out = True
                else:
                    eof_err = True
            else:
                buf += chunk
                if len(buf) > MAX_OUTPUT_BYTES:
                    over = True
        if over:
            proc.kill()
            result = {"transport": "overflow",
                      "reason": "output exceeded byte bound; child terminated"}
            break
        if proc.poll() is not None:
            exited = True
        if exited and eof_out and eof_err:
            break
        if time.monotonic() >= deadline:
            # The deadline governs pipe draining too: a direct child that
            # already exited must not leave us waiting on inherited pipes.
            proc.kill()
            result = {"transport": "timeout",
                      "reason": "timeout", "timeout_s": timeout}
            break
        time.sleep(0.005)
    # One immediate drain pass only: collect what is already available for
    # partial evidence, but never wait out a live descendant. A bounded wait
    # here would reintroduce the inherited-pipe hang this deadline prevents.
    for fd, buf, done in ((proc.stdout.fileno(), out, eof_out),
                          (proc.stderr.fileno(), err, eof_err)):
        if done:
            continue
        try:
            chunk = os.read(fd, 65536)
        except (BlockingIOError, OSError):
            continue
        if chunk == b"":
            if fd == proc.stdout.fileno():
                eof_out = True
            else:
                eof_err = True
        elif len(buf) <= MAX_OUTPUT_BYTES:
            buf += chunk
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except ValueError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # Recheck after the final drain: no scheduling order can downgrade an
    # overflow (or an over-cap final burst) into success.
    if len(out) > MAX_OUTPUT_BYTES or len(err) > MAX_OUTPUT_BYTES:
        over = True
    text_out = bytes(out[:MAX_OUTPUT_BYTES]).decode("utf-8", errors="replace")
    text_err = bytes(err[:MAX_OUTPUT_BYTES]).decode("utf-8", errors="replace")
    if over:
        return {"transport": "overflow",
                "reason": "output exceeded byte bound; child terminated",
                "stdout": text_out, "stderr": text_err, "partial": True}
    if result is not None:
        result.update({"stdout": text_out, "stderr": text_err,
                       "partial": True})
        return result
    if proc.returncode != 0:
        return {"transport": "error",
                "reason": redact(text_err.strip())[:200]
                          or f"exit {proc.returncode}",
                "returncode": proc.returncode,
                "stdout": text_out, "stderr": text_err}
    return {"transport": "ok", "stdout": text_out, "stderr": text_err}


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


def _scrub_report(report, serials):
    """Replace every known device serial in the PUBLIC report with an alias.

    The real serials live only in the private raw bundle. Aliases are
    run-local: the selected target is target-1, others device-2..N.
    """
    mapping = {}
    for i, serial in enumerate(dict.fromkeys(s for s in serials if s)):
        mapping[serial] = "target-1" if i == 0 else f"device-{i + 1}"
    text = json.dumps(report)
    for serial in sorted(mapping, key=len, reverse=True):
        text = text.replace(serial, mapping[serial])
    return json.loads(text)


EPHEMERAL_LABEL = ("ephemeral: observations not persisted — not accepted "
                   "evidence (pass --raw-dir under PRIVATE_ROOT)")


def live_capture(target, adb="adb", timeout=DEFAULT_TIMEOUT, run_id=None,
                 conditions=None, raw_dir=None, config_path=None):
    """Target-bound live collection. Returns (exit_code, report).

    Every return path passes through _scrub_report: no device serial ever
    appears in public output, including enumeration failures and
    unknown-target errors.
    """
    run_id = run_id or ("live-" + datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    procs, cfg_err = load_procedures(config_path or _default_config_path())
    status, devs = list_devices(adb=adb, timeout=timeout)
    if status != "ok":
        return 3, _scrub_report(
            {"schema_version": SCHEMA_VERSION, "run_id": run_id,
             "status": "error",
             "observed": f"device enumeration failed: {redact(devs)}",
             "cases": []}, [target])
    try:
        target = resolve_target(devs, target)
    except RuntimeError as exc:
        return 3, _scrub_report(
            {"schema_version": SCHEMA_VERSION, "run_id": run_id,
             "status": "error", "observed": str(exc), "cases": []},
            [target] + list(devs))
    # Alias namespace, fixed BEFORE any reference is constructed: the
    # selected target is target-1, others device-2..N. Public refs and the
    # sidecar keys use these aliases, so envelope scrubbing can never
    # silently break resolvability; real paths live only in sidecar values.
    aliases = {}
    for i, serial in enumerate(dict.fromkeys([target] + list(devs))):
        if serial:
            aliases[serial] = "target-1" if i == 0 else f"device-{i + 1}"

    def pub(text):
        for serial in sorted(aliases, key=len, reverse=True):
            text = text.replace(serial, aliases[serial])
        return text

    raws = {}
    cases = []
    for shell_argv in sorted(ALLOWLIST):
        name = " ".join(shell_argv)
        res = run_cmd([adb, "-s", target, "shell"] + list(shell_argv),
                      timeout=timeout)
        if res["transport"] == "timeout":
            raws[name] = {"out": res.get("stdout", ""),
                          "err": res.get("stderr", "")}
            case = {"test_id": name, "status": "error",
                    "expected": "bounded collection or explicit timeout",
                    "observed": "timeout (partial output preserved privately)",
                    "metric": "unchecked",
                    "transport": "timeout", "evidence_refs": []}
        elif res["transport"] == "tool-missing":
            case = {"test_id": name, "status": "error",
                    "expected": "adb available",
                    "observed": res["reason"], "metric": "unchecked",
                    "transport": "tool-missing", "evidence_refs": []}
        elif res["transport"] == "overflow":
            raws[name] = {"out": res.get("stdout", ""),
                          "err": res.get("stderr", "")}
            case = {"test_id": name, "status": "error",
                    "expected": "output within byte bound",
                    "observed": ("output overflow beyond bound; partial "
                                 "preserved privately"), "metric": "unchecked",
                    "transport": "overflow", "evidence_refs": []}
        elif res["transport"] == "error" and "returncode" not in res:
            # No process ran (OSError): a setup failure, never an
            # unsupported empty observation.
            case = {"test_id": name, "status": "error",
                    "expected": "bounded collection",
                    "observed": res["reason"], "metric": "unchecked",
                    "transport": "error", "evidence_refs": []}
        else:
            raws[name] = {"out": res.get("stdout", ""),
                          "err": res.get("stderr", "")}
            case = classify(shell_argv, res.get("returncode", 0),
                            res.get("stdout", ""), res.get("stderr", ""))
            case["transport"] = ("process" if res["transport"] == "ok"
                                 else res["transport"])
        cases.append(case)
    if raw_dir is not None:
        run_dir = os.path.join(raw_dir, run_id)
        if os.path.exists(run_dir) and os.listdir(run_dir):
            return 3, _scrub_report(
                {"schema_version": SCHEMA_VERSION, "run_id": run_id,
                 "status": "error",
                 "observed": "refusing: raw directory not empty: " + run_dir,
                 "cases": []}, [target] + list(devs))
        os.makedirs(run_dir, exist_ok=True)
        # Public refs are stable and non-identifying (run-local relative
        # paths); the private sidecar maps them back to real locations, so
        # envelope scrubbing can never silently break resolvability (R5).
        raw_refs = {}
        refmap = {"run_id": pub(run_id), "files": {}}
        for name, streams in raws.items():
            refs = []
            for stream in ("out", "err"):
                content = streams.get(stream, "")
                if not content:
                    continue
                base = name.replace(" ", "_") + "." + stream + ".txt"
                dest = os.path.join(run_dir, base)
                with open(dest, "w") as fh:
                    fh.write(content)
                digest = hashlib.sha256(
                    content.encode("utf-8", "replace")).hexdigest()
                ref = (f"{pub(run_id)}/{base}#{name}.{stream}"
                       f"@sha256:{digest}")
                refs.append(ref)
                refmap["files"][ref] = dest
            raw_refs[name] = refs
        with open(os.path.join(run_dir, ".refmap.json"), "w") as fh:
            json.dump(refmap, fh, indent=2)
            fh.write("\n")
        raw_location = (pub(run_dir) +
                        " (resolve refs via .refmap.json there)")
    else:
        raw_location = EPHEMERAL_LABEL
        raw_refs = {}
        for name, streams in raws.items():
            for stream in ("out", "err"):
                content = streams.get(stream, "")
                if not content:
                    continue
                digest = hashlib.sha256(
                    content.encode("utf-8", "replace")).hexdigest()
                raw_refs.setdefault(name, []).append(
                    f"sha256:{digest} ({stream}, raw not persisted)")
    for case in cases:
        refs = raw_refs.get(case["test_id"], [])
        if refs:
            case["evidence_refs"] = refs
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
        # lives in the private raw bundle and is never published.
        "environment": {"source": "live-device", "device_alias": "target-1",
                        "device_role": "unassigned",
                        "conditions": conditions or
                        "unrecorded (repeat with --conditions)",
                        "repetitions": {"pass": 1, "note": "single pass; "
                                         "repetitions controlled by the hardware harness"}},
        "os_build": builds[0] if builds else "unsupported: no device",
        "raw_evidence_location": raw_location,
        "procedures": procs if procs is not None else [],
        "config_error": cfg_err,
        "cases": cases,
    }
    return 0, _scrub_report(report, [target] + list(devs))


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
