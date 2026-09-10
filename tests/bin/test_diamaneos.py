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

    def test_unknown_command(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "nope"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
