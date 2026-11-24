"""What built code calls, read from the code itself.

The image checks use it to show that hooks in upstream code reached the build, however they could have been lost:
the invoke instructions of a dex method, and the direct branches (BL and B) of an AArch64 function, resolved through
the function symbols Android keeps in an ELF file's .gnu_debugdata. Anything that cannot be read raises ValueError.
"""
from __future__ import annotations

import io
import lzma
import re
import struct
import zipfile

# ------------------------------------------------------------ dex

# Code units of each dex instruction by opcode (dalvik-bytecode.html). Unused opcodes are one unit.
_SIZES = bytearray([1] * 256)
for _first, _last, _size in (
        (0x02, 0x02, 2), (0x03, 0x03, 3), (0x05, 0x05, 2), (0x06, 0x06, 3), (0x08, 0x08, 2), (0x09, 0x09, 3),
        (0x13, 0x13, 2), (0x14, 0x14, 3), (0x15, 0x16, 2), (0x17, 0x17, 3), (0x18, 0x18, 5), (0x19, 0x1a, 2),
        (0x1b, 0x1b, 3), (0x1c, 0x1c, 2), (0x1f, 0x20, 2), (0x22, 0x23, 2), (0x24, 0x26, 3), (0x29, 0x29, 2),
        (0x2a, 0x2c, 3), (0x2d, 0x3d, 2), (0x44, 0x6d, 2), (0x6e, 0x72, 3), (0x74, 0x78, 3), (0x90, 0xaf, 2),
        (0xd0, 0xe2, 2), (0xfa, 0xfb, 4), (0xfc, 0xfd, 3), (0xfe, 0xff, 2)):
    for _opcode in range(_first, _last + 1):
        _SIZES[_opcode] = _size
_INVOKES = set(range(0x6e, 0x73)) | set(range(0x74, 0x79)) | {0xfa, 0xfb, 0xfc, 0xfd}
_CUSTOM = {0xfc, 0xfd}  # invoke-custom names a call site, not a method
_FIELD_OPS = set(range(0x52, 0x6e))
_NEST_ACCESSOR = re.compile(r'(L[^;]+;)->-\$\$Nest\$f(?:get|put)(.+)')


def _uleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
        if shift > 28:
            raise ValueError('bad ULEB128 in dex file')


