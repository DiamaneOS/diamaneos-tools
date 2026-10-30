"""The FP6 device tree of the 6.18 kernel set: a pinned base with declared fix-ups.

The 5.0-based vendor drivers and the 6.18 core read some 6.1 device tree
bindings differently. Until the sources hold a 6.18 device tree, the set ships
the 6.1 base device tree and overlay image (pinned by SHA-256 in
config/fp6-kernel-dt.json) and edits the base through the fix-ups listed there.
Each edit states the value it expects to find, so a different base stops the
build, and the result must differ from the base in exactly the declared
properties and in nothing else.
"""
import hashlib
import struct

MAGIC = 0xd00dfeed
BEGIN_NODE, END_NODE, PROP, NOP, END = 1, 2, 3, 4, 9
DTBO_MAGIC = 0xd7b7ab1e
MAX_BLOB = 8 * 1024 * 1024


class DeviceTreeError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DeviceTreeError(message)


class Node:
    def __init__(self, name):
        self.name = name
        self.props = []
        self.children = []

    def prop(self, name):
        for key, value in self.props:
            if key == name:
                return value
        return None

    def child(self, name):
        for node in self.children:
            if node.name == name:
                return node
        return None


class Tree:
    def __init__(self, root, reserved, boot_cpu):
        self.root, self.reserved, self.boot_cpu = root, reserved, boot_cpu


def cstring(data, at, end):
    stop = data.find(b'\0', at, end)
    require(stop >= 0, 'unterminated device tree string')
    return data[at:stop].decode('utf-8'), stop + 1


def parse(data):
    """The tree of one flattened device tree blob."""
    require(isinstance(data, bytes) and 40 <= len(data) <= MAX_BLOB, 'invalid device tree size')
    magic, total, off_struct, off_strings, off_rsvmap, version, compatible, boot_cpu, size_strings, size_struct = \
        struct.unpack('>10I', data[:40])
    require(magic == MAGIC and total == len(data) and version >= 17 and compatible <= 16, 'not a version 17 device tree')
    require(off_struct + size_struct <= total and off_strings + size_strings <= total and off_rsvmap < off_struct
            and off_struct % 4 == 0, 'invalid device tree layout')
    reserved, at = [], off_rsvmap
    while True:
        require(at + 16 <= total, 'unterminated reservation map')
        address, size = struct.unpack('>QQ', data[at:at + 16])
        at += 16
        if address == 0 and size == 0:
            break
        reserved.append((address, size))
    strings_end = off_strings + size_strings
    at, end = off_struct, off_struct + size_struct
    stack, root = [], None
    while True:
        require(at + 4 <= end, 'device tree structure ends early')
        (token,) = struct.unpack('>I', data[at:at + 4])
        at += 4
        if token == BEGIN_NODE:
            name, at = cstring(data, at, end)
            at = (at + 3) & ~3
            node = Node(name)
            if stack:
                stack[-1].children.append(node)
            else:
                require(root is None and name == '', 'device tree has more than one root')
                root = node
            stack.append(node)
        elif token == END_NODE:
            require(stack, 'unbalanced device tree node end')
            stack.pop()
        elif token == PROP:
            require(stack and at + 8 <= end, 'device tree property outside a node')
            length, name_at = struct.unpack('>II', data[at:at + 8])
            at += 8
            require(at + length <= end and off_strings + name_at < strings_end, 'invalid device tree property')
            name, _ = cstring(data, off_strings + name_at, strings_end)
            stack[-1].props.append((name, data[at:at + length]))
            at = (at + length + 3) & ~3
        elif token == NOP:
            continue
        elif token == END:
            break
        else:
            raise DeviceTreeError('unknown device tree token')
    require(not stack and root is not None, 'device tree ends inside a node')
    return Tree(root, reserved, boot_cpu)


