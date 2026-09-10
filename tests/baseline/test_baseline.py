"""baseline capture acceptance: unsupported-not-zero, target guard, redaction,
plus live-boundary classification through a fake adb transport (no hardware)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "baseline", os.path.join(TOOLS, "src", "diamaneos_tools", "baseline.py"))
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)
resolve_target = baseline.resolve_target
run_fixture = baseline.run_fixture
classify = baseline.classify
live_capture = baseline.live_capture

FAKE_ADB = """#!/usr/bin/env python3
import os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
args = sys.argv[1:]
def emit(s):
    sys.stdout.write(s)
if args == ["version"]:
    emit("Android Debug Bridge version 1.0.41\\n"); sys.exit(0)
if args == ["devices"]:
    if mode == "devices-fail":
        sys.stderr.write("daemon failed\\n"); sys.exit(1)
    emit("List of devices attached\\nFAKE123\\tdevice\\n"); sys.exit(0)
assert args[:2] == ["-s", "FAKE123"] and args[2] == "shell", args
cmd = " ".join(args[3:])
if mode == "sleep-shell":
    time.sleep(30)
if cmd == "getprop":
    emit("[ro.build.id]: [FP6.QREL.16.82.0]\\n[ro.product.model]: [FP6]\\n")
elif cmd == "dumpsys carrier_config":
    emit("mccmnc=26201\\nVoLTE=true\\n")
elif cmd == "dumpsys telephony.registry":
    if mode == "missing-service":
        emit("Can't find service: telephony.registry\\n"); sys.exit(0)
    emit("mServiceState voiceRegState=0 operatorNumeric=26201\\n")
elif cmd == "dumpsys imsservice":
    emit("ims voiceCapable=true registered=true\\n")
elif cmd == "dumpsys battery":
    if mode == "bad-battery":
        emit("level: banana\\n")
    else:
        emit("level: 87\\nscale: 100\\nstatus: 3\\n")
elif cmd == "dumpsys gfxinfo":
    if mode == "truncated-out":
        emit("frames...[TRUNCATED]")
    else:
        emit("frames rendered: 60\\n")
