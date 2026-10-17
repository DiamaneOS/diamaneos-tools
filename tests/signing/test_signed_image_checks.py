"""The signed image check profile with the platform avbtool and disposable keys."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from diamaneos_tools import image_profiles as profiles, image_verify, signing_archive as za, signing_native
from .test_native_signatures import OTATOOLS, SOURCE, require_native_inputs

TEST_KEY = 'external/avb/test/data/testkey_rsa4096.pem'


class SignedImageCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_native_inputs()
        temp = tempfile.TemporaryDirectory(prefix='signed-image-checks-')
        cls.addClassCleanup(temp.cleanup)
        cls.root, cls.source = Path(temp.name), Path(SOURCE)
        cls.otatools = za.unpack_tools(Path(OTATOOLS), cls.root / 'otatools')
        cls.tools = image_verify.Tools(cls.otatools / 'bin', cls.source, cls.root / 'work')
        for name in ('own', 'other'):
            subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:4096',
                            '-out', cls.root / (name + '.pem')], check=True, capture_output=True)

    def key(self, name):
        return self.source / TEST_KEY if name == 'test' else self.root / (name + '.pem')

    def image_set(self, signer, chain, boot, published=None):
        """vbmeta signed by signer, chaining boot at location 30 to chain's key, boot signed by boot."""
        images = Path(tempfile.mkdtemp(dir=self.root))
        (images / 'boot.img').write_bytes(b'boot' * 4096)
        self.tools.run('avbtool', ['add_hash_footer', '--image', images / 'boot.img', '--partition_size', '131072',
                                   '--partition_name', 'boot', '--algorithm', 'SHA256_RSA4096',
                                   '--key', self.key(boot), '--rollback_index', '7'])
        for name in {chain, published or signer}:
            self.tools.run('avbtool', ['extract_public_key', '--key', self.key(name),
                                       '--output', images.parent / (images.name + name + '.bin')])
        self.tools.run('avbtool', ['make_vbmeta_image', '--output', images / 'vbmeta.img', '--algorithm',
                                   'SHA256_RSA4096', '--key', self.key(signer), '--chain_partition',
                                   f'boot:30:{images.parent / (images.name + chain + ".bin")}'])
        key = (images.parent / (images.name + (published or signer) + '.bin')).read_bytes()
        (images / 'avb_pkmd.bin').write_bytes(key)
        (images / 'SHA256SUMS').write_text(f'{hashlib.sha256(key).hexdigest()}  avb_pkmd.bin\n')
        config = {'avb': {'test_key': TEST_KEY, 'chains': {'boot': 30}}}
        record = {'never_lock': True, 'release': False, 'signing': 'qualification-keys', 'official': True,
                  'avb_custom_key': {'file': 'avb_pkmd.bin', 'sha256': hashlib.sha256(key).hexdigest()},
                  'avb_rollback_indexes': {'vbmeta': 0, 'boot': 7}}
        return SimpleNamespace(images=images, record=record, tools=self.tools, config=config, src=self.source)

    def checks(self, v):
        """The record and AVB counterparts. The FP6 layout and the rest of the record have their own tests."""
        info = v.tools.run('avbtool', ['info_image', '--image', v.images / 'vbmeta.img'])
        with mock.patch.object(image_verify, 'avb_layout', return_value=([], info)), \
                mock.patch.object(image_verify, 'record_problems', return_value=[]):
            results = []
            for check in (profiles.check_signed_record, profiles.check_signed_avb):
                try:
                    results.append(check(v))
                except image_verify.CheckFailed as error:
                    results.append((False, str(error)))
            return results

    def test_own_key_passes(self):
        v = self.image_set('own', 'own', 'own')
        (record, avb) = self.checks(v)
        self.assertEqual((True, ''), record)
        self.assertEqual((True, 'rollback indexes: vbmeta 0, boot 7'), avb)
        native = signing_native.NativeTools(self.otatools, self.source, self.root)
        self.assertEqual(7, native.avb_rollback_index(v.images / 'boot.img'))
        v.record['avb_rollback_indexes']['boot'] = 8
        self.assertFalse(self.checks(v)[1][0])

    def test_aosp_test_key_fails(self):
        for record_or_avb in self.checks(self.image_set('test', 'test', 'test')):
            self.assertFalse(record_or_avb[0])
            self.assertIn('is the AOSP AVB test key testkey_rsa4096.pem', record_or_avb[1])

    def test_mismatched_keys_fail(self):
        (record, avb) = self.checks(self.image_set('own', 'other', 'other'))
        self.assertTrue(record[0], record)
        self.assertFalse(avb[0])
        (record, avb) = self.checks(self.image_set('own', 'own', 'other'))
        self.assertFalse(avb[0])
        (record, avb) = self.checks(self.image_set('own', 'own', 'own', published='other'))
        self.assertEqual((False, 'vbmeta.img is not signed with avb_pkmd.bin'), record)
        self.assertFalse(avb[0])


if __name__ == '__main__':
    unittest.main()
