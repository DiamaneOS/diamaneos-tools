"""Track the upstreams DiamaneOS builds from and prepare fork updates.

check: read only the remote refs of every fork and pinned source and report
which followed references moved and which newer branches or tags appeared.
status: fetch (optionally) each fork's upstream reference and report how many
upstream commits the fork lacks and how many DiamaneOS patches it carries.
update: rebase the fork's patches onto a newer upstream reference into a new
local candidate branch. The fork branch is never moved and nothing is pushed;
adopt a candidate only after it builds and passes review.
"""
import argparse
import datetime
import json
import os
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


def git(repo, *arguments, check=True, offline=False):
    # offline: never fetch a missing object from a partial clone's promisor remote.
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1') if offline else None
    result = subprocess.run(['git', '-C', str(repo), *arguments], capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise ForkError(f'git {arguments[0]} failed in {repo}: {result.stderr.strip()}')
    return result


def newer_pattern(value, where):
    """A newer-branch or newer-tag pattern: a full-match regex with one group that orders matches."""
    try:
        pattern = re.compile(value)
    except (re.error, TypeError):
        raise ForkError(f'invalid newer pattern: {where}') from None
    if pattern.groups != 1 or not value.startswith('^') or not value.endswith('$'):
        raise ForkError(f'newer pattern needs one ordering group and anchors: {where}')
    return pattern


def load_registry(path=CONFIG):
    data = json.loads(Path(path).read_text())
    if data.get('schema_version') not in (1, 2) or not isinstance(data.get('forks'), list):
        raise ForkError('invalid fork configuration')
    forks = load_forks(data['forks'])
    sources = load_sources(data.get('sources', []))
    if {f['id'] for f in forks} & {s['id'] for s in sources}:
        raise ForkError('fork and source ids overlap')
    return forks, sources


def load(path=CONFIG):
    return load_registry(path)[0]


def load_sources(sources):
    seen = set()
    for source in sources:
        follow, pin = source.get('follow', {}), source.get('pin', {})
        if (not NAME.match(source.get('id', '')) or source['id'] in seen
                or not isinstance(source.get('url'), str) or not source['url'].startswith('https://')
                or follow.get('kind') not in ('branch', 'tags', 'manual')
                or not NAME.match(pin.get('repository', '')) or not REF.match(pin.get('file', ''))
                or sum(k in pin for k in ('pointer', 'list', 'xml_project_path')) != 1):
            raise ForkError(f"invalid source entry: {source.get('id')}")
        if follow['kind'] == 'branch':
            if not REF.match(follow.get('ref', '')):
                raise ForkError(f"invalid source branch: {source['id']}")
            if 'newer_branches' in follow:
                newer_pattern(follow['newer_branches'], source['id'])
        elif follow['kind'] == 'tags':
            newer_pattern(follow.get('pattern'), source['id'])
        if 'list' in pin and not (isinstance(pin.get('match'), dict) and NAME.match(pin.get('field', ''))):
            raise ForkError(f"invalid source pin: {source['id']}")
        seen.add(source['id'])
    return sources


def load_forks(forks):
    seen = set()
    for fork in forks:
        upstream = fork.get('upstream', {})
        if (not NAME.match(fork.get('id', '')) or not NAME.match(fork.get('slug', ''))
                or not NAME.match(fork.get('branch', '')) or fork['id'] in seen
                or upstream.get('kind') not in KINDS or not REF.match(upstream.get('ref', ''))
                or not isinstance(upstream.get('url'), str) or not upstream['url']):
            raise ForkError(f"invalid fork entry: {fork.get('id')}")
        if upstream['kind'] == 'commit' and not re.fullmatch(r'[0-9a-f]{40}', upstream['ref']):
            raise ForkError(f"commit reference must be a full hash: {fork['id']}")
        for kind, pattern in fork.get('newer', {}).items():
            if kind not in ('branches', 'tags'):
                raise ForkError(f"invalid newer reference kind: {fork['id']}")
            newer_pattern(pattern, fork['id'])
        seen.add(fork['id'])
    return forks


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


def remote_refs(url):
    """Remote refs by name, with annotated tags peeled to their commits."""
    result = subprocess.run(['git', 'ls-remote', '--heads', '--tags', url], capture_output=True, text=True,
                            timeout=300, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if result.returncode != 0:
        raise ForkError(f'cannot list {url}: {result.stderr.strip()[-300:]}')
    refs = {}
    for line in result.stdout.splitlines():
        sha, name = line.split('\t')
        if name.endswith('^{}'):
            refs[name[:-3]] = sha
        else:
            refs.setdefault(name, sha)
    return refs


def order_key(value):
    return tuple(int(p) if p.isdigit() else p for p in re.split(r'([0-9]+)', value))


def newer_refs(refs, prefix, pattern, current):
    """Names under prefix matching pattern whose ordering group sorts after current's."""
    matched = {}
    for name in refs:
        if name.startswith(prefix):
            found = pattern.fullmatch(name[len(prefix):])
            if found:
                matched[name[len(prefix):]] = order_key(found.group(1))
    here = pattern.fullmatch(current)
    if not here:
        return sorted(matched, key=matched.get)
    return sorted((n for n, k in matched.items() if k > order_key(here.group(1))), key=matched.get)


def json_pointer(data, pointer):
    for part in pointer.lstrip('/').split('/'):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def pinned_value(root, source):
    pin = source['pin']
    base = ROOT if pin['repository'] == 'tools' else Path(root) / pin['repository']
    path = base / pin['file']
    if not path.is_file():
        return None
    if 'xml_project_path' in pin:
        import xml.etree.ElementTree as ET
        found = [p.get('revision') for p in ET.parse(path).getroot().iter('project')
                 if p.get('path') == pin['xml_project_path']]
        if len(found) != 1 or not found[0]:
            raise ForkError(f"pin of {source['id']} does not resolve to one manifest revision")
        return found[0]
    data = json.loads(path.read_text())
    if 'pointer' in pin:
        return json_pointer(data, pin['pointer'])
    values = {row[pin['field']] for row in json_pointer(data, pin['list'])
              if all(row.get(k) == v for k, v in pin['match'].items())}
    if len(values) != 1:
        raise ForkError(f"pin of {source['id']} does not resolve to one value: {sorted(values)}")
    return values.pop()


def is_ancestor(repo, commit, branch):
    if not (repo / '.git').exists():
        return None
    if git(repo, 'cat-file', '-e', commit + '^{commit}', check=False, offline=True).returncode != 0:
        return False
    return git(repo, 'merge-base', '--is-ancestor', commit, branch, check=False, offline=True).returncode == 0


def check_fork(root, fork, refs):
    upstream = fork['upstream']
    result = {'id': fork['id'], 'type': 'fork', 'follows': upstream['ref']}
    if upstream['kind'] == 'branch':
        head = refs.get('refs/heads/' + upstream['ref'])
        if head is None:
            result['state'] = 'followed-branch-missing'
        else:
            contained = is_ancestor(Path(root) / fork['slug'], head, fork['branch'])
            result['upstream_head'] = head
            result['state'] = {None: 'clone-missing', True: 'current', False: 'update-available'}[contained]
    elif upstream['kind'] == 'tag':
        result['state'] = 'current' if 'refs/tags/' + upstream['ref'] in refs else 'followed-tag-missing'
    else:
        result['state'] = 'pinned-commit'
        if fork.get('follow_note'):
            result['note'] = fork['follow_note']
    newer = fork.get('newer', {})
    found = []
    if 'branches' in newer:
        found += newer_refs(refs, 'refs/heads/', re.compile(newer['branches']), upstream['ref'])
    if 'tags' in newer:
        pattern = re.compile(newer['tags'])
        current = upstream['ref'] if upstream['kind'] == 'tag' else ''
        if upstream['kind'] == 'branch':
            # A branch-following fork is measured from the latest release tag it already contains.
            repo = Path(root) / fork['slug']
            contained = [t for t in newer_refs(refs, 'refs/tags/', pattern, '')
                         if is_ancestor(repo, refs['refs/tags/' + t], fork['branch'])]
            current = contained[-1] if contained else ''
            result['latest_release_contained'] = current or None
        # With no release contained yet, the fork predates the whole series: every release is newer.
        found += newer_refs(refs, 'refs/tags/', pattern, current)
    if found:
        result['newer'] = found
        if result['state'] in ('current', 'pinned-commit'):
            result['state'] = 'newer-release'
    return result


def check_source(root, source, refs):
    follow = source['follow']
    result = {'id': source['id'], 'type': 'source', 'pin': pinned_value(root, source)}
    if result['pin'] is None:
        result['state'] = 'pin-unavailable'
    if follow['kind'] == 'manual':
        result.update(state='manual-check', note=follow['note'], url=source['url'])
        return result
    if follow['kind'] == 'tags':
        newer = newer_refs(refs, 'refs/tags/', re.compile(follow['pattern']), result['pin'] or '')
        result.setdefault('state', 'current' if not newer else 'newer-release')
        if newer:
            result['newer'] = newer
        return result
    head = refs.get('refs/heads/' + follow['ref'])
    result['follows'] = follow['ref']
    result['upstream_head'] = head
    if 'state' not in result:
        result['state'] = 'followed-branch-missing' if head is None else 'current' if head == result['pin'] else 'update-available'
    if 'newer_branches' in follow:
        newer = newer_refs(refs, 'refs/heads/', re.compile(follow['newer_branches']), follow['ref'])
        if newer:
            result['newer'] = newer
            if result['state'] == 'current':
                result['state'] = 'newer-release'
    return result


def check(root, forks, sources, lister=remote_refs):
    """Compare every followed reference with its pin from remote refs alone."""
    cache = {}
    def refs(url):
        if url not in cache:
            cache[url] = lister(url)
        return cache[url]
    results = []
    for kind, entries, checker in (('fork', forks, check_fork), ('source', sources, check_source)):
        for entry in entries:
            url = entry['upstream']['url'] if kind == 'fork' else entry['url']
            try:
                needs_refs = kind == 'fork' or entry['follow']['kind'] != 'manual'
                results.append(checker(root, entry, refs(url) if needs_refs else {}))
            except (ForkError, subprocess.TimeoutExpired, KeyError, ValueError) as error:
                results.append({'id': entry['id'], 'type': kind, 'state': 'error', 'error': str(error)})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--root', type=Path, default=ROOT.parent, help='directory holding the fork clones')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check', help='compare every fork and pinned source with its upstream refs')
    report = commands.add_parser('status')
    report.add_argument('--fetch', action='store_true', help='fetch each upstream reference first')
    report.add_argument('ids', nargs='*')
    prepare = commands.add_parser('update')
    prepare.add_argument('id')
    prepare.add_argument('--ref', help='upstream branch, tag or full commit hash to move to')
    args = parser.parse_args(argv)
    try:
        registry, sources = load_registry(args.config)
        forks = {fork['id']: fork for fork in registry}
        if args.command == 'check':
            results = check(args.root, registry, sources)
            print(json.dumps(results, indent=2))
            if any(r['state'] == 'error' for r in results):
                return 2
            return 1 if any(r['state'] in ('update-available', 'newer-release', 'followed-branch-missing',
                                            'followed-tag-missing') for r in results) else 0
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
