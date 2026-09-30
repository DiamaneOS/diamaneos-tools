"""Verify an exported FP6 test image set.

Generic checks bind the set to its record, the kernel run and the vendor
generation, and check the security properties every public build must have.
The device checks in config/fp6-image-checks.json run on the same archive.
File-level checks read the target-files archive the images were made from;
image-level checks read the exported images.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from . import build_workspace as bw
from .build_workspace import Action, CheckFailed

ROOT = bw.ROOT
MODULE_SIGNATURE = b'~Module signature appended~\n'
SPARSE_MAGIC = 0xED26FF3A
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


class Tools:
    """Host tools from the synced source's build output."""

    def __init__(self, host_bin: Path, src: Path, work: Path | None = None):
        self.host_bin, self.src = host_bin, src
        # Temporary files (unpacked super, expanded images) can take several
        # GB; they go to the workspace, never to /tmp.
        self.work = work or Path(tempfile.gettempdir())

    def scratch(self):
        self.work.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=self.work, prefix='.verify-')

    def path(self, name: str) -> Path:
        if name == 'llvm-readelf':
            found = sorted((self.src / 'prebuilts/clang/host/linux-x86').glob('clang-r*/bin/llvm-readelf'))
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
        jdks = sorted((self.src / 'prebuilts/jdk').glob('jdk*/linux-x86'))
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
                raise FileNotFoundError('no kernel run recorded')
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
    return {'header_version': version, 'os_version_field': os_field, 'kernel_size': kernel_size,
            'page_size': 4096 if version >= 3 else struct.unpack_from('<I', data, 36)[0]}


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