class Dex:
    """One dex file: its classes, their methods and the references of each method's code."""

    def __init__(self, data: bytes):
        if data[:4] != b'dex\n' or len(data) < 0x70:
            raise ValueError('not a dex file')
        self.data = data
        (self.string_count, self.string_off, self.type_count, self.type_off, self.proto_count, self.proto_off,
         self.field_count, self.field_off, self.method_count, self.method_off, self.class_count,
         self.class_off) = struct.unpack_from('<12I', data, 0x38)
        self._strings = {}

    def _u32(self, at: int) -> int:
        return struct.unpack_from('<I', self.data, at)[0]

    def string(self, index: int) -> str:
        if index not in self._strings:
            if index >= self.string_count:
                raise ValueError('dex string index out of range')
            _, at = _uleb(self.data, self._u32(self.string_off + 4 * index))
            self._strings[index] = self.data[at:self.data.index(b'\0', at)].decode('utf-8', 'replace')
        return self._strings[index]

    def type(self, index: int) -> str:
        if index >= self.type_count:
            raise ValueError('dex type index out of range')
        return self.string(self._u32(self.type_off + 4 * index))

    def proto(self, index: int) -> str:
        if index >= self.proto_count:
            raise ValueError('dex proto index out of range')
        _, returns, parameters = struct.unpack_from('<III', self.data, self.proto_off + 12 * index)
        types = []
        if parameters:
            count = self._u32(parameters)
            types = [self.type(t) for t in struct.unpack_from(f'<{count}H', self.data, parameters + 4)]
        return '(' + ''.join(types) + ')' + self.type(returns)

    def method(self, index: int, proto: bool = True) -> str:
        """'Lclass;->name(parameters)return', or 'Lclass;->name' without the prototype."""
        if index >= self.method_count:
            raise ValueError('dex method index out of range')
        owner, proto_index, name = struct.unpack_from('<HHI', self.data, self.method_off + 8 * index)
        head = self.type(owner) + '->' + self.string(name)
        return head + self.proto(proto_index) if proto else head

    def field(self, index: int) -> str:
        """'Lclass;->name'."""
        if index >= self.field_count:
            raise ValueError('dex field index out of range')
        owner, _, name = struct.unpack_from('<HHI', self.data, self.field_off + 8 * index)
        return self.type(owner) + '->' + self.string(name)

    def class_data(self, descriptors: set) -> dict:
        """{class descriptor: offset of its class data} for the named classes this file defines."""
        found = {}
        for index in range(self.class_count):
            at = self.class_off + 32 * index
            if self.type(self._u32(at)) in descriptors:
                found[self.type(self._u32(at))] = self._u32(at + 24)
        return found

    def methods(self, class_data: int) -> list:
        """[(method index, code offset)] of a class's direct and virtual methods."""
        if not class_data:
            return []
        counts = []
        at = class_data
        for _ in range(4):
            value, at = _uleb(self.data, at)
            counts.append(value)
        for _ in range(counts[0] + counts[1]):
            _, at = _uleb(self.data, at)
            _, at = _uleb(self.data, at)
        found = []
        for group in (counts[2], counts[3]):
            index = 0
            for _ in range(group):
                step, at = _uleb(self.data, at)
                _, at = _uleb(self.data, at)
                code, at = _uleb(self.data, at)
                index += step
                found.append((index, code))
        return found

    def references(self, code: int) -> tuple[set, set]:
        """The methods ('Lclass;->name') a method's code invokes and the fields ('Lclass;->name') it reads or
        writes."""
        calls, fields = set(), set()
        if not code:
            return calls, fields
        units = self._u32(code + 12)
        start = code + 16
        insns = struct.unpack_from(f'<{units}H', self.data, start)
        at = 0
        while at < units:
            unit = insns[at]
            opcode = unit & 0xff
            if opcode == 0 and unit >> 8 in (1, 2, 3):
                # A switch or array data table, not an instruction.
                if unit == 0x0100:
                    size = 4 + 2 * insns[at + 1]
                elif unit == 0x0200:
                    size = 2 + 4 * insns[at + 1]
                else:
                    width = insns[at + 1]
                    count = insns[at + 2] | insns[at + 3] << 16
                    size = 4 + (width * count + 1) // 2
            else:
                size = _SIZES[opcode]
                if at + size > units:
                    raise ValueError('truncated dex code')
                if opcode in _INVOKES and opcode not in _CUSTOM:
                    called = self.method(insns[at + 1], proto=False)
                    calls.add(called)
                    # D8 reaches a nest member's private field through a synthetic accessor.
                    nest = _NEST_ACCESSOR.fullmatch(called)
                    if nest:
                        fields.add(nest.group(1) + '->' + nest.group(2))
                elif opcode in _FIELD_OPS:
                    fields.add(self.field(insns[at + 1]))
            at += size
        return calls, fields


def archive_dex(data: bytes) -> list:
    """The dex files of a JAR or APK, classes.dex first."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if re.fullmatch(r'classes[0-9]*\.dex', n)]
        names.sort(key=lambda n: int(n[7:-4] or 1))
        return [Dex(archive.read(n)) for n in names]


def dex_references(data: bytes, methods: list) -> dict:
    """{method: (calls, fields)} for the listed methods ('Lclass;->name(parameters)return') of an archive's dex
    files. A method the archive does not define is left out. An archive without dex files raises ValueError."""
    files = archive_dex(data)
    if not files:
        raise ValueError('no dex files')
    wanted = {m.split('->', 1)[0] for m in methods}
    found = {}
    try:
        for dex in files:
            for _, class_data in dex.class_data(wanted).items():
                for index, code in dex.methods(class_data):
                    name = dex.method(index)
                    if name in methods and name not in found:
                        found[name] = dex.references(code)
    except (struct.error, IndexError):
        raise ValueError('truncated dex file') from None
    return found


# ------------------------------------------------------------ AArch64 ELF

EM_AARCH64 = 183
STT_FUNC = 2
MAX_DEBUGDATA = 64 * 1024 * 1024


def _sections(data: bytes) -> list:
    """[(name, type, flags, address, offset, size, link)] of an ELF64 little-endian file."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError('not a 64-bit little-endian ELF file')
    offset, = struct.unpack_from('<Q', data, 0x28)
    entry_size, count, names = struct.unpack_from('<HHH', data, 0x3a)
    raw = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * entry_size) for i in range(count)]
    if names >= count:
        raise ValueError('ELF file has no section names')
    table = raw[names][4]

    def name(at):
        return data[table + at:data.index(b'\0', table + at)].decode('ascii', 'replace')
    return [(name(s[0]), s[1], s[2], s[3], s[4], s[5], s[6]) for s in raw]


