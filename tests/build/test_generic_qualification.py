"""Deployment contract for the clean generic Android build."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy" / "builder" / "run-generic-qualification"
SERVICE = (ROOT / "deploy" / "builder" /
           "diamaneos-builder-generic-qualification.service")
SYNC = ROOT / "deploy" / "builder" / "sync-pinned-source"
SYNC_SERVICE = (ROOT / "deploy" / "builder" /
                "diamaneos-builder-source-sync.service")


class GenericQualificationDeploymentTests(unittest.TestCase):
    def test_shell_runner_is_syntactically_valid(self):
        for script in (RUNNER, SYNC):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_runner_uses_declared_source_local_output_path(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            'relative_out=$(realpath --relative-to="$source_root" "$output_root")',
            script,
        )
        self.assertIn('export OUT_DIR=$relative_out', script)
        self.assertIn("build-facing output path escapes the source root", script)
        self.assertIn(
            '[[ $(realpath -m "$source_root/$relative_out") == "$output_root" ]]',
            script,
        )
        self.assertNotIn('export OUT_DIR=$output_root', script)
        self.assertLess(script.index("build preflight"),
                        script.index("export OUT_DIR=$relative_out"))

    def test_runner_fails_closed_before_build_and_records_exact_revision(self):
        script = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("DIAMANEOS_THERMAL_CHECK", script)
        self.assertIn('--thermal-check "$thermal_check"', script)
        self.assertIn("--require-empty-output", script)
        self.assertIn("production_signing_material_used", script)
        self.assertLess(script.index("--require-empty-output"),
                        script.index("\nm\n"))
        self.assertIn("repo manifest -r", script)

    def test_service_uses_unprivileged_identity_and_blocks_network(self):
        unit = SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=diamaneos-build", unit)
        self.assertIn("Requires=diamaneos-builder-source-sync.service", unit)
        self.assertNotIn("diamaneos-builder-fan-guard.service", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("ProtectSystem=full", unit)
        self.assertNotIn("\nProtectSystem=strict\n", unit)
        self.assertNotIn("\nProtectHostname=", unit)
        self.assertNotIn("\nProtectKernelTunables=", unit)
        self.assertNotIn("\nProtectKernelModules=", unit)
        self.assertNotIn("\nProtectKernelLogs=", unit)
        self.assertNotIn("\nProtectControlGroups=", unit)
        self.assertNotIn("\nRestrictAddressFamilies=", unit)
        self.assertIn("TimeoutStartSec=infinity", unit)
        self.assertNotIn("WantedBy=", unit)
        self.assertNotIn("sudo", unit)

    def test_source_sync_binds_revision_and_has_networked_unprivileged_unit(self):
        script = SYNC.read_text(encoding="utf-8")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("DIAMANEOS_THERMAL_CHECK", script)
        self.assertIn('--thermal-check "$thermal_check"', script)
        unit = SYNC_SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=diamaneos-build", unit)
        self.assertIn("EnvironmentFile=/etc/diamaneos/builder-source-sync.env", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", unit)
        self.assertNotIn("IPAddressDeny=any", unit)
        self.assertNotIn("WantedBy=", unit)


if __name__ == "__main__":
    unittest.main()
