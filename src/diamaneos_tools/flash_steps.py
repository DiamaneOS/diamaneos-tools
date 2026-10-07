"""Print the fastboot commands for a verified DiamaneOS test build.

Never runs fastboot. Prints every image, or with --since only the images that
differ from an earlier build. --wipe adds the data wipe, done by flashing
empty images as Fairphone's factory package does (never fastboot -w or erase).

The image set carries Fairphone's firmware for the release its vendor files
come from. The firmware steps come first, in the stock order and on both
slots, and only when the phone runs older firmware: never older firmware than
the phone has. Say which firmware the phone runs with --phone-firmware (the
FP6 bootloader does not report it), or let --since take it from the earlier
build, and save the phone's bootloader state for --phone:

  { fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys
import textwrap

from . import build_workspace as bw
from . import firmware as fw
from .image_package import RECORD, check_sums

# The bootloader values flash-steps reads from "fastboot getvar all".
PHONE_KEYS = ('product', 'unlocked', 'is-userspace', 'version-bootloader', 'version-baseband')
# ... and from "fastboot oem device-info".
DEVICE_INFO = {'Device unlocked': 'device-unlocked', 'Device critical unlocked': 'critical-unlocked'}
VERSIONS = ('version-bootloader', 'version-baseband')
MODES = ('auto', 'skip', 'rewrite')


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


def parse_phone(text: str) -> dict:
    """The values flash-steps needs from saved "fastboot getvar all" (or single
    getvar) and "fastboot oem device-info" output."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('(bootloader)'):
            line = line[len('(bootloader)'):].strip()
        key, separator, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if not separator or (key not in PHONE_KEYS and key not in DEVICE_INFO):
            continue
        key = DEVICE_INFO.get(key, key)
        if key in values and values[key] != value:
            raise FlashError(f'the saved phone state gives {key} twice, with different values; save one phone\'s state')
        values[key] = value
    return values


def check_phone(phone: dict, record: dict, writes_firmware: bool) -> None:
    if phone.get('product') != record['product']:
        raise FlashError(f'the saved phone state is not a Fairphone 6 (product: {phone.get("product")!r})')
    if phone.get('unlocked') != 'yes':
        raise FlashError('the bootloader is locked; unlock it as Fairphone describes')
    if phone.get('is-userspace') != 'no':
        raise FlashError('the phone was not in the bootloader (fastbootd, or not reported); run "fastboot reboot '
                         'bootloader" and save its state again')
    if writes_firmware:
        if 'device-unlocked' not in phone or 'critical-unlocked' not in phone:
            raise FlashError('the saved phone state lacks the output of "fastboot oem device-info"')
        if phone['device-unlocked'] != 'true' or phone['critical-unlocked'] != 'true':
            raise FlashError('the critical partitions are locked, and the firmware steps write them: run "fastboot '
                             'flashing unlock_critical" as Fairphone describes (it wipes the phone)')


def release_of(value: str, what: str) -> tuple:
    try:
        return fw.release_key(value)
    except fw.FirmwareError as error:
        raise FlashError(f'{what}: {error}') from error


def firmware_decision(record: dict, previous: dict | None, phone: dict | None, stated: str | None,
                      mode: str) -> dict:
    """Whether the firmware steps run, and why. Never towards older firmware."""
    carried = record['firmware']
    shipped = carried['release']
    if mode == 'skip':
        return {'write': False, 'why': 'skip', 'release': None}
    sources = {}
    if stated is not None:
        release_of(stated, '--phone-firmware')
        sources['as you stated'] = stated
    earlier = (previous or {}).get('firmware')
    if earlier:
        sources['as the earlier build installed'] = earlier['release']
    if len(set(sources.values())) > 1:
        raise FlashError(f'--phone-firmware says {stated}, but the earlier build installed firmware '
                         f'{earlier["release"]}')
    release, source = next(((v, k) for k, v in sources.items()), (None, None))
    if phone is not None:
        missing = [k for k in VERSIONS if k not in phone]
        if missing:
            raise FlashError('the saved phone state lacks ' + ', '.join(missing) + '; save the whole output of '
                             '"fastboot getvar all"')
        reported = {k: phone[k] for k in VERSIONS}
        if any(reported.values()):
            recorded = carried['fastboot_versions'].get(release) if release else None
            if recorded != reported:
                raise FlashError(f'the phone reports version-bootloader {reported["version-bootloader"]!r} and '
                                 f'version-baseband {reported["version-baseband"]!r}, which the firmware inventory '
                                 f'does not record for {release or "any stated release"}: check which firmware the '
                                 'phone runs, and record these values from a phone running it before flashing firmware')
    if release is None:
        raise FlashError('Which firmware does the phone run? The FP6 bootloader does not report it. Give its stock '
                         'release with --phone-firmware (stock Android shows it in Settings > About phone > Build '
                         'number; after a DiamaneOS flash it is the firmware those flash steps installed), use '
                         '--since with the image set last flashed, or --no-firmware to leave the firmware as it is.')
    have, want = release_of(release, 'the phone firmware'), release_of(shipped, 'the carried firmware')
    if have > want:
        raise FlashError(f'the phone runs firmware {release}, newer than this build\'s {shipped}, and DiamaneOS never '
                         'installs older firmware than the phone has. Use a build with firmware of that release or '
                         'newer, or --no-firmware to flash only the OS (its vendor files come from ' + shipped + ').')
    same_bytes = earlier is None or earlier.get('images') == carried['images']
    if have == want and mode != 'rewrite' and same_bytes:
        return {'write': False, 'why': 'same', 'release': release, 'source': source}
    if phone is None:
        raise FlashError('the firmware steps need the phone\'s bootloader state: in the bootloader, run '
                         '"{ fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1" and pass --phone '
                         'phone.txt')
    why = 'older' if have < want else ('rewrite' if mode == 'rewrite' else 'changed')
    return {'write': True, 'why': why, 'release': release, 'source': source}


