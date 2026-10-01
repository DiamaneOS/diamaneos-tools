"""Portable builder contract."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "builder"


class BuilderPortabilityTests(unittest.TestCase):
    def test_bootstrap_supports_deb822_and_optional_wake_on_lan(self):
        script = DEPLOY / "bootstrap-debian13"
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        text = script.read_text(encoding="utf-8")
        self.assertIn("/etc/apt/sources.list.d/*.sources", text)
        self.assertIn("network-manager", text)
        self.assertIn("DIAMANEOS_REQUIRE_WOL", text)
        self.assertIn("NO_ACTIVE_ETHERNET_OPTIONAL", text)

    def test_generic_entry_points_use_the_thermal_interface(self):
        for name in ("sync-pinned-source", "run-generic-qualification"):
            with self.subTest(script=name):
                text = (DEPLOY / name).read_text(encoding="utf-8")
                self.assertIn("DIAMANEOS_THERMAL_CHECK", text)
                self.assertIn('--thermal-check "$thermal_check"', text)
                self.assertNotIn("/usr/local/sbin/", text)


if __name__ == "__main__":
    unittest.main()
