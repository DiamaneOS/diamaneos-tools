"""FP6-022 camera-plan and original-media safety tests."""

from pathlib import Path
import hashlib
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_camera
from diamaneos_tools import baseline_protocol


def protocol():
    return baseline_protocol.load_protocol(TOOLS / "config" / "baseline.json")[0]


class CameraPlanTest(unittest.TestCase):
    def test_pilot_plan_has_eleven_rear_then_four_front_captures(self):
        plan = baseline_camera.build_capture_plan(protocol(), 1)
        self.assertEqual(15, len(plan))
        self.assertTrue(all(item["orientation"] == "rear-facing"
                            for item in plan[:11]))
        self.assertTrue(all(item["orientation"] == "front-facing"
                            for item in plan[11:]))
        self.assertEqual(9, sum(item["required"] for item in plan))
        self.assertEqual(6, sum(not item["required"] for item in plan))
        self.assertEqual("super-night", plan[6]["mode"])
        self.assertEqual("multi-person", plan[12]["zoom"])

    def test_declared_series_repeats_standard_but_not_mode_survey(self):
        plan = baseline_camera.build_capture_plan(protocol(), 2)
        self.assertEqual(24, len(plan))
        self.assertEqual(18, sum(item["required"] for item in plan))
        self.assertEqual(6, sum(not item["required"] for item in plan))

    def test_capture_ids_are_unique_and_token_safe(self):
        plan = baseline_camera.build_capture_plan(protocol(), 2)
        ids = [item["capture_id"] for item in plan]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(baseline_camera.test_runner.CASE_ID_RE.fullmatch(item)
                            for item in ids))


class CameraPreflightTest(unittest.TestCase):
    def valid_observed(self):
        return {
            "display": {
                "adaptive_brightness": False,
                "wakefulness": "Awake",
                "screen_timeout_ms": 600000,
                "peak_refresh_rate_hz": 90.0,
                "physical_width_px": 1116,
                "physical_height_px": 2484,
                "physical_density_dpi": 480,
            },
            "network": {
                "airplane_mode": True,
                "wifi": False,
                "bluetooth": False,
                "sim_states": ["LOADED", "NOT_READY"],
            },
            "battery": {
                "level_percent": 99,
                "status_code": 2,
                "ac_powered": True,
                "usb_powered": False,
                "temperature_c": 26.0,
            },
            "apps": [{"role": "camera", "version_name": "stock"}],
        }

    def test_camera_accepts_charging_at_99_percent(self):
        reasons = baseline_camera.evaluate_camera_preflight(
            self.valid_observed(), protocol(), True, True)
        self.assertEqual([], reasons)

    def test_camera_still_rejects_changed_controls_and_unsafe_temperature(self):
        observed = self.valid_observed()
        observed["network"]["wifi"] = True
        observed["display"]["wakefulness"] = "Asleep"
        observed["battery"]["temperature_c"] = 50.0
        reasons = baseline_camera.evaluate_camera_preflight(
            observed, protocol(), True, True)
        self.assertTrue(any("Wi-Fi" in item for item in reasons))
        self.assertTrue(any("awake" in item for item in reasons))
        self.assertTrue(any("temperature" in item for item in reasons))


