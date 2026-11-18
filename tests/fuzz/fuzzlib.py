"""Shared helpers for the fuzz targets: findings, a time guard and a JSON mutator.

Findings are written to $FUZZ_FINDINGS (one file per kind) and the run goes on.
"""
import copy
import hashlib
import os
from pathlib import Path
import signal
import sys
import tempfile
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
FINDINGS = Path(os.environ.get('FUZZ_FINDINGS', tempfile.gettempdir() + '/fuzz-findings'))
_seen = set()


def record(kind, data, detail=''):
    """Keep the first input of each kind of finding and go on."""
    if kind in _seen:
        return
    _seen.add(kind)
    FINDINGS.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(kind.encode()).hexdigest()[:16]
    (FINDINGS / (name + '.input')).write_bytes(data)
    (FINDINGS / (name + '.txt')).write_text(kind + '\n' + detail + '\n')
    print('FINDING ' + kind, file=sys.stderr, flush=True)


def where(error):
    frames = traceback.extract_tb(error.__traceback__)
    last = frames[-1] if frames else None
    return type(error).__name__ + (' at ' + Path(last.filename).name + ':' + str(last.lineno) if last else '')


class Slow(Exception):
    pass


def _alarm(*_):
    raise Slow()


def guarded(seconds, fn, *args):
    """Run fn, raising Slow after seconds of work."""
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


INTERESTING = [None, True, False, 0, -1, 1, 2 ** 31, 2 ** 63, 2 ** 64, 10 ** 30, 1.5, -0.0, '', '.', '..', '../x',
               '/abs', 'a/b', 'a\\b', 'x\x00y', 'A' * 300, 'COM.EXAMPLE.APP', 'com.exаmple.app',
               'Com.Example.App', 'de.diamaneos', 'DE.DIAMANEOS.X', 'base.apk', '..apk', 'a/../b.apk',
               '0' * 64, 'f' * 64, 'F' * 64, '1', '01', '-1', '1e3', '١', 'arm64-v8a', 'webp', 'png', 'svg',
               [], {}, [1, 2], {'a': 1}]


EXTRA_KEYS = ['schema_version', 'sequence', 'issued', 'releases', 'build', 'id', 'keyId', 'signature',
              'payload', 'format', 'version', 'password', 'release_prefix', 'not_checked', 'sparse', 'bytes']


def _paths(value, prefix=()):
    yield prefix
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _paths(v, prefix + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _paths(v, prefix + (i,))


def _get(value, path):
    for p in path:
        value = value[p]
    return value


def mutate_json(value, rng):
    """One structural change of a JSON value, in place where possible."""
    paths = list(_paths(value))
    path = rng.choice(paths)
    if not path:
        return value
    parent, key = _get(value, path[:-1]), path[-1]
    op = rng.randrange(8)
    if op == 0:
        parent[key] = copy.deepcopy(rng.choice(INTERESTING))
    elif op == 1 and isinstance(parent, dict):
        del parent[key]
    elif op == 1 and isinstance(parent, list):
        parent.pop(key)
    elif op == 2 and isinstance(parent, dict):
        parent[rng.choice([k for k in INTERESTING if isinstance(k, str)])] = parent.pop(key)
    elif op == 3 and isinstance(parent, list):
        parent.insert(key, parent[key])
    elif op == 4:
        other = _get(value, rng.choice(paths))
        parent[key] = copy.deepcopy(other) if other is not value else 1
    elif op == 5 and isinstance(parent[key], int) and not isinstance(parent[key], bool):
        parent[key] = parent[key] + rng.choice([-1, 1, -parent[key], parent[key], 2 ** 32])
    elif op == 6 and isinstance(parent[key], str):
        s = parent[key]
        i = rng.randrange(len(s) + 1)
        parent[key] = s[:i] + rng.choice(['/', '..', '.', 'A', 'і', '\x00', '%2e', ' ', '\n']) + s[i:]
    elif op == 7 and isinstance(parent, dict):
        parent[rng.choice(EXTRA_KEYS)] = copy.deepcopy(rng.choice(INTERESTING))
    return value


def json_mutator(data, max_size, seed, *, encode=None):
    import json
    import random
    import atheris
    rng = random.Random(seed)
    if rng.random() < 0.1:
        return atheris.Mutate(data, max_size)
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return atheris.Mutate(data, max_size)
    for _ in range(rng.randrange(1, 4)):
        value = mutate_json(value, rng)
    try:
        out = (encode or (lambda v: json.dumps(v, separators=(',', ':')).encode()))(value)
    except (ValueError, TypeError, RecursionError):
        return atheris.Mutate(data, max_size)
    return out[:max_size]
