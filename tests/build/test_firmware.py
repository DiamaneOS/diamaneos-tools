import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import firmware

RELEASE = 'FP6.QREL.16.111.0'
PREVIOUS = 'FP6.QREL.16.100.0'
# A small stand-in for a factory package: the same kinds of entries as the
# real one (A/B, single and mixed partitions, a stock pvmfw, state partitions).
FIRMWARE = {
    'abl.elf': ['abl_a', 'abl_b'],
    'logfs_ufs_8mb.bin': ['logfs'],
    'NON-HLOS.bin': ['modem_a', 'modem_b'],
    'pvmfw.img': ['pvmfw_a', 'pvmfw_b'],
    'storsec.mbn': ['storsec'],
    'study.img': ['study', 'studybk_a', 'studybk_b'],
    'vm-persist.img': ['vm-persist'],
    'xbl_s.melf': ['xbl_a', 'xbl_b'],
}
OS = {'boot.img': ['boot_a', 'boot_b'], 'super.img': ['super']}
WIPE = {'userdata.img': ['userdata']}
ORDER = ['abl_a', 'abl_b', 'logfs', 'modem_a', 'modem_b', 'pvmfw_a', 'pvmfw_b', 'storsec', 'study', 'studybk_a',
         'studybk_b', 'vm-persist', 'xbl_a', 'xbl_b', 'boot_a', 'boot_b', 'super', 'userdata']
RESET_BYTES = 8192


