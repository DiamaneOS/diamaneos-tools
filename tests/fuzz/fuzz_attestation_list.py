#!/usr/bin/env python3
"""attestation_releases.verified_payload(): the signed release list, with real OpenSSL P-256 checks.

Lists are signed with a throwaway P-256 key made for the corpus ($FUZZ_KEYS), and some with a second
throwaway key that is not trusted. Only seeds need the private keys. The previous list has
sequence 2, issued at PREVIOUS_ISSUED.
Property checks on an accepted list: its payload bytes are exactly ones the trusted key signed. Its
sequence is above the previous one, and its issue time is not before the previous list's. Only
SigningError and safe_json's JsonError stop it: attestation-releases reports both as a refusal.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import attestation_releases as ar
    from diamaneos_tools.safe_json import JsonError
    from diamaneos_tools.signing_inputs import SigningError

KEYS = Path(os.environ.get('FUZZ_KEYS', 'keys'))
PREVIOUS_SEQUENCE, PREVIOUS_ISSUED = 2, '2026-11-01T00:00:00Z'
SIGNED = set()


def key(name):
    path, public = KEYS / (name + '.pem'), KEYS / (name + '.pub.pem')
    if public.exists() and not path.exists():
        # A replay has only the public keys.
        return types.SimpleNamespace(public=ar.public_key(public))
    if not path.exists():
        KEYS.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-out', path], check=True, capture_output=True)
    if not public.exists():
        subprocess.run(['/usr/bin/openssl', 'pkey', '-in', path, '-pubout', '-out', public], check=True,
                       capture_output=True)
    return ar.TestSigner(path)


def setup():
    global TRUSTED, OTHER
    TRUSTED, OTHER = key('throwaway-trusted'), key('throwaway-other')
    signed = KEYS / 'signed.json'
    if signed.exists():
        SIGNED.update(json.loads(signed.read_text()))


def payload(sequence, issued, build='FP6.TEST.1'):
    release = {'build': build, 'verifiedBootHash': 'A' * 64, 'osVersion': 170000, 'osPatchLevel': 202610,
               'vendorPatchLevel': 20261001, 'bootPatchLevel': 20261001, 'attestationVersion': 400,
               'keyMintVersion': 400}
    value = {'format': ar.PAYLOAD_FORMAT, 'version': 1, 'sequence': sequence, 'issued': issued,
             'devices': [{'id': 'FP6', 'verifiedBootKey': 'B' * 64, 'releases': [release]}]}
    ar.validate_payload(value)
    return ar.encode_payload(value)


def sign(data, signer):
    if signer is TRUSTED:
        SIGNED.add(hashlib.sha256(data).hexdigest())
        (KEYS / 'signed.json').write_text(json.dumps(sorted(SIGNED)))
    return signer.sign(data)


def TestOneInput(data):
    try:
        value = ar.verified_payload(data, TRUSTED.public,
                                    previous={'sequence': PREVIOUS_SEQUENCE, 'issued': PREVIOUS_ISSUED})
    except (SigningError, JsonError):
        return
    except Exception as error:
        fuzzlib.record('verified_payload raised ' + fuzzlib.where(error), data, repr(error))
        return
    envelope = json.loads(data)
    raw = base64.b64decode(envelope['payload'])
    if hashlib.sha256(raw).hexdigest() not in SIGNED:
        fuzzlib.record('accepted a payload the trusted key never signed', data)
    if value['sequence'] <= PREVIOUS_SEQUENCE:
        fuzzlib.record('accepted a list that does not move forward', data)
    if value['issued'] < PREVIOUS_ISSUED:
        fuzzlib.record('accepted a later list with an earlier issue time', data, value['issued'])


def encode(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    seeds = {}
    for sequence, issued in ((3, '2026-11-02T00:00:00Z'), (2, '2026-11-01T00:00:00Z'), (3, '2026-10-01T00:00:00Z')):
        data = payload(sequence, issued)
        seeds[f'trusted-{sequence}-{issued[:10]}'] = ar.envelope_bytes(data, TRUSTED.public, sign(data, TRUSTED))
    data = payload(4, '2026-11-03T00:00:00Z')
    seeds['other-key'] = ar.envelope_bytes(data, OTHER.public, sign(data, OTHER))
    seeds['trusted-id-other-signature'] = ar.envelope_bytes(data, TRUSTED.public, sign(data, OTHER))
    both = json.loads(ar.envelope_bytes(data, TRUSTED.public, sign(data, TRUSTED)))
    both['signatures'].append(json.loads(ar.envelope_bytes(data, OTHER.public, sign(data, OTHER)))['signatures'][0])
    seeds['two-signatures'] = encode(both)
    other_payload = payload(5, '2026-11-04T00:00:00Z', build='FP6.TEST.2')
    seeds['signature-of-another-payload'] = ar.envelope_bytes(other_payload, TRUSTED.public, sign(data, TRUSTED))
    for name, value in seeds.items():
        (Path(directory) / ('seed-' + name + '.json')).write_bytes(value)


if __name__ == '__main__':
    setup()
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput,
                      custom_mutator=lambda d, m, s: fuzzlib.json_mutator(d, m, s, encode=encode))
        atheris.Fuzz()
