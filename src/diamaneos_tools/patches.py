"""Apply the patch series on top of the synced sources.

Fixes that upstream is expected to make itself, and library updates upstream
will overtake, live as patches in one repository instead of in forks. It is a
manifest project at PROJECT. Its series.json names every patch, the project it
changes and, for each file it touches, the file's mode and blob before and
after. These pins are the contract: a patch is applied only to the exact file
contents it was reviewed against, and a patched project is exactly its
manifest commit plus the pinned changes, whatever else moved in that project.

A patched project stays at its manifest commit. The series is its staged
changes: "git diff --cached" shows the patches and "git diff" a developer's own
edits on top. So the resolved manifest ("repo manifest -r" records each HEAD)
names the same commits with and without the series, and the series is checked
from the index alone, with no state file to trust.

apply: build the patched tree in a temporary index, check every step against
the pins, then stage it in the checkout.
reset: take the series off again. "build sync" does it before "repo sync",
which would otherwise stop at a patched file that changed upstream.
status: say for each project whether the series is applied.
check: say for each patch whether it still fits the manifest commit, whether
upstream already has its change, or which files moved under it.
pins: print a patch's SHA-256 and pins against a project, to add a patch or
to refresh one that still applies after its files changed elsewhere.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PROJECT = 'diamaneos/patches'
SERIES = 'series.json'
SCHEMA = 1
KINDS = ('fix', 'backport', 'import')
MAX_SERIES_BYTES = 16 * 1024 * 1024
MAX_PATCH_BYTES = 64 * 1024 * 1024
MAX_PATCHES = 2000
MAX_FILES = 20000
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
SOURCE_PATH = re.compile(r'[A-Za-z0-9._+@/-]{1,1024}')
SHA256 = re.compile(r'[0-9a-f]{64}')
PIN = re.compile(r'(100644|100755|120000) [0-9a-f]{40}(?:[0-9a-f]{24})?')
ENTRY_KEYS = {'id', 'project', 'file', 'sha256', 'title', 'kind', 'files'}
OPTIONAL_KEYS = {'upstream', 'finding', 'test', 'drop_when'}


class PatchError(Exception):
    pass


def git(repo, *arguments, env=None, check=True, stdin=None):
    result = subprocess.run(['git', '-C', str(repo), *arguments], capture_output=True, input=stdin,
                            env=dict(os.environ, **env) if env else None)
    if check and result.returncode != 0:
        raise PatchError(f'git {arguments[0]} failed in {repo}: '
                         + result.stderr.decode(errors='replace').strip()[:2000])
    return result


def text(result) -> str:
    return result.stdout.decode().strip()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PatchError('the patch series has a duplicate key')
        value[key] = item
    return value


def _relative(value, pattern=SOURCE_PATH) -> bool:
    return (isinstance(value, str) and pattern.fullmatch(value) is not None
            and all(part not in ('', '.', '..') for part in value.split('/')))


def _file_path(value) -> bool:
    """A path inside a project. Projects hold names the stricter source pattern refuses."""
    return (isinstance(value, str) and 0 < len(value) <= 4096 and not value.startswith('/')
            and not any(c in value for c in '\0\n\r')
            and all(part not in ('', '.', '..') and part.lower() != '.git' for part in value.split('/')))


def _line(value, limit=500) -> bool:
    return isinstance(value, str) and len(value) <= limit and '\n' not in value and '\r' not in value


def validate(data) -> list:
    """The entries of a series document, in order. Raises PatchError on anything unexpected."""
    if not isinstance(data, dict) or set(data) != {'schema', 'patches'} or data['schema'] != SCHEMA:
        raise PatchError(f'the patch series needs "schema": {SCHEMA} and "patches", nothing else')
    entries = data['patches']
    if not isinstance(entries, list) or len(entries) > MAX_PATCHES:
        raise PatchError('the patch series holds no list of patches, or too many')
    seen, files_seen = set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or not ENTRY_KEYS <= set(entry) <= ENTRY_KEYS | OPTIONAL_KEYS:
            raise PatchError('a patch entry has missing or unknown keys')
        name = entry['id']
        if not isinstance(name, str) or ID.fullmatch(name) is None or name in seen:
            raise PatchError('a patch id is invalid or used twice')
        seen.add(name)
        project = entry['project']
        if (not _relative(project) or project.split('/')[0] == '.repo'
                or project == PROJECT or project.startswith(PROJECT + '/')):
            raise PatchError(f'{name}: invalid project path')
        if not _relative(entry['file']) or not entry['file'].endswith('.patch') or entry['file'] in files_seen:
            raise PatchError(f'{name}: invalid patch file path, or one file for two patches')
        files_seen.add(entry['file'])
        if not isinstance(entry['sha256'], str) or SHA256.fullmatch(entry['sha256']) is None:
            raise PatchError(f'{name}: invalid SHA-256')
        if not _line(entry['title'], 200) or not entry['title'].strip():
            raise PatchError(f'{name}: the title must be one line')
        if entry['kind'] not in KINDS:
            raise PatchError(f'{name}: kind must be one of ' + ', '.join(KINDS))
        if any(not _line(entry[key]) for key in OPTIONAL_KEYS & set(entry)):
            raise PatchError(f'{name}: notes must be single lines')
        if entry['kind'] != 'fix' and not entry.get('upstream', '').strip():
            raise PatchError(f'{name}: a {entry["kind"]} names its upstream commit or version')
        files = entry['files']
        if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
            raise PatchError(f'{name}: no pinned files, or too many')
        for path, pins in files.items():
            if (not _file_path(path) or not isinstance(pins, list) or len(pins) != 2 or pins[0] == pins[1]
                    or any(pin is not None and (not isinstance(pin, str) or PIN.fullmatch(pin) is None)
                           for pin in pins)):
                raise PatchError(f'{name}: invalid pin for a file')
    return entries


def load_series(directory: Path) -> tuple[list, str]:
    """The validated entries of DIRECTORY/series.json and the file's SHA-256."""
    path = Path(directory) / SERIES
    if path.is_symlink() or not path.is_file():
        raise PatchError(f'no {SERIES} in {directory}')
    raw = path.read_bytes()
    if len(raw) > MAX_SERIES_BYTES:
        raise PatchError(f'{SERIES} exceeds its size limit')
    try:
        data = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise PatchError(f'{SERIES} is not valid JSON') from None
    return validate(data), hashlib.sha256(raw).hexdigest()


