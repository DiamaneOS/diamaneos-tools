"""Static guards for the service-owned compatibility boundary."""

from pathlib import Path
import stat
import unittest


TOOLS = Path(__file__).resolve().parents[2]
RUNNER = TOOLS / "deploy/builder/run-compatibility-job"
UNIT = TOOLS / "deploy/builder/diamaneos-builder-compatibility@.service"
ADB_UNIT = TOOLS / "deploy/builder/diamaneos-builder-adb.service"


class CompatibilityDeploymentTest(unittest.TestCase):
    def test_runner_is_executable_and_fixes_sensitive_paths(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertTrue(RUNNER.stat().st_mode & stat.S_IXUSR)
        for value in (
                'Path("/opt/diamaneos/tools")',
                'Path("/var/lib/diamaneos-build")',
                'Path("/etc/diamaneos/builder-compatibility-rig.json")',
                'Path("/usr/local/bin/adb")'):
            self.assertIn(value, text)
        self.assertIn('item.get("role") == "harness"', text)
        self.assertIn('"status", "--porcelain=v1"', text)
        self.assertNotIn("shell=True", text)

    def test_unit_is_nonrecurring_unprivileged_and_hardened(self):
        text = UNIT.read_text(encoding="utf-8")
        for value in (
                "Type=oneshot", "User=diamaneos-build",
                "NoNewPrivileges=yes", "ProtectSystem=strict",
                "ProtectHome=yes", "PrivateTmp=yes",
                "RestrictSUIDSGID=yes"):
            self.assertIn(value, text)
        self.assertNotIn("[Install]", text)
        self.assertNotIn("User=root", text)

    def test_adb_service_is_usb_only_unprivileged_and_nonrecurring(self):
        text = ADB_UNIT.read_text(encoding="utf-8")
        for value in (
                "User=diamaneos-build", "ADB_MDNS=0",
                "tcp:localhost:5037", "NoNewPrivileges=yes",
                "ProtectSystem=strict"):
            self.assertIn(value, text)
        self.assertNotIn("[Install]", text)


if __name__ == "__main__":
    unittest.main()
