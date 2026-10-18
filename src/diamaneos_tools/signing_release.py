"""The DiamaneOS release keys. Release mode refuses every key that is not one of them.

config/release-keys.json pins the public half of each release key: certificates by the
SHA-256 of their DER form, the AVB key as avb_pkmd.bin and as its public key DER, the
attestation release-list signer as its public key DER, and the factory signer as its
allowed_signers line. A release run compares every key it uses or
finds with these pins and names each one that differs.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import zipfile

from .signing_inputs import CERT_ROLES, ROOT, SHA256, SigningError, fields, load_json, require, safe_file

PINS = ROOT / 'config/release-keys.json'
CERTIFICATE = re.compile(rb'-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----', re.S)
PUBLIC_KEY = re.compile(rb'-----BEGIN PUBLIC KEY-----(.+?)-----END PUBLIC KEY-----', re.S)
OTA_CERTIFICATES = re.compile(r'(^|/)etc/security/otacerts\.zip$')
DEX = re.compile(r'classes[0-9]*\.dex')
MAX_MEMBER = 256 * 1024 ** 2


def pins() -> dict:
    value = load_json(PINS)
    fields(value, {'schema_version', 'certificates', 'avb_pkmd_sha256', 'avb_public_key_sha256',
                   'release_list_signer_sha256', 'factory_signer', 'apps', 'catalog'})
    require(value['schema_version'] == 1 and isinstance(value['certificates'], dict)
            and set(value['certificates']) == CERT_ROLES and isinstance(value['apps'], dict) and value['apps']
            and all(isinstance(d, str) and SHA256.fullmatch(d) for d in
                    [*value['certificates'].values(), value['avb_pkmd_sha256'], value['avb_public_key_sha256'],
                     value['release_list_signer_sha256'], *value['apps'].values()])
            and isinstance(value['factory_signer'], str) and len(value['factory_signer'].split()) == 3
            and isinstance(value['catalog'], dict) and set(value['catalog']) == {'apk', 'key'}
            and value['catalog']['apk'] in value['apps']
            and re.fullmatch(r'[A-Za-z0-9+/]{56}', str(value['catalog']['key'])), 'Invalid release key pins')
    return value


def refuse(problems) -> None:
    """Stop with every key that differs from the release keys."""
    require(not problems, 'Release mode refuses keys that are not the DiamaneOS release keys: ' + ', '.join(problems))


def pem_digests(text: bytes, block=CERTIFICATE) -> list:
    """SHA-256 of the DER form of each PEM block."""
    try:
        return [hashlib.sha256(base64.b64decode(b''.join(body.split()), validate=True)).hexdigest()
                for body in block.findall(text)]
    except binascii.Error:
        raise SigningError('Malformed PEM data') from None


def key_problems(certificates=None, avb_pkmd_sha256=None) -> list:
    """The roles whose certificate SHA-256 (DER) or AVB key SHA-256 (avb_pkmd.bin) is not pinned."""
    value = pins()
    problems = []
    if certificates is not None:
        problems += [role for role in sorted(CERT_ROLES) if certificates.get(role) != value['certificates'][role]]
    if avb_pkmd_sha256 is not None and avb_pkmd_sha256 != value['avb_pkmd_sha256']:
        problems.append('avb')
    return problems


def inventory_problems(root, inventory, allowed_lines) -> list:
    """Every key of a public inventory that is not a release key, from its public files alone."""
    require(isinstance(inventory.get('certificate_roles'), dict), 'Malformed public certificate inventory')
    def read(name):
        path = safe_file(root, name)
        require(path.stat().st_size <= 1024 * 1024, 'Public key input exceeds its bounds')
        return path.read_bytes()
    certificates = {}
    for role, name in inventory['certificate_roles'].items():
        digests = pem_digests(read(name))
        if len(digests) == 1:
            certificates[role] = digests[0]
    problems = key_problems(certificates=certificates)
    if pem_digests(read(inventory['avb_public_key']), PUBLIC_KEY) != [pins()['avb_public_key_sha256']]:
        problems.append('avb')
    return problems + factory_problems(allowed_lines, inventory['factory']['identity'])


def factory_problems(allowed_lines, identity) -> list:
    """The factory signer: exactly the pinned identity and Ed25519 key."""
    signer = pins()['factory_signer']
    lines = [' '.join(line.split()) for line in allowed_lines]
    return [] if lines == [signer] and identity == signer.split()[0] else ['factory']


def list_signer_problems(public_key_der: bytes) -> list:
    """The attestation release list's signer, by its public key DER."""
    return [] if hashlib.sha256(public_key_der).hexdigest() == pins()['release_list_signer_sha256'] else ['release list signer']


def key_dir_problems(key_dir) -> list:
    """The certificate roles of a signing key directory that are not the release certificates."""
    found = {}
    for role in CERT_ROLES:
        path = Path(key_dir) / (role + '.x509.pem')
        digests = pem_digests(path.read_bytes()) if path.is_file() else []
        if len(digests) == 1:
            found[role] = digests[0]
    return key_problems(certificates=found)


def policy_problems(policy) -> list:
    """The release apps whose certificate in the signing policy is not their release certificate."""
    rules = {rule['metadata_name']: rule['certificate_sha256'] for rule in policy['presigned_packages']}
    return [name for name, digest in sorted(pins()['apps'].items()) if rules.get(name) != [digest]]


def content_problems(names, read, *, signed=True) -> list:
    """Release keys inside target-files: the App Store's catalog key and, once signed,
    the OTA certificates the phone and recovery trust."""
    value = pins()
    problems = []
    if signed:
        lists = sorted(name for name in names if OTA_CERTIFICATES.search(name))
        problems += [] if lists else ['otacerts.zip']
        for name in lists:
            found = [digest for text in zip_members(read(name)) for digest in pem_digests(text)]
            if found != [value['certificates']['releasekey']]:
                problems.append(name)
    apps = [name for name in names if PurePosixPath(name).name == value['catalog']['apk']]
    if len(apps) != 1:
        return problems + [value['catalog']['apk']]
    if not any(value['catalog']['key'].encode() in dex for dex in zip_members(read(apps[0]), DEX.fullmatch)):
        problems.append(apps[0] + ' catalog key')
    return problems


def zip_members(data, wanted=lambda name: True) -> list:
    """The bytes of the wanted members of a ZIP, or nothing for a broken one."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return [archive.read(name) for name in archive.namelist() if wanted(name)]
    except (zipfile.BadZipFile, OSError, ValueError):
        return []


def archive_problems(handle, *, signed=True) -> list:
    """content_problems for an open target-files ZIP."""
    def read(name):
        require(handle.getinfo(name).file_size <= MAX_MEMBER, 'Release key member exceeds its bound')
        return handle.read(name)
    return content_problems(handle.namelist(), read, signed=signed)
