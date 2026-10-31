import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel, kernel_util


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
    """The vendor and common repositories, the toolchain and the base device tree, at their exact pins."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        header = 'struct drm_mode_config;\n'
        self.vendor_rev = repository(self.root / 'vendor', {
            'modules/base/core-headers.json': json.dumps({'include/drm/mode.h': 'include/drm/mode.h'}),
            'modules/base/include/drm/mode.h': header, 'modules/base/Makefile': 'all:\n'})
        self.common_rev = repository(self.root / 'common', {
            'include/drm/mode.h': header, 'Makefile': 'VERSION = 6\nPATCHLEVEL = 18\nSUBLEVEL = 37\n'})
        self.tool_rev = repository(self.root / 'tool', {'bin/tool': 'tool\n', 'unused/big.bin': 'x\n'})
        self.dt_rev = repository(self.root / 'dt', {'dtbs/fp6.dtb': 'dtb', 'dtbo.img': 'dtbo'})
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.plan = {'schema_version': 2,
                     'vendor': {'path': 'vendor', 'url': str(self.root / 'vendor'), 'revision': self.vendor_rev},
                     'common': {'path': 'common', 'url': str(self.root / 'common'), 'revision': self.common_rev},
                     'toolchain': [{'name': 'tool', 'path': 'toolchain/tool', 'url': str(self.root / 'tool'),
                                    'revision': self.tool_rev}]}
        self.dt_plan = {'schema_version': 2, 'scope': 'x', 'differences': [],
                        'base': {'repository': str(self.root / 'dt'), 'revision': self.dt_rev, 'overlay_count': 1,
                                 'files': {'dtbs/fp6.dtb': sha('dtb'), 'dtbo.img': sha('dtbo')}}}
        self.workspace = self.root / 'workspace'

    def prepare(self, overrides=None):
        with patch.object(kernel, 'configuration', return_value=(self.plan, self.dt_plan)):
            return kernel.prepare(self.workspace, overrides)

    def prepared(self):
        return kernel.prepared(self.workspace, self.plan, self.dt_plan)

    def test_prepares_every_pinned_checkout(self):
        result = self.prepare()
        self.assertEqual('PASS', result['status'])
        self.assertEqual({'vendor', 'common', 'tool', 'base-dt'}, set(result['projects']))
        self.assertEqual(self.vendor_rev, kernel_util.git(self.workspace / 'vendor', 'rev-parse', 'HEAD'))
        self.assertEqual(self.common_rev, result['projects']['common']['revision'])
        self.assertEqual('tool\n', (self.workspace / 'toolchain/tool/bin/tool').read_text())
        self.assertEqual(1, result['core_header_copies_checked'])
        self.assertEqual('6.18.37', kernel.linux_version(self.workspace / 'common'))
        self.assertEqual(result, self.prepare())

    def test_a_local_repository_replaces_the_url_of_a_fetch_and_never_the_pin(self):
        self.plan['vendor']['url'] = 'https://unreachable.invalid/kernel_qcom-6.18'
        other = self.root / 'other-vendor'
        subprocess.check_call(['git', 'clone', '-q', str(self.root / 'vendor'), str(other)])
        result = self.prepare({'vendor': str(other)})
        self.assertEqual(['vendor'], result['fetched_from_overrides'])
        self.assertEqual(self.vendor_rev, result['projects']['vendor']['revision'])
        self.assertRaisesRegex(kernel.KernelError, 'unknown source to override: nothing', self.prepare, {'nothing': str(other)})
        self.assertRaisesRegex(kernel.KernelError, 'not an absolute path to a Git repository', self.prepare, {'common': str(self.root)})
        os.chdir(self.root)
        self.addCleanup(os.chdir, '/')
        subprocess.check_call(['git', 'init', '-q', '--bare', 'bare.git'])
        self.assertRaisesRegex(kernel.KernelError, 'not an absolute path', self.prepare, {'common': 'bare.git'})

    def test_an_override_without_the_pinned_commit_is_refused(self):
        other = self.root / 'unrelated'
        repository(other, {'a': 'b'})
        self.plan['common']['revision'] = '1' * 40
        self.assertRaises(kernel_util.KernelError, self.prepare, {'common': str(other)})

    def test_dirty_source_is_preserved_and_refused(self):
        self.prepare()
        edited = self.workspace / 'common/include/drm/mode.h'
        edited.write_text('local edit\n')
        self.assertRaisesRegex(kernel.KernelError, 'tracked source changes', self.prepare)
        self.assertEqual('local edit\n', edited.read_text())

    def test_untracked_inputs_and_changed_toolchain_are_rejected_when_building(self):
        self.prepare()
        self.prepared()
        (self.workspace / 'vendor/modules/unowned.c').write_text('extra')
        self.assertRaisesRegex(kernel.KernelError, 'untracked inputs: vendor', self.prepared)
        (self.workspace / 'vendor/modules/unowned.c').unlink()
        (self.workspace / 'toolchain/tool/bin/tool').write_text('changed\n')
        self.assertRaisesRegex(kernel.KernelError, 'source changes or untracked inputs: toolchain/tool', self.prepared)

    def test_core_header_drift_is_rejected(self):
        (self.root / 'vendor/modules/base/include/drm/mode.h').write_text('struct other;\n')
        run_git(self.root / 'vendor', 'commit', '-qam', 'drift')
        self.plan['vendor']['revision'] = run_git(self.root / 'vendor', 'rev-parse', 'HEAD')
        self.assertRaisesRegex(kernel.KernelError, 'core header copy differs', self.prepare)
        self.assertFalse((self.workspace / 'preparation.json').exists())

    def test_base_device_tree_must_match_its_digest(self):
        self.dt_plan['base']['files']['dtbo.img'] = '0' * 64
        self.assertRaisesRegex(kernel.KernelError.__mro__[1], 'differs from its pin', self.prepare)

    def test_other_files_symlinks_and_stale_preparations_are_refused(self):
        self.workspace.mkdir()
        (self.workspace / 'stray').write_text('x')
        self.assertRaisesRegex(kernel.KernelError, 'holds other files', self.prepare)
        (self.workspace / 'stray').unlink()
        self.prepare()
        other = self.root / 'other-plan.json'
        other.write_text('{}')
        with patch.object(kernel, 'SOURCE_PLAN', other):
            self.assertRaisesRegex(kernel.KernelError, 'other pins', self.prepared)
        self.plan['vendor']['revision'] = '0' * 40
        self.assertRaisesRegex(kernel.KernelError, 'not at the pin: vendor', self.prepared)

    def test_changing_the_differences_does_not_ask_for_a_new_preparation_but_a_new_base_does(self):
        self.prepare()
        self.dt_plan['differences'] = [{'id': 'x'}]
        self.prepared()
        self.dt_plan['base']['overlay_count'] = 2
        self.assertRaisesRegex(kernel.KernelError, 'other pins', self.prepared)

    def test_workspace_symlink_rejected(self):
        self.workspace.symlink_to(self.root, target_is_directory=True)
        self.assertRaisesRegex(kernel.KernelError, 'symlink', self.prepare)

    def test_missing_verification_tools_reject_before_workspace_changes(self):
        with patch.dict('os.environ', {'PATH': ''}):
            with self.assertRaisesRegex(kernel.KernelError, 'missing from PATH: modinfo, modprobe'):
                kernel.build(self.workspace, 1, 60)
        self.assertFalse(self.workspace.exists())


class SourcePlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((kernel.ROOT / 'config/kernel-sources-fp6.json').read_text())

    def test_the_committed_plan_pins_two_repositories_and_the_toolchain(self):
        plan, dt_plan = kernel.configuration()
        names = [row['name'] for row in kernel.projects(plan, dt_plan)]
        self.assertEqual(['vendor', 'common', 'clang', 'rust', 'clang-tools', 'kernel-build-tools', 'build-tools',
                          'host-libraries', 'host-zlib', 'base-dt'], names)
        for row in kernel.projects(plan, dt_plan):
            self.assertRegex(row['revision'], '^[0-9a-f]{40}$')

    def test_plans_need_https_urls_exact_commits_and_distinct_names(self):
        for change in (lambda p: p['vendor'].update(url='http://example.invalid/x'),
                       lambda p: p['common'].update(revision='main'),
                       lambda p: p['toolchain'][0].update(name='vendor'),
                       lambda p: p['toolchain'][1].update(path=p['toolchain'][0]['path']),
                       lambda p: p['toolchain'][0].update(sparse=['../x']),
                       lambda p: p.update(toolchain=[]),
                       lambda p: p.update(extra=1)):
            with self.subTest(change=change):
                plan = copy.deepcopy(self.plan)
                change(plan)
                self.assertRaises(kernel.KernelError, kernel.validate_plan, plan)

    def test_overrides_are_command_line_pairs(self):
        with patch.object(kernel, 'prepare', return_value={}) as prepare:
            kernel.main(['prepare', '--workspace', '/tmp/ws', '--source', 'vendor=/tmp/v'])
        prepare.assert_called_once()
        self.assertEqual({'vendor': '/tmp/v'}, prepare.call_args[0][1])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, kernel.main(['prepare', '--workspace', '/tmp/ws', '--source', 'vendor']))
            self.assertEqual(2, kernel.main(['build', '--workspace', '/tmp/ws', '--source', 'vendor=/tmp/v']))


class KernelDenyListTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_recipe_keeps_denied_modules_out(self):
        denied = kernel.denied_modules(self.recipe)
        for name in ('can.ko', 'nfc.ko', 'bluetooth.ko', 'focaltech_fts.ko', 'icc-test.ko', 'memory_dump_v2.ko',
                     'dmesg_dumper.ko', 'sysmon_subsystem_stats.ko', 'gpu_dump_skip_cdev.ko', 'coresight.ko'):
            self.assertIn(name, denied)
        # Kept on purpose: glink_probe imports qcom_glink_spss, the audio machine
        # driver wcd937x, and stmvl53l1 is the laser autofocus sensor, not STM.
        for name in ('qcom_glink_spss.ko', 'wcd937x_dlkm.ko', 'stmvl53l1.ko'):
            self.assertNotIn(name, denied)
            self.assertTrue(any(name in names for names in self.recipe['partitions'].values()))

    def test_regenerated_list_with_a_denied_module_is_rejected(self):
        vendor = (('partitions', 'vendor_boot'), ('partitions', 'vendor_dlkm'),
                  ('load_lists', 'vendor_dlkm/modules.load'))
        rows = [('partitions', 'system_dlkm', 'can.ko'), ('load_lists', 'system_dlkm/modules.load', 'can.ko')]
        # The legacy qseecom driver in both spellings, and the HDCP client.
        rows += [(key, target, spelling)
                 for key, target in vendor + (('load_lists', 'vendor_boot/modules.load.recovery'),)
                 for spelling in ('qseecom_dlkm.ko', 'qseecom-dlkm.ko')]
        rows += [(key, target, 'hdcp_qseecom_dlkm.ko') for key, target in vendor]
        rows += [(key, target, 'debugcc-volcano.ko') for key, target in vendor]
        for key, target, name in rows:
            with self.subTest(key=key, target=target, name=name):
                recipe = copy.deepcopy(self.recipe)
                recipe[key][target].append(name)
                self.assertRaisesRegex(kernel.KernelError, 'denied module in the packaging recipe: ' + name,
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


class KernelPackagingRecipeTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_first_stage_ramdisk_uses_the_vendor_blocklist(self):
        blocklists = self.recipe['blocklists']
        self.assertEqual(blocklists['vendor_boot/modules.blocklist'], blocklists['vendor_dlkm/modules.blocklist'])
        self.assertIn('blocklist llcc_perfmon\n', blocklists['vendor_boot/modules.blocklist'])
        self.assertIn('llcc_perfmon.ko', self.recipe['load_lists']['vendor_boot/modules.load.recovery'])

    def test_lists_are_sorted_unique_and_complete_in_load_order(self):
        for partition, names in self.recipe['partitions'].items():
            self.assertEqual(sorted(names), names, partition)
            self.assertEqual(len(names), len(set(names)))
        for key, names in self.recipe['load_lists'].items():
            self.assertEqual(len(names), len(set(names)), key)
            self.assertLessEqual(set(names), set(self.recipe['partitions'][key.split('/')[0]]), key)
        first, second = self.recipe['load_lists']['vendor_boot/modules.load'], self.recipe['load_lists']['vendor_dlkm/modules.load']
        recovery = self.recipe['load_lists']['vendor_boot/modules.load.recovery']
        self.assertEqual(first + second, recovery)
        # Every vendor module is in vendor_dlkm, the first stage ones as well.
        self.assertLessEqual(set(first) | set(second), set(self.recipe['partitions']['vendor_dlkm']))

    def test_the_production_command_line_is_the_stable_disk_order_only(self):
        from diamaneos_tools import kernel_package
        forbidden = set(kernel.load_json(kernel.ROOT / 'config/fp6-build.json')['kernel_arguments']['forbidden'])
        self.assertEqual(['scsi_mod.scan=sync'], kernel_package.cmdline_arguments(self.recipe, forbidden))

    def test_the_cmdline_of_the_board_config_has_no_bring_up_words(self):
        from diamaneos_tools import kernel_compare, kernel_package
        words = kernel_package.cmdline_arguments(self.recipe, set())
        self.assertEqual([], kernel_compare.bring_up_words(words))

    def test_only_the_fp6_device_tree_ships(self):
        self.assertEqual(['fp6.dtb'], kernel.shipped_dtbs(self.recipe))
        self.assertEqual(kernel.load_json(kernel.DT_PLAN)['base']['overlay_count'], self.recipe['dtbo_count'])
        for bad in (None, [], ['fp6.dtb', 'fp6.dtb'], ['../fp6.dtb'], ['fp6.dtbo']):
            self.assertRaises(kernel.KernelError, kernel.shipped_dtbs, {'dtbs': bad})


class KernelSymbolRuleTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_rules_parse_and_the_hardened_changes_are_required(self):
        self.assertEqual({'register_kretprobe': ['dwc3-msm.ko']}, kernel.import_allowlist(self.recipe))
        self.assertEqual([], kernel.forbidden_symbols(self.recipe))
        required, pending = kernel.required_symbols(self.recipe)
        self.assertEqual(['names_command_line', 'selinux_enforcing_lock'], sorted(required))
        self.assertEqual({}, pending)

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

    def test_a_pending_symbol_is_reported_when_missing_and_refused_when_present(self):
        pending = {'names_command_line': 'not carried'}
        self.assertEqual(['names_command_line'], kernel.check_required_symbols('ffffffc080000000 T _text\n', [], pending))
        with self.assertRaisesRegex(kernel.KernelError, 'remove its pending reason: names_command_line'):
            kernel.check_required_symbols('ffffffc082000000 d names_command_line\n', [], pending)

    def test_the_production_profile_counts_a_pending_symbol_as_missing(self):
        pending = {'names_command_line': 'not carried'}
        absent = 'ffffffc080000000 T _text\n'
        self.assertEqual(['names_command_line'], kernel.check_required_symbols(absent, [], pending, 'development'))
        with self.assertRaisesRegex(kernel.KernelError, 'required kernel symbol missing: names_command_line'):
            kernel.check_required_symbols(absent, [], pending, 'production')
        with self.assertRaisesRegex(kernel.KernelError, 'required kernel symbol missing: other'):
            kernel.check_required_symbols(absent, ['other'], pending, 'development')

    def test_rules_need_reasons_and_safe_names(self):
        for rules in ({'register_kretprobe': {'modules': ['x.ko'], 'reason': ' '}},
                      {'bad symbol': {'modules': ['x.ko'], 'reason': 'r'}},
                      {'s': {'modules': ['../x.ko'], 'reason': 'r'}}):
            with self.subTest(rules=rules):
                self.assertRaises(kernel.KernelError, kernel.import_allowlist, {'module_import_allowlist': rules})
        self.assertRaises(kernel.KernelError, kernel.forbidden_symbols,
                          {'forbidden_symbols': [{'symbol': 'x', 'reason': ''}]})
        for rules in ([{'symbol': 'x', 'reason': ''}], [{'symbol': 'bad symbol', 'reason': 'r'}],
                      [{'symbol': 'x', 'reason': 'r', 'extra': 1}], {'symbol': 'x'}, [{'symbol': 'x', 'reason': 'r', 'pending': ' '}]):
            with self.subTest(required=rules):
                self.assertRaises(kernel.KernelError, kernel.required_symbols, {'required_symbols': rules})


class KmiTests(unittest.TestCase):
    def test_the_committed_exceptions_each_name_their_importers_and_a_reason(self):
        recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')
        found = kernel.kmi_exceptions(recipe)
        importers = {}
        for symbol, modules in found.items():
            importers.setdefault(modules[0], []).append(symbol)
        # Only the exports of cfg80211.ko and cqhci.ko, which the device kernel build makes. The common kernel's
        # fairphone symbol list keeps the exports of the core that vendor modules import.
        self.assertEqual(['qca_cld3_qca6750.ko', 'sdhci-msm.ko'], sorted(importers))
        self.assertEqual((41, 4), (len(importers['qca_cld3_qca6750.ko']), len(importers['sdhci-msm.ko'])))
        self.assertIn('cqhci_init', importers['sdhci-msm.ko'])
        self.assertIn('cfg80211_scan_done', importers['qca_cld3_qca6750.ko'])
        self.assertTrue(all(group['reason'].strip() for group in recipe['kmi_exceptions']))

    def test_a_malformed_exception_is_refused(self):
        group = {'symbols': ['x'], 'modules': ['a.ko'], 'reason': 'r'}
        self.assertEqual({'x': ['a.ko']}, kernel.kmi_exceptions({'kmi_exceptions': [group]}))
        for bad in (dict(group, modules=[]), dict(group, modules=['../x.ko']), dict(group, modules=['a.ko', 'a.ko']),
                    dict(group, symbols=[]), dict(group, symbols=['bad name']), dict(group, reason=' '),
                    {'symbol': 'x', 'modules': ['a.ko'], 'reason': 'r'}):
            with self.subTest(bad=bad):
                self.assertRaises(kernel.KernelError, kernel.kmi_exceptions, {'kmi_exceptions': [bad]})
        self.assertRaisesRegex(kernel.KernelError, 'listed twice: x', kernel.kmi_exceptions, {'kmi_exceptions': [group, group]})

    def test_the_debug_profile_adds_the_symbols_its_aids_make_modules_import_with_no_importer_list(self):
        recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')
        production, debug = kernel.kmi_exceptions(recipe), kernel.kmi_exceptions(recipe, 'debug')
        added = {symbol: modules for symbol, modules in debug.items() if symbol not in production}
        self.assertEqual({'__might_fault', '__might_resched', '__might_sleep', '__ubsan_handle_out_of_bounds'}, set(added))
        self.assertEqual({None}, set(added.values()))
        self.assertEqual(production, {symbol: debug[symbol] for symbol in production})
        self.assertIn('__might_sleep', kernel.kmi_whitelist(self.common_with_lists(), debug).split())
        self.assertNotIn('__might_sleep', kernel.kmi_whitelist(self.common_with_lists(), production).split())

    def common_with_lists(self):
        common = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, common)
        (common / 'gki/aarch64/symbols').mkdir(parents=True)
        (common / 'BUILD.bazel').write_text('')
        (common / 'gki/aarch64/symbols/base').write_text('\n'.join('s%d' % n for n in range(1001)))
        return common

    def test_a_debug_symbol_may_not_repeat_a_production_exception(self):
        group = {'symbols': ['x'], 'modules': ['a.ko'], 'reason': 'r'}
        recipe = {'kmi_exceptions': [group], 'debug': {'kmi_symbols': {'symbols': ['x'], 'reason': 'r'}}}
        self.assertRaisesRegex(kernel.KernelError, 'listed twice: x', kernel.kmi_exceptions, recipe, 'debug')
        recipe['debug']['kmi_symbols']['symbols'] = ['y']
        self.assertEqual({'x': ['a.ko'], 'y': None}, kernel.kmi_exceptions(recipe, 'debug'))
        self.assertEqual({'x': ['a.ko']}, kernel.kmi_exceptions(recipe))

    def test_an_exception_with_no_importer_list_covers_any_importer_but_must_still_be_used(self):
        imports = {'a.ko': {'mine'}, 'b.ko': {'mine', 'printk'}}
        exports = {'printk', 'mine'}
        self.assertEqual((['mine'], 2), kernel.check_kmi(imports, exports, {'printk'}, {'mine': None}))
        self.assertRaisesRegex(kernel.KernelError, 'no longer needed: mine', kernel.check_kmi, {'a.ko': {'printk'}}, exports,
                               {'printk'}, {'mine': None})
        self.assertRaisesRegex(kernel.KernelError, 'no KMI symbol list keeps: mine', kernel.check_kmi, imports, exports,
                               {'printk'}, {})

    def test_imports_outside_the_symbol_lists_need_a_reviewed_exception_with_the_right_importers(self):
        imports = {'a.ko': {'printk', 'mine', 'local_function'}, 'b.ko': {'kmalloc'}}
        exports = {'printk', 'mine', 'kmalloc'}
        outside, used = kernel.check_kmi(imports, exports, {'printk', 'kmalloc'}, {'mine': ['a.ko']})
        self.assertEqual((['mine'], 3), (outside, used))
        self.assertRaisesRegex(kernel.KernelError, 'no KMI symbol list keeps: mine', kernel.check_kmi, imports, exports,
                               {'printk', 'kmalloc'}, {})
        self.assertRaisesRegex(kernel.KernelError, 'no longer needed: printk', kernel.check_kmi, imports, exports,
                               {'printk', 'kmalloc'}, {'mine': ['a.ko'], 'printk': ['a.ko']})
        # Another module importing an excepted symbol is not covered by the exception.
        imports['c.ko'] = {'mine'}
        self.assertRaisesRegex(kernel.KernelError, 'mine is imported by a.ko, c.ko, not a.ko', kernel.check_kmi, imports,
                               exports, {'printk', 'kmalloc'}, {'mine': ['a.ko']})
        self.assertRaisesRegex(kernel.KernelError, 'mine is imported by a.ko, not a.ko, c.ko', kernel.check_kmi,
                               {'a.ko': {'mine'}}, exports, {'printk', 'kmalloc'}, {'mine': ['a.ko', 'c.ko']})

    def test_the_symbol_lists_are_the_ones_the_build_file_names(self):
        with tempfile.TemporaryDirectory() as temp:
            common = Path(temp)
            (common / 'gki/aarch64/symbols').mkdir(parents=True)
            (common / 'BUILD.bazel').write_text('srcs = ["gki/aarch64/symbols/qcom", "gki/aarch64/symbols/pixel"]\n')
            for name, symbols in (('base', ['kmalloc']), ('qcom', ['printk']), ('pixel', ['mine']), ('unlisted', ['other'])):
                (common / 'gki/aarch64/symbols' / name).write_text('[abi_symbol_list]\n# comment\n  ' + '\n  '.join(symbols) + '\n' +
                                                                  ''.join('  filler%d_%s\n' % (i, name) for i in range(600)))
            found = kernel.kmi_symbols(common)
            self.assertLessEqual({'kmalloc', 'printk', 'mine'}, found)
            self.assertNotIn('other', found)
            whitelist = kernel.kmi_whitelist(common, {'extra_symbol': ['x.ko']}).splitlines()
            self.assertEqual(sorted(whitelist), whitelist)
            self.assertLessEqual({'kmalloc', 'printk', 'mine', 'extra_symbol'}, set(whitelist))
            self.assertNotIn('other', whitelist)
            self.assertFalse(any(line.startswith(('[', '#', ' ')) for line in whitelist))

    def test_the_image_exports_are_its_ksymtab_entries(self):
        nm = 'ffff800080001000 R __ksymtab_printk\nffff800080001018 R __ksymtab_kmalloc\nffff800080002000 T printk\n'
        self.assertEqual({'printk', 'kmalloc'}, kernel.image_exports(nm))


class DeniedBuiltTests(unittest.TestCase):
    def setUp(self):
        self.recipe = {'partitions': {'system_dlkm': ['a.ko']}, 'load_lists': {},
                       'denied_modules': [{'modules': ['can.ko', 'old-driver.ko'], 'absent': ['old-driver.ko'], 'reason': 'r'},
                                          {'modules': ['nfc.ko'], 'reason': 'r'}]}

    def test_a_denied_name_the_build_does_not_make_must_be_marked_absent(self):
        self.assertEqual(['old-driver.ko'], kernel.check_denied_built(self.recipe, {'can.ko', 'nfc.ko', 'other.ko'}))
        # A renamed copy would slip past the list: the unmarked name is no longer built.
        self.assertRaisesRegex(kernel.KernelError, 'no longer built, review the entry: nfc.ko', kernel.check_denied_built,
                               self.recipe, {'can.ko', 'nfc_new.ko'})

    def test_a_name_marked_absent_that_the_build_makes_needs_its_mark_removed(self):
        self.assertRaisesRegex(kernel.KernelError, 'marked absent but built, remove the mark: old-driver.ko',
                               kernel.check_denied_built, self.recipe, {'can.ko', 'nfc.ko', 'old_driver.ko'})

    def test_absent_names_belong_to_their_group(self):
        self.recipe['denied_modules'][0]['absent'] = ['elsewhere.ko']
        self.assertRaisesRegex(kernel.KernelError, 'invalid absent denied modules', kernel.denied_modules, self.recipe)

    def test_the_committed_deny_list_marks_exactly_the_names_the_618_sources_do_not_build(self):
        recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')
        marked = [n for g in recipe['denied_modules'] for n in g.get('absent', [])]
        self.assertEqual(32, len(marked))
        self.assertIn('qseecom_dlkm.ko', marked)
        self.assertNotIn('coresight.ko', marked)
        self.assertNotIn('debugcc-volcano.ko', marked)


class KernelReleaseTests(unittest.TestCase):
    def test_banner_release_is_read_from_vmlinux(self):
        with tempfile.TemporaryDirectory() as temp:
            vmlinux = Path(temp) / 'vmlinux'
            vmlinux.write_bytes(b'\0Linux version 6.18.37-4k-g0f9205ec8de7 (build@build) #1\0')
            self.assertEqual('6.18.37-4k-g0f9205ec8de7', kernel.kernel_release(vmlinux))

    def test_missing_or_conflicting_banner_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            vmlinux = Path(temp) / 'vmlinux'
            vmlinux.write_bytes(b'no banner')
            self.assertRaises(kernel.KernelError, kernel.kernel_release, vmlinux)
            vmlinux.write_bytes(b'Linux version 6.18.1-a (x) Linux version 6.18.2-b (x)')
            self.assertRaises(kernel.KernelError, kernel.kernel_release, vmlinux)


class CommitTimeTests(unittest.TestCase):
    """Every kernel tree gets the pinned commit's date."""

    def test_is_the_pinned_commits_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'kernel'
            commit = repository(root, {'Makefile': 'VERSION = 6\n'})
            self.assertEqual(run_git(root, 'log', '-1', '--pretty=%ct', commit), kernel_util.commit_time(root, commit))
            with self.assertRaises(Exception):
                kernel_util.commit_time(root, '0' * 40)


