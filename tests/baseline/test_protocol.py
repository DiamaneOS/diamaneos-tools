"""FP6 stock baseline protocol and comparison boundary tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2]
CONFIG = TOOLS / "config" / "baseline.json"
_SPEC = importlib.util.spec_from_file_location(
    "baseline_protocol", TOOLS / "src" / "diamaneos_tools" /
    "baseline_protocol.py")
baseline_protocol = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(baseline_protocol)


def actual_protocol():
    protocol, _ = baseline_protocol.load_protocol(CONFIG)
    return protocol


def conditions(run_id="run-1"):
    return {
        "run_id": run_id,
        "build": "FP6.QREL.16.100.0",
        "ambient": {"start_c": 22.0, "end_c": 22.5},
        "display": {
            "adaptive_brightness": False,
            "brightness_raw": 2048,
            "screen_timeout_ms": 600000,
            "peak_refresh_rate_hz": 90.0,
        },
        "network": {"profile": "offline-radio-controlled"},
        "power": {"profile": "usb-data-at-full-battery"},
    }


class ProtocolValidationTest(unittest.TestCase):
    def test_actual_protocol_is_complete(self):
        protocol, digest = baseline_protocol.load_protocol(CONFIG)
        self.assertEqual(protocol["protocol_id"], "fp6-stock-baseline-v1")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_duplicate_json_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version":2,"schema_version":2}\n')
            with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                        "duplicate JSON key"):
                baseline_protocol.load_protocol(path)

    def test_unknown_field_is_rejected(self):
        protocol = actual_protocol()
        protocol["unexpected"] = True
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "unknown fields"):
            baseline_protocol.validate_protocol(protocol)

    def test_placeholder_is_rejected(self):
        protocol = actual_protocol()
        protocol["procedures"][0]["method"] = "TBD later"
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "unresolved placeholder"):
            baseline_protocol.validate_protocol(protocol)

    def test_arbitrary_launch_component_is_rejected(self):
        protocol = actual_protocol()
        app = protocol["procedures"][0]["fixed_parameters"]["apps"][0]
        app["component"] = "com.android.settings/.Settings;wipe"
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "component is invalid"):
            baseline_protocol.validate_protocol(protocol)

    def test_thermal_abort_must_leave_manufacturer_margin(self):
        protocol = actual_protocol()
        thermal = next(item for item in protocol["procedures"]
                       if item["id"] == "thermal")
        thermal["fixed_parameters"]["safety"][
            "abort_skin_degC_at_or_above"] = 55.0
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "must leave margin"):
            baseline_protocol.validate_protocol(protocol)

    def test_swipe_geometry_is_bounded_by_actual_display(self):
        protocol = actual_protocol()
        frame = next(item for item in protocol["procedures"]
                     if item["id"] == "frame-time")
        frame["fixed_parameters"]["interaction"]["bottom_y_px"] = 9000
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "exceeds its maximum"):
            baseline_protocol.validate_protocol(protocol)

    def test_camera_fixture_distance_is_bounded(self):
        protocol = actual_protocol()
        camera = next(item for item in protocol["procedures"]
                      if item["id"] == "camera-scene")
        camera["fixed_parameters"]["fixture"][
            "nominal_phone_to_focus_target_distance_cm"] = 0
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "below its minimum"):
            baseline_protocol.validate_protocol(protocol)

    def test_camera_settle_time_is_bounded(self):
        protocol = actual_protocol()
        camera = next(item for item in protocol["procedures"]
                      if item["id"] == "camera-scene")
        camera["fixed_parameters"]["capture_defaults"][
            "lamp_settle_seconds"] = 121
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "exceeds its maximum"):
            baseline_protocol.validate_protocol(protocol)


class NumericSummaryTest(unittest.TestCase):
    def test_failure_is_retained_and_never_averaged_as_zero(self):
        summary = baseline_protocol.summarize_numeric_samples([
            {"run_id": "run-pass", "status": "PASS", "value": 120},
            {"run_id": "run-fail", "status": "FAIL"},
        ], "ms")
        self.assertEqual(summary["status"], "INCOMPLETE")
        self.assertEqual(summary["statistics"]["mean"], 120.0)
        self.assertEqual(summary["successful_sample_count"], 1)
        self.assertEqual(summary["non_passing_samples"], [
            {"run_id": "run-fail", "status": "FAIL"}
        ])

    def test_nonpassing_sample_cannot_smuggle_numeric_zero(self):
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "must not carry"):
            baseline_protocol.summarize_numeric_samples([
                {"run_id": "failed", "status": "FAIL", "value": 0}
            ], "ms")

    def test_pass_requires_a_value(self):
        with self.assertRaisesRegex(baseline_protocol.ProtocolError,
                                    "has no value"):
            baseline_protocol.summarize_numeric_samples([
                {"run_id": "missing", "status": "PASS"}
            ], "ms")


class ComparabilityTest(unittest.TestCase):
    def test_matched_repeat_is_comparable(self):
        first = conditions("run-1")
        second = conditions("run-2")
        second["ambient"] = {"start_c": 23.0, "end_c": 22.0}
        result = baseline_protocol.assess_comparability(
            first, second, actual_protocol())
        self.assertEqual(result["status"], "COMPARABLE")
        self.assertEqual(result["reasons"], [])

    def test_changed_ambient_is_non_comparable(self):
        first = conditions("run-1")
        second = conditions("run-2")
        second["ambient"] = {"start_c": 25.0, "end_c": 25.0}
        result = baseline_protocol.assess_comparability(
            first, second, actual_protocol())
        self.assertEqual(result["status"], "NON_COMPARABLE")
        self.assertIn("repeat start temperatures differ beyond tolerance",
                      result["reasons"])

    def test_changed_network_is_non_comparable(self):
        first = conditions("run-1")
        second = conditions("run-2")
        second["network"] = {"profile": "connected-wifi-and-sim"}
        result = baseline_protocol.assess_comparability(
            first, second, actual_protocol())
        self.assertEqual(result["status"], "NON_COMPARABLE")
        self.assertIn("network conditions differ", result["reasons"])

    def test_camera_conditions_must_match_when_present(self):
        first = conditions("run-1")
        second = conditions("run-2")
        first["camera"] = {"rig_id": "rig-1", "distance_cm": 50}
        second["camera"] = {"rig_id": "rig-1", "distance_cm": 55}
        result = baseline_protocol.assess_comparability(
            first, second, actual_protocol())
        self.assertEqual(result["status"], "NON_COMPARABLE")
        self.assertIn("camera conditions differ", result["reasons"])


if __name__ == "__main__":
    unittest.main()
