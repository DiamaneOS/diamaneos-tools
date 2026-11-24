"""Bind filesystem VM isolation evidence to the kernel in the delivered boot image."""
import gzip
from pathlib import Path
import struct
import tempfile
import unittest

from tests.build.test_image_verify import Harness, arm64_image, boot_parameter_files, kernel_fixture, scan_rule, subject


class ShippedKernelTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def check(self, files):
        h = Harness(self.root, {**files, 'SYSTEM_EXT/bin/fsvmd': b'x'})
        self.addCleanup(h.tf.close)
        return subject.rule_fs_in_vm_coupling(scan_rule(), h)

    def test_positive_has_a_nonempty_packed_kernel(self):
        files = boot_parameter_files()
        image = files['IMAGES/boot.img']
        self.assertGreater(struct.unpack_from('<I', image, 8)[0], 0)
        self.assertEqual(files['BOOT/kernel'], subject.boot_kernel(image))
        ok, detail = self.check(files)
        self.assertTrue(ok, detail)

    def test_only_staged_and_packed_kernel_disagree(self):
        files = boot_parameter_files()
        other = boot_parameter_files(**{'BOOT/kernel': kernel_fixture(extra=b'external_part_scan\0')})
        files['IMAGES/boot.img'] = other['IMAGES/boot.img']
        ok, detail = self.check(files)
        self.assertFalse(ok, detail)
        self.assertIn('BOOT/kernel differs from IMAGES/boot.img kernel payload', detail)

    def test_embedded_option_overrules_clean_metadata(self):
        for config in [b'# CONFIG_BLK_NO_EXTERNAL_PART_SCAN is not set\n',
                       b'CONFIG_BLK_NO_EXTERNAL_PART_SCAN=m\n', b'CONFIG_IKCONFIG=y\n']:
            with self.subTest(config=config):
                files = boot_parameter_files(**{'BOOT/kernel': kernel_fixture(config)})
                ok, detail = self.check(files)
                self.assertFalse(ok, detail)
                self.assertIn('CONFIG_BLK_NO_EXTERNAL_PART_SCAN=y is required', detail)
                self.assertIn('embedded', detail)

    def test_missing_or_malformed_embedded_config_is_refused(self):
        valid = kernel_fixture()
        duplicate = b'CONFIG_BLK_NO_EXTERNAL_PART_SCAN=y\n' * 2
        for kernel in [arm64_image(b'Linux version 6.18.39-test (test)'), valid[:-1], valid + valid,
                       kernel_fixture(duplicate),
                       valid.replace(b'IKCFG_ST\x1f\x8b', b'IKCFG_STxx', 1)]:
            with self.subTest(kernel=kernel):
                ok, detail = self.check(boot_parameter_files(**{'BOOT/kernel': kernel}))
                self.assertFalse(ok, detail)
                self.assertIn('embedded', detail)

    def test_matched_shipped_kernel_cannot_contain_legacy_override(self):
        files = boot_parameter_files(**{'BOOT/kernel': kernel_fixture(extra=b'external_part_scan\0')})
        ok, detail = self.check(files)
        self.assertFalse(ok, detail)
        self.assertIn('IMAGES/boot.img kernel still contains', detail)

    def test_gzip_shipped_kernel_uses_its_embedded_config(self):
        for enabled in [True, False]:
            config = b'CONFIG_BLK_NO_EXTERNAL_PART_SCAN=' + (b'y' if enabled else b'n') + b'\n'
            files = boot_parameter_files(**{'BOOT/kernel': gzip.compress(kernel_fixture(config), mtime=0)})
            ok, detail = self.check(files)
            self.assertEqual(enabled, ok, detail)

    def test_empty_and_truncated_packed_kernel_are_refused(self):
        for size in [0, 1 << 20]:
            files = boot_parameter_files()
            image = bytearray(files['IMAGES/boot.img'])
            struct.pack_into('<I', image, 8, size)
            files['IMAGES/boot.img'] = bytes(image)
            ok, detail = self.check(files)
            self.assertFalse(ok, detail)
            self.assertIn('IMAGES/boot.img', detail)
