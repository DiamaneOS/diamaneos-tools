import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import image_verify as subject


def dtb(nodes):
    """Build a flattened device tree from {path: {prop: bytes}} (parents first)."""
    strings, offsets = bytearray(), {}

    def string(name):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b'\0')
        return offsets[name]

    structure = bytearray()

    def pad():
        while len(structure) % 4:
            structure.append(0)

    def emit(path, children):
        name = '' if path == '/' else path.rsplit('/', 1)[1]
        structure.extend(struct.pack('>I', 1) + name.encode() + b'\0'); pad()
        for prop, value in nodes[path].items():
            structure.extend(struct.pack('>III', 3, len(value), string(prop)) + value); pad()
        for child in children.get(path, []):
            emit(child, children)
        structure.extend(struct.pack('>I', 2))

    children = {}
    for path in nodes:
        if path != '/':
            parent = path.rsplit('/', 1)[0] or '/'
            children.setdefault(parent, []).append(path)
    emit('/', children)
    structure.extend(struct.pack('>I', 9))
    header_size = 40
    off_struct = header_size
    off_strings = off_struct + len(structure)
    total = off_strings + len(strings)
    header = struct.pack('>IIIIIIIIII', 0xD00DFEED, total, off_struct, off_strings, 0, 17, 16, 0, len(strings), len(structure))
    return header + bytes(structure) + bytes(strings)


def cells(*values):
    return struct.pack('>' + 'I' * len(values), *values)


class FakeTools:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.calls = []

    def scratch(self):
        return tempfile.TemporaryDirectory()

    def run(self, name, args, cwd=None, check=True):
        self.calls.append((name, [str(a) for a in args]))
        for key, value in self.outputs.items():
            if key[0] == name and (len(key) == 1 or key[1] in [str(a) for a in args]):
                return value(args) if callable(value) else value
        raise subject.ToolMissing(name)


class Harness:
    def __init__(self, root: Path, members: dict, symlinks=None, tools=None, kernel=None, variant='user'):
        path = root / 'tf.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
            for name, target in (symlinks or {}).items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, target)
        self.tf = subject.TargetFiles(path)
        self.tools = tools or FakeTools()
        self.images = root
        self.variant = variant
        self.config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        self.sources = subject.Sources(self.tf, kernel, root)


class RuleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def harness(self, members, **kw):
        h = Harness(self.root, members, **kw)
        self.addCleanup(h.tf.close)
        return h

    def test_file_presence_absence_and_symlinks(self):
        v = self.harness({'VENDOR/bin/a': b'x', 'SYSTEM_EXT/priv-app/Old/Old.apk': b'x'},
                         symlinks={'VENDOR/etc/link.xml': 'target.xml'})
        self.assertTrue(subject.rule_files_present({'paths': ['VENDOR/bin/a']}, v)[0])
        self.assertFalse(subject.rule_files_present({'paths': ['VENDOR/bin/b']}, v)[0])
        self.assertFalse(subject.rule_files_absent({'paths': ['SYSTEM_EXT/priv-app/Old']}, v)[0])
        self.assertTrue(subject.rule_files_absent({'paths': ['VENDOR/bin/b']}, v)[0])
        self.assertFalse(subject.rule_files_absent({'paths': ['VENDOR/etc/link.xml']}, v)[0])
        self.assertTrue(subject.rule_symlink({'path': 'VENDOR/etc/link.xml', 'target': 'target.xml'}, v)[0])
        self.assertFalse(subject.rule_symlink({'path': 'VENDOR/etc/link.xml', 'target': 'other.xml'}, v)[0])

    def test_text_rules(self):
        v = self.harness({'VENDOR/etc/a.rc': b'on boot\n    write /x 1\n', 'VENDOR/etc/init/b.rc': b'service x\n',
                          'VENDOR/etc/vintf/manifest/one.xml': b'<hal>android.hardware.sensors</hal>',
                          'VENDOR/etc/vintf/manifest/two.xml': b'<hal>other</hal>'})
        ok = lambda rule: subject.rule_text(rule, v)[0]
        self.assertTrue(ok({'files': ['VENDOR/etc/a.rc'], 'lines': ['    write /x 1'], 'contains': ['on boot']}))
        self.assertFalse(ok({'files': ['VENDOR/etc/a.rc'], 'lines': ['write /x 1']}))
        self.assertTrue(ok({'files': ['VENDOR/etc/a.rc'], 'regex': [r'^ +write /x \d$']}))
        self.assertFalse(ok({'files': ['VENDOR/etc/a.rc'], 'absent_regex': [r'^on boot$']}))
        self.assertTrue(ok({'files': ['VENDOR/etc/init/**'], 'absent': ['zygote-start']}))
        self.assertTrue(ok({'files': ['VENDOR/etc/vintf/manifest/*.xml'], 'match': 'any', 'contains': ['android.hardware.sensors']}))
        self.assertFalse(ok({'files': ['VENDOR/etc/vintf/manifest/*.xml'], 'contains': ['android.hardware.sensors']}))
        self.assertFalse(ok({'files': ['VENDOR/etc/missing.rc'], 'contains': ['x']}))
        self.assertTrue(ok({'files': ['VENDOR/etc/missing.rc'], 'contains': ['x'], 'optional': True}))

    def test_property_rules(self):
        v = self.harness({'VENDOR/build.prop': b'a=1\nb=true\nb=true\nc=2\nc=3\n# d=1\n'})
        ok = lambda rule: subject.rule_properties(dict(file='VENDOR/build.prop', **rule), v)[0]
        self.assertTrue(ok({'equals': ['a=1']}))
        self.assertFalse(ok({'equals': ['b=true']}))
        self.assertTrue(ok({'equals': ['b=true'], 'mode': 'all'}))
        self.assertFalse(ok({'equals': ['c=2'], 'mode': 'all'}))
        self.assertTrue(ok({'absent': ['d']}))
        self.assertFalse(ok({'absent': ['a']}))

    def test_fingerprint_prefix(self):
        v = self.harness({'PRODUCT/etc/build.prop': b'ro.product.build.fingerprint=Fairphone/FP6/FP6:17/X/test.1:user/test-keys\n',
                          'VENDOR/build.prop': b'ro.vendor.build.fingerprint=generic/x\n'})
        rule = {'files': ['PRODUCT/etc/build.prop'], 'key': 'build.fingerprint', 'prefix': 'Fairphone/FP6/FP6:'}
        self.assertTrue(subject.rule_property_prefix(rule, v)[0])
        rule['files'].append('VENDOR/build.prop')
        self.assertFalse(subject.rule_property_prefix(rule, v)[0])

    def test_sepolicy_exclusive_and_attribute_expansion(self):
        cil = (b'(typeattributeset dev_type (pmsg_device other_device))\n'
               b'(typeattributeset base_typeattr_9 (and (dev_type ) (not (other_device ))))\n'
               b'(allow vendor_init base_typeattr_9 (chr_file (getattr setattr)))\n'
               b'(allow hal_audio_default vendor_hal_audio_internal_hwservice (hwservice_manager (add find)))\n')
        v = self.harness({'SYSTEM/etc/selinux/plat_sepolicy.cil': cil})
        allows = {'files': ['SYSTEM/etc/selinux/plat_sepolicy.cil'], 'source': 'vendor_init', 'target': 'pmsg_device',
                  'cls': 'chr_file', 'perm': 'setattr'}
        self.assertTrue(subject.rule_sepolicy_allows(allows, v)[0])
        self.assertFalse(subject.rule_sepolicy_allows(dict(allows, target='other_device'), v)[0])
        self.assertFalse(subject.rule_sepolicy_allows(dict(allows, perm='write'), v)[0])
        exclusive = {'file': 'SYSTEM/etc/selinux/plat_sepolicy.cil', 'target': 'vendor_hal_audio_internal_hwservice',
                     'cls': 'hwservice_manager', 'allowed': ['hal_audio_default']}
        self.assertTrue(subject.rule_sepolicy_exclusive(exclusive, v)[0])
        v = self.harness({'SYSTEM/etc/selinux/plat_sepolicy.cil': cil + b'(allow untrusted_app vendor_hal_audio_internal_hwservice (hwservice_manager (find)))\n'})
        self.assertFalse(subject.rule_sepolicy_exclusive(exclusive, v)[0])

    def test_camera_power_client_rule(self):
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        rule = next(r for r in rules if r['id'] == 'camera-power-client')
        cil = (b'(typeattributeset hal_power (hal_power_default))\n'
               b'(typeattributeset hal_power_server (hal_power_default))\n'
               b'(typeattributeset hal_power_client (hal_camera_default))\n'
               b'(allow hal_camera_default hal_power (memfd_file (read write getattr map)))\n'
               b'(allow hal_power hal_camera_default (memfd_file (read write getattr map)))\n')
        ok = lambda text: subject.rule_text(rule, self.harness({'VENDOR/etc/selinux/vendor_sepolicy.cil': text}))[0]
        self.assertTrue(ok(cil))
        self.assertFalse(ok(cil.replace(b'hal_power_client (hal_camera_default)', b'hal_power_client (hal_camera_default hal_audio_default)')))
        self.assertFalse(ok(cil.replace(b'hal_power_server (hal_power_default)', b'hal_power_server (hal_power_default hal_camera_default)')))
        self.assertFalse(ok(cil + b'(allow hal_camera_default hal_power_default (binder (call transfer)))\n'))
        self.assertFalse(ok(cil + b'(allow hal_camera_default hal_power_stats_service_202604 (service_manager (find)))\n'))
        self.assertFalse(ok(cil + b'(allow hal_power_default hal_camera_default (process (setsched)))\n'))
        self.assertFalse(ok(cil.replace(b'(typeattributeset hal_power_client (hal_camera_default))\n', b'')))

    def test_component_override_must_be_the_only_one(self):
        override = (b'<config><component-override package="com.qualcomm.qti.lpa">'
                    b'<component class="a.Esim" enabled="false"/><component class="a.Lpa" enabled="false"/>'
                    b'</component-override></config>')
        members = {'PRODUCT/etc/sysconfig/lpa.xml': override,
                   'PRODUCT/etc/permissions/privapp.xml': b'<permissions package="com.qualcomm.qti.lpa"/>'}
        rule = {'file': 'PRODUCT/etc/sysconfig/lpa.xml', 'package': 'com.qualcomm.qti.lpa',
                'components': {'a.Esim': 'false', 'a.Lpa': 'false'}, 'scan': ['PRODUCT', 'VENDOR'],
                'allowed_files': ['PRODUCT/etc/sysconfig/lpa.xml', 'PRODUCT/etc/permissions/privapp.xml']}
        self.assertTrue(subject.rule_component_override(rule, self.harness(members))[0])
        members['VENDOR/etc/sysconfig/extra.xml'] = override.replace(b'false', b'true')
        self.assertFalse(subject.rule_component_override(rule, self.harness(members))[0])

    def test_zip_and_binary_rules(self):
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, 'w') as inner:
            inner.writestr('classes2.dex', b'dex\x00network_state_poll_window_ms\x00')
        v = self.harness({'SYSTEM/framework/telephony-common.jar': jar.getvalue()},
                         kernel=self.root)
        (self.root / 'Image').write_bytes(b'\x017    %.*s\nprint_kernel_cmdline_names Unknown kernel command line parameters')
        self.assertTrue(subject.rule_zip_contains({'file': 'SYSTEM/framework/telephony-common.jar',
                                                   'text': 'network_state_poll_window_ms'}, v)[0])
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        mac = next(r for r in rules if r['id'] == 'wifi-mac-print')
        self.assertTrue(subject.rule_binary_count(mac, v)[0])
        (self.root / 'Image').write_bytes(b'\x017    %s\nprint_kernel_cmdline_names Unknown kernel command line parameters')
        self.assertFalse(subject.rule_binary_count(mac, v)[0])

    def test_device_tree_rules(self):
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        ramoops = next(r for r in rules if r['id'] == 'ramoops')
        firmware = next(r for r in rules if r['id'] == 'touch-firmware-path')
        tree = {'/': {}, '/chosen': {'bootargs': b'console=null firmware_class.path=/vendor/firmware_mnt,/vendor/firmware\0'},
                '/reserved-memory': {},
                '/reserved-memory/ramoops_region': {'compatible': b'ramoops\0', 'size': cells(0, 0x400000),
                    'alignment': cells(0, 0x400000), 'console-size': cells(0x200000), 'pmsg-size': cells(0x200000),
                    'record-size': cells(0), 'ftrace-size': cells(0), 'mem-type': cells(0), 'no-map': b''},
                '/soc': {}}
        (self.root / 'dtbs').mkdir()
        (self.root / 'dtbs/fp6.dtb').write_bytes(dtb(tree))
        v = self.harness({}, kernel=self.root)
        self.assertEqual((True, ''), subject.rule_devicetree(ramoops, v))
        self.assertEqual((True, ''), subject.rule_devicetree(firmware, v))
        tree['/soc/ramoops_region'] = {}
        tree['/reserved-memory/ramoops_region']['size'] = cells(0, 0x200000)
        (self.root / 'dtbs/fp6.dtb').write_bytes(dtb(tree))
        ok, detail = subject.rule_devicetree(ramoops, v)
        self.assertFalse(ok)
        self.assertIn('size', detail)
        self.assertIn('/soc still has ramoops_region', detail)

    def test_tool_based_rules_use_the_build_tools(self):
        dump = ('Package name=x\n    resource 0x7f010000 bool/config_nightDisplayAvailable\n      () true\n'
                '    resource 0x7f020000 integer/config_tether_usb_functions\n      () 1\n')
        tools = FakeTools({('aapt2', 'resources'): dump,
                           ('aapt2', 'xmltree'): 'A: android:targetPackage="com.android.networkstack.tethering"',
                           ('aapt2', 'permissions'): "uses-permission: name='android.permission.MODIFY_AUDIO_SETTINGS'\n",
                           ('apksigner',): lambda args: 'Signer #1 certificate SHA-256 digest: '
                               + ('a' if 'bridge.apk' in str(args[-1]) else 'b') * 64 + '\n',
                           ('llvm-readelf',): '   12: 0000000000001234   120 FUNC    GLOBAL DEFAULT   14 AServiceManager_addService\n'
                                              '   13: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND AIBinder_new\n'})
        v = self.harness({'VENDOR/overlay/o.apk': b'apk', 'SYSTEM_EXT/priv-app/B/bridge.apk': b'apk',
                          'SYSTEM/framework/framework-res.apk': b'apk', 'VENDOR/bin/hal': b'elf'}, tools=tools)
        self.assertTrue(subject.rule_overlay({'apk': 'VENDOR/overlay/o.apk', 'values': {
            'bool/config_nightDisplayAvailable': 'true', 'integer/config_tether_usb_functions': '1'},
            'target': 'com.android.networkstack.tethering'}, v)[0])
        self.assertFalse(subject.rule_overlay({'apk': 'VENDOR/overlay/o.apk',
                                               'values': {'bool/config_nightDisplayAvailable': 'false'}}, v)[0])
        self.assertTrue(subject.rule_apk({'path': 'SYSTEM_EXT/priv-app/B/bridge.apk',
                                          'permissions': ['android.permission.MODIFY_AUDIO_SETTINGS'],
                                          'not_signed_like': 'SYSTEM/framework/framework-res.apk'}, v)[0])
        self.assertTrue(subject.rule_elf_exports({'path': 'VENDOR/bin/hal', 'symbols': ['AServiceManager_addService']}, v)[0])
        self.assertFalse(subject.rule_elf_exports({'path': 'VENDOR/bin/hal', 'symbols': ['AIBinder_new']}, v)[0])

    def test_codec2_graphicbuffer_abi_guard(self):
        rule = {'path': 'VENDOR/lib64/libcodec2_vndk.so', 'alloc_bytes': 256, 'min_sized_allocations': 2,
                'needed_present': ['uiv34.so'], 'needed_absent': ['libui.so'],
                'symbols_present': ['_ZN7android13GraphicBufV34C1Ev'],
                'symbols_absent': ['_ZN7android13GraphicBufferC1Ev'],
                'ctor_symbols': ['_ZN7android13GraphicBufV34C1Ev']}
        dyn = ('Dynamic section at offset 0x1 contains entries:\n'
               '  0x0000000000000001 (NEEDED)  Shared library: [uiv34.so]\n'
               '  0x0000000000000001 (NEEDED)  Shared library: [libutils.so]\n'
               'Symbol table (.dynsym):\n'
               '   10: 0000000000000000 0 FUNC GLOBAL DEFAULT UND _ZN7android13GraphicBufV34C1Ev\n'
               '   11: 0000000000001000 8 FUNC GLOBAL DEFAULT 15 _ZN7android2spINS_13GraphicBufferEEaSERKS2_\n')
        good = ('0000000000008000 <fn>:\n'
                '   8000:\tmov\tw0, #0x100\n'
                '   8004:\tbl\t0xc4070 <_Znwm@plt>\n'
                '   8008:\tbl\t0xc56d8 <_ZN7android13GraphicBufV34C1Ev@plt>\n'
                '   800c:\tmov\tw0, #0x100\n'
                '   8010:\tstr\txzr, [sp]\n'
                '   8014:\tbl\t0xc4070 <_Znwm@plt>\n'
                '   8018:\tbl\t0xc56d8 <_ZN7android13GraphicBufV34C1Ev@plt>\n')
        tools = FakeTools({('llvm-readelf',): dyn, ('llvm-objdump',): good})
        v = self.harness({'VENDOR/lib64/libcodec2_vndk.so': b'elf'}, tools=tools)
        self.assertTrue(subject.rule_codec2_abi_guard(rule, v)[0])
        # Not rewritten: still libui, still the Android 17 GraphicBuffer symbols.
        stock_dyn = dyn.replace('uiv34.so', 'libui.so').replace('GraphicBufV34', 'GraphicBuffer')
        stock = good.replace('GraphicBufV34', 'GraphicBuffer')
        v = self.harness({'VENDOR/lib64/libcodec2_vndk.so': b'elf'},
                         tools=FakeTools({('llvm-readelf',): stock_dyn, ('llvm-objdump',): stock}))
        ok, detail = subject.rule_codec2_abi_guard(rule, v)
        self.assertFalse(ok)
        self.assertIn('still depends on libui.so', detail)
        # A construction whose allocation is not 256 bytes is caught.
        wrong = good.replace('mov\tw0, #0x100', 'mov\tw0, #0xd30', 1)
        v = self.harness({'VENDOR/lib64/libcodec2_vndk.so': b'elf'},
                         tools=FakeTools({('llvm-readelf',): dyn, ('llvm-objdump',): wrong}))
        self.assertFalse(subject.rule_codec2_abi_guard(rule, v)[0])

    def test_missing_tool_is_a_failed_check_not_a_crash(self):
        v = subject.Verification.__new__(subject.Verification)
        v.results = []
        v.add('x', 'why', lambda: (_ for _ in ()).throw(subject.ToolMissing('aapt2')))
        self.assertEqual('FAIL', v.results[0]['status'])
        self.assertIn('tool missing: aapt2', v.results[0]['detail'])


