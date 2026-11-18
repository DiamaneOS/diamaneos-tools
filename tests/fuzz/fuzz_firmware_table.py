#!/usr/bin/env python3
"""firmware_release.table(): the firmware release table the vendor image carries, from the firmware
inventory and the firmware_release block of the build config.

Input: {"inventory": ..., "config": ...} JSON, seeded with a reduced copy of the FP6 files. Property
checks: the same input gives the same bytes. Each line is `image <partition> <N.N.N> <bytes> <sha256>`
within fwrelease's limits. Releases are newest first. No partition is listed twice for one release.
Only VendorError stops it.
"""
import copy
import json
import os
from pathlib import Path
import re
import sys

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import firmware_release
    from diamaneos_tools.vendor import VendorError

LINE = re.compile(r'image ([a-z0-9_-]{1,32}) ([0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}) ([0-9]+) ([0-9a-f]{64})')


def TestOneInput(data):
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return
    if not isinstance(value, dict) or not isinstance(value.get('inventory'), dict) or not isinstance(value.get('config'), dict):
        return
    try:
        first = firmware_release.table(copy.deepcopy(value['inventory']), copy.deepcopy(value['config']))
    except VendorError:
        return
    except Exception as error:
        fuzzlib.record('table raised ' + fuzzlib.where(error), data, repr(error))
        return
    if firmware_release.table(copy.deepcopy(value['inventory']), copy.deepcopy(value['config'])) != first:
        fuzzlib.record('table is not deterministic', data)
    rows, seen = [], set()
    for line in first.decode().splitlines():
        match = LINE.fullmatch(line)
        if not match or not 0 < int(match[3]) <= firmware_release.MAX_IMAGE_BYTES:
            fuzzlib.record('table line outside the format', data, line)
            return
        key = (match[1], match[2])
        if key in seen:
            fuzzlib.record('table lists a partition twice for one release', data, line)
        seen.add(key)
        rows.append(tuple(int(n) for n in match[2].split('.')))
    if rows != sorted(rows, reverse=True):
        fuzzlib.record('table releases are not newest first', data)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    inventory = json.loads((fuzzlib.REPO / 'config/fp6-firmware-inventory.json').read_bytes())
    config = json.loads((fuzzlib.REPO / 'config/fp6-build.json').read_bytes())['firmware_release']
    config.pop('scope', None)
    releases = {}
    for build, release in inventory['releases'].items():
        releases[build] = {'images': {'firmware': release['images']['firmware']}}
    seed = {'inventory': {'releases': releases}, 'config': config}
    (Path(directory) / 'seed-fp6.json').write_bytes(json.dumps(seed, separators=(',', ':')).encode())


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput, custom_mutator=fuzzlib.json_mutator)
        atheris.Fuzz()
