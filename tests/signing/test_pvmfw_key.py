"""The signer's check that pvmfw carries the release AVB key, and no other, in its binary."""
import base64
import random
import struct
import unittest

from diamaneos_tools.avb_test_keys import TEST_KEY_MODULI
from diamaneos_tools.signing_native import avb_public_keys, pvmfw_carries_key


def avb_key(bits=4096, seed=1):
    """An AVB RSA public key blob (avb_crypto.h) for a made-up odd modulus of exactly bits bits."""
    size = bits // 8
    n = random.Random(seed).getrandbits(bits) | (1 << (bits - 1)) | 1
    n0inv = (-pow(n, -1, 1 << 32)) % (1 << 32)
    return struct.pack('>II', bits, n0inv) + n.to_bytes(size, 'big') + pow(2, 2 * bits, n).to_bytes(size, 'big')


KEY = avb_key(seed=1)
TEST_KEY = avb_key(seed=2)


def image(binary, footer_key=b'', padding=b'', fields=None, trailer=b''):
    """A pvmfw binary padded to a partition with an AVB footer, its vbmeta optionally holding footer_key and followed
    by trailer. fields overrides the footer's (major, minor, original size, vbmeta offset, vbmeta size)."""
    vbmeta = b'AVB0' + bytes(636) + footer_key
    data = binary + padding + vbmeta + trailer
    data += bytes(65536 - len(data) - 64)
    fields = fields or (1, 0, len(binary), len(binary) + len(padding), len(vbmeta))
    footer = b'AVBf' + struct.pack('>IIQQQ', *fields) + bytes(28)
    return data + footer


STOCK = TEST_KEY_MODULI['testkey_rsa4096']


class PvmfwKeyTests(unittest.TestCase):
    def test_key_in_the_binary_passes(self):
        self.assertTrue(pvmfw_carries_key(image(b'pvmfw code' + KEY + b'more code'), KEY))

    def test_other_key_fails(self):
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code' + TEST_KEY + b'more code'), KEY))

    def test_a_second_key_beside_the_release_key_fails(self):
        for other in (TEST_KEY, avb_key(2048, 3), avb_key(8192, 4)):
            with self.subTest(bits=struct.unpack('>I', other[:4])[0]):
                self.assertFalse(pvmfw_carries_key(image(b'code' + KEY + b'config' + other), KEY))
                self.assertFalse(pvmfw_carries_key(image(b'code' + other + b'config' + KEY), KEY))

    def test_two_copies_of_the_release_key_fail(self):
        self.assertFalse(pvmfw_carries_key(image(b'code' + KEY + b'config' + KEY), KEY))

    def test_key_only_in_the_footer_fails(self):
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code' + bytes(len(KEY)), footer_key=KEY), KEY))

    def test_release_key_in_the_binary_and_test_key_in_the_footer_passes(self):
        self.assertTrue(pvmfw_carries_key(image(b'code' + KEY, footer_key=TEST_KEY), KEY))

    def test_empty_key_fails(self):
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code'), b''))

    def test_image_without_footer(self):
        self.assertTrue(pvmfw_carries_key(b'pvmfw code' + KEY, KEY))

    def test_bytes_that_only_look_like_a_key_header_are_not_keys(self):
        fake = bytearray(KEY)
        fake[-1] ^= 1  # R^2 mod n no longer matches the modulus
        header_only = struct.pack('>II', 4096, 0) + bytes(1024)
        self.assertEqual([], avb_public_keys(bytes(fake) + header_only + b'\x00\x00\x10\x00' * 300))
        self.assertTrue(pvmfw_carries_key(image(b'code' + bytes(fake) + KEY + header_only), KEY))


    def test_stock_test_key_modulus_outside_a_blob_fails(self):
        for name, form in (('big-endian', STOCK), ('little-endian', STOCK[::-1]), ('hex', STOCK.hex().encode()),
                           ('base64', base64.b64encode(b'x' + STOCK)), ('base64 lines',
                            b'\n'.join(base64.b64encode(b'xy' + STOCK)[i:i + 64] for i in range(0, 700, 64)))):
            with self.subTest(form=name):
                self.assertFalse(pvmfw_carries_key(image(b'code' + KEY + b'config' + form), KEY))
                self.assertFalse(pvmfw_carries_key(image(b'code' + KEY, padding=b'pad' + form), KEY))
        for modulus in TEST_KEY_MODULI.values():
            self.assertFalse(pvmfw_carries_key(image(b'code' + KEY + modulus), KEY))

    def test_stock_test_key_only_in_the_footer_vbmeta_passes(self):
        self.assertTrue(pvmfw_carries_key(image(b'code' + KEY, footer_key=STOCK), KEY))

    def test_footer_that_does_not_add_up_fails(self):
        binary = b'code' + KEY
        size, vbmeta = len(binary), 640
        for name, fields in (('empty binary', (1, 0, 0, size, vbmeta)),
                             ('binary past the vbmeta', (1, 0, size + 1, size, vbmeta)),
                             ('binary past the file', (1, 0, 1 << 40, 1 << 40, vbmeta)),
                             ('vbmeta past the footer', (1, 0, size, 65536 - 64 - vbmeta + 1, vbmeta)),
                             ('empty vbmeta', (1, 0, size, size, 0)),
                             ('footer version 2', (2, 0, size, size, vbmeta)),
                             ('offset that wraps', (1, 0, size, (1 << 64) - 8, vbmeta))):
            with self.subTest(footer=name):
                self.assertFalse(pvmfw_carries_key(image(binary, fields=fields), KEY))
        self.assertTrue(pvmfw_carries_key(image(binary, fields=(1, 0, size, size, vbmeta)), KEY))


    def test_stock_test_key_as_separated_hex_spaced_base64_or_swapped_words_fails(self):
        def words(value, size, order, inner):
            parts = [value[i:i + size] for i in range(0, len(value), size)]
            return b''.join(p[::inner] for p in parts[::order])
        forms = {
            'C array': ', '.join(f'0x{b:02x}' for b in STOCK).encode(),
            'colons': ':'.join(f'{b:02x}' for b in STOCK).encode(),
            'upper case, spaces': ' '.join(f'0X{b:02X}' for b in STOCK).encode(),
            'C string': ''.join(f'\\x{b:02x}' for b in STOCK).encode(),
            'little-endian hex lines': b'\n'.join(STOCK[::-1].hex().encode()[i:i + 32] for i in range(0, 1024, 32)),
            'base64 with spaces': b' '.join(base64.b64encode(STOCK)[i:i + 4] for i in range(0, 684, 4)),
        }
        for size in (4, 8):
            forms[f'{size}-byte words reversed'] = words(STOCK, size, -1, 1)
            forms[f'{size}-byte words, bytes reversed'] = words(STOCK, size, 1, -1)
            forms[f'{size}-byte words reversed, as hex'] = words(STOCK, size, -1, 1).hex().encode()
        for name, form in forms.items():
            with self.subTest(form=name):
                self.assertFalse(pvmfw_carries_key(image(b'code' + KEY + b'config' + form), KEY))

    def test_bytes_after_the_vbmeta_fail(self):
        self.assertFalse(pvmfw_carries_key(image(b'code' + KEY, trailer=b'x'), KEY))
        self.assertFalse(pvmfw_carries_key(image(b'code' + KEY, trailer=bytes(100) + STOCK), KEY))
        self.assertTrue(pvmfw_carries_key(image(b'code' + KEY, trailer=bytes(100)), KEY))


if __name__ == '__main__':
    unittest.main()
