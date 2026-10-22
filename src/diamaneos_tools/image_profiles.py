"""Image check profiles, and "diamaneos images check".

build.json's signing value selects the generic checks. A set signed with the
public test keys gets image_verify's checks unchanged. A set re-signed with its
own keys replaces the four that assume the public test keys with signed
counterparts of the same strictness. An unknown value stops the check. A set
signed with release-keys must carry the DiamaneOS release keys.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from . import build_workspace as bw, firmware, image_verify as iv, signing_release
from .build_workspace import CheckFailed, UsageError

PUBLIC_TEST_KEYS = 'public-test-keys'
SIGNED = ('release-keys', 'own-keys', 'qualification-keys', 'throwaway-test-keys')
RELEASE_KEYS = 'release-keys'
KEY_FILE = 'avb_pkmd.bin'
OTATOOLS = 'otatools.zip'
RSA_OID = bytes.fromhex('2a864886f70d010101')
PROPERTY_FILES = re.compile(r'(^|/)(build\.prop|prop\.default|default\.prop)$')
BUILD_PROPS = ('SYSTEM/build.prop', 'SYSTEM_EXT/etc/build.prop', 'PRODUCT/etc/build.prop', 'VENDOR/build.prop')


def generic_checks(record: dict) -> tuple[str, list]:
    """The profile build.json selects and its generic checks (id, why, check)."""
    signing = record.get('signing')
    if signing == PUBLIC_TEST_KEYS:
        return PUBLIC_TEST_KEYS, iv.GENERIC
    if signing not in SIGNED:
        raise ValueError(f'build.json signing {signing!r} selects no image check profile')
    return 'signed', [(check_id, *COUNTERPARTS.get(check_id, (why, func))) for check_id, why, func in iv.GENERIC]


def avb_key_pem(blob: bytes) -> bytes:
    """The PEM public key of an AVB RSA public key blob (avb_crypto.h: key size,
    n0inv, modulus, rr). AVB keys use exponent 65537."""
    bits = int.from_bytes(blob[:4], 'big')
    if len(blob) < 8 or not bits or bits % 64 or len(blob) != 8 + bits // 4:
        raise CheckFailed(KEY_FILE + ' is not an AVB RSA public key')

    def tlv(tag, body):
        if len(body) < 128:
            return bytes([tag, len(body)]) + body
        length = len(body).to_bytes((len(body).bit_length() + 7) // 8, 'big')
        return bytes([tag, 0x80 | len(length)]) + length + body

    def integer(value):
        return tlv(2, value.to_bytes(value.bit_length() // 8 + 1, 'big'))
    modulus = int.from_bytes(blob[8:8 + bits // 8], 'big')
    rsa = tlv(0x30, integer(modulus) + integer(65537))
    text = base64.b64encode(tlv(0x30, tlv(0x30, tlv(6, RSA_OID) + tlv(5, b'')) + tlv(3, b'\0' + rsa))).decode()
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    return ('-----BEGIN PUBLIC KEY-----\n' + '\n'.join(lines) + '\n-----END PUBLIC KEY-----\n').encode()


def set_key(v, directory: Path) -> Path:
    """A PEM copy of the set's AVB public key (avb_pkmd.bin), in directory.

    build.json and SHA256SUMS must name the same file. avbtool must turn the
    PEM back into exactly its bytes, and it must be none of the AOSP AVB test keys."""
    from .image_package import read_sums
    entry = v.record.get('avb_custom_key')
    digest = read_sums(v.images).get(KEY_FILE)
    if not digest or entry != {'file': KEY_FILE, 'sha256': digest}:
        raise CheckFailed(f'build.json and SHA256SUMS name no common avb_custom_key {KEY_FILE}')
    blob = (v.images / KEY_FILE).read_bytes()
    if hashlib.sha256(blob).hexdigest() != digest:
        raise CheckFailed(KEY_FILE + ' differs from SHA256SUMS')
    pem = directory / 'avb.pem'
    pem.write_bytes(avb_key_pem(blob))
    v.tools.run('avbtool', ['extract_public_key', '--key', pem, '--output', directory / 'avb.bin'])
    if (directory / 'avb.bin').read_bytes() != blob:
        raise CheckFailed(KEY_FILE + ' is not an AVB RSA public key with exponent 65537')
    tests = {v.src / v.config['avb']['test_key'], *(v.src / 'external/avb/test/data').glob('testkey_rsa*.pem')}
    for key in sorted(tests):
        v.tools.run('avbtool', ['extract_public_key', '--key', key, '--output', directory / 'test.bin'])
        if (directory / 'test.bin').read_bytes() == blob:
            raise CheckFailed(f'{KEY_FILE} is the AOSP AVB test key {key.name}')
    return pem


def vbmeta_key_problem(v) -> str:
    """Why vbmeta.img does not embed exactly avb_pkmd.bin as its public key, or ''.
    The platform parser in the source reads the embedded key."""
    from .signing_native import AVB_BINARY_AUTHORITY
    result = subprocess.run([sys.executable, '-I', '-c', AVB_BINARY_AUTHORITY, v.src / 'external/avb/avbtool.py',
                             v.images / 'vbmeta.img', v.images / KEY_FILE], capture_output=True, timeout=600)
    if result.returncode == 2:
        return f'vbmeta.img is not signed with {KEY_FILE}'
    if result.returncode:
        return 'cannot read the public key of vbmeta.img: ' + result.stderr.decode('utf-8', 'replace')[-500:]
    return ''


def check_signed_record(v):
    problems = []
    never_lock = v.record.get('never_lock')
    if not isinstance(never_lock, bool):
        problems.append('build.json never_lock is not true or false')
    elif v.record.get('official') is True and never_lock != (v.record['signing'] == 'qualification-keys'):
        # Sets that are not official may record either: their owners lock as they like.
        problems.append('an official set signed with release or throwaway keys carries the relock record '
                        '(never_lock false)' if never_lock else
                        'an official set signed with qualification keys must never be locked (never_lock true)')
    if not isinstance(v.record.get('release'), bool) or (v.record['release'] and v.record['signing'] != RELEASE_KEYS):
        problems.append('build.json release is not true or false, or true without release keys')
    if v.record['signing'] == RELEASE_KEYS and signing_release.key_problems(
            avb_pkmd_sha256=(v.record.get('avb_custom_key') or {}).get('sha256')):
        problems.append(f'{KEY_FILE} is not the DiamaneOS release AVB key')
    problems += iv.record_problems(v)
    try:
        with v.tools.scratch() as temporary:
            set_key(v, Path(temporary))
        problems += [problem for problem in [vbmeta_key_problem(v)] if problem]
    except CheckFailed as error:
        problems.append(str(error))
    return not problems, '; '.join(problems)


def spki_digest(certificate: bytes, form: str) -> str:
    """SHA-256 of a certificate's public key, through openssl."""
    def openssl(args, data):
        return subprocess.run(['/usr/bin/openssl', *args], input=data, capture_output=True, check=True,
                              timeout=60).stdout
    public = openssl(['x509', '-inform', form, '-noout', '-pubkey'], certificate)
    return hashlib.sha256(openssl(['pkey', '-pubin', '-outform', 'DER'], public)).hexdigest()


