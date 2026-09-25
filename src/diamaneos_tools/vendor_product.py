"""Materialize the reviewed FP6 native Android integration from authenticated files."""
import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from . import components, vendor_files
from .vendor import VendorError, encoded

ROOT = Path(__file__).resolve().parents[2]
SOURCE_INTERFACES = {
    'libdrm',
    'libkeymaster_messages',
    'android.hardware.gatekeeper-V1-ndk',
    'android.hardware.graphics.allocator-V1-ndk',
    'android.hardware.graphics.composer3-V2-ndk',
    'android.hardware.keymaster@3.0',
    'android.hardware.keymaster@4.0',
    'android.hardware.keymaster@4.1',
    'android.hardware.security.keymint-V3-ndk',
    'android.hardware.security.rkp-V3-ndk',
    'android.hardware.security.secureclock-V1-ndk',
    'android.hardware.security.sharedsecret-V1-ndk',
    # Qualcomm display stack built from source (OP-DISPLAY-HAL-SOURCE). The stock
    # Android 14 composer cannot present under Android 17's SurfaceFlinger.
    'libdisplayconfig.qti',
    'libdisplaydebug',
    'libdrmutils',
    'libgpu_tonemapper',
    'libgralloc.qti',
    'libgralloccore',
    'libgrallocutils',
    'libhistogram',
    'libqservice',
    'libsdedrm',
    'libsdmcore',
    'libsdmdal',
    'libsdmutils',
    'libvmmem',
    'vendor.display.config@2.0',
    'vendor.qti.hardware.display.color-V1-ndk',
    'vendor.qti.hardware.display.composer3-V1-ndk',
    'vendor.qti.hardware.display.config-V5-ndk',
    'vendor.qti.hardware.display.config-V7-ndk',
    'vendor.qti.hardware.display.config-V11-ndk',
    'vendor.qti.hardware.display.demura-V1-ndk',
    'vendor.qti.hardware.display.mapper@2.0',
    'vendor.qti.hardware.display.mapper@3.0',
    'vendor.qti.hardware.display.mapper@4.0',
    'vendor.qti.hardware.display.mapperextensions@1.0',
    'vendor.qti.hardware.display.mapperextensions@1.1',
    'vendor.qti.hardware.display.mapperextensions@1.2',
    'vendor.qti.hardware.display.mapperextensions@1.3',
    'vendor.qti.hardware.display.postproc-V1-ndk',
    # Sensors interfaces used by the stock Qualcomm sub-HAL, built from source
    # with the AOSP sensors multi-HAL (frozen HIDL/AIDL interfaces).
    'android.hardware.sensors@1.0',
    'android.hardware.sensors@2.0',
    'android.hardware.sensors@2.0-ScopedWakelock',
    'android.hardware.sensors@2.1',
    'android.hardware.sensors-V2-ndk',
    # AOSP libraries the stock audio HAL, PAL and AGM link, built from source
    # with the AOSP audio service and HIDL wrappers (device audio.mk).
    'android.hidl.allocator@1.0',
    'libhidltransport',
    'libtinycompress',
    'android.hardware.gnss-V3-ndk',
    'android.hardware.health-V1-ndk',
    'android.hardware.health@1.0',
    'android.hardware.health@2.0',
    'android.hardware.health@2.1',
    # AOSP radio, secure-element and netd interfaces linked by the stock radio
    # daemon, its data module and nicmd; built from source (vendor variants).
    'android.hardware.radio-V2-ndk',
    'android.hardware.radio.config-V2-ndk',
    'android.hardware.radio.data-V2-ndk',
    'android.hardware.radio.messaging-V2-ndk',
    'android.hardware.radio.modem-V2-ndk',
    'android.hardware.radio.network-V2-ndk',
    'android.hardware.radio.sap-V1-ndk',
    'android.hardware.radio.sim-V2-ndk',
    'android.hardware.radio.voice-V2-ndk',
    'android.hardware.radio@1.0',
    'android.hardware.radio@1.1',
    'android.hardware.radio@1.2',
    'android.hardware.radio@1.3',
    'android.hardware.radio@1.4',
    'android.hardware.radio@1.5',
    'android.hardware.radio@1.6',
    'android.hardware.secure_element-V1-ndk',
    'android.system.net.netd-V1-ndk',
    # AOSP seccomp helper through which qcrilNrd applies its stock policy.
    'libavservices_minijail',
    # Frozen AOSP Bluetooth HCI HIDL interfaces linked by the stock Qualcomm
    # Bluetooth service and HCI implementation (device bluetooth/bluetooth.mk).
    'android.hardware.bluetooth@1.0',
    'android.hardware.bluetooth@1.1',
    # AOSP NFC interfaces the stock Samsung NFC HAL and its implementation
    # (nfc_nci_sec.so) link, built from source (frozen AIDL V1 and HIDL 1.0-1.2).
    'android.hardware.nfc-V1-ndk',
    'android.hardware.nfc@1.0',
    'android.hardware.nfc@1.1',
    'android.hardware.nfc@1.2',
}
# Stable AIDL libraries a selected blob links that are built from the pinned
# Android tree instead of taken from the factory image (no stock row exists).
SOURCE_MODULE_DEPENDENCIES = {
    # Camera (OP-HW-BRINGUP camera): AOSP camera AIDL interfaces, the HIDL
    # shims and the Qualcomm interface libraries the stock CamX/CHI link.
    'android.hardware.camera.common-V1-ndk',
    'android.hardware.camera.device-V2-ndk',
    'android.hardware.camera.metadata-V2-ndk',
    'android.hardware.camera.provider-V2-ndk',
    'libhidltransport',
    'libhwbinder',
    'vendor.qti.hardware.camera.offlinecamera-V1-ndk',
    'vendor.qti.hardware.camera.postproc@1.0',
    'vendor.qti.hardware.display.allocator@4.0',
    'vendor.qti.hardware.display.config-V2-ndk',
}
ACTIVATION={
 'android.hardware.gatekeeper-service-qti':('android.hardware.gatekeeper-service-qti.rc',None),
 'android.hardware.security.keymint-service-qti':('android.hardware.security.keymint-service-qti.rc','android.hardware.security.keymint-service-qti.xml'),
 'vendor.qti.hardware.display.color-service':('vendor.qti.hardware.display.color-service.rc',None),
 'vendor.qti.hardware.memtrack-service':('memtrack_qti.rc','memtrack_qti.xml'),
 'vendor.qti.hardware.qseecom@1.0-service':('vendor.qti.hardware.qseecom@1.0-service.rc','vendor.qti.hardware.qseecom@1.0-service.xml'),
 'qseecomd':('qseecomd.rc',None),
 'thermal-engine-v2':('init_thermal-engine-v2.rc',None),
 'vendor.qti.hardware.perf2-hal-service':('vendor.qti.hardware.perf2-hal-service.rc','vendor.qti.hardware.perf2.xml'),
 'rmt_storage':('vendor.qti.rmt_storage.rc',None),
 'tftp_server':('vendor.qti.tftp.rc',None),
 # Stock starts these from its board-wide init.target.rc and init.qti.kernel.rc,
 # which are not selected; the FP6 device init.qcom.rc defines their services.
 'pd-mapper':(None,None),
 'pm-service':(None,None),
 'pm-proxy':(None,None),
 # ssr_setup enables subsystem restart (recovery) for the modem and DSPs from
 # persist.vendor.ssr.restart_level; stock starts it from init.qcom.rc, which
 # is not selected. The FP6 device modem/init.modem.rc defines the service.
 'ssr_setup':(None,None),
 # sscrpcd starts the sensors protection domain on the ADSP; the sensors
 # multi-HAL itself is built from source (device.mk).
 'sscrpcd':('vendor.sensors.sscrpcd.rc',None),
 # audioadsprpcd starts the audio protection domain on the ADSP.
 'audioadsprpcd':('vendor.qti.audio-adsprpc-service.rc',None),
 # Stock GNSS HAL (IGnss v3); its rc is a pinned derivation (GNSS_CONFIG_REWRITES).
 # vendor.qti.gnss-service.xml (ILocAidlGnss) is not installed.
 'android.hardware.gnss-aidl-service-qti':('android.hardware.gnss-aidl-service-qti.rc','android.hardware.gnss-aidl-service-qti.xml'),
 # The stock CamX/CHI camera provider (AIDL ICameraProvider/vendor_qti/0).
 'vendor.qti.camera.provider-service_64':('vendor.qti.camera.provider-service_64.rc','vendor.qti.camera.provider.xml'),
 # The QCRIL radio daemon declares only the services r9p uses: the AOSP radio
 # HAL and the Qualcomm IMS, radio-config, audio-messenger and LPA services.
 'qcrilNrd':('qcrilNrd.rc',('android.hardware.radio.config.xml','android.hardware.radio.data.xml',
                            'android.hardware.radio.messaging.xml','android.hardware.radio.modem.xml',
                            'android.hardware.radio.network.xml','android.hardware.radio.sim.xml',
                            'android.hardware.radio.voice.xml','vendor.qti.hardware.radio.ims.xml',
                            'vendor.qti.hardware.radio.qtiradioconfig.xml','vendor.qti.hardware.radio.am.xml',
                            'vendor.qti.hardware.radio.lpa.xml')),
 # nicmd configures the rmnet data interfaces for modem data calls.
 'nicmd':('nicmd.rc',None),
 # The Bluetooth HCI service: the device bluetooth/init.fp6.bluetooth.rc starts
 # it without the stock diag and ssgtzd groups, and the device manifest.xml
 # declares android.hardware.bluetooth@1.1::IBluetoothHci/default.
 'android.hardware.bluetooth@1.1-service-qti':(None,None),
 # Stock Samsung S3NRN4V NFC HAL (AIDL INfc/default); its rc also sets the
 # /dev/sec-nfc owner at boot.
 'android.hardware.nfc-service.sec':('nfc-service-sec.rc','nfc-service-sec.xml'),
}

