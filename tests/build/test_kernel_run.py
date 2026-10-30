"""The stages of a kernel build run, each with stand-ins for make and the toolchain."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel, kernel_build, kernel_dt
from diamaneos_tools.kernel_util import KernelError

POLICY = {'schema_version': 1, 'id': 'test', 'baseline': {'CONFIG_RUST': 'y', 'CONFIG_TRIM_UNUSED_KSYMS': 'y'},
          'production': {'CONFIG_SECURITY_DMESG_RESTRICT': 'y'}}
VENDOR_POLICY = {'schema_version': 1, 'id': 'vendor', 'baseline': {'CONFIG_QRTR': 'm'}, 'production': {'CONFIG_X': 'y'}}
BUILD_PLAN = {
    'toolchain': {'clang': 'toolchain/clang', 'kernel_build_tools': 'toolchain/kbt', 'host_libraries': 'toolchain/host',
                  'system_libraries': []},
    'core': {'defconfig': 'arch/arm64/configs/gki_defconfig', 'fragments': ['configs/diamaneos_defconfig'],
             'profile_fragments': {'production': 'configs/production_defconfig', 'development': 'configs/production_defconfig'},
             'tool_fragments': {'production': [], 'development': [], 'debug': []},
             'goals': ['Image', 'modules']},
    'device': {'fragments': ['kernel/wireless_defconfig'], 'goals': ['modules'], 'modules': ['net/cfg80211.ko'],
               'config_symbols': ['CONFIG_CFG80211'], 'reason': 'r'},
    'vendor': {'kernel_overrides': {'symbols': {'CONFIG_QRTR': ['m', 'n']}, 'reason': 'r'},
               'steps': [{'name': 'base', 'kind': 'base'}, {'name': 'pkg', 'kind': 'package'}]}}


def symbols_table(*rows):
    return ''.join('0x00000000\t%s\t%s\tEXPORT_SYMBOL\t\n' % row for row in rows)


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.root = self.top / 'ws'
        run = self.root / 'runs/20261030T000000Z-1'
        for path in ('common/arch/arm64/configs', 'vendor/configs', 'vendor/kernel', run):
            (self.root / path if not isinstance(path, Path) else path).mkdir(parents=True)
        (self.root / 'common/arch/arm64/configs/gki_defconfig').write_text('CONFIG_RUST=y\n')
        (self.root / 'vendor/configs/diamaneos_defconfig').write_text('CONFIG_QRTR=m\n')
        (self.root / 'vendor/configs/production_defconfig').write_text('CONFIG_SECURITY_DMESG_RESTRICT=y\n')
        (self.root / 'vendor/kernel/wireless_defconfig').write_text('CONFIG_CFG80211=m\n')
        self.policy = self.top / 'policy.json'
        self.policy.write_text(json.dumps(POLICY))
        self.vendor_policy = self.top / 'vendor-policy.json'
        self.vendor_policy.write_text(json.dumps(VENDOR_POLICY))
        for target, path in (('POLICY', self.policy), ('VENDOR_POLICY', self.vendor_policy)):
            patcher = patch.object(kernel, target, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.result = {'commands': []}
        self.phases = []
        self.recipe = {'partitions': {'system_dlkm': ['a.ko'], 'vendor_boot': [], 'vendor_dlkm': []}, 'load_lists': {},
                       'denied_modules': [], 'dtbs': ['fp6.dtb'], 'dtbo_count': 1, 'blocklists': {}}
        self.preparation = {'projects': {'common': {'revision': 'c' * 40}, 'vendor': {'revision': 'v' * 40}}}
        self.run = kernel.Run(self.root, run, {}, {}, json.loads(json.dumps(BUILD_PLAN)), self.recipe, 'development', 4, 60,
                              self.preparation, self.result, self.phases.append)
        self.run.run.mkdir(exist_ok=True)
        self.run.logs.mkdir(exist_ok=True)
        self.run.env, self.run.make = {'PATH': '/bin'}, ['make']
        self.calls = []
        self.on_make = lambda argv: None
        patcher = patch.object(kernel, 'call', self.fake_call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_call(self, argv, cwd=None, env=None, timeout=None, log=None):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if log is not None:
            Path(log).write_text('log of ' + ' '.join(argv[:3]))
        if argv[-1] == 'olddefconfig':
            self.normalize(Path([a for a in argv if a.startswith('O=')][0][2:]) / '.config')
        return self.on_make(argv) or ''

    @staticmethod
    def normalize(path):
        """What olddefconfig does to a merged configuration: the last request of a symbol wins."""
        values = {}
        for line in path.read_text().splitlines():
            if line.startswith('CONFIG_'):
                values[line.split('=')[0]] = line
            elif line.startswith('# CONFIG_'):
                values[line.split()[1]] = line
        path.write_text('\n'.join(values.values()) + '\n')


class CoreAndDeviceTests(RunFixture):
    def test_the_core_build_trims_the_exports_and_checks_the_configuration_before_compiling(self):
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            self.run.core_build()
        self.assertEqual('printk\n', (self.run.run / 'kmi-whitelist').read_text())
        config = (self.run.core / '.config').read_text()
        self.assertIn('CONFIG_TRIM_UNUSED_KSYMS=y', config)
        self.assertIn('CONFIG_UNUSED_KSYMS_WHITELIST="../kmi-whitelist"', config)
        self.assertEqual(['olddefconfig'], self.calls[0][-1:])
        self.assertEqual(['Image', 'modules'], self.calls[1][-2:])
        self.assertIn('O=' + str(self.run.core), self.calls[1])
        self.assertEqual(['core-configure', 'core-build'], self.phases)
        self.assertEqual(['core-configure', 'core-build'], [c['name'] for c in self.result['commands']])
        self.assertEqual({'unmet': {}}, json.loads((self.run.run / 'fragment-check.json').read_text()))
        self.assertEqual('PASS', json.loads((self.run.run / 'kernel-config.json').read_text())['status'])

    def test_the_tool_fragments_of_the_profile_come_after_the_vendor_ones_and_must_be_honoured(self):
        (self.top / 'config').mkdir()
        (self.top / 'config/net.config').write_text('CONFIG_NET_X=m\n# CONFIG_NET_Y is not set\n')
        (self.top / 'config/more.config').write_text('CONFIG_NET_Z=y\n')
        self.run.build_plan = copy.deepcopy(self.run.build_plan)
        self.run.build_plan['core']['tool_fragments'] = {'development': ['config/net.config'], 'production': ['config/net.config', 'config/more.config']}
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'), patch.object(kernel, 'ROOT', self.top):
            self.run.core_build()
            config = (self.run.core / '.config').read_text()
            self.assertIn('CONFIG_NET_X=m', config)
            self.assertIn('# CONFIG_NET_Y is not set', config)
            self.assertNotIn('CONFIG_NET_Z', config)
            # The production profile adds the second file, and a request the build drops stops it before compiling.
            self.run.profile = 'production'
            self.on_make = lambda argv: (self.run.core / '.config').write_text('CONFIG_RUST=y\nCONFIG_NET_X=m\n')
            self.calls.clear()
            self.assertRaisesRegex(KernelError, 'production profile asks for configuration the build does not honour: CONFIG_NET_Z', self.run.core_build)
        self.assertEqual(1, len(self.calls))

    def test_a_request_kconfig_does_not_honour_stops_before_compiling(self):
        def drop(argv):
            if argv[-1] == 'olddefconfig':
                (self.run.core / '.config').write_text('CONFIG_RUST=y\nCONFIG_QRTR=m\n')
        self.on_make = drop
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            self.assertRaisesRegex(KernelError, 'development profile asks for configuration the build does not honour: '
                                   'CONFIG_SECURITY_DMESG_RESTRICT .wants y, the pinned common kernel does not declare it., CONFIG_TRIM_UNUSED_KSYMS',
                                   self.run.core_build)
        self.assertEqual(1, len(self.calls))

    def test_a_configuration_that_misses_the_policy_stops_before_compiling(self):
        self.policy.write_text(json.dumps(dict(POLICY, baseline={'CONFIG_RUST': 'y', 'CONFIG_KGDB': 'n'})))
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            self.assertRaisesRegex(KernelError, 'development kernel configuration regressed', self.run.core_build)
        self.assertEqual(1, len(self.calls))

    def test_the_production_profile_builds_the_same_configuration_and_enforces_the_production_tier(self):
        self.policy.write_text(json.dumps(dict(POLICY, production={'CONFIG_TIPC': 'm'})))
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            self.run.core_build()
            self.run.profile = 'production'
            self.assertRaisesRegex(KernelError, 'production kernel configuration regressed', self.run.core_build)
        report = json.loads((self.run.run / 'kernel-config.json').read_text())
        self.assertEqual(['CONFIG_TIPC'], [f['symbol'] for f in report['failures']])
        self.assertIn('configs/production_defconfig', ' '.join(str(p) for p in [self.root / 'vendor/configs/production_defconfig']))

    def device_made(self, argv):
        if argv[-1] == 'modules':
            (self.run.device / kernel_build.SYMVERS).write_text(self.device_symvers)

    def prepare_core(self):
        self.run.core.mkdir(parents=True)
        (self.run.core / '.config').write_text('CONFIG_RUST=y\nCONFIG_QRTR=m\n# CONFIG_CFG80211 is not set\n')
        (self.run.core / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux')))
        self.device_symvers = symbols_table(('printk', 'vmlinux'), ('cfg80211_x', 'net/cfg80211'))
        self.on_make = self.device_made

    def test_the_device_kernel_adds_only_the_declared_symbols_and_exports_the_same_kernel(self):
        self.prepare_core()
        self.run.device_build()
        self.assertEqual({'CONFIG_CFG80211': ['n', 'm']}, json.loads((self.run.run / 'device-config.json').read_text())['changed'])
        self.assertEqual(['device-configure', 'device-build'], self.phases)
        self.assertEqual('PASS', json.loads((self.run.run / 'vendor-role-kernel-config.json').read_text())['status'])

    def test_a_device_kernel_that_exports_other_kernel_symbols_is_refused(self):
        self.prepare_core()
        self.device_symvers = symbols_table(('printk', 'vmlinux'), ('extra', 'vmlinux'))
        self.assertRaisesRegex(KernelError, 'exports other kernel symbols', self.run.device_build)

    def test_a_device_kernel_that_changes_the_gki_configuration_is_refused(self):
        self.prepare_core()

        def kconfig_adds_a_symbol(argv):
            if argv[-1] == 'olddefconfig':
                with open(self.run.device / '.config', 'a') as stream:
                    stream.write('CONFIG_EXTRA=y\n')
        self.on_make = kconfig_adds_a_symbol
        self.assertRaisesRegex(KernelError, 'changes the GKI configuration: CONFIG_EXTRA', self.run.device_build)


class VendorStageTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.run.device.mkdir(parents=True)
        (self.run.device / '.config').write_text('CONFIG_QRTR=n\n')
        self.steps = {}
        patcher = patch.dict(kernel_build.STEPS, {'base': self.step('base'), 'package': self.step('package')})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(kernel_build, 'stage_vendor_modules', lambda vendor, revision, stage: stage.mkdir(parents=True))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(kernel_build, 'kconfig_symbols', lambda common: {'CONFIG_QRTR'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conflicts = {'CONFIG_QRTR': ['m', 'n']}

    def step(self, kind):
        def run_step(ctx, step, stage, outputs):
            self.declared = ctx['declared']
            out = ctx['out'] / step['name']
            out.mkdir(parents=True)
            (ctx['logs'] / (step['name'] + '.log')).write_text('built')
            outputs[step['name']] = out
            ctx['conflicts'].update(self.conflicts)
            return [step['name'] + '.ko']
        return run_step

    def test_the_steps_run_in_plan_order_and_the_overrides_are_recorded(self):
        for name in ('core-build', 'device-build'):
            (self.run.logs / (name + '.log')).write_text('fine')
        self.run.vendor_build()
        self.assertEqual(['base', 'pkg'], self.phases)
        # The steps get the symbols the Kconfig files of the common tree declare.
        self.assertEqual({'CONFIG_QRTR'}, self.declared)
        self.assertEqual({'base': ['base.ko'], 'pkg': ['pkg.ko']}, json.loads((self.run.run / 'modules-built.json').read_text()))
        self.assertEqual(self.conflicts, json.loads((self.run.run / 'vendor-overrides.json').read_text()))

    def test_an_override_that_no_longer_differs_is_refused(self):
        self.conflicts = {}
        for name in ('core-build', 'device-build'):
            (self.run.logs / (name + '.log')).write_text('fine')
        self.assertRaisesRegex(KernelError, 'no longer differs from the kernel configuration: CONFIG_QRTR', self.run.vendor_build)

    def test_a_visibility_warning_in_any_log_fails(self):
        (self.run.logs / 'core-build.log').write_text('x.c:1: warning: struct y will not be visible [-Wvisibility]\n')
        (self.run.logs / 'device-build.log').write_text('fine')
        self.assertRaisesRegex(KernelError, 'Wvisibility', self.run.vendor_build)


class DebugProfileTests(RunFixture):
    def setUp(self):
        super().setUp()
        (self.root / 'vendor/configs/bringup_defconfig').write_text(
            'CONFIG_DEBUG_FS_ALLOW_ALL=y\n# CONFIG_DEBUG_FS_DISALLOW_MOUNT is not set\nCONFIG_FUNCTION_TRACER=y\n')
        self.build_plan = json.loads(json.dumps(BUILD_PLAN))
        self.build_plan['core']['profile_fragments']['debug'] = 'configs/bringup_defconfig'
        self.recipe.update({'kernel_cmdline': [{'argument': 'scsi_mod.scan=sync', 'reason': 'r'}],
                            'denied_modules': [{'modules': ['dbg.ko'], 'reason': 'r'}],
                            'partitions': {'system_dlkm': ['a.ko'], 'vendor_boot': ['v.ko'], 'vendor_dlkm': ['v.ko']},
                            'load_lists': {'vendor_boot/modules.load.recovery': ['v.ko'], 'vendor_dlkm/modules.load': ['v.ko']},
                            'debug': {'kernel_cmdline': [{'argument': 'loglevel=8', 'reason': 'r'}],
                                      'modules': {'names': ['dbg.ko'], 'after': 'v.ko', 'partitions': ['vendor_boot', 'vendor_dlkm'],
                                                  'reason': 'r'},
                                      'kmi_symbols': {'symbols': ['__might_sleep'], 'reason': 'r'}}})
        self.policy.write_text(json.dumps(dict(POLICY, baseline={'CONFIG_RUST': 'y', 'CONFIG_DEBUG_FS_ALLOW_ALL': 'n'},
                                               debug={'CONFIG_DEBUG_FS_ALLOW_ALL': 'y', 'CONFIG_FUNCTION_TRACER': 'y'})))

    def make_run(self, profile):
        run = kernel.Run(self.root, self.run.run, {}, {}, self.build_plan, self.recipe, profile, 4, 60, self.preparation,
                         self.result, self.phases.append)
        run.logs.mkdir(exist_ok=True)
        run.env, run.make = {'PATH': '/bin'}, ['make']
        return run

    def test_the_debug_profile_builds_the_bring_up_fragment_and_checks_the_debug_tier(self):
        run = self.make_run('debug')
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            run.core_build()
        config = (run.core / '.config').read_text()
        self.assertIn('CONFIG_FUNCTION_TRACER=y', config)
        self.assertIn('CONFIG_DEBUG_FS_ALLOW_ALL=y', config)
        self.assertEqual('debug', json.loads((run.run / 'kernel-config.json').read_text())['profile'])
        # The same configuration fails the development profile, whose baseline refuses mountable debugfs.
        run = self.make_run('development')
        (self.root / 'vendor/configs/production_defconfig').write_text('CONFIG_DEBUG_FS_ALLOW_ALL=y\n')
        with patch.object(kernel, 'kmi_whitelist', lambda common, exceptions: 'printk\n'):
            self.assertRaisesRegex(KernelError, 'development kernel configuration regressed', run.core_build)

    def test_the_debug_profile_ships_the_debug_modules_and_the_deny_list_is_checked_without_them(self):
        run = self.make_run('debug')
        self.assertEqual(['dbg.ko', 'v.ko'], sorted(run.recipe['partitions']['vendor_dlkm']))
        self.assertEqual(['v.ko', 'dbg.ko'], run.recipe['load_lists']['vendor_dlkm/modules.load'])
        self.assertEqual(['v.ko'], run.base_recipe['partitions']['vendor_dlkm'])
        self.assertEqual({'dbg.ko'}, set(run.denied))
        self.assertEqual(['v.ko'], self.make_run('production').recipe['partitions']['vendor_dlkm'])

    def package(self, profile):
        run = self.make_run(profile)
        run.tree, run.dtbo, run.staged = b'tree', b'dtbo', run.run / 'staged'
        shown = {}

        def render(candidate, staged, image, dtbs, dtbo, recipe, arguments):
            candidate.mkdir()
            shown.update(arguments=arguments, recipe=recipe)
        with patch.object(kernel.kernel_package, 'render_package', render):
            run.package()
        return run, shown

    def test_the_debug_command_line_adds_the_bring_up_arguments_and_the_candidate_names_the_profile(self):
        run, shown = self.package('debug')
        self.assertEqual(['scsi_mod.scan=sync', 'loglevel=8'], shown['arguments'])
        readme = (run.candidate / 'README.md').read_text()
        self.assertIn('debug profile', readme)
        self.assertIn('Never publish it', readme)
        self.assertIn('dbg.ko', shown['recipe']['partitions']['vendor_dlkm'])

    def test_a_production_candidate_has_no_debug_arguments_and_no_readme(self):
        run, shown = self.package('production')
        self.assertEqual(['scsi_mod.scan=sync'], shown['arguments'])
        self.assertFalse((run.candidate / 'README.md').exists())


class DeviceTreeStageTests(RunFixture):
    def test_the_base_files_get_the_fixups_and_the_result_must_decompile(self):
        root = kernel_dt.Node('')
        root.props = [('#address-cells', struct.pack('>I', 2))]
        soc = kernel_dt.Node('soc')
        soc.props = [('status', b'okay\0')]
        root.children = [soc]
        blob = kernel_dt.serialize(kernel_dt.Tree(root, [], 0))
        fragment = kernel_dt.Node('fragment@0')
        fragment.props = [('target-path', b'/soc\0')]
        body = kernel_dt.Node('__overlay__')
        engine = kernel_dt.Node('engine')
        engine.props = [('compatible', b'qcom,qcedev\0'), ('status', b'okay\0')]
        body.children, fragment.children = [engine], [body]
        top = kernel_dt.Node('')
        top.children = [fragment]
        piece = kernel_dt.serialize(kernel_dt.Tree(top, [], 0))
        overlay = struct.pack('>8I', kernel_dt.DTBO_MAGIC, 64 + len(piece), 32, 32, 1, 32, 4096, 0) + \
            struct.pack('>8I', len(piece), 64, 0, 0, 0, 0, 0, 0) + piece
        (self.root / 'base-dt/dtbs').mkdir(parents=True)
        (self.root / 'base-dt/dtbs/fp6.dtb').write_bytes(blob)
        (self.root / 'base-dt/dtbo.img').write_bytes(overlay)
        import hashlib
        self.run.dt_plan = {'base': {'files': {'dtbs/fp6.dtb': hashlib.sha256(blob).hexdigest(),
                                               'dtbo.img': hashlib.sha256(overlay).hexdigest()}, 'overlay_count': 1},
                            'fixups': [{'id': 'off', 'reason': 'r', 'edits': [
                                {'node': '/soc', 'property': 'status', 'expect': {'strings': ['okay']}, 'set': {'strings': ['disabled']}}]}],
                            'disabled_nodes': [{'compatible': 'qcom,qcedev', 'reason': 'r'}],
                            'forbidden_properties': ['qcom,disable-lpm']}
        self.run.build_plan['toolchain']['kernel_build_tools'] = 'dtc-dir'
        # The overlay enables a node that must be off: the build stops before it writes anything.
        self.assertRaisesRegex(KernelError, 'nodes of drivers that do not ship are enabled: /fragment@0/__overlay__/engine in overlay 0',
                               self.run.device_tree)
        engine.props = [('compatible', b'qcom,qcedev\0'), ('status', b'disabled\0')]
        piece = kernel_dt.serialize(kernel_dt.Tree(top, [], 0))
        overlay = overlay[:64] + piece
        overlay = overlay[:32] + struct.pack('>8I', len(piece), 64, 0, 0, 0, 0, 0, 0) + overlay[64:]
        (self.root / 'base-dt/dtbo.img').write_bytes(overlay)
        self.run.dt_plan['base']['files']['dtbo.img'] = hashlib.sha256(overlay).hexdigest()
        self.run.device_tree()
        self.assertEqual(1, self.result['disabled_node_rules_checked'])
        fixed = kernel_dt.parse((self.run.run / 'fp6.dtb').read_bytes())
        self.assertEqual(b'disabled\0', kernel_dt.find(fixed.root, '/soc').prop('status'))
        self.assertEqual(['device-tree', 'device-tree'], self.phases)
        self.assertEqual(1, self.run.overlays)
        self.assertEqual('dtc', Path(self.calls[0][0]).name)
        self.assertEqual(['-q', '-I', 'dtb', '-O', 'dts'], self.calls[0][1:6])
        report = json.loads((self.run.run / 'device-tree-fixups.json').read_text())
        self.assertEqual([{'node': '/soc', 'property': 'status', 'before': '"okay"', 'after': '"disabled"'}], report['fixups'][0]['changes'])
        # A different overlay count than the recipe's stops the build.
        self.recipe['dtbo_count'] = 2
        self.assertRaisesRegex(KernelError, 'overlay count changed', self.run.device_tree)


class ModuleSetStageTests(RunFixture):
    def setUp(self):
        super().setUp()
        for name in ('vendor.ko', 'a.ko'):
            path = self.run.core / 'drivers' / name
            path.parent.mkdir(parents=True, exist_ok=True)
        (self.run.core / 'drivers/a.ko').write_bytes(b'ko')
        self.run.outputs = {'pkg': self.top / 'out-pkg'}
        self.run.outputs['pkg'].mkdir()
        (self.run.outputs['pkg'] / 'vendor.ko').write_bytes(b'ko')
        (self.run.device / 'net').mkdir(parents=True)
        (self.run.device / 'net/cfg80211.ko').write_bytes(b'ko')
        self.recipe['partitions'] = {'system_dlkm': ['a.ko'], 'vendor_boot': ['vendor.ko'], 'vendor_dlkm': ['vendor.ko', 'cfg80211.ko']}
        for name in ('certs/signing_key.pem', 'certs/signing_key.x509', 'scripts/sign-file'):
            (self.run.core / name).parent.mkdir(parents=True, exist_ok=True)
            (self.run.core / name).write_bytes(b'x')
        (self.run.core / '.config').write_text('CONFIG_MODULE_SIG_FORCE=y\nCONFIG_MODULE_SIG_HASH="sha256"\n')
        patcher = patch.object(kernel, 'dependencies', lambda path: [])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.staged = []
        patcher = patch.object(kernel.kernel_package, 'stage_modules', lambda *args: self.staged.append(args))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_listed_module_comes_from_its_build_and_is_staged_for_signing(self):
        self.run.module_set()
        self.assertEqual({'a.ko', 'vendor.ko', 'cfg80211.ko'}, set(self.run.selected))
        self.assertEqual(self.run.core / 'drivers/a.ko', self.run.selected['a.ko'])
        self.assertEqual(self.run.device / 'net/cfg80211.ko', self.run.selected['cfg80211.ko'])
        self.assertEqual(self.run.run / 'staged-modules', self.staged[0][0])
        self.assertEqual(0, self.result['denied_module_count'])
        self.assertEqual(['package'], self.phases)

    def test_a_listed_module_nobody_built_stops_the_build(self):
        self.recipe['partitions']['vendor_dlkm'].append('ghost.ko')
        self.assertRaisesRegex(KernelError, 'listed module was not built: ghost.ko', self.run.module_set)

    def test_a_denied_name_that_is_no_longer_built_stops_the_build(self):
        self.recipe['denied_modules'] = [{'modules': ['renamed.ko'], 'reason': 'r'}]
        self.run.denied = kernel.denied_modules(self.recipe)
        self.assertRaisesRegex(KernelError, 'no longer built, review the entry: renamed.ko', self.run.module_set)

    def test_kernel_modules_must_be_signed_with_a_forced_sha256_key(self):
        (self.run.core / '.config').write_text('CONFIG_MODULE_SIG_FORCE=y\n')
        self.assertRaisesRegex(KernelError, 'does not enforce sha256', self.run.module_set)


class RequiredModuleStageTests(ModuleSetStageTests):
    def setUp(self):
        super().setUp()
        self.recipe['required_modules'] = [{'module': 'a.ko', 'partition': 'system_dlkm', 'needed_by': 'vendor/bin/daemon',
                                            'reason': 'r'}]
        self.recipe['load_lists'] = {'system_dlkm/modules.load': ['a.ko']}
        patcher = patch.object(kernel, 'shipped_vendor_files', lambda: {'vendor/bin/daemon'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_stage_checks_the_listing_and_records_the_module(self):
        self.run.module_set()
        self.assertEqual(['a.ko'], self.result['required_modules_checked'])

    def test_a_module_missing_from_the_list_or_the_load_list_stops_the_build(self):
        self.recipe['load_lists'] = {'system_dlkm/modules.load': []}
        self.assertRaisesRegex(KernelError, 'a.ko is needed by vendor/bin/daemon and is not in the system_dlkm load list',
                               self.run.module_set)

    def test_a_module_missing_from_the_partition_stops_the_build(self):
        self.recipe['partitions']['system_dlkm'] = []
        self.assertRaisesRegex(KernelError, 'a.ko is needed by vendor/bin/daemon and is not in the system_dlkm module list',
                               self.run.module_set)

    def test_a_module_that_was_not_built_stops_the_build(self):
        (self.run.core / 'drivers/a.ko').unlink()
        self.assertRaisesRegex(KernelError, 'listed module was not built: a.ko', self.run.module_set)


class ChecksStageTests(RunFixture):
    def setUp(self):
        super().setUp()
        for name in ('core', 'device', 'candidate/modules'):
            (self.run.run / name).mkdir(parents=True, exist_ok=True)
        (self.run.core / 'include/config').mkdir(parents=True)
        (self.run.core / 'include/config/kernel.release').write_text('6.18.37-4k-g' + 'c' * 12 + '\n')
        (self.run.core / 'vmlinux').write_bytes(b'Linux version 6.18.37-4k-g' + b'c' * 12 + b' (build@build)')
        (self.run.core / '.config').write_text('CONFIG_RUST=y\n')
        (self.run.device / '.config').write_text('CONFIG_RUST=y\n')
        (self.run.core / 'System.map').write_text('ffffffc080000000 T _text\n')
        (self.run.core / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux'), ('kmalloc', 'vmlinux')))
        (self.run.device / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux'), ('kmalloc', 'vmlinux')))
        self.run.build_plan['device']['modules'] = ['net/cfg80211.ko']
        self.run.device_pool = {'cfg80211.ko': Path('c')}
        self.run.candidate = self.run.run / 'candidate'
        self.run.selected = {'vendor.ko': Path('v'), 'a.ko': Path('a')}
        self.run.vendor_pool = {'vendor.ko': Path('v')}
        self.run.overlays = 95
        self.run.outputs = {}
        for name in ('vendor.ko', 'a.ko'):
            (self.run.candidate / 'modules' / name).write_bytes(b'ko')
        (self.run.run / 'module-interfaces.json').write_text('{}')
        self.recipe.update({'required_symbols': [], 'forbidden_symbols': [], 'kmi_exceptions': []})
        self.nm_undefined = '                 U printk\n'
        self.nm_defined = 'ffff R __ksymtab_printk\nffff R __ksymtab_kmalloc\n'
        patcher = patch.object(kernel, 'call', self.nm)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('diamaneos_tools.kernel_interfaces.verify_built', lambda *args, **kwargs: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(kernel, 'kmi_symbols', lambda common: {'printk', 'kmalloc'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def nm(self, argv, cwd=None, env=None, timeout=None, log=None):
        argv = list(map(str, argv))
        return self.nm_defined if '--defined-only' in argv else self.nm_undefined

    def test_a_clean_run_passes_and_writes_the_inventory_and_the_configurations(self):
        self.run.checks()
        self.assertEqual('PASS', self.result['status'])
        self.assertEqual(2, self.result['module_count'])
        self.assertEqual({'modules_checked': 1, 'kernel_symbols_imported': 1, 'outside_symbol_lists': [], 'image_exports': 2,
                          'symbol_table_exports': 2}, self.result['kmi'])
        self.assertEqual([], self.result['pending_required_symbols'])
        inventory = json.loads((self.run.run / 'artifacts.json').read_text())
        self.assertEqual(['modules/a.ko', 'modules/vendor.ko'], [row['path'] for row in inventory])
        for name in ('core.config', 'device.config', 'core.symvers', 'System.map'):
            self.assertTrue((self.run.run / name).is_file())

    def test_a_module_that_imports_a_symbol_the_image_no_longer_exports_fails(self):
        self.nm_defined = 'ffff R __ksymtab_kmalloc\n'
        self.assertRaisesRegex(KernelError, 'the Image no longer exports: printk', self.run.checks)

    def test_a_vendor_import_outside_the_lists_needs_an_exception_with_that_importer(self):
        (self.run.core / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux'), ('odd', 'vmlinux')))
        self.nm_undefined = '                 U odd\n'
        self.nm_defined = 'ffff R __ksymtab_printk\nffff R __ksymtab_odd\n'
        self.assertRaisesRegex(KernelError, 'no KMI symbol list keeps: odd', self.run.checks)
        self.run.exceptions = {'odd': ['vendor.ko']}
        self.run.vendor_pool = {'vendor.ko': Path('v'), 'a.ko': Path('a')}
        self.assertRaisesRegex(KernelError, 'odd is imported by a.ko, vendor.ko, not vendor.ko', self.run.checks)
        self.run.exceptions = {'odd': ['a.ko', 'vendor.ko']}
        self.run.checks()
        self.assertEqual(['odd'], self.result['kmi']['outside_symbol_lists'])

    def test_the_result_says_whether_the_build_is_a_debug_build(self):
        self.run.checks()
        self.assertIs(False, self.result['debug_build'])
        self.run.profile = 'debug'
        self.run.checks()
        self.assertIs(True, self.result['debug_build'])

    def test_an_export_of_a_device_kernel_module_is_held_to_the_lists_too(self):
        (self.run.device / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux'), ('cqhci_init', 'drivers/mmc/host/cqhci')))
        self.nm_undefined = '                 U cqhci_init\n'
        self.assertRaisesRegex(KernelError, 'no KMI symbol list keeps: cqhci_init', self.run.checks)
        self.run.exceptions = {'cqhci_init': ['vendor.ko']}
        self.run.checks()
        self.assertEqual(['cqhci_init'], self.result['kmi']['outside_symbol_lists'])

    def test_the_device_kernel_modules_may_use_each_others_exports(self):
        (self.run.device / kernel_build.SYMVERS).write_text(symbols_table(('printk', 'vmlinux'), ('wiphy_new', 'net/wireless/cfg80211')))
        self.run.selected['cfg80211.ko'] = Path('c')
        (self.run.candidate / 'modules/cfg80211.ko').write_bytes(b'ko')
        self.nm_undefined = '                 U wiphy_new\n'
        self.run.vendor_pool = {}
        self.run.checks()
        self.assertEqual([], self.result['kmi']['outside_symbol_lists'])
        self.run.vendor_pool = {'vendor.ko': Path('v')}
        self.assertRaisesRegex(KernelError, 'no KMI symbol list keeps: wiphy_new', self.run.checks)

    def test_a_kernel_release_that_does_not_name_the_pinned_commit_fails(self):
        self.preparation['projects']['common']['revision'] = 'd' * 40
        self.assertRaisesRegex(KernelError, 'does not name the pinned common kernel', self.run.checks)

    def test_pending_symbols_are_reported_in_development_and_missing_in_production(self):
        self.recipe['required_symbols'] = [{'symbol': 'names_command_line', 'reason': 'r', 'pending': 'not carried'}]
        self.run.checks()
        self.assertEqual(['names_command_line'], self.result['pending_required_symbols'])
        self.run.profile = 'production'
        self.assertRaisesRegex(KernelError, 'required kernel symbol missing: names_command_line', self.run.checks)


class ExecuteTests(unittest.TestCase):
    def test_the_stages_run_in_this_order(self):
        order = []

        class Stages:
            def __getattr__(self, name):
                return lambda: order.append(name)
        kernel.execute(Stages())
        self.assertEqual(['start', 'core_build', 'device_build', 'vendor_build', 'module_set', 'device_tree', 'package', 'checks'],
                         order)


if __name__ == '__main__':
    unittest.main()
