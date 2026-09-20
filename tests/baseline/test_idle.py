"""FP6-022 physical-disconnect idle-pilot tests."""

from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_idle
from diamaneos_tools import baseline_protocol


def protocol():
    return baseline_protocol.load_protocol(TOOLS / "config" / "baseline.json")[0]


class ParserTest(unittest.TestCase):
    def test_wifi_parser_discards_network_names(self):
        parsed = baseline_idle.parse_wifi_connection(
            'Wifi is enabled\nWifi is connected to "private-name"\n'
            'BSSID: 00:11:22:33:44:55\n')
        self.assertEqual({"enabled": True, "connected": True}, parsed)
        self.assertNotIn("private-name", str(parsed))
        self.assertEqual({"enabled": False, "connected": False},
                         baseline_idle.parse_wifi_connection("Wifi is disabled\n"))

    def test_telephony_parser_retains_only_registration_counts(self):
        parsed = baseline_idle.parse_telephony_registration(
            "mServiceState={mVoiceRegState=0(IN_SERVICE), "
            "mDataRegState=1(OUT_OF_SERVICE), cellIdentity=secret}\n")
        self.assertTrue(parsed["registered"])
        self.assertNotIn("secret", str(parsed))
        with self.assertRaises(ValueError):
            baseline_idle.parse_telephony_registration("unrelated output")

    def test_battery_snapshot_adds_optional_charge_counter(self):
        parsed = baseline_idle.parse_battery_snapshot(
            "AC powered: true\nUSB powered: false\nWireless powered: false\n"
            "status: 5\nlevel: 100\nscale: 100\nvoltage: 4400\n"
            "temperature: 250\nCharge counter: 4455000\n")
        self.assertEqual(4455000, parsed["charge_counter_uah"])

    def test_panel_off_doze_is_a_valid_screen_off_state(self):
        self.assertEqual("OFF", baseline_idle.parse_display_panel_state(
            "Display Device:\n    mState=OFF\n"))
        self.assertTrue(baseline_idle.screen_off_state_is_valid("Dozing", "OFF"))
        self.assertTrue(baseline_idle.screen_off_state_is_valid("Asleep", "OFF"))
        self.assertFalse(baseline_idle.screen_off_state_is_valid("Awake", "OFF"))
        self.assertFalse(baseline_idle.screen_off_state_is_valid("Dozing", "DOZE"))

    def test_panel_state_remote_filter_is_bounded_to_known_states(self):
        remote = baseline_idle._display_panel_state_remote()
        self.assertTrue(remote.startswith("sh -c "))
        self.assertIn("grep -m 1", remote)
        self.assertIn("mState=(OFF|ON|DOZE|DOZE_SUSPEND|VR|UNKNOWN)", remote)

    def test_screen_off_wait_tolerates_transition_and_requires_stability(self):
        states = iter([
            ("Awake", "ON"),
            ("Dozing", "OFF"),
            ("Dozing", "OFF"),
        ])
        observations = baseline_idle.wait_for_screen_off(
            lambda: next(states), 1.0, 0.0, 2)
        self.assertEqual(3, len(observations))
        self.assertEqual("OFF", observations[-1]["builtin_panel_state"])

    def test_screen_off_wait_fails_closed_after_bound(self):
        with self.assertRaisesRegex(baseline_idle.IdleError, "did not settle"):
            baseline_idle.wait_for_screen_off(
                lambda: ("Awake", "ON"), 0.0, 0.0, 2)

    def test_reconnect_wait_uses_first_sample_from_stable_presence(self):
        states = iter([False, True, False, True, True])
        samples = iter([
            {"utc": "discarded"},
            {"utc": "first-stable"},
            {"utc": "confirmed-stable"},
        ])
        observed = baseline_idle.wait_for_authorized_reconnect(
            lambda: next(states), lambda: next(samples), 1.0, 0.0, 2)
        self.assertEqual("first-stable", observed["first_present"]["utc"])
        self.assertEqual(2, observed["confirmed_present_samples"])
        self.assertEqual(5, observed["observation_count"])

    def test_reconnect_wait_fails_closed_after_bound(self):
        with self.assertRaisesRegex(baseline_idle.IdleError,
                                    "reconnect was not observed"):
            baseline_idle.wait_for_authorized_reconnect(
                lambda: False, lambda: {}, 0.0, 0.0, 2)


