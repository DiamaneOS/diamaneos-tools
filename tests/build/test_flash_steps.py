import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import flash_steps, image_package
from tests.build.test_image_package import fixture_workspace, run_plan


class FlashStepTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ctx = fixture_workspace(self.root)
        outputs = run_plan(self.ctx, image_package.plan(self.ctx))
        self.directory = self.ctx.workspace.root / outputs['directory']
        self.record = flash_steps.load(self.directory)
        self.report = {'checks': [{'id': 'record', 'status': 'PASS'}]}

    def text(self, **kw):
        return '\n'.join(flash_steps.steps(self.directory, self.record, report=self.report, **kw))

    def test_full_flash_is_slot_a_without_lock_erase_or_wipe(self):
        text = self.text()
        order = [line.strip() for line in text.splitlines() if line.strip().startswith('fastboot')]
        self.assertEqual('fastboot flash boot_a boot.img', order[0])
        self.assertIn('fastboot flash super super.img', order)
        self.assertLess(order.index('fastboot flash vbmeta_a vbmeta.img'), order.index('fastboot flash super super.img'))
        self.assertIn('fastboot --set-active=a', order)
        commands = '\n'.join(order)
        for forbidden in ('flashing lock', ' -w', 'erase', '_b '):
            self.assertNotIn(forbidden, commands)
        self.assertNotIn('userdata', commands)
        self.assertIn('never run', text)

    def test_wipe_flashes_empty_images_and_says_it_is_unvalidated(self):
        text = self.text(wipe=True)
        for line in ('fastboot flash userdata userdata.img', 'fastboot flash metadata metadata.img',
                     'fastboot flash frp frp.img'):
            self.assertIn(line, text)
        self.assertIn('not yet tested on a phone', text)
        self.assertIn('factory package', text)
        self.assertNotIn('erase', '\n'.join(l for l in text.splitlines() if 'fastboot' in l and l.startswith('  ')))

    def test_since_prints_only_changed_images(self):
        previous = json.loads(json.dumps(self.record))
        self.assertIn('Nothing to flash', self.text(previous=previous))
        previous['images']['vendor_boot'] = '0' * 64
        previous['images']['vendor'] = '0' * 64
        commands = [l.strip() for l in self.text(previous=previous).splitlines() if l.strip().startswith('fastboot flash')]
        self.assertEqual(['fastboot flash vendor_boot_a vendor_boot.img', 'fastboot flash super super.img',
                          'fastboot flash vendor_a vendor.img'], commands)

    def test_unverified_or_failed_sets_are_refused(self):
        with self.assertRaisesRegex(flash_steps.FlashError, 'not been verified'):
            flash_steps.steps(self.directory, self.record, report=None)
        failed = {'checks': [{'id': 'boot-header', 'status': 'FAIL'}]}
        with self.assertRaisesRegex(flash_steps.FlashError, 'boot-header'):
            flash_steps.steps(self.directory, self.record, report=failed)
        text = '\n'.join(flash_steps.steps(self.directory, self.record, report=failed, accepted_failures=['boot-header']))
        self.assertIn('accepted for this flash: boot-header', text)

    def test_altered_image_directory_is_refused(self):
        boot = self.directory / 'boot.img'
        os.chmod(boot, 0o640)
        boot.write_bytes(b'changed')
        with self.assertRaisesRegex(flash_steps.FlashError, 'SHA256SUMS'):
            flash_steps.load(self.directory)


if __name__ == '__main__':
    unittest.main()
