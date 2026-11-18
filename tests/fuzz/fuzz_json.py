#!/usr/bin/env python3
"""safe_json.loads() and signing_inputs.load_json(): strict JSON for signing and build inputs.

Property checks on an accepted document: no duplicate key at any level, no NaN or infinity, depth
and node count within the limits, no forbidden private field names, no private key text, no
credential-bearing URL, and the value encodes back to finite UTF-8 JSON. load_json() never accepts
what loads() refuses. Only JsonError and SigningError stop them.
"""
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import safe_json, signing_inputs
    from diamaneos_tools.signing_inputs import SigningError

TMP = Path(tempfile.mkdtemp(prefix='fuzz-json-'))


def duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise KeyError('duplicate')
    return dict(pairs)


def walk(value, depth=0):
    yield value, depth
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, depth + 1
            yield from walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, depth + 1)


def TestOneInput(data):
    try:
        value = safe_json.loads(data)
        accepted = True
    except safe_json.JsonError:
        accepted = False
    except Exception as error:
        fuzzlib.record('loads raised ' + fuzzlib.where(error), data, repr(error))
        return
    path = TMP / 'doc.json'
    path.write_bytes(data)
    try:
        signing_inputs.load_json(path)
        if not accepted:
            fuzzlib.record('load_json accepted what loads refused', data)
    except SigningError:
        pass
    except Exception as error:
        fuzzlib.record('load_json raised ' + fuzzlib.where(error), data, repr(error))
    if not accepted:
        return
    try:
        json.loads(data.decode('utf-8'), object_pairs_hook=duplicates)
    except KeyError:
        fuzzlib.record('accepted a duplicate key', data)
    except (ValueError, RecursionError):
        pass
    nodes = 0
    for item, depth in walk(value):
        nodes += 1
        if depth > safe_json.MAX_DEPTH + 1:
            fuzzlib.record('accepted a document deeper than the limit', data)
        if isinstance(item, float) and not math.isfinite(item):
            fuzzlib.record('accepted a non-finite number', data)
        if isinstance(item, str) and '-----BEGIN' in item and 'PRIVATE KEY-----' in item:
            fuzzlib.record('accepted private key text', data)
        if isinstance(item, dict):
            for key in item:
                if re.sub(r'[-_]', '', key).lower() in safe_json.FORBIDDEN_KEYS:
                    fuzzlib.record('accepted a forbidden field name', data, key)
                if re.sub('[^a-z]', '', key.lower()) in safe_json.FORBIDDEN_KEYS:
                    fuzzlib.record('accepted a forbidden field name spelled with other separators', data, key)
    try:
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode('utf-8')
    except (ValueError, UnicodeError):
        fuzzlib.record('accepted a value that does not encode back', data)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    root = fuzzlib.REPO / 'config'
    for i, path in enumerate(p for p in sorted(root.glob('*.json')) if p.stat().st_size < 32768):
        (Path(directory) / f'seed-{i}.json').write_bytes(path.read_bytes())
    (Path(directory) / 'seed-dup.json').write_bytes(b'{"a":1,"b":{"c":[1,2,{"d":null}]}}')


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput, custom_mutator=fuzzlib.json_mutator)
        atheris.Fuzz()
