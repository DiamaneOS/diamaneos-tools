"""The install scripts an image set carries: flash-all.sh and flash-all.bat.

Run from the bootloader, they erase the phone and install the whole set: first
Fairphone's firmware of the release the vendor files come from, both slots of
each A/B partition in the stock order, then the OS and the empty data images.
They always write the firmware, as Fairphone's own flash script does, so the
phone must not already run a newer Fairphone release than the set carries.
"flash-steps" prints the commands for anything else (an update that keeps
data, only the images that changed).
"""
from __future__ import annotations

SH, BAT = 'flash-all.sh', 'flash-all.bat'
# What the bootloader must report before anything is written.
CHECKS = (('product', '{product}', 'This image set is for the {product}: connect that phone in fastboot mode.'),
          ('is-userspace', 'no', 'The phone is in fastbootd. Run "fastboot reboot bootloader" and start again.'),
          ('unlocked', 'yes', 'The bootloader is locked. Unlock it as Fairphone describes, then start again.'))
CRITICAL = ('Device critical unlocked: true',
            'The critical partitions are locked, and the firmware is written to them. Run "fastboot flashing '
            'unlock_critical" as Fairphone describes (it erases the phone), then start again.')


def commands(record: dict) -> list[list[str]]:
    """The fastboot arguments of a clean install, in order."""
    slot, carried = record['flash']['slot'], record.get('firmware')
    result = []
    if carried:
        result += [['flash', step['partition'], step['image']] for step in carried['steps']]
        result += [['flash', name, image['file']] for name, image in carried['reset'].items()]
    result += [['flash', f'{name}_{slot}', f'{name}.img'] for name in record['flash']['bootloader']]
    if record['flash']['logical']:
        result.append(['flash', 'super', 'super.img'])
    result += [['flash', name, image['file']] for name, image in record['wipe']['images'].items()]
    return result + [[f'--set-active={slot}'], ['reboot']]


def notes(record: dict) -> list[str]:
    carried = record.get('firmware')
    lines = [f'DiamaneOS {record["build_id"]} for the {record["product"]}: a clean install from the bootloader.',
             'This erases all data on the phone.']
    if carried:
        lines.append(f'It writes Fairphone\'s {carried["release"]} firmware first. Do not run it on a phone that '
                     'already runs a newer Fairphone release.')
    if record.get('never_lock'):
        lines.append('This build is signed with public test keys: keep the bootloader unlocked while it is installed.')
    return lines


def shell(record: dict) -> str:
    out = ['#!/bin/sh', *('# ' + line for line in notes(record)), 'set -eu', 'cd "$(dirname "$0")"', '',
           'fail() { echo "$1" >&2; exit 1; }',
           'command -v fastboot >/dev/null 2>&1 || fail "fastboot is not installed (Android platform-tools)."',
           # A value is on a line "name: value", which some bootloaders prefix with "(bootloader)".
           # Each reply is read to the end before it is filtered: a filter that stops early ends
           # fastboot mid-reply, and the bootloader then answers no further command.
           'value() { reply=$(fastboot getvar "$1" 2>&1) || true; '
           'printf "%s\\n" "$reply" | sed -n -e "s/^(bootloader) *//" -e "s/^$1: *//p" | head -n 1; }']
    for name, wanted, message in CHECKS:
        wanted, message = wanted.format(**record), message.format(**record)
        out.append(f'[ "$(value {name})" = "{wanted}" ] || fail \'{message}\'')
    if record.get('firmware'):
        out.append('info=$(fastboot oem device-info 2>&1) || true')
        out.append(f'printf "%s\\n" "$info" | grep -q "{CRITICAL[0]}" || fail \'{CRITICAL[1]}\'')
    out += ['if command -v sha256sum >/dev/null 2>&1; then sha256sum --quiet -c SHA256SUMS',
            'elif command -v shasum >/dev/null 2>&1; then shasum -a 256 --quiet -c SHA256SUMS',
            'else fail "Neither sha256sum nor shasum is installed, so the images cannot be checked."; fi', '']
    out += [f'echo "{line}"' for line in notes(record)]
    out += ['fastboot ' + ' '.join(arguments) for arguments in commands(record)]
    return '\n'.join(out) + '\n'


def batch(record: dict) -> str:
    out = ['@echo off', *('rem ' + line for line in notes(record)), 'setlocal', 'cd /d "%~dp0"', '',
           'where fastboot >nul 2>&1 || (echo fastboot is not installed ^(Android platform-tools^). & exit /b 1)']
    for name, wanted, message in CHECKS:
        wanted, message = wanted.format(**record), _escaped(message.format(**record))
        out.append(f'fastboot getvar {name} 2>&1 | findstr /r /c:"{name}: *{wanted}" >nul || (echo {message} & exit /b 1)')
    if record.get('firmware'):
        out.append(f'fastboot oem device-info 2>&1 | findstr /c:"{CRITICAL[0]}" >nul || '
                   f'(echo {_escaped(CRITICAL[1])} & exit /b 1)')
    out.append('')
    out += [f'echo {_escaped(line)}' for line in notes(record)]
    out += ['fastboot ' + ' '.join(arguments) + ' || exit /b 1' for arguments in commands(record)]
    return '\r\n'.join(out) + '\r\n'


def _escaped(text: str) -> str:
    """Text for a batch echo inside parentheses."""
    for character in '^&<>|()':
        text = text.replace(character, '^' + character)
    return text.replace('"', "'")


def scripts(record: dict) -> dict:
    return {SH: shell(record), BAT: batch(record)}
