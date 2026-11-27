"""Conservative capacity bounds for Linux's eight named memory reservations."""
import unittest

from tests.build import test_boot_input_boundary as boundary
from tests.build.test_image_verify import dtb


def reserves(first, count):
    return b' '.join(f'reserve_mem=1M:2M:other{i}'.encode() for i in range(first, first + count))


class NamedReservationsTest(unittest.TestCase):
    setUp = boundary.BootBoundaryTest.setUp
    check = boundary.BootBoundaryTest.check

    def test_pool_plus_seven_other_entries_fits_the_table(self):
        args = reserves(0, 7) + b' reserve_mem=512M:2M:gunyah_vm_pool'
        ok, detail = self.check({'VENDOR_BOOT/vendor_cmdline': args}, reserve=True)
        self.assertTrue(ok, detail)

    def test_eight_other_entries_can_refuse_the_pool(self):
        args = reserves(0, 8) + b' reserve_mem=512M:2M:gunyah_vm_pool'
        ok, detail = self.check({'VENDOR_BOOT/vendor_cmdline': args}, reserve=True)
        self.assertFalse(ok, detail)
        self.assertIn('9 named reserve_mem entries exceed Linux table capacity 8', detail)

    def test_every_packaged_source_counts_even_if_inactive_or_reordered(self):
        overrides = {
            'BOOT/cmdline': reserves(0, 1),
            'VENDOR_BOOT/dtb': dtb({'/': {}, '/inactive': {'bootargs': reserves(1, 3) + b'\0'}}),
            'IMAGES/dtbo.img': dtb({'/': {}, '/fragment@0': {'bootargs': reserves(4, 1) + b'\0'}}),
            'META/kernel_configs.txt': b'CONFIG_CMDLINE="' + reserves(5, 2) + b'"\n',
            'VENDOR_BOOT/vendor_cmdline': b'reserve_mem=512M:2M:gunyah_vm_pool ' + reserves(7, 1),
        }
        ok, detail = self.check(overrides, reserve=True)
        self.assertFalse(ok, detail)
        self.assertIn('9 named reserve_mem entries exceed Linux table capacity 8', detail)

    def test_repeated_other_names_do_not_reduce_the_conservative_bound(self):
        args = (b'reserve_mem=1M:2M:other ' * 8) + b'reserve_mem=512M:2M:gunyah_vm_pool'
        ok, detail = self.check({'VENDOR_BOOT/vendor_cmdline': args}, reserve=True)
        self.assertFalse(ok, detail)
        self.assertIn('table capacity 8', detail)
