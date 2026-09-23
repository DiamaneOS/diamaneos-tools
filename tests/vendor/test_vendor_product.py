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

    def test_qseecomd_listeners_installed_as_required_not_linked(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp = rendered['Android.bp'].decode()
        qseecomd = bp[bp.index('name: "fp6_stock_vendor_bin_qseecomd"'):]
        qseecomd = qseecomd[:qseecomd.index('}\n')]
        for stem in ['librpmb', 'libssd', 'libgpt', 'libdrmtime', 'libGPreqcancel',
                     'libops', 'libqisl', 'libspl']:
            name = 'fp6_stock_vendor_lib64_' + stem
            self.assertIn(name, modules)
            required = qseecomd[qseecomd.index('required:'):]
            self.assertIn('"' + name + '"', required[:required.index('\n')])
            self.assertNotIn('"' + name + '"', qseecomd[qseecomd.index('shared_libs:'):].split('\n')[0])
        for stem in ['libGPreqcancel_svc', 'libtime_genoff']:
            self.assertIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertIn('"vendor.qti.hardware.display.config-V7-ndk"', bp)

    def test_source_display_stack_replaces_stock_services(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        for stem in ['bin_hw_vendor.qti.hardware.display.composer-service',
                     'bin_hw_vendor.qti.hardware.display.allocator-service',
                     'lib64_hw_android.hardware.graphics.mapper@4.0-impl-qti-display',
                     'lib64_libsdmcore', 'lib64_libgralloc.qti']:
            self.assertNotIn('fp6_stock_vendor_' + stem, modules)
        # Closed libraries the source stack loads with dlopen stay installed.
        for stem in ['libsdm-color', 'libsnapdragoncolor-manager', 'libsdm-disp-vndapis',
                     'libmemutils', 'libqrtrclient', 'libadreno_utils']:
            self.assertIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertIn(b'"libgralloc.qti"', rendered['Android.bp'])

    def test_adreno_compiler_backends_installed_as_required(self):
        bp = self.render()['Android.bp'].decode()
        for loader, stem in [('fp6_stock_vendor_lib64_libllvm-glnext', 'libllvm-qgl'),
                             ('fp6_stock_vendor_lib64_egl_libGLESv2_adreno', 'libCB')]:
            block = bp[bp.index('name: "' + loader + '"'):]
            block = block[:block.index('}\n')]
            self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', block[block.index('required:'):].split('\n')[0])

    def test_remote_processor_support_daemons_installed_with_activation(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp = rendered['Android.bp'].decode()
        for stem in ['pd-mapper', 'pm-service', 'pm-proxy', 'rmt_storage', 'tftp_server']:
            self.assertIn('fp6_stock_vendor_bin_' + stem, modules)
        for stem, rc in [('rmt_storage', 'vendor.qti.rmt_storage.rc'), ('tftp_server', 'vendor.qti.tftp.rc')]:
            block = bp[bp.index('name: "fp6_stock_vendor_bin_' + stem + '"'):]
            self.assertIn('init_rc: ["files/vendor/etc/init/' + rc + '"]', block[:block.index('}\n')])
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        for stem in ['libqrtr', 'libqmi_cci', 'libqmi_csi', 'libperipheral_client']:
            self.assertEqual('firmware-trusted-boot', owners['vendor/lib64/' + stem + '.so'])

    def test_display_color_manager_installed_as_required(self):
        bp = self.render()['Android.bp'].decode()
        for loader, stems in [('fp6_stock_vendor_lib64_libsnapdragoncolor-manager', ['libcolor-default', 'libsnapdragoncolor-qdcm']),
                              ('fp6_stock_vendor_lib64_libsnapdragoncolor-qdcm', ['libqdcm-algo', 'libqdcm-json-mode-parser'])]:
            block = bp[bp.index('name: "' + loader + '"'):]
            block = block[:block.index('}\n')]
            for stem in stems:
                self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', block[block.index('required:'):].split('\n')[0])
        self.assertNotIn('fp6_stock_vendor_lib64_libsdmextension', bp)
        make = self.render()['device-vendor.mk'].decode()
        self.assertIn('vendor/etc/display/qdcm_calib_data_nt37705_amoled_command_mode_dsi_panel.json', make)
        self.assertIn('vendor/etc/snapdragon_color_libs_config.xml', make)

    def test_color_manager_uses_old_abi_tinyxml2_module(self):
        bp = self.render()['Android.bp'].decode()
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libsnapdragoncolor-manager"'):]
        shared = block[:block.index('}\n')]
        shared = shared[shared.index('shared_libs:'):].split('\n')[0]
        self.assertIn('"libtxml2v34"', shared)
        self.assertNotIn('"libtinyxml2"', shared)

    def test_dependency_rewrite_rejects_unsafe_input(self):
        with self.assertRaises(VendorError):
            vendor_product.rewrite_needed(b'\x7fELF\x02\x01' + bytes(64), 'libtinyxml2.so', 'libtxml2v3.so')
        with self.assertRaises(VendorError):
            vendor_product.rewrite_needed(b'not an elf', 'libtinyxml2.so', 'libtxml2v34.so')

    def test_vendor_has_no_vndk_version_and_blobs_use_current_variants(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        self.assertNotIn('.vndk.', bp)
        self.assertNotIn('ro.vndk.version', make)
        self.assertNotIn('PRODUCT_EXTRA_VNDK_VERSIONS', make)
        color = bp[bp.index('name: "fp6_stock_vendor_bin_hw_vendor.qti.hardware.display.color-service"'):]
        self.assertIn('"libbinder"', color[:color.index('}\n')])

    def test_stock_display_vintf_fragments_are_not_installed(self):
        make = self.render()['device-vendor.mk'].decode()
        for name in ['vendor.qti.hardware.display.composer-service.xml', 'vendor.qti.hardware.display.allocator-service.xml',
                     'android.hardware.graphics.mapper-impl-qti-display.xml']:
            self.assertNotIn(name, make)

    def test_fp6_fingerprint_uses_stock_hardware_and_source_interfaces(self):
        rendered = self.render()
        bp = rendered['Android.bp'].decode()
        service = bp[bp.index('name: "fp6_stock_vendor_bin_hw_android.hardware.biometrics.fingerprint-service"'):]
        service = service[:service.index('}\n')]
        # FP6-specific service names and the module's own SONAME as its file
        # name: no collision with AOSP's reference fingerprint service or
        # libhardware stub, no module shadowing, and Soong's ELF checks hold.
        self.assertIn('"fp6_stock_vendor_lib64_hw_libfingerprint.default"', service)
        driver = bp[bp.index('name: "fp6_stock_vendor_lib64_hw_libfingerprint.default"'):]
        driver = driver[:driver.index('}\n')]
        self.assertNotIn('prefer', driver)
        self.assertIn('files/vendor/lib64/hw/libfingerprint.default.so', driver)
        self.assertNotIn('check_elf_files', driver)
        self.assertIn('android.hardware.biometrics.fingerprint-service.fp6.rc', service)
        self.assertIn('android.hardware.biometrics.fingerprint-service.fp6.xml', service)
        self.assertNotIn('name: "fingerprint.default"', bp)
        self.assertNotIn('fingerprint-default.rc', bp)
        stock = {r['path']: r['input'] for r in self.recipe['files']}
        self.assertEqual(stock['vendor/lib64/hw/libfingerprint.default.so'], 'vendor/lib64/hw/fingerprint.default.so')
        for stem in ['android.hardware.biometrics.common-V3-ndk',
                     'android.hardware.biometrics.fingerprint-V3-ndk']:
            self.assertIn('"' + stem + '"', service)
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, bp)
        self.assertIn('android.hardware.fingerprint.xml', rendered['device-vendor.mk'].decode())
        self.assertNotIn('android.hardware.biometrics.face', bp)

    def test_unreviewed_fingerprint_source_dependency_rejected(self):
        edge = next(e for e in self.selection['edges'] if e['kind'] == 'source-module')
        edge['needed'] = 'unreviewed-source-V3-ndk.so'
        with self.assertRaises(VendorError): self.render()

    def test_undeclared_runtime_edge_rejected(self):
        row = next(r for r in self.recipe['files'] if r['path'] == 'vendor/bin/qseecomd')
        row['runtime_dependencies'] = row['runtime_dependencies'][1:]
        with self.assertRaises(VendorError): self.render()

    def test_missing_runtime_edge_rejected(self):
        edge = next(e for e in self.selection['edges'] if e['kind'] == 'selected-stock-runtime')
        self.selection['edges'].remove(edge)
        with self.assertRaises(VendorError): self.render()

    def test_runtime_edge_soname_must_match_provider(self):
        edge = next(e for e in self.selection['edges'] if e['kind'] == 'selected-stock-runtime')
        edge['needed'] = 'libother.so'
        with self.assertRaises(VendorError): self.render()

    def test_configuration_transform_rejects_unreviewed_bytes(self):
        with self.assertRaises(VendorError): vendor_product.performance_config(b'<PerfConfigsStore/>')


if __name__ == '__main__': unittest.main()
