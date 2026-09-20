"""Deployment contract for pinned target-files signing discovery."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "builder" / "run-signing-discovery"
SERVICE = (ROOT / "deploy" / "builder" /
           "diamaneos-builder-signing-discovery.service")


class SigningDiscoveryDeploymentTest(unittest.TestCase):
    def test_runner_is_syntactically_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(RUNNER)], capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_runner_is_revision_bound_and_does_not_create_keys(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("/usr/local/sbin/diamaneos-builder-fan-check", script)
        self.assertIn('"$tools_root/bin/diamaneos" signing roles', script)
        self.assertIn("m target-files-package otatools-package", script)
        self.assertIn("--stage unsigned", script)
        self.assertIn('"dummy_keys_created": False', script)
        self.assertIn('"signing_operations": 0', script)
        self.assertNotIn("make_key", script)
        self.assertNotIn("sign_target_files_apks", script)

    def test_only_exact_presigned_review_can_be_nonpassing_discovery(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            'errors == [\n    "target-files contains an unlisted presigned package"\n]',
            script,
        )
        self.assertIn("presigned_packages", script)
        self.assertIn("NEEDS_REVIEW", script)

    def test_service_is_unprivileged_offline_and_nonpersistent(self):
        unit = SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=diamaneos-build", unit)
        self.assertIn("After=diamaneos-builder-source-sync.service", unit)
        self.assertNotIn("Requires=diamaneos-builder-source-sync.service", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("ProtectSystem=full", unit)
        self.assertIn("TimeoutStartSec=infinity", unit)
        self.assertNotIn("WantedBy=", unit)
        self.assertNotIn("sudo", unit)


if __name__ == "__main__":
    unittest.main()
