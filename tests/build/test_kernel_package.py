from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel_package as package
from diamaneos_tools.kernel_util import KernelError

RECIPE = {
    'partitions': {'system_dlkm': ['rfkill.ko'], 'vendor_boot': ['a.ko', 'b-dev.ko', 'rfkill.ko'], 'vendor_dlkm': ['a.ko', 'b-dev.ko', 'c.ko']},
    'load_lists': {'vendor_boot/modules.load': ['a.ko'], 'vendor_boot/modules.load.recovery': ['a.ko', 'b-dev.ko'],
                   'system_dlkm/modules.load': ['rfkill.ko'], 'vendor_dlkm/modules.load': ['b-dev.ko', 'c.ko']},
    'blocklists': {'vendor_boot/modules.blocklist': 'blocklist x\n', 'vendor_dlkm/modules.blocklist': 'blocklist y\n'},
}
DEPENDS = {'a.ko': ['rfkill'], 'b-dev.ko': ['a'], 'c.ko': ['b_dev'], 'rfkill.ko': []}


class PlacementTests(unittest.TestCase):
    def test_select_prefers_the_vendor_build_and_refuses_a_name_both_make(self):
        selected = package.select({'a.ko', 'rfkill.ko'}, {'a.ko': Path('v/a.ko')}, {'rfkill.ko': Path('c/rfkill.ko')})
        self.assertEqual({'a.ko': Path('v/a.ko'), 'rfkill.ko': Path('c/rfkill.ko')}, selected)
        self.assertRaisesRegex(KernelError, 'built by the vendor and the core', package.select, {'a.ko'},
                               {'a.ko': Path('v')}, {'a.ko': Path('c')})
        self.assertRaisesRegex(KernelError, 'was not built: z.ko', package.select, {'z.ko'}, {}, {})

    def test_placement_accepts_dependencies_in_the_partition_or_system_dlkm(self):
        package.check_placement(RECIPE, lambda n: DEPENDS[n])

    def test_placement_refuses_a_missing_dependency_and_a_load_list_outside_its_partition(self):
        recipe = {**RECIPE, 'partitions': {**RECIPE['partitions'], 'vendor_boot': ['a.ko', 'b-dev.ko']}}
        self.assertRaisesRegex(KernelError, 'a.ko .vendor_boot. needs rfkill.ko', package.check_placement, recipe,
                               lambda n: DEPENDS[n])
        recipe = {**RECIPE, 'load_lists': {**RECIPE['load_lists'], 'vendor_dlkm/modules.load': ['zzz.ko']}}
        self.assertRaisesRegex(KernelError, 'escapes partition', package.check_placement, recipe, lambda n: DEPENDS[n])
        self.assertRaisesRegex(KernelError, 'which no partition lists', package.check_placement, RECIPE,
                               lambda n: DEPENDS[n] + ['ghost'])

    def test_command_line_words_are_checked_against_the_forbidden_ones(self):
        word = {'argument': 'scsi_mod.scan=sync', 'reason': 'stable disk letters'}
        self.assertEqual(['scsi_mod.scan=sync'], package.cmdline_arguments({'kernel_cmdline': [word]}, {'reboot'}))
        self.assertEqual([], package.cmdline_arguments({}, set()))
        for bad in ({'argument': 'reboot=warm', 'reason': 'x'}, {'argument': 'bad word', 'reason': 'x'},
                    {'argument': 'a=b', 'reason': ' '}):
            with self.subTest(bad=bad):
                self.assertRaises(KernelError, package.cmdline_arguments, {'kernel_cmdline': [bad]}, {'reboot'})
        self.assertRaisesRegex(KernelError, 'duplicate', package.cmdline_arguments, {'kernel_cmdline': [word, word]}, set())

    def test_board_config_names_modules_lists_command_line_and_blocklists(self):
        text = package.board_config(RECIPE, ['scsi_mod.scan=sync'])
        lines = dict(l.split(' := ', 1) if ' := ' in l else l.split(' += ', 1) for l in text.splitlines() if ' := ' in l or ' += ' in l)
        self.assertEqual('$(FP6_KERNEL_PATH)/modules/rfkill.ko', lines['BOARD_SYSTEM_KERNEL_MODULES'])
        self.assertEqual('a.ko b-dev.ko', lines['BOARD_VENDOR_RAMDISK_RECOVERY_KERNEL_MODULES_LOAD'])
        self.assertEqual('b-dev.ko c.ko', lines['BOARD_VENDOR_KERNEL_MODULES_LOAD'])
        self.assertEqual('scsi_mod.scan=sync', lines['BOARD_KERNEL_CMDLINE'])
        self.assertEqual('$(FP6_KERNEL_PATH)/vendor_dlkm-modules.blocklist', lines['BOARD_VENDOR_KERNEL_MODULES_BLOCKLIST_FILE'])
        self.assertNotIn('BOARD_KERNEL_CMDLINE', package.board_config(RECIPE, []))


