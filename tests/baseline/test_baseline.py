"""Baseline acceptance: unsupported-not-zero, target guard, redaction,
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

sys.path.insert(0, os.path.join(TOOLS, "src"))
from diamaneos_tools import baseline
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
if mode == "deny-all":
    sys.stderr.write("Permission denied\\n"); sys.exit(1)
if mode == "sleep-shell":
    time.sleep(30)
if mode == "shell-gone":
    print("error: device 'FAKE123' not found", file=sys.stderr)
    sys.exit(1)
if mode == "sleep-partial":
    print("level: 8")
    sys.stdout.flush()
    time.sleep(30)
if mode == "bad-encoding":
    sys.stdout.buffer.write(bytes([255, 254, 10]))
    print("level: 87")
    sys.stdout.buffer.write(bytes([253, 10]))
if mode == "nonascii-big":
    sys.stdout.write(chr(233) * 200000)
if mode == "endless":
    for _ in range(100000):
        sys.stdout.write("z" * 65536)
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
elif cmd == "dumpsys gfxinfo com.android.systemui":
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
        c = classify(baseline.GFXINFO_COMMAND, 0,
                     "frames...[TRUNCATED]", "")
        self.assertEqual(c["status"], "error")

    def test_gfxinfo_is_scoped_to_systemui(self):
        self.assertIn(("dumpsys", "gfxinfo", "com.android.systemui"),
                      baseline.ALLOWLIST)
        self.assertNotIn(("dumpsys", "gfxinfo"), baseline.ALLOWLIST)

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
        # run_cmd uses Popen, which inherits os.environ: set FAKE_MODE
        # around the call instead of patching subprocess.
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = mode
        try:
            return live_capture("FAKE123", adb=adb, timeout=10, **kw)
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev

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
        self.assertEqual(
            by_id["dumpsys gfxinfo com.android.systemui"]["status"],
            "error")

    def test_live_bad_battery_malformed(self):
        code, rep = self._live("bad-battery")
        by_id = {c["test_id"]: c for c in rep["cases"]}
        self.assertEqual(by_id["dumpsys battery"]["metric"], "malformed")

    def test_live_timeout_controlled(self):
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "sleep-shell"
        try:
            code, rep = live_capture("FAKE123", adb=adb, timeout=1)
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev
        self.assertEqual(code, 0)
        timeouts = [c for c in rep["cases"]
                    if c["observed"].startswith("timeout")]
        self.assertTrue(timeouts, rep)

    def test_live_devices_failure_controlled(self):
        code, rep = self._live("devices-fail")
        self.assertEqual(code, 3)
        self.assertEqual(rep["cases"], [])

    def test_live_missing_adb_controlled(self):
        code, rep = live_capture("FAKE123", adb="/nonexistent/adb-xyz",
                                 timeout=5)
        self.assertEqual(code, 3)

    # --- public envelope carries no identity or location fields ---
    def test_full_report_has_no_serial(self):
        code, rep = self._live("ok")
        self.assertEqual(code, 0)
        self.assertNotIn("FAKE123", json.dumps(rep))

    def test_location_lines_dropped(self):
        out = ("mServiceState voiceRegState=0 operatorNumeric=26201\n"
               "mTac=26201 mCi=12345678 mPci=42\n")
        c = classify(("dumpsys", "telephony.registry"), 0, out, "")
        self.assertNotIn("12345678", c["observed"])
        self.assertIn("26201", c["observed"])
        self.assertGreater(c.get("dropped_sensitive", 0), 0)

    def test_gfxinfo_keeps_stats_drops_names(self):
        out = "package: com.example.app\nframes rendered: 60\n"
        c = classify(baseline.GFXINFO_COMMAND, 0, out, "")
        self.assertNotIn("com.example.app", c["observed"])
        self.assertIn("60", c["observed"])

    # --- bounds hold while reading ---
    def test_stdout_overflow_bounded(self):
        res = baseline.run_cmd(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*600000)"],
            timeout=30)
        self.assertEqual(res["transport"], "overflow")
        self.assertLessEqual(len(res["stdout"]), baseline.MAX_OUTPUT_BYTES)

    def test_stderr_overflow_bounded(self):
        res = baseline.run_cmd(
            [sys.executable, "-c", "import sys; sys.stderr.write('y'*600000)"],
            timeout=30)
        self.assertEqual(res["transport"], "overflow")

    # --- failures keep their meaning ---
    def test_permission_denied_stays_error(self):
        c = classify(("getprop",), 1, "", "Permission denied")
        self.assertEqual(c["status"], "error")
        self.assertIn("Permission denied", c["observed"])

    def test_unknown_operator_stays_ok(self):
        c = classify(("dumpsys", "telephony.registry"), 0,
                     "mServiceState voiceRegState=0 operatorNumeric=unknown\n",
                     "")
        self.assertEqual(c["status"], "ok")

    def test_live_deny_all_partial(self):
        code, rep = self._live("deny-all")
        self.assertEqual(code, 0)
        self.assertEqual(rep["collection_status"], "partial")
        self.assertTrue(all(c["status"] == "error" for c in rep["cases"]))
        self.assertIn("Permission denied", rep["cases"][0]["observed"])

    # --- evidence cannot silently change ---
    def test_raw_dir_reuse_refused(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "ok"
        try:
            root = tempfile.mkdtemp(prefix="rawdir-")
            code1, rep1 = live_capture("FAKE123", adb=adb, timeout=10,
                                       run_id="run-one", raw_dir=root)
            self.assertEqual(code1, 0)
            code2, rep2 = live_capture("FAKE123", adb=adb, timeout=10,
                                       run_id="run-one", raw_dir=root)
            self.assertEqual(code2, 3)
            self.assertIn("not empty", rep2["observed"])
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev

    def test_evidence_refs_carry_hashes(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "ok"
        try:
            root = tempfile.mkdtemp(prefix="rawhash-")
            code, rep = live_capture("FAKE123", adb=adb, timeout=10,
                                     run_id="run-h", raw_dir=root)
            self.assertEqual(code, 0)
            self.assertTrue(all("@sha256:" in c["evidence_refs"][0]
                                for c in rep["cases"]
                                if c["evidence_refs"]))
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev


    def test_unknown_target_scrubbed(self):
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "ok"
        try:
            code, rep = live_capture("NOPE", adb=adb, timeout=10)
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev
        self.assertEqual(code, 3)
        self.assertNotIn("FAKE123", json.dumps(rep))

    def test_disconnected_error_not_unsupported(self):
        code, rep = self._live("shell-gone")
        self.assertEqual(code, 0)
        self.assertEqual(rep["collection_status"], "partial")
        self.assertTrue(all(c["status"] == "error" for c in rep["cases"]))
        self.assertNotIn("FAKE123", json.dumps(rep))

    def test_byte_cap_counts_bytes(self):
        res = baseline.run_cmd(
            [sys.executable, "-c",
             "import sys; sys.stdout.write(chr(233) * 200000)"],
            timeout=30)
        self.assertEqual(res["transport"], "overflow")
        self.assertLessEqual(len(res["stdout"].encode("utf-8")), 262144)

    def test_invalid_utf8_stays_visible(self):
        res = baseline.run_cmd(
            [sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(bytes([255, 254, 10]))"],
            timeout=30)
        self.assertEqual(res["transport"], "ok")
        self.assertIn("�", res["stdout"])

    def test_overflow_terminates_child(self):
        import time
        start = time.monotonic()
        res = baseline.run_cmd(
            [sys.executable, "-c",
             "import sys" + chr(10) + "while 1: sys.stdout.write('z' * 65536)"],
            timeout=15)
        elapsed = time.monotonic() - start
        self.assertEqual(res["transport"], "overflow")
        self.assertLess(elapsed, 12)

    def test_nonascii_stderr_bound_is_bytes(self):
        res = baseline.run_cmd(
            [sys.executable, "-c",
             "import sys; sys.stderr.write(chr(233) * 262144)"],
            timeout=30)
        self.assertEqual(res["transport"], "overflow")
        self.assertLessEqual(len(res["stderr"].encode("utf-8")), 262144)

    def test_one_byte_over_marker_killed(self):
        res = baseline.run_cmd(
            [sys.executable, "-c",
             "import sys,time" + chr(10)
             + "sys.stdout.write('x' * 262144); sys.stdout.write('y'); "
               "sys.stdout.flush(); time.sleep(0.3); "
               "sys.stdout.write('MARKER'); sys.stdout.flush()"],
            timeout=30)
        self.assertEqual(res["transport"], "overflow")
        self.assertNotIn("MARKER", res["stdout"])
        self.assertEqual(len(res["stdout"].encode("utf-8")), 262144)

    def test_timeout_partial_evidence_saved(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "sleep-partial"
        try:
            root = tempfile.mkdtemp(prefix="rawtimeout-")
            code, rep = live_capture("FAKE123", adb=adb, timeout=2,
                                     run_id="run-t", raw_dir=root)
            self.assertEqual(code, 0)
            dest = os.path.join(root, "run-t", "dumpsys_battery.out.txt")
            self.assertTrue(os.path.exists(dest))
            with open(dest) as fh: self.assertIn("level: 8", fh.read())
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev

    def test_stderr_evidence_saved(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "deny-all"
        try:
            root = tempfile.mkdtemp(prefix="rawerr-")
            code, rep = live_capture("FAKE123", adb=adb, timeout=10,
                                     run_id="run-e", raw_dir=root)
            self.assertEqual(code, 0)
            dest = os.path.join(root, "run-e", "getprop.err.txt")
            self.assertTrue(os.path.exists(dest))
            with open(dest) as fh: self.assertIn("Permission denied", fh.read())
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev


    def test_serial_dirname_refs_stay_resolvable(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "ok"
        try:
            root = tempfile.mkdtemp(prefix="raw-FAKE123-")
            code, rep = live_capture("FAKE123", adb=adb, timeout=10,
                                     run_id="run-s", raw_dir=root)
            self.assertEqual(code, 0)
            text = json.dumps(rep)
            self.assertNotIn("FAKE123", text)
            sidecar = os.path.join(root, "run-s", ".refmap.json")
            self.assertTrue(os.path.exists(sidecar))
            with open(sidecar) as fh: mapping = json.load(fh)["files"]
            for case in rep["cases"]:
                for ref in case["evidence_refs"]:
                    self.assertIn(ref, mapping)
                    real = mapping[ref]
                    self.assertNotIn("FAKE123", ref)
                    self.assertTrue(os.path.exists(real))
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev


    def test_serial_run_id_refs_stay_resolvable(self):
        import tempfile
        adb = self._fake()
        prev = os.environ.get("FAKE_MODE")
        os.environ["FAKE_MODE"] = "ok"
        try:
            root = tempfile.mkdtemp(prefix="rawrun-")
            code, rep = live_capture("FAKE123", adb=adb, timeout=10,
                                     run_id="FAKE123-run", raw_dir=root)
            self.assertEqual(code, 0)
            text = json.dumps(rep)
            self.assertNotIn("FAKE123", text)
            sidecar = os.path.join(root, "FAKE123-run", ".refmap.json")
            self.assertTrue(os.path.exists(sidecar))
            with open(sidecar) as fh: mapping = json.load(fh)["files"]
            for case in rep["cases"]:
                for ref in case["evidence_refs"]:
                    self.assertIn(ref, mapping)
                    self.assertTrue(os.path.exists(mapping[ref]))
        finally:
            if prev is None:
                os.environ.pop("FAKE_MODE", None)
            else:
                os.environ["FAKE_MODE"] = prev


    def test_inherited_pipe_deadline_holds(self):
        import time
        child = ("import subprocess,sys; "
                 "subprocess.Popen([sys.executable, '-c', "
                 "'import time; time.sleep(1.0)']); "
                 "print('parent finished', flush=True)")
        start = time.monotonic()
        res = baseline.run_cmd([sys.executable, "-c", child], timeout=0.1)
        elapsed = time.monotonic() - start
        self.assertEqual(res["transport"], "timeout")
        self.assertIn("parent finished", res["stdout"])
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