def synthetic_firmware(root: Path, release=RELEASE, content=None, misc_sectors=256, reset_sectors=None):
    """A factory package, its inventory and a firmware policy for tests.

    Returns (inventory, policy, archive path, {image: bytes})."""
    top = f'{release}.20260831102426_WS1Q-factory'
    data = {name: (content or {}).get(name, f'{release} {name}'.encode())
            for name in list(FIRMWARE) + list(OS) + list(WIPE)}
    archive = root / f'{top}.zip'
    sectors = {'modemst1': reset_sectors or RESET_BYTES // 4096, 'modemst2': RESET_BYTES // 4096, 'misc': misc_sectors}
    rawprogram = ''.join(f'<program num_partition_sectors="{count}" label="{label}" '
                         f'SECTOR_SIZE_IN_BYTES="4096" filename=""/>' for label, count in sectors.items())
    with zipfile.ZipFile(archive, 'w') as bundle:
        for name, value in data.items():
            bundle.writestr(f'{top}/images/{name}', value)
        bundle.writestr(f'{top}/images/rawprogram5.xml', f'<data>{rawprogram}</data>')

    def identities(names):
        return {n: {'bytes': len(data[n]), 'sha256': hashlib.sha256(data[n]).hexdigest(),
                    'fastboot_partitions': names[n]} for n in names}

    order = []
    for partition in ORDER:
        image = next(n for n, parts in {**FIRMWARE, **OS, **WIPE}.items() if partition in parts)
        order.append({'partition': partition, 'image': image})
    entry = {'archive': {'filename': archive.name, 'bytes': archive.stat().st_size,
                         'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()},
             'fastboot_versions': None,
             'images': {'firmware': identities(FIRMWARE), 'os': identities(OS), 'wipe': identities(WIPE)}}
    older = copy.deepcopy(entry)
    older['archive'] = {'filename': 'older.zip', 'bytes': 1, 'sha256': '0' * 64}
    inventory = {'schema_version': 1, 'selected_build': release, 'previous_build': PREVIOUS,
                 'stock_flash_script': {'order': order}, 'releases': {release: entry, PREVIOUS: older}}
    policy = copy.deepcopy(json.loads((ROOT / 'config/fp6-build.json').read_text())['firmware'])
    for reset in policy['reset'].values():
        reset['bytes'] = RESET_BYTES
    return inventory, policy, archive, data


class CommittedInventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT / 'config/fp6-firmware-inventory.json').read_text())
        self.config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        self.plan = firmware.plan(self.inventory, self.config['firmware'])

    def test_firmware_comes_from_the_release_of_the_vendor_files(self):
        recipe = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_text())
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        self.assertEqual(recipe['stock_build'], self.plan['release'])
        self.assertEqual(environment['device_inputs']['selected_stock_build'], self.plan['release'])
        archive = self.inventory['releases'][self.plan['release']]['archive']
        self.assertEqual((recipe['archive_sha256'], recipe['archive_bytes']), (archive['sha256'], archive['bytes']))

    def test_every_stock_firmware_image_ships_except_the_stock_pvmfw(self):
        stock = self.inventory['releases'][self.plan['release']]['images']['firmware']
        self.assertEqual(set(stock) - {'pvmfw.img'}, set(self.plan['images']))
        for name, identity in self.plan['images'].items():
            self.assertEqual((stock[name]['bytes'], stock[name]['sha256']), (identity['bytes'], identity['sha256']))

    def test_steps_follow_the_stock_order_with_both_slots(self):
        steps = self.plan['steps']
        stock = [e['partition'] for e in self.inventory['stock_flash_script']['order']
                 if e['image'] in self.plan['images']]
        self.assertEqual(stock, [s['partition'] for s in steps])
        self.assertEqual(('abl_a', 'abl_b'), (steps[0]['partition'], steps[1]['partition']))
        self.assertEqual('xbl_ramdump_b', steps[-1]['partition'])
        partitions = [s['partition'] for s in steps]
        for index, name in enumerate(partitions):
            if name.endswith('_a'):
                self.assertEqual(name[:-2] + '_b', partitions[index + 1])
        self.assertEqual({'logfs', 'vm-persist'}, {s['partition'] for s in steps if s.get('wipe_only')})
        self.assertNotIn('pvmfw_a', partitions)
        self.assertEqual(3, len([s for s in steps if s['image'] == 'study.img']))

    def test_an_update_carries_the_ab_firmware_but_no_state_partition(self):
        policy = self.config['firmware']
        update = firmware.update_partitions(self.plan, policy)
        self.assertEqual(['abl', 'aop', 'aop_config', 'bluetooth', 'cpucp', 'cpucp_dtb', 'devcfg', 'dsp',
                          'featenabler', 'hyp', 'imagefv', 'keymaster', 'modem', 'multiimgoem', 'qupfw', 'shrm',
                          'tz', 'uefi', 'uefisecapp', 'vm-bootsys', 'xbl', 'xbl_config', 'xbl_ramdump'],
                         list(update))
        self.assertEqual(('NON-HLOS.bin', 'xbl_s.melf'), (update['modem'], update['xbl']))
        # Fairphone's study partitions hold run-time state: flashed, never updated.
        self.assertEqual(['study.img'], list(policy['update_skips']))
        self.assertIn('studybk', firmware.update_partitions(self.plan, dict(policy, update_skips={})))
        device_ab = {'boot', 'dtbo', 'init_boot', 'odm', 'product', 'pvmfw', 'recovery', 'system', 'system_dlkm',
                     'system_ext', 'vbmeta', 'vbmeta_system', 'vendor', 'vendor_boot', 'vendor_dlkm'}
        self.assertFalse(device_ab & set(update))

    def test_no_shipped_image_lowers_the_qualcomm_anti_rollback_version(self):
        self.assertEqual([], firmware.anti_rollback_problems(self.inventory, self.plan['release']))
        changed = copy.deepcopy(self.inventory)
        changed['releases'][self.plan['release']]['images']['firmware']['tz.mbn']['signing'][
            'oem_anti_rollback_version'] = 0
        self.assertIn('tz.mbn', ' '.join(firmware.anti_rollback_problems(changed, self.plan['release'])))

    def test_reset_images_match_the_stock_partition_table(self):
        reset = self.config['firmware']['reset']
        self.assertEqual({'modemst1', 'modemst2'}, set(reset))
        for name, image in reset.items():
            self.assertEqual((name + '.img', 'zeros', name, 2560 * 4096),
                             (image['image'], image['kind'], image['partition_label'], image['bytes']))
        self.assertTrue(self.config['firmware']['validated'])

    def test_no_release_records_fastboot_versions_yet(self):
        for release in self.inventory['releases'].values():
            self.assertIsNone(release['fastboot_versions'])
        self.assertEqual({PREVIOUS: None, RELEASE: None}, self.plan['fastboot_versions'])


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inventory, self.policy, self.archive, self.data = synthetic_firmware(self.root)

    def test_plan_of_a_synthetic_release(self):
        plan = firmware.plan(self.inventory, self.policy)
        self.assertEqual(RELEASE, plan['release'])
        self.assertEqual(set(FIRMWARE) - {'pvmfw.img'}, set(plan['images']))
        self.assertEqual(['abl_a', 'abl_b', 'logfs', 'modem_a', 'modem_b', 'storsec', 'study', 'studybk_a',
                          'studybk_b', 'vm-persist', 'xbl_a', 'xbl_b'], [s['partition'] for s in plan['steps']])
        self.assertEqual({'abl': 'abl.elf', 'modem': 'NON-HLOS.bin', 'xbl': 'xbl_s.melf'},
                         firmware.update_partitions(plan, self.policy))
        self.assertEqual(['abl', 'modem', 'studybk', 'xbl'],
                         list(firmware.update_partitions(plan, dict(self.policy, update_skips={}))))
        self.assertRaises(firmware.FirmwareError, firmware.plan, self.inventory,
                          dict(self.policy, update_skips={'missing.img': 'state'}))

    def test_incomplete_or_inconsistent_inventories_are_refused(self):
        def broken(change):
            inventory = copy.deepcopy(self.inventory)
            change(inventory)
            return inventory
        images = lambda i: i['releases'][RELEASE]['images']['firmware']  # noqa: E731
        order = lambda i: i['stock_flash_script']['order']  # noqa: E731
        cases = {
            'not written by the stock flash script': lambda i: images(i).update(
                {'extra.bin': {'bytes': 1, 'sha256': 'a' * 64, 'fastboot_partitions': ['extra']}}),
            'abl_b': lambda i: order(i).remove({'partition': 'abl_b', 'image': 'abl.elf'}),
            'more than once': lambda i: order(i).append({'partition': 'abl_a', 'image': 'abl.elf'}),
            'unknown image': lambda i: order(i).insert(0, {'partition': 'foo', 'image': 'foo.bin'}),
            'lists partitions': lambda i: images(i)['abl.elf'].update(fastboot_partitions=['abl_a']),
        }
        for message, change in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(firmware.FirmwareError, message):
                    firmware.plan(broken(change), self.policy)
        policy = copy.deepcopy(self.policy)
        policy['wipe_only']['missing.bin'] = 'typo'
        with self.assertRaisesRegex(firmware.FirmwareError, 'missing.bin'):
            firmware.plan(self.inventory, policy)
        with self.assertRaisesRegex(firmware.FirmwareError, 'not in the inventory'):
            firmware.plan(self.inventory, self.policy, release='FP6.QREL.16.120.0')

    def test_release_numbers_compare_numerically(self):
        self.assertLess(firmware.release_key('FP6.QREL.16.100.0'), firmware.release_key('FP6.QREL.16.111.0'))
        self.assertLess(firmware.release_key('FP6.QREL.16.99.9'), firmware.release_key('FP6.QREL.16.100.0'))
        self.assertLess(firmware.release_key('FP6.QREL.16.111.0'), firmware.release_key('FP6.QREL.17.1.0'))
        for bad in ('16.111.0', 'FP6.QREL.16.111', 'FP5.QREL.16.111.0', 'FP6.QREL.16.111.0 ', ''):
            with self.subTest(bad):
                with self.assertRaises(firmware.FirmwareError):
                    firmware.release_key(bad)


class StageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inventory, self.policy, self.archive, self.data = synthetic_firmware(self.root)
        self.plan = firmware.plan(self.inventory, self.policy)
        self.out = self.root / 'out'
        self.out.mkdir()

    def test_stage_copies_exact_stock_bytes(self):
        digests = firmware.stage(self.archive, self.inventory, self.plan, self.out)
        self.assertEqual(set(self.plan['images']), {p.name for p in self.out.iterdir()})
        for name, identity in self.plan['images'].items():
            self.assertEqual(self.data[name], (self.out / name).read_bytes())
            self.assertEqual(identity['sha256'], digests[name])
        self.assertFalse((self.out / 'pvmfw.img').exists())

    def test_another_package_is_refused(self):
        inventory = copy.deepcopy(self.inventory)
        inventory['releases'][RELEASE]['archive']['sha256'] = '0' * 64
        with self.assertRaisesRegex(firmware.FirmwareError, 'not the factory package of FP6.QREL.16.111.0'):
            firmware.stage(self.archive, inventory, self.plan, self.out)
        self.assertEqual([], list(self.out.iterdir()))

    def test_an_image_that_differs_from_its_pin_is_refused(self):
        plan = copy.deepcopy(self.plan)
        plan['images']['abl.elf']['sha256'] = '0' * 64
        with self.assertRaisesRegex(firmware.FirmwareError, 'abl.elf differs from the inventory'):
            firmware.stage(self.archive, self.inventory, plan, self.out)

    def test_missing_member_and_existing_file_are_refused(self):
        plan = copy.deepcopy(self.plan)
        plan['images']['absent.bin'] = {'bytes': 1, 'sha256': '0' * 64}
        with self.assertRaisesRegex(firmware.FirmwareError, 'lacks absent.bin'):
            firmware.stage(self.archive, self.inventory, plan, self.out)
        for p in self.out.iterdir():
            p.unlink()
        (self.out / 'abl.elf').write_bytes(b'left over')
        with self.assertRaises((firmware.FirmwareError, FileExistsError)):
            firmware.stage(self.archive, self.inventory, self.plan, self.out)


class CheckSetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inventory, self.policy, self.archive, self.data = synthetic_firmware(self.root)
        self.plan = firmware.plan(self.inventory, self.policy)
        self.out = self.root / 'out'
        self.out.mkdir()
        firmware.stage(self.archive, self.inventory, self.plan, self.out)
        reset = {}
        for name, image in self.policy['reset'].items():
            (self.out / image['image']).write_bytes(bytes(image['bytes']))
            reset[name] = {'file': image['image'], 'sha256': hashlib.sha256(bytes(image['bytes'])).hexdigest()}
        self.record = firmware.record(self.plan, self.inventory, reset)

    def problems(self, record=None):
        return firmware.check_set(self.out, record or self.record, self.inventory, self.policy, RELEASE)

    def test_a_complete_exact_set_passes(self):
        self.assertEqual([], self.problems())
        self.assertEqual(RELEASE, self.record['release'])
        self.assertEqual(self.inventory['releases'][RELEASE]['archive']['filename'], self.record['archive']['file'])

    def test_changed_missing_or_extra_images_fail(self):
        (self.out / 'abl.elf').chmod(0o640)
        (self.out / 'abl.elf').write_bytes(b'changed')
        self.assertIn('abl.elf differs from the inventory', ' '.join(self.problems()))
        (self.out / 'abl.elf').unlink()
        self.assertIn('abl.elf is missing', ' '.join(self.problems()))
        record = copy.deepcopy(self.record)
        record['images']['extra.bin'] = '0' * 64
        self.assertIn('extra.bin', ' '.join(self.problems(record)))

    def test_changed_steps_release_or_reset_fail(self):
        record = copy.deepcopy(self.record)
        record['steps'] = [s for s in record['steps'] if s['partition'] != 'xbl_b']
        self.assertIn('flash steps differ', ' '.join(self.problems(record)))
        record = copy.deepcopy(self.record)
        record['release'] = PREVIOUS
        self.assertIn('FP6.QREL.16.100.0', ' '.join(self.problems(record)))
        self.assertIn('vendor files come from FP6.QREL.16.100.0',
                      ' '.join(firmware.check_set(self.out, self.record, self.inventory, self.policy, PREVIOUS)))
        (self.out / 'modemst1.img').write_bytes(b'\1' + bytes(RESET_BYTES - 1))
        self.assertIn('modemst1 image is not the declared zeros', ' '.join(self.problems()))


if __name__ == '__main__':
    unittest.main()
