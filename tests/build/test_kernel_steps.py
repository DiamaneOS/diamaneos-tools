"""The vendor build steps, with a stand-in for make that makes the files a build would."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel_build as build
from diamaneos_tools.kernel_util import KernelError

REGISTRY = '''
def register_modules(registry):
    registry.register(name = "one", out = "one.ko", srcs = ["one.c"])
    registry.register(name = "two", out = "two.ko", srcs = ["two.c"])
'''


class StepFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.stage = self.top / 'run/vendor-src'
        for package in ('base', 'pkg', 'display', 'gpu', 'touch', 'thermal'):
            (self.stage / 'modules' / package).mkdir(parents=True)
            (self.stage / 'modules' / package / 'defconfig').write_text('CONFIG_VENDOR_%s=m\n' % package.upper())
        (self.stage / 'modules/pkg/modules.bzl').write_text(REGISTRY)
        self.device = self.top / 'run/device'
        (self.device / 'include/config').mkdir(parents=True)
        (self.device / 'include/config/auto.conf').write_text('CONFIG_SMP=y\n')
        (self.device / '.config').write_text('CONFIG_SMP=y\n# CONFIG_MODULE_SIG_ALL is not set\n')
        self.out = self.top / 'run/out'
        (self.out / 'vendor').mkdir(parents=True)
        (self.out / 'vendor' / build.SYMVERS).write_text('')
        (self.out / 'earlier').mkdir()
        (self.out / 'earlier' / build.SYMVERS).write_text('')
        (self.top / 'run/logs').mkdir()
        self.calls = []
        self.ctx = {'env': {'PATH': '/bin'}, 'make': ['make', 'ARCH=arm64'], 'common': self.top / 'common', 'jobs': 4,
                    'timeout': 60, 'logs': self.top / 'run/logs', 'runs': self.top / 'run', 'out': self.out,
                    'tools_root': self.top, 'device': self.device,
                    'kernel_values': {'CONFIG_SMP': 'y', 'CONFIG_MODULE_SIG_ALL': 'n'}, 'overrides': {'CONFIG_VENDOR_BASE'},
                    'conflicts': {}}
        self.made = lambda argv, env: None
        patcher = patch.object(build, 'call', self.fake_call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_call(self, argv, cwd=None, env=None, timeout=None, log=None):
        self.calls.append((list(map(str, argv)), cwd, env))
        self.made(list(map(str, argv)), env)
        return ''


class PackageStepTests(StepFixture):
    step = {'name': 'pkg', 'kind': 'package', 'package': 'pkg', 'symbols': ['earlier']}

    def make_modules(self, names):
        def made(argv, env):
            out = Path([a for a in argv if a.startswith('MO=')][0][3:])
            for name in names:
                (out / name).write_bytes(b'ko')
        self.made = made

    def test_builds_the_registered_modules_against_the_device_kernel(self):
        self.make_modules(['one.ko', 'two.ko'])
        outputs = {}
        self.assertEqual(['one.ko', 'two.ko'], build.package_step(self.ctx, self.step, self.stage, outputs))
        self.assertEqual({'pkg': self.out / 'pkg'}, outputs)
        argv, cwd, env = self.calls[0]
        self.assertEqual(['make', 'ARCH=arm64', '-C', str(self.top / 'common')], argv[:4])
        self.assertIn('O=' + str(self.device), argv)
        self.assertIn('M=' + str(self.stage / 'modules/pkg'), argv)
        self.assertIn('KBUILD_EXTRA_SYMBOLS=%s %s' % (self.out / 'vendor' / build.SYMVERS, self.out / 'earlier' / build.SYMVERS), argv)
        self.assertIn('-j4', argv)
        self.assertIn('obj-m += one.o', (self.stage / 'modules/pkg/Kbuild').read_text())
        self.assertIn('CONFIG_VENDOR_PKG', (self.out / 'pkg/vendor_autoconf.h').read_text())

    def test_the_built_modules_must_be_the_registered_ones(self):
        for names in (['one.ko'], ['one.ko', 'two.ko', 'three.ko']):
            with self.subTest(names=names):
                self.make_modules(names)
                self.assertRaisesRegex(KernelError, 'pkg: the built modules differ from the registry', build.package_step,
                                       self.ctx, self.step, self.stage, {})

    def test_only_a_module_filter_builds_the_named_modules(self):
        self.make_modules(['two.ko'])
        step = dict(self.step, modules=['two'])
        self.assertEqual(['two.ko'], build.package_step(self.ctx, step, self.stage, {}))

    def test_an_earlier_package_must_have_made_its_symbol_table(self):
        (self.out / 'earlier' / build.SYMVERS).unlink()
        self.assertRaisesRegex(KernelError, 'symbol table missing for pkg: earlier/Module.symvers', build.package_step,
                               self.ctx, self.step, self.stage, {})
        self.assertEqual([], self.calls)

    def test_a_vendor_value_that_differs_from_the_kernel_must_be_a_reviewed_override(self):
        (self.stage / 'modules/pkg/defconfig').write_text('CONFIG_SMP=n\nCONFIG_MODULE_SIG_ALL=y\n')
        self.make_modules(['one.ko', 'two.ko'])
        self.assertRaisesRegex(KernelError, 'pkg sets kernel symbols to other values than the kernel configuration: '
                                            'CONFIG_MODULE_SIG_ALL=y .kernel n., CONFIG_SMP=n .kernel y.',
                               build.package_step, self.ctx, self.step, self.stage, {})
        self.assertEqual([], self.calls)
        self.ctx['overrides'] |= {'CONFIG_SMP', 'CONFIG_MODULE_SIG_ALL'}
        build.package_step(self.ctx, self.step, self.stage, {})
        self.assertEqual({'CONFIG_SMP': ['n', 'y'], 'CONFIG_MODULE_SIG_ALL': ['y', 'n']}, self.ctx['conflicts'])

    def test_extra_configurations_are_held_to_the_kernel_too(self):
        extra = self.top / 'extra.config'
        extra.write_text('CONFIG_SMP=n\n')
        self.make_modules(['one.ko', 'two.ko'])
        self.assertRaisesRegex(KernelError, 'CONFIG_SMP=n', build.package_step, self.ctx,
                               dict(self.step, extra_defconfig='extra.config'), self.stage, {})

    def test_the_wifi_feature_probes_must_find_what_the_plan_declares_and_the_header_is_saved(self):
        common = self.top / 'common'
        (common / 'net').mkdir(parents=True)
        (common / 'net/a.c').write_text('int has_feature(void);\n')
        step = dict(self.step, feature_header='gen/features.h',
                    feature_probes=[['net/a.c', 'has_feature', 'HAS_FEATURE', True], ['net/a.c', 'punct', 'HAS_PUNCT', False]])
        (self.stage / 'modules/pkg/gen').mkdir()
        self.make_modules(['one.ko', 'two.ko'])
        build.package_step(self.ctx, step, self.stage, {})
        self.assertEqual('#define HAS_FEATURE (1)\n', (self.stage / 'modules/pkg/gen/features.h').read_text())
        self.assertEqual('#define HAS_FEATURE (1)\n', (self.top / 'run/pkg-features.h').read_text())
        (common / 'net/a.c').write_text('int has_feature(void);\nint punct;\n')
        self.assertRaisesRegex(KernelError, 'features of the common kernel changed: HAS_PUNCT is found', build.package_step,
                               self.ctx, step, self.stage, {})
        (common / 'net/a.c').write_text('nothing\n')
        self.assertRaisesRegex(KernelError, 'HAS_FEATURE is missing', build.package_step, self.ctx, step, self.stage, {})

    def test_the_vendor_defconfig_must_hold_in_the_effective_configuration(self):
        (self.stage / 'modules/pkg/vendor.defconfig').write_text('CONFIG_A=y\n')
        extra = self.top / 'extra.config'
        extra.write_text('CONFIG_B=y\n')
        step = dict(self.step, extra_defconfig='extra.config', defconfig_check='vendor.defconfig')
        self.assertRaisesRegex(KernelError, 'differs from the vendor defconfig: CONFIG_A', build.package_step, self.ctx, step,
                               self.stage, {})
        (self.stage / 'modules/pkg/vendor.defconfig').unlink()
        self.assertRaisesRegex(Exception, 'configuration file missing', build.package_step, self.ctx, step, self.stage, {})


class BaseAndGroupStepTests(StepFixture):
    def test_the_base_layer_builds_with_the_vendor_makefile_and_its_table_names_the_modules(self):
        def made(argv, env):
            out = Path([a for a in argv if a.startswith('OUT=')][0][4:])
            out.mkdir(parents=True, exist_ok=True)
            (out / 'modules.tsv').write_text('a.ko\tx\tobjs\nb.ko\ty\tobjs\n')
            for name in ('a.ko', 'b.ko'):
                (out / name).write_bytes(b'ko')
        self.made = made
        step = {'name': 'base', 'kind': 'base', 'package': 'base', 'output': 'vendor'}
        outputs = {}
        self.assertEqual(['a.ko', 'b.ko'], build.base_step(self.ctx, step, self.stage, outputs))
        argv = self.calls[0][0]
        self.assertEqual(['-C', str(self.stage / 'modules/base')], argv[2:4])
        self.assertIn('KERNEL_SRC=' + str(self.top / 'common'), argv)
        self.assertIn('KERNEL_OUT=' + str(self.device), argv)
        self.assertEqual({'base': self.out / 'vendor'}, outputs)
        (self.out / 'vendor' / 'extra.ko').write_bytes(b'ko')
        self.assertRaisesRegex(KernelError, 'base: the built modules differ from the registry: extra.ko', build.base_step,
                               self.ctx, step, self.stage, {})

    def test_the_base_layer_is_held_to_the_kernel_values_too(self):
        (self.stage / 'modules/base/defconfig').write_text('CONFIG_SMP=n\n')
        step = {'name': 'base', 'kind': 'base', 'package': 'base', 'output': 'vendor'}
        self.assertRaisesRegex(KernelError, 'base sets kernel symbols to other values', build.base_step, self.ctx, step,
                               self.stage, {})

    def groups(self, groups):
        script = self.stage / 'modules/display/build/check.sh'
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/bash\n')

        def made(argv, env):
            for group in groups:
                (Path(env['OUT']) / 'obj' / group).mkdir(parents=True)
                (Path(env['OUT']) / 'obj' / group / (group + '.ko')).write_bytes(b'ko')
        self.made = made
        return {'name': 'display', 'kind': 'groups', 'package': 'display', 'output': 'p4', 'groups': ['emkit', 'kgsl'],
                'symbols': ['earlier'], 'defconfigs': ['base', 'thermal']}

    def test_the_display_groups_run_the_vendor_check_script_with_its_environment(self):
        step = self.groups(['emkit', 'kgsl'])
        outputs = {}
        self.assertEqual(['emkit.ko', 'kgsl.ko'], build.groups_step(self.ctx, step, self.stage, outputs))
        argv, cwd, env = self.calls[0]
        self.assertEqual(['bash', str(self.stage / 'modules/display/build/check.sh'), 'emkit', 'kgsl'], argv)
        self.assertEqual(str(self.device), env['KOUT'])
        self.assertEqual(str(self.top / 'common'), env['KSRC'])
        self.assertEqual('make ARCH=arm64', env['MK'])
        self.assertEqual('%s %s' % (self.out / 'vendor' / build.SYMVERS, self.out / 'earlier' / build.SYMVERS), env['BASE_SYMVERS'])
        self.assertEqual('%s %s' % (self.stage / 'modules/base/defconfig', self.stage / 'modules/thermal/defconfig'), env['VCONFIG'])
        self.assertEqual({'p4/obj/emkit': self.out / 'p4/obj/emkit', 'p4/obj/kgsl': self.out / 'p4/obj/kgsl'}, outputs)

    def test_a_group_without_a_module_fails(self):
        step = self.groups(['emkit'])
        self.assertRaisesRegex(KernelError, 'the group kgsl built no module', build.groups_step, self.ctx, step, self.stage, {})


class OverrideTests(unittest.TestCase):
    def test_conflicts_are_the_values_that_differ_from_the_kernel_configuration(self):
        kernel = {'CONFIG_A': 'y', 'CONFIG_B': 'n', 'CONFIG_C': 'm'}
        self.assertEqual({'CONFIG_B': ['m', 'n'], 'CONFIG_C': ['n', 'm']},
                         build.vendor_conflicts(kernel, {'CONFIG_A': 'y', 'CONFIG_B': 'm', 'CONFIG_C': 'n', 'CONFIG_D': 'y'}))


class HostToolTests(unittest.TestCase):
    def test_records_where_each_host_tool_is_with_a_digest_and_its_version(self):
        with tempfile.TemporaryDirectory() as temp:
            top = Path(temp)
            tool = top / 'tool'
            tool.write_text('binary')
            (top / 'farm/lib').mkdir(parents=True)
            library = top / 'libdw-0.1.so'
            library.write_text('library')
            (top / 'farm/lib/libdw.so').symlink_to(library)
            with patch.object(build.shutil, 'which', lambda name, path=None: str(tool)), \
                    patch.object(build, 'call', lambda argv, cwd=None, env=None: 'tool 1.2\nmore\n'):
                found = build.host_tools(top / 'farm', ['libdw.so.1'])
            self.assertEqual(set(build.HOST_TOOLS) | {'libdw.so.1'}, set(found))
            self.assertEqual({'path': str(tool.resolve()), 'sha256': found['make']['sha256'], 'version': 'tool 1.2'}, found['make'])
            self.assertEqual(64, len(found['make']['sha256']))
            self.assertEqual(str(library.resolve()), found['libdw.so.1']['path'])
            with patch.object(build.shutil, 'which', lambda name, path=None: None):
                self.assertRaisesRegex(KernelError, 'host tool missing', build.host_tools, top / 'farm', [])
            (top / 'farm/lib/libdw.so').unlink()
            with patch.object(build.shutil, 'which', lambda name, path=None: str(tool)), \
                    patch.object(build, 'call', lambda argv, cwd=None, env=None: ''):
                self.assertRaisesRegex(KernelError, 'host library missing', build.host_tools, top / 'farm', ['libdw.so.1'])


if __name__ == '__main__':
    unittest.main()
