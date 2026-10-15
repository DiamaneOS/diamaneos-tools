"""The install scripts an image set carries: flash-all.sh and flash-all.bat.

Run from the bootloader, they erase the phone and install the whole set: first
Fairphone's firmware of the release the vendor files come from, both slots of
each A/B partition in the stock order, then the boot images. The phone then
restarts into fastbootd for the logical partitions and returns to the
bootloader for the empty data images.
They always write the firmware, as Fairphone's own flash script does, so the
phone must not already run a newer Fairphone release than the set carries.
A set signed with private keys also installs its AVB public key and stops in
the bootloader, so the user can lock it before Android first starts.
"flash-steps" prints the commands for anything else (an update that keeps
data, only the images that changed).
"""
from __future__ import annotations

SH, BAT = 'flash-all.sh', 'flash-all.bat'
# What the bootloader must report before anything is written.
CHECKS = (('product', '{product}', 'This image set is for the {product}: connect that phone in fastboot mode.'),
          ('is-userspace', 'no', 'The phone is in fastbootd. Run "fastboot reboot bootloader" and start again.'),
          ('unlocked', 'yes', 'The bootloader is locked. Unlock it as Fairphone describes, then start again.'))
# Printed again when the script ends, because the lock is the user's next step.
LOCK = ('To lock, run fastboot flashing get_unlock_ability (it must report 1), then fastboot flashing lock, '
        'and confirm on the phone. Do not start Android before the lock. Never lock the critical partitions.')
# The restarts between the bootloader and fastbootd: what "getvar is-userspace"
# reports once the phone is there, and what the scripts call it.
MODES = {('reboot', 'fastboot'): ('yes', 'fastbootd'), ('reboot-bootloader',): ('no', 'the bootloader')}
SUPER_EMPTY = 'super_empty.img'
# An update the OS left pending is cancelled in the bootloader before the boot
# images, as fastboot flashall and fastboot.js do, and only when the bootloader
# reports one.
CANCEL = ['snapshot-update', 'cancel']
STUCK = ('The phone did not reach $2. Hold Power and Volume down until it restarts into the bootloader, then run '
         'this script again.')
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
    if record['flash']['logical']:
        result.append(CANCEL)
    result += [['flash', f'{name}_{slot}', f'{name}.img'] for name in record['flash']['bootloader']]
    result += logical(record)
    result += [['flash', name, image['file']] for name, image in record['wipe']['images'].items()]
    key = custom_key(record)
    if key:
        return result + [['flash', 'avb_custom_key', key], [f'--set-active={slot}'], ['reboot-bootloader']]
    return result + [[f'--set-active={slot}'], ['reboot']]


def logical(record: dict) -> list[list[str]]:
    """Every logical partition, written in fastbootd on empty super metadata.

    The bootloader can stall while it writes super.img. Recovery, which holds
    fastbootd, is written before this.
    """
    names, slot = record['flash']['logical'], record['flash']['slot']
    if not names:
        return []
    return ([[f'--set-active={slot}'], ['reboot', 'fastboot'], ['wipe-super', SUPER_EMPTY]]
            + [['flash', f'{name}_{slot}', f'{name}.img'] for name in names] + [['reboot-bootloader']])


def script_steps(record: dict) -> list[tuple]:
    """Each command, and the mode the scripts then wait for: after a switch
    between the bootloader and fastbootd that more commands follow."""
    result = commands(record)
    return [(arguments, MODES.get(tuple(arguments)) if i < len(result) - 1 else None)
            for i, arguments in enumerate(result)]


def custom_key(record: dict) -> str | None:
    """The AVB public key file a set signed with private keys installs, or None."""
    if record.get('never_lock') is not False:
        return None
    key = (record.get('avb_custom_key') or {}).get('file')
    if not isinstance(key, str) or not key or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-' for c in key) or key.startswith('.'):
        raise ValueError('A lockable set requires a safe AVB public key file')
    return key


def notes(record: dict) -> list[str]:
    carried = record.get('firmware')
    lines = [f'DiamaneOS {record["build_id"]} for the {record["product"]}: a clean install from the bootloader.',
             'This erases all data on the phone.']
    if carried:
        lines.append(f'It writes Fairphone\'s {carried["release"]} firmware first. Do not run it on a phone that '
                     'already runs a newer Fairphone release.')
    if record['flash']['logical']:
        lines.append('The phone restarts into fastbootd for the system partitions, then back into the bootloader.')
    if record.get('never_lock'):
        lines.append('This build is signed with public test keys: keep the bootloader unlocked while it is installed.')
    elif custom_key(record):
        lines.append('It installs the signing key and stops in the bootloader. ' + LOCK)
    return lines


