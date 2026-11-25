import io
import lzma
from pathlib import Path
import re
import struct
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import code_calls as subject

TYPE = re.compile(r'\[*(?:L[^;]+;|[ZBSCIJFDV])')


def _uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _split(descriptor):
    owner, rest = descriptor.split('->')
    if '(' not in rest:
        return owner, rest, '()V'
    name, proto = rest.split('(', 1)
    return owner, name, '(' + proto


def make_dex(methods):
    """A dex file defining the methods {'Lclass;->name(parameters)return': code}. Code is a list of
    ('call', 'Lclass;->name'), ('call-range', ...), ('field', 'Lclass;->name') and ('units', [...])."""
    strings, types, protos, fields, refs = set(), set(), set(), set(), set()

    def proto_of(proto):
        params, returns = proto[1:].split(')')
        params = tuple(TYPE.findall(params))
        shorty = ''.join('L' if t[0] in 'L[' else t for t in (returns,) + params)
        protos.add((shorty, returns, params))
        strings.update((shorty, returns) + params)
        types.update((returns,) + params)

    def method_of(descriptor):
        owner, name, proto = _split(descriptor)
        proto_of(proto)
        refs.add((owner, name, proto))
        strings.update((owner, name))
        types.add(owner)

    for descriptor, code in methods.items():
        method_of(descriptor)
        for kind, value in code:
            if kind in ('call', 'call-range'):
                method_of(value)
            elif kind == 'field':
                owner, name = value.split('->')
                fields.add((owner, name))
                strings.update((owner, name, 'I'))
                types.update((owner, 'I'))
    strings, types = sorted(strings), sorted(types)
    protos, fields, refs = sorted(protos), sorted(fields), sorted(refs)
    s_index = {s: i for i, s in enumerate(strings)}
    t_index = {t: i for i, t in enumerate(types)}
    p_index = {(r, p): i for i, (_, r, p) in enumerate(protos)}
    f_index = {f: i for i, f in enumerate(fields)}

    def m_index(descriptor):
        return refs.index(_split(descriptor))

    classes = sorted({_split(d)[0] for d in methods})
    at = 0x70
    string_ids, at = at, at + 4 * len(strings)
    type_ids, at = at, at + 4 * len(types)
    proto_ids, at = at, at + 12 * len(protos)
    field_ids, at = at, at + 8 * len(fields)
    method_ids, at = at, at + 8 * len(refs)
    class_defs, at = at, at + 32 * len(classes)
    data = bytearray(at)
    data[:8] = b'dex\n035\0'
    struct.pack_into('<12I', data, 0x38, len(strings), string_ids, len(types), type_ids, len(protos), proto_ids,
                     len(fields), field_ids, len(refs), method_ids, len(classes), class_defs)
    for i, s in enumerate(strings):
        struct.pack_into('<I', data, string_ids + 4 * i, len(data))
        data += _uleb(len(s)) + s.encode() + b'\0'
    for i, t in enumerate(types):
        struct.pack_into('<I', data, type_ids + 4 * i, s_index[t])
    for i, (shorty, returns, params) in enumerate(protos):
        lists = 0
        if params:
            data += b'\0' * (-len(data) % 4)
            lists = len(data)
            data += struct.pack(f'<I{len(params)}H', len(params), *(t_index[t] for t in params))
        struct.pack_into('<III', data, proto_ids + 12 * i, s_index[shorty], t_index[returns], lists)
    for i, (owner, name) in enumerate(fields):
        struct.pack_into('<HHI', data, field_ids + 8 * i, t_index[owner], t_index['I'], s_index[name])
    for i, (owner, name, proto) in enumerate(refs):
        params, returns = proto[1:].split(')')
        struct.pack_into('<HHI', data, method_ids + 8 * i, t_index[owner],
                         p_index[(returns, tuple(TYPE.findall(params)))], s_index[name])
    codes = {}
    for descriptor, code in methods.items():
        units = []
        for kind, value in code:
            if kind == 'call':
                units += [0x0071, m_index(value), 0]
            elif kind == 'call-range':
                units += [0x0077, m_index(value), 0]
            elif kind == 'field':
                units += [0x0062, f_index[tuple(value.split('->'))]]
            else:
                units += value
        data += b'\0' * (-len(data) % 4)
        codes[descriptor] = len(data)
        data += struct.pack(f'<HHHHII{len(units)}H', 1, 0, 0, 0, 0, len(units), *units)
    for i, owner in enumerate(classes):
        own = sorted((m_index(d), codes[d]) for d in methods if _split(d)[0] == owner)
        blob, last = _uleb(0) + _uleb(0) + _uleb(len(own)) + _uleb(0), 0
        for index, code in own:
            blob += _uleb(index - last) + _uleb(9) + _uleb(code)
            last = index
        struct.pack_into('<8I', data, class_defs + 32 * i, t_index[owner], 1, 0xffffffff, 0, 0xffffffff, 0,
                         len(data), 0)
        data += blob
    return bytes(data)