def serialize(tree):
    """A version 17 blob: header, reservation map, structure, strings."""
    strings, offsets = bytearray(), {}
    body = bytearray()

    def emit(node):
        body.extend(struct.pack('>I', BEGIN_NODE) + node.name.encode() + b'\0')
        body.extend(b'\0' * (-len(body) % 4))
        for name, value in node.props:
            if name not in offsets:
                offsets[name] = len(strings)
                strings.extend(name.encode() + b'\0')
            body.extend(struct.pack('>III', PROP, len(value), offsets[name]) + value)
            body.extend(b'\0' * (-len(body) % 4))
        for child in node.children:
            emit(child)
        body.extend(struct.pack('>I', END_NODE))

    emit(tree.root)
    body.extend(struct.pack('>I', END))
    rsvmap = b''.join(struct.pack('>QQ', a, s) for a, s in tree.reserved) + bytes(16)
    off_rsvmap = 40
    off_struct = off_rsvmap + len(rsvmap)
    off_strings = off_struct + len(body)
    total = off_strings + len(strings)
    header = struct.pack('>10I', MAGIC, total, off_struct, off_strings, off_rsvmap, 17, 16, tree.boot_cpu,
                         len(strings), len(body))
    return header + rsvmap + bytes(body) + bytes(strings)


def find(root, path):
    require(isinstance(path, str) and path.startswith('/'), 'invalid device tree path')
    node = root
    for part in [p for p in path.split('/') if p]:
        node = node.child(part)
        require(node is not None, 'no device tree node ' + path)
    return node


def encode(value):
    """Property bytes of a JSON value: {"cells": [...]} or {"strings": [...]}."""
    require(isinstance(value, dict) and len(value) == 1, 'invalid device tree value')
    (kind, items), = value.items()
    require(isinstance(items, list) and items, 'invalid device tree value')
    if kind == 'cells':
        require(all(type(i) is int and 0 <= i <= 0xffffffff for i in items), 'invalid device tree cells')
        return b''.join(struct.pack('>I', i) for i in items)
    require(kind == 'strings' and all(isinstance(i, str) and i and '\0' not in i for i in items),
            'invalid device tree strings')
    return b''.join(i.encode() + b'\0' for i in items)


