"""Track the upstreams DiamaneOS builds from and prepare fork updates.

check: read only the remote refs of every fork and pinned source and report
which followed references moved and which newer branches or tags appeared.
The upstream projects imported into kernel_qcom-6.1 are pinned sources: their
import record holds the imported upstream commit and the newest release tag it
contains.
status: fetch (optionally) each fork's upstream reference and report how many
upstream commits the fork lacks and how many DiamaneOS patches it carries.
update: rebase the fork's patches onto a newer upstream reference into a new
local candidate branch. With --rerere, recorded conflict resolutions are loaded
first, used for repeated conflicts, and saved back with any new ones. The fork branch is never moved and nothing is pushed;
adopt a candidate only after it builds and passes review.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
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
            for key in ('newer_branches', 'newer_tags'):
                if key in follow:
                    newer_pattern(follow[key], source['id'])
        elif follow['kind'] == 'tags':
            newer_pattern(follow.get('pattern'), source['id'])
        if 'list' in pin and not (isinstance(pin.get('match'), dict) and NAME.match(pin.get('field', ''))):
            raise ForkError(f"invalid source pin: {source['id']}")
        if 'newer_tags' in follow or 'release_field' in pin:
            # Release tags are measured from the release recorded in the pinned row.
            if not (follow['kind'] == 'branch' and 'newer_tags' in follow and 'list' in pin
                    and NAME.match(pin.get('release_field', ''))):
                raise ForkError(f"newer_tags need a followed branch and a release_field in a list pin: {source['id']}")
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
    if git(repo, 'config', '--get', 'remote.origin.promisor', check=False).stdout.strip() == 'true':
        # A blobless clone: fetch the upstream blobless too, through a second promisor remote, so the
        # rebase downloads only the files our patches touch instead of the upstream's whole history.
        remote = 'diamaneos-upstream'
        if git(repo, 'remote', 'get-url', remote, check=False).returncode != 0:
            git(repo, 'remote', 'add', remote, fork['upstream']['url'])
        else:
            git(repo, 'remote', 'set-url', remote, fork['upstream']['url'])
        git(repo, 'config', f'remote.{remote}.promisor', 'true')
        git(repo, 'config', f'remote.{remote}.partialclonefilter', 'blob:none')
        git(repo, 'fetch', '--quiet', '--no-tags', '--filter=blob:none', remote, source)
    else:
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


def pinned_base(repo, fork, branch):
    """The commit of the tag or commit the fork is based on now (its pin), if the fork branch has it.

    Our patches are the commits after the pin. Upstreams that rewrite their history between releases
    (GrapheneOS rebases its kernel patches onto each LTS update) would otherwise make their old commits
    look like ours: the merge-base with the new release lies before them.
    """
    kind = fork['upstream']['kind']
    if kind in ('tag', 'commit'):
        pinned = fetch(repo, fork)
        return pinned if is_ancestor(repo, pinned, branch) else None
    pattern = (fork.get('newer') or {}).get('tags')
    if kind != 'branch' or not pattern:
        return None
    # A fork that follows a branch: the newest upstream release tag the fork branch contains.
    partial = git(repo, 'config', '--get', 'remote.origin.promisor', check=False).stdout.strip() == 'true'
    git(repo, 'fetch', '--quiet', '--no-tags', '--force', *(['--filter=blob:none'] if partial else []),
        fork['upstream']['url'], '+refs/tags/*:refs/diamaneos/upstream-tags/*')
    names = git(repo, 'for-each-ref', '--format=%(refname:strip=3)', 'refs/diamaneos/upstream-tags/').stdout.split()
    for name in sorted((n for n in names if re.fullmatch(pattern, n)), key=order_key, reverse=True):
        commit = git(repo, 'rev-parse', f'refs/diamaneos/upstream-tags/{name}^{{commit}}').stdout.strip()
        if is_ancestor(repo, commit, branch):
            return commit
    return None


def rerere_cache(repo):
    return Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()) / 'rr-cache'


def load_resolutions(repo, records):
    """Turn on rerere and copy recorded resolutions (one directory per conflict) into the clone."""
    git(repo, 'config', 'rerere.enabled', 'true')
    git(repo, 'config', 'rerere.autoupdate', 'true')
    if records.is_dir():
        shutil.copytree(records, rerere_cache(repo), dirs_exist_ok=True)


def save_resolutions(repo, records):
    cache = rerere_cache(repo)
    if cache.is_dir() and any(cache.iterdir()):
        shutil.copytree(cache, records, dirs_exist_ok=True)


def update(root, fork, ref=None, today=None, rerere=None):
    """Apply our patches (the commits after the pin) on top of the new upstream reference.

    Like GrapheneOS: the new release is the base and our commits are replayed on it, merges dropped and
    `fixup!` commits folded into the commit they name (autosquash). The fork branch is never moved.
    rerere: a directory of recorded resolutions (one subdirectory per fork slug). A stop whose every
    conflict a recorded resolution settles continues; the files are reported as rerere_resolved.
    """
    repo = Path(root) / fork['slug']
    if not (repo / '.git').exists():
        raise ForkError(f'fork clone missing: {repo}')
    branch = fork['branch']
    pinned = pinned_base(repo, fork, branch) if fork['upstream']['kind'] == 'branch' or (
        ref and ref != fork['upstream']['ref']) else None
    upstream = fetch(repo, fork, ref)
    base = pinned or git(repo, 'merge-base', branch, upstream, check=False).stdout.strip()
    if not base:
        raise ForkError('fork and upstream share no history')
    patches = git(repo, 'rev-list', '--reverse', '--no-merges', f'{base}..{branch}').stdout.split()
    stamp = (today or datetime.date.today()).strftime('%Y%m%d')
    candidate = f'update/{stamp}-{upstream[:12]}'
    if git(repo, 'rev-parse', '--verify', '-q', f'refs/heads/{candidate}', check=False).stdout.strip():
        raise ForkError(f'candidate branch already exists: {candidate}')
    records = Path(rerere) / fork['slug'] if rerere else None
    if records:
        load_resolutions(repo, records)
    if not patches:
        git(repo, 'branch', candidate, upstream)
        return {'id': fork['id'], 'candidate': candidate, 'upstream': upstream, 'rebased_patches': 0, 'state': 'prepared'}
    with tempfile.TemporaryDirectory(prefix='fork-update-') as scratch:
        work = Path(scratch) / 'work'
        git(repo, 'worktree', 'add', '--quiet', '-b', candidate, str(work), branch)
        try:
            # A non-interactive interactive rebase: only for --autosquash.
            rebase = git(work, '-c', 'sequence.editor=true', 'rebase', '--quiet', '--interactive', '--autosquash',
                         '--onto', upstream, base, check=False)
            resolved = []
            for _ in range(len(patches)):
                # rerere.autoupdate stages a recorded resolution; continue only when nothing is left unmerged.
                if rebase.returncode == 0 or not records or git(work, 'diff', '--name-only', '--diff-filter=U').stdout.split():
                    break
                found = re.findall(r"(?:Resolved|Staged) '(.+?)' using previous resolution", rebase.stdout + rebase.stderr)
                if not found:
                    break
                resolved += found
                rebase = git(work, '-c', 'core.editor=true', 'rebase', '--continue', check=False)
            if records:
                save_resolutions(repo, records)
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
    commits = int(git(repo, 'rev-list', '--count', '--no-merges', f'{upstream}..{candidate}').stdout)
    result = {'id': fork['id'], 'candidate': candidate, 'upstream': upstream, 'base': base,
              'rebased_patches': len(patches), 'commits': commits, 'state': 'prepared'}
    if resolved:
        result['rerere_resolved'] = sorted(set(resolved))
    return result


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


def pinned_value(root, source, field=None):
    """The pinned value; field reads another column of a list pin's row, such as its release."""
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
    values = {row[field or pin['field']] for row in json_pointer(data, pin['list'])
              if all(row.get(k) == v for k, v in pin['match'].items())}
    if len(values) != 1:
        raise ForkError(f"pin of {source['id']} does not resolve to one value: {sorted(values, key=str)}")
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
    newer = []
    if 'newer_branches' in follow:
        newer += newer_refs(refs, 'refs/heads/', re.compile(follow['newer_branches']), follow['ref'])
    if 'newer_tags' in follow and result['pin'] is not None:
        # No history is fetched, so the release the pinned commit contains is read from the pin's row;
        # with none recorded, the pin predates the whole series and every release is newer.
        result['release'] = pinned_value(root, source, source['pin']['release_field'])
        newer += newer_refs(refs, 'refs/tags/', re.compile(follow['newer_tags']), result['release'] or '')
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
    prepare.add_argument('--rerere', type=Path, metavar='DIR',
                         help='recorded conflict resolutions, DIR/<fork slug>: loaded first, saved back after')
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
        result = update(args.root, forks[args.id], args.ref, rerere=args.rerere)
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'prepared' else 1
    except (ForkError, OSError, ValueError) as error:
        print(f'ERROR: {error}')
        return 2
