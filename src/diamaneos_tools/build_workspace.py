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
import importlib.util
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
# kmod installs modinfo and modprobe under /usr/sbin on Debian.
SBIN = ('/usr/sbin', '/sbin')
# Third-party Python modules the build path imports (vendor product
# generation validates its recipes with jsonschema).
PYTHON_MODULES = {'jsonschema': 'python3-jsonschema'}
# MemTotal reports less than the installed RAM (firmware and kernel reserve
# some), so a 32 GB machine passes a 32 GiB floor with this margin.
MEMORY_MARGIN = 2 * GIB
# Variables that lead to local sockets or services (agents, desktop buses,
# container daemons). A network-off build has no use for them.
SCRUBBED = ('SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'GPG_AGENT_INFO', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY',
            'DISPLAY', 'XAUTHORITY', 'DOCKER_HOST', 'CONTAINER_HOST', 'PULSE_SERVER', 'KRB5CCNAME')
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
    # Compile actions run in a network namespace (see Runner.isolated).
    compile: bool = False

    def text(self, isolated: bool) -> str:
        if self.argv is None:
            return self.description
        prefix = ' '.join([f'-u {name}' for name in self.unset] + [f'{k}={shlex.quote(str(v))}' for k, v in sorted(self.env.items())])
        prefix = 'env ' + prefix if prefix else ''
        argv = [str(a) for a in self.argv]
        if isolated and self.compile:
            argv = ISOLATION + argv
        command = shlex.join(argv)
        where = f'(in {self.cwd}) ' if self.cwd else ''
        return f'{self.description}: {where}{prefix + " " if prefix else ""}{command}'


def tool_path(path: str | None = None) -> str:
    """PATH plus the sbin directories, where some distributions put kmod."""
    parts = [p for p in (path if path is not None else os.environ.get('PATH', '')).split(os.pathsep) if p]
    return os.pathsep.join(parts + [d for d in SBIN if d not in parts])


def which(command: str) -> str | None:
    return shutil.which(command, path=tool_path())


def isolation_problem() -> str | None:
    """Why compilation cannot run in a network namespace here, or None."""
    if platform.system() != 'Linux':
        return 'network namespaces need Linux'
    if which('unshare') is None:
        return 'unshare (util-linux) is missing'
    try:
        usage = subprocess.run(['unshare', '--help'], capture_output=True, text=True, timeout=30).stdout
        if '--map-current-user' not in usage:
            return 'unshare is older than util-linux 2.38 (it has no --map-current-user)'
        result = subprocess.run(ISOLATION + ['true'], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 'unshare could not be run'
    if result.returncode:
        return 'unprivileged user namespaces are turned off on this host'
    return None


def isolation_available() -> bool:
    return isolation_problem() is None


def scrubbed_environment(env: dict) -> dict:
    return {k: v for k, v in env.items()
            if k not in SCRUBBED and not k.startswith('DBUS_') and not k.endswith('_SOCK')}


class Runner:
    """Runs actions with logging; network-off for compile steps by default."""

    def __init__(self, allow_network: bool = False, echo=print, tmpdir: Path | None = None):
        self.allow_network = allow_network
        self.echo = echo
        # Large temporary files belong in the workspace, not in /tmp.
        self.tmpdir = tmpdir

    def isolated(self, action: Action) -> bool:
        """Compilation runs without network access unless the user allowed it."""
        return action.compile and not action.network and not self.allow_network and action.argv is not None

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
        if action.compile:
            env = scrubbed_environment(env)
        env['PATH'] = tool_path(env.get('PATH'))
        if self.tmpdir is not None:
            self.tmpdir.mkdir(parents=True, exist_ok=True)
            env['TMPDIR'] = str(self.tmpdir)
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


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def needed_space(build_config: dict, steps, present=()) -> int:
    """Bytes the given steps still need; steps whose output already exists
    (a resumed or repeated run) reuse most of their space."""
    return sum(build_config['disk_estimate_gib'].get(step, 0) for step in steps if step not in present) * GIB


def check_host(workspace: Workspace, steps, environment: dict, build_config: dict,
               need_isolation: bool, probe=None, present=()) -> dict:
    """Stop early with every missing requirement; record the host for build.json.

    ``steps`` are the steps that will run; ``present`` those of them whose
    output already exists. ``probe`` replaces system queries in tests.
    """
    probe = probe or {}
    problems, warnings = [], []
    system = probe.get('system', platform.system())
    machine = probe.get('machine', platform.machine())
    if system != 'Linux' or machine != environment['host']['architecture']:
        problems.append(f'needs Linux on {environment["host"]["architecture"]} (found {system} {machine})')
    if probe.get('python', sys.version_info[:2]) < (3, 11):
        problems.append('needs Python 3.11 or newer')
    find = probe.get('which', which)
    missing = sorted({c for step in steps for c in STEP_COMMANDS.get(step, ()) if find(c) is None})
    if need_isolation and find('unshare') is None:
        missing.append('unshare')
    if missing:
        problems.append('missing commands: ' + ', '.join(missing))
    has_module = probe.get('modules', module_available)
    modules = sorted(package for name, package in PYTHON_MODULES.items() if not has_module(name))
    if modules:
        problems.append('missing Python modules for ' + sys.executable + ': install ' + ', '.join(modules))
    memory = probe.get('memory', memory_bytes())
    minimum = environment['host']['minimum_memory_bytes']
    if memory is not None and memory < minimum - MEMORY_MARGIN:
        problems.append(f'needs at least {minimum // GIB} GB of RAM (found {memory // GIB} GiB)')
    elif memory is not None and memory < 2 * minimum - MEMORY_MARGIN:
        warnings.append(f'{memory // GIB} GiB of RAM works but {2 * minimum // GIB} GB is recommended')
    workspace.root.mkdir(parents=True, exist_ok=True)
    needed = needed_space(build_config, steps, present)
    free = probe.get('free', shutil.disk_usage(workspace.root).free)
    if free < needed:
        problems.append(f'needs about {needed // GIB} GiB free in {workspace.root} for these steps '
                        f'(found {free // GIB} GiB)')
    if not probe.get('case_sensitive', case_sensitive(workspace.root)):
        problems.append(f'{workspace.root} must be on a case-sensitive filesystem')
    if need_isolation:
        reason = probe['isolation'] if 'isolation' in probe else isolation_problem()
        if reason:
            problems.append(f'the build cannot compile with network access off: {reason}. Fix that '
                            '(see the build guide) or pass --allow-network to compile with network '
                            'access (recorded in build.json)')
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