class PreflightTest(unittest.TestCase):
    def valid_observed(self):
        return {
            "display": {
                "adaptive_brightness": False,
                "wakefulness": "Awake",
                "screen_timeout_ms": 600000,
                "peak_refresh_rate_hz": 90.0,
            },
            "network": {
                "airplane_mode": False,
                "wifi_enabled": True,
                "wifi_connected": True,
                "sim_states": ["LOADED", "NOT_READY"],
                "sim_registered": True,
            },
            "battery": {
                "level_percent": 100,
                "ac_powered": True,
                "usb_powered": False,
                "temperature_c": 25.0,
            },
        }

    def test_matching_connected_idle_setup_is_accepted(self):
        self.assertEqual([], baseline_idle.evaluate_idle_preflight(
            self.valid_observed(), protocol(), True, True))

    def test_changed_network_power_and_display_are_all_reported(self):
        observed = self.valid_observed()
        observed["network"].update(
            airplane_mode=True, wifi_connected=False, sim_registered=False)
        observed["battery"].update(level_percent=80, ac_powered=False)
        observed["display"].update(adaptive_brightness=True, wakefulness="Asleep")
        reasons = baseline_idle.evaluate_idle_preflight(
            observed, protocol(), False, False)
        self.assertGreaterEqual(len(reasons), 8)


class ResultTest(unittest.TestCase):
    def test_metrics_keep_raw_delta_and_rate(self):
        metrics = baseline_idle.idle_metrics(
            {"level_percent": 100, "charge_counter_uah": 4500000},
            {"level_percent": 99, "charge_counter_uah": 4475000}, 300)
        self.assertEqual(1, metrics["battery_level_delta_percentage_points"])
        self.assertEqual(12.0, metrics["battery_level_drain_percent_per_hour"])
        self.assertEqual(25.0, metrics["charge_counter_delta_mah"])

    def test_comparability_rejects_late_or_changed_finish(self):
        report = {"pilot_duration_seconds": 300}
        network = {"wifi_enabled": False, "wifi_connected": False,
                   "sim_registered": False}
        reasons = baseline_idle.evaluate_comparability(
            report, protocol(), 361, network, False)
        self.assertEqual(4, len(reasons))

    def test_declared_profile_binds_eight_hours_and_repeat_identity(self):
        plan = baseline_idle.dry_run(
            str(TOOLS / "config" / "baseline.json"), 1,
            "fp6-stock16-baseline-20260912")
        self.assertEqual("declared", plan["measurement_kind"])
        self.assertEqual("DECLARED_STOCK_BASELINE_EVIDENCE", plan["label"])
        self.assertEqual(28800, plan["duration_seconds"])
        self.assertEqual(1, plan["repeat_index"])
        self.assertEqual(2, plan["repeat_count"])

    def test_declared_profile_requires_series_identity(self):
        with self.assertRaisesRegex(baseline_idle.IdleError, "--series-id"):
            baseline_idle.dry_run(
                str(TOOLS / "config" / "baseline.json"), 1, None)
        with self.assertRaisesRegex(baseline_idle.IdleError, "pilot idle"):
            baseline_idle.dry_run(
                str(TOOLS / "config" / "baseline.json"), None,
                "fp6-stock16-baseline-20260912")

    def test_private_ref_verifier_accepts_bounded_large_batterystats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            payload = b"battery-stats\n" * 30000
            path = raw / "finish-batterystats.stdout.txt"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            baseline_idle._verify_evidence_refs(
                root / "result.json",
                [f"raw/{path.name}@sha256:{digest}"])
            with self.assertRaises(baseline_idle.IdleError):
                baseline_idle._verify_evidence_refs(
                    root / "result.json",
                    [f"raw/{path.name}@sha256:" + "0" * 64])