# Reviewed stock Java components outside vendor (private bring-up). They keep
# the stock signature (presigned); their privileges come only from the device's
# privileged-permission allowlist, never from our platform key.
# path -> (module name = install directory, privileged). Every privileged app
# needs a complete device allowlist entry (grants and denials).
STOCK_APPS = {
 'system_ext/priv-app/ims/ims.apk':('ims', True),
 # Privileged so that its shared UID android.uid.qtiphone is privileged: a
 # non-privileged package can then not join it (InstallPackageHelper
 # assertPackageWithSharedUserIdIsPrivileged).
 'system_ext/app/QtiTelephonyService/QtiTelephonyService.apk':('QtiTelephonyService', True),
 # eSIM LPA; the device disables its services by default (sysconfig).
 'product/app/uimlpaservice/uimlpaservice.apk':('uimlpaservice', True),
}
# JNI libraries of the stock apps and the platform libraries they link.
STOCK_JNI = {
 'system_ext/lib64/libimscamera_jni.so':['libc++', 'libc', 'libcutils', 'libdl', 'liblog', 'libm', 'libnativehelper', 'libutils'],
 'system_ext/lib64/libimsmedia_jni.so':['libandroid', 'libbinder', 'libc++', 'libc', 'libcutils', 'libdl', 'libgui', 'liblog', 'libm', 'libnativehelper', 'libutils'],
}
# Data copied as is: shared-library jars (not on the boot class path, not
# preopted) and the permission XMLs that declare them. Reviewed one by one:
# SystemConfig gives system_ext and product permission XMLs full rights, so a
# selected XML may only map its library name to the jar listed here.
STOCK_LIBRARIES = {
 'system_ext/etc/permissions/qti_telephony_hidl_wrapper.xml':'system_ext/framework/qti-telephony-hidl-wrapper.jar',
 'system_ext/etc/permissions/qti_telephony_utils.xml':'system_ext/framework/qti-telephony-utils.jar',
 'product/etc/permissions/ims_ext_common.xml':'product/framework/ims-ext-common.jar',
 'product/etc/permissions/lpa.xml':'product/framework/uimlpalibrary.jar',
 'product/etc/permissions/qti_telephony_hidl_wrapper_prd.xml':'product/framework/qti-telephony-hidl-wrapper-prd.jar',
 'system_ext/etc/permissions/extphonelib.xml':'system_ext/framework/extphonelib.jar',
}
STOCK_DATA = set(STOCK_LIBRARIES) | set(STOCK_LIBRARIES.values())
PARTITIONS = {'system_ext': ('system_ext_specific', '$(TARGET_COPY_OUT_SYSTEM_EXT)'),
              'product': ('product_specific', '$(TARGET_COPY_OUT_PRODUCT)')}


def notice_license(partition):
    return 'fp6_selected_stock_notices' + ('' if partition == 'vendor' else '_' + partition)


def module(path):
    return 'fp6_stock_' + path.replace('/', '_').removesuffix('.so')


def blueprint(kind, properties):
    def value(item):
        if isinstance(item, dict):
            return '{ ' + ', '.join(k + ': ' + value(v) for k, v in item.items()) + ', }'
        return json.dumps(item)
    return kind + ' {\n' + ''.join('    ' + k + ': ' + value(v) + ',\n'
                                  for k, v in properties.items()) + '}\n\n'


