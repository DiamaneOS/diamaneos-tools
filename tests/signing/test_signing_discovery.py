"""Deployment contract for pinned target-files signing discovery."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "builder" / "run-signing-discovery"
SERVICE = (ROOT / "deploy" / "builder" /
           "diamaneos-builder-signing-discovery.service")
OTA_SERVICE = (ROOT / "deploy" / "builder" /
               "diamaneos-builder-ota-signing-discovery.service")


class SigningDiscoveryDeploymentTest(unittest.TestCase):
    def test_runner_is_syntactically_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(RUNNER)], capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_runner_is_revision_bound_and_does_not_create_keys(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("must name an explicit absolute path", script)
        self.assertNotIn("/usr/local/sbin/diamaneos-builder-fan-check", script)
        self.assertIn('environment["upstream"]["release_tag"]', script)
        self.assertIn('environment["host"]["external_tools"]["node"]["version"]', script)
        self.assertNotIn("grapheneos-allowed-signers-2026091000", script)
        self.assertNotIn("/opt/nodejs/v24.21.0/bin", script)
        self.assertIn('"$tools_root/bin/diamaneos" signing roles', script)
        self.assertIn("m target-files-package otatools-package", script)
        self.assertIn("--stage unsigned", script)
        self.assertIn('"dummy_keys_created": False', script)
        self.assertIn('"signing_operations": 0', script)
        self.assertNotIn("make_key", script)
        self.assertNotIn("sign_target_files_apks", script)

    def test_runner_has_exact_default_and_ota_profile_bindings(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_SIGNING_PROFILE_ID", script)
        self.assertIn("unsupported signing-discovery profile binding", script)
        self.assertIn(
            "generic-x86_64-ota-qualification:ota-signing-discovery",
            script,
        )

    def test_ota_artifact_resolution_is_module_bounded_and_unique(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("resolve_unique_artifact", script)
        self.assertIn(
            "$output_root/soong/.intermediates/build/soong/fsgen/"
            "${TARGET_PRODUCT}_generated_device",
            script,
        )
        self.assertIn(
            "$output_root/soong/.intermediates/build/make/tools/"
            "otatools_package/otatools-package",
            script,
        )
        self.assertIn(
            "module root did not contain exactly one artifact", script,
        )
        self.assertIn("artifact escaped its module root", script)
        self.assertNotIn('find "$OUT"', script)
        self.assertNotIn(
            "$OUT/soong/.intermediates", script,
        )

    def test_ota_artifact_paths_match_the_soong_output_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "out"
            target_module = (
                output_root / "soong" / ".intermediates" / "build" /
                "soong" / "fsgen" /
                "aosp_cf_x86_64_phone_generated_device"
            )
            target = (
                target_module / "android_x86_64_silvermont" /
                "target_files.zip"
            )
            otatools_module = (
                output_root / "soong" / ".intermediates" / "build" /
                "make" / "tools" / "otatools_package" /
                "otatools-package"
            )
            otatools = (
                otatools_module / "linux_glibc_x86_64" / "gen" /
                "otatools.zip"
            )
            target.parent.mkdir(parents=True)
            otatools.parent.mkdir(parents=True)
            target.write_bytes(b"target-files fixture")
            otatools.write_bytes(b"otatools fixture")

            self.assertEqual(
                [target],
                list(target_module.glob("*/target_files.zip")),
            )
            self.assertEqual(
                [otatools],
                list(otatools_module.glob("*/*/otatools.zip")),
            )

    def test_explicit_failure_reason_is_preserved_in_result(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("failure_reason=$*", script)
        self.assertIn('result["error"] = error', script)
        self.assertIn(
            "resolve_unique_artifact target_files", script,
        )
        self.assertNotIn(
            "target_files=$(resolve_unique_artifact", script,
        )

    def test_only_exact_presigned_review_can_be_nonpassing_discovery(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            'errors == [\n    "target-files contains an unlisted presigned package"\n]',
            script,
        )
        self.assertIn("presigned_packages", script)
        self.assertIn("NEEDS_REVIEW", script)

    def test_service_is_unprivileged_offline_and_nonpersistent(self):
        for path in (SERVICE, OTA_SERVICE):
            unit = path.read_text(encoding="utf-8")
            self.assertIn("User=diamaneos-build", unit)
            self.assertIn("After=diamaneos-builder-source-sync.service", unit)
            self.assertNotIn("Requires=diamaneos-builder-source-sync.service", unit)
            self.assertIn("IPAddressDeny=any", unit)
            self.assertIn("NoNewPrivileges=yes", unit)
            self.assertIn("ProtectSystem=full", unit)
            self.assertIn("TimeoutStartSec=infinity", unit)
            self.assertNotIn("WantedBy=", unit)
            self.assertNotIn("sudo", unit)
            self.assertNotIn("/opt/nodejs/v24.21.0/bin", unit)

    def test_ota_service_selects_the_fixed_virtual_ab_profile(self):
        unit = OTA_SERVICE.read_text(encoding="utf-8")
        self.assertIn(
            "Environment=DIAMANEOS_SIGNING_PROFILE_ID="
            "generic-x86_64-ota-qualification", unit)
        self.assertIn(
            "Environment=DIAMANEOS_DISCOVERY_RUN_PREFIX="
            "ota-signing-discovery", unit)


if __name__ == "__main__":
    unittest.main()
