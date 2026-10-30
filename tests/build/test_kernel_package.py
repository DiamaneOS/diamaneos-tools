import copy
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


class RequiredModuleTests(unittest.TestCase):
    ROW = {'module': 'rfkill.ko', 'partition': 'system_dlkm', 'needed_by': 'vendor/bin/daemon', 'reason': 'r'}
    SHIPPED = {'vendor/bin/daemon'}

    def recipe(self, **changes):
        return {**RECIPE, 'required_modules': [{**self.ROW, **changes}]}

    def test_a_module_the_shipped_vendor_file_needs_must_be_listed_and_in_the_load_list(self):
        self.assertEqual((['rfkill.ko'], []), package.check_required_modules(self.recipe(), self.SHIPPED))
        recipe = self.recipe()
        recipe['partitions'] = {**RECIPE['partitions'], 'system_dlkm': []}
        self.assertRaisesRegex(KernelError, 'rfkill.ko is needed by vendor/bin/daemon and is not in the system_dlkm module list',
                               package.check_required_modules, recipe, self.SHIPPED)
        recipe = self.recipe()
        recipe['load_lists'] = {**RECIPE['load_lists'], 'system_dlkm/modules.load': []}
        self.assertRaisesRegex(KernelError, 'rfkill.ko is needed by vendor/bin/daemon and is not in the system_dlkm load list',
                               package.check_required_modules, recipe, self.SHIPPED)

    def test_a_module_nothing_shipped_needs_is_skipped_when_unplaced_and_refused_when_placed(self):
        recipe = self.recipe()
        recipe['partitions'] = {k: [n for n in v if n != 'rfkill.ko'] for k, v in RECIPE['partitions'].items()}
        recipe['load_lists'] = {k: [n for n in v if n != 'rfkill.ko'] for k, v in RECIPE['load_lists'].items()}
        self.assertEqual(([], ['rfkill.ko']), package.check_required_modules(recipe, set()))
        # The file is not shipped but the module is: it ships for nothing, whichever list names it.
        self.assertRaisesRegex(KernelError, r'rfkill.ko is placed \(system_dlkm, system_dlkm/modules.load, vendor_boot\) and '
                                            'nothing shipped needs it: vendor/bin/daemon is not shipped',
                               package.check_required_modules, self.recipe(), set())
        recipe['load_lists']['vendor_boot/modules.load'] = ['rfkill.ko']
        self.assertRaisesRegex(KernelError, r'placed \(vendor_boot/modules.load\)', package.check_required_modules, recipe, set())

    def test_a_malformed_row_is_refused(self):
        for changes in ({'module': 'rfkill'}, {'partition': 'boot'}, {'reason': ' '}, {'needed_by': ''}):
            with self.subTest(changes=changes):
                self.assertRaisesRegex(KernelError, 'invalid required module', package.required_modules, self.recipe(**changes))
        self.assertRaisesRegex(KernelError, 'invalid required module', package.required_modules,
                               {'required_modules': [{**self.ROW, 'extra': 1}]})
        self.assertRaisesRegex(KernelError, 'listed twice', package.required_modules, {'required_modules': [self.ROW, self.ROW]})
        self.assertEqual([], package.required_modules({}))

    def test_the_committed_recipe_ships_the_tipc_module_nicmd_needs_as_6_1_does(self):
        import json
        top = Path(__file__).resolve().parents[2]
        recipe = json.loads((top / 'config/fp6-kernel-packaging.json').read_text())
        shipped = {row['path'] for row in json.loads((top / 'config/fp6-minimal/vendor-files.json').read_text())['files']}
        self.assertIn('vendor/bin/nicmd', shipped)
        self.assertEqual((['tipc.ko'], []), package.check_required_modules(recipe, shipped))
        # Without the vendor file the module may not ship.
        self.assertRaisesRegex(KernelError, 'tipc.ko is placed .* nothing shipped needs it', package.check_required_modules, recipe,
                               shipped - {'vendor/bin/nicmd'})
        self.assertEqual(['tipc.ko'], [r['module'] for r in recipe['required_modules']])
        self.assertIn('tipc.ko', recipe['partitions']['system_dlkm'])
        load = recipe['load_lists']['system_dlkm/modules.load']
        self.assertEqual(['8021q.ko', 'tipc.ko', 'rfkill.ko'], load[-3:])
        package.check_placement(recipe, lambda name: [])


