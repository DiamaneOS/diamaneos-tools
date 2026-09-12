"""FP6-022 physical-disconnect idle-pilot tests."""

from pathlib import Path
import hashlib
import sys
import tempfile
import unittest


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
            self.valid_observed(), protocol(), 22.0, True, True))

    def test_changed_network_power_and_display_are_all_reported(self):
        observed = self.valid_observed()
        observed["network"].update(
            airplane_mode=True, wifi_connected=False, sim_registered=False)
        observed["battery"].update(level_percent=80, ac_powered=False)
        observed["display"].update(adaptive_brightness=True, wakefulness="Asleep")
        reasons = baseline_idle.evaluate_idle_preflight(
            observed, protocol(), 35.0, False, False)
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
        report = {"ambient_start_c": 22.0, "pilot_duration_seconds": 300}
        network = {"wifi_enabled": False, "wifi_connected": False,
                   "sim_registered": False}
        reasons = baseline_idle.evaluate_comparability(
            report, protocol(), 26.0, 361, network, False)
        self.assertEqual(5, len(reasons))

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
            "--conditions", "controlled", "--ambient-start-c", "22.0",
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


if __name__ == "__main__":
    unittest.main()