class CliContractTest(unittest.TestCase):
    def test_reset_requires_an_explicit_named_authorization(self):
        args = baseline_idle.build_parser().parse_args([
            "start", "--target", "private-target", "--device-role", "idle-pilot",
            "--device-map", "/private/map.json", "--run-id", "idle-pilot-1",
            "--output", "/private/runs", "--expected-build", "build",
            "--conditions", "controlled",
        ])
        self.assertFalse(args.operator_authorized_batterystats_reset)

    def test_dry_run_contacts_no_device_and_declares_five_minutes(self):
        plan = baseline_idle.dry_run(
            str(TOOLS / "config" / "baseline.json"))
        self.assertEqual(0, plan["device_commands_executed"])
        self.assertEqual(0, plan["output_directories_created"])
        self.assertEqual(300, plan["duration_seconds"])
        self.assertIn("dumpsys batterystats --reset",
                      " ".join(plan["device_state_changes"]))
        self.assertIn("--wait-for-reconnect",
                      " ".join(plan["physical_actions"]))

    def test_rig_dry_run_declares_automated_vbus_off_and_restore(self):
        plan = baseline_idle.dry_run(
            str(TOOLS / "config" / "baseline.json"),
            disconnect_method=baseline_idle.DISCONNECT_METHOD_RIG)
        self.assertEqual("verified-rig-port-off", plan["disconnect_method"])
        actions = " ".join(plan["physical_actions"])
        self.assertIn("physically attached", actions)
        self.assertIn("automatically", actions)

    def test_rig_start_parser_requires_explicit_method_and_config(self):
        args = baseline_idle.build_parser().parse_args([
            "start", "--target", "private-target",
            "--device-role", "idle-pilot",
            "--device-map", "/private/map.json",
            "--rig-config", "/private/rig.json",
            "--disconnect-method", "verified-rig-port-off",
            "--run-id", "idle-pilot-1",
            "--output", "/private/runs", "--expected-build", "build",
            "--conditions", "controlled",
        ])
        self.assertEqual("verified-rig-port-off", args.disconnect_method)
        self.assertEqual("/private/rig.json", args.rig_config)

    def test_finish_can_arm_before_physical_reconnect(self):
        args = baseline_idle.build_parser().parse_args([
            "finish", "--target", "private-target",
            "--device-role", "idle-pilot",
            "--device-map", "/private/map.json",
            "--run-dir", "/private/runs/idle-pilot-1.partial",
            "--wait-for-reconnect",
            "--operator-confirmed-physical-disconnect",
            "--operator-confirmed-no-interaction",
            "--operator-confirmed-no-known-network-outage",
        ])
        self.assertTrue(args.wait_for_reconnect)
        self.assertEqual(120, args.reconnect_timeout_seconds)

    def test_start_parser_carries_declared_series_binding(self):
        args = baseline_idle.build_parser().parse_args([
            "start", "--target", "private-target",
            "--device-role", "idle-pilot",
            "--device-map", "/private/map.json",
            "--run-id", "idle-declared-r1",
            "--output", "/private/runs",
            "--expected-build", "FP6.QREL.16.100.0",
            "--conditions", "controlled",
            "--declared-repeat-index", "1",
            "--series-id", "fp6-stock16-baseline-20260912",
        ])
        self.assertEqual(1, args.declared_repeat_index)
        self.assertEqual("fp6-stock16-baseline-20260912", args.series_id)

    def test_series_launch_requires_two_distinct_reset_authorizations(self):
        args = baseline_idle.build_parser().parse_args([
            "series", "launch",
            "--series-id", "fp6-stock16-final-20260918",
            "--expected-build", "FP6.QREL.16.100.0",
            "--conditions", "controlled",
            "--operator-authorized-repeat-1-batterystats-reset",
        ])
        self.assertTrue(
            args.operator_authorized_repeat_1_batterystats_reset)
        self.assertFalse(
            args.operator_authorized_repeat_2_batterystats_reset)

    def test_unattended_start_commitments_must_be_supplied_together(self):
        args = types.SimpleNamespace(
            run_id="idle-declared-r1", target="private-target",
            device_role="harness", device_map="/private/map.json",
            output="/private/runs", expected_build="build",
            conditions="controlled", operator_confirmed_display_50=True,
            operator_confirmed_unlocked=True,
            operator_authorized_batterystats_reset=True,
            operator_committed_no_interaction=True,
            operator_declared_no_planned_network_outage=False,
            disconnect_method="verified-rig-port-off",
            rig_config="/private/rig.json")
        with self.assertRaisesRegex(
                baseline_idle.IdleError, "supplied together"):
            baseline_idle.start(args, TOOLS)


