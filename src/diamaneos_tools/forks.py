"""Report and prepare upstream updates for DiamaneOS forks.

status: fetch (optionally) each fork's upstream reference and report how many
upstream commits the fork lacks and how many DiamaneOS patches it carries.
update: rebase the fork's patches onto a newer upstream reference into a new
local candidate branch. The fork branch is never moved and nothing is pushed;
adopt a candidate only after it builds and passes review.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config/forks.json'
UPSTREAM_REF = 'refs/diamaneos/upstream'
KINDS = {'branch', 'tag', 'commit'}
NAME = re.compile(r'^[A-Za-z0-9._-]+$')
REF = re.compile(r'^[A-Za-z0-9._/-]+$')


class ForkError(Exception):
    pass


def git(repo, *arguments, check=True):
    result = subprocess.run(['git', '-C', str(repo), *arguments], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ForkError(f'git {arguments[0]} failed in {repo}: {result.stderr.strip()}')
    return result


def load(path=CONFIG):
    data = json.loads(Path(path).read_text())
    if data.get('schema_version') != 1 or not isinstance(data.get('forks'), list):
        raise ForkError('invalid fork configuration')
    seen = set()
    for fork in data['forks']:
        upstream = fork.get('upstream', {})
        if (not NAME.match(fork.get('id', '')) or not NAME.match(fork.get('slug', ''))
                or not NAME.match(fork.get('branch', '')) or fork['id'] in seen
                or upstream.get('kind') not in KINDS or not REF.match(upstream.get('ref', ''))
                or not isinstance(upstream.get('url'), str) or not upstream['url']):
            raise ForkError(f"invalid fork entry: {fork.get('id')}")
        if upstream['kind'] == 'commit' and not re.fullmatch(r'[0-9a-f]{40}', upstream['ref']):
            raise ForkError(f"commit reference must be a full hash: {fork['id']}")
        seen.add(fork['id'])
    return data['forks']


def fetch(repo, fork, ref=None, kind=None):
    """Fetch the upstream reference into a private ref and return its commit."""
    ref = ref or fork['upstream']['ref']
    kind = kind or (fork['upstream']['kind'] if ref == fork['upstream']['ref'] else 'commit' if re.fullmatch(r'[0-9a-f]{40}', ref) else 'tag')
    if not REF.match(ref):
        raise ForkError('invalid upstream reference')
    source = {'branch': 'refs/heads/', 'tag': 'refs/tags/', 'commit': ''}[kind] + ref
    git(repo, 'fetch', '--quiet', '--no-tags', fork['upstream']['url'], source)
    commit = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    if kind == 'commit' and commit != ref:
        raise ForkError('fetched commit does not match the requested hash')
    git(repo, 'update-ref', UPSTREAM_REF, commit)
    return commit


def status(root, fork, do_fetch=False):
    repo = Path(root) / fork['slug']
    if not (repo / '.git').exists():
        return {'id': fork['id'], 'state': 'missing', 'path': str(repo)}
    upstream = fetch(repo, fork) if do_fetch else git(repo, 'rev-parse', '--verify', '-q', UPSTREAM_REF, check=False).stdout.strip()
    head = git(repo, 'rev-parse', fork['branch']).stdout.strip()
    result = {'id': fork['id'], 'branch': fork['branch'], 'head': head,
              'upstream_ref': fork['upstream']['ref'], 'dirty': bool(git(repo, 'status', '--porcelain').stdout.strip())}
    if not upstream:
        result['state'] = 'upstream-not-fetched'
        return result
    base = git(repo, 'merge-base', fork['branch'], upstream, check=False).stdout.strip()
    if not base:
        result.update(state='unrelated-history', upstream=upstream)
        return result
    patches = int(git(repo, 'rev-list', '--count', '--no-merges', f'{upstream}..{fork["branch"]}').stdout)
    behind = int(git(repo, 'rev-list', '--count', f'{fork["branch"]}..{upstream}').stdout)
    result.update(upstream=upstream, base=base, patches=patches, behind=behind,
                  state='current' if behind == 0 else 'update-available')
    return result


def update(root, fork, ref=None, today=None):
    repo = Path(root) / fork['slug']
    if not (repo / '.git').exists():
        raise ForkError(f'fork clone missing: {repo}')
    upstream = fetch(repo, fork, ref)
    branch = fork['branch']
    base = git(repo, 'merge-base', branch, upstream, check=False).stdout.strip()
    if not base:
        raise ForkError('fork and upstream share no history')
    patches = git(repo, 'rev-list', '--reverse', '--no-merges', f'{upstream}..{branch}').stdout.split()
    stamp = (today or datetime.date.today()).strftime('%Y%m%d')
    candidate = f'update/{stamp}-{upstream[:12]}'
    if git(repo, 'rev-parse', '--verify', '-q', f'refs/heads/{candidate}', check=False).stdout.strip():
        raise ForkError(f'candidate branch already exists: {candidate}')
    if not patches:
        git(repo, 'branch', candidate, upstream)
        return {'id': fork['id'], 'candidate': candidate, 'upstream': upstream, 'rebased_patches': 0, 'state': 'prepared'}
    with tempfile.TemporaryDirectory(prefix='fork-update-') as scratch:
        work = Path(scratch) / 'work'
        git(repo, 'worktree', 'add', '--quiet', '-b', candidate, str(work), branch)
        try:
            rebase = git(work, 'rebase', '--quiet', '--onto', upstream, base, check=False)
            if rebase.returncode != 0:
                conflicts = git(work, 'diff', '--name-only', '--diff-filter=U').stdout.split()
                git(work, 'rebase', '--abort', check=False)
                git(repo, 'worktree', 'remove', '--force', str(work))
                git(repo, 'branch', '-D', candidate)
                return {'id': fork['id'], 'state': 'conflict', 'upstream': upstream, 'conflicts': conflicts,
                        'patches': len(patches)}
        finally:
            if work.exists():
                git(repo, 'worktree', 'remove', '--force', str(work), check=False)
    return {'id': fork['id'], 'candidate': candidate, 'upstream': upstream,
            'rebased_patches': len(patches), 'state': 'prepared'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--root', type=Path, default=ROOT.parent, help='directory holding the fork clones')
    commands = parser.add_subparsers(dest='command', required=True)
    report = commands.add_parser('status')
    report.add_argument('--fetch', action='store_true', help='fetch each upstream reference first')
    report.add_argument('ids', nargs='*')
    prepare = commands.add_parser('update')
    prepare.add_argument('id')
    prepare.add_argument('--ref', help='upstream branch, tag or full commit hash to move to')
    args = parser.parse_args(argv)
    try:
        forks = {fork['id']: fork for fork in load(args.config)}
        if args.command == 'status':
            unknown = set(args.ids) - set(forks)
            if unknown:
                raise ForkError('unknown fork: ' + ', '.join(sorted(unknown)))
            print(json.dumps([status(args.root, forks[i], args.fetch) for i in (args.ids or forks)], indent=2))
            return 0
        if args.id not in forks:
            raise ForkError('unknown fork: ' + args.id)
        result = update(args.root, forks[args.id], args.ref)
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'prepared' else 1
    except (ForkError, OSError, ValueError) as error:
        print(f'ERROR: {error}')
        return 2
