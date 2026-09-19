"""FP6 component-model and generated-closure validation."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import components as api
from jsonschema import Draft7Validator


class ComponentModelTest(unittest.TestCase):
    def setUp(self):
        self.model_path = TOOLS / "config" / "components.json"
        self.sources_path = TOOLS / "config" / "fp6-sources.json"
        self.model = api.load_json(self.model_path)
        self.sources = api.load_json(self.sources_path)
        self.environment = api.load_json(
            TOOLS / "config" / "build-environment.json")
        self.model_sha256 = hashlib.sha256(
            self.model_path.read_bytes()).hexdigest()
        self.source_sha256 = hashlib.sha256(
            self.sources_path.read_bytes()).hexdigest()

    def validate(self):
        return api.validate_model(
            self.model, self.sources, self.environment,
            source_sha256=self.source_sha256)

    def assert_bad(self, fragment=None):
        errors = self.validate()
        self.assertTrue(errors)
        if fragment:
            self.assertTrue(any(fragment in error for error in errors), errors)

    def closure(self):
        camera_path = "vendor/lib64/hw/camera.fp6.so"
        return {
            "schema_version": 1,
            "model_sha256": self.model_sha256,
            "stock_build": "FP6.QREL.16.100.0",
            "region": "EU",
            "artifacts": [{
                "path": camera_path,
                "sha256": "1" * 64,
                "component_id": "camera-stack",
                "source_or_prebuilt": "prebuilt",
                "inventory_ref": "userspace_hal_families:camera",
                "dependencies": [],
            }],
            "component_results": [{
                "component_id": item["id"],
                "presence": "present" if item["id"] == "camera-stack" else "absent",
                "artifact_paths": [camera_path] if item["id"] == "camera-stack" else [],
            } for item in self.model["fp6_components"]],
        }

    @staticmethod
    def evidence(*kinds):
        return [{"kind": kind, "reference": f"fixture {kind} evidence"}
                for kind in kinds]

    def test_committed_model_is_valid(self):
        self.assertEqual([], self.validate())

    def test_schemas_are_valid(self):
        for name in ("component-model.schema.json",
                     "component-closure.schema.json"):
            schema = json.loads((TOOLS / "schemas" / name).read_text())
            Draft7Validator.check_schema(schema)

    def test_model_binds_source_and_platform_inputs(self):
        self.model["fp6_model"]["inputs"]["fp6_source_inventory"]["sha256"] = "0" * 64
        self.assert_bad("source inventory hash mismatch")
        self.setUp()
        self.model["fp6_model"]["inputs"]["selected_stock"]["build"] = "FP6.QREL.16.104.0"
        self.assert_bad()
        self.setUp()
        self.environment["upstream"]["peeled_commit"] = "0" * 40
        self.assert_bad("platform environment binding mismatch")
        self.setUp()
        self.environment["device_inputs"]["selected_stock_build"] = \
            "FP6.QREL.16.104.0"
        self.assert_bad("build environment stock binding mismatch")

    def test_every_inventory_record_has_exactly_one_owner(self):
        first = self.model["fp6_components"][0]
        removed = first["inventory_refs"].pop()
        self.assert_bad("source inventory coverage mismatch")
        self.setUp()
        self.model["fp6_components"][1]["inventory_refs"].append(
            self.model["fp6_components"][0]["inventory_refs"][0])
        self.assert_bad("multiple owners")
        self.assertTrue(removed)

    def test_every_known_category_and_blocker_has_an_owner(self):
        self.model["fp6_components"].pop()
        self.assert_bad()
        self.setUp()
        self.model["fp6_components"][0]["blocker_refs"].pop()
        self.assert_bad("blocker coverage mismatch")

    def test_unknown_dependency_and_cycles_fail(self):
        self.model["fp6_components"][0]["dependencies"] = ["not-present"]
        self.assert_bad("dependency is missing")
        self.setUp()
        by_id = {entry["id"]: entry for entry in self.model["fp6_components"]}
        by_id["device-platform-kernel"]["dependencies"] = ["audio-stack"]
        self.assert_bad("dependency graph contains a cycle")

    def test_mismatched_reference_cannot_be_qualified(self):
        optional = next(entry for entry in self.model["fp6_components"]
                        if entry["id"] == "optional-vendor-services")
        optional["source_candidates"][0]["assessment"] = "qualified"
        self.assert_bad("mismatched source candidate is called qualified")

    def test_source_built_requires_exact_qualified_evidence(self):
        platform = self.model["fp6_components"][0]
        platform["decision_state"] = "accepted"
        platform["public_disposition"] = "source-built"
        self.assert_bad("source-built disposition lacks exact qualification")

    def test_public_dispositions_require_specific_evidence_classes(self):
        platform = self.model["fp6_components"][0]
        platform["decision_state"] = "accepted"
        platform["public_disposition"] = "source-built"
        platform["source_candidates"][0]["assessment"] = "qualified"
        platform["source_candidates"][0]["compatibility"] = "exact"
        platform["qualification_evidence"] = [{
            "kind": "build", "reference": "fixture build evidence",
        }]
        self.assert_bad("lacks required evidence classes")

    def test_necessary_prebuilt_requires_exact_allowed_references(self):
        camera = next(entry for entry in self.model["fp6_components"]
                      if entry["id"] == "camera-stack")
        camera["decision_state"] = "accepted"
        camera["public_disposition"] = "necessary-prebuilt"
        self.assert_bad("no exact allowed references")

    def test_model_supports_all_public_dispositions(self):
        platform = self.model["fp6_components"][0]
        platform["decision_state"] = "accepted"
        platform["public_disposition"] = "source-built"
        platform["source_candidates"][0]["assessment"] = "qualified"
        platform["source_candidates"][0]["compatibility"] = "exact"
        platform["qualification_evidence"] = self.evidence(
            "exact-source", "build", "interface", "security", "functional",
            "maintenance", "update-recovery")
        self.assertEqual([], self.validate())

        self.setUp()
        camera = next(entry for entry in self.model["fp6_components"]
                      if entry["id"] == "camera-stack")
        camera["decision_state"] = "accepted"
        camera["public_disposition"] = "necessary-prebuilt"
        camera["public_prebuilt_refs"] = [
            "userspace_hal_families:camera"]
        camera["qualification_evidence"] = self.evidence(
            "exact-artifact", "necessity", "alternatives", "integrity",
            "security", "functional", "maintenance", "exposure",
            "update-recovery")
        self.assertEqual([], self.validate())

        self.setUp()
        optional = next(entry for entry in self.model["fp6_components"]
                        if entry["id"] == "optional-vendor-services")
        optional["decision_state"] = "accepted"
        optional["public_disposition"] = "removed"
        optional["qualification_evidence"] = self.evidence(
            "absence", "functional", "security", "update-recovery")
        self.assertEqual([], self.validate())

        self.setUp()
        esim = next(entry for entry in self.model["fp6_components"]
                    if entry["id"] == "esim-stack")
        esim["decision_state"] = "accepted"
        esim["public_disposition"] = "research-only"
        esim["qualification_evidence"] = self.evidence("absence")
        self.assertEqual([], self.validate())

    def test_private_bringup_requires_declared_necessity(self):
        closure = self.closure()
        self.assertEqual([], api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256))
        camera = next(entry for entry in self.model["fp6_components"]
                      if entry["id"] == "camera-stack")
        camera["private_bringup"]["verified_necessary_for"] = []
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("not declared necessary" in error for error in errors))

    def test_private_closure_is_not_public_qualification(self):
        errors = api.validate_closure(
            self.model, self.closure(), model_sha256=self.model_sha256,
            public=True)
        self.assertTrue(any("unaccepted component" in error for error in errors))

    def test_unmapped_output_or_missing_dependency_fails(self):
        closure = self.closure()
        closure["artifacts"][0]["component_id"] = "unmapped-output"
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("unknown component" in error for error in errors))
        self.setUp()
        closure = self.closure()
        closure["artifacts"][0]["dependencies"] = ["vendor/lib64/missing.so"]
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("dependency is absent" in error for error in errors))

    def test_duplicate_artifact_provider_fails(self):
        closure = self.closure()
        closure["artifacts"].append(copy.deepcopy(closure["artifacts"][0]))
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("duplicate component artifact path" in error
                            for error in errors), errors)

    def test_artifact_source_reference_must_belong_to_component(self):
        closure = self.closure()
        closure["artifacts"][0]["inventory_ref"] = \
            "userspace_hal_families:audio"
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("not owned by component" in error
                            for error in errors), errors)

    def test_artifact_dependencies_follow_component_graph(self):
        closure = self.closure()
        foreign_path = "vendor/lib64/hw/audio.fp6.so"
        closure["artifacts"].append({
            "path": foreign_path,
            "sha256": "2" * 64,
            "component_id": "audio-stack",
            "source_or_prebuilt": "prebuilt",
            "inventory_ref": "userspace_hal_families:audio",
            "dependencies": [],
        })
        closure["artifacts"][0]["dependencies"] = [foreign_path]
        audio_result = next(
            result for result in closure["component_results"]
            if result["component_id"] == "audio-stack")
        audio_result["presence"] = "present"
        audio_result["artifact_paths"] = [foreign_path]
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("violates component graph" in error
                            for error in errors), errors)

    def test_closure_cannot_omit_a_component_result(self):
        closure = self.closure()
        closure["component_results"].pop()
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("closure coverage mismatch" in error for error in errors))

    def test_result_cannot_claim_an_unknown_artifact(self):
        closure = self.closure()
        closure["component_results"][0]["artifact_paths"] = [
            "vendor/lib64/not-in-artifact-inventory.so"]
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256,
            public=True)
        self.assertTrue(any("artifact mapping mismatch" in error
                            for error in errors), errors)

    def test_closure_is_bound_to_exact_model_and_stock(self):
        closure = self.closure()
        closure["model_sha256"] = "0" * 64
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("model hash mismatch" in error for error in errors))
        closure = self.closure()
        closure["stock_build"] = "FP6.QREL.16.104.0"
        errors = api.validate_closure(
            self.model, closure, model_sha256=self.model_sha256)
        self.assertTrue(any("stock identity mismatch" in error for error in errors))

    def test_schema_rejects_unknown_fields_and_missing_required_fields(self):
        self.model["fp6_components"][0]["unreviewed"] = True
        self.assert_bad()
        self.setUp()
        del self.model["fp6_components"][0]["next_experiment"]
        self.assert_bad()

    def test_schema_errors_and_secret_guards_do_not_echo_values(self):
        marker = "sentinel-private-value"
        self.model["fp6_components"][0]["id"] = marker
        self.assertNotIn(marker, " ".join(self.validate()))
        payload = json.dumps({"note": f"https://user:{marker}@example.invalid"}).encode()
        with self.assertRaises(api.ComponentError) as caught:
            api.loads(payload)
        self.assertNotIn(marker, str(caught.exception))

    def test_missing_schema_dependency_fails_closed(self):
        with patch.object(api, "Draft7Validator", None):
            self.assertIn("missing jsonschema", self.validate()[0])

    def test_cli_is_portable_and_read_only(self):
        command = [sys.executable, str(TOOLS / "bin" / "diamaneos"),
                   "components", "validate"]
        with tempfile.TemporaryDirectory() as temporary:
            before = set(Path(temporary).iterdir())
            result = subprocess.run(command, cwd=temporary,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("12 FP6 categories", result.stdout)
            self.assertIn("No artifact closure", result.stdout)
            self.assertEqual(before, set(Path(temporary).iterdir()))

    def test_cli_accepts_private_closure_and_rejects_public_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            closure_path = Path(temporary) / "closure.json"
            closure_path.write_text(json.dumps(self.closure()), encoding="utf-8")
            command = [sys.executable, str(TOOLS / "bin" / "diamaneos"),
                       "components", "validate", "--closure", str(closure_path)]
            result = subprocess.run(command, cwd=temporary,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("VALID private-bringup closure", result.stdout)
            result = subprocess.run(command + ["--public"], cwd=temporary,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(2, result.returncode)
            self.assertIn("unaccepted component", result.stderr)


if __name__ == "__main__":
    unittest.main()
