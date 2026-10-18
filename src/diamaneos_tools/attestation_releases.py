"""Signed list of each release's attestation values, for first pairings without a manufacturer root."""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import textwrap

from . import safe_json, signing_archive as za, signing_release
from .signing_inputs import (DEFAULT_CONFIG, ROOT, SigningError, command, fields, load_json, require,
                             safe_file, snapshot_file, validate_config)
from .signing_native import avb_descriptors, factory_authority, host_ssh_signature

FORMAT = 'diamaneos-attestation-releases'
PAYLOAD_FORMAT = 'diamaneos-attestation-releases-payload'
ALGORITHM = 'ECDSA_P256_SHA256'
HEX = re.compile(r'[0-9A-F]{64}')
DEVICE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
BUILD = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
# SubjectPublicKeyInfo DER of a P-256 key, up to its uncompressed point.
P256_SPKI = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d030107034200')
PROPERTY_FIELDS = ('osVersion', 'osPatchLevel', 'vendorPatchLevel', 'bootPatchLevel')
RELEASE_FIELDS = ('build', 'verifiedBootHash', *PROPERTY_FIELDS, 'attestationVersion', 'keyMintVersion')
MAX_FILE = 4 * 1024 * 1024


def device_config(product):
    require(isinstance(product, str) and DEVICE.fullmatch(product), 'Invalid product name')
    path = ROOT / 'config' / (product.lower() + '-attestation.json')
    require(path.is_file(), 'No attestation release config for ' + product)
    config = load_json(path)
    fields(config, {'schema_version', 'scope', 'device', 'attestation_version', 'keymint_version', 'properties'})
    require(type(config['schema_version']) is int and config['schema_version'] == 1
            and isinstance(config['device'], str) and DEVICE.fullmatch(config['device'])
            and all(type(config[k]) is int and config[k] > 0 for k in ('attestation_version', 'keymint_version')),
            'Invalid attestation release config')
    fields(config['properties'], PROPERTY_FIELDS)
    for field, name in config['properties'].items():
        kind = 'os_version' if field == 'osVersion' else 'security_patch'
        require(isinstance(name, str) and re.fullmatch(r'com\.android\.build\.[a-z_]+\.' + kind, name),
                'Invalid attestation property name')
    return config


# ------------------------------------------------------------ release values

def descriptor_text(info):
    """The descriptor list of an avbtool description. A release string cannot add one."""
    parts = re.split(r'(?m)^Descriptors:$', info)
    require(len(parts) == 2, 'AVB description has no single descriptor list')
    return parts[1]


def chained(info):
    return [name for name, location in avb_descriptors(descriptor_text(info)) if location is not None]


def property_values(infos, names):
    found = {name: [] for name in names}
    for info in infos:
        for line in descriptor_text(info).splitlines():
            match = re.fullmatch(r'\s*Prop: (\S+) -> (.*)', line)
            if match and match[1] in found:
                found[match[1]].append(match[2])
    for name, values in found.items():
        require(len(values) == 1 and re.fullmatch(r"'[^']*'", values[0]),
                'AVB property is missing, repeated or not text: ' + name)
    return {name: values[0][1:-1] for name, values in found.items()}


def os_version(value):
    match = re.fullmatch(r'([1-9][0-9]?)(?:\.([0-9]{1,2}))?(?:\.([0-9]{1,2}))?', value)
    require(match, 'Invalid AVB OS version property')
    major, minor, patch = (int(part or 0) for part in match.groups())
    return major * 10000 + minor * 100 + patch


def patch_date(value):
    require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value), 'Invalid AVB security patch property')
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise SigningError('Invalid AVB security patch property') from None


def boot_key(run, public_key, scratch):
    """verifiedBootKey: SHA-256 of the AVB public key in avbtool's binary form (avb_pkmd.bin)."""
    output = Path(scratch) / 'avb_pkmd.bin'
    run(['extract_public_key', '--key', public_key, '--output', output])
    data = output.read_bytes()
    require(0 < len(data) <= 1024 * 1024, 'Invalid AVB public key export')
    return hashlib.sha256(data).hexdigest().upper()


