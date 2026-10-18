"""Presigned APEX authority and byte-preservation boundaries, with no committed keys."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from diamaneos_tools import signing_inputs as inputs, signing_native as native
from diamaneos_tools import signing_qualification as plans, signing_verify as verify


class PresignedApexPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        self.rule = self.policy['presigned_apexes'][0]

    def test_reviewed_policy_binds_exact_stock_identity_not_a_name_only_exemption(self):
        inputs.validate_config(self.policy)
        self.assertEqual('VENDOR/apex/com.google.android.widevine.nonupdatable.apex', self.rule['artifact_path'])
        self.assertEqual('008e4037a9176952a41fe69e78d485d317cd874632bd2c0ad12869f3cd2c879a', self.rule['artifact_sha256'])
        self.assertIsNone(inputs.presigned_apex_rule(self.policy, 'SYSTEM/apex/' + self.rule['metadata_name']))
        self.assertIsNone(inputs.presigned_apex_rule(self.policy, self.rule['artifact_path'].replace('.apex', '.capex')))

    def test_missing_hash_unknown_fields_duplicates_and_identity_aliases_refuse(self):
        changes = [lambda p: p['presigned_apexes'][0].pop('payload_key_sha256'),
                   lambda p: p['presigned_apexes'][0].update(waive_verification=True),
                   lambda p: p['presigned_apexes'].append(copy.deepcopy(p['presigned_apexes'][0])),
                   lambda p: p['presigned_apexes'][0].update(metadata_name='Other.apex'),
                   lambda p: p['presigned_apexes'][0].update(artifact_path='../VENDOR/apex/Other.apex'),
                   lambda p: p['presigned_apexes'][0].update(artifact_sha256='a'*40)]
        for change in changes:
            p = copy.deepcopy(self.policy); change(p)
            with self.subTest(change=changes.index(change)), self.assertRaises(inputs.SigningError):
                inputs.validate_config(p)

    def test_only_reviewed_presigned_apex_gets_a_role_and_cannot_be_resigned(self):
        name = self.rule['metadata_name']
        metadata = {'apk': {}, 'apex': {name: {'container_certificate': 'PRESIGNED'}}}
        self.assertEqual({'kind': 'apex', 'container': 'PRESIGNED', 'payload': 'PRESIGNED'},
                         plans.role_map(metadata, self.policy, {})[name])
        with self.assertRaises(inputs.SigningError): plans.role_map(metadata, self.policy, {name: 'releasekey'})
        metadata['apex'][name]['container_certificate'] = 'keys/releasekey.x509.pem'
        with self.assertRaises(inputs.SigningError): plans.role_map(metadata, self.policy, {})
        with self.assertRaises(inputs.SigningError):
            plans.role_map({'apk': {}, 'apex': {'Unknown.apex': {'container_certificate': 'PRESIGNED'}}}, self.policy, {})


class ExternalBinaryAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.key = self.root / 'expected.avbpubkey'; self.key.write_bytes(b'independently supplied fixture public bytes')
        self.logical = 'VENDOR/apex/Fixture.apex'
        self.rule = {'metadata_name': 'Fixture.apex', 'artifact_path': self.logical, 'artifact_sha256': 'a'*64,
                     'certificate_sha256': ['b'*64], 'payload_key_sha256': inputs.sha256_file(self.key)}
        self.keys = native.PublicKeys.__new__(native.PublicKeys)
        self.keys.root = self.root; self.keys.artifact_root = None; self.keys.files = {}
        self.keys.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        self.keys.policy['presigned_apexes'] = [self.rule]
        self.keys.inventory = {'presigned_apex_payload_keys': {'Fixture.apex': self.key.name}}

    def test_external_binary_path_is_required_and_rechecked_against_reviewed_hash(self):
        self.assertEqual(self.key, self.keys.avb(self.logical))
        self.assertEqual(inputs.sha256_file(self.key), self.keys.files[self.key.name])
        self.key.write_bytes(b'replaced external authority')
        with self.assertRaises(inputs.SigningError): self.keys.avb(self.logical)

    def test_missing_authority_wrong_policy_and_unknown_logical_path_refuse(self):
        for change in (lambda: self.keys.inventory.clear(),
                       lambda: self.rule.update(payload_key_sha256='0'*64)):
            original = copy.deepcopy(self.keys.inventory); digest = self.rule['payload_key_sha256']
            change()
            with self.assertRaises(inputs.SigningError): self.keys.avb(self.logical)
            self.keys.inventory = original; self.rule['payload_key_sha256'] = digest
        for logical in ('VENDOR/apex/Unknown.apex', 'SYSTEM/apex/Fixture.apex', self.logical+'!/Fixture.apk'):
            with self.assertRaises(inputs.SigningError): self.keys.avb(logical)

    def test_authority_inside_publication_or_symlinked_is_refused(self):
        self.keys.artifact_root = self.root
        with self.assertRaises(inputs.SigningError): self.keys.avb(self.logical)
        self.keys.artifact_root = None
        alias = self.root/'linked.avbpubkey'; alias.symlink_to(self.key.name)
        self.keys.inventory['presigned_apex_payload_keys']['Fixture.apex'] = alias.name
        with self.assertRaises(inputs.SigningError): self.keys.avb(self.logical)

    def test_final_plan_cannot_downgrade_reviewed_preservation_to_resigning(self):
        plan = {'metadata': {}, 'packages': {self.logical: {'kind': 'apex', 'container': 'releasekey',
                'payload': 'avb', 'presigned_sha256': 'a'*64}}}
        tools = Mock()
        with patch.object(verify.za, 'metadata', return_value={}), \
             patch.object(verify.za, 'package_metadata_shape', return_value={}), \
             patch.object(verify, 'package_members', return_value=iter([('apex', self.logical, self.key)])):
            with self.assertRaises(inputs.SigningError): verify.verify_packages('unused', plan, tools, self.keys)
        tools.apk.assert_not_called(); tools.avb.assert_not_called()


if __name__ == '__main__': unittest.main()
