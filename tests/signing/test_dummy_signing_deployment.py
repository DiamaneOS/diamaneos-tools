"""Static deployment contract for disposable signing qualification."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy/builder/run-dummy-signing-qualification"
SERVICE = ROOT / "deploy/builder/diamaneos-builder-dummy-signing.service"


class DummySigningDeploymentTest(unittest.TestCase):
    def test_runner_compiles_and_is_no_argument_revision_bound(self):
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(RUNNER)],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("this qualification entry point accepts no arguments", script)
        self.assertIn("qualified_unsigned_target_files_sha256", script)
        self.assertIn("qualified_otatools_sha256", script)
        self.assertIn("signing_command", script)
        planner = (ROOT / "src/diamaneos_tools/signing_qualification.py").read_text(
            encoding="utf-8")
        self.assertNotIn("--override_apk_keys", planner)
        self.assertNotIn("--override_apex_keys", planner)

    def test_runner_covers_signing_and_wrong_key_verifiers(self):
        script = RUNNER.read_text(encoding="utf-8")
        for token in (
                "sign_target_files_apks", "ota_from_target_files",
                "img_from_target_files", "apksigner", "avbtool",
                "check_ota_package_signature", "ssh-keygen", "wrong_exit_code",
                "restart-recovery"):
            self.assertIn(token, script)
        self.assertIn("shutil.rmtree(private_root)", script)
        self.assertIn(
            '"private_key_directory_present_during_verification": False',
            script)

    def test_service_is_unprivileged_offline_and_nonpersistent(self):
        unit = SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=diamaneos-build", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("PrivateDevices=yes", unit)
        self.assertIn("ProtectSystem=full", unit)
        self.assertIn("TimeoutStartSec=infinity", unit)
        self.assertNotIn("WantedBy=", unit)
        self.assertNotIn("sudo", unit)


if __name__ == "__main__":
    unittest.main()