def firmware_text(record: dict, decision: dict) -> list[str]:
    shipped = record['firmware']['release']
    why, release, source = decision['why'], decision.get('release'), decision.get('source')
    if why == 'skip':
        text = (f'Firmware: left as it is (--no-firmware). This build\'s vendor files come from {shipped}, so the '
                'phone should run that firmware.')
    elif why == 'same':
        text = f'Firmware: the phone already runs {shipped} ({source}), so there are no firmware steps.'
    else:
        text = {'older': f'Firmware: the phone runs {release} ({source}), older than this build\'s {shipped}.',
                'rewrite': f'Firmware: the phone runs {release} ({source}); --rewrite-firmware writes it again.',
                'changed': f'Firmware: this build\'s {shipped} images differ from the earlier build\'s.'}[why]
        text += (' The firmware steps write it first, then zero the modem file system so that the modem rebuilds it '
                 'from its factory backup. The saved phone state shows an unlocked FP6 with unlocked critical '
                 'partitions in the bootloader; flash that phone. If a firmware step fails, keep the phone in the '
                 'bootloader and run the steps again from the first one.')
    return textwrap.wrap(text, 96)


def steps(directory: Path, record: dict, previous: dict | None = None, wipe: bool = False,
          report: dict | None = None, phone: dict | None = None, phone_firmware: str | None = None,
          firmware: str = 'auto') -> list[str]:
    if record.get('release') is not False or record.get('signing') != 'public-test-keys':
        raise FlashError('flash-steps only prints commands for test builds')
    failed = [c['id'] for c in (report or {}).get('checks', []) if c['status'] != 'PASS']
    if report is None:
        raise FlashError('this image set has not been verified; run "diamaneos build verify" first')
    if failed:
        raise FlashError('verification failed (' + ', '.join(failed) + '); do not flash this build')
    if firmware not in MODES:
        raise FlashError('unknown firmware mode ' + firmware)
    carried = record.get('firmware')
    decision = firmware_decision(record, previous, phone, phone_firmware, firmware) if carried else None
    writes_firmware = bool(decision and decision['write'])
    if phone is not None:
        check_phone(phone, record, writes_firmware)
    slot = record['flash']['slot']
    bootloader = changed(record, previous, record['flash']['bootloader'])
    logical = changed(record, previous, record['flash']['logical'])
    out = [f'DiamaneOS test build {record["build_id"]} ({record["product"]}, {record["variant"]}, '
           f'build number {record["build_number"]})', '', 'Safety rules:',
           '- This build is signed with public test keys. Keep the bootloader unlocked: never run',
           '  "fastboot flashing lock" or "fastboot flashing lock_critical" while it is installed.',
           '- Never run "fastboot -w" or "fastboot erase". A wipe flashes empty images instead.']
    if carried:
        out += [f'- The OS goes to slot {slot} only. Do not flash it to the other slot.',
                f'- Firmware: Fairphone\'s exact {carried["release"]} images, the release this build\'s vendor',
                '  files come from. As in Fairphone\'s own flash, each A/B firmware partition gets both slots.']
    else:
        out += [f'- Everything goes to slot {slot}. Do not flash the other slot.',
                f'- The phone\'s firmware must come from stock {record["stock_build"]}, the release this',
                '  build\'s vendor files come from.']
    if not record.get('reproducible', False):
        out.append('- Built from a modified tools checkout: not reproducible.')
    out.append('- Never install firmware older than the phone already runs.')
    if wipe:
        out += ['- This wipes all data on the phone.']
        if not record['wipe']['validated']:
            out += ['- The wipe by flashing empty images is not yet tested on a phone.']
    if writes_firmware and not carried.get('validated', False):
        out += ['- The firmware steps are not yet tested on a phone.']
    if wipe and carried:
        out += ['', 'First install (from stock or another OS): unlock the bootloader as Fairphone describes,',
                'including "fastboot flashing unlock_critical", which writing firmware needs.']
    elif wipe:
        out += ['', 'First install (from stock or another OS): unlock the bootloader as Fairphone',
                'describes, including "fastboot flashing unlock_critical", because the firmware is',
                f'written next. Then install Fairphone\'s {record["stock_build"]} factory package with',
                'Fairphone\'s instructions (it writes the firmware), and continue here.']
    if decision:
        out += [''] + firmware_text(record, decision)
    wipe_lines = []
    if wipe:
        if carried and decision['why'] == 'same':
            # The firmware's state partitions are reset with a wipe; with
            # firmware steps they come at their stock place among them.
            wipe_lines += [f'  fastboot flash {s["partition"]} {s["image"]}' for s in carried['steps']
                           if s.get('wipe_only')]
        wipe_lines += [f'  fastboot flash {name} {image["file"]}' for name, image in record['wipe']['images'].items()]
    if previous is None or bootloader or logical or wipe or writes_firmware:
        out += ['', 'Copy the whole image directory if you flash from another computer. Check the files,',
                'then put the phone in fastboot mode (hold Volume down while it starts, or run',
                '"adb reboot bootloader"):', '',
                f'  cd {shlex.quote(str(directory))}', '  sha256sum -c SHA256SUMS']
        if writes_firmware:
            out += [f'  fastboot flash {s["partition"]} {s["image"]}' for s in carried['steps']
                    if wipe or not s.get('wipe_only')]
            out += [f'  fastboot flash {name} {image["file"]}' for name, image in carried['reset'].items()]
        for name in bootloader:
            out.append(f'  fastboot flash {name}_{slot} {name}.img')
        if logical:
            out.append('  fastboot flash super super.img')
        out += wipe_lines
        out += [f'  fastboot --set-active={slot}', '  fastboot reboot']
        if logical:
            # A stalled super flash may already have written the new partition
            # layout, so the fallback writes every logical partition.
            out += ['', 'If flashing super stops after its first part, flash every logical partition',
                    'through fastbootd instead, then set the slot and reboot:', '', '  fastboot reboot fastboot']
            out += [f'  fastboot flash {name}_{slot} {name}.img' for name in record['flash']['logical']]
            out += ['  fastboot reboot bootloader']
            out += wipe_lines
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
    parser.add_argument('--since', help='only images that differ from this earlier image directory, the one '
                                        'last flashed to the phone')
    parser.add_argument('--wipe', action='store_true', help='also wipe all data (needed for a first install)')
    parser.add_argument('--phone', help='the phone\'s saved "fastboot getvar all" and "fastboot oem device-info" '
                                        'output, needed for firmware steps')
    parser.add_argument('--phone-firmware', metavar='BUILD',
                        help='the stock release of the firmware the phone runs, such as FP6.QREL.16.100.0')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--no-firmware', dest='firmware', action='store_const', const='skip', default='auto',
                       help='leave the phone\'s firmware as it is')
    group.add_argument('--rewrite-firmware', dest='firmware', action='store_const', const='rewrite',
                       help='write the firmware even if the phone runs the same release (never an older one)')
    args = parser.parse_args(argv)
    try:
        workspace = Path(args.workspace).expanduser().absolute() if args.workspace else bw.default_workspace()
        directory = resolve(args.images, workspace)
        record = load(directory)
        previous = load(Path(args.since).expanduser().absolute()) if args.since else None
        if previous is not None and previous['product'] != record['product']:
            raise FlashError('the earlier build is for another product')
        phone = parse_phone(Path(args.phone).expanduser().read_text(errors='replace')) if args.phone else None
        print('\n'.join(steps(directory, record, previous, args.wipe, verification(directory, record),
                              phone=phone, phone_firmware=args.phone_firmware, firmware=args.firmware)))
        return 0
    except (FlashError, OSError, ValueError, KeyError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2