# Passthrough HAL libraries whose VINTF declaration makes them discoverable.
# Without hwservicemanager, HIDL resolves passthrough HALs only through VINTF.
LIBRARY_VINTF = {
 # The CHI override links this library, which registers the offline camera
 # AIDL service inside the camera provider process (stock declaration).
 'vendor.qti.hardware.camera.offlinecamera-service-impl': 'vendor.qti.camera.offlinecamera-impl.xml',
}
# Non-ELF data the stock CamX reads from /vendor/lib64 (sensor modules, tuning,
# flash/face-detection tables, BitML networks). Copied as is; generate()
# rejects ELF content under these paths.
LIB64_DATA = (
    re.compile(r'vendor/lib64/camera/[A-Za-z0-9_.]+\.bin'),
    re.compile(r'vendor/lib64/bm4d73v02s12n[0-9]{2}\.bin'),
)
# Hexagon (DSP) libraries loaded over FastRPC. Installed with Soong
# prebuilt_rfsa into /vendor/lib/rfsa/adsp, which the stock FastRPC loader
# searches (libcdsprpc/libadsprpc path strings); the stock
# /vendor/lib64/rfs/dsp copies move there because no Soong type installs to
# that directory and PRODUCT_COPY_FILES rejects ELF files.
DSP_DIRECTORIES = ('vendor/lib/rfsa/adsp/', 'vendor/lib64/rfs/dsp/')
DSP_MACHINE_MAGIC = b'\x7fELF\x01\x01\x01\x00'  # ELF32 little-endian (QDSP6, e_machine 164)
RUNTIME_EDGE = 'selected-stock-runtime'

# Blobs built against an Android 14 VNDK library whose Android 17 ABI differs.
# The dependency is renamed to a source-built copy with the old ABI (device
# compat module); input and output bytes are pinned.
NEEDED_REWRITES = {
 'vendor/lib64/libsnapdragoncolor-manager.so': {
  'needed': 'libtinyxml2.so', 'replacement': 'libtxml2v34.so', 'module': 'libtxml2v34',
  'source_sha256': 'e66febb064b81332eaf79525f1dd7a3e531dd28ac46e430495e484c71611c52e',
  'sha256': 'd75f85d85ac41a1f79617cc374dc144626f6b7d34e8285be8f484bc433e4e03a'},
}


def rewrite_needed(data, needed, replacement):
    """Rename one DT_NEEDED string in place; refuse if any other reference shares its bytes."""
    import struct
    if len(needed) != len(replacement) or data[:5] != b'\x7fELF\x02' or data[5] != 1:
        raise VendorError('unsupported dependency rewrite')
    shoff, = struct.unpack_from('<Q', data, 0x28)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x3a)
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * shentsize) for i in range(shnum)]
    by_type = {}
    for s in sections:
        by_type.setdefault(s[1], []).append(s)
    dynamic, dynsym = by_type.get(6, []), by_type.get(11, [])
    if len(dynamic) != 1 or len(dynsym) != 1:
        raise VendorError('unsupported dependency rewrite')
    strtab = sections[dynamic[0][6]]
    if strtab is not sections[dynsym[0][6]]:
        raise VendorError('unsupported dependency rewrite')
    base, size = strtab[4], strtab[5]
    references, targets = [], []
    for offset in range(dynamic[0][4], dynamic[0][4] + dynamic[0][5], 16):
        tag, value = struct.unpack_from('<qQ', data, offset)
        if tag in (1, 14, 15, 29):  # NEEDED, SONAME, RPATH, RUNPATH
            references.append(value)
            if tag == 1 and data[base + value:base + value + len(needed) + 1] == needed.encode() + b'\0':
                targets.append(value)
    for offset in range(dynsym[0][4], dynsym[0][4] + dynsym[0][5], 24):
        references.append(struct.unpack_from('<I', data, offset)[0])
    for kind in (0x6ffffffe, 0x6ffffffd):  # verneed, verdef
        for s in by_type.get(kind, []):
            position = s[4]
            for _ in range(s[7]):
                if kind == 0x6ffffffe:
                    _, count, name, first, following = struct.unpack_from('<HHIII', data, position)
                    references.append(name)
                    item = position + first
                    for _ in range(count):
                        _, _, _, name, step = struct.unpack_from('<IHHII', data, item)
                        references.append(name)
                        item += step
                else:
                    _, _, _, count, _, first, following = struct.unpack_from('<HHHHIII', data, position)
                    item = position + first
                    for _ in range(count):
                        name, step = struct.unpack_from('<II', data, item)
                        references.append(name)
                        item += step
                position += following
    if len(targets) != 1:
        raise VendorError('dependency to rewrite is not present exactly once')
    start = targets[0]
    end = start + len(needed)
    if end >= size or any(start < r <= end for r in references) or references.count(start) != 1:
        raise VendorError('dependency string is shared with another reference')
    return data[:base + start] + replacement.encode() + data[base + end:]


def reachable(selection):
    """Keep explicit static/dynamic roots and their transitive ELF and dlopen providers."""
    paths = {r['path'] for r in selection['files']}
    roots = selection['roots']
    if not roots or len(roots) != len(set(roots)) or not set(roots) <= paths:
        raise VendorError('invalid native runtime roots')
    kept, pending = set(), list(roots)
    while pending:
        path = pending.pop()
        if path in kept:
            continue
        kept.add(path)
        pending.extend(e['provider'] for e in selection['edges']
                       if e['consumer'] == path and e['kind'] in ('selected-stock', RUNTIME_EDGE))
    if not kept <= paths:
        raise VendorError('runtime dependency has no selected provider')
    return kept


