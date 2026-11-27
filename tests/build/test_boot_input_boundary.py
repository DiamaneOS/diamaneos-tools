"""Boot-input regressions for the compile-time filesystem VM boundary."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.build.test_image_verify import Harness, ROOT, boot_parameter_files, dtb, kernel_fixture, scan_rule, subject


class BootBoundaryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        self.reserve = next(r for r in rules if r['id'] == 'protected-vm-memory-pool')
        self.packaging = json.loads((ROOT / 'config/fp6-kernel-packaging.json').read_text())

    def check(self, overrides=None, reserve=False, daemon=True):
        files = boot_parameter_files(**{
            'META/kernel_configs.txt': b'CONFIG_BLK_NO_EXTERNAL_PART_SCAN=y\n'
                                       b'CONFIG_CMDLINE="console=ttynull bootconfig"\nCONFIG_CMDLINE_EXTEND=y\n',
            **({'SYSTEM_EXT/bin/fsvmd': b'x'} if daemon else {}), **(overrides or {})})
        h = Harness(self.root, files, packaging=self.packaging)
        self.addCleanup(h.tf.close)
        return (subject.rule_vm_pool_reserve(self.reserve, h) if reserve
                else subject.rule_fs_in_vm_coupling(scan_rule(), h))

    def test_medium_1_byte_160_does_not_hide_the_override(self):
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_cmdline': b'unrelated\xa0external_part_scan=1'})[0])

    def test_medium_2_childless_bootconfig_group_does_not_hide_the_override(self):
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_bootconfig': b'kernel { external_part_scan {} }\n'})[0])

    def test_medium_3_quote_state_at_the_join_does_not_hide_the_override(self):
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_bootconfig': b'kernel.prefix=\'"\';\n',
                                     'VENDOR_BOOT/vendor_cmdline': b'payload="junk external_part_scan=1'})[0])

    def test_raw_search_includes_non_bootargs_properties_and_trailing_bytes(self):
        for name, value in [
            ('VENDOR_BOOT/dtb', dtb({'/': {'unrelated': b'external_part_scan=1\0'}})),
            ('IMAGES/dtbo.img', dtb({'/': {'unrelated': b'external-part-scan\0'}})),
            ('BOOT/cmdline', b'\0external_part_scan'),
            ('VENDOR_BOOT/vendor_bootconfig', b'# external_part_scan=0\n'),
        ]:
            with self.subTest(name=name):
                self.assertFalse(self.check({name: value})[0])

    def test_scan_requires_the_compile_time_option_and_no_registered_override(self):
        self.assertTrue(self.check()[0])
        for override in [
            {'META/kernel_configs.txt': b'# CONFIG_BLK_NO_EXTERNAL_PART_SCAN is not set\nCONFIG_CMDLINE=""\n'},
            {'BOOT/kernel': kernel_fixture(extra=b'external_part_scan\0')},
        ]:
            with self.subTest(override=override):
                self.assertFalse(self.check(override)[0])

    def test_low_4_reserve_beyond_2047_data_bytes_fails(self):
        token = b'reserve_mem=512M:2M:gunyah_vm_pool'
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_cmdline': b'x=' + b'a' * 2010 + b' ' + token}, reserve=True)[0])

    def test_low_4_builtin_prefix_uses_the_same_budget(self):
        config = b'CONFIG_CMDLINE="x=' + b'a' * 2010 + b'"\nCONFIG_CMDLINE_EXTEND=y\n'
        self.assertFalse(self.check({'META/kernel_configs.txt': config}, reserve=True)[0])

    def test_low_4_bootconfig_quote_cannot_swallow_the_reserve(self):
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_bootconfig': b'kernel.prefix=\'"\';\n'}, reserve=True)[0])

    def test_empty_boot_input_still_has_a_separator_budget(self):
        token = b'reserve_mem=512M:2M:gunyah_vm_pool'
        prefix = b'p=abc'
        vendor = b'x=' + b'a' * (2047 - len(prefix) - 1 - 3 - len(token)) + b' ' + token
        self.assertFalse(self.check({
            'META/kernel_configs.txt': b'CONFIG_CMDLINE="' + prefix + b'"\nCONFIG_CMDLINE_EXTEND=y\n',
            'VENDOR_BOOT/dtb': dtb({'/': {}}), 'VENDOR_BOOT/vendor_cmdline': vendor}, reserve=True)[0])

    def test_packaged_whitespace_cannot_move_the_reserve_past_the_bound(self):
        token = b'reserve_mem=512M:2M:gunyah_vm_pool'
        image = bytearray(boot_parameter_files(**{'VENDOR_BOOT/vendor_cmdline': token})['IMAGES/vendor_boot.img'])
        field = b' ' * 1980 + token
        image[28:2076] = field + bytes(2048 - len(field))
        self.assertFalse(self.check({'VENDOR_BOOT/vendor_cmdline': token,
                                     'IMAGES/vendor_boot.img': bytes(image)}, reserve=True)[0])

    def test_reserve_token_boundary_counts_builtin_prefix_and_nul(self):
        token = b'reserve_mem=512M:2M:gunyah_vm_pool'
        prefix = b'p=abc'
        vendor = b'x=' + b'a' * (2047 - len(prefix) - 2 - 3 - len(token)) + b' ' + token
        overrides = {'META/kernel_configs.txt': b'CONFIG_CMDLINE="' + prefix + b'"\nCONFIG_CMDLINE_EXTEND=y\n',
                     'VENDOR_BOOT/dtb': dtb({'/': {}}), 'VENDOR_BOOT/vendor_cmdline': vendor}
        ok, detail = self.check(overrides, reserve=True)
        self.assertTrue(ok, detail)
        self.assertIn('2047/2047', detail)
        overrides['VENDOR_BOOT/vendor_cmdline'] = b'x' + vendor
        self.assertFalse(self.check(overrides, reserve=True)[0])

    def test_reserve_rejects_lexical_state_from_any_input(self):
        for source, value in [
            ('BOOT/cmdline', b'prefix="'),
            ('VENDOR_BOOT/dtb', dtb({'/': {}, '/chosen': {'bootargs': b'prefix="\0'}})),
            ('VENDOR_BOOT/vendor_bootconfig', b'kernel { prefix {} }\n'),
            ('META/kernel_configs.txt', b'CONFIG_CMDLINE="prefix=\\\""\nCONFIG_CMDLINE_EXTEND=y\n'),
            ('VENDOR_BOOT/vendor_cmdline', b'unrelated\xa0reserve_mem=512M:2M:gunyah_vm_pool'),
        ]:
            with self.subTest(source=source):
                self.assertFalse(self.check({source: value}, reserve=True)[0])

    def test_low_5_no_daemon_profile_is_not_accepted(self):
        for node in ['/chosen', '/unrelated']:
            with self.subTest(node=node):
                self.assertFalse(self.check({'VENDOR_BOOT/dtb': dtb({
                    '/': {}, node: {'bootargs': b'external_part_scan=1\0'}})}, daemon=False)[0])

