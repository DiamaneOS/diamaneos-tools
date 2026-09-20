"""Static deployment contract for disposable signing qualification."""

from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy/builder/run-dummy-signing-qualification"
SERVICE = ROOT / "deploy/builder/diamaneos-builder-dummy-signing.service"


class DummySigningDeploymentTest(unittest.TestCase):
    def test_runner_compiles_and_is_no_argument_revision_bound(self):
        script = RUNNER.read_text(encoding="utf-8")
        compile(script, str(RUNNER), "exec")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("this qualification entry point accepts no arguments", script)
        self.assertIn("qualified_unsigned_target_files_sha256", script)
        self.assertIn("qualified_otatools_sha256", script)
        self.assertIn("signing_command", script)
        self.assertIn("def make_android_key", script)
        self.assertIn("allowed=(0, 1)", script)
        self.assertIn('"openssl", "x509"', script)
        self.assertIn('"openssl", "pkcs8"', script)
        self.assertIn('partial / "key-generation.json"', script)
        self.assertIn('"--source-inventory"', script)
        self.assertIn('record.get("expected_certificate_role")', script)
        self.assertIn('prebuilts/jdk/jdk21/linux-x86/bin', script)
        self.assertIn('environment["upstream"]["release_tag"]', script)
        self.assertIn('environment["host"]["external_tools"]["node"]["version"]', script)
        self.assertIn("explicit thermal-safety preflight path is missing", script)
        self.assertNotIn("/usr/local/sbin/diamaneos-builder-fan-check", script)
        self.assertNotIn("grapheneos-allowed-signers-2026091000", script)
        self.assertNotIn("/opt/nodejs/v24.21.0/bin", script)
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

    def test_make_key_exit_one_requires_parseable_artifacts(self):
        runner = runpy.run_path(str(RUNNER))
        make_android_key = runner["make_android_key"]
        failure = runner["Failure"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "keys"
            logs = root / "logs"
            destination.mkdir()
            logs.mkdir()
            helper = root / "make-key-fixture"
            helper.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "openssl req -x509 -newkey rsa:2048 -nodes "
                "-keyout \"$1.key.pem\" -out \"$1.x509.pem\" "
                "-days 1 -subj \"$2\" >/dev/null 2>&1\n"
                "openssl pkcs8 -topk8 -inform PEM -outform DER "
                "-in \"$1.key.pem\" -nocrypt -out \"$1.pk8\"\n"
                "rm -- \"$1.key.pem\"\n"
                "exit 1\n",
                encoding="utf-8")
            helper.chmod(0o700)
            record = make_android_key(
                helper, "fixture", "fixture", "/CN=fixture/",
                destination, logs)
            self.assertEqual(1, record["make_key_exit_code"])
            self.assertEqual("PASS", record["certificate_parse"])
            self.assertEqual("PASS", record["private_key_parse"])

            malformed = root / "make-key-malformed"
            malformed.write_text(
                "#!/bin/sh\n"
                "printf bad >\"$1.x509.pem\"\n"
                "printf bad >\"$1.pk8\"\n"
                "exit 1\n",
                encoding="utf-8")
            malformed.chmod(0o700)
            with self.assertRaises(failure):
                make_android_key(
                    malformed, "bad", "bad", "/CN=bad/",
                    destination, logs)

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
        self.assertNotIn("/opt/nodejs/v24.21.0/bin", unit)


if __name__ == "__main__":
    unittest.main()
