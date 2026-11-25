"""The vendored guest contract and release-critical parser cases."""
import ast
import hashlib
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import guest_vm_contract as contract
from tests.build.test_image_verify import newc, rsa_test_key, signed_avb_image


class ContractTest(unittest.TestCase):
    def test_vendored_source_is_the_reviewed_shared_module(self):
        self.assertEqual(hashlib.sha256(Path(contract.__file__).read_bytes()).hexdigest(),
                         '0e743079ebf8c8b43efb8cb4b7c5d293425a07f5f3f67f31a88d249a46969367')

    def test_archives_have_one_empty_trailer_and_unique_names(self):
        good = newc('bin', 'dev', 'init')
        for data in [newc('init', 'init'), newc('init', trailer=False), good + newc('extra')]:
            with self.subTest(data=data[:110]):
                with self.assertRaises(ValueError):
                    contract.cpio_members(data)
        trailer = good.index(b'TRAILER!!!') - 110
        for offset, replacement in [(trailer + 54, b'00000004'), (54, b'7fffffff')]:
            with self.assertRaises(ValueError):
                contract.cpio_members(good[:offset] + replacement + good[offset + 8:])

    def test_sha512_partition_digest_is_not_supported_by_pvmfw(self):
        data, salt = b'image', b'salt'
        self.assertFalse(contract.hash_descriptor_matches(
            (len(data), 'sha512', salt, hashlib.sha512(salt + data).digest()), data))
        self.assertFalse(contract.hash_descriptor_matches(
            (len(data) - 1, 'sha256', salt, hashlib.sha256(salt + data).digest()), data))

    def test_rsa_signature_has_a_canonical_integer_encoding(self):
        modulus = rsa_test_key()[0]
        for attempt in range(32):
            data = b'kernel' + bytes([attempt])
            meta = contract.avb_vbmeta(signed_avb_image(data, [('boot', data, b'salt')]))
            signature = int.from_bytes(meta['signature'], 'big') + modulus
            if signature < 1 << (8 * len(meta['signature'])):
                self.assertEqual(contract.avb_signature_problem(meta), '')
                meta['signature'] = signature.to_bytes(len(meta['signature']), 'big')
                self.assertIn('canonical RSA range', contract.avb_signature_problem(meta))
                break
        else:
            self.fail('fixture needs a noncanonical encoding that fits the signature field')
