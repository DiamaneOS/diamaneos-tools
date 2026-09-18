"""Declared connected-workflow contract tests."""

from pathlib import Path
import json
import os
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import baseline_connected
from diamaneos_tools import baseline_pilot


class ConnectedCliTest(unittest.TestCase):
    def test_run_requires_series_and_repeat_identity(self):
        parser = baseline_connected.build_parser()
        args = parser.parse_args([
            "run", "--target", "private", "--device-role", "harness",
            "--device-map", "map.json", "--run-id", "declared-r1",
            "--series-id", "stock16-series", "--repeat-index", "1",
            "--expected-build", "FP6.QREL.16.100.0",
            "--output", "runs", "--conditions", "fixed",
            "--ambient-start-c", "22.0",
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


class ConnectedFinalizeTest(unittest.TestCase):
    def _partial(self, root: Path, ambient_start: float) -> Path:
        run_dir = root / "connected-r1.partial"
        run_dir.mkdir(mode=0o750)
        report = {
            "schema_version": baseline_pilot.SCHEMA_VERSION,
            "operation": "baseline-connected-measurement",
            "label": baseline_pilot.DECLARED_LABEL,
            "status": "AWAITING_AMBIENT_END",
            "protocol": {
                "sha256": baseline_pilot.baseline_protocol.load_protocol(
                    TOOLS / "config" / "baseline.json")[1],
            },
            "target": {"role": "harness"},
            "series_id": "stock16-series",
            "repeat_index": 1,
            "run_profile": baseline_pilot.connected_profile(
                baseline_pilot.baseline_protocol.load_protocol(
                    TOOLS / "config" / "baseline.json")[0], True),
            "ambient_start_c": ambient_start,
            "identity_evidence_refs": [],
            "cases": [
                {"test_id": test_id, "status": "PASS", "raw_evidence_refs": []}
                for test_id in (
                    "connected-preflight", "app-launch", "frame-time", "thermal",
                    "memory-pressure", "connected-postflight")
            ],
            "run_order": [
                "connected-preflight", "app-launch", "frame-time", "thermal",
                "memory-pressure", "connected-postflight",
            ],
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
            code, result = baseline_connected.finalize(
                str(run_dir), 22.5, str(TOOLS / "config" / "baseline.json"))
            self.assertEqual(0, code)
            self.assertEqual("PASS", json.loads(result.read_text())["status"])
            self.assertFalse(run_dir.exists())

    def test_finalize_quarantines_out_of_tolerance_ambient(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o750)
            run_dir = self._partial(root, 22.0)
            code, result = baseline_connected.finalize(
                str(run_dir), 26.0, str(TOOLS / "config" / "baseline.json"))
            report = json.loads(result.read_text())
            self.assertEqual(4, code)
            self.assertEqual("NON_COMPARABLE", report["status"])
            self.assertTrue(report["comparability_exclusions"])
            self.assertTrue(result.parent.name.endswith(".non-comparable"))

    def test_declared_report_can_exceed_generic_runner_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o750)
            run_dir = self._partial(root, 22.0)
            result = run_dir / "result.json"
            report = json.loads(result.read_text(encoding="utf-8"))
            report["bounded_fixture_padding"] = (
                "x" * baseline_pilot.test_runner.MAX_REPORT_BYTES)
            result.write_text(json.dumps(report), encoding="utf-8")
            self.assertGreater(
                result.stat().st_size,
                baseline_pilot.test_runner.MAX_REPORT_BYTES)
            self.assertLess(
                result.stat().st_size,
                baseline_connected.MAX_CONNECTED_REPORT_BYTES)
            loaded = baseline_connected._load_partial(run_dir)
            self.assertEqual("AWAITING_AMBIENT_END", loaded["status"])


if __name__ == "__main__":
    unittest.main()
