"""Reject inconsistent native installation and dependency declarations."""
import copy
import hashlib
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
        for stem in ['liblearningmodule', 'libmemperfd', 'libmeters']:
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
        for stem in ['pd-mapper', 'pm-service', 'pm-proxy', 'rmt_storage', 'tftp_server', 'ssr_setup']:
            self.assertIn('fp6_stock_vendor_bin_' + stem, modules)
        # The device modem/init.modem.rc starts ssr_setup; no stock rc is installed.
        block = bp[bp.index('name: "fp6_stock_vendor_bin_ssr_setup"'):]
        self.assertNotIn('init_rc:', block[:block.index('}\n')])
        # The full-RAM subsystem dump collector stays out (privacy).
        self.assertNotIn('fp6_stock_vendor_bin_subsystem_ramdump', modules)
        for stem, rc in [('rmt_storage', 'vendor.qti.rmt_storage.rc'), ('tftp_server', 'vendor.qti.tftp.rc')]:
            block = bp[bp.index('name: "fp6_stock_vendor_bin_' + stem + '"'):]
            self.assertIn('init_rc: ["files/vendor/etc/init/' + rc + '"]', block[:block.index('}\n')])
        # Userspace daemons and libraries have their own component, not the
        # firmware they talk to (whose replacement needs OEM signing).
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        for stem in ['libqrtr', 'libqmi_cci', 'libqmi_csi', 'libperipheral_client', 'libjson']:
            self.assertEqual('remote-processor-services', owners['vendor/lib64/' + stem + '.so'])
        for stem in ['pd-mapper', 'pm-service', 'pm-proxy', 'rmt_storage', 'tftp_server', 'ssr_setup']:
            self.assertEqual('remote-processor-services', owners['vendor/bin/' + stem])
        self.assertFalse([p for p, owner in owners.items() if owner == 'firmware-trusted-boot'
                          and p.startswith(('vendor/bin/', 'vendor/lib64/'))])

    def test_display_color_manager_installed_as_required(self):
        bp = self.render()['Android.bp'].decode()
        for loader, stems in [('fp6_stock_vendor_lib64_libsnapdragoncolor-manager', ['libcolor-default', 'libsnapdragoncolor-qdcm']),
                              ('fp6_stock_vendor_lib64_libsnapdragoncolor-qdcm', ['libqdcm-algo', 'libqdcm-json-mode-parser'])]:
            block = bp[bp.index('name: "' + loader + '"'):]
            block = block[:block.index('}\n')]
            for stem in stems:
                self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', block[block.index('required:'):].split('\n')[0])
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libsdmextension"'):]
        shared = block[:block.index('}\n')]
        for dep in ['fp6_stock_vendor_lib64_libdisplayqos', 'fp6_stock_vendor_lib64_libdisplayskuutils',
                    'android.hardware.thermal@2.0', 'libhidltransport']:
            self.assertIn('"' + dep + '"', shared)
        self.assertNotIn('fp6_stock_vendor_lib64_android.hardware.thermal', bp)
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

    def test_sensor_stack_installed_with_activation_and_configuration(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for name in ['fp6_stock_vendor_bin_sscrpcd', 'fp6_stock_vendor_lib64_sensors.qsh',
                     'fp6_stock_vendor_lib64_libssc_default_listener',
                     'fp6_stock_vendor_lib64_libprotobuf-cpp-lite-21.7']:
            self.assertIn(name, modules)
        # The AOSP multi-HAL, dynamic sub-HAL and sensors interfaces are source-built.
        for name in ['fp6_stock_vendor_bin_hw_android.hardware.sensors-service.multihal',
                     'fp6_stock_vendor_lib64_hw_sensors.dynamic_sensor_hal',
                     'fp6_stock_vendor_lib64_android.hardware.sensors@2.1']:
            self.assertNotIn(name, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_sensors.qsh"'):]
        block = block[:block.index('}\n')]
        self.assertIn('"android.hardware.sensors@2.1"', block)
        self.assertIn('"android.hardware.sensors@2.0-ScopedWakelock"', block)
        block = bp[bp.index('name: "fp6_stock_vendor_bin_sscrpcd"'):]
        block = block[:block.index('}\n')]
        self.assertIn('files/vendor/etc/init/vendor.sensors.sscrpcd.rc', block)
        self.assertIn('"fp6_stock_vendor_lib64_libssc_default_listener"', block)
        self.assertIn('"fp6_stock_vendor_lib64_libadsp_default_listener"', block)
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        for stem in ['libadsprpc', 'libcdsprpc', 'libadsp_default_listener', 'libvmmem']:
            self.assertEqual('remote-processor-services', owners['vendor/lib64/' + stem + '.so'])
        for path in ['sensors/hals.conf', 'sensors/sns_reg_config', 'sensors/config/volcano_tmd2755_0.json']:
            self.assertIn('vendor/fairphone/FP6/files/vendor/etc/' + path + ':$(TARGET_COPY_OUT_VENDOR)/etc/' + path, make)

    def test_bluetooth_hci_service_installed_with_device_activation(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for name in ['fp6_stock_vendor_bin_hw_android.hardware.bluetooth@1.1-service-qti',
                     'fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.0-impl-qti',
                     'fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.1-impl-qti',
                     'fp6_stock_vendor_lib64_libbtnv', 'fp6_stock_vendor_lib64_libsoc_helper']:
            self.assertIn(name, modules)
        # AOSP HCI interfaces are source-built; FM, ANT, SAR and config-store
        # implementations and the stock Bluetooth audio stack are not installed.
        for name in ['fp6_stock_vendor_lib64_android.hardware.bluetooth@1.0',
                     'fp6_stock_vendor_lib64_android.hardware.bluetooth@1.1',
                     'fp6_stock_vendor_lib64_hw_vendor.qti.hardware.fm@1.0-impl',
                     'fp6_stock_vendor_lib64_hw_com.dsi.ant@1.0-impl',
                     'fp6_stock_vendor_lib64_hw_vendor.qti.hardware.bluetooth_sar@1.1-impl',
                     'fp6_stock_vendor_lib64_hw_vendor.qti.hardware.btconfigstore@2.0-impl',
                     'fp6_stock_vendor_lib64_hw_audio.bluetooth_qti.default']:
            self.assertNotIn(name, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_android.hardware.bluetooth@1.1-service-qti"'):]
        block = block[:block.index('}\n')]
        self.assertNotIn('init_rc', block)
        self.assertNotIn('vintf_fragments', block)
        self.assertIn('"android.hardware.bluetooth@1.1"', block)
        self.assertIn('"fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.1-impl-qti"', block)
        self.assertNotIn('android.hardware.bluetooth@1.1-service-qti.rc', make)
        # The derived audio policy no longer includes the stock hearing-aid file.
        self.assertNotIn('bluetooth_qti_hearing_aid_audio_policy_configuration.xml', make)
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        self.assertEqual('connectivity-peripherals', owners['vendor/bin/hw/android.hardware.bluetooth@1.1-service-qti'])
        for stem in ['libqmiservices', 'libidl']:
            self.assertEqual('remote-processor-services', owners['vendor/lib64/' + stem + '.so'])

    def test_audio_policy_routes_bluetooth_through_software_module(self):
        path = 'vendor/etc/audio/sku_volcano/audio_policy_configuration.xml'
        with self.assertRaises(VendorError): vendor_product.audio_config(path, b'<audioPolicyConfiguration/>')
        rule = vendor_product.AUDIO_CONFIG_REWRITES[path]
        self.assertNotEqual(rule['source_sha256'], rule['sha256'])

    def test_bluetooth_audio_policy_derivation_on_synthetic_policy(self):
        # Same layout as the stock sku_volcano policy: offload-only A2DP/BLE
        # ports and routes go, SCO stays, the hearing-aid include is replaced.
        source = (b'<modules>\n'
                  b'            <devicePorts>\n'
                  b'                <devicePort tagName="BT SCO" type="AUDIO_DEVICE_OUT_BLUETOOTH_SCO" role="sink">\n'
                  b'                </devicePort>\n'
                  b'                <devicePort tagName="BT A2DP Out" type="AUDIO_DEVICE_OUT_BLUETOOTH_A2DP" role="sink"\n'
                  b'                            encodedFormats="AUDIO_FORMAT_SBC">\n'
                  b'                    <gains/>\n'
                  b'                </devicePort>\n'
                  b'                <devicePort tagName="BT BLE Broadcast" type="AUDIO_DEVICE_OUT_BLE_BROADCAST" role="sink"\n'
                  b'                            encodedFormats="AUDIO_FORMAT_LC3">\n'
                  b'                </devicePort>\n'
                  b'                <devicePort tagName="A2DP In" type="AUDIO_DEVICE_IN_BLUETOOTH_A2DP" role="source">\n'
                  b'                </devicePort>\n'
                  b'                <devicePort tagName="BLE In" type="AUDIO_DEVICE_IN_BLE_HEADSET" role="source">\n'
                  b'                </devicePort>\n'
                  b'            </devicePorts>\n'
                  b'            <routes>\n'
                  b'                <route type="mix" sink="BT SCO"\n'
                  b'                       sources="primary output"/>\n'
                  b'                <route type="mix" sink="BT A2DP Out"\n'
                  b'                       sources="primary output,deep_buffer"/>\n'
                  b'                <route type="mix" sink="BT BLE Broadcast"\n'
                  b'                       sources="primary output"/>\n'
                  b'                <route type="mix" sink="primary input"\n'
                  b'                       sources="Built-In Mic,BT SCO Headset Mic,A2DP In,BLE In,IP In"/>\n'
                  b'            </routes>\n'
                  b'        <!-- Bluetooth Audio HAL for hearing aid -->\n'
                  b'        <xi:include href="/vendor/etc/bluetooth_qti_hearing_aid_audio_policy_configuration.xml"/>\n'
                  b'</modules>\n')
        derived = vendor_product.bluetooth_software_audio_policy(source)
        for gone in [b'"BT A2DP Out"', b'"BT BLE Broadcast"', b'tagName="A2DP In"', b'tagName="BLE In"',
                     b'A2DP In,', b'BLE In,', b'bluetooth_qti_hearing_aid', b'encodedFormats']:
            self.assertNotIn(gone, derived)
        self.assertIn(b'<devicePort tagName="BT SCO" type="AUDIO_DEVICE_OUT_BLUETOOTH_SCO" role="sink">', derived)
        self.assertIn(b'<route type="mix" sink="BT SCO"\n                       sources="primary output"/>', derived)
        self.assertIn(b'sources="Built-In Mic,BT SCO Headset Mic,IP In"', derived)
        self.assertIn(b'<xi:include href="/vendor/etc/bluetooth_with_le_audio_policy_configuration_7_0.xml"/>', derived)
        self.assertEqual(derived.count(b'<devicePort '), 1)
        self.assertEqual(derived.count(b'<route '), 2)
        self.assertEqual(derived, vendor_product.bluetooth_software_audio_policy(derived))

    def test_nfc_hal_installed_with_activation_configuration_and_firmware(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for name in ['fp6_stock_vendor_bin_hw_android.hardware.nfc-service.sec',
                     'fp6_stock_vendor_lib64_nfc_nci_sec']:
            self.assertIn(name, modules)
        # The NFC AIDL and HIDL interfaces are source-built; the factory test
        # tool is not selected.
        for name in ['fp6_stock_vendor_lib64_android.hardware.nfc-V1-ndk',
                     'fp6_stock_vendor_lib64_android.hardware.nfc@1.2',
                     'fp6_stock_vendor_bin_sec_nfc_test']:
            self.assertNotIn(name, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_android.hardware.nfc-service.sec"'):]
        block = block[:block.index('}\n')]
        self.assertIn('files/vendor/etc/init/nfc-service-sec.rc', block)
        self.assertIn('files/vendor/etc/vintf/manifest/nfc-service-sec.xml', block)
        self.assertIn('"android.hardware.nfc-V1-ndk"', block)
        self.assertIn('"fp6_stock_vendor_lib64_nfc_nci_sec"', block)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_nfc_nci_sec"'):]
        block = block[:block.index('}\n')]
        for name in ['android.hardware.nfc@1.0', 'android.hardware.nfc@1.1',
                     'android.hardware.nfc@1.2', 'libhardware_legacy']:
            self.assertIn('"' + name + '"', block)
        for path in ['libnfc-sec-vendor.conf', 'libnfc-nci.conf', 'sec_s3nrn4v_hwreg.bin',
                     'sec_s3nrn4v_swreg.bin']:
            self.assertIn('vendor/fairphone/FP6/files/vendor/etc/' + path + ':$(TARGET_COPY_OUT_VENDOR)/etc/' + path, make)
        self.assertIn('files/vendor/firmware/sec_s3nrn4v_firmware.bin:$(TARGET_COPY_OUT_VENDOR)/firmware/sec_s3nrn4v_firmware.bin', make)
        self.assertNotIn('android.hardware.secure_element.xml', bp + make)
        self.assertNotIn('android.hardware.se.omapi.uicc.xml', make)

    def test_nfc_default_route_rewrite_binds_the_selected_stock_config(self):
        # The pinned host-route rewrite applies to exactly the selected stock
        # config; any other input is rejected before anything is derived.
        rows = {r['path']: r for r in self.recipe['files']}
        for path, rule in vendor_product.NFC_CONFIG_REWRITES.items():
            self.assertEqual(rows[path]['sha256'], rule['source_sha256'])
            self.assertNotEqual(rule['sha256'], rule['source_sha256'])
        path = 'vendor/etc/libnfc-sec-vendor.conf'
        with self.assertRaises(VendorError):
            vendor_product.nfc_config(path, b'DEFAULT_ROUTE=0x83\nDEFAULT_ISODEP_ROUTE=0x83\nDEFAULT_NFCF_ROUTE=0x83\n')
        with self.assertRaises(KeyError):
            vendor_product.nfc_config('vendor/etc/libnfc-nci.conf', b'')

    def test_gnss_hal_installed_with_activation_and_source_interfaces(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for stem in ['bin_hw_android.hardware.gnss-aidl-service-qti', 'lib64_hw_android.hardware.gnss-aidl-impl-qti',
                     'lib64_liblocation_api', 'lib64_libgnss', 'lib64_libloc_core', 'lib64_libloc_api_v02',
                     'lib64_libgps.utils']:
            self.assertIn('fp6_stock_vendor_' + stem, modules)
        # Interfaces are source-built; the Qualcomm cloud, Wi-Fi, daemon, batching and geofence layers are not selected.
        for stem in ['lib64_android.hardware.gnss-V3-ndk', 'lib64_android.hardware.health@2.1',
                     'bin_loc_launcher', 'bin_xtra-daemon', 'bin_lowi-server', 'bin_xtwifi-client',
                     'lib64_liblbs_core', 'lib64_libizat_core', 'lib64_vendor.qti.gnss-service',
                     'lib64_libbatching', 'lib64_libgeofencing', 'lib64_liblocation_qesdk', 'lib64_liblocdiagiface']:
            self.assertNotIn('fp6_stock_vendor_' + stem, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_android.hardware.gnss-aidl-service-qti"'):]
        block = block[:block.index('}\n')]
        self.assertIn('files/vendor/etc/init/android.hardware.gnss-aidl-service-qti.rc', block)
        self.assertIn('files/vendor/etc/vintf/manifest/android.hardware.gnss-aidl-service-qti.xml', block)
        self.assertIn('"android.hardware.gnss-V3-ndk"', block)
        self.assertIn('"fp6_stock_vendor_lib64_libloc_api_v02"', block)
        for name in ['vendor.qti.gnss-service.xml', 'loc-launcher.rc', 'xtwifi.conf', 'lowi.conf',
                     'gnss_antenna_info.conf', 'batching.conf']:
            self.assertNotIn(name, make)
        for name in ['gps.conf', 'izat.conf', 'sap.conf']:
            self.assertIn('files/vendor/etc/' + name + ':$(TARGET_COPY_OUT_VENDOR)/etc/' + name, make)

    def test_gnss_rewrites_bind_the_selected_stock_inputs(self):
        rows = {r['path']: r for r in self.recipe['files']}
        for path, rule in vendor_product.GNSS_CONFIG_REWRITES.items():
            self.assertEqual(rows[path]['sha256'], rule['source_sha256'])
            with self.assertRaises(VendorError): vendor_product.gnss_config(path, b'GTP_MODE=SDK\n')

    def test_gnss_rewrites_keep_logging_and_cloud_invariants(self):
        required = vendor_product.GNSS_REQUIRED['vendor/etc/gps.conf']
        for line in [b'\nLOG_BUFFER_ENABLED = 0\n', b'\nQXDM_LOG = 0\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n']:
            self.assertIn(line, required)
        self.assertIn(b'\nPROCESS_STATE=ENABLED', vendor_product.GNSS_FORBIDDEN['vendor/etc/izat.conf'])
        self.assertIn(b'ILocAidlGnss', vendor_product.GNSS_FORBIDDEN['vendor/etc/init/android.hardware.gnss-aidl-service-qti.rc'])
        self.assertEqual(set(vendor_product.GNSS_REQUIRED), set(vendor_product.GNSS_CONFIG_REWRITES))
        self.assertEqual(set(vendor_product.GNSS_FORBIDDEN), set(vendor_product.GNSS_CONFIG_REWRITES))

    def test_recipe_binds_the_current_component_model(self):
        model = ROOT / 'config/components.json'
        self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(), self.recipe['model_sha256'])

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

    def test_fp6_fingerprint_ships_stock_module_without_stock_service(self):
        rendered = self.render()
        bp = rendered['Android.bp'].decode()
        # The device tree builds the AIDL service; only the FocalTech module is
        # taken from stock, under its own SONAME so it cannot shadow AOSP's
        # libhardware stub and Soong's ELF checks hold.
        self.assertNotIn('android.hardware.biometrics.fingerprint-service', bp)
        self.assertNotIn('fingerprint-service', rendered['device-vendor.mk'].decode())
        driver = bp[bp.index('name: "fp6_stock_vendor_lib64_hw_libfingerprint.default"'):]
        driver = driver[:driver.index('}\n')]
        self.assertNotIn('prefer', driver)
        self.assertIn('files/vendor/lib64/hw/libfingerprint.default.so', driver)
        self.assertNotIn('check_elf_files', driver)
        self.assertNotIn('name: "fingerprint.default"', bp)
        self.assertIn('fp6_stock_vendor_lib64_hw_libfingerprint.default', rendered['device-vendor.mk'].decode())
        stock = {r['path']: r['input'] for r in self.recipe['files']}
        self.assertEqual(stock['vendor/lib64/hw/libfingerprint.default.so'], 'vendor/lib64/hw/fingerprint.default.so')
        self.assertIn('android.hardware.fingerprint.xml', rendered['device-vendor.mk'].decode())
        self.assertNotIn('android.hardware.biometrics.face', bp)

    def test_unreviewed_source_module_dependency_rejected(self):
        consumer = self.selection['files'][0]['path']
        self.selection['edges'].append({'consumer': consumer, 'needed': 'unreviewed-source-V3-ndk.so',
                                        'kind': 'source-module'})
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

    def test_camera_provider_installed_with_activation_and_source_interfaces(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_vendor.qti.camera.provider-service_64"'):]
        block = block[:block.index('}\n')]
        self.assertIn('init_rc: ["files/vendor/etc/init/vendor.qti.camera.provider-service_64.rc"]', block)
        self.assertIn('vintf_fragments: ["files/vendor/etc/vintf/manifest/vendor.qti.camera.provider.xml"]', block)
        shared = block[block.index('shared_libs:'):].split('\n')[0]
        self.assertIn('"android.hardware.camera.provider-V2-ndk"', shared)
        required = block[block.index('required:'):].split('\n')[0]
        # dlopen and directory-scan loads hang off the provider executable, so
        # required edges cannot form a cycle with the DT_NEEDED graph.
        for stem in ['hw_camera.qcom', 'hw_camera.qcom.milos', 'hw_com.qti.chi.override',
                     'com.qti.feature2.gs.milos', 'camera_components_com.qti.stats.aec',
                     'camera_com.qti.sensor.fp6_imx896', 'hw_sensors.vl53l1']:
            self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', required)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_vendor.qti.hardware.camera.offlinecamera-service-impl"'):]
        self.assertIn('vendor.qti.camera.offlinecamera-impl.xml', block[:block.index('}\n')])
        for stem in ['android.hardware.camera.device-V2-ndk', 'libhwbinder', 'vendor.qti.hardware.camera.postproc@1.0',
                     'vendor.qti.hardware.display.allocator@4.0', 'vendor.qti.hardware.camera.offlinecamera-V1-ndk']:
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_vendor.qti.hardware.camera.offlinecamera-service-impl"'):]
        shared = block[block.index('shared_libs:'):].split('\n')[0]
        self.assertIn('"vendor.qti.hardware.camera.offlinecamera-V1-ndk"', shared)
        # 'tct' alone also matches libdsi_netctrl and librmnetctl (radio closure).
        for marker in ['com.tct.', 'libtct', 'bin_tctd', 'libanc_', 'Qnn', 'com.fp.node', 'cameraalgoservice', 'qseeaon', 'libdepth',
                       'afbfusion', 'bu63169gwz', 'libqll10', 'mfnr_t4']:
            self.assertFalse([m for m in modules if marker in m], marker)
        for name in ['postproc-impl.xml', 'algoservice-default', 'vendor/etc/camera/AncRawAlgos']:
            self.assertNotIn(name, bp + make)

    def test_camera_data_firmware_and_dsp_libraries_installed(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for path in ['vendor/lib64/camera/com.qti.tuned.fp6_tsp_imx896.bin', 'vendor/lib64/camera/com.qti.sensormodule.tsp_s5kkd1sp_fp6.bin',
                     'vendor/lib64/bm4d73v02s12n02.bin', 'vendor/firmware/CAMERA_ICP.mbn', 'vendor/etc/media_profiles_V1_0.xml']:
            self.assertIn('vendor/fairphone/FP6/files/' + path + ':$(TARGET_COPY_OUT_VENDOR)/' + path.removeprefix('vendor/'), make)
        for name in ['_mtf.bin', 'milos_qtech', 'camano', 'qti_tpg', 'CAMERA_ICP.elf', 'bm4d68']:
            self.assertNotIn(name, make)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_rfs_dsp_libfastcvdsp_skel"'):]
        block = block[:block.index('}\n')]
        self.assertIn('filename: "libfastcvdsp_skel.so"', block)
        self.assertIn('relative_install_path: "adsp"', block)
        self.assertTrue(bp[:bp.index('name: "fp6_stock_vendor_lib64_rfs_dsp_libfastcvdsp_skel"')].rstrip().endswith('prebuilt_rfsa {'))

    def test_unreviewed_lib64_data_or_dsp_input_rejected(self):
        for path in ['vendor/lib64/unreviewed.bin', 'vendor/lib64/camera/sub/x.bin', 'vendor/lib/rfsa/adsp/bad-name.so']:
            recipe = copy.deepcopy(self.recipe)
            row = copy.deepcopy(next(r for r in recipe['files'] if r['path'] == 'vendor/lib64/camera/bitmlconfig.bin'))
            row['path'] = row['input'] = path
            recipe['files'].append(row)
            with self.assertRaises(VendorError):
                vendor_product.render(recipe, self.selection, 'synthetic_notice_kind')
    def test_radio_daemon_installed_with_modules_and_declared_services(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp = rendered['Android.bp'].decode()
        qcrild = bp[bp.index('name: "fp6_stock_vendor_bin_hw_qcrilNrd"'):]
        qcrild = qcrild[:qcrild.index('}\n')]
        self.assertIn('files/vendor/etc/init/qcrilNrd.rc', qcrild)
        for fragment in ['android.hardware.radio.data.xml', 'android.hardware.radio.voice.xml',
                         'vendor.qti.hardware.radio.ims.xml', 'vendor.qti.hardware.radio.am.xml',
                         'vendor.qti.hardware.radio.lpa.xml']:
            self.assertIn('files/vendor/etc/vintf/manifest/' + fragment, qcrild)
        for fragment in ['android.hardware.radio.sap.xml', 'qtiradio-saidl.xml', 'qcrilhook-saidl.xml',
                         'vendor.qti.hardware.radio.uim.xml', 'android.hardware.secure_element.xml']:
            self.assertNotIn(fragment, bp)
        # The seccomp helper is the source-built AOSP library.
        self.assertIn('"libavservices_minijail"', qcrild)
        loader = bp[bp.index('name: "fp6_stock_vendor_lib64_qcrild_libqcrilnr"'):]
        loader = loader[:loader.index('}\n')]
        for stem in ['libqcrilNrVoiceModule', 'libqcrilNrImsModule', 'libqcrilDataModule', 'libqcrilNrSmsModule']:
            self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', loader[loader.index('required:'):])
        self.assertIn('fp6_stock_vendor_bin_nicmd', modules)
        # Interfaces are source-built; excluded daemons and plugins are absent.
        for stem in ['lib64_android.hardware.radio.data-V2-ndk', 'lib64_android.hardware.radio@1.6',
                     'lib64_libavservices_minijail', 'lib64_libqcrilNrSocketModule',
                     'lib64_deviceInfoServiceModuleNr', 'lib64_libqcrildataqos', 'bin_cnd', 'bin_qms',
                     'bin_imsdaemon', 'bin_ims_rtp_daemon', 'bin_ipacm', 'bin_port-bridge']:
            self.assertNotIn('fp6_stock_vendor_' + stem, modules)
        self.assertIn('"android.hardware.radio.data-V2-ndk"', bp)

    def test_stock_ims_app_is_presigned_with_libraries_and_jni(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        ims = bp[bp.index('name: "ims"'):]
        ims = ims[:ims.index('}\n')]
        for line in ['system_ext_specific: true', 'presigned: true', 'privileged: true',
                     'apk: "files/system_ext/priv-app/ims/ims.apk"']:
            self.assertIn(line, ims)
        self.assertNotIn('certificate', ims)
        self.assertIn('installed_location: "priv-app/ims/lib/arm64/libimsmedia_jni.so"', bp)
        audio = bp[bp.index('name: "QtiTelephonyService"'):]
        audio = audio[:audio.index('}\n')]
        self.assertIn('privileged: true', audio)
        self.assertIn('license_text: ["NOTICE-system_ext.xml"]', bp)
        for path in ['system_ext/framework/qti-telephony-utils.jar:$(TARGET_COPY_OUT_SYSTEM_EXT)/framework/qti-telephony-utils.jar',
                     'product/etc/permissions/ims_ext_common.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/permissions/ims_ext_common.xml']:
            self.assertIn('vendor/fairphone/FP6/files/' + path, make)

    def test_stock_lpa_is_a_presigned_product_privileged_app_with_its_libraries(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        lpa = bp[bp.index('name: "uimlpaservice"'):]
        lpa = lpa[:lpa.index('}\n')]
        for line in ['product_specific: true', 'presigned: true', 'privileged: true',
                     'apk: "files/product/app/uimlpaservice/uimlpaservice.apk"',
                     'licenses: ["fp6_selected_stock_notices_product"]']:
            self.assertIn(line, lpa)
        self.assertNotIn('certificate', lpa)
        for path in ['product/framework/uimlpalibrary.jar:$(TARGET_COPY_OUT_PRODUCT)/framework/uimlpalibrary.jar',
                     'product/etc/permissions/lpa.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/permissions/lpa.xml',
                     'system_ext/framework/extphonelib.jar:$(TARGET_COPY_OUT_SYSTEM_EXT)/framework/extphonelib.jar']:
            self.assertIn('vendor/fairphone/FP6/files/' + path, make)

    def test_unreviewed_stock_jar_or_permission_file_rejected(self):
        for path in ['product/framework/other.jar', 'system_ext/etc/permissions/privapp-permissions-other.xml']:
            with self.subTest(path=path):
                recipe = copy.deepcopy(self.recipe)
                row = copy.deepcopy(next(r for r in recipe['files'] if r['path'].startswith('product/')))
                row['path'] = row['input'] = path
                recipe['files'].append(row)
                with self.assertRaises(VendorError):
                    vendor_product.render(recipe, self.selection, 'synthetic_notice_kind')
        # A permission file is only installed with the jar it declares.
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != 'product/framework/uimlpalibrary.jar']
        with self.assertRaises(VendorError): self.render()

    def test_stock_permission_file_may_only_declare_its_library(self):
        path = 'product/etc/permissions/lpa.xml'
        good = (b'<?xml version="1.0" encoding="utf-8"?>\n<!-- licence -->\n<permissions>\n'
                b' <library name="com.qualcomm.qti.lpa.uimlpalibrary"\n'
                b'          file="/product/framework/uimlpalibrary.jar"/>\n</permissions>\n')
        self.assertEqual('com.qualcomm.qti.lpa.uimlpalibrary', vendor_product.stock_library_declaration(path, good))
        for bad in [good.replace(b'/product/framework/uimlpalibrary.jar', b'/product/framework/other.jar'),
                    good.replace(b'</permissions>', b'<privapp-permissions package="x"/></permissions>'),
                    good.replace(b'<permissions>', b'<config>').replace(b'</permissions>', b'</config>'),
                    b'<permissions><feature name="android.hardware.telephony.euicc"/></permissions>', b'<permissions']:
            with self.subTest(bad=bad), self.assertRaises(VendorError):
                vendor_product.stock_library_declaration(path, bad)

    def test_telephony_rewrites_are_pinned_to_the_recipe(self):
        rows = {r['path']: r for r in self.recipe['files']}
        for path, rule in vendor_product.TELEPHONY_CONFIG_REWRITES.items():
            self.assertEqual(rule['source_sha256'], rows[path]['sha256'])
            with self.assertRaises(VendorError): vendor_product.telephony_config(path, b'unreviewed')

    def test_recipe_satisfies_the_component_graph(self):
        from diamaneos_tools import components, vendor_files
        model_data = (ROOT / 'config/components.json').read_bytes()
        source_data = (ROOT / 'config/fp6-sources.json').read_bytes()
        closure = vendor_files.selection(
            self.recipe, model=components.loads(model_data), sources=components.loads(source_data),
            environment=components.load_json(ROOT / 'config/build-environment.json'),
            model_sha256=hashlib.sha256(model_data).hexdigest(),
            source_sha256=hashlib.sha256(source_data).hexdigest(), public=False)
        radio = next(r for r in closure['component_results'] if r['component_id'] == 'radio-ims-data')
        self.assertIn('system_ext/priv-app/ims/ims.apk', radio['artifact_paths'])

    def test_unreviewed_system_ext_input_rejected(self):
        row = copy.deepcopy(next(r for r in self.recipe['files'] if r['path'].startswith('system_ext/')))
        row['path'] = row['input'] = 'system_ext/priv-app/Other/Other.apk'
        self.recipe['files'].append(row)
        with self.assertRaises(VendorError): self.render()

    def test_system_ext_link_needs_reviewed_jni_provider(self):
        link = next(l for l in self.recipe['symlinks'] if l['path'].startswith('system_ext/'))
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != link['target'][1:]]
        with self.assertRaises(VendorError): self.render()

    def test_configuration_transform_rejects_unreviewed_bytes(self):
        with self.assertRaises(VendorError): vendor_product.performance_config(b'<PerfConfigsStore/>')

    def test_camera_provider_rc_rewrite_is_pinned_to_the_recipe(self):
        path = 'vendor/etc/init/vendor.qti.camera.provider-service_64.rc'
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(vendor_product.CAMERA_CONFIG_REWRITES[path]['source_sha256'], row['sha256'])
        with self.assertRaises(VendorError):
            vendor_product.camera_config(path, b'service vendor.camera-provider /vendor/bin/hw/x\n')


if __name__ == '__main__': unittest.main()
