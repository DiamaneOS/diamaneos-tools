"""The install scripts an image set carries: flash-all.sh and flash-all.bat.

Run from the bootloader, they erase the phone and install the whole set: first
the device maker's firmware of the release the vendor files come from, both
slots of each A/B partition in the stock order, then the boot images. The phone
then restarts into fastbootd for the logical partitions and returns to the
bootloader for the empty data images.
They always write the firmware, as the stock flash script does, so the phone
must not already run a newer stock release than the set carries.
A set signed with private keys also installs its AVB public key and stops in
the bootloader, so the user can lock it before Android first starts. A test
build does so only when run with the argument "lock", after a warning and a
typed yes.
"flash-steps" prints the commands for anything else (an update that keeps
data, only the images that changed). The device's name, maker and keys come
from the "device" block of the build profile, which build.json carries.
"""
from __future__ import annotations

SH, BAT = 'flash-all.sh', 'flash-all.bat'
# Where the user learns to unlock the bootloader and the critical partitions.
INSTALL_GUIDE = 'https://diamaneos.de/install/cli/'
# What the bootloader must report before anything is written.
CHECKS = (('product', '{product}',
           'This image set is for the {name} ({product}). Connect that phone in fastboot mode.'),
          ('is-userspace', 'no', 'The phone is in fastbootd. Run "fastboot reboot bootloader" and start again.'),
          ('unlocked', 'yes', 'The bootloader is locked. Unlock it as {guide} describes, then start again.'))
# Printed again when the script ends, because the lock is the user's next step. It
# follows the stock relock order, which leaves no boot between the two locks, and
# checks before each lock that the phone can be unlocked again.
LOCK = ('To lock, first check that fastboot flashing get_unlock_ability reports 1, or stop there. Then run fastboot '
        'flashing lock_critical and confirm on the phone. It wipes the phone and restarts. Hold Volume down while it '
        'restarts, so that it comes back to the bootloader: Android must not start before the second lock. Then '
        'fastboot flashing get_unlock_ability must report 1 again, or stop there. Then run fastboot flashing lock '
        'and confirm on the phone.')
# A test build is signed with the public test keys. It carries their AVB key and
# locks only on request: the argument "lock", this warning and a typed yes.
TEST_KEYS = 'public-test-keys'
LOCK_ARGUMENT = 'lock'
TEST_KEY_LOCK = ('WARNING: this build is signed with the public test keys, which anyone can download. A phone locked '
                 'with it accepts any image or update signed with them, so the lock does not protect it. Lock it '
                 'only for a test. Then unlock it again. To keep the phone locked, install a release or a build '
                 'signed with your own keys.')
# Before the lock of a test build, which keeps rollback index 0 and must stay unlockable.
TEST_KEY_BEFORE = ('If the phone ran a locked build before, first run fastboot oem reset-rollback, or the test build '
                   'will not start once locked. Keep OEM unlocking on in the setup, so that you can unlock the phone '
                   'again.')
ASK = 'Type yes to install the public test key, so that you can lock the bootloader after the install: '
NOT_CONFIRMED = 'Not confirmed. Nothing was written.'
USAGE = f'Run the script without arguments, or with {LOCK_ARGUMENT} to lock the bootloader after the install.'
# The restarts between the bootloader and fastbootd: what "getvar is-userspace"
# reports once the phone is there, and what the scripts call it.
MODES = {('reboot', 'fastboot'): ('yes', 'fastbootd'), ('reboot-bootloader',): ('no', 'the bootloader')}
SUPER_EMPTY = 'super_empty.img'
# An update the OS left pending is cancelled in the bootloader before the boot
# images, as fastboot flashall and fastboot.js do, and only when the bootloader
# reports one.
CANCEL = ['snapshot-update', 'cancel']
STUCK = ('The phone did not reach $2. Hold {bootloader_keys} until it restarts into the bootloader, then run '
         'this script again.')
CRITICAL = ('Device critical unlocked: true',
            'The critical partitions are locked, and the firmware is written to them. Run "fastboot flashing '
            'unlock_critical" as {guide} describes (it erases the phone), then start again.')


def fields(record: dict) -> dict:
    """The values the messages name: the product, the device profile's name, maker and keys, and the guide."""
    return dict(record['device'], product=record['product'], guide=INSTALL_GUIDE)


def commands(record: dict, lock: bool = False) -> list[list[str]]:
    """The fastboot arguments of a clean install, in order. lock: a test build
    installs its public test key and stops in the bootloader too."""
    if lock and not test_key(record):
        raise ValueError('Only a test build that carries the public test key locks on request')
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
    key = custom_key(record) or (test_key(record) if lock else None)
    if key:
        return result + [['flash', 'avb_custom_key', key], [f'--set-active={slot}'], ['reboot-bootloader']]
    return result + [[f'--set-active={slot}'], ['reboot']]