def render(recipe, selection, notice_kind):
    """Bind every ELF and dependency to the selected recipe before rendering."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', notice_kind):
        raise VendorError('invalid Android notice classification')
    rows = {r['path']: r for r in recipe['files']}
    elfs = {r['path']: r for r in selection['files']}
    if len(elfs) != len(selection['files']) or not elfs:
        raise VendorError('empty or duplicate ELF selection')
    for path, row in elfs.items():
        if path not in rows or any(row[k] != rows[path][k] for k in ('sha256', 'bytes')):
            raise VendorError('ELF selection identity differs from stock recipe')
        if not path.startswith(('vendor/lib64/', 'vendor/bin/')):
            raise VendorError('unsupported native install partition')
    dependencies = {path: [] for path in elfs}
    # dlopen providers are installed with their consumer but never linked.
    required = {path: [] for path in elfs}
    edge_keys = set()
    for edge in selection['edges']:
        if edge['consumer'] not in elfs:
            raise VendorError('undeclared ELF consumer')
        key = (edge['consumer'], edge['needed'])
        if key in edge_keys:
            raise VendorError('duplicate or ambiguous ELF dependency')
        edge_keys.add(key)
        if edge['kind'] == 'selected-stock':
            provider = edge['provider']
            if provider not in elfs or provider not in rows[edge['consumer']]['dependencies']:
                raise VendorError('undeclared ELF dependency')
            stem = Path(provider).name.removesuffix('.so')
            dep = stem if stem in SOURCE_INTERFACES else module(provider)
        elif edge['kind'] == RUNTIME_EDGE:
            provider = edge['provider']
            declared = {r['path']: r['soname'] for r in rows[edge['consumer']].get('runtime_dependencies', [])}
            if (provider not in elfs or declared.get(provider) != edge['needed']
                    or Path(provider).name != edge['needed']):
                raise VendorError('undeclared runtime dependency')
            stem = Path(provider).name.removesuffix('.so')
            required[edge['consumer']].append(stem if stem in SOURCE_INTERFACES else module(provider))
            continue
        elif edge['kind'] == 'platform-or-vndk34':
            if not re.fullmatch(r'[A-Za-z0-9_.@+-]+\.so', edge['needed']):
                raise VendorError('invalid platform dependency')
            if not edge['export_lists'] or any(p not in {
                    '/etc/llndk.libraries.34.txt', '/etc/vndkcore.libraries.34.txt',
                    '/etc/vndksp.libraries.34.txt', '/etc/vndkprivate.libraries.34.txt'}
                    for p in edge['export_lists']):
                raise VendorError('unreviewed platform export namespace')
            # The export lists record the stock VNDK 34 interface a blob was
            # built against. The vendor is an Android 17 vendor without a VNDK
            # version, so the dependency is the current vendor variant.
            dep = edge['needed'].removesuffix('.so')
            rewrite = NEEDED_REWRITES.get(edge['consumer'])
            if rewrite and rewrite['needed'] == edge['needed']:
                dep = rewrite['module']
        elif edge['kind'] == 'source-module':
            dep = edge['needed'].removesuffix('.so')
            if dep not in SOURCE_MODULE_DEPENDENCIES or edge['needed'] != dep + '.so':
                raise VendorError('unreviewed source module dependency')
        else:
            raise VendorError('unresolved ELF dependency')
        dependencies[edge['consumer']].append(dep)
    for path in elfs:
        declared = set(rows[path]['dependencies'])
        observed = {e['provider'] for e in selection['edges']
                    if e['consumer'] == path and e['kind'] == 'selected-stock'}
        if declared != observed:
            raise VendorError('incomplete selected ELF dependency edges')
        declared_runtime = {r['path'] for r in rows[path].get('runtime_dependencies', [])}
        observed_runtime = {e['provider'] for e in selection['edges']
                            if e['consumer'] == path and e['kind'] == RUNTIME_EDGE}
        if declared_runtime != observed_runtime:
            raise VendorError('incomplete selected runtime dependency edges')
    firmware = selection.get('firmware_inputs', [])
    firmware_paths = {r['path'] for r in firmware}
    if len(firmware_paths) != len(firmware):
        raise VendorError('duplicate firmware input')
    for item in firmware:
        path = item['path']
        if (not path.startswith('vendor/firmware/') or path not in rows
                or rows[path]['component_id'] != 'firmware-trusted-boot'
                or any(rows[path][k] != item[k] for k in ('sha256', 'bytes'))
                or not item.get('consumer') or not item.get('source')):
            raise VendorError('missing or inconsistent firmware dependency')
    text = '// Generated from the selected stock recipe; do not edit.\n'
    text += blueprint('package', {'default_applicable_licenses': ['fp6_selected_stock_notices']})
    text += blueprint('license', {'name': 'fp6_selected_stock_notices',
                                 'license_kinds': [notice_kind], 'license_text': ['NOTICE.xml']})
    for partition in sorted({p.split('/')[0] for p in rows} - {'vendor'}):
        if partition not in PARTITIONS:
            raise VendorError('unsupported install partition')
        text += blueprint('license', {'name': notice_license(partition), 'license_kinds': [notice_kind],
                                     'license_text': ['NOTICE-' + partition + '.xml']})
    kept = reachable(selection)
    names, consumed = [], set(elfs) - kept
    for path in sorted(elfs):
        if path not in kept:
            continue
        stem = Path(path).name.removesuffix('.so')
        if stem in SOURCE_INTERFACES:
            consumed.add(path)
            continue
        name = module(path)
        names.append(name)
        consumed.add(path)
        library = path.endswith('.so')
        base = Path('vendor/lib64' if library else 'vendor/bin')
        relative = Path(path).parent.relative_to(base).as_posix()
        props = {'name': name, 'vendor': True, 'compile_multilib': '64',
                 'srcs': ['files/' + path], 'stem': stem, 'strip': {'none': True},
                 'shared_libs': sorted(set(dependencies[path])), 'system_shared_libs': []}
        if required[path]:
            props['required'] = sorted(set(required[path]))
        if relative != '.':
            props['relative_install_path'] = relative
        if library and stem in LIBRARY_VINTF:
            config = 'vendor/etc/vintf/manifest/' + LIBRARY_VINTF[stem]
            if config not in rows:
                raise VendorError('missing library VINTF declaration')
            props['vintf_fragments'] = ['files/' + config]
            consumed.add(config)
        if not library:
            if stem not in ACTIVATION:
                raise VendorError('native executable lacks reviewed activation')
            rc, fragment = ACTIVATION[stem]
            for key, directory, filenames in [('init_rc', 'init', rc),
                                               ('vintf_fragments', 'vintf/manifest', fragment)]:
                filenames = (filenames,) if isinstance(filenames, str) else (filenames or ())
                for filename in filenames:
                    config = 'vendor/etc/' + directory + '/' + filename
                    if config not in rows:
                        raise VendorError('missing service activation file')
                    props.setdefault(key, []).append('files/' + config)
                    consumed.add(config)
        text += blueprint('cc_prebuilt_library_shared' if library else 'cc_prebuilt_binary', props)
    for path in sorted(rows):
        partition = path.split('/')[0]
        if partition == 'vendor':
            continue
        flag = PARTITIONS[partition][0]
        if path in STOCK_APPS:
            name, privileged = STOCK_APPS[path]
            if Path(path).parent.name != name:
                raise VendorError('stock app install directory differs')
            names.append(name)
            consumed.add(path)
            text += blueprint('android_app_import', {
                'name': name, flag: True, 'apk': 'files/' + path, 'presigned': True,
                'preprocessed': True, 'privileged': privileged, 'dex_preopt': {'enabled': False},
                'enforce_uses_libs': False, 'licenses': [notice_license(partition)]})
        elif path in STOCK_JNI:
            if rows[path]['dependencies'] or partition != 'system_ext':
                raise VendorError('stock JNI library has unreviewed dependencies')
            name = module(path)
            names.append(name)
            consumed.add(path)
            text += blueprint('cc_prebuilt_library_shared', {
                'name': name, flag: True, 'compile_multilib': '64', 'srcs': ['files/' + path],
                'stem': Path(path).name.removesuffix('.so'), 'strip': {'none': True},
                'shared_libs': STOCK_JNI[path], 'system_shared_libs': [],
                'licenses': [notice_license(partition)]})
        elif path not in STOCK_DATA or (path in STOCK_LIBRARIES and STOCK_LIBRARIES[path] not in rows):
            raise VendorError('unclassified Android installation input')
    for link in recipe.get('symlinks', []):
        destination = vendor_files.link_destination(link)
        partition = link['path'].split('/')[0]
        if partition == 'vendor':
            if destination not in elfs:
                raise VendorError('alias has no native provider')
            placement = {'vendor': True}
        else:
            if destination not in STOCK_JNI or destination not in rows or not link['path'].startswith(tuple(
                    str(Path(app).parent) + '/lib/arm64/' for app in STOCK_APPS)):
                raise VendorError('alias has no native provider')
            placement = {PARTITIONS[partition][0]: True, 'licenses': [notice_license(partition)]}
        name = module(link['path']) + '_alias'
        names.append(name)
        text += blueprint('install_symlink', dict(name=name, **placement,
                          installed_location=link['path'].removeprefix(partition + '/'),
                          symlink_target=link['target'], required=[module(destination)]))
    dsp_names = set()
    for path in sorted(rows):
        if not path.startswith(DSP_DIRECTORIES):
            continue
        if (path in elfs or Path(path).parent.as_posix() + '/' not in DSP_DIRECTORIES
                or not re.fullmatch(r'[A-Za-z0-9_]+\.so', Path(path).name) or Path(path).name in dsp_names):
            raise VendorError('invalid DSP library input')
        dsp_names.add(Path(path).name)
        name = module(path)
        names.append(name)
        consumed.add(path)
        text += blueprint('prebuilt_rfsa', {'name': name, 'vendor': True, 'src': 'files/' + path,
                                            'filename': Path(path).name, 'relative_install_path': 'adsp'})
    make = '# Generated from the authenticated selection.\nPRODUCT_PACKAGES += ' + ' '.join(names)
    make += '\nPRODUCT_VENDOR_PROPERTIES += ro.hardware.egl=adreno ro.hardware.vulkan=adreno\n'
    for path in sorted(set(rows) - consumed):
        if path.startswith('vendor/etc/lm/'):
            # No learning plugin is installed or enabled in this composition.
            continue
        partition = path.split('/')[0]
        if partition in PARTITIONS and path in STOCK_DATA:
            make += ('PRODUCT_COPY_FILES += vendor/fairphone/FP6/files/' + path + ':'
                     + PARTITIONS[partition][1] + '/' + path.removeprefix(partition + '/') + '\n')
            continue
        if path in elfs or path.startswith(('vendor/etc/init/', 'vendor/etc/vintf/')) or (
                path not in firmware_paths and not path.startswith('vendor/etc/')
                and not any(p.fullmatch(path) for p in LIB64_DATA)):
            raise VendorError('unclassified Android installation input')
        make += 'PRODUCT_COPY_FILES += vendor/fairphone/FP6/files/' + path + ':$(TARGET_COPY_OUT_VENDOR)/' + path.removeprefix('vendor/') + '\n'
    return {'Android.bp': text.encode(), 'device-vendor.mk': make.encode(),
            'BoardConfigVendor.mk': b'# Selected stock vendor patch level.\nVENDOR_SECURITY_PATCH := 2026-08-05\n',
            'modules.json': encoded(names)}


def performance_config(data):
    """Pinned correction of optional startup gates; preserve every other byte."""
    if hashlib.sha256(data).hexdigest() != 'bc2c287db99b1d184ee281429cd8703e8b8976fe9e0a951378d454cfb69e60db':
        raise VendorError('performance configuration differs from reviewed input')
    import xml.etree.ElementTree as ET
    disabled = {'vendor.debug.enable.lm', 'vendor.debug.enable.memperfd', 'ro.vendor.perf.enable.prekill'}
    def replace(match):
        token = match[0]
        if token.startswith(b'<!--'):
            return token
        node = ET.fromstring(token)
        if node.attrib.get('Name') in disabled:
            return token.replace(b'Value="true"', b'Value="false"')
        return token
    result = re.sub(rb'<!--.*?-->|<Prop\s[^>]*?/>', replace, data, flags=re.S)
    if hashlib.sha256(result).hexdigest() != '960b5b4088af3601279297e9169e82168dd931e5b901701924bc9db47651873b':
        raise VendorError('derived performance configuration differs from reviewed result')
    return result


AUDIO_CONFIG_REWRITES = {
    'vendor/etc/audio/sku_volcano/audio_effects.xml': {
        'source_sha256': '6de7bf739222d27f88adb1a95fa6f010e0d1c151b60d0a3da5603dbf0c74d1fe',
        'sha256': '04990d1f19e82641c50c00c66dbddb3be06811ad817a9f73bbbde8e78109b856',
        'reason': 'Defer closed AudioSphere and Quasar effects'},
    'vendor/etc/audio/sku_volcano/resourcemanager_volcano_mtp_fps.xml': {
        'source_sha256': 'ad423c0311b365759c692c64bc5f25ddca7ee788e8b769f2d53f8e801d4e3513',
        'sha256': 'e507aa16508f03f8550b49e11ec7440bf32b68e1c29648a28bd27bd3f00d0f5e',
        'reason': 'Disable context detection and remove deferred sound-trigger/model configuration'},
    'vendor/etc/audio/sku_volcano/audio_policy_configuration.xml': {
        'source_sha256': '3bf77c8f71e1ad1186e226b177c0c4cc9881c9ed9bad91cf848b632b7f039219',
        'sha256': 'db37e22a193fceadd924b1c91d2ad3cf552e2cfa36709ee18ac3ada3ea48ff25',
        'reason': 'Route Bluetooth A2DP and LE audio through the AOSP software Bluetooth audio module instead of DSP offload'},
}
# Primary-module device ports that exist only for Bluetooth DSP offload.
BT_OFFLOAD_PORTS = (b'BT A2DP Out', b'BT A2DP Headphones', b'BT A2DP Speaker', b'BT BLE Out',
                    b'BT BLE Speaker', b'BT BLE Broadcast', b'A2DP In', b'BLE In')


def bluetooth_software_audio_policy(data):
    """Drop the offload-only Bluetooth ports from the primary module and use the AOSP Bluetooth module."""
    names = b'|'.join(re.escape(n) for n in BT_OFFLOAD_PORTS)
    derived = re.sub(rb'^[ \t]*<devicePort tagName="(?:' + names + rb')"[^>]*>.*?</devicePort>\n',
                     b'', data, flags=re.S | re.M)
    derived = re.sub(rb'^[ \t]*<route type="mix" sink="(?:' + names + rb')"\s+sources="[^"]*"/>\n',
                     b'', derived, flags=re.M)
    derived = re.sub(rb'sources="([^"]*)"', lambda m: b'sources="' + b','.join(
        s for s in m[1].split(b',') if s not in (b'A2DP In', b'BLE In')) + b'"', derived)
    return derived.replace(
        b'        <!-- Bluetooth Audio HAL for hearing aid -->\n'
        b'        <xi:include href="/vendor/etc/bluetooth_qti_hearing_aid_audio_policy_configuration.xml"/>\n',
        b'        <!-- Bluetooth Audio HAL: AOSP software A2DP, hearing aid and LE audio -->\n'
        b'        <xi:include href="/vendor/etc/bluetooth_with_le_audio_policy_configuration_7_0.xml"/>\n')


def audio_config(path, data):
    """Pinned removal of deferred closed effects, sound-trigger and Bluetooth offload configuration."""
    rule = AUDIO_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('audio configuration differs from reviewed EU stock input')
    if path.endswith('/audio_policy_configuration.xml'):
        derived = bluetooth_software_audio_policy(data)
    elif path.endswith('/audio_effects.xml'):
        derived = re.sub(rb'^.*<(?:library|effect) name="(?:audiosphere|quasar)"[^\n]*\n',
                         b'', data, flags=re.M)
    else:
        derived = data.replace(b'<param key="context_manager_enable" value ="true" />',
                               b'<param key="context_manager_enable" value ="false" />')
        derived = re.sub(rb'    <sound_trigger_platform_info>.*?</sound_trigger_platform_info>\n',
                         b'', derived, flags=re.S)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived audio configuration differs from reviewed result')
    return derived


CAMERA_CONFIG_REWRITES = {
    'vendor/etc/init/vendor.qti.camera.provider-service_64.rc': {
        'source_sha256': 'ccc0c2b945c1be4fee8061ddc519d090c2c8f76ea70b3c733b11429eb20175f8',
        'sha256': '9f3fa09193baeb9a6afc2a92e562f0b0701dc81e03057b891061c0c89cdf75fe',
        'reason': 'Limit camera provider groups to camera, media (secure FastRPC node), oem_2907 (thermal) '
                  'and wakelock; drop the undeclared postproc/AON interface lines and the '
                  'cam_event_inject fault-injection chown'},
}


def camera_config(path, data):
    """Pinned reduction of the stock camera provider service definition."""
    rule = CAMERA_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('camera configuration differs from reviewed EU stock input')
    derived = re.sub(rb'^    interface vendor\.qti\.hardware\.camera\.(?:postproc|aon)@1\.0::\S+ \S+\n',
                     b'', data, flags=re.M)
    derived = derived.replace(b'    group audio camera input drmrpc oem_2907 oem_2912 wakelock\n',
                              b'    group camera media oem_2907 wakelock\n')
    derived = re.sub(rb'\non boot\n    chown cameraserver camera /sys/module/camera/parameters/cam_event_inject\n\Z',
                     b'', derived)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived camera configuration differs from reviewed result')
    return derived


TELEPHONY_CONFIG_REWRITES = {
    'vendor/etc/init/qcrilNrd.rc': {
        'source_sha256': 'fbb2c179c4c4ede0ceb3445dbb7c169f198546f4d1eed672d2ff8287eb74e26a',
        'sha256': 'd41b734e8a936f052de2fbddd44ab9a441f1b9ff32f9ba6a5c8c47864edaf66f',
        'reason': 'Drop the log, readproc, diag (oem_2901) and SSG socket (oem_2912) groups from the radio daemon'},
    'vendor/etc/init/nicmd.rc': {
        'source_sha256': 'b756045f1b044b7f690bd3296fec9e82351cd8afee99ee5af8bdc2f8c1a3f7e3',
        'sha256': '0a4a6373d3eb0b6d587739a93111d65fb30ea15b0074a28f8086731cf5b3cfc1',
        'reason': 'Declare the root user and bound nicmd to the capabilities its stock SELinux domain allows'},
    'vendor/etc/data/nicm_config.xml': {
        'source_sha256': 'd9c5fd8ab8be49512d6ce4bf628c1f7c3218de1c287569e3f19e64040d35d0a0',
        'sha256': '03914a36e14990016b6bf72da90b95230c4e67381b0faadd2b82454dd30defaf',
        'reason': 'Turn off the persistent nicmd file log (/data/vendor/nicmd/nicmd.log)'},
}


def telephony_config(path, data):
    """Pinned reductions of the stock radio daemon, nicmd service and nicmd log configuration."""
    rule = TELEPHONY_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('telephony configuration differs from reviewed EU stock input')
    if path.endswith('/qcrilNrd.rc'):
        derived = data.replace(b'    group radio cache inet misc audio log readproc wakelock oem_2901 oem_2912\n',
                               b'    group radio cache inet misc audio wakelock\n')
    elif path.endswith('/nicmd.rc'):
        service = b'service vendor.nicmd /system/vendor/bin/nicmd\n    class main\n'
        derived = data.replace(service, service + b'    user root\n'
                               b'    capabilities NET_ADMIN NET_RAW SETGID SETUID SETPCAP KILL BLOCK_SUSPEND\n')
    else:
        # nicmd enables its file logger only when both values are non-zero
        # (libnicm_internal.so ConfigurationManager::parseConfiguration).
        derived = data.replace(b'<data name="num_log_files" type="int"> 4 </data>',
                               b'<data name="num_log_files" type="int"> 0 </data>')
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived telephony configuration differs from reviewed result')
    return derived


def stock_library_declaration(path, data):
    """A selected stock permission XML may only declare its reviewed shared library."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise VendorError('stock permission file is not well formed') from None
    children = list(root)
    if (root.tag != 'permissions' or root.attrib or len(children) != 1 or children[0].tag != 'library'
            or set(children[0].attrib) != {'name', 'file'} or list(children[0])
            or children[0].get('file') != '/' + STOCK_LIBRARIES[path]):
        raise VendorError('stock permission file declares more than its reviewed library')
    return children[0].get('name')


