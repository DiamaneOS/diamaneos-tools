"""Print the fastboot commands for a verified DiamaneOS test build.

Never runs fastboot. Prints every image, or with --since only the images that
differ from an earlier build. --wipe adds the data wipe, done by flashing
empty images as Fairphone's factory package does (never fastboot -w or erase).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys

from . import build_workspace as bw
from .image_package import RECORD, check_sums


class FlashError(Exception):
    pass


def load(directory: Path) -> dict:
    record_path = directory / RECORD
    if not record_path.is_file():
        raise FlashError(f'{directory} is not a DiamaneOS image directory (no {RECORD})')
    if not check_sums(directory):
        raise FlashError(f'{directory} does not match its SHA256SUMS; do not flash it')
    return json.loads(record_path.read_bytes())


def verification(directory: Path, record: dict) -> dict | None:
    """The verify report for exactly this image set, or None."""
    report = directory.parent / (record['build_id'] + '.verify.json')
    if not report.is_file():
        return None
    value = json.loads(report.read_bytes())
    if value.get('sums_sha256') != bw.sha_file(directory / 'SHA256SUMS') or value.get('build_id') != record['build_id']:
        raise FlashError('the verification report belongs to another image set; run "diamaneos build verify" again')
    return value


def changed(record: dict, previous: dict | None, names) -> list[str]:
    if previous is None:
        return list(names)
    return [n for n in names if record['images'].get(n) != previous['images'].get(n)]


def steps(directory: Path, record: dict, previous: dict | None = None, wipe: bool = False,
          report: dict | None = None) -> list[str]:
    if record.get('release') is not False or record.get('signing') != 'public-test-keys':
        raise FlashError('flash-steps only prints commands for test builds')
    failed = [c['id'] for c in (report or {}).get('checks', []) if c['status'] != 'PASS']
    if report is None:
        raise FlashError('this image set has not been verified; run "diamaneos build verify" first')
    if failed:
        raise FlashError('verification failed (' + ', '.join(failed) + '); do not flash this build')
    slot = record['flash']['slot']
    bootloader = changed(record, previous, record['flash']['bootloader'])
    logical = changed(record, previous, record['flash']['logical'])
    out = [f'DiamaneOS test build {record["build_id"]} ({record["product"]}, {record["variant"]}, '
           f'build number {record["build_number"]})', '', 'Safety rules:',
           '- This build is signed with public test keys. Keep the bootloader unlocked: never run',
           '  "fastboot flashing lock" or "fastboot flashing lock_critical" while it is installed.',
           '- Never run "fastboot -w" or "fastboot erase". A wipe flashes empty images instead.',
           f'- Everything goes to slot {slot}. Do not flash the other slot.',
           f'- The phone\'s firmware must come from stock {record["stock_build"]}, the release this',
           '  build\'s vendor files come from.']
    if not record.get('reproducible', False):
        out.append('- Built from a modified tools checkout: not reproducible.')
    out.append('- Never install firmware older than the phone already runs.')
    if wipe:
        out += ['- This wipes all data on the phone.']
        if not record['wipe']['validated']:
            out += ['- The wipe by flashing empty images is not yet tested on a phone.']
        out += ['', 'First install (from stock or another OS): unlock the bootloader as Fairphone',
                'describes, including "fastboot flashing unlock_critical", because the firmware is',
                f'written next. Then install Fairphone\'s {record["stock_build"]} factory package with',
                'Fairphone\'s instructions (it writes the firmware), and continue here.']
    if previous is None or bootloader or logical or wipe:
        out += ['', 'Copy the whole image directory if you flash from another computer. Check the files,',
                'then put the phone in fastboot mode (hold Volume down while it starts, or run',
                '"adb reboot bootloader"):', '',
                f'  cd {shlex.quote(str(directory))}', '  sha256sum -c SHA256SUMS']
        for name in bootloader:
            out.append(f'  fastboot flash {name}_{slot} {name}.img')
        if logical:
            out.append('  fastboot flash super super.img')
        if wipe:
            for name, image in record['wipe']['images'].items():
                out.append(f'  fastboot flash {name} {image["file"]}')
        out += [f'  fastboot --set-active={slot}', '  fastboot reboot']
        if logical:
            # A stalled super flash may already have written the new partition
            # layout, so the fallback writes every logical partition.
            out += ['', 'If flashing super stops after its first part, flash every logical partition',
                    'through fastbootd instead, then set the slot and reboot:', '', '  fastboot reboot fastboot']
            out += [f'  fastboot flash {name}_{slot} {name}.img' for name in record['flash']['logical']]
            out += ['  fastboot reboot bootloader']
            if wipe:
                out += [f'  fastboot flash {name} {image["file"]}' for name, image in record['wipe']['images'].items()]
            out += [f'  fastboot --set-active={slot}', '  fastboot reboot']
    else:
        out += ['', 'Nothing to flash: every image equals the earlier build.']
    return out


def resolve(value: str | None, workspace: Path) -> Path:
    if value:
        return Path(value).expanduser().absolute()
    latest = workspace / 'images' / 'latest'
    if not latest.is_symlink():
        raise FlashError(f'no packaged build in {workspace}; run "diamaneos build all" first')
    return latest.resolve()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='diamaneos flash-steps', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('images', nargs='?', help='image directory (default: the latest build in the workspace)')
    parser.add_argument('--workspace', help='build directory (default: $DIAMANEOS_WORKSPACE or ~/diamaneos-build)')
    parser.add_argument('--since', help='only images that differ from this earlier image directory')
    parser.add_argument('--wipe', action='store_true', help='also wipe all data (needed for a first install)')
    args = parser.parse_args(argv)
    try:
        workspace = Path(args.workspace).expanduser().absolute() if args.workspace else bw.default_workspace()
        directory = resolve(args.images, workspace)
        record = load(directory)
        previous = load(Path(args.since).expanduser().absolute()) if args.since else None
        if previous is not None and previous['product'] != record['product']:
            raise FlashError('the earlier build is for another product')
        print('\n'.join(steps(directory, record, previous, args.wipe, verification(directory, record))))
        return 0
    except (FlashError, OSError, ValueError, KeyError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2
