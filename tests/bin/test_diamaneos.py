"""bin/diamaneos routing: help, signing and unknown commands."""
import io
import os
import runpy
import subprocess
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CliTest(unittest.TestCase):
    def test_help(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0)
        for text in ("build all", "native publication signature", "fonts check"):
            with self.subTest(text=text):
                self.assertIn(text, out.stdout)

    def test_signing_help(self):
        out = subprocess.run(
            [os.path.join(TOOLS, "bin", "diamaneos"), "signing", "--help"],
            capture_output=True, text=True, cwd=TOOLS, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("target-files", out.stdout)

    def test_build_help(self):
        command = os.path.join(TOOLS, "bin", "diamaneos")
        out = subprocess.run([command, "build", "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("build sync|vendor|android|package|verify", out.stdout)
        bare = subprocess.run([command, "build"], capture_output=True, text=True, timeout=30)
        self.assertEqual(bare.returncode, 2)
        self.assertIn("build all", bare.stderr)

    def test_output_is_line_buffered(self):
        module = runpy.run_path(os.path.join(TOOLS, "bin", "diamaneos"), run_name="diamaneos")
        stream = io.TextIOWrapper(io.BytesIO())
        module["line_buffered"](stream)
        self.assertTrue(stream.line_buffering)

    def test_unknown_command(self):
        out = subprocess.run([os.path.join(TOOLS, "bin", "diamaneos"), "nope"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
