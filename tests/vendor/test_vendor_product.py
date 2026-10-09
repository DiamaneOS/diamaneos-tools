"""Reject inconsistent native installation and dependency declarations."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

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

    def test_perf2_daemon_and_client_are_not_selected(self):
        # The device builds the libperfmgr power HAL and its own
        # libqti-perfd-client; the closed perf2 daemon, its client, plugins and
        # configuration stay out of the selection and the rendered tree.
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        paths = {r['path'] for r in self.recipe['files']}
        self.assertNotIn('vendor.qti.hardware.perf2-hal-service', vendor_product.ACTIVATION)
        for stem in ['libqti-perfd', 'libqti-perfd-client', 'libperfconfig', 'libperfgluelayer',
                     'libperfioctl', 'libq-perflog', 'libqti-util', 'vendor.qti.hardware.perf2-V1-ndk',
                     'vendor.qti.memory.pasrmanager-V1-ndk', 'liblearningmodule', 'libmemperfd', 'libmeters']:
            self.assertNotIn('vendor/lib64/' + stem + '.so', paths)
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertFalse([p for p in paths if 'perf2' in p or p.startswith(('vendor/etc/perf/', 'vendor/etc/lm/'))])
        self.assertFalse([r for r in self.selection['roots'] if 'perf' in r])
        for marker in ['perf2', 'libqti-perfd', 'vendor/etc/perf/', 'vendor/etc/lm/']:
            self.assertNotIn(marker, bp + make)
        # The camera keeps the thermal client it links.
        self.assertIn('vendor/lib64/libthermalclient.so', paths)

    def test_missing_or_changed_firmware_rejected(self):
        path = self.selection['firmware_inputs'][0]['path']
        self.assertIn(path.encode(), self.render()['device-vendor.mk'])
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != path]
        with self.assertRaises(VendorError): self.render()

    def test_carrier_apk_is_data_only_and_iwlan_has_its_jni(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        self.assertNotIn('CustomerCarrierConfig', modules)
        self.assertNotIn(b'CustomerCarrierConfig.apk:', rendered['device-vendor.mk'])
        self.assertIn(b'name: "fp6_stock_carrier_assets"', rendered['Android.bp'])
        for name in ('IWlanService', 'CACertService', 'fp6_stock_vendor_lib64_libWlanServiceJni',
                     'fp6_stock_vendor_lib64_libjnihelper', 'fp6_stock_vendor_lib64_libcacertclient'):
            self.assertIn(name, modules)
        self.assertNotIn('CneApp', modules)
        bp = rendered['Android.bp'].decode()
        for name in ('IWlanService', 'CACertService'):
            block = bp[bp.index('name: "' + name + '"'):].split('\n}\n', 1)[0]
            for property in ('vendor: true', 'presigned: true', 'privileged: true'):
                self.assertIn(property, block)

    def test_touch_controller_firmware_copied_to_vendor_firmware(self):
        # eswin_ts.ko requests EPH86XX_fw.bin at probe; without it ueventd's
        # firmware fallback waits out the kernel's 60 s timeout.
        path = 'vendor/firmware/EPH86XX_fw.bin'
        make = self.render()['device-vendor.mk'].decode()
        self.assertIn('vendor/fairphone/FP6/files/' + path + ':$(TARGET_COPY_OUT_VENDOR)/firmware/EPH86XX_fw.bin', make)
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(('firmware-trusted-boot', 'firmware_families:vendor-peripheral-firmware'),
                         (row['component_id'], row['inventory_ref']))
        self.assertEqual((0, 0, 0o644, 'u:object_r:vendor_firmware_file:s0'),
                         tuple(row['metadata'][k] for k in ('uid', 'gid', 'mode', 'selinux')))
        firmware = next(f for f in self.selection['firmware_inputs'] if f['path'] == path)
        self.assertEqual((row['sha256'], row['bytes']), (firmware['sha256'], firmware['bytes']))
        firmware['sha256'] = '0' * 64
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

    def test_unused_qseecom_proxy_service_not_selected(self):
        # No installed file looks up the userspace QSEECom AIDL proxy; qseecomd
        # and the TEE clients use libQSEEComAPI directly.
        rendered = self.render()
        paths = {r['path'] for r in self.recipe['files']}
        self.assertFalse([p for p in paths if 'vendor.qti.hardware.qseecom' in p])
        self.assertFalse([r for r in self.selection['roots'] if 'vendor.qti.hardware.qseecom' in r])
        self.assertNotIn('vendor.qti.hardware.qseecom@1.0-service', vendor_product.ACTIVATION)
        self.assertNotIn(b'vendor.qti.hardware.qseecom', rendered['Android.bp'])
        modules = json.loads(rendered['modules.json'])
        for name in ['fp6_stock_vendor_bin_qseecomd', 'fp6_stock_vendor_lib64_libQSEEComAPI']:
            self.assertIn(name, modules)

    def test_unused_display_colour_service_not_selected(self):
        # Nothing is a client of the lazy colour service; the composer dlopens
        # libsdm-disp-vndapis and loads the colour libraries itself.
        rendered = self.render()
        paths = {r['path'] for r in self.recipe['files']}
        for path in ['vendor/bin/hw/vendor.qti.hardware.display.color-service',
                     'vendor/etc/init/vendor.qti.hardware.display.color-service.rc',
                     'vendor/lib64/vendor.qti.hardware.display.color-V1-ndk.so',
                     'vendor/lib64/vendor.qti.hardware.display.postproc-V1-ndk.so']:
            self.assertNotIn(path, paths)
        self.assertFalse([r for r in self.selection['roots'] if 'display.color' in r])
        self.assertNotIn('vendor.qti.hardware.display.color-service', vendor_product.ACTIVATION)
        self.assertNotIn(b'display.color', rendered['Android.bp'] + rendered['device-vendor.mk'])
        self.assertIn('vendor/lib64/libsdm-disp-vndapis.so', self.selection['roots'])
        modules = json.loads(rendered['modules.json'])
        for stem in ['libsdm-disp-vndapis', 'libsdm-color', 'libsnapdragoncolor-manager']:
            self.assertIn('fp6_stock_vendor_lib64_' + stem, modules)

    def test_keymint_links_the_stock_keymaster_messages(self):
        # A C++ implementation library, not a frozen interface: the stock
        # KeyMint HAL keeps the copy it was built with.
        self.assertNotIn('libkeymaster_messages', vendor_product.SOURCE_INTERFACES)
        rendered = self.render()
        self.assertIn('fp6_stock_vendor_lib64_libkeymaster_messages', json.loads(rendered['modules.json']))
        bp = rendered['Android.bp'].decode()
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libqtikeymint"'):]
        block = block[:block.index('}\n')]
        self.assertIn('"fp6_stock_vendor_lib64_libkeymaster_messages"', block)
        self.assertNotIn('"libkeymaster_messages"', block)
        # Installed on odm: an AOSP libkeymaster_messages variant owns the
        # /vendor/lib64 path, and the vendor namespace searches /odm/lib64 first.
        own = bp[bp.index('name: "fp6_stock_vendor_lib64_libkeymaster_messages"'):]
        own = own[:own.index('}\n')]
        self.assertIn('device_specific: true', own)
        self.assertNotIn('vendor: true', own)
        self.assertIn('stem: "libkeymaster_messages"', own)

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
        for stem in ['pm-service', 'pm-proxy', 'rmt_storage', 'ssr_setup']:
            self.assertIn('fp6_stock_vendor_bin_' + stem, modules)
        # The device modem/init.modem.rc starts ssr_setup; no stock rc is installed.
        block = bp[bp.index('name: "fp6_stock_vendor_bin_ssr_setup"'):]
        self.assertNotIn('init_rc:', block[:block.index('}\n')])
        # The full-RAM subsystem dump collector stays out (privacy).
        self.assertNotIn('fp6_stock_vendor_bin_subsystem_ramdump', modules)
        block = bp[bp.index('name: "fp6_stock_vendor_bin_rmt_storage"'):]
        self.assertIn('init_rc: ["files/vendor/etc/init/vendor.qti.rmt_storage.rc"]', block[:block.index('}\n')])
        # Userspace daemons and libraries have their own component, not the
        # firmware they talk to (whose replacement needs OEM signing).
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        for stem in ['libqrtr', 'libqmi_cci', 'libqmi_csi', 'libperipheral_client', 'libjson']:
            self.assertEqual('remote-processor-services', owners['vendor/lib64/' + stem + '.so'])
        for stem in ['pm-service', 'pm-proxy', 'rmt_storage', 'ssr_setup']:
            self.assertEqual('remote-processor-services', owners['vendor/bin/' + stem])
        self.assertFalse([p for p, owner in owners.items() if owner == 'firmware-trusted-boot'
                          and p.startswith(('vendor/bin/', 'vendor/lib64/'))])

    def test_linux_msm_daemons_replace_the_stock_ones(self):
        # The device builds tqftpserv and pd-mapper from linux-msm; the stock
        # tftp_server, pd-mapper, tftp_server's libqsocket and its rc are gone.
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp = rendered['Android.bp'].decode()
        paths = {r['path'] for r in self.recipe['files']}
        for path in ['vendor/bin/tftp_server', 'vendor/bin/pd-mapper', 'vendor/lib64/libqsocket.so',
                     'vendor/etc/init/vendor.qti.tftp.rc']:
            self.assertNotIn(path, paths)
            self.assertNotIn(path, {r['path'] for r in self.selection['files']})
            self.assertNotIn(path, self.selection['roots'])
        for name in ['fp6_stock_vendor_bin_tftp_server', 'fp6_stock_vendor_bin_pd-mapper',
                     'fp6_stock_vendor_lib64_libqsocket']:
            self.assertNotIn(name, modules)
        self.assertNotIn('tftp_server', vendor_product.ACTIVATION)
        self.assertNotIn('pd-mapper', vendor_product.ACTIVATION)
        self.assertNotIn(b'vendor.qti.tftp.rc', rendered['device-vendor.mk'])
        # Qualcomm's libqrtr stays for the stock libraries that link it; the
        # linux-msm daemons use the source library as libqrtr_linux_msm.so.
        self.assertIn('libqrtr', [Path(p).name.removesuffix('.so') for p in paths if p.startswith('vendor/lib64/')])
        self.assertNotIn('libqrtr', vendor_product.SOURCE_INTERFACES)
        self.assertIn('fp6_stock_vendor_lib64_libqrtr', modules)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libqrtr"'):]
        self.assertIn('stem: "libqrtr"', block[:block.index('}\n')])
        for consumer in ['libqrtrclient', 'libminksocket_vendor', 'libintervmipc', 'libvmfilexfer']:
            block = bp[bp.index('name: "fp6_stock_vendor_lib64_' + consumer + '"'):]
            block = block[:block.index('}\n')]
            self.assertIn('"fp6_stock_vendor_lib64_libqrtr"', block)
            self.assertNotIn('"libqrtr"', block)

    def test_stock_effects_config_and_effect_libraries_are_not_selected(self):
        # The device installs its own audio_effects.xml (AOSP software effects,
        # no DSP offload halves) and builds the VoIP pre-processing descriptors
        # and the volume listener from source. The stock config and all four
        # Qualcomm effect libraries leave the selection, including the offload
        # bundle and visualizer that received every app's effect commands.
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        recipe_paths = {r['path'] for r in self.recipe['files']}
        elf_paths = {f['path'] for f in self.selection['files']}
        for stem in ['libqcompostprocbundle', 'libqcomvisualizer', 'libqcomvoiceprocessing', 'libvolumelistener']:
            path = 'vendor/lib64/soundfx/' + stem + '.so'
            self.assertNotIn(path, recipe_paths)
            self.assertNotIn(path, elf_paths)
            self.assertNotIn(path, self.selection['roots'])
            self.assertFalse([e for e in self.selection['edges'] if path in (e['consumer'], e.get('provider'))])
            self.assertNotIn('fp6_stock_vendor_lib64_soundfx_' + stem, modules)
        config = 'vendor/etc/audio/sku_volcano/audio_effects.xml'
        self.assertNotIn(config, recipe_paths)
        self.assertNotIn(config, vendor_product.AUDIO_CONFIG_REWRITES)
        self.assertNotIn(b'audio_effects.xml', rendered['device-vendor.mk'])
        self.assertNotIn(b'soundfx', rendered['Android.bp'])
        # The source-built HAL opens the offload bundle and visualizer only if
        # the files exist.
        self.assertNotIn('vendor/lib64/hw/audio.primary.volcano.so', recipe_paths)

    def test_source_built_audio_stack_is_not_selected(self):
        # The device builds the primary HAL, PAL, AGM, its HIDL service and
        # ALSA plugins and audioadsprpcd from Fairphone's published sources
        # under the stock names. Their stock rows, the PAL HIDL service, the
        # memory logger, dynamic logging and the AOSP libraries only they
        # linked leave the selection.
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        recipe_paths = {r['path'] for r in self.recipe['files']}
        elf_paths = {f['path'] for f in self.selection['files']}
        gone = ['vendor/lib64/hw/audio.primary.volcano.so', 'vendor/bin/audioadsprpcd',
                'vendor/lib64/libar-pal.so', 'vendor/lib64/libagm.so', 'vendor/lib64/libagmclient.so',
                'vendor/lib64/libsndcardparser.so', 'vendor/lib64/libagm_pcm_plugin.so',
                'vendor/lib64/libagm_mixer_plugin.so', 'vendor/lib64/libagm_compress_plugin.so',
                'vendor/lib64/vendor.qti.hardware.AGMIPC@1.0.so', 'vendor/lib64/vendor.qti.hardware.AGMIPC@1.0-impl.so',
                'vendor/lib64/vendor.qti.hardware.pal@1.0.so', 'vendor/lib64/vendor.qti.hardware.pal@1.0-impl.so',
                'vendor/lib64/libarmemlog.so', 'vendor/lib64/libaudio_log_utils.so', 'vendor/lib64/libtinycompress.so',
                'vendor/lib64/android.hidl.allocator@1.0.so', 'vendor/lib64/libhidltransport.so']
        for path in gone:
            self.assertNotIn(path, recipe_paths)
            self.assertNotIn(path, elf_paths)
            self.assertNotIn(path, self.selection['roots'])
            self.assertFalse([e for e in self.selection['edges'] if path in (e['consumer'], e.get('provider'))])
            self.assertNotIn(vendor_product.module(path), modules)
            self.assertNotIn('"' + vendor_product.module(path) + '"', bp)
        self.assertNotIn('vendor/etc/init/vendor.qti.audio-adsprpc-service.rc', recipe_paths)
        self.assertNotIn('audioadsprpcd', vendor_product.ACTIVATION)
        self.assertNotIn('audio-adsprpc', make)
        # The closed libraries the source build links keep their library name,
        # so the source modules can link them; they are roots now.
        for stem in sorted(vendor_product.SOURCE_LINKED_STOCK):
            path = 'vendor/lib64/' + stem + '.so'
            self.assertEqual(stem, vendor_product.module(path))
            self.assertIn(stem, modules)
            block = bp[bp.index('name: "' + stem + '"'):]
            self.assertIn('stem: "' + stem + '"', block[:block.index('}\n')])
        for root in ['libar-gsl', 'libats', 'libvui_intf', 'libadm']:
            self.assertIn('vendor/lib64/' + root + '.so', self.selection['roots'])
        # Their own stock dependencies keep the prefixed names, and stock
        # consumers name the linkable ones by library name.
        self.assertIn('fp6_stock_vendor_lib64_libar-acdb', modules)
        block = bp[bp.index('name: "libats"'):]
        block = block[:block.index('}\n')]
        self.assertIn('"libar-gsl"', block)
        self.assertIn('"liblx-osal"', block)
        self.assertIn('"fp6_stock_vendor_lib64_libar-acdb"', block)
        # The deadline manager PAL loads with dlopen stays, as stock.
        self.assertIn('fp6_stock_vendor_lib64_libadm', modules)
        # Only the plain lib64 copy is renamed.
        self.assertEqual('fp6_stock_vendor_lib64_hw_libar-gsl', vendor_product.module('vendor/lib64/hw/libar-gsl.so'))

    def test_allocator_v1_is_installed_not_linked(self):
        out = self.render()
        bp = out['Android.bp'].decode()
        self.assertNotIn('"android.hardware.graphics.allocator-V1-ndk"', bp)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libcommonchiutils"'):]
        self.assertIn('check_elf_files: false', block[:block.index('}\n')])
        self.assertIn('"android.hardware.graphics.allocator-V2-ndk"',
                      bp[bp.index('name: "fp6_stock_vendor_lib64_libcamximageformatutils"'):].split('}\n')[0])
        self.assertIn('PRODUCT_PACKAGES += android.hardware.graphics.allocator-V1-ndk.vendor',
                      out['device-vendor.mk'].decode())

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

    @staticmethod
    def synthetic_elf(symbol_names, sym_defined=False):
        """A minimal ELF64 with .dynstr, .dynamic (two DT_NEEDED) and .dynsym sections."""
        import struct
        dynstr = b'\0libfoo.so\0libhardware.so\0sym\0'
        dynamic = struct.pack('<qQqQqQ', 1, 1, 1, 11, 0, 0)
        shndx = 1 if sym_defined else 0  # the "sym" name is at offset 26
        dynsym = b''.join(struct.pack('<IBBHQQ', name, 0, 0, shndx if name == 26 else 0, 0, 0)
                          for name in [0, *symbol_names])
        body = dynstr + dynamic + dynsym
        sections = [bytes(64)]
        for kind, offset, size, entsize in [(3, 64, len(dynstr), 0), (6, 64 + len(dynstr), len(dynamic), 16),
                                            (11, 64 + len(dynstr) + len(dynamic), len(dynsym), 24)]:
            sections.append(struct.pack('<IIQQQQIIQQ', 0, kind, 0, 0, offset, size, 0 if kind == 3 else 1, 0, 8,
                                        entsize))
        header = bytearray(b'\x7fELF\x02\x01\x01' + bytes(57))
        struct.pack_into('<Q', header, 0x28, 64 + len(body))
        struct.pack_into('<HHH', header, 0x3a, 64, len(sections), 0)
        return bytes(header) + body + b''.join(sections)

    def test_dependency_rewrite_renames_one_needed_entry(self):
        data = self.synthetic_elf([26])
        derived = vendor_product.rewrite_needed(data, 'libhardware.so', 'libcamxjail.so')
        self.assertEqual(data.replace(b'\0libhardware.so\0', b'\0libcamxjail.so\0'), derived)
        # A symbol name sharing the renamed bytes ("hardware.so") is refused.
        with self.assertRaises(VendorError):
            vendor_product.rewrite_needed(self.synthetic_elf([26, 14]), 'libhardware.so', 'libcamxjail.so')
        with self.assertRaises(VendorError):
            vendor_product.rewrite_needed(data, 'libmissing.so', 'libcamxjail.s')

    def test_camera_provider_links_the_seccomp_loader(self):
        path = 'vendor/bin/hw/vendor.qti.camera.provider-service_64'
        rewrite = vendor_product.NEEDED_REWRITES[path]
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(rewrite['source_sha256'], row['sha256'])
        self.assertEqual(('libhardware.so', 'libcamxjail.so', 'libcamxjail'),
                         (rewrite['needed'], rewrite['replacement'], rewrite['module']))
        bp = self.render()['Android.bp'].decode()
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_vendor.qti.camera.provider-service_64"'):]
        shared = block[:block.index('}\n')]
        shared = shared[shared.index('shared_libs:'):].split('\n')[0]
        self.assertIn('"libcamxjail"', shared)
        self.assertNotIn('"libhardware"', shared)
        # Other libhardware users are unchanged.
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_camx.provider-impl"'):]
        self.assertIn('"libhardware"', block[:block.index('}\n')])

    def test_dependency_rewrites_are_reviewed_same_length_renames(self):
        for path, rewrite in vendor_product.NEEDED_REWRITES.items():
            self.assertTrue(path.startswith(('vendor/lib64/', 'vendor/bin/')))
            self.assertEqual(len(rewrite['needed']), len(rewrite['replacement']))
            self.assertEqual(rewrite['replacement'], rewrite['module'] + '.so')
            self.assertRegex(rewrite['source_sha256'], '^[0-9a-f]{64}$')
            self.assertRegex(rewrite['sha256'], '^[0-9a-f]{64}$')
            self.assertNotEqual(rewrite['source_sha256'], rewrite['sha256'])
            self.assertIn(rewrite['module'], rewrite['reason'])
            if 'symbols' in rewrite:
                self.assertIn(rewrite['symbols'], vendor_product.SYMBOL_RENAMES)

    def test_symbol_rename_only_touches_one_undefined_symbol(self):
        # Rename an undefined symbol in place; the length must match and the byte
        # span must belong to no other symbol. "sym" is an undefined dynsym here.
        data = self.synthetic_elf([26])
        renamed = vendor_product.rename_dynamic_symbols(data, [('sym', 'syX')])
        self.assertEqual(data.replace(b'\0sym\0', b'\0syX\0'), renamed)
        # A different length is refused.
        with self.assertRaises(VendorError):
            vendor_product.rename_dynamic_symbols(data, [('sym', 'symbol')])
        # A name that is not a dynamic symbol is refused.
        with self.assertRaises(VendorError):
            vendor_product.rename_dynamic_symbols(data, [('missing', 'present')])
        # A defined symbol (shndx != 0) is refused: renaming it would break the
        # GNU hash table. "sym" is defined in this variant.
        defined = self.synthetic_elf([26], sym_defined=True)
        with self.assertRaises(VendorError):
            vendor_product.rename_dynamic_symbols(defined, [('sym', 'syX')])
        # The six GraphicBuffer renames keep the length (so the Itanium length
        # prefix "13" stays valid) and rename only the class token.
        for old, new in vendor_product.GRAPHICBUFFER_V34_SYMBOLS:
            self.assertEqual(len(old), len(new))
            self.assertIn('13GraphicBuffer', old)
            self.assertIn('13GraphicBufV34', new)
            self.assertNotIn('GraphicBuffer', new)

    def test_codec2_video_service_installed_with_device_activation(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_vendor.qti.media.c2@1.0-service"'):]
        block = block[:block.index('}\n')]
        # The device rc and manifest.xml activate it; the stock rc and the
        # fragment that also claims the platform's software store are not used.
        self.assertNotIn('init_rc', block)
        self.assertNotIn('vintf_fragments', block)
        self.assertNotIn('vendor.qti.media.c2@1.0-service.rc', make)
        self.assertNotIn('c2_manifest_vendor', bp + make)
        shared = block[block.index('shared_libs:'):].split('\n')[0]
        for name in ['"android.hardware.media.c2@1.2"', '"libavservices_minijail"', '"libhidltransport"',
                     '"fp6_stock_vendor_lib64_libcodec2_hidl@1.2"', '"fp6_stock_vendor_lib64_libcodec2_vndk"',
                     '"libc2hwjail_avservices"']:
            self.assertIn(name, shared)
        required = block[block.index('required:'):].split('\n')[0]
        for stem in ['libqcodec2_core', 'libqcodec2_v4l2codec', 'libqcodec2_imgtxrfilter']:
            self.assertIn('"fp6_stock_vendor_lib64_' + stem + '"', required)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libqcodec2_utils"'):]
        shared = block[:block.index('}\n')]
        self.assertIn('"vendor.qti.hardware.display.config-V5-ndk"', shared)
        self.assertIn('"libgralloc.qti"', shared)
        # Frozen interfaces are source-built; audio codecs, OMX and test plugins stay out.
        for stem in ['android.hardware.media.c2@1.0', 'android.hardware.media.c2@1.2',
                     'android.hardware.media.bufferpool2-V1-ndk', 'libOmxCore', 'libstagefrighthw',
                     'libqcodec2_mockfilter', 'libqc2audio_core', 'libvideooptfeature', 'libvideoml']:
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertNotIn('fp6_stock_vendor_bin_hw_vendor.qti.media.c2audio@1.0-service', modules)
        # The Android 14 Codec2 framework libraries share their names with AOSP
        # vendor-available libraries, whose install rules Soong always defines;
        # they install on odm, which the vendor namespace searches first.
        for stem in ['libcodec2_hidl@1.0', 'libcodec2_hidl@1.1', 'libcodec2_hidl@1.2', 'libcodec2_hidl_plugin',
                     'libcodec2_vndk', 'libstagefright_aidl_bufferpool2', 'libstagefright_bufferpool@2.0.1']:
            self.assertIn(stem, vendor_product.ODM_LIBRARIES)
            block = bp[bp.index('name: "fp6_stock_vendor_lib64_' + stem + '"'):]
            block = block[:block.index('}\n')]
            self.assertIn('device_specific: true', block)
            self.assertNotIn('vendor: true', block)
        for stem in ['libqcodec2_core', 'libqcodec2_utils', 'libvideotxr']:
            self.assertNotIn(stem, vendor_product.ODM_LIBRARIES)
        # Android 14 ABI compat (device compat/codec2-v34): libcodec2_vndk binds
        # the compat GraphicBuffer/mapper (uiv34, which links libui). libui stays
        # listed (keep_link) because Soong's ELF check resolves the blob's other
        # libui imports only against the listed libraries.
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_libcodec2_vndk"'):]
        shared = block[:block.index('}\n')]
        shared = shared[shared.index('shared_libs:'):].split('\n')[0]
        self.assertIn('"uiv34"', shared)
        self.assertIn('"libui"', shared)
        # Each stock Codec2 HIDL library binds the getHGraphicBufferProducer
        # compat and keeps the real helper listed for its other imports.
        for version in ['1.0', '1.1', '1.2']:
            block = bp[bp.index('name: "fp6_stock_vendor_lib64_libcodec2_hidl@' + version + '"'):]
            shared = block[:block.index('}\n')]
            shared = shared[shared.index('shared_libs:'):].split('\n')[0]
            self.assertIn('"libstagefright_bqhelper_v34compat"', shared)
            self.assertIn('"libstagefright_bufferqueue_helper"', shared)
            self.assertIn('"libui"', shared)
        # Rewrites without keep_link still drop the original (camera provider).
        block = bp[bp.index('name: "fp6_stock_vendor_bin_hw_vendor.qti.camera.provider-service_64"'):]
        shared = block[:block.index('}\n')]
        self.assertNotIn('"libhardware"', shared[shared.index('shared_libs:'):].split('\n')[0])
        # Both stock seccomp policies are installed; the device configs and the
        # firmware are copied; the generic and other-variant configs are not.
        for path in ['media_codecs_volcano_v1.xml', 'media_codecs_performance_volcano_v1.xml',
                     'media_volcano_v1/video_system_specs.json', 'media_codecs_google_c2_video.xml',
                     'seccomp_policy/codec2.vendor.base-arm64.policy',
                     'seccomp_policy/codec2.vendor.ext-arm64.policy']:
            self.assertIn('vendor/fairphone/FP6/files/vendor/etc/' + path + ':$(TARGET_COPY_OUT_VENDOR)/etc/' + path, make)
        for name in ['media_codecs_volcano_qv0.xml', 'init.qti.media', 'c2audio']:
            self.assertNotIn(name, make)
        # The hardware-decoding variant (device media/media.mk) is installed
        # next to the encoder-only one.
        for path in ['media_codecs_volcano_v1_hwdec.xml', 'media_volcano_v1_hwdec/video_system_specs.json']:
            self.assertIn('vendor/fairphone/FP6/files/vendor/etc/' + path + ':$(TARGET_COPY_OUT_VENDOR)/etc/' + path, make)
        self.assertIn('vendor/firmware/vpu20_2v.mbn:$(TARGET_COPY_OUT_VENDOR)/firmware/vpu20_2v.mbn', make)
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        self.assertEqual('graphics-display-media', owners['vendor/bin/hw/vendor.qti.media.c2@1.0-service'])
        # Only the phone's SoC variant (_volcano_v1) is selected.
        for other in ['v0', 'v2', 'v3']:
            self.assertNotIn('volcano_' + other, make)

    def test_media_derivations_bind_the_selected_encoder_only_configs(self):
        # Hardware encoders only: both configs that decide which Qualcomm codecs
        # exist are pinned derivations of the selected stock rows.
        rows = {r['path']: r for r in self.recipe['files']}
        self.assertEqual(set(vendor_product.MEDIA_CONFIG_REWRITES),
                         {'vendor/etc/media_codecs_volcano_v1.xml',
                          'vendor/etc/media_volcano_v1/video_system_specs.json'})
        for path, rule in vendor_product.MEDIA_CONFIG_REWRITES.items():
            self.assertEqual(rule['source_sha256'], rows[path]['sha256'])
            self.assertNotEqual(rule['source_sha256'], rule['sha256'])
            with self.assertRaises(VendorError): vendor_product.media_config(path, b'unreviewed')
        self.assertTrue(all(name.startswith('c2.qti.') and name.split('.')[3] == 'encoder'
                            for name in vendor_product.MEDIA_ENCODERS))
        self.assertFalse([n for n in vendor_product.MEDIA_ENCODERS if 'secure' in n or 'decoder' in n])

    def test_codec_service_links_the_configuration_check_and_seccomp_loader(self):
        path = 'vendor/bin/hw/vendor.qti.media.c2@1.0-service'
        rewrite = vendor_product.NEEDED_REWRITES[path]
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(rewrite['source_sha256'], row['sha256'])
        self.assertEqual(('libavservices_minijail.so', 'libc2hwjail_avservices.so', 'libc2hwjail_avservices'),
                         (rewrite['needed'], rewrite['replacement'], rewrite['module']))
        # The loader links libavservices_minijail, where the service's own
        # SetUpMinijail import still resolves.
        self.assertTrue(rewrite['keep_link'])
        self.assertNotIn('symbols', rewrite)

    def test_media_hardware_decoding_variant_binds_the_selected_stock_configs(self):
        # Hardware video decoding (off by default): a second pinned pair from the
        # same stock rows, with the three non-secure decoders and nothing else.
        rows = {r['path']: r for r in self.recipe['files']}
        self.assertEqual(set(vendor_product.MEDIA_HWDEC_COPIES),
                         {'vendor/etc/media_codecs_volcano_v1_hwdec.xml',
                          'vendor/etc/media_volcano_v1_hwdec/video_system_specs.json'})
        for path, rule in vendor_product.MEDIA_HWDEC_COPIES.items():
            self.assertNotIn(path, rows)
            self.assertIn(rule['from'], vendor_product.MEDIA_CONFIG_REWRITES)
            self.assertEqual(rule['source_sha256'], rows[rule['from']]['sha256'])
            self.assertEqual(rule['source_sha256'], vendor_product.MEDIA_CONFIG_REWRITES[rule['from']]['source_sha256'])
            self.assertNotEqual(rule['sha256'], vendor_product.MEDIA_CONFIG_REWRITES[rule['from']]['sha256'])
            with self.assertRaises(VendorError): vendor_product.media_hwdec_config(path, b'unreviewed')
        self.assertEqual(('c2.qti.avc.decoder', 'c2.qti.hevc.decoder', 'c2.qti.vp9.decoder'),
                         vendor_product.MEDIA_HW_DECODERS)
        self.assertFalse([n for n in vendor_product.MEDIA_HW_DECODERS if 'secure' in n or 'low_latency' in n])

    def test_media_codec_list_with_hardware_decoders_keeps_only_the_allowed_ones(self):
        def codec(name, kind, extra=''):
            return ('        <MediaCodec name="%s" type="%s" >\n'
                    '            <Limit name="size" min="96x96" max="4096x4096" />\n%s'
                    '        </MediaCodec>\n' % (name, kind, extra))
        decoders = ''.join(codec(n, 'video/avc') + codec(n + '.low_latency', 'video/avc',
                                                          '            <Feature name="low-latency" />\n')
                           + codec(n + '.secure', 'video/avc',
                                   '            <Feature name="secure-playback" required="true" />\n')
                           for n in vendor_product.MEDIA_HW_DECODERS)
        encoders = ''.join(codec(n, 'video/avc') for n in vendor_product.MEDIA_ENCODERS)
        stock = ('<?xml version="1.0" encoding="utf-8" ?>\n<MediaCodecs>\n'
                 '    <Decoders>\n        <!-- C2 decoders -->\n' + decoders + '    </Decoders>\n'
                 '    <Encoders>\n' + encoders + '    </Encoders>\n'
                 '    <Include href="media_codecs_google_c2.xml" />\n</MediaCodecs>\n').encode()
        derived = vendor_product.media_decoder_codec_list(stock)
        self.assertNotIn(b'secure', derived)
        self.assertNotIn(b'low_latency', derived)
        for name in vendor_product.MEDIA_HW_DECODERS + vendor_product.MEDIA_ENCODERS:
            self.assertIn(b'"' + name.encode() + b'"', derived)
        self.assertEqual(stock.split(b'    <Decoders>')[0], derived.split(b'    <Decoders>')[0])
        self.assertEqual(stock.split(b'    </Decoders>')[1], derived.split(b'    </Decoders>')[1])
        # A missing allowed decoder, an unknown one left in Encoders or a second
        # section is refused.
        with self.assertRaises(VendorError):
            vendor_product.media_decoder_codec_list(stock.replace(b'c2.qti.vp9.decoder"', b'c2.qti.vp8.decoder"'))
        with self.assertRaises(VendorError):
            vendor_product.media_decoder_codec_list(stock.replace(
                b'    <Encoders>\n', b'    <Encoders>\n' + codec('c2.qti.av1.decoder.secure', 'video/av01').encode()))
        with self.assertRaises(VendorError):
            vendor_product.media_decoder_codec_list(stock.replace(b'    <Decoders>', b'    <Decoderz>'))

    def test_media_target_spec_with_hardware_decoders(self):
        stock = (b'// Qualcomm\n{\n    "Video": {\n'
                 b'        //\n        // Put below optional codecs under "OptionalCodecs" to enable it\n'
                 b'        //\n        "OptionalCodecs": [\n        ]\n    }\n}\n')
        derived = vendor_product.media_encoders_only_target_spec(stock, vendor_product.MEDIA_HW_DECODERS)
        available, optional = vendor_product.media_target_spec_codecs(derived)
        self.assertEqual({'decoders': list(vendor_product.MEDIA_HW_DECODERS),
                          'encoders': list(vendor_product.MEDIA_ENCODERS)}, available)
        self.assertEqual([], optional)

    def test_media_codec_list_keeps_only_hardware_encoders(self):
        encoders = ''.join('        <MediaCodec name="%s" type="video/avc">\n'
                           '            <Limit name="size" min="128x128" max="4096x4096" />\n'
                           '        </MediaCodec>\n' % name for name in vendor_product.MEDIA_ENCODERS)
        stock = ('<?xml version="1.0" encoding="utf-8" ?>\n<MediaCodecs>\n'
                 '    <Include href="media_codecs_google_audio.xml" />\n'
                 '    <Decoders>\n        <!-- C2 decoders -->\n'
                 '        <MediaCodec name="c2.qti.avc.decoder" type="video/avc">\n'
                 '            <Alias name="OMX.qcom.video.decoder.avc"/>\n'
                 '        </MediaCodec>\n'
                 '    </Decoders>\n    <Encoders>\n' + encoders + '    </Encoders>\n'
                 '    <Include href="media_codecs_google_c2.xml" />\n</MediaCodecs>\n').encode()
        derived = vendor_product.media_encoders_only_codec_list(stock)
        self.assertNotIn(b'decoder', derived.replace(b'hardware decoders', b''))
        self.assertIn(b'<Include href="media_codecs_google_c2.xml" />', derived)
        self.assertEqual(stock.split(b'    <Decoders>')[0], derived.split(b'    <!-- DiamaneOS')[0])
        # A decoder outside the removed section, or a second section, is refused.
        with self.assertRaises(VendorError):
            vendor_product.media_encoders_only_codec_list(stock.replace(
                b'    <Encoders>\n', b'    <Encoders>\n        <MediaCodec name="c2.qti.vp9.decoder" />\n'))
        with self.assertRaises(VendorError):
            vendor_product.media_encoders_only_codec_list(stock.replace(b'    <Decoders>', b'    <Decoderz>'))

    def test_media_target_spec_lists_only_hardware_encoders(self):
        stock = (b'// Qualcomm\n{\n    "Video": {\n        "QC2CodecPlugins": [\n'
                 b'            "libqcodec2_imgtxrfilter.so"\n        ],\n\n'
                 b'        //\n        // Put below optional codecs under "OptionalCodecs" to enable it\n'
                 b'        // "c2.qti.dv.decoder",\n        //\n'
                 b'        "OptionalCodecs": [\n        ]\n    }\n}\n')
        derived = vendor_product.media_encoders_only_target_spec(stock)
        available, optional = vendor_product.media_target_spec_codecs(derived)
        self.assertEqual({'decoders': [], 'encoders': list(vendor_product.MEDIA_ENCODERS)}, available)
        self.assertEqual([], optional)
        self.assertTrue(derived.startswith(stock.split(b'\n        //\n        // Put')[0]))
        # An optional codec would join the same set (an empty set enables every
        # codec, a non-empty one adds to it), so it is refused, as is an input
        # that already has a codec list.
        with self.assertRaises(VendorError):
            vendor_product.media_encoders_only_target_spec(stock.replace(
                b'"OptionalCodecs": [\n        ]', b'"OptionalCodecs": [\n            "c2.qti.dv.decoder"\n        ]'))
        with self.assertRaises(VendorError):
            vendor_product.media_encoders_only_target_spec(derived)

    def test_sensor_stack_installed_with_activation_and_configuration(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for name in ['fp6_stock_vendor_bin_sscrpcd', 'fp6_stock_vendor_lib64_sensors.qsh',
                     'fp6_stock_vendor_lib64_libssc_default_listener',
                     'fp6_stock_vendor_lib64_libprotobuf-cpp-lite-21.7']:
            self.assertIn(name, modules)
        # The AOSP multi-HAL and sensors interfaces are source-built; the AOSP
        # dynamic sub-HAL is neither stock nor loaded (SENSORS_CONFIG_REWRITES).
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

    def test_bluetooth_hci_implementation_installed_without_stock_service(self):
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for name in ['fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.0-impl-qti',
                     'fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.1-impl-qti',
                     'fp6_stock_vendor_lib64_libbtnv', 'fp6_stock_vendor_lib64_libsoc_helper']:
            self.assertIn(name, modules)
        # The device's own service registers the stock implementation, so the
        # implementation is a root; the stock service and the FM, ANT, SAR,
        # config-store and TPI libraries it links are not selected.
        self.assertIn('vendor/lib64/hw/android.hardware.bluetooth@1.1-impl-qti.so', self.selection['roots'])
        self.assertNotIn('android.hardware.bluetooth@1.1-service-qti', vendor_product.ACTIVATION)
        paths = {r['path'] for r in self.recipe['files']}
        for marker in ['bluetooth@1.1-service', 'vendor.qti.hardware.fm@', 'com.dsi.ant@', 'bluetooth_sar',
                       'btconfigstore', 'bttpi']:
            self.assertFalse([p for p in paths if marker in p], marker)
            self.assertNotIn(marker, bp + make)
        # AOSP HCI interfaces are source-built; the stock Bluetooth audio stack
        # is not installed.
        for name in ['fp6_stock_vendor_lib64_android.hardware.bluetooth@1.0',
                     'fp6_stock_vendor_lib64_android.hardware.bluetooth@1.1',
                     'fp6_stock_vendor_lib64_hw_audio.bluetooth_qti.default']:
            self.assertNotIn(name, modules)
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.1-impl-qti"'):]
        block = block[:block.index('}\n')]
        self.assertIn('relative_install_path: "hw"', block)
        self.assertIn('"android.hardware.bluetooth@1.1"', block)
        self.assertIn('"fp6_stock_vendor_lib64_hw_android.hardware.bluetooth@1.0-impl-qti"', block)
        self.assertNotIn('init_rc', block)
        # The derived audio policy no longer includes the stock hearing-aid file.
        self.assertNotIn('bluetooth_qti_hearing_aid_audio_policy_configuration.xml', make)
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        self.assertEqual('connectivity-peripherals',
                         owners['vendor/lib64/hw/android.hardware.bluetooth@1.1-impl-qti.so'])
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

    def test_gnss_hal_is_built_from_source(self):
        # The device builds the GNSS HAL and its location libraries from
        # CodeLinaro source; no stock GNSS code, interface library or activation
        # file is selected. The stock engine configuration stays.
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        paths = {r['path'] for r in self.recipe['files']}
        elf_paths = {r['path'] for r in self.selection['files']}
        stock = ['bin/hw/android.hardware.gnss-aidl-service-qti', 'lib64/hw/android.hardware.gnss-aidl-impl-qti.so',
                 'lib64/liblocation_api.so', 'lib64/libgnss.so', 'lib64/libloc_core.so', 'lib64/libgps.utils.so',
                 'lib64/libloc_api_v02.so', 'lib64/libqti_vndfwk_detect_vendor.so',
                 'lib64/android.hardware.gnss-V3-ndk.so', 'lib64/android.hardware.health-V1-ndk.so',
                 'lib64/android.hardware.health@1.0.so', 'lib64/android.hardware.health@2.0.so',
                 'lib64/android.hardware.health@2.1.so',
                 'etc/init/android.hardware.gnss-aidl-service-qti.rc',
                 'etc/vintf/manifest/android.hardware.gnss-aidl-service-qti.xml']
        for path in ['vendor/' + p for p in stock]:
            self.assertNotIn(path, paths)
            self.assertNotIn(path, elf_paths)
            self.assertNotIn(path, self.selection['roots'])
            self.assertNotIn(vendor_product.module(path), modules)
        self.assertFalse([e for e in self.selection['edges']
                          if 'gnss' in e['consumer'] or 'gnss' in e.get('provider', '')
                          or e['needed'] in ('libgnss.so', 'libloc_api_v02.so', 'libgps.utils.so')])
        self.assertNotIn('android.hardware.gnss-aidl-service-qti', vendor_product.ACTIVATION)
        for stem in ['android.hardware.gnss-V3-ndk', 'android.hardware.health-V1-ndk',
                     'android.hardware.health@1.0', 'android.hardware.health@2.0', 'android.hardware.health@2.1']:
            self.assertNotIn(stem, vendor_product.SOURCE_INTERFACES)
        for marker in ['gnss', 'libloc_', 'libgps.utils', 'liblocation_api', 'vndfwk_detect_vendor']:
            self.assertNotIn(marker, bp)
            self.assertNotIn(marker, '\n'.join(modules))
        # The Qualcomm cloud, Wi-Fi, daemon, batching and geofence layers stay out too.
        for stem in ['bin_loc_launcher', 'bin_xtra-daemon', 'bin_lowi-server', 'bin_xtwifi-client',
                     'lib64_liblbs_core', 'lib64_libizat_core', 'lib64_vendor.qti.gnss-service',
                     'lib64_libbatching', 'lib64_libgeofencing', 'lib64_liblocation_qesdk', 'lib64_liblocdiagiface']:
            self.assertNotIn('fp6_stock_vendor_' + stem, modules)
        for name in ['vendor.qti.gnss-service.xml', 'loc-launcher.rc', 'xtwifi.conf', 'lowi.conf',
                     'gnss_antenna_info.conf', 'batching.conf', 'android.hardware.gnss-aidl-service-qti']:
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
        for line in [b'\nLOG_BUFFER_ENABLED = 0\n', b'\nQXDM_LOG = 0\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n',
                     b'\nDEBUG_LEVEL = 2\n']:
            self.assertIn(line, required)
        self.assertIn(b'\nPROCESS_STATE=ENABLED', vendor_product.GNSS_FORBIDDEN['vendor/etc/izat.conf'])
        # The service's init file comes from the source HAL, not from stock.
        self.assertEqual({'vendor/etc/gps.conf', 'vendor/etc/izat.conf'}, set(vendor_product.GNSS_CONFIG_REWRITES))
        self.assertEqual(set(vendor_product.GNSS_REQUIRED), set(vendor_product.GNSS_CONFIG_REWRITES))
        self.assertEqual(set(vendor_product.GNSS_FORBIDDEN), set(vendor_product.GNSS_CONFIG_REWRITES))

    def test_recipe_matches_the_stock_image_recipe(self):
        stock = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_bytes())
        self.assertEqual((stock['stock_build'], stock['region'], stock['archive_sha256']),
                         (self.recipe['stock_build'], self.recipe['region'], self.recipe['archive_sha256']))

    def test_vendor_has_no_vndk_version_and_blobs_use_current_variants(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        self.assertNotIn('.vndk.', bp)
        self.assertNotIn('ro.vndk.version', make)
        self.assertNotIn('PRODUCT_EXTRA_VNDK_VERSIONS', make)
        service = bp[bp.index('name: "fp6_stock_vendor_bin_pm-service"'):]
        self.assertIn('"libbinder"', service[:service.index('}\n')])

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
        self.assertNotIn('vintf_fragments', block[:block.index('}\n')])
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

    def test_camera_runs_without_the_opencl_runtime(self):
        # The Adreno OpenCL runtime and compiler were selected only for the
        # camera, and the software CAC library links the runtime; CamX's GPU
        # node and the OpenCV users fall back without them.
        removed = ['libOpenCL', 'libOpenCL_adreno', 'libllvm-qcom', 'libadreno_compiler_cl', 'libmmcamera_cac']
        paths = {r['path'] for r in self.recipe['files']}
        provider = next(r for r in self.recipe['files']
                        if r['path'] == 'vendor/bin/hw/vendor.qti.camera.provider-service_64')
        runtime = {d['path'] for d in provider['runtime_dependencies']}
        rendered = self.render()
        modules = json.loads(rendered['modules.json'])
        for stem in removed:
            self.assertNotIn('vendor/lib64/' + stem + '.so', paths)
            self.assertNotIn('vendor/lib64/' + stem + '.so', runtime)
            self.assertNotIn('fp6_stock_vendor_lib64_' + stem, modules)
        self.assertFalse([e for e in self.selection['edges']
                          if e['needed'] in {s + '.so' for s in removed}])
        # The GLES driver still loads the compute backend; the GPU node stays.
        for stem in ['libCB', 'camera_components_com.qti.node.gpu', 'camera_components_com.qti.node.swcac']:
            self.assertIn('fp6_stock_vendor_lib64_' + stem, modules)

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
        # The stock call-audio client is replaced by the device's own bridge.
        self.assertNotIn('QtiTelephonyService', bp)
        self.assertNotIn('QtiTelephonyService', make)
        self.assertIn('license_text: ["NOTICE-system_ext.xml"]', bp)
        for path in ['system_ext/framework/qti-telephony-utils.jar:$(TARGET_COPY_OUT_SYSTEM_EXT)/framework/qti-telephony-utils.jar',
                     'product/etc/permissions/ims_ext_common.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/permissions/ims_ext_common.xml']:
            self.assertIn('vendor/fairphone/FP6/files/' + path, make)

    def test_deferred_lpa_is_absent_but_native_radio_dependency_remains(self):
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        for value in ('uimlpaservice', 'uimlpalibrary', 'qti-telephony-hidl-wrapper-prd', 'libjni_aidl_service'):
            self.assertNotIn(value, bp + make)
        self.assertIn('vendor.qti.hardware.radio.lpa-V1-ndk', bp)

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
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != 'product/framework/ims-ext-common.jar']
        with self.assertRaises(VendorError): self.render()

    def test_stock_permission_file_may_only_declare_its_library(self):
        path = 'product/etc/permissions/ims_ext_common.xml'
        good = (b'<?xml version="1.0" encoding="utf-8"?>\n<!-- licence -->\n<permissions>\n'
                b' <library name="org.codeaurora.ims"\n'
                b'          file="/product/framework/ims-ext-common.jar"/>\n</permissions>\n')
        self.assertEqual('org.codeaurora.ims', vendor_product.stock_library_declaration(path, good))
        for bad in [good.replace(b'/product/framework/ims-ext-common.jar', b'/product/framework/other.jar'),
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

    def test_iris_video_firmware_copied_to_vendor_firmware(self):
        # msm_video.ko requests vpu20_2v.mbn for every volcano SKU; without it
        # the video core fails "sys init" at probe.
        make = self.render()['device-vendor.mk'].decode()
        self.assertIn('vendor/firmware/vpu20_2v.mbn:$(TARGET_COPY_OUT_VENDOR)/firmware/vpu20_2v.mbn', make)
        self.assertNotIn('vpu20_2v_unsigned', make)
        owners = {r['path']: r['component_id'] for r in self.recipe['files']}
        self.assertEqual('firmware-trusted-boot', owners['vendor/firmware/vpu20_2v.mbn'])

    def test_recipe_passes_the_selection_checks(self):
        from diamaneos_tools import vendor_files
        stock = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_bytes())
        vendor_files.selection(self.recipe, stock)
        paths = {r['path'] for r in self.recipe['files']}
        self.assertIn('system_ext/priv-app/ims/ims.apk', paths)
        # The stock call-audio client is not selected (the device builds its own
        # bridge); QCRIL keeps its IQcRilAudio interface library and VINTF
        # declaration, which the bridge talks to.
        self.assertFalse(any('QtiTelephonyService' in p for p in paths))
        self.assertIn('vendor/lib64/vendor.qti.hardware.radio.am-V1-ndk.so', paths)

    def test_unreviewed_system_ext_input_rejected(self):
        row = copy.deepcopy(next(r for r in self.recipe['files'] if r['path'].startswith('system_ext/')))
        row['path'] = row['input'] = 'system_ext/priv-app/Other/Other.apk'
        self.recipe['files'].append(row)
        with self.assertRaises(VendorError): self.render()

    def test_system_ext_link_needs_reviewed_jni_provider(self):
        link = next(l for l in self.recipe['symlinks'] if l['path'].startswith('system_ext/'))
        self.recipe['files'] = [r for r in self.recipe['files'] if r['path'] != link['target'][1:]]
        with self.assertRaises(VendorError): self.render()

    def test_vendor_patch_level_is_read_strictly_from_the_stock_build_prop(self):
        import datetime
        today = datetime.date(2026, 10, 5)
        prop = (b'# begin common build properties\n#ro.vendor.build.security_patch=2027-01-05\n'
                b'ro.vendor.build.date.utc=1788145138\nro.vendor.build.security_patch=2026-09-05\n'
                b'ro.vendor.build.security_patch_extra=2020-01-01\n')
        self.assertEqual('2026-09-05', vendor_product.vendor_patch_level(prop, '2026-09-28', today))
        self.assertEqual('2026-09-05', vendor_product.vendor_patch_level(prop, None, today))
        # The build date may precede the patch level; the release date bounds it.
        self.assertEqual('2026-09-05', vendor_product.vendor_patch_level(prop, '2026-09-05', today))
        for data in (b'ro.vendor.build.date.utc=1788145138\n', b'ro.vendor.build.security_patch=\n',
                     prop + b'ro.vendor.build.security_patch=2026-09-05\n',
                     b'ro.vendor.build.security_patch=2026-9-05\n', b'ro.vendor.build.security_patch=20260905\n',
                     b'ro.vendor.build.security_patch=2026-09-05 x\n', b'ro.vendor.build.security_patch=2026-02-30\n',
                     b'ro.vendor.build.security_patch=2026-13-05\n'):
            with self.subTest(data=data), self.assertRaises(VendorError):
                vendor_product.vendor_patch_level(data, '2026-09-28', today)
        with self.assertRaisesRegex(VendorError, 'release date'):
            vendor_product.vendor_patch_level(prop, '2026-09-04', today)
        with self.assertRaisesRegex(VendorError, 'future'):
            vendor_product.vendor_patch_level(prop, None, datetime.date(2026, 9, 4))
        for release in ('2026-9-28', '2026-02-30', 28):
            with self.subTest(release=release), self.assertRaises(VendorError):
                vendor_product.vendor_patch_level(prop, release, today)

    def test_vendor_patch_level_comes_from_the_authenticated_build_prop(self):
        import datetime, tempfile
        today = datetime.date(2026, 10, 5)
        data = b'ro.vendor.build.id=X\nro.vendor.build.security_patch=2026-09-05\n'
        with tempfile.TemporaryDirectory() as temporary:
            inputs, scratch = Path(temporary) / 'inputs', Path(temporary) / 'scratch'
            (inputs / 'vendor').mkdir(parents=True)
            scratch.mkdir()
            (inputs / 'vendor/build.prop').write_bytes(data)
            recipe = {'build_properties': [{'input': 'vendor/build.prop', 'bytes': len(data),
                                            'sha256': hashlib.sha256(data).hexdigest()}]}
            self.assertEqual('2026-09-05', vendor_product.stock_vendor_patch_level(recipe, inputs, scratch, None, today))
            (inputs / 'vendor/build.prop').write_bytes(data.replace(b'09-05', b'10-05'))
            with self.assertRaises(VendorError):
                vendor_product.stock_vendor_patch_level(recipe, inputs, scratch / 'again', None, today)
            with self.assertRaises(VendorError):
                vendor_product.stock_vendor_patch_level({}, inputs, scratch / 'none', None, today)

    def test_recipe_pins_the_stock_vendor_build_prop(self):
        rows = self.recipe['build_properties']
        self.assertEqual(['vendor/build.prop'], [r['input'] for r in rows])
        self.assertNotIn('vendor/build.prop', {r['input'] for r in self.recipe['files']})
        # The generation writes the patch level it read; rendering has none.
        self.assertNotIn('BoardConfigVendor.mk', self.render())
        self.assertEqual(b'# ro.vendor.build.security_patch of the stock vendor image (vendor/build.prop).\n'
                         b'VENDOR_SECURITY_PATCH := 2026-09-05\n', vendor_product.board_config('2026-09-05'))
        stock = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_bytes())
        inventory = json.loads((ROOT / 'config/stock-inputs.json').read_bytes())
        release = vendor_product.release_date_of(stock, inventory)
        archive = next(a for a in inventory['archives'] if a['sha256'] == stock['archive_sha256'])
        self.assertEqual(archive['release_date'], release)
        vendor_product.iso_date(release, 'release date')
        self.assertIsNone(vendor_product.release_date_of(dict(stock, archive_sha256='0' * 64), inventory))

    def test_firmware_release_table_is_installed_in_the_vendor_image(self):
        self.assertEqual(b'PRODUCT_COPY_FILES += vendor/fairphone/FP6/firmware-releases.txt:'
                         b'$(TARGET_COPY_OUT_VENDOR)/etc/diamaneos/firmware-releases.txt\n',
                         vendor_product.firmware_table_copy())
        # Rendering alone has no table; the generation needs one.
        self.assertNotIn(vendor_product.FIRMWARE_TABLE, self.render())
        stock = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_bytes())
        for table in (b'', None, 'image tz 16.111.0 1 ' + '0' * 64 + '\n'):
            with self.subTest(table=table), self.assertRaisesRegex(VendorError, 'firmware release table'):
                vendor_product.generate(self.recipe, self.selection, ROOT / 'missing', ROOT / 'missing',
                                        notice_kind='legacy_proprietary', stock=stock, firmware_releases=table)

    def test_camera_provider_rc_rewrite_is_pinned_to_the_recipe(self):
        path = 'vendor/etc/init/vendor.qti.camera.provider-service_64.rc'
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(vendor_product.CAMERA_CONFIG_REWRITES[path]['source_sha256'], row['sha256'])
        with self.assertRaises(VendorError):
            vendor_product.camera_config(path, b'service vendor.camera-provider /vendor/bin/hw/x\n')

    def test_camx_override_rewrite_is_pinned_to_the_recipe(self):
        path = 'vendor/etc/camera/camxoverridesettings.txt'
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        self.assertEqual(vendor_product.CAMERA_CONFIG_REWRITES[path]['source_sha256'], row['sha256'])
        with self.assertRaises(VendorError):
            vendor_product.camera_config(path, b'enableTOFInterface=TRUE\n')

    def test_camx_override_turns_core_dumps_off(self):
        path = 'vendor/etc/camera/camxoverridesettings.txt'
        source = b'enableTOFInterface=TRUE\nenableHealthMonitor=FALSE\n'
        expected = source + vendor_product.CAMX_NO_CORE_DUMPS
        rule = {'source_sha256': hashlib.sha256(source).hexdigest(), 'sha256': hashlib.sha256(expected).hexdigest()}
        with mock.patch.dict(vendor_product.CAMERA_CONFIG_REWRITES, {path: rule}):
            derived = vendor_product.camera_config(path, source)
        self.assertEqual(expected, derived)
        for key in (b'enableCameraCoreDumpText', b'enableCameraCoreDumpBinary',
                    b'enableCoredumpOfflineTextLogging', b'enableCoredumpOfflineBinaryLogging'):
            self.assertIn(b'\n' + key + b'=FALSE\n', derived)

    def test_sensors_config_loads_only_the_qualcomm_sub_hal(self):
        # The stock list is just two library names; the dynamic-sensor (HID)
        # sub-HAL is dropped and the AOSP module is not installed (device.mk).
        path = 'vendor/etc/sensors/hals.conf'
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        rule = vendor_product.SENSORS_CONFIG_REWRITES[path]
        self.assertEqual(rule['source_sha256'], row['sha256'])
        stock = b'sensors.qsh.so\nsensors.dynamic_sensor_hal.so\n'
        self.assertEqual(rule['source_sha256'], hashlib.sha256(stock).hexdigest())
        self.assertEqual(b'sensors.qsh.so\n', vendor_product.sensors_config(path, stock))
        with self.assertRaises(VendorError):
            vendor_product.sensors_config(path, b'sensors.qsh.so\n')
        other = b'sensors.qsh.so\nsensors.other.so\nsensors.dynamic_sensor_hal.so\n'
        patched = dict(rule, source_sha256=hashlib.sha256(other).hexdigest())
        with mock.patch.dict(vendor_product.SENSORS_CONFIG_REWRITES, {path: patched}):
            with self.assertRaises(VendorError):
                vendor_product.sensors_config(path, other)

    def test_wifi_config_drops_only_the_magic_packet_wake(self):
        # gEnableWoW goes in before the END marker the driver's parser stops at;
        # 2 keeps pattern wake-ups and drops the magic packet.
        path = 'vendor/etc/wifi/qca6750/WCNSS_qcom_cfg.ini'
        row = next(r for r in self.recipe['files'] if r['path'] == path)
        rule = vendor_product.WIFI_CONFIG_REWRITES[path]
        self.assertEqual(rule['source_sha256'], row['sha256'])
        self.assertNotEqual(rule['sha256'], rule['source_sha256'])
        with self.assertRaises(VendorError):
            vendor_product.wifi_config(path, b'gDot11Mode=0\nEND\n')

        def patched(stock):
            derived = stock.replace(b'\nEND\n', b'\ngEnableWoW=2\nEND\n')
            return dict(rule, source_sha256=hashlib.sha256(stock).hexdigest(),
                        sha256=hashlib.sha256(derived).hexdigest())
        stock = b'# overrides\ngDot11Mode=0\nEND\n\n# Note: nothing is read past END\n'
        with mock.patch.dict(vendor_product.WIFI_CONFIG_REWRITES, {path: patched(stock)}):
            self.assertEqual(b'# overrides\ngDot11Mode=0\ngEnableWoW=2\nEND\n\n# Note: nothing is read past END\n',
                             vendor_product.wifi_config(path, stock))
        for other in (b'gEnableWoW=3\nEND\n', b'gDot11Mode=0\n', b'END\nEND\n'):
            with mock.patch.dict(vendor_product.WIFI_CONFIG_REWRITES, {path: patched(other)}):
                with self.assertRaises(VendorError):
                    vendor_product.wifi_config(path, other)

    def test_offline_camera_service_is_linked_but_not_declared(self):
        # The CHI override and libchifeature2 link the offline camera library,
        # so it stays; its service has no client, so neither its VINTF
        # declaration nor the provider's interface line is installed.
        rendered = self.render()
        bp, make = rendered['Android.bp'].decode(), rendered['device-vendor.mk'].decode()
        self.assertNotIn('vendor.qti.hardware.camera.offlinecamera-service-impl', vendor_product.LIBRARY_VINTF)
        self.assertFalse([r for r in self.recipe['files'] if r['path'].startswith('vendor/etc/vintf/')
                          and 'offlinecamera' in r['path']])
        self.assertNotIn('offlinecamera-impl.xml', bp + make)
        self.assertIn('fp6_stock_vendor_lib64_vendor.qti.hardware.camera.offlinecamera-service-impl',
                      json.loads(rendered['modules.json']))
        block = bp[bp.index('name: "fp6_stock_vendor_lib64_hw_com.qti.chi.override"'):]
        self.assertIn('"fp6_stock_vendor_lib64_vendor.qti.hardware.camera.offlinecamera-service-impl"',
                      block[:block.index('}\n')])

    def test_camera_provider_rc_drops_the_offline_camera_interface_and_restarts_with_cameraserver(self):
        path = 'vendor/etc/init/vendor.qti.camera.provider-service_64.rc'
        source = (b'service vendor.camera-provider /vendor/bin/hw/vendor.qti.camera.provider-service_64\n'
                  b'    interface aidl android.hardware.camera.provider.ICameraProvider/vendor_qti/0\n'
                  b'    interface aidl vendor.qti.hardware.camera.offlinecamera.IOfflineCameraService/default\n'
                  b'    interface vendor.qti.hardware.camera.postproc@1.0::IPostProcService camerapostprocservice\n'
                  b'    class hal\n')
        expected = (b'service vendor.camera-provider /vendor/bin/hw/vendor.qti.camera.provider-service_64\n'
                    b'    interface aidl android.hardware.camera.provider.ICameraProvider/vendor_qti/0\n'
                    b'    class hal cameraWatchdog\n')
        rule = {'source_sha256': hashlib.sha256(source).hexdigest(), 'sha256': hashlib.sha256(expected).hexdigest()}
        with mock.patch.dict(vendor_product.CAMERA_CONFIG_REWRITES, {path: rule}):
            self.assertEqual(expected, vendor_product.camera_config(path, source))


if __name__ == '__main__': unittest.main()
