import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw, image_package

FSTAB = '''/dev/block/by-name/metadata /metadata f2fs noatime wait,check,formattable,first_stage_mount
/dev/block/bootdevice/by-name/userdata /data f2fs noatime latemount,wait,check,formattable,quota
'''


def fixture_workspace(root: Path, variant='user'):
    """A workspace whose android step passed, with a synthetic target-files."""
    ctx = steps.make_context(argparse.Namespace(
        workspace=str(root / 'ws'), environment=None, variant=variant, jobs=None, allow_network=True,
        objects_from=None, factory_zip=None, shallow=False), lambda *a: None)
    ws, config = ctx.workspace, ctx.config
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
        self.assertEqual('public-test-keys', record['signing'])
        self.assertTrue(record['never_lock'])
        self.assertEqual(b'boot image', (directory / 'boot.img').read_bytes())
        self.assertEqual(set(image_package.image_names(self.ctx.config)) | {'super'}, set(record['images']))
        self.assertEqual((self.ctx.workspace.images / 'latest').resolve(), directory.resolve())
        self.assertIn('-g android -r -T 1790000000 -U edfbd922-2398-582a-bc24-062261d4ee5e -l metadata -S 67108864',
                      (directory / 'metadata.img').read_text())

    def test_repackaging_the_same_build_reuses_the_published_set(self):
        first = run_plan(self.ctx, image_package.plan(self.ctx))
        second = run_plan(self.ctx, image_package.plan(self.ctx))
        self.assertEqual(first, second)

    def test_changed_target_files_is_refused(self):
        path = self.ctx.workspace.root / self.ctx.workspace.passed('android')['outputs']['target_files']
        os.chmod(path, 0o640)
        with path.open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(bw.BuildStepError, 'changed after the build'):
            run_plan(self.ctx, image_package.plan(self.ctx))

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
