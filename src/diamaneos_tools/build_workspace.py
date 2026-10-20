"""One build workspace: layout, step state, lock, host checks and command runs.

The public build commands keep everything in one directory owned by the
invoking user. Each step records what it consumed and produced in
``state/<step>.json``. A rerun skips a step whose inputs are unchanged and
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
STEPS = ('sync', 'vendor', 'android', 'package', 'verify')
# Steps outside "build all": building the kernel from source is for maintainers
# (the Android build uses the kernel prebuilts in the manifest).
MAINTAINER_STEPS = ('kernel',)
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
# Full build logs are large. Keep the whole stream in the log, a tail in memory.
MAX_LOG_BYTES = 64 * GIB


class BuildStepError(RuntimeError):
    """A step failed. ``exit_code`` follows the command's documented codes."""
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


def tools_checkout(tools: Path = ROOT) -> Path | None:
    """The source checkout these tools run from (<checkout>/tools/diamaneos), whatever its name, or None."""
    checkout = tools.parent.parent
    if (tools.name, tools.parent.name) == ('diamaneos', 'tools') and (checkout / '.repo').is_dir():
        return checkout
    return None


def beside(checkout: Path) -> Path:
    """The default workspace of a checkout: the directory that holds it as src, else <checkout>-build."""
    if checkout.name == 'src' and checkout.parent not in (Path(checkout.anchor), Path.home()):
        return checkout.parent
    return checkout.parent / (checkout.name + '-build')


def locate(given: str | os.PathLike | None = None, tools: Path = ROOT) -> 'Workspace':
    """The workspace and its source.

    The workspace is ``given``, else $DIAMANEOS_WORKSPACE, else the default of
    the checkout these tools run from, else ~/diamaneos-build. The source is the
    workspace's src directory when it has one, else that checkout."""
    checkout = tools_checkout(tools)
    value = given or os.environ.get('DIAMANEOS_WORKSPACE')
    if value:
        root = Path(value).expanduser().absolute()
        src = root / 'src'
        if checkout is not None and not (src.exists() or src.is_symlink()):
            src = checkout
    elif checkout is not None:
        root, src = beside(checkout), checkout
        if root.parent == checkout.parent and os.stat(root.parent).st_dev != os.stat(checkout).st_dev:
            raise UsageError(f'{root}, the default workspace beside the checkout, is on another filesystem than '
                             f'{checkout}. Pass --workspace with a directory on the checkout\'s disk')
    else:
        root = Path.home() / 'diamaneos-build'
        src = root / 'src'
    return Workspace(root, src)


def default_workspace(tools: Path = ROOT) -> Path:
    return locate(tools=tools).root


