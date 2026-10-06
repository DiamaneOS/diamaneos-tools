"""Source sync helpers for "diamaneos build sync".

Reproducing a recorded build (--resolved-manifest): the sync checks out every
project at the commit an image set's resolved manifest records, with the
manifest checkout at the manifest commit that build recorded.

Shallow syncs (--shallow): the very large prebuilt projects are fetched first,
one revision each at depth 1, with a low-speed abort and a bounded number of
attempts; project git directories an interrupted sync left without data are
removed so repo creates them again as shallow projects.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET

from . import build
from .build_workspace import BuildStepError, UsageError

MAX_RECORD_BYTES = 4 * 1024 * 1024
# Manifest composition that "repo manifest -r" flattens; a resolved manifest
# never contains it.
COMPOSITION = ('include', 'extend-project', 'remove-project')
# What "repo manifest -r" adds to a project of "repo manifest": its commit and
# the expression it was resolved from.
RESOLVED_ATTRIBUTES = ('revision', 'upstream', 'dest-branch')
TAG = re.compile(r'refs/tags/[A-Za-z0-9][A-Za-z0-9._/-]{0,127}')
SAFE_PATH = re.compile(r'[A-Za-z0-9._+@/-]+')
BUILD_ID = re.compile(r'[A-Za-z0-9._-]{1,128}')
SHA256 = re.compile(r'[0-9a-f]{64}')
PREFETCH_KEYS = {'projects', 'jobs', 'attempts', 'retry_delay_seconds', 'low_speed_limit_bytes',
                 'low_speed_time_seconds', 'reason'}
PREFETCH_LIMITS = {'jobs': (1, 16), 'attempts': (1, 50), 'retry_delay_seconds': (0, 3600),
                   'low_speed_limit_bytes': (1, 1 << 30), 'low_speed_time_seconds': (1, 3600)}
# The low-speed abort ends a stalled transfer; this only bounds a slow one.
FETCH_TIMEOUT = 8 * 3600


def git(argv, timeout=3600) -> subprocess.CompletedProcess:
    """git without hooks or prompts; a failure or timeout is a return code."""
    command = ['git', '-c', 'core.hooksPath=/dev/null', *map(str, argv)]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, -1, '', f'timed out after {timeout} s')


def tail(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or '').strip()[-400:] or f'exit status {result.returncode}'


def safe_path(value) -> bool:
    return (isinstance(value, str) and len(value) <= 4096 and SAFE_PATH.fullmatch(value) is not None
            and all(part not in ('', '.', '..') for part in value.split('/')))


def shown(value, pattern) -> str:
    """A value from a record in a message, only when it has the expected form."""
    return value if isinstance(value, str) and pattern.fullmatch(value) else 'an invalid value'


# ------------------------------------------------------- pinned manifests

@dataclass(frozen=True)
class PinnedManifest:
    """A recorded resolved manifest the sync reproduces."""
    data: bytes
    sha256: str
    manifest_commit: str
    build_id: str | None = None
    build_json_sha256: str | None = None
    # The recorded build's sync outputs (build.json "source"), compared with
    # the new sync's.
    expected_source: dict = field(default_factory=dict)
    notes: tuple = ()

    def record(self) -> dict:
        """What the sync state records about the reproduction."""
        return {'resolved_manifest_sha256': self.sha256, 'manifest_commit': self.manifest_commit,
                'build_id': self.build_id, 'build_json_sha256': self.build_json_sha256}


def read_limited(path: Path, limit: int, label: str) -> bytes:
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise UsageError(f'cannot read the {label} {path}: {error.strerror or error}') from None
    if len(data) > limit:
        raise UsageError(f'the {label} {path} is larger than {limit // (1024 * 1024)} MiB')
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def check_resolved(data: bytes) -> None:
    """Checks of a resolved manifest that need nothing but the file."""
    try:
        build.parse_project_map(data)
        root = ET.fromstring(data)
    except build.BuildError as error:
        raise UsageError(f'the resolved manifest cannot be used: {error}') from None
    if root.tag != 'manifest' or any(root.find(tag) is not None for tag in COMPOSITION):
        raise UsageError('the resolved manifest includes or edits other manifests, which "repo manifest -r" '
                         'never writes')
    if any(not remote.get('fetch', '').startswith('https://') for remote in root.findall('remote')):
        raise UsageError('the resolved manifest fetches from a remote without HTTPS')


def load_pinned(resolved_path: Path, build_json_path: Path | None, manifest_commit: str | None,
                environment: dict, environment_sha256: str, tools_commit: str | None = None) -> PinnedManifest:
    """Read and check what "build sync --resolved-manifest" reproduces.

    The manifest commit comes from build.json (``manifest.commit``, equal to
    the recorded sync's ``source.manifest_commit``) or from --manifest-commit.
    A resolved manifest whose SHA-256 differs from the one build.json records
    is refused, as is a build.json made from another manifest.
    """
    data = read_limited(resolved_path, build.MAX_MANIFEST_BYTES, 'resolved manifest')
    sha256 = hashlib.sha256(data).hexdigest()
    check_resolved(data)
    declared = environment['manifest']
    if manifest_commit is not None and not build.SHA1_RE.fullmatch(manifest_commit):
        raise UsageError('--manifest-commit must be a full 40-character commit id')
    commit, build_id, record_sha256, expected, notes = manifest_commit, None, None, {}, []
    if build_json_path is not None:
        raw = read_limited(build_json_path, MAX_RECORD_BYTES, 'build record')
        try:
            record = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, RecursionError):
            raise UsageError(f'{build_json_path} is not a valid build.json') from None
        manifest = record.get('manifest') if isinstance(record, dict) else None
        source = record.get('source') if isinstance(record, dict) else None
        if not isinstance(manifest, dict) or not isinstance(source, dict):
            raise UsageError(f'{build_json_path} has no manifest or source record')
        for recorded in (manifest.get('resolved_sha256'), source.get('resolved_manifest_sha256')):
            if recorded != sha256:
                raise UsageError(f'the resolved manifest does not match {build_json_path}: its SHA-256 is {sha256}, '
                                 f'build.json records {shown(recorded, SHA256)}')
        recorded_commit = manifest.get('commit')
        if not isinstance(recorded_commit, str) or not build.SHA1_RE.fullmatch(recorded_commit) \
                or source.get('manifest_commit') != recorded_commit:
            raise UsageError(f'{build_json_path} records no consistent manifest commit')
        if commit is not None and commit != recorded_commit:
            raise UsageError(f'--manifest-commit {commit} differs from the manifest commit {recorded_commit} '
                             f'{build_json_path} records')
        commit = recorded_commit
        if (manifest.get('url'), manifest.get('branch')) != (declared['url'], declared['branch']):
            raise UsageError(f'{build_json_path} was built from another manifest URL or branch than the build '
                             f'environment declares ({declared["url"]}, {declared["branch"]}); pass the '
                             'environment it was built with (--environment)')
        expected = {key: source[key] for key in ('project_map_sha256', 'project_count', 'kernel_prebuilts_commit')
                    if key in source}
        if isinstance(record.get('build_id'), str) and BUILD_ID.fullmatch(record['build_id']):
            build_id = record['build_id']
        record_sha256 = hashlib.sha256(raw).hexdigest()
        part = lambda key, name: record[key].get(name) if isinstance(record.get(key), dict) else None
        if part('environment', 'sha256') != environment_sha256:
            notes.append('build.json was made with another build environment file; the source identity and the '
                         'build number will differ')
        if record.get('official') is True:
            notes.append('build.json records an official build; build it with --official to make the same images')
        recorded_tools = part('tools', 'commit')
        if tools_commit and recorded_tools != tools_commit:
            notes.append(f'build.json records tools commit {shown(recorded_tools, build.SHA1_RE)}, this checkout is '
                         f'{tools_commit}; the image set\'s build identity and directory name include it')
    elif commit is None:
        raise UsageError('--resolved-manifest needs --build-json (the image set\'s build.json) or --manifest-commit '
                         '(the manifest commit the build recorded)')
    if 'revision' in declared and declared['revision'] != commit:
        raise UsageError(f'the build environment pins manifest revision {declared["revision"]}, '
                         f'but the build recorded manifest commit {commit}')
    return PinnedManifest(data=data, sha256=sha256, manifest_commit=commit, build_id=build_id,
                          build_json_sha256=record_sha256, expected_source=expected, notes=tuple(notes))


def branch_refspec(branch: str) -> str:
    """Where repo keeps the declared manifest branch (or tag) in the manifest checkout."""
    if branch.startswith('refs/tags/'):
        return f'+{branch}:{branch}'
    return f'+refs/heads/{branch}:refs/remotes/origin/{branch}'


def checkout_manifest_commit(source: Path, url: str, branch: str, commit: str, echo=print, run=git) -> None:
    """Put the manifest checkout at ``commit``, which must be in the history of ``branch``.

    The commit is never fetched by its id: a host may serve a commit that is
    on no branch of the repository, such as a fork's commit on GitHub. A
    shallow checkout (a shallow workspace) first gets the branch's whole
    history, which for the manifest is small.
    """
    manifests = source / '.repo/manifests'
    if run(['-C', manifests, 'rev-parse', '--is-shallow-repository']).stdout.strip() == 'true':
        echo(f'    fetching the history of {branch} to find the recorded manifest commit')
        result = run(['-C', manifests, '-c', 'http.version=HTTP/1.1', 'fetch', '--quiet', '--unshallow',
                      '--no-tags', url, branch_refspec(branch)], timeout=1800)
        if result.returncode:
            raise BuildStepError(f'cannot fetch the history of {branch} from {url}: {tail(result)}')
    if run(['-C', manifests, 'rev-parse', '--verify', '-q', commit + '^{commit}']).returncode:
        raise BuildStepError(f'the recorded manifest commit {commit} is not in the history of {branch} ({url})')
    result = run(['-C', manifests, 'checkout', '--quiet', '--detach', commit])
    if result.returncode:
        raise BuildStepError(f'cannot check out the recorded manifest commit {commit}: {tail(result)}')


def manifest_shape(data: bytes):
    """A manifest without its projects' revisions: the part "repo manifest"
    and "repo manifest -r" print alike."""
    def shape(element):
        attributes = dict(element.attrib)
        if element.tag == 'project':
            for name in RESOLVED_ATTRIBUTES:
                attributes.pop(name, None)
        return element.tag, sorted(attributes.items()), (element.text or '').strip(), \
            [shape(child) for child in element]
    return shape(ET.fromstring(data))


def check_against_manifest(pinned: bytes, manifest: bytes, commit: str) -> None:
    """The pinned resolved manifest must be the recorded commit's manifest
    ("repo manifest") with only the project revisions filled in: the same
    remotes, defaults, projects, groups and copy and link files. It can then
    choose commits, but not where the source comes from or what is checked out."""
    if manifest_shape(pinned) == manifest_shape(manifest):
        return
    paths = lambda data: {p.get('path', p.get('name')) for p in ET.fromstring(data).findall('project')}
    extra, missing = sorted(paths(pinned) - paths(manifest)), sorted(paths(manifest) - paths(pinned))
    if extra or missing:
        detail = '; '.join(part for part in (
            'projects not in that manifest: ' + ', '.join(extra[:5]) if extra else '',
            'projects missing from it: ' + ', '.join(missing[:5]) if missing else '') if part)
    else:
        detail = 'its remotes, defaults or project settings differ'
    raise BuildStepError(f'the pinned resolved manifest does not belong to manifest commit {commit}: {detail}')


# -------------------------------------------------- shallow prefetch

@dataclass(frozen=True)
class Project:
    name: str
    path: str
    url: str | None
    revision: str | None


@dataclass(frozen=True)
class PrefetchTarget:
    path: str
    name: str
    url: str
    refspec: str
    # Must resolve once the fetch is done.
    want: str


def manifest_projects(data: bytes) -> dict:
    """Path -> Project for the top-level projects of a flat manifest."""
    root = ET.fromstring(data)
    remotes = {remote.get('name'): remote for remote in root.findall('remote')}
    element = root.find('default')
    default = element.attrib if element is not None else {}
    projects = {}
    for project in root.findall('project'):
        name = project.get('name')
        path = project.get('path', name)
        remote = remotes.get(project.get('remote', default.get('remote')))
        fetch = remote.get('fetch') if remote is not None else None
        revision = (project.get('revision') or (remote.get('revision') if remote is not None else None)
                    or default.get('revision'))
        projects[path] = Project(name, path, fetch.rstrip('/') + '/' + name if fetch else None, revision)
    return projects


def prefetch_settings(config: dict) -> dict:
    """config/fp6-build.json shallow_prefetch, checked."""
    settings = config.get('shallow_prefetch')
    if not isinstance(settings, dict) or set(settings) != PREFETCH_KEYS:
        raise UsageError('config/fp6-build.json shallow_prefetch must have exactly: '
                         + ', '.join(sorted(PREFETCH_KEYS)))
    projects = settings['projects']
    if (not isinstance(projects, list) or not all(safe_path(p) for p in projects)
            or len(set(projects)) != len(projects)):
        raise UsageError('config/fp6-build.json shallow_prefetch.projects must be distinct relative paths')
    for key, (low, high) in PREFETCH_LIMITS.items():
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise UsageError(f'config/fp6-build.json shallow_prefetch.{key} must be an integer from {low} to {high}')
    return settings


def prefetch_targets(data: bytes, paths, echo=print) -> list:
    """What to fetch for each configured project: its commit, or its tag."""
    projects = manifest_projects(data)
    targets = []
    for path in paths:
        project = projects.get(path)
        if project is None:
            echo(f'    {path}: not in the manifest; nothing to prefetch')
            continue
        if not project.url or not project.url.startswith('https://') or not safe_path(project.name):
            raise BuildStepError(f'{path}: the manifest gives it no HTTPS fetch URL')
        revision = project.revision or ''
        if build.SHA1_RE.fullmatch(revision):
            targets.append(PrefetchTarget(path, project.name, project.url, revision, revision + '^{commit}'))
        elif TAG.fullmatch(revision) and '..' not in revision and not revision.endswith(('/', '.lock')):
            targets.append(PrefetchTarget(path, project.name, project.url, f'+{revision}:{revision}',
                                          revision + '^{commit}'))
        else:
            echo(f'    {path}: follows {revision or "no revision"}, not a commit or tag; repo fetches it')
    return targets


def git_directories(source: Path, project) -> tuple:
    """repo's git directory and object directory for a project."""
    return (source / '.repo/projects' / (project.path + '.git'),
            source / '.repo/project-objects' / (project.name + '.git'))


def remove_partial_packs(directory: Path) -> None:
    pack = (directory / 'objects').resolve() / 'pack'
    if pack.is_dir():
        for path in [*pack.glob('tmp_pack_*'), *pack.glob('tmp_idx_*')]:
            path.unlink(missing_ok=True)


def fetch_target(source: Path, target: PrefetchTarget, settings: dict, echo=print, run=git, wait=None) -> None:
    """Fetch one revision at depth 1 where repo will look for it.

    An existing project git directory gets the fetch; otherwise the project's
    object directory does. repo (the version the environment pins) creates a
    missing git directory from the object directory, copying its refs and
    shallow file, so it finds the revision and does not fetch the project.
    ``wait(seconds)`` sleeps between attempts and returns True to stop.
    """
    wait = wait or threading.Event().wait
    gitdir, objdir = git_directories(source, target)
    destination = gitdir if gitdir.is_dir() else objdir
    resolves = lambda: run(['-C', destination, 'rev-parse', '--verify', '-q', target.want]).returncode == 0
    if destination.is_dir() and resolves():
        echo(f'    {target.path}: already fetched')
        return
    if not destination.is_dir():
        result = run(['init', '--quiet', '--bare', '--template=', destination])
        if result.returncode:
            raise BuildStepError(f'{target.path}: cannot create {destination}: {tail(result)}')
    command = ['-C', destination, '-c', 'http.version=HTTP/1.1',
               '-c', f'http.lowSpeedLimit={settings["low_speed_limit_bytes"]}',
               '-c', f'http.lowSpeedTime={settings["low_speed_time_seconds"]}', '-c', 'gc.auto=0',
               'fetch', '--depth=1', '--no-tags', '--quiet', target.url, target.refspec]
    attempts = settings['attempts']
    for attempt in range(1, attempts + 1):
        echo(f'    {target.path}: fetching at depth 1 (attempt {attempt} of {attempts})')
        result = run(command, timeout=FETCH_TIMEOUT)
        if result.returncode == 0:
            if not resolves():
                raise BuildStepError(f'{target.path}: the fetch finished but {target.want} is missing')
            echo(f'    {target.path}: fetched')
            return
        remove_partial_packs(destination)
        if attempt < attempts and wait(settings['retry_delay_seconds']):
            break
    raise BuildStepError(f'{target.path}: the depth-1 fetch did not finish in {attempts} attempts: {tail(result)}')


def prefetch(source: Path, targets, settings: dict, echo=print, run=git, wait=None) -> None:
    """Fetch the targets, ``settings['jobs']`` at a time; every failure is reported."""
    stop = threading.Event()
    wait = wait or stop.wait

    def one(target):
        try:
            fetch_target(source, target, settings, echo, run, wait)
        except BuildStepError as error:
            return str(error)
        return None

    with ThreadPoolExecutor(max_workers=settings['jobs']) as pool:
        try:
            failures = [message for message in pool.map(one, targets) if message]
        except BaseException:
            stop.set()
            raise
    if failures:
        raise BuildStepError('prefetching the large projects failed:\n  ' + '\n  '.join(failures)
                             + '\nRun the sync again; it starts these fetches over.')


def has_refs(directory: Path) -> bool:
    """Whether a git directory holds any ref (loose or packed)."""
    for _root, _dirs, files in os.walk(directory / 'refs'):
        if files:
            return True
    try:
        packed = (directory / 'packed-refs').read_text(errors='replace')
    except OSError:
        return False
    return any(line.strip() and not line.startswith(('#', '^')) for line in packed.splitlines())


def _has_checkout(worktree: Path, path: str, paths) -> bool:
    """Whether a project's work tree holds anything but its .git link and
    the directories of projects nested in it."""
    if worktree.is_symlink() or (worktree.exists() and not worktree.is_dir()):
        return True
    if not worktree.is_dir():
        return False
    for entry in worktree.iterdir():
        relative = path + '/' + entry.name
        if entry.name != '.git' and not any(p == relative or p.startswith(relative + '/') for p in paths):
            return True
    return False


def clear_half_initialised(source: Path, projects: dict) -> list:
    """Remove project git directories an interrupted sync created but never filled.

    repo treats an existing git directory without a shallow file as one the
    user unshallowed and fetches it with its whole history. A git directory
    with no refs and no shallow file holds nothing repo fetched. Removing it,
    its object directory when no other project shares it and holds no refs
    either, and its empty work tree, lets repo create the project again as a
    new shallow one. Returns the cleared project paths.
    """
    names = Counter(project.name for project in projects.values())
    paths = set(projects)
    cleared = []
    for path, project in sorted(projects.items()):
        gitdir, objdir = git_directories(source, project)
        if (gitdir.is_symlink() or not gitdir.is_dir() or (gitdir / 'shallow').exists() or has_refs(gitdir)
                or _has_checkout(source / path, path, paths)):
            continue
        shutil.rmtree(gitdir)
        if (names[project.name] == 1 and objdir.is_dir() and not objdir.is_symlink()
                and not (objdir / 'shallow').exists() and not has_refs(objdir)):
            shutil.rmtree(objdir)
        worktree = source / path
        if worktree.is_dir():
            dotgit = worktree / '.git'
            if dotgit.is_dir() and not dotgit.is_symlink():
                shutil.rmtree(dotgit)
            else:
                dotgit.unlink(missing_ok=True)
            if not any(worktree.iterdir()):
                worktree.rmdir()
        cleared.append(path)
    return cleared