def by_project(entries) -> dict:
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry['project'], []).append(entry)
    return grouped


def patch_file(directory: Path, entry) -> Path:
    """The patch file of an entry, after checking that it is the pinned one."""
    path = Path(directory) / entry['file']
    resolved, root = path.resolve(), Path(directory).resolve()
    if path.is_symlink() or not path.is_file() or root not in resolved.parents:
        raise PatchError(f'{entry["id"]}: patch file {entry["file"]} is missing')
    if path.stat().st_size > MAX_PATCH_BYTES:
        raise PatchError(f'{entry["id"]}: the patch file exceeds its size limit')
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        raise PatchError(f'{entry["id"]}: {entry["file"]} is not the pinned file (SHA-256 differs)')
    return path


def expected(entry) -> dict:
    return {path: tuple(pins) for path, pins in entry['files'].items()}


def cumulative(entries) -> dict:
    """What a project's patches change together: path -> (pin before the first, pin after the last)."""
    total = {}
    for entry in entries:
        for path, (before, after) in expected(entry).items():
            if path in total and total[path][1] != before:
                raise PatchError(f'{entry["id"]}: its pin for {path} does not continue the patch before it')
            total[path] = (total[path][0] if path in total else before, after)
    return {path: pair for path, pair in total.items() if pair[0] != pair[1]}


def _raw(output: bytes) -> dict:
    """A "--raw -z" diff as path -> (pin before, pin after), None for absent."""
    fields = output.split(b'\0')
    result = {}
    for meta, path in zip(fields[0::2], fields[1::2]):
        old_mode, new_mode, old, new, _status = meta.decode().lstrip(':').split(' ')
        result[os.fsdecode(path)] = (None if int(old_mode, 8) == 0 else f'{old_mode} {old}',
                                     None if int(new_mode, 8) == 0 else f'{new_mode} {new}')
    return result


def changes(repo, before, after) -> dict:
    """What differs between two trees or commits."""
    return _raw(git(repo, 'diff-tree', '-r', '-z', '--raw', '--no-renames', '--no-abbrev', before, after).stdout)


def staged(repo) -> dict:
    """What the index holds on top of HEAD."""
    return _raw(git(repo, 'diff-index', '--cached', '-r', '-z', '--raw', '--no-renames', '--no-abbrev',
                    'HEAD').stdout)