class Workspace:
    """Paths and state of one build workspace."""

    def __init__(self, root: Path, src: Path | None = None):
        self.root = Path(root).expanduser().absolute()
        self.src = Path(src).absolute() if src is not None else self.root / 'src'
        self.kernel = self.root / 'kernel'
        self.stock = self.root / 'stock'
        self.stock_images = self.stock / 'images'
        self.stock_files = self.stock / 'files'
        self.vendor = self.root / 'vendor'
        self.cache = self.root / 'cache'
        self.logs = self.root / 'logs'
        self.state_dir = self.root / 'state'
        self.work = self.root / 'work'
        self.images = self.root / 'images'

    def relative(self, path: Path) -> str:
        """A path as the state records it: relative to the workspace (src/out/... in the usual layout)."""
        return os.path.relpath(path, self.root)

    def exists(self) -> bool:
        return self.root.is_dir()

    def check_root(self) -> None:
        """Refuse a source checkout as the workspace, before anything is created in it."""
        if self.root.is_symlink():
            raise UsageError('the workspace must not be a symlink')
        if (self.root / '.repo').exists() or (self.root / '.repo').is_symlink():
            raise UsageError(f'{self.root} is a source checkout. The workspace is another directory, for images, logs '
                             f'and build state. Leave out --workspace to use {beside(self.root)}, or name another '
                             'directory')
        if self.root.is_relative_to(self.src):
            raise UsageError(f'the workspace {self.root} is inside the source checkout {self.src}. Name a directory '
                             'outside it, such as ' + str(beside(self.src)))
        own = (self.kernel, self.stock, self.vendor, self.cache, self.logs, self.state_dir, self.work, self.images)
        if any(self.src.is_relative_to(path) for path in own):
            raise UsageError(f'the source checkout {self.src} is where the workspace {self.root} keeps its own '
                             'files. Name another workspace directory')

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
        """One build command per workspace. The lock file is removed when the
        command ends, and so is a workspace directory this command created and
        left empty."""
        self.check_root()
        created = not self.root.exists()
        path = self.root / '.workspace.lock'
        handle = None
        try:
            while handle is None:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
                try:
                    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                except FileNotFoundError:
                    # A command that ended just now removed the empty workspace.
                    continue
                candidate = os.fdopen(fd, 'rb')
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise UsageError('the workspace lock is not a regular file')
                    try:
                        fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise UsageError('another build command is using this workspace') from None
                    # A command that just ended removed its lock file: hold
                    # the file the path names now, or two commands could run.
                    try:
                        current = os.path.samestat(os.fstat(fd), os.stat(path, follow_symlinks=False))
                    except FileNotFoundError:
                        current = False
                    if current:
                        handle, candidate = candidate, None
                finally:
                    if candidate is not None:
                        candidate.close()
            yield
        finally:
            if handle is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                handle.close()
            if created:
                try:
                    self.root.rmdir()
                except OSError:
                    pass


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
    # What the action does in more words. The log and the dry run show it,
    # the terminal only the short description.
    detail: str = ''

    def text(self, isolated: bool) -> str:
        title = f'{self.description} ({self.detail})' if self.detail else self.description
        if self.argv is None:
            return title
        prefix = ' '.join([f'-u {name}' for name in self.unset] + [f'{k}={shlex.quote(str(v))}' for k, v in sorted(self.env.items())])
        prefix = 'env ' + prefix if prefix else ''
        argv = [str(a) for a in self.argv]
        if isolated and self.compile:
            argv = ISOLATION + argv
        command = shlex.join(argv)
        where = f'(in {self.cwd}) ' if self.cwd else ''
        return f'{title}: {where}{prefix + " " if prefix else ""}{command}'


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
    """Runs actions with logging, network-off for compile steps by default."""

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
        # The output joins the step log when the command ends.
        self.echo(f'    live output: {partial}')
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


# Commands each step runs itself, checked before a step starts.
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
    """Bytes the given steps still need. Steps whose output already exists
    (a resumed or repeated run) reuse most of their space."""
    return sum(build_config['disk_estimate_gib'].get(step, 0) for step in steps if step not in present) * GIB


def check_host(workspace: Workspace, steps, environment: dict, build_config: dict,
               need_isolation: bool, probe=None, present=()) -> dict:
    """Stop early with every missing requirement, and record the host for build.json.

    ``steps`` are the steps that will run, ``present`` those of them whose
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
    # A source checkout on another filesystem needs the same checks.
    places = [workspace.root]
    if workspace.src.is_dir() and os.stat(workspace.src).st_dev != os.stat(workspace.root).st_dev:
        places.append(workspace.src)
    for place in places:
        free = probe.get('free', shutil.disk_usage(place).free)
        if free < needed:
            problems.append(f'needs about {needed // GIB} GiB free in {place} for these steps '
                            f'(found {free // GIB} GiB)')
        if not probe.get('case_sensitive', case_sensitive(place)):
            problems.append(f'{place} must be on a case-sensitive filesystem')
    if need_isolation:
        reason = probe['isolation'] if 'isolation' in probe else isolation_problem()
        if reason:
            problems.append(f'the build cannot compile with network access off: {reason}. Fix that '
                            '(see the build guide) or pass --allow-network to compile with network '
                            'access (recorded in build.json)')
    release = probe.get('os_release', os_release())
    host = environment['host']
    if (release.get('ID'), release.get('VERSION_ID')) != (host['os_id'], host['os_version_id']):
        warnings.append(f'tested on {host["os_id"]} {host["os_version_id"]}. This host is '
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
