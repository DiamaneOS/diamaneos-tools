"""Source sync helpers for "diamaneos build sync".

Reproducing a recorded build (--resolved-manifest): the sync checks out every
project at the commit an image set's resolved manifest records, with the
manifest checkout at the manifest commit that build recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
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
BUILD_ID = re.compile(r'[A-Za-z0-9._-]{1,128}')
SHA256 = re.compile(r'[0-9a-f]{64}')


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
