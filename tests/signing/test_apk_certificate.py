"""Certificate pinning against actual apksigner output shapes."""
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from diamaneos_tools.signing_verify import apk_certificate_digests, SigningError


class ApkCertificateTest(unittest.TestCase):
    def output(self, label="V3.0 Signer:", digest="a" * 64):
        return ("Verifies\nVerified using v3 scheme (APK Signature Scheme v3): true\n"
                "Number of signers: 1\n" + label + " certificate DN: O=fixture\n"
                + label + " certificate SHA-256 digest: " + digest + "\n"
                + label + " public key SHA-256 digest: " + "b" * 64 + "\n")

    def test_scheme_and_legacy_certificate_labels(self):
        for label in ("Signer #1", "V1 Signer:", "V2 Signer:", "V3.0 Signer:", "V3.1 Signer:", "V3.2 Signer:"):
            with self.subTest(label=label):
                self.assertEqual(["a" * 64], apk_certificate_digests(self.output(label), "a" * 64))

    def test_wrong_key_missing_malformed_and_extra_signer_are_rejected(self):
        variants = [self.output(digest="b" * 64), self.output(digest="abc"),
                    self.output().replace("Number of signers: 1", "Number of signers: 2"),
                    self.output().replace("Number of signers: 1\n", ""),
                    self.output().replace("certificate SHA-256 digest:", "unsupported digest:"),
                    self.output() + "Signer #2 certificate SHA-256 digest: " + "b" * 64 + "\n",
                    self.output("Unknown Signer:")]
        for output in variants:
            with self.subTest(output=output):
                with self.assertRaises(SigningError):
                    apk_certificate_digests(output, "a" * 64)

    def test_matching_public_key_digest_does_not_replace_certificate_pin(self):
        with self.assertRaises(SigningError):
            apk_certificate_digests(self.output(), "b" * 64)

    def test_real_runner_requires_tool_success_before_accepting_certificate(self):
        runner = runpy.run_path(str(ROOT / "deploy/builder/run-dummy-signing-qualification"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = root / "apksigner"
            for code in (0, 7):
                tool.write_text("#!" + sys.executable + "\nprint(" + repr(self.output()) + ")\nraise SystemExit(" + str(code) + ")\n")
                tool.chmod(0o750)
                if code:
                    with self.assertRaises(runner["Failure"]):
                        runner["apk_certificate_check"](tool, root / "fixture.apk", "a" * 64, root / "failed.log", env={})
                else:
                    self.assertEqual(["a" * 64], runner["apk_certificate_check"](
                        tool, root / "fixture.apk", "a" * 64, root / "valid.log", env={}))
