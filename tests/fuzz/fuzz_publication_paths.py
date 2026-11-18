#!/usr/bin/env python3
"""signing_inputs.safe_file() and signing_layout.check_publication_files() on made directory trees.

Input builds a publication directory: regular files, directories, symlinks (inside and to outside),
hard links to a file outside, case-only and Unicode-form variants, then a publication name list.
Property checks: safe_file() returns only a regular file inside the root reached without a symlink.
check_publication_files() passes only when the tree holds exactly the listed regular files and their
directories. A hard link is a regular file and passes, since large inputs are linked on purpose to
save space.
"""
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import signing_layout as layout
    from diamaneos_tools.signing_inputs import SigningError, safe_file

TMP = Path(tempfile.mkdtemp(prefix='fuzz-paths-'))
NAMES = ['ota.zip', 'images.zip', 'release.json', 'sub/ota.zip', 'OTA.zip', 'ota.zip ', 'sub', 'sub/x',
         'café.zip', 'café.zip', '../outside.zip', '/abs.zip', 'a//b', './ota.zip', 'sub/../ota.zip',
         '', '.', 'x\\y', 'deep/er/file']


def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    base = Path(tempfile.mkdtemp(dir=TMP))
    root, outside = base / 'publication', base / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'secret').write_bytes(b'outside')
    try:
        for _ in range(fdp.ConsumeIntInRange(0, 6)):
            name = NAMES[fdp.ConsumeIntInRange(0, len(NAMES) - 1)]
            if not name or name.startswith('/') or '..' in name.split('/'):
                continue
            path = root / name
            kind = fdp.ConsumeIntInRange(0, 4)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == 0:
                    path.write_bytes(b'artifact')
                elif kind == 1:
                    path.mkdir(exist_ok=True)
                elif kind == 2:
                    path.symlink_to(outside / 'secret')
                elif kind == 3:
                    path.symlink_to(root / 'ota.zip')
                else:
                    os.link(outside / 'secret', path)
            except OSError:
                pass
        names = {NAMES[fdp.ConsumeIntInRange(0, len(NAMES) - 1)] for _ in range(fdp.ConsumeIntInRange(1, 4))}
        for name in names:
            try:
                found = safe_file(root, name)
            except SigningError:
                continue
            except Exception as error:
                fuzzlib.record('safe_file raised ' + fuzzlib.where(error), data, repr(error))
                continue
            real = Path(os.path.realpath(found))
            if not real.is_relative_to(root.resolve()) or not stat.S_ISREG(found.lstat().st_mode):
                fuzzlib.record('safe_file returned a path outside or not regular', data, name)
            if any(p.is_symlink() for p in [found, *found.parents] if p.is_relative_to(root)):
                fuzzlib.record('safe_file returned a path through a symlink', data, name)
        try:
            layout.check_publication_files(root.resolve(), names)
        except SigningError:
            return
        except Exception as error:
            fuzzlib.record('check_publication_files raised ' + fuzzlib.where(error), data, repr(error))
            return
        present = {str(p.relative_to(root)) for p in root.rglob('*') if not p.is_dir() or p.is_symlink()}
        if present != names:
            fuzzlib.record('publication check passed with other files', data, repr((present, names)))
    finally:
        shutil.rmtree(base)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    (Path(directory) / 'seed-0').write_bytes(bytes([2, 0, 0, 1, 0, 1, 0, 0]))
    (Path(directory) / 'seed-1').write_bytes(bytes([1, 0, 4, 1, 0]))


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