def jar(*dex_files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
        for index, dex in enumerate(dex_files):
            archive.writestr('classes.dex' if index == 0 else f'classes{index + 1}.dex', dex)
    return out.getvalue()


def mangled(name):
    """'_ZN7android4vold12PublicVolume7doMountEv' for 'android::vold::PublicVolume::doMount'."""
    parts = name.split('::')
    tail = ''
    if len(parts) > 1 and parts[-1] == parts[-2]:
        parts, tail = parts[:-1], 'C2'
    elif parts[-1].startswith('~'):
        parts, tail = parts[:-1], 'D2'
    return '_ZN' + ''.join(f'{len(p)}{p}' for p in parts) + tail + 'Ev'


def _elf(sections, machine=183):
    """An ELF64 file with these sections [(name, type, flags, address, bytes, link)], section 0 empty."""
    names = b'\0' + b''.join(n.encode() + b'\0' for n, *_ in sections) + b'.shstrtab\0'
    body = bytearray(0x40)
    headers = [struct.pack('<IIQQQQIIQQ', *([0] * 10))]
    at = 1
    for name, kind, flags, address, content, link in sections + [('.shstrtab', 3, 0, 0, names, 0)]:
        offset = len(body)
        body += content
        headers.append(struct.pack('<IIQQQQIIQQ', at, kind, flags, address, offset, len(content), link, 0, 1,
                                   24 if kind == 2 else 0))
        at += len(name) + 1
    body += b'\0' * (-len(body) % 8)
    shoff = len(body)
    body += b''.join(headers)
    struct.pack_into('<16sHHIQQQIHHHHHH', body, 0, b'\x7fELF\x02\x01\x01' + b'\0' * 9, 3, machine, 1, 0, 0, shoff,
                     0, 0x40, 0, 0, 64, len(headers), len(headers) - 1)
    return bytes(body)


def make_elf(functions, machine=183, debugdata=True):
    """An AArch64 binary with the functions {qualified name: [('bl' | 'b', callee qualified name) or 'nop']} in
    .text, at 0x1000 on, their symbols in .gnu_debugdata."""
    text, symbols, at = bytearray(), [], 0x1000
    places = {}
    for name, code in functions.items():
        places[name] = at + len(text)
        text += b'\0' * 4 * len(code)
    for name, code in functions.items():
        for index, op in enumerate(code):
            pc = places[name] + 4 * index
            word = 0xd503201f
            if op != 'nop':
                kind, callee = op
                word = (0x94000000 if kind == 'bl' else 0x14000000) | ((places[callee] - pc) // 4 & 0x3ffffff)
            struct.pack_into('<I', text, pc - 0x1000, word)
        symbols.append((mangled(name), places[name], 4 * len(code)))
    names = b'\0'
    table = bytearray(24)
    for symbol, address, size in symbols:
        table += struct.pack('<IBBHQQ', len(names), 0x12, 0, 1, address, size)
        names += symbol.encode() + b'\0'
    inner = _elf([('.symtab', 2, 0, 0, bytes(table), 2), ('.strtab', 3, 0, 0, names, 0)])
    sections = [('.text', 1, 0x6, 0x1000, bytes(text), 0)]
    if debugdata:
        sections.append(('.gnu_debugdata', 1, 0, 0, lzma.compress(inner), 0))
    return _elf(sections, machine)


GATES = 'Landroid/graphics/DecodeGate;->gates'
DECODE = 'Landroid/graphics/BitmapFactory;->decodeByteArray([BII)Landroid/graphics/Bitmap;'


class DexTests(unittest.TestCase):
    def test_calls_and_fields_of_a_method(self):
        dex = make_dex({DECODE: [('call', GATES), ('call-range', 'Landroid/graphics/GatedDecodes;->bitmapFactory'),
                                 ('field', 'Landroid/graphics/Foo;->mBar'), ('units', [0x000e])],
                        'Landroid/graphics/Other;->run()V': [('units', [0x000e])]})
        found = subject.dex_references(jar(dex), [DECODE, 'Landroid/graphics/Other;->run()V', 'La;->missing()V'])
        self.assertEqual(({GATES, 'Landroid/graphics/GatedDecodes;->bitmapFactory'}, {'Landroid/graphics/Foo;->mBar'}),
                         found[DECODE])
        self.assertEqual((set(), set()), found['Landroid/graphics/Other;->run()V'])
        self.assertNotIn('La;->missing()V', found)

    def test_instruction_sizes_and_tables_are_skipped(self):
        # A wide constant and switch and array tables hold the invoke-static opcode in their data.
        # Read as instructions, they would add calls that are not there.
        wide = [0x0018, 0x0071, 0, 0, 0]
        packed = [0x0100, 1, 0x0071, 0, 0x0071, 0]
        sparse = [0x0200, 1, 0x0071, 0, 0x0071, 0]
        array = [0x0300, 2, 3, 0, 0x0071, 0x0071, 0x0071]
        dex = make_dex({DECODE: [('units', wide + packed), ('units', sparse + array), ('call', GATES)]})
        calls, _ = subject.dex_references(jar(dex), [DECODE])[DECODE]
        self.assertEqual({GATES}, calls)

    def test_a_nest_accessor_counts_as_its_field(self):
        dex = make_dex({DECODE: [('call', 'La/Stream;->-$$Nest$fputmSource')]})
        _, fields = subject.dex_references(jar(dex), [DECODE])[DECODE]
        self.assertEqual({'La/Stream;->mSource'}, fields)

    def test_the_method_is_found_in_any_dex_file(self):
        other = make_dex({'La;->b()V': [('units', [0x000e])]})
        dex = make_dex({DECODE: [('call', GATES)]})
        self.assertEqual({GATES}, subject.dex_references(jar(other, dex), [DECODE])[DECODE][0])

    def test_unreadable_code_fails(self):
        with self.assertRaisesRegex(ValueError, 'no dex files'):
            subject.dex_references(jar(), [DECODE])
        with self.assertRaisesRegex(ValueError, 'not a dex file'):
            subject.dex_references(jar(b'PK\3\4'), [DECODE])
        dex = make_dex({DECODE: [('call', GATES)]})
        for cut in (0x80, len(dex) - 3):
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                subject.dex_references(jar(dex[:cut]), [DECODE])
        with self.assertRaises(zipfile.BadZipFile):
            subject.dex_references(b'not a zip', [DECODE])

    def test_callers_of_a_method_in_every_class_and_dex_file(self):
        read = 'La/Clean;->read'
        own = 'Lb/Snapshots;->openOwn(Ljava/io/File;)La/Clean;'
        other = 'Lc/Other;->load()V'
        first = make_dex({own: [('call', read), ('units', [0x000e])], 'Lb/Snapshots;->write()V': [('call', GATES)]})
        second = make_dex({other: [('units', [0x0018, 0x0071, 0, 0, 0]), ('call-range', read)],
                           DECODE: [('call', GATES)]})
        self.assertEqual({read: {own, other}}, subject.dex_callers(jar(first, second), [read]))
        self.assertEqual({read: {own}}, subject.dex_callers(jar(first), [read, 'La/Clean;->absent']))
        self.assertEqual({}, subject.dex_callers(jar(second), ['La/Clean;->absent']))
        with self.assertRaisesRegex(ValueError, 'no dex files'):
            subject.dex_callers(jar(), [read])
        with self.assertRaises(ValueError):
            subject.dex_callers(jar(first[:len(first) - 3]), [read])


class NativeTests(unittest.TestCase):
    FUNCTIONS = {
        'android::vold::PublicVolume::doMount': ['nop', ('bl', 'android::vold::PublicVolume::checkInVm'),
                                                  ('b', 'android::vold::PublicVolume::mountInVm')],
        'android::vold::PublicVolume::checkInVm': ['nop'],
        'android::vold::PublicVolume::mountInVm': ['nop', ('bl', 'android::vold::vfat::Check')],
        'android::vold::vfat::Check': ['nop'],
        'android::vold::PublicVolume::UnmountInVm::UnmountInVm': ['nop'],
    }

    def test_direct_calls_and_tail_branches_by_qualified_name(self):
        code = subject.NativeCode(make_elf(self.FUNCTIONS))
        self.assertEqual({'android::vold::PublicVolume::checkInVm', 'android::vold::PublicVolume::mountInVm'},
                         code.callees('android::vold::PublicVolume::doMount'))
        self.assertEqual({'android::vold::vfat::Check'}, code.callees('android::vold::PublicVolume::mountInVm'))
        self.assertTrue(code.has('android::vold::PublicVolume::UnmountInVm::UnmountInVm'))
        self.assertFalse(code.has('android::vold::vfat::Mount'))

    def test_qualified_names(self):
        for symbol, name in (
                ('_ZN7android4vold12PublicVolume7doMountEv', 'android::vold::PublicVolume::doMount'),
                ('_ZNK7android4vold12PublicVolume9guardInVmEi', 'android::vold::PublicVolume::guardInVm'),
                ('_ZN7android4vold12PublicVolume11UnmountInVmC2EPS1_',
                 'android::vold::PublicVolume::UnmountInVm::UnmountInVm'),
                ('_ZN7android4vold12PublicVolume11UnmountInVmD1Ev',
                 'android::vold::PublicVolume::UnmountInVm::~UnmountInVm'),
                ('_ZN7android4vold12readMetadataERKNSt3__112basic_stringIcEEb.llvm.1314170661',
                 'android::vold::readMetadata'),
                ('_Z4mainiPPc', 'main'),
                ('_ZNSt3__16vectorIiE9push_backEOi', None),
                ('_ZN7android4vold4testIiEEvv', None),
                ('memcpy', None)):
            with self.subTest(symbol=symbol):
                self.assertEqual(name, subject.qualified_name(symbol))

    def test_unreadable_binaries_fail(self):
        good = make_elf(self.FUNCTIONS)
        for data, message in ((b'#!/bin/sh\n' + b'\0' * 64, 'not an ELF file'),
                              (make_elf(self.FUNCTIONS, machine=62), 'not an AArch64'),
                              (make_elf(self.FUNCTIONS, debugdata=False), 'no .gnu_debugdata'),
                              (good[:len(good) - 100], 'truncated'),
                              (good.replace(b'\xfd7zXZ', b'\xfd7zXY'), '.gnu_debugdata')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                subject.NativeCode(data)


if __name__ == '__main__':
    unittest.main()