def tree_pins(repo, tree, paths) -> dict:
    """The pins of PATHS in a tree or commit, None for absent."""
    found = {}
    paths = sorted(paths)
    for start in range(0, len(paths), 200):
        raw = git(repo, 'ls-tree', '-z', '--full-tree', tree, '--', *paths[start:start + 200]).stdout
        for record in raw.split(b'\0'):
            if record:
                meta, path = record.split(b'\t', 1)
                mode, _kind, blob = meta.decode().split(' ')
                found[os.fsdecode(path)] = f'{mode} {blob}'
    return {path: found.get(path) for path in paths}


def mismatches(wanted: dict, found: dict) -> list:
    return sorted(path for path in set(wanted) | set(found) if wanted.get(path) != found.get(path))


class Index:
    """A temporary index on a project's objects, so nothing in the checkout moves until a result is checked."""

    def __init__(self, repo, tree):
        self.repo = repo
        self.directory = tempfile.TemporaryDirectory(prefix='diamaneos-patches-')
        self.env = {'GIT_INDEX_FILE': str(Path(self.directory.name) / 'index')}
        git(repo, 'read-tree', tree, env=self.env)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.directory.cleanup()

    def apply(self, patch: Path, reverse=False, check=False):
        return git(self.repo, 'apply', '--cached', '--whitespace=nowarn', '-p1',
                   *(['--reverse'] if reverse else []), *(['--check'] if check else []),
                   str(Path(patch).resolve()), env=self.env, check=False)

    def tree(self) -> str:
        # The pins compare blob ids, so a partial clone need not fetch every neighbouring file to write the tree.
        return text(git(self.repo, 'write-tree', '--missing-ok', env=self.env))


def pins(repo, base, patch: Path) -> dict:
    """The pins a patch has when applied to BASE: path -> [before, after]."""
    with Index(repo, base) as index:
        result = index.apply(patch)
        if result.returncode != 0:
            raise PatchError('the patch does not apply: ' + result.stderr.decode(errors='replace').strip()[:2000])
        return {path: list(pair) for path, pair in sorted(changes(repo, base, index.tree()).items())}


def build_tree(repo, entries, directory: Path) -> str:
    """The tree of HEAD with ENTRIES applied, built without touching the checkout."""
    with Index(repo, 'HEAD') as index:
        current = text(git(repo, 'rev-parse', 'HEAD^{tree}'))
        for entry in entries:
            name, wanted = entry['id'], expected(entry)
            patch = patch_file(directory, entry)
            before = tree_pins(repo, current, wanted)
            moved = sorted(path for path, pin in before.items() if pin != wanted[path][0])
            if moved:
                raise PatchError(f'{name}: {len(moved)} file(s) are not what the patch was pinned to: '
                                 + ', '.join(moved[:5]) + '. Run "diamaneos patches check"')
            result = index.apply(patch)
            if result.returncode != 0:
                raise PatchError(f'{name}: does not apply: '
                                 + result.stderr.decode(errors='replace').strip()[:2000])
            tree = index.tree()
            wrong = mismatches(wanted, changes(repo, current, tree))
            if wrong:
                raise PatchError(f'{name}: the patch changes other files, or other contents, than its pins: '
                                 + ', '.join(wrong[:5]))
            current = tree
    return current


def state(repo, entries) -> str:
    """'unpatched', 'applied' or 'other': what a project's index holds on top of HEAD against its patches."""
    held = staged(repo)
    if not mismatches(cumulative(entries), held):
        return 'applied'
    return 'other' if held else 'unpatched'


def local_status(repo, entries) -> bytes:
    """"git status --porcelain=v1 -z" of a patched project without the series' own staged changes.

    What is left is local: files changed, added or deleted in the working tree,
    untracked files and anything else staged.
    """
    own = cumulative(entries)
    raw = git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames').stdout
    kept = [record for record in raw.split(b'\0') if record and not (
        record[1:2] == b' ' and record[0:1] in b'MADT' and os.fsdecode(record[3:]) in own)]
    return b''.join(record + b'\0' for record in kept)


def series_of(source: Path, rows, allow_modified=False):
    """(entries, series SHA-256, patches commit), or None when the manifest has no series."""
    paths = {row[0] for row in rows}
    if PROJECT not in paths:
        return None
    directory = Path(source) / PROJECT
    if not (directory / '.git').exists():
        raise PatchError(f'the patch project {PROJECT} is not checked out')
    if not allow_modified and git(directory, 'status', '--porcelain=v1', '--untracked-files=all').stdout:
        raise PatchError(f'the patch project {PROJECT} has local changes')
    entries, digest = load_series(directory)
    missing = sorted(set(by_project(entries)) - paths)
    if missing:
        raise PatchError('the patch series names projects the manifest does not have: ' + ', '.join(missing[:5]))
    return entries, digest, text(git(directory, 'rev-parse', '--verify', 'HEAD^{commit}'))


