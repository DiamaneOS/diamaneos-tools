#!/usr/bin/env python3
"""signing_native.host_ssh_signature(): installed OpenSSH checks of the release record, factory ZIP,
verification report and earlier results.

Two throwaway Ed25519 keys are made for the corpus ($FUZZ_KEYS): the trusted one and another. Only
seeds need the private keys.
Input: namespace, identity and allowed-signers choice, then the message and the signature file.
Property check: with only the trusted key allowed, a check passes only for a message the trusted key
signed in exactly that namespace. Only SigningError stops it.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools.signing_inputs import SigningError
    from diamaneos_tools.signing_native import host_ssh_signature

KEYS = Path(os.environ.get('FUZZ_KEYS', 'keys'))
WORK = Path(tempfile.mkdtemp(prefix='fuzz-ssh-'))
NAMESPACES = ['factory images', 'diamaneos-release-record', 'diamaneos-build-verification', 'diamaneos-signing-result',
              'file', 'factory images ', 'diamaneos-release-recor']
IDENTITIES = ['diamaneos-test', 'other', '*', 'diamaneos-test,other']
SIGNED = set()


def setup():
    KEYS.mkdir(parents=True, exist_ok=True)
    for name in ('throwaway-trusted', 'throwaway-other'):
        # A replay has only the public keys.
        if not (KEYS / (name + '.pub')).exists():
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'throwaway fuzz key',
                            '-f', KEYS / name], check=True)
    trusted = (KEYS / 'throwaway-trusted.pub').read_text().split()[:2]
    other = (KEYS / 'throwaway-other.pub').read_text().split()[:2]
    variants = [f'diamaneos-test {trusted[0]} {trusted[1]}\n', f'diamaneos-test {other[0]} {other[1]}\n',
                f'* {trusted[0]} {trusted[1]}\n', f'diamaneos-test {other[0]} {other[1]}\nother {trusted[0]} {trusted[1]}\n',
                f'diamaneos-test namespaces="file" {trusted[0]} {trusted[1]}\n']
    for i, text in enumerate(variants):
        (WORK / f'allowed-{i}').write_text(text)
    signed = KEYS / 'signed.json'
    if signed.exists():
        SIGNED.update(json.loads(signed.read_text()))


def sign(message, namespace, key):
    path = WORK / 'to-sign'
    path.write_bytes(message)
    out = subprocess.run(['/usr/bin/ssh-keygen', '-q', '-Y', 'sign', '-n', namespace, '-f', KEYS / key],
                         input=message, capture_output=True, check=True).stdout
    if key == 'throwaway-trusted':
        SIGNED.add(hashlib.sha256(namespace.encode() + b'\0' + message).hexdigest())
        (KEYS / 'signed.json').write_text(json.dumps(sorted(SIGNED)))
    return out


def TestOneInput(data):
    if len(data) < 5:
        return
    namespace = NAMESPACES[data[0] % len(NAMESPACES)]
    identity = IDENTITIES[data[1] % len(IDENTITIES)]
    allowed = data[2] % 5
    length = struct.unpack('>H', data[3:5])[0]
    message, signature = data[5:5 + length], data[5 + length:]
    (WORK / 'message').write_bytes(message)
    (WORK / 'message.sig').write_bytes(signature)
    try:
        host_ssh_signature(WORK / 'message', WORK / 'message.sig', WORK / f'allowed-{allowed}', identity, namespace, WORK)
    except SigningError:
        return
    except Exception as error:
        fuzzlib.record('host_ssh_signature raised ' + fuzzlib.where(error), data, repr(error))
        return
    # Allowed signers 0, 2 and 4 name only the trusted key.
    if allowed in (0, 2, 4) and hashlib.sha256(namespace.encode() + b'\0' + message).hexdigest() not in SIGNED:
        fuzzlib.record('accepted a message or namespace the trusted key never signed', data, namespace)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    message = b'{"schema_version": 1, "plan_sha256": "' + b'0' * 64 + b'", "artifacts": {}}\n'
    for i, namespace in enumerate(NAMESPACES[:4]):
        sig = sign(message, namespace, 'throwaway-trusted')
        (Path(directory) / f'seed-{i}').write_bytes(bytes([i, 0, 0]) + struct.pack('>H', len(message)) + message + sig)
    sig = sign(message, 'factory images', 'throwaway-other')
    (Path(directory) / 'seed-other').write_bytes(bytes([0, 0, 3]) + struct.pack('>H', len(message)) + message + sig)


if __name__ == '__main__':
    setup()
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
