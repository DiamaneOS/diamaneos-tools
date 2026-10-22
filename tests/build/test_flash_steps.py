import json
import re
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import flash_all, flash_steps, image_package, image_verify
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
        self.report = self.passing_report()

    def passing_report(self):
        checks = [{'id': check, 'status': 'PASS'} for check in image_verify.REQUIRED_CHECKS]
        return {'schema_version': 1, 'build_id': self.record['build_id'],
                'sums_sha256': image_package.bw.sha_file(self.directory / 'SHA256SUMS'),
                'status': 'PASS', 'checked': len(checks), 'failed': 0, 'checks': checks}

    def text(self, **kw):
        return '\n'.join(flash_steps.steps(self.directory, self.record, report=self.report, **kw))

    def commands(self, **kw):
        return [line.strip() for line in self.text(**kw).splitlines() if line.strip().startswith('fastboot')]

    def phone(self, text=PHONE):
        return flash_steps.parse_phone(text)

    def test_full_flash_is_slot_a_without_lock_erase_or_wipe(self):
        text = self.text(firmware='skip')
        order = [line.strip() for line in text.splitlines() if line.strip().startswith('fastboot')]
        # A pending update is cancelled first, as fastboot flashall does.
        self.assertEqual(['fastboot snapshot-update cancel', 'fastboot flash boot_a boot.img'], order[:2])
        self.assertIn('reports anything but none', text)
        # The logical partitions go through fastbootd, which recovery holds.
        fastbootd = order.index('fastboot reboot fastboot')
        self.assertEqual('fastboot wipe-super super_empty.img', order[fastbootd + 1])
        self.assertLess(order.index('fastboot flash recovery_a recovery.img'), fastbootd)
        self.assertLess(order.index('fastboot flash vbmeta_a vbmeta.img'), fastbootd)
        self.assertEqual(['fastboot --set-active=a', 'fastboot getvar current-slot'], order[fastbootd - 2:fastbootd])
        self.assertIn('both commands succeed and current-slot reports a', text)
        self.assertNotIn('fastboot flash super super.img', order)
        self.assertIn('Wait until the phone is in fastbootd, then:', text)
        self.assertIn('fastboot --set-active=a', order)
        commands = '\n'.join(order)
        for forbidden in ('flashing lock', ' -w', 'erase', '_b '):
            self.assertNotIn(forbidden, commands)
        self.assertNotIn('userdata', commands)
        self.assertIn('never run', text)

    def resign(self, signing='throwaway-test-keys', **record):
        """The fixture set as re-signed with its own keys, with a passing signed report."""
        (self.directory / 'avb_pkmd.bin').write_bytes(b'own AVB public key fixture')
        (self.directory / 'SHA256SUMS').chmod(0o640)
        image_package.write_sums(self.directory)
        self.record.update({'signing': signing, 'never_lock': False, 'avb_custom_key': {'file': 'avb_pkmd.bin'},
                            **record})
        self.report = dict(self.passing_report(), profile='signed')

    def test_a_set_with_its_own_keys_ends_with_the_relock(self):
        for signing, record in (('throwaway-test-keys', {}), ('release-keys', {'release': True, 'official': True})):
            with self.subTest(signing):
                self.resign(signing, **record)
                text = self.text(wipe=True, firmware='skip')
                commands = self.commands(wipe=True, firmware='skip')
                self.assertEqual(['fastboot flash avb_custom_key avb_pkmd.bin', 'fastboot --set-active=a',
                                  'fastboot flashing lock_critical', 'fastboot flashing get_unlock_ability',
                                  'fastboot flashing lock'], commands[-5:])
                self.assertLess(commands.index('fastboot flash userdata userdata.img'), len(commands) - 5)
                self.assertIn('Hold Volume down while it restarts', text)
                self.assertIn('Continue only if it reports 1. Otherwise stop.', text)
                self.assertIn('install its AVB key and lock the', text)
                self.assertNotIn('public test keys', text)
        # A set that must never be locked keeps the test build's rules.
        self.resign('qualification-keys', never_lock=True, official=True)
        text = self.text(firmware='skip')
        self.assertIn('Keep the bootloader unlocked', text)
        commands = self.commands(firmware='skip')
        self.assertEqual(['fastboot --set-active=a', 'fastboot reboot'], commands[-2:])
        self.assertNotIn('fastboot flash avb_custom_key avb_pkmd.bin', commands)

    def test_relock_needs_a_report_that_its_key_signs_vbmeta(self):
        self.resign()
        for check in ('record', 'avb-chain'):
            report = dict(self.report, status='FAIL', failed=1, checks=[
                dict(c, status='FAIL') if c['id'] == check else c for c in self.report['checks']])
            with self.subTest(check), self.assertRaisesRegex(flash_steps.FlashError, f'verification failed \\({check}\\)'):
                flash_steps.steps(self.directory, self.record, report=report, firmware='skip')
        cases = ((dict(self.report, profile='public-test-keys'), {}, 'used other checks'),
                 (None, {}, 'Run "diamaneos images check" with --report'),
                 (self.report, {'signing': 'public-test-keys', 'release': False}, 'must never be locked'),
                 (self.report, {'signing': 'other-keys'}, 'only prints commands for test builds and sets'))
        for report, record, message in cases:
            with self.subTest(message), self.assertRaisesRegex(flash_steps.FlashError, message):
                flash_steps.steps(self.directory, dict(self.record, **record), report=report, firmware='skip')

    def test_wipe_flashes_empty_images_and_says_it_is_unvalidated(self):
        text = self.text(wipe=True, firmware='skip')
        for line in ('fastboot flash userdata userdata.img', 'fastboot flash metadata metadata.img',
                     'fastboot flash frp frp.img', 'fastboot flash misc misc.img'):
            self.assertIn(line, text)
        self.assertIn('not been tested on a phone', text)
        self.assertNotIn('erase', '\n'.join(l for l in text.splitlines() if 'fastboot' in l and l.startswith('  ')))
        # Left alone, the firmware partitions stay as they are, the state ones too.
        self.assertNotIn('vm-persist', text)
        self.assertNotIn('modemst', text)

    def test_since_prints_only_changed_images(self):
        previous = json.loads(json.dumps(self.record))
        self.assertIn('Nothing to flash', self.text(previous=previous))
        previous['images']['vendor_boot'] = '0' * 64
        previous['images']['vendor'] = '0' * 64
        # wipe-super leaves no logical partition, so every one is written, not only vendor.
        logical = [f'fastboot flash {n}_a {n}.img' for n in self.record['flash']['logical']]
        self.assertEqual(['fastboot snapshot-update cancel', 'fastboot flash vendor_boot_a vendor_boot.img',
                          'fastboot --set-active=a', 'fastboot getvar current-slot', 'fastboot reboot fastboot',
                          'fastboot wipe-super super_empty.img', *logical, 'fastboot reboot-bootloader',
                          'fastboot --set-active=a', 'fastboot reboot'], self.commands(previous=previous))

    def test_failed_checks_stop_every_set(self):
        for official in (False, True):
            report = self.passing_report()
            report['checks'][3]['status'] = 'FAIL'
            report.update(status='FAIL', failed=1)
            with self.subTest(official=official), \
                    self.assertRaisesRegex(flash_steps.FlashError, 'verification failed \\(avb-chain\\). Do not flash'):
                flash_steps.steps(self.directory, dict(self.record, official=official), report=report, firmware='skip')

    def test_the_image_set_carries_install_scripts_for_a_clean_install(self):
        # The same commands as flash-steps prints for a wipe that writes the firmware again.
        printed = self.commands(wipe=True, phone=self.phone(), phone_firmware=SHIPPED, firmware='rewrite')
        wanted = ['fastboot ' + ' '.join(arguments) for arguments in flash_all.commands(self.record)]
        self.assertEqual([c for c in printed if c != 'fastboot getvar current-slot'][:len(wanted)], wanted)
        self.assertEqual(['fastboot --set-active=a', 'fastboot reboot'], wanted[-2:])
        scripts = flash_all.scripts(self.record)
        for name, text in scripts.items():
            self.assertEqual(text, (self.directory / name).read_bytes().decode())
            self.assertEqual(wanted, [line.split(' ||')[0] for line in text.splitlines() if line.startswith(
                ('fastboot flash', 'fastboot snapshot-update', 'fastboot wipe-super', 'fastboot --set',
                 'fastboot reboot'))])
            # Nothing else is run: no lock, unlock, erase or format command.
            self.assertEqual([], [line for line in text.splitlines() if line.startswith('fastboot ')
                                  and not line.startswith(('fastboot flash ', 'fastboot snapshot-update cancel',
                                                           'fastboot wipe-super ',
                                                           'fastboot --set-active=', 'fastboot reboot',
                                                           'fastboot getvar ', 'fastboot oem device-info '))])
            self.assertIn('keep the bootloader unlocked', text)
            self.assertIn(f"Fairphone's {SHIPPED} firmware first", text)
        self.assertTrue(image_package.check_sums(self.directory))
        # Each restart is followed by a wait for the mode, and every command stops the batch file on failure.
        lines = scripts[flash_all.SH].splitlines()
        cancel = lines.index('fastboot snapshot-update cancel')
        self.assertEqual('fi', lines[cancel + 1])
        self.assertIn('[ "$status" != none ]', lines[cancel - 1])
        self.assertEqual('waitfor yes "fastbootd"', lines[lines.index('fastboot reboot fastboot') + 1])
        self.assertEqual('waitfor no "the bootloader"', lines[lines.index('fastboot reboot-bootloader') + 1])
        lines = scripts[flash_all.BAT].split('\r\n')
        cancel = lines.index('fastboot snapshot-update cancel || exit /b 1')
        self.assertTrue(lines[cancel - 1].endswith('|| goto no_snapshot'))
        self.assertEqual(':no_snapshot', lines[cancel + 1])
        self.assertEqual('call :waitfor yes "fastbootd" || exit /b 1',
                         lines[lines.index('fastboot reboot fastboot || exit /b 1') + 1])
        self.assertEqual([], [line for line in lines if line.startswith(('fastboot', 'call'))
                              and '|' not in line])
        # fastboot is never piped into a filter that stops early (grep -q, head): that ends it
        # mid-reply and leaves the bootloader unable to answer the next command.
        self.assertEqual([], [line for line in scripts[flash_all.SH].splitlines()
                              if re.search(r'fastboot [^|;]*\|\s*(grep -q|head)', line)])

    def test_the_device_texts_come_from_the_build_profile(self):
        record = json.loads(json.dumps(self.record))
        record['device'] = {'name': 'Example 1', 'maker': 'Example', 'bootloader_keys': 'Power and Volume up'}
        steps = '\n'.join(flash_steps.steps(self.directory, record, report=self.report, wipe=True,
                                            phone=self.phone(), phone_firmware=OLDER))
        scripts = flash_all.scripts(record)
        for text in [steps, *scripts.values()]:
            self.assertNotIn('Fairphone', text)
            self.assertIn("Example's", text)
        self.assertIn('unlock the bootloader as Example describes', steps)
        self.assertIn('hold Power and Volume up until the bootloader appears', steps)
        self.assertIn('for the Example 1', scripts[flash_all.SH])
        for text in scripts.values():
            self.assertIn('Hold Power and Volume up until it restarts into the bootloader', text)

    def run_install_script(self, answers, initial_slot='a', selection_status=0, slot_reply=None, slot_status=0):
        """Run flash-all.sh against a fastboot stand-in. Returns (status, fastboot calls, stderr)."""
        work = self.root / 'run'
        shutil.rmtree(work, ignore_errors=True)
        (work / 'bin').mkdir(parents=True)
        log, mode, slot, recovery = work / 'calls', work / 'mode', work / 'slot', work / 'recovery-slot'
        slot.write_text(initial_slot)
        replies = '\n'.join(f'  "getvar {name}") echo "{line}" >&2 ;;' for name, line in answers.items()
                            if name != 'is-userspace')
        # Answers is-userspace as given until the script restarts the phone into another mode.
        (work / 'bin/fastboot').write_text(
            f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$*" in\n' + replies + '\n'
            f'  "getvar is-userspace") if [ -f "{mode}" ]; then echo "is-userspace: $(cat "{mode}")" >&2; '
            f'else echo "{answers["is-userspace"]}" >&2; fi ;;\n'
            f'  --set-active=*) [ {selection_status} -eq 0 ] || exit {selection_status}; '
            f'echo "${{1#--set-active=}}" > "{slot}" ;;\n'
            '  "getvar current-slot") ' +
            (f'echo "current-slot: $(cat "{slot}")" >&2; ' if slot_reply is None else
             'printf "%s\\n" ' + shlex.quote(slot_reply) + ' >&2; ') + f'exit {slot_status} ;;\n'
            f'  "reboot fastboot") cat "{slot}" > "{recovery}"; echo yes > "{mode}" ;;\n'
            f'  "reboot-bootloader") echo no > "{mode}" ;;\n'
            '  "oem device-info") echo "(bootloader) Device critical unlocked: true" >&2 ;;\nesac\n')
        (work / 'bin/fastboot').chmod(0o755)
        result = subprocess.run(['sh', str(self.directory / flash_all.SH)], capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, PATH=str(work / 'bin') + os.pathsep + os.environ['PATH']))
        calls = log.read_text().splitlines() if log.exists() else []
        self.recovery_slot = recovery.read_text().strip() if recovery.exists() else None
        return result.returncode, [c for c in calls if not c.startswith(('getvar', 'oem'))], result.stderr

    def test_installer_selects_and_verifies_admitted_slot_before_loading_recovery(self):
        good = {'product': 'product: FP6', 'is-userspace': 'is-userspace: no', 'unlocked': 'unlocked: yes',
                'snapshot-update-status': 'snapshot-update-status: none'}
        for target in ('a', 'b'):
            self.record['flash']['slot'] = target
            self.record.update(never_lock=False, avb_custom_key={'file': 'avb_pkmd.bin'})
            (self.directory / 'avb_pkmd.bin').write_bytes(b'disposable public fixture')
            for name, text in flash_all.scripts(self.record).items():
                (self.directory / name).chmod(0o640); (self.directory / name).write_bytes(text.encode())
            (self.directory / 'SHA256SUMS').chmod(0o640); image_package.write_sums(self.directory)
            for initial in ('a', 'b'):
                with self.subTest(target=target, initial=initial):
                    status, calls, _ = self.run_install_script(good, initial_slot=initial)
                    self.assertEqual(0, status)
                    self.assertEqual(target, self.recovery_slot)
                    self.assertLess(calls.index(f'--set-active={target}'), calls.index('reboot fastboot'))
                    self.assertEqual(['flash avb_custom_key avb_pkmd.bin', f'--set-active={target}',
                                      'reboot-bootloader'], calls[-3:])
                    self.assertNotIn('reboot', calls)
            batch = flash_all.scripts(self.record)['flash-all.bat'].splitlines()
            index = batch.index(f'fastboot --set-active={target} || exit /b 1')
            self.assertEqual(f'call :checkslot {target} || exit /b 1', batch[index + 1])
            self.assertLess(index + 1, batch.index('fastboot reboot fastboot || exit /b 1'))
            self.assertIn('if ($LASTEXITCODE -ne 0)', '\n'.join(batch))

    def test_selection_or_slot_verification_failure_stops_before_fastbootd(self):
        good = {'product': 'product: FP6', 'is-userspace': 'is-userspace: no', 'unlocked': 'unlocked: yes'}
        failures = [dict(selection_status=1), dict(slot_reply='current-slot: b'), dict(slot_reply=''),
                    dict(slot_reply='current-slot: a\ncurrent-slot: a'),
                    dict(slot_reply='current-slot: a', slot_status=1)]
        for failure in failures:
            with self.subTest(failure=failure):
                status, calls, _ = self.run_install_script(good, initial_slot='b', **failure)
                self.assertNotEqual(0, status)
                self.assertIsNone(self.recovery_slot)
                self.assertNotIn('reboot fastboot', calls)
                self.assertNotIn('wipe-super super_empty.img', calls)

    def test_the_install_script_checks_the_phone_and_the_images_before_it_writes(self):
        good = {'product': 'product: FP6', 'is-userspace': '(bootloader) is-userspace:no', 'unlocked': 'unlocked: yes',
                'snapshot-update-status': '(bootloader) snapshot-update-status:merging'}
        status, calls, _ = self.run_install_script(good)
        self.assertEqual((0, [' '.join(a) for a in flash_all.commands(self.record)]), (status, calls))
        # Without a pending update, or a bootloader that reports none, nothing is cancelled.
        for line in ('snapshot-update-status: none', ''):
            status, calls, _ = self.run_install_script(dict(good, **{'snapshot-update-status': line}))
            self.assertEqual((0, [' '.join(a) for a in flash_all.commands(self.record)
                                  if a != flash_all.CANCEL]), (status, calls))
        for name, line, message in (('product', 'product: FP5', 'is for the Fairphone 6 (FP6)'),
                                    ('unlocked', 'unlocked: no', 'bootloader is locked'),
                                    ('is-userspace', 'is-userspace: yes', 'fastbootd')):
            with self.subTest(name):
                status, calls, stderr = self.run_install_script(dict(good, **{name: line}))
                self.assertEqual((1, []), (status, calls))
                self.assertIn(message, stderr)
        # A changed image stops the install before the first write.
        image = self.directory / 'boot.img'
        image.chmod(0o640)
        image.write_bytes(image.read_bytes() + b'x')
        status, calls, _ = self.run_install_script(good)
        self.assertEqual([], calls)
        self.assertNotEqual(0, status)

    def test_empty_partial_or_failed_reports_are_refused(self):
        def changed(**values):
            report = self.passing_report()
            report.update(values)
            return report
        checks = self.passing_report()['checks']
        failed = self.passing_report()
        failed['checks'][3]['status'] = 'FAIL'
        cases = {'not been verified': None,
                 'verification failed \\(avb-chain\\)': failed,
                 'lists no checks': changed(checks=None),
                 'lacks checks: sums, record': changed(status='FAIL', checks=[], checked=0),
                 'lacks checks: firmware-ota': changed(checks=checks[:-1], checked=len(checks) - 1),
                 'lists a check twice': changed(checks=checks + checks[:1], checked=len(checks) + 1),
                 'does not say that all its checks passed': changed(status='FAIL'),
                 'unknown format': changed(schema_version=2),
                 'another image set': changed(sums_sha256='0' * 64)}
        for message, report in cases.items():
            with self.subTest(message), self.assertRaisesRegex(flash_steps.FlashError, message):
                flash_steps.steps(self.directory, self.record, report=report, firmware='skip')
        self.assertTrue(flash_steps.steps(self.directory, self.record, report=self.passing_report(),
                                          firmware='skip'))

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
                                     'fastboot snapshot-update cancel', 'fastboot flash boot_a boot.img'],
                         commands[:len(firmware) + 4])
        self.assertIn('fastboot flash storsec storsec.mbn', commands)
        self.assertIn('fastboot flash study study.img', commands)
        for absent in ('logfs', 'vm-persist', 'pvmfw_b', 'boot_b', 'erase', ' -w', 'oem', 'flashing'):
            self.assertNotIn(absent, '\n'.join(commands))
        self.assertEqual('fastboot --set-active=a', commands[-2])
        self.assertIn(OLDER, text)
        self.assertIn('both slots', text)
        # The firmware steps are tested on a phone, so no warning. A record that says
        # otherwise still gets it.
        warning = 'The firmware steps have not been tested on a phone'
        self.assertNotIn(warning, text)
        untested = dict(self.record, firmware=dict(self.record['firmware'], validated=False))
        self.assertIn(warning, '\n'.join(flash_steps.steps(
            self.directory, untested, report=self.report, phone=self.phone(), phone_firmware=OLDER)))

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
        self.assertNotIn('not been tested on a phone', text)
        # A wipe still resets the firmware's state partitions, back in the bootloader.
        commands = self.commands(phone_firmware=SHIPPED, wipe=True)
        self.assertLess(commands.index('fastboot reboot-bootloader'),
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
        report.write_text(json.dumps(self.report))
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