def _function_symbols(data: bytes, table: str) -> list:
    """[(address, size, name)] of the defined functions in a symbol table (.symtab or .dynsym)."""
    sections = _sections(data)
    found = []
    for name, _, _, _, offset, size, link in sections:
        if name != table:
            continue
        strings = sections[link][4]
        for at in range(offset, offset + size, 24):
            name_at, info, _, index, address, length = struct.unpack_from('<IBBHQQ', data, at)
            if info & 0xf == STT_FUNC and index and length:
                found.append((address, length, data[strings + name_at:data.index(b'\0', strings + name_at)]
                              .decode('ascii', 'replace')))
    return found


def qualified_name(symbol: str) -> str | None:
    """'android::vold::PublicVolume::doMount' for an Itanium-mangled function name, without its parameters.
    Constructors and destructors are named Class::Class and Class::~Class. None for names this does not read (such as
    templates and std substitutions)."""
    symbol = re.sub(r'\.(llvm|cold|part)\.[0-9.]+$', '', symbol)
    if not symbol.startswith('_Z'):
        return None
    if not symbol.startswith('_ZN'):
        plain = re.match(r'_Z([0-9]+)', symbol)
        if not plain:
            return None
        start = plain.end()
        return symbol[start:start + int(plain.group(1))]
    at, parts = 3, []
    while at < len(symbol) and symbol[at] in 'KVrRO':
        at += 1
    while at < len(symbol) and symbol[at] != 'E':
        number = re.match(r'[0-9]+', symbol[at:])
        if number:
            at += number.end()
            parts.append(symbol[at:at + int(number.group())])
            at += int(number.group())
        elif symbol[at:at + 2] in ('C1', 'C2', 'C3') and parts:
            parts.append(parts[-1])
            at += 2
        elif symbol[at:at + 2] in ('D0', 'D1', 'D2') and parts:
            parts.append('~' + parts[-1])
            at += 2
        else:
            return None
    return '::'.join(parts) if parts and at < len(symbol) else None


class NativeCode:
    """An AArch64 ELF file's functions, by qualified name, and the functions each one branches to directly."""

    def __init__(self, data: bytes):
        if len(data) < 0x40 or data[:4] != b'\x7fELF':
            raise ValueError('not an ELF file')
        if struct.unpack_from('<H', data, 0x12)[0] != EM_AARCH64:
            raise ValueError('not an AArch64 ELF file')
        self.data = data
        try:
            sections = _sections(data)
            self.sections = [s for s in sections if s[2] & 0x4 and s[1] == 1]  # SHF_EXECINSTR, PROGBITS
            mini = [s for s in sections if s[0] == '.gnu_debugdata']
            if not mini:
                raise ValueError('no .gnu_debugdata function symbols')
            unpack = lzma.LZMADecompressor()
            inner = unpack.decompress(data[mini[0][4]:mini[0][4] + mini[0][5]], max_length=MAX_DEBUGDATA)
            if not unpack.eof:
                raise ValueError('.gnu_debugdata is truncated or larger than expected')
            symbols = _function_symbols(inner, '.symtab') + _function_symbols(data, '.dynsym')
        except lzma.LZMAError as error:
            raise ValueError(f'.gnu_debugdata: {error}') from None
        except (struct.error, IndexError):
            raise ValueError('truncated ELF file') from None
        if not symbols:
            raise ValueError('no function symbols')
        self.by_address, self.by_name = {}, {}
        for address, size, symbol in symbols:
            name = qualified_name(symbol)
            if name:
                self.by_address.setdefault(address, set()).add(name)
                self.by_name.setdefault(name, set()).add((address, size))

    def has(self, name: str) -> bool:
        return name in self.by_name

    def _offset(self, address: int) -> int:
        for _, _, _, start, offset, size, _ in self.sections:
            if start <= address < start + size:
                return offset + address - start
        raise ValueError(f'function at {address:#x} is outside the code sections')

    def callees(self, name: str) -> set:
        """The functions every symbol of this name branches to with BL or B, by qualified name."""
        found = set()
        for address, size in sorted(self.by_name[name]):
            base = self._offset(address)
            if base + size > len(self.data):
                raise ValueError(f'{name} runs past the end of the file')
            for at in range(0, size - size % 4, 4):
                word, = struct.unpack_from('<I', self.data, base + at)
                if word >> 26 in (0b100101, 0b000101):
                    offset = word & 0x3ffffff
                    if offset & 0x2000000:
                        offset -= 1 << 26
                    found |= self.by_address.get(address + at + 4 * offset, set())
        return found
