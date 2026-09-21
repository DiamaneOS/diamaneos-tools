"""Stage stock images or materialize hash-bound, component-reviewed regular files.

Selection is explicit. Neither operation establishes Android product-graph or
device compatibility.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

from .components import ComponentError, load_json

ROOT = Path(__file__).resolve().parents[2]
# Inputs needed for partition/AVB and kernel-ramdisk discovery. Writable device
# state (userdata, persist, modem NV, provisioning, etc.) is never stageable here.
PARTITIONS = {'super', 'boot', 'vendor_boot', 'init_boot', 'dtbo', 'vbmeta', 'vbmeta_system'}
MAX_IMAGE_BYTES = 16 * 1024**3
MAX_TOTAL_BYTES = 64 * 1024**3


class VendorError(ValueError):
    """A diagnostic without input paths or untrusted values."""


def digest(stream):
    value = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        value.update(chunk)
    return value.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def validate_recipe(recipe):
    if (not isinstance(recipe, dict) or set(recipe) != {
            'schema_version', 'stock_build', 'region', 'archive_sha256',
            'archive_bytes', 'images'} or type(recipe['schema_version']) is not int
            or recipe['schema_version'] != 1):
        raise VendorError('invalid stock image recipe')
    if (recipe['region'] not in ('EU', 'US')
            or not isinstance(recipe['stock_build'], str)
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', recipe['stock_build'])
            or not isinstance(recipe['archive_sha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', recipe['archive_sha256'])
            or type(recipe['archive_bytes']) is not int
            or not 0 < recipe['archive_bytes'] <= MAX_TOTAL_BYTES):
        raise VendorError('invalid stock identity')
    images = recipe['images']
    if not isinstance(images, list) or not 0 < len(images) <= len(PARTITIONS):
        raise VendorError('invalid image selection')
    partitions, members = set(), set()
    for image in images:
        if not isinstance(image, dict) or set(image) != {'partition', 'member', 'sha256', 'bytes'}:
            raise VendorError('invalid image record')
        partition, member = image['partition'], image['member']
        if not isinstance(partition, str) or partition not in PARTITIONS or partition in partitions:
            raise VendorError('prohibited or repeated partition')
        if (not isinstance(member, str) or len(member) > 1024
                or not re.fullmatch(r'[A-Za-z0-9._/-]+', member)
                or member.startswith('/') or any(p in ('', '.', '..') for p in member.split('/'))
                or PurePosixPath(member).name != partition + '.img' or member in members):
            raise VendorError('unsafe or mismatched archive member')
        if (type(image['bytes']) is not int or not 0 < image['bytes'] <= MAX_IMAGE_BYTES
                or not isinstance(image['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', image['sha256'])):
            raise VendorError('invalid image bounds or digest')
        partitions.add(partition)
        members.add(member)
    if sum(i['bytes'] for i in images) > MAX_TOTAL_BYTES:
        raise VendorError('image selection exceeds total limit')


def verify_generation(root, recipe, provenance):
    expected = {'provenance.json'} | {i['partition'] + '.img' for i in recipe['images']}
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != expected:
        raise VendorError('existing generation has unexpected contents')
    for image in recipe['images']:
        p = root / (image['partition'] + '.img')
        if p.is_symlink() or not p.is_file() or p.stat().st_size != image['bytes']:
            raise VendorError('existing image is not the declared regular file')
        with p.open('rb') as f:
            if digest(f) != image['sha256']:
                raise VendorError('existing image digest mismatch')
    p = root / 'provenance.json'
    if (p.is_symlink() or not p.is_file() or p.stat().st_size != len(provenance)
            or p.read_bytes() != provenance):
        raise VendorError('existing provenance mismatch')


def stage(recipe, archive, output):
    validate_recipe(recipe)
    recipe = dict(recipe, images=sorted(recipe['images'], key=lambda i: i['partition']))
    identity = hashlib.sha256(encoded(recipe)).hexdigest()
    provenance = encoded({'operation': 'stock-image-staging', 'recipe_sha256': identity,
                          'recipe': recipe, 'vendor_tree_generated': False})
    archive, output = Path(archive), Path(output)
    if archive.is_symlink() or not archive.is_file():
        raise VendorError('archive must be a regular file')
    # Keep one descriptor across authentication and extraction.
    with archive.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size != recipe['archive_bytes'] or digest(stream) != recipe['archive_sha256']:
            raise VendorError('factory archive identity mismatch')
        stream.seek(0)
        with zipfile.ZipFile(stream) as bundle:
            entries = bundle.infolist()
            if len(entries) > 100000 or len({i.filename for i in entries}) != len(entries):
                raise VendorError('archive member inventory is oversized or ambiguous')
            selected = []
            for image in recipe['images']:
                try:
                    member = bundle.getinfo(image['member'])
                except KeyError:
                    raise VendorError('declared image is missing') from None
                if (member.is_dir() or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                        or member.file_size != image['bytes'] or member.flag_bits & 1):
                    raise VendorError('declared image is not a matching unencrypted regular file')
                selected.append((image, member))
            if output.is_symlink():
                raise VendorError('output root cannot be a symlink')
            output.mkdir(parents=True, exist_ok=True, mode=0o750)
            lock_path = output / '.lock'
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'rb') as lock:
                if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                    raise VendorError('invalid staging lock')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                generations = output / 'generations'
                if generations.is_symlink():
                    raise VendorError('generation root cannot be a symlink')
                generations.mkdir(exist_ok=True, mode=0o750)
                final = generations / identity
                if not final.exists() and not final.is_symlink():
                    with tempfile.TemporaryDirectory(prefix='.stage-', dir=generations) as temporary:
                        staged = Path(temporary) / 'tree'
                        staged.mkdir(mode=0o750)
                        for image, member in selected:
                            value, size = hashlib.sha256(), 0
                            p = staged / (image['partition'] + '.img')
                            with bundle.open(member) as source, p.open('xb') as target:
                                while chunk := source.read(1024 * 1024):
                                    size += len(chunk)
                                    if size > image['bytes']:
                                        raise VendorError('image exceeds declared size')
                                    value.update(chunk)
                                    target.write(chunk)
                            p.chmod(0o640)
                            if size != image['bytes'] or value.hexdigest() != image['sha256']:
                                raise VendorError('image digest or size mismatch')
                        (staged / 'provenance.json').write_bytes(provenance)
                        (staged / 'provenance.json').chmod(0o640)
                        verify_generation(staged, recipe, provenance)
                        staged.rename(final)
                else:
                    verify_generation(final, recipe, provenance)
                # Publication is a single pointer replacement, never a partial tree.
                current = output / 'current'
                target = 'generations/' + identity
                if not current.is_symlink() or os.readlink(current) != target:
                    with tempfile.TemporaryDirectory(prefix='.publish-', dir=output) as temp:
                        link = Path(temp) / 'current'
                        link.symlink_to(target)
                        os.replace(link, current)
    return {'status': 'PASS', 'operation': 'stock-image-staging',
            'recipe_sha256': identity, 'image_count': len(recipe['images']),
            'vendor_tree_generated': False, 'device_commands_executed': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['stage', 'generate'])
    parser.add_argument('--recipe', type=Path, default=ROOT / 'config/fp6-stock-image-recipe.json')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--inputs', type=Path, help='extracted partition roots for selected-file generation')
    parser.add_argument('--model', type=Path, default=ROOT / 'config/components.json')
    parser.add_argument('--sources', type=Path, default=ROOT / 'config/fp6-sources.json')
    parser.add_argument('--environment', type=Path, default=ROOT / 'config/build-environment.json')
    parser.add_argument('--public', action='store_true', help='require accepted public component dispositions')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'stage':
            if args.archive is None or args.inputs is not None or args.public:
                raise VendorError('stage requires an archive and no generation options')
            result = stage(load_json(args.recipe), args.archive, args.output)
        else:
            from . import components, vendor_files
            if args.inputs is None or args.archive is not None:
                raise VendorError('generate requires extracted inputs and a selected-file recipe')
            with args.model.open('rb') as stream:
                model_data = stream.read(components.MAX_FILE_BYTES + 1)
            with args.sources.open('rb') as stream:
                source_data = stream.read(components.MAX_FILE_BYTES + 1)
            result = vendor_files.generate(
                load_json(args.recipe), args.inputs, args.output,
                model=components.loads(model_data), sources=components.loads(source_data),
                environment=load_json(args.environment),
                model_sha256=hashlib.sha256(model_data).hexdigest(),
                source_sha256=hashlib.sha256(source_data).hexdigest(), public=args.public)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (VendorError, ComponentError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        print('ERROR: unable to read, lock or publish stock inputs safely', file=sys.stderr)
        return 2