def release_values(run, images, scratch, config, build, verified_boot_key):
    """One release's values from vbmeta.img and its chained images. run(arguments) calls avbtool."""
    require(isinstance(build, str) and BUILD.fullmatch(build), 'Invalid release build name')
    vbmeta = safe_file(images, 'vbmeta.img')
    infos = [run(['info_image', '--image', vbmeta])]
    for name in chained(infos[0]):
        infos.append(run(['info_image', '--image', safe_file(images, name + '.img')]))
        require(not chained(infos[-1]), 'A chained partition chains another partition')
    # libavb hashes the same vbmeta structures, in this order, for the attested boot hash.
    output = Path(scratch) / 'vbmeta.digest'
    run(['calculate_vbmeta_digest', '--image', vbmeta, '--hash_algorithm', 'sha256', '--output', output])
    digest = output.read_text(encoding='ascii').strip()
    require(re.fullmatch('[0-9a-f]{64}', digest), 'Unexpected vbmeta digest output')
    props = property_values(infos, config['properties'].values())
    value = {field: props[config['properties'][field]] for field in PROPERTY_FIELDS}
    os_patch, vendor_patch, boot_patch = (patch_date(value[field]) for field in PROPERTY_FIELDS[1:])
    release = {'build': build, 'verifiedBootHash': digest.upper(), 'osVersion': os_version(value['osVersion']),
               'osPatchLevel': os_patch.year * 100 + os_patch.month,
               'vendorPatchLevel': int(vendor_patch.strftime('%Y%m%d')),
               'bootPatchLevel': int(boot_patch.strftime('%Y%m%d')),
               'attestationVersion': config['attestation_version'], 'keyMintVersion': config['keymint_version']}
    record = {'device': config['device'], 'verifiedBootKey': verified_boot_key, 'release': release}
    validate_record(record)
    return record


def publication_release(images_archive, plan, tools, keys):
    """Release values of a verified publication, from the vbmeta images in its image archive."""
    config = device_config(plan['provenance']['product'])
    run = lambda arguments: tools.run('avbtool', arguments)
    with tempfile.TemporaryDirectory(dir=tools.scratch, prefix='attestation-') as directory:
        root = Path(directory)
        images = root / 'images'
        images.mkdir()
        with za.archive(images_archive, allow_links=False) as handle:
            za.extract(handle, 'vbmeta.img', images / 'vbmeta.img')
            for name in chained(run(['info_image', '--image', images / 'vbmeta.img'])):
                require(name in plan['partitions'], 'AVB chains a partition the publication lacks')
                za.extract(handle, name + '.img', images / (name + '.img'))
        return release_values(run, images, root, config, plan['provenance']['build_number'],
                              boot_key(run, keys.avb(), root))


# ------------------------------------------------------------ list format

