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
                     'fastboot flash frp frp.img', 'fastboot flash misc misc.img'):
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
        # A stalled super flash may have written the new layout already, so the
        # fastbootd fallback lists every logical partition, not only vendor.
        logical = [f'fastboot flash {n}_a {n}.img' for n in self.record['flash']['logical']]
        self.assertEqual(['fastboot flash vendor_boot_a vendor_boot.img', 'fastboot flash super super.img'] + logical,
                         commands)

    def test_unverified_or_failed_sets_are_refused(self):
        with self.assertRaisesRegex(flash_steps.FlashError, 'not been verified'):
            flash_steps.steps(self.directory, self.record, report=None)
        failed = {'checks': [{'id': 'boot-header', 'status': 'FAIL'}]}
        with self.assertRaisesRegex(flash_steps.FlashError, 'boot-header'):
            flash_steps.steps(self.directory, self.record, report=failed)

    def test_full_super_fallback_lists_every_logical_partition(self):
        text = self.text()
        fallback = text[text.index('fastboot reboot fastboot'):]
        for name in self.record['flash']['logical']:
            self.assertIn(f'fastboot flash {name}_a {name}.img', fallback)

    def test_stale_verification_report_is_refused(self):
        report = self.directory.parent / (self.record['build_id'] + '.verify.json')
        report.write_text(json.dumps({'build_id': self.record['build_id'], 'sums_sha256': '0' * 64, 'checks': []}))
        with self.assertRaisesRegex(flash_steps.FlashError, 'another image set'):
            flash_steps.verification(self.directory, self.record)
        current = image_package.bw.sha_file(self.directory / 'SHA256SUMS')
        report.write_text(json.dumps({'build_id': self.record['build_id'], 'sums_sha256': current, 'checks': []}))
        self.assertEqual(current, flash_steps.verification(self.directory, self.record)['sums_sha256'])

    def test_first_install_unlocks_before_the_factory_package(self):
        text = self.text(wipe=True)
        self.assertLess(text.index('unlock_critical'), text.index('factory package with'))
        self.assertIn('Never install firmware older', text)

    def test_altered_image_directory_is_refused(self):
        boot = self.directory / 'boot.img'
        os.chmod(boot, 0o640)
        boot.write_bytes(b'changed')
        with self.assertRaisesRegex(flash_steps.FlashError, 'SHA256SUMS'):
            flash_steps.load(self.directory)


if __name__ == '__main__':
    unittest.main()
