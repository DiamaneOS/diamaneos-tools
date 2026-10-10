"""Materialize the reviewed FP6 native Android integration from authenticated files."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from . import carrier_data, firmware_release, safe_json, vendor_files
from .vendor import VendorError, encoded

ROOT = Path(__file__).resolve().parents[2]
SOURCE_INTERFACES = {
    'libdrm',
    # libkeymaster_messages is not listed: it is a C++ implementation library
    # that only the closed KeyMint HAL uses, so that HAL keeps the stock copy it
    # was built with (a source copy could change class layouts unnoticed). It
    # is installed on odm (ODM_LIBRARIES below).
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
    # Qualcomm display stack built from source. The stock
    # Android 14 composer failed to present under Android 17 when tried without
    # libsdmextension and is untested with it; source is kept so we can patch
    # and harden the code that handles every app's buffers.
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
    # Sensors interfaces used by the stock Qualcomm sub-HAL, built from source
    # with the AOSP sensors multi-HAL (frozen HIDL/AIDL interfaces).
    'android.hardware.sensors@1.0',
    'android.hardware.sensors@2.0',
    'android.hardware.sensors@2.0-ScopedWakelock',
    'android.hardware.sensors@2.1',
    'android.hardware.sensors-V2-ndk',
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
    # HCI implementation and the device's own Bluetooth service.
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
    # Camera: AOSP camera AIDL interfaces, the HIDL
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
    # Display: thermal interfaces linked by the stock SDM
    # composition-strategy extension; the tree builds all three.
    'android.hardware.thermal-V1-ndk',
    'android.hardware.thermal@1.0',
    'android.hardware.thermal@2.0',
    # Media (hardware video encoders): the frozen Codec2 HIDL and bufferpool2
    # AIDL V1 interfaces, the minijail helper and the display-config V5
    # interface linked by the stock Qualcomm Codec2 service and its plugins.
    # The last two are also SOURCE_INTERFACES (their stock rows belong to
    # radio-ims-data and public-hal-services); listing them here admits the
    # media source-module edges without a component dependency on those lanes.
    'android.hardware.media.bufferpool2-V1-ndk',
    'android.hardware.media.c2@1.0',
    'android.hardware.media.c2@1.1',
    'android.hardware.media.c2@1.2',
    'libavservices_minijail',
    'vendor.qti.hardware.display.config-V5-ndk',
}
ACTIVATION={
 'android.hardware.gatekeeper-service-qti':('android.hardware.gatekeeper-service-qti.rc',None),
 'android.hardware.security.keymint-service-qti':('android.hardware.security.keymint-service-qti.rc','android.hardware.security.keymint-service-qti.xml'),
 # Not selected: the stock display colour service, a lazy IDisplayColor and
 # IDisplayPostproc server that platform apps could start. Nothing in this build
 # is a client. libsdm-disp-vndapis stays as its own root: the composer dlopens it.
 'vendor.qti.hardware.memtrack-service':('memtrack_qti.rc','memtrack_qti.xml'),
 'qseecomd':('qseecomd.rc',None),
 'thermal-engine-v2':('init_thermal-engine-v2.rc',None),
 # Not selected: Qualcomm's perf2 daemon (a root service), its client and
 # plugin libraries and their configuration. The device builds LineageOS's
 # libperfmgr power HAL and its own libqti-perfd-client for the stock camera
 # and SDM extension, which load the client by name (device power/).
 'rmt_storage':('vendor.qti.rmt_storage.rc',None),
 # Not selected: tftp_server and pd-mapper. The device builds the open
 # linux-msm tqftpserv and pd-mapper instead (device modem/modem.mk).
 # Stock starts these from its board-wide init.target.rc and init.qti.kernel.rc,
 # which are not selected; the FP6 device init.qcom.rc defines their services.
 'pm-service':(None,None),
 'pm-proxy':(None,None),
 # ssr_setup enables subsystem restart (recovery) for the modem and DSPs from
 # persist.vendor.ssr.restart_level; stock starts it from init.qcom.rc, which
 # is not selected. The FP6 device modem/init.modem.rc defines the service.
 'ssr_setup':(None,None),
 # sscrpcd starts the sensors protection domain on the ADSP; the sensors
 # multi-HAL itself is built from source (device.mk).
 'sscrpcd':('vendor.sensors.sscrpcd.rc',None),
 # Not selected: audioadsprpcd, which starts the audio protection domain on
 # the ADSP. The device builds it from Fairphone's published audio-ar source
 # with the same init file (audio/audio.mk).
 # Not selected: the stock GNSS HAL (IGnss v3) and its location libraries. The
 # device builds them from CodeLinaro source (device gnss/gnss.mk); only the
 # stock gps.conf, izat.conf (pinned derivations, GNSS_CONFIG_REWRITES) and
 # sap.conf stay.
 # The stock CamX/CHI camera provider (AIDL ICameraProvider/vendor_qti/0).
 'vendor.qti.camera.provider-service_64':('vendor.qti.camera.provider-service_64.rc','vendor.qti.camera.provider.xml'),
 # The QCRIL radio daemon declares only the services the device uses: the AOSP radio
 # HAL and the Qualcomm IMS, radio-config, call-audio (IQcRilAudio, served to
 # the device's own bridge) and LPA services.
 'qcrilNrd':('qcrilNrd.rc',('android.hardware.radio.config.xml','android.hardware.radio.data.xml',
                            'android.hardware.radio.messaging.xml','android.hardware.radio.modem.xml',
                            'android.hardware.radio.network.xml','android.hardware.radio.sim.xml',
                            'android.hardware.radio.voice.xml','vendor.qti.hardware.radio.ims.xml',
                            'vendor.qti.hardware.radio.qtiradioconfig.xml','vendor.qti.hardware.radio.am.xml',
                            'vendor.qti.hardware.radio.lpa.xml','vendor.qti.hardware.data.iwlandata.xml')),
 # nicmd configures the rmnet data interfaces for modem data calls.
 'nicmd':('nicmd.rc',None),
 # Not selected: the stock Bluetooth HCI service. It links FM, ANT, SAR,
 # config-store and TPI libraries it never registers here; the device's own
 # service (bluetooth/service.cpp) registers the stock HCI implementation, a
 # runtime root, and the device manifest.xml declares IBluetoothHci 1.1.
 # Stock Samsung S3NRN4V NFC HAL (AIDL INfc/default); its rc also sets the
 # /dev/sec-nfc owner at boot.
 'android.hardware.nfc-service.sec':('nfc-service-sec.rc','nfc-service-sec.xml'),
 # Stock Qualcomm Codec2 video service (HIDL IComponentStore/default),
 # hardware encoders only (MEDIA_CONFIG_REWRITES). The device
 # media/init.fp6.media.rc starts it in group mediacodec only (not the stock
 # camera, mediadrm, drmrpc and system groups), and the device manifest.xml
 # declares only the default instance (the stock fragment also claims the
 # platform's software instance).
 'vendor.qti.media.c2@1.0-service':(None,None),
}

# Reviewed stock Java components outside vendor (bring-up). They keep
# the stock signature (presigned); their privileges come only from the device's
# privileged-permission allowlist, never from our platform key.
# path -> (module name = install directory, privileged). Every privileged app
# needs a complete device allowlist entry (grants and denials).
STOCK_APPS = {
 # The coupled Qualcomm IWLAN and modem-certificate frontend, without CNE.
 # Privileged placement keeps their shared UID closed to new ordinary installs;
 # they request only normal permissions and receive no additional privileged grants.
 'vendor/app/IWlanService/IWlanService.apk':('IWlanService', True),
 'vendor/app/CACertService/CACertService.apk':('CACertService', True),
 'system_ext/priv-app/ims/ims.apk':('ims', True),
 # Not selected: QtiTelephonyService (the IQcRilAudio call-audio client). The
 # device's own call-audio bridge (device callaudio/) replaces it with the
 # normal permission MODIFY_AUDIO_SETTINGS instead of MODIFY_AUDIO_ROUTING.
 # Deferred: no inactive stock LPA or its download privileges are packaged.

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
 'system_ext/etc/permissions/extphonelib.xml':'system_ext/framework/extphonelib.jar',
}
STOCK_DATA = set(STOCK_LIBRARIES) | set(STOCK_LIBRARIES.values()) | {'product/etc/apns-conf.xml'}
# Stock vendor libraries whose name is also an AOSP vendor-available library.
# Soong defines an install rule for every variant in the tree, so a stock copy
# in /vendor/lib64 collides with the AOSP one at the same path. These go to
# /odm/lib64 instead, which the vendor and sphal linker namespaces search before
# /vendor/lib64, so their stock consumers still load the stock copy.
# The Codec2 framework libraries are the Android 14 builds the Qualcomm codec
# plugins were built against; the Android 17 ones are not ABI compatible.
ODM_LIBRARIES = {'libkeymaster_messages',
                 'libcodec2_hidl@1.0', 'libcodec2_hidl@1.1', 'libcodec2_hidl@1.2',
                 'libcodec2_hidl_plugin', 'libcodec2_vndk',
                 'libstagefright_aidl_bufferpool2', 'libstagefright_bufferpool@2.0.1'}
PARTITIONS = {'system_ext': ('system_ext_specific', '$(TARGET_COPY_OUT_SYSTEM_EXT)'),
              'product': ('product_specific', '$(TARGET_COPY_OUT_PRODUCT)')}


def notice_license(partition):
    return 'fp6_selected_stock_notices' + ('' if partition == 'vendor' else '_' + partition)


# Stock libraries that source-built modules link by library name, so they are
# rendered under that name instead of the fp6_stock_ one: the AudioReach graph
# services (Fairphone published only their headers), the tuning server and the
# voice UI interface, linked by the source-built PAL, AGM and AGM HIDL service
# (device audio/). Their own stock dependencies keep the fp6_stock_ names.
SOURCE_LINKED_STOCK = {'libar-gsl', 'libats', 'liblx-osal', 'libvui_intf'}


def module(path):
    if path.startswith('vendor/lib64/') and path.count('/') == 2:
        stem = Path(path).name.removesuffix('.so')
        if stem in SOURCE_LINKED_STOCK:
            return stem
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
# vendor.qti.hardware.camera.offlinecamera-service-impl is installed without
# its stock declaration (vendor.qti.camera.offlinecamera-impl.xml): the CHI
# override and libchifeature2 link it, but nothing uses the offline camera
# service it registers, and without a declaration servicemanager refuses the
# registration. The library logs the refusal and the provider carries on.
LIBRARY_VINTF = {}
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

# Stable AIDL interface versions that selected stock libraries link but that
# cannot be link dependencies here: Android 17's libui links allocator V2, the
# stock camera libraries (built against Android 14) link V1, and Soong rejects
# a module whose dependency graph reaches two versions of one interface. The
# link is dropped from the stock modules that have it, their ELF check is
# skipped, and the vendor library is installed as its own package, so both
# versions are on the device as in stock.
RUNTIME_ONLY_AIDL = {'android.hardware.graphics.allocator-V1-ndk'}

# One DT_NEEDED name of a stock blob renamed to a source-built device module
# of the same name length; input and output bytes are pinned.
# - The colour manager was built against the Android 14 VNDK tinyxml2, whose
#   Android 17 ABI differs: it links an old-ABI copy (device compat module).
# - The camera provider links the seccomp loader (device camera/seccomp) in
#   place of libhardware, which the loader links in turn (and
#   camx.provider-impl.so links too), so nothing else changes. The dynamic
#   linker runs the loader's constructor before the provider's main(), and so
#   before CamX is loaded; without the loader the provider does not start.
NEEDED_REWRITES = {
 'vendor/lib64/libsnapdragoncolor-manager.so': {
  'needed': 'libtinyxml2.so', 'replacement': 'libtxml2v34.so', 'module': 'libtxml2v34',
  'source_sha256': 'e66febb064b81332eaf79525f1dd7a3e531dd28ac46e430495e484c71611c52e',
  'sha256': 'd75f85d85ac41a1f79617cc374dc144626f6b7d34e8285be8f484bc433e4e03a',
  'reason': 'Use the Android 14 tinyxml2 ABI (libtxml2v34) instead of libtinyxml2.so'},
 'vendor/bin/hw/vendor.qti.camera.provider-service_64': {
  'needed': 'libhardware.so', 'replacement': 'libcamxjail.so', 'module': 'libcamxjail',
  'source_sha256': '751fa24b9d44a790a2594828c5aaa2a9e98f1a2a7734cf7cd103023893031f6a',
  'sha256': 'fcec163779acab018729a028fec7ed9d431a2355bc0b714a3911b62367c890ff',
  'reason': 'Load the seccomp loader (libcamxjail, which links libhardware) before the provider starts'},
 # Hardware video encoders (compat/codec2-v34):
 # - libcodec2_vndk was built against Android 14, where GraphicBuffer is 256
 #   bytes; it allocates 256 bytes itself and calls the platform constructor.
 #   Android 17's GraphicBuffer is 3376 bytes, so binding it to Android 17 libui
 #   overflows the codec service's heap. Its libui.so dependency
 #   is renamed to uiv34.so and its six GraphicBuffer imports to GraphicBufV34
 #   (same lengths; see GRAPHICBUFFER_V34_SYMBOLS), so they bind to uiv34's
 #   Android 14 sized GraphicBuffer instead (which fails closed unless hardware
 #   video decoding is on, and then wraps Surface buffers as Android 14 did). Every other
 #   libui symbol still comes from the real libui, which uiv34 links.
 # - libcodec2_hidl@1.0-1.2 need GraphicBufferSource::getHGraphicBufferProducer,
 #   which Android 17 dropped on a class whose layout did not change; their
 #   bufferqueue-helper dependency is renamed to the compat library that re-adds
 #   it (device compat/codec2-v34).
 # Both keep the original library in their Soong shared_libs (keep_link): they
 # still import its other symbols, which Soong's ELF check resolves only
 # against the listed libraries.
 # Hardware video codec service: its libavservices_minijail.so dependency is
 # renamed to the device's seccomp filter and codec gate (media/seccomp),
 # which links libavservices_minijail in turn (keep_link). It comes before
 # libavservices_minijail and libcodec2_hidl@1.2 in the service's dependency
 # order, so the service's SetUpMinijail and Codec2 ComponentStore
 # constructor calls reach it: it installs DiamaneOS's filter (and the stock
 # one on top), checks the codec configuration (the Qualcomm library
 # registers every codec when it cannot read it) and builds the store around
 # a filter that offers only the codecs this boot allows, or none; the store
 # always registers. Without the library the service does not start.
 'vendor/bin/hw/vendor.qti.media.c2@1.0-service': {
  'needed': 'libavservices_minijail.so', 'replacement': 'libc2hwjail_avservices.so',
  'module': 'libc2hwjail_avservices', 'keep_link': True,
  'source_sha256': '43e2535ccea69a64c7743559ed4e85920395c257d2aec25f82535059745e8da9',
  'sha256': '5e823841aa5a420d27bea9ca0597a9a4cbd0dcbcfaace618a853eda3dcc5ae4a',
  'reason': 'Install the seccomp filter and offer only the allowed codecs (libc2hwjail_avservices, '
            'links libavservices_minijail) in the codec service'},
 'vendor/lib64/libcodec2_vndk.so': {
  'needed': 'libui.so', 'replacement': 'uiv34.so', 'module': 'uiv34',
  'symbols': 'GRAPHICBUFFER_V34_SYMBOLS', 'keep_link': True,
  'source_sha256': 'f1d104621b5575f6603fa6779b55548034c2b11be85e6fcd47bffb2afc199e5d',
  'sha256': 'ae840358c2b8ad2aed69d816b7896603bab1ffe2e9af57c964163de2faf34c1f',
  'reason': 'Bind the Android 14 sized GraphicBuffer and mapper entry points (uiv34, links libui) instead of libui.so'},
 'vendor/lib64/libcodec2_hidl@1.0.so': {
  'needed': 'libstagefright_bufferqueue_helper.so', 'replacement': 'libstagefright_bqhelper_v34compat.so',
  'module': 'libstagefright_bqhelper_v34compat', 'keep_link': True,
  'source_sha256': 'f77c0f4abfcc8b3e6ffcd5cfde19babd377aa5bd6bc933f9c2e4663afb1dede2',
  'sha256': 'bf3868b031c5242e1ffd8e064faff6287322212fbf7c81195953347c28d69519',
  'reason': 'Add the Android 14 GraphicBufferSource::getHGraphicBufferProducer entry point '
            '(libstagefright_bqhelper_v34compat, links the helper) instead of libstagefright_bufferqueue_helper.so'},
 'vendor/lib64/libcodec2_hidl@1.1.so': {
  'needed': 'libstagefright_bufferqueue_helper.so', 'replacement': 'libstagefright_bqhelper_v34compat.so',
  'module': 'libstagefright_bqhelper_v34compat', 'keep_link': True,
  'source_sha256': '8c637bfdff0f59802dba11ba493c0f014cf9a16163839312a26616e2a3329010',
  'sha256': 'a5246ffd6080415159089c38477fb2390d30de8f28d9833cf63470b8e9b052c8',
  'reason': 'Add the Android 14 GraphicBufferSource::getHGraphicBufferProducer entry point '
            '(libstagefright_bqhelper_v34compat, links the helper) instead of libstagefright_bufferqueue_helper.so'},
 'vendor/lib64/libcodec2_hidl@1.2.so': {
  'needed': 'libstagefright_bufferqueue_helper.so', 'replacement': 'libstagefright_bqhelper_v34compat.so',
  'module': 'libstagefright_bqhelper_v34compat', 'keep_link': True,
  'source_sha256': '9d87cc30c7eeec29eaca509a8910d93bf331fa588fc7cc831b2a36cb04d0a2ae',
  'sha256': '02076417238b400cdf4eb456b99cf1423fc87acd0e60859f2ec9ddc3222d574c',
  'reason': 'Add the Android 14 GraphicBufferSource::getHGraphicBufferProducer entry point '
            '(libstagefright_bqhelper_v34compat, links the helper) instead of libstagefright_bufferqueue_helper.so'},
}

# The android::GraphicBuffer symbols the stock libcodec2_vndk imports from libui
# and constructs at the Android 14 size, renamed to a 13-character name only the
# uiv34 compat defines (GraphicBuffer and GraphicBufV34 are both 13 characters,
# so every Itanium length prefix stays valid and the strings keep their length).
# Only these six undefined symbols are renamed; the library's own defined
# sp<GraphicBuffer> template symbols keep their names.
GRAPHICBUFFER_V34_SYMBOLS = (
    ('_ZN7android13GraphicBufferC1Ev',
     '_ZN7android13GraphicBufV34C1Ev'),
    ('_ZN7android13GraphicBufferC1EPK13native_handleNS0_16HandleWrapMethodEjjijmj',
     '_ZN7android13GraphicBufV34C1EPK13native_handleNS0_16HandleWrapMethodEjjijmj'),
    ('_ZNK7android13GraphicBuffer9initCheckEv',
     '_ZNK7android13GraphicBufV349initCheckEv'),
    ('_ZN7android13GraphicBuffer17toAHardwareBufferEv',
     '_ZN7android13GraphicBufV3417toAHardwareBufferEv'),
    ('_ZN7android13GraphicBuffer19fromAHardwareBufferEP15AHardwareBuffer',
     '_ZN7android13GraphicBufV3419fromAHardwareBufferEP15AHardwareBuffer'),
    ('_ZN7android13GraphicBuffer19fromAHardwareBufferEPK15AHardwareBuffer',
     '_ZN7android13GraphicBufV3419fromAHardwareBufferEPK15AHardwareBuffer'),
)
SYMBOL_RENAMES = {'GRAPHICBUFFER_V34_SYMBOLS': GRAPHICBUFFER_V34_SYMBOLS}


def rename_dynamic_symbols(data, pairs):
    """Rename specific undefined dynamic symbols in place; refuse anything unsafe.

    Each new name must have the same length as the old one (so .dynstr offsets
    stay valid). Every old name must resolve to exactly one symbol, that symbol
    must be undefined (so its name is not in the GNU hash table and renaming it
    cannot break symbol lookup for the library's own exports), and the edited
    bytes must belong to no other dynamic symbol's name.
    """
    import struct
    if data[:5] != b'\x7fELF\x02' or data[5] != 1:
        raise VendorError('unsupported ELF for symbol rename')
    shoff, = struct.unpack_from('<Q', data, 0x28)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x3a)
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * shentsize) for i in range(shnum)]
    dynsym = [s for s in sections if s[1] == 11]
    if len(dynsym) != 1:
        raise VendorError('unsupported symbol rename')
    dynsym = dynsym[0]
    strtab = sections[dynsym[6]]
    base, size = strtab[4], strtab[5]
    entries = []
    for offset in range(dynsym[4], dynsym[4] + dynsym[5], 24):
        st_name, _, _, shndx, _, _ = struct.unpack_from('<IBBHQQ', data, offset)
        entries.append((st_name, shndx))
    data = bytearray(data)
    edits = []
    for old, new in pairs:
        if len(old) != len(new):
            raise VendorError('symbol rename changes length')
        old_bytes, new_bytes = old.encode(), new.encode()
        matches = [(st_name, shndx) for st_name, shndx in entries
                   if data[base + st_name:base + st_name + len(old_bytes) + 1] == old_bytes + b'\0']
        if len(matches) != 1:
            raise VendorError('symbol to rename is not present exactly once')
        st_name, shndx = matches[0]
        if shndx != 0:
            raise VendorError('symbol to rename is not undefined')
        start = base + st_name
        end = start + len(old_bytes)
        if end >= base + size:
            raise VendorError('symbol name runs past the string table')
        for other_name, _ in entries:
            other = base + other_name
            if other == start:
                continue
            if start < other <= end:
                raise VendorError('symbol name is shared with another symbol')
        edits.append((start, old_bytes, new_bytes))
    for start, old_bytes, new_bytes in edits:
        data[start:start + len(old_bytes)] = new_bytes
    return bytes(data)


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
                if rewrite.get('keep_link'):
                    # The blob still imports the original library's other
                    # symbols, which it now reaches through the replacement
                    # (which links it). Soong's ELF check resolves imports only
                    # against the listed shared_libs, so both are listed; the
                    # blob's DT_NEEDED names only the replacement.
                    dependencies[edge['consumer']].append(dep)
                dep = rewrite['module']
        elif edge['kind'] == 'source-module':
            dep = edge['needed'].removesuffix('.so')
            if dep not in SOURCE_MODULE_DEPENDENCIES or edge['needed'] != dep + '.so':
                raise VendorError('unreviewed source module dependency')
            rewrite = NEEDED_REWRITES.get(edge['consumer'])
            if rewrite and rewrite['needed'] == edge['needed']:
                # As for platform libraries above (the codec service's minijail
                # library is a source module).
                if rewrite.get('keep_link'):
                    dependencies[edge['consumer']].append(dep)
                dep = rewrite['module']
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
    runtime_only = set()
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
        if library and stem in ODM_LIBRARIES:
            if relative != '.':
                raise VendorError('odm library outside lib64')
            del props['vendor']
            props = {'name': name, 'device_specific': True, **{k: v for k, v in props.items() if k != 'name'}}
        dropped = RUNTIME_ONLY_AIDL.intersection(props['shared_libs'])
        if dropped:
            props['shared_libs'] = [d for d in props['shared_libs'] if d not in dropped]
            props['check_elf_files'] = False
            runtime_only.update(dropped)
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
        if partition == 'vendor' and path not in STOCK_APPS:
            continue
        flag = 'vendor' if partition == 'vendor' else PARTITIONS[partition][0]
        if path == carrier_data.APK_PATH:
            consumed.add(path)
            text += blueprint('filegroup', {
                'name': 'fp6_stock_carrier_assets',
                'srcs': [carrier_data.ASSET_DIRECTORY + '/*.xml'],
                'path': 'carrier-assets', 'licenses': [notice_license(partition)]})
        elif path in STOCK_APPS:
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
            if rows[path]['dependencies'] or partition not in ('system_ext', 'product'):
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
    if runtime_only:
        make += '\nPRODUCT_PACKAGES += ' + ' '.join(sorted(d + '.vendor' for d in runtime_only))
    make += '\nPRODUCT_VENDOR_PROPERTIES += ro.hardware.egl=adreno ro.hardware.vulkan=adreno\n'
    for path in sorted(set(rows) - consumed):
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
    # Derived files with no stock row of their own (generate() writes them).
    for path, rule in sorted(MEDIA_HWDEC_COPIES.items()):
        if rule['from'] not in rows or path in rows:
            raise VendorError('hardware decoding configuration has no selected stock input')
        make += 'PRODUCT_COPY_FILES += vendor/fairphone/FP6/files/' + path + ':$(TARGET_COPY_OUT_VENDOR)/' + path.removeprefix('vendor/') + '\n'
    return {'Android.bp': text.encode(), 'device-vendor.mk': make.encode(), 'modules.json': encoded(names)}


# The vendor patch level is the one the selected stock vendor files were
# released with: ro.vendor.build.security_patch of the stock vendor/build.prop,
# which the recipe pins by hash under build_properties.
VENDOR_BUILD_PROP = 'vendor/build.prop'
VENDOR_PATCH_PROPERTY = 'ro.vendor.build.security_patch'


def iso_date(value, what):
    """A real calendar date written as YYYY-MM-DD."""
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise VendorError(what + ' is not a YYYY-MM-DD date')
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise VendorError(what + ' is not a real date') from None


def vendor_patch_level(data, release_date=None, today=None):
    """The patch level the stock vendor build.prop sets, checked strictly.

    The property must be set exactly once to a real date. Stock builds are made
    before the bulletin date they carry (a build of 31 August can carry a
    5 September patch level), so the bound is the factory package's release
    date and today, not the build date.
    """
    values = []
    for line in data.decode('latin-1').splitlines():
        key, separator, value = line.partition('=')
        if separator and not line.lstrip().startswith('#') and key.strip() == VENDOR_PATCH_PROPERTY:
            values.append(value.strip())
    if len(values) != 1:
        raise VendorError('the stock vendor build.prop must set ' + VENDOR_PATCH_PROPERTY + ' exactly once')
    patch = iso_date(values[0], 'the stock vendor patch level')
    if release_date is not None and patch > iso_date(release_date, 'the factory package release date'):
        raise VendorError(f'the stock vendor patch level {patch} is later than the factory package release date')
    if patch > (today or datetime.datetime.now(datetime.timezone.utc).date()):
        raise VendorError(f'the stock vendor patch level {patch} is in the future')
    return patch.isoformat()


def stock_vendor_patch_level(recipe, inputs, scratch, release_date=None, today=None):
    """Authenticate the pinned stock vendor build.prop and read its patch level."""
    rows = [r for r in recipe.get('build_properties', []) if r['input'] == VENDOR_BUILD_PROP]
    if len(rows) != 1:
        raise VendorError('the selected-file recipe does not pin the stock vendor build.prop')
    copy = Path(scratch) / 'stock-vendor-build.prop'
    vendor_files.copy_verified(inputs, rows[0], copy)
    return vendor_patch_level(copy.read_bytes(), release_date, today)


def board_config(patch):
    return ('# ' + VENDOR_PATCH_PROPERTY + ' of the stock vendor image (' + VENDOR_BUILD_PROP + ').\n'
            'VENDOR_SECURITY_PATCH := ' + patch + '\n').encode()


# The firmware release table (firmware_release.py) sits at the tree root, next
# to the build files, and device-vendor.mk installs it for fwrelease.
FIRMWARE_TABLE = 'firmware-releases.txt'


def firmware_table_copy():
    return ('PRODUCT_COPY_FILES += vendor/fairphone/FP6/' + FIRMWARE_TABLE + ':$(TARGET_COPY_OUT_VENDOR)/'
            + firmware_release.TABLE_PATH.removeprefix('vendor/') + '\n').encode()


# Not selected: the stock audio_effects.xml and its four Qualcomm effect
# libraries. The device installs its own effects configuration (AOSP software
# effects, no DSP offload halves) and builds the VoIP pre-processing
# descriptors and volume listener from source (device audio/effects).
AUDIO_CONFIG_REWRITES = {
    'vendor/etc/audio/sku_volcano/resourcemanager_volcano_mtp_fps.xml': {
        'source_sha256': 'ad423c0311b365759c692c64bc5f25ddca7ee788e8b769f2d53f8e801d4e3513',
        'sha256': 'e507aa16508f03f8550b49e11ec7440bf32b68e1c29648a28bd27bd3f00d0f5e',
        'reason': 'Disable context detection and remove deferred sound-trigger/model configuration'},
    'vendor/etc/audio/sku_volcano/audio_policy_configuration.xml': {
        'source_sha256': '3bf77c8f71e1ad1186e226b177c0c4cc9881c9ed9bad91cf848b632b7f039219',
        'sha256': '9df51292f1d17bb3877f58fac0d29e58d24c1c6fd756183e4e513234e7173c4b',
        'reason': 'Route Bluetooth A2DP and LE audio through the AOSP software Bluetooth audio module instead of DSP '
                  'offload, and decode compressed music in the sandboxed software codecs instead of the DSP'},
}
# Primary-module device ports that exist only for Bluetooth DSP offload.
BT_OFFLOAD_PORTS = (b'BT A2DP Out', b'BT A2DP Headphones', b'BT A2DP Speaker', b'BT BLE Out',
                    b'BT BLE Speaker', b'BT BLE Broadcast', b'A2DP In', b'BLE In')


def bluetooth_software_audio_policy(data):
    """Drop the offload-only Bluetooth ports and compressed offload; use the AOSP Bluetooth module."""
    names = b'|'.join(re.escape(n) for n in BT_OFFLOAD_PORTS)
    derived = re.sub(rb'^[ \t]*<devicePort tagName="(?:' + names + rb')"[^>]*>.*?</devicePort>\n',
                     b'', data, flags=re.S | re.M)
    derived = re.sub(rb'^[ \t]*<route type="mix" sink="(?:' + names + rb')"\s+sources="[^"]*"/>\n',
                     b'', derived, flags=re.M)
    derived = re.sub(rb'sources="([^"]*)"', lambda m: b'sources="' + b','.join(
        s for s in m[1].split(b',') if s not in (b'A2DP In', b'BLE In')) + b'"', derived)
    # No compressed offload: apps cannot hand MP3, AAC, FLAC and other bitstreams to the
    # closed DSP decoders; Android's sandboxed software codecs decode them instead.
    derived = re.sub(rb'^[ \t]*<mixPort name="compressed_offload".*?</mixPort>\n', b'', derived, flags=re.S | re.M)
    derived = derived.replace(b',compressed_offload', b'')
    return derived.replace(
        b'        <!-- Bluetooth Audio HAL for hearing aid -->\n'
        b'        <xi:include href="/vendor/etc/bluetooth_qti_hearing_aid_audio_policy_configuration.xml"/>\n',
        b'        <!-- Bluetooth Audio HAL: AOSP software A2DP, hearing aid and LE audio -->\n'
        b'        <xi:include href="/vendor/etc/bluetooth_with_le_audio_policy_configuration_7_0.xml"/>\n')


def audio_config(path, data):
    """Pinned removal of sound-trigger and Bluetooth offload configuration."""
    rule = AUDIO_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('audio configuration differs from reviewed EU stock input')
    if path.endswith('/audio_policy_configuration.xml'):
        derived = bluetooth_software_audio_policy(data)
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
        'sha256': 'da955f72ded8e2d93776745149958b7f421c158ce54365330e629828af6e496d',
        'reason': 'Limit camera provider groups to camera, media (secure FastRPC node), oem_2907 (thermal) '
                  'and wakelock; drop the undeclared offline camera, postproc and AON interface lines and the '
                  'cam_event_inject fault-injection chown; add class cameraWatchdog so init restarts the '
                  'provider with cameraserver'},
    'vendor/etc/camera/camxoverridesettings.txt': {
        'source_sha256': '620b6aeb7fc0e57cb19971f0c64796563ccfb9439dba2ddb4151cd314d9ad3af',
        'sha256': 'ce093a922922fd98360fb93aefe436971adf24e1c46b23e0b6dedff2172891ee',
        'reason': 'Turn off CamX camera core dumps (text, binary and their offline logging): they keep '
                  'camera state, metadata and logs in /data/vendor/camera/coredump, and writing one after '
                  'a recovery can hang the provider'},
}

CAMX_NO_CORE_DUMPS = (
    b'\n# DiamaneOS: no camera core dumps. They hold camera state, metadata and logs in\n'
    b'# /data/vendor/camera/coredump, and writing one after a recovery can hang the provider.\n'
    b'enableCameraCoreDumpText=FALSE\n'
    b'enableCameraCoreDumpBinary=FALSE\n'
    b'enableCoredumpOfflineTextLogging=FALSE\n'
    b'enableCoredumpOfflineBinaryLogging=FALSE\n')


def camera_config(path, data):
    """Pinned reductions of the stock camera provider service definition and CamX overrides."""
    rule = CAMERA_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('camera configuration differs from reviewed EU stock input')
    if path.endswith('/camxoverridesettings.txt'):
        derived = data + CAMX_NO_CORE_DUMPS
    else:
        derived = camera_provider_rc(data)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived camera configuration differs from reviewed result')
    return derived


def camera_provider_rc(data):
    # The offline camera service is not declared (LIBRARY_VINTF), so the
    # provider does not advertise it either.
    derived = re.sub(rb'^    interface aidl vendor\.qti\.hardware\.camera\.offlinecamera\.IOfflineCameraService/default\n',
                     b'', data, flags=re.M)
    derived = re.sub(rb'^    interface vendor\.qti\.hardware\.camera\.(?:postproc|aon)@1\.0::\S+ \S+\n',
                     b'', derived, flags=re.M)
    derived = derived.replace(b'    group audio camera input drmrpc oem_2907 oem_2912 wakelock\n',
                              b'    group camera media oem_2907 wakelock\n')
    # cameraserver.rc restarts class cameraWatchdog with cameraserver, so a provider left with a
    # hung session (CamX close() that never returns) restarts with it, as AOSP intends.
    derived = derived.replace(b'    class hal\n', b'    class hal cameraWatchdog\n')
    return re.sub(rb'\non boot\n    chown cameraserver camera /sys/module/camera/parameters/cam_event_inject\n\Z',
                  b'', derived)


TELEPHONY_CONFIG_REWRITES = {
    'vendor/etc/init/qcrilNrd.rc': {
        'source_sha256': 'fbb2c179c4c4ede0ceb3445dbb7c169f198546f4d1eed672d2ff8287eb74e26a',
        'sha256': 'd41b734e8a936f052de2fbddd44ab9a441f1b9ff32f9ba6a5c8c47864edaf66f',
        'reason': 'Drop the log, readproc, diag (oem_2901) and SSG socket (oem_2912) groups from the radio daemon'},
    'vendor/etc/init/nicmd.rc': {
        'source_sha256': 'b756045f1b044b7f690bd3296fec9e82351cd8afee99ee5af8bdc2f8c1a3f7e3',
        'sha256': '569b4569f639f51a263134faece0db072b1facd06cadc262ece4fb968488e361',
        'reason': 'Start every nicmd thread as radio with only network administration, raw-network and wake-lock capabilities'},
    'vendor/etc/data/nicm_config.xml': {
        'source_sha256': 'd9c5fd8ab8be49512d6ce4bf628c1f7c3218de1c287569e3f19e64040d35d0a0',
        'sha256': '03914a36e14990016b6bf72da90b95230c4e67381b0faadd2b82454dd30defaf',
        'reason': 'Turn off the persistent nicmd file log (/data/vendor/nicmd/nicmd.log)'},
    'vendor/etc/qcril_database/qcrilNr.db': {
        'source_sha256': '02fe923c48da86a583c5017bc93f97fbcf05198d56ebf15626c014f84402d82c',
        'sha256': '355131cac7371611eb3518c03cff0e6728825e9be5f13e5d45f75bd27c652885',
        'reason': 'Turn off QCRIL power-up optimisation, which holds incoming SMS and USSD until an OEM-hook '
                  'UI-ready call we do not ship, and bump the version so an existing /data copy is upgraded'},
}


def telephony_config(path, data):
    """Pinned reductions of the stock radio daemon, nicmd service, nicmd log and QCRIL database."""
    rule = TELEPHONY_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('telephony configuration differs from reviewed EU stock input')
    if path.endswith('/qcrilNrd.rc'):
        derived = data.replace(b'    group radio cache inet misc audio log readproc wakelock oem_2901 oem_2912\n',
                               b'    group radio cache inet misc audio wakelock\n')
    elif path.endswith('/nicmd.rc'):
        service = b'service vendor.nicmd /system/vendor/bin/nicmd\n    class main\n'
        derived = data.replace(service, service + b'    user radio\n    group radio\n'
                               b'    capabilities NET_ADMIN NET_RAW BLOCK_SUSPEND\n')
    elif path.endswith('/qcrilNr.db'):
        # Same-length edits of two SQLite records (SQLite keeps no page
        # checksums): poweron_opt def_val 1 -> 0 and qcrildb_version 15.0 -> 16.0.
        derived = (data.replace(b'\x04M\x0f\x00persist.vendor.radio.poweron_opt1',
                                b'\x04M\x0f\x00persist.vendor.radio.poweron_opt0')
                       .replace(b'\x04+\x15\x00qcrildb_version15.0', b'\x04+\x15\x00qcrildb_version16.0'))
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


# The stock sensors multi-HAL configuration also loads the AOSP dynamic-sensor
# sub-HAL, which parses HID sensor descriptors from Bluetooth and USB devices
# (head trackers). The HAL has no hidraw access, so it cannot serve one; only
# the Qualcomm sub-HAL is loaded.
SENSORS_CONFIG_REWRITES = {
    'vendor/etc/sensors/hals.conf': {
        'source_sha256': 'bc557b49cb701087efcf16281e9e394c80a11e49693f144e84fed8e74b2f4f9b',
        'sha256': 'cc212c26215c9a282fc068a481d4f2ed42881f80c721109e731b3c65fcc5a020',
        'reason': 'Load only the Qualcomm sensors sub-HAL, not the AOSP dynamic-sensor (HID) sub-HAL'},
}


def sensors_config(path, data):
    """Pinned removal of the dynamic-sensor sub-HAL from the multi-HAL configuration."""
    rule = SENSORS_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('sensors configuration differs from reviewed EU stock input')
    derived = b''.join(line for line in data.splitlines(keepends=True)
                       if line.strip() != b'sensors.dynamic_sensor_hal.so')
    if [line.strip() for line in derived.splitlines()] != [b'sensors.qsh.so']:
        raise VendorError('derived sensors configuration loads an unreviewed sub-HAL')
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived sensors configuration differs from reviewed result')
    return derived


# The qcacld driver takes its wake-on-WLAN setting from this file and ignores
# the supplicant's WoWLAN triggers. Stock leaves gEnableWoW at the driver
# default 3 (magic packet and pattern match), so any peer on the network can
# wake the phone with a magic packet. 2 keeps the pattern wake-ups and drops
# the magic packet.
WIFI_CONFIG_REWRITES = {
    'vendor/etc/wifi/qca6750/WCNSS_qcom_cfg.ini': {
        'source_sha256': '08b2b77f0f3e10ebf4a8b3ef5394d3900e485077260d069d238d4b8733c84c9e',
        'sha256': '9ec6ad5e13b9bdfceb6fb3c2ae9ca3dc4b3dceff8b0717fd776e81fd0cdbab81',
        'reason': 'No wake-on-LAN magic packet: gEnableWoW=2 keeps pattern wake-ups only'},
}


def wifi_config(path, data):
    """Pinned addition of gEnableWoW=2 before the END marker the driver's parser stops at."""
    rule = WIFI_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('Wi-Fi configuration differs from reviewed EU stock input')
    if re.search(rb'^\s*gEnableWoW\s*=', data, re.M) or len(re.findall(rb'^END$', data, re.M)) != 1:
        raise VendorError('Wi-Fi configuration differs from reviewed EU stock input')
    derived = re.sub(rb'^END$', b'gEnableWoW=2\nEND', data, count=1, flags=re.M)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived Wi-Fi configuration differs from reviewed result')
    return derived


GNSS_CONFIG_REWRITES = {
    'vendor/etc/izat.conf': {
        'source_sha256': 'faaf2906fb7520b8c348e4f47e797d4a0b574d332cd647cc0c251a3352352c4f',
        'sha256': 'e1f26cf0524ed52d102e27f8efaa490598985455c73158cd4fb1eda257b50c6d',
        'reason': 'Disable Qualcomm cloud positioning, Wi-Fi scan injection and the uninstalled location daemons'},
    'vendor/etc/gps.conf': {
        'source_sha256': 'a89ca530acfa96685d87160ecf61ac49f3a26128dfa24372c4eb917ec7ccdd57',
        'sha256': '29dee8ed4ebde298418da8bd31669eaa2bb162f2085a698e8de767dd6114fbb8',
        'reason': 'Remove the Qualcomm XTRA time server and the diagnostic logging interface; log warnings and errors only'},
}
IZAT_DISABLED_PROCESSES = ('lowi-server', 'xtwifi-client', 'slim_daemon', 'xtra-daemon', 'edgnss-daemon', 'blpsvc')
GNSS_REQUIRED = {
    'vendor/etc/gps.conf': (b'\nLOG_BUFFER_ENABLED = 0\n', b'\nQXDM_LOG = 0\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n',
                            b'\nDEBUG_LEVEL = 2\n'),
    'vendor/etc/izat.conf': (b'\nGTP_MODE=DISABLED\n', b'\nFREE_WIFI_SCAN_INJECT=DISABLED\n',
                             b'\nSUPL_WIFI=DISABLED\n', b'\nWIFI_SUPPLICANT_INFO=DISABLED\n'),
}
GNSS_FORBIDDEN = {
    'vendor/etc/gps.conf': (b'xtracloud', b'izatcloud', b'://'),
    'vendor/etc/izat.conf': (b'xtracloud', b'izatcloud', b'://', b'\nPROCESS_STATE=ENABLED'),
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
    else:
        derived = data.replace(b'#NTP server\nNTP_SERVER=time.xtracloud.net\n', b'')
        derived = derived.replace(b'\nLOC_DIAGIFACE_ENABLED = 1\n', b'\nLOC_DIAGIFACE_ENABLED = 0\n')
        # Warnings and errors only, the level the engine library itself uses on user builds.
        derived = derived.replace(b'\nDEBUG_LEVEL = 3\n', b'\nDEBUG_LEVEL = 2\n')
    if (any(token not in derived for token in GNSS_REQUIRED[path])
            or any(token in derived for token in GNSS_FORBIDDEN[path])):
        raise VendorError('derived GNSS configuration violates its privacy invariants')
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived GNSS configuration differs from reviewed result')
    return derived


# Hardware video encoders only: every decoder stays
# the platform software decoder in the sandboxed mediaswcodec. Two pinned
# derivations keep the stock Qualcomm Codec2 service from exposing a hardware
# decoder by any path:
# - the target specification gets a "codecs-available" list with the five
#   encoders and no decoder; libqcodec2_platform collects the decoders,
#   encoders and OptionalCodecs lists into one set, and libqcodec2_v4l2codec
#   registers only the codecs in it (store listing and component creation
#   both go through that registry). An empty set means every codec, so the
#   invariants below also require OptionalCodecs to stay empty;
# - the codec list loses its decoder section, so MediaCodec (which only
#   creates codecs that MediaCodecList lists) cannot pick one even if the
#   service ever registered one.
MEDIA_ENCODERS = ('c2.qti.avc.encoder', 'c2.qti.hevc.encoder', 'c2.qti.hevc.encoder.cq',
                  'c2.qti.hevc.encoder.hdr', 'c2.qti.heic.encoder')
# Hardware video decoding (Settings switch, off by default; device
# media/media.mk): a second pair of pinned derivations, the _volcano_v1_hwdec
# variant, which the device selects at boot only when the owner turned the
# switch on. It adds the three non-secure decoders to both files; the secure
# (DRM) and low-latency variants stay out. The device's configuration check
# (media/seccomp) requires exactly these lists in the codec service.
MEDIA_HW_DECODERS = ('c2.qti.avc.decoder', 'c2.qti.hevc.decoder', 'c2.qti.vp9.decoder')
MEDIA_HWDEC_COPIES = {
    'vendor/etc/media_codecs_volcano_v1_hwdec.xml': {
        'from': 'vendor/etc/media_codecs_volcano_v1.xml',
        'source_sha256': '19704733060e7eaabc8d1cdd5b3101e9b6111af1fa379ef52c6f4f7370f68b9f',
        'sha256': 'deff1af8eb87870e4f90556cdbd86dace357e696c7ce8617f91be9f0369562a5',
        'reason': 'Codec list with hardware video decoding on: the hardware encoders and the three '
                  'non-secure hardware decoders'},
    'vendor/etc/media_volcano_v1_hwdec/video_system_specs.json': {
        'from': 'vendor/etc/media_volcano_v1/video_system_specs.json',
        'source_sha256': '994abc6e26e3225e3ee64cd6b460817036624a17b78d7a3a9bcb4930e96ccc09',
        'sha256': '2797fd3f359a33c14817544ef88ef2870b3552551f9b53112746fdfe103a253a',
        'reason': 'Target specification with hardware video decoding on: the five hardware encoders and '
                  'the three non-secure hardware decoders'},
}
MEDIA_CONFIG_REWRITES = {
    'vendor/etc/media_codecs_volcano_v1.xml': {
        'source_sha256': '19704733060e7eaabc8d1cdd5b3101e9b6111af1fa379ef52c6f4f7370f68b9f',
        'sha256': 'f3bab56928d262ef354814d4a77bbace55cd80c4c0ed56965a3e496c852090b5',
        'reason': 'Drop the hardware decoder section from the codec list; decoding stays in the software codecs'},
    'vendor/etc/media_volcano_v1/video_system_specs.json': {
        'source_sha256': '994abc6e26e3225e3ee64cd6b460817036624a17b78d7a3a9bcb4930e96ccc09',
        'sha256': 'eca3f4cbae94b0a19d4847085544f66501775eb94592f3c5198871a77e8d3dcd',
        'reason': 'List only the five hardware encoders as available, so the Codec2 service registers no decoder'},
}


def media_encoders_only_codec_list(data):
    """Replace the codec list's Decoders section with a note; every other byte stays."""
    derived, count = re.subn(
        rb'\n    <Decoders>\n.*?\n    </Decoders>\n',
        b'\n    <!-- DiamaneOS: no hardware decoders. Decoding stays in the platform\n'
        b'         software codecs (device media/media.mk). -->\n', data, flags=re.S)
    if count != 1:
        raise VendorError('codec list differs from reviewed structure')
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(derived)
    except ET.ParseError:
        raise VendorError('derived codec list is not well formed') from None
    names = [codec.get('name') for codec in root.iter('MediaCodec')]
    if root.findall('Decoders') or sorted(names) != sorted(MEDIA_ENCODERS):
        raise VendorError('derived codec list lists more than the hardware encoders')
    return derived


def media_decoder_codec_list(data):
    """Keep only the allowed hardware decoders in the codec list; every other byte stays."""
    section = re.search(rb'\n    <Decoders>\n.*?\n    </Decoders>\n', data, flags=re.S)
    if section is None or data.count(b'<Decoders>') != 1:
        raise VendorError('codec list differs from reviewed structure')
    block = re.compile(rb'        <MediaCodec name="([^"]+)" [^\n]*>\n(?:(?!        </MediaCodec>\n).*\n)*?'
                       rb'        </MediaCodec>\n')
    kept, removed = [], []
    def keep(match):
        name = match.group(1).decode()
        (kept if name in MEDIA_HW_DECODERS else removed).append(name)
        return match.group(0) if name in MEDIA_HW_DECODERS else b''
    decoders = block.sub(keep, section.group(0))
    if sorted(kept) != sorted(MEDIA_HW_DECODERS) or not removed:
        raise VendorError('codec list differs from reviewed structure')
    derived = data[:section.start()] + decoders + data[section.end():]
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(derived)
    except ET.ParseError:
        raise VendorError('derived codec list is not well formed') from None
    sections = {child.tag: [codec.get('name') for codec in child.iter('MediaCodec')]
                for child in root if child.tag in ('Decoders', 'Encoders')}
    if (sorted(sections.get('Decoders', [])) != sorted(MEDIA_HW_DECODERS)
            or sorted(sections.get('Encoders', [])) != sorted(MEDIA_ENCODERS)
            or b'secure-playback' in derived or b'.secure' in derived):
        raise VendorError('derived codec list lists more than the allowed hardware codecs')
    return derived


def media_target_spec_codecs(data):
    """Codec lists of a target specification (JSON with whole-line // comments)."""
    try:
        text = data.decode()
        if '/*' in text:
            raise ValueError('block comment')
        video = json.loads('\n'.join(line for line in text.split('\n')
                                     if not line.lstrip().startswith('//')))['Video']
    except (ValueError, KeyError, TypeError):
        raise VendorError('target specification is not well formed') from None
    return video.get('codecs-available'), video.get('OptionalCodecs')


def media_encoders_only_target_spec(data, decoders=()):
    """Add a codecs-available list naming only the hardware encoders (and the given decoders)."""
    anchor = b'\n        //\n        // Put below optional codecs under "OptionalCodecs" to enable it\n'
    if data.count(anchor) != 1 or b'"codecs-available"' in data:
        raise VendorError('target specification differs from reviewed structure')
    if decoders:
        note = (b'\n        // DiamaneOS: hardware video decoding on. libqcodec2_v4l2codec\n'
                b'        // registers only the codecs listed here (and under "OptionalCodecs",\n'
                b'        // which stays empty): the hardware encoders and the non-secure\n'
                b'        // hardware decoders.\n')
        listed = b'\n' + b',\n'.join(b'                "' + name.encode() + b'"' for name in decoders) + b'\n'
    else:
        note = (b'\n        // DiamaneOS: hardware encoders only. libqcodec2_v4l2codec registers\n'
                b'        // only the codecs listed here (and under "OptionalCodecs", which\n'
                b'        // stays empty); decoding stays in the platform software codecs.\n')
        listed = b'\n'
    block = (note + b'        "codecs-available": {\n'
             b'            "decoders": [' + listed + b'            ],\n'
             b'            "encoders": [\n'
             + b',\n'.join(b'                "' + name.encode() + b'"' for name in MEDIA_ENCODERS)
             + b'\n            ]\n'
             b'        },\n')
    derived = data.replace(anchor, block + anchor)
    available, optional = media_target_spec_codecs(derived)
    if available != {'decoders': list(decoders), 'encoders': list(MEDIA_ENCODERS)} or optional != []:
        raise VendorError('derived target specification enables more than the allowed hardware codecs')
    return derived


def media_config(path, data):
    """Pinned encoder-only derivations of the stock codec list and target specification."""
    rule = MEDIA_CONFIG_REWRITES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('media configuration differs from reviewed EU stock input')
    if path.endswith('.xml'):
        derived = media_encoders_only_codec_list(data)
    else:
        derived = media_encoders_only_target_spec(data)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived media configuration differs from reviewed result')
    return derived


def media_hwdec_config(path, data):
    """Pinned hardware-decoding derivation (MEDIA_HWDEC_COPIES) of a stock media configuration."""
    rule = MEDIA_HWDEC_COPIES[path]
    if hashlib.sha256(data).hexdigest() != rule['source_sha256']:
        raise VendorError('media configuration differs from reviewed EU stock input')
    if path.endswith('.xml'):
        derived = media_decoder_codec_list(data)
    else:
        derived = media_encoders_only_target_spec(data, MEDIA_HW_DECODERS)
    if hashlib.sha256(derived).hexdigest() != rule['sha256']:
        raise VendorError('derived media configuration differs from reviewed result')
    return derived


def generate(recipe, selection, inputs, output, *, notice_kind, stock, firmware_releases, aapt2=None,
             release_date=None):
    vendor_files.selection(recipe, stock)
    rendered = render(recipe, selection, notice_kind)
    if not isinstance(firmware_releases, bytes) or not firmware_releases:
        raise VendorError('the vendor product needs the firmware release table')
    rendered[FIRMWARE_TABLE] = firmware_releases
    rendered['device-vendor.mk'] += firmware_table_copy()
    provenance = {'operation': 'fp6-native-product-generation',
                  'scope': 'private-bringup',
                  'recipe_sha256': hashlib.sha256(encoded(recipe)).hexdigest(),
                  'elf_selection_sha256': hashlib.sha256(encoded(selection)).hexdigest(),
                  'renderer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'firmware_releases_sha256': hashlib.sha256(firmware_releases).hexdigest(),
                  'notice_kind': notice_kind, 'native_or_device_accepted': False}
    has_carrier_data = any(row['path'] == carrier_data.APK_PATH for row in recipe['files'])
    if has_carrier_data:
        if aapt2 is None:
            raise VendorError('stock carrier data requires --aapt2')
        provenance['carrier_extractor_sha256'] = hashlib.sha256(
            Path(carrier_data.__file__).read_bytes()).hexdigest()
        provenance['aapt2_sha256'] = hashlib.sha256(Path(aapt2).read_bytes()).hexdigest()
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
            vendor_patch = stock_vendor_patch_level(recipe, inputs, temporary, release_date)
            rendered['BoardConfigVendor.mk'] = board_config(vendor_patch)
            provenance['vendor_security_patch'] = vendor_patch
            for item in recipe.get('symlinks', []):
                vendor_files.verify_symlink(inputs, item)
            for item in recipe['files']:
                vendor_files.copy_verified(inputs, item, tree / 'files' / item['path'])
            for item in recipe['notices']:
                vendor_files.copy_verified(inputs, item, tree / 'notices' / item['sha256'])
            if has_carrier_data:
                assets, report = carrier_data.extract(tree / 'files' / carrier_data.APK_PATH, aapt2)
                if report['aapt2_sha256'] != provenance['aapt2_sha256']:
                    raise VendorError('carrier extraction tool changed during generation')
                asset_dir = tree / carrier_data.ASSET_DIRECTORY
                asset_dir.mkdir(parents=True)
                for name, data in assets.items():
                    (asset_dir / name).write_bytes(data)
                provenance['carrier_data'] = report
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
            provenance['derived_files'] = []
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
            for path, rewrite in SENSORS_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(sensors_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in WIFI_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(wifi_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in GNSS_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(gnss_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            # The hardware-decoding copies derive from the stock bytes, so they
            # are written before the encoder-only derivation replaces them.
            for path, rewrite in MEDIA_HWDEC_COPIES.items():
                copy = tree / 'files' / path
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(media_hwdec_config(path, (tree / 'files' / rewrite['from']).read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            for path, rewrite in MEDIA_CONFIG_REWRITES.items():
                config = tree / 'files' / path
                config.write_bytes(media_config(path, config.read_bytes()))
                provenance['derived_files'].append({'path': path, **rewrite})
            provenance['stock_shared_libraries'] = {
                path: stock_library_declaration(path, (tree / 'files' / path).read_bytes())
                for path in sorted(STOCK_LIBRARIES) if path in {i['path'] for i in recipe['files']}}
            for path, rewrite in sorted(NEEDED_REWRITES.items()):
                blob = tree / 'files' / path
                original = blob.read_bytes()
                if hashlib.sha256(original).hexdigest() != rewrite['source_sha256']:
                    raise VendorError('dependency rewrite input differs from reviewed blob')
                derived = original
                if 'symbols' in rewrite:
                    derived = rename_dynamic_symbols(derived, SYMBOL_RENAMES[rewrite['symbols']])
                derived = rewrite_needed(derived, rewrite['needed'], rewrite['replacement'])
                if hashlib.sha256(derived).hexdigest() != rewrite['sha256']:
                    raise VendorError('dependency rewrite differs from reviewed result')
                blob.write_bytes(derived)
                provenance['derived_files'].append({'path': path, 'source_sha256': rewrite['source_sha256'],
                    'sha256': rewrite['sha256'], 'reason': rewrite['reason']})
            rendered.update({'provenance.json': encoded(provenance), 'recipe.json': encoded(recipe)})
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
                generation_sha256=identity, inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(),
                vendor_security_patch=vendor_patch, firmware_releases_sha256=provenance['firmware_releases_sha256'],
                scope='private-bringup', native_or_device_accepted=False)


def release_date_of(stock, inventory):
    """The release date stock-inputs.json records for the pinned factory package, if any."""
    matches = [a for a in inventory.get('archives', []) if a.get('sha256') == stock['archive_sha256']]
    return matches[0].get('release_date') if len(matches) == 1 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe', type=Path, default=ROOT / 'config/fp6-minimal/vendor-files.json')
    parser.add_argument('--selection', type=Path, default=ROOT / 'config/fp6-minimal/vendor-elf.json')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notice-kind', required=True, help='reviewed Android notice classification')
    parser.add_argument('--aapt2', type=Path, help='aapt2 for selected stock carrier data extraction')
    args = parser.parse_args(argv)
    try:
        stock = safe_json.load_json(ROOT / 'config/fp6-stock-image-recipe.json')
        firmware = firmware_release.table(safe_json.load_json(ROOT / 'config/fp6-firmware-inventory.json'),
                                          safe_json.load_json(ROOT / 'config/fp6-build.json')['firmware_release'])
        result = generate(safe_json.load_json(args.recipe), safe_json.load_json(args.selection),
            args.inputs, args.output, notice_kind=args.notice_kind, aapt2=args.aapt2, stock=stock,
            firmware_releases=firmware,
            release_date=release_date_of(stock, safe_json.load_json(ROOT / 'config/stock-inputs.json')))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, EOFError, safe_json.JsonError):
        print('ERROR: unable to authenticate or publish native product inputs')
        return 2
