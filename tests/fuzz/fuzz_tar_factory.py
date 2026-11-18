#!/usr/bin/env python3
"""signing_verify.verify_factory_contents(): the version 1 factory tar holds exactly the image archive.

Input builds a tar (plain, gzip, bzip2 or xz) with fuzzed member names and types (file, directory,
symlink, hard link, device, FIFO, long names) and contents, then optionally damages it. Property
checks: an accepted tar has exactly one regular member, named as the image archive, with its bytes.
reading is bounded in time. Only SigningError and tarfile errors stop it.
"""
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import signing_verify
    from diamaneos_tools.signing_inputs import SigningError

TMP = Path(tempfile.mkdtemp(prefix='fuzz-tar-'))
IMAGES = TMP / 'images.zip'
IMAGES.write_bytes(b'PK\x05\x06' + b'\0' * 18)
NAMES = ['images.zip', './images.zip', '/images.zip', '../images.zip', 'images.zip/', 'IMAGES.zip', 'other',
         'images.zip\x00x', 'a/images.zip', 'images.zip' + 'x' * 120]
TYPES = [tarfile.REGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE,
         tarfile.CONTTYPE, tarfile.AREGTYPE]


def build(fdp):
    out = io.BytesIO()
    mode = ['w', 'w:gz', 'w:bz2', 'w:xz'][fdp.ConsumeIntInRange(0, 3)]
    fmt = [tarfile.GNU_FORMAT, tarfile.PAX_FORMAT, tarfile.USTAR_FORMAT][fdp.ConsumeIntInRange(0, 2)]
    try:
        with tarfile.open(fileobj=out, mode=mode, format=fmt) as tar:
            for _ in range(fdp.ConsumeIntInRange(0, 4)):
                name = NAMES[fdp.ConsumeIntInRange(0, len(NAMES) - 1)] if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(16)
                info = tarfile.TarInfo(name)
                info.type = TYPES[fdp.ConsumeIntInRange(0, len(TYPES) - 1)]
                content = IMAGES.read_bytes() if fdp.ConsumeBool() else fdp.ConsumeBytes(32)
                if info.type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = ['images.zip', '/etc/passwd', '../x'][fdp.ConsumeIntInRange(0, 2)]
                    tar.addfile(info)
                elif info.isreg():
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
                else:
                    tar.addfile(info)
    except (ValueError, UnicodeError, tarfile.TarError):
        pass
    data = bytearray(out.getvalue())
    for _ in range(fdp.ConsumeIntInRange(0, 3)):
        if data:
            data[fdp.ConsumeIntInRange(0, len(data) - 1)] = fdp.ConsumeIntInRange(0, 255)
    return bytes(data)


def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)
    raw = build(fdp) if fdp.ConsumeBool() else fdp.ConsumeBytes(8192)
    factory = TMP / 'factory.tar'
    factory.write_bytes(raw)
    try:
        fuzzlib.guarded(5, signing_verify.verify_factory_contents, factory, IMAGES)
    except (SigningError, tarfile.TarError):
        return
    except fuzzlib.Slow:
        fuzzlib.record('factory tar check slow', data)
        return
    except (OSError, EOFError, ValueError, KeyError) as error:
        # signing verify reports these as invalid input, or as a traceback for EOFError.
        fuzzlib.record('factory tar check raised ' + fuzzlib.where(error), data, repr(error))
        return
    except Exception as error:
        fuzzlib.record('factory tar check raised unexpected ' + fuzzlib.where(error), data, repr(error))
        return
    with tarfile.open(factory, 'r:*') as tar:
        members = tar.getmembers()
        if len(members) != 1 or not members[0].isreg() or members[0].name != 'images.zip':
            fuzzlib.record('accepted a factory tar with other members', data, repr([m.name for m in members]))
            return
        with tar.extractfile(members[0]) as stream:
            if hashlib.sha256(stream.read()).digest() != hashlib.sha256(IMAGES.read_bytes()).digest():
                fuzzlib.record('accepted a factory tar with other bytes', data)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    for i, mode in enumerate(['w', 'w:gz', 'w:bz2', 'w:xz']):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode=mode) as tar:
            tar.add(IMAGES, 'images.zip')
        (Path(directory) / f'seed-raw-{i}').write_bytes(b'\x00' + out.getvalue())
    (Path(directory) / 'seed-built').write_bytes(bytes([1, 1, 0, 1, 1, 0, 0, 1]))


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
