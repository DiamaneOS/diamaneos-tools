"""The FP6 device tree of the 6.18 kernel set, checked against the 6.1 tree it replaces.

fp6.dtb is built from the vendor tree's device tree sources, which carry the
bindings the 5.0-based drivers and the 6.18 core read. The 6.1 base device
tree and overlay image are pinned by SHA-256 in config/fp6-kernel-dt.json.
The set ships that overlay image, and the tree from source may differ from the
6.1 base only in the differences the plan declares, each with the value it
has in both trees.
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


def encode(value, phandle_of=None):
    """Property bytes of a JSON value: {"cells": [...]} or {"strings": [...]}. A cell may be {"phandle": PATH},
    the phandle of that node in phandle_of (path -> phandle)."""
    require(isinstance(value, dict) and len(value) == 1, 'invalid device tree value')
    (kind, items), = value.items()
    require(isinstance(items, list) and items, 'invalid device tree value')
    if kind == 'cells':
        cells = []
        for item in items:
            if isinstance(item, dict):
                require(set(item) == {'phandle'} and isinstance(item['phandle'], str), 'invalid device tree cells')
                require(phandle_of is not None and item['phandle'] in phandle_of,
                        'no phandle for the device tree node ' + item['phandle'])
                item = phandle_of[item['phandle']]
            require(type(item) is int and 0 <= item <= 0xffffffff, 'invalid device tree cells')
            cells.append(item)
        return b''.join(struct.pack('>I', i) for i in cells)
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


def phandles(tree):
    """path -> phandle of every node that has one."""
    found = {}
    for path, node in walk(tree.root):
        value = node.prop('phandle') or node.prop('linux,phandle')
        if value is not None and len(value) == 4:
            found[path] = struct.unpack('>I', value)[0]
    return found


def same(first, second, first_nodes, second_nodes):
    """Equal bytes, or as many cells where each cell that differs is the phandle of the same node in both trees
    (first_nodes and second_nodes map a phandle to its path)."""
    if first == second:
        return True
    if first is None or second is None or len(first) != len(second) or len(first) % 4:
        return False
    count = len(first) // 4
    return all(a == b or (a in first_nodes and first_nodes[a] == second_nodes.get(b))
               for a, b in zip(struct.unpack('>%dI' % count, first), struct.unpack('>%dI' % count, second)))


def tree_differences(base, source):
    """How two trees differ when phandle numbers do not count: (path, property, base value, source value) rows,
    None where absent, and the nodes, child orders, reservations and boot CPU that differ."""
    first = {value: path for path, value in phandles(base).items()}
    second = {value: path for path, value in phandles(source).items()}
    a, b = dict(walk(base.root)), dict(walk(source.root))
    rows, problems = [], []
    for path in sorted(set(a) | set(b)):
        if path not in a or path not in b:
            problems.append('node %s is only in the %s' % (path, 'base' if path in a else 'source tree'))
            continue
        x, y = dict(a[path].props), dict(b[path].props)
        for name in sorted(set(x) | set(y)):
            if name in ('phandle', 'linux,phandle') and name in x and name in y:
                continue
            if not same(x.get(name), y.get(name), first, second):
                rows.append((path, name, x.get(name), y.get(name)))
        shared = {c.name for c in a[path].children} & {c.name for c in b[path].children}
        order = [[c.name for c in node.children if c.name in shared] for node in (a[path], b[path])]
        if order[0] != order[1]:
            problems.append('the children of %s come in another order' % path)
    if base.reserved != source.reserved:
        problems.append('the memory reservations differ')
    if base.boot_cpu != source.boot_cpu:
        problems.append('the boot CPU differs')
    return rows, problems


def declared_rows(base, source, maps, edit):
    """The (path, property, base value, source value) rows one declared edit expects."""
    require(isinstance(edit, dict), 'invalid device tree edit')
    if 'inherit_cells' in edit:
        require(set(edit) == {'node', 'inherit_cells'} and isinstance(edit['inherit_cells'], list)
                and len(edit['inherit_cells']) == 2
                and all(type(c) is int and 0 <= c <= 0xffffffff for c in edit['inherit_cells']),
                'invalid inherited cells edit')
        rows = []
        before, after = find(base.root, edit['node']), find(source.root, edit['node'])
        for name, wanted in zip(('#address-cells', '#size-cells'), edit['inherit_cells']):
            require(walk_cells(base.root, edit['node'], name) == wanted,
                    'the cells %s of %s resolve to another value than %d in the base' % (name, edit['node'], wanted))
            value = struct.pack('>I', wanted)
            require(before.prop(name) in (None, value) and after.prop(name) == value,
                    '%s of %s is not %d' % (name, edit['node'], wanted))
            if before.prop(name) is None:
                rows.append((edit['node'], name, None, value))
        return rows
    require(set(edit) == {'node', 'property', 'base', 'source'} and isinstance(edit['property'], str),
            'invalid device tree edit')
    find(base.root, edit['node'])
    values = [None if edit[key] is None else encode(edit[key], m) for key, m in zip(('base', 'source'), maps)]
    return [(edit['node'], edit['property'], *values)]


def check_differences(base_data, source_data, differences):
    """The tree from source may differ from the base only where the plan declares it. Each edit names a property
    with its value in both trees (null where absent), or asks for the #address-cells and #size-cells the base
    resolves by walking up. Nodes, child order and reservations must match, a declared difference must still be
    one, and phandle numbers do not count. Returns the changes of each declared difference."""
    require(isinstance(differences, list) and differences, 'no declared device tree differences')
    base, source = parse(base_data), parse(source_data)
    rows, problems = tree_differences(base, source)
    require(not problems, 'the device tree differs from the base: ' + ', '.join(problems[:5]))
    found = {(path, name): (x, y) for path, name, x, y in rows}
    maps = (phandles(base), phandles(source))
    declared, report = set(), []
    for difference in differences:
        require(isinstance(difference, dict) and set(difference) == {'id', 'reason', 'edits'}
                and isinstance(difference['edits'], list) and difference['edits']
                and isinstance(difference['reason'], str) and difference['reason'].strip(),
                'invalid device tree difference')
        changes = []
        for edit in difference['edits']:
            for path, name, x, y in declared_rows(base, source, maps, edit):
                require((path, name) not in declared, 'two edits of %s %s' % (path, name))
                declared.add((path, name))
                require((path, name) in found, 'the declared difference %s %s is no longer one' % (path, name))
                before, after = found[path, name]
                require((before, after) == (x, y), 'unexpected %s of %s: %s in the base and %s from source, declared %s and %s'
                        % (name, path, show(before), show(after), show(x), show(y)))
                changes.append({'node': path, 'property': name, 'base': show(x), 'source': show(y)})
        report.append({'id': difference['id'], 'changes': changes})
    stray = sorted(set(found) - declared)
    require(not stray, 'the device tree differs from the base beyond the declared differences: ' +
            ', '.join('%s %s' % key for key in stray[:5]) +
            (' and %d more' % (len(stray) - 5) if len(stray) > 5 else ''))
    return report


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


def overlay_table(data):
    """(count, header size, entry size, entries offset) of a dtbo.img, checked against its size."""
    require(len(data) >= 32, 'invalid overlay image')
    magic, total, header_size, entry_size, count, offset = struct.unpack('>6I', data[:24])
    require(magic == DTBO_MAGIC and total <= len(data) and header_size >= 32 and entry_size >= 20, 'not an overlay image')
    require(offset >= header_size and offset + count * entry_size <= total, 'invalid overlay image table')
    return count, entry_size, offset


def overlay_count(data):
    """The number of overlays in a dtbo.img."""
    return overlay_table(data)[0]


def overlays(data):
    """The overlay blobs of a dtbo.img, in table order."""
    count, entry_size, offset = overlay_table(data)
    blobs = []
    for index in range(count):
        size, start = struct.unpack('>2I', data[offset + index * entry_size:offset + index * entry_size + 8])
        require(start + size <= len(data), 'overlay %d lies outside the image' % index)
        blobs.append(data[start:start + size])
    return blobs


def walk(node, path='/'):
    """(path, node) for a node and every node below it, the first one at the given path."""
    yield path, node
    for child in node.children:
        yield from walk(child, path.rstrip('/') + '/' + child.name)


def compatibles(node):
    return {part.decode(errors='replace') for part in (node.prop('compatible') or b'').split(b'\0') if part}


def soc_ids(tree):
    """The SoC ids of a tree's qcom,msm-id (id and revision pairs)."""
    data = tree.root.prop('qcom,msm-id') or b''
    return set(struct.unpack('>%dI' % (len(data) // 4), data[:len(data) // 4 * 4])[0::2])


def lookup(root, path):
    node = root
    for part in [p for p in path.split('/') if p]:
        node = node.child(part)
        if node is None:
            return None
    return node


def overlay_targets(base, overlay):
    """The fragments of an overlay and the base nodes they apply to: ((overlay node, base path) for every fragment
    whose target resolves, the names of the fragments whose target does not). A target resolves by target-path, by a
    phandle of the base, or by the symbol the overlay's __fixups__ names, which the base's __symbols__ resolves."""
    symbols = base.root.child('__symbols__')
    labels = {name: value.rstrip(b'\0').decode(errors='replace') for name, value in (symbols.props if symbols else [])}
    phandles = {node.prop('phandle'): path for path, node in walk(base.root) if node.prop('phandle')}
    fixups = overlay.root.child('__fixups__')
    placed = {}
    for label, value in (fixups.props if fixups else []):
        for entry in filter(None, value.split(b'\0')):
            fragment, name, field = entry.decode(errors='replace').rsplit(':', 2)
            if name == 'target' and field == '0':
                placed[fragment] = label
    found, unresolved = [], []
    for fragment in overlay.root.children:
        body = fragment.child('__overlay__')
        if body is None:
            continue
        path = fragment.prop('target-path')
        if path is not None:
            target = path.rstrip(b'\0').decode(errors='replace')
        elif fragment.prop('target') == bytes.fromhex('ffffffff'):
            target = labels.get(placed.get('/' + fragment.name))
        else:
            target = phandles.get(fragment.prop('target'))
        if target and lookup(base.root, target) is not None:
            found.append((body, target))
        else:
            unresolved.append(fragment.name)
    return found, unresolved


def applicable(base, overlay_blobs):
    """(index, tree) of the overlays the bootloader applies to the base's SoC. An overlay without a qcom,msm-id
    applies to every SoC."""
    socs = soc_ids(base)
    trees = [(index, parse(blob)) for index, blob in enumerate(overlay_blobs)]
    return [(index, tree) for index, tree in trees if not soc_ids(tree) or soc_ids(tree) & socs]


def check_disabled_nodes(base_data, overlay_blobs, rules):
    """The nodes of drivers that do not ship, which the rules name by compatible, are disabled in the base and
    in every overlay the bootloader applies to its SoC (an overlay without a qcom,msm-id applies to all). An
    overlay also may not enable such a base node it targets, and every fragment of those overlays must have a target
    the base resolves, or this check could not see what the fragment enables. Returns the problems found."""
    wanted = {rule['compatible'] for rule in rules}
    base = parse(base_data)
    off = (b'disabled\0', b'fail\0')
    problems = []
    for path, node in walk(base.root):
        if compatibles(node) & wanted and node.prop('status') not in off:
            problems.append('%s in the device tree' % path)
    for index, overlay in applicable(base, overlay_blobs):
        for path, node in walk(overlay.root):
            if compatibles(node) & wanted and node.prop('status') not in off:
                problems.append('%s in overlay %d' % (path, index))
        targets, unresolved = overlay_targets(base, overlay)
        problems += ['/%s of overlay %d has a target the device tree does not resolve' % (name, index) for name in unresolved]
        for body, target in targets:
            for path, node in walk(body, target):
                status, original = node.prop('status'), lookup(base.root, path)
                if status is not None and status not in off and original is not None and compatibles(original) & wanted:
                    problems.append('%s enabled by overlay %d' % (path, index))
    return problems


def check_forbidden_properties(base_data, overlay_blobs, names, exemptions=()):
    """No property of the forbidden list is in the device tree or in an overlay the bootloader applies to its SoC.
    An exemption names one overlay by index and the board id (the first cell of its qcom,board-id) that justifies
    it, and must still be needed. Returns the problems found."""
    base = parse(base_data)
    problems = ['%s %s in the device tree' % (path, name) for path, node in walk(base.root)
                for name in sorted({n for n, _ in node.props} & set(names))]
    used = set()
    exempt = {(row['overlay'], row['property']): row for row in exemptions}
    for index, overlay in applicable(base, overlay_blobs):
        board = (overlay.root.prop('qcom,board-id') or b'')[:4]
        for path, node in walk(overlay.root):
            for name in sorted({n for n, _ in node.props} & set(names)):
                row = exempt.get((index, name))
                if row is None:
                    problems.append('%s %s in overlay %d' % (path, name, index))
                    continue
                used.add((index, name))
                if board != struct.pack('>I', row['board_id']):
                    problems.append('overlay %d is exempt for board id %d and has another' % (index, row['board_id']))
    problems += ['the exemption of %s in overlay %d is no longer needed' % (name, index) for index, name in sorted(set(exempt) - used)]
    return problems