class RigIdleIntegrationTest(unittest.TestCase):
    def _run_dir(self, root):
        root.chmod(0o750)
        run_dir = root / "idle-rig.partial"
        run_dir.mkdir(mode=0o750)
        (run_dir / "raw").mkdir(mode=0o750)
        return run_dir

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
                run_id="idle-r1", target="private-target",
                device_role="harness", device_map="/private/map.json",
                rig_config="/private/rig.json", adb="adb",
                output=str(Path(directory) / "runs"),
                expected_build="FP6.QREL.16.100.0",
                conditions="fixed", declared_repeat_index=None,
                series_id=None,
                disconnect_method=baseline_idle.DISCONNECT_METHOD_RIG,
                operator_confirmed_display_50=True,
                operator_confirmed_unlocked=True,
                operator_authorized_batterystats_reset=True,
                operator_committed_no_interaction=False,
                operator_declared_no_planned_network_outage=False,
                config=str(TOOLS / "config/baseline.json"))
            with mock.patch.object(
                    baseline_idle.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_idle.rig, "acquire_test_start_guard",
                        side_effect=acquire_guard), \
                    mock.patch.object(
                        baseline_idle.device, "authorized_devices",
                        side_effect=authorized), \
                    mock.patch.object(
                        baseline_idle.device, "capture_identity",
                        side_effect=baseline_idle.IdleError("stop")):
                with self.assertRaisesRegex(baseline_idle.IdleError, "stop"):
                    baseline_idle.start(args, TOOLS)
            guard.release.assert_called_once_with()

    def test_observe_disconnect_switches_bound_rig_port_off(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._run_dir(root)
            report = {
                "schema_version": 1,
                "operation": "baseline-idle-pilot",
                "run_id": "idle-rig",
                "duration_seconds": 300,
                "disconnect_method": "verified-rig-port-off",
                "status": baseline_idle.STATUS_ARMED_RIG,
                "target": {"role": "harness"},
            }
            (run_dir / "result.json").write_text(
                json.dumps(report), encoding="utf-8")
            controller = mock.Mock()
            controller.set_power.return_value = {
                "status": "PASS", "role": "harness", "action": "off"
            }
            args = types.SimpleNamespace(
                device_map="/private/map.json", device_role="harness",
                target="private-target", run_dir=str(run_dir), adb="adb",
                rig_config="/private/rig.json", timeout_seconds=10)
            clock = {
                "utc": "2026-09-14T00:00:00Z",
                "host_boottime_seconds": 100.0,
                "host_boot_id_sha256": "a" * 64,
            }
            def command(_adb, _target, argv, **_kwargs):
                stdout = ("mWakefulness=Dozing\n"
                          if "dumpsys power" in argv[0] else "mState=OFF\n")
                return {"transport": "ok", "stdout": stdout, "stderr": ""}
            with mock.patch.object(
                    baseline_idle.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_idle.rig, "controller_for_target",
                        return_value=controller), \
                    mock.patch.object(
                        baseline_idle, "_run_required", side_effect=command), \
                    mock.patch.object(
                        baseline_idle.device, "authorized_devices",
                        return_value=[]), \
                    mock.patch.object(
                        baseline_idle, "_host_clock_sample", return_value=clock), \
                    mock.patch.object(baseline_idle.time, "sleep"):
                result = baseline_idle.observe_disconnect(args)
            self.assertEqual(result, (run_dir / "result.json").resolve())
            controller.set_power.assert_called_once_with(
                "harness", "off", "idle-measurement",
                allowed_run_id="idle-rig")
            saved = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], baseline_idle.STATUS_DISCONNECTED_RIG)
            self.assertTrue(saved["disconnect"]["rig_port_off_verified"])
            self.assertFalse(saved["disconnect"][
                "physical_disconnect_requires_finish_attestation"])

    def test_finish_restores_rig_without_physical_disconnect_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._run_dir(root)
            _, protocol_digest = baseline_protocol.load_protocol(
                TOOLS / "config" / "baseline.json")
            report = {
                "schema_version": 1,
                "operation": "baseline-idle-pilot",
                "label": "PILOT_ONLY_NOT_BASELINE_EVIDENCE",
                "run_id": "idle-rig",
                "duration_seconds": 300,
                "disconnect_method": "verified-rig-port-off",
                "status": baseline_idle.STATUS_DISCONNECTED_RIG,
                "target": {"role": "harness"},
                "protocol": {"sha256": protocol_digest},
                "start_state": {"battery": {
                    "level_percent": 100, "charge_counter_uah": 4000000,
                }},
                "disconnect": {
                    "host_boottime_seconds": 100.0,
                    "host_boot_id_sha256": "a" * 64,
                    "raw_evidence_refs": [],
                },
                "operator_attestations": {
                    "physical_usb_disconnect": None,
                    "verified_rig_port_off": None,
                },
                "identity_evidence_refs": [],
                "condition_evidence_refs": [],
                "reset_evidence_refs": [],
                "finish_evidence_refs": [],
                "comparability_exclusions": [],
                "tool": {},
            }
            (run_dir / "result.json").write_text(
                json.dumps(report), encoding="utf-8")
            controller = mock.Mock()
            controller.set_power.return_value = {
                "status": "PASS", "role": "harness", "action": "on"
            }
            args = types.SimpleNamespace(
                operator_confirmed_physical_disconnect=False,
                operator_confirmed_no_interaction=True,
                operator_confirmed_no_known_network_outage=True,
                device_map="/private/map.json", device_role="harness",
                target="private-target", run_dir=str(run_dir), adb="adb",
                rig_config="/private/rig.json", wait_for_reconnect=False,
                reconnect_timeout_seconds=10,
                config=str(TOOLS / "config" / "baseline.json"))
            clock = {
                "utc": "2026-09-14T00:05:01Z",
                "host_boottime_seconds": 401.0,
                "host_boot_id_sha256": "a" * 64,
            }
            end_state = {
                "battery": {
                    "level_percent": 99, "charge_counter_uah": 3975000,
                },
                "network": {
                    "wifi_enabled": True, "wifi_connected": True,
                    "sim_registered": True,
                },
            }
            batterystats = {
                "transport": "ok", "stdout": "bounded stats\n", "stderr": "",
            }
            reconnect = {
                "first_present": clock,
                "confirmed_present_samples": 2,
                "observation_count": 2,
            }
            with mock.patch.object(
                    baseline_idle.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_idle.device, "authorized_devices",
                        return_value=[]), \
                    mock.patch.object(
                        baseline_idle.rig, "controller_for_target",
                        return_value=controller), \
                    mock.patch.object(
                        baseline_idle, "_host_clock_sample", return_value=clock), \
                    mock.patch.object(
                        baseline_idle, "wait_for_authorized_reconnect",
                        return_value=reconnect), \
                    mock.patch.object(
                        baseline_idle, "_collect_state",
                        return_value=(end_state, [])), \
                    mock.patch.object(
                        baseline_idle, "_run_required",
                        return_value=batterystats), \
                    mock.patch.object(
                        baseline_idle, "evaluate_comparability", return_value=[]):
                code, result = baseline_idle.finish(args)
            self.assertEqual(code, 0)
            self.assertTrue(result.parent.name == "idle-rig")
            controller.set_power.assert_called_once_with(
                "harness", "on", "idle-finish", allowed_run_id="idle-rig")
            saved = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "PASS")
            self.assertFalse(saved["operator_attestations"][
                "physical_usb_disconnect"])
            self.assertTrue(saved["operator_attestations"][
                "verified_rig_port_off"])

    def test_automated_finish_requires_bound_start_commitments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = self._run_dir(root)
            report = {
                "schema_version": 1,
                "run_id": "idle-rig",
                "duration_seconds": 300,
                "disconnect_method": "verified-rig-port-off",
                "status": baseline_idle.STATUS_DISCONNECTED_RIG,
                "target": {"role": "harness"},
                "unattended_series_commitments": None,
            }
            (run_dir / "result.json").write_text(
                json.dumps(report), encoding="utf-8")
            args = types.SimpleNamespace(
                automated_rig_series=True,
                operator_confirmed_no_interaction=False,
                operator_confirmed_no_known_network_outage=False,
                operator_confirmed_physical_disconnect=False,
                device_map="/private/map.json", device_role="harness",
                target="private-target", run_dir=str(run_dir), adb="adb",
                rig_config="/private/rig.json", wait_for_reconnect=False)
            with mock.patch.object(
                    baseline_idle.test_runner, "load_device_map"), \
                    mock.patch.object(
                        baseline_idle, "_load_report", return_value=report):
                with self.assertRaisesRegex(
                        baseline_idle.IdleError, "bound unattended"):
                    baseline_idle.finish(args)


if __name__ == "__main__":
    unittest.main()
