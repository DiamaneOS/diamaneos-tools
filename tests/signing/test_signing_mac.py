"""Native X.509 representation fixtures; no APK signing or device qualification."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from diamaneos_tools import signing_archive as za, signing_inputs as inputs
from diamaneos_tools import signing_mac as mac, signing_native as native
from .fixtures import TemporaryInputs, write_zip

MEMBER = 'SYSTEM/etc/selinux/plat_mac_permissions.xml'
EXT_MEMBER = 'SYSTEM_EXT/etc/selinux/system_ext_mac_permissions.xml'


class SigningMacTests(TemporaryInputs, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = tempfile.TemporaryDirectory(prefix='mac-public-material-')
        cls.addClassCleanup(cls.material.cleanup)
        root = Path(cls.material.name).resolve()
        cls.pem, cls.der = {}, {}
        for name in sorted(inputs.CERT_ROLES) + ['old_testkey', 'old_platform', 'vendor_fixture']:
            cert, key = root / (name + '.x509.pem'), root / (name + '.ephemeral.key')
            result = subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                                     '-sha256', '-days', '1', '-subj', '/CN=MAC fixture ' + name,
                                     '-keyout', str(key), '-out', str(cert)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=30, check=False)
            if result.returncode:
                raise RuntimeError('Disposable native X.509 fixture generation failed')
            cls.pem[name] = cert.read_bytes()
            cls.der[name] = subprocess.run(['/usr/bin/openssl', 'x509', '-in', str(cert), '-outform', 'DER'],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          timeout=30, check=True).stdout
            key.unlink()

    def setUp(self):
        super().setUp()
        self.source, self.trust = self.root / 'source', self.root / 'external-public'
        (self.source / 'keys').mkdir(parents=True)
        self.trust.mkdir()
        for filename, key in (('testkey', 'old_testkey'), ('platform', 'old_platform')):
            (self.source / 'keys' / (filename + '.x509.pem')).write_bytes(self.pem[key])
        self.tools = native.NativeTools.__new__(native.NativeTools)
        self.tools.source = self.source
        self.tools.env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
        self.keys = native.PublicKeys.__new__(native.PublicKeys)
        self.keys.tools, self.keys.root = self.tools, self.trust
        self.keys.artifact_root, self.keys.files = None, {}
        self.keys.inventory = {'schema_version': 2, 'certificate_roles': {role: role + '.x509.pem' for role in inputs.CERT_ROLES}}
        self.keys.policy = {'apk_selectors': {'testkey': 'releasekey', 'platform': 'platform'}, 'presigned_packages': []}
        for role in inputs.CERT_ROLES:
            (self.trust / (role + '.x509.pem')).write_bytes(self.pem[role])
            self.keys.cert(role, '')
        self.roles = {'Fixture.apk': {'kind': 'apk', 'container': 'releasekey', 'payload': None},
                      'Privileged.apk': {'kind': 'apk', 'container': 'platform', 'payload': None}}
        self.apk_metadata = ('name="Fixture.apk" certificate="keys/testkey.x509.pem" private_key="keys/testkey.pk8" partition="system"\n'
                             'name="Privileged.apk" certificate="keys/platform.x509.pem" private_key="keys/platform.pk8" partition="system"\n')
        self.apex_metadata = ''
        self.original, self.signed = self.root / 'original.zip', self.root / 'signed.zip'
        self.xml = self.policy_xml()
        self.original_xml(self.xml)
        self.signed_xml(self.replaced(self.xml))

    def hex(self, name): return self.der[name].hex()
    def pin(self, name): return hashlib.sha256(self.der[name]).hexdigest()

    def policy_xml(self):
        return ('<?xml version="1.0" encoding="utf-8"?><policy>'
                '<signer signature="' + self.hex('old_platform') + '"><seinfo value="platform"/></signer>'
                '<signer signature="' + self.hex('old_testkey') + '"><seinfo value="default"/></signer>'
                '<signer signature="' + self.hex('old_testkey') + '"><package name="example.isolated"><seinfo value="isolated_label"/></package></signer>'
                '<signer signature="' + self.hex('old_testkey') + '"><package name="example.second"><seinfo value="second_label"/></package></signer>'
                '</policy>')

    def replaced(self, text):
        for old, new in (('old_testkey', 'releasekey'), ('old_platform', 'platform')):
            text = re.sub(self.hex(old), self.hex(new), text, flags=re.IGNORECASE)
        return text

    def archive(self, path, xml, extra=None):
        entries = {'META/apkcerts.txt': self.apk_metadata, 'META/apexkeys.txt': self.apex_metadata,
                   'META/misc_info.txt': 'default_system_dev_certificate=keys/testkey\n'}
        if xml is not None:
            entries[MEMBER] = xml
        write_zip(path, [*entries.items(), *(extra or {}).items()])

    def original_xml(self, xml, extra=None): self.archive(self.original, xml, extra)
    def signed_xml(self, xml, extra=None): self.archive(self.signed, xml, extra)

    def prepare(self):
        return mac.prepare_mac_permissions(self.original, za.metadata(self.original), self.roles, self.keys, self.tools)

    def verify(self, expected=None):
        return mac.verify_mac_permissions(self.signed, self.prepare() if expected is None else expected)

    def test_prepare_uses_external_public_roles_and_exact_package_isolation(self):
        expected = self.prepare()
        self.assertEqual({MEMBER}, set(expected))
        self.assertEqual({self.pin('platform'), self.pin('releasekey')}, {c for p in expected[MEMBER] for c in p['certificates']})
        self.assertNotIn(self.pin('old_testkey'), json.dumps(expected))
        self.assertIn({'example.isolated': 'isolated_label'}, [p['packages'] for p in expected[MEMBER]])
        self.assertEqual({'files': 1, 'signers': 4, 'global_selectors': 2, 'package_selectors': 2, 'certificate_selectors': 4}, self.verify(expected))

    def test_final_verification_needs_no_unsigned_source_keys_or_native_tools(self):
        expected = json.loads(json.dumps(self.prepare()))
        self.original.unlink(); shutil.rmtree(self.source); shutil.rmtree(self.trust)
        with patch.object(native.NativeTools, 'openssl', side_effect=AssertionError('Final verification must not use native tools')):
            self.assertEqual(2, self.verify(expected)['package_selectors'])

    def test_native_relative_original_skip_is_detected_from_an_unrelated_cwd(self):
        unrelated = self.root / 'unrelated'; unrelated.mkdir()
        with patch('os.getcwd', return_value=str(unrelated)):
            expected = self.prepare()
        self.signed_xml(self.xml)
        with self.assertRaisesRegex(inputs.SigningError, 'certificate replacements differ'): self.verify(expected)

    def test_cert_element_sets_and_certificate_case_order_are_supported(self):
        xml = '<policy><signer signature="' + self.hex('old_platform').upper() + '"><cert signature="' + self.hex('old_testkey') + '"/><seinfo value="combined"/></signer></policy>'
        self.original_xml(xml)
        final = '<policy><signer><cert signature="' + self.hex('releasekey').upper() + '"/><cert signature="' + self.hex('platform') + '"/><seinfo value="combined"/></signer></policy>'
        self.signed_xml(final)
        self.assertEqual(2, self.verify()['certificate_selectors'])

    def test_selector_order_whitespace_and_comments_are_nonsemantic(self):
        self.signed_xml(self.replaced(self.xml).replace('<policy>', '<policy>\n<!-- fixture & literal &amp; DOCTYPE text -->\n'))
        self.assertEqual(4, self.verify()['signers'])

    def test_multiple_files_allow_disjoint_package_selectors_and_global_fallback(self):
        extra = '<policy><signer signature="' + self.hex('old_testkey') + '"><package name="example.third"><seinfo value="third_label"/></package></signer></policy>'
        self.original_xml(self.xml, {EXT_MEMBER: extra})
        self.signed_xml(self.replaced(self.xml), {EXT_MEMBER: self.replaced(extra)})
        self.assertEqual(3, self.verify()['package_selectors'])

    def test_identical_system_vendor_global_mapping_retains_both_files(self):
        vendor = '<policy><signer signature="' + self.hex('old_platform') + '"><seinfo value="platform"/></signer></policy>'
        member = 'VENDOR/etc/selinux/vendor_mac_permissions.xml'
        self.original_xml(self.xml, {member: vendor})
        self.signed_xml(self.replaced(self.xml), {member: self.replaced(vendor)})
        expected = self.prepare()
        self.assertEqual({MEMBER, member}, set(expected))
        self.assertEqual(3, self.verify(expected)['global_selectors'])
        changed = copy.deepcopy(expected)
        changed[member][0]['seinfo'] = 'different_label'
        with self.assertRaises(inputs.SigningError): self.verify(changed)
        self.signed_xml(self.replaced(self.xml), {member: self.replaced(vendor).replace('value="platform"', 'value="different_label"')})
        with self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_conflicting_global_mapping_across_partitions_is_refused(self):
        other = '<policy><signer signature="' + self.hex('old_platform') + '"><seinfo value="other_label"/></signer></policy>'
        self.original_xml(self.xml, {EXT_MEMBER: other})
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_identical_global_duplicate_within_one_file_stays_refused(self):
        extra = '<signer signature="' + self.hex('old_platform') + '"><seinfo value="platform"/></signer>'
        self.original_xml(self.xml.replace('</policy>', extra + '</policy>'))
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_cross_partition_package_overlap_stays_refused_with_same_label(self):
        other = '<policy><signer signature="' + self.hex('old_testkey') + '"><package name="example.isolated"><seinfo value="isolated_label"/></package></signer></policy>'
        self.original_xml(self.xml, {EXT_MEMBER: other})
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_preserved_vendor_certificate_requires_independent_presigned_policy(self):
        xml = self.xml.replace('</policy>', '<signer signature="' + self.hex('vendor_fixture') + '"><seinfo value="vendor_label"/></signer></policy>')
        self.original_xml(xml); self.signed_xml(self.replaced(xml))
        with self.assertRaisesRegex(inputs.SigningError, 'independently approved'): self.prepare()
        self.keys.policy['presigned_packages'].append({'metadata_name': 'Vendor.apk', 'artifact_basename': 'Vendor.apk',
            'parent_apex': None, 'certificate_sha256': [self.pin('vendor_fixture')]})
        self.assertEqual(3, self.verify()['global_selectors'])

    def test_presigned_metadata_requires_approval_and_resigning_does_not_invent_mac_selectors(self):
        self.apk_metadata += 'name="Vendor.apk" certificate="PRESIGNED" private_key="PRESIGNED"\n'
        self.roles['Vendor.apk'] = {'kind': 'apk', 'container': 'PRESIGNED', 'payload': None}
        self.original_xml(self.xml)
        with self.assertRaisesRegex(inputs.SigningError, 'exact external policy rule'): self.prepare()
        self.keys.policy['presigned_packages'].append({'metadata_name': 'Vendor.apk', 'artifact_basename': 'Vendor.apk',
            'parent_apex': None, 'certificate_sha256': [self.pin('vendor_fixture')]})
        expected = self.prepare()
        self.assertEqual(4, self.verify(expected)['signers'])
        self.roles['Vendor.apk']['container'] = 'releasekey'
        self.assertEqual(expected, self.prepare())

    def test_expected_external_material_is_not_discovered_in_artifacts(self):
        self.original_xml(self.xml.replace(self.hex('old_testkey'), self.hex('vendor_fixture')),
                          {'META/vendor_fixture.x509.pem': self.pem['vendor_fixture']})
        with self.assertRaisesRegex(inputs.SigningError, 'independently approved'): self.prepare()

    def test_presigned_policy_cannot_implicitly_replace_a_preserved_authority(self):
        self.keys.policy['presigned_packages'].append({'metadata_name': 'Kept.apk', 'artifact_basename': 'Kept.apk',
            'parent_apex': None, 'certificate_sha256': [self.pin('old_testkey')]})
        with self.assertRaisesRegex(inputs.SigningError, 'Presigned MAC certificate would be replaced'): self.prepare()

    def test_package_key_override_does_not_change_native_mac_key_map(self):
        expected = self.prepare()
        self.roles['Fixture.apk']['container'] = 'platform'
        self.assertEqual(expected, self.prepare())

    def test_unknown_role_missing_source_and_wrong_metadata_refuse(self):
        self.roles['Fixture.apk']['container'] = 'unknown'
        with self.assertRaises(inputs.SigningError): self.prepare()
        self.roles['Fixture.apk']['container'] = 'releasekey'
        (self.source / 'keys/testkey.x509.pem').unlink()
        with self.assertRaises(inputs.SigningError): self.prepare()
        with self.assertRaises(inputs.SigningError):
            mac.prepare_mac_permissions(self.original, {'apk': {}, 'apex': {}}, self.roles, self.keys, self.tools)

    def test_nonstandard_module_mac_certificate_is_not_replaced_by_package_override(self):
        (self.source / 'module').mkdir()
        (self.source / 'module/fixture.x509.pem').write_bytes(self.pem['vendor_fixture'])
        self.apk_metadata += 'name="Module.apk" certificate="module/fixture.x509.pem" private_key="module/fixture.pk8"\n'
        self.roles['Module.apk'] = {'kind': 'apk', 'container': 'shared', 'payload': None}
        xml = self.xml.replace('</policy>', '<signer signature="' + self.hex('vendor_fixture') + '"><package name="example.module"><seinfo value="module_label"/></package></signer></policy>')
        self.original_xml(xml)
        self.signed_xml(self.replaced(xml).replace(self.hex('vendor_fixture'), self.hex('shared')))
        with self.assertRaisesRegex(inputs.SigningError, 'independently approved'): self.prepare()

    def test_apex_package_mapping_does_not_add_native_mac_key_map(self):
        (self.source / 'module').mkdir()
        (self.source / 'module/fixture.x509.pem').write_bytes(self.pem['vendor_fixture'])
        self.apex_metadata = 'name="Module.apex" container_certificate="module/fixture.x509.pem" private_key="module/fixture.pem"\n'
        self.roles['Module.apex'] = {'kind': 'apex', 'container': 'releasekey', 'payload': 'avb'}
        xml = '<policy><signer signature="' + self.hex('vendor_fixture') + '"><seinfo value="module_label"/></signer></policy>'
        self.original_xml(xml); self.signed_xml(xml.replace(self.hex('vendor_fixture'), self.hex('releasekey')))
        with self.assertRaisesRegex(inputs.SigningError, 'independently approved'): self.prepare()
        self.apex_metadata = self.apex_metadata.replace('module/fixture.x509.pem', 'keys/platform.x509.pem')
        self.original_xml(xml)
        with self.assertRaisesRegex(inputs.SigningError, 'independently approved'): self.prepare()

    def test_external_public_certificate_changed_during_preparation_refuses(self):
        original = self.tools.openssl
        mutated = False
        def changed(args, data=None):
            nonlocal mutated
            result = original(args, data)
            if not mutated:
                mutated = True
                path = self.trust / 'platform.x509.pem'
                path.write_bytes(path.read_bytes() + b'\n')
            return result
        with patch.object(self.tools, 'openssl', side_effect=changed), self.assertRaises(inputs.SigningError): self.prepare()

    def test_expected_snapshot_changed_during_final_check_refuses(self):
        expected = self.prepare()
        real_read = mac._read_policies
        def changed(handle, names):
            result = real_read(handle, names)
            expected[MEMBER][0]['seinfo'] = 'changed'
            return result
        with patch.object(mac, '_read_policies', side_effect=changed), self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_package_isolation_cannot_be_dropped_or_widened_to_global(self):
        expected = self.prepare()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.replaced(self.xml)); root.remove(root[2])
        self.signed_xml(ET.tostring(root))
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        root = ET.fromstring(self.replaced(self.xml)); root[2].clear(); root[2].set('signature', self.hex('releasekey'))
        ET.SubElement(root[2], 'seinfo', value='isolated_label')
        self.signed_xml(ET.tostring(root))
        with self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_package_names_seinfo_and_global_cert_roles_cannot_change(self):
        expected = self.prepare()
        for old, new in (('example.isolated', 'example.other'), ('isolated_label', 'platform'),
                         (self.hex('platform'), self.hex('releasekey')), (self.hex('releasekey'), self.hex('shared'))):
            self.signed_xml(self.replaced(self.xml).replace(old, new))
            with self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_missing_added_and_moved_xml_files_refuse(self):
        expected = self.prepare()
        self.signed_xml(None)
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        self.signed_xml(self.replaced(self.xml), {EXT_MEMBER: '<policy/>'})
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        self.signed_xml(None, {EXT_MEMBER: self.replaced(self.xml)})
        with self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_conflicting_global_and_overlapping_package_selectors_across_files_refuse(self):
        for stanza in ('<seinfo value="different_default"/>', '<package name="example.isolated"><seinfo value="isolated_label"/></package>'):
            extra = '<policy><signer signature="' + self.hex('old_testkey') + '">' + stanza + '</signer></policy>'
            self.original_xml(self.xml, {EXT_MEMBER: extra})
            with self.assertRaises(inputs.SigningError): self.prepare()

    def test_replacement_collisions_and_certificate_set_collapse_refuse(self):
        (self.source / 'keys/devkey.x509.pem').write_bytes(self.pem['vendor_fixture'])
        for xml in (
            '<policy><signer signature="' + self.hex('old_testkey') + '"><cert signature="' + self.hex('vendor_fixture') + '"/><seinfo value="x"/></signer></policy>',
            '<policy><signer signature="' + self.hex('old_testkey') + '"><seinfo value="x"/></signer><signer signature="' + self.hex('vendor_fixture') + '"><seinfo value="x"/></signer></policy>'):
            self.original_xml(xml)
            with self.assertRaises(inputs.SigningError): self.prepare()

    def test_repeated_conflicting_missing_or_unknown_xml_fields_refuse(self):
        cert = self.hex('old_testkey')
        invalid = (
            '<signer signature="' + cert + '"><seinfo value="x"/><seinfo value="y"/></signer>',
            '<signer signature="' + cert + '"><seinfo value="x"/><package name="example.x"><seinfo value="x"/></package></signer>',
            '<signer><seinfo value="x"/></signer>', '<signer signature="@PLACEHOLDER"><seinfo value="x"/></signer>',
            '<signer signature="' + cert + '"><package name="example.x"/></signer>',
            '<signer signature="' + cert + '"><package name="example.x"><seinfo value="x"/><seinfo value="y"/></package></signer>',
            '<signer signature="' + cert + '"><package name="example.x"><seinfo value="x"/></package><package name="example.x"><seinfo value="y"/></package></signer>',
            '<signer signature="' + cert + '" unknown="x"><seinfo value="x"/></signer>',
            '<signer signature="' + cert + '"><seinfo value="x"><unknown/></seinfo></signer>',
            '<unknown/>', '<signer signature="' + cert + '"><seinfo value="x:privapp"/></signer>',
        )
        for stanza in invalid:
            with self.subTest(stanza=stanza[:50]):
                self.original_xml('<policy>' + stanza + '</policy>')
                with self.assertRaises(inputs.SigningError): self.prepare()

    def test_identical_inner_values_follow_framework_set_semantics(self):
        cert = self.hex('old_testkey')
        xml = ('<policy><signer signature="' + cert + '"><cert signature="' + cert.upper() + '"/>'
               '<package name="example.x"><seinfo value="x"/><seinfo value="x"/></package>'
               '<package name="example.x"><seinfo value="x"/></package></signer></policy>')
        self.original_xml(xml); self.signed_xml(self.replaced(xml))
        self.assertEqual(1, self.verify()['package_selectors'])

    def test_dtd_entities_encoding_and_processing_instructions_refuse(self):
        for xml in ('<!DOCTYPE policy>' + self.xml, '<!DOCTYPE policy [<!ENTITY x "default">]>' + self.xml.replace('value="default"', 'value="&x;"'),
                    self.xml.replace('value="default"', 'value="&#100;efault"'), '<?fixture instruction?>' + self.xml,
                    self.xml.encode('utf-16'), self.xml.replace('<policy>', '<policy xmlns="urn:fixture">')):
            self.original_xml(xml)
            with self.assertRaises(inputs.SigningError): self.prepare()

    def test_malformed_der_cannot_be_prepared_from_a_presigned_pin(self):
        raw = b'not a certificate'
        pin = hashlib.sha256(raw).hexdigest()
        self.keys.policy['presigned_packages'] = [{'certificate_sha256': [pin]}]
        self.original_xml('<policy><signer signature="' + raw.hex() + '"><seinfo value="x"/></signer></policy>')
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_xml_size_depth_and_certificate_limits_refuse(self):
        expected = self.prepare()
        self.signed_xml('<policy>' + '<unknown>' * 5 + '</unknown>' * 5 + '</policy>')
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        self.signed_xml(self.replaced(self.xml))
        with patch.object(mac, 'MAX_XML', 32), self.assertRaises(inputs.SigningError): self.verify(expected)
        self.original_xml('<policy><signer signature="' + 'ab' * (mac.MAX_CERTIFICATE + 1) + '"><seinfo value="x"/></signer></policy>')
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_xml_archive_duplicates_links_traversal_and_nonregular_members_refuse(self):
        expected = self.prepare()
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            with zipfile.ZipFile(self.signed, 'w') as handle:
                info = zipfile.ZipInfo(MEMBER); info.external_attr = mode << 16
                handle.writestr(info, self.replaced(self.xml))
            with self.assertRaises(inputs.SigningError): self.verify(expected)
        self.signed_xml(self.replaced(self.xml))
        with zipfile.ZipFile(self.signed, 'a') as handle, warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning); handle.writestr(MEMBER, self.replaced(self.xml))
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        self.signed_xml(self.replaced(self.xml), {'../escape': b'x'})
        with self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_archive_and_public_material_symlinks_hardlinks_or_private_inputs_refuse(self):
        expected = self.prepare()
        alias = self.root / 'alias.zip'; alias.symlink_to(self.signed)
        with self.assertRaises(inputs.SigningError): mac.verify_mac_permissions(alias, expected)
        alias.unlink(); os.link(self.signed, alias)
        with self.assertRaises(inputs.SigningError): self.verify(expected)
        alias.unlink()
        source = self.source / 'keys/testkey.x509.pem'; raw = source.read_bytes(); source.unlink()
        source.symlink_to(self.source / 'keys/platform.x509.pem')
        with self.assertRaises(inputs.SigningError): self.prepare()
        source.unlink(); source.write_bytes(raw + b'PRIVATE KEY')
        with self.assertRaises(inputs.SigningError): self.prepare()

    def test_changed_metadata_public_files_or_signed_bytes_refuse(self):
        expected = self.prepare()
        original = self.tools.openssl
        mutated = False
        def changed(args, data=None):
            nonlocal mutated
            result = original(args, data)
            if not mutated:
                mutated = True
                self.roles['Fixture.apk']['payload'] = 'changed'
            return result
        with patch.object(self.tools, 'openssl', side_effect=changed), self.assertRaises(inputs.SigningError): self.prepare()
        self.signed_xml(self.replaced(self.xml))
        real_read = mac._read_policies
        def changed_archive(handle, names):
            result = real_read(handle, names)
            self.signed.write_bytes(self.signed.read_bytes() + b'changed')
            return result
        with patch.object(mac, '_read_policies', side_effect=changed_archive), self.assertRaises(inputs.SigningError): self.verify(expected)

    def test_expected_snapshot_rejects_unknown_repeated_missing_and_unsafe_mappings(self):
        expected = self.prepare()
        for value in ({}, {'../escape_mac_permissions.xml': expected[MEMBER]},
                      {MEMBER: []}, {MEMBER: [dict(expected[MEMBER][0], unexpected=True)]},
                      {MEMBER: [dict(expected[MEMBER][0], certificates=['a' * 64, 'a' * 64])]},
                      {MEMBER: [dict(expected[MEMBER][0], certificates=[])]},
                      {MEMBER: [dict(expected[MEMBER][0], certificates=['not-a-pin'])]}):
            with self.assertRaises(inputs.SigningError): self.verify(value)


if __name__ == '__main__':
    unittest.main()