def validate_release(release):
    require(isinstance(release, dict) and set(release) == set(RELEASE_FIELDS)
            and isinstance(release['build'], str) and BUILD.fullmatch(release['build'])
            and isinstance(release['verifiedBootHash'], str) and HEX.fullmatch(release['verifiedBootHash'])
            and all(type(release[k]) is int and 0 < release[k] < 2 ** 31 for k in RELEASE_FIELDS[2:]),
            'Invalid release entry')
    days = ('vendorPatchLevel', 'bootPatchLevel')
    require(10 ** 5 <= release['osPatchLevel'] < 10 ** 6 and all(10 ** 7 <= release[k] < 10 ** 8 for k in days),
            'Invalid release patch level')
    try:
        datetime.date(release['osPatchLevel'] // 100, release['osPatchLevel'] % 100, 1)
        for field in days:
            datetime.datetime.strptime(str(release[field]), '%Y%m%d')
    except ValueError:
        raise SigningError('Invalid release patch level') from None


def validate_record(record):
    fields(record, {'device', 'verifiedBootKey', 'release'})
    require(isinstance(record['device'], str) and DEVICE.fullmatch(record['device'])
            and isinstance(record['verifiedBootKey'], str) and HEX.fullmatch(record['verifiedBootKey']),
            'Invalid release record')
    validate_release(record['release'])


def validate_payload(payload):
    fields(payload, {'format', 'version', 'sequence', 'issued', 'devices'})
    require(payload['format'] == PAYLOAD_FORMAT and type(payload['version']) is int and payload['version'] == 1
            and type(payload['sequence']) is int and 0 < payload['sequence'] < 2 ** 63
            and isinstance(payload['issued'], str) and isinstance(payload['devices'], list),
            'Invalid release list payload')
    try:
        datetime.datetime.strptime(payload['issued'], '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        raise SigningError('Invalid release list issue time') from None
    ids = []
    for device in payload['devices']:
        fields(device, {'id', 'verifiedBootKey', 'releases'})
        require(isinstance(device['releases'], list) and device['releases'], 'Device lists no release')
        builds, hashes = set(), set()
        for release in device['releases']:
            validate_record({'device': device['id'], 'verifiedBootKey': device['verifiedBootKey'], 'release': release})
            require(release['build'] not in builds and release['verifiedBootHash'] not in hashes,
                    'Release list repeats a build or boot hash')
            builds.add(release['build'])
            hashes.add(release['verifiedBootHash'])
        ids.append(device['id'])
    require(len(ids) == len(set(ids)), 'Release list repeats a device')


def merged(previous, record, withdraw, issued):
    """The next payload: listed releases stay unless withdrawn, and the new release is appended."""
    devices = {device['id']: dict(device, releases=list(device['releases']))
               for device in (previous or {}).get('devices', [])}
    for build in withdraw:
        holders = [device for device in devices.values() if any(r['build'] == build for r in device['releases'])]
        require(holders, 'No listed release to withdraw: ' + build)
        for device in holders:
            device['releases'] = [r for r in device['releases'] if r['build'] != build]
    if record is not None:
        device = devices.setdefault(record['device'], {'id': record['device'], 'verifiedBootKey':
                                                       record['verifiedBootKey'], 'releases': []})
        require(device['verifiedBootKey'] == record['verifiedBootKey'],
                'Release has another verified boot key than its listed device')
        require(all(r['build'] != record['release']['build']
                    and r['verifiedBootHash'] != record['release']['verifiedBootHash'] for r in device['releases']),
                'Release is already listed')
        device['releases'].append(record['release'])
    payload = {'format': PAYLOAD_FORMAT, 'version': 1, 'sequence': (previous or {}).get('sequence', 0) + 1,
               'issued': issued, 'devices': [devices[k] for k in sorted(devices) if devices[k]['releases']]}
    validate_payload(payload)
    return payload


def encode_payload(payload):
    return json.dumps(payload, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def strict_base64(text):
    require(isinstance(text, str), 'Invalid base64 field')
    try:
        data = base64.b64decode(text, validate=True)
    except ValueError:
        raise SigningError('Invalid base64 field') from None
    require(base64.b64encode(data).decode() == text, 'Base64 field is not canonical')
    return data


def envelope_bytes(payload, public_key, signature):
    value = {'format': FORMAT, 'version': 1, 'payload': base64.b64encode(payload).decode(),
             'signatures': [{'keyId': key_id(public_key), 'algorithm': ALGORITHM,
                             'signature': base64.b64encode(signature).decode()}]}
    return (json.dumps(value, indent=2) + '\n').encode()


def verified_payload(data, public_key, *, after=0):
    """Payload of a list that public_key signed, with a sequence above after."""
    envelope = safe_json.loads(data)
    fields(envelope, {'format', 'version', 'payload', 'signatures'})
    require(envelope['format'] == FORMAT and type(envelope['version']) is int and envelope['version'] == 1
            and isinstance(envelope['signatures'], list), 'Invalid release list envelope')
    payload = strict_base64(envelope['payload'])
    ids, matching = [], []
    for entry in envelope['signatures']:
        fields(entry, {'keyId', 'algorithm', 'signature'})
        require(entry['algorithm'] == ALGORITHM and isinstance(entry['keyId'], str) and HEX.fullmatch(entry['keyId']),
                'Invalid release list signature entry')
        ids.append(entry['keyId'])
        if entry['keyId'] == key_id(public_key):
            matching.append(strict_base64(entry['signature']))
    require(len(ids) == len(set(ids)) and len(matching) == 1, 'Release list lacks a signature by the expected key')
    verify_signature(payload, matching[0], public_key)
    value = safe_json.loads(payload)
    validate_payload(value)
    require(value['sequence'] > after, 'Release list sequence does not increase')
    return value


# ------------------------------------------------------------ signatures

def openssl(arguments):
    return command(['/usr/bin/openssl', *arguments], env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})


def p256(der):
    require(len(der) == 91 and der.startswith(P256_SPKI) and der[26] == 4, 'Signer key is not a P-256 public key')
    return der


def public_key(path):
    return p256(openssl(['pkey', '-pubin', '-in', path, '-outform', 'DER']))


def key_id(der):
    return hashlib.sha256(der).hexdigest().upper()


def verify_signature(data, signature, der):
    require(8 <= len(signature) <= 72 and signature[0] == 0x30, 'Invalid ECDSA signature encoding')
    pem = '\n'.join(['-----BEGIN PUBLIC KEY-----', *textwrap.wrap(base64.b64encode(der).decode(), 64),
                     '-----END PUBLIC KEY-----', ''])
    with tempfile.TemporaryDirectory(prefix='attestation-releases-') as directory:
        root = Path(directory)
        for name, value in (('payload', data), ('signature', signature), ('key.pem', pem.encode())):
            (root / name).write_bytes(value)
        try:
            openssl(['dgst', '-sha256', '-verify', root / 'key.pem', '-signature', root / 'signature', root / 'payload'])
        except SigningError:
            raise SigningError('Release list signature does not verify') from None


class TestSigner:
    """A local P-256 PEM key for test lists. A signer backend has public (SPKI DER) and sign(data)."""

    def __init__(self, key):
        self.key = Path(key)
        self.public = p256(openssl(['pkey', '-in', self.key, '-pubout', '-outform', 'DER']))

    def sign(self, data):
        with tempfile.TemporaryDirectory(prefix='attestation-releases-') as directory:
            payload = Path(directory) / 'payload'
            payload.write_bytes(data)
            return openssl(['dgst', '-sha256', '-sign', self.key, payload])


# ------------------------------------------------------------ commands

def read_bounded(path, limit=MAX_FILE):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Input exceeds its limit')
    return data


def write_new(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)


def result_release(args, scratch):
    """The release record of a signing verify result. Production lists need its signature by the release
    factory key and a release result."""
    result_path = snapshot_file(args.signing_result, scratch / 'result.json')
    if args.signing_result_signature is not None or not args.test:
        require(args.signing_result_signature is not None and args.keys is not None,
                'Production lists need the signing result signature and the public key inventory')
        policy = load_json(DEFAULT_CONFIG)
        validate_config(policy)
        authority, identity = factory_authority(args.keys, policy, release=not args.test)
        signature = snapshot_file(args.signing_result_signature, scratch / 'result.json.sig', 1024 * 1024)
        host_ssh_signature(result_path, signature, authority, identity, policy['result_namespace'], scratch)
    result = load_json(result_path)
    require(isinstance(result, dict) and result.get('status') == 'PASS'
            and result.get('artifact_signatures_verified') is True, 'Signing result did not verify a publication')
    require(result.get('mode') == ('qualification' if args.test else 'release'),
            'Test lists take qualification results, production lists take release results')
    record = result.get('attestation_release')
    validate_record(record)
    require(record['release']['build'] == result['provenance']['build_number'],
            'Release record differs from the signing result build')
    return record


def prepare(args):
    require(args.signer != 'test' or (args.test and args.test_key), 'The test signer needs --test and --test-key')
    require(args.signer != 'detached' or args.public_key, 'The detached signer needs the signer public key')
    require((args.previous is not None) != args.first, 'Give the previous list or --first')
    require(args.entry is None or args.test, 'Release entries from images are for test lists only')
    signer = TestSigner(args.test_key) if args.signer == 'test' else None
    trusted = signer.public if signer else public_key(args.public_key)
    if not args.test:
        signing_release.refuse(signing_release.list_signer_problems(trusted))
    previous = verified_payload(read_bounded(args.previous), trusted) if args.previous else None
    with tempfile.TemporaryDirectory(prefix='attestation-releases-') as directory:
        record = (result_release(args, Path(directory)) if args.signing_result else
                  load_json(args.entry) if args.entry else None)
    if record is not None:
        validate_record(record)
    require(record is not None or args.withdraw, 'Nothing to add or withdraw')
    issued = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = encode_payload(merged(previous, record, args.withdraw, issued))
    if signer:
        data = envelope_bytes(payload, trusted, signer.sign(payload))
        verified_payload(data, trusted, after=previous['sequence'] if previous else 0)
        write_new(args.output, data)
        print('Release list signed with the test key: ' + str(args.output))
        return 0
    digest = Path(str(args.output) + '.sha256')
    require(not args.output.exists() and not digest.exists(), 'Output already exists')
    write_new(args.output, payload)
    write_new(digest, (hashlib.sha256(payload).hexdigest() + '  ' + args.output.name + '\n').encode())
    print('Payload for the offline signer: ' + str(args.output) + ' and ' + digest.name)
    return 0


def trusted_key(args):
    """The signer public key. A release list must have the DiamaneOS release-list signer."""
    trusted = public_key(args.public_key)
    if args.release:
        signing_release.refuse(signing_release.list_signer_problems(trusted))
    return trusted


def attach(args):
    require((args.previous is not None) != args.first, 'Give the previous list or --first')
    trusted = trusted_key(args)
    previous = verified_payload(read_bounded(args.previous), trusted) if args.previous else None
    data = envelope_bytes(read_bounded(args.payload), trusted, read_bounded(args.signature, 1024))
    verified_payload(data, trusted, after=previous['sequence'] if previous else 0)
    write_new(args.output, data)
    print('Release list verified and written: ' + str(args.output))
    return 0


def verify(args):
    trusted = trusted_key(args)
    after = verified_payload(read_bounded(args.previous), trusted)['sequence'] if args.previous else 0
    payload = verified_payload(read_bounded(args.list), trusted, after=after)
    count = sum(len(device['releases']) for device in payload['devices'])
    print(f"Release list: PASS, sequence {payload['sequence']}, {count} releases")
    return 0


def entry(args):
    config = device_config(args.product)
    avbtool = shutil.which(args.avbtool)
    require(avbtool is not None, 'avbtool not found')
    with tempfile.TemporaryDirectory(prefix='attestation-entry-') as directory:
        scratch = Path(directory)
        env = {'PATH': str(Path(avbtool).parent) + ':/usr/bin:/bin', 'HOME': str(scratch), 'TMPDIR': str(scratch),
               'LANG': 'C', 'LC_ALL': 'C'}
        run = lambda arguments: command([avbtool, *arguments], env=env).decode('utf-8', 'strict')
        run(['verify_image', '--image', safe_file(args.images, 'vbmeta.img'), '--key', args.avb_public_key,
             '--follow_chain_partitions'])
        record = release_values(run, args.images, scratch, config, args.build,
                                boot_key(run, args.avb_public_key, scratch))
    write_new(args.output, (json.dumps(record, indent=2) + '\n').encode())
    print('Release entry written: ' + str(args.output))
    return 0


def parser():
    root = argparse.ArgumentParser(prog='diamaneos attestation-releases',
                                   description='Signed list of each release\'s attestation values')
    commands = root.add_subparsers(dest='command', required=True)
    values = commands.add_parser('entry', help='release values from verified images, for test lists')
    values.add_argument('--images', type=Path, required=True, help='directory with vbmeta.img and every image it covers')
    values.add_argument('--avb-public-key', type=Path, required=True, help='AVB public key (PEM)')
    values.add_argument('--build', required=True)
    values.add_argument('--product', default='FP6')
    values.add_argument('--avbtool', default='avbtool')
    values.add_argument('--output', type=Path, required=True)
    build = commands.add_parser('prepare', help='merge a release into the list and sign it or write it for signing')
    build.add_argument('--previous', type=Path)
    build.add_argument('--first', action='store_true', help='start a new list at sequence 1')
    source = build.add_mutually_exclusive_group()
    source.add_argument('--signing-result', type=Path, help='result of signing verify')
    source.add_argument('--entry', type=Path, help='output of entry (test lists)')
    build.add_argument('--signing-result-signature', type=Path)
    build.add_argument('--keys', type=Path, help='public key inventory of the signing result authority')
    build.add_argument('--withdraw', action='append', default=[], metavar='BUILD')
    build.add_argument('--signer', choices=('test', 'detached'), required=True)
    build.add_argument('--test', action='store_true', help='test list: qualification results or image entries only')
    build.add_argument('--test-key', type=Path, help='local P-256 private key (PEM) of the test signer')
    build.add_argument('--public-key', type=Path, help='signer public key (PEM) for the detached signer')
    build.add_argument('--output', type=Path, required=True)
    add = commands.add_parser('attach', help='add the offline signer\'s DER signature and verify the list')
    for name in ('payload', 'signature', 'public-key', 'output'):
        add.add_argument('--' + name, type=Path, required=True)
    add.add_argument('--previous', type=Path)
    add.add_argument('--first', action='store_true')
    check = commands.add_parser('verify', help='verify a list against the signer public key')
    check.add_argument('--list', type=Path, required=True)
    check.add_argument('--public-key', type=Path, required=True)
    check.add_argument('--previous', type=Path)
    for sub in (add, check):
        sub.add_argument('--release', action='store_true', help='a release list: refuse any other signer than '
                         'the DiamaneOS release-list key')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return {'entry': entry, 'prepare': prepare, 'attach': attach, 'verify': verify}[args.command](args)
    except (SigningError, safe_json.JsonError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        message = (str(error) if isinstance(error, (SigningError, safe_json.JsonError)) else
                   'Output already exists' if isinstance(error, FileExistsError) else 'Invalid attestation release input')
        print('ERROR: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