def show(data):
    """A readable form of property bytes for reports."""
    if data is None:
        return 'absent'
    if data and data[-1] == 0:
        parts = data[:-1].split(b'\0')
        if all(part and all(32 <= b < 127 for b in part) for part in parts):
            return ', '.join('"%s"' % part.decode() for part in parts)
    if data and len(data) % 4 == 0:
        return '<' + ' '.join('0x%x' % c for c in struct.unpack('>%dI' % (len(data) // 4), data)) + '>'
    return data.hex()


def walk_cells(root, path, name):
    """The #address-cells or #size-cells a node resolves by walking up from itself."""
    parts = [p for p in path.split('/') if p]
    nodes = [root]
    for part in parts:
        nodes.append(nodes[-1].child(part))
    for node in reversed(nodes):
        value = node.prop(name)
        if value is not None:
            require(len(value) == 4, 'invalid ' + name)
            return struct.unpack('>I', value)[0]
    return None


def set_property(node, name, value):
    for index, (key, _) in enumerate(node.props):
        if key == name:
            if value is None:
                del node.props[index]
            else:
                node.props[index] = (name, value)
            return
    require(value is not None, 'cannot delete a missing property: ' + name)
    # A new property goes first in its node, as libfdt (fdtput) adds it.
    node.props.insert(0, (name, value))


def apply_edit(tree, original, edit):
    """One declared edit. Returns what changed, as (node, property, before, after) rows."""
    require(isinstance(edit, dict), 'invalid device tree edit')
    rows = []
    if 'inherit_cells' in edit:
        require(set(edit) == {'node', 'inherit_cells'} and isinstance(edit['inherit_cells'], list)
                and len(edit['inherit_cells']) == 2, 'invalid inherited cells edit')
        node = find(tree.root, edit['node'])
        for name, wanted in zip(('#address-cells', '#size-cells'), edit['inherit_cells']):
            require(walk_cells(original.root, edit['node'], name) == wanted,
                    'the cells %s of %s resolve to another value than %d' % (name, edit['node'], wanted))
            before = node.prop(name)
            after = struct.pack('>I', wanted)
            require(before in (None, after), '%s of %s is set to another value' % (name, edit['node']))
            if before is None:
                set_property(node, name, after)
                rows.append((edit['node'], name, None, after))
        return rows
    require(set(edit) == {'node', 'property', 'expect', 'set'}, 'invalid device tree edit')
    node = find(tree.root, edit['node'])
    name = edit['property']
    before = node.prop(name)
    expected = None if edit['expect'] is None else encode(edit['expect'])
    require(before == expected, 'unexpected %s of %s: found %s, expected %s' %
            (name, edit['node'], show(before), show(expected)))
    after = None if edit['set'] is None else encode(edit['set'])
    set_property(node, name, after)
    return [(edit['node'], name, before, after)]


def flatten(node, path=''):
    """Every (path, property, value) of a tree."""
    here = path + '/' + node.name if node.name else path
    rows = [(here or '/', name, value) for name, value in node.props]
    for child in node.children:
        rows += flatten(child, here)
    return rows


def differences(first, second):
    """Properties that differ between two trees, and nodes that exist in only one:
    (path, property, before, after) rows with None for absent."""
    a = {(p, n): v for p, n, v in flatten(first.root)}
    b = {(p, n): v for p, n, v in flatten(second.root)}
    rows = [(p, n, a.get((p, n)), b.get((p, n))) for p, n in sorted(set(a) | set(b)) if a.get((p, n)) != b.get((p, n))]

    def nodes(node, path=''):
        here = path + '/' + node.name if node.name else path
        found = {here or '/'}
        for child in node.children:
            found |= nodes(child, here)
        return found
    return rows, sorted(nodes(first.root) ^ nodes(second.root))


def apply_fixups(data, fixups):
    """The base blob with every fix-up applied, and a report. The result must
    differ from the base in exactly the edited properties."""
    require(isinstance(fixups, list) and fixups, 'no device tree fix-ups')
    base = parse(data)
    tree = parse(data)
    declared, report = {}, []
    for fixup in fixups:
        require(isinstance(fixup, dict) and set(fixup) == {'id', 'reason', 'edits'} and isinstance(fixup['edits'], list)
                and fixup['edits'] and isinstance(fixup['reason'], str) and fixup['reason'].strip(),
                'invalid device tree fix-up')
        changed = []
        for edit in fixup['edits']:
            for path, name, before, after in apply_edit(tree, base, edit):
                key = (path, name)
                require(key not in declared, 'two edits of %s %s' % key)
                # An edit that keeps the value only asserts it.
                if before != after:
                    declared[key] = (before, after)
                    changed.append({'node': path, 'property': name, 'before': show(before), 'after': show(after)})
        report.append({'id': fixup['id'], 'changes': changed})
    out = serialize(tree)
    again = parse(out)
    rows, nodes = differences(base, again)
    require(not nodes, 'the fix-ups added or removed nodes')
    require({(p, n): (b, a) for p, n, b, a in rows} == declared, 'the fixed device tree differs from the base beyond the declared edits')
    return out, report


def base_files(directory, spec):
    """The pinned base device tree and overlay image, each checked against its SHA-256."""
    files = {}
    for name, digest in sorted(spec['files'].items()):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'base device tree file missing: ' + name)
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == digest, 'base device tree file differs from its pin: ' + name)
        files[name] = data
    return files


def overlay_count(data):
    """The number of overlays in a dtbo.img."""
    require(len(data) >= 32, 'invalid overlay image')
    magic, total, header_size, entry_size, count = struct.unpack('>5I', data[:20])
    require(magic == DTBO_MAGIC and total <= len(data) and header_size >= 32 and entry_size >= 20,
            'not an overlay image')
    return count
