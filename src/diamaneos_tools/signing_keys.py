"""The public key inventory of a key directory made as GrapheneOS's build guide makes one.

It reads only public files: each role's certificate, avb_pkmd.bin and id_ed25519.pub.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from . import signing_archive as za
from .build_workspace import CheckFailed
from .signing_inputs import CERT_ROLES, SigningError, require
from .signing_release import pem_digests

IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,63}')


def public_file(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024,
            f'{path.name} is missing or not a small regular file')
    data = path.read_bytes()
    require(b'PRIVATE KEY' not in data, f'{path.name} holds private key material')
    return data


def inventory_files(key_dir, identity, target_files, policy) -> dict:
    """The inventory's files by name, inventory.json included."""
    from .image_profiles import avb_key_pem
    key_dir = Path(key_dir)
    require(isinstance(identity, str) and IDENTITY.fullmatch(identity), 'Invalid factory signer identity')
    files = {}
    for role in sorted(CERT_ROLES):
        data = public_file(key_dir / (role + '.x509.pem'))
        require(len(pem_digests(data)) == 1, role + '.x509.pem is not one certificate')
        files[role + '.x509.pem'] = data
    try:
        files['avb.pub.pem'] = avb_key_pem(public_file(key_dir / 'avb_pkmd.bin'))
    except CheckFailed as error:
        raise SigningError(str(error)) from None
    public = public_file(key_dir / 'id_ed25519.pub').decode('ascii', 'replace').split()
    require(len(public) >= 2 and public[0] == 'ssh-ed25519', 'id_ed25519.pub is not an Ed25519 public key')
    files['factory.allowed_signers'] = f'{identity} {public[0]} {public[1]}\n'.encode()
    inventory = {'schema_version': 2, 'certificate_roles': {role: role + '.x509.pem' for role in sorted(CERT_ROLES)},
                 'avb_public_key': 'avb.pub.pem',
                 'factory': {'allowed_signers': 'factory.allowed_signers', 'identity': identity}}
    # A presigned APEX keeps its maker's payload key, which the policy pins.
    apexes = policy.get('presigned_apexes', [])
    if apexes:
        inventory['presigned_apex_payload_keys'] = {}
        with za.archive(target_files) as handle:
            for rule in apexes:
                with zipfile.ZipFile(io.BytesIO(handle.read(rule['artifact_path']))) as apex:
                    key = apex.read('apex_pubkey')
                require(hashlib.sha256(key).hexdigest() == rule['payload_key_sha256'],
                        'The payload key of ' + rule['metadata_name'] + ' differs from the signing policy')
                name = rule['metadata_name'].removesuffix('.apex') + '.avbpubkey'
                files[name] = key
                inventory['presigned_apex_payload_keys'][rule['metadata_name']] = name
    files['inventory.json'] = (json.dumps(inventory, indent=2, sort_keys=True) + '\n').encode()
    return files


def write_inventory(key_dir, identity, target_files, output, policy) -> None:
    """Write the inventory into output, a new directory."""
    files = inventory_files(key_dir, identity, target_files, policy)
    output = Path(output)
    output.mkdir(mode=0o700)
    for name, data in sorted(files.items()):
        with (output / name).open('xb') as stream:
            stream.write(data)
