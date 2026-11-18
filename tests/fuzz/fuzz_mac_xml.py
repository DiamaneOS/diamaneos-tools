#!/usr/bin/env python3
"""signing_mac._policies(): SELinux mac_permissions.xml from target-files.

Property checks: DTDs, entities and processing instructions are refused. An accepted policy has
only literal hex certificates, safe selectors and each signer either a global seinfo or package
mappings. Nesting and size stay within the bounds. Only SigningError, ParseError and UnicodeError stop it.
"""
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import signing_mac
    from diamaneos_tools.signing_inputs import SigningError

CERT = '3082' + 'ab' * 40
SEEDS = [
    f'<?xml version="1.0" encoding="utf-8"?>\n<policy>\n  <signer signature="{CERT}">\n    <seinfo value="platform"/>\n  </signer>\n</policy>\n',
    f'<policy><signer><cert signature="{CERT}"/><cert signature="{CERT[:-2]}cd"/><package name="com.example.app"><seinfo value="media"/></package></signer></policy>',
    f'<!-- a comment --><policy><signer signature="{CERT}"><seinfo value="x"/></signer><signer signature="{CERT[:-2]}00"><seinfo value="y"/></signer></policy>',
    '<!DOCTYPE policy [<!ENTITY e "x">]><policy>&e;</policy>',
]


def TestOneInput(data):
    try:
        result = signing_mac._policies(data, {})
    except (SigningError, ET.ParseError, UnicodeError):
        return
    except Exception as error:
        fuzzlib.record('MAC parser raised ' + fuzzlib.where(error), data, repr(error))
        return
    text = data.decode('utf-8')
    stripped = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    if '<!DOCTYPE' in stripped or '<!ENTITY' in stripped or '&' in stripped or re.search(r'<\?(?!xml\s)', stripped):
        fuzzlib.record('MAC parser accepted a DTD, entity or processing instruction', data)
    for certs, label, packages in result:
        if not certs or not all(re.fullmatch('[0-9a-f]{64}', c) for c in certs) or bool(label) == bool(packages):
            fuzzlib.record('MAC parser accepted an odd signer', data, repr((certs, label, packages)))
        for name, value in packages:
            if not re.fullmatch(r'[A-Za-z0-9_.]{1,255}', name) or not re.fullmatch(r'[A-Za-z0-9_.]{1,255}', value):
                fuzzlib.record('MAC parser accepted an unsafe selector', data, repr((name, value)))


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    for i, seed in enumerate(SEEDS):
        (Path(directory) / f'seed-{i}.xml').write_bytes(seed.encode())


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
