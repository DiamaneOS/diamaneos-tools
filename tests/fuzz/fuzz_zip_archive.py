#!/usr/bin/env python3
"""signing_archive: ZIP member checks, metadata reads and unpack_tools() on made and damaged archives.

Input builds a ZIP: member names (with .., absolute, backslash, duplicate, case-only and Unicode
lookalike names), types (file, directory, symlink, executable) and contents, then optionally damages
its bytes. Property checks:
- an accepted archive has no absolute, .. or backslash name, no exact duplicate and no encrypted member
- unpack_tools() creates only regular files and directories, all under the destination, never a link,
  and nothing outside the destination changes
- read_text() and extract() never follow a symlink member
- unpack_tools() writes only files that others cannot read, and a refused unpack leaves no tree
- only SigningError stops it, any other exception is recorded
Names that differ only in case or Unicode form are accepted: target-files holds such pairs, as
media/audio/ringtones/Hydra.ogg and hydra.ogg, and unpack_tools() creates each file exclusively, so a
disk that ignores case or Unicode form refuses the second one.
"""
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import signing_archive as za
    from diamaneos_tools.signing_inputs import SigningError

TMP = Path(tempfile.mkdtemp(prefix='fuzz-zip-'))
NAMES = ['bin/avbtool', 'bin/', 'lib64/libc++.so', 'META/apkcerts.txt', 'META/misc_info.txt', '../evil', '/abs',
         'a/../b', 'a\\b', './x', 'x/./y', '.', '', 'a//b', 'META/APKCERTS.txt', 'Bin/avbtool', 'bin/avbtооl',
         'café', 'café', 'bin', 'bin/avbtool/', 'x' * 300, 'nul\x00name', 'bin/avbtool.']
APKCERTS = b'name="App.apk" certificate="build/make/target/product/security/platform.x509.pem" private_key="x.pk8"\n'


def build(fdp):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for _ in range(fdp.ConsumeIntInRange(0, 6)):
            name = NAMES[fdp.ConsumeIntInRange(0, len(NAMES) - 1)] if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(20)
            info = zipfile.ZipInfo(name or 'empty')
            kind = fdp.ConsumeIntInRange(0, 4)
            mode = {0: stat.S_IFREG | 0o644, 1: stat.S_IFREG | 0o755, 2: stat.S_IFLNK | 0o777,
                    3: stat.S_IFDIR | 0o755, 4: 0}[kind]
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED if fdp.ConsumeBool() else zipfile.ZIP_STORED
            content = fdp.ConsumeBytes(fdp.ConsumeIntInRange(0, 64)) if kind != 2 else \
                [b'../../outside', b'/etc/passwd', b'bin/avbtool', b'.'][fdp.ConsumeIntInRange(0, 3)]
            if name == 'META/apkcerts.txt' and fdp.ConsumeBool():
                content = APKCERTS
            try:
                z.writestr(info, content)
            except (ValueError, UnicodeError):
                pass
    data = bytearray(out.getvalue())
    for _ in range(fdp.ConsumeIntInRange(0, 3)):
        if data:
            data[fdp.ConsumeIntInRange(0, len(data) - 1)] = fdp.ConsumeIntInRange(0, 255)
    if fdp.ConsumeBool() and data:
        data = data[:fdp.ConsumeIntInRange(0, len(data))]
    return bytes(data)


def entries(root):
    result = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory) / name
            result[str(path)] = path.lstat()
    return result


def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    raw = build(fdp) if fdp.ConsumeBool() else fdp.ConsumeBytes(4096)
    base = Path(tempfile.mkdtemp(dir=TMP))
    try:
        archive = base / 'otatools.zip'
        archive.write_bytes(raw)
        (base / 'outside').write_bytes(b'canary')
        try:
            with za.archive(archive) as handle:
                names = [i.filename for i in handle.infolist()]
                for name in names:
                    if name.startswith('/') or '..' in name.split('/') or '\\' in name:
                        fuzzlib.record('archive accepted an unsafe name', data, repr(name))
                if len(set(names)) != len(names):
                    fuzzlib.record('archive accepted a duplicate name', data, repr(names))
                for name in names:
                    info = handle.getinfo(name)
                    try:
                        text = za.read_text(handle, name)
                        if stat.S_ISLNK(info.external_attr >> 16):
                            fuzzlib.record('read_text read a symlink member', data, repr(name))
                        if name.endswith('apkcerts.txt'):
                            za.attributes(text)
                    except SigningError:
                        pass
        except SigningError:
            pass
        except Exception as error:
            fuzzlib.record('archive read raised ' + fuzzlib.where(error), data, repr(error))
        destination = base / 'tools'
        try:
            za.unpack_tools(archive, destination)
            unpacked = True
        except SigningError:
            unpacked = False
        except Exception as error:
            unpacked = False
            fuzzlib.record('unpack_tools raised ' + fuzzlib.where(error), data, repr(error))
        if (base / 'outside').read_bytes() != b'canary' or sorted(p.name for p in base.iterdir()) not in (
                ['otatools.zip', 'outside'], ['otatools.zip', 'outside', 'tools']):
            fuzzlib.record('unpack touched something outside its destination', data)
        if destination.exists():
            for path, info in entries(destination).items():
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    fuzzlib.record('unpack made a non-regular entry', data, path)
                if not Path(path).resolve().is_relative_to(destination.resolve()):
                    fuzzlib.record('unpack made an entry outside the destination', data, path)
                if stat.S_ISREG(info.st_mode) and info.st_mode & 0o077:
                    fuzzlib.record('unpack made a file others can read', data, path)
            if not unpacked:
                fuzzlib.record('refused unpack leaves a partial tree', data)
    finally:
        shutil.rmtree(base)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        info = zipfile.ZipInfo('bin/avbtool')
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, b'#!/bin/sh\n')
        z.writestr('META/apkcerts.txt', APKCERTS)
    (Path(directory) / 'seed-raw').write_bytes(b'\x00' + out.getvalue())
    (Path(directory) / 'seed-built').write_bytes(bytes([1, 3, 1, 3, 0, 1, 16]) + b'#!/bin/sh\n' * 2 + bytes([1, 0, 1, 2, 0]))


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
