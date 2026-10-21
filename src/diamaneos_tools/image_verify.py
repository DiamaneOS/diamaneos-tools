"""Verify an exported FP6 test image set.

Generic checks bind the set to its record, the kernel prebuilts it was built
with and the vendor generation, and check the security properties every public
build must have.
The device checks in config/fp6-image-checks.json run on the same archive.
File-level checks read the target-files archive the images were made from.
Image-level checks read the exported images.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from . import build_workspace as bw
from . import firmware
from .build_workspace import Action, CheckFailed

ROOT = bw.ROOT
MODULE_SIGNATURE = b'~Module signature appended~\n'
SPARSE_MAGIC = 0xED26FF3A
# liblp's metadata_format.h: the geometry block, then the metadata header.
LP_GEOMETRY_MAGIC, LP_HEADER_MAGIC, LP_GEOMETRY_SIZE = 0x616C4467, 0x414C5030, 4096
F2FS_MAGIC = 0xF2F52010
PARTITION_DIRS = {'system': 'SYSTEM', 'system_ext': 'SYSTEM_EXT', 'product': 'PRODUCT', 'vendor': 'VENDOR',
                  'odm': 'ODM', 'vendor_dlkm': 'VENDOR_DLKM', 'system_dlkm': 'SYSTEM_DLKM'}
MAX_MEMBER_BYTES = 512 * 1024 * 1024


class ToolMissing(Exception):
    pass


# ------------------------------------------------------------ inputs

class TargetFiles:
    """Read-only view of a target-files archive."""

    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path)
        self.infos = {i.filename.rstrip('/'): i for i in self.archive.infolist()}
        self.names = sorted(n for n, i in self.infos.items() if not i.is_dir())

    def close(self):
        self.archive.close()

    def is_symlink(self, path: str) -> bool:
        info = self.infos.get(path)
        return info is not None and stat.S_ISLNK(info.external_attr >> 16)

    def exists(self, path: str) -> bool:
        return path in self.infos or any(n.startswith(path + '/') for n in self.names)

    def link_target(self, path: str) -> str:
        return self.archive.read(self.infos[path]).decode()

    def read(self, path: str) -> bytes:
        info = self.infos.get(path)
        if info is None or info.is_dir():
            raise FileNotFoundError(path)
        if info.file_size > MAX_MEMBER_BYTES:
            raise ValueError('archive member too large: ' + path)
        return self.archive.read(info)

    def glob(self, pattern: str) -> list[str]:
        if pattern.endswith('/**'):
            prefix = pattern[:-2]
            return [n for n in self.names if n.startswith(prefix) and not self.is_symlink(n)]
        if any(c in pattern for c in '*?['):
            return [n for n in self.names if fnmatch.fnmatchcase(n, pattern) and not self.is_symlink(n)]
        return [pattern]

    def extract(self, path: str, directory: Path) -> Path:
        target = directory / PurePosixPath(path).name
        target.write_bytes(self.read(path))
        return target


# The host tools packaging and the checks run from the build's otatools.zip.
OTATOOLS_TOOLS = ('build_super_image', 'make_f2fs', 'avbtool', 'apksigner', 'validate_target_files',
                  'check_target_files_vintf', 'aapt2', 'simg2img', 'lpunpack', 'debugfs_static')


def unpack_otatools(archive: Path, sha256: str, parent: Path) -> Path:
    """Unpack a copy of otatools.zip with this hash into a new directory under
    parent, as GrapheneOS's release scripts do, and return its bin/."""
    from .signing_archive import unpack_tools
    from .signing_inputs import SigningError
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=parent, prefix='otatools-'))
    try:
        # Hashed and unpacked from one copy, so a later change to out/ cannot slip in between.
        snapshot = root / 'otatools.zip'
        with Path(archive).open('rb') as source, snapshot.open('xb') as target:
            shutil.copyfileobj(source, target, 16 * 1024 * 1024)
        if bw.sha_file(snapshot) != sha256:
            raise bw.BuildStepError(f'{Path(archive).name} differs from its recorded hash')
        try:
            bin_dir = unpack_tools(snapshot, root / 'tools') / 'bin'
        except SigningError as error:
            raise bw.BuildStepError(f'otatools.zip cannot be unpacked: {error}') from None
        snapshot.unlink()
        missing = [name for name in OTATOOLS_TOOLS if not (bin_dir / name).is_file()]
        if missing:
            raise bw.BuildStepError('otatools.zip lacks ' + ', '.join(missing))
        return bin_dir
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


class Tools:
    """Host tools from the build's unpacked otatools.zip."""

    def __init__(self, host_bin: Path, src: Path, work: Path | None = None):
        self.host_bin, self.src = host_bin, src
        # Temporary files (unpacked super, expanded images) can take several
        # GB. They go to the workspace, never to /tmp.
        self.work = work or Path(tempfile.gettempdir())

    def scratch(self):
        self.work.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=self.work, prefix='.verify-')

    def path(self, name: str) -> Path:
        if name in ('llvm-readelf', 'llvm-objdump'):
            found = sorted((self.src / 'prebuilts/clang/host/linux-x86').glob('clang-r*/bin/' + name))
            if not found:
                raise ToolMissing(name)
            return found[-1]
        path = self.host_bin / name
        if not path.is_file():
            raise ToolMissing(name)
        return path

    def environment(self) -> dict:
        """releasetools finds avbtool, lpmake and java through PATH."""
        env = dict(os.environ)
        paths = [str(self.host_bin)]
        # The newest JDK: jdk8 sorts after jdk25 as text.
        jdks = sorted((self.src / 'prebuilts/jdk').glob('jdk*/linux-x86'),
                      key=lambda p: int(re.sub(r'\D', '', p.parent.name) or 0))
        if jdks:
            env['JAVA_HOME'] = str(jdks[-1])
            paths.append(str(jdks[-1] / 'bin'))
        env['PATH'] = os.pathsep.join(paths + [env.get('PATH', '')])
        self.work.mkdir(parents=True, exist_ok=True)
        env['TMPDIR'] = str(self.work)
        return env

    def run(self, name: str, args, cwd=None, check=True) -> str:
        result = subprocess.run([str(self.path(name)), *map(str, args)], cwd=cwd, capture_output=True,
                                timeout=3600, env=self.environment())
        if check and result.returncode:
            raise CheckFailed(f'{name} failed: ' + (result.stdout + result.stderr).decode('utf-8', 'replace')[-2000:])
        return (result.stdout + result.stderr).decode('utf-8', 'replace')


class Sources:
    def __init__(self, target_files: TargetFiles, kernel_dir: Path | None, images: Path):
        self.tf, self.kernel_dir, self.images = target_files, kernel_dir, images

    def read(self, spec: str) -> bytes:
        if spec.startswith('kernel:'):
            if self.kernel_dir is None:
                raise FileNotFoundError('no verified kernel prebuilts')
            return (self.kernel_dir / spec.removeprefix('kernel:')).read_bytes()
        if spec.startswith('image:'):
            return (self.images / spec.removeprefix('image:')).read_bytes()
        return self.tf.read(spec)


def text_of(data: bytes) -> str:
    # One character per byte, so binaries can be searched for ASCII strings.
    return data.decode('latin-1')


def lines_of(data: bytes) -> list[str]:
    return text_of(data).splitlines()


# ------------------------------------------------------------ parsers

def boot_header(data: bytes) -> dict:
    if data[:8] != b'ANDROID!':
        raise ValueError('not an Android boot image')
    version = struct.unpack_from('<I', data, 40)[0]
    os_field = struct.unpack_from('<I', data, 16 if version >= 3 else 44)[0]
    kernel_size = struct.unpack_from('<I', data, 8)[0]
    cmdline = data[44:1580] if version >= 3 else data[64:576] + data[608:1632]
    return {'header_version': version, 'os_version_field': os_field, 'kernel_size': kernel_size,
            'page_size': 4096 if version >= 3 else struct.unpack_from('<I', data, 36)[0],
            'cmdline': text_of(cmdline).replace('\0', ' ')}


