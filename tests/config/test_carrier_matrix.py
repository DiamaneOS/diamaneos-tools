"""Contract and safety tests for the public FP6 carrier matrix."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import carrier_matrix as api


class CarrierMatrixTest(unittest.TestCase):
    def setUp(self):
        self.path = TOOLS / "config" / "carrier-matrix.json"
        self.matrix = api.load_matrix(self.path)

    def test_committed_matrix_is_valid_and_pending_is_explicit(self):
        self.assertEqual([], api.validate_matrix(self.matrix))
        profiles = {item["id"]: item for item in self.matrix["profiles"]}
        self.assertEqual("active",
                         profiles["fp6-vodafone-callya-classic-esim"]["availability"])
        self.assertEqual("pending-test-input",
                         profiles["fp6-blau-physical"]["availability"])
        self.assertTrue(all(row["observation_status"] in {"NOT_RUN", "BLOCKED"}
                            for row in self.matrix["capability_rows"]))

    def test_plan_eligibility_cannot_become_an_unsourced_claim(self):
        changed = copy.deepcopy(self.matrix)
        changed["capability_rows"][0]["source_refs"] = []
        self.assertIn("eligible capability row requires a source",
                      api.validate_matrix(changed))

    def test_device_result_requires_evidence(self):
        changed = copy.deepcopy(self.matrix)
        changed["capability_rows"][0]["observation_status"] = "PASS"
        self.assertIn("observed PASS/FAIL requires an evidence reference",
                      api.validate_matrix(changed))

    def test_duplicate_profile_capability_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        duplicate = copy.deepcopy(changed["capability_rows"][0])
        duplicate["id"] = "different-row-id"
        changed["capability_rows"].append(duplicate)
        self.assertIn("duplicate profile/capability row",
                      api.validate_matrix(changed))

    def test_private_subscriber_field_is_rejected_without_echo(self):
        changed = copy.deepcopy(self.matrix)
        changed["profiles"][0]["iccid"] = "synthetic-sensitive-value"
        errors = api.validate_matrix(changed)
        self.assertIn("private subscriber or credential field is not allowed", errors)
        self.assertNotIn("synthetic-sensitive-value", json.dumps(errors))

    def test_phone_like_value_is_rejected_without_echo(self):
        changed = copy.deepcopy(self.matrix)
        changed["profiles"][0]["plan_name"] = "+49 151 23456789"
        errors = api.validate_matrix(changed)
        self.assertIn("phone-like or subscriber-like value is not allowed", errors)
        self.assertNotIn("23456789", json.dumps(errors))

    def test_emergency_and_esim_boundaries_are_fail_closed(self):
        changed = copy.deepcopy(self.matrix)
        changed["safety"]["emergency_calls"] = "allowed"
        self.assertIn("carrier-matrix safety policy is not fail-closed",
                      api.validate_matrix(changed))

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "matrix.json"
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(api.MatrixError, "duplicate JSON key"):
                api.load_matrix(path)

    def test_cli_is_read_only_and_reports_pending_scope(self):
        before = self.path.read_bytes()
        result = subprocess.run(
            [str(TOOLS / "bin" / "diamaneos"), "carrier", "matrix", "validate"],
            cwd=TOOLS, capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("13 capability rows; 13 pending observations", result.stdout)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
