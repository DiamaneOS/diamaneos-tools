"""One build workspace: layout, step state, lock, host checks and command runs.

The public build commands keep everything in one directory owned by the
invoking user. Each step records what it consumed and produced in
``state/<step>.json``; a rerun skips a step whose inputs are unchanged and
whose outputs still verify.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from . import process

ROOT = Path(__file__).resolve().parents[2]
STEPS = ('sync', 'kernel', 'vendor', 'android', 'package', 'verify')
STATE_SCHEMA = 1
GIB = 1024 ** 3
# unshare(1) >= 2.38. The mapped user keeps its own uid, so the Android build,
# which refuses to run as root, sees an ordinary user without network access.
ISOLATION = ['unshare', '--user', '--map-current-user', '--net', '--']
LONG_TIMEOUT = 48 * 3600
# Full build logs are large; keep the whole stream in the log, a tail in memory.
MAX_LOG_BYTES = 64 * GIB


class BuildStepError(RuntimeError):
    """A step failed; ``exit_code`` follows the command's documented codes."""
    exit_code = 4


class UsageError(BuildStepError):
    exit_code = 2


class HostError(BuildStepError):
    exit_code = 3


class CheckFailed(BuildStepError):
    exit_code = 1


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def write_atomic(path: Path, data: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def load_config(name: str) -> tuple[dict, bytes]:
    raw = (ROOT / 'config' / name).read_bytes()
    return json.loads(raw), raw


def default_workspace() -> Path:
    value = os.environ.get('DIAMANEOS_WORKSPACE')
    return Path(value) if value else Path.home() / 'diamaneos-build'


class Workspace:
    """Paths and state of one build workspace."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().absolute()
        self.src = self.root / 'src'
        self.kernel = self.root / 'kernel'
        self.stock = self.root / 'stock'
        self.stock_images = self.stock / 'images'
        self.stock_files = self.stock / 'files'
        self.vendor = self.root / 'vendor'
        self.cache = self.root / 'cache'
        self.trust = self.cache / 'trust'
        self.logs = self.root / 'logs'
        self.state_dir = self.root / 'state'
        self.work = self.root / 'work'
        self.images = self.root / 'images'

    def exists(self) -> bool:
        return self.root.is_dir()

    def state(self, step: str) -> dict | None:
        path = self.state_dir / (step + '.json')
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(path.read_bytes())
        except ValueError:
            return None
        if not isinstance(value, dict) or value.get('schema_version') != STATE_SCHEMA or value.get('step') != step:
            return None
        return value

    def passed(self, step: str) -> dict | None:
        value = self.state(step)
        return value if value and value.get('status') == 'PASS' else None

    def write_state(self, step: str, value: dict) -> None:
        write_atomic(self.state_dir / (step + '.json'), encoded(dict(value, schema_version=STATE_SCHEMA, step=step)))

    def new_log(self, step: str) -> Path:
        self.logs.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        path = self.logs / f'{step}-{stamp}.log'
        counter = 1
        while path.exists():
            path = self.logs / f'{step}-{stamp}-{counter}.log'
            counter += 1
        return path

    @contextmanager
    def lock(self):
        if self.root.is_symlink():
            raise UsageError('the workspace must not be a symlink')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        fd = os.open(self.root / '.workspace.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise UsageError('the workspace lock is not a regular file')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UsageError('another build command is using this workspace') from None
            yield


@dataclass
class Action:
    """One thing a step does: run a command, or call tools code."""
    description: str
    argv: list | None = None
    func: object = None
    cwd: Path | None = None
    env: dict = field(default_factory=dict)
    unset: tuple = ()
    network: bool = False
    compile: bool = False

    def text(self, isolated: bool) -> str:
        if self.argv is None:
            return self.description
        prefix = ' '.join([f'-u {name}' for name in self.unset] + [f'{k}={shlex.quote(str(v))}' for k, v in sorted(self.env.items())])
        prefix = 'env ' + prefix if prefix else ''
        argv = [str(a) for a in self.argv]
        if isolated and not self.network:
            argv = ISOLATION + argv
        command = shlex.join(argv)
        where = f'(in {self.cwd}) ' if self.cwd else ''
        return f'{self.description}: {where}{prefix + " " if prefix else ""}{command}'


def isolation_available() -> bool:
    if platform.system() != 'Linux' or shutil.which('unshare') is None:
        return False
    try:
        result = subprocess.run(ISOLATION + ['true'], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class Runner:
    """Runs actions with logging; network-off for compile steps by default."""

    def __init__(self, allow_network: bool = False, echo=print):
        self.allow_network = allow_network
        self.echo = echo

    def isolated(self, action: Action) -> bool:
        return not action.network and not self.allow_network and action.argv is not None

    def run(self, action: Action, log: Path):
        self.echo('  ' + action.description)
        with log.open('ab') as stream:
            stream.write(('\n## ' + action.text(self.isolated(action)) + '\n').encode())
        if action.argv is None:
            return action.func()
        if action.compile and os.environ.get('DIAMANEOS_THERMAL_CHECK'):
            check = os.environ['DIAMANEOS_THERMAL_CHECK']
            result = subprocess.run([check], capture_output=True, timeout=60)
            if result.returncode:
                raise HostError('the thermal check refused the build: ' + check)
        argv = [str(a) for a in action.argv]
        if self.isolated(action):
            argv = ISOLATION + argv
        env = {k: v for k, v in os.environ.items() if k not in action.unset}
        env.update({k: str(v) for k, v in action.env.items()})
        partial = log.with_suffix('.part')
        partial.unlink(missing_ok=True)
        result = process.run(argv, LONG_TIMEOUT, MAX_LOG_BYTES, cwd=action.cwd, env=env,
                             log_path=partial, capture_bytes=32768)
        with log.open('ab') as stream, partial.open('rb') as source:
            shutil.copyfileobj(source, stream)
        partial.unlink(missing_ok=True)
        if result['transport'] == 'interrupted':
            raise KeyboardInterrupt
        if result['transport'] != 'ok':
            tail = result['stdout'][-4000:].decode('utf-8', 'replace').strip()
            raise BuildStepError(f'{action.description} failed ({result["transport"]}). '
                                 f'Last output:\n{tail}\nFull log: {log}')
        return result


def os_release(path: Path = Path('/etc/os-release')) -> dict:
    values = {}
    try:
        for line in path.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def memory_bytes(path: Path = Path('/proc/meminfo')) -> int | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith('MemTotal:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def case_sensitive(directory: Path) -> bool:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory, prefix='.case-') as temporary:
        (Path(temporary) / 'a').write_bytes(b'')
        return not (Path(temporary) / 'A').exists()


def package_versions(names) -> dict | None:
    if shutil.which('dpkg-query') is None:
        return None
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n', *sorted(names)],
                            capture_output=True, text=True, timeout=60)
    versions = {}
    for line in result.stdout.splitlines():
        package, _, version = line.partition('\t')
        versions[package.split(':')[0]] = version
    return versions


# Commands each step runs itself; checked before a step starts.
STEP_COMMANDS = {
    'sync': ('git', 'repo', 'ssh-keygen', 'gpg'),
    'kernel': ('git', 'bash', 'make', 'perl', 'openssl', 'modinfo', 'modprobe', 'nm', 'readelf'),
    'vendor': ('bash', 'zip', 'unzip'),
    'android': ('bash', 'zip', 'unzip', 'rsync'),
    'package': (),
    'verify': (),
}


def check_host(workspace: Workspace, steps, environment: dict, build_config: dict,
               need_isolation: bool, probe=None) -> dict:
    """Stop early with every missing requirement; record the host for build.json.

    ``probe`` replaces system queries in tests.
    """
    probe = probe or {}
    problems, warnings = [], []
    system = probe.get('system', platform.system())
    machine = probe.get('machine', platform.machine())
    if system != 'Linux' or machine != environment['host']['architecture']:
        problems.append(f'needs Linux on {environment["host"]["architecture"]} (found {system} {machine})')
    if probe.get('python', sys.version_info[:2]) < (3, 11):
        problems.append('needs Python 3.11 or newer')
    which = probe.get('which', shutil.which)
    missing = sorted({c for step in steps for c in STEP_COMMANDS.get(step, ()) if which(c) is None})
    if need_isolation and which('unshare') is None:
        missing.append('unshare')
    if missing:
        problems.append('missing commands: ' + ', '.join(missing))
    memory = probe.get('memory', memory_bytes())
    minimum = environment['host']['minimum_memory_bytes']
    if memory is not None and memory < minimum:
        problems.append(f'needs at least {minimum // GIB} GiB of RAM (found {memory // GIB} GiB)')
    elif memory is not None and memory < 2 * minimum:
        warnings.append(f'{memory // GIB} GiB of RAM works but {2 * minimum // GIB} GiB is recommended')
    workspace.root.mkdir(parents=True, exist_ok=True)
    needed = sum(build_config['disk_estimate_gib'].get(step, 0) for step in steps) * GIB
    free = probe.get('free', shutil.disk_usage(workspace.root).free)
    if free < needed:
        problems.append(f'needs about {needed // GIB} GiB free in {workspace.root} for these steps '
                        f'(found {free // GIB} GiB)')
    if not probe.get('case_sensitive', case_sensitive(workspace.root)):
        problems.append(f'{workspace.root} must be on a case-sensitive filesystem')
    if need_isolation and not probe.get('isolation', isolation_available()):
        problems.append('unprivileged user namespaces are not available, so the build cannot run '
                        'with network access off. Enable them (see the build guide) or pass '
                        '--allow-network to build with network access (recorded in build.json)')
    release = probe.get('os_release', os_release())
    host = environment['host']
    if (release.get('ID'), release.get('VERSION_ID')) != (host['os_id'], host['os_version_id']):
        warnings.append(f'tested on {host["os_id"]} {host["os_version_id"]}; this host is '
                        f'{release.get("ID", "unknown")} {release.get("VERSION_ID", "")}'.rstrip())
    versions = probe.get('packages', package_versions(host['required_packages']))
    deviations = None
    if versions is not None:
        deviations = {name: {'pinned': pin, 'installed': versions.get(name)}
                      for name, pin in sorted(host['required_packages'].items()) if versions.get(name) != pin}
    if problems:
        raise HostError('this host cannot run the build:\n  - ' + '\n  - '.join(problems))
    return {'system': system, 'machine': machine, 'os_id': release.get('ID'),
            'os_version_id': release.get('VERSION_ID'), 'kernel': probe.get('kernel', platform.release()),
            'cpu_count': probe.get('cpus', os.cpu_count()), 'memory_bytes': memory,
            'package_deviations': deviations, 'warnings': warnings}


def default_jobs(requested: int | None, memory: int | None, cap: int | None = None) -> int:
    """Parallel jobs: the request, or the CPU count limited to 2 GiB of RAM per job."""
    jobs = requested or os.cpu_count() or 1
    if requested is None and memory:
        jobs = min(jobs, max(1, memory // (2 * GIB)))
    if cap:
        jobs = min(jobs, cap)
    return max(1, jobs)
