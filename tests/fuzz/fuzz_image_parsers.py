#!/usr/bin/env python3
"""Binary parsers of image_verify on images a build writes: boot and vendor_boot headers, AVB footer,
device tree blobs and dtbo tables, Android sparse images, dex string pools and super metadata.

Property checks: each parser ends within 2 seconds and never makes more bytes than a declared limit
(sparse_to_raw with a limit), and raises only what Verifier.add() turns into a FAIL (OSError,
ValueError, KeyError, struct.error). Anything else would stop build verify with a traceback.
"""
import os
from pathlib import Path
import struct
import sys

import atheris

with atheris.instrument_imports():
    import fuzzlib
    from diamaneos_tools import image_verify as iv

EXPECTED = (OSError, ValueError, KeyError, struct.error)


def fdt():
    strings = b'compatible\0model\0'
    body = struct.pack('>I', 1) + b'\0\0\0\0'
    body += struct.pack('>III', 3, 4, 0) + b'abc\0'
    body += struct.pack('>I', 1) + b'soc@0\0\0\0'
    body += struct.pack('>III', 3, 4, 11) + b'xyz\0'
    body += struct.pack('>II', 2, 2) + struct.pack('>I', 9)
    off_struct = 40
    off_strings = off_struct + len(body)
    total = off_strings + len(strings)
    header = struct.pack('>IIIIIIIIII', 0xD00DFEED, total, off_struct, off_strings, 40, 17, 16, 0, len(strings), len(body))
    return header + body + strings


def sparse():
    header = struct.pack('<IHHHHIIII', iv.SPARSE_MAGIC, 1, 0, 28, 12, 4096, 3, 3, 0)
    raw = struct.pack('<HHII', 0xCAC1, 0, 1, 12 + 4096) + b'R' * 4096
    fill = struct.pack('<HHII', 0xCAC2, 0, 1, 16) + b'\xab\xcd\xef\x01'
    skip = struct.pack('<HHII', 0xCAC3, 0, 1, 12)
    return header + raw + fill + skip


def dex():
    data = bytearray(b'dex\n035\0' + b'\0' * 0x68)
    data[0x38:0x40] = struct.pack('<II', 2, 0x70)
    data += struct.pack('<II', 0x78, 0x7e)
    data += b'\x04abcd\0' + b'\x03xyz\0'
    return bytes(data)


def boot(version):
    data = bytearray(4096 * 2)
    data[:8] = b'ANDROID!'
    struct.pack_into('<I', data, 8, 100)
    struct.pack_into('<I', data, 40, version)
    if version < 3:
        struct.pack_into('<I', data, 36, 4096)
    data[64:80] = b'console=ttyMSM0 '
    return bytes(data)


def vendor_boot():
    data = bytearray(4096 * 3)
    data[:8] = b'VNDRBOOT'
    struct.pack_into('<I', data, 12, 4096)
    struct.pack_into('<I', data, 24, 100)
    struct.pack_into('<II', data, 2096, 2112, 64)
    data[28:40] = b'androidboot.'
    return bytes(data)


def super_empty():
    data = bytearray(iv.LP_GEOMETRY_SIZE + 4096)
    struct.pack_into('<I', data, 0, iv.LP_GEOMETRY_MAGIC)
    struct.pack_into('<I', data, iv.LP_GEOMETRY_SIZE, iv.LP_HEADER_MAGIC)
    struct.pack_into('<I', data, iv.LP_GEOMETRY_SIZE + 8, 256)
    struct.pack_into('<12I', data, iv.LP_GEOMETRY_SIZE + 80, 0, 2, 52, 0, 0, 24, 0, 0, 48, 104, 1, 64)
    tables = iv.LP_GEOMETRY_SIZE + 256
    data[tables:tables + 8] = b'system_a'
    data[tables + 52:tables + 60] = b'system_b'
    entry = tables + 104
    struct.pack_into('<Q', data, entry + 16, 1 << 30)
    data[entry + 24:entry + 29] = b'super'
    return bytes(data)


def avb(data):
    footer = bytearray(64)
    footer[:4] = b'AVBf'
    struct.pack_into('>Q', footer, 12, len(data))
    return data + b'\0' * 64 + bytes(footer)


PARSERS = [
    ('boot_header', iv.boot_header),
    ('vendor_boot_parts', iv.vendor_boot_parts),
    ('avb_payload', iv.avb_payload),
    ('parse_dtb', iv.parse_dtb),
    ('device_trees', iv.device_trees),
    ('sparse_to_raw', lambda d: iv.sparse_to_raw(d, limit=4096)),
    ('dex_strings', iv.dex_strings),
    ('super_metadata', iv.super_metadata),
]


def TestOneInput(data):
    if not data:
        return
    name, parser = PARSERS[data[0] % len(PARSERS)]
    body = data[1:]
    try:
        result = fuzzlib.guarded(2, parser, body)
    except EXPECTED:
        return
    except fuzzlib.Slow:
        fuzzlib.record(name + ' does unbounded work', data)
        return
    except MemoryError:
        fuzzlib.record(name + ' exhausts memory', data)
        return
    except Exception as error:
        fuzzlib.record(name + ' raised ' + fuzzlib.where(error), data, repr(error))
        return
    if name == 'sparse_to_raw' and len(result) > 4096:
        fuzzlib.record('sparse_to_raw made more than its limit', data)


def write_seeds(directory):
    os.makedirs(directory, exist_ok=True)
    blob = fdt()
    table = b'\xd7\xb7\xab\x1e' + b'\0' * 8 + struct.pack('>III', 32, 1, 32) + b'\0' * 12
    table += struct.pack('>II', len(blob), len(table) + 32) + b'\0' * 24 + blob
    seeds = {0: [boot(2), boot(4)], 1: [vendor_boot()], 2: [avb(b'payload')], 3: [blob], 4: [blob + blob, table],
             5: [sparse()], 6: [dex()], 7: [super_empty()]}
    for index, items in seeds.items():
        for j, item in enumerate(items):
            (Path(directory) / f'seed-{index}-{j}').write_bytes(bytes([index]) + item)


if __name__ == '__main__':
    if sys.argv[1:2] == ['seeds']:
        write_seeds(sys.argv[2])
    else:
        atheris.instrument_all()
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