class VendorConfigTests(unittest.TestCase):
    def tree(self, temp, base='CONFIG_QRTR=m\n# CONFIG_QRTR_TUN is not set\n', audio='CONFIG_QRTR=m\nCONFIG_X=y\n'):
        vendor = Path(temp)
        for name, text in (('base', base), ('audio', audio)):
            (vendor / 'modules' / name).mkdir(parents=True)
            (vendor / 'modules' / name / 'defconfig').write_text(text)
        return vendor

    def test_vendor_defconfigs_sit_on_top_of_the_device_kernel_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.tree(temp)
            text = kernel.vendor_config(b'# CONFIG_QRTR is not set\nCONFIG_SMP=y\n', vendor).decode()
            self.assertEqual('CONFIG_QRTR=m\n# CONFIG_QRTR_TUN is not set\nCONFIG_SMP=y\nCONFIG_X=y\n', text)
            (vendor / 'modules/audio/defconfig').write_text('CONFIG_QRTR=y\n')
            self.assertRaisesRegex(kernel.KernelError, 'set CONFIG_QRTR differently', kernel.vendor_config, b'CONFIG_SMP=y\n', vendor)

    def test_a_vendor_n_does_not_switch_off_what_the_kernel_has_on(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.tree(temp, base='# CONFIG_SMP is not set\n# CONFIG_MODULES is not set\n', audio='')
            text = kernel.vendor_config(b'CONFIG_SMP=y\n# CONFIG_MODULES is not set\nCONFIG_A=m\n', vendor).decode()
            self.assertEqual('CONFIG_A=m\n# CONFIG_MODULES is not set\nCONFIG_SMP=y\n', text)

    def test_extra_configurations_count_and_a_missing_one_is_an_error(self):
        with tempfile.TemporaryDirectory() as temp:
            vendor = self.tree(temp)
            extra = Path(temp) / 'qcacld.config'
            extra.write_text('CONFIG_WLAN=y\nCONFIG_X=m\n')
            text = kernel.vendor_config(b'CONFIG_SMP=y\n', vendor, [extra]).decode()
            self.assertIn('CONFIG_WLAN=y\n', text)
            self.assertIn('CONFIG_X=m\n', text)
            self.assertRaisesRegex(Exception, 'configuration file missing', kernel.vendor_config, b'CONFIG_SMP=y\n', vendor,
                                   [Path(temp) / 'gone'])


class DeviceTreePlanTests(unittest.TestCase):
    def test_the_plan_forbids_the_property_the_ufs_low_power_work_removed(self):
        plan = kernel.validate_dt_plan(kernel.load_json(kernel.DT_PLAN))
        self.assertEqual(['qcom,disable-lpm'], plan['forbidden_properties'])
        plan['forbidden_properties'] = ['bad name']
        self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan, plan)


if __name__ == '__main__':
    unittest.main()
