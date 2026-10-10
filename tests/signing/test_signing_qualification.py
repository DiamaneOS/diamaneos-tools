"""Explicit role planning and repeated-run input refusal."""
import copy
from pathlib import Path
import tempfile
import unittest

from diamaneos_tools import signing_inputs as inputs, signing_qualification as api


class SigningPlanTests(unittest.TestCase):
    def setUp(self):
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        self.metadata = {'apk': {'Default.apk': {'certificate': 'keys/testkey.x509.pem'},
                                 'Platform.apk': {'certificate': 'keys/platform.x509.pem'},
                                 'Kept.apk': {'certificate': 'PRESIGNED'}},
                         'apex': {'com.example.apex': {'container_certificate': 'keys/module.x509.pem'}}}

    def test_roles_are_explicit_and_presigned_does_not_get_an_implicit_authority(self):
        roles = api.role_map(self.metadata, self.policy, {})
        self.assertEqual('releasekey', roles['Default.apk']['container'])
        self.assertEqual('platform', roles['Platform.apk']['container'])
        self.assertEqual('PRESIGNED', roles['Kept.apk']['container'])
        self.assertEqual('avb', roles['com.example.apex']['payload'])
        with self.assertRaises(inputs.SigningError): api.member_role(roles, 'apk', 'SYSTEM/app/Unknown.apk')

    def test_unknown_certificate_selector_is_not_implicitly_releasekey(self):
        self.metadata['apk']['Default.apk']['certificate'] = 'keys/unknown.x509.pem'
        with self.assertRaises(inputs.SigningError): api.role_map(self.metadata, self.policy, {})

    def test_source_release_override_can_resign_a_presigned_module(self):
        self.metadata['apex']['com.example.apex']['container_certificate'] = 'PRESIGNED'
        roles = api.role_map(self.metadata, self.policy, {'com.example.apex': 'releasekey'})
        self.assertEqual({'kind': 'apex', 'container': 'releasekey', 'payload': 'avb'}, roles['com.example.apex'])

    def test_source_overrides_reject_unknown_or_duplicate_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'script').mkdir(); path = root / 'script/generate-release.sh'
            path.write_text('--extra_apks Test.apk="$KEY_DIR/platform"')
            self.assertEqual({'Test.apk': 'platform'}, api.release_overrides(root, self.policy))
            for text in ('--extra_apks Test.apk="$KEY_DIR/unknown"',
                         '--extra_apks Test.apk="$KEY_DIR/platform" --extra_apks Test.apk="$KEY_DIR/platform"',
                         '--extra_apks Test.apk="$KEY_DIR/platform" --extra_apex_payload_key X.apex="$KEY_DIR/other.pem"'):
                path.write_text(text)
                with self.assertRaises(inputs.SigningError): api.release_overrides(root, self.policy)

    def test_signing_arguments_recheck_input_hash_and_refuse_output_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'unsigned.zip'; source.write_bytes(b'input')
            signer = root / 'sign_target_files_apks'; signer.write_bytes(b'fixture native signer')
            plan = {'schema_version': 2, 'provenance': {'target_files_sha256': inputs.sha256_file(source),
                                                      'signer_sha256': inputs.sha256_file(signer)},
                    'roles': api.role_map(self.metadata, self.policy, {}), 'chains': {'boot': 3}}
            plan['plan_sha256'] = inputs.canonical_sha256(plan)
            kwargs = {'signer': signer, 'key_dir': '/keys', 'source': source, 'destination': root / 'signed.zip'}
            command = api.signing_command(plan, **kwargs)
            self.assertIn('Platform.apk=/keys/platform', command)
            self.assertIn('Kept.apk=', command)
            self.assertIn('--avb_boot_algorithm', command)
            source.write_bytes(b'other')
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)
            source.write_bytes(b'input'); kwargs['destination'].touch()
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)
            kwargs['destination'].unlink(); plan['roles']['Default.apk']['container'] = 'platform'
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)


if __name__ == '__main__':
    unittest.main()