# The stock Samsung NFC HAL returns these routes to the NFC stack
# (nfc_hal_getVendorConfig). Stock sends unregistered ISO-DEP AIDs, the NFC-A/B
# default and NFC-F to the SIM (0x83). Route them to the host (0x00, the AOSP
# default). DEFAULT_OFFHOST_ROUTE and OFFHOST_ROUTE_UICC stay, so a registered
# off-host service still reaches the SIM.
NFC_CONFIG_REWRITES = {
    'vendor/etc/libnfc-sec-vendor.conf': {
        'source_sha256': 'f18694ead707eb26e818368edf3e1a854ee86a1408e82f28b1c1ac8d9c40c05c',
        'sha256': '99dc5bbac35db5856987623073a917efa9b183256ac008b6aa55e08fb43e329b',
        'reason': 'Route unregistered ISO-DEP, NFC-F and default listen traffic to the host, not the SIM'},
}


def nfc_config(path, data):
    """Pinned change of the default listen routes from the SIM (0x83) to the host (0x00)."""
    rule = NFC_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('NFC configuration differs from reviewed EU stock input')
    derived = data
    for key in (b'DEFAULT_ROUTE', b'DEFAULT_ISODEP_ROUTE', b'DEFAULT_NFCF_ROUTE'):
        derived, count = re.subn(rb'^' + key + rb'=0x83$', key + b'=0x00', derived, flags=re.M)
        if count != 1:
            raise VendorError('NFC configuration differs from reviewed EU stock input')
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived NFC configuration differs from reviewed result')
    return derived


