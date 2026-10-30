"""kernel compare: two builds of one pin match except for their signing keys, and a build against a reference set."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import kernel_compare as subject


def signed(payload, signature):
    info = bytes(8) + len(signature).to_bytes(4, 'big')
    return payload + signature + info + subject.MARKER


class CompareTests(unittest.TestCase):
    def run_dir(self, root, name, key, modules, extra=None):
        run = root / name
        (run / 'candidate/modules').mkdir(parents=True)
        (run / 'result.json').write_text(json.dumps({'status': 'PASS', 'source_commit': 'c', 'common_commit': 'g',
                                                     'packaging_recipe_sha256': 'p'}))
        (run / 'builtin-cert.der').write_bytes(b'cert-' + key)
        vdso = subject.BUILD_ID + bytes(range(20))
        (run / 'candidate/Image').write_bytes(b'head ' + vdso + b' cert-' + key + b' notes ' +
                                              subject.BUILD_ID + key.ljust(20, b'.') + b' tail')
        for module, payload in modules.items():
            (run / 'candidate/modules' / module).write_bytes(signed(payload, b'sig-' + key))
        for path, data in (extra or {}).items():
            (run / 'candidate' / path).write_bytes(data)
        return run

    def test_key_only_differences_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {'a.ko': b'code'}, {'dtbo.img': b'dt'})
            b = self.run_dir(root, 'b', b'two', {'a.ko': b'code'}, {'dtbo.img': b'dt'})
            result = subject.compare(a, b)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual((result['identical'], result['signature_only'], result['key_only']),
                             (1, 1, ['Image']))

    def test_other_differences_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {'a.ko': b'code', 'b.ko': b'x'}, {'dtbo.img': b'dt'})
            b = self.run_dir(root, 'b', b'two', {'a.ko': b'c0de'}, {'dtbo.img': b'dT'})
            result = subject.compare(a, b)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['different'], ['dtbo.img', 'modules/a.ko'])
            self.assertEqual(result['only_in_run'], ['modules/b.ko'])

    def test_runs_of_other_sources_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {})
            b = self.run_dir(root, 'b', b'two', {})
            (b / 'result.json').write_text(json.dumps({'status': 'PASS', 'source_commit': 'd', 'common_commit': 'g',
                                                       'packaging_recipe_sha256': 'p'}))
            self.assertRaisesRegex(ValueError, 'source_commit', subject.compare, a, b)


BOARD = """BOARD_SYSTEM_KERNEL_MODULES := $(FP6_KERNEL_PATH)/modules/z.ko
BOARD_VENDOR_RAMDISK_KERNEL_MODULES := $(FP6_KERNEL_PATH)/modules/a.ko $(FP6_KERNEL_PATH)/modules/dbg.ko
BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD := a.ko dbg.ko
BOARD_BLOCKLIST_FILE := $(FP6_KERNEL_PATH)/b
"""


class ReferenceTests(unittest.TestCase):
    """A build against a kernel set made another way."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / 'run'
        self.reference = self.root / 'reference'
        for top, board, modules in ((self.run / 'candidate', BOARD.replace(' $(FP6_KERNEL_PATH)/modules/dbg.ko', '')
                                     .replace(' dbg.ko', '') + 'BOARD_KERNEL_CMDLINE += scsi_mod.scan=sync\n', ['a.ko', 'z.ko']),
                                    (self.reference, BOARD, ['a.ko', 'dbg.ko', 'z.ko'])):
            (top / 'modules').mkdir(parents=True)
            (top / 'dtbs').mkdir()
            for name in modules:
                (top / 'modules' / name).write_bytes(b'module ' + name.encode())
            (top / 'BoardConfigKernel.mk').write_text(board)
            (top / 'dtbo.img').write_bytes(b'overlays')
            (top / 'dtbs/fp6.dtb').write_bytes(b'tree')
            (top / 'Image').write_bytes(b'image ' + top.name.encode())
        (self.run / 'result.json').write_text(json.dumps({'status': 'PASS', 'kernel_release': '6.18.37'}))
        (self.run / 'core.config').write_text('CONFIG_A=y\n# CONFIG_DEBUG is not set\n')
        (self.reference / 'config').write_text('CONFIG_A=y\nCONFIG_DEBUG=y\n')
        (self.reference / 'vendor_cmdline').write_text('bootconfig loglevel=8 panic=5 scsi_mod.scan=sync\n')
        self.metadata = lambda path: {'depends': ['x'], 'vermagic': [path.name]}
        patcher = patch.object(subject, 'decompile', lambda dtc, blob: Path(blob).read_text())
        patcher.start()
        self.addCleanup(patcher.stop)

    def compare(self, denied=('dbg',)):
        return subject.compare_reference(self.run, self.reference, set(denied), self.metadata, 'dtc')

    def test_a_denied_module_the_reference_ships_is_policy_not_a_difference(self):
        result = self.compare()
        self.assertEqual('PASS', result['status'], result['problems'])
        self.assertEqual(['dbg.ko'], result['modules']['denied_by_policy'])
        self.assertEqual({}, result['lists'])
        self.assertEqual(['scsi_mod.scan=sync'], result['command_line']['run'])
        self.assertEqual(['loglevel=8', 'panic=5'], result['command_line']['reference_bring_up_words'])
        self.assertEqual({'CONFIG_DEBUG': ['n', 'y']}, result['image']['configuration_differences'])

    def test_a_missing_module_a_changed_list_or_a_bring_up_word_fails(self):
        self.assertEqual(['dbg.ko'], self.compare(denied=())['modules']['only_in_reference'])
        self.assertEqual('FAIL', self.compare(denied=())['status'])
        (self.run / 'candidate/BoardConfigKernel.mk').write_text(
            (self.run / 'candidate/BoardConfigKernel.mk').read_text().replace('LOAD := a.ko', 'LOAD := z.ko a.ko'))
        self.assertTrue(self.compare()['lists']['BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD']['only_in_run'] == ['z.ko'])
        (self.run / 'candidate/BoardConfigKernel.mk').write_text(
            BOARD.replace(' $(FP6_KERNEL_PATH)/modules/dbg.ko', '').replace(' dbg.ko', '') + 'BOARD_KERNEL_CMDLINE += loglevel=8\n')
        result = self.compare()
        self.assertEqual(['loglevel=8'], result['command_line']['run_bring_up_words'])
        self.assertIn('bring-up words on the command line', result['problems'])

    def test_module_metadata_differences_are_reported_per_module(self):
        self.metadata = lambda path: {'depends': ['x' if 'candidate' in path.parts else 'y']}
        result = self.compare()
        self.assertEqual('FAIL', result['status'])
        self.assertEqual({'depends': [['x'], ['y']]}, result['modules']['metadata_differences']['a.ko'])

    def test_overlay_and_device_tree_differences_fail(self):
        (self.reference / 'dtbo.img').write_bytes(b'other overlays')
        result = self.compare()
        self.assertEqual('FAIL', result['status'])
        self.assertIn('overlay image differs', result['problems'])
        (self.reference / 'dtbo.img').write_bytes(b'overlays')
        (self.reference / 'dtbs/fp6.dtb').write_bytes(b'other tree')
        self.assertIn('device trees differ', self.compare()['problems'])
        (self.reference / 'dtbs/fp6.dtb').write_bytes(b'tree')
        (self.reference / 'dtbs/extra.dtb').write_bytes(b'tree')
        result = self.compare()
        self.assertIn('device trees differ', result['problems'])
        self.assertEqual([['fp6.dtb'], ['extra.dtb', 'fp6.dtb']], result['device_tree']['files'])

    def test_a_reference_comparison_needs_dtc(self):
        self.assertRaisesRegex(ValueError, 'needs dtc', subject.compare_reference, self.run, self.reference, set(), self.metadata, None)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            subject.main(['--run', str(self.run), '--reference', str(self.reference)])

    def test_a_debug_run_carries_the_bring_up_words_on_purpose(self):
        (self.run / 'candidate/BoardConfigKernel.mk').write_text(
            BOARD.replace(' $(FP6_KERNEL_PATH)/modules/dbg.ko', '').replace(' dbg.ko', '') + 'BOARD_KERNEL_CMDLINE += loglevel=8\n')
        self.assertIn('bring-up words on the command line', self.compare()['problems'])
        (self.run / 'result.json').write_text(json.dumps({'status': 'PASS', 'kernel_release': '6.18.37', 'config_profile': 'debug'}))
        self.assertNotIn('bring-up words on the command line', self.compare()['problems'])

    def test_the_order_of_listed_dependencies_does_not_matter(self):
        self.metadata = lambda path: {'depends': ['x,y' if 'candidate' in path.parts else 'y,x']}
        self.assertEqual('PASS', self.compare()['status'])

    def test_configuration_and_word_helpers(self):
        self.assertEqual({'CONFIG_B': ['n', 'y']}, subject.config_differences('# CONFIG_B is not set\n', 'CONFIG_B=y\n'))
        self.assertEqual(['initcall_debug'], subject.bring_up_words(['scsi_mod.scan=sync', 'initcall_debug']))
        self.assertEqual({'only_in_run': ['x'], 'only_in_reference': [], 'order_differs': False},
                         subject.list_differences(['a', 'x'], ['a']))
        self.assertTrue(subject.list_differences(['a', 'b'], ['b', 'a'])['order_differs'])


if __name__ == '__main__':
    unittest.main()
