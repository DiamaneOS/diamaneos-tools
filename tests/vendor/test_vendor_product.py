"""Reject inconsistent native installation and dependency declarations."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import vendor_product
from diamaneos_tools.vendor import VendorError

ROOT = Path(__file__).resolve().parents[2]


class NativeProductTests(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'config/fp6-minimal/vendor-files.json').read_text())
        self.selection = json.loads((ROOT / 'config/fp6-minimal/vendor-elf.json').read_text())

    def render(self):
        return vendor_product.render(self.recipe, self.selection, 'synthetic_notice_kind')

    def test_deterministic_and_source_interfaces_are_not_packaged_as_stock(self):
        first = self.render()
        self.assertEqual(first, self.render())
        modules = json.loads(first['modules.json'])
        self.assertEqual(len(modules), len(set(modules)))
        self.assertNotIn('fp6_stock_vendor_lib64_libdrm', modules)
        self.assertIn(b'"libdrm"', first['Android.bp'])
        self.assertIn(b'vendor.qti.hardware.perf2.xml', first['Android.bp'])
        for stem in ['liblearningmodule', 'libmemperfd', 'libmeters', 'libprotobuf-cpp-lite-21.7']:
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertIn('fp6_stock_vendor_lib64_libqti-perfd', modules)
        self.assertNotIn(b'vendor/etc/lm/', first['device-vendor.mk'])

    def test_missing_or_changed_firmware_rejected(self):
        path = self.selection['firmware_inputs'][0]['path']
        self.assertIn(path.encode(), self.render()['device-vendor.mk'])
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != path]
        with self.assertRaises(VendorError): self.render()

    def test_missing_runtime_root_rejected(self):
        self.selection['roots'].append('vendor/lib64/missing.so')
        with self.assertRaises(VendorError): self.render()

    def test_changed_elf_identity_rejected(self):
        self.selection['files'][0]['sha256'] = '0' * 64
        with self.assertRaises(VendorError): self.render()

    def test_missing_activation_rejected(self):
        self.recipe['files'] = [r for r in self.recipe['files']
                                if not r['path'].endswith('qseecomd.rc')]
        with self.assertRaises(VendorError): self.render()

    def test_unknown_install_input_rejected(self):
        row = copy.deepcopy(self.recipe['files'][0])
        row['path'] = 'vendor/bin/unreviewed-service'
        self.recipe['files'].append(row)
        with self.assertRaises(VendorError): self.render()

    def test_missing_dependency_edge_rejected(self):
        edge = next(e for e in self.selection['edges'] if e['kind'] == 'selected-stock')
        self.selection['edges'].remove(edge)
        with self.assertRaises(VendorError): self.render()

    def test_ambiguous_dependency_rejected(self):
        self.selection['edges'].append(copy.deepcopy(self.selection['edges'][0]))
        with self.assertRaises(VendorError): self.render()

    def test_unreviewed_namespace_rejected(self):
        edge = next(e for e in self.selection['edges'] if e['kind'] == 'platform-or-vndk34')
        edge['export_lists'] = ['/etc/unreviewed.txt']
        with self.assertRaises(VendorError): self.render()

    def test_configuration_transform_rejects_unreviewed_bytes(self):
        with self.assertRaises(VendorError): vendor_product.performance_config(b'<PerfConfigsStore/>')


if __name__ == '__main__': unittest.main()
