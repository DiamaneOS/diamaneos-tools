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

# What "fastboot getvar all" and "fastboot oem device-info" print on an
# unlocked FP6 in the bootloader (September 2026, stock firmware): the
# bootloader reports no bootloader or baseband version.
PHONE = '''(bootloader) snapshot-update-status:none
(bootloader) unlocked:yes
(bootloader) version-baseband:
(bootloader) version-bootloader:
(bootloader) variant:SM_KIMOLOS UFS
(bootloader) current-slot:a
(bootloader) slot-retry-count:a:7
(bootloader) product:FP6
(bootloader) is-userspace:no
all:
Finished. Total time: 0.025s
(bootloader) Verity mode: true
(bootloader) Device unlocked: true
(bootloader) Device critical unlocked: true
(bootloader) Rollback index (0): 0
OKAY [  0.003s]
Finished. Total time: 0.003s
'''
OLDER, SHIPPED, NEWER = 'FP6.QREL.16.100.0', 'FP6.QREL.16.111.0', 'FP6.QREL.16.120.0'


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

    def commands(self, **kw):
        return [line.strip() for line in self.text(**kw).splitlines() if line.strip().startswith('fastboot')]

    def phone(self, text=PHONE):
        return flash_steps.parse_phone(text)

    def test_full_flash_is_slot_a_without_lock_erase_or_wipe(self):
        text = self.text(firmware='skip')
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
        text = self.text(wipe=True, firmware='skip')
        for line in ('fastboot flash userdata userdata.img', 'fastboot flash metadata metadata.img',
                     'fastboot flash frp frp.img', 'fastboot flash misc misc.img'):
            self.assertIn(line, text)
        self.assertIn('not yet tested on a phone', text)
        self.assertNotIn('erase', '\n'.join(l for l in text.splitlines() if 'fastboot' in l and l.startswith('  ')))
        # Left alone, the firmware partitions stay as they are, the state ones too.
        self.assertNotIn('vm-persist', text)
        self.assertNotIn('modemst', text)

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
            flash_steps.steps(self.directory, self.record, report=None, firmware='skip')
        failed = {'checks': [{'id': 'boot-header', 'status': 'FAIL'}]}
        with self.assertRaisesRegex(flash_steps.FlashError, 'boot-header'):
            flash_steps.steps(self.directory, self.record, report=failed)

    def test_full_super_fallback_lists_every_logical_partition(self):
        text = self.text(firmware='skip')
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

    def test_first_install_needs_no_stock_flash(self):
        text = self.text(wipe=True, phone=self.phone(), phone_firmware=OLDER)
        self.assertIn('unlock_critical', text)
        self.assertNotIn('factory package with', text)
        self.assertIn('Never install firmware older', text)
        self.assertLess(text.index('unlock_critical'), text.index('fastboot flash abl_a abl.elf'))

    def test_image_sets_without_firmware_keep_the_stock_package_first(self):
        record = json.loads(json.dumps(self.record))
        del record['firmware']
        text = '\n'.join(flash_steps.steps(self.directory, record, report=self.report, wipe=True))
        self.assertLess(text.index('unlock_critical'), text.index('factory package with'))
        self.assertNotIn('abl', text)

    def test_firmware_needs_to_know_what_the_phone_runs(self):
        with self.assertRaisesRegex(flash_steps.FlashError, 'Which firmware does the phone run'):
            self.text()
        with self.assertRaisesRegex(flash_steps.FlashError, 'Which firmware does the phone run'):
            self.text(phone=self.phone())
        with self.assertRaisesRegex(flash_steps.FlashError, 'not an FP6 stock build number'):
            self.text(phone=self.phone(), phone_firmware='16.100')

    def test_older_firmware_is_written_first_to_both_slots_in_stock_order(self):
        text = self.text(phone=self.phone(), phone_firmware=OLDER)
        commands = self.commands(phone=self.phone(), phone_firmware=OLDER)
        firmware = [f'fastboot flash {s["partition"]} {s["image"]}' for s in self.record['firmware']['steps']
                    if not s.get('wipe_only')]
        self.assertEqual(['fastboot flash abl_a abl.elf', 'fastboot flash abl_b abl.elf'], firmware[:2])
        self.assertEqual(firmware + ['fastboot flash modemst1 modemst1.img', 'fastboot flash modemst2 modemst2.img',
                                     'fastboot flash boot_a boot.img'], commands[:len(firmware) + 3])
        self.assertIn('fastboot flash storsec storsec.mbn', commands)
        self.assertIn('fastboot flash study study.img', commands)
        for absent in ('logfs', 'vm-persist', 'pvmfw_b', 'boot_b', 'erase', ' -w', 'oem', 'flashing'):
            self.assertNotIn(absent, '\n'.join(commands))
        self.assertEqual('fastboot --set-active=a', commands[-2])
        self.assertIn(OLDER, text)
        self.assertIn('both slots', text)
        self.assertIn('The firmware steps are not yet tested on a phone', text)

    def test_wipe_writes_the_state_partitions_at_their_stock_place(self):
        commands = self.commands(wipe=True, phone=self.phone(), phone_firmware=OLDER)
        partitions = [c.split()[2] for c in commands if c.startswith('fastboot flash')]
        self.assertEqual(['abl_a', 'abl_b', 'logfs', 'modem_a', 'modem_b', 'storsec', 'study', 'studybk_a',
                          'studybk_b', 'vm-persist', 'xbl_a', 'xbl_b', 'modemst1', 'modemst2', 'boot_a'], partitions[:15])
        self.assertIn('fastboot flash misc misc.img', commands)

    def test_the_same_firmware_is_skipped(self):
        text = self.text(phone_firmware=SHIPPED)
        self.assertNotIn('abl', text)
        self.assertNotIn('modemst', text)
        self.assertIn('already runs FP6.QREL.16.111.0', text)
        self.assertNotIn('not yet tested on a phone', text)
        # A wipe still resets the firmware's state partitions, after super.
        commands = self.commands(phone_firmware=SHIPPED, wipe=True)
        self.assertLess(commands.index('fastboot flash super super.img'),
                        commands.index('fastboot flash vm-persist vm-persist.img'))
        self.assertIn('fastboot flash logfs logfs_ufs_8mb.bin', commands)

    def test_rewrite_writes_the_same_release_again(self):
        commands = self.commands(phone=self.phone(), phone_firmware=SHIPPED, firmware='rewrite')
        self.assertEqual('fastboot flash abl_a abl.elf', commands[0])
        with self.assertRaisesRegex(flash_steps.FlashError, 'bootloader state'):
            self.text(phone_firmware=SHIPPED, firmware='rewrite')

    def test_newer_firmware_is_never_replaced(self):
        for mode in ('auto', 'rewrite'):
            with self.subTest(mode):
                with self.assertRaisesRegex(flash_steps.FlashError, 'never installs older firmware'):
                    self.text(phone=self.phone(), phone_firmware=NEWER, firmware=mode)
        text = self.text(phone_firmware=NEWER, firmware='skip')
        self.assertNotIn('abl', text)

    def test_reported_versions_must_be_ones_the_inventory_records(self):
        reporting = PHONE.replace('version-bootloader:', 'version-bootloader:MXF.2.1-02027')
        with self.assertRaisesRegex(flash_steps.FlashError, 'MXF.2.1-02027'):
            self.text(phone=self.phone(reporting), phone_firmware=OLDER)
        record = json.loads(json.dumps(self.record))
        record['firmware']['fastboot_versions'][OLDER] = {'version-bootloader': 'MXF.2.1-02027', 'version-baseband': ''}
        text = '\n'.join(flash_steps.steps(self.directory, record, report=self.report,
                                           phone=self.phone(reporting), phone_firmware=OLDER))
        self.assertIn('fastboot flash abl_a abl.elf', text)
        with self.assertRaisesRegex(flash_steps.FlashError, 'MXF.2.1-02027'):
            flash_steps.steps(self.directory, record, report=self.report, phone=self.phone(reporting),
                              phone_firmware=SHIPPED, firmware='rewrite')

    def test_firmware_steps_need_an_unlocked_fp6_in_the_bootloader(self):
        cases = {'critical partitions are locked': PHONE.replace('critical unlocked: true', 'critical unlocked: false'),
                 'fastbootd': PHONE.replace('is-userspace:no', 'is-userspace:yes'),
                 'not a Fairphone 6': PHONE.replace('product:FP6', 'product:FP5'),
                 'bootloader is locked': PHONE.replace('unlocked:yes', 'unlocked:no'),
                 'fastboot oem device-info': PHONE.split('all:')[0],
                 'version-bootloader': PHONE.replace('(bootloader) version-bootloader:\n', '')}
        for message, text in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(flash_steps.FlashError, message):
                    self.text(phone=self.phone(text), phone_firmware=OLDER)
        with self.assertRaisesRegex(flash_steps.FlashError, 'twice'):
            self.phone(PHONE + '(bootloader) product:FP5\n')

    def test_since_takes_the_phone_firmware_from_the_earlier_build(self):
        previous = json.loads(json.dumps(self.record))
        self.assertIn('Nothing to flash', self.text(previous=previous))
        previous['firmware']['images']['abl.elf'] = '0' * 64
        self.assertIn('images differ from the earlier build', self.text(previous=previous, phone=self.phone()))
        previous['firmware']['release'] = OLDER
        with self.assertRaisesRegex(flash_steps.FlashError, 'bootloader state'):
            self.text(previous=previous)
        self.assertIn('fastboot flash abl_a abl.elf', self.text(previous=previous, phone=self.phone()))
        with self.assertRaisesRegex(flash_steps.FlashError, 'earlier build'):
            self.text(previous=previous, phone=self.phone(), phone_firmware=SHIPPED)
        previous['firmware']['release'] = NEWER
        with self.assertRaisesRegex(flash_steps.FlashError, 'never installs older firmware'):
            self.text(previous=previous, phone=self.phone())
        del previous['firmware']
        with self.assertRaisesRegex(flash_steps.FlashError, 'Which firmware does the phone run'):
            self.text(previous=previous)

    def test_command_line_reads_the_saved_phone_state(self):
        phone = self.root / 'phone.txt'
        phone.write_text(PHONE)
        report = self.directory.parent / (self.record['build_id'] + '.verify.json')
        report.write_text(json.dumps({'build_id': self.record['build_id'], 'checks': self.report['checks'],
                                      'sums_sha256': image_package.bw.sha_file(self.directory / 'SHA256SUMS')}))
        import contextlib, io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flash_steps.main([str(self.directory), '--phone', str(phone), '--phone-firmware', OLDER])
        self.assertEqual(0, code, err.getvalue())
        self.assertIn('fastboot flash abl_b abl.elf', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(2, flash_steps.main([str(self.directory)]))
        self.assertIn('Which firmware', err.getvalue())

    def test_altered_image_directory_is_refused(self):
        boot = self.directory / 'boot.img'
        os.chmod(boot, 0o640)
        boot.write_bytes(b'changed')
        with self.assertRaisesRegex(flash_steps.FlashError, 'SHA256SUMS'):
            flash_steps.load(self.directory)


if __name__ == '__main__':
    unittest.main()