sys.exit(0)
"""


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as fh:
        return json.load(fh)


class BaselineTest(unittest.TestCase):
    # --- fixture contract  ---
    def test_valid_fixture_ok(self):
        rep = run_fixture(fixture("valid.json"))
        self.assertTrue(all(c["status"] == "ok" for c in rep["cases"]), rep)
        self.assertEqual(rep["os_build"], "synthetic FP6.QREL.16.82.0")

    def test_truncated_is_error_not_zero(self):
        rep = run_fixture(fixture("truncated.json"))
        self.assertEqual(rep["cases"][0]["status"], "error")

    def test_missing_metric_unsupported_not_zero(self):
        rep = run_fixture(fixture("unsupported.json"))
        self.assertEqual(rep["cases"][0]["status"], "unsupported")

    def test_sensitive_redacted(self):
        rep = run_fixture(fixture("sensitive.json"))
        obs = rep["cases"][0]["observed"]
        self.assertNotIn("490154203237518", obs)
        self.assertNotIn("FP6ABC123", obs)
        self.assertNotIn("8949221100001234567", obs)
        self.assertIn("FP6.QREL.16.82.0", obs)  # useful context preserved

    def test_ambiguous_target_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "AMBIGUOUS_TARGET"):
            resolve_target(["emulator-5554", "FP6ABC123"], None)
        self.assertEqual(resolve_target(["only-one"], None), "only-one")

    def test_timeout_is_error(self):
        rep = run_fixture(fixture("timeout.json"))
        self.assertEqual(rep["cases"][0]["status"], "error")
        self.assertIn("timeout", rep["cases"][0]["observed"])

    def test_raw_evidence_location_recorded(self):
        for name in ("valid.json", "timeout.json"):
            rep = run_fixture(fixture(name))
            self.assertIn("raw_evidence_location", rep)
            self.assertTrue(rep["raw_evidence_location"])

    def test_dry_run_writes_nothing(self):
        out = subprocess.run(
            [sys.executable, "src/diamaneos_tools/baseline.py", "--dry-run"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("target_required", out.stdout)

    # --- shared classification boundary (fixtures AND live) ---
    def test_exit_zero_missing_service_is_unsupported(self):
        c = classify(("dumpsys", "telephony.registry"), 0,
                     "Can't find service: telephony.registry\n", "")
        self.assertEqual(c["status"], "unsupported")

    def test_exit_zero_truncated_is_error(self):
        c = classify(("dumpsys", "gfxinfo"), 0, "frames...[TRUNCATED]", "")
        self.assertEqual(c["status"], "error")

    def test_malformed_battery_not_a_measurement(self):
        c = classify(("dumpsys", "battery"), 0, "level: banana\n", "")
        self.assertEqual(c["status"], "ok")
        self.assertEqual(c["metric"], "malformed")

    def test_identifier_allowlist(self):
        out = ("mServiceState voiceRegState=0 operatorNumeric=26201\n"
               "mImsi=310150123456789 phone=+15551234567 "
               "eid=89049032001012345678901234567890 "
               "user=someone@example.com mac=AA:BB:CC:DD:EE:FF\n")
        c = classify(("dumpsys", "telephony.registry"), 0, out, "")
        for leaked in ("310150123456789", "+15551234567",
                       "89049032001012345678901234567890",
                       "someone@example.com", "AA:BB:CC:DD:EE:FF"):
            self.assertNotIn(leaked, c["observed"], c)
        self.assertIn("26201", c["observed"])  # useful context preserved

    def test_stderr_is_redacted(self):
        c = classify(("getprop",), 1, "", "serialno: FP6ABC123")
        self.assertNotIn("FP6ABC123", c["observed"])

    def test_oversized_output_rejected(self):
        big = "mServiceState " + "x" * (baseline.MAX_OUTPUT_BYTES + 1)
        c = classify(("dumpsys", "telephony.registry"), 0, big, "")
        self.assertEqual(c["status"], "error")

    # --- live path through fake transport ---
    def _fake(self):
        tmp = tempfile.mkdtemp(prefix="fakeadb-")
        path = os.path.join(tmp, "adb")
        with open(path, "w") as fh:
            fh.write(FAKE_ADB)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def _live(self, mode, **kw):
        adb = self._fake()
        env = dict(os.environ, FAKE_MODE=mode)
        # live_capture uses run_cmd -> subprocess; inject env via wrapper
        old_run = subprocess.run
        def patched(*a, **k):
            k.setdefault("env", env)
            return old_run(*a, **k)
        subprocess.run = patched
        try:
            return live_capture("FAKE123", adb=adb, timeout=10, **kw)
        finally:
            subprocess.run = old_run

    def test_live_ok_via_fake_transport(self):
        code, rep = self._live("ok")
        self.assertEqual(code, 0, rep)
        self.assertEqual(len(rep["cases"]), 6)
        by_id = {c["test_id"]: c for c in rep["cases"]}
        self.assertEqual(by_id["dumpsys battery"]["metric"], "valid")
        self.assertTrue(rep["raw_evidence_location"])
        self.assertTrue(all(c["evidence_refs"] for c in rep["cases"]))

    def test_live_missing_service_unsupported(self):
        code, rep = self._live("missing-service")
        by_id = {c["test_id"]: c for c in rep["cases"]}
        self.assertEqual(by_id["dumpsys telephony.registry"]["status"],
                         "unsupported")

    def test_live_truncated_error(self):
        code, rep = self._live("truncated-out")
        by_id = {c["test_id"]: c for c in rep["cases"]}
        self.assertEqual(by_id["dumpsys gfxinfo"]["status"], "error")

    def test_live_bad_battery_malformed(self):
        code, rep = self._live("bad-battery")
        by_id = {c["test_id"]: c for c in rep["cases"]}
        self.assertEqual(by_id["dumpsys battery"]["metric"], "malformed")

    def test_live_timeout_controlled(self):
        adb = self._fake()
        env = dict(os.environ, FAKE_MODE="sleep-shell")
        old_run = subprocess.run
        def patched(*a, **k):
            k.setdefault("env", env)
            return old_run(*a, **k)
        subprocess.run = patched
        try:
            code, rep = live_capture("FAKE123", adb=adb, timeout=1)
        finally:
            subprocess.run = old_run
        self.assertEqual(code, 0)
        timeouts = [c for c in rep["cases"] if c["observed"] == "timeout"]
        self.assertTrue(timeouts, rep)

    def test_live_devices_failure_controlled(self):
        code, rep = self._live("devices-fail")
        self.assertEqual(code, 3)
        self.assertEqual(rep["cases"], [])

    def test_live_missing_adb_controlled(self):
        code, rep = live_capture("FAKE123", adb="/nonexistent/adb-xyz",
                                 timeout=5)
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
