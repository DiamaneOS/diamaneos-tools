import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw, firmware, image_package
from tests.build.test_firmware import synthetic_firmware

FSTAB = '''/dev/block/by-name/metadata /metadata f2fs noatime wait,check,formattable,first_stage_mount
/dev/block/bootdevice/by-name/userdata /data f2fs noatime latemount,wait,check,formattable,quota
'''


def fixture_workspace(root: Path, variant='user', **firmware_options):
    """A workspace whose vendor and android steps passed, with a synthetic
    target-files and a synthetic factory package that its firmware inventory pins."""
    ctx = steps.make_context(argparse.Namespace(
        workspace=str(root / 'ws'), environment=None, variant=variant, jobs=None, allow_network=True,
        factory_zip=None, shallow=False), lambda *a: None)
    ws, config = ctx.workspace, ctx.config
    (root / 'stock').mkdir(parents=True)
    inventory, policy, archive, _ = synthetic_firmware(root / 'stock', **firmware_options)
    ctx.firmware_inventory = inventory
    config['firmware'] = policy
    ws.write_state('vendor', {'status': 'PASS', 'inputs_sha256': 'v', 'outputs': {
        'factory_zip': str(archive), 'factory_sha256': inventory['releases'][inventory['selected_build']]['archive']['sha256']}})
    product = ctx.out / 'target/product/FP6/obj/PACKAGING/target_files_intermediates'
    product.mkdir(parents=True)
    target_files = product / 'FP6-target_files.zip'
    with zipfile.ZipFile(target_files, 'w') as archive:
        for name in image_package.image_names(config):
            archive.writestr(f'IMAGES/{name}.img', f'{name} image'.encode())
        archive.writestr('VENDOR/etc/fstab.qcom', FSTAB)
    host = ctx.host_bin
    host.mkdir(parents=True)
    (host / 'build_super_image').write_text('#!/bin/sh\nprintf "super from %s" "$1" > "$2"\n')
    (host / 'make_f2fs').write_text('#!/bin/sh\nfor last; do :; done\nprintf "f2fs %s" "$*" > "$last"\n')
    for tool in ('build_super_image', 'make_f2fs'):
        (host / tool).chmod(0o755)
    outputs = {'target_files': str(target_files.relative_to(ws.root)), 'target_files_sha256': bw.sha_file(target_files),
               'build_identity': 'f' * 64, 'build_number': 'test.ffffffffffff', 'build_datetime': 1790000000,
               'variant': variant, 'lunch': f'FP6-cur-{variant}', 'descriptor_sha256': 'd' * 64,
               'network_isolation': 'on'}
    ws.write_state('android', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': outputs})
    ctx.resolved_manifest.write_bytes(b'<manifest/>')
    ws.write_state('sync', {'status': 'PASS', 'inputs_sha256': 's', 'outputs': {
        'manifest_url': 'https://github.com/DiamaneOS/platform_manifest.git', 'manifest_branch': 'android17',
        'manifest_commit': 'c' * 40, 'resolved_manifest_sha256': bw.sha_file(ctx.resolved_manifest),
        'kernel_prebuilts_commit': 'b' * 40, 'project_map_sha256': 'p' * 64, 'project_count': 1040}})
    return ctx


def run_plan(ctx, plan):
    runner = bw.Runner(True, lambda *a: None)
    log = ctx.workspace.new_log(plan.name)
    ctx.cache['log'] = log
    for action in plan.actions:
        runner.run(action, log)
    return plan.outputs()


class PackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ctx = fixture_workspace(self.root)

    def test_package_exports_one_coherent_set_with_records(self):
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        directory = self.ctx.workspace.root / outputs['directory']
        self.assertEqual('20260921-user-ffffffffff', outputs['build_id'])
        self.assertTrue(image_package.check_sums(directory))
        record = json.loads((directory / 'build.json').read_text())
        self.assertIs(False, record['release'])
        self.assertIs(False, record['official'])
        self.assertEqual('public-test-keys', record['signing'])
        self.assertTrue(record['never_lock'])
        self.assertEqual(b'boot image', (directory / 'boot.img').read_bytes())
        self.assertEqual(set(image_package.image_names(self.ctx.config)) | {'super'}, set(record['images']))
        self.assertEqual((self.ctx.workspace.images / 'latest').resolve(), directory.resolve())
        self.assertIn('-g android -r -T 1790000000 -U edfbd922-2398-582a-bc24-062261d4ee5e -l metadata -S 67108864',
                      (directory / 'metadata.img').read_text())
        # The record names the manifest commit, the resolved manifest and the kernel prebuilts.
        self.assertEqual(b'<manifest/>', (directory / 'resolved-manifest.xml').read_bytes())
        self.assertEqual({'url': 'https://github.com/DiamaneOS/platform_manifest.git', 'branch': 'android17',
                          'commit': 'c' * 40, 'file': 'resolved-manifest.xml',
                          'resolved_sha256': bw.sha_file(directory / 'resolved-manifest.xml')}, record['manifest'])
        self.assertEqual({'path': 'device/fairphone/FP6-kernel', 'commit': 'b' * 40}, record['kernel_prebuilts'])
        self.assertNotIn('kernel', record['generated_inputs'])

    def test_an_official_build_says_so_in_its_record(self):
        android = self.ctx.workspace.passed('android')
        android['outputs']['official'] = True
        self.ctx.workspace.write_state('android', android)
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        record = json.loads((self.ctx.workspace.root / outputs['directory'] / 'build.json').read_text())
        self.assertIs(True, record['official'])
        self.assertEqual('public-test-keys', record['signing'])
        self.assertIn('installs none while the build is signed with public test keys', record['notice'])

    def test_missing_factory_archive_is_refused_without_probing_the_working_directory(self):
        # The firmware comes from the factory package, so packaging needs it;
        # a vendor record without one must not make the step look at '.'.
        self.ctx.workspace.write_state('vendor', {'status': 'PASS', 'inputs_sha256': 'v',
                                                  'outputs': {'generation': 'authenticated'}})
        original = Path.is_file
        def is_file(path):
            if path == Path('.'):
                raise PermissionError('inherited working directory is inaccessible')
            return original(path)
        with patch.object(Path, 'is_file', is_file):
            with self.assertRaisesRegex(bw.BuildStepError, 'needs the factory package'):
                run_plan(self.ctx, image_package.plan(self.ctx))

    def test_build_record_names_the_factory_package_without_its_path(self):
        record = image_package.public_vendor_outputs(
            {'factory_zip': '/var/lib/x/ws/cache/FP6-factory.zip', 'factory_sha256': 'f' * 64})
        self.assertEqual('FP6-factory.zip', record['factory_zip'])
        self.assertEqual('f' * 64, record['factory_sha256'])

    def test_repackaging_the_same_build_reuses_the_published_set(self):
        first = run_plan(self.ctx, image_package.plan(self.ctx))
        second = run_plan(self.ctx, image_package.plan(self.ctx))
        self.assertEqual(first, second)

    def test_changed_target_files_never_reuse_an_image_set(self):
        first = run_plan(self.ctx, image_package.plan(self.ctx))
        ws = self.ctx.workspace
        state = ws.passed('android')
        path = ws.root / state['outputs']['target_files']
        with zipfile.ZipFile(path, 'w') as archive:
            for name in image_package.image_names(self.ctx.config):
                archive.writestr(f'IMAGES/{name}.img', f'new {name} image'.encode())
            archive.writestr('VENDOR/etc/fstab.qcom', FSTAB)
        state['outputs']['target_files_sha256'] = bw.sha_file(path)
        ws.write_state('android', state)
        with self.assertRaisesRegex(bw.BuildStepError, 'is not this build'):
            run_plan(self.ctx, image_package.plan(self.ctx))
        state['outputs']['build_identity'] = 'e' * 64
        ws.write_state('android', state)
        second = run_plan(self.ctx, image_package.plan(self.ctx))
        self.assertNotEqual(first['directory'], second['directory'])
        self.assertEqual(b'new boot image', (ws.root / second['directory'] / 'boot.img').read_bytes())
        self.assertEqual(b'boot image', (ws.root / first['directory'] / 'boot.img').read_bytes())

    def test_a_changed_packaging_configuration_never_reuses_an_image_set(self):
        first = run_plan(self.ctx, image_package.plan(self.ctx))
        record = json.loads((self.ctx.workspace.root / first['directory'] / 'build.json').read_text())
        self.assertFalse(record['wipe']['validated'])
        self.ctx.config['wipe']['validated'] = True
        with self.assertRaisesRegex(bw.BuildStepError, 'is not this build'):
            run_plan(self.ctx, image_package.plan(self.ctx))

    def test_a_package_step_with_a_changed_image_is_stale(self):
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        previous = {'outputs': outputs}
        self.assertTrue(image_package.plan(self.ctx).valid(previous))
        boot = self.ctx.workspace.root / outputs['directory'] / 'boot.img'
        os.chmod(boot, 0o640)
        boot.write_bytes(b'corrupt')
        self.ctx.cache.clear()
        self.assertFalse(image_package.plan(self.ctx).valid(previous))

    def test_changed_target_files_is_refused(self):
        path = self.ctx.workspace.root / self.ctx.workspace.passed('android')['outputs']['target_files']
        os.chmod(path, 0o640)
        with path.open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(bw.BuildStepError, 'changed after the build'):
            run_plan(self.ctx, image_package.plan(self.ctx))

    def test_the_record_names_the_tools_that_built_the_images(self):
        producer, other = {'commit': 'a' * 40, 'clean': True}, {'commit': 'b' * 40, 'clean': True}
        cases = ((producer, producer, True), (producer, other, False), (None, producer, False),
                 ({'commit': 'a' * 40, 'clean': False}, {'commit': 'a' * 40, 'clean': False}, False))
        for number, (built, packaging, reproducible) in enumerate(cases):
            with self.subTest(built=built, packaging=packaging):
                ctx = fixture_workspace(self.root / str(number))
                android = ctx.workspace.passed('android')
                if built:
                    android['outputs']['tools'] = built
                ctx.workspace.write_state('android', android)
                with patch.object(image_package.product_inputs, 'tools_identity', return_value=packaging):
                    outputs = run_plan(ctx, image_package.plan(ctx))
                record = json.loads((ctx.workspace.root / outputs['directory'] / 'build.json').read_text())
                self.assertEqual(built or {'commit': None, 'clean': None}, record['tools'])
                self.assertEqual(packaging, record['packaging_tools'])
                self.assertIs(reproducible, record['reproducible'])
                self.assertEqual([], record['modified'])

    def test_the_record_lists_local_changes_and_they_are_not_reproducible(self):
        clean = {'commit': 'a' * 40, 'clean': True}
        android = self.ctx.workspace.passed('android')
        android['outputs'].update(tools=clean, modified=['device/fairphone/FP6'])
        self.ctx.workspace.write_state('android', android)
        with patch.object(image_package.product_inputs, 'tools_identity', return_value=clean):
            outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        record = json.loads((self.ctx.workspace.root / outputs['directory'] / 'build.json').read_text())
        self.assertEqual(['device/fairphone/FP6'], record['modified'])
        self.assertIs(False, record['reproducible'])

    def test_misc_is_zeros_sized_from_the_stock_partition_table(self):
        factory = self.root / 'factory.zip'
        table = ('<data><zeroout start_sector="9544" num_partition_sectors="256" label="misc" SECTOR_SIZE_IN_BYTES="4096"/>'
                 '<program start_sector="9544" num_partition_sectors="256" filename="" label="misc" SECTOR_SIZE_IN_BYTES="4096"/>'
                 '<program num_partition_sectors="8" label="frp" SECTOR_SIZE_IN_BYTES="4096"/></data>')
        with zipfile.ZipFile(factory, 'w') as archive:
            archive.writestr('FP6-factory/images/rawprogram0.xml', table)
        self.assertEqual(1048576, image_package.stock_partition_size(factory, 'misc'))
        self.assertIsNone(image_package.stock_partition_size(factory, 'absent'))
        # The fixture's factory package gives misc 256 sectors of 4096 bytes.
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        directory = self.ctx.workspace.root / outputs['directory']
        self.assertEqual(bytes(1048576), (directory / 'misc.img').read_bytes())
        record = json.loads((directory / 'build.json').read_text())
        self.assertEqual({'misc': True}, record['wipe']['partition_table_checked'])
        self.assertIn('misc', record['wipe']['images'])

    def test_misc_size_disagreeing_with_the_stock_table_is_refused(self):
        ctx = fixture_workspace(self.root / 'other', misc_sectors=512)
        with self.assertRaisesRegex(bw.BuildStepError, 'stock partition table says 2097152'):
            run_plan(ctx, image_package.plan(ctx))

    def test_package_carries_the_exact_stock_firmware(self):
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        directory = self.ctx.workspace.root / outputs['directory']
        inventory, policy = self.ctx.firmware_inventory, self.ctx.config['firmware']
        plan = firmware.plan(inventory, policy)
        record = json.loads((directory / 'build.json').read_text())
        self.assertEqual('FP6.QREL.16.111.0', record['firmware']['release'])
        self.assertEqual(record['stock_build'], record['firmware']['release'])
        sums = image_package.read_sums(directory)
        for name, identity in plan['images'].items():
            self.assertEqual(identity['sha256'], bw.sha_file(directory / name))
            self.assertEqual(identity['sha256'], sums[name])
        self.assertEqual(plan['steps'], record['firmware']['steps'])
        # Our own pvmfw, not the stock one.
        self.assertEqual(b'pvmfw image', (directory / 'pvmfw.img').read_bytes())
        for name, image in policy['reset'].items():
            self.assertEqual(bytes(image['bytes']), (directory / image['image']).read_bytes())
            self.assertEqual({'file': image['image'], 'sha256': sums[image['image']]}, record['firmware']['reset'][name])
        self.assertEqual([], firmware.check_set(directory, record['firmware'], inventory, policy, record['stock_build']))

    def test_firmware_from_another_package_is_refused(self):
        release = self.ctx.firmware_inventory['selected_build']
        self.ctx.firmware_inventory['releases'][release]['archive']['sha256'] = '0' * 64
        with self.assertRaisesRegex(bw.BuildStepError, 'not the factory package of FP6.QREL.16.111.0'):
            run_plan(self.ctx, image_package.plan(self.ctx))

    def test_firmware_of_another_release_than_the_vendor_files_is_refused(self):
        inventory = self.ctx.firmware_inventory
        inventory['selected_build'] = inventory['previous_build']
        with self.assertRaisesRegex(bw.BuildStepError, 'vendor files come from FP6.QREL.16.111.0'):
            run_plan(self.ctx, image_package.plan(self.ctx))

    def test_reset_size_disagreeing_with_the_stock_table_is_refused(self):
        ctx = fixture_workspace(self.root / 'other', reset_sectors=3)
        with self.assertRaisesRegex(bw.BuildStepError, 'modemst1 image is 8192 bytes but the stock partition table says 12288'):
            run_plan(ctx, image_package.plan(ctx))

    def test_a_published_set_with_other_firmware_is_not_reused(self):
        run_plan(self.ctx, image_package.plan(self.ctx))
        release = self.ctx.firmware_inventory['selected_build']
        self.ctx.firmware_inventory['releases'][release]['images']['firmware']['abl.elf']['sha256'] = 'a' * 64
        with self.assertRaisesRegex(bw.BuildStepError, 'is not this build'):
            run_plan(self.ctx, image_package.plan(self.ctx))

    def test_firmware_names_never_replace_an_os_image(self):
        image_package.check_firmware_names(['abl.elf', 'NON-HLOS.bin'], self.ctx.config)
        for name in ('pvmfw.img', 'boot.img', 'super.img', 'misc.img', 'modemst1.img', 'build.json', 'SHA256SUMS'):
            with self.subTest(name):
                with self.assertRaisesRegex(bw.BuildStepError, name):
                    image_package.check_firmware_names(['abl.elf', name], self.ctx.config)

    def test_wipe_images_match_the_stock_frp_and_the_device_fstab(self):
        frp = self.root / 'frp.img'
        image_package.frp_image(frp, 524288)
        self.assertEqual(self.ctx.config['wipe']['images']['frp']['sha256'], bw.sha_file(frp))
        image_package.check_wipe_against_fstab(FSTAB, self.ctx.config['wipe'])
        with self.assertRaisesRegex(bw.BuildStepError, 'format /data'):
            image_package.check_wipe_against_fstab(FSTAB.replace(',formattable,quota', ',quota'), self.ctx.config['wipe'])
        with self.assertRaisesRegex(bw.BuildStepError, 'metadata image type'):
            image_package.check_wipe_against_fstab(FSTAB.replace('/metadata f2fs', '/metadata ext4'), self.ctx.config['wipe'])


if __name__ == '__main__':
    unittest.main()
