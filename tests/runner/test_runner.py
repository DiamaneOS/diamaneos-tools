"""Contract and failure-path tests for the target-bound device runner."""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft7Validator


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import test_runner as api

FAKE_ADB = Path(__file__).parent / "fixtures" / "fake_adb.py"
SCHEMA = json.loads((TOOLS / "schemas" / "test-run.schema.json").read_text())
SMOKE = json.loads((TOOLS / "tests" / "device" / "suites" / "smoke.json").read_text())

IDENTITY = {
    "getprop ro.product.model": {"stdout": "The Fairphone (Gen. 6)\n"},
    "getprop ro.product.device": {"stdout": "FP6\n"},
    "getprop ro.build.id": {"stdout": "BP1A.synthetic\n"},
    "getprop ro.build.version.incremental": {"stdout": "FP6.QREL.15.synthetic\n"},
    "getprop ro.build.type": {"stdout": "user\n"},
    "getprop ro.build.version.security_patch": {"stdout": "2026-08-01\n"},
    "getprop gsm.version.baseband": {"stdout": "synthetic-baseband\n"},
}


def responses(**overrides):
    result = dict(IDENTITY)
    result.update({
        "getprop sys.boot_completed": {"stdout": "1\n"},
        "dumpsys battery": {"stdout": "Battery Service state:\n  level: 73\n"},
        "dumpsys imsservice": {
            "stderr": "Can't find service: imsservice\n", "returncode": 1,
        },
        "getenforce": {"stdout": "Enforcing\n"},
    })
    result.update(overrides)
    return result


class RunnerTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "runs"
        self.log = self.root / "adb.log"
        self.remote_state = self.root / "fake-device-temp-file"
        self.mapping = self.root / "device-map.json"
        self.write_map("SERIAL-A", disposable=True)
        self.env = os.environ.copy()
        self.env.update({
            "FAKE_ADB_LOG": str(self.log),
            "FAKE_ADB_DEVICES": json.dumps([
                {"serial": "SERIAL-A", "state": "device"},
                {"serial": "SERIAL-B", "state": "device"},
            ]),
            "FAKE_ADB_RESPONSES": json.dumps(responses()),
            "FAKE_ADB_REMOTE_STATE": str(self.remote_state),
            "PYTHONDONTWRITEBYTECODE": "1",
        })

    def tearDown(self):
        self.temp.cleanup()

    def write_map(self, serial, disposable):
        self.mapping.write_text(json.dumps({
            "schema_version": 1,
            "devices": [{
                "role": "harness", "adb_serial": serial,
                "disposable": disposable,
            }],
        }))
        self.mapping.chmod(0o640)

    def command(self, run_id="synthetic-run", suite="smoke"):
        return [
            str(TOOLS / "bin" / "diamaneos"), "test", "run",
            "--suite", suite,
            "--target", "SERIAL-A",
            "--device-role", "harness",
            "--device-map", str(self.mapping),
            "--evidence-kind", "synthetic-fixture",
            "--run-id", run_id,
            "--conditions", "synthetic fixture; no hardware claim",
            "--output", str(self.output),
            "--adb", str(FAKE_ADB),
        ]

    def invoke(self, command):
        return subprocess.run(command, cwd=TOOLS, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def read_result(self, run_id):
        return json.loads((self.output / run_id / "result.json").read_text())

    def adb_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def write_temp_suite(self):
        suite = {
            "schema_version": 1,
            "suite_id": "temporary-data-fixture",
            "description": "Synthetic cleanup fixture; never a hardware claim.",
            "cases": [{
                "test_id": "temporary-file-roundtrip",
                "requirement_ids": ["synthetic-data-cleanup"],
                "stage": "smoke",
                "adapter": "adb-temp-file-roundtrip",
                "preconditions": ["The temporary Android test directory is available."],
                "expected": {
                    "oracle": "exit-zero",
                    "description": "The generated payload round-trips and its device copy is removed."
                },
                "applicability": {"kind": "required"},
                "timeout_seconds": 2,
            }],
        }
        path = self.root / "temporary-data.json"
        path.write_text(json.dumps(suite))
        return path

    def test_result_schema_and_committed_smoke_suite(self):
        Draft7Validator.check_schema(SCHEMA)
        api.validate_suite(SMOKE)
        out = self.invoke(self.command())
        self.assertEqual(0, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual([], list(Draft7Validator(SCHEMA).iter_errors(report)))
        self.assertEqual("PASS", report["status"])
        self.assertEqual("COMPLETE", report["completeness"])
        self.assertEqual("USER_BUILD_EVIDENCE", report["target"]["evidence_label"])
        self.assertEqual(5, report["counts"]["PASS"])
        self.assertEqual(1, report["counts"]["SKIP"])
        self.assertEqual("SKIP", next(
            case for case in report["cases"]
            if case["test_id"] == "smoke-ims-service")["status"])
        serialized = json.dumps(report)
        self.assertNotIn("SERIAL-A", serialized)
        self.assertNotIn("SERIAL-B", serialized)

    def test_selected_stage_keeps_full_inventory_visible(self):
        out = self.invoke(self.command() + ["--stage", "inspect"])
        self.assertEqual(0, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual("SELECTED", report["completeness"])
        self.assertEqual(6, len(report["expected_case_ids"]))
        self.assertEqual(2, len(report["selected_case_ids"]))
        self.assertEqual(4, report["counts"]["NOT_RUN"])

    def test_userdebug_and_fixture_labels_are_explicit(self):
        changed = responses()
        changed["getprop ro.build.type"] = {"stdout": "userdebug\n"}
        self.env["FAKE_ADB_RESPONSES"] = json.dumps(changed)
        out = self.invoke(self.command())
        self.assertEqual(0, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual("synthetic-fixture", report["evidence_kind"])
        self.assertEqual("USERDEBUG_DIAGNOSTIC_EVIDENCE",
                         report["target"]["evidence_label"])
        self.assertTrue(all(case["evidence_kind"] == "synthetic-fixture"
                            for case in report["cases"]))

    def test_empty_optional_capability_is_skip_not_pass(self):
        changed = responses()
        changed["dumpsys imsservice"] = {"stdout": ""}
        self.env["FAKE_ADB_RESPONSES"] = json.dumps(changed)
        out = self.invoke(self.command())
        self.assertEqual(0, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        case = next(case for case in report["cases"]
                    if case["test_id"] == "smoke-ims-service")
        self.assertEqual("SKIP", case["status"])
        self.assertNotEqual("PASS", case["status"])

    def test_target_is_scrubbed_from_operator_conditions(self):
        command = self.command()
        conditions_index = command.index("--conditions") + 1
        command[conditions_index] = "synthetic target SERIAL-A; no hardware claim"
        out = self.invoke(command)
        self.assertEqual(0, out.returncode, out.stderr)
        serialized = (self.output / "synthetic-run" / "result.json").read_text()
        self.assertNotIn("SERIAL-A", serialized)
        self.assertIn("<redacted:device-target>", serialized)

    def test_dry_run_contacts_no_adb_and_writes_nothing(self):
        out = self.invoke([
            str(TOOLS / "bin" / "diamaneos"), "test", "run",
            "--suite", "smoke", "--dry-run", "--adb", str(FAKE_ADB),
        ])
        self.assertEqual(0, out.returncode, out.stderr)
        plan = json.loads(out.stdout)
        self.assertEqual(0, plan["device_commands_executed"])
        self.assertEqual("none", plan["device_state_changes"])
        self.assertFalse(self.log.exists())
        self.assertFalse(self.output.exists())

    def test_multiple_devices_use_only_explicit_mapped_target(self):
        out = self.invoke(self.command())
        self.assertEqual(0, out.returncode, out.stderr)
        shell_calls = [call for call in self.adb_calls()
                       if len(call) >= 3 and call[2] == "shell"]
        self.assertTrue(shell_calls)
        self.assertTrue(all(call[1] == "SERIAL-A" for call in shell_calls))

    def test_wrong_private_role_target_refuses_before_adb(self):
        self.write_map("SERIAL-B", disposable=True)
        out = self.invoke(self.command())
        self.assertEqual(3, out.returncode)
        self.assertIn("does not match", out.stderr)
        self.assertNotIn("SERIAL-", out.stderr)
        self.assertFalse(self.log.exists())

    def test_world_readable_private_map_is_rejected_before_adb(self):
        self.mapping.chmod(0o644)
        out = self.invoke(self.command())
        self.assertEqual(2, out.returncode)
        self.assertIn("permissions", out.stderr)
        self.assertFalse(self.log.exists())

    def test_unallowlisted_suite_command_is_invalid(self):
        suite = copy.deepcopy(SMOKE)
        suite["cases"][0]["argv"] = ["reboot"]
        path = self.root / "bad.json"
        path.write_text(json.dumps(suite))
        out = self.invoke([str(TOOLS / "bin" / "diamaneos"), "test", "run",
                           "--suite", str(path), "--dry-run"])
        self.assertEqual(2, out.returncode)
        self.assertIn("not allowlisted", out.stderr)

    def test_destructive_stage_needs_flag_and_disposable_role_before_adb(self):
        suite = {
            "schema_version": 1,
            "suite_id": "destructive-fixture",
            "description": "Synthetic gate fixture; never a hardware claim.",
            "cases": [{
                "test_id": "fixture-flash-boundary",
                "requirement_ids": ["destructive-target-gate"],
                "stage": "destructive",
                "adapter": "installer-runbook",
                "runbook_ref": "docs/INSTALLER.md#flash",
                "preconditions": ["A reviewed external installer owns flashing."],
                "expected": {"oracle": "exit-zero", "description": "Operator uses the reviewed external runbook."},
                "applicability": {"kind": "required"},
                "timeout_seconds": 20,
            }],
        }
        path = self.root / "destructive.json"
        path.write_text(json.dumps(suite))
        out = self.invoke(self.command(suite=str(path)))
        self.assertEqual(3, out.returncode)
        self.assertFalse(self.log.exists())
        self.write_map("SERIAL-A", disposable=False)
        out = self.invoke(self.command(run_id="synthetic-run-2", suite=str(path))
                          + ["--destructive"])
        self.assertEqual(3, out.returncode)
        self.assertFalse(self.log.exists())

    def test_authorized_destructive_boundary_still_contains_no_flash_recipe(self):
        suite = {
            "schema_version": 1,
            "suite_id": "destructive-fixture",
            "description": "Synthetic boundary fixture; never a hardware claim.",
            "cases": [{
                "test_id": "fixture-flash-boundary",
                "requirement_ids": ["destructive-target-gate"],
                "stage": "destructive", "adapter": "installer-runbook",
                "runbook_ref": "docs/INSTALLER.md#flash",
                "preconditions": ["A reviewed external installer owns flashing."],
                "expected": {"oracle": "exit-zero", "description": "A separate operator action is required."},
                "applicability": {"kind": "required"}, "timeout_seconds": 20,
            }],
        }
        path = self.root / "destructive.json"
        path.write_text(json.dumps(suite))
        out = self.invoke(self.command(suite=str(path)) + ["--destructive"])
        self.assertEqual(3, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual("installer-runbook", suite["cases"][0]["adapter"])
        self.assertFalse(any("flash" in " ".join(call) or "wipe" in " ".join(call)
                             for call in self.adb_calls()))

    def test_timeout_is_harness_error_and_later_case_is_not_run(self):
        suite = copy.deepcopy(SMOKE)
        suite["cases"] = [
            next(case for case in suite["cases"]
                 if case["test_id"] == "smoke-battery-service"),
            next(case for case in suite["cases"]
                 if case["test_id"] == "smoke-boot-completed"),
        ]
        suite["cases"][0]["timeout_seconds"] = 1
        path = self.root / "timeout.json"
        path.write_text(json.dumps(suite))
        self.env["FAKE_ADB_RESPONSES"] = json.dumps(responses(**{
            "dumpsys battery": {"sleep": 5, "stdout": "level: 73\n"},
        }))
        out = self.invoke(self.command(suite=str(path)))
        self.assertEqual(5, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual("HARNESS_ERROR", report["status"])
        self.assertEqual("HARNESS_ERROR", report["cases"][0]["status"])
        self.assertEqual("NOT_RUN", report["cases"][1]["status"])
        shell_argvs = [call[3:] for call in self.adb_calls()
                      if len(call) >= 3 and call[2] == "shell"]
        self.assertNotIn(["getprop", "sys.boot_completed"], shell_argvs)

    def test_device_loss_blocks_and_fail_stops(self):
        suite = copy.deepcopy(SMOKE)
        suite["cases"] = suite["cases"][2:4]
        path = self.root / "disconnect.json"
        path.write_text(json.dumps(suite))
        self.env["FAKE_ADB_RESPONSES"] = json.dumps(responses(**{
            "getprop sys.boot_completed": {
                "stderr": "error: device offline\n", "returncode": 1,
            },
        }))
        out = self.invoke(self.command(suite=str(path)))
        self.assertEqual(3, out.returncode, out.stderr)
        report = self.read_result("synthetic-run")
        self.assertEqual("BLOCKED", report["cases"][0]["status"])
        self.assertEqual("NOT_RUN", report["cases"][1]["status"])

    def test_temporary_device_data_is_verified_and_removed(self):
        path = self.write_temp_suite()
        out = self.invoke(self.command(suite=str(path)))
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertFalse(self.remote_state.exists())
        report = self.read_result("synthetic-run")
        self.assertEqual("PASS", report["cases"][0]["status"])
        calls = self.adb_calls()
        self.assertTrue(any(len(call) >= 4 and call[2] == "push" for call in calls))
        self.assertTrue(any(call[3:5] == ["rm", "-f"] for call in calls))
        self.assertTrue(any(call[3:6] == ["test", "!", "-e"] for call in calls))

    def test_temporary_device_data_cleanup_runs_after_timeout(self):
        path = self.write_temp_suite()
        suite = json.loads(path.read_text())
        suite["cases"][0]["timeout_seconds"] = 1
        path.write_text(json.dumps(suite))
        self.env["FAKE_ADB_TEMP_READ_SLEEP"] = "5"
        out = self.invoke(self.command(suite=str(path)))
        self.assertEqual(5, out.returncode, out.stderr)
        self.assertFalse(self.remote_state.exists())
        report = self.read_result("synthetic-run")
        self.assertEqual("HARNESS_ERROR", report["cases"][0]["status"])
        calls = self.adb_calls()
        self.assertTrue(any(call[3:5] == ["rm", "-f"] for call in calls))
        self.assertTrue(any(call[3:6] == ["test", "!", "-e"] for call in calls))

    def test_sigterm_preserves_checkpoint_and_cleans_temporary_device_data(self):
        path = self.write_temp_suite()
        self.env["FAKE_ADB_TEMP_READ_SLEEP"] = "30"
        proc = subprocess.Popen(self.command(suite=str(path)), cwd=TOOLS,
                                env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        read_started = False
        while time.monotonic() < deadline:
            calls = self.adb_calls()
            read_started = any(len(call) >= 4 and call[3] == "cat" for call in calls)
            if self.remote_state.exists() and read_started:
                break
            time.sleep(0.02)
        self.assertTrue(self.remote_state.exists(), "fake device data was never created")
        self.assertTrue(read_started, "fake readback was never started")
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=15)
        self.assertEqual(5, proc.returncode, stderr)
        self.assertIn("result=", stdout)
        self.assertFalse(self.remote_state.exists())
        report = self.read_result("synthetic-run")
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertEqual("INCOMPLETE", report["cases"][0]["status"])

    def test_immutable_output_collision_does_not_replace_result(self):
        out = self.invoke(self.command())
        self.assertEqual(0, out.returncode, out.stderr)
        result_path = self.output / "synthetic-run" / "result.json"
        before = result_path.read_bytes()
        out = self.invoke(self.command())
        self.assertEqual(3, out.returncode)
        self.assertIn("collision", out.stderr)
        self.assertEqual(before, result_path.read_bytes())

    def test_bounded_executor_rejects_output_overflow(self):
        result = api.run_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096)"],
            timeout_seconds=5, max_output_bytes=1024)
        self.assertEqual("overflow", result["transport"])
        self.assertLessEqual(len(result["stdout"].encode()), 1024)

    def test_interrupted_results_survive_and_retry_selection_is_explicit(self):
        suite = copy.deepcopy(SMOKE)
        suite["cases"] = suite["cases"][:2]
        suite_path = self.root / "interrupt.json"
        suite_path.write_text(json.dumps(suite))
        suite, suite_hash, _ = api.load_suite(str(suite_path), TOOLS)

        def ok_result(stdout=""):
            return {"transport": "ok", "stdout": stdout, "stderr": "",
                    "returncode": 0, "duration_ms": 1}

        command_counts = {}

        def interrupting_executor(argv, timeout_seconds, *unused):
            if argv[:3] == ["adb", "-s", "SERIAL-A"]:
                command = argv[4:]
                key = " ".join(command)
                command_counts[key] = command_counts.get(key, 0) + 1
                if key == "getprop ro.build.type" and command_counts[key] == 2:
                    raise api.CommandInterrupted({
                        "transport": "interrupted", "stdout": "partial\n",
                        "stderr": "", "duration_ms": 2,
                    })
                return ok_result(responses()[key]["stdout"])
            if argv == ["adb", "devices"]:
                return ok_result("List of devices attached\nSERIAL-A\tdevice\n")
            if argv == ["adb", "version"]:
                return ok_result("Android Debug Bridge version synthetic\n")
            if argv[0] == "git":
                return ok_result("a" * 40 + "\n")
            self.fail(f"unexpected argv: {argv}")

        args = self.args("interrupted-run", suite_path)
        code, result_path = api.execute_run(
            args, suite, suite_hash, suite_path, TOOLS,
            executor=interrupting_executor)
        self.assertEqual(5, code)
        report = json.loads(result_path.read_text())
        self.assertEqual(["PASS", "INCOMPLETE"],
                         [case["status"] for case in report["cases"]])
        self.assertEqual(["inspect-build-type"], report["rerun_case_ids"])

        identity_path = result_path.parent / "raw" / "identity" / "model.stdout.txt"
        original_identity = identity_path.read_bytes()
        identity_path.write_bytes(original_identity + b"tampered")
        bad_retry_args = self.args("tampered-retry", suite_path)
        bad_retry_args.rerun_from = str(result_path)
        with self.assertRaisesRegex(api.RunnerError, "hash mismatch"):
            api.execute_run(
                bad_retry_args, suite, suite_hash, suite_path, TOOLS,
                executor=lambda *_args: self.fail("tampered retry contacted a tool"))
        identity_path.write_bytes(original_identity)

        def retry_executor(argv, timeout_seconds, *unused):
            if argv[:3] == ["adb", "-s", "SERIAL-A"]:
                key = " ".join(argv[4:])
                return ok_result(responses()[key]["stdout"])
            if argv == ["adb", "devices"]:
                return ok_result("List of devices attached\nSERIAL-A\tdevice\n")
            if argv == ["adb", "version"]:
                return ok_result("Android Debug Bridge version synthetic\n")
            if argv[0] == "git":
                return ok_result("a" * 40 + "\n")
            self.fail(f"unexpected argv: {argv}")

        retry_args = self.args("retry-run", suite_path)
        retry_args.rerun_from = str(result_path)
        code, retry_path = api.execute_run(
            retry_args, suite, suite_hash, suite_path, TOOLS,
            executor=retry_executor)
        self.assertEqual(0, code)
        retry = json.loads(retry_path.read_text())
        self.assertEqual("interrupted-run", retry["retry_parent"])
        self.assertEqual(["inspect-build-type"], retry["selected_case_ids"])
        self.assertEqual(["NOT_RUN", "PASS"],
                         [case["status"] for case in retry["cases"]])
        self.assertEqual([], retry["rerun_case_ids"])

    def args(self, run_id, suite_path):
        return argparse.Namespace(
            run_id=run_id, target="SERIAL-A", device_role="harness",
            device_map=str(self.mapping), output=str(self.output),
            conditions="synthetic fixture; no hardware claim", stage=None,
            destructive=False, rerun_from=None, adb="adb", timeout=20,
            candidate=None, suite=str(suite_path), dry_run=False,
            evidence_kind="synthetic-fixture",
        )


if __name__ == "__main__":
    unittest.main()
