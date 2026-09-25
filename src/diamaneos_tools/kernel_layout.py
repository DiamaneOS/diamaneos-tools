"""Find Clang RANDSTRUCT layout splits in the kernel's debug objects.

With RANDSTRUCT, Clang randomizes a structure that has only function-pointer
members, but only if every struct or enum named by those members is already
declared where the structure is defined. When a callback return type is the
first mention of such a tag, that compilation unit silently keeps declaration
order. The same structure then has two layouts depending on include order, and
a call through it jumps to the wrong callback (a CFI failure in the kernel).
Clang gives no diagnostic for the return-type case.

The scan reads every DWARF definition of every function-pointer-only structure
in each object (pahole keeps each distinct definition, not only the first) and
fails when one structure, identified by name and member set, has more than one
member order anywhere in the Image or the modules.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import re

from . import process

START = re.compile(r'^struct (\w+) \{$')
FUNCTION_POINTER = re.compile(r'\(\*\s*\w+\)\s*\(')
COMMENT = re.compile(r'/\*.*?\*/')
VISIBILITY_WARNING = re.compile(rb'^.*\[-Wvisibility\]$', re.M)


def definitions(text):
    """Return (name, members in declared order) for each function-pointer-only
    structure in pahole's output."""
    found = []
    name = None
    members = []
    depth = 0
    for line in text.splitlines():
        if name is None:
            match = START.match(line)
            if match:
                name, members, depth = match.group(1), [], 1
            continue
        depth += line.count('{') - line.count('}')
        if depth <= 0:
            if members and all(FUNCTION_POINTER.search(m) for m in members):
                found.append((name, tuple(members)))
            name = None
            continue
        if depth == 1:
            member = COMMENT.sub('', line).strip().rstrip(';').strip()
            if member:
                members.append(re.sub(r'\s+', ' ', member))
    return found


def scan_one(pahole, obj):
    result = process.run([str(pahole), '-F', 'dwarf', '--suppress_aligned_attribute',
                          '--suppress_packed', '--suppress_force_paddings', str(obj)],
                         1800, max_output_bytes=1024 * 1024 * 1024)
    if result['transport'] != 'ok':
        return str(obj), [], result['transport'] + ': ' + result['stderr'][-1000:].decode(errors='replace')
    return str(obj), definitions(result['stdout'].decode(errors='replace')), None


def compare(results):
    """Group definitions by structure name and member set; more than one member
    order is a layout split."""
    grouped = defaultdict(lambda: defaultdict(set))
    count = 0
    for obj, found, error in results:
        if error:
            continue
        for name, layout in set(found):
            count += 1
            grouped[(name, tuple(sorted(layout)))][layout].add(obj)
    mismatches = []
    for (name, members), variants in sorted(grouped.items()):
        if len(variants) > 1:
            mismatches.append({'name': name, 'members': len(members),
                               'variants': [{'layout': list(layout), 'objects': sorted(objects)}
                                            for layout, objects in sorted(variants.items())]})
    return {'objects': len(results), 'structure_definitions': count,
            'errors': [{'object': obj, 'message': error} for obj, _, error in results if error],
            'mismatches': mismatches}


def scan(pahole, objects, jobs=4):
    with ThreadPoolExecutor(jobs) as pool:
        return compare(list(pool.map(lambda obj: scan_one(pahole, obj), objects)))


def visibility_warnings(log_bytes):
    """Clang's -Wvisibility names a struct first declared inside a parameter
    list: the parameter-type variant of the same bug. It is on by default, so
    any occurrence in a build log is a new instance."""
    return [m.decode(errors='replace').strip() for m in VISIBILITY_WARNING.findall(log_bytes)]
