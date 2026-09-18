"""Declared connected-workflow contract tests."""

from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_connected
from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol


class ConnectedCliTest(unittest.TestCase):
    def test_run_requires_series_and_repeat_identity_without_manual_environment_input(self):
        parser = baseline_connected.build_parser()
        args = parser.parse_args([
            "run", "--target", "private", "--device-role", "harness",
            "--device-map", "map.json", "--run-id", "declared-r1",
            "--series-id", "stock16-series", "--repeat-index", "1",
            "--expected-build", "FP6.QREL.16.100.0",
            "--output", "runs", "--conditions", "fixed",
        ])
        self.assertEqual("stock16-series", args.series_id)
        self.assertEqual(1, args.repeat_index)
        self.assertEqual("FP6.QREL.16.100.0", args.expected_build)

    def test_dry_run_is_declared_and_runs_no_device_commands(self):
        plan = baseline_pilot.dry_run_plan(
            str(TOOLS / "config" / "baseline.json"), declared=True)
        self.assertEqual("DECLARED_STOCK_BASELINE_EVIDENCE", plan["label"])
        self.assertEqual(0, plan["device_commands_executed"])
        self.assertEqual(3, plan["run_profile"]["frame_repetitions"])

    def test_declared_template_has_no_manual_environment_fields(self):
        protocol, digest = baseline_protocol.load_protocol(
            TOOLS / "config" / "baseline.json")
        args = types.SimpleNamespace(
            run_id="declared-r1", series_id="stock16-series", repeat_index=1,
            adb="adb", device_role="harness", conditions="fixed",
            expected_build="FP6.QREL.16.100.0")
        identity = {
            "model": "Fairphone 6", "device": "FP6", "build_id": "stock",
            "incremental": "inc", "firmware": "fw", "build_type": "user",
            "security_patch": "2026-01-01", "evidence_label": "USER_BUILD_EVIDENCE",
        }
        original = baseline_pilot._git_revision
        baseline_pilot._git_revision = lambda _root: "a" * 40
        original_adb = baseline_pilot.test_runner._adb_version
        baseline_pilot.test_runner._adb_version = lambda _adb: "adb"
        try:
            report = baseline_pilot._report_template(
                args, protocol, digest, TOOLS, identity, [],
                baseline_pilot.connected_profile(protocol, True))
        finally:
            baseline_pilot._git_revision = original
            baseline_pilot.test_runner._adb_version = original_adb
        self.assertEqual("INCOMPLETE", report["status"])

    def test_start_guard_restores_before_device_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            restored = False
            guard = mock.Mock()

            def acquire_guard(*_args):
                nonlocal restored
                restored = True
                return guard

            def authorized(_adb):
                self.assertTrue(restored)
                return ["private-target"]

            args = types.SimpleNamespace(
                run_id="connected-r1", series_id="stock16-series",
                repeat_index=1, target="private-target",
                device_role="harness", device_map="/private/map.json",
                rig_config="/private/rig.json",
                output=str(Path(directory) / "runs"),
                expected_build="FP6.QREL.16.100.0",
                conditions="fixed", adb="adb",
                config=str(TOOLS / "config/baseline.json"),
                operator_confirmed_display_50=True,
                operator_confirmed_unlocked=True)
            with mock.patch.object(
                    baseline_pilot.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_pilot.rig, "acquire_test_start_guard",
                        side_effect=acquire_guard), \
                    mock.patch.object(
                        baseline_pilot.test_runner, "_authorized_devices",
                        side_effect=authorized), \
                    mock.patch.object(
                        baseline_pilot.test_runner, "_capture_identity",
                        side_effect=baseline_pilot.PilotError("stop")):
                with self.assertRaisesRegex(baseline_pilot.PilotError, "stop"):
                    baseline_pilot.execute_connected(args, TOOLS, True)
            guard.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