class ParserTests(unittest.TestCase):
    def test_boot_header_os_field_and_kernel(self):
        header = bytearray(4096)
        header[:8] = b'ANDROID!'
        struct.pack_into('<IIII', header, 8, 5, 0, 0x220001a9, 4096)
        struct.pack_into('<I', header, 40, 4)
        image = bytes(header) + b'KERNEL' + bytes(10)
        parsed = subject.boot_header(image)
        self.assertEqual((4, 0x220001a9), (parsed['header_version'], parsed['os_version_field']))
        self.assertEqual(b'KERNE', subject.boot_kernel(image))

    def test_avb_footer_and_chain_parsing(self):
        payload = b'dtbo payload'
        footer = b'AVBf' + bytes(8) + struct.pack('>Q', len(payload)) + bytes(44)
        self.assertEqual(payload, subject.avb_payload(payload + bytes(20) + footer))
        info = ('    Chain Partition descriptor:\n      Partition Name:          boot\n'
                '      Rollback Index Location: 3\n      Public key (sha1):       x\n      Flags:                   0\n'
                '    Chain Partition descriptor:\n      Partition Name:          recovery\n'
                '      Rollback Index Location: 1\n      Flags:                   0\n')
        self.assertEqual({'boot': (3, 0), 'recovery': (1, 0)}, subject.avb_chains(info))

    def test_sparse_expansion(self):
        block = 4096
        body = b'A' * block
        chunks = [struct.pack('<HHII', 0xCAC1, 0, 1, 12 + block) + body,
                  struct.pack('<HHII', 0xCAC3, 0, 2, 12),
                  struct.pack('<HHII', 0xCAC2, 0, 1, 16) + b'BBBB']
        image = struct.pack('<IHHHHIIII', subject.SPARSE_MAGIC, 1, 0, 28, 12, block, 4, 3, 0) + b''.join(chunks)
        raw = subject.sparse_to_raw(image)
        self.assertEqual(4 * block, len(raw))
        self.assertEqual(body + bytes(2 * block) + b'B' * block, raw)

    def test_tool_scratch_and_tmpdir_stay_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / 'ws/work/tmp'
            tools = subject.Tools(Path(temp) / 'bin', Path(temp) / 'src', work)
            with tools.scratch() as scratch:
                self.assertTrue(Path(scratch).is_relative_to(work))
            self.assertEqual(str(work), tools.environment()['TMPDIR'])

    def test_board_lists(self):
        text = 'BOARD_VENDOR_KERNEL_MODULES := a/x.ko b/y.ko\nBOARD_SYSTEM_KERNEL_MODULES := z.ko\n'
        self.assertEqual({'BOARD_VENDOR_KERNEL_MODULES': ['x.ko', 'y.ko'], 'BOARD_SYSTEM_KERNEL_MODULES': ['z.ko']},
                         subject.board_lists(text))


class GenericCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def harness(self, members, **kw):
        h = Harness(self.root, members, **kw)
        self.addCleanup(h.tf.close)
        return h

    def test_permissive_domains_follow_the_variant(self):
        members = {'VENDOR/etc/selinux/vendor_sepolicy.cil': b'(typepermissive su)\n'}
        self.assertTrue(subject.check_permissive(self.harness(members, variant='userdebug'))[0])
        self.assertFalse(subject.check_permissive(self.harness(members, variant='user'))[0])

    def test_only_the_aosp_adb_keys_link_is_allowed(self):
        link = {'ROOT/adb_keys': '/product/etc/security/adb_keys'}
        self.assertTrue(subject.check_adb_keys(self.harness({'SYSTEM/build.prop': b''}, symlinks=link))[0])
        self.assertFalse(subject.check_adb_keys(self.harness({'PRODUCT/etc/security/adb_keys': b'key'}))[0])

    def test_test_keys_marking(self):
        prop = b'ro.build.tags=test-keys\nro.build.fingerprint=Fairphone/FP6/FP6:17/X/test.1:user/test-keys\n'
        members = {name: prop for name in ('SYSTEM/build.prop', 'SYSTEM_EXT/etc/build.prop',
                                          'PRODUCT/etc/build.prop', 'VENDOR/build.prop')}
        self.assertTrue(subject.check_test_keys(self.harness(members))[0])
        members['VENDOR/build.prop'] = prop.replace(b'test-keys', b'release-keys')
        self.assertFalse(subject.check_test_keys(self.harness(members))[0])

    def test_boot_header_check_names_the_upstream_finding(self):
        v = self.harness({}, tools=FakeTools({('avbtool',): lambda args: 'Prop: com.android.build.boot.os_version -> 17\n'
                                                                        'Prop: com.android.build.init_boot.os_version -> 17'}))
        for name in ('boot', 'init_boot', 'recovery'):
            header = bytearray(4096)
            header[:8] = b'ANDROID!'
            struct.pack_into('<I', header, 40, 4)
            (self.root / f'{name}.img').write_bytes(bytes(header))
        self.assertTrue(subject.check_boot_headers(v)[0])
        header = bytearray((self.root / 'boot.img').read_bytes())
        struct.pack_into('<I', header, 16, 0x220001a9)
        (self.root / 'boot.img').write_bytes(bytes(header))
        ok, detail = subject.check_boot_headers(v)
        self.assertFalse(ok)
        self.assertIn('release tools', detail)


    def test_kernel_binding(self):
        kernel = self.root / 'kernel'
        (kernel / 'dtbs').mkdir(parents=True)
        (kernel / 'Image').write_bytes(b'IMAGE')
        (kernel / 'dtbs/fp6.dtb').write_bytes(b'FP6DTB')
        (kernel / 'dtbo.img').write_bytes(b'DTBO')
        header = bytearray(4096)
        header[:8] = b'ANDROID!'
        struct.pack_into('<I', header, 8, 5)
        struct.pack_into('<I', header, 40, 4)
        (self.root / 'boot.img').write_bytes(bytes(header) + b'IMAGE')
        (self.root / 'vendor_boot.img').write_bytes(b'headerFP6DTBtail')
        footer = b'AVBf' + bytes(8) + struct.pack('>Q', 4) + bytes(44)
        (self.root / 'dtbo.img').write_bytes(b'DTBO' + bytes(12) + footer)
        v = self.harness({})
        v.kernel_dir = kernel
        self.assertEqual((True, ''), subject.check_kernel(v))
        (self.root / 'vendor_boot.img').write_bytes(b'other')
        self.assertFalse(subject.check_kernel(v)[0])

    def test_kernel_prebuilts_must_be_the_recorded_clean_commit(self):
        import subprocess
        src = self.root / 'src'
        prebuilts = src / 'device/fairphone/FP6-kernel'
        prebuilts.mkdir(parents=True)
        run = lambda *a: subprocess.run(['git', '-C', str(prebuilts), '-c', 'user.name=F', '-c',
                                         'user.email=f@example.invalid', '-c', 'commit.gpgsign=false', *a],
                                        check=True, capture_output=True, text=True).stdout.strip()
        run('init', '-q')
        (prebuilts / 'Image').write_bytes(b'IMAGE')
        run('add', 'Image')
        run('commit', '-qm', 'kernel')
        commit = run('rev-parse', 'HEAD')
        record = {'kernel_prebuilts': {'path': 'device/fairphone/FP6-kernel', 'commit': commit}}
        self.assertEqual((prebuilts, ''), subject.kernel_prebuilts(src, record))
        directory, problem = subject.kernel_prebuilts(src, {'kernel_prebuilts': {
            'path': 'device/fairphone/FP6-kernel', 'commit': '0' * 40}})
        self.assertIsNone(directory)
        self.assertIn('the build used 000000000000', problem)
        (prebuilts / 'Image').write_bytes(b'OTHER')
        self.assertEqual((None, 'device/fairphone/FP6-kernel has local changes'), subject.kernel_prebuilts(src, record))
        self.assertIsNone(subject.kernel_prebuilts(src, {'generated_inputs': {}})[0])
        v = self.harness({})
        v.kernel_dir, v.kernel_problem = None, 'device/fairphone/FP6-kernel has local changes'
        self.assertEqual((False, 'device/fairphone/FP6-kernel has local changes'), subject.check_kernel(v))

    def test_module_placement_follows_the_kernel_prebuilts(self):
        kernel = self.root / 'kernel'
        kernel.mkdir()
        (kernel / 'BoardConfigKernel.mk').write_text(
            'BOARD_VENDOR_KERNEL_MODULES := m/a.ko\nBOARD_SYSTEM_KERNEL_MODULES := m/s.ko\n'
            'BOARD_VENDOR_RAMDISK_KERNEL_MODULES := m/r.ko\nBOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD := m/r.ko\n'
            'BOARD_VENDOR_RAMDISK_RECOVERY_KERNEL_MODULES_LOAD := m/r.ko\nBOARD_VENDOR_KERNEL_MODULES_LOAD := a.ko\n')
        signed = b'module' + subject.MODULE_SIGNATURE
        members = {'VENDOR_DLKM/lib/modules/a.ko': signed, 'SYSTEM_DLKM/lib/modules/s.ko': signed,
                   'VENDOR_BOOT/RAMDISK/lib/modules/r.ko': signed,
                   'VENDOR_BOOT/RAMDISK/lib/modules/modules.load': b'r.ko\n',
                   'VENDOR_BOOT/RAMDISK/lib/modules/modules.load.recovery': b'r.ko\n',
                   'VENDOR_BOOT/RAMDISK/lib/modules/modules.blocklist': b'blocklist x\n',
                   'VENDOR_DLKM/lib/modules/modules.blocklist': b'blocklist x\n',
                   'VENDOR_DLKM/lib/modules/modules.load': b'a.ko\n'}
        packaging = {'denied_modules': [{'modules': ['can.ko'], 'reason': 'no CAN'}],
                     'partitions': {'vendor_dlkm': ['a.ko']}, 'load_lists': {}}
        v = self.harness(members)
        v.kernel_dir, v.packaging = kernel, packaging
        self.assertEqual((True, ''), subject.check_modules(v))
        members['VENDOR_DLKM/lib/modules/can.ko'] = signed
        members['SYSTEM_DLKM/lib/modules/s.ko'] = b'unsigned'
        v = self.harness(members)
        v.kernel_dir, v.packaging = kernel, packaging
        ok, detail = subject.check_modules(v)
        self.assertFalse(ok)
        self.assertIn('denied module', detail)
        self.assertIn('unsigned module', detail)

    def test_vendor_load_list_and_streams_follow_the_kernel_prebuilts(self):
        kernel = self.root / 'kernel'
        kernel.mkdir()
        (kernel / 'BoardConfigKernel.mk').write_text(
            'BOARD_VENDOR_KERNEL_MODULES := m/a.ko m/b-x.ko m/c.ko m/d.ko\n'
            'BOARD_VENDOR_KERNEL_MODULES_LOAD := a.ko b-x.ko c.ko d.ko\n')
        signed = b'module' + subject.MODULE_SIGNATURE
        base = {f'VENDOR_DLKM/lib/modules/{n}': signed for n in ('a.ko', 'b-x.ko', 'c.ko', 'd.ko')}
        base.update({'VENDOR_BOOT/RAMDISK/lib/modules/modules.load': b'',
                     'VENDOR_BOOT/RAMDISK/lib/modules/modules.load.recovery': b'',
                     'VENDOR_BOOT/RAMDISK/lib/modules/modules.blocklist': b'',
                     'VENDOR_DLKM/lib/modules/modules.blocklist': b'',
                     'VENDOR_DLKM/lib/modules/modules.load': b'a.ko\nb-x.ko\nc.ko\nd.ko\n'})
        packaging = {'partitions': {'vendor_dlkm': []}, 'load_lists': {}}

        rc = ''.join(f'service vendor.modprobe_{s} /vendor/bin/modprobe -q -b '
                     f'--all=/vendor/lib/modules/modules.load.{s}\n    oneshot\n\n' for s in ('first', 'second'))
        base['VENDOR/etc/init/hw/init.qcom.rc'] = rc.encode()

        def check(**changes):
            members = dict(base)
            for name, data in changes.items():
                path = 'VENDOR_DLKM/lib/modules/modules.load' + ('' if name == 'full' else '.' + name)
                if data is None:
                    members.pop(path)
                else:
                    members[path] = data
            v = self.harness(members)
            v.kernel_dir, v.packaging = kernel, packaging
            return subject.check_modules(v)

        self.assertEqual((True, ''), check())
        self.assertEqual((True, ''), check(first=b'a.ko\nc.ko\n', second=b'b_x.ko\nd.ko\n'))
        self.assertIn('no vendor init service loads stream third',
                      check(first=b'a.ko\nc.ko\n', second=b'b_x.ko\n', third=b'd.ko\n')[1])
        self.assertIn('order differs from the kernel prebuilts', check(full=b'b-x.ko\na.ko\nc.ko\nd.ko\n')[1])
        self.assertIn('lists 3 modules, the kernel prebuilts 4', check(full=b'a.ko\nb-x.ko\nc.ko\n')[1])
        self.assertIn('modules.load is missing', check(full=None)[1])
        self.assertIn('1 modules.load modules are in no stream: d.ko',
                      check(first=b'a.ko\nc.ko\n', second=b'b-x.ko\n')[1])
        self.assertIn('stream first is not in modules.load order', check(first=b'c.ko\na.ko\n', second=b'b-x.ko\nd.ko\n')[1])
        self.assertIn('c is in streams first and second', check(first=b'a.ko\nc.ko\n', second=b'b-x.ko\nc.ko\nd.ko\n')[1])
        self.assertIn('stream second lists modules outside modules.load: e.ko',
                      check(first=b'a.ko\nb-x.ko\nc.ko\n', second=b'd.ko\ne.ko\n')[1])

    def test_vendor_binding_compares_generated_bytes(self):
        from diamaneos_tools import carrier_data
        vendor = self.root / 'vendor'
        files = {'vendor/firmware/touch.bin': b'fw', 'vendor/lib64/libkeymaster_messages.so': b'km',
                 'vendor/lib64/libreplaced.so': b'stock', carrier_data.APK_PATH: b'apk'}
        for rel, data in files.items():
            (vendor / 'files' / rel).parent.mkdir(parents=True, exist_ok=True)
            (vendor / 'files' / rel).write_bytes(data)
        (vendor / 'provenance.json').write_text(json.dumps({'source_interface_replacements': ['vendor/lib64/libreplaced.so'],
                                                            'uninstalled_optional_libraries': []}))
        members = {'VENDOR/firmware/touch.bin': b'fw', 'ODM/lib64/libkeymaster_messages.so': b'km',
                   'VENDOR/lib64/libreplaced.so': b'source build'}
        v = self.harness(members)
        v.vendor_dir = vendor
        self.assertTrue(subject.check_vendor(v)[0])
        members['VENDOR/firmware/touch.bin'] = b'changed'
        v = self.harness(members)
        v.vendor_dir = vendor
        ok, detail = subject.check_vendor(v)
        self.assertFalse(ok)
        self.assertIn('differs VENDOR/firmware/touch.bin', detail)

    def test_vendor_patch_level_matches_the_stock_value(self):
        vendor = self.root / 'vendor'
        vendor.mkdir()
        (vendor / 'provenance.json').write_text(json.dumps({'vendor_security_patch': '2026-09-05'}))
        (self.root / 'vendor.img').write_bytes(b'not sparse')
        prop = b'ro.vendor.build.date.utc=1\nro.vendor.build.security_patch=2026-09-05\n'
        image = {'prop': prop}

        def debugfs(args):
            command = str(args[1])
            if command.startswith('dump '):
                self.assertEqual('/build.prop', command.split()[1])
                Path(command.split()[2]).write_bytes(image['prop'])
            return 'Type: regular\n'

        def check(members):
            v = self.harness(members, tools=FakeTools({('debugfs_static',): debugfs}))
            v.vendor_dir = vendor
            return subject.check_vendor_patch_level(v)
        self.assertEqual((True, 'ro.vendor.build.security_patch=2026-09-05'), check({'VENDOR/build.prop': prop}))
        ok, detail = check({'VENDOR/build.prop': prop.replace(b'09-05', b'08-05')})
        self.assertFalse(ok)
        self.assertIn('VENDOR/build.prop sets', detail)
        ok, _ = check({'VENDOR/build.prop': prop + b'ro.vendor.build.security_patch=2026-09-05\n'})
        self.assertFalse(ok)
        image['prop'] = b'ro.vendor.build.date.utc=1\n'
        ok, detail = check({'VENDOR/build.prop': prop})
        self.assertFalse(ok)
        self.assertIn('vendor.img sets []', detail)
        image['prop'] = prop
        (vendor / 'provenance.json').write_text(json.dumps({}))
        self.assertFalse(check({'VENDOR/build.prop': prop})[0])
        v = self.harness({'VENDOR/build.prop': prop})
        v.vendor_dir = None
        self.assertFalse(subject.check_vendor_patch_level(v)[0])

    def test_super_holds_exactly_the_logical_images(self):
        logical = json.loads((ROOT / 'config/fp6-build.json').read_text())['images']['logical']
        for name in logical:
            (self.root / f'{name}.img').write_bytes(name.encode() * 10)
        (self.root / 'super.img').write_bytes(b'raw super' + bytes(91))

        def lpunpack(args):
            out = Path(args[-1])
            for name in logical:
                (out / f'{name}_a.img').write_bytes(name.encode() * 10)
                (out / f'{name}_b.img').write_bytes(b'')
            return ''
        tools = FakeTools({('lpunpack',): lpunpack})
        v = self.harness({'META/dynamic_partitions_info.txt': b'super_partition_size=100\n'}, tools=tools)
        self.assertEqual((True, ''), subject.check_super(v))
        self.assertNotIn('simg2img', [c[0] for c in tools.calls])
        (self.root / 'vendor.img').write_bytes(b'changed')
        ok, detail = subject.check_super(v)
        self.assertFalse(ok)
        self.assertIn('vendor in super.img differs', detail)

    def test_wipe_images(self):
        v = self.harness({})
        wipe = v.config['wipe']['images']
        (self.root / 'userdata.img').write_bytes(bytes(wipe['userdata']['bytes']))
        (self.root / 'frp.img').write_bytes(bytes(wipe['frp']['bytes'] - 1) + b'\1')
        (self.root / 'misc.img').write_bytes(bytes(wipe['misc']['bytes']))
        raw = bytearray(8192)
        struct.pack_into('<I', raw, 1024, subject.F2FS_MAGIC)
        raw[1024 + 0x7c:1024 + 0x7c + 16] = 'metadata'.encode('utf-16le')
        (self.root / 'metadata.img').write_bytes(bytes(raw))
        self.assertEqual((True, ''), subject.check_wipe(v))
        (self.root / 'misc.img').write_bytes(b'\1' + bytes(wipe['misc']['bytes'] - 1))
        ok, detail = subject.check_wipe(v)
        self.assertFalse(ok)
        self.assertIn('misc image is not the declared zeros', detail)