GNSS_CONFIG_REWRITES = {
    'vendor/etc/izat.conf': {
        'source_sha256': 'faaf2906fb7520b8c348e4f47e797d4a0b574d332cd647cc0c251a3352352c4f',
        'sha256': 'e1f26cf0524ed52d102e27f8efaa490598985455c73158cd4fb1eda257b50c6d',
        'reason': 'Disable Qualcomm cloud positioning, Wi-Fi scan injection and the uninstalled location daemons'},
    'vendor/etc/gps.conf': {
        'source_sha256': 'a89ca530acfa96685d87160ecf61ac49f3a26128dfa24372c4eb917ec7ccdd57',
        'sha256': 'b7c87609084cc1b65fa7f9a32bfe38100fddea9a6e2f2f248d287e9dfe55002f',
        'reason': 'Remove the Qualcomm XTRA time server and the diagnostic logging interface'},
    'vendor/etc/init/android.hardware.gnss-aidl-service-qti.rc': {
        'source_sha256': 'c26a05a20d168de2ff460b6493029368e7ae7fe774c65c794c11e635d3856d1f',
        'sha256': 'e497937eefe98b5127ceca4331fa271067ef7bd1eb66b794db1aea5728c3f555',
        'reason': 'Advertise only IGnss/default and keep only the system and gps groups'},
}
IZAT_DISABLED_PROCESSES = ('lowi-server', 'xtwifi-client', 'slim_daemon', 'xtra-daemon', 'edgnss-daemon', 'blpsvc')
GNSS_REQUIRED = {
    'vendor/etc/gps.conf': (b'\nLOG_BUFFER_ENABLED = 0\n', b'\nQXDM_LOG = 0\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n'),
    'vendor/etc/izat.conf': (b'\nGTP_MODE=DISABLED\n', b'\nFREE_WIFI_SCAN_INJECT=DISABLED\n',
                             b'\nSUPL_WIFI=DISABLED\n', b'\nWIFI_SUPPLICANT_INFO=DISABLED\n'),
    'vendor/etc/init/android.hardware.gnss-aidl-service-qti.rc': (b'\n    interface aidl android.hardware.gnss.IGnss/default\n',
                                                                  b'\n    group system gps\n'),
}
GNSS_FORBIDDEN = {
    'vendor/etc/gps.conf': (b'xtracloud', b'izatcloud', b'://'),
    'vendor/etc/izat.conf': (b'xtracloud', b'izatcloud', b'://', b'\nPROCESS_STATE=ENABLED'),
    'vendor/etc/init/android.hardware.gnss-aidl-service-qti.rc': (b'ILocAidlGnss', b'radio', b'vendor_qti_diag',
                                                                  b'vendor_ssgtzd', b'capabilities', b'inet'),
}


