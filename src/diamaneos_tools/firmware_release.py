"""The firmware release table the vendor image carries for fwrelease.

fwrelease (device repository, firmware/) hashes the booted slot's A/B firmware
partitions once per boot and reports the Fairphone release they all match,
"mixed" or "unknown" (ro.vendor.diamaneos.firmware_release, shown in Settings).
The phone cannot tell releases apart otherwise: the Qualcomm version strings
are the same in 16.100.0 and 16.111.0. This module writes the table it compares
against from config/fp6-firmware-inventory.json and the firmware_release block
of config/fp6-build.json, one line per partition and release:

    image <partition> <release> <bytes> <sha256>

<partition> has no slot suffix; <bytes> is the image length, the part of the
partition fwrelease hashes. The limits below are fwrelease's: a table outside
them would read "unknown" on the phone, so the build fails instead.
"""
import re

from .vendor import VendorError

# In the vendor image, covered by dm-verity; fwrelease reads this path.
TABLE_PATH = 'vendor/etc/diamaneos/firmware-releases.txt'
VERSION = re.compile(r'[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}')
PARTITION = re.compile(r'[a-z0-9_-]{1,32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_IMAGE_BYTES = 512 * 1024**2
MAX_PARTITIONS = 64
MAX_RELEASES = 64
MAX_TABLE_BYTES = 256 * 1024


def version(build, prefix):
    """16.111.0 for FP6.QREL.16.111.0."""
    if not isinstance(build, str) or not build.startswith(prefix) or not VERSION.fullmatch(build[len(prefix):]):
        raise VendorError('a firmware release is not named ' + prefix + 'N.N.N')
    return build[len(prefix):]


def ab_partition(entry):
    """The partition an A/B image is flashed to, without slot suffix."""
    partitions = entry.get('fastboot_partitions')
    if not isinstance(partitions, list) or len(partitions) != 2 or not all(isinstance(p, str) for p in partitions):
        return None
    base = partitions[0].removesuffix('_a')
    if partitions != [base + '_a', base + '_b'] or not PARTITION.fullmatch(base):
        return None
    return base


def table(inventory, config):
    """The table for every release in the inventory, newest release first."""
    prefix = config.get('release_prefix')
    excluded = config.get('not_checked')
    if not isinstance(prefix, str) or not prefix or not isinstance(excluded, dict):
        raise VendorError('firmware_release needs release_prefix and not_checked')
    for name, exclusion in excluded.items():
        if not isinstance(exclusion, dict) or not isinstance(exclusion.get('reason'), str) \
                or not exclusion['reason'].strip():
            raise VendorError('a firmware image is left unchecked without a reason')
        if set(exclusion) - {'reason', 'identical'} or not isinstance(exclusion.get('identical', False), bool):
            raise VendorError('an unchecked firmware image has unknown fields')
    releases = inventory.get('releases')
    if not isinstance(releases, dict) or not 0 < len(releases) <= MAX_RELEASES:
        raise VendorError('the firmware inventory has no or too many releases')
    images = {build: release['images']['firmware'] for build, release in releases.items()}
    names = {frozenset(firmware) for firmware in images.values()}
    if len(names) != 1:
        raise VendorError('the releases in the firmware inventory have different firmware images')
    names = set(next(iter(names)))
    if not set(excluded) <= names:
        raise VendorError('an unchecked firmware image is not in the inventory')
    for name, exclusion in excluded.items():
        # Left out because it tells no release apart; a change would make it.
        if exclusion.get('identical') and len({(f[name]['bytes'], f[name]['sha256']) for f in images.values()}) != 1:
            raise VendorError('an unchecked firmware image said to be identical differs between releases')
    checked = sorted(names - set(excluded))
    if not 0 < len(checked) <= MAX_PARTITIONS:
        raise VendorError('no or too many firmware images to check')
    partitions, versions, rows = {}, set(), []
    for build, firmware in images.items():
        release = version(build, prefix)
        number = tuple(int(n) for n in release.split('.'))
        if number in versions:
            raise VendorError('two firmware releases have the same version')
        versions.add(number)
        for name in checked:
            entry = firmware[name]
            # fwrelease compares raw partition bytes; a sparse image is not
            # what the partition holds.
            if entry.get('sparse', False) is not False:
                raise VendorError('a checked firmware image is sparse')
            partition = ab_partition(entry)
            if partition is None:
                raise VendorError('a checked firmware image is not flashed to one A/B partition')
            if partitions.setdefault(name, partition) != partition:
                raise VendorError('a firmware image goes to different partitions in different releases')
            size, digest = entry.get('bytes'), entry.get('sha256')
            if type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES or not isinstance(digest, str) \
                    or not SHA256.fullmatch(digest):
                raise VendorError('a checked firmware image has no valid size or SHA-256')
            rows.append((number, partition, release, size, digest))
    if len(set(partitions.values())) != len(partitions):
        raise VendorError('two checked firmware images go to the same partition')
    rows.sort(key=lambda row: (tuple(-n for n in row[0]), row[1]))
    text = ''.join(f'image {partition} {release} {size} {digest}\n' for _, partition, release, size, digest in rows)
    data = text.encode()
    if len(data) > MAX_TABLE_BYTES:
        raise VendorError('the firmware release table is too large')
    return data