def shell(record: dict) -> str:
    out = ['#!/bin/sh', *('# ' + line for line in notes(record)), 'set -eu', 'cd "$(dirname "$0")"', '',
           'fail() { echo "$1" >&2; exit 1; }',
           'command -v fastboot >/dev/null 2>&1 || fail "fastboot is not installed (Android platform-tools)."',
           # A value is on a line "name: value", which some bootloaders prefix with "(bootloader)".
           # Each reply is read to the end before it is filtered: a filter that stops early ends
           # fastboot mid-reply, and the bootloader then answers no further command.
           'value() { reply=$(fastboot getvar "$1" 2>&1) || true; '
           'printf "%s\\n" "$reply" | sed -n -e "s/^(bootloader) *//" -e "s/^$1: *//p" | head -n 1; }',
           'checkslot() { reply=$(fastboot getvar current-slot 2>&1) || fail "Cannot verify the selected slot."; '
           'observed=$(printf "%s\\n" "$reply" | sed -n -e "s/^(bootloader) *//" '
           '-e "s/^current-slot: *//p" | tr -d "\\r"); '
           '[ "$observed" = "$1" ] || fail "The active slot does not match this image set."; }',
           # fastboot itself waits while no phone is connected, so this counts only the
           # answers from the mode the phone is leaving.
           'waitfor() { echo "Waiting for $2."; n=0; until [ "$(value is-userspace)" = "$1" ]; do n=$((n + 1)); '
           f'[ $n -lt 90 ] || fail "{STUCK}"; sleep 2; done; }}']
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
    for arguments, mode in script_steps(record):
        if arguments == CANCEL:
            out += ['status=$(value snapshot-update-status)', 'if [ -n "$status" ] && [ "$status" != none ]; then']
        out.append('fastboot ' + ' '.join(arguments))
        if arguments == [f'--set-active={record["flash"]["slot"]}']:
            out.append('checkslot ' + record['flash']['slot'])
        if arguments == CANCEL:
            out.append('fi')
        if mode:
            out.append('waitfor {} "{}"'.format(*mode))
    if custom_key(record):
        out.append(f'echo "{LOCK}"')
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
    out += ['where powershell >nul 2>&1 || (echo PowerShell is required to check SHA256SUMS. & exit /b 1)',
            'powershell -NoProfile -NonInteractive -Command "$ErrorActionPreference=\'Stop\'; $seen=@{}; '
            'Get-Content -LiteralPath \'SHA256SUMS\' | ForEach-Object { '
            'if ($_ -notmatch \'^([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$\') { throw \'Malformed checksums\' }; '
            '$digest=$Matches[1]; $name=$Matches[2]; '
            'if ($seen.ContainsKey($name)) { throw \'Duplicate checksum\' }; $seen[$name]=$true; '
            'if ((Get-FileHash -Algorithm SHA256 -LiteralPath $name).Hash.ToLowerInvariant() -ne $digest) '
            '{ throw \'Checksum mismatch\' } }; if ($seen.Count -eq 0) { throw \'No checksums\' }" || exit /b 1']
    out.append('')
    out += [f'echo {_escaped(line)}' for line in notes(record)]
    for arguments, mode in script_steps(record):
        if arguments == CANCEL:
            out.append('fastboot getvar snapshot-update-status 2>&1 | findstr /r /c:"snapshot-update-status: *[a-z]" '
                       '| findstr /v /r /c:"snapshot-update-status: *none" >nul || goto no_snapshot')
        out.append('fastboot ' + ' '.join(arguments) + ' || exit /b 1')
        if arguments == [f'--set-active={record["flash"]["slot"]}']:
            out.append('call :checkslot ' + record['flash']['slot'] + ' || exit /b 1')
        if arguments == CANCEL:
            out.append(':no_snapshot')
        if mode:
            out.append('call :waitfor {} "{}" || exit /b 1'.format(*mode))
    if custom_key(record):
        out.append(f'echo {_escaped(LOCK)}')
    out += ['exit /b 0', '', ':checkslot',
            'powershell -NoProfile -NonInteractive -Command "$reply=@(& fastboot getvar current-slot 2>&1); '
            'if ($LASTEXITCODE -ne 0) { exit 1 }; '
            '$slots=@($reply | ForEach-Object { $_.ToString().Trim() } | '
            'Where-Object { $_ -match \'^(\\(bootloader\\)\\s*)?current-slot:\' }); '
            'if ($slots.Count -ne 1 -or $slots[0] -notmatch '
            '\'^(\\(bootloader\\)\\s*)?current-slot:\\s*%1$\') { exit 1 }" || exit /b 1', 'exit /b 0',
            '', ':waitfor', 'echo Waiting for %~2.', 'set n=0', ':waitfor_poll',
            'fastboot getvar is-userspace 2>&1 | findstr /r /c:"is-userspace: *%1" >nul && exit /b 0',
            'set /a n+=1', f'if %n% geq 90 (echo {_escaped(STUCK.replace("$2", "%~2"))} & exit /b 1)',
            'ping -n 3 127.0.0.1 >nul', 'goto waitfor_poll']
    return '\r\n'.join(out) + '\r\n'


def _escaped(text: str) -> str:
    """Text for a batch echo inside parentheses."""
    for character in '^&<>|()':
        text = text.replace(character, '^' + character)
    return text.replace('"', "'")


def scripts(record: dict) -> dict:
    return {SH: shell(record), BAT: batch(record)}
