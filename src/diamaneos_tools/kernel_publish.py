"""Copy a kernel build into a checkout of the published kernel prebuilts.

diamaneos kernel publish --run RUN --to CHECKOUT copies the candidate of one
passed kernel build (Image, dtbo.img, dtbs/, modules/, the board makefiles and
the module blocklists) into a device_fairphone_FP6-kernels checkout. It checks
every file against the run's artifacts.json, refuses private key material and
strings that name the build host, and updates the README's "This build" lines.
It never commits or pushes: review the change and commit it yourself.
"""
import argparse
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

from .vendor_extract import sha

TOOLS_URL = 'https://github.com/DiamaneOS/diamaneos-tools'
# Every path a kernel candidate may contain; anything else is refused.
ALLOWED = (re.compile(r'Image'), re.compile(r'dtbo\.img'), re.compile(r'dtbs/[A-Za-z0-9_.,+-]+\.dtb'),
           re.compile(r'modules/[A-Za-z0-9_.,+-]+\.ko'), re.compile(r'BoardConfigKernel\.mk'),
           re.compile(r'device-kernel\.mk'), re.compile(r'[a-z_]+-modules\.blocklist'))
MANAGED_FILES = ('Image', 'dtbo.img', 'BoardConfigKernel.mk', 'device-kernel.mk')
MANAGED_DIRS = ('dtbs', 'modules')
BLOCKLIST = re.compile(r'[a-z_]+-modules\.blocklist')
PRIVATE_KEY_MARKERS = (b'PRIVATE KEY-----', b'PRIVATE KEY BLOCK-----')
HOST_PATHS = (b'/var/lib/', b'/home/', b'/Users/')
# Names too generic to search binaries for.
GENERIC_NAMES = {'root', 'localhost', 'localhost.localdomain'}
RUN_NAME = re.compile(r'([0-9]{8})T[0-9]{6}Z(?:-[0-9]+)?')
SHA1 = re.compile(r'[0-9a-f]{40}')


class PublishError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PublishError(message)


def read_json(path, limit=16 * 1024 * 1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit, 'missing or invalid ' + path.name)
    return json.loads(path.read_bytes())


def host_strings(extra=()):
    """Strings that would identify the build machine: this user and host name."""
    names = set(extra)
    try:
        names.add(getpass.getuser())
    except (OSError, KeyError):
        pass
    hostname = socket.gethostname()
    names |= {hostname, hostname.split('.')[0]}
    return sorted(n for n in names if len(n) >= 3 and n.lower() not in GENERIC_NAMES)


def scan(path, names):
    """Problems in one file: private key material or host-identifying strings."""
    data = path.read_bytes()
    problems = [path.name + ' contains private key material' for m in PRIVATE_KEY_MARKERS if m in data][:1]
    problems += [path.name + ' contains the host path ' + p.decode() for p in HOST_PATHS if p in data]
    problems += [path.name + ' contains the host string ' + repr(n) for n in names if n.encode() in data]
    return problems


def candidate_files(run):
    """The verified candidate: relative path -> file, exactly as artifacts.json lists it."""
    result = read_json(run / 'result.json')
    require(result.get('status') == 'PASS', 'the kernel run did not pass')
    inventory_path = run / 'artifacts.json'
    require(sha(inventory_path) == result.get('inventory_sha256'), 'artifacts.json does not match the run result')
    inventory = read_json(inventory_path)
    require(isinstance(inventory, list) and inventory, 'artifacts.json is empty')
    candidate = run / 'candidate'
    require(candidate.is_dir() and not candidate.is_symlink(), 'the kernel run has no candidate')
    records = {}
    for row in inventory:
        require(isinstance(row, dict) and set(row) == {'path', 'bytes', 'sha256'}, 'invalid artifacts.json entry')
        require(any(p.fullmatch(row['path']) for p in ALLOWED), 'unexpected file in the kernel build: ' + str(row['path']))
        require(row['path'] not in records, 'duplicate artifact: ' + row['path'])
        records[row['path']] = row
    present = {}
    for path in sorted(candidate.rglob('*')):
        rel = path.relative_to(candidate).as_posix()
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), 'unexpected file type in the candidate: ' + rel)
        if path.is_file():
            present[rel] = path
    require(set(present) == set(records), 'the candidate and artifacts.json list different files: ' +
            ', '.join(sorted(set(present) ^ set(records))[:5]))
    for rel, path in present.items():
        require(path.stat().st_size == records[rel]['bytes'] and sha(path) == records[rel]['sha256'],
                'candidate file differs from artifacts.json: ' + rel)
    require(set(MANAGED_FILES) <= set(present), 'the candidate lacks ' + ', '.join(sorted(set(MANAGED_FILES) - set(present))))
    modules = sum(1 for rel in present if rel.startswith('modules/'))
    dtbs = sum(1 for rel in present if rel.startswith('dtbs/'))
    require(modules == result.get('module_count') and dtbs == result.get('dtb_count'),
            'the candidate does not match the module and device tree counts of its result')
    return result, records, present


