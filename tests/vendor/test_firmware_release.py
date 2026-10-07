"""The firmware release table fwrelease checks the firmware partitions against."""
import copy
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import firmware_release
from diamaneos_tools.vendor import VendorError

ROOT = Path(__file__).resolve().parents[2]
CHECKED = ['abl', 'aop', 'aop_config', 'bluetooth', 'cpucp', 'cpucp_dtb', 'devcfg', 'dsp', 'featenabler', 'hyp',
           'imagefv', 'keymaster', 'modem', 'multiimgoem', 'qupfw', 'shrm', 'tz', 'uefi', 'uefisecapp', 'xbl',
           'xbl_config', 'xbl_ramdump']
LINE = re.compile(r'image [a-z0-9_-]{1,32} [0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5} [1-9][0-9]* [0-9a-f]{64}')


class FirmwareReleaseTableTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT / 'config/fp6-firmware-inventory.json').read_text())
        self.config = json.loads((ROOT / 'config/fp6-build.json').read_text())['firmware_release']

    def table(self):
        return firmware_release.table(self.inventory, self.config)

    def firmware(self, build):
        return self.inventory['releases'][build]['images']['firmware']

    def test_committed_table_checks_the_22_ab_partitions_of_both_releases(self):
        data = self.table()
        self.assertEqual(data, self.table())
        lines = data.decode().splitlines()
        self.assertTrue(all(LINE.fullmatch(line) for line in lines))
        self.assertEqual(44, len(lines))
        rows = [line.split() for line in lines]
        # Newest release first, partitions in name order.
        self.assertEqual(['16.111.0'] * 22 + ['16.100.0'] * 22, [row[2] for row in rows])
        for release in ('16.111.0', '16.100.0'):
            names = [row[1] for row in rows if row[2] == release]
            self.assertEqual(CHECKED, names)
            # 266,677,504 bytes: modem 186 MB, dsp 67 MB, the rest 13.6 MB.
            self.assertEqual(266677504, sum(int(row[3]) for row in rows if row[2] == release))
        tz = self.firmware('FP6.QREL.16.111.0')['tz.mbn']
        self.assertIn(f'image tz 16.111.0 {tz["bytes"]} {tz["sha256"]}', lines)
        # Every checked image differs between the two releases, so each
        # partition alone tells them apart.
        for name in CHECKED:
            self.assertEqual(2, len({row[4] for row in rows if row[1] == name}), name)
        self.assertLess(len(data), firmware_release.MAX_TABLE_BYTES)

    def test_every_firmware_image_is_checked_or_excluded_with_a_reason(self):
        images = set(self.firmware('FP6.QREL.16.111.0'))
        excluded = set(self.config['not_checked'])
        self.assertEqual({'pvmfw.img', 'vm-bootsys.img', 'vm-persist.img', 'logfs_ufs_8mb.bin', 'study.img',
                          'storsec.mbn', 'tools.fv'}, excluded)
        self.assertEqual(29, len(images))
        self.assertEqual(22, len(images - excluded))
        self.assertEqual(['vm-bootsys.img'], [n for n, e in self.config['not_checked'].items() if e.get('identical')])

    def test_single_copy_image_cannot_be_checked(self):
        del self.config['not_checked']['storsec.mbn']
        with self.assertRaisesRegex(VendorError, 'A/B'):
            self.table()

    def test_sparse_image_cannot_be_checked(self):
        del self.config['not_checked']['vm-bootsys.img']
        with self.assertRaisesRegex(VendorError, 'sparse'):
            self.table()

    def test_releases_must_have_the_same_images(self):
        del self.firmware('FP6.QREL.16.100.0')['tz.mbn']
        with self.assertRaisesRegex(VendorError, 'different firmware images'):
            self.table()
        inventory = copy.deepcopy(self.inventory)
        inventory['releases']['FP6.QREL.16.100.0']['images']['firmware']['new.mbn'] = {
            'bytes': 1, 'sha256': '0' * 64, 'fastboot_partitions': ['new_a', 'new_b']}
        with self.assertRaisesRegex(VendorError, 'different firmware images'):
            firmware_release.table(inventory, self.config)

    def test_identical_exclusion_must_stay_identical(self):
        self.firmware('FP6.QREL.16.100.0')['vm-bootsys.img']['sha256'] = '0' * 64
        with self.assertRaisesRegex(VendorError, 'identical'):
            self.table()

    def test_exclusions_need_a_reason_and_an_inventory_image(self):
        for change in ({'reason': ''}, {'reason': '  '}, {}, {'reason': 'x', 'identical': 'yes'}, {'reason': 'x', 'identical': 1},
                       {'reason': 'x', 'checked': False}):
            config = copy.deepcopy(self.config)
            config['not_checked']['tools.fv'] = change
            with self.subTest(change=change), self.assertRaises(VendorError):
                firmware_release.table(self.inventory, config)
        config = copy.deepcopy(self.config)
        config['not_checked']['missing.img'] = {'reason': 'x'}
        with self.assertRaisesRegex(VendorError, 'not in the inventory'):
            firmware_release.table(self.inventory, config)

    def test_invalid_entries_are_rejected(self):
        for name, change in [('tz.mbn', {'sha256': 'A' * 64}), ('tz.mbn', {'sha256': '0' * 63}),
                             ('tz.mbn', {'bytes': 0}), ('tz.mbn', {'bytes': '4031136'}),
                             ('tz.mbn', {'bytes': True}),
                             ('tz.mbn', {'bytes': firmware_release.MAX_IMAGE_BYTES + 1}),
                             ('tz.mbn', {'fastboot_partitions': ['tz_a']}),
                             ('tz.mbn', {'fastboot_partitions': ['tz_b', 'tz_a']}),
                             ('tz.mbn', {'fastboot_partitions': ['TZ_a', 'TZ_b']}),
                             ('tz.mbn', {'fastboot_partitions': ['hyp_a', 'hyp_b']}),
                             ('tz.mbn', {'sparse': True})]:
            inventory = copy.deepcopy(self.inventory)
            inventory['releases']['FP6.QREL.16.100.0']['images']['firmware'][name].update(change)
            with self.subTest(change=change), self.assertRaises(VendorError):
                firmware_release.table(inventory, self.config)

    def test_release_names_must_carry_a_version(self):
        for build in ('FP6.QREL.16.111', 'FP6.QREL.16.111.0.1', 'FP6.QREL.v16.111.0', 'FP5.QREL.16.111.0',
                      'FP6.QREL.123456.1.0'):
            inventory = copy.deepcopy(self.inventory)
            inventory['releases'][build] = inventory['releases'].pop('FP6.QREL.16.100.0')
            with self.subTest(build=build), self.assertRaises(VendorError):
                firmware_release.table(inventory, self.config)
        inventory = copy.deepcopy(self.inventory)
        inventory['releases']['FP6.QREL.16.111.00'] = inventory['releases'].pop('FP6.QREL.16.100.0')
        with self.assertRaisesRegex(VendorError, 'same version'):
            firmware_release.table(inventory, self.config)
        inventory['releases'] = {}
        with self.assertRaises(VendorError):
            firmware_release.table(inventory, self.config)

    def test_newer_release_sorts_first_by_number(self):
        inventory = copy.deepcopy(self.inventory)
        inventory['releases']['FP6.QREL.16.99.0'] = inventory['releases'].pop('FP6.QREL.16.111.0')
        inventory['releases']['FP6.QREL.16.99.0']['images']['firmware']['tz.mbn']['sha256'] = 'f' * 64
        releases = [line.split()[2] for line in firmware_release.table(inventory, self.config).decode().splitlines()]
        self.assertEqual(['16.100.0'] * 22 + ['16.99.0'] * 22, releases)


if __name__ == '__main__':
    unittest.main()
