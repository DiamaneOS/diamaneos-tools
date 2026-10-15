"""Compare two kernel builds of the same pin byte for byte.

diamaneos kernel compare --run RUN --against RUN checks that two passed kernel
builds (KERNEL_WORKSPACE/runs/...) packaged the same files with the same bytes.
Each build signs with a key of its own, so a module may differ in its appended
signature only, and the Image in its built-in certificate and the build ID that
hashes it. Everything else must match.
"""
import argparse
import json
from pathlib import Path
import sys

MARKER = b'~Module signature appended~\n'
# The GNU build ID note header: name size 4, descriptor size 20, type 3, "GNU".
BUILD_ID = (4).to_bytes(4, 'little') + (20).to_bytes(4, 'little') + (3).to_bytes(4, 'little') + b'GNU\0'


def unsigned(data):
    """A signed module without its signature, or None when it carries none."""
    if not data.endswith(MARKER) or len(data) < len(MARKER) + 12:
        return None
    size = int.from_bytes(data[-len(MARKER) - 4:-len(MARKER)], 'big')
    end = len(data) - len(MARKER) - 12 - size
    return data[:end] if end >= 0 else None


def without(data, certificate):
    """The Image with its built-in certificate and every build ID zeroed (the
    kernel's hashes the certificate, the vDSOs' their own code), and where they
    were. None unless the certificate is there exactly once."""
    at = data.find(certificate)
    if not certificate or at < 0 or data.find(certificate, at + 1) >= 0:
        return None
    spans = [(at, len(certificate))]
    at = data.find(BUILD_ID)
    while at >= 0:
        spans.append((at + len(BUILD_ID), 20))
        at = data.find(BUILD_ID, at + 1)
    masked = bytearray(data)
    for start, size in spans:
        masked[start:start + size] = bytes(size)
    return bytes(masked), spans


def files(candidate):
    return {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()}


def compare(run, against):
    a, b = run / 'candidate', against / 'candidate'
    records = [json.loads((r / 'result.json').read_text()) for r in (run, against)]
    for path, record in zip((run, against), records):
        if record.get('status') != 'PASS' or not (path / 'candidate').is_dir():
            raise ValueError('not a passed kernel build run: ' + str(path))
    for key in ('source_commit', 'packaging_recipe_sha256'):
        if records[0].get(key) != records[1].get(key):
            raise ValueError('the runs differ in ' + key)
    certificates = [(r / 'builtin-cert.der').read_bytes() for r in (run, against)]
    names_a, names_b = files(a), files(b)
    result = {'only_in_run': sorted(names_a - names_b), 'only_in_against': sorted(names_b - names_a),
              'identical': 0, 'signature_only': 0, 'key_only': [], 'different': []}
    for name in sorted(names_a & names_b):
        data = [(a / name).read_bytes(), (b / name).read_bytes()]
        if data[0] == data[1]:
            result['identical'] += 1
            continue
        payloads = [unsigned(d) for d in data]
        if None not in payloads and payloads[0] == payloads[1]:
            result['signature_only'] += 1
            continue
        if name == 'Image':
            masked = [without(d, c) for d, c in zip(data, certificates)]
            if None not in masked and masked[0] == masked[1]:
                result['key_only'].append(name)
                continue
        result['different'].append(name)
    same = not (result['only_in_run'] or result['only_in_against'] or result['different'])
    result['status'] = 'PASS' if same else 'FAIL'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='diamaneos kernel compare', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, required=True, help='kernel build run directory (KERNEL_WORKSPACE/runs/...)')
    parser.add_argument('--against', type=Path, required=True, help='another build run of the same pin')
    args = parser.parse_args(argv)
    try:
        result = compare(args.run, args.against)
    except (OSError, ValueError, KeyError) as error:
        print('ERROR: kernel compare failed: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1
