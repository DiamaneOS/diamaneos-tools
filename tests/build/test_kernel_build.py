import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel, kernel_build as build
from diamaneos_tools.kernel_util import KernelError

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationTests(unittest.TestCase):
    def test_requests_are_the_last_value_asked_for(self):
        wanted = build.requested_values(['CONFIG_A=y\n# CONFIG_B is not set\n', 'CONFIG_A=m\nCONFIG_C=0x10\n# comment\n'])
        self.assertEqual({'CONFIG_A': 'm', 'CONFIG_B': 'n', 'CONFIG_C': '0x10'}, wanted)

    def test_unmet_requests_name_what_kconfig_changed(self):
        final = b'CONFIG_A=y\nCONFIG_C=1\n# CONFIG_D is not set\n'
        unmet = build.unmet_requests(['CONFIG_A=m\nCONFIG_B=y\nCONFIG_C=1\n# CONFIG_D is not set\n# CONFIG_E is not set\n'], final)
        self.assertEqual({'CONFIG_A': ['m', 'y'], 'CONFIG_B': ['y', None]}, unmet)

    def test_device_build_may_differ_only_in_its_declared_symbols(self):
        core = b'CONFIG_A=y\n# CONFIG_CFG80211 is not set\nCONFIG_MODULE_SIG_ALL=y\n'
        device = b'CONFIG_A=y\nCONFIG_CFG80211=m\n# CONFIG_MODULE_SIG_ALL is not set\n'
        allowed = ['CONFIG_CFG80211', 'CONFIG_MODULE_SIG_ALL']
        self.assertEqual({'CONFIG_CFG80211': ['n', 'm'], 'CONFIG_MODULE_SIG_ALL': ['y', 'n']},
                         build.device_config_differences(core, device, allowed))
        self.assertRaisesRegex(KernelError, 'changes the GKI configuration: CONFIG_A', build.device_config_differences,
                               core, device.replace(b'CONFIG_A=y', b'CONFIG_A=m'), allowed)

    def test_merge_puts_the_defconfig_before_the_fragments(self):
        with tempfile.TemporaryDirectory() as temp:
            top = Path(temp)
            (top / 'a').write_text('CONFIG_A=y\n')
            (top / 'b').write_text('CONFIG_A=n\n')
            build.merge_config([top / 'a', top / 'b'], top / 'out/.config')
            self.assertEqual(['CONFIG_A=y', '', 'CONFIG_A=n'], (top / 'out/.config').read_text().rstrip('\n').split('\n'))
            self.assertRaises(KernelError, build.merge_config, [top / 'missing'], top / 'x')

    def test_the_kernel_exports_are_the_vmlinux_rows(self):
        table = '0x1\tfoo\tvmlinux\tEXPORT_SYMBOL\t\n0x2\tbar\tnet/x/mod\tEXPORT_SYMBOL_GPL\t\n'
        self.assertEqual(['0x1\tfoo\tvmlinux\tEXPORT_SYMBOL\t'], build.vmlinux_exports(table))

    def test_visibility_warnings_are_found_in_a_log(self):
        log = b'ok\nfoo.c:1:2: warning: declaration of struct x will not be visible [-Wvisibility]\nother\n'
        self.assertEqual(1, len(build.visibility_warnings(log)))
        self.assertEqual([], build.visibility_warnings(b'clean\n'))


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = json.loads((ROOT / 'config/fp6-kernel-build.json').read_text())
        self.plan['toolchain']['system_libraries'] = []
        tc = self.plan['toolchain']
        files = [tc['bindgen'], tc['kernel_build_tools'] + '/bin/pahole', tc['zlib_include'] + '/zlib.h',
                 tc['zlib_include'] + '/x86_64-linux-gnu/zconf.h', tc['host_libraries'] + '/lib64/libz-host.so']
        files += [tc['build_tools'] + '/' + n for n in ('bison', 'flex', 'm4', 'gavinhoward-bc')]
        files += [tc['kernel_build_tools'] + '/include/' + n for n in ('elfutils', 'gelf.h', 'libelf.h', 'dwarf.h', 'nlist.h')]
        for name in files:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text('x')
        for name in ('clang', 'rust', 'bison_data', 'host_libraries'):
            (self.root / tc[name]).mkdir(parents=True, exist_ok=True)

    def test_the_environment_holds_the_pinned_toolchain_and_nothing_of_the_callers(self):
        with patch.dict('os.environ', {'LD_PRELOAD': 'evil.so', 'MAKEFLAGS': '-j1'}):
            env, make = build.toolchain(self.root, self.plan, self.root / 'farm', '1793273496', [Path('/ws/common'), Path('/ws/run/out')])
        self.assertTrue(env['PATH'].startswith(str(self.root / 'toolchain/clang/clang-r584948c/bin')))
        self.assertNotIn('LD_PRELOAD', env)
        self.assertNotIn('MAKEFLAGS', env)
        self.assertEqual('1793273496', env['SOURCE_DATE_EPOCH'])
        self.assertIn('2026 ', env['KBUILD_BUILD_TIMESTAMP'] + ' ')
        # Neither C nor Rust code keeps the build directories in its file names.
        self.assertEqual('-D__ANDROID_COMMON_KERNEL__ -ffile-prefix-map=/ws/common= -ffile-prefix-map=/ws/run/out=', env['KCFLAGS'])
        self.assertEqual('--remap-path-prefix=/ws/common= --remap-path-prefix=/ws/run/out=', env['KRUSTFLAGS'])
        self.assertEqual(['make', 'ARCH=arm64', 'LLVM=1', 'LLVM_IAS=1'], make[:4])
        self.assertTrue(make[4].startswith('HOSTCFLAGS=-I'))
        self.assertTrue((self.root / 'farm/bin/bison').is_symlink())
        self.assertEqual((self.root / 'farm/include/zlib.h').resolve(), (self.root / self.plan['toolchain']['zlib_include'] / 'zlib.h').resolve())

    def test_pahole_runs_with_one_thread(self):
        build.toolchain(self.root, self.plan, self.root / 'farm', '1')
        wrapper = self.root / 'farm/bin/pahole'
        recorder = self.root / 'real-pahole'
        recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        recorder.chmod(0o755)
        build.single_thread_pahole(wrapper, recorder)
        shown = subprocess.run([str(wrapper), '-j32', '--btf_features=var', 'vmlinux'], capture_output=True, text=True).stdout
        self.assertEqual(['-j1', '--btf_features=var', 'vmlinux'], shown.split())

    def test_a_missing_toolchain_part_stops_the_build(self):
        (self.root / self.plan['toolchain']['build_tools'] / 'bison').unlink()
        self.assertRaisesRegex(KernelError, 'toolchain tool missing: bison', build.toolchain, self.root, self.plan,
                               self.root / 'farm', '1')


class VendorTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.top / 'vendor'), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                                        '-c', 'commit.gpgsign=false', *args], text=True).strip()

    def test_the_modules_tree_comes_from_the_commit_not_the_checkout(self):
        (self.top / 'vendor/modules/base').mkdir(parents=True)
        subprocess.check_call(['git', '-C', str(self.top / 'vendor'), 'init', '-q'])
        (self.top / 'vendor/modules/base/Makefile').write_text('all:\n')
        (self.top / 'vendor/other.txt').write_text('x')
        self.git('add', '-A')
        self.git('commit', '-qm', 'one')
        revision = self.git('rev-parse', 'HEAD')
        (self.top / 'vendor/modules/base/Makefile').write_text('changed\n')
        (self.top / 'vendor/modules/untracked.c').write_text('x')
        build.stage_vendor_modules(self.top / 'vendor', revision, self.top / 'stage')
        self.assertEqual('all:\n', (self.top / 'stage/modules/base/Makefile').read_text())
        self.assertFalse((self.top / 'stage/modules/untracked.c').exists())
        self.assertFalse((self.top / 'stage/other.txt').exists())

    def test_a_module_name_is_built_once(self):
        for directory in ('a', 'b'):
            (self.top / directory).mkdir()
            (self.top / directory / 'x.ko').write_bytes(b'1')
        self.assertEqual(['x.ko'], list(build.collect([self.top / 'a'])))
        self.assertRaisesRegex(KernelError, 'two builds make the module x.ko', build.collect, [self.top / 'a', self.top / 'b'])

    def test_the_effective_wifi_configuration_must_hold_every_vendor_value(self):
        (self.top / 'vendor.defconfig').write_text('CONFIG_A=y\n# CONFIG_B is not set\n')
        (self.top / 'effective').write_text('CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C=y\n')
        build.check_defconfig(self.top / 'vendor.defconfig', self.top / 'effective')
        (self.top / 'effective').write_text('CONFIG_A=y\nCONFIG_B=y\n')
        self.assertRaisesRegex(KernelError, 'CONFIG_B', build.check_defconfig, self.top / 'vendor.defconfig', self.top / 'effective')


class BuildPlanTests(unittest.TestCase):
    """The shipped build plan is consistent with itself."""

    def setUp(self):
        self.plan = json.loads((ROOT / 'config/fp6-kernel-build.json').read_text())

    def test_every_step_reads_only_symbol_tables_of_earlier_steps(self):
        built = set()
        for step in self.plan['vendor']['steps']:
            self.assertIn(step['kind'], build.STEPS)
            for name in step.get('symbols', []):
                self.assertIn(name, built, step['name'] + ' reads ' + name + ' before it is built')
            built.add(step.get('output', step['name']))
            if step['kind'] == 'groups':
                built |= {'p4/obj/' + g for g in step['groups']}
        self.assertEqual('base', self.plan['vendor']['steps'][0]['kind'])

    def test_package_names_are_unique_outputs(self):
        outputs = [s.get('output', s['name']) for s in self.plan['vendor']['steps']]
        self.assertEqual(len(outputs), len(set(outputs)))

    def test_the_device_kernel_adds_only_drivers_the_gki_build_lacks(self):
        device = self.plan['device']
        self.assertEqual(['bam_dma.ko', 'cfg80211.ko', 'cqhci.ko', 'mac80211.ko'], sorted(Path(m).name for m in device['modules']))
        self.assertIn('CONFIG_MODULE_SIG_ALL', device['config_symbols'])
        self.assertTrue(device['reason'].strip())

    def test_the_production_profile_builds_the_production_fragments(self):
        core = self.plan['core']
        self.assertEqual({'production', 'development'}, set(core['profile_fragments']))
        self.assertEqual(set(kernel.CONFIG_PROFILES), set(core['profile_fragments']))


if __name__ == '__main__':
    unittest.main()
