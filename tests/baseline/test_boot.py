"""Declared stock boot-time workflow contract tests."""

from pathlib import Path
import json
import os
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_boot
from diamaneos_tools import baseline_protocol


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class RestartObserverTest(unittest.TestCase):
    def parameters(self):
        return {
            "poll_interval_ms": 250,
            "disconnect_timeout_seconds": 5,
            "completion_timeout_seconds": 30,
            "required_milestones": [
                "adb-unavailable", "adb-authorized",
                "sys.boot_completed", "service.bootanim.exit",
            ],
        }

    def test_restart_records_every_milestone_without_target(self):
        clock = FakeClock()
        state_calls = 0
        prop_round = {"sys.boot_completed": 0,
                      "service.bootanim.exit": 0}

        def executor(argv, _timeout, _cap):
            nonlocal state_calls
            if argv[-1] == "reboot":
                return {"transport": "ok", "stdout": "", "stderr": "",
                        "returncode": 0}
            if argv[-1] == "get-state":
                state_calls += 1
                if state_calls == 1:
                    return {"transport": "ok", "stdout": "device\n",
                            "stderr": "", "returncode": 0}
                if state_calls in {2, 3}:
                    return {"transport": "error", "stdout": "",
                            "stderr": "not found", "returncode": 1}
                return {"transport": "ok", "stdout": "device\n",
                        "stderr": "", "returncode": 0}
            prop = argv[-1]
            prop_round[prop] += 1
            value = "1\n" if prop_round[prop] >= 2 else "0\n"
            return {"transport": "ok", "stdout": value,
                    "stderr": "", "returncode": 0}

        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            sample = baseline_boot.observe_restart(
                "adb", "private-target", self.parameters(), raw, 1,
                executor=executor, clock=clock, sleeper=clock.sleep)
            self.assertEqual("PASS", sample["status"])
            self.assertEqual(set(self.parameters()["required_milestones"]),
                             set(sample["milestones_seconds"]))
            self.assertGreater(sample["ready_seconds"], 0)
            self.assertNotIn("private-target", json.dumps(sample))
            self.assertEqual(3, len(sample["raw_evidence_refs"]))

    def test_restart_fails_if_target_never_disappears(self):
        clock = FakeClock()

        def executor(argv, _timeout, _cap):
            if argv[-1] == "reboot":
                return {"transport": "ok", "stdout": "", "stderr": "",
                        "returncode": 0}
            return {"transport": "ok", "stdout": "device\n", "stderr": "",
                    "returncode": 0}

        params = self.parameters()
        params["disconnect_timeout_seconds"] = 1
        with tempfile.TemporaryDirectory() as directory:
            sample = baseline_boot.observe_restart(
                "adb", "private-target", params, Path(directory), 1,
                executor=executor, clock=clock, sleeper=clock.sleep)
            self.assertEqual("FAIL", sample["status"])
            self.assertIn("ADB-unavailable", sample["reason"])

    def test_network_preflight_retains_only_safe_state_counts(self):
        def executor(argv, _timeout, _cap):
            command = tuple(argv[-4:])
            if command == ("settings", "get", "global", "airplane_mode_on"):
                output = "0\n"
            elif command == ("settings", "get", "global", "bluetooth_on"):
                output = "0\n"
            elif tuple(argv[-3:]) == ("cmd", "wifi", "status"):
                output = "Wifi is enabled\n"
            else:
                output = "LOADED,READY\n"
            return {"transport": "ok", "stdout": output, "stderr": "",
                    "returncode": 0}

        original = baseline_boot.test_runner.run_bounded
        baseline_boot.test_runner.run_bounded = executor
        try:
            with tempfile.TemporaryDirectory() as directory:
                observed, refs, problems = baseline_boot._network_preflight(
                    "adb", "private-target", Path(directory))
        finally:
            baseline_boot.test_runner.run_bounded = original
        self.assertEqual([], problems)
        self.assertEqual(2, observed["reported_sim_slot_count"])
        self.assertEqual(2, observed["ready_or_loaded_sim_slot_count"])
        self.assertNotIn("LOADED", json.dumps(observed))
        self.assertEqual(8, len(refs))


class BootPlanAndFinalizeTest(unittest.TestCase):
    def test_dry_run_contacts_no_device_and_separates_cold_boot(self):
        plan = baseline_boot.dry_run_plan(
            str(TOOLS / "config" / "baseline.json"))
        self.assertEqual(0, plan["device_commands_executed"])
        self.assertEqual(3, plan["restart"]["repetitions"])
        self.assertEqual("manual-source-media-required",
                         plan["cold_power_on"]["automation_status"])

    def _partial(self, root: Path, ambient_start: float) -> Path:
        protocol, digest = baseline_protocol.load_protocol(
            TOOLS / "config" / "baseline.json")
        run_dir = root / "boot-r1.partial"
        run_dir.mkdir(mode=0o750)
        report = {
            "schema_version": baseline_boot.SCHEMA_VERSION,
            "operation": baseline_boot.OPERATION,
            "label": baseline_boot.LABEL,
            "status": "AWAITING_AMBIENT_END",
            "protocol": {"sha256": digest},
            "series_id": "stock16-final",
            "repeat_index": 1,
            "target": {"role": "harness"},
            "ambient_start_c": ambient_start,
            "identity_evidence_refs": [],
            "preflight_evidence_refs": [],
            "case": {
                "status": "PASS",
                "samples": [
                    {"status": "PASS", "raw_evidence_refs": []}
                    for _ in range(3)
                ],
            },
            "tool": {},
            "comparability_exclusions": [],
        }
        (run_dir / "result.json").write_text(
            json.dumps(report), encoding="utf-8")
        return run_dir

    def test_finalize_passes_with_bounded_ambient_span(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o750)
            run_dir = self._partial(root, 22.0)
            code, result = baseline_boot.finalize(
                str(run_dir), 22.4, str(TOOLS / "config" / "baseline.json"))
            self.assertEqual(0, code)
            self.assertEqual("PASS", json.loads(result.read_text())["status"])

    def test_finalize_quarantines_out_of_tolerance_ambient(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o750)
            run_dir = self._partial(root, 22.0)
            code, result = baseline_boot.finalize(
                str(run_dir), 26.0, str(TOOLS / "config" / "baseline.json"))
            self.assertEqual(4, code)
            report = json.loads(result.read_text())
            self.assertEqual("NON_COMPARABLE", report["status"])


if __name__ == "__main__":
    unittest.main()
