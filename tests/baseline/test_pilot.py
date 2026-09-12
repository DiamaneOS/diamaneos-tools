"""Connected FP6 baseline-pilot parser and safety tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_pilot
from diamaneos_tools import baseline_protocol


def protocol():
    return baseline_protocol.load_protocol(TOOLS / "config" / "baseline.json")[0]


class ParserTest(unittest.TestCase):
    def test_launch_parser_keeps_reported_time_and_state(self):
        parsed = baseline_pilot.parse_am_start(
            "Status: ok\nLaunchState: COLD\nActivity: com.example/.Main\n"
            "TotalTime: 321\nWaitTime: 340\nComplete\n")
        self.assertEqual("PASS", parsed["status"])
        self.assertEqual(321, parsed["timing_ms"])
        self.assertEqual("TotalTime", parsed["timing_field"])
        self.assertEqual("COLD", parsed["launch_state"])

    def test_failed_launch_has_no_zero_timing(self):
        parsed = baseline_pilot.parse_am_start("Status: timeout\nWaitTime: 0\n")
        self.assertEqual("FAIL", parsed["status"])
        self.assertNotIn("timing_ms", parsed)

    def test_zero_launch_time_is_not_a_measurement(self):
        parsed = baseline_pilot.parse_am_start(
            "Status: ok\nLaunchState: UNKNOWN (0)\nTotalTime: 0\n")
        self.assertEqual("FAIL", parsed["status"])
        self.assertNotIn("timing_ms", parsed)

    def test_gfxinfo_parser_uses_package_aggregate(self):
        parsed = baseline_pilot.parse_gfxinfo(
            "Total frames rendered: 411\n"
            "Janky frames: 13 (3.16%)\n"
            "50th percentile: 6ms\n90th percentile: 17ms\n"
            "95th percentile: 19ms\n99th percentile: 30ms\n")
        self.assertEqual("PASS", parsed["status"])
        self.assertEqual(411, parsed["total_frames"])
        self.assertEqual(13, parsed["janky_frames"])
        self.assertEqual({"50": 6, "90": 17, "95": 19, "99": 30},
                         parsed["percentiles_ms"])

    def test_gfxinfo_zero_frames_is_a_failure_not_zero_sample(self):
        parsed = baseline_pilot.parse_gfxinfo(
            "Total frames rendered: 0\nJanky frames: 0 (0.00%)\n"
            "50th percentile: 0ms\n90th percentile: 0ms\n"
            "95th percentile: 0ms\n99th percentile: 0ms\n")
        self.assertEqual("FAIL", parsed["status"])
        self.assertNotIn("total_frames", parsed)

    def test_frame_count_must_cover_completed_swipes(self):
        parsed = {"status": "PASS", "total_frames": 1}
        checked = baseline_pilot.enforce_frame_minimum(parsed, 4, 1)
        self.assertEqual("FAIL", checked["status"])
        self.assertEqual(4, checked["minimum_frames"])

    def test_awake_power_state_is_parsed_exactly(self):
        self.assertEqual("Awake", baseline_pilot.parse_power_wakefulness(
            "Power Manager State:\n  mWakefulness=Awake\n"))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            baseline_pilot.parse_power_wakefulness("mInteractive=true\n")

    def test_power_wakefulness_capture_is_a_bounded_remote_filter(self):
        remote = baseline_pilot._power_wakefulness_remote()
        self.assertTrue(remote.startswith("sh -c '"))
        self.assertIn("grep -m 1", remote)
        self.assertIn("mWakefulness=", remote)

    def test_battery_parser_preserves_fairphone_ac_classification(self):
        parsed = baseline_pilot.parse_battery(
            "Battery Service state:\n  AC powered: true\n  USB powered: false\n"
            "  Wireless powered: false\n  status: 5\n  level: 100\n"
            "  scale: 100\n  voltage: 4457\n  temperature: 250\n")
        self.assertTrue(parsed["ac_powered"])
        self.assertFalse(parsed["usb_powered"])
        self.assertEqual(25.0, parsed["temperature_c"])

    def test_dedicated_wifi_status_not_global_integer_is_authoritative(self):
        self.assertFalse(baseline_pilot.parse_wifi_status("Wifi is disabled\n"))
        self.assertTrue(baseline_pilot.parse_wifi_status(
            "Wifi is enabled\nWifi scanning is always available\n"))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            baseline_pilot.parse_wifi_status("3\n")

    def test_package_version_preserves_spaces_and_build_suffix(self):
        parsed = baseline_pilot.parse_package_version(
            "  Package [com.example] (abc):\n"
            "    versionCode=85022643 minSdk=32 targetSdk=37\n"
            "    versionName=9.3 (967717812)\n")
        self.assertEqual(85022643, parsed["version_code"])
        self.assertEqual("9.3 (967717812)", parsed["version_name"])

    def test_meminfo_and_vmstat_parsers_keep_real_counters(self):
        meminfo = baseline_pilot.parse_meminfo_summary(
            "Total RAM: 7,579,252K (status moderate)\n"
            " Free RAM: 4,761,803K\n Used RAM: 3,349,052K\n"
            " Lost RAM: 171,703K\n")
        self.assertEqual(7579252, meminfo["total_kib"])
        vmstat = baseline_pilot.parse_vmstat_counters(
            "pgmajfault 1240087\noom_kill 0\ncompact_fail 5\n"
            "compact_success 33\n")
        self.assertEqual(0, vmstat["oom_kill"])
        self.assertEqual(33, vmstat["compact_success"])

    def test_logcat_header_is_not_counted_as_an_event(self):
        self.assertEqual(2, baseline_pilot.count_logcat_records(
            "--------- beginning of events\n1.0 I am_kill: first\n"
            "2.0 I am_low_memory: second\n"))

    def test_thermal_parser_ignores_stale_cached_temperatures(self):
        parsed = baseline_pilot.parse_thermalservice(
            "Thermal Status: 0\nCached temperatures:\n"
            "\tTemperature{mValue=80.0, mType=2, mName=battery, mStatus=3}\n"
            "\tTemperature{mValue=70.0, mType=3, mName=skin, mStatus=3}\n"
            "Current temperatures from HAL:\n"
            "\tTemperature{mValue=25.0, mType=2, mName=battery, mStatus=0}\n"
            "\tTemperature{mValue=26.274, mType=3, mName=skin, mStatus=0}\n"
            "Current cooling devices from HAL:\n")
        self.assertEqual(25.0, parsed["temperatures_c"]["battery"])
        self.assertEqual(26.274, parsed["temperatures_c"]["skin"])
        safety = next(item for item in protocol()["procedures"]
                      if item["id"] == "thermal")["fixed_parameters"]["safety"]
        self.assertIsNone(baseline_pilot.thermal_stop_reason(parsed, safety))

    def test_thermal_sysfs_parser_retains_zone_type_and_raw_unit(self):
        parsed = baseline_pilot.parse_thermal_sysfs(
            "thermal_zone10\tcpuss-0\t26400\n"
            "thermal_zone53\tbattery\t24000\n", 128)
        self.assertEqual("cpuss-0", parsed[0]["type"])
        self.assertEqual(26400, parsed[0]["raw_millidegree_c"])
        self.assertEqual("battery", parsed[1]["type"])

    def test_thermal_stop_is_conservative(self):
        sample = {
            "android_status": 0,
            "temperatures_c": {"battery": 50.0, "skin": 30.0},
        }
        safety = next(item for item in protocol()["procedures"]
                      if item["id"] == "thermal")["fixed_parameters"]["safety"]
        self.assertIn("battery", baseline_pilot.thermal_stop_reason(sample, safety))

    def test_thermal_remote_script_is_one_quoted_adb_operand(self):
        argv = baseline_pilot._thermal_workload_argv(
            "adb", "private-target", "/data/local/tmp/fixed.pids", 4, 30,
            1048576)
        self.assertEqual(["adb", "-s", "private-target", "shell"], argv[:4])
        self.assertEqual(5, len(argv))
        self.assertIn("sh -c '", argv[4])
        self.assertIn("/data/local/tmp/fixed.pids 4 30 1048576", argv[4])


class PreflightTest(unittest.TestCase):
    def valid_observed(self):
        return {
            "display": {
                "adaptive_brightness": False,
                "brightness_raw": 512,
                "wakefulness": "Awake",
                "screen_timeout_ms": 600000,
                "peak_refresh_rate_hz": 90.0,
                "minimum_refresh_rate_hz": 1.0,
                "physical_width_px": 1116,
                "physical_height_px": 2484,
                "physical_density_dpi": 480,
            },
            "network": {
                "profile": "offline-radio-controlled",
                "airplane_mode": True,
                "wifi": False,
                "bluetooth": False,
                "sim_states": ["LOADED", "NOT_READY"],
            },
            "battery": {
                "level_percent": 100,
                "status_code": 5,
                "ac_powered": True,
                "usb_powered": False,
            },
        }

    def test_matching_controls_allow_workload(self):
        reasons = baseline_pilot.evaluate_preflight(
            self.valid_observed(), protocol(), 22.0, True, True)
        self.assertEqual([], reasons)

    def test_every_changed_control_is_visible(self):
        observed = self.valid_observed()
        observed["display"]["adaptive_brightness"] = True
        observed["display"]["wakefulness"] = "Asleep"
        observed["network"]["wifi"] = True
        observed["battery"]["level_percent"] = 90
        reasons = baseline_pilot.evaluate_preflight(
            observed, protocol(), 35.0, False, False)
        self.assertGreaterEqual(len(reasons), 6)
        self.assertTrue(any("room temperature" in reason for reason in reasons))
        self.assertTrue(any("brightness" in reason for reason in reasons))
        self.assertTrue(any("not awake" in reason for reason in reasons))
        self.assertTrue(any("Wi-Fi" in reason for reason in reasons))
        self.assertTrue(any("battery level" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