def describe(result, run):
    """The README's "This build" lines for this run."""
    for key in ('repository', 'source_commit', 'linux_version', 'tools', 'config_profile'):
        require(result.get(key), 'the kernel run does not record ' + key + '; build it again with these tools')
    tools = result['tools']
    require(isinstance(tools, dict) and isinstance(tools.get('commit'), str) and SHA1.fullmatch(tools['commit'])
            and tools.get('clean') is True, 'the kernel run was built from a modified tools checkout')
    require(SHA1.fullmatch(result['source_commit']), 'invalid source commit in the kernel run')
    require(str(result['repository']).startswith('https://'), 'invalid source repository in the kernel run')
    match = RUN_NAME.fullmatch(run.name)
    built = (datetime.datetime.strptime(match.group(1), '%Y%m%d').date() if match else
             datetime.datetime.fromtimestamp((run / 'result.json').stat().st_mtime, datetime.timezone.utc).date())
    name = result['repository'].rstrip('/').removesuffix('.git').rsplit('/', 1)[-1]
    return [
        f'- Sources: [{name}]({result["repository"].removesuffix(".git")}) at `{result["source_commit"][:7]}`, '
        f'Linux {result["linux_version"]}.',
        f'- Tools: [diamaneos-tools]({TOOLS_URL}) at `{tools["commit"][:7]}`, {result["config_profile"]} '
        'kernel configuration.',
        f'- {result.get("module_count")} modules ({result.get("denied_module_count")} left out by policy), '
        f'{result.get("dtb_count")} device trees, {result.get("dtbo_count")} overlays.',
        f'- Built {built.day} {built.strftime("%B")} {built.year}.',
    ]


def update_readme(text, lines):
    head = '## This build'
    rows = text.split('\n')
    require(rows.count(head) == 1, 'the README has no single "## This build" section')
    start = rows.index(head) + 1
    end = next((i for i in range(start, len(rows)) if rows[i].startswith('## ')), len(rows))
    return '\n'.join(rows[:start] + [''] + lines + [''] + rows[end:])


def managed(checkout):
    """The files of a checkout that a publish replaces."""
    found = [checkout / name for name in MANAGED_FILES + MANAGED_DIRS if (checkout / name).exists()]
    return found + sorted(p for p in checkout.iterdir() if BLOCKLIST.fullmatch(p.name))


def git_status(checkout):
    result = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain=v1', '--untracked-files=all'],
                            capture_output=True, text=True, timeout=300)
    require(result.returncode == 0, 'the target is not a Git checkout: ' + str(checkout))
    return result.stdout


def publish(run, checkout, forbid=()):
    run, checkout = Path(run).absolute(), Path(checkout).absolute()
    result, records, present = candidate_files(run)
    lines = describe(result, run)
    names = host_strings(forbid)
    problems = [p for path in present.values() for p in scan(path, names)]
    require(not problems, 'refusing to publish: ' + '; '.join(problems[:10]))
    require(checkout.is_dir() and not checkout.is_symlink() and (checkout / '.git').exists(),
            'the target must be a device_fairphone_FP6-kernels checkout')
    require(not run.resolve().is_relative_to(checkout.resolve()) and not checkout.resolve().is_relative_to(run.resolve()),
            'the run and the checkout must be separate directories')
    readme = checkout / 'README.md'
    require(readme.is_file() and not readme.is_symlink(), 'the checkout has no README.md')
    require(not git_status(checkout), 'the checkout has uncommitted changes; commit or discard them first')
    text = update_readme(readme.read_text(), lines)
    with tempfile.TemporaryDirectory(prefix='.diamaneos-publish-', dir=checkout) as temp:
        staged = Path(temp) / 'candidate'
        for rel, source in present.items():
            target = staged / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o644)
            require(sha(target) == records[rel]['sha256'], 'copy differs from artifacts.json: ' + rel)
        for path in managed(checkout):
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        for entry in sorted(staged.iterdir()):
            os.replace(entry, checkout / entry.name)
    readme.write_text(text)
    published = {p.relative_to(checkout).as_posix(): p for path in managed(checkout)
                 for p in ([path] if path.is_file() else sorted(path.rglob('*'))) if p.is_file()}
    require(set(published) == set(records) and all(sha(p) == records[rel]['sha256'] for rel, p in published.items()),
            'the published files differ from the kernel run')
    return {'status': 'PASS', 'operation': 'kernel-publish', 'files': len(published),
            'source_commit': result['source_commit'], 'tools_commit': result['tools']['commit'],
            'module_count': result.get('module_count'), 'dtb_count': result.get('dtb_count'),
            'readme': lines, 'changed': git_status(checkout).count('\n'), 'committed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='diamaneos kernel publish', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, required=True, help='kernel build run directory (KERNEL_WORKSPACE/runs/...)')
    parser.add_argument('--to', type=Path, required=True, help='device_fairphone_FP6-kernels checkout to update')
    parser.add_argument('--forbid', action='append', default=[],
                        help='another string that must not appear in the published files (repeatable)')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(publish(args.run, args.to, args.forbid), indent=2))
        return 0
    except (PublishError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ERROR: kernel publish failed: ' + str(error), file=sys.stderr)
        return 2