def logical(record: dict, wipe: bool = True) -> list[list[str]]:
    """Every logical partition, written in fastbootd on empty super metadata.

    The bootloader can stall while it writes super.img. Recovery, which holds
    fastbootd, is written before this. A wipe clears misc first: an unlock leaves
    an erase request there, which recovery runs instead of fastbootd.
    """
    names, slot = record['flash']['logical'], record['flash']['slot']
    if not names:
        return []
    misc = []
    if wipe:
        image = record['wipe']['images'].get('misc')
        if not image:
            raise ValueError('A set that installs through fastbootd requires a misc wipe image')
        misc = [['flash', 'misc', image['file']]]
    return (misc + [[f'--set-active={slot}'], ['reboot', 'fastboot'], ['wipe-super', SUPER_EMPTY]]
            + [['flash', f'{name}_{slot}', f'{name}.img'] for name in names] + [['reboot-bootloader']])


def script_steps(record: dict, lock: bool = False) -> list[tuple]:
    """Each command, and the mode the scripts then wait for: after a switch
    between the bootloader and fastbootd that more commands follow."""
    result = commands(record, lock)
    return [(arguments, MODES.get(tuple(arguments)) if i < len(result) - 1 else None)
            for i, arguments in enumerate(result)]


def custom_key(record: dict) -> str | None:
    """The AVB public key file a set signed with private keys installs, or None."""
    if record.get('never_lock') is not False or record.get('signing') == TEST_KEYS:
        return None
    return safe_key(record)


def test_key(record: dict) -> str | None:
    """The public test key file a test build carries, which it installs only on request, or None."""
    if record.get('signing') != TEST_KEYS or record.get('never_lock') is not True or 'avb_custom_key' not in record:
        return None
    return safe_key(record)


def safe_key(record: dict) -> str:
    key = (record.get('avb_custom_key') or {}).get('file')
    if not isinstance(key, str) or not key or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-' for c in key) or key.startswith('.'):
        raise ValueError('A lockable set requires a safe AVB public key file')
    return key


def notes(record: dict) -> list[str]:
    carried, device = record.get('firmware'), fields(record)
    lines = [f'DiamaneOS {record["build_id"]} for the {device["name"]}: a clean install from the bootloader.',
             'This erases all data on the phone.']
    if carried:
        lines.append(f'It writes {device["maker"]}\'s {carried["release"]} firmware first. Do not run it on a phone '
                     f'that already runs a newer {device["maker"]} release.')
    if record['flash']['logical']:
        lines.append('The phone restarts into fastbootd for the system partitions, then back into the bootloader.')
    if record.get('never_lock'):
        lines.append('This build is signed with public test keys: keep the bootloader unlocked while it is installed.')
        if test_key(record):
            lines.append(f'To lock it anyway, run the script with the argument {LOCK_ARGUMENT}. It warns and asks '
                         'first.')
    elif custom_key(record):
        lines.append('It installs the signing key and stops in the bootloader. ' + LOCK)
    return lines


def shell(record: dict) -> str:
    values = fields(record)
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
           f'[ $n -lt 90 ] || fail "{STUCK.format(**values)}"; sleep 2; done; }}']
    for name, wanted, message in CHECKS:
        wanted, message = wanted.format(**values), message.format(**values)
        out.append(f'[ "$(value {name})" = "{wanted}" ] || fail \'{message}\'')
    if record.get('firmware'):
        out.append('info=$(fastboot oem device-info 2>&1) || true')
        out.append(f'printf "%s\\n" "$info" | grep -q "{CRITICAL[0]}" || fail \'{CRITICAL[1].format(**values)}\'')
    if test_key(record):
        # Asked before the first write, so the install then runs on its own.
        out += [f'case "$*" in \'\'|{LOCK_ARGUMENT}) ;; *) fail "{USAGE}" ;; esac',
                'lock=no', f'if [ "${{1:-}}" = {LOCK_ARGUMENT} ]; then', f'echo "{TEST_KEY_LOCK}"',
                f"printf '%s' '{ASK}'", 'read -r answer || answer=', f'[ "$answer" = yes ] || fail "{NOT_CONFIRMED}"',
                'lock=yes', 'fi']
    out += ['if command -v sha256sum >/dev/null 2>&1; then sha256sum --quiet -c SHA256SUMS',
            'elif command -v shasum >/dev/null 2>&1; then shasum -a 256 --quiet -c SHA256SUMS',
            'else fail "Neither sha256sum nor shasum is installed, so the images cannot be checked."; fi', '']
    out += [f'echo "{line}"' for line in notes(record)]

    def run(steps):
        lines = []
        for arguments, mode in steps:
            if arguments == CANCEL:
                lines += ['status=$(value snapshot-update-status)',
                          'if [ -n "$status" ] && [ "$status" != none ]; then']
            lines.append('fastboot ' + ' '.join(arguments))
            if arguments == [f'--set-active={record["flash"]["slot"]}']:
                lines.append('checkslot ' + record['flash']['slot'])
            if arguments == CANCEL:
                lines.append('fi')
            if mode:
                lines.append('waitfor {} "{}"'.format(*mode))
        return lines
    steps = script_steps(record)
    if test_key(record):
        # The same install. Only when confirmed, it ends with the key and the lock text.
        locked = script_steps(record, lock=True)
        out += run(steps[:-2]) + ['if [ "$lock" = yes ]; then'] + run(locked[-3:])
        out += [f'echo "{TEST_KEY_LOCK}"', f'echo "{TEST_KEY_BEFORE}"', f'echo "{LOCK}"', 'else']
        out += run(steps[-2:]) + ['fi']
    else:
        out += run(steps)
    if custom_key(record):
        out.append(f'echo "{LOCK}"')
    return '\n'.join(out) + '\n'