def certificate_problems(v) -> list:
    """The OTA and SELinux certificates the set carries that hold a public test key."""
    paths = sorted((v.src / 'build/make/target/product/security').glob('*.x509.pem'))
    paths += sorted((v.src / 'system/apex').rglob('*.x509.pem'))
    if not paths:
        raise CheckFailed('the source holds no public test certificates to compare with')
    tests = {spki_digest(path.read_bytes(), 'PEM'): path.name for path in paths}
    found = {}
    for name in v.tf.names:
        if name.endswith('/etc/security/otacerts.zip'):
            with zipfile.ZipFile(io.BytesIO(v.tf.read(name))) as archive:
                text = b''.join(archive.read(member) for member in archive.namelist())
            found[name] = [pem.encode() for pem in re.findall(
                r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', text.decode('latin-1'), re.S)]
        elif name.endswith('mac_permissions.xml'):
            found[name] = [bytes.fromhex(hex_der) for hex_der in re.findall(
                r'signature="([0-9a-fA-F]+)"', iv.text_of(v.tf.read(name)))]
    problems = [name + ' is missing' for name in ('SYSTEM/etc/security/otacerts.zip',
                                                   'SYSTEM/etc/selinux/plat_mac_permissions.xml')
                if not found.get(name)]
    for name, certificates in sorted(found.items()):
        for certificate in certificates:
            test = tests.get(spki_digest(certificate, 'DER' if name.endswith('.xml') else 'PEM'))
            if test:
                problems.append(f'{name} holds the public test key of {test}')
    return problems


def check_release_keys(v):
    problems = []
    if ('ro.build.tags', 'release-keys') not in iv.property_lines(v.tf.read('SYSTEM/build.prop')):
        problems.append('ro.build.tags is not release-keys')
    names = set(BUILD_PROPS) | {n for n in v.tf.names if PROPERTY_FILES.search(n) and not v.tf.is_symlink(n)}
    for name in sorted(names) + ['META/misc_info.txt']:
        data = v.tf.read(name)
        for key, value in iv.property_lines(data):
            if key.endswith('build.fingerprint') and not value.endswith('/release-keys'):
                problems.append(f'{name}: {key} does not end in release-keys')
            elif key.endswith('build.tags') and value != 'release-keys':
                problems.append(f'{name}: {key} is not release-keys')
        if b'test-keys' in data:
            problems.append(name + ' names test-keys')
    problems += certificate_problems(v)
    found = signing_release.content_problems(v.tf.names, v.tf.read) if v.record.get('signing') == RELEASE_KEYS else []
    if found:
        problems.append('not the DiamaneOS release keys: ' + ', '.join(found))
    return not problems, '; '.join(problems)


def check_signed_avb(v):
    chains = v.config['avb']['chains']
    with v.tools.scratch() as temporary:
        args = ['verify_image', '--image', v.images / 'vbmeta.img', '--key', set_key(v, Path(temporary)),
                '--follow_chain_partitions']
        for name, location in sorted(chains.items()):
            args += ['--expected_chain_partition', f'{name}:{location}:{v.images / KEY_FILE}']
        v.tools.run('avbtool', args, cwd=v.images)
    problems, info = iv.avb_layout(v)
    indexes = {}
    for name in ['vbmeta', *sorted(chains)]:
        if name != 'vbmeta':
            info = v.tools.run('avbtool', ['info_image', '--image', v.images / f'{name}.img'])
        header = info.split('Descriptors:')[0]
        # avbtool prints the release string as it is, so a field counts only once.
        if re.findall(r'(?m)^Flags:\s+(\d+)\s*$', header) != ['0']:
            problems.append(f'{name} does not keep AVB flags 0')
        indexes[name] = iv.rollback_index(info)
    if v.record.get('avb_rollback_indexes') != indexes:
        problems.append(f'rollback indexes are {indexes} (build.json avb_rollback_indexes: '
                        f'{v.record.get("avb_rollback_indexes")})')
    problems += iv.rollback_problems(v.record, indexes)
    return not problems, '; '.join(problems) or 'rollback indexes: ' + ', '.join(f'{n} {i}' for n, i in indexes.items())


def otatools(v, root: Path) -> iv.Tools:
    return iv.Tools(root / 'bin', v.src, v.tools.work)


def check_otatools_validators(v):
    from .image_package import read_sums
    from .signing_archive import unpack_tools
    if OTATOOLS in v.outside:
        tools_archive, detail = v.outside[OTATOOLS][0], 'otatools.zip of the build the set was signed from'
    else:
        entry, digest = v.record.get('otatools'), read_sums(v.images).get(OTATOOLS)
        if not digest or entry != {'file': OTATOOLS, 'sha256': digest} or bw.sha_file(v.images / OTATOOLS) != digest:
            return False, 'build.json and SHA256SUMS name no common otatools.zip'
        tools_archive, detail = v.images / OTATOOLS, 'otatools.zip of the set'
    archive = v.file(v.record['target_files']['file'])
    misc = iv.property_lines(v.tf.read('META/misc_info.txt'))
    chained = sorted({found[1] for key, _ in misc for found in [re.fullmatch(r'avb_(\w+)_key_path', key)]
                      if found and found[1] != 'vbmeta'})
    with v.tools.scratch() as temporary:
        root = Path(temporary)
        pem = set_key(v, root)
        tools = otatools(v, unpack_tools(tools_archive, root / 'otatools'))
        # The archive names the private keys it was signed with. The set's public key stands in for them.
        keys = ['--verity_key', pem] + [arg for name in chained for arg in (f'--avb_{name}_key_path', pem)]
        tools.run('validate_target_files', [archive, *keys], cwd=root)
        tools.run('check_target_files_vintf', [archive], cwd=root)
    return True, detail


def outside_files(images: Path, record: dict, target_files: Path | None, build_record: Path | None):
    """What factory images leave out, each as build.json binds it: the record of
    the build they were signed from, its otatools.zip, and the signed target-files.
    Returns name -> (path, SHA-256) and that record, both empty for a set that
    carries everything."""
    from .image_package import read_sums
    sums, outside, original = read_sums(images), {}, {}
    if OTATOOLS in sums:
        if build_record:
            raise bw.BuildStepError('the set carries otatools.zip. Leave out --build-record')
        if (record.get('otatools') or {}).get('sha256') != sums[OTATOOLS]:
            raise bw.BuildStepError('build.json and SHA256SUMS name no common otatools.zip')
    elif not build_record:
        raise bw.BuildStepError('the set does not carry otatools.zip. Give the build record it was signed from '
                                'with --build-record')
    elif bw.sha_file(build_record) != (record.get('signing_input') or {}).get('build_record_sha256'):
        raise bw.BuildStepError(f'{build_record} is not the build record the set was signed from')
    else:
        original = json.loads(build_record.read_bytes())
        entry = original.get('otatools') or {}
        if entry.get('file') != OTATOOLS or read_sums(build_record.parent).get(OTATOOLS) != entry.get('sha256'):
            raise bw.BuildStepError(f'{build_record} and its SHA256SUMS name no common otatools.zip')
        outside[OTATOOLS] = ((build_record.parent / OTATOOLS).absolute(), entry['sha256'])
    name, digest = record['target_files']['file'], record['target_files']['sha256']
    if name in sums:
        if target_files:
            raise bw.BuildStepError(f'the set carries {name}. Leave out --target-files')
    elif not target_files:
        raise bw.BuildStepError(f'the set does not carry {name}. Give the signed target-files with --target-files')
    elif bw.sha_file(target_files) != digest:
        raise bw.BuildStepError(f'{target_files} is not the {name} build.json names')
    else:
        outside[name] = (target_files.absolute(), digest)
    return outside, original


COUNTERPARTS = {
    'record': ('The image set matches its build record. An official set says whether its own AVB key may lock the '
               'phone: never with qualification keys, always with others. A release-keys set has the DiamaneOS '
               'release AVB key.', check_signed_record),
    'test-keys': ('A re-signed set says release-keys wherever a build names its keys, and no OTA or SELinux '
                  'certificate holds a public test key. A release-keys set trusts only the DiamaneOS release OTA '
                  'key, and its App Store only the DiamaneOS catalog key.', check_release_keys),
    'avb-chain': ('The AVB chain verifies with the set\'s own key, never an AOSP test key, and has the published FP6 '
                  'layout, flags 0, SHA-256 hashtrees and the rollback indexes build.json records, above 0 on an '
                  'official or release-keys set.', check_signed_avb),
    'validators': ('validate_target_files and check_target_files_vintf from the set\'s own otatools pass on the '
                   'archive.', check_otatools_validators),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='diamaneos images check',
        description='Check an image set as "build verify" does, with the profile its build.json selects. '
                    'Writes only the report and temporary files.')
    parser.add_argument('images', type=Path, help='the image set directory')
    parser.add_argument('--report', type=Path, required=True, help='the report to write: a new file outside the set')
    parser.add_argument('--workspace', type=Path,
                        help='the workspace whose source and vendor generations the checks use '
                             '(default: as for "diamaneos build")')
    parser.add_argument('--scratch', type=Path, help="temporary files (default: the workspace's work/tmp)")
    parser.add_argument('--target-files', type=Path,
                        help='the signed target-files, for factory images, which leave it out')
    parser.add_argument('--build-record', type=Path,
                        help='the build.json of the build that factory images were signed from. The otatools.zip '
                             'next to it runs the checks')
    args = parser.parse_args(argv)
    try:
        images, path = args.images.resolve(), args.report.absolute()
        if path.exists() or path.is_symlink():
            raise UsageError(f'{path} already exists')
        if path.resolve().is_relative_to(images):
            raise UsageError('the report must be outside the image set')
        from .image_package import read_sums
        ws = bw.locate(args.workspace)
        config, _ = bw.load_config('fp6-build.json')
        checks = json.loads((iv.ROOT / 'config/fp6-image-checks.json').read_bytes())
        # The host tools come from the set's own otatools.zip, or for factory images from the build's.
        record = json.loads((images / 'build.json').read_bytes())
        outside, original = outside_files(images, record, args.target_files, args.build_record)
        tools_archive, digest = outside.get(OTATOOLS) or (images / OTATOOLS, read_sums(images)[OTATOOLS])
        scratch = args.scratch or ws.work / 'tmp'
        bin_dir = iv.unpack_otatools(tools_archive, digest, scratch)
        try:
            report = iv.verify_set(images, ws, config, checks, iv.Tools(bin_dir, ws.src, scratch),
                                   firmware.load_inventory(), outside, original)
        finally:
            shutil.rmtree(bin_dir.parent.parent, ignore_errors=True)
        with path.open('xb') as stream:
            stream.write(bw.encoded(report))
    except (bw.BuildStepError, ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    for result in report['checks']:
        if result['status'] != 'PASS':
            print(f'FAIL {result["id"]}: {result["detail"]}')
    print(f'{report["profile"]} profile: {report["failed"]} of {report["checked"]} checks failed. Report: {path}')
    return 1 if report['failed'] else 0