class ConfigTests(unittest.TestCase):
    def test_committed_checks_are_valid_and_explained(self):
        document = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())
        rules = subject.validate_rules(document)
        self.assertGreater(len(rules), 80)
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        for rule in rules:
            for variant in rule.get('variants', []):
                self.assertIn(variant, config['variants'])
            for key in ('paths', 'files'):
                for path in rule.get(key, []):
                    self.assertRegex(path, r'^(SYSTEM|SYSTEM_EXT|PRODUCT|VENDOR|ODM|VENDOR_DLKM|SYSTEM_DLKM|'
                                           r'VENDOR_BOOT|INIT_BOOT|RECOVERY|META)/', rule['id'])

    def test_rule_documents_are_rejected_when_incomplete(self):
        document = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())
        for change in ({'type': 'shell'}, {'why': ' '}, {'id': 'Bad Id'}):
            broken = copy.deepcopy(document)
            broken['rules'][0].update(change)
            with self.subTest(change=change):
                self.assertRaises(ValueError, subject.validate_rules, broken)
        broken = copy.deepcopy(document)
        broken['rules'].append(copy.deepcopy(broken['rules'][0]))
        self.assertRaises(ValueError, subject.validate_rules, broken)

    def test_build_config_is_consistent(self):
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        self.assertIn(config['default_variant'], config['variants'])
        self.assertEqual('user', config['default_variant'])
        self.assertEqual([], config['variants']['user']['permissive_domains'])
        self.assertEqual({'boot', 'init_boot', 'recovery', 'vendor_boot', 'dtbo', 'pvmfw', 'vbmeta', 'vbmeta_system'},
                         set(config['images']['bootloader']))
        self.assertEqual(config['images']['bootloader'][-1], 'vbmeta')
        self.assertIn('make_f2fs', config['make_targets'])
        self.assertFalse(config['wipe']['validated'])
        data = bytearray(config['wipe']['images']['frp']['bytes'])
        data[-1] = 1
        self.assertEqual(config['wipe']['images']['frp']['sha256'], hashlib.sha256(bytes(data)).hexdigest())
        inventory = json.loads((ROOT / 'config/fp6-firmware-inventory.json').read_text())
        wipe = inventory['releases']['FP6.QREL.16.111.0']['images']['wipe']
        self.assertEqual(wipe['frp_for_factory.img']['sha256'], config['wipe']['images']['frp']['sha256'])
        self.assertEqual(wipe['userdata.img']['bytes'], config['wipe']['images']['userdata']['bytes'])