def gnss_config(path, data):
    """Pinned privacy and activation edits of the stock GNSS configuration."""
    rule = GNSS_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('GNSS configuration differs from reviewed EU stock input')
    if path.endswith('/izat.conf'):
        derived = data.replace(b'GTP_PRIVACY_VERSION_URL = https://info.izatcloud.net/privacy/version.html\n', b'')
        for key, old in ((b'GTP_MODE', b'SDK'), (b'FREE_WIFI_SCAN_INJECT', b'BASIC'),
                         (b'SUPL_WIFI', b'BASIC'), (b'WIFI_SUPPLICANT_INFO', b'BASIC')):
            derived = derived.replace(b'\n' + key + b'=' + old + b'\n', b'\n' + key + b'=DISABLED\n')
        for name in IZAT_DISABLED_PROCESSES:
            derived = re.sub(rb'(\nPROCESS_NAME=' + re.escape(name.encode()) + rb'\nPROCESS_ARGUMENT=[^\n]*\nPROCESS_STATE=)ENABLED\n',
                             rb'\1DISABLED\n', derived)
    elif path.endswith('/gps.conf'):
        derived = data.replace(b'#NTP server\nNTP_SERVER=time.xtracloud.net\n', b'')
        derived = derived.replace(b'\nLOC_DIAGIFACE_ENABLED = 1\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n')
    else:
        derived = data.replace(b'    interface aidl vendor.qti.gnss.ILocAidlGnss/default\n', b'')
        derived = derived.replace(b'    group system gps radio vendor_qti_diag vendor_ssgtzd\n', b'    group system gps\n')
    if (any(token not in derived for token in GNSS_REQUIRED[path])
            or any(token in derived for token in GNSS_FORBIDDEN[path])):
        raise VendorError('derived GNSS configuration violates its privacy invariants')
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived GNSS configuration differs from reviewed result')
    return derived