class StagingTests(unittest.TestCase):
    """Strip and sign with stand-in tools."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.sources = {}
        for name in ('a.ko', 'b-dev.ko'):
            path = self.top / 'src' / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'ELF debug ' + name.encode())
            self.sources[name] = path
        self.signed = set()

    def fake_call(self, argv, cwd=None, env=None):
        argv = [str(a) for a in argv]
        if argv[0].endswith('strip'):
            shutil.copyfile(argv[-1], argv[-2])
        elif argv[0].endswith('sign-file'):
            Path(argv[-1]).write_bytes(Path(argv[-1]).read_bytes() + b'SIG')
            self.signed.add(argv[-1])
        elif argv[:2] == ['modprobe', '--dump-modversions']:
            return '0x00000001\tsymbol\n'
        return ''

    def metadata(self, path):
        data = Path(path).read_bytes()
        rows = [(b'name', Path(path).stem.encode()), (b'vermagic', b'6.18.37')]
        return rows + ([(b'signer', b'build'), (b'sig_key', b'AB'), (b'signature', b'x')] if data.endswith(b'SIG') else [])

    def test_modules_are_stripped_signed_and_keep_their_metadata(self):
        with patch.object(package, 'call', self.fake_call):
            package.stage_modules(self.top / 'staged', self.sources, 'strip', ('sign-file', 'k', 'c', 'sha256'),
                                  self.metadata, self.top, self.top)
        self.assertEqual(['a.ko', 'b-dev.ko'], sorted(p.name for p in (self.top / 'staged').iterdir()))
        self.assertTrue((self.top / 'staged/a.ko').read_bytes().endswith(b'SIG'))

    def test_signing_that_changes_metadata_stops_the_build(self):
        def changing(path):
            rows = self.metadata(path)
            return rows + [(b'extra', b'1')] if Path(path).parent.name == 'staged' else rows
        with patch.object(package, 'call', self.fake_call):
            self.assertRaisesRegex(KernelError, 'changed module metadata', package.stage_modules, self.top / 'staged',
                                   self.sources, 'strip', ('sign-file', 'k', 'c', 'sha256'), changing, self.top, self.top)

    def test_render_writes_the_layout_the_android_build_reads(self):
        staged = self.top / 'staged'
        staged.mkdir()
        (staged / 'a.ko').write_bytes(b'm')
        (self.top / 'Image').write_bytes(b'kernel')
        out = self.top / 'candidate'
        package.render_package(out, staged, self.top / 'Image', {'fp6.dtb': b'dtb'}, b'dtbo', RECIPE, ['scsi_mod.scan=sync'])
        self.assertEqual(['BoardConfigKernel.mk', 'Image', 'device-kernel.mk', 'dtbo.img', 'dtbs', 'modules',
                          'vendor_boot-modules.blocklist', 'vendor_dlkm-modules.blocklist'],
                         sorted(p.name for p in out.iterdir()))
        self.assertEqual(b'dtb', (out / 'dtbs/fp6.dtb').read_bytes())
        self.assertEqual('blocklist y\n', (out / 'vendor_dlkm-modules.blocklist').read_text())
        self.assertIn('Image:kernel', (out / 'device-kernel.mk').read_text())


if __name__ == '__main__':
    unittest.main()
