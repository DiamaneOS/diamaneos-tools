"""Tests for the bounded disposable-signing command planner."""

from pathlib import Path
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "src"))
from diamaneos_tools import signing_qualification as api


class SigningQualificationPlanTest(unittest.TestCase):
    @staticmethod
    def inventory():
        apk_roles = [
            {"name": "Release.apk", "certificate_role": "testkey"},
            {"name": "Platform.apk", "certificate_role": "platform"},
            {"name": "Shared.apk", "certificate_role": "shared"},
            {"name": "Media.apk", "certificate_role": "media"},
            {"name": "Network.apk", "certificate_role": "networkstack"},
            {"name": "Bluetooth.apk",
             "certificate_role": "com.android.bluetooth"},
            {"name": "Sandbox.apk", "certificate_role": "sdk_sandbox"},
            {"name": "Compat.apk", "certificate_role": "gmscompat_lib"},
            {"name": "Nfc.apk", "certificate_role": "nfc"},
            {"name": "TestOnly.apk", "certificate_role": "cts-testkey"},
            {"name": "Reviewed.apk", "certificate_role": "PRESIGNED"},
        ]
        return {
            "status": "PASS",
            "stage": "unsigned",
            "apk_roles": apk_roles,
            "apex_roles": [
                {"name": "com.example.runtime.apex",
                 "container_certificate_role": "com.example.runtime"},
                {"name": "com.example.shim.apex",
                 "container_certificate_role": "PRESIGNED"},
            ],
            "avb_roles": [
                {"chain": "boot"}, {"chain": "system"},
                {"chain": "vbmeta"},
            ],
        }

    def test_role_map_is_exact_and_preserves_reviewed_presigned_entries(self):
        mapping = api.explicit_role_map(self.inventory())
        self.assertEqual("releasekey", mapping["Release.apk"]["container"])
        self.assertEqual("releasekey", mapping["TestOnly.apk"]["container"])
        self.assertEqual("bluetooth", mapping["Bluetooth.apk"]["container"])
        self.assertEqual("PRESIGNED", mapping["Reviewed.apk"]["container"])
        self.assertEqual("releasekey",
                         mapping["com.example.runtime.apex"]["container"])
        self.assertEqual("avb",
                         mapping["com.example.runtime.apex"]["payload"])
        self.assertEqual("PRESIGNED",
                         mapping["com.example.shim.apex"]["payload"])

    def test_command_enumerates_roles_without_global_override(self):
        command = api.signing_command(
            self.inventory(), signer="sign_target_files_apks",
            key_dir="keys", source="unsigned.zip", destination="signed.zip")
        joined = "\n".join(command)
        self.assertNotIn("--override_apk_keys", command)
        self.assertNotIn("--override_apex_keys", command)
        self.assertIn("Bluetooth.apk=keys/bluetooth", joined)
        self.assertIn("Reviewed.apk,com.example.shim.apex=", joined)
        self.assertIn("com.example.runtime.apex=keys/avb.pem", joined)
        self.assertIn("--avb_boot_key", command)
        self.assertIn("--avb_system_key", command)
        self.assertIn("--avb_vbmeta_key", command)
        self.assertEqual(["unsigned.zip", "signed.zip"], command[-2:])

    def test_unaccepted_inventory_and_unsafe_names_fail_closed(self):
        changed = self.inventory()
        changed["status"] = "FAIL"
        with self.assertRaises(api.QualificationPlanError):
            api.explicit_role_map(changed)
        changed = self.inventory()
        changed["apk_roles"][0]["name"] = "../escape.apk"
        with self.assertRaises(api.QualificationPlanError):
            api.explicit_role_map(changed)

    def test_unsupported_avb_chain_fails_closed(self):
        changed = self.inventory()
        changed["avb_roles"].append({"chain": "future_partition"})
        with self.assertRaises(api.QualificationPlanError):
            api.signing_command(
                changed, signer="sign_target_files_apks", key_dir="keys",
                source="unsigned.zip", destination="signed.zip")

    def test_reviewed_dlkm_vbmeta_chains_require_prepared_metadata(self):
        changed = self.inventory()
        changed["avb_roles"].extend([
            {"chain": "vbmeta_system_dlkm"},
            {"chain": "vbmeta_vendor_dlkm"},
        ])
        with self.assertRaises(api.QualificationPlanError):
            api.signing_command(
                changed, signer="sign_target_files_apks", key_dir="keys",
                source="unsigned.zip", destination="signed.zip")
        command = api.signing_command(
            changed, signer="sign_target_files_apks", key_dir="keys",
            source="unsigned.zip", destination="signed.zip",
            prepared_custom_vbmeta_chains=(
                "vbmeta_system_dlkm", "vbmeta_vendor_dlkm"))
        self.assertNotIn("--avb_vbmeta_system_dlkm_key", command)
        self.assertNotIn("--avb_vbmeta_vendor_dlkm_key", command)
        self.assertNotIn("--avb_extra_custom_image_key", command)
        self.assertNotIn("--avb_extra_custom_image_algorithm", command)

        with self.assertRaises(api.QualificationPlanError):
            api.signing_command(
                self.inventory(), signer="sign_target_files_apks",
                key_dir="keys", source="unsigned.zip",
                destination="signed.zip",
                prepared_custom_vbmeta_chains=("vbmeta_system_dlkm",))


if __name__ == "__main__":
    unittest.main()
