"""bin/diamaneos routing: baseline capture behaves like the module entry."""
import os
import subprocess
import sys
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CliTest(unittest.TestCase):
    def test_help(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("baseline capture", out.stdout)

    def test_baseline_capture_dry_run(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "baseline", "capture", "--dry-run"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("target_required", out.stdout)

    def test_baseline_protocol_validate(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "baseline", "protocol", "validate"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"status": "VALID"', out.stdout)
        self.assertIn('"device_commands_executed": 0', out.stdout)

    def test_baseline_pilot_dry_run_contacts_no_device(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "baseline", "pilot", "--dry-run", "--adb", "/does/not/exist"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"device_commands_executed": 0', out.stdout)
        self.assertIn("PILOT_ONLY_NOT_BASELINE_EVIDENCE", out.stdout)

    def test_device_runner_dry_run(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "test", "run", "--suite", "smoke", "--dry-run"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"device_commands_executed": 0', out.stdout)

    def test_carrier_matrix_validate(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "carrier", "matrix", "validate"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Plan eligibility is not device evidence", out.stdout)

    def test_unknown_command(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "nope"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