def vendor_boot_parts(data: bytes) -> tuple[str, bytes]:
    """The kernel command line and the device tree section of a vendor boot image."""
    if data[:8] != b'VNDRBOOT':
        raise ValueError('not a vendor boot image')
    page, ramdisk = struct.unpack_from('<I', data, 12)[0], struct.unpack_from('<I', data, 24)[0]
    header, size = struct.unpack_from('<II', data, 2096)
    start = (-(-header // page) + -(-ramdisk // page)) * page
    return text_of(data[28:2076]).replace('\0', ' '), data[start:start + size]


def boot_kernel(data: bytes) -> bytes:
    header = boot_header(data)
    start = header['page_size']
    return data[start:start + header['kernel_size']]


def avb_payload(data: bytes) -> bytes:
    footer = data[-64:]
    if footer[:4] != b'AVBf':
        raise ValueError('image has no AVB footer')
    return data[:struct.unpack('>Q', footer[12:20])[0]]


def parse_dtb(data: bytes) -> dict:
    """A minimal flattened device tree reader: path -> {'props', 'children'}."""
    magic, total, off_struct, off_strings = struct.unpack_from('>IIII', data, 0)
    if magic != 0xD00DFEED or total > len(data):
        raise ValueError('not a device tree blob')
    strings = data[off_strings:]
    nodes, stack, offset = {}, [], off_struct

    def path_of(names):
        return '/' + '/'.join(names[1:])
    while True:
        token = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        if token == 1:  # FDT_BEGIN_NODE
            end = data.index(b'\0', offset)
            name = data[offset:end].decode()
            offset = (end + 1 + 3) & ~3
            stack.append(name)
            nodes[path_of(stack)] = {'props': {}, 'children': set()}
            if len(stack) > 1:
                nodes[path_of(stack[:-1])]['children'].update({name, name.split('@')[0]})
        elif token == 2:  # FDT_END_NODE
            stack.pop()
        elif token == 3:  # FDT_PROP
            length, name_offset = struct.unpack_from('>II', data, offset)
            offset += 8
            name = strings[name_offset:strings.index(b'\0', name_offset)].decode()
            nodes[path_of(stack)]['props'][name] = data[offset:offset + length]
            offset = (offset + length + 3) & ~3
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            return nodes
        else:
            raise ValueError('malformed device tree structure')


def device_trees(data: bytes) -> list[dict]:
    """Every tree of a device tree image: blobs one after another, or an overlay table (dtbo)."""
    if data[:4] == b'\xd7\xb7\xab\x1e':
        entry_size, count, first = struct.unpack_from('>III', data, 12)
        spans = [struct.unpack_from('>II', data, first + index * entry_size) for index in range(count)]
        return [parse_dtb(data[offset:offset + size]) for size, offset in spans]
    trees, offset = [], 0
    while offset < len(data):
        size = struct.unpack_from('>I', data, offset + 4)[0]
        trees.append(parse_dtb(data[offset:offset + size]))
        offset += size
    return trees


def sparse_to_raw(data: bytes, limit: int | None = None) -> bytes:
    """Expand an Android sparse image (enough of it for header checks).

    With ``limit`` no more than ``limit`` bytes are ever made, whatever sizes
    the image declares. Chunk sizes must agree with the header."""
    magic, _, _, header_size, chunk_header, block, blocks, chunks, _ = struct.unpack_from('<IHHHHIIII', data, 0)
    if magic != SPARSE_MAGIC:
        return data if limit is None else data[:limit]
    if header_size < 28 or chunk_header < 12 or not block or block % 4:
        raise ValueError('malformed sparse header')
    out = bytearray()
    offset = header_size
    for _ in range(chunks):
        if limit is not None and len(out) >= limit:
            break
        kind, _, count, total = struct.unpack_from('<HHII', data, offset)
        if total < chunk_header or offset + total > len(data):
            raise ValueError('truncated sparse chunk')
        body = data[offset + chunk_header:offset + total]
        offset += total
        size = count * block
        want = size if limit is None else min(size, limit - len(out))
        if kind == 0xCAC1:
            if len(body) != size:
                raise ValueError('sparse raw chunk size differs from its block count')
            out += body[:want]
        elif kind == 0xCAC2:
            if len(body) != 4:
                raise ValueError('sparse fill chunk is not 4 bytes')
            out += body * (want // 4) + body[:want % 4]
        elif kind == 0xCAC3:
            if body:
                raise ValueError('sparse skip chunk has data')
            out += bytes(want)
        elif kind != 0xCAC4:
            raise ValueError('unknown sparse chunk type')
    return bytes(out) if limit is None else bytes(out[:limit])


def cil_attributes(text: str) -> dict:
    """typeattributeset NAME EXPR, with EXPR as nested lists of names."""
    result = {}
    for match in re.finditer(r'^\(typeattributeset (\S+) (.*)\)$', text, re.M):
        tokens = re.findall(r'\(|\)|[^\s()]+', match.group(2))
        stack = [[]]
        for token in tokens:
            if token == '(':
                stack.append([])
            elif token == ')':
                done = stack.pop()
                stack[-1].append(done)
            else:
                stack[-1].append(token)
        result[match.group(1)] = stack[0][0] if len(stack[0]) == 1 else stack[0]
    return result


def cil_member(type_name: str, attribute: str, attributes: dict, depth=0) -> bool:
    if type_name == attribute:
        return True
    if depth > 32 or attribute not in attributes:
        return False

    def evaluate(expr):
        if isinstance(expr, str):
            return cil_member(type_name, expr, attributes, depth + 1)
        if expr and expr[0] == 'and':
            return all(evaluate(e) for e in expr[1:])
        if expr and expr[0] == 'or':
            return any(evaluate(e) for e in expr[1:])
        if expr and expr[0] == 'not':
            return not any(evaluate(e) for e in expr[1:])
        if expr and expr[0] == 'all':
            return True
        return any(evaluate(e) for e in expr)
    return evaluate(attributes[attribute])


def aapt2_value(dump: str, name: str) -> str | None:
    lines = dump.splitlines()
    for index, line in enumerate(lines):
        if line.rstrip().endswith(' ' + name):
            for following in lines[index + 1:index + 6]:
                match = re.search(r'\(\) (.+)$', following.strip())
                if match:
                    return match.group(1).strip()
                if 'resource ' in following:
                    break
    return None


def aapt2_array(dump: str, name: str) -> list[str]:
    """The items of an array resource, in order, as strings. aapt2 prints the
    items on indented lines after the ``resource ... array/<name>`` line, up to
    the next resource entry: "quoted" strings (one or several per line), or
    integers in brackets (``[1, 2,`` continued on the next lines)."""
    lines = dump.splitlines()
    items: list[str] = []
    for index, line in enumerate(lines):
        if line.rstrip().endswith(' ' + name):
            for following in lines[index + 1:]:
                if re.search(r'\bresource 0x', following):
                    break
                quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', following)
                if quoted:
                    items += quoted
                elif re.fullmatch(r'\s*\[?-?[0-9]+(?:,\s*-?[0-9]+)*,?\s*\]?\s*', following):
                    items += re.findall(r'-?[0-9]+', following)
            break
    return items


# --------------------------------------------------------- rule engine

def property_lines(data: bytes) -> list[tuple[str, str]]:
    result = []
    for line in lines_of(data):
        if '=' in line and not line.lstrip().startswith('#'):
            key, _, value = line.partition('=')
            result.append((key.strip(), value))
    return result


def rule_files_present(rule, v):
    missing = [p for p in rule['paths'] if not v.tf.exists(p)]
    return not missing, 'missing: ' + ', '.join(missing) if missing else ''


def rule_files_absent(rule, v):
    present = [p for p in rule['paths'] if v.tf.exists(p) or v.tf.is_symlink(p)]
    return not present, 'present: ' + ', '.join(present) if present else ''


def rule_files_not_stock(rule, v):
    """Each file is present and is not the stock file with the listed SHA-256,
    for files that a source build now provides at the stock path."""
    problems = []
    for path, stock_sha256 in sorted(rule['files'].items()):
        if not v.tf.exists(path):
            problems.append('missing ' + path)
        elif hashlib.sha256(v.tf.read(path)).hexdigest() == stock_sha256:
            problems.append(path + ' is the stock file')
    return not problems, '; '.join(problems)


def rule_symlink(rule, v):
    if not v.tf.is_symlink(rule['path']):
        return False, rule['path'] + ' is not a symlink'
    target = v.tf.link_target(rule['path'])
    return target == rule['target'], f'{rule["path"]} -> {target}'


def rule_text(rule, v):
    files = [name for pattern in rule['files'] for name in v.tf.glob(pattern)]
    contents = {}
    for name in files:
        try:
            contents[name] = text_of(v.tf.read(name))
        except FileNotFoundError:
            if rule.get('optional'):
                continue
            return False, 'missing: ' + name
    if not contents:
        if rule.get('optional') or (not rule.get('contains') and not rule.get('lines') and not rule.get('regex')):
            return True, 'no files'
        return False, 'no files match ' + ', '.join(rule['files'])
    problems = []
    any_file = rule.get('match') == 'any'
    for text_value in rule.get('contains', []):
        hits = [n for n, t in contents.items() if text_value in t]
        if (any_file and not hits) or (not any_file and len(hits) != len(contents)):
            problems.append('lacks ' + repr(text_value))
    for text_value in rule.get('absent', []):
        hits = [n for n, t in contents.items() if text_value in t]
        if hits:
            problems.append(repr(text_value) + ' in ' + ', '.join(hits))
    for line in rule.get('lines', []):
        hits = [n for n, t in contents.items() if line in t.splitlines()]
        if (any_file and not hits) or (not any_file and len(hits) != len(contents)):
            problems.append('lacks line ' + repr(line))
    for pattern in rule.get('regex', []):
        hits = [n for n, t in contents.items() if re.search(pattern, t, re.M)]
        if (any_file and not hits) or (not any_file and len(hits) != len(contents)):
            problems.append('no match for ' + repr(pattern))
    for pattern in rule.get('absent_regex', []):
        hits = [n for n, t in contents.items() if re.search(pattern, t, re.M)]
        if hits:
            problems.append(repr(pattern) + ' matches in ' + ', '.join(hits))
    return not problems, '; '.join(problems)


def rule_properties(rule, v):
    pairs = property_lines(v.tf.read(rule['file']))
    problems = []
    for entry in rule.get('equals', []):
        key, _, value = entry.partition('=')
        values = [val for k, val in pairs if k == key]
        if rule.get('mode') == 'all':
            if set(values) != {value}:
                problems.append(f'{key} is {sorted(set(values))} (want only {value!r})')
        elif values != [value]:
            problems.append(f'{key} is {values} (want exactly one {value!r})')
    for key in rule.get('absent', []):
        if any(k == key for k, _ in pairs):
            problems.append(key + ' is set')
    return not problems, '; '.join(problems)


def rule_property_prefix(rule, v):
    found, bad = 0, []
    for name in rule['files']:
        for key, value in property_lines(v.tf.read(name)):
            if key.endswith(rule['key']):
                found += 1
                if not value.startswith(rule['prefix']):
                    bad.append(f'{name}: {key}={value}')
    if not found:
        return False, 'no ' + rule['key'] + ' found'
    return not bad, '; '.join(bad)


def rule_sepolicy_exclusive(rule, v):
    text = text_of(v.tf.read(rule['file']))
    pattern = r'^\(allow (\S+) ' + re.escape(rule['target']) + r' \(' + re.escape(rule['cls']) + r' '
    sources = sorted(set(re.findall(pattern, text, re.M)))
    others = [s for s in sources if s not in rule['allowed']]
    return not others and bool(sources), ('also reachable by ' + ', '.join(others)) if others else (
        '' if sources else 'no rule grants it')


def rule_sepolicy_allows(rule, v):
    text = '\n'.join(text_of(v.tf.read(name)) for name in rule['files'])
    attributes = cil_attributes(text)
    source = re.compile(re.escape(rule['source']) + r'(_[0-9]+)?')
    for match in re.finditer(r'^\(allow (\S+) (\S+) \((\S+) \(([^)]*)\)\)\)$', text, re.M):
        src, target, cls, perms = match.groups()
        if (source.fullmatch(src) and cls == rule['cls'] and rule['perm'] in perms.split()
                and cil_member(rule['target'], target, attributes)):
            return True, f'allowed through {target}'
    return False, f'{rule["source"]} may not {rule["perm"]} {rule["target"]}'


def cil_attribute_sets(text: str) -> dict:
    """Like cil_attributes, but an attribute set in several files (the platform
    and the vendor policy both add to sysfs_type) keeps every expression."""
    result = {}
    for match in re.finditer(r'^\(typeattributeset (\S+) (.*)\)$', text, re.M):
        single = cil_attributes(match.group(0))
        result.setdefault(match.group(1), []).append(single[match.group(1)])
    return result


def cil_expand(name: str, attributes: dict, types: set, memo: dict) -> set:
    """The types an attribute (or a type) stands for, through nested and/or/not.
    attributes maps a name to the list of expressions that add to it."""
    if name in memo:
        return memo[name]
    memo[name] = set()  # cycle guard
    if name not in attributes:
        memo[name] = {name} if name in types else set()
        return memo[name]

    def evaluate(expr):
        if isinstance(expr, str):
            return set(types) if expr == 'all' else cil_expand(expr, attributes, types, memo)
        if expr and expr[0] == 'and':
            result = evaluate(expr[1])
            for e in expr[2:]:
                result &= evaluate(e)
            return result
        if expr and expr[0] == 'or':
            return set().union(*(evaluate(e) for e in expr[1:]))
        if expr and expr[0] == 'not':
            return set(types) - set().union(*(evaluate(e) for e in expr[1:]))
        if expr and expr[0] == 'all':
            return set(types)
        return set().union(*(evaluate(e) for e in expr)) if expr else set()
    memo[name] = set().union(*(evaluate(e) for e in attributes[name]))
    return memo[name]


def rule_sepolicy_sources(rule, v):
    """Every domain the combined policy lets use a permission on a type is listed.

    Reads the platform, mapping and vendor CIL files the rule names (globs
    allowed, versioned compatibility files *.compat.cil skipped) and expands
    attributes on both sides, so platform rules on attributes such as sysfs_type
    count too.
    """
    names = [n for pattern in rule['files'] for n in v.tf.glob(pattern) if not n.endswith('.compat.cil')]
    if not names:
        return False, 'no files match ' + ', '.join(rule['files'])
    text = '\n'.join(text_of(v.tf.read(name)) for name in names)
    attributes = cil_attribute_sets(text)
    types = set(re.findall(r'^\(type (\S+)\)$', text, re.M))
    if rule['target'] not in types:
        return False, rule['target'] + ' is not a type in the policy'
    memo, sources = {}, set()
    for match in re.finditer(r'^\(allow (\S+) (\S+) \((\S+) \(([^)]*)\)\)\)$', text, re.M):
        src, target, cls, perms = match.groups()
        if cls != rule['cls'] or rule['perm'] not in perms.split() or target == 'self':
            continue
        if rule['target'] in cil_expand(target, attributes, types, memo):
            sources |= cil_expand(src, attributes, types, memo)
    others = sorted(sources - set(rule['allowed']))
    return not others, ('also ' + ', '.join(others)) if others else 'only ' + ', '.join(sorted(sources))


def installed_paths(v) -> list[str]:
    """Device paths of the files in the partition directories of the target files."""
    roots = {directory + '/': '/' + partition + '/' for partition, directory in PARTITION_DIRS.items()}
    roots['ROOT/'] = '/'
    return sorted(roots[n[:n.index('/') + 1]] + n[n.index('/') + 1:] for n in v.tf.names
                  if '/' in n and n[:n.index('/') + 1] in roots)


def rule_sepolicy_dormant(rule, v):
    """No installed file can start one of the listed domains.

    A process enters a domain only from a file whose type the domain may use
    as an entry point. Collects those types from the CIL files (attributes
    expanded, *.compat.cil skipped) and fails when an installed file matches a
    file_contexts entry that assigns one of them. Every match counts, also one
    that a later entry would override.
    """
    names = [n for pattern in rule['files'] for n in v.tf.glob(pattern) if not n.endswith('.compat.cil')]
    if not names:
        return False, 'no files match ' + ', '.join(rule['files'])
    text = '\n'.join(text_of(v.tf.read(name)) for name in names)
    attributes = cil_attribute_sets(text)
    types = set(re.findall(r'^\(type (\S+)\)$', text, re.M))
    unknown = sorted(set(rule['domains']) - types)
    if unknown:
        return False, 'not a type in the policy: ' + ', '.join(unknown)
    memo, starts = {}, {}
    for match in re.finditer(r'^\(allow (\S+) (\S+) \((\S+) \(([^)]*)\)\)\)$', text, re.M):
        src, target, cls, perms = match.groups()
        if cls != 'file' or 'entrypoint' not in perms.split() or target == 'self':
            continue
        domains = set(rule['domains']) & cil_expand(src, attributes, types, memo)
        for entry in cil_expand(target, attributes, types, memo) if domains else ():
            starts.setdefault(entry, set()).update(domains)
    contexts = [n for pattern in rule['contexts'] for n in v.tf.glob(pattern)]
    if not contexts:
        return False, 'no files match ' + ', '.join(rule['contexts'])
    paths, problems = installed_paths(v), []
    for name in contexts:
        for line in lines_of(v.tf.read(name)):
            fields = line.split()
            if len(fields) < 2 or fields[0].startswith('#'):
                continue
            label = fields[-1].split(':')
            if len(label) < 3 or label[2] not in starts:
                continue
            try:
                pattern = re.compile(fields[0])
            except re.error:
                problems.append(f'{name}: cannot read the entry for {label[2]}')
                continue
            for path in paths:
                if pattern.fullmatch(path):
                    problems.append(f'{path} can start ' + ', '.join(sorted(starts[label[2]])))
    return not problems, '; '.join(sorted(set(problems))) or f'no file starts any of {len(rule["domains"])} domains'


def cil_xperm_numbers(text: str) -> set:
    """The numbers of one CIL extended-permission list: hexadecimal values and
    (range FIRST LAST) entries. Anything else is refused."""
    numbers = set()
    rest = text
    for first, last in re.findall(r'\(range (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)\)', text):
        numbers.update(range(int(first, 16), int(last, 16) + 1))
    rest = re.sub(r'\(range 0x[0-9a-fA-F]+ 0x[0-9a-fA-F]+\)', ' ', rest)
    for token in rest.replace('(', ' ').replace(')', ' ').split():
        if not re.fullmatch(r'0x[0-9a-fA-F]+', token):
            raise ValueError('unreadable extended-permission list: ' + text)
        numbers.add(int(token, 16))
    return numbers


def rule_sepolicy_xperms(rule, v):
    """Each listed domain may send exactly the listed ioctl numbers to a type.

    Adds up every allowx rule of the CIL files that applies to the domain and
    the target (attributes expanded on both sides, *.compat.cil skipped), so
    a rule on an attribute that widens the list fails too, as does a missing
    list, which would leave every number allowed.
    """
    names = [n for pattern in rule['files'] for n in v.tf.glob(pattern) if not n.endswith('.compat.cil')]
    if not names:
        return False, 'no files match ' + ', '.join(rule['files'])
    text = '\n'.join(text_of(v.tf.read(name)) for name in names)
    attributes = cil_attribute_sets(text)
    types = set(re.findall(r'^\(type (\S+)\)$', text, re.M))
    unknown = sorted(({rule['target']} | set(rule['sources'])) - types)
    if unknown:
        return False, 'not a type in the policy: ' + ', '.join(unknown)
    want = {int(number, 16) for number in rule['allowed']}
    memo, found = {}, {source: set() for source in rule['sources']}
    for match in re.finditer(r'^\(allowx (\S+) (\S+) \((\S+) (\S+) (.*)\)\)$', text, re.M):
        src, target, perm, cls, numbers = match.groups()
        if perm != 'ioctl' or cls != rule['cls'] or rule['target'] not in cil_expand(target, attributes, types, memo):
            continue
        for source in set(rule['sources']) & cil_expand(src, attributes, types, memo):
            found[source] |= cil_xperm_numbers(numbers)
    problems = []
    for source in sorted(found):
        if not found[source]:
            problems.append(source + ' has no ioctl allow-list')
        elif found[source] != want:
            extra, missing = sorted(found[source] - want), sorted(want - found[source])
            problems.append(source + ':' + (' also ' + ', '.join(hex(n) for n in extra[:8]) if extra else '')
                            + (' lacks ' + ', '.join(hex(n) for n in missing) if missing else ''))
    return not problems, '; '.join(problems) or 'only ' + ', '.join(hex(n) for n in sorted(want))


def rule_overlay(rule, v):
    with v.tools.scratch() as temporary:
        apk = v.tf.extract(rule['apk'], Path(temporary))
        dump = v.tools.run('aapt2', ['dump', 'resources', apk])
        problems = [f'{name} is {aapt2_value(dump, name)!r} (want {want!r})'
                    for name, want in sorted(rule.get('values', {}).items()) if aapt2_value(dump, name) != want]
        for name, wanted in sorted(rule.get('arrays', {}).items()):
            have = aapt2_array(dump, name)
            missing = [item for item in wanted if item not in have]
            if missing:
                problems.append(f'{name} is missing {missing} (has {have})')
        if rule.get('target'):
            manifest = v.tools.run('aapt2', ['dump', 'xmltree', '--file', 'AndroidManifest.xml', apk])
            if f'"{rule["target"]}"' not in manifest:
                problems.append('overlay target is not ' + rule['target'])
    return not problems, '; '.join(problems)


def overlay_target(manifest: str) -> str | None:
    """The target package of an overlay manifest dump, or None when it is no overlay."""
    match = re.search(r'E: overlay\b.*?targetPackage\([^)]*\)="([^"]+)"', manifest, re.S)
    return match.group(1) if match else None


def rule_overlay_resources_denied(rule, v):
    """No preinstalled overlay for the target package defines a resource whose
    name matches one of the patterns, except the exact device values the rule
    lists for one overlay."""
    patterns = [re.compile(p) for p in rule['patterns']]
    device_values = rule.get('device_values', {})
    names = sorted({n for pattern in rule['files'] for n in v.tf.glob(pattern) if n.endswith('.apk')})
    problems, checked = [], 0
    with v.tools.scratch() as temporary:
        for name in names:
            apk = v.tf.extract(name, Path(temporary))
            manifest = v.tools.run('aapt2', ['dump', 'xmltree', '--file', 'AndroidManifest.xml', apk])
            if overlay_target(manifest) != rule['target']:
                continue
            checked += 1
            dump = v.tools.run('aapt2', ['dump', 'resources', apk])
            found = re.findall(r'resource 0x[0-9a-f]+ ([a-z]+)/([A-Za-z0-9_.]+)', dump)
            bad = sorted({f'{kind}/{res}' for kind, res in found if any(p.search(res) for p in patterns)}
                         - set(device_values.get(name, ())))
            if bad:
                problems.append(name + ': ' + ', '.join(bad[:8]))
    return not problems, '; '.join(problems) or f'{checked} overlays for {rule["target"]} checked'


def panel_nits(reference: list, level: float) -> float:
    """Luminance at a panel level, from measured (level, nits) pairs:
    straight lines between neighbours in log-log space."""
    points = sorted((float(a), float(b)) for a, b in reference)
    if level <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if level <= x1:
            share = (math.log(level) - math.log(x0)) / (math.log(x1) - math.log(x0))
            return math.exp(math.log(y0) + share * (math.log(y1) - math.log(y0)))
    return points[-1][1]


def rule_display_config(rule, v):
    """The display configuration's brightness table against the measured panel.

    The framework reads nits, backlight and brightness per evenDimmer point.
    The display HAL writes panel level 1 + backlight * (panel_levels - 1). A
    column that does not rise strictly stops system_server at boot."""
    path = rule['path']
    if v.tf.is_symlink(path):
        return False, path + ' is a symlink'
    root = ET.fromstring(v.tf.read(path))

    def number(node, tag):
        text = node.findtext(tag)
        if text is None:
            raise ValueError(f'{path}: no {tag} in {node.tag}')
        return float(text)

    level = lambda backlight: 1 + backlight * (rule['panel_levels'] - 1)
    levels, tolerance, reference = rule['levels'], rule['tolerance'], rule['reference']
    off_curve = lambda backlight, nits: abs(nits / panel_nits(reference, level(backlight)) - 1) > tolerance
    problems = []

    dimmer = root.find('evenDimmer')
    if dimmer is None or dimmer.get('enabled') != 'true':
        return False, 'no enabled evenDimmer brightness map'
    points = [(number(p, 'nits'), number(p, 'backlight'), number(p, 'brightness'))
              for p in dimmer.iterfind('brightnessMapping/brightnessPoint')]
    if len(points) < rule['min_points']:
        return False, f'{len(points)} brightness points (want at least {rule["min_points"]})'
    for index, name in enumerate(('nits', 'backlight', 'brightness')):
        if any(a[index] >= b[index] for a, b in zip(points, points[1:])):
            problems.append(name + ' does not rise strictly')
    (low, floor, first), (high, top, last) = points[0], points[-1]
    if (first, last) != (0.0, 1.0):
        problems.append(f'brightness runs {first} to {last} (want 0 to 1)')
    if high > low and any(abs(b - (n - low) / (high - low)) > 0.0002 for n, _, b in points):
        problems.append('brightness is not linear in nits')
    wrong = [f'{n:g} at level {level(bl):.0f}' for n, bl, _ in points if off_curve(bl, n)]
    if wrong:
        problems.append('nits off the measured panel curve: ' + ', '.join(wrong))
    if number(dimmer, 'transitionPoint') != 0:
        problems.append('evenDimmer transition point is not 0')
    if any(number(p, 'nits') > low for p in dimmer.iterfind('luxToMinimumNitsMap/point')):
        problems.append('the minimum nits rise with lux')
    for name, backlight in (('floor', floor), ('top', top)):
        if int(level(backlight)) != levels[name]:
            problems.append(f'{name} is level {int(level(backlight))} (want {levels[name]})')

    hbm = root.find('highBrightnessMode')
    if hbm is None or hbm.get('enabled') != 'true':
        problems.append('high-brightness mode is not enabled')
    else:
        transition = number(hbm, 'transitionPoint')
        if transition not in [bl for _, bl, _ in points] or int(level(transition)) != levels['manual']:
            problems.append(f'the manual range ends at level {int(level(transition))}, or between points '
                            f'(want level {levels["manual"]})')
        if number(hbm, 'minimumLux') != rule['minimum_lux']:
            problems.append(f'sunlight starts at {number(hbm, "minimumLux"):g} lux (want {rule["minimum_lux"]})')
        if number(hbm, 'timing/timeMaxSecs') < number(hbm, 'timing/timeWindowSecs') + number(hbm, 'timing/timeMinSecs'):
            problems.append('sunlight brightness has a time limit')
        if hbm.findtext('allowInLowPowerMode') != 'true':
            problems.append('battery saver blocks sunlight brightness')

    plain = [(number(p, 'value'), number(p, 'nits')) for p in root.iterfind('screenBrightnessMap/point')]
    if not plain or plain[0][0] > floor or plain[-1][0] < top:
        problems.append('screenBrightnessMap does not cover the brightness range')
    elif any(a[0] > b[0] or a[1] > b[1] for a, b in zip(plain, plain[1:])):
        problems.append('screenBrightnessMap falls')
    elif any(off_curve(bl, n) for bl, n in plain) or any((bl, n) not in plain for n, bl, _ in points):
        problems.append('screenBrightnessMap differs from the evenDimmer points or the panel curve')
    doze = root.findtext('defaultDozeBrightness')
    if doze is None or int(level(float(doze))) != levels['doze']:
        problems.append(f'doze brightness is not level {levels["doze"]}')

    with v.tools.scratch() as temporary:
        dump = v.tools.run('aapt2', ['dump', 'resources', v.tf.extract(rule['overlay'], Path(temporary))])
    if aapt2_value(dump, 'bool/config_evenDimmerEnabled') != 'true':
        problems.append('the overlay does not enable evenDimmer')
    for name, want in (('Minimum', floor), ('Maximum', top)):
        have = aapt2_value(dump, f'dimen/config_screenBrightnessSetting{name}Float')
        if have is None or abs(float(have) - want) > 1e-5 * want:  # aapt2 prints six digits
            problems.append(f'overlay {name.lower()} backlight is {have} (table: {want:g})')
    return not problems, '; '.join(problems)


def signer_digests(v, apk: Path) -> set:
    output = v.tools.run('apksigner', ['verify', '--print-certs', apk])
    return set(re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', output))


def rule_apk(rule, v):
    problems = []
    with v.tools.scratch() as temporary:
        directory = Path(temporary)
        apk = v.tf.extract(rule['path'], directory)
        if 'permissions' in rule:
            dump = v.tools.run('aapt2', ['dump', 'permissions', apk])
            requested = sorted(re.findall(r"^uses-permission: name='([^']+)'", dump, re.M))
            if requested != sorted(rule['permissions']):
                problems.append('requests ' + ', '.join(requested))
        if rule.get('manifest_regex'):
            tree = v.tools.run('aapt2', ['dump', 'xmltree', '--file', 'AndroidManifest.xml', apk])
            problems += ['manifest does not match ' + pattern for pattern in rule['manifest_regex']
                         if not re.search(pattern, tree, re.M)]
        if rule.get('not_signed_like'):
            other_dir = directory / 'other'
            other_dir.mkdir()
            other = v.tf.extract(rule['not_signed_like'], other_dir)
            mine, theirs = signer_digests(v, apk), signer_digests(v, other)
            if not mine or not theirs or mine & theirs:
                problems.append('signed with the same key as ' + rule['not_signed_like'])
    return not problems, '; '.join(problems)


def rule_elf_exports(rule, v):
    with v.tools.scratch() as temporary:
        path = v.tf.extract(rule['path'], Path(temporary))
        dump = v.tools.run('llvm-readelf', ['--dyn-syms', '-W', path])
    defined = set()
    for line in dump.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[0].rstrip(':').isdigit() and fields[6] != 'UND':
            defined.add(fields[7].split('@')[0])
    missing = [s for s in rule['symbols'] if s not in defined]
    return not missing, 'does not export ' + ', '.join(missing) if missing else ''


def rule_codec2_abi_guard(rule, v):
    """Prove the stock Codec2 library's 256-byte GraphicBuffer allocations bind a
    256-byte object, not Android 17's 3376-byte one.

    The stock libcodec2_vndk was built against Android 14, where GraphicBuffer is
    256 bytes: it allocates 256 bytes and calls the constructor. The vendor
    product renames its libui dependency to the compat library and its six
    GraphicBuffer symbols to GraphicBufV34, which the compat defines at 256 bytes
    (a build-time static_assert). On the built image this confirms that the
    dependency and the construction symbols were renamed, so every GraphicBuffer
    construction binds to the 256-byte compat and none reaches Android 17 libui's
    3376-byte GraphicBuffer, and that the stock 256-byte allocations are intact.

    The stock lens distortion node of the camera provider has the same problem
    and the same fix (three GraphicBuffer symbols, two allocations), so a second
    rule runs this check on it.
    """
    with v.tools.scratch() as temporary:
        path = v.tf.extract(rule['path'], Path(temporary))
        dyn = v.tools.run('llvm-readelf', ['--dynamic', '--dyn-syms', '-W', path])
        text = v.tools.run('llvm-objdump', ['-d', '--no-show-raw-insn', path])
    problems = []
    needed = {line.split('[')[-1].rstrip(']') for line in dyn.splitlines() if '(NEEDED)' in line}
    for name in rule['needed_present']:
        if name not in needed:
            problems.append('does not depend on ' + name)
    for name in rule['needed_absent']:
        if name in needed:
            problems.append('still depends on ' + name)
    undefined = set()
    for line in dyn.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[0].rstrip(':').isdigit() and fields[6] == 'UND':
            undefined.add(fields[7].split('@')[0])
    for name in rule['symbols_present']:
        if name not in undefined:
            problems.append('does not import ' + name)
    for name in rule['symbols_absent']:
        if name in undefined:
            problems.append('still imports ' + name)
    # Each renamed constructor is still reached (the sites were not optimised
    # away): the renames above then guarantee every construction binds to the
    # 256-byte compat.
    lines = [line.replace('\t', ' ') for line in text.splitlines()]
    is_bl = re.compile(r'\bbl\b')
    calls = {name: 0 for name in rule['ctor_symbols']}
    for line in lines:
        if is_bl.search(line):
            for name in rule['ctor_symbols']:
                if ('<' + name + '@plt>') in line or ('<' + name + '>') in line:
                    calls[name] += 1
    for name, count in calls.items():
        if count < 1:
            problems.append('no construction call to ' + name)
    # The stock 256-byte allocations are intact: an operator new call (bl _Znwm)
    # preceded (within a few instructions) by mov w0, #<alloc_bytes>. The four
    # GraphicBuffer construction sites each have one. Require them all.
    alloc = re.compile(r'\bw0, #(0x[0-9a-f]+|\d+)\b')
    sized = 0
    for i, line in enumerate(lines):
        if is_bl.search(line) and '_Znwm' in line:
            for prev in lines[max(0, i - 3):i]:
                m = alloc.search(prev)
                if m and int(m.group(1), 0) == rule['alloc_bytes']:
                    sized += 1
                    break
    if sized < rule['min_sized_allocations']:
        problems.append('found %d %d-byte allocations before operator new, want at least %d'
                        % (sized, rule['alloc_bytes'], rule['min_sized_allocations']))
    return not problems, '; '.join(problems)


def pattern_bytes(pattern: dict) -> bytes:
    return bytes.fromhex(pattern['hex']) if 'hex' in pattern else pattern['text'].encode('latin-1')


def rule_binary_count(rule, v):
    data = v.sources.read(rule['file'])
    problems = []
    for pattern in rule['patterns']:
        count = data.count(pattern_bytes(pattern))
        if 'count' in pattern and count != pattern['count']:
            problems.append(f'{pattern.get("hex") or pattern["text"]!r} found {count} times (want {pattern["count"]})')
        if 'min' in pattern and count < pattern['min']:
            problems.append(f'{pattern.get("hex") or pattern["text"]!r} found {count} times (want at least {pattern["min"]})')
    return not problems, '; '.join(problems)


def rule_devicetree(rule, v):
    nodes = parse_dtb(v.sources.read(rule['file']))
    problems = []
    for check in rule['checks']:
        node = nodes.get(check['node'])
        if node is None:
            problems.append('missing node ' + check['node'])
            continue
        if 'child_absent' in check:
            if check['child_absent'] in node['children']:
                problems.append(f'{check["node"]} still has {check["child_absent"]}')
            continue
        if check.get('absent'):
            if check['property'] in node['props']:
                problems.append(f'{check["node"]} still has {check["property"]}')
            continue
        value = node['props'].get(check['property'])
        if value is None:
            problems.append(f'{check["node"]} lacks {check["property"]}')
            continue
        if 'string' in check and value.rstrip(b'\0').decode(errors='replace') != check['string']:
            problems.append(f'{check["property"]} is {value!r}')
        if 'cells' in check:
            cells = list(struct.unpack('>' + 'I' * (len(value) // 4), value))
            if cells != check['cells']:
                problems.append(f'{check["property"]} is {cells} (want {check["cells"]})')
        if 'word' in check:
            words = value.rstrip(b'\0').decode(errors='replace').split()
            items = [w.split('=', 1)[1] for w in words if w.startswith(check['word'] + '=')]
            if not any(check['list_includes'] in item.split(',') for item in items):
                problems.append(f'{check["word"]} does not include {check["list_includes"]}')
    return not problems, '; '.join(problems)


def rule_component_override(rule, v):
    root = ET.fromstring(v.tf.read(rule['file']))
    overrides = [o for o in root.iter('component-override') if o.get('package') == rule['package']]
    problems = []
    if len(overrides) != 1:
        problems.append(f'{len(overrides)} overrides for {rule["package"]}')
    else:
        state = {c.get('class'): c.get('enabled') for c in overrides[0].findall('component')}
        if state != rule['components']:
            problems.append(f'components are {state}')
    naming = set()
    for partition in rule['scan']:
        for sub in ('etc/sysconfig/', 'etc/permissions/'):
            prefix = f'{partition}/{sub}'
            for name in v.tf.names:
                if not name.startswith(prefix) or '/' in name[len(prefix):] or v.tf.is_symlink(name):
                    continue
                data = v.tf.read(name)
                if rule['package'].encode() in data:
                    naming.add(name)
                if b'component-override' in data and name != rule['file']:
                    for other in ET.fromstring(data).iter('component-override'):
                        if other.get('package') == rule['package']:
                            problems.append('another override in ' + name)
    if naming != set(rule['allowed_files']):
        problems.append('files naming the package differ: ' + ', '.join(sorted(naming ^ set(rule['allowed_files']))))
    return not problems, '; '.join(problems)


def rule_zip_contains(rule, v):
    """A text in a file or in one of its dex files. Texts in 'absent' must be in neither."""
    data = v.tf.read(rule['file'])
    dex = []

    def dex_files():
        if not dex:
            with zipfile.ZipFile(__import__('io').BytesIO(data)) as inner:
                dex.extend(inner.read(name) for name in inner.namelist()
                           if name.startswith('classes') and name.endswith('.dex'))
            dex.append(b'')
        return dex

    problems = []
    needle = rule['text'].encode()
    if needle not in data and not any(needle in blob for blob in dex_files()):
        problems.append('does not contain ' + rule['text'])
    for text in rule.get('absent', []):
        if text.encode() in data or any(text.encode() in blob for blob in dex_files()):
            problems.append('contains ' + text)
    return not problems, '; '.join(problems)


def ext4_file(v, source: Path, inside: str, directory: Path) -> tuple[str, bytes]:
    """stat and content of one file of a raw ext4 image, through debugfs."""
    info = v.tools.run('debugfs_static', ['-R', f'stat {inside}', source])
    dump = directory / 'dump'
    # debugfs writes nothing for a file it cannot read. Never read an older dump.
    dump.unlink(missing_ok=True)
    v.tools.run('debugfs_static', ['-R', f'dump {inside} {dump}', source])
    return info, dump.read_bytes()


def image_file(v, image: str, path: str, directory: Path) -> tuple[str, bytes]:
    """stat and content of a file inside an ext4 image, through debugfs."""
    source = v.images / f'{image}.img'
    with source.open('rb') as stream:
        sparse = struct.unpack('<I', stream.read(4))[0] == SPARSE_MAGIC
    if sparse:
        raw = directory / f'{image}.raw'
        v.tools.run('simg2img', [source, raw])
        source = raw
    return ext4_file(v, source, '/' + path.split('/', 1)[1], directory)


EXT4_MAGIC = bytes.fromhex('53ef')  # at byte 1080 of an ext4 image
MAX_EXT4_DIRECTORIES = 256


def ext4_files(v, source: Path) -> dict:
    """Every entry of a raw ext4 image that is not a directory: path -> mode.

    Reads debugfs "ls -p" lines (/inode/mode/uid/gid/name/size/), directory by
    directory. A line or name it cannot read is an error, never skipped."""
    found, pending, seen = {}, [''], 0
    while pending:
        directory = pending.pop()
        seen += 1
        if seen > MAX_EXT4_DIRECTORIES:
            raise ValueError('too many directories in the ext4 image')
        for line in v.tools.run('debugfs_static', ['-R', f'ls -p {directory or "/"}', source]).splitlines():
            if not line.startswith('/'):
                continue  # the debugfs banner and blank lines
            entry = re.fullmatch(r'/(\d+)/([0-7]{6})/\d+/\d+/([^/]*)/\d*/', line)
            if entry is None:
                raise ValueError('unreadable ext4 directory entry: ' + line)
            inode, mode, name = int(entry.group(1)), int(entry.group(2), 8), entry.group(3)
            if not inode or name in ('.', '..'):
                continue  # an unused entry, or the directory and its parent
            if not re.fullmatch(r'[A-Za-z0-9_.+@-]+', name):
                raise ValueError('unsupported ext4 file name: ' + repr(name))
            if stat.S_ISDIR(mode):
                pending.append(f'{directory}/{name}')
            else:
                found[f'{directory}/{name}'.lstrip('/')] = mode
    return found


def rule_apex_payload(rule, v):
    """The ext4 payload of an APEX holds exactly the listed files, each with
    its bytes, owner, mode and label.

    The container's own hash already binds the payload. This names what the
    package brings (its service, init file, VINTF fragment, libraries and
    their labels), so a new package version is reviewed file by file."""
    problems = []
    with v.tools.scratch() as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(v.tf.extract(rule['file'], directory)) as apex:
            if 'apex_payload.img' not in apex.namelist():
                return False, rule['file'] + ' has no apex_payload.img'
            member = apex.getinfo('apex_payload.img')
            if member.file_size > MAX_MEMBER_BYTES:
                raise ValueError('APEX payload too large: ' + rule['file'])
            payload = directory / 'apex_payload.img'
            payload.write_bytes(apex.read(member))
        with payload.open('rb') as stream:
            stream.seek(1080)
            if stream.read(2) != EXT4_MAGIC:
                return False, 'the payload of ' + rule['file'] + ' is not an ext4 image'
        found = ext4_files(v, payload)
        for name, want in sorted(rule['payload'].items()):
            if name not in found:
                problems.append('missing ' + name)
                continue
            info, content = ext4_file(v, payload, '/' + name, directory)
            if not stat.S_ISREG(found[name]) or 'Type: regular' not in info:
                problems.append(name + ' is not a regular file')
            if hashlib.sha256(content).hexdigest() != want['sha256']:
                problems.append('bytes differ in ' + name)
            if not re.search(r'Mode: +0?' + want['mode'].lstrip('0') + r'\b', info) or \
                    not re.search(rf'User: +{want["uid"]} +Group: +{want["gid"]}\b', info):
                problems.append('owner or mode differ in ' + name)
            if f'"{want["label"]}' not in info:
                problems.append('label differs in ' + name)
        problems += ['unlisted ' + name for name in sorted(set(found) - set(rule['payload']))]
    return not problems, '; '.join(problems) or f'{len(found)} files'


def rule_file_metadata(rule, v):
    partition, _, rest = rule['path'].partition('/')
    problems = []
    data = v.tf.read(PARTITION_DIRS[partition] + '/' + rest)
    if hashlib.sha256(data).hexdigest() != rule['sha256']:
        problems.append('bytes differ in target-files')
    config = lines_of(v.tf.read(f'META/{partition}_filesystem_config.txt'))
    want = f'{rule["path"]} {rule["uid"]} {rule["gid"]} {rule["mode"].lstrip("0")} capabilities=0x0'
    if want not in config:
        problems.append('owner or mode differ in the filesystem config')
    with v.tools.scratch() as temporary:
        info, content = image_file(v, rule['image'], rule['path'], Path(temporary))
    if hashlib.sha256(content).hexdigest() != rule['sha256']:
        problems.append(f'bytes differ in {rule["image"]}.img')
    if not re.search(r'Mode: +0?' + rule['mode'].lstrip('0') + r'\b', info) or \
            not re.search(rf'User: +{rule["uid"]} +Group: +{rule["gid"]}\b', info):
        problems.append(f'owner or mode differ in {rule["image"]}.img')
    if f'"{rule["label"]}' not in info:
        problems.append(f'label differs in {rule["image"]}.img')
    return not problems, '; '.join(problems)


RULES = {'files_present': rule_files_present, 'files_absent': rule_files_absent,
         'files_not_stock': rule_files_not_stock, 'symlink': rule_symlink,
         'text': rule_text, 'properties': rule_properties, 'property_prefix': rule_property_prefix,
         'sepolicy_exclusive': rule_sepolicy_exclusive, 'sepolicy_allows': rule_sepolicy_allows,
         'sepolicy_sources': rule_sepolicy_sources, 'sepolicy_dormant': rule_sepolicy_dormant,
         'sepolicy_xperms': rule_sepolicy_xperms,
         'overlay': rule_overlay, 'overlay_resources_denied': rule_overlay_resources_denied,
         'display_config': rule_display_config,
         'apk': rule_apk, 'elf_exports': rule_elf_exports,
         'binary_count': rule_binary_count, 'devicetree': rule_devicetree,
         'component_override': rule_component_override, 'zip_contains': rule_zip_contains,
         'file_metadata': rule_file_metadata, 'apex_payload': rule_apex_payload,
         'codec2_abi_guard': rule_codec2_abi_guard}


def validate_rules(document: dict) -> list:
    if document.get('schema_version') != 1 or not isinstance(document.get('rules'), list):
        raise ValueError('invalid image checks document')
    ids = set()
    for rule in document['rules']:
        if not isinstance(rule, dict) or rule.get('type') not in RULES:
            raise ValueError('unknown image check type: ' + str(rule.get('type')))
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', str(rule.get('id'))) or rule['id'] in ids:
            raise ValueError('invalid or duplicate image check id: ' + str(rule.get('id')))
        if not isinstance(rule.get('why'), str) or not rule['why'].strip():
            raise ValueError('image check needs a reason: ' + rule['id'])
        if 'official' in rule and not isinstance(rule['official'], bool):
            raise ValueError('image check "official" must be true or false: ' + rule['id'])
        ids.add(rule['id'])
    return document['rules']


def rule_applies(rule: dict, variant: str, official: bool) -> bool:
    """A rule may name the variants it checks, and whether it checks only
    official builds (true) or only the others (false)."""
    if rule.get('variants') and variant not in rule['variants']:
        return False
    return rule.get('official', official) == official


# ------------------------------------------------------ generic checks

class Verification:
    def __init__(self, images: Path, record: dict, target_files: TargetFiles, tools: Tools, config: dict,
                 kernel_dir: Path | None, vendor_dir: Path | None, packaging: dict | None, src: Path,
                 firmware_inventory: dict | None = None):
        self.images, self.record, self.tf, self.tools, self.config = images, record, target_files, tools, config
        self.kernel_dir, self.vendor_dir, self.packaging, self.src = kernel_dir, vendor_dir, packaging, src
        self.firmware_inventory = firmware_inventory or firmware.load_inventory()
        self.kernel_problem = ''
        self.sources = Sources(target_files, kernel_dir, images)
        self.variant = record['variant']
        self.results = []

    def add(self, check_id: str, why: str, func):
        try:
            ok, detail = func()
        except ToolMissing as missing:
            ok, detail = False, f'tool missing: {missing}'
        except (OSError, ValueError, KeyError, struct.error, ET.ParseError, zipfile.BadZipFile,
                subprocess.SubprocessError, CheckFailed) as error:
            ok, detail = False, f'{type(error).__name__}: {error}'
        self.results.append({'id': check_id, 'status': 'PASS' if ok else 'FAIL', 'why': why, 'detail': detail})


def check_record(v):
    problems = []
    if v.record.get('release') is not False or v.record.get('signing') != 'public-test-keys' or not v.record.get('never_lock'):
        problems.append('build.json does not mark a test build that must never be locked')
    problems += record_problems(v)
    return not problems, '; '.join(problems)


def record_problems(v) -> list:
    """Where the image set differs from its build record."""
    from .image_package import read_sums
    sums = read_sums(v.images)
    problems = []
    if not isinstance(v.record.get('official', False), bool):
        problems.append('build.json official is not true or false')
    if sums.get(v.record['target_files']['file']) != v.record['target_files']['sha256']:
        problems.append('target-files copy differs from the build record')
    for name, digest in v.record['images'].items():
        if sums.get(name + '.img') != digest:
            problems.append(name + '.img differs from the build record')
    return problems


def check_test_keys(v):
    problems = []
    if ('ro.build.tags', 'test-keys') not in property_lines(v.tf.read('SYSTEM/build.prop')):
        problems.append('ro.build.tags is not test-keys')
    for name in ('SYSTEM/build.prop', 'SYSTEM_EXT/etc/build.prop', 'PRODUCT/etc/build.prop', 'VENDOR/build.prop'):
        for key, value in property_lines(v.tf.read(name)):
            if key.endswith('build.fingerprint') and not value.endswith('/test-keys'):
                problems.append(f'{name}: {key} does not end in test-keys')
    return not problems, '; '.join(problems)


def avb_chains(info: str) -> dict:
    chains = {}
    for block in info.split('Chain Partition descriptor:')[1:]:
        name = re.search(r'Partition Name:\s+(\S+)', block)
        location = re.search(r'Rollback Index Location:\s+(\d+)', block)
        flags = re.search(r'Flags:\s+(\d+)', block)
        if name and location:
            chains[name.group(1)] = (int(location.group(1)), int(flags.group(1)) if flags else 0)
    return chains


def avb_hashtrees(info: str) -> dict:
    """Partition name -> hash algorithm of every hashtree descriptor in avbtool info output."""
    trees = {}
    for block in re.split(r'\n\s*(?=\S[^\n]*descriptor:)', info):
        if not block.lstrip().startswith('Hashtree descriptor:'):
            continue
        name = re.search(r'Partition Name:\s+(\S+)', block)
        algorithm = re.search(r'Hash Algorithm:\s+(\S+)', block)
        if name and algorithm:
            trees[name.group(1)] = algorithm.group(1)
    return trees


def check_avb(v):
    key = v.src / v.config['avb']['test_key']
    v.tools.run('avbtool', ['verify_image', '--image', v.images / 'vbmeta.img', '--key', key,
                            '--follow_chain_partitions'], cwd=v.images)
    problems, info = avb_layout(v)
    if lockable(v.record):
        indexes = {'vbmeta': rollback_index(info)}
        for name in sorted(v.config['avb']['chains']):
            indexes[name] = rollback_index(v.tools.run('avbtool', ['info_image', '--image', v.images / f'{name}.img']))
        problems += rollback_problems(v.record, indexes)
    return not problems, '; '.join(problems)


def rollback_index(info: str):
    """The rollback index in the image's own vbmeta header, or the list of what avbtool printed."""
    # avbtool prints the release string as it is, so a field counts only once.
    found = re.findall(r'(?m)^Rollback Index:\s+(\d+)\s*$', info.split('Descriptors:')[0])
    return int(found[0]) if len(found) == 1 else found


def lockable(record: dict) -> bool:
    """Official and release-keys builds may be locked, so their indexes must advance."""
    return record.get('official') is True or record.get('signing') == 'release-keys'


def rollback_problems(record: dict, indexes: dict) -> list:
    """An official or release-keys build needs a non-zero rollback index on vbmeta and every chain.
    Index 0 lets an older signed image of the same key boot on a locked phone."""
    if not lockable(record):
        return []
    zero = sorted(name for name, index in indexes.items() if type(index) is not int or index <= 0)
    return ['no rollback index above 0 on ' + ', '.join(zero)] if zero else []


def avb_layout(v) -> tuple[list, str]:
    """Where the AVB chain differs from the published FP6 layout, and vbmeta's avbtool description."""
    info = v.tools.run('avbtool', ['info_image', '--image', v.images / 'vbmeta.img'])
    chains = avb_chains(info)
    want = {name: (location, 0) for name, location in v.config['avb']['chains'].items()}
    problems = [] if chains == want else [f'chains are {chains} (want {want})']
    header = info.split('Descriptors:')[0]
    if re.findall(r'(?m)^Rollback Index Location:\s+(\d+)\s*$', header) != ['0']:
        problems.append('vbmeta does not use rollback index location 0')
    system = v.tools.run('avbtool', ['info_image', '--image', v.images / 'vbmeta_system.img'])
    if not re.search(r'Partition Name:\s+pvmfw\b', system):
        problems.append('vbmeta_system lacks the pvmfw descriptor')
    trees = {**avb_hashtrees(info), **avb_hashtrees(system)}
    algorithm = v.config['avb']['hashtree_algorithm']
    if set(trees) != set(v.config['images']['logical']):
        problems.append(f'hashtrees for {sorted(trees)} (want {sorted(v.config["images"]["logical"])})')
    weak = sorted(name for name, used in trees.items() if used != algorithm)
    if weak:
        problems.append(f'hashtrees not {algorithm}: ' + ', '.join(f'{n} ({trees[n]})' for n in weak))
    return problems, info


def check_boot_headers(v):
    problems = []
    for name in v.config['avb']['zero_header_os_fields']:
        header = boot_header((v.images / f'{name}.img').read_bytes())
        if header['os_version_field']:
            problems.append(f'{name}.img header OS field is 0x{header["os_version_field"]:08x}')
    for name in ('boot', 'init_boot'):
        info = v.tools.run('avbtool', ['info_image', '--image', v.images / f'{name}.img'])
        if f'com.android.build.{name}.os_version' not in info:
            problems.append(f'{name}.img lacks its AVB OS version property')
    if problems and any('header OS field' in p for p in problems):
        problems.append('the Android release tools rebuilt the image with the platform version in its header, '
                        'where the device configuration asks for zeros')
    return not problems, '; '.join(problems)


def misc_value(v, key: str) -> str | None:
    for name in ('META/misc_info.txt', 'META/dynamic_partitions_info.txt'):
        try:
            for k, value in property_lines(v.tf.read(name)):
                if k == key:
                    return value
        except FileNotFoundError:
            continue
    return None


def check_super(v):
    size = misc_value(v, 'super_partition_size')
    problems = []
    with v.tools.scratch() as temporary:
        directory = Path(temporary)
        raw = directory / 'super.raw'
        with (v.images / 'super.img').open('rb') as stream:
            super_sparse = struct.unpack('<I', stream.read(4))[0] == SPARSE_MAGIC
        if super_sparse:
            v.tools.run('simg2img', [v.images / 'super.img', raw])
        else:
            raw.symlink_to(v.images / 'super.img')
        if size is None or raw.stat().st_size != int(size):
            problems.append(f'raw super is {raw.stat().st_size} bytes (partition {size})')
        parts = directory / 'parts'
        parts.mkdir()
        v.tools.run('lpunpack', [raw, parts])
        raw.unlink()
        for name in v.config['images']['logical']:
            unpacked = parts / f'{name}_a.img'
            image = v.images / f'{name}.img'
            with image.open('rb') as stream:
                sparse = struct.unpack('<I', stream.read(4))[0] == SPARSE_MAGIC
            if sparse:
                expanded = directory / f'{name}.expanded'
                v.tools.run('simg2img', [image, expanded])
                image = expanded
            if not unpacked.is_file() or bw.sha_file(unpacked) != bw.sha_file(image):
                problems.append(f'{name} in super.img differs from {name}.img')
        for other in parts.glob('*_b.img'):
            if other.stat().st_size:
                problems.append(other.name + ' is not empty')
    # The install writes super_empty.img in fastbootd, then each logical image.
    names, devices = super_metadata((v.images / 'super_empty.img').read_bytes())
    if sorted(names) != sorted(f'{n}_{s}' for n in v.config['images']['logical'] for s in 'ab'):
        problems.append('super_empty.img lists ' + (', '.join(names) or 'no partitions'))
    if size is None or devices != {'super': int(size)}:
        problems.append(f'super_empty.img describes {devices} (partition {size})')
    return not problems, '; '.join(problems)


def super_metadata(data: bytes) -> tuple[list, dict]:
    """The partition names and block device sizes in a metadata-only super image."""
    if (len(data) < LP_GEOMETRY_SIZE + 128 or struct.unpack_from('<I', data)[0] != LP_GEOMETRY_MAGIC
            or struct.unpack_from('<I', data, LP_GEOMETRY_SIZE)[0] != LP_HEADER_MAGIC):
        return [], {}
    tables = LP_GEOMETRY_SIZE + struct.unpack_from('<I', data, LP_GEOMETRY_SIZE + 8)[0]
    # Offset, count and size of the partition, extent, group and block device entries.
    descriptors = struct.unpack_from('<12I', data, LP_GEOMETRY_SIZE + 80)

    def entries(index):
        offset, count, size = descriptors[3 * index:3 * index + 3]
        return [data[tables + offset + i * size:tables + offset + (i + 1) * size] for i in range(count)]
    names = [entry[:36].rstrip(b'\0').decode() for entry in entries(0)]
    devices = {entry[24:60].rstrip(b'\0').decode(): struct.unpack_from('<Q', entry, 16)[0] for entry in entries(3)}
    return names, devices


def check_validators(v):
    archive = v.images / v.record['target_files']['file']
    v.tools.run('validate_target_files', [archive], cwd=v.src)
    v.tools.run('check_target_files_vintf', [archive], cwd=v.src)
    return True, ''


def board_lists(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        if ' := ' in line:
            key, _, rest = line.partition(' := ')
            values[key.strip()] = [PurePosixPath(n).name for n in rest.split()]
    return values


def module_key(name: str) -> str:
    return PurePosixPath(name.strip()).name.removesuffix('.ko').replace('-', '_')


def kernel_prebuilts(src: Path, record: dict) -> tuple[Path | None, str]:
    """The kernel prebuilts checkout the set was built with, or None and why.

    It must be the project path and commit build.json names, with nothing
    changed, so the checks compare the images with exactly those files.
    """
    from .product_inputs import KERNEL_PREBUILTS
    named = record.get('kernel_prebuilts') or {}
    if named.get('path') != KERNEL_PREBUILTS or not re.fullmatch(r'[0-9a-f]{40}', str(named.get('commit'))):
        return None, 'build.json names no kernel prebuilts commit'
    directory = src / KERNEL_PREBUILTS
    if not directory.is_dir() or directory.is_symlink():
        return None, f'the kernel prebuilts checkout {KERNEL_PREBUILTS} is missing'
    try:
        head = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                              timeout=60)
        status = subprocess.run(['git', '-C', str(directory), 'status', '--porcelain=v1', '--untracked-files=all'],
                                capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f'cannot read the kernel prebuilts checkout: {error}'
    if head.returncode or head.stdout.strip() != named['commit']:
        return None, (f'{KERNEL_PREBUILTS} is at {head.stdout.strip()[:12] or "no commit"}, the build used '
                      f'{named["commit"][:12]}. Sync the source the set was built from')
    if status.returncode or status.stdout:
        return None, f'{KERNEL_PREBUILTS} has local changes'
    return directory, ''


def check_kernel(v):
    if v.kernel_dir is None:
        return False, v.kernel_problem or 'no verified kernel prebuilts'
    problems = []
    image = (v.kernel_dir / 'Image').read_bytes()
    booted = boot_kernel((v.images / 'boot.img').read_bytes())
    if booted != image:
        problems.append(f'boot.img carries a kernel with SHA-256 {hashlib.sha256(booted).hexdigest()[:16]}, '
                        f'the prebuilts Image has {hashlib.sha256(image).hexdigest()[:16]}')
    # The build packs the prebuilts' device trees one after another, in name order.
    trees = b''.join(path.read_bytes() for path in sorted((v.kernel_dir / 'dtbs').glob('*.dtb')))
    if vendor_boot_parts((v.images / 'vendor_boot.img').read_bytes())[1] != trees:
        problems.append('vendor_boot.img does not carry exactly the prebuilts device trees')
    if avb_payload((v.images / 'dtbo.img').read_bytes()) != (v.kernel_dir / 'dtbo.img').read_bytes():
        problems.append('dtbo.img is not the prebuilts dtbo.img')
    return not problems, '; '.join(problems)


VENDOR_LOAD = 'VENDOR_DLKM/lib/modules/modules.load'


def load_entries(data: bytes) -> list:
    """The modules a load list names, read the way libmodprobe reads it."""
    return [line.split(' ')[0] for line in text_of(data).split('\n') if line and not line.startswith('#')]


def load_stream_problems(full: list, streams: dict) -> list:
    """What is wrong with the vendor_dlkm load streams that init loads in
    parallel: together they must name every module of modules.load once, and
    each must keep the modules.load order."""
    position = {module_key(n): i for i, n in enumerate(full)}
    problems, owner = [], {}
    for name, entries in sorted(streams.items()):
        keys = [module_key(e) for e in entries]
        unknown = [e for e, k in zip(entries, keys) if k not in position]
        if unknown:
            problems.append(f'stream {name} lists modules outside modules.load: {", ".join(unknown[:5])}')
        known = [position[k] for k in keys if k in position]
        if known != sorted(known):
            problems.append(f'stream {name} is not in modules.load order')
        for key in keys:
            if key in owner:
                problems.append(f'{key} is in streams {owner[key]} and {name}')
            owner.setdefault(key, name)
    missing = [n for n in full if module_key(n) not in owner]
    if missing:
        problems.append(f'{len(missing)} modules.load modules are in no stream: {", ".join(missing[:5])}')
    return problems


def check_modules(v):
    if v.kernel_dir is None or v.packaging is None:
        return False, v.kernel_problem or 'no verified kernel prebuilts'
    lists = board_lists((v.kernel_dir / 'BoardConfigKernel.mk').read_text())
    places = {'VENDOR_DLKM/lib/modules/': 'BOARD_VENDOR_KERNEL_MODULES',
              'SYSTEM_DLKM/lib/modules/': 'BOARD_SYSTEM_KERNEL_MODULES',
              'VENDOR_BOOT/RAMDISK/lib/modules/': 'BOARD_VENDOR_RAMDISK_KERNEL_MODULES'}
    from .kernel import denied_modules
    denied = {module_key(n) for n in denied_modules(v.packaging)}
    problems = []
    for prefix, key in places.items():
        present = {PurePosixPath(n).name for n in v.tf.names if n.startswith(prefix) and n.endswith('.ko')}
        if present != set(lists.get(key, [])):
            problems.append(f'{prefix} has {len(present)} modules, the kernel prebuilts list {len(lists.get(key, []))}')
        if {module_key(n) for n in present} & denied:
            problems.append(f'denied module in {prefix}')
        for name in v.tf.names:
            if name.startswith(prefix) and name.endswith('.ko') and not v.tf.read(name).endswith(MODULE_SIGNATURE):
                problems.append('unsigned module ' + name)
                break
        # The build copies modules unstripped (BOARD_DO_NOT_STRIP_*), so each
        # one is byte for byte the kernel prebuilts' file.
        differ = []
        for name in v.tf.names:
            if name.startswith(prefix) and name.endswith('.ko'):
                source = v.kernel_dir / 'modules' / PurePosixPath(name).name
                if source.is_symlink() or not source.is_file() or v.tf.read(name) != source.read_bytes():
                    differ.append(PurePosixPath(name).name)
        if differ:
            problems.append(f'{len(differ)} modules in {prefix} differ from the kernel prebuilts: '
                            + ', '.join(differ[:5]))
        for load in ('modules.load', 'modules.load.recovery'):
            path = prefix + load
            if path in v.tf.infos and {module_key(l) for l in lines_of(v.tf.read(path)) if l.strip()} & denied:
                problems.append('denied module in ' + path)
    counts = {'VENDOR_BOOT/RAMDISK/lib/modules/modules.load': 'BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD',
              'VENDOR_BOOT/RAMDISK/lib/modules/modules.load.recovery': 'BOARD_VENDOR_RAMDISK_RECOVERY_KERNEL_MODULES_LOAD'}
    for path, key in counts.items():
        listed = [l for l in lines_of(v.tf.read(path)) if l.strip()]
        if len(listed) != len(lists.get(key, [])):
            problems.append(f'{path} lists {len(listed)} modules, the kernel prebuilts {len(lists.get(key, []))}')
    blocklists = [v.tf.read(p) for p in ('VENDOR_BOOT/RAMDISK/lib/modules/modules.blocklist',
                                          'VENDOR_DLKM/lib/modules/modules.blocklist')]
    if blocklists[0] != blocklists[1]:
        problems.append('the vendor ramdisk and vendor_dlkm blocklists differ')
    if VENDOR_LOAD not in v.tf.infos:
        problems.append(VENDOR_LOAD + ' is missing')
    else:
        full = load_entries(v.tf.read(VENDOR_LOAD))
        board = lists.get('BOARD_VENDOR_KERNEL_MODULES_LOAD', [])
        if full != board:
            problems.append(f'{VENDOR_LOAD} lists {len(full)} modules, the kernel prebuilts {len(board)}'
                            if len(full) != len(board) else f'{VENDOR_LOAD} order differs from the kernel prebuilts')
        streams = {n.removeprefix(VENDOR_LOAD + '.'): load_entries(v.tf.read(n))
                   for n in v.tf.names if n.startswith(VENDOR_LOAD + '.')}
        problems += load_stream_problems(full, streams) if streams else []
        rc = '\n'.join(text_of(v.tf.read(n)) for n in v.tf.names
                       if n.startswith('VENDOR/etc/init/') and n.endswith('.rc'))
        unloaded = [s for s in sorted(streams) if not re.search(
            r'^service \S+ /vendor/bin/modprobe .*--all=/vendor/lib/modules/modules\.load\.' + re.escape(s) + '$',
            rc, re.M)]
        if unloaded:
            problems.append('no vendor init service loads stream ' + ', '.join(unloaded))
    return not problems, '; '.join(problems)


def vintf_declarations(data: bytes):
    """What a VINTF manifest fragment declares, independent of how it is written."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    hals, other = set(), []
    for element in root:
        if not isinstance(element.tag, str):
            continue
        if element.tag != 'hal':
            other.append(ET.tostring(element, encoding='unicode').split())
            continue
        fmt = element.get('format', 'hidl')
        name = (element.findtext('name') or '').strip()
        transport = (element.findtext('transport') or '').strip()
        versions = sorted(v.text.strip() for v in element.findall('version') if v.text)
        if fmt == 'aidl' and not versions:
            versions = ['1']
        instances = set()
        for interface in element.findall('interface'):
            iface = (interface.findtext('name') or '').strip()
            instances |= {(iface, (i.text or '').strip()) for i in interface.findall('instance')}
        for fq in element.findall('fqname'):
            text = (fq.text or '').strip()
            if fmt == 'hidl' and '::' in text:
                version, _, text = text.partition('::')
                versions = sorted(set(versions) | {version.lstrip('@')})
            iface, _, instance = text.partition('/')
            instances.add((iface, instance))
        hals.add((fmt, name, transport, tuple(versions), tuple(sorted(instances)),
                  element.get('optional', 'false'), element.get('override', 'false')))
    return hals, sorted(map(tuple, other))


VENDOR_FILES = ROOT / 'config/fp6-minimal/vendor-files.json'


def check_vendor(v):
    """Every selected stock file arrives in the images with its generated bytes.

    The generation's own recipe names the files, so a missing file fails
    instead of being skipped. The recipe must select exactly the files and
    hashes pinned in the tools, so a blob edited together with the recipe in
    the workspace fails. Each generated file has the recipe's bytes, or
    the reviewed result where the generator rewrote it. Files the generator
    derives from a selected file under a new path (provenance "from") are
    checked the same way."""
    if v.vendor_dir is None:
        return False, 'no vendor generation recorded'
    from . import carrier_data, vendor_product
    provenance = json.loads((v.vendor_dir / 'provenance.json').read_bytes())
    recipe = json.loads((v.vendor_dir / 'recipe.json').read_bytes())
    expected = {item['path']: item['sha256'] for item in recipe.get('files', [])}
    if not expected:
        return False, 'the vendor generation selects no files'
    pinned = {item['path']: item['sha256'] for item in json.loads(VENDOR_FILES.read_bytes())['files']}
    if expected != pinned:
        changed = sorted(set(expected) ^ set(pinned) | {p for p in expected.keys() & pinned if expected[p] != pinned[p]})
        return False, "the vendor generation's recipe differs from the pinned vendor-files.json: " + ', '.join(changed[:10])
    expected.update({item['path']: item['sha256'] for item in provenance.get('derived_files', [])
                     if item['path'] in expected or item.get('from') in expected})
    skipped = set(provenance.get('source_interface_replacements', [])) | set(
        provenance.get('uninstalled_optional_libraries', [])) | {carrier_data.APK_PATH}
    files = v.vendor_dir / 'files'
    problems, checked = [], 0
    for rel in sorted(expected):
        path = files / rel
        if path.is_symlink() or not path.is_file() or bw.sha_file(path) != expected[rel]:
            problems.append('vendor generation lacks the selected ' + rel)
            continue
        partition, _, rest = rel.partition('/')
        if rel in skipped or partition not in PARTITION_DIRS:
            continue
        candidates = [PARTITION_DIRS[partition] + '/' + rest]
        if partition == 'vendor' and PurePosixPath(rel).name.removesuffix('.so') in vendor_product.ODM_LIBRARIES:
            candidates = ['ODM/lib64/' + PurePosixPath(rel).name]
        if vendor_product.STOCK_APPS.get(rel, (None, False))[1] and rest.startswith('app/'):
            # The renderer installs privileged stock apps under priv-app.
            candidates = [PARTITION_DIRS[partition] + '/priv-' + rest]
        if rel.startswith(vendor_product.DSP_DIRECTORIES):
            # DSP libraries install with prebuilt_rfsa into lib/rfsa/adsp.
            candidates = ['VENDOR/lib/rfsa/adsp/' + PurePosixPath(rel).name]
        member = next((c for c in candidates if c in v.tf.infos), None)
        if member is None:
            problems.append('missing ' + candidates[0])
            continue
        checked += 1
        data = v.tf.read(member)
        if hashlib.sha256(data).hexdigest() != bw.sha_file(path):
            # Soong assembles VINTF fragments: it rewrites the comment, raises the
            # manifest version and writes AIDL instances as fqname. Compare what
            # they declare instead of their bytes.
            if '/etc/vintf/' in rel and vintf_declarations(data) == vintf_declarations(path.read_bytes()):
                continue
            problems.append('differs ' + member)
    return not problems, (f'{checked} files: ' if problems else f'{checked} files') + '; '.join(problems[:20])


def check_vendor_patch_level(v):
    """The vendor partition reports the patch level read from the stock vendor image."""
    if v.vendor_dir is None:
        return False, 'no vendor generation recorded'
    from .vendor_product import VENDOR_PATCH_PROPERTY
    want = json.loads((v.vendor_dir / 'provenance.json').read_bytes()).get('vendor_security_patch')
    if not isinstance(want, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', want):
        return False, 'the vendor generation records no stock vendor patch level'

    def values(data):
        return [value for key, value in property_lines(data) if key == VENDOR_PATCH_PROPERTY]
    problems = []
    found = values(v.tf.read('VENDOR/build.prop'))
    if found != [want]:
        problems.append(f'VENDOR/build.prop sets {found} (want exactly one {want!r})')
    with v.tools.scratch() as temporary:
        _, content = image_file(v, 'vendor', 'vendor/build.prop', Path(temporary))
    found = values(content)
    if found != [want]:
        problems.append(f'vendor.img sets {found} (want exactly one {want!r})')
    return not problems, '; '.join(problems) if problems else f'{VENDOR_PATCH_PROPERTY}={want}'


def check_firmware_release_table(v):
    """The vendor image's firmware release table is the one the inventory gives now."""
    from . import firmware_release
    inventory = json.loads((ROOT / 'config/fp6-firmware-inventory.json').read_bytes())
    want = firmware_release.table(inventory, v.config['firmware_release'])
    path = firmware_release.TABLE_PATH
    problems = []
    if v.tf.read('VENDOR/' + path.removeprefix('vendor/')) != want:
        problems.append('VENDOR/' + path.removeprefix('vendor/') + ' differs from the inventory')
    with v.tools.scratch() as temporary:
        _, content = image_file(v, 'vendor', path, Path(temporary))
    if content != want:
        problems.append('vendor.img differs from the inventory')
    releases = sorted({line.split()[2] for line in lines_of(want)})
    return not problems, '; '.join(problems) if problems else 'releases ' + ', '.join(releases)


def check_permissive(v):
    allowed = set(v.config['variants'][v.variant]['permissive_domains'])
    found = set()
    for name in v.tf.names:
        if name.endswith('.cil') and '/etc/selinux/' in name:
            found |= set(re.findall(r'^\(typepermissive (\S+)\)$', text_of(v.tf.read(name)), re.M))
    return found == allowed, f'permissive domains: {sorted(found)} (allowed {sorted(allowed)})'


def check_bootconfig(v):
    problems = []
    bootconfig = text_of(v.tf.read('VENDOR_BOOT/vendor_bootconfig'))
    for entry in v.config['bootconfig']['required']:
        if entry not in bootconfig.split():
            problems.append('vendor bootconfig lacks ' + entry)
    for key in v.config['bootconfig']['forbidden_keys']:
        if key in bootconfig:
            problems.append('vendor bootconfig sets ' + key)
        for name in ('boot', 'init_boot', 'vendor_boot'):
            if key.encode() in (v.images / f'{name}.img').read_bytes():
                problems.append(f'{name}.img sets {key}')
    return not problems, '; '.join(problems)


def check_kernel_arguments(v):
    forbidden = set(v.config['kernel_arguments']['forbidden'])
    found = {}

    def scan(source, text):
        for word in text.split():
            if word.split('=', 1)[0] in forbidden:
                found.setdefault(word, []).append(source)
    for name in ('boot', 'init_boot', 'recovery'):
        scan(f'{name}.img', boot_header((v.images / f'{name}.img').read_bytes())['cmdline'])
    cmdline, dtbs = vendor_boot_parts((v.images / 'vendor_boot.img').read_bytes())
    scan('vendor_boot.img', cmdline)
    overlays = avb_payload((v.images / 'dtbo.img').read_bytes())
    for kind, trees in (('device tree', device_trees(dtbs)), ('overlay', device_trees(overlays))):
        for index, tree in enumerate(trees):
            for node in tree.values():
                scan(f'{kind} {index}', text_of(node['props'].get('bootargs', b'')).replace('\0', ' '))
    # The DiamaneOS bootconfig carries Android keys only. A kernel. key there is a kernel argument too.
    for line in lines_of(v.tf.read('VENDOR_BOOT/vendor_bootconfig')):
        if line.lstrip().startswith('kernel'):
            found.setdefault(line.strip(), []).append('vendor bootconfig')
    details = [f'{word} in {", ".join(sources[:3])}' + (f' and {len(sources) - 3} more' if len(sources) > 3 else '')
               for word, sources in sorted(found.items())]
    return not found, '; '.join(details)


def check_adb_keys(v):
    problems = []
    for name in v.tf.names:
        if name.endswith('/adb_keys') or name == 'adb_keys':
            if not v.tf.is_symlink(name) or v.tf.link_target(name) != '/product/etc/security/adb_keys':
                problems.append('adb key installed: ' + name)
    if v.tf.exists('PRODUCT/etc/security/adb_keys'):
        problems.append('adb key installed: PRODUCT/etc/security/adb_keys')
    return not problems, '; '.join(problems)


def check_wipe(v):
    images = v.config['wipe']['images']
    problems = []
    for name, image in images.items():
        if image['kind'] == 'zeros':
            data = (v.images / image['image']).read_bytes()
            if len(data) != image['bytes'] or data.count(0) != len(data):
                problems.append(f'{name} image is not the declared zeros')
    if bw.sha_file(v.images / images['frp']['image']) != images['frp']['sha256']:
        problems.append('FRP image differs from the stock factory image')
    raw = sparse_to_raw((v.images / images['metadata']['image']).read_bytes(), limit=4096)
    if len(raw) < 1024 + 0x7c + 32 or struct.unpack_from('<I', raw, 1024)[0] != F2FS_MAGIC:
        problems.append('metadata image is not an f2fs filesystem')
    else:
        label = raw[1024 + 0x7c:1024 + 0x7c + 1024].decode('utf-16le', 'ignore').split('\0')[0]
        if label != images['metadata']['label']:
            problems.append(f'metadata label is {label!r}')
    return not problems, '; '.join(problems)


def check_firmware(v):
    carried = v.record.get('firmware')
    if not carried:
        return False, 'the image set carries no firmware'
    problems = firmware.check_set(v.images, carried, v.firmware_inventory, v.config['firmware'],
                                  v.record['stock_build'])
    return not problems, '; '.join(problems)


def check_firmware_ota(v):
    """Every A/B update carries the A/B firmware of the release, byte for byte,
    and writes no other firmware partition: a locked phone gets firmware only
    this way, and a partial set would mix two releases' firmware."""
    expected = firmware.plan(v.firmware_inventory, v.config['firmware'])
    image_of = firmware.update_partitions(expected, v.config['firmware'])
    listed = set(v.tf.read('META/ab_partitions.txt').decode().split())
    problems = []
    missing = sorted(set(image_of) - listed)
    if missing:
        problems.append('the update partition list lacks firmware: ' + ', '.join(missing))
    others = {s['partition'].removesuffix('_a').removesuffix('_b') for s in expected['steps']} - set(image_of)
    if listed & others:
        problems.append('the update partition list names firmware no update may write: '
                        + ', '.join(sorted(listed & others)))
    for partition in image_of:
        # The release tools take IMAGES/ first, then RADIO/.
        member = next((m for m in (f'IMAGES/{partition}.img', f'RADIO/{partition}.img') if m in v.tf.infos), None)
        if member is None:
            problems.append(f'target-files lacks the {partition} image')
            continue
        digest = hashlib.sha256()
        with v.tf.archive.open(v.tf.infos[member]) as stream:
            for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b''):
                digest.update(chunk)
        image = image_of[partition]
        if digest.hexdigest() != expected['images'][image]['sha256']:
            problems.append(f'{member} is not the stock {image}')
    return not problems, '; '.join(problems)


GENERIC = [
    ('record', 'The image set matches its build record and is marked as a test build that must never be locked.', check_record),
    ('test-keys', 'Public builds are signed with the public test keys and say so in the fingerprint.', check_test_keys),
    ('avb-chain', 'The AVB chain verifies with the test key, has the published FP6 layout and SHA-256 hashtrees. '
     'An official build has a rollback index above 0 on vbmeta and every chain.', check_avb),
    ('boot-header', 'Boot-family headers carry zero OS fields. The versions live in AVB properties.', check_boot_headers),
    ('super', 'super.img holds exactly the exported logical images and an empty slot b. super_empty.img, which '
     'the install writes, lists the same partitions.', check_super),
    ('validators', 'validate_target_files and check_target_files_vintf pass on the archive.', check_validators),
    ('kernel-binding', 'boot, vendor_boot and dtbo carry the kernel prebuilts the build record names.', check_kernel),
    ('modules', 'Module placement and load lists match the kernel prebuilts, the parallel vendor_dlkm load streams '
     'name each listed module once in list order, and no module is denied or unsigned.', check_modules),
    ('vendor-binding', 'Every selected stock file arrives with its generated bytes.', check_vendor),
    ('vendor-patch-level', 'The vendor partition reports the patch level of the stock vendor image its files come '
     'from.', check_vendor_patch_level),
    ('firmware-release-table', 'The vendor image carries the firmware release table the firmware inventory gives, so '
     'Settings names the installed stock firmware from current hashes.', check_firmware_release_table),
    ('selinux-enforcing', 'No permissive domain beyond the variant\'s allowance.', check_permissive),
    ('bootconfig', 'Required bootconfig present. Nothing overrides SELinux or turns the EUD debugger on.', check_bootconfig),
    ('kernel-arguments', 'No device tree, overlay, boot image header or bootconfig entry sets a kernel argument '
     'that turns a protection off.', check_kernel_arguments),
    ('adb-keys', 'No pre-trusted adb key.', check_adb_keys),
    ('wipe-images', 'The wipe images are the declared deterministic images.', check_wipe),
    ('firmware', 'The set carries every firmware image of the stock release its vendor files come from, byte for '
     'byte as the firmware inventory pins it, with the stock flash order and the declared modem file system reset '
     'images. No image lowers a Qualcomm anti-rollback version.', check_firmware),
    ('firmware-ota', 'An A/B update built from this target-files carries the A/B firmware of that release, '
     'byte for byte, and writes no other firmware partition.', check_firmware_ota),
]


# The checks every report has, whatever the variant: device rules depend on it.
REQUIRED_CHECKS = ('sums',) + tuple(check_id for check_id, _, _ in GENERIC)


# Checks no build may fail and still be flashed: the set's own hashes, the
# firmware it carries and the vendor files it was built with.
NEVER_WAIVED = ('sums', 'firmware', 'firmware-ota', 'vendor-binding')


def failed_checks(report) -> list:
    """The ids of the checks a verify report lists as not passed."""
    return [str(c.get('id')) for c in report.get('checks') or [] if c.get('status') != 'PASS']


def report_problem(report, build_id: str, sums_sha256: str, allow_failed: bool = False) -> str:
    """Why a verify report does not show that this image set passed, or ''.

    Every listed check passed, the generic checks are all there once, and the
    totals and status agree: an empty or partial report passes nothing. With
    ``allow_failed`` (an image set that is not official) failed checks are no
    problem as long as the report counts them itself."""
    checks = report.get('checks') if isinstance(report, dict) else None
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return 'the verification report lists no checks'
    failed = failed_checks(report)
    if failed and (not allow_failed or any(check in NEVER_WAIVED for check in failed)):
        return 'verification failed (' + ', '.join(failed[:20]) + ')'
    if report.get('schema_version') != 1:
        return 'the verification report has an unknown format'
    if report.get('build_id') != build_id or report.get('sums_sha256') != sums_sha256:
        return 'the verification report belongs to another image set'
    ids = [c.get('id') for c in checks]
    missing = [i for i in REQUIRED_CHECKS if i not in ids]
    if missing:
        return 'the verification report lacks checks: ' + ', '.join(missing)
    if len(ids) != len(set(ids)):
        return 'the verification report lists a check twice'
    if failed:
        if (report.get('status'), report.get('checked'), report.get('failed')) != ('FAIL', len(checks), len(failed)):
            return 'the verification report does not count its failed checks'
    elif report.get('status') != 'PASS' or report.get('checked') != len(checks) or report.get('failed') != 0:
        return 'the verification report does not say that all its checks passed'
    return ''


def verify(images: Path, config: dict, checks: dict, tools: Tools, kernel_dir: Path | None,
           vendor_dir: Path | None, packaging: dict | None, src: Path, kernel_problem: str = '',
           firmware_inventory: dict | None = None) -> dict:
    from .image_package import check_sums
    from .image_profiles import generic_checks
    record = json.loads((images / 'build.json').read_bytes())
    # build.json's signing value selects the generic checks: an unknown value stops here.
    profile, generic = generic_checks(record)
    results_head = [{'id': 'sums', 'status': 'PASS' if check_sums(images) else 'FAIL',
                     'why': 'Every file matches SHA256SUMS.', 'detail': ''}]
    target_files = TargetFiles(images / record['target_files']['file'])
    try:
        v = Verification(images, record, target_files, tools, config, kernel_dir, vendor_dir, packaging, src,
                         firmware_inventory)
        v.kernel_problem = kernel_problem
        for check_id, why, func in generic:
            v.add(check_id, why, lambda func=func: func(v))
        official = v.record.get('official') is True
        for rule in validate_rules(checks):
            if not rule_applies(rule, v.variant, official):
                continue
            v.add(rule['id'], rule['why'], lambda rule=rule: RULES[rule['type']](rule, v))
    finally:
        target_files.close()
    results = results_head + v.results
    failed = [r for r in results if r['status'] != 'PASS']
    # The report belongs to exactly this image set: flash-steps compares the
    # SHA256SUMS digest before it trusts the result.
    return {'schema_version': 1, 'build_id': record['build_id'], 'variant': record['variant'],
            'sums_sha256': bw.sha_file(images / 'SHA256SUMS'), 'build_identity': record.get('build_identity'),
            'rule_set_sha256': bw.digest(checks), 'profile': profile,
            'status': 'FAIL' if failed else 'PASS', 'checked': len(results), 'failed': len(failed),
            'checks': results}


def verify_set(images: Path, ws, config: dict, checks: dict, tools: Tools, inventory: dict) -> dict:
    """verify() with the kernel prebuilts and vendor generation the set's record
    names in the workspace (not whatever the workspace holds now)."""
    record = json.loads((images / 'build.json').read_bytes())
    vendor = (record.get('generated_inputs') or {}).get('vendor') or {}
    packaging = json.loads((ROOT / 'config/fp6-kernel-packaging.json').read_bytes())
    kernel_dir, kernel_problem = kernel_prebuilts(ws.src, record)
    vendor_dir = ws.vendor / 'generations' / vendor['generation'] if vendor.get('generation') else None
    return verify(images, config, checks, tools, kernel_dir, vendor_dir, packaging, ws.src, kernel_problem, inventory)


def plan(ctx):
    from .build_steps import StepPlan, otatools_bin
    ws = ctx.workspace
    package = ws.passed('package')
    checks_raw = (ROOT / 'config/fp6-image-checks.json').read_bytes()
    inventory = ctx.firmware_inventory or firmware.load_inventory()
    inputs = None if package is None else {
        'package': package['outputs'], 'checks_sha256': hashlib.sha256(checks_raw).hexdigest(),
        'code': bw.sha_file(Path(__file__)),
        'profiles_code': bw.sha_file(Path(__file__).with_name('image_profiles.py')),
        'firmware': {'inventory': bw.digest(inventory), 'policy': bw.digest(ctx.config['firmware']),
                     'code': bw.sha_file(Path(firmware.__file__))}}
    state = {}

    def run():
        images = ws.root / package['outputs']['directory']
        record = json.loads((images / 'build.json').read_bytes())
        tools = Tools(otatools_bin(ctx), ws.src, ws.work / 'tmp')
        report = verify_set(images, ws, ctx.config, json.loads(checks_raw), tools, inventory)
        path = ws.images / (package['outputs']['build_id'] + '.verify.json')
        bw.write_atomic(path, bw.encoded(report))
        state.update(report=report, path=path)
        for result in report['checks']:
            if result['status'] != 'PASS':
                ctx.echo(f'    FAIL {result["id"]}: {result["detail"]}')
        if report['status'] != 'PASS':
            message = f'{report["failed"]} of {report["checked"]} checks failed. Report: {path}'
            # The image set says whether it is official, whatever this command was given.
            if (record.get('official') is True or ctx.official
                    or any(check in NEVER_WAIVED for check in failed_checks(report))):
                raise CheckFailed(message)
            ctx.echo(f'    warning: {message}. An official build stops here. flash-steps names the failed checks')
            return
        ctx.echo(f'    all {report["checked"]} checks passed')

    def outputs():
        return {'status': state['report']['status'], 'checked': state['report']['checked'],
                'report': str(state['path'].relative_to(ws.root))}

    def valid(previous):
        # The report itself, not only its file, must still show this set passed.
        try:
            report = json.loads((ws.root / previous['outputs']['report']).read_bytes())
        except (OSError, ValueError):
            return False
        return not report_problem(report, package['outputs']['build_id'], package['outputs']['sums_sha256'])

    return StepPlan('verify', inputs, [Action('Check the exported image set', func=run)], outputs, valid,
                    waiting_for=None if package else 'package')