def _record(digest, commit, projects) -> dict:
    record = {'series_sha256': digest, 'patches_commit': commit, 'projects': projects}
    record['sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record


def _project_record(repo, entries) -> dict:
    return {'base': text(git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')), 'tree': text(git(repo, 'write-tree')),
            'patches': [entry['id'] for entry in entries]}


def _refresh(repo):
    git(repo, 'update-index', '-q', '--refresh', check=False)


def apply(source: Path, rows, echo=print, allow_modified=False, mtimes=None):
    """Stage the series in every project it names. Returns the record, or None without a series.

    ``mtimes`` is what reset returned just before: a file that gets the same
    contents back keeps its old modification time, so a sync that changed
    nothing under the patches causes no rebuild.
    """
    loaded = series_of(source, rows, allow_modified)
    if loaded is None:
        return None
    entries, digest, commit = loaded
    directory, projects = Path(source) / PROJECT, {}
    for path, group in sorted(by_project(entries).items()):
        repo = Path(source) / path
        if not (repo / '.git').exists():
            raise PatchError(f'source project is missing: {path}')
        total = cumulative(group)
        _refresh(repo)
        current = state(repo, group)
        if current == 'other':
            raise PatchError(f'{path} has staged changes that are not the patch series. Unstage them, or drop '
                             f'them with "git -C {path} reset --hard"')
        if current == 'unpatched':
            tree = build_tree(repo, group, directory)
            moved = git(repo, 'read-tree', '-m', '-u', 'HEAD', tree, check=False)
            if moved.returncode != 0:
                raise PatchError(f'{path}: cannot stage the patched files, local changes are in the way: '
                                 + moved.stderr.decode(errors='replace').strip()[:1000])
            kept = 0
            for name, (pin, stamp) in ((mtimes or {}).get(path) or {}).items():
                target = repo / name
                if total.get(name, (None, None))[1] == pin and (target.is_file() or target.is_symlink()):
                    os.utime(target, ns=(target.lstat().st_atime_ns, stamp), follow_symlinks=False)
                    kept += 1
            _refresh(repo)
            echo(f'    {path}: applied {len(group)} patch(es) to {len(total)} file(s)'
                 + (f', {kept} unchanged since the last time' if kept else ''))
        projects[path] = _project_record(repo, group)
    return _record(digest, commit, projects)


def inspect(source: Path, rows, allow_modified=False):
    """(record, entries by project) of an applied series, without changing anything; (None, {}) without one.

    Raises unless every project the series names holds exactly the series.
    """
    loaded = series_of(source, rows, allow_modified)
    if loaded is None:
        return None, {}
    entries, digest, commit = loaded
    directory, projects, groups = Path(source) / PROJECT, {}, by_project(entries)
    for entry in entries:
        patch_file(directory, entry)
    for path, group in sorted(groups.items()):
        repo = Path(source) / path
        if not (repo / '.git').exists():
            raise PatchError(f'source project is missing: {path}')
        if state(repo, group) != 'applied':
            raise PatchError(f'the patch series is not applied to {path}. Run "diamaneos patches apply"')
        projects[path] = _project_record(repo, group)
    return _record(digest, commit, projects), groups


def verify(source: Path, rows, allow_modified=False):
    """The record of an applied series, or None without one."""
    return inspect(source, rows, allow_modified)[0]


def reset(source: Path, echo=print, strict=True) -> dict:
    """Take the series off every project that holds it.

    Works from the patch project as it is checked out, so it undoes the
    series that was applied before a sync brings a new one. Returns, per
    project, path -> (pin, modification time) of the patched files, for
    apply. A project with other staged changes is an error when ``strict``
    and is left alone otherwise.
    """
    directory = Path(source) / PROJECT
    if not (directory / SERIES).is_file():
        return {}
    entries, _digest = load_series(directory)
    saved = {}
    for path, group in sorted(by_project(entries).items()):
        repo = Path(source) / path
        if not (repo / '.git').exists():
            continue
        _refresh(repo)
        current = state(repo, group)
        if current == 'unpatched':
            continue
        if current == 'other':
            if strict:
                raise PatchError(f'{path} has staged changes that are not the patch series')
            echo(f'    {path}: staged changes that are not the patch series, left as they are')
            continue
        stamps = {}
        for name, (_before, after) in cumulative(group).items():
            target = repo / name
            if after is not None and (target.is_file() or target.is_symlink()):
                stamps[name] = (after, target.lstat().st_mtime_ns)
        result = git(repo, 'read-tree', '-m', '-u', text(git(repo, 'write-tree')), 'HEAD', check=False)
        if result.returncode != 0:
            raise PatchError(f'{path}: cannot take the patches off, local changes are in the way: '
                             + result.stderr.decode(errors='replace').strip()[:1000])
        echo(f'    {path}: {len(group)} patch(es) taken off')
        saved[path] = stamps
    return saved


def status(source: Path, rows) -> list:
    """(project, 'applied' | 'unpatched' | 'other', patch count) for every project the series names."""
    loaded = series_of(source, rows, allow_modified=True)
    if loaded is None:
        return []
    return [(path, state(Path(source) / path, group), len(group))
            for path, group in sorted(by_project(loaded[0]).items())]


def check(source: Path, rows) -> list:
    """How each patch stands against its project's manifest commit, in series order.

    fits: the pinned files are unchanged. refresh: files changed but the patch
    still applies, so only its pins need renewing. upstream: the project
    already has the change (the results are there, or the patch applies in
    reverse), so the patch can be dropped. conflict: the patch no longer
    applies and its change is not there, so it needs a new version or, if its
    test passes without it, removal. Later patches of a project are judged on
    top of the earlier ones that fit.
    """
    loaded = series_of(source, rows, allow_modified=True)
    if loaded is None:
        return []
    entries = loaded[0]
    directory, findings = Path(source) / PROJECT, {}
    for path, group in by_project(entries).items():
        repo = Path(source) / path
        with Index(repo, 'HEAD') as index:
            for entry in group:
                wanted, patch = expected(entry), patch_file(directory, entry)
                found = tree_pins(repo, index.tree(), wanted)
                moved = sorted(p for p in wanted if found[p] != wanted[p][0])
                if not moved and index.apply(patch).returncode == 0:
                    verdict = 'fits'
                elif all(found[p] == wanted[p][1] for p in wanted):
                    verdict = 'upstream'
                elif index.apply(patch, check=True).returncode == 0:
                    index.apply(patch)
                    verdict = 'refresh'
                elif index.apply(patch, reverse=True, check=True).returncode == 0:
                    verdict = 'upstream'
                else:
                    verdict = 'conflict'
                findings[entry['id']] = (entry['id'], path, verdict, moved)
    return [findings[entry['id']] for entry in entries]


def manifest_rows(source: Path):
    from . import build
    return build.parse_project_map(build.repo_manifest(Path(source)))[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='diamaneos patches', description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('apply', 'reset', 'status', 'check'):
        commands.add_parser(name).add_argument('--source', type=Path, required=True,
                                               help='the repo checkout (the directory with .repo)')
    one = commands.add_parser('pins', help='print the SHA-256 and pins of a patch file against a project')
    one.add_argument('--source', type=Path, required=True)
    one.add_argument('--on', default='HEAD', help='the commit to pin against (default: the checkout)')
    one.add_argument('project')
    one.add_argument('patch', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'pins':
            if not _relative(args.project):
                raise PatchError('invalid project path')
            print(json.dumps({'sha256': hashlib.sha256(args.patch.read_bytes()).hexdigest(),
                              'files': pins(args.source / args.project, args.on, args.patch)}, indent=2))
            return 0
        if args.command == 'reset':
            reset(args.source)
            return 0
        rows = manifest_rows(args.source)
        if PROJECT not in {row[0] for row in rows}:
            print(f'the manifest has no patch project at {PROJECT}')
            return 0
        if args.command == 'apply':
            record = apply(args.source, rows, allow_modified=True)
            print(f'patch series {record["series_sha256"][:12]} is applied to {len(record["projects"])} project(s)')
        elif args.command == 'status':
            listed = status(args.source, rows)
            for path, current, count in listed:
                print(f'{current:10} {count:4}  {path}')
            return 0 if all(current == 'applied' for _path, current, _count in listed) else 1
        else:
            findings = check(args.source, rows)
            for name, path, verdict, moved in findings:
                print(f'{verdict:9} {name}  {path}' + (f'  ({len(moved)} file(s) changed)' if moved else ''))
            return 0 if all(verdict == 'fits' for _name, _path, verdict, _moved in findings) else 1
    except (PatchError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
