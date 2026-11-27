"""Pool accounting uses built-in arguments from the delivered kernel."""
import unittest

from tests.build import test_boot_input_boundary as boundary
from tests.build.test_image_verify import kernel_fixture


class ShippedPoolConfigTest(unittest.TestCase):
    setUp = boundary.BootBoundaryTest.setUp
    check = boundary.BootBoundaryTest.check

    def run_config(self, arguments, extra=b'', metadata=None):
        config = b'CONFIG_BLK_NO_EXTERNAL_PART_SCAN=y\nCONFIG_CMDLINE="' + arguments + b'"\n' + extra
        overrides = {'BOOT/kernel': kernel_fixture(config)}
        if metadata is not None:
            overrides['META/kernel_configs.txt'] = metadata
        return self.check(overrides, reserve=True)

    def test_builtin_reservations_cannot_hide_behind_stale_metadata(self):
        args = b' '.join(f'reserve_mem=1M:2M:extra{i}'.encode() for i in range(8))
        ok, detail = self.run_config(args)
        self.assertFalse(ok, detail)
        self.assertIn('9 named reserve_mem entries exceed Linux table capacity 8', detail)

    def test_builtin_length_cannot_hide_behind_stale_metadata(self):
        ok, detail = self.run_config(b'x=' + b'a' * 2010)
        self.assertFalse(ok, detail)
        self.assertIn('beyond arm64 2047', detail)

    def test_builtin_force_cannot_hide_behind_stale_metadata(self):
        ok, detail = self.run_config(b'console=ttynull', b'CONFIG_CMDLINE_FORCE=y\n')
        self.assertFalse(ok, detail)
        self.assertIn('CONFIG_CMDLINE_FORCE ignores', detail)

    def test_stale_metadata_does_not_supply_the_builtin_arguments(self):
        metadata = b'CONFIG_CMDLINE="x=' + b'a' * 2010 + b'"\nCONFIG_CMDLINE_FORCE=y\n'
        ok, detail = self.run_config(b'console=ttynull', metadata=metadata)
        self.assertTrue(ok, detail)
        self.assertIn('1/8 named reservations', detail)