def generate(recipe, selection, inputs, output, *, notice_kind, **policy):
    closure = vendor_files.selection(recipe, public=False, **policy)
    rendered = render(recipe, selection, notice_kind)
    provenance = {'operation': 'fp6-native-product-generation',
                  'scope': 'private-bringup',
                  'recipe_sha256': hashlib.sha256(encoded(recipe)).hexdigest(),
                  'elf_selection_sha256': hashlib.sha256(encoded(selection)).hexdigest(),
                  'renderer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'notice_kind': notice_kind, 'native_or_device_accepted': False}
    kept = reachable(selection)
    provenance['uninstalled_optional_libraries'] = sorted(
        r['path'] for r in selection['files'] if r['path'] not in kept)
    provenance['source_interface_replacements'] = sorted(
        r['path'] for r in selection['files']
        if r['path'] in kept and Path(r['path']).name.removesuffix('.so') in SOURCE_INTERFACES)
    identity = hashlib.sha256(encoded(provenance)).hexdigest()
    output = Path(output)
    if output.is_symlink():
        raise VendorError('output root cannot be a symlink')
    output.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = os.open(output / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        import stat
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise VendorError('invalid product generation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        generations = output / 'generations'
        if generations.is_symlink():
            raise VendorError('generation root cannot be a symlink')
        generations.mkdir(exist_ok=True, mode=0o750)
        with tempfile.TemporaryDirectory(prefix='.product-', dir=generations) as temporary:
            tree = Path(temporary) / 'tree'
            tree.mkdir()
            for item in recipe.get('symlinks', []):
                vendor_files.verify_symlink(inputs, item)
            for item in recipe['files']:
                vendor_files.copy_verified(inputs, item, tree / 'files' / item['path'])
            for item in recipe['notices']:
                vendor_files.copy_verified(inputs, item, tree / 'notices' / item['sha256'])
            for item in recipe['files']:
                data_input = any(p.fullmatch(item['path']) for p in LIB64_DATA)
                dsp_input = item['path'].startswith(DSP_DIRECTORIES)
                if not (data_input or dsp_input):
                    continue
                with open(tree / 'files' / item['path'], 'rb') as stream:
                    head = stream.read(20)
                if data_input and head[:4] == b'\x7fELF':
                    raise VendorError('ELF content in a vendor/lib64 data input')
                if dsp_input and (head[:8] != DSP_MACHINE_MAGIC or head[18:20] != (164).to_bytes(2, 'little')):
                    raise VendorError('DSP library input is not a QDSP6 ELF')
            partitions = sorted({i['path'].split('/')[0] for i in recipe['files']})
            archives = {n['input']: n for n in recipe['notices']}
            if (len(archives) != len(partitions)
                    or set(archives) != {p + '/etc/NOTICE.xml.gz' for p in partitions}):
                raise VendorError('FP6 product requires its reviewed stock notice archive')
            for partition in partitions:
                archive = archives[partition + '/etc/NOTICE.xml.gz']
                with gzip.GzipFile(fileobj=io.BytesIO((tree / 'notices' / archive['sha256']).read_bytes())) as stream:
                    notice = stream.read(64 * 1024**2 + 1)
                if len(notice) > 64 * 1024**2:
                    raise VendorError('expanded notice exceeds size limit')
                rendered['NOTICE.xml' if partition == 'vendor' else 'NOTICE-' + partition + '.xml'] = notice
            config = tree / 'files/vendor/etc/perf/perfconfigstore.xml'
            original = config.read_bytes()
            derived = performance_config(original)
            config.write_bytes(derived)
            provenance['derived_files'] = [{'path': 'vendor/etc/perf/perfconfigstore.xml',
                'source_sha256': hashlib.sha256(original).hexdigest(),
                'sha256': hashlib.sha256(derived).hexdigest(),
                'reason': 'Disable optional learning, memory plugin and prekill startup gates'}]
            for path, rewrite in AUDIO_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(audio_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in CAMERA_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(camera_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in TELEPHONY_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(telephony_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in NFC_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(nfc_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in GNSS_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(gnss_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            provenance['stock_shared_libraries'] = {
                path: stock_library_declaration(path, (tree / 'files' / path).read_bytes())
                for path in sorted(STOCK_LIBRARIES) if path in {i['path'] for i in recipe['files']}}
            for path, rewrite in sorted(NEEDED_REWRITES.items()):
                blob = tree / 'files' / path
                original = blob.read_bytes()
                if hashlib.sha256(original).hexdigest() != rewrite['source_sha256']:
                    raise VendorError('dependency rewrite input differs from reviewed blob')
                derived = rewrite_needed(original, rewrite['needed'], rewrite['replacement'])
                if hashlib.sha256(derived).hexdigest() != rewrite['sha256']:
                    raise VendorError('dependency rewrite differs from reviewed result')
                blob.write_bytes(derived)
                provenance['derived_files'].append({'path': path, 'source_sha256': rewrite['source_sha256'],
                    'sha256': rewrite['sha256'],
                    'reason': 'Use the Android 14 tinyxml2 ABI (' + rewrite['module'] + ') instead of ' + rewrite['needed']})
            rendered.update({'provenance.json': encoded(provenance), 'recipe.json': encoded(recipe),
                             'component-closure.json': encoded(closure)})
            for name, content in rendered.items():
                (tree / name).write_bytes(content)
            records = {}
            for p in tree.rglob('*'):
                if p.is_file():
                    p.chmod(0o640)
                    records[str(p.relative_to(tree))] = {'bytes': p.stat().st_size,
                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            # Closure records describe authenticated source bytes. Derivations
            # explicitly bind changed output; neither claims runtime acceptance.
            vendor_files.verify_tree(tree, records)
            final = generations / identity
            if final.exists() or final.is_symlink():
                vendor_files.verify_tree(final, records)
            else:
                tree.rename(final)
        inventories = output / 'inventories'
        if inventories.is_symlink():
            raise VendorError('inventory directory cannot be a symlink')
        inventories.mkdir(exist_ok=True, mode=0o750)
        inventory = inventories / (identity + '.json')
        inventory_bytes = encoded(records)
        if inventory.exists() or inventory.is_symlink():
            if inventory.is_symlink() or not inventory.is_file() or inventory.read_bytes() != inventory_bytes:
                raise VendorError('existing product inventory differs')
        else:
            with tempfile.TemporaryDirectory(prefix='.inventory-', dir=inventories) as temporary:
                staged = Path(temporary) / 'inventory.json'
                staged.write_bytes(inventory_bytes)
                staged.rename(inventory)
        target = 'generations/' + identity
        current = output / 'current'
        if not current.is_symlink() or os.readlink(current) != target:
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=output) as temporary:
                link = Path(temporary) / 'current'
                link.symlink_to(target)
                os.replace(link, current)
    return dict(operation='fp6-native-product-generation', status='PASS',
                generation_sha256=identity, inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(), scope='private-bringup', native_or_device_accepted=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe', type=Path, default=ROOT / 'config/fp6-minimal/vendor-files.json')
    parser.add_argument('--selection', type=Path, default=ROOT / 'config/fp6-minimal/vendor-elf.json')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notice-kind', required=True, help='reviewed Android notice classification')
    args = parser.parse_args(argv)
    try:
        model_data = (ROOT / 'config/components.json').read_bytes()
        source_data = (ROOT / 'config/fp6-sources.json').read_bytes()
        result = generate(components.load_json(args.recipe), components.load_json(args.selection),
            args.inputs, args.output, notice_kind=args.notice_kind,
            model=components.loads(model_data), sources=components.loads(source_data),
            environment=components.load_json(ROOT / 'config/build-environment.json'),
            model_sha256=hashlib.sha256(model_data).hexdigest(),
            source_sha256=hashlib.sha256(source_data).hexdigest())
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, EOFError, components.ComponentError):
        print('ERROR: unable to authenticate or publish native product inputs')
        return 2
