"""Shared helpers of the kernel pipeline: commands, locking and pinned checkouts."""
from contextlib import contextmanager
import fcntl
import os
import re
import stat

from . import process

SHA1 = re.compile(r'[0-9a-f]{40}')
FETCH_ATTEMPTS = 4
SPARSE_PATTERN = re.compile(r'!?/[A-Za-z0-9_.*/+-]*')


class KernelError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise KernelError(message)


@contextmanager
def locked(root):
    require(not root.is_symlink(), 'workspace cannot be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / '.preparation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        require(stat.S_ISREG(os.fstat(lock.fileno()).st_mode), 'workspace lock is not a regular file')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def call(argv, *, cwd, env=None, timeout=600, log=None):
    env = dict(os.environ) if env is None else dict(env)
    env['GIT_TERMINAL_PROMPT'] = '0'
    result = process.run(list(map(str, argv)), timeout, max_output_bytes=128*1024*1024,
                         cwd=cwd, env=env, log_path=log,
                         capture_bytes=16384 if log else None)
    if result['transport'] != 'ok':
        output = b''.join(result.get(k) or b'' for k in ('stdout', 'stderr'))
        tail = output.decode('utf-8', 'replace').strip()[-2000:] if isinstance(output, bytes) else ''
        raise KernelError('command failed: ' + ' '.join(map(str, argv[:4])) + ' (' + result['transport'] + ')'
                          + (': ' + tail if tail else '. Inspect the command log'))
    return result['stdout'].decode()


def git(path, *args):
    return call(['git', '-c', 'core.hooksPath=/dev/null', '-C', path, *args], cwd=path).strip()


def check_url(url):
    require(isinstance(url, str) and url.startswith('https://') and len(url) <= 512
            and not any(c.isspace() for c in url), 'source URL must use HTTPS: ' + str(url))


def has_commit(repository, revision):
    return process.run(['git', '-C', str(repository), 'cat-file', '-e', revision + '^{commit}'], 60,
                       cwd=repository)['transport'] == 'ok'


def tracked_changes(repository):
    return git(repository, 'diff', '--name-only') or git(repository, 'diff', '--cached', '--name-only')


def checkout_project(dest, row, fetch_from=None):
    """One project at its exact commit, in a Git repository of its own. A sparse
    project is a partial clone. fetch_from replaces the URL for the fetch only:
    the revision stays the pin, so the checkout is the same commit either way."""
    if not (dest / '.git').exists():
        require(not dest.exists() or not any(dest.iterdir()), 'unowned directory is occupied: ' + row['path'])
        dest.mkdir(parents=True, exist_ok=True)
        git(dest, 'init', '-q')
    elif has_commit(dest, 'HEAD'):
        require(not tracked_changes(dest), 'tracked source changes: ' + row['path'])
    url = fetch_from or row['url']
    if row.get('sparse'):
        git(dest, 'config', 'extensions.partialClone', 'origin')
        if process.run(['git', '-C', str(dest), 'remote', 'get-url', 'origin'], 60, cwd=dest)['transport'] == 'ok':
            git(dest, 'remote', 'set-url', 'origin', url)
        else:
            git(dest, 'remote', 'add', 'origin', url)
        git(dest, 'sparse-checkout', 'set', '--no-cone', *row['sparse'])
    if not has_commit(dest, row['revision']):
        source = 'origin' if row.get('sparse') else url
        extra = ['--filter=blob:none'] if row.get('sparse') else []
        for attempt in range(FETCH_ATTEMPTS):
            try:
                call(['git', '-c', 'http.version=HTTP/1.1', '-c', 'core.hooksPath=/dev/null',
                      '-c', 'protocol.file.allow=always', '-C', dest, 'fetch', '-q', '--depth=1',
                      '--no-tags', *extra, source, row['revision']], cwd=dest, timeout=5400)
                break
            except KernelError:
                if attempt == FETCH_ATTEMPTS - 1:
                    raise
    require(has_commit(dest, row['revision']), 'fetched source lacks the pinned commit: ' + row['path'])
    git(dest, 'checkout', '-q', '--detach', row['revision'])
    require(git(dest, 'rev-parse', 'HEAD') == row['revision'], 'source revision mismatch: ' + row['path'])


def verify_project(dest, row):
    """The checkout is the pinned commit with no changes and no untracked inputs."""
    require(git(dest, 'rev-parse', 'HEAD') == row['revision'], 'source revision mismatch: ' + row['path'])
    require(not git(dest, 'status', '--porcelain=v1', '--untracked-files=all'),
            'source changes or untracked inputs: ' + row['path'])
    if row.get('sparse'):
        require(git(dest, 'sparse-checkout', 'list').splitlines() == row['sparse'],
                'sparse checkout differs from the pin: ' + row['path'])


def commit_time(repository, revision):
    """The commit time of a pinned commit, the build date of every tree built from it."""
    value = git(repository, 'log', '-1', '--pretty=%ct', revision)
    require(value.isdigit() and int(value) > 0, 'invalid commit time for ' + revision)
    return value
