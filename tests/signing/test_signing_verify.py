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
import warnings
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

    def unqualified_config(self):
        config = copy.deepcopy(self.config)
        profile = next(entry for entry in config["target_profiles"]
                       if entry["id"] == "generic-x86_64-qualification")
        profile["qualified_unsigned_target_files_sha256"] = None
        profile["qualified_otatools_sha256"] = None
        profile["presigned_allowlist"] = []
        profile["presigned_metadata_only"] = []
        profile["presigned_artifacts"] = []
        profile["qualification_evidence"] = None
        profile["inventory_status"] = "pending-target-files-qualification"
        return config

    def test_committed_role_contract_is_complete_and_valid(self):
        self.assertEqual([], api.validate_config(self.config, self.environment))
        self.assertEqual(api.EXPECTED_KEYS,
                         {entry["id"] for entry in self.config["key_roles"]})
        self.assertEqual(api.EXPECTED_ARTIFACT_ROLES,
                         {entry["id"] for entry in self.config["artifact_roles"]})
        self.assertEqual(api.EXPECTED_PROOFS,
                         set(self.config["required_dummy_proofs"]))
        self.assertEqual(
            ["bluetooth", "nfc", "sdk_sandbox"],
            self.config["dummy_qualification"][
                "metadata_only_apk_key_ids"],
        )

    def test_committed_ota_profile_is_bound_to_reviewed_inputs(self):
        profile = next(
            entry for entry in self.config["target_profiles"]
            if entry["id"] == "generic-x86_64-ota-qualification")
        self.assertEqual("qualified", profile["inventory_status"])
        self.assertEqual(
            "40adc07f630f4659389abf046e1daf7ca20c0b6439b008e4e2027ad0de51bebb",
            profile["qualified_unsigned_target_files_sha256"],
        )
        self.assertEqual(
            "3f93de1eb56f36affc36d8fca726a4a9605d3d277a4e87d88928291fc68a8f6a",
            profile["qualified_otatools_sha256"],
        )
        self.assertEqual(37, len(profile["presigned_allowlist"]))
        self.assertEqual(13, len(profile["presigned_artifacts"]))
        self.assertEqual(24, len(profile["presigned_metadata_only"]))
        self.assertIn("GmsCompatConfig.apk",
                      profile["presigned_metadata_only"])
        self.assertNotIn(
            "GmsCompatConfig.apk",
            {entry["metadata_name"]
             for entry in profile["presigned_artifacts"]},
        )
        self.assertEqual({
            "artifact_review_sha256":
                "1950d003e7b5c9b74a13181de543c3e92f0cd1c5ec9b720c5f908064a26c6764",
            "source_review_sha256":
                "0c6bbd45e45ffc56ea65dc8d308391b5a47cb9c2a56194279811619f5b28eb46",
            "source_inventory_sha256":
                "cc39184a313d558601f1b29751b1c306e324f18e4892124a71077e2e52a63125",
        }, profile["qualification_evidence"])

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
        changed = copy.deepcopy(self.config)
        changed["dummy_qualification"][
            "metadata_only_apk_key_ids"] = ["avb"]
        self.assertIn(
            "metadata-only APK policy refers to a non-APK key role",
            api.validate_config(changed, self.environment),
        )
        changed = copy.deepcopy(self.config)
        changed["dummy_qualification"]["sdk_profile_id"] = "fp6-release"
        self.assertIn(
            "dummy qualification profile roles are not reviewed",
            api.validate_config(changed, self.environment),
        )

    def test_generic_build_target_drift_fails_closed(self):
        changed = copy.deepcopy(self.config)
        profile = next(entry for entry in changed["target_profiles"]
                       if entry["id"] == "generic-x86_64-qualification")
        profile["build_target"] = "different-cur-userdebug"
        self.assertIn(
            "generic signing target does not match build environment",
            api.validate_config(changed, self.environment),
        )
        changed = copy.deepcopy(self.config)
        profile = next(entry for entry in changed["target_profiles"]
                       if entry["id"] == "generic-x86_64-ota-qualification")
        profile["build_target"] = "different-cur-userdebug"
        self.assertIn(
            "generic OTA signing target is not the reviewed product",
            api.validate_config(changed, self.environment),
        )

    def test_safety_boundary_cannot_authorize_production_operations(self):
        changed = copy.deepcopy(self.config)
        changed["safety"]["production_key_generation_allowed"] = True
        self.assertTrue(api.validate_config(changed, self.environment))

    @staticmethod
    def target_files(path, *, presigned=False, signed=True, avb_signed=None,
                     duplicate=False, settings_artifact=None):
        if avb_signed is None:
            avb_signed = signed
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
        avb_key = "avb" if avb_signed else "testkey"
        avb_algorithm = "SHA256_RSA4096" if avb_signed else "SHA256_RSA2048"
        misc = (
            f"avb_vbmeta_key_path=keys/{avb_key}.pem\n"
            f"avb_vbmeta_algorithm={avb_algorithm}\n"
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META/apkcerts.txt", apk)
            archive.writestr("META/apexkeys.txt", apex)
            archive.writestr("META/misc_info.txt", misc)
            if settings_artifact is not None:
                archive.writestr("SYSTEM/app/Settings/Settings.apk",
                                 settings_artifact)
            if duplicate:
                # The duplicate is intentional: this fixture verifies that the
                # parser rejects ambiguous ZIP member names. Suppress only the
                # warning emitted while constructing that invalid fixture.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("META/apkcerts.txt", apk)

    def test_signed_inventory_uses_accepted_source_labels_and_explicit_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "unsigned-target-files.zip"
            self.target_files(source_path, signed=False)
            source = api.inspect_target_files(
                source_path, self.unqualified_config(),
                "generic-x86_64-qualification", stage="unsigned")
            path = Path(temp) / "signed-target-files.zip"
            self.target_files(path, signed=False, avb_signed=True)
            result = api.inspect_target_files(
                path, self.unqualified_config(),
                "generic-x86_64-qualification", stage="signed",
                source_inventory=source)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(1, result["apk_count"])
            self.assertEqual(1, result["apex_count"])
            self.assertEqual("testkey",
                             result["apk_roles"][0]["certificate_role"])
            self.assertEqual("releasekey",
                             result["apk_roles"][0]["expected_certificate_role"])
            self.assertEqual("releasekey", result["apex_roles"][0][
                "expected_container_certificate_role"])
            self.assertEqual("avb", result["apex_roles"][0][
                "expected_payload_private_key_role"])
            self.assertEqual("avb", result["avb_roles"][0]["key_role"])
            self.assertEqual(source["inventory_sha256"],
                             result["source_inventory_sha256"])

    def test_signed_inventory_requires_source_and_rejects_metadata_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "unsigned-target-files.zip"
            self.target_files(source_path, signed=False)
            source = api.inspect_target_files(
                source_path, self.unqualified_config(),
                "generic-x86_64-qualification", stage="unsigned")
            path = Path(temp) / "signed-target-files.zip"
            self.target_files(path)
            with self.assertRaisesRegex(
                    api.SigningError, "requires a source inventory"):
                api.inspect_target_files(
                    path, self.unqualified_config(),
                    "generic-x86_64-qualification", stage="signed")
            result = api.inspect_target_files(
                path, self.unqualified_config(),
                "generic-x86_64-qualification", stage="signed",
                source_inventory=source)
            self.assertEqual("FAIL", result["status"])
            self.assertIn(
                "signed target-files metadata differs from accepted unsigned input",
                result["errors"])

            tampered = copy.deepcopy(source)
            tampered["apk_roles"][0]["certificate_role"] = "platform"
            with self.assertRaisesRegex(
                    api.SigningError, "source inventory self-hash mismatch"):
                api.inspect_target_files(
                    path, self.unqualified_config(),
                    "generic-x86_64-qualification", stage="signed",
                    source_inventory=tampered)

    def test_unlisted_presigned_package_fails_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path, presigned=True)
            result = api.inspect_target_files(
                path, self.unqualified_config(),
                "generic-x86_64-qualification", stage="unsigned")
            self.assertEqual("FAIL", result["status"])
            self.assertIn("target-files contains an unlisted presigned package",
                          result["errors"])
            self.assertEqual(["Settings.apk"], result["presigned_packages"])

    def test_unsigned_input_records_development_roles_without_approving_them(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path, signed=False)
            result = api.inspect_target_files(
                path, self.unqualified_config(),
                "generic-x86_64-qualification",
                stage="unsigned")
            self.assertEqual("PASS", result["status"])
            self.assertEqual("testkey",
                             result["apk_roles"][0]["certificate_role"])

    def test_qualified_presigned_policy_binds_presence_hash_and_input(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            payload = b"reviewed-presigned-fixture"
            self.target_files(path, presigned=True,
                              settings_artifact=payload)
            config = self.unqualified_config()
            profile = config["target_profiles"][0]
            profile["qualified_unsigned_target_files_sha256"] = \
                api.sha256_file(path)
            profile["presigned_allowlist"] = ["Settings.apk"]
            profile["presigned_artifacts"] = [{
                "metadata_name": "Settings.apk",
                "artifact_basename": "Settings.apk",
                "path": "SYSTEM/app/Settings/Settings.apk",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }]
            profile["qualification_evidence"] = {
                "artifact_review_sha256": "1" * 64,
                "source_review_sha256": "2" * 64,
                "source_inventory_sha256": "3" * 64,
            }
            profile["inventory_status"] = "qualified"
            result = api.inspect_target_files(
                path, config, "generic-x86_64-qualification",
                stage="unsigned")
            self.assertEqual("PASS", result["status"])

            profile["presigned_artifacts"][0]["sha256"] = "4" * 64
            result = api.inspect_target_files(
                path, config, "generic-x86_64-qualification",
                stage="unsigned")
            self.assertIn("target-files presigned artifact hash mismatch",
                          result["errors"])

    def test_qualified_metadata_only_policy_requires_archive_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target-files.zip"
            self.target_files(path, presigned=True)
            config = self.unqualified_config()
            profile = config["target_profiles"][0]
            profile["qualified_unsigned_target_files_sha256"] = \
                api.sha256_file(path)
            profile["presigned_allowlist"] = ["Settings.apk"]
            profile["presigned_metadata_only"] = ["Settings.apk"]
            profile["qualification_evidence"] = {
                "artifact_review_sha256": "1" * 64,
                "source_review_sha256": "2" * 64,
                "source_inventory_sha256": "3" * 64,
            }
            profile["inventory_status"] = "qualified"
            result = api.inspect_target_files(
                path, config, "generic-x86_64-qualification",
                stage="unsigned")
            self.assertEqual("PASS", result["status"])

            self.target_files(path, presigned=True,
                              settings_artifact=b"unexpected")
            profile["qualified_unsigned_target_files_sha256"] = \
                api.sha256_file(path)
            result = api.inspect_target_files(
                path, config, "generic-x86_64-qualification",
                stage="unsigned")
            self.assertIn(
                "metadata-only presigned package is present in archive",
                result["errors"])

    def test_misc_info_accepts_identical_and_rejects_conflicting_duplicates(self):
        fields, duplicates = api._misc_info(
            "build_non_sparse_super_partition=true\n"
            "avb_vbmeta_algorithm=SHA256_RSA4096\n"
            "build_non_sparse_super_partition=true\n"
        )
        self.assertEqual("true", fields["build_non_sparse_super_partition"])
        self.assertEqual(["build_non_sparse_super_partition"], duplicates)
        with self.assertRaisesRegex(api.SigningError,
                                    "conflicting duplicate field"):
            api._misc_info(
                "build_non_sparse_super_partition=true\n"
                "build_non_sparse_super_partition=false\n"
            )

    def test_unsafe_duplicate_or_missing_zip_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.zip"
            self.target_files(path, duplicate=True)
            with self.assertRaisesRegex(api.SigningError, "unsafe member"):
                api.inspect_target_files(
                    path, self.unqualified_config(),
                    "generic-x86_64-qualification",
                    stage="unsigned")
            missing = Path(temp) / "missing.zip"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("META/apkcerts.txt", "")
            with self.assertRaisesRegex(api.SigningError, "lacks required"):
                api.inspect_target_files(
                    missing, self.unqualified_config(),
                    "generic-x86_64-qualification",
                    stage="unsigned")

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
            "profile_ids": [
                "generic-x86_64-qualification",
                "generic-x86_64-ota-qualification",
            ],
            "run_id": "dummy-signing-20260102T030405Z",
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
            "factory_archive_proof": {
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
            [sys.executable,
             str(TOOLS / "bin" / "diamaneos"), "signing", "roles"],
            cwd=TOOLS, capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"status": "VALID"', result.stdout)
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