def sparse_to_raw(data: bytes, limit: int | None = None) -> bytes:
    """Expand an Android sparse image (enough of it for header checks)."""
    magic, _, _, header_size, chunk_header, block, blocks, chunks, _ = struct.unpack_from('<IHHHHIIII', data, 0)
    if magic != SPARSE_MAGIC:
        return data if limit is None else data[:limit]
    out = bytearray()
    offset = header_size
    for _ in range(chunks):
        kind, _, count, total = struct.unpack_from('<HHII', data, offset)
        body = data[offset + chunk_header:offset + total]
        offset += total
        size = count * block
        if kind == 0xCAC1:
            out += body
        elif kind == 0xCAC2:
            out += body[:4] * (size // 4)
        elif kind == 0xCAC3:
            out += bytes(size)
        if limit is not None and len(out) >= limit:
            return bytes(out[:limit])
    return bytes(out)


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


def rule_overlay(rule, v):
    with v.tools.scratch() as temporary:
        apk = v.tf.extract(rule['apk'], Path(temporary))
        dump = v.tools.run('aapt2', ['dump', 'resources', apk])
        problems = [f'{name} is {aapt2_value(dump, name)!r} (want {want!r})'
                    for name, want in sorted(rule.get('values', {}).items()) if aapt2_value(dump, name) != want]
        if rule.get('target'):
            manifest = v.tools.run('aapt2', ['dump', 'xmltree', '--file', 'AndroidManifest.xml', apk])
            if f'"{rule["target"]}"' not in manifest:
                problems.append('overlay target is not ' + rule['target'])
    return not problems, '; '.join(problems)


def signer_digests(v, apk: Path) -> set:
    output = v.tools.run('apksigner', ['verify', '--print-certs', apk])
    return set(re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', output))


def rule_apk(rule, v):
    problems = []
    with v.tools.scratch() as temporary:
        directory = Path(temporary)
        apk = v.tf.extract(rule['path'], directory)
        dump = v.tools.run('aapt2', ['dump', 'permissions', apk])
        requested = sorted(re.findall(r"^uses-permission: name='([^']+)'", dump, re.M))
        if requested != sorted(rule['permissions']):
            problems.append('requests ' + ', '.join(requested))
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
    data = v.tf.read(rule['file'])
    needle = rule['text'].encode()
    if needle in data:
        return True, ''
    with zipfile.ZipFile(__import__('io').BytesIO(data)) as inner:
        for name in inner.namelist():
            if name.startswith('classes') and name.endswith('.dex') and needle in inner.read(name):
                return True, ''
    return False, 'does not contain ' + rule['text']


def image_file(v, image: str, path: str, directory: Path) -> tuple[str, bytes]:
    """stat and content of a file inside an ext4 image, through debugfs."""
    source = v.images / f'{image}.img'
    with source.open('rb') as stream:
        sparse = struct.unpack('<I', stream.read(4))[0] == SPARSE_MAGIC
    if sparse:
        raw = directory / f'{image}.raw'
        v.tools.run('simg2img', [source, raw])
        source = raw
    inside = '/' + path.split('/', 1)[1]
    info = v.tools.run('debugfs_static', ['-R', f'stat {inside}', source])
    dump = directory / 'dump'
    v.tools.run('debugfs_static', ['-R', f'dump {inside} {dump}', source])
    return info, dump.read_bytes()


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


RULES = {'files_present': rule_files_present, 'files_absent': rule_files_absent, 'symlink': rule_symlink,
         'text': rule_text, 'properties': rule_properties, 'property_prefix': rule_property_prefix,
         'sepolicy_exclusive': rule_sepolicy_exclusive, 'sepolicy_allows': rule_sepolicy_allows,
         'overlay': rule_overlay, 'apk': rule_apk, 'elf_exports': rule_elf_exports,
         'binary_count': rule_binary_count, 'devicetree': rule_devicetree,
         'component_override': rule_component_override, 'zip_contains': rule_zip_contains,
         'file_metadata': rule_file_metadata}


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
        ids.add(rule['id'])
    return document['rules']


# ------------------------------------------------------ generic checks

class Verification:
    def __init__(self, images: Path, record: dict, target_files: TargetFiles, tools: Tools, config: dict,
                 kernel_dir: Path | None, vendor_dir: Path | None, packaging: dict | None, src: Path):
        self.images, self.record, self.tf, self.tools, self.config = images, record, target_files, tools, config
        self.kernel_dir, self.vendor_dir, self.packaging, self.src = kernel_dir, vendor_dir, packaging, src
        self.sources = Sources(target_files, kernel_dir, images)
        self.variant = record['variant']
        self.results = []

    def add(self, check_id: str, why: str, func):
        try:
            ok, detail = func()
        except ToolMissing as missing:
            ok, detail = False, f'tool missing: {missing} (build it with "diamaneos build android")'
        except (OSError, ValueError, KeyError, struct.error, ET.ParseError, zipfile.BadZipFile,
                subprocess.SubprocessError, CheckFailed) as error:
            ok, detail = False, f'{type(error).__name__}: {error}'
        self.results.append({'id': check_id, 'status': 'PASS' if ok else 'FAIL', 'why': why, 'detail': detail})


def check_record(v):
    from .image_package import read_sums
    sums = read_sums(v.images)
    problems = []
    if v.record.get('release') is not False or v.record.get('signing') != 'public-test-keys' or not v.record.get('never_lock'):
        problems.append('build.json does not mark a test build that must never be locked')
    if sums.get(v.record['target_files']['file']) != v.record['target_files']['sha256']:
        problems.append('target-files copy differs from the build record')
    for name, digest in v.record['images'].items():
        if sums.get(name + '.img') != digest:
            problems.append(name + '.img differs from the build record')
    return not problems, '; '.join(problems)


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


def check_avb(v):
    key = v.src / v.config['avb']['test_key']
    v.tools.run('avbtool', ['verify_image', '--image', v.images / 'vbmeta.img', '--key', key,
                            '--follow_chain_partitions'], cwd=v.images)
    chains = avb_chains(v.tools.run('avbtool', ['info_image', '--image', v.images / 'vbmeta.img']))
    want = {name: (location, 0) for name, location in v.config['avb']['chains'].items()}
    problems = [] if chains == want else [f'chains are {chains} (want {want})']
    system = v.tools.run('avbtool', ['info_image', '--image', v.images / 'vbmeta_system.img'])
    if not re.search(r'Partition Name:\s+pvmfw\b', system):
        problems.append('vbmeta_system lacks the pvmfw descriptor')
    return not problems, '; '.join(problems)


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
    return not problems, '; '.join(problems)


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


def check_kernel(v):
    if v.kernel_dir is None:
        return False, 'no kernel run recorded'
    problems = []
    image = (v.kernel_dir / 'Image').read_bytes()
    if boot_kernel((v.images / 'boot.img').read_bytes()) != image:
        problems.append('boot.img does not carry the recorded kernel Image')
    if (v.kernel_dir / 'dtbs/fp6.dtb').read_bytes() not in (v.images / 'vendor_boot.img').read_bytes():
        problems.append('vendor_boot.img does not carry the recorded fp6.dtb')
    if avb_payload((v.images / 'dtbo.img').read_bytes()) != (v.kernel_dir / 'dtbo.img').read_bytes():
        problems.append('dtbo.img is not the recorded kernel run\'s dtbo')
    return not problems, '; '.join(problems)


def check_modules(v):
    if v.kernel_dir is None or v.packaging is None:
        return False, 'no kernel run recorded'
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
            problems.append(f'{prefix} has {len(present)} modules, the kernel run lists {len(lists.get(key, []))}')
        if {module_key(n) for n in present} & denied:
            problems.append(f'denied module in {prefix}')
        for name in v.tf.names:
            if name.startswith(prefix) and name.endswith('.ko') and not v.tf.read(name).endswith(MODULE_SIGNATURE):
                problems.append('unsigned module ' + name)
                break
        for load in ('modules.load', 'modules.load.recovery'):
            path = prefix + load
            if path in v.tf.infos and {module_key(l) for l in lines_of(v.tf.read(path)) if l.strip()} & denied:
                problems.append('denied module in ' + path)
    counts = {'VENDOR_BOOT/RAMDISK/lib/modules/modules.load': 'BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD',
              'VENDOR_BOOT/RAMDISK/lib/modules/modules.load.recovery': 'BOARD_VENDOR_RAMDISK_RECOVERY_KERNEL_MODULES_LOAD'}
    for path, key in counts.items():
        listed = [l for l in lines_of(v.tf.read(path)) if l.strip()]
        if len(listed) != len(lists.get(key, [])):
            problems.append(f'{path} lists {len(listed)} modules, the kernel run {len(lists.get(key, []))}')
    blocklists = [v.tf.read(p) for p in ('VENDOR_BOOT/RAMDISK/lib/modules/modules.blocklist',
                                          'VENDOR_DLKM/lib/modules/modules.blocklist')]
    if blocklists[0] != blocklists[1]:
        problems.append('the vendor ramdisk and vendor_dlkm blocklists differ')
    return not problems, '; '.join(problems)


def check_vendor(v):
    """Every selected stock file arrives in the images with its generated bytes."""
    if v.vendor_dir is None:
        return False, 'no vendor generation recorded'
    from . import carrier_data, vendor_product
    provenance = json.loads((v.vendor_dir / 'provenance.json').read_bytes())
    skipped = set(provenance.get('source_interface_replacements', [])) | set(
        provenance.get('uninstalled_optional_libraries', [])) | {carrier_data.APK_PATH}
    files = v.vendor_dir / 'files'
    problems, checked = [], 0
    for path in sorted(p for p in files.rglob('*') if p.is_file()):
        rel = path.relative_to(files).as_posix()
        partition, _, rest = rel.partition('/')
        if rel in skipped or partition not in PARTITION_DIRS:
            continue
        candidates = [PARTITION_DIRS[partition] + '/' + rest]
        if partition == 'vendor' and PurePosixPath(rel).name.removesuffix('.so') in vendor_product.ODM_LIBRARIES:
            candidates = ['ODM/lib64/' + PurePosixPath(rel).name]
        member = next((c for c in candidates if c in v.tf.infos), None)
        if member is None:
            problems.append('missing ' + candidates[0])
            continue
        checked += 1
        if hashlib.sha256(v.tf.read(member)).hexdigest() != bw.sha_file(path):
            problems.append('differs ' + member)
    return not problems, (f'{checked} files; ' if problems else f'{checked} files') + '; '.join(problems[:20])


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


GENERIC = [
    ('record', 'The image set matches its build record and is marked as a test build that must never be locked.', check_record),
    ('test-keys', 'Public builds are signed with the public test keys and say so in the fingerprint.', check_test_keys),
    ('avb-chain', 'The AVB chain verifies with the test key and has the published FP6 layout.', check_avb),
    ('boot-header', 'Boot-family headers carry zero OS fields; the versions live in AVB properties.', check_boot_headers),
    ('super', 'super.img holds exactly the exported logical images; slot b is empty.', check_super),
    ('validators', 'validate_target_files and check_target_files_vintf pass on the archive.', check_validators),
    ('kernel-binding', 'boot, vendor_boot and dtbo carry the recorded kernel run.', check_kernel),
    ('modules', 'Module placement and load lists match the kernel run; no denied or unsigned module.', check_modules),
    ('vendor-binding', 'Every selected stock file arrives with its generated bytes.', check_vendor),
    ('selinux-enforcing', 'No permissive domain beyond the variant\'s allowance.', check_permissive),
    ('bootconfig', 'Required bootconfig present; nothing overrides SELinux.', check_bootconfig),
    ('adb-keys', 'No pre-trusted adb key.', check_adb_keys),
    ('wipe-images', 'The wipe images are the declared deterministic images.', check_wipe),
]


def verify(images: Path, config: dict, checks: dict, tools: Tools, kernel_dir: Path | None,
           vendor_dir: Path | None, packaging: dict | None, src: Path) -> dict:
    from .image_package import check_sums
    record = json.loads((images / 'build.json').read_bytes())
    results_head = [{'id': 'sums', 'status': 'PASS' if check_sums(images) else 'FAIL',
                     'why': 'Every file matches SHA256SUMS.', 'detail': ''}]
    target_files = TargetFiles(images / record['target_files']['file'])
    try:
        v = Verification(images, record, target_files, tools, config, kernel_dir, vendor_dir, packaging, src)
        for check_id, why, func in GENERIC:
            v.add(check_id, why, lambda func=func: func(v))
        for rule in validate_rules(checks):
            if rule.get('variants') and v.variant not in rule['variants']:
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
            'status': 'FAIL' if failed else 'PASS', 'checked': len(results), 'failed': len(failed),
            'checks': results}


def plan(ctx):
    from .build_steps import StepPlan
    ws = ctx.workspace
    package = ws.passed('package')
    checks_raw = (ROOT / 'config/fp6-image-checks.json').read_bytes()
    inputs = None if package is None else {
        'package': package['outputs'], 'checks_sha256': hashlib.sha256(checks_raw).hexdigest(),
        'code': bw.sha_file(Path(__file__))}
    state = {}

    def run():
        images = ws.root / package['outputs']['directory']
        # The kernel run and vendor generation this set was made from, as its
        # record names them (not whatever the workspace holds now).
        inputs = json.loads((images / 'build.json').read_bytes()).get('generated_inputs') or {}
        kernel, vendor = inputs.get('kernel') or {}, inputs.get('vendor') or {}
        packaging = json.loads((ROOT / 'config/fp6-kernel-packaging.json').read_bytes())
        kernel_dir = ws.kernel / kernel['run'] if kernel.get('run') else None
        vendor_dir = ws.vendor / 'generations' / vendor['generation'] if vendor.get('generation') else None
        report = verify(images, ctx.config, json.loads(checks_raw), Tools(ctx.host_bin, ws.src, ws.work / 'tmp'), kernel_dir,
                        vendor_dir, packaging, ws.src)
        path = ws.images / (package['outputs']['build_id'] + '.verify.json')
        bw.write_atomic(path, bw.encoded(report))
        state.update(report=report, path=path)
        for result in report['checks']:
            if result['status'] != 'PASS':
                ctx.echo(f'    FAIL {result["id"]}: {result["detail"]}')
        if report['status'] != 'PASS':
            raise CheckFailed(f'{report["failed"]} of {report["checked"]} checks failed; report: {path}')
        ctx.echo(f'    all {report["checked"]} checks passed')

    def outputs():
        return {'status': state['report']['status'], 'checked': state['report']['checked'],
                'report': str(state['path'].relative_to(ws.root))}

    def valid(previous):
        return (ws.root / previous['outputs']['report']).is_file()

    return StepPlan('verify', inputs, [Action('Check the exported image set', func=run)], outputs, valid,
                    waiting_for=None if package else 'package')
