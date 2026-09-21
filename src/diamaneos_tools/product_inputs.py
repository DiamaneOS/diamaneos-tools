"""Install verified generated vendor and kernel inputs into an Android checkout."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .kernel import KernelError, locked, require
from .vendor import encoded
from .vendor_extract import relative, sha
from .vendor_files import verify_tree


def read(path):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 4*1024*1024,
            'missing or invalid input inventory')
    return json.loads(path.read_bytes())


def install_one(source, records, destination):
    require(isinstance(records,dict) and 0 < len(records) <= 10000, 'invalid input record count')
    for name, row in records.items():
        relative(name)
        require(set(row) == {'bytes','sha256'} and type(row['bytes']) is int and
                0 <= row['bytes'] <= 1024**3 and re.fullmatch('[a-f0-9]{64}',row['sha256']),
                'invalid input file record')
    require(sum(r['bytes'] for r in records.values()) <= 4*1024**3, 'input set exceeds bound')
    verify_tree(source, records)
    if destination.exists() or destination.is_symlink():
        verify_tree(destination, records)
        return 'verified-existing'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.inputs-', dir=destination.parent) as temp:
        tree = Path(temp) / 'tree'
        shutil.copytree(source, tree)
        verify_tree(tree, records)
        tree.rename(destination)
    return 'installed'


def install(source, vendor, kernel):
    require(source.is_dir() and not source.is_symlink(), 'Android source root is absent or a symlink')
    source = source.resolve()
    require((source / '.repo').is_dir() and not (source / '.repo').is_symlink(), 'expected an Android repo checkout')
    vcurrent = vendor / 'current'
    require(vcurrent.is_symlink(), 'vendor current generation is absent')
    target = os.readlink(vcurrent)
    require(re.fullmatch(r'generations/[a-f0-9]{64}',target), 'invalid vendor generation pointer')
    vtree = vendor / target
    vinventory = vendor / 'inventories' / (Path(target).name + '.json')
    vrecords = read(vinventory)
    kcurrent = kernel / 'current'
    require(kcurrent.is_symlink(), 'kernel current candidate is absent')
    target = os.readlink(kcurrent); relative(target)
    require(target.startswith('runs/') and target.endswith('/candidate'), 'invalid kernel candidate pointer')
    ktree = kernel / target
    require(ktree.resolve().is_relative_to(kernel.resolve()), 'kernel candidate escaped workspace')
    report = read(ktree.parent / 'result.json')
    kinventory = ktree.parent / 'artifacts.json'
    require(report['status'] == 'PASS' and sha(kinventory) == report['inventory_sha256'], 'kernel build result does not bind its inventory')
    rows = read(kinventory)
    krecords = {r['path']:{'bytes':r['bytes'],'sha256':r['sha256']} for r in rows}
    require(len(rows) == len(krecords), 'duplicate kernel artifact')
    result = {'operation':'generated-input-installation', 'device_commands_executed':0,
              'vendor_inventory_sha256':sha(vinventory), 'kernel_inventory_sha256':sha(kinventory)}
    with locked(source / '.repo'):
        for label, tree, records, rel in (
                ('vendor',vtree,vrecords,'vendor/fairphone/FP6'),
                ('kernel',ktree,krecords,'device/fairphone/FP6-kernel')):
            dest = source / rel
            # Do not install through any existing source-tree symlink.
            for parent in [dest,*dest.parents]:
                if parent == source: break
                require(not parent.is_symlink(), 'generated destination has a symlink ancestor')
            result[label] = install_one(tree,records,dest)
        result['status']='PASS'
        report = source / '.repo/diamaneos-generated-inputs.json'
        with tempfile.TemporaryDirectory(prefix='.inputs-report-',dir=source / '.repo') as temp:
            staged=Path(temp)/'report';staged.write_bytes(encoded(result));os.replace(staged,report)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--vendor',type=Path,required=True,help='vendor product generation root')
    parser.add_argument('--kernel',type=Path,required=True,help='prepared kernel workspace')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(install(args.source.absolute(),args.vendor.absolute(),args.kernel.absolute()),indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError):
        print('ERROR: generated inputs could not be verified or installed');return 2
