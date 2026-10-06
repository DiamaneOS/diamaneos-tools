import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from diamaneos_tools import kernel


def run_git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c',
                                    'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                                    '-c', 'protocol.file.allow=always', *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def repository(path, files):
    path.mkdir(parents=True)
    run_git(path, 'init', '-q')
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    run_git(path, 'add', '-A')
    run_git(path, 'commit', '-qm', 'fixture')
    return run_git(path, 'rev-parse', 'HEAD')


class KernelPreparationTests(unittest.TestCase):
    """The kernel repository, its submodule and toolchains, at their exact pins."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        header = 'struct drm_mode_config;\n'
        self.tool = repository(self.root / 'tool', {'bin/tool': 'tool\n'})
        self.common = repository(self.root / 'common', {
            'include/drm/mode.h': header, 'Makefile': 'VERSION = 6\nPATCHLEVEL = 1\nSUBLEVEL = 177\n'})
        source = self.root / 'source'
        self.revision = repository(source, {
            '.gitignore': '/kernel_platform/prebuilts/\n/kernel_platform/out/\n',
            '.gitmodules': '[submodule "kernel_platform/common"]\n\tpath = kernel_platform/common\n'
                           '\turl = ' + str(self.root / 'common') + '\n',
            'prebuilts.json': json.dumps({'prebuilts': [{'path': 'kernel_platform/prebuilts/tool',
                                                         'url': str(self.root / 'tool'),
                                                         'revision': self.tool}]}),
            'kernel_platform/msm-kernel/include/drm/mode.h': header,
            'vendor/qcom/proprietary/display-devicetree/display.dtsi': '/ {};\n'})
        (source / 'kernel_platform/common').mkdir()
        run_git(source, 'update-index', '--add', '--cacheinfo', '160000,' + self.common + ',kernel_platform/common')
        run_git(source, 'commit', '-qm', 'common')
        self.revision = run_git(source, 'rev-parse', 'HEAD')
        self.plan = {'repository': str(source), 'revision': self.revision}
        self.workspace = self.root / 'workspace'

    def prepare(self):
        with patch.object(kernel, 'configuration', return_value=self.plan), \
                patch.object(kernel, 'check_url', lambda url: None):
            return kernel.prepare(self.workspace)

    def verify(self):
        preparation = json.loads((self.workspace / 'preparation.json').read_text())
        with patch.object(kernel, 'check_url', lambda url: None):
            kernel.verify_source(self.workspace, self.plan, preparation['submodules'], preparation['prebuilts'])

    def test_prepares_the_pinned_commit_submodule_and_toolchains(self):
        result = self.prepare()
        self.assertEqual('PASS', result['status'])
        self.assertEqual(self.revision, result['source_commit'])
        self.assertEqual({'kernel_platform/common': self.common}, result['submodules'])
        self.assertEqual({'kernel_platform/prebuilts/tool': self.tool}, result['prebuilts'])
        self.assertEqual(1, result['shared_headers_checked'])
        self.assertEqual(self.revision, kernel.git(self.workspace, 'rev-parse', 'HEAD'))
        self.assertEqual(self.common, kernel.git(self.workspace / 'kernel_platform/common', 'rev-parse', 'HEAD'))
        self.assertEqual('tool\n', (self.workspace / 'kernel_platform/prebuilts/tool/bin/tool').read_text())
        manifest = (self.workspace / 'resolved-manifest.xml').read_text()
        self.assertIn('path="common" revision="' + self.common + '"', manifest)
        self.assertIn('path="msm-kernel" revision="' + self.revision + '"', manifest)
        self.assertEqual('6.1.177', kernel.linux_version(self.workspace / 'kernel_platform'))
        # The tools' own files do not show up as untracked sources.
        self.assertEqual('', kernel.git(self.workspace, 'status', '--porcelain'))
        self.assertEqual(result, self.prepare())

    def test_dirty_source_is_preserved_and_refused(self):
        self.prepare()
        edited = self.workspace / 'kernel_platform/msm-kernel/include/drm/mode.h'
        edited.write_text('owner edit\n')
        self.assertRaisesRegex(kernel.KernelError, 'tracked source changes', self.prepare)
        self.assertEqual('owner edit\n', edited.read_text())

    def test_untracked_input_is_rejected_but_bazel_links_are_not(self):
        self.prepare()
        (self.workspace / 'kernel_platform/bazel-out').symlink_to(self.root)
        self.verify()
        (self.workspace / 'vendor/unowned.c').write_text('extra')
        self.assertRaisesRegex(kernel.KernelError, 'untracked inputs: vendor/unowned.c', self.verify)

    def test_changed_toolchain_or_submodule_is_rejected(self):
        self.prepare()
        (self.workspace / 'kernel_platform/prebuilts/tool/bin/tool').write_text('changed\n')
        self.assertRaisesRegex(kernel.KernelError, 'prebuilt has local changes', self.verify)
        run_git(self.workspace / 'kernel_platform/prebuilts/tool', 'checkout', '-q', '--', '.')
        (self.workspace / 'kernel_platform/common/extra.h').write_text('extra')
        self.assertRaisesRegex(kernel.KernelError, 'kernel_platform/common', self.verify)

    def test_undeclared_nested_gitlink_stays_empty(self):
        # edk2 carries its upstream's own submodules; they are not initialised.
        source = Path(self.plan['repository'])
        (source / 'kernel_platform/bootable/nested').mkdir(parents=True)
        run_git(source, 'update-index', '--add', '--cacheinfo', '160000,' + self.tool + ',kernel_platform/bootable/nested')
        run_git(source, 'commit', '-qm', 'nested')
        self.plan['revision'] = run_git(source, 'rev-parse', 'HEAD')
        self.assertEqual({'kernel_platform/common': self.common}, self.prepare()['submodules'])
        nested = self.workspace / 'kernel_platform/bootable/nested'
        self.assertEqual([], list(nested.iterdir()))
        (nested / 'hidden.c').write_text('not seen by git status')
        self.assertRaisesRegex(kernel.KernelError, 'undeclared submodule directory is not empty', self.verify)

    def test_shared_header_drift_is_rejected(self):
        source = Path(self.plan['repository'])
        (source / 'kernel_platform/msm-kernel/include/drm/mode.h').write_text('struct other;\n')
        run_git(source, 'commit', '-qam', 'drift')
        self.plan['revision'] = run_git(source, 'rev-parse', 'HEAD')
        self.assertRaisesRegex(kernel.KernelError, 'shared headers differ', self.prepare)
        self.assertFalse((self.workspace / 'preparation.json').exists())

    def test_unpinned_or_insecure_prebuilts_are_rejected(self):
        source = Path(self.plan['repository'])
        for entry in ({'path': 'kernel_platform/prebuilts/tool', 'url': str(self.root / 'tool'), 'revision': 'main'},
                      {'path': 'elsewhere/tool', 'url': str(self.root / 'tool'), 'revision': self.tool}):
            with self.subTest(entry=entry):
                (source / 'prebuilts.json').write_text(json.dumps({'prebuilts': [entry]}))
                with self.assertRaises(kernel.KernelError):
                    kernel.prebuilt_plan(source)
        with self.assertRaisesRegex(kernel.KernelError, 'HTTPS'):
            kernel.check_url('http://example.invalid/tool')

    def test_old_per_project_workspace_and_other_repositories_are_refused(self):
        (self.workspace / 'kernel_platform/common').mkdir(parents=True)
        self.assertRaisesRegex(kernel.KernelError, 'holds other files', self.prepare)
        other = self.root / 'other'
        repository(other, {'README': 'not a kernel\n'})
        self.workspace = other
        self.assertRaisesRegex(kernel.KernelError, 'another Git repository', self.prepare)
        self.assertEqual('not a kernel\n', (other / 'README').read_text())

    def test_build_refuses_a_preparation_from_another_pin(self):
        self.prepare()
        self.plan['revision'] = '0' * 40
        with self.assertRaisesRegex(kernel.KernelError, 'another pin'):
            kernel.prepared(self.workspace, self.plan)

    def test_workspace_symlink_rejected(self):
        self.workspace.symlink_to(self.root, target_is_directory=True)
        self.assertRaisesRegex(kernel.KernelError, 'symlink', self.prepare)

    def test_committed_source_plan_names_one_https_commit(self):
        plan = kernel.configuration()
        self.assertEqual('https://github.com/DiamaneOS/kernel_qcom-6.1', plan['repository'])
        self.assertRegex(plan['revision'], '^[0-9a-f]{40}$')

    def test_missing_verification_tools_reject_before_workspace_changes(self):
        with patch.dict('os.environ', {'PATH': ''}):
            with self.assertRaisesRegex(kernel.KernelError, 'missing from PATH: modinfo, modprobe'):
                kernel.build(self.workspace, 1, 60)
        self.assertFalse(self.workspace.exists())


if __name__=='__main__':unittest.main()


class KernelDenyListTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_recipe_keeps_denied_modules_out(self):
        denied = kernel.denied_modules(self.recipe)
        for name in ('can.ko', 'nfc.ko', 'bluetooth.ko', 'focaltech_fts.ko', 'f_fs_ipc_log.ko', 'eud.ko'):
            self.assertIn(name, denied)
        # Kept on purpose: glink_probe imports qcom_glink_spss, KGSL imports
        # coresight and the audio machine driver wcd937x.
        for name in ('qcom_glink_spss.ko', 'coresight.ko', 'wcd937x_dlkm.ko'):
            self.assertNotIn(name, denied)
            self.assertTrue(any(name in names for names in self.recipe['partitions'].values()))

    def test_regenerated_list_with_a_denied_module_is_rejected(self):
        for key in ('partitions', 'load_lists'):
            with self.subTest(key=key):
                recipe = copy.deepcopy(self.recipe)
                target = 'system_dlkm' if key == 'partitions' else 'system_dlkm/modules.load'
                recipe[key][target].append('can.ko')
                self.assertRaisesRegex(kernel.KernelError, 'denied module in the packaging recipe: can.ko',
                                       kernel.denied_modules, recipe)

    def test_dash_and_underscore_spellings_match(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['partitions']['vendor_dlkm'].append('snd_soc_hdmi_codec.ko')
        self.assertRaisesRegex(kernel.KernelError, 'snd_soc_hdmi_codec.ko', kernel.denied_modules, recipe)
        recipe = copy.deepcopy(self.recipe)
        recipe['denied_modules'].append({'modules': ['snd_soc_hdmi_codec.ko'], 'reason': 'again'})
        self.assertRaisesRegex(kernel.KernelError, 'duplicate denied module', kernel.denied_modules, recipe)

    def test_every_denied_group_states_a_reason(self):
        for group in ({'modules': ['x.ko'], 'reason': ' '}, {'modules': [], 'reason': 'r'},
                      {'modules': ['x.ko'], 'reason': 'r', 'extra': 1}, {'modules': ['../x.ko'], 'reason': 'r'}):
            with self.subTest(group=group):
                recipe = copy.deepcopy(self.recipe)
                recipe['denied_modules'] = [group]
                self.assertRaises(kernel.KernelError, kernel.denied_modules, recipe)


class KernelPackagingBlocklistTests(unittest.TestCase):
    def test_first_stage_ramdisk_uses_the_vendor_blocklist(self):
        recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')
        blocklists = recipe['blocklists']
        self.assertEqual(blocklists['vendor_boot/modules.blocklist'],
                         blocklists['vendor_dlkm/modules.blocklist'])
        self.assertIn('blocklist llcc_perfmon\n', blocklists['vendor_boot/modules.blocklist'])
        recovery = recipe['load_lists']['vendor_boot/modules.load.recovery']
        self.assertIn('llcc_perfmon.ko', recovery)

    def test_rendered_board_config_installs_ramdisk_blocklist(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            merged = temp / 'merged'; merged.mkdir()
            (merged / 'dtbo.img').write_bytes(b'dtbo')
            image = temp / 'Image'; image.write_bytes(b'kernel')
            recipe = {'partitions': {'vendor_boot': [], 'vendor_dlkm': [], 'system_dlkm': []},
                      'load_lists': {},
                      'blocklists': {'vendor_boot/modules.blocklist': 'blocklist a\n',
                                     'vendor_dlkm/modules.blocklist': 'blocklist a\n'}}
            candidate = temp / 'candidate'
            kernel.render_package(candidate, {}, merged, image, recipe, temp / 'strip', temp)
            board = (candidate / 'BoardConfigKernel.mk').read_text()
            self.assertIn('BOARD_VENDOR_RAMDISK_KERNEL_MODULES_BLOCKLIST_FILE := '
                          '$(FP6_KERNEL_PATH)/vendor_boot-modules.blocklist\n', board)
            self.assertIn('BOARD_VENDOR_KERNEL_MODULES_BLOCKLIST_FILE := '
                          '$(FP6_KERNEL_PATH)/vendor_dlkm-modules.blocklist\n', board)
            self.assertEqual((candidate / 'vendor_boot-modules.blocklist').read_text(), 'blocklist a\n')


class KernelSymbolRuleTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_rules_parse(self):
        self.assertEqual({'register_kretprobe': ['dwc3-msm.ko']}, kernel.import_allowlist(self.recipe))
        self.assertEqual([], kernel.forbidden_symbols(self.recipe))
        self.assertEqual(['names_command_line'], kernel.required_symbols(self.recipe))

    def test_import_allowlist_requires_exact_importers(self):
        undefined = {'dwc3-msm.ko': {'register_kretprobe', 'printk'}, 'other.ko': {'printk'}}
        modules = {name: name for name in undefined}
        kernel.check_module_imports(modules, {'register_kretprobe': ['dwc3-msm.ko']}, undefined.get)
        undefined['other.ko'].add('register_kretprobe')
        with self.assertRaisesRegex(kernel.KernelError, 'other.ko'):
            kernel.check_module_imports(modules, {'register_kretprobe': ['dwc3-msm.ko']}, undefined.get)
        with self.assertRaisesRegex(kernel.KernelError, 'none'):
            kernel.check_module_imports(modules, {'missing_symbol': ['dwc3-msm.ko']}, undefined.get)

    def test_nm_output_and_system_map_parsing(self):
        self.assertEqual({'register_kretprobe', '__stack_chk_fail'},
                         kernel.undefined_symbols('                 U register_kretprobe\n'
                                                  '                 U __stack_chk_fail\n'))
        kernel.check_forbidden_symbols('ffffffc080000000 T _text\n', ['param_name_len'])
        with self.assertRaisesRegex(kernel.KernelError, 'param_name_len'):
            kernel.check_forbidden_symbols('ffffffc080001000 t param_name_len\n', ['param_name_len'])

    def test_required_symbols_must_be_in_the_system_map(self):
        kernel.check_required_symbols('ffffffc082000000 d names_command_line\n', ['names_command_line'])
        # ThinLTO's suffix for a promoted static symbol still counts.
        kernel.check_required_symbols('ffffffc082000000 d names_command_line.llvm.123\n', ['names_command_line'])
        for system_map in ('ffffffc080000000 T _text\n', 'ffffffc082000000 d names_command_line2\n'):
            with self.subTest(system_map=system_map):
                with self.assertRaisesRegex(kernel.KernelError, 'required kernel symbol missing: names_command_line'):
                    kernel.check_required_symbols(system_map, ['names_command_line'])

    def test_rules_need_reasons_and_safe_names(self):
        for rules in ({'register_kretprobe': {'modules': ['x.ko'], 'reason': ' '}},
                      {'bad symbol': {'modules': ['x.ko'], 'reason': 'r'}},
                      {'s': {'modules': ['../x.ko'], 'reason': 'r'}}):
            with self.subTest(rules=rules):
                self.assertRaises(kernel.KernelError, kernel.import_allowlist, {'module_import_allowlist': rules})
        self.assertRaises(kernel.KernelError, kernel.forbidden_symbols,
                          {'forbidden_symbols': [{'symbol': 'x', 'reason': ''}]})
        for rules in ([{'symbol': 'x', 'reason': ''}], [{'symbol': 'bad symbol', 'reason': 'r'}],
                      [{'symbol': 'x', 'reason': 'r', 'extra': 1}], {'symbol': 'x'}):
            with self.subTest(required=rules):
                self.assertRaises(kernel.KernelError, kernel.required_symbols, {'required_symbols': rules})

    def test_clang_comes_from_the_pinned_build_constants(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'common').mkdir()
            (work / 'common/build.config.constants').write_text('BRANCH=android14-6.1\nCLANG_VERSION=r487747c\n')
            with self.assertRaisesRegex(kernel.KernelError, 'clang-r487747c'):
                kernel.clang_bin(work)
            (work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin').mkdir(parents=True)
            (work / 'prebuilts/clang/host/linux-x86/clang-r999/bin').mkdir(parents=True)
            self.assertEqual(work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin', kernel.clang_bin(work))
