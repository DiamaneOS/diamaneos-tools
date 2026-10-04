"""Install the verified generated vendor tree into an Android checkout.

Installation writes a descriptor (``.repo/diamaneos-generated-inputs.json``)
that binds the tree to the selected build environment, the recipes that
produced it and its complete inventory. The full source preflight accepts the
generated directory only when that descriptor still matches. The kernel is not
generated: it comes from the kernel prebuilts project in the manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .kernel import KernelError, locked, require
from .vendor import ROOT, encoded
from .vendor_extract import relative, sha
from .vendor_files import verify_tree

DESCRIPTOR = '.repo/diamaneos-generated-inputs.json'
PREVIOUS = '.repo/diamaneos-previous-inputs'
DESCRIPTOR_SCHEMA = 3
DEFAULT_ENVIRONMENT = ROOT / 'config/build-environment-fp6.json'
TARGET_PRODUCT = 'FP6'
# Where the device configuration expects the kernel prebuilts (a manifest project).
KERNEL_PREBUILTS = 'device/fairphone/FP6-kernel'
# Generated trees and where the device configuration expects them.
DESTINATIONS = {'vendor': 'vendor/fairphone/FP6'}
# Trees earlier tools generated where the manifest now has a project. A sync
# moves such a tree aside (it has no .git) so repo can check out the project.
LEGACY_DESTINATIONS = {'kernel': KERNEL_PREBUILTS}
# The recipes the vendor tree records about itself in its provenance.
# Installation and the preflight require them to equal the recipes in this
# checkout, so a tree made from other recipes is refused.
RECIPE_KEYS = ('vendor_files', 'vendor_elf')
MAX_DESCRIPTOR_BYTES = 16 * 1024 * 1024


def read(path):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 4*1024*1024,
            'missing or invalid input inventory')
    return json.loads(path.read_bytes())


def current_recipes(root=ROOT):
    """Recipe digests of this checkout, computed as the generator records them."""
    from . import safe_json
    return {
        'vendor_files': hashlib.sha256(encoded(safe_json.load_json(root / 'config/fp6-minimal/vendor-files.json'))).hexdigest(),
        'vendor_elf': hashlib.sha256(encoded(safe_json.load_json(root / 'config/fp6-minimal/vendor-elf.json'))).hexdigest(),
    }


def generation_recipes(vendor_tree):
    """Recipe digests the vendor generation recorded."""
    provenance = read(vendor_tree / 'provenance.json')
    recipes = {'vendor_files': provenance.get('recipe_sha256'), 'vendor_elf': provenance.get('elf_selection_sha256')}
    missing = sorted(k for k, v in recipes.items() if not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v))
    require(not missing, 'the generated inputs do not record their recipes: ' + ', '.join(missing))
    return recipes


def require_current(recipes, root=ROOT):
    stale = sorted(k for k, v in current_recipes(root).items() if recipes.get(k) != v)
    require(not stale, 'generated inputs were made with other recipes (' + ', '.join(stale) +
            '); rebuild them with "diamaneos build all"')


def tools_identity(root=ROOT):
    """The tools commit and whether the checkout is clean (None when unknown)."""
    try:
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, timeout=60)
        status = subprocess.run(['git', '-C', str(root), 'status', '--porcelain=v1', '--untracked-files=no'],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'clean': None}
    commit = head.stdout.strip()
    if head.returncode or not re.fullmatch('[0-9a-f]{40}', commit) or status.returncode:
        return {'commit': None, 'clean': None}
    return {'commit': commit, 'clean': status.stdout == ''}


def validate_records(records):
    require(isinstance(records,dict) and 0 < len(records) <= 10000, 'invalid input record count')
    for name, row in records.items():
        # Generated carrier assets retain upstream Unicode and punctuation.
        # These names go through filesystem APIs, never a shell or debugfs.
        # Stock extraction keeps its separate, narrower argument validation.
        require(isinstance(name, str) and 0 < len(name.encode('utf-8')) <= 4096
                and '\\' not in name and not any(ord(c) < 32 or ord(c) == 127 for c in name)
                and all(p not in ('', '.', '..') and len(p.encode('utf-8')) <= 255
                        for p in name.split('/')), 'invalid generated input path')
        require(isinstance(row, dict) and set(row) == {'bytes','sha256'} and type(row['bytes']) is int and
                0 <= row['bytes'] <= 1024**3 and isinstance(row['sha256'], str)
                and re.fullmatch('[a-f0-9]{64}',row['sha256']), 'invalid input file record')
    require(sum(r['bytes'] for r in records.values()) <= 4*1024**3, 'input set exceeds bound')


def records_sha256(records):
    return hashlib.sha256(encoded(records)).hexdigest()


def install_one(source, records, destination, previous=None):
    """Install one verified tree. With ``previous``, a differing existing tree
    is moved there (replacing an older previous copy) instead of refused."""
    validate_records(records)
    verify_tree(source, records)
    if destination.exists() or destination.is_symlink():
        try:
            verify_tree(destination, records)
            return 'verified-existing'
        except (ValueError, OSError):
            if previous is None:
                raise
        require(not destination.is_symlink() and destination.is_dir(), 'generated destination is not a directory')
        require(not previous.is_symlink(), 'previous-input location is a symlink')
        previous.parent.mkdir(parents=True, exist_ok=True)
        if previous.exists():
            shutil.rmtree(previous)
        destination.rename(previous)
        outcome = 'replaced'
    else:
        outcome = 'installed'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.inputs-', dir=destination.parent) as temp:
        tree = Path(temp) / 'tree'
        shutil.copytree(source, tree)
        verify_tree(tree, records)
        tree.rename(destination)
    return outcome


def selected_inputs(vendor):
    """Resolve and authenticate the current vendor generation."""
    vcurrent = vendor / 'current'
    require(vcurrent.is_symlink(), 'vendor current generation is absent')
    target = os.readlink(vcurrent)
    require(re.fullmatch(r'generations/[a-f0-9]{64}',target), 'invalid vendor generation pointer')
    vtree = vendor / target
    vinventory = vendor / 'inventories' / (Path(target).name + '.json')
    vrecords = read(vinventory)
    return {
        'vendor': {'tree': vtree, 'records': vrecords, 'generation': Path(target).name,
                   'inventory_sha256': sha(vinventory)},
        'recipes': generation_recipes(vtree),
    }


def install(source, vendor, environment=DEFAULT_ENVIRONMENT, replace=False):
    require(source.is_dir() and not source.is_symlink(), 'Android source root is absent or a symlink')
    source = source.resolve()
    require((source / '.repo').is_dir() and not (source / '.repo').is_symlink(), 'expected an Android repo checkout')
    selected = selected_inputs(vendor)
    require_current(selected['recipes'])
    environment = Path(environment)
    environment_bytes = environment.read_bytes()
    environment_id = json.loads(environment_bytes)['environment_id']
    result = {'operation':'generated-input-installation', 'device_commands_executed':0,
              'vendor_inventory_sha256':selected['vendor']['inventory_sha256']}
    descriptor = {
        'schema_version': DESCRIPTOR_SCHEMA, 'operation': 'generated-input-installation',
        'target_product': TARGET_PRODUCT, 'device_commands_executed': 0,
        'environment_id': environment_id,
        'environment_sha256': hashlib.sha256(environment_bytes).hexdigest(),
        'tools': tools_identity(), 'recipes': selected['recipes'], 'inputs': {},
    }
    with locked(source / '.repo'):
        for label, rel in DESTINATIONS.items():
            item = selected[label]
            dest = source / rel
            # Do not install through any existing source-tree symlink.
            for parent in [dest,*dest.parents]:
                if parent == source: break
                require(not parent.is_symlink(), 'generated destination has a symlink ancestor')
            previous = source / PREVIOUS / label if replace else None
            result[label] = install_one(item['tree'], item['records'], dest, previous)
            descriptor['inputs'][label] = {'path': rel, 'inventory_sha256': item['inventory_sha256'],
                                           'records_sha256': records_sha256(item['records']),
                                           'records': item['records'], 'generation': item['generation']}
        result['status']='PASS'
        descriptor['status'] = 'PASS'
        report = source / DESCRIPTOR
        with tempfile.TemporaryDirectory(prefix='.inputs-report-',dir=source / '.repo') as temp:
            staged=Path(temp)/'report';staged.write_bytes(encoded(descriptor));os.replace(staged,report)
        result['descriptor_sha256'] = sha(report)
    return result


def verify_descriptor(source, environment_sha256=None, root=ROOT):
    """Check the installed generated inputs against their descriptor.

    Returns the accepted generated paths. Raises KernelError or ValueError
    when the descriptor is stale, altered or does not match the trees.
    """
    path = Path(source) / DESCRIPTOR
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_DESCRIPTOR_BYTES,
            'generated-input descriptor is missing or invalid')
    descriptor = json.loads(path.read_bytes())
    require(isinstance(descriptor, dict) and descriptor.get('schema_version') == DESCRIPTOR_SCHEMA,
            'generated-input record predates the bound descriptor; reinstall the generated inputs')
    require(descriptor.get('status') == 'PASS' and descriptor.get('target_product') == TARGET_PRODUCT,
            'generated-input descriptor is incomplete')
    if environment_sha256 is not None:
        require(descriptor.get('environment_sha256') == environment_sha256,
                'generated inputs were installed for another build environment')
    recipes = descriptor.get('recipes')
    require(isinstance(recipes, dict) and set(recipes) == set(RECIPE_KEYS), 'generated-input descriptor is incomplete')
    require_current(recipes, root)
    inputs = descriptor.get('inputs')
    require(isinstance(inputs, dict) and set(inputs) == set(DESTINATIONS), 'generated-input descriptor is incomplete')
    accepted = set()
    for label, rel in DESTINATIONS.items():
        entry = inputs[label]
        require(isinstance(entry, dict) and entry.get('path') == rel, 'generated input path differs')
        records = entry.get('records')
        validate_records(records)
        require(entry.get('records_sha256') == records_sha256(records), 'generated input records altered')
        verify_tree(Path(source) / rel, records)
        accepted.add(rel)
    return accepted


def move_aside(source, label, destination):
    require(not destination.is_symlink() and destination.is_dir(), 'generated destination is not a directory')
    previous = source / PREVIOUS / label
    previous.parent.mkdir(parents=True, exist_ok=True)
    if previous.exists():
        shutil.rmtree(previous)
    destination.rename(previous)
    # Parents left empty (vendor/fairphone) are not source projects;
    # the source tree check would reject them.
    for parent in destination.parents:
        if parent == source or any(parent.iterdir()):
            break
        parent.rmdir()


def retire_stale(source, environment_sha256=None, root=ROOT):
    """Move generated trees aside when they no longer match their descriptor,
    the environment or the recipes, and trees of earlier tools where the
    manifest now has a project. Returns the labels that were moved."""
    source = Path(source)
    moved = []
    for label, rel in LEGACY_DESTINATIONS.items():
        destination = source / rel
        if (destination.exists() or destination.is_symlink()) and not (destination / '.git').exists():
            move_aside(source, label, destination)
            moved.append(label)
    descriptor = source / DESCRIPTOR
    if descriptor.exists() or descriptor.is_symlink():
        try:
            verify_descriptor(source, environment_sha256, root)
            return moved
        except (ValueError, OSError):
            pass
    for label, rel in DESTINATIONS.items():
        destination = source / rel
        if destination.exists() or destination.is_symlink():
            move_aside(source, label, destination)
            moved.append(label)
    if descriptor.exists() or descriptor.is_symlink():
        descriptor.unlink()
    return moved


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--vendor',type=Path,required=True,help='vendor product generation root')
    parser.add_argument('--environment',type=Path,default=DEFAULT_ENVIRONMENT,
                        help='build environment the inputs are bound to')
    parser.add_argument('--replace',action='store_true',
                        help='move differing installed trees aside instead of refusing')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(install(args.source.absolute(),args.vendor.absolute(),
                                 args.environment, args.replace),indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError):
        print('ERROR: generated inputs could not be verified or installed');return 2
