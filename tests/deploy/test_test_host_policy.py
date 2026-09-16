"""Safety contract for the passwordless test-runner delegation."""

from pathlib import Path
import re
import unittest


TOOLS = Path(__file__).resolve().parents[2]
POLICY = TOOLS / "deploy" / "test-host" / "diamaneos-runner.sudoers"
INSTALLER = TOOLS / "deploy" / "test-host" / "install-runner-sudoers"


class TestHostPolicyTest(unittest.TestCase):
    def test_delegation_never_grants_root_or_generic_execution(self):
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn("(DIAMANEOS_TEST_RUNNER) NOPASSWD:", policy)
        self.assertNotIn("(root)", policy)
        self.assertNotRegex(policy, r"/bin/(?:ba)?sh\b")
        self.assertNotIn("/usr/bin/env", policy)
        self.assertNotIn("systemctl", policy)
        self.assertNotIn("git ", policy)
        self.assertNotIn("fastboot", policy)
        self.assertNotIn(" flash ", policy)

    def test_only_reviewed_cli_routes_are_delegated(self):
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn(
            "/opt/diamaneos/tools/bin/diamaneos baseline protocol validate,",
            policy)
        self.assertIn(
            "/opt/diamaneos/tools/bin/diamaneos carrier matrix validate,",
            policy)
        self.assertIn(
            "/opt/diamaneos/tools/bin/diamaneos endpoints validate,",
            policy)
        routes = {
            match.group(1).strip()
            for match in re.finditer(
                r"/opt/diamaneos/tools/bin/diamaneos ([^*\\\n]+) \*", policy)
        }
        self.assertEqual({
            "baseline capture",
            "baseline pilot",
            "baseline connected",
            "baseline camera",
            "baseline idle",
            "baseline protocol validate",
            "carrier matrix validate",
            "endpoints validate",
            "test run",
            "rig validate",
            "rig dry-run",
            "rig status",
            "rig power",
            "rig maintain",
            "rig inhibit list",
            "rig inhibit acquire",
            "rig inhibit release",
        }, routes)

    def test_installer_has_fixed_source_and_target(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("source_file=/opt/diamaneos/tools/deploy/test-host/diamaneos-runner.sudoers", installer)
        self.assertIn("target_file=/etc/sudoers.d/diamaneos-runner", installer)
        self.assertIn('/usr/sbin/visudo -cf "$source_file"', installer)
        self.assertIn("find /opt/diamaneos/tools -xdev", installer)
        self.assertNotIn("$1", installer)


if __name__ == "__main__":
    unittest.main()