class CameraUiTest(unittest.TestCase):
    def expected(self, **changes):
        value = {"orientation": "rear-facing", "mode": "photo", "zoom": "1x"}
        value.update(changes)
        return value

    def test_launcher_is_rejected_before_shutter(self):
        reasons = baseline_camera.validate_camera_ui(
            '<node package="com.google.android.apps.nexuslauncher"/>', self.expected())
        self.assertEqual(["stock camera is not the foreground UI"], reasons)

    def test_rear_1x_requires_live_shutter_orientation_and_selected_zoom(self):
        xml = ('<node package="com.fps.camera" text="PHOTO" content-desc="Shutter"/>'
               '<node package="com.fps.camera" content-desc="Switch to front camera"/>'
               '<node package="com.fps.camera" content-desc="Zoom value is 1.0" '
               'selected="true"/>')
        self.assertEqual([], baseline_camera.validate_camera_ui(xml, self.expected()))
        self.assertTrue(baseline_camera.validate_camera_ui(
            xml, self.expected(orientation="front-facing")))

    def test_macro_must_be_selected(self):
        xml = ('<node package="com.fps.camera" text="PHOTO" content-desc="Shutter"/>'
               '<node package="com.fps.camera" content-desc="Switch to front camera"/>'
               '<node package="com.fps.camera" content-desc="SUPER MACRO" '
               'selected="true"/>')
        self.assertEqual([], baseline_camera.validate_camera_ui(
            xml, self.expected(zoom="macro")))

    def test_control_center_uses_live_accessibility_bounds(self):
        xml = ('<node text="PRO" bounds="[650,1771][792,1864]"/>'
               '<node text="PHOTO" bounds="[184,1771][412,1864]"/>')
        self.assertEqual((721, 1817),
                         baseline_camera.camera_node_center(xml, "text", "PRO"))
        with self.assertRaises(baseline_camera.CameraError):
            baseline_camera.camera_node_center(xml, "text", "VIDEO")

    def test_duplicate_wrapper_prefers_the_one_clickable_control(self):
        xml = ('<node content-desc="Face beauty" clickable="true" '
               'bounds="[39,1596][141,1698]"/>'
               '<node content-desc="Face beauty" clickable="false" '
               'bounds="[39,1596][141,1698]"/>')
        self.assertEqual((90, 1647), baseline_camera.camera_node_center(
            xml, "content-desc", "Face beauty"))

    def test_portrait_has_mode_specific_selected_state_and_zoom_coordinate(self):
        expected = {"orientation": "rear-facing", "mode": "portrait", "zoom": "1x"}
        xml = ('<node package="com.fps.camera" content-desc="Shutter"/>'
               '<node package="com.fps.camera" content-desc="Switch to front camera"/>'
               '<node package="com.fps.camera" content-desc="PORTRAIT,Selected"/>'
               '<node package="com.fps.camera" '
               'resource-id="com.fps.camera:id/zoom_1x" selected="true"/>')
        self.assertEqual([], baseline_camera.validate_camera_ui(xml, expected))

    def test_portrait_distance_warning_is_retained(self):
        xml = '<node text="Move farther for better effects."/>'
        self.assertEqual(["Move farther for better effects."],
                         baseline_camera.camera_ui_observations(xml))

    def test_selected_pro_without_switch_is_observed_as_rear_only(self):
        expected = {"orientation": "rear-facing", "mode": "pro", "zoom": "1x"}
        xml = ('<node package="com.fps.camera" content-desc="Shutter"/>'
               '<node package="com.fps.camera" content-desc="PRO,Selected"/>'
               '<node package="com.fps.camera" content-desc="Zoom value is 1.0" '
               'selected="true"/>')
        self.assertEqual("rear-facing", baseline_camera.current_camera_orientation(xml))
        self.assertEqual([], baseline_camera.validate_camera_ui(xml, expected))

    def test_front_uses_back_switch_and_single_person_field_of_view(self):
        expected = {"orientation": "front-facing", "camera": "front",
                    "mode": "photo", "zoom": "1x"}
        xml = ('<node package="com.fps.camera" text="PHOTO" '
               'content-desc="Shutter"/>'
               '<node package="com.fps.camera" '
               'content-desc="Switch to back camera"/>'
               '<node package="com.fps.camera" '
               'resource-id="com.fps.camera:id/single_person"/>'
               '<node package="com.fps.camera" '
               'resource-id="com.fps.camera:id/multi_person"/>'
               '<node package="com.fps.camera" content-desc="Face beauty"/>')
        self.assertEqual("front-facing",
                         baseline_camera.current_camera_orientation(xml))
        self.assertEqual([], baseline_camera.validate_camera_ui(xml, expected))
        observations = baseline_camera.camera_ui_observations(xml)
        self.assertTrue(any("field-of-view" in item for item in observations))
        self.assertTrue(any("Face beauty" in item for item in observations))
        self.assertEqual([], baseline_camera.validate_camera_ui(
            xml, {**expected, "zoom": "multi-person"}))

    def test_face_beauty_level_requires_matching_bounded_value(self):
        xml = ('<node resource-id="com.fps.camera:id/face_beauty_info" '
               'text="0" content-desc="0"/>')
        self.assertEqual(0, baseline_camera.face_beauty_level(xml))
        for invalid in (
                '<node resource-id="com.fps.camera:id/face_beauty_info" '
                'text="1" content-desc="0"/>',
                '<node resource-id="com.fps.camera:id/face_beauty_info" '
                'text="101" content-desc="101"/>'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(baseline_camera.CameraError):
                    baseline_camera.face_beauty_level(invalid)

    def test_face_beauty_panel_is_dismissed_through_its_preview_root(self):
        xml = ('<node resource-id="com.fps.camera:id/face_beauty_root" '
               'bounds="[0,0][1116,2484]"/>'
               '<node resource-id="com.fps.camera:id/face_beauty_seekbar_layout" '
               'bounds="[0,1410][1116,1728]"/>')
        self.assertEqual((558, 705),
                         baseline_camera.face_beauty_dismiss_point(xml))

    def test_super_night_requires_selected_mode_and_retains_zoom(self):
        expected = {"orientation": "rear-facing", "mode": "super-night",
                    "zoom": "1x"}
        xml = ('<node package="com.fps.camera" content-desc="Shutter"/>'
               '<node package="com.fps.camera" '
               'content-desc="Switch to front camera"/>'
               '<node package="com.fps.camera" '
               'content-desc="SUPER NIGHT,Selected" text="SUPER NIGHT"/>'
               '<node package="com.fps.camera" content-desc="Zoom value is 1.0" '
               'selected="true"/>')
        self.assertEqual([], baseline_camera.validate_camera_ui(xml, expected))

    def test_quarantine_defaults_to_harness_error_and_accepts_non_comparable(self):
        parser = baseline_camera.build_parser()
        common = ["quarantine", "--target", "target", "--device-role", "role",
                  "--device-map", "map.json", "--run-dir", "run.partial",
                  "--reason", "fixture changed"]
        self.assertEqual("HARNESS_ERROR", parser.parse_args(common).status)
        self.assertEqual("NON_COMPARABLE", parser.parse_args(
            common + ["--status", "NON_COMPARABLE"]).status)


class MediaParserTest(unittest.TestCase):
    def test_media_listing_accepts_original_extensions(self):
        parsed = baseline_camera.parse_remote_media_listing(
            "/sdcard/DCIM/Camera/IMG_001.JPG\n"
            "/sdcard/DCIM/Camera/IMG_002.HEIC\n")
        self.assertEqual({"IMG_001.JPG", "IMG_002.HEIC"}, parsed)

    def test_media_listing_rejects_path_escape_and_unknown_extension(self):
        for value in ("/sdcard/DCIM/other/IMG_001.JPG\n",
                      "/sdcard/DCIM/Camera/IMG 001.JPG\n",
                      "/sdcard/DCIM/Camera/IMG_001.mp4\n"):
            with self.subTest(value=value):
                with self.assertRaises(baseline_camera.CameraError):
                    baseline_camera.parse_remote_media_listing(value)

    def test_checksum_binds_expected_basename(self):
        digest = "a" * 64
        self.assertEqual(digest, baseline_camera.parse_sha256(
            digest + "  /sdcard/DCIM/Camera/IMG_001.JPG\n", "IMG_001.JPG"))
        with self.assertRaises(baseline_camera.CameraError):
            baseline_camera.parse_sha256(
                digest + "  /sdcard/DCIM/Camera/IMG_002.JPG\n", "IMG_001.JPG")

    def test_empty_and_changing_media_are_not_stable(self):
        digest_a = "a" * 64
        digest_b = "b" * 64
        with self.assertRaisesRegex(baseline_camera.CameraError, "still empty"):
            baseline_camera.parse_media_size("0\n")
        self.assertFalse(baseline_camera.media_state_is_stable(
            [(100, digest_a), (200, digest_b), (200, digest_b)]))
        self.assertTrue(baseline_camera.media_state_is_stable(
            [(100, digest_a), (200, digest_b), (200, digest_b), (200, digest_b)]))

    def test_private_ref_verifier_accepts_binary_above_text_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"camera-screenshot" * 20000
            screenshot = root / "raw" / "front.png"
            screenshot.parent.mkdir()
            screenshot.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            baseline_camera.verify_private_refs(
                root / "result.json",
                [f"raw/front.png@sha256:{digest}"])
            with self.assertRaises(baseline_camera.CameraError):
                baseline_camera.verify_private_refs(
                    root / "result.json",
                    ["raw/front.png@sha256:" + "0" * 64])


class CliContractTest(unittest.TestCase):
    def test_declared_mode_is_explicit_and_defaults_to_pilot(self):
        parser = baseline_camera.build_parser()
        self.assertFalse(parser.parse_args(["dry-run"]).declared)
        self.assertTrue(parser.parse_args(["dry-run", "--declared"]).declared)
        declared = baseline_camera.dry_run(str(TOOLS / "config" / "baseline.json"),
                                           declared=True)
        self.assertEqual("DECLARED_STOCK_BASELINE_EVIDENCE", declared["label"])
        self.assertEqual(24, declared["capture_count"])

    def test_start_names_the_human_facing_build_property(self):
        parser = baseline_camera.build_parser()
        args = parser.parse_args([
            "start", "--target", "private-target", "--device-role", "camera-pilot",
            "--device-map", "/private/map.json", "--run-id", "camera-pilot-1",
            "--output", "/private/runs", "--expected-build", "FP6.QREL.15.176.0",
            "--conditions", "fixed fixture",
        ])
        self.assertEqual("FP6.QREL.15.176.0", args.expected_build)
        self.assertFalse(hasattr(args, "expected_incremental"))


class RunnerProvenanceTest(unittest.TestCase):
    def test_staged_run_retains_initial_and_corrected_runner_versions(self):
        report = {"tool": {
            "revision": "a" * 40,
            "runner_sha256": "b" * 64,
        }}
        original_revision = baseline_camera.test_runner._git_revision
        original_file = baseline_camera.__file__
        baseline_camera.test_runner._git_revision = lambda _root: "c" * 40
        try:
            current = baseline_camera._register_runner_version(report, TOOLS)
        finally:
            baseline_camera.test_runner._git_revision = original_revision
        self.assertEqual(hashlib.sha256(Path(original_file).read_bytes()).hexdigest(),
                         current)
        self.assertEqual(2, len(report["tool"]["runner_versions"]))
        self.assertEqual("b" * 64,
                         report["tool"]["runner_versions"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
