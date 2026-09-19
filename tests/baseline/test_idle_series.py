"""Tests for the detached two-repeat idle-series controller."""

from pathlib import Path
import hashlib
import json
import sys
import tempfile
import types
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_idle_series


class SeriesLaunchTest(unittest.TestCase):
    def args(self, root: Path):
        return types.SimpleNamespace(
            series_id="fp6-stock16-final-20260918",
            device_role="harness",
            device_map=str(root / "map.json"),
            rig_config=str(root / "rig.json"),
            adb="adb", output=str(root / "runs"),
            job_root=str(root / "jobs"),
            config=str(TOOLS / "config" / "baseline.json"),
            expected_build="FP6.QREL.16.100.0",
            conditions="controlled permanent configuration",
            operator_confirmed_display_50=True,
            operator_confirmed_unlocked=True,
            operator_authorized_repeat_1_batterystats_reset=True,
            operator_authorized_repeat_2_batterystats_reset=True,
            operator_committed_no_interaction=True,
            operator_declared_no_planned_network_outage=True)

    def test_launch_rejects_missing_second_reset_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.operator_authorized_repeat_2_batterystats_reset = False
            with self.assertRaisesRegex(
                    baseline_idle_series.SeriesError, "both named"):
                baseline_idle_series.launch(args, TOOLS)

    def test_launch_writes_hash_bound_job_and_detaches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = self.args(root)
            Path(args.device_map).write_text("{}", encoding="utf-8")
            Path(args.rig_config).write_text("{}", encoding="utf-8")
            process = mock.Mock(pid=1234)
            config = {"device_map": args.device_map}
            with mock.patch.object(
                    baseline_idle_series, "_timer_is_inactive",
                    return_value=True), \
                    mock.patch.object(
                        baseline_idle_series.rig, "load_config",
                        return_value=config), \
                    mock.patch.object(
                        baseline_idle_series.rig, "mapped_serial",
                        return_value="private-target"), \
                    mock.patch.object(
                        baseline_idle_series.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_idle_series.test_runner,
                        "_authorized_devices",
                        return_value=["private-target"]), \
                    mock.patch.object(
                        baseline_idle_series.subprocess, "Popen",
                        return_value=process) as popen:
                result = baseline_idle_series.launch(args, TOOLS)
            self.assertEqual("LAUNCHED", result["status"])
            self.assertEqual(1234, result["worker_pid"])
            job = Path(args.job_root) / args.series_id
            spec = json.loads((job / "spec.json").read_text())
            state = json.loads((job / "state.json").read_text())
            self.assertTrue(spec["authorizations"][
                "repeat_1_batterystats_reset"])
            self.assertTrue(spec["authorizations"][
                "repeat_2_batterystats_reset"])
            self.assertFalse(state["reset_authorizations_consumed"]["1"])
            self.assertFalse(state["reset_authorizations_consumed"]["2"])
            self.assertEqual("PREPARED", state["phase"])
            command = popen.call_args.args[0]
            self.assertIn("--expected-spec-sha256", command)
            self.assertNotIn("private-target", " ".join(command))


