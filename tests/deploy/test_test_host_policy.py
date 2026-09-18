"""Safety contract for the passwordless test-runner delegation."""

from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[2]
POLICY = TOOLS / "deploy" / "test-host" / "diamaneos-runner.sudoers"
INSTALLER = TOOLS / "deploy" / "test-host" / "install-runner-sudoers"
MAINTENANCE = (TOOLS / "deploy" / "test-host" /
               "diamaneos-rig-maintenance-control")
DELEGATED = (TOOLS / "deploy" / "test-host" /
             "diamaneos-delegated-runner")
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import delegated_runner


class TestHostPolicyTest(unittest.TestCase):
    def test_delegation_never_grants_generic_execution(self):
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn("(DIAMANEOS_TEST_RUNNER) NOPASSWD:", policy)
        self.assertIn(
            "%diamaneos-test ALL=(root) NOPASSWD: "
            "DIAMANEOS_MAINTENANCE_CONTROL", policy)
        self.assertNotRegex(policy, r"/bin/(?:ba)?sh\b")
        self.assertNotIn("/usr/bin/env", policy)
        self.assertNotIn("systemctl", policy)
        self.assertNotIn("git ", policy)
        self.assertNotIn("fastboot", policy)
        self.assertNotIn(" flash ", policy)

    def test_only_fixed_maintenance_actions_run_as_root(self):
        policy = POLICY.read_text(encoding="utf-8")
        root_routes = {
            match.group(1)
            for match in re.finditer(
                r"/opt/diamaneos/tools/deploy/test-host/"
                r"diamaneos-rig-maintenance-control ([a-z-]+)", policy)
        }
        self.assertEqual({"enable", "disable-and-restore"}, root_routes)

        controller = MAINTENANCE.read_text(encoding="utf-8")
        self.assertIn('[ "$#" -eq 1 ]', controller)
        self.assertIn('case "$1" in', controller)
        self.assertNotIn("eval ", controller)
        self.assertNotIn("$2", controller)
        self.assertNotIn("fastboot", controller)
        self.assertNotIn("git ", controller)
        self.assertIn("verify_both_present", controller)
        self.assertIn("verify_no_leases", controller)

    def test_only_reviewed_cli_routes_are_delegated(self):
        policy = POLICY.read_text(encoding="utf-8")
        wrapper = "/opt/diamaneos/tools/deploy/test-host/diamaneos-delegated-runner"
        self.assertIn(wrapper + " *", policy)
        self.assertNotIn("/opt/diamaneos/tools/bin/diamaneos ", policy)
        self.assertEqual(1, policy.count(wrapper + " *"))

    def test_wrapper_rejects_caller_selected_program_and_abbreviation(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            fake = Path(directory) / "fake-adb"
            fake.write_text("#!/bin/sh\ntouch " + str(marker) + "\n",
                            encoding="utf-8")
            for option in ("--adb", "--ad"):
                with self.subTest(option=option):
                    with self.assertRaises(delegated_runner.DelegationError):
                        delegated_runner.validate_argv([
                            "baseline", "capture", "--target", "synthetic",
                            option, str(fake),
                        ])
            self.assertFalse(marker.exists())

    def test_wrapper_constrains_configuration_inputs_and_private_outputs(self):
        rejected = [
            ["baseline", "protocol", "validate", "--config", "/tmp/x.json"],
            ["baseline", "protocol", "validate", "--config",
             "/etc/diamaneos/rig.json"],
            ["baseline", "capture", "--fixture", "/tmp/x.json"],
            ["endpoints", "validate", "--services", "/tmp/services.json"],
            ["test", "run", "--suite", "/tmp/suite.json", "--dry-run"],
            ["test", "run", "--suite", "smoke", "--destructive"],
            ["baseline", "idle", "status", "--run-dir", "/tmp/run"],
            ["baseline", "idle", "series", "run", "--job-dir",
             "/var/lib/diamaneos-test/idle-series/job",
             "--expected-spec-sha256", "a" * 64],
        ]
        for argv in rejected:
            with self.subTest(argv=argv):
                with self.assertRaises(delegated_runner.DelegationError):
                    delegated_runner.validate_argv(argv)

        accepted = [
            "baseline", "idle", "series", "launch",
            "--series-id", "stock16-final",
            "--device-map", "/var/lib/diamaneos-test/devices/test-host.json",
            "--rig-config", "/etc/diamaneos/rig.json",
            "--output", "/var/lib/diamaneos-test/baseline-runs",
            "--job-root", "/var/lib/diamaneos-test/idle-series",
            "--expected-build", "FP6.QREL.16.100.0",
            "--conditions", "fixed",
        ]
        self.assertEqual(accepted, delegated_runner.validate_argv(accepted))

    def test_wrapper_executes_only_fixed_python_cli_and_environment(self):
        argv = ["rig", "status", "--config", "/etc/diamaneos/rig.json"]
        with mock.patch.object(delegated_runner.os, "execve") as execute:
            self.assertEqual(0, delegated_runner.main(argv))
        executable, command, environment = execute.call_args.args
        self.assertEqual("/usr/bin/python3", executable)
        self.assertEqual(
            ["/usr/bin/python3", "/opt/diamaneos/tools/bin/diamaneos", *argv],
            command)
        self.assertEqual("/var/lib/diamaneos-test", environment["HOME"])
        self.assertNotIn("PYTHONPATH", environment)

    def test_installer_has_fixed_source_and_target(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("source_file=/opt/diamaneos/tools/deploy/test-host/diamaneos-runner.sudoers", installer)
        self.assertIn("target_file=/etc/sudoers.d/diamaneos-runner", installer)
        self.assertIn(
            "runner_file=/opt/diamaneos/tools/deploy/test-host/"
            "diamaneos-delegated-runner", installer)
        self.assertIn('[ -x "$runner_file" ]', installer)
        self.assertIn('/usr/sbin/visudo -cf "$source_file"', installer)
        self.assertIn("find /opt/diamaneos/tools -xdev", installer)
        self.assertNotIn("$1", installer)

    def test_delegated_entry_point_is_fixed_and_imports_only_deployed_code(self):
        wrapper = DELEGATED.read_text(encoding="utf-8")
        self.assertTrue(wrapper.startswith("#!/usr/bin/python3\n"))
        self.assertIn('sys.path.insert(0, "/opt/diamaneos/tools/src")', wrapper)
        self.assertNotIn("/usr/bin/env", wrapper)


if __name__ == "__main__":
    unittest.main()
