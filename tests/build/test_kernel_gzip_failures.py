"""Damaged compressed kernels and IKCONFIG remain ordinary verification failures."""
import gzip
import unittest

from tests.build import test_boot_input_boundary as boundary
from tests.build.test_image_verify import Harness, boot_parameter_files, kernel_fixture, scan_rule, subject


def broken_deflate():
    return gzip.compress(b'x', mtime=0)[:10] + b'\x07' + bytes(8)


class KernelGzipFailuresTest(unittest.TestCase):
    setUp = boundary.BootBoundaryTest.setUp

    def assert_rule_failure_and_continuation(self, kernel, message):
        files = boot_parameter_files(**{'BOOT/kernel': kernel, 'SYSTEM_EXT/bin/fsvmd': b'x'})
        h = Harness(self.root, files, packaging=self.packaging)
        self.addCleanup(h.tf.close)
        for name, rule, check in [('scan', scan_rule(), subject.rule_fs_in_vm_coupling),
                                  ('pool', self.reserve, subject.rule_vm_pool_reserve)]:
            with self.subTest(rule=name):
                report = object.__new__(subject.Verification)
                report.results = []
                report.add(name, 'damaged gzip', lambda: check(rule, h))
                report.add('following', 'verification continues', lambda: (True, 'ran'))
                self.assertEqual(['FAIL', 'PASS'], [row['status'] for row in report.results])
                self.assertIn(message, report.results[0]['detail'])
                self.assertIn('invalid block type', report.results[0]['detail'])

    def test_corrupt_embedded_deflate_records_a_failure_and_keeps_running(self):
        kernel = kernel_fixture()
        start, end = kernel.index(b'IKCFG_ST') + 8, kernel.index(b'IKCFG_ED')
        self.assert_rule_failure_and_continuation(kernel[:start] + broken_deflate() + kernel[end:],
                                                  'embedded IKCONFIG')

    def test_corrupt_outer_deflate_records_a_failure_and_keeps_running(self):
        self.assert_rule_failure_and_continuation(broken_deflate(), 'kernel gzip')