class RechargeTest(unittest.TestCase):
    def test_recharge_requires_full_status_on_the_bound_path(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "state.json").write_text("{}", encoding="utf-8")
            controller = mock.Mock()
            controller.status.side_effect = [
                {"captured_at_utc": "first", "roles": [{
                    "role": "harness", "adb_state": "device",
                    "path_matches": True, "port_powered": True,
                    "battery": {"externally_powered": True,
                                "level_percent": 99, "status_code": 2},
                }]},
                {"captured_at_utc": "second", "roles": [{
                    "role": "harness", "adb_state": "device",
                    "path_matches": True, "port_powered": True,
                    "battery": {"externally_powered": True,
                                "level_percent": 100, "status_code": 5},
                }]},
            ]
            spec = {"rig_config": "rig", "device_role": "harness",
                    "device_map": "map", "target": "target"}
            state = {}
            with mock.patch.object(
                    baseline_idle_series, "_timer_is_inactive",
                    return_value=True), \
                    mock.patch.object(
                    baseline_idle_series.rig, "controller_for_target",
                    return_value=controller), \
                    mock.patch.object(
                        baseline_idle_series, "_write_state"), \
                    mock.patch.object(baseline_idle_series.time, "sleep"):
                baseline_idle_series._wait_for_full(spec, state, job)
            self.assertEqual(2, controller.status.call_count)
            self.assertEqual(100, state["recharge"]["level_percent"])

    def test_recharge_fails_closed_if_maintenance_is_not_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            controller = mock.Mock()
            with mock.patch.object(
                    baseline_idle_series, "_timer_is_inactive",
                    return_value=False), \
                    mock.patch.object(
                        baseline_idle_series.rig, "controller_for_target",
                        return_value=controller):
                with self.assertRaisesRegex(
                        baseline_idle_series.SeriesError,
                        "maintenance became active"):
                    baseline_idle_series._wait_for_full(
                        {"rig_config": "rig", "device_role": "harness",
                         "device_map": "map", "target": "target"},
                        {}, job)
            controller.status.assert_not_called()


class FinishWaitTest(unittest.TestCase):
    def test_rounded_zero_does_not_finish_before_duration_gate(self):
        idle = baseline_idle_series.baseline_idle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o750)
            run_dir = root / "idle.partial"
            run_dir.mkdir(mode=0o750)
            (run_dir / "result.json").write_text(json.dumps({
                "schema_version": 1,
                "operation": "baseline-idle-measurement",
                "run_id": "idle",
                "status": idle.STATUS_DISCONNECTED_RIG,
                "duration_seconds": 28800,
                "disconnect": {
                    "host_boottime_seconds": 111215.71,
                    "finish_not_before_boottime_seconds": 140015.71,
                    "host_boot_id_sha256": "a" * 64,
                },
            }), encoding="utf-8")
            # First the displayed countdown rounds to zero. At the next
            # sample, float subtraction is still just below the full duration.
            # Neither sample may advance to the device-touching finish stage.
            samples = [{"host_boottime_seconds": value,
                        "host_boot_id_sha256": "a" * 64}
                       for value in (140015.68, 140015.71, 140015.81)]
            with mock.patch.object(idle, "_host_clock_sample",
                                   side_effect=samples) as clock, \
                    mock.patch.object(baseline_idle_series.time,
                                      "sleep") as sleep, \
                    mock.patch.object(baseline_idle_series, "_write_state"):
                baseline_idle_series._wait_until_finish(run_dir, {}, root)
            self.assertEqual(3, clock.call_count)
            self.assertEqual([mock.call(0.1), mock.call(0.1)],
                             sleep.call_args_list)

    def test_wait_polls_at_bounded_intervals(self):
        idle = baseline_idle_series.baseline_idle
        statuses = [{"status": idle.STATUS_DISCONNECTED_RIG,
                     "host_rebooted": False,
                     "ready_to_reconnect": ready,
                     "remaining_seconds": remaining}
                    for ready, remaining in ((False, 90), (True, 0))]
        with mock.patch.object(idle, "status", side_effect=statuses), \
                mock.patch.object(baseline_idle_series.time, "sleep") as sleep, \
                mock.patch.object(baseline_idle_series, "_write_state"):
            baseline_idle_series._wait_until_finish(Path("run"), {}, Path("job"))
        sleep.assert_called_once_with(30)

    def test_wait_rejects_reboot_and_missing_or_invalid_interval_state(self):
        idle = baseline_idle_series.baseline_idle
        valid = {"status": idle.STATUS_DISCONNECTED_RIG,
                 "host_rebooted": False, "ready_to_reconnect": False,
                 "remaining_seconds": 0.0}
        invalid = [{}, {**valid, "status": "HARNESS_ERROR"},
                   {**valid, "host_rebooted": True},
                   {**valid, "ready_to_reconnect": "false"},
                   {**valid, "remaining_seconds": -1},
                   {**valid, "remaining_seconds": float("nan")},
                   {**valid, "remaining_seconds": float("inf")}]
        invalid.extend({k: v for k, v in valid.items() if k != missing}
                       for missing in valid)
        for status in invalid:
            with self.subTest(status=status), \
                    mock.patch.object(idle, "status", return_value=status), \
                    mock.patch.object(baseline_idle_series.time,
                                      "sleep", side_effect=AssertionError(
                                          "invalid state must not keep polling")) as sleep, \
                    mock.patch.object(baseline_idle_series, "_write_state"):
                with self.assertRaises(baseline_idle_series.SeriesError):
                    baseline_idle_series._wait_until_finish(
                        Path("run"), {}, Path("job"))
                sleep.assert_not_called()


