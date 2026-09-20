"""Contract tests for signing role, target-files and proof validation."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import signing_verify as api
from jsonschema import Draft7Validator


class SigningVerifyTest(unittest.TestCase):
    def setUp(self):
        self.config = api.load_json(TOOLS / "config" / "signing-roles.json")
        self.environment = api.load_json(
            TOOLS / "config" / "build-environment.json")

    def test_committed_role_contract_is_complete_and_valid(self):
        self.assertEqual([], api.validate_config(self.config, self.environment))
        self.assertEqual(api.EXPECTED_KEYS,
                         {entry["id"] for entry in self.config["key_roles"]})
        self.assertEqual(api.EXPECTED_ARTIFACT_ROLES,
                         {entry["id"] for entry in self.config["artifact_roles"]})
        self.assertEqual(api.EXPECTED_PROOFS,
                         set(self.config["required_dummy_proofs"]))

    def test_signing_schemas_are_valid(self):
        for name in ("signing-roles.schema.json",
                     "signing-dummy-result.schema.json"):
            schema = json.loads((TOOLS / "schemas" / name).read_text())
            Draft7Validator.check_schema(schema)

    def test_source_or_role_drift_fails_closed(self):
        changed = copy.deepcopy(self.config)
        changed["source_binding"]["manifest_commit"] = "0" * 40
        self.assertIn("signing source binding does not match build environment",
                      api.validate_config(changed, self.environment))
        changed = copy.deepcopy(self.config)
        changed["key_roles"].pop()
        self.assertIn("signing key-role set is incomplete or duplicated",
                      api.validate_config(changed, self.environment))
        changed = copy.deepcopy(self.config)
        changed["artifact_roles"][0]["key_ids"] = ["unlisted"]
        self.assertIn("artifact role refers to an unknown key role",
                      api.validate_config(changed, self.environment))

    def test_safety_boundary_cannot_authorize_production_operations(self):
        changed = copy.deepcopy(self.config)
        changed["safety"]["production_key_generation_allowed"] = True
        self.assertTrue(api.validate_config(changed, self.environment))

    @staticmethod
    def target_files(path, *, presigned=False, signed=True, duplicate=False):
        cert = "PRESIGNED" if presigned else (
            "keys/platform.x509.pem" if signed else
            "build/make/target/product/security/testkey.x509.pem")
        private = "PRESIGNED" if presigned else (
            "keys/platform.pk8" if signed else
            "build/make/target/product/security/testkey.pk8")
        apk = f'name="Settings.apk" certificate="{cert}" private_key="{private}"\n'
        apex_public = "avb" if signed else "com.android.runtime"
        apex_private = "avb" if signed else "com.android.runtime"
        apex_container = "releasekey" if signed else "testkey"
        apex = (
            'name="com.android.runtime.apex" '
            f'public_key="keys/{apex_public}.avbpubkey" '
            f'private_key="keys/{apex_private}.pem" '
            f'container_certificate="keys/{apex_container}.x509.pem" '
            f'container_private_key="keys/{apex_container}.pk8"\n'
        )
        avb_key = "avb" if signed else "testkey"
        avb_algorithm = "SHA256_RSA4096" if signed else "SHA256_RSA2048"
        misc = (
            f"avb_vbmeta_key_path=keys/{avb_key}.pem\n"
            f"avb_vbmeta_algorithm={avb_algorithm}\n"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META/apkcerts.txt", apk)
            archive.writestr("META/apexkeys.txt", apex)
            archive.writestr("META/misc_info.txt", misc)
            if duplicate:
                archive.writestr("META/apkcerts.txt", apk)

    def test_signed_target_files_inventory_passes_exact_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path)
            result = api.inspect_target_files(
                path, self.config, "generic-x86_64-qualification",
                stage="signed")
            self.assertEqual("PASS", result["status"])
            self.assertEqual(1, result["apk_count"])
            self.assertEqual(1, result["apex_count"])
            self.assertEqual("platform",
                             result["apk_roles"][0]["certificate_role"])
            self.assertEqual("avb", result["avb_roles"][0]["key_role"])

    def test_unlisted_role_or_presigned_package_fails_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path, presigned=True)
            result = api.inspect_target_files(
                path, self.config, "generic-x86_64-qualification",
                stage="signed")
            self.assertEqual("FAIL", result["status"])
            self.assertIn("target-files contains an unlisted presigned package",
                          result["errors"])
            self.assertEqual(["Settings.apk"], result["presigned_packages"])

            self.target_files(path, signed=False)
            result = api.inspect_target_files(
                path, self.config, "generic-x86_64-qualification",
                stage="signed")
            self.assertEqual("FAIL", result["status"])
            self.assertIn("target-files contains an unlisted signing role",
                          result["errors"])

    def test_unsigned_input_records_development_roles_without_approving_them(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path, signed=False)
            result = api.inspect_target_files(
                path, self.config, "generic-x86_64-qualification",
                stage="unsigned")
            self.assertEqual("PASS", result["status"])
            self.assertEqual("testkey",
                             result["apk_roles"][0]["certificate_role"])

    def test_unsafe_duplicate_or_missing_zip_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.zip"
            self.target_files(path, duplicate=True)
            with self.assertRaisesRegex(api.SigningError, "unsafe member"):
                api.inspect_target_files(
                    path, self.config, "generic-x86_64-qualification",
                    stage="signed")
            missing = Path(temp) / "missing.zip"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("META/apkcerts.txt", "")
            with self.assertRaisesRegex(api.SigningError, "lacks required"):
                api.inspect_target_files(
                    missing, self.config, "generic-x86_64-qualification",
                    stage="signed")

    def _make_signed_result(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / "release-manifest.json"
        manifest.write_text('{"dummy":true}\n')
        key = root / "release-record"
        wrong_key = root / "wrong-release-record"
        subprocess.run([
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)
        ], check=True, timeout=20)
        subprocess.run([
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
            str(wrong_key)
        ], check=True, timeout=20)
        subprocess.run([
            "ssh-keygen", "-Y", "sign", "-f", str(key),
            "-n", "diamaneos-dummy-release-record", str(manifest)
        ], check=True, capture_output=True, timeout=20)
        allowed = root / "allowed-signers"
        wrong = root / "wrong-allowed-signers"
        allowed.write_text("dummy-release " + (root / "release-record.pub").read_text())
        wrong.write_text("dummy-release " +
                         (root / "wrong-release-record.pub").read_text())
        artifacts = []
        for identifier, path in (
                ("manifest", manifest),
                ("manifest-signature", root / "release-manifest.json.sig"),
                ("allowed-signers", allowed),
                ("wrong-allowed-signers", wrong)):
            artifacts.append({
                "id": identifier,
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            })
        result = {
            "schema_version": 1,
            "inventory_id": self.config["inventory_id"],
            "source_binding": self.config["source_binding"],
            "profile_id": "generic-x86_64-qualification",
            "run_id": "dummy-fixture",
            "status": "PASS",
            "dummy_keys_only": True,
            "production_material_present": False,
            "key_public_fingerprints": [
                {"key_id": key_id, "sha256": "1" * 64}
                for key_id in sorted(api.EXPECTED_KEYS)
            ],
            "proofs": [
                {"id": proof, "status": "PASS", "evidence_refs": ["manifest"]}
                for proof in sorted(api.EXPECTED_PROOFS)
            ],
            "artifacts": artifacts,
            "release_record_proof": {
                "manifest_path": manifest.name,
                "signature_path": "release-manifest.json.sig",
                "allowed_signers_path": allowed.name,
                "wrong_allowed_signers_path": wrong.name,
                "identity": "dummy-release",
                "namespace": "diamaneos-dummy-release-record",
            },
        }
        result_path = root / "result.json"
        result_path.write_text(json.dumps(result, indent=2) + "\n")
        return result_path

    def test_dummy_result_verifies_valid_signature_and_rejects_wrong_key(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self._make_signed_result(temp)
            self.assertEqual([], api.verify_dummy_result(
                result, temp, self.config))

    def test_dummy_result_rejects_tamper_missing_proof_and_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            result_path = self._make_signed_result(temp)
            result = json.loads(result_path.read_text())
            result["proofs"].pop()
            result_path.write_text(json.dumps(result))
            self.assertIn("dummy result proof set is incomplete",
                          api.verify_dummy_result(result_path, temp, self.config))

            result_path = self._make_signed_result(Path(temp) / "fresh")
            result = json.loads(result_path.read_text())
            result["artifacts"][0]["path"] = "../escape"
            result_path.write_text(json.dumps(result))
            self.assertTrue(api.verify_dummy_result(
                result_path, Path(temp) / "fresh", self.config))

    def test_cli_validation_is_read_only(self):
        path = TOOLS / "config" / "signing-roles.json"
        before = path.read_bytes()
        result = subprocess.run(
            [str(TOOLS / ".venv" / "bin" / "python"),
             str(TOOLS / "bin" / "diamaneos"), "signing", "roles"],
            cwd=TOOLS, capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"status": "VALID"', result.stdout)
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