class VintfAndJdkTests(unittest.TestCase):
    STOCK = (b'<!-- Copyright -->\n<manifest version="1.0" type="device">\n'
             b'  <hal format="aidl">\n    <name>android.hardware.nfc</name>\n    <version>1</version>\n'
             b'    <interface>\n      <name>INfc</name>\n      <instance>default</instance>\n    </interface>\n'
             b'  </hal>\n</manifest>\n')
    ASSEMBLED = (b'<!--\n    Input:\n        files/nfc.xml\n-->\n<manifest version="9.0" type="device">\n'
                 b'    <hal format="aidl">\n        <name>android.hardware.nfc</name>\n'
                 b'        <fqname>INfc/default</fqname>\n    </hal>\n</manifest>\n')

    def test_assembled_fragment_declares_the_same(self):
        self.assertEqual(subject.vintf_declarations(self.STOCK), subject.vintf_declarations(self.ASSEMBLED))

    def test_changed_instance_or_version_differs(self):
        self.assertNotEqual(subject.vintf_declarations(self.STOCK),
                            subject.vintf_declarations(self.ASSEMBLED.replace(b'INfc/default', b'INfc/other')))
        self.assertNotEqual(subject.vintf_declarations(self.STOCK),
                            subject.vintf_declarations(self.STOCK.replace(b'<version>1</version>', b'<version>2</version>')))

    def test_newest_jdk_is_chosen_numerically(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            for name in ('jdk8', 'jdk21', 'jdk11'):
                (src / 'prebuilts/jdk' / name / 'linux-x86/bin').mkdir(parents=True)
            env = subject.Tools(src / 'out/host', src, src / 'work').environment()
            self.assertTrue(env['JAVA_HOME'].endswith('jdk21/linux-x86'))


if __name__ == '__main__':
    unittest.main()
