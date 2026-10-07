"""The stock firmware an FP6 image set carries: exact bytes, pinned hashes, stock order.

DiamaneOS cannot build or sign firmware, so it ships Fairphone's: every image
is copied byte for byte from the authenticated factory package and must match
the size and SHA-256 that config/fp6-firmware-inventory.json pins for the
release the vendor files come from. The flash steps follow Fairphone's flash
script: its order, and both slots of every A/B firmware partition. The
"firmware" block of config/fp6-build.json names the stock steps DiamaneOS
leaves out or ties to a wipe, each with its reason.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from . import build_workspace as bw

INVENTORY = 'fp6-firmware-inventory.json'
RELEASE = re.compile(r'FP6\.QREL\.(\d+)\.(\d+)\.(\d+)')
MAX_IMAGE_BYTES = 2 * 1024 ** 3


class FirmwareError(ValueError):
    pass


def release_key(name: str) -> tuple[int, int, int]:
    """A stock build number as numbers, so releases compare in order."""
    match = RELEASE.fullmatch(name) if isinstance(name, str) else None
    if not match:
        raise FirmwareError(f'{name!r} is not an FP6 stock build number (FP6.QREL.<major>.<minor>.<patch>)')
    return tuple(int(part) for part in match.groups())


def load_inventory() -> dict:
    return bw.load_config(INVENTORY)[0]


def plan(inventory: dict, policy: dict, release: str | None = None) -> dict:
    """The images a release ships and the flash steps for them, in stock order.

    Every firmware image of the release ships unless the policy excludes it, and
    each must be written to exactly the partitions the inventory lists for it."""
    release = release or inventory['selected_build']
    release_key(release)
    if release not in inventory['releases']:
        raise FirmwareError(f'{release} is not in the inventory')
    images = inventory['releases'][release]['images']
    stock = images['firmware']
    known = {name for group in images.values() for name in group}
    for name in list(policy['excluded']) + list(policy['wipe_only']):
        if name not in stock:
            raise FirmwareError(f'the firmware policy names {name}, which {release} does not have')
    written, steps = {}, []
    for entry in inventory['stock_flash_script']['order']:
        partition, image = entry['partition'], entry['image']
        if image not in known:
            raise FirmwareError(f'the stock flash script writes {image}, an unknown image')
        if partition in {p for parts in written.values() for p in parts}:
            raise FirmwareError(f'the stock flash script writes {partition} more than once')
        written.setdefault(image, []).append(partition)
        if image in stock and image not in policy['excluded']:
            step = {'partition': partition, 'image': image}
            if image in policy['wipe_only']:
                step['wipe_only'] = True
            steps.append(step)
    for name, identity in stock.items():
        if name not in written:
            raise FirmwareError(f'{name} is not written by the stock flash script')
        if sorted(written[name]) != sorted(identity['fastboot_partitions']):
            raise FirmwareError(f'{name}: the stock flash script writes {sorted(written[name])}, but the inventory '
                                f'lists partitions {sorted(identity["fastboot_partitions"])}')
        for partition in written[name]:
            if partition.endswith('_a') and partition[:-2] + '_b' not in written[name]:
                raise FirmwareError(f'{name} is written to {partition} but not to {partition[:-2]}_b')
    shipped = sorted({s['image'] for s in steps})
    return {'release': release,
            'images': {n: {'bytes': stock[n]['bytes'], 'sha256': stock[n]['sha256']} for n in shipped},
            'steps': steps,
            'fastboot_versions': {name: entry.get('fastboot_versions')
                                  for name, entry in sorted(inventory['releases'].items())}}


def ab_partitions(firmware_plan: dict) -> list[str]:
    """The A/B firmware partitions (without slot suffix): what an A/B update
    would carry. Single-copy partitions cannot be updated atomically."""
    names = {s['partition'] for s in firmware_plan['steps']}
    return sorted(n[:-2] for n in names if n.endswith('_a') and n[:-2] + '_b' in names)


def anti_rollback_problems(inventory: dict, release: str) -> list[str]:
    """Shipped images whose Qualcomm anti-rollback version is lower than in an
    older inventoried release."""
    problems = []
    current = inventory['releases'][release]['images']['firmware']
    for other, entry in inventory['releases'].items():
        if release_key(other) >= release_key(release):
            continue
        for name, identity in entry['images']['firmware'].items():
            old = (identity.get('signing') or {}).get('oem_anti_rollback_version')
            new = ((current.get(name) or {}).get('signing') or {}).get('oem_anti_rollback_version')
            if old is not None and (new is None or new < old):
                problems.append(f'{name}: anti-rollback version {new} in {release}, {old} in {other}')
    return problems


def member_name(archive_entry: dict, image: str) -> str:
    """Where the factory package keeps an image: <package name>/images/<image>."""
    top = PurePosixPath(archive_entry['filename'])
    if top.suffix != '.zip' or top.name != archive_entry['filename']:
        raise FirmwareError('unexpected factory package name ' + archive_entry['filename'])
    return f'{top.stem}/images/{image}'


def stage(archive: Path, inventory: dict, firmware_plan: dict, directory: Path) -> dict:
    """Copy the plan's images out of the factory package into ``directory``.

    The package must be the inventory's package of the release (size and
    SHA-256), and each image must match its own pinned size and SHA-256. One
    open file serves the check and the copy."""
    release = firmware_plan['release']
    entry = inventory['releases'][release]['archive']
    archive = Path(archive)
    if archive.is_symlink() or not archive.is_file():
        raise FirmwareError(f'the factory package {archive.name} is missing')
    digests = {}
    with archive.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b''):
            digest.update(chunk)
        if os.fstat(stream.fileno()).st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise FirmwareError(f'{archive.name} is not the factory package of {release} the inventory pins')
        stream.seek(0)
        with zipfile.ZipFile(stream) as bundle:
            for name, identity in sorted(firmware_plan['images'].items()):
                try:
                    member = bundle.getinfo(member_name(entry, name))
                except KeyError:
                    raise FirmwareError(f'the factory package lacks {name}') from None
                if (member.is_dir() or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                        or member.flag_bits & 1 or member.file_size != identity['bytes']
                        or identity['bytes'] > MAX_IMAGE_BYTES):
                    raise FirmwareError(f'{name} in the factory package is not the declared regular file')
                value, size = hashlib.sha256(), 0
                with bundle.open(member) as source, (directory / name).open('xb') as target:
                    for chunk in iter(lambda: source.read(16 * 1024 * 1024), b''):
                        size += len(chunk)
                        if size > identity['bytes']:
                            break
                        value.update(chunk)
                        target.write(chunk)
                if size != identity['bytes'] or value.hexdigest() != identity['sha256']:
                    (directory / name).unlink()
                    raise FirmwareError(f'{name} differs from the inventory')
                digests[name] = value.hexdigest()
    return digests


def record(firmware_plan: dict, inventory: dict, reset: dict) -> dict:
    """The firmware part of build.json: what flash-steps needs, without the tools."""
    archive = inventory['releases'][firmware_plan['release']]['archive']
    return {'release': firmware_plan['release'],
            'archive': {'file': archive['filename'], 'sha256': archive['sha256']},
            'images': {n: i['sha256'] for n, i in firmware_plan['images'].items()},
            'steps': firmware_plan['steps'], 'reset': reset,
            'fastboot_versions': firmware_plan['fastboot_versions']}


def check_set(directory: Path, value: dict, inventory: dict, policy: dict, stock_build: str) -> list[str]:
    """Problems with an image set's firmware: complete, byte-exact, in stock order."""
    problems = []
    expected = plan(inventory, policy)
    if expected['release'] != stock_build:
        problems.append(f'the inventory selects firmware {expected["release"]}, but the vendor files come from '
                        f'{stock_build}')
    if value.get('release') != expected['release']:
        problems.append(f'the set carries firmware {value.get("release")}, not {expected["release"]}')
    want = {n: i['sha256'] for n, i in expected['images'].items()}
    for name in sorted(set(value.get('images', {})) - set(want)):
        problems.append(f'{name} is not a firmware image of {expected["release"]}')
    for name, identity in sorted(expected['images'].items()):
        path = directory / name
        if value.get('images', {}).get(name) != identity['sha256']:
            problems.append(f'build.json records {name} with another hash than the inventory')
        if not path.is_file():
            problems.append(f'{name} is missing')
        elif path.stat().st_size != identity['bytes'] or bw.sha_file(path) != identity['sha256']:
            problems.append(f'{name} differs from the inventory')
    if value.get('steps') != expected['steps']:
        problems.append('the firmware flash steps differ from the stock order')
    if value.get('fastboot_versions') != expected['fastboot_versions']:
        problems.append('the recorded fastboot versions differ from the inventory')
    archive = inventory['releases'][expected['release']]['archive']
    if value.get('archive') != {'file': archive['filename'], 'sha256': archive['sha256']}:
        problems.append('the recorded factory package differs from the inventory')
    if set(value.get('reset', {})) != set(policy['reset']):
        problems.append('the reset images differ from the firmware policy')
    for name, image in policy['reset'].items():
        path = directory / image['image']
        data = path.read_bytes() if path.is_file() else b''
        if len(data) != image['bytes'] or data.count(0) != len(data):
            problems.append(f'{name} image is not the declared zeros')
        elif value.get('reset', {}).get(name) != {'file': image['image'], 'sha256': hashlib.sha256(data).hexdigest()}:
            problems.append(f'build.json records the {name} image wrongly')
    problems += anti_rollback_problems(inventory, expected['release'])
    return problems
