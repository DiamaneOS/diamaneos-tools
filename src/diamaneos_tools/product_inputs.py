"""Install verified generated vendor and kernel inputs into an Android checkout.

Installation writes a descriptor (``.repo/diamaneos-generated-inputs.json``)
that binds both trees to the selected build environment, the recipes that
produced them and their complete inventories. The full source preflight accepts
the two generated directories only when that descriptor still matches.
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
DESCRIPTOR_SCHEMA = 2
DEFAULT_ENVIRONMENT = ROOT / 'config/build-environment-fp6.json'
TARGET_PRODUCT = 'FP6'
# Where the device configuration expects the kernel prebuilts.
KERNEL_PREBUILTS = 'device/fairphone/FP6-kernel'
# Generated trees and where the device configuration expects them.
DESTINATIONS = {'vendor': 'vendor/fairphone/FP6', 'kernel': KERNEL_PREBUILTS}
# The recipes each generated tree records about itself: the vendor product in
# its provenance, the kernel run in its result, preparation and configuration
# reports. Installation and the preflight require them to equal the recipes
# in this checkout, so a tree made from other recipes is refused.
RECIPE_KEYS = ('vendor_files', 'vendor_elf', 'kernel_sources', 'kernel_packaging',
               'kernel_policy', 'kernel_vendor_policy')
MAX_DESCRIPTOR_BYTES = 16 * 1024 * 1024


def read(path):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 4*1024*1024,
            'missing or invalid input inventory')
    return json.loads(path.read_bytes())


def policy_sha256(policy):
    """The kernel policy digest kernel_config.check records."""
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def current_recipes(root=ROOT):
    """Recipe digests of this checkout, computed as the generators record them."""
    from . import safe_json
    return {
        'vendor_files': hashlib.sha256(encoded(safe_json.load_json(root / 'config/fp6-minimal/vendor-files.json'))).hexdigest(),
        'vendor_elf': hashlib.sha256(encoded(safe_json.load_json(root / 'config/fp6-minimal/vendor-elf.json'))).hexdigest(),
        'kernel_sources': sha(root / 'config/kernel-sources-fp6.json'),
        'kernel_packaging': sha(root / 'config/fp6-kernel-packaging.json'),
        'kernel_policy': policy_sha256(json.loads((root / 'config/kernel-policy-fp6.json').read_bytes())),
        'kernel_vendor_policy': policy_sha256(json.loads((root / 'config/kernel-vendor-policy-fp6.json').read_bytes())),
    }


def generation_recipes(vendor_tree, kernel_root, run_dir):
    """Recipe digests the vendor generation and the kernel run recorded."""
    provenance = read(vendor_tree / 'provenance.json')
    result = read(run_dir / 'result.json')
    preparation_path = kernel_root / 'preparation.json'
    require(sha(preparation_path) == result.get('preparation_sha256'),
            'the kernel run no longer matches its preparation; run the kernel build again')
    preparation = read(preparation_path)
    policies = {read(run_dir / name).get('policy_sha256') for name in ('kernel-config.json', 'vendor-kernel-config.json')}
    require(len(policies) == 1, 'the kernel run checked two different policies')
    vendor_policy = read(run_dir / 'vendor-role-kernel-config.json')
    require(vendor_policy.get('status') == 'PASS', 'vendor IMS ownership configuration was not accepted')
    recipes = {'vendor_files': provenance.get('recipe_sha256'), 'vendor_elf': provenance.get('elf_selection_sha256'),
               'kernel_sources': preparation.get('source_plan_sha256'),
               'kernel_packaging': result.get('packaging_recipe_sha256'), 'kernel_policy': policies.pop(),
               'kernel_vendor_policy': vendor_policy.get('policy_sha256')}
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


def selected_inputs(vendor, kernel):
    """Resolve and authenticate the current vendor generation and kernel run."""
    vcurrent = vendor / 'current'
    require(vcurrent.is_symlink(), 'vendor current generation is absent')
    target = os.readlink(vcurrent)
    require(re.fullmatch(r'generations/[a-f0-9]{64}',target), 'invalid vendor generation pointer')
    vtree = vendor / target
    vinventory = vendor / 'inventories' / (Path(target).name + '.json')
    vrecords = read(vinventory)
    kcurrent = kernel / 'current'
    require(kcurrent.is_symlink(), 'kernel current candidate is absent')
    ktarget = os.readlink(kcurrent); relative(ktarget)
    require(ktarget.startswith('runs/') and ktarget.endswith('/candidate'), 'invalid kernel candidate pointer')
    ktree = kernel / ktarget
    require(ktree.resolve().is_relative_to(kernel.resolve()), 'kernel candidate escaped workspace')
    report = read(ktree.parent / 'result.json')
    kinventory = ktree.parent / 'artifacts.json'
    require(report['status'] == 'PASS' and sha(kinventory) == report['inventory_sha256'], 'kernel build result does not bind its inventory')
    rows = read(kinventory)
    krecords = {r['path']:{'bytes':r['bytes'],'sha256':r['sha256']} for r in rows}
    require(len(rows) == len(krecords), 'duplicate kernel artifact')
    return {
        'vendor': {'tree': vtree, 'records': vrecords, 'generation': Path(target).name,
                   'inventory_sha256': sha(vinventory)},
        'kernel': {'tree': ktree, 'records': krecords, 'run': ktarget,
                   'result_sha256': sha(ktree.parent / 'result.json'), 'inventory_sha256': sha(kinventory)},
        'recipes': generation_recipes(vtree, kernel, ktree.parent),
    }


def install(source, vendor, kernel, environment=DEFAULT_ENVIRONMENT, replace=False):
    require(source.is_dir() and not source.is_symlink(), 'Android source root is absent or a symlink')
    source = source.resolve()
    require((source / '.repo').is_dir() and not (source / '.repo').is_symlink(), 'expected an Android repo checkout')
    selected = selected_inputs(vendor, kernel)
    require_current(selected['recipes'])
    environment = Path(environment)
    environment_bytes = environment.read_bytes()
    environment_id = json.loads(environment_bytes)['environment_id']
    result = {'operation':'generated-input-installation', 'device_commands_executed':0,
              'vendor_inventory_sha256':selected['vendor']['inventory_sha256'],
              'kernel_inventory_sha256':selected['kernel']['inventory_sha256']}
    descriptor = {
        'schema_version': DESCRIPTOR_SCHEMA, 'operation': 'generated-input-installation',
        'target_product': TARGET_PRODUCT, 'device_commands_executed': 0,
        'environment_id': environment_id,
        'environment_sha256': hashlib.sha256(environment_bytes).hexdigest(),
        'tools': tools_identity(), 'recipes': selected['recipes'], 'inputs': {},
    }
    with locked(source / '.repo'):
        for label in ('vendor', 'kernel'):
            item = selected[label]
            rel = DESTINATIONS[label]
            dest = source / rel
            # Do not install through any existing source-tree symlink.
            for parent in [dest,*dest.parents]:
                if parent == source: break
                require(not parent.is_symlink(), 'generated destination has a symlink ancestor')
            previous = source / PREVIOUS / label if replace else None
            result[label] = install_one(item['tree'], item['records'], dest, previous)
            entry = {'path': rel, 'inventory_sha256': item['inventory_sha256'],
                     'records_sha256': records_sha256(item['records']), 'records': item['records']}
            if label == 'vendor':
                entry['generation'] = item['generation']
            else:
                entry.update(run=item['run'], result_sha256=item['result_sha256'])
            descriptor['inputs'][label] = entry
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


def retire_stale(source, environment_sha256=None, root=ROOT):
    """Move generated trees aside when they no longer match their descriptor,
    the environment or the recipes. Returns the labels that were moved."""
    source = Path(source)
    descriptor = source / DESCRIPTOR
    if descriptor.exists() or descriptor.is_symlink():
        try:
            verify_descriptor(source, environment_sha256, root)
            return []
        except (ValueError, OSError):
            pass
    moved = []
    for label, rel in DESTINATIONS.items():
        destination = source / rel
        if destination.exists() or destination.is_symlink():
            require(not destination.is_symlink() and destination.is_dir(), 'generated destination is not a directory')
            previous = source / PREVIOUS / label
            previous.parent.mkdir(parents=True, exist_ok=True)
            if previous.exists():
                shutil.rmtree(previous)
            destination.rename(previous)
            moved.append(label)
            # Parents left empty (vendor/fairphone) are not source projects;
            # the source tree check would reject them.
            for parent in destination.parents:
                if parent == source or any(parent.iterdir()):
                    break
                parent.rmdir()
    if descriptor.exists() or descriptor.is_symlink():
        descriptor.unlink()
    return moved


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--vendor',type=Path,required=True,help='vendor product generation root')
    parser.add_argument('--kernel',type=Path,required=True,help='prepared kernel workspace')
    parser.add_argument('--environment',type=Path,default=DEFAULT_ENVIRONMENT,
                        help='build environment the inputs are bound to')
    parser.add_argument('--replace',action='store_true',
                        help='move differing installed trees aside instead of refusing')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(install(args.source.absolute(),args.vendor.absolute(),args.kernel.absolute(),
                                 args.environment, args.replace),indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError):
        print('ERROR: generated inputs could not be verified or installed');return 2
