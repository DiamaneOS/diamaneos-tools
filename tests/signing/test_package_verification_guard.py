"""Controlled orchestration guards; these tests do not establish cryptographic validity."""
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from diamaneos_tools import signing_archive as za, signing_inputs as inputs
from diamaneos_tools import signing_native as native, signing_verify as api
from .fixtures import (TemporaryInputs, apk_plan, apex_plan, publication_description,
                       publication_plan, release_record, rewrite_zip, write_zip)


class PackageVerificationGuardTests(TemporaryInputs, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.public = self.root / 'external-trust'
        self.public.mkdir()
        self.certificates = {}
        self.fingerprints = {}
        for role in ('platform', 'releasekey', 'shared'):
            path = self.public / (role + '.certificate-fixture')
            path.write_bytes(('controlled external certificate ' + role).encode())
            self.certificates[role] = path
            self.fingerprints[role] = inputs.sha256_file(path)
        self.avb_public = self.public / 'avb-public-fixture'
        self.avb_public.write_bytes(b'controlled independent public payload authority')
        allowed = self.public / 'allowed-signers-fixture'
        allowed.write_bytes(b'controlled external record authority; not a real key')
        self.keys = Mock()
        self.keys.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        self.keys.root = self.public
        self.keys.inventory = {'factory': {'allowed_signers': allowed.name, 'identity': 'fixture'}}
        self.keys.public_ids = {}
        self.keys.files = {path.name: inputs.sha256_file(path) for path in self.public.iterdir()}
        self.keys.cert.side_effect = lambda role, logical: self.certificates[role]
        self.keys.fingerprint.side_effect = lambda role, logical: self.tools.certificate(self.keys.cert(role, logical))[0]
        self.keys.avb.return_value = self.avb_public
        self.keys.file.side_effect = lambda name: self.public / name
        self.tools = Mock(spec=native.NativeTools)
        self.tools.scratch = self.root
        self.tools.source = self.root / 'source-fixture'
        self.tools.java_home = self.root / 'java-fixture'
        (self.tools.java_home / 'bin').mkdir(parents=True)
        (self.tools.java_home / 'bin/java').write_bytes(b'controlled provenance fixture')
        self.tools.certificate.side_effect = lambda path: (inputs.sha256_file(path), 'controlled-public-identity')
        self.tools.package_id.side_effect = lambda path: ('de.diamaneos.standalone' if path.name == 'standalone.apk'
                                                        else 'de.diamaneos.enclosed')

    def packages(self, compressed=False):
        apk_name = 'SYSTEM/app/Standalone/Standalone.apk'
        apex_name = 'SYSTEM/apex/Fixture.' + ('capex' if compressed else 'apex')
        enclosed_name = apex_name + '!/app/Enclosed/Enclosed.apk'
        artifact_dir = self.root / 'package-artifacts'
        artifact_dir.mkdir(exist_ok=True)
        paths = {}
        for name in ('standalone.apk', 'outer.apex', 'inner.apex', 'payload.img', 'enclosed.apk'):
            path = artifact_dir / name
            path.write_bytes(('controlled package bytes ' + name).encode())
            paths[name] = path
        target = self.root / 'target-files.zip'
        write_zip(target, {apk_name: paths['standalone.apk'], apex_name: paths['outer.apex'],
                          'META/apkcerts.txt': 'name="Standalone.apk" certificate="platform.x509.pem" '
                          'private_key="platform.pk8" compressed="false" partition="system"\n',
                          'META/apexkeys.txt': 'name="Fixture.apex" container_certificate="releasekey.x509.pem" '
                          'public_key="module.avbpubkey" private_key="module.pem" partition="system"\n'})
        plan = {'metadata': za.package_metadata_shape(za.metadata(target)), 'packages': {
            apk_name: apk_plan('platform', self.fingerprints['platform'], 'de.diamaneos.standalone'),
            apex_name: apex_plan(self.fingerprints['releasekey'], self.avb_public),
            enclosed_name: apk_plan('shared', self.fingerprints['shared'], 'de.diamaneos.enclosed')}}
        members = [('apk', apk_name, paths['standalone.apk']), ('apex', apex_name, paths['outer.apex'])]
        if compressed:
            members.append(('apex', apex_name, paths['inner.apex']))
        members.extend([('payload', apex_name, paths['payload.img']), ('apk', enclosed_name, paths['enclosed.apk'])])
        return target, plan, members

    def verify(self, target, plan, members):
        with patch.object(api, 'package_members', return_value=iter(members)) as enumerate_packages:
            result = api.verify_packages(target, plan, self.tools, self.keys)
        enumerate_packages.assert_called_once_with(target, self.tools)
        return result

    def test_verify_packages_checks_each_external_certificate_and_payload(self):
        target, plan, members = self.packages()
        result = self.verify(target, plan, members)
        self.assertEqual({'apk': 2, 'apex': 1, 'payload': 1}, result)
        self.assertEqual([call(self.certificates[role]) for role in ('platform', 'releasekey', 'shared')],
                         self.tools.certificate.call_args_list)
        self.assertEqual([call(members[i][2], self.fingerprints[role])
                          for i, role in ((0, 'platform'), (1, 'releasekey'), (3, 'shared'))],
                         self.tools.apk.call_args_list)
        self.assertEqual([call(role, members[i][1])
                          for i, role in ((0, 'platform'), (1, 'releasekey'), (3, 'shared'))],
                         self.keys.cert.call_args_list)
        self.keys.avb.assert_called_once_with(None)
        self.tools.avb.assert_called_once_with(members[2][2], self.avb_public, payload=True)

    def test_capex_checks_outer_and_inner_containers_and_one_payload(self):
        target, plan, members = self.packages(compressed=True)
        self.assertEqual({'apk': 2, 'apex': 2, 'payload': 1}, self.verify(target, plan, members))
        self.assertEqual([call(self.certificates[role]) for role in ('platform', 'releasekey', 'releasekey', 'shared')],
                         self.tools.certificate.call_args_list)
        self.assertEqual([call(members[i][2], self.fingerprints[role])
                          for i, role in ((0, 'platform'), (1, 'releasekey'), (2, 'releasekey'), (4, 'shared'))],
                         self.tools.apk.call_args_list)
        self.tools.avb.assert_called_once_with(members[3][2], self.avb_public, payload=True)

    def rewrite_metadata(self, target, changes):
        def change(name, data):
            for old, new in changes.get(name, ()):
                data = data.replace(old, new)
            return data
        rewrite_zip(target, change)

    def test_rewritten_certificate_and_key_paths_preserve_accepted_metadata_shape(self):
        target, plan, members = self.packages()
        self.rewrite_metadata(target, {
            'META/apkcerts.txt': [(b'platform.x509.pem', b'/signed/certificates/platform.x509.pem'),
                                 (b'platform.pk8', b'/signed/keys/platform.pk8')],
            'META/apexkeys.txt': [(b'releasekey.x509.pem', b'/signed/certificates/releasekey.x509.pem'),
                                  (b'module.avbpubkey', b'/signed/keys/module.avbpubkey'),
                                  (b'module.pem', b'/signed/keys/module.pem')]})
        self.assertEqual({'apk': {'Standalone.apk': {'compressed': 'false', 'partition': 'system'}},
                          'apex': {'Fixture.apex': {'partition': 'system'}}}, plan['metadata'])
        self.assertEqual({'apk': 2, 'apex': 1, 'payload': 1}, self.verify(target, plan, members))

    def test_changed_partition_or_compression_is_refused_before_verification(self):
        for member, old, new in (('META/apkcerts.txt', b'partition="system"', b'partition="vendor"'),
                                 ('META/apkcerts.txt', b'compressed="false"', b'compressed="true"'),
                                 ('META/apexkeys.txt', b'partition="system"', b'partition="vendor"'),
                                 # Changed membership.
                                 ('META/apkcerts.txt', b'Standalone.apk', b'Unlisted.apk')):
            with self.subTest(member=member, property=old):
                target, plan, members = self.packages()
                self.rewrite_metadata(target, {member: [(old, new)]})
                with self.assertRaisesRegex(inputs.SigningError, 'membership or storage properties'):
                    self.verify(target, plan, members)
                self.tools.apk.assert_not_called()
                self.tools.avb.assert_not_called()

    def test_missing_planned_package_is_refused(self):
        target, plan, members = self.packages()
        with self.assertRaisesRegex(inputs.SigningError, 'lacks planned packages'):
            self.verify(target, plan, members[:-1])

    def test_unexpected_final_package_is_refused(self):
        target, plan, members = self.packages()
        members.append(('apk', 'SYSTEM/app/Unlisted/Unlisted.apk', members[0][2]))
        with self.assertRaisesRegex(inputs.SigningError, 'Unlisted final package'):
            self.verify(target, plan, members)

    def test_missing_apex_payload_is_refused(self):
        target, plan, members = self.packages()
        with self.assertRaisesRegex(inputs.SigningError, 'lacks an APEX payload'):
            self.verify(target, plan, [member for member in members if member[0] != 'payload'])
        self.tools.avb.assert_not_called()

    def test_changed_external_certificate_is_refused_before_apk_verification(self):
        target, plan, members = self.packages()
        self.certificates['platform'].write_bytes(b'changed independent certificate')
        with self.assertRaisesRegex(inputs.SigningError, 'Package authority changed'):
            self.verify(target, plan, members)
        self.tools.apk.assert_not_called()

    def test_changed_external_payload_authority_is_refused_before_avb_verification(self):
        target, plan, members = self.packages()
        self.avb_public.write_bytes(b'changed independent payload authority')
        with self.assertRaisesRegex(inputs.SigningError, 'APEX payload authority changed'):
            self.verify(target, plan, members)
        self.tools.avb.assert_not_called()

    def publication(self, compressed=False):
        target, package_plan, _ = self.packages(compressed)
        root = self.root / 'publication-fixture'
        root.mkdir(exist_ok=True)
        (root / 'target-files.zip').write_bytes(target.read_bytes())
        for name in ('images.zip', 'factory.tar', 'factory.tar.sig', 'release.json.sig'):
            (root / name).write_bytes(('controlled publication bytes ' + name).encode())
        write_zip(root / 'ota.zip', {'payload.bin': b'controlled payload bytes',
                                  'META-INF/com/android/metadata': 'ota-type=AB\n'})
        policy = {'record_namespace': 'diamaneos-release-record', 'factory_namespace': 'factory images'}
        provenance = {'projects': {}, 'source_files': {}, 'java_sha256': inputs.sha256_file(self.tools.java_home / 'bin/java')}
        plan = publication_plan(target, package_plan['packages'], self.keys, provenance, policy, partitions=['boot', 'vbmeta'])
        description = publication_description()
        release_record(root, plan, description)
        # Plan authentication and crypto are isolated away from this boundary
        # fixture. No policy hash or signing key is introduced by the simulation.
        return root, plan, description, policy

    def verify_publication(self, fixture, counts, expect_failure=False):
        root, plan, description, policy = fixture
        with patch.object(api, 'validate_plan') as validate_plan, \
                patch.object(api, 'source_files', return_value={}), \
                patch.object(api, 'verify_packages', return_value=counts) as verify_packages, \
                patch.object(api.signing_mac, 'verify_mac_permissions', return_value={'files': 1}), \
                patch.object(api, 'publication_images', return_value=plan['partitions']) as images, \
                patch.object(api, 'verify_factory_contents') as factory:
            if expect_failure:
                with self.assertRaisesRegex(inputs.SigningError, 'Package verification coverage is incomplete'):
                    api.verify_publication(plan, description, root, policy, self.tools, self.keys)
                images.assert_not_called()
                factory.assert_not_called()
                self.tools.ota.assert_not_called()
                self.tools.apply_payload.assert_not_called()
                result = None
            else:
                result = api.verify_publication(plan, description, root, policy, self.tools, self.keys)
                images.assert_called_once()
                factory.assert_called_once()
                self.tools.ota.assert_called_once()
                self.tools.apply_payload.assert_called_once()
            validate_plan.assert_called_once_with(plan, policy)
            verify_packages.assert_called_once_with(root / 'target-files.zip', plan, self.tools, self.keys)
        return result

    def test_simulated_publication_reaches_success_with_complete_package_counts(self):
        fixture = self.publication()
        counts = {'apk': 2, 'apex': 1, 'payload': 1}
        result = self.verify_publication(fixture, counts)
        self.assertEqual('PASS', result['status'])
        self.assertEqual(counts, result['packages'])

    def test_publication_rejects_missing_extra_or_incorrect_package_counts(self):
        fixture = self.publication()
        for counts in ({}, {'apk': 2, 'apex': 1}, {'apk': 1, 'apex': 1, 'payload': 1},
                       {'apk': 2, 'apex': 1, 'payload': 0},
                       {'apk': 2, 'apex': 1, 'payload': 1, 'extra': 1}, None, []):
            with self.subTest(counts=counts):
                self.verify_publication(fixture, counts, expect_failure=True)

    def test_publication_requires_both_capex_containers(self):
        fixture = self.publication(compressed=True)
        self.verify_publication(fixture, {'apk': 2, 'apex': 1, 'payload': 1}, expect_failure=True)
        result = self.verify_publication(fixture, {'apk': 2, 'apex': 2, 'payload': 1})
        self.assertEqual({'apk': 2, 'apex': 2, 'payload': 1}, result['packages'])

    def test_empty_plan_and_empty_coverage_cannot_produce_publication_success(self):
        root, plan, description, policy = self.publication()
        plan = copy.deepcopy(plan)
        plan['packages'] = {}
        self.verify_publication((root, plan, description, policy), {'apk': 0, 'apex': 0, 'payload': 0}, expect_failure=True)


if __name__ == '__main__':
    unittest.main()
