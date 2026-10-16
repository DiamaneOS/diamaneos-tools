import unittest

from diamaneos_tools import flash_all


def record(**extra):
    base = {'build_id': 'test', 'product': 'FP6',
            'device': {'name': 'Fairphone 6', 'maker': 'Fairphone', 'bootloader_keys': 'Power and Volume down'},
            'flash': {'slot': 'a', 'bootloader': ['boot', 'vbmeta'], 'logical': ['system']},
            'wipe': {'images': {'userdata': {'file': 'userdata.img'}}}}
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
                self.assertIn('fastboot flashing lock, and confirm on the phone', text)
                # The script never locks or erases: the user locks after it, before Android starts.
                commands = [line for line in text.splitlines() if line.startswith('fastboot ')]
                self.assertFalse([c for c in commands if ' flashing ' in c or c.startswith('fastboot erase')])
                self.assertNotIn('fastboot reboot\n', text.replace('\r\n', '\n'))

    def test_a_set_with_public_test_keys_never_installs_a_key(self):
        public = record(never_lock=True, avb_custom_key={'file': 'avb_pkmd.bin'})
        self.assertEqual([['--set-active=a'], ['reboot']], flash_all.commands(public)[-2:])
        self.assertNotIn('avb_custom_key', flash_all.scripts(public)[flash_all.SH])
        self.assertEqual([['--set-active=a'], ['reboot']], flash_all.commands(record())[-2:])


if __name__ == '__main__':
    unittest.main()
