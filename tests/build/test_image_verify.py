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

    def test_files_not_stock(self):
        stock = hashlib.sha256(b'stock').hexdigest()
        v = self.harness({'VENDOR/lib64/libsource.so': b'source', 'VENDOR/lib64/libstock.so': b'stock'})
        self.assertTrue(subject.rule_files_not_stock({'files': {'VENDOR/lib64/libsource.so': stock}}, v)[0])
        ok, detail = subject.rule_files_not_stock({'files': {'VENDOR/lib64/libstock.so': stock}}, v)
        self.assertFalse(ok)
        self.assertIn('is the stock file', detail)
        ok, detail = subject.rule_files_not_stock({'files': {'VENDOR/lib64/libmissing.so': stock}}, v)
        self.assertFalse(ok)
        self.assertIn('missing', detail)

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

    def test_sepolicy_sources_expands_attributes_across_files(self):
        plat = (b'(type apexd)\n(type vold)\n(type sysfs)\n(type sysfs_batteryinfo)\n'
                b'(typeattributeset sysfs_type (sysfs sysfs_batteryinfo))\n'
                b'(typeattributeset base_typeattr_1 (and (sysfs_type ) (not (sysfs_batteryinfo ))))\n'
                b'(allow apexd sysfs_type (file (read open)))\n'
                b'(allow vold base_typeattr_1 (file (read open)))\n')
        mapping = b'(typeattributeset sysfs_type_202604 (sysfs_type))\n'
        vendor = (b'(type vendor_serial)\n(type vendor_other)\n(type hal_x_default)\n(type hal_y_default)\n'
                  b'(typeattributeset sysfs_type (vendor_serial vendor_other))\n'
                  b'(typeattributeset base_typeattr_2 (and (sysfs_type ) (not (vendor_serial ))))\n'
                  b'(allow hal_x_default base_typeattr_2 (file (read open)))\n'
                  b'(allow hal_y_default vendor_other (file (read open)))\n')
        members = {'SYSTEM/etc/selinux/plat_sepolicy.cil': plat,
                   'SYSTEM/etc/selinux/mapping/202604.cil': mapping,
                   'SYSTEM/etc/selinux/mapping/202604.compat.cil': b'(allow hal_y_default sysfs_type (file (read)))\n',
                   'VENDOR/etc/selinux/vendor_sepolicy.cil': vendor}
        rule = {'files': ['SYSTEM/etc/selinux/plat_sepolicy.cil', 'SYSTEM/etc/selinux/mapping/*.cil',
                          'VENDOR/etc/selinux/vendor_sepolicy.cil'],
                'target': 'vendor_serial', 'cls': 'file', 'perm': 'read', 'allowed': ['apexd', 'vold']}
        self.assertTrue(subject.rule_sepolicy_sources(rule, self.harness(members))[0])
        self.assertFalse(subject.rule_sepolicy_sources(dict(rule, allowed=['apexd']), self.harness(members))[0])
        leaky = dict(members)
        leaky['VENDOR/etc/selinux/vendor_sepolicy.cil'] = vendor + b'(allow hal_y_default sysfs_type (file (read)))\n'
        ok, detail = subject.rule_sepolicy_sources(rule, self.harness(leaky))
        self.assertFalse(ok)
        self.assertIn('hal_y_default', detail)
        self.assertFalse(subject.rule_sepolicy_sources(dict(rule, target='absent_type'), self.harness(members))[0])

    def test_soc_serial_and_thermal_hal_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        ueventd = (b'/sys/devices/soc0         serial_number    0400   root   root\n'
                   b'/sys/devices/virtual/thermal/thermal_zone*   trip_point_1_temp   0664   root   system\n'
                   b'/sys/devices/virtual/thermal/thermal_zone*   trip_point_1_hyst   0664   root   system\n'
                   b'/sys/devices/platform/soc/1d84000.ufshc   string_descriptors/serial_number   0400   root   root\n'
                   b'/sys/devices/platform/soc/1d84000.ufshc/host0/target0:0:0/0:0:0:*   vpd_pg80   0400   root   root\n'
                   b'/sys/devices/platform/soc/1d84000.ufshc/host0/target0:0:0/0:0:0:*   vpd_pg83   0400   root   root\n')
        rc = (b'service vendor.thermal-hal /vendor/bin/hw/android.hardware.thermal-service.qti\n'
              b'    interface aidl android.hardware.thermal.IThermal/default\n'
              b'    class hal\n    user system\n    group system\n    override\n')
        cil = b'(genfscon sysfs "/devices/soc0/serial_number" (u object_r vendor_sysfs_soc_serial ((s0) (s0))))\n'
        v = self.harness({'VENDOR/etc/ueventd.rc': ueventd, 'VENDOR/etc/init/init.fp6.thermal.rc': rc,
                          'VENDOR/etc/selinux/vendor_sepolicy.cil': cil})
        for rule_id in ('soc-serial-root-only', 'thermal-hal-trip-nodes', 'thermal-hal-as-system', 'soc-serial-label',
                        'ufs-serial-root-only'):
            self.assertTrue(subject.rule_text(rules[rule_id], v)[0], rule_id)
        v = self.harness({'VENDOR/etc/ueventd.rc': ueventd.replace(b'0400', b'0444'),
                          'VENDOR/etc/init/init.fp6.thermal.rc': rc.replace(b'user system', b'user root'),
                          'VENDOR/etc/selinux/vendor_sepolicy.cil':
                              cil + b'(allow hal_graphics_composer_default vendor_sysfs_soc_serial (file (read)))\n'})
        for rule_id in ('soc-serial-root-only', 'thermal-hal-as-system', 'soc-serial-label', 'ufs-serial-root-only'):
            self.assertFalse(subject.rule_text(rules[rule_id], v)[0], rule_id)

    def test_boot_hal_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        rc = (b'# Runs as its own user with CAP_SYS_RAWIO only.\n'
              b'service vendor.boot-qti /vendor/bin/hw/android.hardware.boot-service.qti\n'
              b'    class early_hal\n    user vendor_bootctl\n    group vendor_bootctl\n    capabilities SYS_RAWIO\n')
        ueventd = (b'/dev/block/sdb            0660   root       vendor_bootctl\n'
                   b'/dev/block/sdc            0660   root       vendor_bootctl\n'
                   b'/dev/block/sde            0660   root       vendor_bootctl\n'
                   b'/dev/block/platform/soc/1d84000.ufshc/by-name/misc   0660   root   vendor_bootctl\n'
                   b'/dev/ufs-bsg*             0660   root       vendor_bootctl\n')
        good = {'VENDOR/etc/init/android.hardware.boot-service.qti.rc': rc, 'VENDOR/etc/ueventd.rc': ueventd,
                'VENDOR/etc/passwd': b'vendor_bootctl::2996:2996::/:\n', 'VENDOR/etc/group': b'vendor_bootctl::2996:\n'}
        for rule_id in ('boot-hal-own-user', 'boot-hal-users', 'boot-hal-nodes'):
            self.assertTrue(subject.rule_text(rules[rule_id], self.harness(good))[0], rule_id)
        for rule_id, name, data in (
                ('boot-hal-own-user', 'VENDOR/etc/init/android.hardware.boot-service.qti.rc',
                 rc.replace(b'user vendor_bootctl', b'user root')),
                ('boot-hal-own-user', 'VENDOR/etc/init/android.hardware.boot-service.qti.rc',
                 rc.replace(b'    capabilities SYS_RAWIO\n', b'')),
                ('boot-hal-nodes', 'VENDOR/etc/ueventd.rc', ueventd + b'/dev/block/sda            0660   root   vendor_bootctl\n'),
                ('boot-hal-nodes', 'VENDOR/etc/ueventd.rc', ueventd.replace(b'/dev/block/sde ', b'/dev/block/sdf ')),
                ('boot-hal-users', 'VENDOR/etc/group', b'vendor_other::2996:\n')):
            self.assertFalse(subject.rule_text(rules[rule_id], self.harness(dict(good, **{name: data})))[0], rule_id)

    def test_eud_bluetooth_contexts_and_gralloc_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        cil = b'(genfscon sysfs "/module/eud/parameters/enable" (u object_r vendor_sysfs_eud_enable ((s0) (s0))))\n'
        v = self.harness({'VENDOR/etc/selinux/vendor_sepolicy.cil': cil,
                          'VENDOR/etc/init/init.qcom.rc': b'on boot\n    write /sys/module/x/parameters/y 1\n',
                          'VENDOR/etc/selinux/vendor_hwservice_contexts':
                              b'vendor.qti.hardware.fm::IFmHci u:object_r:vendor_hal_fm_hwservice:s0\n',
                          'VENDOR/lib64/libgrallocutils.so': b'\x7fELF qdgralloc',
                          'VENDOR/lib64/libgralloccore.so': b'\x7fELF qdgralloc'})
        for rule_id in ('eud-enable-label', 'eud-stays-off', 'no-bluetooth-value-add-contexts', 'gralloc-no-libubwcp'):
            self.assertTrue(subject.rule_text(rules[rule_id], v)[0], rule_id)
        v = self.harness({'VENDOR/etc/selinux/vendor_sepolicy.cil':
                              cil + b'(allow vendor_hal_usb_qti vendor_sysfs_eud_enable (file (open write)))\n',
                          'VENDOR/etc/init/init.qcom.rc': b'on boot\n    write /sys/module/eud/parameters/enable 1\n',
                          'VENDOR/etc/selinux/vendor_hwservice_contexts':
                              b'com.dsi.ant::IAnt u:object_r:hal_bluetooth_hwservice:s0\n',
                          'VENDOR/lib64/libgrallocutils.so': b'\x7fELF libubwcp.so\x00',
                          'VENDOR/lib64/libgralloccore.so': b'\x7fELF'})
        for rule_id in ('eud-enable-label', 'eud-stays-off', 'no-bluetooth-value-add-contexts', 'gralloc-no-libubwcp'):
            self.assertFalse(subject.rule_text(rules[rule_id], v)[0], rule_id)
        self.assertFalse(subject.rule_text(rules['eud-stays-off'], self.harness(
            {'VENDOR_DLKM/lib/modules/modules.options': b'options eud enable=1\n'}))[0])
        plat = (b'(type ueventd)\n(type vendor_init)\n(type vold)\n(type sysfs)\n'
                b'(typeattributeset sysfs_type (sysfs))\n'
                b'(allow ueventd sysfs_type (file (open write)))\n(allow vendor_init sysfs_type (file (write)))\n'
                b'(allow vold sysfs (file (open write)))\n')
        vendor = b'(type vendor_sysfs_eud_enable)\n(typeattributeset sysfs_type (vendor_sysfs_eud_enable))\n'
        writers = rules['eud-enable-writers']
        policy = {name: b'' for name in writers['files'] if '*' not in name}
        policy.update({'SYSTEM/etc/selinux/plat_sepolicy.cil': plat, 'VENDOR/etc/selinux/vendor_sepolicy.cil': vendor})
        self.assertTrue(subject.rule_sepolicy_sources(writers, self.harness(policy))[0])
        policy['VENDOR/etc/selinux/vendor_sepolicy.cil'] = vendor + b'(allow vold vendor_sysfs_eud_enable (file (write)))\n'
        self.assertFalse(subject.rule_sepolicy_sources(writers, self.harness(policy))[0])

    def test_adb_network_and_display_colour_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        clean = {'SYSTEM/build.prop': b'ro.adb.secure=1\n', 'VENDOR/build.prop': b'ro.vendor.x=1\n',
                 'VENDOR/etc/init/a.rc': b'service a /vendor/bin/a\n'}
        self.assertTrue(subject.rule_text(rules['no-adb-over-network'], self.harness(clean))[0])
        self.assertFalse(subject.rule_text(rules['no-adb-over-network'], self.harness(
            dict(clean, **{'VENDOR/etc/init/a.rc': b'on boot\n    setprop persist.adb.tcp.port 5555\n'})))[0])
        policy = {'VENDOR/etc/selinux/vendor_sepolicy.cil': b'(typeattribute vendor_hal_display_color)\n',
                  'VENDOR/etc/selinux/vendor_file_contexts': b'/vendor/bin/x u:object_r:x_exec:s0\n',
                  'VENDOR/etc/selinux/vendor_service_contexts': b'x u:object_r:x_service:s0\n'}
        self.assertTrue(subject.rule_text(rules['no-display-colour-policy'], self.harness(policy))[0])
        self.assertFalse(subject.rule_text(rules['no-display-colour-policy'], self.harness(
            dict(policy, **{'VENDOR/etc/selinux/vendor_sepolicy.cil': b'(type vendor_hal_display_color_default)\n'})))[0])

    def test_wifi_wake_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        ini = 'VENDOR/etc/wifi/qca6750/WCNSS_qcom_cfg.ini'
        overlay = 'VENDOR/etc/wifi/wpa_supplicant_overlay.conf'
        good = {ini: b'gDot11Mode=0\ngEnableWoW=2\nEND\n\n# Note\n',
                overlay: b'disable_scan_offload=1\np2p_disabled=1\n'}
        for rule_id in ('wifi-no-magic-packet-wake', 'wifi-no-wowlan-triggers'):
            self.assertTrue(subject.rule_text(rules[rule_id], self.harness(good))[0], rule_id)
        for members in ({ini: b'gDot11Mode=0\nEND\n'}, {ini: b'gEnableWoW=3\ngEnableWoW=2\nEND\n'},
                        {ini: b'END\ngEnableWoW=2\n'}):
            self.assertFalse(subject.rule_text(rules['wifi-no-magic-packet-wake'], self.harness(members))[0])
        self.assertFalse(subject.rule_text(rules['wifi-no-wowlan-triggers'], self.harness(
            {overlay: good[overlay] + b'wowlan_triggers=magic_pkt\n'}))[0])

    def test_usb_port_control_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        node = b'/sys/devices/platform/soc/a600000.ssusb/dynamic_disable'
        sic = b'/sys/class/qcom-battery/suspend_input_current'
        rc = (b'on early-boot && property:ro.debuggable=0\n    write ' + node + b' 1\n'
              b'    wait ' + sic + b' 5\n    write ' + sic + b' 1\n\n'
              b'on boot && property:ro.debuggable=0\n    write ' + sic + b' 1\n\n'
              b'on early-boot && property:ro.debuggable=1\n    wait ' + sic + b' 5\n    write ' + sic + b' 0\n\n'
              b'on charger\n    write ' + sic + b' 0\n\n'
              b'on shutdown\n    write ' + sic + b' 0\n\n'
              b'on property:sys.port_security_mode=ports_disabled\n    write ' + node + b' 1\n'
              b'    write ' + sic + b' 1\n\n'
              b'on property:sys.port_security_mode=charging-only_immediate\n    write ' + sic + b' 0\n'
              b'    write ' + node + b' 1\n\n'
              b'on property:sys.port_security_mode=charging-only\n    write ' + sic + b' 0\n\n'
              b'on property:sys.port_security_mode=ports_enabled\n    write ' + sic + b' 0\n'
              b'    write ' + node + b' 0\n')
        cil = (b'(genfscon sysfs "/devices/platform/soc/a600000.ssusb/dynamic_disable" '
               b'(u object_r vendor_sysfs_usb_data_disable ((s0) (s0))))\n')
        triggers = lambda text: subject.rule_text(rules['usb-port-control-triggers'], self.harness(
            {'VENDOR/etc/init/init.qcom.usb.rc': text}))[0]
        self.assertTrue(triggers(rc))
        # charging-only keeps the current connection, so it must not cut data.
        self.assertFalse(triggers(rc.replace(b'charging-only\n    write ' + sic + b' 0\n',
                                             b'charging-only\n    write ' + sic + b' 0\n    write ' + node + b' 1\n')))
        self.assertFalse(triggers(rc.replace(b'=ports_enabled', b'=ports_on')))
        # Off cuts data first, then the charger input.
        self.assertFalse(triggers(rc.replace(b'    write ' + node + b' 1\n    write ' + sic + b' 1\n',
                                             b'    write ' + sic + b' 1\n    write ' + node + b' 1\n')))
        # Only Off suspends the input; every other state, boot, charger mode and shutdown resume it.
        self.assertFalse(triggers(rc.replace(b'charging-only\n    write ' + sic + b' 0',
                                             b'charging-only\n    write ' + sic + b' 1')))
        self.assertFalse(triggers(rc + b'\non property:sys.boot_completed=1\n    write ' + sic + b' 1\n'))
        self.assertFalse(triggers(rc.replace(b'on shutdown\n    write ' + sic + b' 0\n\n', b'')))
        self.assertFalse(triggers(rc.replace(b'on charger\n    write ' + sic + b' 0\n\n', b'')))
        # User builds start with the input suspended; nothing at boot undoes it or cuts the controller.
        self.assertFalse(triggers(rc.replace(b'on boot && property:ro.debuggable=0\n    write ' + sic + b' 1\n\n', b'')))
        self.assertFalse(triggers(rc + b'\non boot\n    write ' + sic + b' 0\n'))
        # User builds also cut the controller at boot, before the input; debuggable builds never do.
        self.assertFalse(triggers(rc.replace(b'debuggable=0\n    write ' + node + b' 1\n', b'debuggable=0\n')))
        self.assertFalse(triggers(rc.replace(b'debuggable=1\n    wait', b'debuggable=1\n    write ' + node + b' 1\n    wait')))
        # Only the port states and the user-build boot cut turn data off; only ports_enabled turns it on.
        self.assertFalse(triggers(rc + b'\non property:sys.boot_completed=1\n    write ' + node + b' 1\n'))
        self.assertFalse(triggers(rc + b'\non property:sys.boot_completed=1\n    write ' + node + b' 0\n'))
        # Debuggable builds keep charging at boot, and the user-build cut waits for the node.
        self.assertFalse(triggers(rc.replace(b'debuggable=1\n    wait ' + sic + b' 5\n    write ' + sic + b' 0',
                                             b'debuggable=1\n    wait ' + sic + b' 5\n    write ' + sic + b' 1')))
        self.assertFalse(triggers(rc.replace(b'debuggable=0\n    write ' + node + b' 1\n    wait ' + sic + b' 5\n',
                                             b'debuggable=0\n    write ' + node + b' 1\n')))
        isl = lambda text: subject.rule_text(rules['usb-port-control-input-suspend-label'], self.harness(
            {'VENDOR/etc/selinux/vendor_sepolicy.cil': text}))[0]
        sic_cil = (b'(genfscon sysfs "/class/qcom-battery/suspend_input_current" '
                   b'(u object_r vendor_sysfs_usb_input_suspend ((s0) (s0))))\n')
        self.assertTrue(isl(sic_cil))
        self.assertFalse(isl(sic_cil.replace(b'vendor_sysfs_usb_input_suspend', b'sysfs')))
        label = lambda text: subject.rule_text(rules['usb-port-control-label'], self.harness(
            {'VENDOR/etc/selinux/vendor_sepolicy.cil': text}))[0]
        self.assertTrue(label(cil))
        self.assertFalse(label(cil.replace(b'vendor_sysfs_usb_data_disable', b'vendor_sysfs_usb_device')))

        plat = (b'(type ueventd)\n(type vendor_init)\n(type sysfs)\n(type sysfs_usermodehelper)\n'
                b'(typeattributeset sysfs_type (sysfs sysfs_usermodehelper))\n'
                b'(typeattributeset base_typeattr_9 (and (sysfs_type ) (not (sysfs_usermodehelper ))))\n'
                b'(allow ueventd sysfs_type (file (open append write lock map)))\n'
                b'(allow vendor_init base_typeattr_9 (file (getattr open read write)))\n')
        vendor = (b'(type vendor_sysfs_usb_data_disable)\n(type vendor_hal_usb_qti)\n'
                  b'(typeattributeset sysfs_type (vendor_sysfs_usb_data_disable))\n'
                  b'(allow vendor_init vendor_sysfs_usb_data_disable (file (open write)))\n')
        rule = dict(rules['usb-port-control-writers'],
                    files=['SYSTEM/etc/selinux/plat_sepolicy.cil', 'VENDOR/etc/selinux/vendor_sepolicy.cil'])
        writers = lambda text: subject.rule_sepolicy_sources(rule, self.harness(
            {'SYSTEM/etc/selinux/plat_sepolicy.cil': plat, 'VENDOR/etc/selinux/vendor_sepolicy.cil': text}))
        self.assertTrue(writers(vendor)[0])
        ok, detail = writers(vendor + b'(allow vendor_hal_usb_qti vendor_sysfs_usb_data_disable (file (open write)))\n')
        self.assertFalse(ok)
        self.assertIn('vendor_hal_usb_qti', detail)
        isw_rule = dict(rules['usb-port-control-input-suspend-writers'],
                        files=['SYSTEM/etc/selinux/plat_sepolicy.cil', 'VENDOR/etc/selinux/vendor_sepolicy.cil'])
        isw = lambda text: subject.rule_sepolicy_sources(isw_rule, self.harness(
            {'SYSTEM/etc/selinux/plat_sepolicy.cil': plat, 'VENDOR/etc/selinux/vendor_sepolicy.cil': text}))
        sic_vendor = (b'(type vendor_sysfs_usb_input_suspend)\n(type hal_health_default)\n'
                      b'(typeattributeset sysfs_type (vendor_sysfs_usb_input_suspend))\n'
                      b'(allow vendor_init vendor_sysfs_usb_input_suspend (file (open write)))\n')
        self.assertTrue(isw(sic_vendor)[0])
        ok, detail = isw(sic_vendor + b'(allow hal_health_default vendor_sysfs_usb_input_suspend (file (open write)))\n')
        self.assertFalse(ok)
        self.assertIn('hal_health_default', detail)

        jar = io.BytesIO()
        with zipfile.ZipFile(jar, 'w') as inner:
            inner.writestr('classes3.dex', b'dex\x00debug.diamaneos.usb_port_security.test\x00'
                                          b'USB-C port Off at boot\x00'
                                          b'port status reports DATA_STATUS_DISABLED_FORCE for %s\x00')
        self.assertTrue(subject.rule_zip_contains(rules['usb-port-control-debug-guards'], self.harness(
            {'SYSTEM/framework/services.jar': jar.getvalue()}))[0])
        self.assertTrue(subject.rule_zip_contains(rules['usb-port-control-off-at-boot'], self.harness(
            {'SYSTEM/framework/services.jar': jar.getvalue()}))[0])
        self.assertTrue(subject.rule_zip_contains(rules['usb-port-control-data-reenable'], self.harness(
            {'SYSTEM/framework/services.jar': jar.getvalue()}))[0])
        clean = {'SYSTEM/build.prop': b'ro.adb.secure=1\n', 'VENDOR/build.prop': b'ro.vendor.x=1\n',
                 'VENDOR/etc/init/a.rc': b'service a /vendor/bin/a\n'}
        protection = lambda members: subject.rule_text(rules['usb-data-protection-unset'], self.harness(members))[0]
        self.assertTrue(protection(clean))
        self.assertFalse(protection(dict(clean, **{
            'VENDOR/build.prop': b'ro.usb.data_protection.disable_when_locked.supported=true\n'})))

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

    def test_audio_effects_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        cfg, fx = 'VENDOR/etc/audio/sku_volcano/audio_effects.xml', 'VENDOR/lib64/soundfx/'
        config = b''.join(b'<library name="l" path="%s.so"/>\n' % stem for stem in (
            b'libbundlewrapper', b'libreverbwrapper', b'libvisualizer', b'libdownmix', b'libldnhncr', b'libdynproc',
            b'libqcomvoiceprocessing', b'libvolumelistener'))
        config += (b'<effect name="aec" library="voice_processing" uuid="0f8d0d2a-59e5-45fe-b6e4-248c8a799109"/>\n'
                   b'<effect name="ns" library="voice_processing" uuid="1d97bb0b-9e2f-4403-9ae3-58c2554306f8"/>\n'
                   b'<preprocess><stream type="voice_communication"><apply effect="aec"/></stream></preprocess>\n')
        voice = b'\x7fELF liblog.so\0AELI\0Qualcomm Fluence\0'
        listener = (b'\x7fELF liblog.so\0/vendor/lib64/hw/audio.primary.%s.so\0volcano\0'
                    b'audio_hw_send_gain_dep_calibration\0')
        good = {cfg: config, fx + 'libqcomvoiceprocessing.so': voice, fx + 'libvolumelistener.so': listener}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        for rule_id in ('audio-effects-closed-absent', 'audio-effects-closed-unreferenced', 'audio-effects-config',
                        'audio-effects-source-built', 'audio-voice-processing-not-stock',
                        'audio-volume-listener-source'):
            self.assertTrue(check(rule_id, good), rule_id)
        # Each stock piece fails its rule.
        for rule_id, change in [
                ('audio-effects-closed-absent', {fx + 'libqcompostprocbundle.so': b'x'}),
                ('audio-effects-closed-absent', {fx + 'libqcomvisualizer.so': b'x'}),
                ('audio-effects-closed-unreferenced', {'ODM/etc/audio_effects.xml': b'path="libqcomvisualizer.so"'}),
                ('audio-effects-config', {cfg: config + b'<effectProxy name="eq"><libhw library="offload_bundle"/>'}),
                ('audio-effects-config', {cfg: config.replace(b'libqcomvoiceprocessing', b'libaudiopreprocessing')}),
                ('audio-effects-source-built', {fx + 'libvolumelistener.so': None}),
                ('audio-voice-processing-not-stock',
                 {fx + 'libqcomvoiceprocessing.so': voice + bytes.fromhex('0544f65efee165554f0b09f8f8eaf294')}),
                ('audio-volume-listener-source',
                 {fx + 'libvolumelistener.so': listener + bytes.fromhex('7eb370162f230575e91bed1399cdc6ea')}),
                ('audio-volume-listener-source', {fx + 'libvolumelistener.so': listener + b'libar-pal.so\0'}),
                ('audio-volume-listener-source', {fx + 'libvolumelistener.so': listener.replace(b'volcano', b'lahaina')})]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                members = {k: v for k, v in {**good, **change}.items() if v is not None}
                self.assertFalse(check(rule_id, members))

    def test_bluetooth_seccomp_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        path, service = 'VENDOR/etc/seccomp_policy/bluetooth-hci.policy', 'VENDOR/bin/hw/android.hardware.bluetooth@1.1-service.fp6'
        policy = (b'futex: 1\nclone: arg0 & 0x10000\nsocket: arg0 == 1; return 97\nkill: arg1 == 9\n'
                  b'mmap: arg2 in ~PROT_EXEC || arg2 in ~PROT_WRITE\nmprotect: arg2 in ~PROT_EXEC || arg2 in ~PROT_WRITE\n')
        good = {path: policy, service: b'\x7fELF\0/vendor/etc/seccomp_policy/bluetooth-hci.policy\0trap\0'}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        for rule_id in ('bluetooth-seccomp-policy', 'bluetooth-service-applies-seccomp', 'bluetooth-seccomp-enforced',
                        'bluetooth-seccomp-rules'):
            self.assertTrue(check(rule_id, good), rule_id)
        for rule_id, change in [
                ('bluetooth-seccomp-policy', {path: None}),
                ('bluetooth-service-applies-seccomp', {service: b'\x7fELF\0/vendor/etc/seccomp_policy/other.policy\0'}),
                ('bluetooth-seccomp-enforced', {service: good[service].replace(b'trap\0', b'log-only\0')}),
                ('bluetooth-seccomp-enforced', {service: good[service] + b'log-only\0'}),
                ('bluetooth-seccomp-rules', {path: policy + b'execve: 1\n'}),
                ('bluetooth-seccomp-rules', {path: policy + b'  clone3: 1\n'}),
                ('bluetooth-seccomp-rules', {path: policy.replace(b'socket: arg0 == 1; return 97', b'socket: 1')}),
                ('bluetooth-seccomp-rules', {path: policy.replace(b'kill: arg1 == 9', b'kill: 1')}),
                ('bluetooth-seccomp-rules', {path: policy.replace(b'clone: arg0 & 0x10000', b'clone: 1')})]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                members = {k: v for k, v in {**good, **change}.items() if v is not None}
                self.assertFalse(check(rule_id, members))

    def test_bluetooth_factory_address_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        imeiprovd, bt_rc = 'VENDOR/etc/init/imeiprovd.rc', 'VENDOR/etc/init/init.fp6.bluetooth.rc'
        contexts, plat, vendor = ('VENDOR/etc/selinux/vendor_property_contexts', 'SYSTEM/etc/selinux/plat_sepolicy.cil',
                                  'VENDOR/etc/selinux/vendor_sepolicy.cil')
        vendor_cil = (b'(type vendor_diamaneos_bt_address_prop)\n(type vendor_bluetooth_address_prop)\n'
                      b'(typeattributeset property_type (vendor_diamaneos_bt_address_prop vendor_bluetooth_address_prop))\n'
                      b'(allow diamaneos_imeiprov vendor_diamaneos_bt_address_prop (property_service (set)))\n'
                      b'(allow diamaneos_imeiprov vendor_diamaneos_bt_address_prop (file (read getattr map open)))\n'
                      b'(allow vendor_init vendor_diamaneos_bt_address_prop (file (read getattr map open)))\n'
                      b'(allow vendor_init vendor_bluetooth_address_prop (property_service (set)))\n'
                      b'(allow vendor_init vendor_bluetooth_address_prop (file (read getattr map open)))\n'
                      b'(allow hal_bluetooth_default vendor_bluetooth_address_prop (file (read getattr map open)))\n')
        good = {
            imeiprovd: b'service vendor.imeiprovd-bt /vendor/bin/imeiprovd --bt-address\n    oneshot\n\n'
                       b'on post-fs\n    start vendor.imeiprovd-bt\n',
            bt_rc: b'on property:ro.vendor.diamaneos.bt.factory_address=*\n'
                   b'    setprop ro.vendor.bt.boot.macaddr ${ro.vendor.diamaneos.bt.factory_address}\n',
            contexts: b'ro.vendor.diamaneos.bt.factory_address u:object_r:vendor_diamaneos_bt_address_prop:s0 exact string\n'
                      b'ro.vendor.bt.boot.macaddr   u:object_r:vendor_bluetooth_address_prop:s0 exact string\n',
            plat: (b'(type init)\n(type dumpstate)\n(type vendor_init)\n(type rild)\n(type hal_bluetooth_default)\n'
                   b'(type diamaneos_imeiprov)\n(allow init property_type (property_service (set)))\n'
                   b'(allow init property_type (file (read open)))\n(allow dumpstate property_type (file (read open)))\n'),
            vendor: vendor_cil,
            'SYSTEM_EXT/etc/selinux/system_ext_sepolicy.cil': b'', 'PRODUCT/etc/selinux/product_sepolicy.cil': b'',
            'VENDOR/etc/selinux/plat_pub_versioned.cil': b'', 'ODM/etc/selinux/odm_sepolicy.cil': b''}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        ids = ('bluetooth-factory-address-service', 'bluetooth-factory-address-copy', 'bluetooth-factory-address-contexts',
               'bluetooth-factory-address-setter', 'bluetooth-factory-address-readers', 'bluetooth-hal-address-setter',
               'bluetooth-hal-address-readers')
        for rule_id in ids:
            self.assertTrue(check(rule_id, good), rule_id)
        for rule_id, change in [
                ('bluetooth-factory-address-service', {imeiprovd: good[imeiprovd].replace(b'start', b'stop')}),
                ('bluetooth-factory-address-copy', {bt_rc: None}),
                ('bluetooth-factory-address-copy', {bt_rc: b'on property:ro.vendor.trace.btmac=*\n'
                                                           b'    setprop ro.vendor.bt.boot.macaddr ${ro.vendor.trace.btmac}\n'}),
                ('bluetooth-factory-address-contexts', {contexts: good[contexts].replace(
                    b'vendor_bluetooth_address_prop', b'vendor_default_prop')}),
                ('bluetooth-factory-address-setter', {vendor: vendor_cil + b'(allow hal_bluetooth_default '
                                                      b'vendor_diamaneos_bt_address_prop (property_service (set)))\n'}),
                ('bluetooth-factory-address-readers', {vendor: vendor_cil + b'(allow rild property_type (file (read)))\n'}),
                ('bluetooth-hal-address-setter', {vendor: vendor_cil + b'(allow hal_bluetooth_default '
                                                  b'vendor_bluetooth_address_prop (property_service (set)))\n'}),
                ('bluetooth-hal-address-readers', {vendor: vendor_cil + b'(allow rild vendor_bluetooth_address_prop '
                                                   b'(file (read open)))\n'}),
                ('bluetooth-hal-address-readers', {vendor: vendor_cil.replace(b'(type vendor_bluetooth_address_prop)\n', b'')})]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                members = {k: v for k, v in {**good, **change}.items() if v is not None}
                self.assertFalse(check(rule_id, members))

    def test_firmware_release_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        rc, ueventd = 'VENDOR/etc/init/fwrelease.rc', 'VENDOR/etc/ueventd.rc'
        contexts, properties = 'VENDOR/etc/selinux/vendor_file_contexts', 'VENDOR/etc/selinux/vendor_property_contexts'
        plat, vendor = 'SYSTEM/etc/selinux/plat_sepolicy.cil', 'VENDOR/etc/selinux/vendor_sepolicy.cil'
        checked = ['abl', 'aop', 'aop_config', 'bluetooth', 'cpucp', 'cpucp_dtb', 'devcfg', 'dsp', 'featenabler', 'hyp',
                   'imagefv', 'keymaster', 'modem', 'multiimgoem', 'qupfw', 'shrm', 'tz', 'uefi', 'uefisecapp', 'xbl',
                   'xbl_config', 'xbl_ramdump']
        relabelled = [n for n in checked if n not in ('bluetooth', 'modem', 'uefi', 'uefisecapp', 'xbl')]
        ufs = '/dev/block/platform/soc/1d84000.ufshc/by-name/'
        nodes = ''.join(f'{ufs}{n}_{s}   0640   root   vendor_fwrelease\n' for n in checked for s in 'ab')
        labels = ''.join(f'{ufs}{n}_[ab]      u:object_r:vendor_firmware_image_block_device:s0\n' for n in relabelled)
        labels += ''.join(f'{ufs}{n}_[ab]      u:object_r:vendor_custom_ab_block_device:s0\n'
                          for n in ('vbmeta', 'dtbo', 'pvmfw', 'multiimgqti', 'qweslicstore'))
        labels += '/(vendor|system/vendor)/bin/fwrelease    u:object_r:vendor_fwrelease_exec:s0\n'
        blocks = ('vendor_firmware_image_block_device', 'vendor_modem_block_device', 'vendor_uefi_block_device',
                  'vendor_xbl_block_device')
        vendor_cil = (''.join(f'(type {t})\n' for t in blocks + ('vendor_diamaneos_firmware_release_prop',))
                      + '(typeattributeset property_type (vendor_diamaneos_firmware_release_prop))\n'
                      + '(typeattributeset dev_type (' + ' '.join(blocks) + '))\n'
                      + '(allow vendor_fwrelease vendor_diamaneos_firmware_release_prop (property_service (set)))\n'
                      + '(allow vendor_fwrelease vendor_diamaneos_firmware_release_prop (file (read getattr map open)))\n'
                      + '(allow system_app vendor_diamaneos_firmware_release_prop (file (read getattr map open)))\n'
                      + '(allow update_engine_common dev_type (blk_file (ioctl read write getattr open)))\n'
                      + ''.join(f'(allow vendor_fwrelease {t} (blk_file (open read)))\n' for t in blocks)
                      + '(allow hal_bootctl_default vendor_xbl_block_device (blk_file (read write open)))\n'
                      + '(allow tee vendor_xbl_block_device (blk_file (read)))\n').encode()
        good = {
            'VENDOR/bin/fwrelease': b'\x7fELF', 'VENDOR/etc/diamaneos/firmware-releases.txt': b'image ...\n',
            rc: b'on property:sys.boot_completed=1\n    start vendor.fwrelease\n\n'
                b'# The group lets it read the firmware partitions.\n'
                b'service vendor.fwrelease /vendor/bin/fwrelease ${ro.boot.slot_suffix}\n'
                b'    user vendor_fwrelease\n    group vendor_fwrelease\n    capabilities\n    ioprio idle 7\n'
                b'    priority 19\n    timeout_period 60\n    oneshot\n    disabled\n',
            ueventd: (f'# fwrelease (vendor_fwrelease) reads these.\n{ufs}traceability   0440   root   vendor_imeiprov\n'
                      + nodes).encode(),
            contexts: labels.encode(),
            properties: b'ro.vendor.diamaneos.firmware_release u:object_r:vendor_diamaneos_firmware_release_prop:s0 '
                        b'exact string\n',
            plat: (b'(type init)\n(type dumpstate)\n(type vendor_init)\n(type system_app)\n(type shell)\n(type rild)\n'
                   b'(type update_engine)\n(type tee)\n(type hal_bootctl_default)\n(type vendor_fwrelease)\n'
                   b'(typeattributeset update_engine_common (update_engine))\n'
                   b'(allow init property_type (property_service (set)))\n(allow init property_type (file (read open)))\n'
                   b'(allow dumpstate property_type (file (read open)))\n(allow init dev_type (blk_file (read open)))\n'
                   b'(allow shell dev_type (blk_file (getattr)))\n'),
            vendor: vendor_cil,
            'SYSTEM_EXT/etc/selinux/system_ext_sepolicy.cil': b'', 'PRODUCT/etc/selinux/product_sepolicy.cil': b'',
            'VENDOR/etc/selinux/plat_pub_versioned.cil': b'', 'ODM/etc/selinux/odm_sepolicy.cil': b''}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        ids = ('firmware-release-present', 'firmware-release-service', 'firmware-release-nodes',
               'firmware-release-labels', 'firmware-release-property', 'firmware-release-setter',
               'firmware-release-readers', 'firmware-image-readers', 'firmware-image-writers',
               'firmware-modem-image-readers', 'firmware-uefi-image-readers', 'firmware-xbl-image-readers')
        for rule_id in ids:
            self.assertTrue(check(rule_id, good), rule_id)
        grant = lambda rule: {vendor: vendor_cil + rule.encode() + b'\n'}
        for rule_id, change in [
                ('firmware-release-present', {'VENDOR/etc/diamaneos/firmware-releases.txt': None}),
                ('firmware-release-service', {rc: good[rc].replace(b'    ioprio idle 7\n', b'')}),
                ('firmware-release-service', {rc: good[rc].replace(b'    timeout_period 60\n', b'')}),
                ('firmware-release-service', {rc: good[rc].replace(b'${ro.boot.slot_suffix}', b'_a')}),
                ('firmware-release-service', {rc: good[rc].replace(b'    capabilities\n', b'    capabilities SYS_RAWIO\n')}),
                ('firmware-release-service', {rc: good[rc] + b'    class main\n'}),
                ('firmware-release-service', {rc: good[rc] + b'    group vendor_fwrelease system\n'}),
                ('firmware-release-service', {rc: good[rc].replace(b'sys.boot_completed=1', b'vendor.fwrelease=1')}),
                ('firmware-release-nodes', {ueventd: good[ueventd].replace(f'{ufs}tz_b   0640'.encode(),
                                                                          f'{ufs}tz_b   0660'.encode())}),
                ('firmware-release-nodes', {ueventd: good[ueventd].replace(f'{ufs}xbl_a   0640   root   vendor_fwrelease\n'
                                                                          .encode(), b'')}),
                ('firmware-release-nodes', {ueventd: good[ueventd] + f'{ufs}vbmeta_a   0640   root   vendor_fwrelease\n'
                                            .encode()}),
                ('firmware-release-nodes', {ueventd: good[ueventd] + b'/dev/block/sdb   0640   root   vendor_fwrelease\n'}),
                ('firmware-release-labels', {contexts: good[contexts].replace(
                    f'{ufs}tz_[ab]      u:object_r:vendor_firmware_image_block_device'.encode(),
                    f'{ufs}tz_[ab]      u:object_r:vendor_custom_ab_block_device'.encode())}),
                ('firmware-release-labels', {contexts: good[contexts].replace(
                    f'{ufs}vbmeta_[ab]      u:object_r:vendor_custom_ab_block_device'.encode(),
                    f'{ufs}vbmeta_[ab]      u:object_r:vendor_firmware_image_block_device'.encode())}),
                ('firmware-release-property', {properties: good[properties].replace(b'exact string', b'exact enum mixed')}),
                ('firmware-release-setter', grant('(allow rild vendor_diamaneos_firmware_release_prop '
                                                  '(property_service (set)))')),
                ('firmware-release-readers', grant('(allow shell vendor_diamaneos_firmware_release_prop (file (read)))')),
                ('firmware-release-readers', grant('(allow rild property_type (file (read)))')),
                ('firmware-image-readers', grant('(allow rild vendor_firmware_image_block_device (blk_file (read)))')),
                ('firmware-image-readers', grant('(allow shell dev_type (blk_file (read)))')),
                ('firmware-image-writers', grant('(allow vendor_fwrelease vendor_firmware_image_block_device '
                                                 '(blk_file (write)))')),
                ('firmware-image-writers', {vendor: vendor_cil.replace(b'(type vendor_firmware_image_block_device)\n', b'')}),
                ('firmware-modem-image-readers', grant('(allow rild vendor_modem_block_device (blk_file (read)))')),
                ('firmware-uefi-image-readers', grant('(allow rild vendor_uefi_block_device (blk_file (read)))')),
                ('firmware-xbl-image-readers', grant('(allow rild vendor_xbl_block_device (blk_file (read)))'))]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                members = {k: v for k, v in {**good, **change}.items() if v is not None}
                self.assertFalse(check(rule_id, members))

    def test_audio_source_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        lib, hal = 'VENDOR/lib64/', 'VENDOR/lib64/hw/audio.primary.volcano.so'
        manifest = 'VENDOR/etc/vintf/manifest.xml'
        rm = 'VENDOR/etc/audio/sku_volcano/resourcemanager_volcano_mtp_fps.xml'
        good = {hal: b'\x7fELF libar-pal.so\0vendor.qti.hardware.AGMIPC@1.0.so\0HMI\0',
                lib + 'libar-pal.so': b'\x7fELF libagmclient.so\0libvui_intf.so\0',
                lib + 'libagm.so': b'\x7fELF libar-gsl.so\0libats.so\0',
                manifest: b'<name>vendor.qti.hardware.AGMIPC</name><name>android.hardware.bluetooth</name>',
                rm: b'<speaker_protection_enabled>1</speaker_protection_enabled>'}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        for rule_id in ('audio-source-dropped', 'audio-hal-not-stock', 'audio-pal-not-stock', 'audio-agm-not-stock',
                        'audio-pal-links', 'audio-agm-links', 'audio-hal-links', 'audio-declared',
                        'audio-speaker-protection-on'):
            self.assertTrue(check(rule_id, good), rule_id)
        # Each stock piece fails its rule.
        for rule_id, change in [
                ('audio-source-dropped', {lib + 'vendor.qti.hardware.pal@1.0-impl.so': b'x'}),
                ('audio-source-dropped', {lib + 'libarmemlog.so': b'x'}),
                ('audio-source-dropped', {'VENDOR/etc/vintf/manifest/manifest_non_qmaa.xml': b'x'}),
                ('audio-hal-not-stock', {hal: good[hal] + bytes.fromhex('5148e9e387efbad9a545760ff257b8a3')}),
                ('audio-pal-not-stock', {lib + 'libar-pal.so': bytes.fromhex('d374661ca1aec5ed9f33f5aefde042f3')}),
                ('audio-agm-not-stock', {lib + 'libagm.so': bytes.fromhex('b214b1a78516520aa6d493c7c44c27e9')}),
                ('audio-pal-links', {lib + 'libar-pal.so': good[lib + 'libar-pal.so'] + b'libarmemlog.so\0'}),
                ('audio-agm-links', {lib + 'libagm.so': good[lib + 'libagm.so'] + b'libaudio_log_utils.so\0'}),
                ('audio-hal-links', {hal: good[hal] + b'vendor.qti.hardware.pal@1.0-impl.so\0'}),
                ('audio-declared', {manifest: good[manifest] + b'<name>vendor.qti.hardware.pal</name>'}),
                ('audio-speaker-protection-on', {rm: good[rm].replace(b'>1<', b'>0<')})]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                self.assertFalse(check(rule_id, {**good, **change}))
    def test_wlan_factory_mac_rules(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        rc, ueventd, fc = 'VENDOR/etc/init/imeiprovd.rc', 'VENDOR/etc/ueventd.rc', 'VENDOR/etc/selinux/vendor_file_contexts'
        ini, plat, vendor = ('VENDOR/etc/wifi/qca6750/WCNSS_qcom_cfg.ini', 'SYSTEM/etc/selinux/plat_sepolicy.cil',
                             'VENDOR/etc/selinux/vendor_sepolicy.cil')
        vendor_cil = (b'(type vendor_diamaneos_wlan_mac_file)\n(typeattributeset file_type (vendor_diamaneos_wlan_mac_file))\n'
                      b'(allow diamaneos_imeiprov vendor_diamaneos_wlan_mac_file (file (write create open)))\n'
                      b'(allow ueventd vendor_diamaneos_wlan_mac_file (file (read getattr open)))\n')
        good = {
            rc: (b'service vendor.imeiprovd-wlan /vendor/bin/imeiprovd --wlan-mac\n    oneshot\n    disabled\n\n'
                 b'on post-fs\n    start vendor.imeiprovd-bt\n    # The firmware tree.\n'
                 b'    mkdir /mnt/vendor/wlan_mac 0710 root vendor_imeiprov\n'
                 b'    mkdir /mnt/vendor/wlan_mac/wlan 0710 root vendor_imeiprov\n'
                 b'    mkdir /mnt/vendor/wlan_mac/wlan/qca_cld 0710 root vendor_imeiprov\n'
                 b'    mkdir /mnt/vendor/wlan_mac/wlan/qca_cld/qca6750 0700 vendor_imeiprov vendor_imeiprov\n'
                 b'    exec_start vendor.imeiprovd-wlan\n'),
            ueventd: b'firmware_directories /vendor/firmware_mnt/image/\nfirmware_directories /mnt/vendor/wlan_mac/\n',
            fc: b'/mnt/vendor/wlan_mac(/.*)?   u:object_r:vendor_diamaneos_wlan_mac_file:s0\n',
            ini: b'gEnableWoW=2\nread_mac_addr_from_mac_file=1\n',
            plat: (b'(type init)\n(type vendor_init)\n(type ueventd)\n(type rild)\n(type diamaneos_imeiprov)\n'
                   b'(allow init file_type (file (read write create open)))\n'
                   b'(allow vendor_init file_type (file (read write create open)))\n'),
            vendor: vendor_cil,
            'SYSTEM_EXT/etc/selinux/system_ext_sepolicy.cil': b'', 'PRODUCT/etc/selinux/product_sepolicy.cil': b'',
            'VENDOR/etc/selinux/plat_pub_versioned.cil': b'', 'ODM/etc/selinux/odm_sepolicy.cil': b''}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        ids = ('wlan-factory-mac-service', 'wlan-factory-mac-firmware-dir', 'wlan-factory-mac-label',
               'wlan-factory-mac-driver-config', 'wlan-factory-mac-readers', 'wlan-factory-mac-writers',
               'wlan-factory-mac-creators')
        for rule_id in ids:
            self.assertTrue(check(rule_id, good), rule_id)
        for rule_id, change in [
                ('wlan-factory-mac-service', {rc: good[rc].replace(b'exec_start vendor.imeiprovd-wlan',
                                                                   b'start vendor.imeiprovd-wlan')}),
                ('wlan-factory-mac-service', {rc: good[rc].replace(b'on post-fs\n', b'on boot\n')}),
                ('wlan-factory-mac-service', {rc: good[rc].replace(b'qca6750 0700', b'qca6750 0777')}),
                ('wlan-factory-mac-firmware-dir', {ueventd: b'firmware_directories /vendor/firmware_mnt/image/\n'}),
                ('wlan-factory-mac-label', {fc: b'/mnt/vendor(/.*)?   u:object_r:mnt_vendor_file:s0\n'}),
                ('wlan-factory-mac-driver-config', {ini: b'read_mac_addr_from_mac_file=0\n'}),
                ('wlan-factory-mac-readers', {vendor: vendor_cil + b'(allow rild file_type (file (read)))\n'}),
                ('wlan-factory-mac-readers', {vendor: vendor_cil + b'(allow diamaneos_imeiprov '
                                              b'vendor_diamaneos_wlan_mac_file (file (read)))\n'}),
                ('wlan-factory-mac-writers', {vendor: vendor_cil + b'(allow ueventd vendor_diamaneos_wlan_mac_file '
                                              b'(file (write)))\n'}),
                ('wlan-factory-mac-creators', {vendor: vendor_cil + b'(allow rild vendor_diamaneos_wlan_mac_file '
                                               b'(file (create)))\n'}),
                ('wlan-factory-mac-creators', {vendor: vendor_cil.replace(b'(type vendor_diamaneos_wlan_mac_file)\n', b'')})]:
            with self.subTest(rule=rule_id, change=sorted(change)):
                members = {k: v for k, v in {**good, **change}.items() if v is not None}
                self.assertFalse(check(rule_id, members))

    def test_wlan_mac_log_levels_on_user_builds(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        product, vendor = 'PRODUCT/etc/build.prop', 'VENDOR/build.prop'
        good = {product: b'ro.product.name=FP6\nlog.tag.netd=W\n', vendor: b'log.tag.wpa_supplicant=I\n'}
        check = lambda rule_id, members: subject.RULES[rules[rule_id]['type']](rules[rule_id], self.harness(members))[0]
        for rule_id in ('netd-calls-not-logged-user', 'supplicant-debug-not-logged-user'):
            self.assertEqual(rules[rule_id]['variants'], ['user'])
            self.assertTrue(check(rule_id, good), rule_id)
        self.assertFalse(check('netd-calls-not-logged-user', {**good, product: b'log.tag.netd=I\n'}))
        self.assertFalse(check('netd-calls-not-logged-user', {**good, product: b'log.tag.netd=W\nlog.tag.netd=D\n'}))
        self.assertFalse(check('supplicant-debug-not-logged-user', {**good, vendor: b'ro.vendor.x=1\n'}))
        self.assertFalse(subject.rule_applies(rules['netd-calls-not-logged-user'], 'userdebug', False))

    def test_wlan_driver_own_mac_not_logged(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        rule, ko = rules['wlan-driver-own-mac-not-logged'], 'VENDOR_DLKM/lib/modules/qca_cld3_qca6750.ko'
        check = lambda data: subject.RULES[rule['type']](rule, self.harness({ko: data}))[0]
        self.assertTrue(check(b'\x7fELF..%s: %d: txrx_peer NULL!! peer_id %u\x00..'))
        self.assertFalse(check(b'\x7fELF..%s: %d: txrx_peer NULL!! peer mac_addr(%02x:%02x:%02x:**:**:%02x)\x00'))
        self.assertFalse(check(b'\x7fELF..no such line\x00'))

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

    def test_usb_controller_takes_no_eud_extcon(self):
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        usb = [r for r in rules if r['id'] in ('usb-events-from-ucsi', 'usb-events-from-ucsi-volcano')]
        self.assertEqual(['kernel:dtbs/fp6.dtb', 'kernel:dtbs/volcano.dtb'], [r['file'] for r in usb])
        tree = {'/': {}, '/soc': {}, '/soc/ssusb@a600000': {'usb-role-switch': b''},
                '/soc/qcom,pmic_glink': {}, '/soc/qcom,pmic_glink/qcom,ucsi': {},
                '/soc/qcom,pmic_glink/qcom,ucsi/connector': {},
                '/soc/qcom,pmic_glink/qcom,ucsi/connector/port': {},
                '/soc/qcom,pmic_glink/qcom,ucsi/connector/port/endpoint': {'remote-endpoint': cells(0x54)},
                '/soc/qcom,msm-eud@88e0000': {'status': b'disabled\0'}}
        (self.root / 'dtbs').mkdir()
        v = self.harness({}, kernel=self.root)
        for rule in usb:
            name = rule['file'].removeprefix('kernel:')
            (self.root / name).write_bytes(dtb(tree))
            self.assertEqual((True, ''), subject.rule_devicetree(rule, v))
        stock = copy.deepcopy(tree)
        stock['/soc/ssusb@a600000']['extcon'] = cells(0x2f0)
        stock['/soc/qcom,msm-eud@88e0000']['status'] = b'ok\0'
        (self.root / 'dtbs/fp6.dtb').write_bytes(dtb(stock))
        ok, detail = subject.rule_devicetree(usb[0], v)
        self.assertFalse(ok)
        self.assertIn('/soc/ssusb@a600000 still has extcon', detail)
        self.assertIn('status', detail)
        del tree['/soc/ssusb@a600000']['usb-role-switch']
        (self.root / 'dtbs/volcano.dtb').write_bytes(dtb(tree))
        self.assertIn('lacks usb-role-switch', subject.rule_devicetree(usb[1], v)[1])

    def test_download_mode_module_ignores_edl_reboots(self):
        rules = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
        rule = next(r for r in rules if r['id'] == 'download-reboot-refused')
        (self.root / 'modules').mkdir()
        v = self.harness({}, kernel=self.root)
        module = self.root / 'modules/qcom-dload-mode.ko'
        module.write_bytes(b'\x7fELF\0qcom,dload-mode\0full\0mini\0')
        self.assertEqual((True, ''), subject.rule_binary_count(rule, v))
        module.write_bytes(b'\x7fELF\0qcom,dload-mode\0edl\0qcom_dload\0Regulator enable failed(rc:%d)\n\0')
        ok, detail = subject.rule_binary_count(rule, v)
        self.assertFalse(ok)
        self.assertIn('edl', detail)
        self.assertIn('Regulator enable failed', detail)

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

    def test_audio_cfi_rules(self):
        rules = [r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']
                 if r['id'].startswith('audio-') and r['id'].endswith('-cfi')]
        self.assertEqual(11, len(rules))
        cfi = '   7: 0000000000004000    64 FUNC    GLOBAL DEFAULT   12 __cfi_check\n'
        plain = '   7: 0000000000004000    64 FUNC    GLOBAL DEFAULT   12 HMI\n'
        for rule in rules:
            for dump, expected in [(cfi, True), (plain, False)]:
                v = self.harness({rule['path']: b'elf'}, tools=FakeTools({('llvm-readelf',): dump}))
                self.assertEqual(expected, subject.rule_elf_exports(rule, v)[0], rule['id'])

    def test_overlay_array_membership(self):
        dump = ('Package name=x\n'
                '    resource 0x7f030000 array/config_highRefreshRateBlacklist\n'
                '      () (no config)\n'
                '        (string8) "app.grapheneos.camera"\n'
                '        (string8) "com.example.other"\n'
                '    resource 0x7f040000 string/decoy\n'
                '      () "app.grapheneos.absent"\n')
        self.assertEqual(subject.aapt2_array(dump, 'array/config_highRefreshRateBlacklist'),
                         ['app.grapheneos.camera', 'com.example.other'])
        v = self.harness({'VENDOR/overlay/o.apk': b'apk'},
                         tools=FakeTools({('aapt2', 'resources'): dump}))
        rule = lambda items: subject.rule_overlay(
            {'apk': 'VENDOR/overlay/o.apk',
             'arrays': {'array/config_highRefreshRateBlacklist': items}}, v)[0]
        self.assertTrue(rule(['app.grapheneos.camera']))
        # A value that only appears under a different resource is not a match.
        self.assertFalse(rule(['app.grapheneos.absent']))

    def test_overlay_integer_and_multi_item_arrays(self):
        dump = ('Package name=x\n'
                '    resource 0x7f010000 array/non_removable_euicc_slots\n'
                '      () (array) size=1\n'
                '        [1]\n'
                '    resource 0x7f010001 array/config_autoBrightnessLevels\n'
                '      () (array) size=6\n'
                '        [2, 4, 10, 20, \n'
                '         40, -1]\n'
                '    resource 0x7f010002 array/config_telephonyEuiccDeviceCapabilities\n'
                '      () (array) size=3\n'
                '        ["gsm,9", "utran,10", \n'
                '         "eutran,16"]\n')
        self.assertEqual(['1'], subject.aapt2_array(dump, 'array/non_removable_euicc_slots'))
        self.assertEqual(['2', '4', '10', '20', '40', '-1'],
                         subject.aapt2_array(dump, 'array/config_autoBrightnessLevels'))
        self.assertEqual(['gsm,9', 'utran,10', 'eutran,16'],
                         subject.aapt2_array(dump, 'array/config_telephonyEuiccDeviceCapabilities'))

    def test_apk_manifest_patterns(self):
        def tree(app, service):
            return ('N: android=http://schemas.android.com/apk/res/android (line=2)\n'
                    '  E: manifest (line=2)\n'
                    '      E: application (line=22)\n'
                    '        A: http://schemas.android.com/apk/res/android:label(0x01010001)=@0x7f0b0000\n'
                    f'        A: http://schemas.android.com/apk/res/android:enabled(0x0101000e)={app}\n'
                    '          E: service (line=30)\n'
                    f'            A: http://schemas.android.com/apk/res/android:enabled(0x0101000e)={service}\n')
        disabled = (r'^\s*E: application \(line=\d+\)\n(?:\s*A: .*\n)*?'
                    r'\s*A: http://schemas\.android\.com/apk/res/android:enabled\(0x0101000e\)=false$')
        rule = {'path': 'SYSTEM_EXT/priv-app/A/A.apk', 'permissions': ['android.permission.X'],
                'manifest_regex': [disabled]}
        for manifest, expected in ((tree('false', 'true'), True),
                                   # A disabled component does not count for the application.
                                   (tree('true', 'false'), False)):
            tools = FakeTools({('aapt2', 'permissions'): "uses-permission: name='android.permission.X'\n",
                               ('aapt2', 'xmltree'): manifest})
            v = self.harness({'SYSTEM_EXT/priv-app/A/A.apk': b'apk'}, tools=tools)
            ok, detail = subject.rule_apk(rule, v)
            self.assertEqual(expected, ok, detail)

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

    def test_avb_hashtree_algorithms(self):
        def tree(name, algorithm):
            return ('    Hashtree descriptor:\n      Version of dm-verity:  1\n      Image Size:            4096 bytes\n'
                    f'      Hash Algorithm:        {algorithm}\n      Partition Name:        {name}\n'
                    '      Salt:                  00\n      Root Digest:           00\n      Flags:                 0\n')
        info = ('Descriptors:\n    Chain Partition descriptor:\n      Partition Name:          boot\n'
                '      Rollback Index Location: 3\n      Flags:                   0\n'
                "    Prop: com.android.build.vendor.os_version -> '17'\n"
                '    Hash descriptor:\n      Hash Algorithm:        sha256\n      Partition Name:        dtbo\n'
                + tree('vendor', 'sha1') + tree('odm', 'sha256'))
        self.assertEqual({'vendor': 'sha1', 'odm': 'sha256'}, subject.avb_hashtrees(info))

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
        self.assertEqual(raw[:block + 10], subject.sparse_to_raw(image, limit=block + 10))
        self.assertEqual(raw[:3 * block + 6], subject.sparse_to_raw(image, limit=3 * block + 6))

    def test_sparse_expansion_stops_at_the_limit_and_checks_chunk_sizes(self):
        import tracemalloc
        block = 4096
        header = lambda chunks: struct.pack('<IHHHHIIII', subject.SPARSE_MAGIC, 1, 0, 28, 12, block,
                                            16384, chunks, 0)
        # 40 bytes that declare 64 MiB (any size would do; this one stays safe to run if the
        # bound breaks): a header check reads 4096 bytes of it.
        for chunk in (struct.pack('<HHII', 0xCAC3, 0, 16384, 12),
                      struct.pack('<HHII', 0xCAC2, 0, 16384, 16) + b'FILL'):
            image = header(1) + chunk
            tracemalloc.start()
            try:
                raw = subject.sparse_to_raw(image, limit=4096)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertEqual(4096, len(raw))
            self.assertLess(peak, 1024 * 1024)
        bad = {'zero block size': struct.pack('<IHHHHIIII', subject.SPARSE_MAGIC, 1, 0, 28, 12, 0, 1, 1, 0)
                                  + struct.pack('<HHII', 0xCAC3, 0, 1, 12),
               'truncated chunk': header(1) + struct.pack('<HHII', 0xCAC1, 0, 1, 12 + block) + b'A' * 10,
               'raw size': header(1) + struct.pack('<HHII', 0xCAC1, 0, 2, 12 + block) + b'A' * block,
               'fill size': header(1) + struct.pack('<HHII', 0xCAC2, 0, 1, 20) + b'FILLFILL',
               'unknown chunk': header(1) + struct.pack('<HHII', 0xCAC9, 0, 1, 12),
               'missing chunk': header(2) + struct.pack('<HHII', 0xCAC3, 0, 1, 12)}
        for name, image in bad.items():
            with self.subTest(name), self.assertRaises((ValueError, struct.error)):
                subject.sparse_to_raw(image, limit=2 * block)

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


    def test_avb_check_requires_sha256_hashtrees(self):
        def tree(name, algorithm):
            return (f'    Hashtree descriptor:\n      Hash Algorithm:        {algorithm}\n'
                    f'      Partition Name:        {name}\n')
        chains = ''.join(f'    Chain Partition descriptor:\n      Partition Name:          {name}\n'
                         f'      Rollback Index Location: {location}\n      Flags:                   0\n'
                         for name, location in (('recovery', 1), ('vbmeta_system', 2), ('boot', 3), ('init_boot', 4)))
        system = ['system', 'system_ext', 'product']

        def outputs(algorithm, vendor=('vendor', 'odm', 'vendor_dlkm', 'system_dlkm')):
            def avbtool(args):
                if args[0] == 'verify_image':
                    return ''
                if str(args[-1]).endswith('vbmeta_system.img'):
                    return ('    Hash descriptor:\n      Partition Name:        pvmfw\n'
                            + ''.join(tree(n, algorithm.get(n, 'sha256')) for n in system))
                return chains + ''.join(tree(n, algorithm.get(n, 'sha256')) for n in vendor)
            return FakeTools({('avbtool',): avbtool})

        def check(tools):
            v = self.harness({}, tools=tools)
            v.src = self.root
            return subject.check_avb(v)
        self.assertEqual((True, ''), check(outputs({})))
        ok, detail = check(outputs({'vendor': 'sha1'}))
        self.assertFalse(ok)
        self.assertIn('vendor (sha1)', detail)
        ok, detail = check(outputs({}, vendor=('vendor', 'odm', 'vendor_dlkm')))
        self.assertFalse(ok)
        self.assertIn('hashtrees for', detail)

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

    def test_firmware_release_table_matches_the_inventory(self):
        from diamaneos_tools import firmware_release
        inventory = json.loads((ROOT / 'config/fp6-firmware-inventory.json').read_text())
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        table = firmware_release.table(inventory, config['firmware_release'])
        stale = table.replace(b' 16.111.0 ', b' 16.110.0 ')
        (self.root / 'vendor.img').write_bytes(b'not sparse')
        image = {'table': table}

        def debugfs(args):
            command = str(args[1])
            if command.startswith('dump '):
                self.assertEqual('/etc/diamaneos/firmware-releases.txt', command.split()[1])
                Path(command.split()[2]).write_bytes(image['table'])
            return 'Type: regular\n'

        def check(members):
            v = self.harness(members, tools=FakeTools({('debugfs_static',): debugfs}))
            return subject.check_firmware_release_table(v)
        member = 'VENDOR/etc/diamaneos/firmware-releases.txt'
        self.assertEqual((True, 'releases 16.100.0, 16.111.0'), check({member: table}))
        ok, detail = check({member: stale})
        self.assertFalse(ok)
        self.assertIn(member + ' differs', detail)
        image['table'] = stale
        ok, detail = check({member: table})
        self.assertFalse(ok)
        self.assertEqual('vendor.img differs from the inventory', detail)
        image['table'] = table
        with self.assertRaises(FileNotFoundError):
            check({})
        self.assertIn('firmware-release-table', [check_id for check_id, _, _ in subject.GENERIC])

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


class FirmwareCheckTests(unittest.TestCase):
    def setUp(self):
        from diamaneos_tools import firmware
        from tests.build.test_firmware import RELEASE, synthetic_firmware
        self.firmware = firmware
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'stock').mkdir()
        self.inventory, self.policy, archive, self.data = synthetic_firmware(self.root / 'stock')
        self.plan = firmware.plan(self.inventory, self.policy)
        firmware.stage(archive, self.inventory, self.plan, self.root)
        reset = {}
        for name, image in self.policy['reset'].items():
            (self.root / image['image']).write_bytes(bytes(image['bytes']))
            reset[name] = {'file': image['image'], 'sha256': hashlib.sha256(bytes(image['bytes'])).hexdigest()}
        self.record = {'stock_build': RELEASE, 'firmware': dict(firmware.record(self.plan, self.inventory, reset),
                                                                validated=False)}

    def harness(self, members):
        v = Harness(self.root, members)
        self.addCleanup(v.tf.close)
        v.record, v.firmware_inventory = self.record, self.inventory
        v.config['firmware'] = self.policy
        return v

    def test_the_set_carries_the_complete_exact_firmware(self):
        v = self.harness({})
        self.assertEqual((True, ''), subject.check_firmware(v))
        (self.root / 'NON-HLOS.bin').write_bytes(b'modem from elsewhere')
        ok, detail = subject.check_firmware(v)
        self.assertFalse(ok)
        self.assertIn('NON-HLOS.bin differs from the inventory', detail)
        v.record = {'stock_build': self.record['stock_build']}
        self.assertEqual((False, 'the image set carries no firmware'), subject.check_firmware(v))

    def test_an_ota_carries_all_ab_firmware_or_none(self):
        os_only = b'boot\nsystem\nvendor\npvmfw\n'
        ok, detail = subject.check_firmware_ota(self.harness({'META/ab_partitions.txt': os_only}))
        self.assertTrue(ok)
        self.assertIn('names no firmware partition', detail)
        radio = {'RADIO/abl.img': self.data['abl.elf'], 'RADIO/modem.img': self.data['NON-HLOS.bin'],
                 'RADIO/studybk.img': self.data['study.img'], 'IMAGES/xbl.img': self.data['xbl_s.melf']}
        listed = os_only + b'abl\nmodem\nstudybk\nxbl\n'
        self.assertEqual((True, ''), subject.check_firmware_ota(self.harness({'META/ab_partitions.txt': listed, **radio})))
        ok, detail = subject.check_firmware_ota(self.harness({'META/ab_partitions.txt': os_only + b'modem\n', **radio}))
        self.assertFalse(ok)
        self.assertIn('missing: abl, studybk, xbl', detail)
        changed = dict(radio, **{'RADIO/modem.img': b'other modem'})
        ok, detail = subject.check_firmware_ota(self.harness({'META/ab_partitions.txt': listed, **changed}))
        self.assertFalse(ok)
        self.assertIn('RADIO/modem.img is not the stock NON-HLOS.bin', detail)
        del changed['RADIO/modem.img']
        ok, detail = subject.check_firmware_ota(self.harness({'META/ab_partitions.txt': listed, **changed}))
        self.assertIn('lacks the modem image', detail)

    def test_both_checks_run_on_every_image_set(self):
        ids = [check_id for check_id, _, _ in subject.GENERIC]
        self.assertIn('firmware', ids)
        self.assertIn('firmware-ota', ids)


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

    def test_rules_can_check_official_builds_only_or_the_others_only(self):
        self.assertTrue(subject.rule_applies({}, 'user', True))
        self.assertTrue(subject.rule_applies({'official': True}, 'user', True))
        self.assertFalse(subject.rule_applies({'official': True}, 'user', False))
        self.assertTrue(subject.rule_applies({'official': False}, 'userdebug', False))
        self.assertFalse(subject.rule_applies({'official': False}, 'userdebug', True))
        self.assertFalse(subject.rule_applies({'official': True, 'variants': ['userdebug']}, 'user', True))
        document = json.loads((ROOT / 'config/fp6-image-checks.json').read_text())
        broken = copy.deepcopy(document)
        broken['rules'][0]['official'] = 'yes'
        self.assertRaises(ValueError, subject.validate_rules, broken)

    def test_official_builds_carry_the_updater_and_others_do_not(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        self.assertEqual({'no-updater': False, 'updater': True, 'updater-server': True, 'updater-permissions': True},
                         {i: rules[i]['official'] for i in ('no-updater', 'updater', 'updater-server',
                                                            'updater-permissions')})
        self.assertEqual(['SYSTEM/priv-app/Updater'], rules['no-updater']['paths'])
        # The Updater asks the host the endpoint contract names.
        endpoints = json.loads((ROOT / 'config/endpoints.json').read_text())['endpoints']
        host = next(e['replacement_host'] for e in endpoints if e['id'] == 'os-updates')
        self.assertEqual(f'"https://{host}/"', rules['updater-server']['values']['string/url'])

    def test_official_builds_need_the_camera_filter_in_trap_mode(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        camera, bluetooth = rules['camera-provider-seccomp-enforced'], rules['bluetooth-seccomp-enforced']
        self.assertIs(True, camera['official'])
        self.assertEqual('VENDOR/lib64/libcamxjail.so', camera['file'])
        self.assertEqual(bluetooth['patterns'], camera['patterns'])
        self.assertFalse(subject.rule_applies(camera, 'user', False))
        self.assertTrue(subject.rule_applies(camera, 'user', True))
        with tempfile.TemporaryDirectory() as temp:
            for mode, ok in ((b'log-only', False), (b'trap', True)):
                v = Harness(Path(temp), {camera['file']: b'\x7fELF filter installed (%s)\x00' + mode + b'\x00'})
                self.addCleanup(v.tf.close)
                self.assertEqual(ok, subject.rule_binary_count(camera, v)[0], mode)

    def test_esim_manager_ships_off_with_network_only_and_unplatform_signed(self):
        rules = {r['id']: r for r in json.loads((ROOT / 'config/fp6-image-checks.json').read_text())['rules']}
        apk = rules['esim-lpa-apk']
        # Network for downloads; no camera (QR codes come from the camera app as text).
        self.assertIn('android.permission.INTERNET', apk['permissions'])
        self.assertNotIn('android.permission.CAMERA', apk['permissions'])
        self.assertEqual(4, len(apk['permissions']))
        self.assertIn('android.permission.WRITE_EMBEDDED_SUBSCRIPTIONS', apk['permissions'])
        self.assertEqual(['log.tag.ApduSender-0=I', 'log.tag.ApduSender-1=I', 'log.tag.TransApdu=I'],
                         sorted(rules['esim-apdu-not-logged']['equals']))
        self.assertTrue(any('enabled' in pattern and '=false' in pattern for pattern in apk['manifest_regex']))
        self.assertEqual('SYSTEM/framework/framework-res.apk', apk['not_signed_like'])
        self.assertEqual({'array/non_removable_euicc_slots': ['1']}, rules['esim-builtin-slot']['arrays'])

    def test_record_official_must_be_true_or_false(self):
        root = Path(tempfile.mkdtemp()); self.addCleanup(lambda: __import__('shutil').rmtree(root))
        (root / 'target-files.zip').write_bytes(b'tf')
        digest = hashlib.sha256(b'tf').hexdigest()
        (root / 'SHA256SUMS').write_text(f'{digest}  target-files.zip\n')
        record = {'release': False, 'signing': 'public-test-keys', 'never_lock': True, 'images': {},
                  'target_files': {'file': 'target-files.zip', 'sha256': digest}}
        class V:
            images = root
        for official, ok in ((None, True), (True, True), (False, True), ('true', False)):
            with self.subTest(official=official):
                V.record = dict(record, **({} if official is None else {'official': official}))
                self.assertEqual(ok, subject.check_record(V)[0])

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

    def test_a_verify_step_is_current_only_while_its_report_shows_a_pass(self):
        from tests.build.test_image_package import fixture_workspace, run_plan
        from diamaneos_tools import image_package
        with tempfile.TemporaryDirectory() as temp:
            ctx = fixture_workspace(Path(temp))
            package = run_plan(ctx, image_package.plan(ctx))
            ctx.workspace.write_state('package', {'status': 'PASS', 'inputs_sha256': 'p', 'outputs': package})
            checks = [{'id': check, 'status': 'PASS'} for check in subject.REQUIRED_CHECKS]
            report = {'schema_version': 1, 'build_id': package['build_id'], 'sums_sha256': package['sums_sha256'],
                      'status': 'PASS', 'checked': len(checks), 'failed': 0, 'checks': checks}
            path = ctx.workspace.images / (package['build_id'] + '.verify.json')
            previous = {'outputs': {'report': str(path.relative_to(ctx.workspace.root))}}
            plan = subject.plan(ctx)
            path.write_text(json.dumps(report))
            self.assertTrue(plan.valid(previous))
            path.write_text(json.dumps(dict(report, status='FAIL', checks=[], checked=0)))
            self.assertFalse(plan.valid(previous))
            path.write_text('not json')
            self.assertFalse(plan.valid(previous))

    def test_newest_jdk_is_chosen_numerically(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            for name in ('jdk8', 'jdk21', 'jdk11'):
                (src / 'prebuilts/jdk' / name / 'linux-x86/bin').mkdir(parents=True)
            env = subject.Tools(src / 'out/host', src, src / 'work').environment()
            self.assertTrue(env['JAVA_HOME'].endswith('jdk21/linux-x86'))


if __name__ == '__main__':
    unittest.main()
