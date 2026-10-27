import unittest

from diamaneos_tools import flash_all


def record(**extra):
    base = {'build_id': 'test', 'product': 'FP6',
            'device': {'name': 'Fairphone 6', 'maker': 'Fairphone', 'bootloader_keys': 'Power and Volume down'},
            'flash': {'slot': 'a', 'bootloader': ['boot', 'vbmeta'], 'logical': ['system']},
            'wipe': {'images': {'misc': {'file': 'misc.img'}, 'userdata': {'file': 'userdata.img'}}}}
    return {**base, **extra}


class FlashAllRelockTest(unittest.TestCase):
    def test_a_set_signed_with_private_keys_installs_its_key_and_stops_in_the_bootloader(self):
        signed = record(never_lock=False, avb_custom_key={'file': 'avb_pkmd.bin'})
        commands = flash_all.commands(signed)
        self.assertEqual([['flash', 'avb_custom_key', 'avb_pkmd.bin'], ['--set-active=a'], ['reboot-bootloader']],
                         commands[-3:])
        # The key goes in from the bootloader, after fastbootd wrote the logical partitions.
        self.assertLess(commands.index(['reboot-bootloader']), commands.index(commands[-3]))
        for name, text in flash_all.scripts(signed).items():
            with self.subTest(name):
                self.assertIn('fastboot flash avb_custom_key avb_pkmd.bin', text)
                self.assertLess(text.index('get_unlock_ability reports 1'),
                                text.index('run fastboot flashing lock_critical and confirm on the phone'))
                self.assertIn('Then run fastboot flashing lock and confirm on the phone', text)
                # The script never locks or erases: the user locks after it, before Android starts.
                commands = [line for line in text.splitlines() if line.startswith('fastboot ')]
                self.assertFalse([c for c in commands if ' flashing ' in c or c.startswith('fastboot erase')])
                self.assertNotIn('fastboot reboot\n', text.replace('\r\n', '\n'))

    def test_a_set_that_must_stay_unlocked_never_installs_a_key(self):
        for extra in ({'signing': 'qualification-keys'}, {'signing': 'public-test-keys', 'never_lock': False}, {}):
            unlocked = record(**{'never_lock': True, 'avb_custom_key': {'file': 'avb_pkmd.bin'}, **extra})
            with self.subTest(extra):
                self.assertEqual([['--set-active=a'], ['reboot']], flash_all.commands(unlocked)[-2:])
                self.assertNotIn('avb_custom_key', flash_all.scripts(unlocked)[flash_all.SH])
                with self.assertRaises(ValueError):
                    flash_all.commands(unlocked, lock=True)

    def test_a_test_build_installs_its_key_only_on_request(self):
        test = record(signing='public-test-keys', never_lock=True, avb_custom_key={'file': 'avb_pkmd.bin'})
        self.assertEqual([['--set-active=a'], ['reboot']], flash_all.commands(test)[-2:])
        self.assertEqual([['flash', 'avb_custom_key', 'avb_pkmd.bin'], ['--set-active=a'], ['reboot-bootloader']],
                         flash_all.commands(test, lock=True)[-3:])
        scripts = flash_all.scripts(test)
        lines = scripts[flash_all.SH].splitlines()
        # Asked before the checksums, and the key and the lock text only in the confirmed branch.
        self.assertLess(lines.index('[ "$answer" = yes ] || fail "Not confirmed. Nothing was written."'),
                        next(i for i, line in enumerate(lines) if 'sha256sum' in line))
        branch = lines[lines.index('if [ "$lock" = yes ]; then'):]
        self.assertEqual('fastboot flash avb_custom_key avb_pkmd.bin', branch[1])
        self.assertLess(branch.index(f'echo "{flash_all.LOCK}"'), branch.index('else'))
        self.assertEqual(['fastboot --set-active=a', 'checkslot a', 'fastboot reboot', 'fi'], branch[-4:])
        lines = scripts[flash_all.BAT].split('\r\n')
        self.assertIn('if "%~1"=="lock" (call :confirm || exit /b 1)', lines)
        self.assertIn('if "!answer!"=="yes" (endlocal & exit /b 0)', lines)
        branch = lines[lines.index('if not "%lock%"=="yes" goto unlocked'):]
        self.assertEqual('fastboot flash avb_custom_key avb_pkmd.bin || exit /b 1', branch[1])
        self.assertEqual(['fastboot --set-active=a || exit /b 1', 'call :checkslot a || exit /b 1',
                          'fastboot reboot || exit /b 1'], branch[branch.index(':unlocked') + 1:][:3])
        for text in scripts.values():
            self.assertIn('which anyone can download', text)
            self.assertNotIn(';', flash_all.TEST_KEY_LOCK + flash_all.ASK + flash_all.NOT_CONFIRMED)


class FlashAllFastbootdTest(unittest.TestCase):
    def test_misc_is_cleared_before_fastbootd_and_again_with_the_wipe(self):
        # An unlock leaves an erase request in misc, which recovery would run instead of fastbootd.
        commands = flash_all.commands(record())
        fastbootd = commands.index(['reboot', 'fastboot'])
        self.assertEqual([['flash', 'misc', 'misc.img'], ['--set-active=a']], commands[fastbootd - 2:fastbootd])
        self.assertIn(['flash', 'misc', 'misc.img'], commands[fastbootd:])
        for name, text in flash_all.scripts(record()).items():
            with self.subTest(name):
                lines = [line.split(' ||')[0] for line in text.splitlines() if line.startswith('fastboot ')]
                self.assertLess(lines.index('fastboot flash misc misc.img'), lines.index('fastboot reboot fastboot'))
        with self.assertRaises(ValueError):
            flash_all.commands(record(wipe={'images': {'userdata': {'file': 'userdata.img'}}}))


if __name__ == '__main__':
    unittest.main()
