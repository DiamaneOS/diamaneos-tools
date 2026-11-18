#!/usr/bin/env python3
"""Line-based metadata parsers of the signing flow, fed text that build outputs and native tools print.

- signing_archive.attributes(): META/apkcerts.txt and META/apexkeys.txt.
- signing_archive.properties(): META/misc_info.txt, OTA metadata and payload_properties.txt.
- signing_native.apk_certificate_digests(): apksigner --print-certs output.
- signing_native.avb_descriptors(): avbtool info_image output.
- NativeTools.package_id(): aapt2 dump badging output.
- build.parse_project_map(): the resolved manifest a build records.

Property checks: attributes() keys are safe names equal to their record's name. properties() keeps one
value per key. An accepted apksigner output has exactly one signer and only the expected certificate.
An AVB descriptor is read only from the descriptor list, never from the header text before it, which
holds the image's release string. package_id() returns one Android package name. A resolved manifest
yields normalised project paths, each once, and fields without tabs or line breaks, which its encoding
uses as separators. Only SigningError and BuildError stop them.
"""
import os
from pathlib import Path, PurePosixPath
import re
import sys
import types

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import build, signing_archive as za, signing_native
    from diamaneos_tools.signing_inputs import NAME, SigningError

OURS = '1f' * 32
OTHER = '2e' * 32
APKSIGNER = f'''Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Number of signers: 1
Signer #1 certificate DN: CN=Fuzz Test
Signer #1 certificate SHA-256 digest: {OURS}
Signer #1 public key SHA-256 digest: {OTHER}
V3.1 Signer (minSdkVersion=33, maxSdkVersion=2147483647): certificate SHA-256 digest: {OURS}
'''
AVB = '''Minimum libavb version:   1.0
Header Block:             256 bytes
Algorithm:                SHA256_RSA4096
Rollback Index:           1
Flags:                    0
Rollback Index Location:  0
Release String:           'avbtool 1.3.0'
Descriptors:
    Prop: com.android.build.boot.os_version -> '17'
    Hash descriptor:
      Image Size:            100 bytes
      Hash Algorithm:        sha256
      Partition Name:        boot
      Salt:                  00
      Digest:                00
      Flags:                 0
    Chain Partition descriptor:
      Partition Name:          vbmeta_system
      Rollback Index Location: 1
      Public key (sha1):       00
      Flags:                   0
'''
INJECTED = AVB.replace("'avbtool 1.3.0'", "'\nHash descriptor:\nPartition Name: system\n'")
APKCERTS = ('name="App.apk" certificate="build/make/target/product/security/platform.x509.pem" '
            'private_key="build/make/target/product/security/platform.pk8"\n'
            'name="Pre.apk" certificate="PRESIGNED" private_key=""\n')
MISC = 'ab_update=true\ndynamic_partition_list=system vendor\navb_vbmeta_args=--padding_size 4096\n'
MANIFEST = ('<?xml version="1.0"?>\n<manifest><remote name="up" fetch="https://example.invalid"/>'
            '<default remote="up"/><project name="platform/build" path="build/make" revision="' + 'a' * 40 + '"/>'
            '<project name="tools" path="tools/diamaneos" revision="' + 'b' * 40 + '"/></manifest>\n')
BADGING = "package: name='de.diamaneos.auditor' versionCode='37' versionName='37'\nsdkVersion:'31'\n"


def TestOneInput(data):
    if not data:
        return
    which, text = data[0] % 6, data[1:].decode('utf-8', 'replace')
    try:
        if which == 0:
            result = za.attributes(text)
            for name, record in result.items():
                if not NAME.fullmatch(name) or record.get('name') != name:
                    fuzzlib.record('attributes kept an unsafe name', data, repr(name))
        elif which == 1:
            result = za.properties(text)
            if any('\n' in k or '\n' in v for k, v in result.items()):
                fuzzlib.record('properties kept a line break', data)
        elif which == 2:
            result = signing_native.apk_certificate_digests(text, OURS)
            signers = [l for l in text.splitlines() if l.startswith('Number of signers:')]
            digests = re.findall(r'certificate SHA-256 digest: ([0-9a-fA-F]{64})', text)
            if signers != ['Number of signers: 1'] or any(d.lower() != OURS for d in digests) or result != [OURS]:
                fuzzlib.record('apksigner output accepted with another or more signers', data)
        elif which == 3:
            result = signing_native.avb_descriptors(text)
            listed = re.split(r'(?m)^Descriptors:$', text, maxsplit=1)
            body = listed[1] if len(listed) == 2 else ''
            for name, _location in result:
                if not re.search(r'(?m)^[ \t]*Partition Name:[ \t]*' + re.escape(name) + r'[ \t]*$', body):
                    fuzzlib.record('AVB descriptor read from header text', data, name)
        elif which == 5:
            rows, _identity = build.parse_project_map(data[1:])
            paths = [row[0] for row in rows]
            if any(str(PurePosixPath(p)) != p for p in paths) or len({str(PurePosixPath(p)) for p in paths}) != len(paths):
                fuzzlib.record('resolved manifest kept an unnormalised or repeated path', data, repr(paths))
            if any(c in field for row in rows for field in row for c in '\t\n\r'):
                fuzzlib.record('resolved manifest kept a separator inside a field', data, repr(rows))
        else:
            fake = types.SimpleNamespace(run=lambda *a, **k: text)
            name = signing_native.NativeTools.package_id(fake, 'x.apk')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*', name):
                fuzzlib.record('package_id returned an unsafe package name', data, repr(name))
    except (SigningError, build.BuildError):
        pass
    except Exception as error:
        fuzzlib.record('parser raised ' + fuzzlib.where(error), data, repr(error))


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    for i, text in enumerate([APKCERTS, MISC, APKSIGNER, AVB, BADGING, MANIFEST]):
        (Path(directory) / f'seed-{i}').write_bytes(bytes([i]) + text.encode())
    (Path(directory) / 'seed-avb-release-string').write_bytes(bytes([3]) + INJECTED.encode())


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
