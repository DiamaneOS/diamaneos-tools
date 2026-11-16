"""The signer's check that pvmfw carries the release AVB key in its binary."""
import struct
import unittest

from diamaneos_tools.signing_native import pvmfw_carries_key

KEY = bytes(range(256)) * 4 + b'pkmdtail'


def image(binary, footer_key=b''):
    """A pvmfw binary padded to a partition with an AVB footer, its vbmeta optionally holding footer_key."""
    vbmeta = b'AVB0' + bytes(636) + footer_key
    data = binary + vbmeta
    data += bytes(65536 - len(data) - 64)
    footer = b'AVBf' + struct.pack('>IIQQQ', 1, 0, len(binary), len(binary), len(vbmeta)) + bytes(28)
    return data + footer


class PvmfwKeyTests(unittest.TestCase):
    def test_key_in_the_binary_passes(self):
        self.assertTrue(pvmfw_carries_key(image(b'pvmfw code' + KEY + b'more code'), KEY))

    def test_other_key_fails(self):
        other = bytes(reversed(KEY))
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code' + other + b'more code'), KEY))

    def test_key_only_in_the_footer_fails(self):
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code' + bytes(len(KEY)), footer_key=KEY), KEY))

    def test_empty_key_fails(self):
        self.assertFalse(pvmfw_carries_key(image(b'pvmfw code'), b''))

    def test_image_without_footer(self):
        self.assertTrue(pvmfw_carries_key(b'pvmfw code' + KEY, KEY))


if __name__ == '__main__':
    unittest.main()
