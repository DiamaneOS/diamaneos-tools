"""Extract recipe-selected files from an authenticated stock super image without mounting it."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from . import process
from .vendor import VendorError, encoded, load_json, ROOT


# Logical partitions of the stock super image a selection may read. Each is an
# ext4 image read with the pinned debugfs; system and odm stay out of scope.
EXTRACT_PARTITIONS = ('vendor', 'system_ext', 'product')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path, size, digest):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size or sha(path) != digest:
        raise VendorError('input/output file does not match its declared identity')


def relative(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.+@/-]+', value):
        raise VendorError('unsupported extraction path')
    if value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/')):
        raise VendorError('unsafe extraction path')
    return Path(value)


TOOL_POLICIES = ('pinned', 'recorded')


def tool_identity(image_tools, tool_pins, policy):
    """Check the image tools against their pins, or record what is used.

    ``recorded`` is for tools built from the pinned source on the build host:
    their hashes enter the generation identity and the result, and every
    extracted file is still checked against the selected-file recipe.
    """
    if policy not in TOOL_POLICIES:
        raise VendorError('unknown image tool policy')
    observed = {'tools': {}, 'libraries': {}}
    for name, digest in tool_pins['tools'].items():
        p = image_tools / name
        if p.is_symlink() or not p.is_file() or (policy == 'pinned' and sha(p) != digest):
            raise VendorError('image tool does not match the pinned tool identity')
        observed['tools'][name] = sha(p)
    for name, digest in tool_pins['libraries'].items():
        p = image_tools.parent / 'lib64' / name
        if policy == 'recorded' and not p.exists() and not p.is_symlink():
            continue
        if p.is_symlink() or not p.is_file() or (policy == 'pinned' and sha(p) != digest):
            raise VendorError('image tool library does not match its pinned identity')
        observed['libraries'][name] = sha(p)
    return observed


def extract(super_image, image_tools, output, stock, selection, tool_pins, tool_policy='pinned'):
    if any(selection[k] != stock[k] for k in ('archive_sha256', 'stock_build', 'region')):
        raise VendorError('selected files and image recipe disagree')
    record = next(i for i in stock['images'] if i['partition'] == 'super')
    regular(super_image, record['bytes'], record['sha256'])
    observed_tools = tool_identity(image_tools, tool_pins, tool_policy)
    if tool_policy == 'recorded':
        tool_pins = observed_tools
    rows = selection['files'] + selection['notices'] + selection.get('build_properties', [])
    links = selection.get('symlinks', [])
    paths = [relative(r['input']) for r in rows + links]
    if len(set(paths)) != len(paths) or any(p.parts[0] not in EXTRACT_PARTITIONS or len(p.parts) < 2
                                            for p in paths):
        raise VendorError('expected unique selected stock partition paths')
    identity = hashlib.sha256(encoded({'stock': stock, 'selection': selection,
                                       'image_tools': tool_pins, 'format': 1})).hexdigest()
    if output.is_symlink():
        raise VendorError('extraction root cannot be a symlink')
    output.mkdir(parents=True, exist_ok=True)
    fd = os.open(output / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise VendorError('extraction lock is not a regular file')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        generations = output / 'generations'
        if generations.is_symlink():
            raise VendorError('generation directory cannot be a symlink')
        generations.mkdir(exist_ok=True)
        final = generations / identity
        def verify(root):
            if root.is_symlink() or not root.is_dir():
                raise VendorError('invalid extraction generation')
            expected = {r['input'] for r in rows + links}
            actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_symlink() or not p.is_dir()}
            if actual != expected:
                raise VendorError('extracted file set differs from selection')
            for r in rows:
                regular(root / r['input'], r['bytes'], r['sha256'])
            for r in links:
                p = root / r['input']
                if not p.is_symlink() or os.readlink(p) != r['target']:
                    raise VendorError('extracted alias differs from selection')
                if hashlib.sha256(os.readlink(p).encode()).hexdigest() != r['sha256']:
                    raise VendorError('alias identity mismatch')
        if final.exists() or final.is_symlink():
            verify(final)
        else:
            with tempfile.TemporaryDirectory(prefix='.extract-', dir=generations) as temp:
                work = Path(temp)
                tree = work / 'tree'; tree.mkdir()
                def call(name, args):
                    result = process.run([str(image_tools / name), *map(str, args)], 1800,
                                         max_output_bytes=8*1024*1024, cwd=work,
                                         env={k:v for k,v in os.environ.items() if k not in ('LD_LIBRARY_PATH', 'LD_PRELOAD')})
                    if result['transport'] != 'ok':
                        raise VendorError(name + ' failed during image extraction')
                    return result['stdout'] + result['stderr']
                # Authenticate the private copy actually consumed by native parsers.
                snapshot = work / 'super.img'
                shutil.copyfile(super_image, snapshot)
                regular(snapshot, record['bytes'], record['sha256'])
                call('simg2img', [snapshot, work / 'super.raw.img'])
                partitions = work / 'partitions'; partitions.mkdir()
                images = {}
                for name in sorted({p.parts[0] for p in paths}):
                    call('lpunpack', ['-p', name + '_a', work / 'super.raw.img', partitions])
                    image = partitions / (name + '_a.img')
                    if image.is_symlink() or not image.is_file():
                        raise VendorError('selected stock partition was not unpacked')
                    with image.open('rb') as stream:
                        stream.seek(1080)
                        if stream.read(2) != bytes.fromhex('53ef'):
                            raise VendorError('pinned extraction requires ext4 stock partition images')
                    images[name] = image
                # Extract only regular files, never rdump a filesystem or follow its links.
                # Relative host paths keep debugfs commands independent of workspace spelling.
                # Each input is read from the image of its own partition.
                for row in rows:
                    rel = relative(row['input']); target = tree / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = '/' + '/'.join(rel.parts[1:])
                    image = images[rel.parts[0]]
                    info = call('debugfs_static', ['-R', 'stat ' + source, image])
                    if b'Type: regular' not in info:
                        raise VendorError('selected input is not a regular filesystem inode')
                    call('debugfs_static', ['-R', 'dump ' + source + ' tree/' + rel.as_posix(), image])
                    regular(target, row['bytes'], row['sha256'])
                for row in links:
                    rel = relative(row['input']); target = row['target']
                    # Stock app library links are absolute (/system_ext/lib64/...);
                    # they must stay inside their own partition.
                    relative(target.removeprefix('/'))
                    if target.startswith('/') and target.split('/')[1] != rel.parts[0]:
                        raise VendorError('selected alias crosses partition boundary')
                    source = '/' + '/'.join(rel.parts[1:])
                    image = images[rel.parts[0]]
                    info = call('debugfs_static', ['-R', 'stat ' + source, image])
                    match = re.search(rb'Fast link dest: "([^"\r\n]+)"', info)
                    if b'Type: symlink' not in info or not match or match[1].decode() != row['target']:
                        raise VendorError('selected alias inode or target differs')
                    target = tree / rel; target.parent.mkdir(parents=True, exist_ok=True)
                    target.symlink_to(row['target'])
                verify(tree)
                tree.rename(final)
        current = output / 'current'
        if not current.is_symlink() or os.readlink(current) != 'generations/' + identity:
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=output) as temp:
                pointer = Path(temp) / 'current'; pointer.symlink_to('generations/' + identity)
                os.replace(pointer, current)
    return {'status': 'PASS', 'operation': 'selected-stock-extraction', 'generation': identity,
            'file_count': len(rows), 'symlink_count': len(links), 'super_sha256': record['sha256'],
            'image_tool_policy': tool_policy, 'image_tools': observed_tools,
            'device_commands_executed': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--super', dest='super_image', type=Path, required=True)
    parser.add_argument('--image-tools', type=Path, required=True, help='pinned image-tool bin directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record-tools', action='store_true',
                        help='accept tools built from the pinned source and record their hashes')
    args = parser.parse_args(argv)
    try:
        with process.interrupt_on_termination():
            result = extract(args.super_image.resolve(), args.image_tools.resolve(), args.output.absolute(),
                         load_json(ROOT / 'config/fp6-stock-image-recipe.json'),
                         load_json(ROOT / 'config/fp6-minimal/vendor-files.json'),
                         load_json(ROOT / 'config/fp6-image-tools.json'),
                         'recorded' if args.record_tools else 'pinned')
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print('ERROR: selected stock extraction interrupted'); return 130
    except (VendorError, OSError, ValueError, KeyError, StopIteration) as exc:
        print('ERROR: selected stock extraction failed: ' + str(exc))
        return 2
