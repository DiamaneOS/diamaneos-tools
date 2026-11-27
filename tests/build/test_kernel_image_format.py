"""Kernel security claims require the supported arm64 Image format."""
import gzip
import struct
import unittest

from tests.build import test_boot_input_boundary as boundary
from tests.build.test_image_verify import kernel_fixture, subject


def stored_lz4(payload):
    """A standard LZ4 frame using uncompressed blocks of at most 64 KiB."""
    frame = bytearray(b'\x04\x22\x4d\x18\x60\x40\x82')
    for start in range(0, len(payload), 65536):
        block = payload[start:start + 65536]
        frame += struct.pack('<I', 0x80000000 | len(block)) + block
    return bytes(frame) + bytes(4)


class KernelImageFormatTest(unittest.TestCase):
    setUp = boundary.BootBoundaryTest.setUp
    check = boundary.BootBoundaryTest.check

    def test_raw_and_gzip_arm64_images_are_recognised(self):
        image = kernel_fixture()
        self.assertEqual(b'ARM\x64', image[56:60])
        self.assertEqual(image, subject.kernel_image(image))
        self.assertEqual(image, subject.kernel_image(gzip.compress(image, mtime=0)))
        self.assertTrue(self.check({'BOOT/kernel': image})[0])

    def test_readable_literals_in_unsupported_formats_do_not_qualify(self):
        image = kernel_fixture()
        for kernel in [image[64:], stored_lz4(image), gzip.compress(stored_lz4(image), mtime=0),
                       image[:56] + b'BAD!' + image[60:]]:
            with self.subTest(prefix=kernel[:8]):
                ok, detail = self.check({'BOOT/kernel': kernel})
                self.assertFalse(ok, detail)
                self.assertIn('unsupported kernel encoding: expected arm64 Image header', detail)

    def test_short_data_and_nested_gzip_do_not_qualify(self):
        image = kernel_fixture()
        for kernel in [b'ARM\x64', image[:60], gzip.compress(gzip.compress(image, mtime=0), mtime=0)]:
            with self.subTest(prefix=kernel[:8]), self.assertRaisesRegex(ValueError, 'arm64 Image header'):
                subject.kernel_image(kernel)
