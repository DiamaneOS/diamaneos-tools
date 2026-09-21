"""Materialize reviewed regular stock files without trusting extraction directories.

The recipe owns selection and dependency decisions. This module authenticates
bytes, validates component policy, retains image metadata and publishes a
complete generation atomically. It does not infer runtime dependencies or
claim the Android product graph has been validated.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from . import components
from .vendor import VendorError, encoded

MAX_TOTAL_BYTES = 32 * 1024**3


def safe_path(value):
    if any(p in ('', '.', '..') for p in value.split('/')):
        raise VendorError('noncanonical selected file path')
    return value


@contextmanager
def regular_input(root, name):
    """Walk using directory descriptors; no ancestor or leaf symlink is followed."""
    parts = safe_path(name).split('/')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise VendorError('selected input is not a regular file')
            yield source
    finally:
        os.close(directory)


def copy_verified(root, item, destination):
    with regular_input(root, item['input']) as source:
        if os.fstat(source.fileno()).st_size != item['bytes']:
            raise VendorError('selected input size mismatch')
        value, size = hashlib.sha256(), 0
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with destination.open('xb') as target:
            while data := source.read(1024 * 1024):
                size += len(data)
                if size > item['bytes']:
                    raise VendorError('selected input exceeds declared size')
                target.write(data)
                value.update(data)
        destination.chmod(0o640)
        if size != item['bytes'] or value.hexdigest() != item['sha256']:
            raise VendorError('selected input digest mismatch')


def selection(recipe, model, sources, environment, model_sha256, source_sha256, public):
    if components._schema_errors(recipe, 'vendor-files.schema.json'):
        raise VendorError('invalid selected-file recipe')
    if components.validate_model(model, sources, environment, source_sha256=source_sha256):
        raise VendorError('component model validation failed')
    stock = model['fp6_model']['inputs']['selected_stock']
    if (recipe['model_sha256'] != model_sha256
            or recipe['stock_build'] != stock['build'] or recipe['region'] != stock['region']
            or recipe['archive_sha256'] != stock['factory_sha256']):
        raise VendorError('selected-file recipe input identity mismatch')
    paths, inputs = set(), set()
    notice_hashes = {n['sha256'] for n in recipe['notices']}
    if len(notice_hashes) != len(recipe['notices']):
        raise VendorError('repeated notice digest')
    used_notices = set()
    for item in recipe['files']:
        for key in ('input', 'path'):
            safe_path(item[key])
        if item['path'] in paths or item['input'] in inputs:
            raise VendorError('repeated selected file or destination')
        paths.add(item['path'])
        inputs.add(item['input'])
        used_notices.update(item['notices'])
        for dependency in item['dependencies']:
            safe_path(dependency)
    if any(parent.as_posix() in paths for name in paths for parent in Path(name).parents):
        raise VendorError('conflicting selected file destinations')
    if used_notices != notice_hashes:
        raise VendorError('missing or unused selected-file notice')
    for notice in recipe['notices']:
        safe_path(notice['input'])
    if sum(i['bytes'] for i in recipe['files'] + recipe['notices']) > MAX_TOTAL_BYTES:
        raise VendorError('selected files exceed total size bound')
    fields = ('path', 'sha256', 'component_id', 'inventory_ref', 'dependencies')
    closure = {'schema_version': 1, 'model_sha256': model_sha256,
               'stock_build': recipe['stock_build'], 'region': recipe['region'],
               'artifacts': [dict({k: i[k] for k in fields}, source_or_prebuilt='prebuilt')
                             for i in sorted(recipe['files'], key=lambda i: i['path'])],
               'component_results': []}
    for component in model['fp6_components']:
        owned = sorted(i['path'] for i in recipe['files'] if i['component_id'] == component['id'])
        closure['component_results'].append({'component_id': component['id'],
                                            'presence': 'present' if owned else 'absent',
                                            'artifact_paths': owned})
    if components.validate_closure(model, closure, model_sha256=model_sha256, public=public):
        raise VendorError('selected-file component closure validation failed')
    return closure


def verify_tree(root, records):
    if root.is_symlink() or not root.is_dir():
        raise VendorError('invalid generated tree')
    actual, actual_dirs = set(), set()
    expected_dirs = {parent.as_posix() for name in records for parent in Path(name).parents if parent != Path('.')}
    for parent, directories, files in os.walk(root, followlinks=False):
        if any((Path(parent) / d).is_symlink() for d in directories):
            raise VendorError('generated tree contains a directory symlink')
        actual_dirs.update((Path(parent) / d).relative_to(root).as_posix() for d in directories)
        for filename in files:
            actual.add((Path(parent) / filename).relative_to(root).as_posix())
    if actual != set(records) or actual_dirs != expected_dirs:
        raise VendorError('generated tree inventory mismatch')
    for name, record in records.items():
        with regular_input(root, name) as source:
            if stat.S_IMODE(os.fstat(source.fileno()).st_mode) != 0o640:
                raise VendorError('generated file mode mismatch')
            if os.fstat(source.fileno()).st_size != record['bytes']:
                raise VendorError('generated file size mismatch')
            value = hashlib.sha256()
            while data := source.read(1024 * 1024):
                value.update(data)
            if value.hexdigest() != record['sha256']:
                raise VendorError('generated file digest mismatch')


def generate(recipe, inputs, output, *, model, sources, environment,
             model_sha256, source_sha256, public=False):
    closure = selection(recipe, model, sources, environment, model_sha256, source_sha256, public)
    recipe = dict(recipe, files=sorted(recipe['files'], key=lambda i: i['path']),
                  notices=sorted(recipe['notices'], key=lambda i: i['sha256']))
    identity = hashlib.sha256(encoded(recipe)).hexdigest()
    manifest = {'operation': 'selected-stock-files', 'recipe_sha256': identity,
                'recipe': recipe, 'product_graph_validated': False}
    metadata = {'manifest.json': encoded(manifest), 'component-closure.json': encoded(closure)}
    records = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
               for name, data in metadata.items()}
    for item in recipe['files']:
        records['files/' + item['path']] = {'bytes': item['bytes'], 'sha256': item['sha256']}
    for item in recipe['notices']:
        records['notices/' + item['sha256']] = {'bytes': item['bytes'], 'sha256': item['sha256']}
    output = Path(output)
    if output.is_symlink():
        raise VendorError('output root cannot be a symlink')
    output.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = os.open(output / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise VendorError('invalid generation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        generations = output / 'generations'
        if generations.is_symlink():
            raise VendorError('generation root cannot be a symlink')
        generations.mkdir(exist_ok=True, mode=0o750)
        final = generations / identity
        # Reauthenticate every selected input even when the output already exists.
        with tempfile.TemporaryDirectory(prefix='.generate-', dir=generations) as temporary:
            tree = Path(temporary) / 'tree'
            tree.mkdir(mode=0o750)
            for item in recipe['files']:
                copy_verified(inputs, item, tree / 'files' / item['path'])
            for item in recipe['notices']:
                copy_verified(inputs, item, tree / 'notices' / item['sha256'])
            for name, data in metadata.items():
                (tree / name).write_bytes(data)
                (tree / name).chmod(0o640)
            verify_tree(tree, records)
            if final.exists() or final.is_symlink():
                verify_tree(final, records)
            else:
                tree.rename(final)
        current = output / 'current'
        target = 'generations/' + identity
        if not current.is_symlink() or os.readlink(current) != target:
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=output) as temporary:
                link = Path(temporary) / 'current'
                link.symlink_to(target)
                os.replace(link, current)
    return {'operation': 'selected-stock-files', 'status': 'PASS',
            'recipe_sha256': identity, 'file_count': len(recipe['files']),
            'notice_count': len(recipe['notices']), 'product_graph_validated': False,
            'scope': 'public-component-policy' if public else 'private-bringup'}
