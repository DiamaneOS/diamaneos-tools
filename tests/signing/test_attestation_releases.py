"""Attestation release lists: values from vbmeta images, merging and P-256 signatures with throwaway keys."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import unittest

from diamaneos_tools import attestation_releases as ar
from diamaneos_tools.signing_inputs import SigningError
from .fixtures import TemporaryInputs

HEADER = "Algorithm:                SHA256_RSA4096\nRelease String:           'avbtool 1.4.0'\n"
INFOS = {
    'vbmeta': HEADER + ('Descriptors:\n    Chain Partition descriptor:\n      Partition Name:          boot\n'
                        '      Rollback Index Location: 30\n    Chain Partition descriptor:\n'
                        '      Partition Name:          init_boot\n      Rollback Index Location: 31\n'
                        "    Prop: com.android.build.vendor.security_patch -> '2026-09-05'\n"
                        '    Hashtree descriptor:\n      Partition Name:        vendor\n'),
    'boot': HEADER + ('Descriptors:\n    Hash descriptor:\n      Partition Name:        boot\n'
                      "    Prop: com.android.build.boot.os_version -> '17'\n"
                      "    Prop: com.android.build.boot.security_patch -> '2026-09-05'\n"),
    'init_boot': HEADER + ('Descriptors:\n    Hash descriptor:\n      Partition Name:        init_boot\n'
                           "    Prop: com.android.build.init_boot.os_version -> '17'\n"
                           "    Prop: com.android.build.init_boot.security_patch -> '2026-10-05'\n"),
}


def release(build='2026101700', boot_hash='AB' * 32):
    return {'build': build, 'verifiedBootHash': boot_hash, 'osVersion': 170000, 'osPatchLevel': 202610,
            'vendorPatchLevel': 20260905, 'bootPatchLevel': 20261005, 'attestationVersion': 300, 'keyMintVersion': 300}


class ReleaseValueTests(TemporaryInputs, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.infos = dict(INFOS)
        for name in self.infos:
            (self.root / (name + '.img')).write_bytes(b'image')

    def avbtool(self, arguments):
        if arguments[0] == 'info_image':
            return self.infos[Path(arguments[2]).stem]
        Path(arguments[-1]).write_text('ab' * 32 + '\n' if arguments[0] == 'calculate_vbmeta_digest' else 'key')
        return ''

    def values(self):
        return ar.release_values(self.avbtool, self.root, self.root, ar.device_config('FP6'), '2026101700',
                                 ar.boot_key(self.avbtool, Path('/external/avb.pub.pem'), self.root))

    def test_values_come_from_the_configured_properties_of_every_vbmeta_structure(self):
        # The FP6 attests init_boot's patch level as its boot patch level, not boot's.
        self.assertEqual({'device': 'FP6', 'verifiedBootKey': hashlib.sha256(b'key').hexdigest().upper(),
                          'release': release()}, self.values())

    def test_repeated_or_injected_properties_are_refused(self):
        self.infos['vbmeta'] += "    Prop: com.android.build.init_boot.security_patch -> '2026-11-05'\n"
        with self.assertRaisesRegex(SigningError, 'repeated'):
            self.values()
        self.infos['vbmeta'] = INFOS['vbmeta'].replace("'avbtool 1.4.0'", "'x'\nDescriptors:\n''")
        with self.assertRaisesRegex(SigningError, 'single descriptor list'):
            self.values()


class ReleaseListTests(TemporaryInputs, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.keys = {}
        for name in ('signer', 'other'):
            key = self.root / (name + '.pem')
            ar.openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-pkeyopt', 'ec_param_enc:named_curve', '-out', key])
            ar.openssl(['pkey', '-in', key, '-pubout', '-out', self.root / (name + '.pub.pem')])
            self.keys[name] = key
        self.entry = self.root / 'entry.json'
        self.write_entry(release())

    def write_entry(self, value, key='C' * 64):
        self.entry.write_text(json.dumps({'device': 'FP6', 'verifiedBootKey': key, 'release': value}))

    def run_cli(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            code = ar.main([str(a) for a in arguments])
        return code, errors.getvalue()

    def sign(self, output, *extra, previous=None):
        source = ['--previous', previous] if previous else ['--first']
        return self.run_cli('prepare', *source, *extra, '--signer', 'test', '--test',
                            '--test-key', self.keys['signer'], '--output', output)

    def verify(self, path, key='signer', previous=None):
        extra = ['--previous', previous] if previous else []
        return self.run_cli('verify', '--list', path, '--public-key', self.root / (key + '.pub.pem'), *extra)[0]

    def test_test_signature_round_trip_names_the_signer_key(self):
        first = self.root / 'first.json'
        self.assertEqual((0, ''), self.sign(first, '--entry', self.entry))
        self.assertEqual(0, self.verify(first))
        envelope = json.loads(first.read_text())
        spki = ar.openssl(['pkey', '-in', self.keys['signer'], '-pubout', '-outform', 'DER'])
        self.assertEqual([hashlib.sha256(spki).hexdigest().upper()], [s['keyId'] for s in envelope['signatures']])
        payload = json.loads(base64.b64decode(envelope['payload']))
        self.assertEqual((ar.PAYLOAD_FORMAT, 1), (payload['format'], payload['sequence']))
        self.assertEqual([{'id': 'FP6', 'verifiedBootKey': 'C' * 64, 'releases': [release()]}], payload['devices'])

    def test_wrong_key_and_tampered_payload_are_refused(self):
        first = self.root / 'first.json'
        self.sign(first, '--entry', self.entry)
        self.assertEqual(2, self.verify(first, 'other'))
        envelope = json.loads(first.read_text())
        payload = json.loads(base64.b64decode(envelope['payload']))
        payload['devices'][0]['releases'][0]['osPatchLevel'] = 202611
        envelope['payload'] = base64.b64encode(ar.encode_payload(payload)).decode()
        first.write_text(json.dumps(envelope))
        self.assertEqual(2, self.verify(first))

    def test_merge_keeps_releases_withdraws_explicitly_and_raises_the_sequence(self):
        first, second, third = (self.root / name for name in ('1.json', '2.json', '3.json'))
        self.sign(first, '--entry', self.entry)
        self.write_entry(release('2026110100', 'CD' * 32))
        self.assertEqual(0, self.sign(second, '--entry', self.entry, previous=first)[0])
        self.assertEqual(2, self.verify(first, previous=second))
        self.assertEqual(0, self.sign(third, '--withdraw', '2026101700', previous=second)[0])
        payload = json.loads(base64.b64decode(json.loads(third.read_text())['payload']))
        self.assertEqual((3, ['2026110100']), (payload['sequence'], [r['build'] for r in payload['devices'][0]['releases']]))
        # A listed build or boot hash and another key for the device are refused.
        for value, key in ((release('2026110100', 'EF' * 32), 'C' * 64), (release('2026120100', 'CD' * 32), 'C' * 64),
                           (release('2026120100', 'EF' * 32), 'D' * 64)):
            self.write_entry(value, key)
            self.assertEqual(2, self.sign(self.root / 'refused.json', '--entry', self.entry, previous=third)[0])
        self.assertFalse((self.root / 'refused.json').exists())

    def test_test_signer_needs_the_test_flag_and_refuses_production_results(self):
        result = self.root / 'result.json'
        record = json.loads(self.entry.read_text())
        result.write_text(json.dumps({'status': 'PASS', 'artifact_signatures_verified': True, 'mode': 'production',
                                      'provenance': {'build_number': '2026101700'}, 'attestation_release': record}))
        output = self.root / 'list.json'
        code, error = self.run_cli('prepare', '--first', '--entry', self.entry, '--signer', 'test',
                                   '--test-key', self.keys['signer'], '--output', output)
        self.assertEqual((2, True), (code, '--test' in error))
        self.assertEqual(2, self.sign(output, '--signing-result', result)[0])
        self.assertFalse(output.exists())
        result.write_text(result.read_text().replace('"production"', '"qualification"'))
        self.assertEqual(0, self.sign(output, '--signing-result', result)[0])

    def test_detached_payload_is_attached_only_with_a_valid_signature(self):
        first = self.root / 'first.json'
        self.sign(first, '--entry', self.entry)
        self.write_entry(release('2026110100', 'CD' * 32))
        payload = self.root / 'payload.json'
        code, _ = self.run_cli('prepare', '--previous', first, '--entry', self.entry, '--test', '--signer', 'detached',
                               '--public-key', self.root / 'signer.pub.pem', '--output', payload)
        self.assertEqual(0, code)
        self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest() + '  payload.json\n',
                         (self.root / 'payload.json.sha256').read_text())
        for key, expected in (('other', 2), ('signer', 0)):
            signature = self.root / (key + '.sig')
            signature.write_bytes(ar.openssl(['dgst', '-sha256', '-sign', self.keys[key], payload]))
            output = self.root / 'second.json'
            code, _ = self.run_cli('attach', '--payload', payload, '--signature', signature, '--previous', first,
                                   '--public-key', self.root / 'signer.pub.pem', '--output', output)
            self.assertEqual((expected, expected == 0), (code, output.exists()))
        self.assertEqual(0, self.verify(output, previous=first))


if __name__ == '__main__':
    unittest.main()