def batch(record: dict) -> str:
    # Windows PowerShell gets only its own modules: the PSModulePath of a PowerShell 7 window, which
    # cmd passes on, hides Get-FileHash from it.
    out = ['@echo off', *('rem ' + line for line in notes(record)), 'setlocal',
           'set "PSModulePath=%SystemRoot%\\system32\\WindowsPowerShell\\v1.0\\Modules"', 'cd /d "%~dp0"', '',
           'where fastboot >nul 2>&1 || (echo fastboot is not installed ^(Android platform-tools^). & exit /b 1)']
    values = fields(record)
    for name, wanted, message in CHECKS:
        wanted, message = wanted.format(**values), _escaped(message.format(**values))
        # The second filter drops a longer value, which findstr matches too ("FP6X" for "FP6").
        out.append(f'fastboot getvar {name} 2>&1 | findstr /r /c:"{name}: *{wanted}" | findstr /v /r '
                   f'/c:"{name}: *{wanted}[0-9A-Za-z_.]" /c:"{name}: *{wanted}-" >nul || (echo {message} & exit /b 1)')
    if record.get('firmware'):
        out.append(f'fastboot oem device-info 2>&1 | findstr /c:"{CRITICAL[0]}" >nul || '
                   f'(echo {_escaped(CRITICAL[1].format(**values))} & exit /b 1)')
    if test_key(record):
        out += [f'if not "%~1"=="" if not "%~1"=="{LOCK_ARGUMENT}" (echo {_escaped(USAGE)} & exit /b 1)',
                f'if not "%~2"=="" (echo {_escaped(USAGE)} & exit /b 1)',
                'set "lock="', f'if "%~1"=="{LOCK_ARGUMENT}" (call :confirm || exit /b 1)',
                f'if "%~1"=="{LOCK_ARGUMENT}" set "lock=yes"']
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

    def run(steps):
        lines = []
        for arguments, mode in steps:
            if arguments == CANCEL:
                lines.append('fastboot getvar snapshot-update-status 2>&1 | findstr /r '
                             '/c:"snapshot-update-status: *[a-z]" | findstr /v /r /c:"snapshot-update-status: *none" '
                             '>nul || goto no_snapshot')
            lines.append('fastboot ' + ' '.join(arguments) + ' || exit /b 1')
            if arguments == [f'--set-active={record["flash"]["slot"]}']:
                lines.append('call :checkslot ' + record['flash']['slot'] + ' || exit /b 1')
            if arguments == CANCEL:
                lines.append(':no_snapshot')
            if mode:
                lines.append('call :waitfor {} "{}" || exit /b 1'.format(*mode))
        return lines
    steps = script_steps(record)
    if test_key(record):
        locked = script_steps(record, lock=True)
        out += run(steps[:-2]) + ['if not "%lock%"=="yes" goto unlocked'] + run(locked[-3:])
        out += [f'echo {_escaped(TEST_KEY_LOCK)}', f'echo {_escaped(TEST_KEY_BEFORE)}', f'echo {_escaped(LOCK)}',
                'exit /b 0', ':unlocked']
        out += run(steps[-2:])
    else:
        out += run(steps)
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
            'set /a n+=1', f'if %n% geq 90 (echo {_escaped(STUCK.format(**values).replace("$2", "%~2"))} & exit /b 1)',
            'ping -n 3 127.0.0.1 >nul', 'goto waitfor_poll']
    if test_key(record):
        # Delayed expansion reads the answer after parsing, so no typed character can run as a command.
        out += ['', ':confirm', f'echo {_escaped(TEST_KEY_LOCK)}', 'setlocal EnableDelayedExpansion', 'set "answer="',
                f'set /p "answer={ASK}"', 'if "!answer!"=="yes" (endlocal & exit /b 0)', 'endlocal',
                f'echo {_escaped(NOT_CONFIRMED)}', 'exit /b 1']
    return '\r\n'.join(out) + '\r\n'


def _escaped(text: str) -> str:
    """Text for a batch echo inside parentheses."""
    for character in '^&<>|()':
        text = text.replace(character, '^' + character)
    return text.replace('"', "'")


def scripts(record: dict) -> dict:
    return {SH: shell(record), BAT: batch(record)}
