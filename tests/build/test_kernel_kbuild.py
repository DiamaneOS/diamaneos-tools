from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel_kbuild as kbuild

REGISTRY = '''
def register_modules(registry):
    registry.register(
        name = "sample_module", out = "sample.ko", srcs = ["drivers/a.c", "drivers/b.c"],
        conditional_srcs = {"CONFIG_FEATURE": {True: ["drivers/feature.c"], False: ["drivers/plain.c"]}},
        includes = ["include", "drivers/inc"], local_defines = ["LOCAL=1"],
        copts = ["-Wno-error", "-include", "wlan/gen.h"])
    registry.register(name = "single", out = "single.ko", srcs = ["single.c"], includes = [], local_defines = [], copts = [])
    registry.register(name = "late", out = "late.ko", srcs = ["late/x.c"], includes = ["late/include"], late_includes = True)
'''


class KbuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.pkg, self.base = self.top / 'pkg', self.top / 'base'
        for directory in (self.pkg / 'fp6', self.base):
            directory.mkdir(parents=True)
        (self.pkg / 'modules.bzl').write_text(REGISTRY)
        (self.pkg / 'defconfig').write_text('CONFIG_PKG_OPTION=y\n# CONFIG_OFF is not set\nCONFIG_LEVEL=3\n')
        (self.pkg / 'Kconfig').write_text('config PKG_OPTION\n\tbool "x"\n\tselect SELECTED\n\nconfig OTHER\n\tbool "y"\n')
        (self.base / 'defconfig').write_text('CONFIG_FROM_BASE=m\nCONFIG_KERNEL_SAME=y\n')
        (self.pkg / 'fp6/rules.extra').write_text('genrule(\n    name = "probe",\n    outs = ["wlan/gen.h"],\n)\n')
        (self.pkg / 'wlan').mkdir()
        (self.pkg / 'wlan/gen.h').write_text('')
        self.auto = self.top / 'auto.conf'
        self.auto.write_text('CONFIG_KERNEL_SAME=y\nCONFIG_FEATURE=y\n')
        self.out = self.top / 'out'

    def generate(self, **kwargs):
        return kbuild.generate(str(self.pkg), str(self.base), str(self.out), str(self.auto), **kwargs)

    def test_writes_objects_flags_and_the_vendor_configuration(self):
        table = self.generate()
        kbuild_text = (self.pkg / 'Kbuild').read_text()
        self.assertIn('obj-m += sample.o', kbuild_text)
        self.assertIn('sample-y := drivers/a.o drivers/b.o drivers/feature.o', kbuild_text)
        self.assertNotIn('plain.o', kbuild_text)
        # A module named as its only object needs no object list.
        self.assertIn('obj-m += single.o', kbuild_text)
        self.assertNotIn('single-y', kbuild_text)
        flags = [l for l in kbuild_text.splitlines() if l.startswith('CFLAGS_drivers/a.o')][0]
        for part in ('-I$(VENDOR_ROOT)/include', '-I$(VENDOR_ROOT)/drivers', '-DLOCAL=1', '-Wno-error',
                     '-include $(VENDOR_ROOT)/wlan/gen.h'):
            self.assertIn(part, flags)
        self.assertEqual(3, len(table))
        header = (self.out / 'vendor_autoconf.h').read_text()
        self.assertIn('#define CONFIG_PKG_OPTION 1', header)
        self.assertIn('#define CONFIG_FROM_BASE_MODULE 1', header)
        self.assertIn('#define CONFIG_LEVEL 3', header)
        self.assertIn('#define CONFIG_SELECTED 1', header)
        # Values the kernel's own configuration already has, and options that are off, define nothing.
        self.assertNotIn('KERNEL_SAME', header)
        self.assertNotIn('CONFIG_OFF', header)

    def test_conditional_sources_follow_the_kernel_configuration(self):
        self.auto.write_text('CONFIG_KERNEL_SAME=y\n')
        self.generate()
        self.assertIn('drivers/plain.o', (self.pkg / 'Kbuild').read_text())

    def test_only_selects_modules_and_keeps_late_include_directories_out_of_the_early_flags(self):
        self.generate(only=('sample', 'late'))
        text = (self.pkg / 'Kbuild').read_text()
        self.assertNotIn('single', text)
        early = [l for l in text.splitlines() if l.startswith('LINUXINCLUDE')][0]
        self.assertIn('-I$(VENDOR_ROOT)/include', early)
        self.assertNotIn('late/include', early)
        self.assertRaisesRegex(kbuild.KbuildError, 'not in modules.bzl: missing', self.generate, only=('missing',))

    def test_extra_defconfig_files_add_values_last(self):
        (self.pkg / 'extra.config').write_text('CONFIG_LEVEL=9\n')
        self.generate(extra_defconfig=['extra.config'])
        self.assertIn('#define CONFIG_LEVEL 9', (self.out / 'vendor_autoconf.h').read_text())

    def test_an_unknown_genrule_in_copts_is_refused(self):
        (self.pkg / 'modules.bzl').write_text(REGISTRY.replace('"wlan/gen.h"]', '"$(location :nothing)"]'))
        self.assertRaisesRegex(kbuild.KbuildError, 'unknown genrule', self.generate)

    def test_registry_needs_registrations_with_output_names(self):
        (self.pkg / 'modules.bzl').write_text('def register_modules(registry):\n    registry.register(name = "x", out = "x")\n')
        self.assertRaisesRegex(kbuild.KbuildError, 'invalid module registration', self.generate)

    def test_feature_probes_define_what_the_sources_contain(self):
        source = self.top / 'src'
        (source / 'net').mkdir(parents=True)
        (source / 'net/a.c').write_text('int nl80211_validate_key_link_id(void);\n')
        lines = kbuild.feature_probes(str(source), [('net/a.c', 'nl80211_validate_key_link_id', 'HAS_LINK_ID'),
                                                     ('net/a.c', 'punct_bitmap', 'HAS_PUNCT')])
        self.assertEqual(['#define HAS_LINK_ID (1)'], lines)


if __name__ == '__main__':
    unittest.main()
