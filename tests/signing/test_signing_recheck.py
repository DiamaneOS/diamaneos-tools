"""Retained-input replay does not import prior results or regenerate artifacts."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RecheckTest(unittest.TestCase):
    def test_input_hashes_public_keys_and_new_output_are_bound(self):
        api = runpy.run_path(str(ROOT / "deploy/builder/recheck-dummy-signing"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "artifacts").mkdir()
            (source / "public-keys").mkdir()
            key = root / "disposable.pem"
            cert = source / "public-keys/releasekey.x509.pem"
            subprocess.run(["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-subj", "/CN=fixture", "-days", "1", "-keyout", str(key), "-out", str(cert)],
                           check=True, capture_output=True, timeout=30)
            key.unlink()
            for name in ("wrong-releasekey.x509.pem", "avb.pub.pem", "wrong-avb.pub.pem",
                         "avb_pkmd.bin", "factory.pub", "wrong-factory.pub"):
                (source / "public-keys" / name).write_text("synthetic public fixture\n")
            for name in ("release-manifest.json.sig", "artifacts/generic-images.zip.sig",
                         "sdk-signed-inventory.json", "ota-signed-inventory.json", "factory-allowed-signers",
                         "factory-wrong-allowed-signers", "release-allowed-signers", "release-wrong-allowed-signers"):
                (source / name).write_text("synthetic metadata\n")
            artifact = source / "artifacts/generic-images.zip"
            artifact.write_bytes(b"retained artifact fixture")
            helper = runpy.run_path(str(ROOT / "deploy/builder/run-dummy-signing-qualification"))
            manifest = {"source_binding": {}, "profile_ids": ["sdk", "ota"],
                        "artifacts": [{"path": "artifacts/generic-images.zip", "bytes": artifact.stat().st_size,
                                       "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
                        "key_public_fingerprints": [
                            {"key_id": "releasekey", "sha256": helper["certificate_fingerprint"](cert)},
                            {"key_id": "factory", "sha256": hashlib.sha256((source / "public-keys/factory.pub").read_bytes()).hexdigest()}]}
            (source / "release-manifest.json").write_text(json.dumps(manifest))
            (source / "result.json").write_text("must not copy")
            (source / "private.pk8").write_text("must not copy")
            config = {"source_binding": {}, "dummy_qualification": {"sdk_profile_id": "sdk", "ota_profile_id": "ota"},
                      "artifact_roles": [{"id": "android-apk-certificates", "key_ids": ["releasekey"]}]}
            output = root / "new"
            output.mkdir()
            api["prepare_inputs"](source, output, config)
            self.assertEqual(artifact.stat().st_ino, (output / "artifacts/generic-images.zip").stat().st_ino)
            self.assertFalse((output / "result.json").exists())
            self.assertFalse((output / "private.pk8").exists())
            artifact.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
                api["prepare_inputs"](source, root / "rejected", config)
            self.assertFalse((root / "rejected").exists())

    def test_retained_paths_cannot_escape_or_use_symlinks(self):
        api = runpy.run_path(str(ROOT / "deploy/builder/recheck-dummy-signing"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "file").touch()
            (root / "link").symlink_to(root / "file")
            for relative in ("../file", "/file", "link"):
                with self.assertRaises(ValueError):
                    api["retained_file"](root, relative)
