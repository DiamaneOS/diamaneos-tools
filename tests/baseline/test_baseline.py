"""baseline capture fixture acceptance: unsupported-not-zero, target guard, redaction."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "baseline", os.path.join(TOOLS, "src", "diamaneos_tools", "baseline.py"))
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)
redact, resolve_target, run_fixture = (
    baseline.redact, baseline.resolve_target, baseline.run_fixture)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as fh:
        return json.load(fh)


class BaselineTest(unittest.TestCase):
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

    def test_dry_run_writes_nothing(self):
        out = subprocess.run(
            [sys.executable, "src/diamaneos_tools/baseline.py", "--dry-run"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("target_required", out.stdout)


if __name__ == "__main__":
    unittest.main()
