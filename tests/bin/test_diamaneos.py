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

    def test_baseline_capture_rejects_incomplete_rig_binding_before_adb(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "baseline", "capture", "--target", "private-target",
             "--rig-config", "/private/rig.json",
             "--adb", "/does/not/exist"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 2)
        self.assertIn("requires --rig-config, --device-role and --device-map",
                      out.stderr)
        self.assertNotIn("executable not found", out.stderr)

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

    def test_baseline_boot_dry_run_contacts_no_device(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"),
             "baseline", "boot", "dry-run"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"device_commands_executed": 0', out.stdout)
        self.assertIn("manual-source-media-required", out.stdout)

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

    def test_rig_help(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "rig", "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("identity-bound USB test-rig control", out.stdout)

    def test_signing_help(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "signing", "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("target-files", out.stdout)
        self.assertIn("dummy-proof", subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30).stdout)

    def test_compatibility_help(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "test",
             "compatibility", "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--dry-run", out.stdout)
        self.assertIn("--run-trial", out.stdout)

    def test_unknown_command(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "nope"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