class DebugPackagingTests(unittest.TestCase):
    def recipe(self):
        recipe = copy.deepcopy(RECIPE)
        recipe['kernel_cmdline'] = [{'argument': 'scsi_mod.scan=sync', 'reason': 'r'}]
        recipe['debug'] = {
            'kernel_cmdline': [{'argument': 'loglevel=8', 'reason': 'r'}, {'argument': 'reboot=panic_warm', 'reason': 'r'}],
            'modules': {'names': ['dbg1.ko', 'dbg-2.ko'], 'after': 'b-dev.ko', 'partitions': ['vendor_boot', 'vendor_dlkm'],
                        'reason': 'r'},
            'kmi_symbols': {'symbols': ['__might_sleep'], 'reason': 'r'}}
        return recipe

    def test_the_debug_words_are_allowed_where_production_forbids_them_and_may_not_repeat_a_production_word(self):
        recipe = self.recipe()
        self.assertEqual(['loglevel=8', 'reboot=panic_warm'], package.debug_arguments(recipe))
        self.assertRaisesRegex(KernelError, 'forbidden kernel argument: reboot=panic_warm', package.cmdline_arguments,
                               {'kernel_cmdline': recipe['debug']['kernel_cmdline']}, {'reboot'})
        recipe['debug']['kernel_cmdline'].append({'argument': 'scsi_mod.scan=async', 'reason': 'r'})
        self.assertRaisesRegex(KernelError, 'repeats a production argument', package.debug_arguments, recipe)

    def test_the_debug_modules_are_placed_after_their_anchor_in_the_second_stage_and_the_recovery_list(self):
        recipe = self.recipe()
        denied = {'dbg1', 'dbg_2'}
        placed = package.debug_recipe(recipe, denied)
        self.assertEqual(['a.ko', 'b-dev.ko', 'dbg1.ko', 'dbg-2.ko'], placed['load_lists']['vendor_boot/modules.load.recovery'])
        self.assertEqual(['b-dev.ko', 'dbg1.ko', 'dbg-2.ko', 'c.ko'], placed['load_lists']['vendor_dlkm/modules.load'])
        self.assertEqual(['a.ko', 'dbg-2.ko', 'dbg1.ko'], [n for n in placed['partitions']['vendor_boot'] if n != 'b-dev.ko' and n != 'rfkill.ko'])
        self.assertIn('dbg1.ko', placed['partitions']['vendor_dlkm'])
        self.assertNotIn('dbg1.ko', placed['partitions']['system_dlkm'])
        self.assertEqual(['a.ko'], placed['load_lists']['vendor_boot/modules.load'])
        # The production recipe is untouched.
        self.assertNotIn('dbg1.ko', recipe['partitions']['vendor_dlkm'])
        package.check_placement(placed, lambda n: [])

    def test_a_debug_module_must_be_denied_unplaced_and_follow_a_listed_module(self):
        recipe = self.recipe()
        self.assertRaisesRegex(KernelError, 'not on the deny list', package.debug_recipe, recipe, {'dbg1'})
        recipe['debug']['modules']['names'] = ['a.ko']
        self.assertRaisesRegex(KernelError, 'already placed', package.debug_recipe, recipe, {'a'})
        recipe = self.recipe()
        recipe['debug']['modules']['after'] = 'ghost.ko'
        self.assertRaisesRegex(KernelError, 'follow ghost.ko', package.debug_recipe, recipe, {'dbg1', 'dbg_2'})

    def test_the_debug_kmi_symbols_need_names_and_a_reason(self):
        recipe = self.recipe()
        self.assertEqual(['__might_sleep'], package.debug_kmi_symbols(recipe))
        for bad in ({'symbols': ['__might_sleep']}, {'symbols': [], 'reason': 'r'}, {'symbols': ['a', 'a'], 'reason': 'r'},
                    {'symbols': ['bad name'], 'reason': 'r'}, {'symbols': ['a'], 'reason': ' '},
                    {'symbols': ['a'], 'reason': 'r', 'extra': 1}):
            with self.subTest(bad=bad):
                recipe['debug']['kmi_symbols'] = bad
                self.assertRaisesRegex(KernelError, 'invalid debug KMI symbols', package.debug_kmi_symbols, recipe)
        del recipe['debug']['kmi_symbols']
        self.assertRaisesRegex(KernelError, 'invalid debug packaging', package.debug_arguments, recipe)

    def test_the_committed_debug_data_is_the_bring_up_command_line_and_the_five_debug_modules(self):
        import json
        from pathlib import Path as P
        recipe = json.loads((P(__file__).resolve().parents[2] / 'config/fp6-kernel-packaging.json').read_text())
        self.assertEqual(['loglevel=8', 'log_buf_len=4M', 'sysctl.kernel.panic_on_rcu_stall=1', 'panic=5',
                          'qcom_dload_mode.download_mode=1', 'qcom_scm.download_mode=1', 'reboot=panic_warm'],
                         package.debug_arguments(recipe))
        denied = {n.removesuffix('.ko').replace('-', '_') for g in recipe['denied_modules'] for n in g['modules']}
        placed = package.debug_recipe(recipe, denied)
        names = recipe['debug']['modules']['names']
        self.assertEqual(5, len(names))
        # The clock debug module would bind a node that must stay disabled.
        self.assertNotIn('debugcc-volcano.ko', names)
        second = placed['load_lists']['vendor_dlkm/modules.load']
        at = second.index(names[0])
        self.assertEqual(names, second[at:at + 5])
        self.assertEqual('qrtr-smd.ko', second[at - 1])
        self.assertEqual(len(recipe['partitions']['vendor_dlkm']) + 5, len(placed['partitions']['vendor_dlkm']))
        self.assertEqual(len(recipe['load_lists']['vendor_boot/modules.load.recovery']) + 5,
                         len(placed['load_lists']['vendor_boot/modules.load.recovery']))


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