class WorkerTest(unittest.TestCase):
    def test_worker_consumes_each_authorization_and_finishes_both_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            output = root / "runs"
            job.mkdir(mode=0o750)
            output.mkdir(mode=0o750)
            spec = {
                "schema_version": 1,
                "series_id": "fp6-stock16-final-20260918",
                "target": "private-target",
                "device_role": "harness",
                "device_map": "/private/map.json",
                "rig_config": "/private/rig.json",
                "adb": "adb",
                "config": str(TOOLS / "config" / "baseline.json"),
                "output": str(output),
                "expected_build": "FP6.QREL.16.100.0",
                "conditions": "controlled permanent configuration",
                "run_ids": {"1": "idle-final-r1", "2": "idle-final-r2"},
                "authorizations": {
                    "repeat_1_batterystats_reset": True,
                    "repeat_2_batterystats_reset": True,
                    "display_50_percent": True,
                    "unlocked_no_screen_lock": True,
                    "no_phone_interaction_for_series": True,
                    "no_material_network_outage_planned": True,
                },
                "prepared_at_utc": "2026-09-18T00:00:00Z",
            }
            baseline_idle_series._atomic_json(job / "spec.json", spec)
            state = {
                "schema_version": 1,
                "series_id": spec["series_id"],
                "phase": "PREPARED",
                "reset_authorizations_consumed": {"1": False, "2": False},
                "results": [],
                "events": [],
            }
            baseline_idle_series._atomic_json(job / "state.json", state)
            digest = hashlib.sha256((job / "spec.json").read_bytes()).hexdigest()
            controller = mock.Mock()
            starts = []

            def start(args, _repo):
                starts.append(args)
                partial = output / f"{args.run_id}.partial"
                partial.mkdir()
                return 0, partial / "result.json"

            def finish(args):
                run_dir = Path(args.run_dir)
                final = run_dir.with_name(run_dir.name.removesuffix(".partial"))
                run_dir.rename(final)
                result = final / "result.json"
                result.write_text("{}\n", encoding="utf-8")
                return 0, result

            with mock.patch.object(
                    baseline_idle_series, "_timer_is_inactive",
                    return_value=True), \
                    mock.patch.object(
                        baseline_idle_series.rig, "controller_for_target",
                        return_value=controller), \
                    mock.patch.object(baseline_idle_series, "_wake_phone"), \
                    mock.patch.object(
                        baseline_idle_series.baseline_idle, "start",
                        side_effect=start), \
                    mock.patch.object(
                        baseline_idle_series.baseline_idle,
                        "observe_disconnect"), \
                    mock.patch.object(
                        baseline_idle_series, "_wait_until_finish"), \
                    mock.patch.object(
                        baseline_idle_series, "_wait_for_full"), \
                    mock.patch.object(
                        baseline_idle_series.baseline_idle, "finish",
                        side_effect=finish):
                code = baseline_idle_series.run(job, digest, TOOLS)
            self.assertEqual(0, code)
            saved = json.loads((job / "state.json").read_text())
            self.assertEqual("PASS", saved["phase"])
            self.assertEqual({"1": True, "2": True},
                             saved["reset_authorizations_consumed"])
            self.assertEqual(2, len(saved["results"]))
            self.assertEqual([1, 2],
                             [item.declared_repeat_index for item in starts])
            self.assertTrue(all(
                item.operator_authorized_batterystats_reset
                for item in starts))


if __name__ == "__main__":
    unittest.main()
