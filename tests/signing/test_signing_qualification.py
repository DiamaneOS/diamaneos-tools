"""Explicit role planning and repeated-run input refusal."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from diamaneos_tools import signing_inputs as inputs, signing_native as native, signing_qualification as api


class SigningPlanTests(unittest.TestCase):
    def setUp(self):
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        self.policy['presigned_packages'].append({'metadata_name': 'Kept.apk', 'artifact_basename': 'Kept.apk',
             'parent_apex': None, 'certificate_sha256': ['a' * 64]})
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

    def derived_plan(self, keys):
        roles, absent, by_member = api.derived_roles(Path('/target-files.zip'), self.policy, Path('/source'), None)
        packages = {logical: {'kind': role['kind'], 'container': role['container'], 'payload': role['payload'],
                              'certificate_sha256': keys.fingerprint(role['container'], logical)}
                    for logical, role in by_member.items()}
        return {'roles': roles, 'absent_metadata_packages': absent, 'packages': packages}

    def test_factory_and_verify_derive_the_plan_roles_again(self):
        members = [('apk', 'SYSTEM/app/Default/Default.apk', None), ('apk', 'SYSTEM/app/Platform/Platform.apk', None),
                   ('apex', 'SYSTEM/apex/com.example.apex', None), ('payload', 'SYSTEM/apex/com.example.apex', None)]
        keys = Mock(); keys.fingerprint.side_effect = lambda role, logical: role + ':' + logical
        with patch.object(api, 'package_members', return_value=members), \
             patch.object(api.za, 'metadata', return_value=self.metadata), \
             patch.object(api, 'release_overrides', return_value={}):
            plan = self.derived_plan(keys)
            self.assertEqual(['Kept.apk'], plan['absent_metadata_packages'])
            api.require_derived_roles(plan, Path('/target-files.zip'), self.policy, Path('/source'), None, keys)

            def changed(edit):
                value = copy.deepcopy(plan); edit(value); return value
            for name, edit in (
                    ('a role', lambda p: p['roles']['Default.apk'].update(container='platform')),
                    ('a package', lambda p: p['packages']['SYSTEM/app/Default/Default.apk'].update(
                        container='platform', certificate_sha256='platform:SYSTEM/app/Default/Default.apk')),
                    ('both', lambda p: (p['roles']['Default.apk'].update(container='platform'),
                                        p['packages']['SYSTEM/app/Default/Default.apk'].update(
                                            container='platform', certificate_sha256='platform:SYSTEM/app/Default/Default.apk'))),
                    ('a certificate', lambda p: p['packages']['SYSTEM/app/Default/Default.apk'].update(certificate_sha256='x')),
                    ('a dropped package', lambda p: (p['roles'].pop('Platform.apk'),
                                                     p['packages'].pop('SYSTEM/app/Platform/Platform.apk'))),
                    ('a package called absent', lambda p: (p['roles'].pop('Platform.apk'),
                                                           p['packages'].pop('SYSTEM/app/Platform/Platform.apk'),
                                                           p['absent_metadata_packages'].append('Platform.apk')))):
                with self.subTest(name), self.assertRaisesRegex(inputs.SigningError, 'roles differ'):
                    api.require_derived_roles(changed(edit), Path('/target-files.zip'), self.policy, Path('/source'), None, keys)

    def test_command_checks_every_listed_role_and_every_top_level_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'script').mkdir()
            (root / 'script/generate-release.sh').write_text('--extra_apks Platform.apk="$KEY_DIR/platform"')
            target = root / 'target-files.zip'
            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('META/apkcerts.txt',
                                 'name="Default.apk" certificate="keys/testkey.x509.pem" private_key="keys/testkey.pk8"\n'
                                 'name="Platform.apk" certificate="keys/platform.x509.pem" private_key="keys/platform.pk8"\n')
                archive.writestr('META/apexkeys.txt', '')
                archive.writestr('SYSTEM/app/Default/Default.apk', b'apk')
                archive.writestr('SYSTEM/app/Platform/Platform.apk', b'apk')
            roles = api.role_map(api.za.metadata(target), self.policy, api.release_overrides(root))
            plan = {'roles': roles}
            api.require_listed_roles(plan, target, self.policy, root)
            changed = copy.deepcopy(plan); changed['roles']['Default.apk']['container'] = 'platform'
            with self.assertRaisesRegex(inputs.SigningError, 'roles differ'):
                api.require_listed_roles(changed, target, self.policy, root)
            dropped = copy.deepcopy(plan); dropped['roles'].pop('Platform.apk')
            with self.assertRaisesRegex(inputs.SigningError, 'leaves out a package'):
                api.require_listed_roles(dropped, target, self.policy, root)

    def test_absent_metadata_is_derived_and_does_not_need_signing_roles(self):
        self.metadata['apk']['NotInstalled.apk'] = {'certificate': 'keys/unmapped-test-certificate.x509.pem'}
        roles = api.role_map(self.metadata, self.policy, {}, {'Default.apk', 'Platform.apk', 'com.example.apex'})
        self.assertEqual({'Default.apk', 'Platform.apk', 'com.example.apex'}, set(roles))
        with self.assertRaises(inputs.SigningError):
            api.role_map(self.metadata, self.policy, {}, {'NotInstalled.apk'})

    def test_installed_module_selector_requires_an_explicit_source_override(self):
        # An unknown certificate selector is not implicitly releasekey either.
        for certificate, present in (('keys/unknown.x509.pem', None),
                                     ('module/com.android.wifi.resources.x509.pem', {'Default.apk'})):
            with self.subTest(certificate=certificate):
                self.metadata['apk']['Default.apk']['certificate'] = certificate
                with self.assertRaises(inputs.SigningError):
                    api.role_map(self.metadata, self.policy, {}, present)
        roles = api.role_map(self.metadata, self.policy, {'Default.apk': 'releasekey'}, {'Default.apk'})
        self.assertEqual('releasekey', roles['Default.apk']['container'])

    def test_source_release_override_can_resign_a_presigned_module(self):
        self.metadata['apex']['com.example.apex']['container_certificate'] = 'PRESIGNED'
        roles = api.role_map(self.metadata, self.policy, {'com.example.apex': 'releasekey'})
        self.assertEqual({'kind': 'apex', 'container': 'releasekey', 'payload': 'avb'}, roles['com.example.apex'])

    def test_source_overrides_reject_unknown_or_duplicate_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'script').mkdir(); path = root / 'script/generate-release.sh'
            path.write_text('--extra_apks Test.apk="$KEY_DIR/platform"')
            self.assertEqual({'Test.apk': 'platform'}, api.release_overrides(root))
            for text in ('--extra_apks Test.apk="$KEY_DIR/unknown"',
                         '--extra_apks Test.apk="$KEY_DIR/platform" --extra_apks Test.apk="$KEY_DIR/platform"',
                         '--extra_apks Test.apk="$KEY_DIR/platform" --extra_apex_payload_key X.apex="$KEY_DIR/other.pem"'):
                path.write_text(text)
                with self.assertRaises(inputs.SigningError): api.release_overrides(root)

    def test_signing_arguments_recheck_input_hash_and_refuse_output_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'unsigned.zip'; source.write_bytes(b'input')
            signer = root / 'sign_target_files_apks'; signer.write_bytes(b'fixture native signer')
            plan = {'schema_version': 2, 'provenance': {'target_files_sha256': inputs.sha256_file(source),
                                                      'signer_sha256': inputs.sha256_file(signer),
                                                      'projects': {}, 'source_files': {}},
                    'roles': api.role_map(self.metadata, self.policy, {}), 'chains': {'boot': 3}}
            plan['plan_sha256'] = inputs.canonical_sha256(plan)
            kwargs = {'signer': signer, 'key_dir': '/keys', 'source': source, 'destination': root / 'signed.zip',
                      'source_root': root}
            with patch.object(api, 'source_files', return_value={}):
                command = api.signing_command(plan, **kwargs)
            self.assertIn('Platform.apk=/keys/platform', command)
            self.assertIn('Kept.apk=', command)
            self.assertIn('--avb_boot_algorithm', command)
            with patch.object(api, 'source_files', return_value={'changed': 'a' * 64}), self.assertRaises(inputs.SigningError):
                api.signing_command(plan, **kwargs)
            source.write_bytes(b'other')
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)
            source.write_bytes(b'input'); kwargs['destination'].touch()
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)
            kwargs['destination'].unlink(); plan['roles']['Default.apk']['container'] = 'platform'
            with self.assertRaises(inputs.SigningError): api.signing_command(plan, **kwargs)


# GrapheneOS ships TalkBack presigned with its app signing key and its release script keeps it.
TALKBACK = '798c9bf8f7f5498dbe1d7ffcf2f1d0fd548cff0674021ea1a415a9596f55c28c'


class TalkBackPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        inputs.validate_config(self.policy)
        self.keys = native.PublicKeys.__new__(native.PublicKeys)
        self.keys.policy = self.policy

    def test_one_exact_rule_outside_apex(self):
        rules = [r for r in self.policy['presigned_packages'] if 'talkback' in r['metadata_name'].lower()]
        self.assertEqual([{'metadata_name': 'talkback.apk', 'artifact_basename': 'talkback.apk',
                           'parent_apex': None, 'certificate_sha256': [TALKBACK]}], rules)
        self.assertEqual(TALKBACK, self.keys.fingerprint('PRESIGNED', 'PRODUCT/app/talkback/talkback.apk'))
        for logical in ('SYSTEM/apex/com.android.example.apex!/app/talkback.apk', 'PRODUCT/app/talkback/TalkBack.apk'):
            with self.subTest(logical=logical), self.assertRaises(inputs.SigningError):
                self.keys.fingerprint('PRESIGNED', logical)

    def test_kept_presigned_only_under_its_exact_name(self):
        roles = api.role_map({'apk': {'talkback.apk': {'certificate': 'PRESIGNED'}}, 'apex': {}}, self.policy, {})
        self.assertEqual('PRESIGNED', roles['talkback.apk']['container'])
        with self.assertRaisesRegex(inputs.SigningError, 'outside the reviewed policy'):
            api.role_map({'apk': {'TalkBack.apk': {'certificate': 'PRESIGNED'}}, 'apex': {}}, self.policy, {})

    def test_another_certificate_is_refused(self):
        pin = self.keys.fingerprint('PRESIGNED', 'PRODUCT/app/talkback/talkback.apk')
        output = 'Number of signers: 1\nV3.0 Signer: certificate SHA-256 digest: {}\n'
        self.assertEqual([TALKBACK], native.apk_certificate_digests(output.format(TALKBACK), pin))
        with self.assertRaises(inputs.SigningError):
            native.apk_certificate_digests(output.format('d' * 64), pin)


if __name__ == '__main__':
    unittest.main()
