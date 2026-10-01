"""bin/diamaneos routing: help, signing and unknown commands."""
import os
import subprocess
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CliTest(unittest.TestCase):
    def test_help(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertIn("build all", out.stdout)

    def test_signing_help(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "signing", "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("target-files", out.stdout)
        self.assertIn("dummy-proof", subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30).stdout)

    def test_unknown_command(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "nope"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
