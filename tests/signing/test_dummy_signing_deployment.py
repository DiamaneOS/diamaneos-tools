"""Static deployment contract for disposable signing qualification."""

import json
from pathlib import Path
import runpy
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "deploy/builder/run-dummy-signing-qualification"
SERVICE = ROOT / "deploy/builder/diamaneos-builder-dummy-signing.service"


class DummySigningDeploymentTest(unittest.TestCase):
    def test_runner_compiles_and_is_no_argument_revision_bound(self):
        script = RUNNER.read_text(encoding="utf-8")
        compile(script, str(RUNNER), "exec")
        self.assertIn("DIAMANEOS_EXPECTED_TOOLS_COMMIT", script)
        self.assertIn("this qualification entry point accepts no arguments", script)
        self.assertIn("qualified_unsigned_target_files_sha256", script)
        self.assertIn("qualified_otatools_sha256", script)
        self.assertIn('qualification = config["dummy_qualification"]', script)
        self.assertIn('sdk_profile_id = qualification["sdk_profile_id"]', script)
        self.assertIn('ota_profile_id = qualification["ota_profile_id"]', script)
        self.assertIn("def unique_regular_artifact", script)
        self.assertIn('"sdk-signed-target-files.zip"', script)
        self.assertIn('"ota-signed-target-files.zip"', script)
        self.assertIn("source=ota_prepared, destination=ota_signed", script)
        self.assertIn(
            'key_dir / "releasekey", ota_signed, full_ota', script)
        self.assertIn(
            '"-i", ota_signed, ota_signed,', script)
        self.assertIn(
            'for record in ota_signed_inventory["avb_roles"]', script)
        self.assertIn('f"ota-avb-{record[\'chain\']}"', script)
        self.assertIn("signing_command", script)
        self.assertIn("def make_android_key", script)
        self.assertIn("allowed=(0, 1)", script)
        self.assertIn('"openssl", "x509"', script)
        self.assertIn('"openssl", "pkcs8"', script)
        self.assertIn('partial / "key-generation.json"', script)
        self.assertIn('"--source-inventory"', script)
        self.assertIn('record.get("expected_certificate_role")', script)
        self.assertIn("target_apk_role_coverage", script)
        self.assertIn('qualification["metadata_only_apk_key_ids"]', script)
        self.assertIn('"standalone-apk-signing-probe"', script)
        self.assertIn('required_tools["apksigner"], "sign"', script)
        self.assertIn(
            'logs / f"verify-apk-{role}.log", env=qualification_env', script)
        self.assertIn(
            'logs / "verify-apex-container.log", env=qualification_env', script)
        self.assertIn('prebuilts/jdk/jdk21/linux-x86/bin', script)
        self.assertIn('environment["upstream"]["release_tag"]', script)
        self.assertIn('environment["host"]["external_tools"]["node"]["version"]', script)
        self.assertIn("explicit thermal-safety preflight path is missing", script)
        self.assertNotIn("/usr/local/sbin/diamaneos-builder-fan-check", script)
        self.assertNotIn("grapheneos-allowed-signers-2026091000", script)
        self.assertNotIn("/opt/nodejs/v24.21.0/bin", script)
        planner = (ROOT / "src/diamaneos_tools/signing_qualification.py").read_text(
            encoding="utf-8")
        self.assertNotIn("--override_apk_keys", planner)
        self.assertNotIn("--override_apex_keys", planner)

    def test_runner_covers_signing_and_wrong_key_verifiers(self):
        script = RUNNER.read_text(encoding="utf-8")
        for token in (
                "sign_target_files_apks", "ota_from_target_files",
                "img_from_target_files", "apksigner", "avbtool",
                "check_ota_package_signature", "ssh-keygen", "wrong_exit_code",
                "restart-recovery"):
            self.assertIn(token, script)
        self.assertIn("shutil.rmtree(private_root)", script)
        self.assertIn(
            '"private_key_directory_present_during_verification": False',
            script)

    def test_make_key_exit_one_requires_parseable_artifacts(self):
        runner = runpy.run_path(str(RUNNER))
        make_android_key = runner["make_android_key"]
        failure = runner["Failure"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "keys"
            logs = root / "logs"
            destination.mkdir()
            logs.mkdir()
            helper = root / "make-key-fixture"
            helper.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "openssl req -x509 -newkey rsa:2048 -nodes "
                "-keyout \"$1.key.pem\" -out \"$1.x509.pem\" "
                "-days 1 -subj \"$2\" >/dev/null 2>&1\n"
                "openssl pkcs8 -topk8 -inform PEM -outform DER "
                "-in \"$1.key.pem\" -nocrypt -out \"$1.pk8\"\n"
                "rm -- \"$1.key.pem\"\n"
                "exit 1\n",
                encoding="utf-8")
            helper.chmod(0o700)
            record = make_android_key(
                helper, "fixture", "fixture", "/CN=fixture/",
                destination, logs)
            self.assertEqual(1, record["make_key_exit_code"])
            self.assertEqual("PASS", record["certificate_parse"])
            self.assertEqual("PASS", record["private_key_parse"])

            malformed = root / "make-key-malformed"
            malformed.write_text(
                "#!/bin/sh\n"
                "printf bad >\"$1.x509.pem\"\n"
                "printf bad >\"$1.pk8\"\n"
                "exit 1\n",
                encoding="utf-8")
            malformed.chmod(0o700)
            with self.assertRaises(failure):
                make_android_key(
                    malformed, "bad", "bad", "/CN=bad/",
                    destination, logs)

    def test_ota_preparation_binds_only_reviewed_custom_vbmeta_keys(self):
        runner = runpy.run_path(str(RUNNER))
        prepare = runner["prepare_ota_custom_vbmeta_input"]
        failure = runner["Failure"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_dir = root / "keys"
            key_dir.mkdir()
            (key_dir / "avb.pem").write_text("disposable test key")
            source = root / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "META/misc_info.txt",
                    "avb_enable=true\n"
                    "avb_custom_vbmeta_images_partition_list="
                    "system_dlkm vendor_dlkm\n"
                    "avb_vbmeta_system_dlkm=system_dlkm\n"
                    "avb_vbmeta_system_dlkm_key_path=old/system.pem\n"
                    "avb_vbmeta_system_dlkm_algorithm=SHA256_RSA4096\n"
                    "avb_vbmeta_vendor_dlkm=vendor_dlkm\n"
                    "avb_vbmeta_vendor_dlkm_key_path=old/vendor.pem\n"
                    "avb_vbmeta_vendor_dlkm_algorithm=SHA256_RSA4096\n")
                archive.writestr("SYSTEM/payload", b"unchanged")
            destination = root / "prepared.zip"
            evidence = root / "evidence.json"
            prepare(source, destination, root / "work", evidence,
                    root / "prepare.log", key_dir)
            with zipfile.ZipFile(destination) as archive:
                misc = archive.read("META/misc_info.txt").decode()
                self.assertEqual(b"unchanged",
                                 archive.read("SYSTEM/payload"))
            self.assertIn(
                f"avb_vbmeta_system_dlkm_key_path={key_dir}/avb.pem\n",
                misc)
            self.assertIn(
                f"avb_vbmeta_vendor_dlkm_key_path={key_dir}/avb.pem\n",
                misc)
            self.assertNotIn("avb_custom_images_partition_list=", misc)
            record = json.loads(evidence.read_text())
            self.assertEqual("PASS", record["status"])
            self.assertEqual(
                ["system_dlkm", "vendor_dlkm"],
                record["custom_vbmeta_partitions"])
            self.assertFalse(record["other_members_changed"])

            rejected = root / "rejected.zip"
            with zipfile.ZipFile(rejected, "w") as archive:
                archive.writestr(
                    "META/misc_info.txt",
                    "avb_custom_vbmeta_images_partition_list=unreviewed\n")
            with self.assertRaises(failure):
                prepare(rejected, root / "unused.zip", root / "reject-work",
                        root / "unused.json", root / "reject.log", key_dir)

    def test_apk_role_coverage_separates_metadata_from_payloads(self):
        runner = runpy.run_path(str(RUNNER))
        coverage = runner["target_apk_role_coverage"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / "sdk.zip"
            ota = root / "ota.zip"
            with zipfile.ZipFile(sdk, "w") as archive:
                archive.writestr("SYSTEM/app/Release/Release.apk", b"release")
                archive.writestr("SYSTEM/app/Gms/Gms.apk", b"gms")
            with zipfile.ZipFile(ota, "w") as archive:
                archive.writestr("SYSTEM/app/Shared/Shared.apk", b"shared")
            sources = (
                ("sdk", sdk, {"apk_roles": [
                    {"name": "Release.apk",
                     "expected_certificate_role": "releasekey"},
                    {"name": "Gms.apk",
                     "expected_certificate_role": "gmscompat_lib"},
                    {"name": "Bluetooth.apk",
                     "expected_certificate_role": "bluetooth"},
                ]}),
                ("ota", ota, {"apk_roles": [
                    {"name": "Shared.apk",
                     "expected_certificate_role": "shared"},
                    {"name": "Nfc.apk",
                     "expected_certificate_role": "nfc"},
                ]}),
            )
            key_ids = ("bluetooth", "gmscompat_lib", "nfc", "releasekey",
                       "shared")
            counts, selected, candidates = coverage(sources, key_ids)
            self.assertEqual(1, counts["bluetooth"]["sdk"])
            self.assertEqual(1, counts["nfc"]["ota"])
            self.assertNotIn("bluetooth", selected)
            self.assertNotIn("nfc", selected)
            self.assertEqual("Gms.apk", selected["gmscompat_lib"]["package_name"])
            self.assertEqual("ota", selected["shared"]["profile_id"])
            self.assertEqual(1, len(candidates["releasekey"]))

    def test_service_is_unprivileged_offline_and_nonpersistent(self):
        unit = SERVICE.read_text(encoding="utf-8")
        self.assertIn("User=diamaneos-build", unit)
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("PrivateDevices=yes", unit)
        self.assertIn("ProtectSystem=full", unit)
        self.assertIn("TimeoutStartSec=infinity", unit)
        self.assertNotIn("WantedBy=", unit)
        self.assertNotIn("sudo", unit)
        self.assertNotIn("/opt/nodejs/v24.21.0/bin", unit)


if __name__ == "__main__":
    unittest.main()
