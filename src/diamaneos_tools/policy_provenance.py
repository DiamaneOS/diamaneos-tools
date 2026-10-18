"""The device policy's provenance record against the files it lists.

``sepolicy/provenance.json`` in the device tree records, for each imported
policy file, where it came from and the SHA-256 of the file as the device tree
holds it. A file that differs from its row changed without its record.
"""
import hashlib
from pathlib import Path, PurePosixPath
import re

from . import safe_json

POLICY_DIR = 'device/fairphone/FP6/sepolicy'
LEDGER = POLICY_DIR + '/provenance.json'


def mismatches(src: Path) -> list[str]:
    """The listed files, relative to the policy directory, that are missing or
    differ from their recorded SHA-256. ValueError if the record is unusable."""
    policy = Path(src) / POLICY_DIR
    ledger = safe_json.load_json(policy / 'provenance.json')
    rows = ledger.get('files') if isinstance(ledger, dict) and ledger.get('schema_version') == 1 else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('the record lists no files')
    root, seen, stale = policy.resolve(), set(), []
    for row in rows:
        path, digest = (row.get('path'), row.get('sha256')) if isinstance(row, dict) else (None, None)
        if (not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or str(PurePosixPath(path)) != path or PurePosixPath(path).is_absolute()
                or '..' in PurePosixPath(path).parts or path in seen):
            raise ValueError('the record has an invalid or repeated row')
        seen.add(path)
        file = policy / path
        if (file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root)
                or hashlib.sha256(file.read_bytes()).hexdigest() != digest):
            stale.append(path)
    return stale
