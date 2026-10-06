"""Prepare and build the pinned FP6 development kernel, modules and device trees.

The sources are one repository, DiamaneOS/kernel_qcom-6.1, at the commit pinned
in config/kernel-sources-fp6.json. It holds Qualcomm's kernel workspace layout
(kernel_platform/, vendor/) with the common kernel as a submodule; the
toolchains it lists in prebuilts.json are fetched at their pinned revisions.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from . import kernel_layout, process
from .vendor_extract import sha, relative
from .vendor import ROOT, load_json, VendorError, encoded

KMI = '--user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom'
STAMP = '--config=stamp'
# The GrapheneOS common kernel changes the GKI configuration and does not keep
# a comparable recorded GKI ABI, so no ABI comparison is run: every module is
# built from source with the kernel and signed with its key (MODULE_SIG_FORCE).
CORE = ['//common:kernel_aarch64', '//msm-kernel:fps_gki', '//msm-kernel:fps_gki_abi']
IMPLICIT = ['//common:kernel_aarch64_modules', '//common:kernel_aarch64_config']
# External module targets left out of the build, with the reason. Each must
# still exist, so a stale entry fails the build instead of hiding a new target.
EXCLUDED_MODULE_TARGETS = {}
MODULE_NAME = re.compile(r'[A-Za-z0-9_.-]+\.ko')
CONFIG_PROFILES = ('production', 'development')
SOURCE_PLAN = ROOT / 'config/kernel-sources-fp6.json'
SHA1 = re.compile(r'[0-9a-f]{40}')
# Files the tools keep in the workspace next to the sources. Git ignores them
# through the clone's own info/exclude, never through a committed file.
OWN_FILES = ('.preparation.lock', 'preparation.json', 'resolved-manifest.xml', 'runs', 'current')
EXCLUDES = ['/' + name + ('/' if name == 'runs' else '') for name in OWN_FILES] + ['/.publish-*']
# Bazel's convenience links next to its workspace; they point into the build output.
BAZEL_LINK = re.compile(r'kernel_platform/bazel-[A-Za-z0-9_.-]+')
# Headers that the core kernel and the vendor kernel tree each carry must stay
# byte-identical: structures in them cross the Image/module boundary, and a
# difference can change a RANDSTRUCT layout on one side only.
SHARED_HEADERS = ({'trees': ('kernel_platform/common', 'kernel_platform/msm-kernel'),
                   'paths': ('include/drm', 'include/uapi/drm')},)
FETCH_ATTEMPTS = 4


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
                          + (': ' + tail if tail else '; inspect the command log'))
    return result['stdout'].decode()


def git(path, *args):
    return call(['git', '-c', 'core.hooksPath=/dev/null', '-C', path, *args], cwd=path).strip()


def check_url(url):
    require(isinstance(url, str) and url.startswith('https://') and len(url) <= 512
            and not any(c.isspace() for c in url), 'source URL must use HTTPS: ' + str(url))


def configuration():
    plan = load_json(SOURCE_PLAN)
    require(isinstance(plan, dict) and set(plan) == {'repository', 'revision'}, 'invalid kernel source plan')
    check_url(plan['repository'])
    require(isinstance(plan['revision'], str) and SHA1.fullmatch(plan['revision']), 'invalid kernel source revision')
    return plan


def has_commit(repository, revision):
    return process.run(['git', '-C', str(repository), 'cat-file', '-e', revision + '^{commit}'], 60,
                       cwd=repository)['transport'] == 'ok'


def fetch(repository, url, revision):
    """Fetch one exact commit (no history) unless it is already present."""
    if has_commit(repository, revision):
        return
    for attempt in range(FETCH_ATTEMPTS):
        try:
            call(['git', '-c', 'http.version=HTTP/1.1', '-c', 'core.hooksPath=/dev/null', '-C', repository,
                  'fetch', '-q', '--depth=1', '--no-tags', url, revision], cwd=repository, timeout=5400)
            break
        except KernelError:
            if attempt == FETCH_ATTEMPTS - 1:
                raise
    require(has_commit(repository, revision), 'fetched source lacks the pinned commit: ' + revision)


def tracked_changes(repository, submodules=True):
    ignore = [] if submodules else ['--ignore-submodules=all']
    return git(repository, 'diff', '--name-only', *ignore) or git(repository, 'diff', '--cached', '--name-only', *ignore)


def checkout(repository, url, revision, label):
    """Bring one repository to an exact commit; never discard a caller's edits."""
    if not (repository / '.git').exists():
        require(not repository.exists() or not any(repository.iterdir()), 'unowned directory is occupied: ' + label)
        repository.mkdir(parents=True, exist_ok=True)
        git(repository, 'init', '-q')
    elif has_commit(repository, 'HEAD'):
        require(not tracked_changes(repository), 'tracked source changes: ' + label)
    fetch(repository, url, revision)
    git(repository, 'checkout', '-q', '--detach', revision)
    require(git(repository, 'rev-parse', 'HEAD') == revision, 'source revision mismatch: ' + label)


def checkout_source(root, plan):
    """The kernel repository itself, checked out in the workspace root."""
    if not (root / '.git').exists():
        occupied = sorted(p.name for p in root.iterdir() if p.name not in OWN_FILES)
        require(not occupied, 'the kernel workspace holds other files (' + ', '.join(occupied[:5]) +
                '); prepare an empty directory or move them aside')
        git(root, 'init', '-q')
        git(root, 'config', 'diamaneos.kernelsource', plan['repository'])
    require((root / '.git').is_dir() and not (root / '.git').is_symlink(), 'the kernel workspace .git is not a directory')
    marked = process.run(['git', '-C', str(root), 'config', '--get', 'diamaneos.kernelsource'], 60, cwd=root)
    shaped = all(process.run(['git', '-C', str(root), 'cat-file', '-e', 'HEAD:' + name], 60, cwd=root)['transport'] == 'ok'
                 for name in ('prebuilts.json', 'kernel_platform'))
    require(marked['transport'] == 'ok' or shaped, 'the workspace is another Git repository, not a kernel source checkout')
    exclude = root / '.git/info/exclude'
    exclude.parent.mkdir(exist_ok=True)
    present = exclude.read_text().splitlines() if exclude.is_file() else []
    missing = [line for line in EXCLUDES if line not in present]
    if missing:
        exclude.write_text('\n'.join(present + missing) + '\n')
    if has_commit(root, 'HEAD'):
        require(not tracked_changes(root, submodules=False), 'tracked source changes in the kernel workspace')
    fetch(root, plan['repository'], plan['revision'])
    git(root, 'checkout', '-q', '--detach', plan['revision'])
    require(git(root, 'rev-parse', 'HEAD') == plan['revision'], 'kernel source revision mismatch')


def gitlinks(root):
    """Path -> commit of every gitlink in the checked-out commit."""
    links = {}
    for line in git(root, 'ls-files', '--stage').splitlines():
        mode, revision, rest = line.split(' ', 2)
        if mode == '160000':
            links[rest.split('\t', 1)[1]] = revision
    return links


def submodule_plan(root):
    """(path, url, gitlink) for every submodule the top-level .gitmodules declares.

    Gitlinks it does not declare (edk2 carries its upstream's own) stay
    unpopulated; verify_source requires their directories to stay empty.
    """
    links = gitlinks(root)
    configured = {}
    if (root / '.gitmodules').is_file():
        listing = process.run(['git', '-C', str(root), 'config', '-f', '.gitmodules', '--get-regexp',
                               r'^submodule\..*\.path$'], 60, cwd=root)
        for line in listing['stdout'].decode().splitlines() if listing['transport'] == 'ok' else []:
            key, path = line.split(' ', 1)
            name = key[len('submodule.'):-len('.path')]
            configured[path] = git(root, 'config', '-f', '.gitmodules', '--get', 'submodule.' + name + '.url')
    require(set(configured) <= set(links), 'a submodule in .gitmodules is not in the tree')
    rows = []
    for path in sorted(configured):
        relative(path)
        check_url(configured[path])
        require(SHA1.fullmatch(links[path]), 'invalid submodule revision: ' + path)
        rows.append((path, configured[path], links[path]))
    return rows


def prebuilt_plan(root):
    """The toolchains prebuilts.json lists, each at an exact revision."""
    data = load_json(root / 'prebuilts.json')
    require(isinstance(data, dict) and set(data) == {'prebuilts'} and isinstance(data['prebuilts'], list)
            and data['prebuilts'], 'invalid prebuilts.json')
    rows = []
    for entry in data['prebuilts']:
        require(isinstance(entry, dict) and set(entry) == {'path', 'url', 'revision'}, 'invalid prebuilts.json entry')
        relative(entry['path'])
        require(entry['path'].startswith('kernel_platform/prebuilts/'), 'prebuilt outside kernel_platform/prebuilts')
        check_url(entry['url'])
        require(isinstance(entry['revision'], str) and SHA1.fullmatch(entry['revision']), 'invalid prebuilt revision')
        rows.append((entry['path'], entry['url'], entry['revision']))
    paths = [r[0] for r in rows]
    require(len(paths) == len(set(paths)) and not any(a != b and b.startswith(a + '/') for a in paths for b in paths),
            'duplicate or nested prebuilt paths')
    return rows


def nested_checkouts(root, rows):
    result = {}
    for path, url, revision in rows:
        dest = root / path
        for parent in [dest, *dest.parents]:
            if parent == root:
                break
            require(not parent.is_symlink(), 'source directory is a symlink: ' + path)
        checkout(dest, url, revision, path)
        result[path] = revision
    return result


def verify_source(root, plan, submodules, prebuilts):
    """The checkout is exactly the pinned commit, its submodules and toolchains."""
    require(git(root, 'rev-parse', 'HEAD') == plan['revision'], 'kernel source revision mismatch')
    status = call(['git', '-C', root, 'status', '--porcelain=v1', '-z', '--untracked-files=all',
                   '--ignore-submodules=none'], cwd=root)
    unexpected = []
    for entry in filter(None, status.split('\0')):
        path = entry[3:]
        if entry.startswith('?? ') and BAZEL_LINK.fullmatch(path) and (root / path).is_symlink():
            continue
        unexpected.append(path)
    require(not unexpected, 'kernel source changes or untracked inputs: ' + ', '.join(unexpected[:10]))
    require({p: r for p, _, r in submodule_plan(root)} == submodules, 'submodule pins differ from the preparation')
    for path in sorted(set(gitlinks(root)) - set(submodules)):
        # Git status does not look inside an unpopulated gitlink directory.
        directory = root / path
        require(not directory.is_symlink() and (not directory.exists() or
                                                (directory.is_dir() and not any(directory.iterdir()))),
                'undeclared submodule directory is not empty: ' + path)
    for path, revision in submodules.items():
        require(git(root / path, 'rev-parse', 'HEAD') == revision, 'submodule revision mismatch: ' + path)
    require({p: r for p, _, r in prebuilt_plan(root)} == prebuilts, 'prebuilt pins differ from the preparation')
    for path, revision in prebuilts.items():
        require(git(root / path, 'rev-parse', 'HEAD') == revision, 'prebuilt revision mismatch: ' + path)
        require(not git(root / path, 'status', '--porcelain=v1', '--untracked-files=all'),
                'prebuilt has local changes: ' + path)
        require(process.run(['git', '-C', str(root), 'check-ignore', '-q', path], 60, cwd=root)['transport'] == 'ok',
                'prebuilt is not ignored by the kernel repository: ' + path)


def tree_listing(root, tree, paths):
    """Mode, object and path (relative to ``tree``) of the committed files under ``paths``."""
    directory = (root / relative(tree)).resolve()
    top = Path(git(directory, 'rev-parse', '--show-toplevel')).resolve()
    prefix = directory.relative_to(top).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    listing = git(top, 'ls-tree', '-r', '--full-tree', 'HEAD', '--',
                  *[prefix + relative(p).as_posix() for p in paths])
    rows = []
    for line in listing.splitlines():
        meta, name = line.split('\t', 1)
        rows.append(meta + '\t' + name[len(prefix):])
    return rows


def shared_headers(root, groups=SHARED_HEADERS):
    checked = 0
    for group in groups:
        listings = []
        for tree in group['trees']:
            listing = tree_listing(root, tree, group['paths'])
            require(listing, 'shared headers missing: ' + tree)
            listings.append(listing)
        require(all(l == listings[0] for l in listings), 'shared headers differ between ' + ' and '.join(group['trees']))
        checked += len(listings[0])
    return checked


def kleaf_manifest(plan, submodules):
    """KLEAF_REPO_MANIFEST: the kernel trees Kleaf stamps with their revisions,
    with paths relative to kernel_platform (the Bazel workspace)."""
    manifest = ET.Element('manifest')
    for path, revision in sorted(submodules.items()):
        if path.startswith('kernel_platform/'):
            ET.SubElement(manifest, 'project', name=path, path=os.path.relpath(path, 'kernel_platform'),
                          revision=revision)
    ET.SubElement(manifest, 'project', name='kernel_platform/msm-kernel', path='msm-kernel', revision=plan['revision'])
    return ET.tostring(manifest, encoding='utf-8')


def prepare(root):
    plan = configuration()
    with locked(root):
        checkout_source(root, plan)
        submodules = nested_checkouts(root, submodule_plan(root))
        prebuilts = nested_checkouts(root, prebuilt_plan(root))
        verify_source(root, plan, submodules, prebuilts)
        shared = shared_headers(root)
        (root / 'resolved-manifest.xml').write_bytes(kleaf_manifest(plan, submodules))
        result = {'status': 'PASS', 'operation': 'kernel-source-preparation',
                  'repository': plan['repository'], 'source_commit': plan['revision'],
                  'source_plan_sha256': sha(SOURCE_PLAN), 'submodules': submodules, 'prebuilts': prebuilts,
                  'prebuilts_sha256': sha(root / 'prebuilts.json'),
                  'resolved_manifest_sha256': sha(root / 'resolved-manifest.xml'),
                  'shared_headers_checked': shared, 'device_commands_executed': 0}
        (root / 'preparation.json').write_bytes(encoded(result))
        return result


def prepared(root, plan):
    """The preparation record, checked against the pin and the checkout."""
    preparation = load_json(root / 'preparation.json')
    require(preparation.get('status') == 'PASS' and preparation.get('source_commit') == plan['revision']
            and preparation.get('source_plan_sha256') == sha(SOURCE_PLAN),
            'the kernel sources were prepared from another pin; run "diamaneos kernel prepare" again')
    require(sha(root / 'resolved-manifest.xml') == preparation.get('resolved_manifest_sha256'),
            'resolved source manifest changed')
    require(isinstance(preparation.get('submodules'), dict) and isinstance(preparation.get('prebuilts'), dict),
            'the preparation record is incomplete; run "diamaneos kernel prepare" again')
    verify_source(root, plan, preparation['submodules'], preparation['prebuilts'])
    return preparation


def linux_version(work):
    """VERSION.PATCHLEVEL.SUBLEVEL of the common kernel."""
    values = dict(re.findall(r'^(VERSION|PATCHLEVEL|SUBLEVEL) = ([0-9]+)$',
                             (work / 'common/Makefile').read_text(errors='replace'), re.M))
    require(set(values) == {'VERSION', 'PATCHLEVEL', 'SUBLEVEL'}, 'common/Makefile names no kernel version')
    return '.'.join(values[k] for k in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL'))


def module_key(name):
    """Module names as the loader compares them: '-' and '_' are the same."""
    return name.removesuffix('.ko').replace('-', '_')


def denied_modules(recipe):
    """The modules FP6 never ships, each with its reason. The partition and load
    lists come from Fairphone's lists and may be regenerated; this deny list
    survives that, and a denied module in any list fails the build. Whether a
    remaining module still needs a denied one is checked on the built set
    (module-interfaces.json)."""
    denied = {}
    for group in recipe.get('denied_modules', []):
        require(isinstance(group, dict) and set(group) == {'modules', 'reason'} and
                isinstance(group['reason'], str) and group['reason'].strip() and
                isinstance(group['modules'], list) and group['modules'], 'invalid denied module group')
        for name in group['modules']:
            require(isinstance(name, str) and MODULE_NAME.fullmatch(name), 'invalid denied module name')
            require(module_key(name) not in {module_key(n) for n in denied}, 'duplicate denied module: ' + name)
            denied[name] = group['reason']
    keys = {module_key(n) for n in denied}
    listed = set().union(*map(set, recipe['partitions'].values()), *map(set, recipe['load_lists'].values()))
    back = sorted(n for n in listed if module_key(n) in keys)
    require(not back, 'denied module in the packaging recipe: ' + ', '.join(back))
    return denied


SYMBOL_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.]{0,127}')


def clang_bin(work):
    """The kernel's own clang, as its pinned common/build.config.constants names it."""
    constants = work / 'common' / 'build.config.constants'
    require(constants.is_file(), 'common/build.config.constants is missing')
    match = re.search(r'^CLANG_VERSION=(r[0-9a-z]+)$', constants.read_text(), re.M)
    require(match is not None, 'common/build.config.constants sets no CLANG_VERSION')
    path = work / 'prebuilts/clang/host/linux-x86' / ('clang-' + match.group(1)) / 'bin'
    require(path.is_dir(), 'the kernel\'s pinned clang is missing: clang-' + match.group(1))
    return path


def import_allowlist(recipe):
    """Symbols only the named modules may import, with the reason."""
    rules = recipe.get('module_import_allowlist', {})
    require(isinstance(rules, dict), 'invalid module import allowlist')
    for symbol, rule in rules.items():
        require(isinstance(symbol, str) and SYMBOL_NAME.fullmatch(symbol) and isinstance(rule, dict)
                and set(rule) == {'modules', 'reason'} and isinstance(rule['reason'], str)
                and rule['reason'].strip() and isinstance(rule['modules'], list)
                and all(isinstance(m, str) and MODULE_NAME.fullmatch(m) for m in rule['modules']),
                'invalid module import allowlist')
    return {symbol: sorted(rule['modules']) for symbol, rule in rules.items()}


def forbidden_symbols(recipe):
    """Kernel symbols that must not exist in the built Image, with the reason."""
    rules = recipe.get('forbidden_symbols', [])
    require(isinstance(rules, list), 'invalid forbidden symbol list')
    for rule in rules:
        require(isinstance(rule, dict) and set(rule) == {'symbol', 'reason'}
                and isinstance(rule['symbol'], str) and SYMBOL_NAME.fullmatch(rule['symbol'])
                and isinstance(rule['reason'], str) and rule['reason'].strip(), 'invalid forbidden symbol')
    return [rule['symbol'] for rule in rules]


def undefined_symbols(nm_output):
    """Names `nm -u` lists as undefined."""
    return {line.split()[-1] for line in nm_output.splitlines() if line.split()[:1] == ['U']}


def check_module_imports(modules, allowlist, undefined):
    """Fail unless each allowlisted symbol is imported by exactly its modules."""
    for symbol, allowed in allowlist.items():
        importers = sorted(name for name, path in modules.items() if symbol in undefined(path))
        require(importers == allowed, 'unexpected importers of ' + symbol + ': ' + (', '.join(importers) or 'none'))


def check_forbidden_symbols(system_map, symbols):
    present = {line.split()[2] for line in system_map.splitlines() if len(line.split()) >= 3}
    found = sorted(set(symbols) & present)
    require(not found, 'forbidden kernel symbol present: ' + ', '.join(found))


def output_files(work, paths):
    execution = Path(call([work / 'tools/bazel', '--batch', 'info', 'execution_root'], cwd=work).strip()).resolve()
    require(execution.is_relative_to(work / 'out'), 'execution root outside workspace output')
    files = []
    for name in sorted(set(paths.splitlines())):
        relative(name)
        p = execution / name
        require(p.resolve().is_relative_to(work / 'out' if name.startswith('bazel-out/') else work.parent),
                'Bazel output escaped workspace')
        require(p.exists(), 'declared output missing: ' + name)
        if p.is_dir():
            for child in sorted(p.rglob('*')):
                require(not child.is_symlink(), 'tree artifact contains a symlink')
                if child.is_file(): files.append(child)
        elif p.is_file():
            files.append(p)
    return execution, sorted(set(files))


def module_metadata(path):
    raw = process.run(['modinfo', '-0', str(path)], 60)
    require(raw['transport'] == 'ok', 'module metadata unavailable')
    values = []
    for row in raw['stdout'].split(b'\0'):
        if not row: continue
        match = re.fullmatch(rb'([a-zA-Z0-9_]+)(?:=|: *)(.*)', row, re.DOTALL)
        require(match, 'unrecognized module metadata')
        if match[1] != b'filename': values.append((match[1], match[2]))
    return sorted(values)


SIGNATURE_FIELDS = (b'sig_id', b'signer', b'sig_key', b'sig_hashalgo', b'signature')


def signature(values):
    return [(k, v) for k, v in values if k in SIGNATURE_FIELDS]


def render_package(candidate, selected, merged, image, recipe, strip, work, signing=None):
    """Stage the kernel package. The GKI build signs its own modules; vendor
    and external modules are stripped and then signed with the same build key,
    because the kernel only loads modules signed with it (MODULE_SIG_FORCE).
    signing is (sign-file, private key, certificate, hash algorithm)."""
    candidate.mkdir()
    (candidate / 'modules').mkdir()
    signed = []
    for name, source in sorted(selected.items()):
        dest = candidate / 'modules' / name
        before = module_metadata(source)
        if any(k == b'signer' and v for k, v in before):
            shutil.copyfile(source, dest); signed.append(name)
            require(sha(dest) == sha(source), 'signed module changed')
            require(module_metadata(dest) == before, 'copying changed module metadata')
        else:
            call([strip, '--strip-debug', '-o', dest, source], cwd=work)
            require(module_metadata(dest) == before, 'stripping changed module metadata')
            if signing:
                sign_file, key, cert, algorithm = signing
                # sign-file is a host tool linked to the kernel build tools' libcrypto.
                tools_env = dict(os.environ, LD_LIBRARY_PATH=str(work / 'prebuilts/kernel-build-tools/linux-x86/lib64'))
                call([sign_file, algorithm, key, cert, dest], cwd=work, env=tools_env)
                after = module_metadata(dest)
                require([r for r in after if r[0] not in SIGNATURE_FIELDS] == before and signature(after),
                        'signing changed module metadata')
        require(call(['modprobe', '--dump-modversions', source], cwd=work) ==
                call(['modprobe', '--dump-modversions', dest], cwd=work), 'stripping changed symbol CRCs')
    require(set(signed) == set(recipe['partitions']['system_dlkm']), 'signed module placement differs')
    if signing:
        keys = {tuple(r for r in signature(module_metadata(p)) if r[0] != b'signature')
                for p in (candidate / 'modules').iterdir()}
        require(len(keys) == 1, 'modules are not all signed with the kernel build key')
    shutil.copyfile(image, candidate / 'Image')
    (candidate / 'dtbs').mkdir()
    for path in sorted(merged.glob('*.dtb')): shutil.copyfile(path, candidate / 'dtbs' / path.name)
    shutil.copyfile(merged / 'dtbo.img', candidate / 'dtbo.img')
    lines = ['# Generated development kernel; runtime acceptance is separate.',
             'FP6_KERNEL_PATH := device/fairphone/FP6-kernel',
             'BOARD_INCLUDE_DTB_IN_BOOTIMG := true',
             'BOARD_PREBUILT_DTBIMAGE_DIR := $(FP6_KERNEL_PATH)/dtbs',
             'BOARD_PREBUILT_DTBOIMAGE := $(FP6_KERNEL_PATH)/dtbo.img',
             'BOARD_DO_NOT_STRIP_VENDOR_MODULES := true',
             'BOARD_DO_NOT_STRIP_VENDOR_RAMDISK_MODULES := true']
    variables = {'vendor_boot': 'VENDOR_RAMDISK', 'vendor_dlkm': 'VENDOR', 'system_dlkm': 'SYSTEM'}
    for partition, names in recipe['partitions'].items():
        lines.append('BOARD_' + variables[partition] + '_KERNEL_MODULES := ' +
                     ' '.join('$(FP6_KERNEL_PATH)/modules/' + n for n in names))
    for key, names in recipe['load_lists'].items():
        partition, filename = key.split('/')
        require(set(names) <= set(recipe['partitions'][partition]), 'load list escapes partition')
        label = 'VENDOR_RAMDISK_RECOVERY' if filename == 'modules.load.recovery' else variables[partition]
        lines.append('BOARD_' + label + '_KERNEL_MODULES_LOAD := ' + ' '.join(names))
    for key, contents in recipe['blocklists'].items():
        partition, filename = key.split('/')
        out = candidate / (partition + '-' + filename); out.write_text(contents)
        lines.append('BOARD_' + variables[partition] + '_KERNEL_MODULES_BLOCKLIST_FILE := $(FP6_KERNEL_PATH)/' + out.name)
    (candidate / 'BoardConfigKernel.mk').write_text('\n'.join(lines) + '\n')
    (candidate / 'device-kernel.mk').write_text('# Generated source-built kernel.\nPRODUCT_COPY_FILES += device/fairphone/FP6-kernel/Image:kernel\n')


def build(root, jobs, timeout, profile='production'):
    missing = [name for name in ('modinfo', 'modprobe', 'nm', 'readelf', 'openssl')
               if shutil.which(name) is None]
    require(not missing, 'kernel verification tools missing from PATH: ' + ', '.join(missing))
    require(profile in CONFIG_PROFILES, 'unknown kernel configuration profile')
    plan = configuration()
    recipe = load_json(ROOT / 'config/fp6-kernel-packaging.json')
    denied = denied_modules(recipe)
    allowlist = import_allowlist(recipe)
    forbidden = forbidden_symbols(recipe)
    with locked(root):
        preparation = prepared(root, plan)
        shared_headers(root)
        work = root / 'kernel_platform'
        from .product_inputs import tools_identity
        run = root / 'runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
        run.mkdir(parents=True)
        result = {'status': 'RUNNING', 'operation': 'kernel-build-and-package', 'commands': [],
                  'device_commands_executed': 0, 'kernel_accepted': False, 'config_profile': profile,
                  'preparation_sha256': sha(root / 'preparation.json'),
                  'repository': preparation['repository'], 'source_commit': preparation['source_commit'],
                  'linux_version': linux_version(work), 'tools': tools_identity(),
                  'recipe_sha256': sha(Path(__file__)), 'packaging_recipe_sha256': sha(ROOT / 'config/fp6-kernel-packaging.json')}
        def save():
            temp = run / 'result.tmp'; temp.write_bytes(encoded(result)); temp.replace(run / 'result.json')
        env = {k: v for k, v in os.environ.items() if k not in (
            'OUT_DIR', 'DIST_DIR', 'BUILD_CONFIG', 'KERNEL_DIR', 'KERNEL_KIT',
            'EXT_MODULES', 'VARIANT', 'MAKEFLAGS', 'LD_PRELOAD', 'LD_LIBRARY_PATH')}
        env.update(KLEAF_REPO_MANIFEST=str(root / 'resolved-manifest.xml'),
                   KLEAF_MAKE_JOBS=str(jobs), LC_ALL='C')
        def command(name, argv, custom_env=None, seconds=None):
            result['phase'] = name; save(); print(name, flush=True)
            log = run / (name + '.log')
            out = call(argv, cwd=work, env=custom_env or env, timeout=seconds or timeout, log=log)
            result['commands'].append({'name': name, 'log_sha256': sha(log), 'status': 'PASS'})
            save(); return out
        def bazel(name, args):
            return command(name, [work / 'tools/bazel', '--batch', *args])
        # Stamped like GrapheneOS and Pixel kernels: the version names the source
        # commit (-g<hash>) and the build date is the commit's, so it stays
        # reproducible. Unstamped Kleaf builds say -maybe-dirty and 1970.
        flags = ['--jobs=' + str(jobs), KMI, STAMP]
        save()
        try:
            bazel('core-build', ['build', *flags, *CORE, *IMPLICIT])
            # Capture only top-level configured outputs; other transitions can be unbuilt.
            paths = call([work / 'tools/bazel', '--batch', 'cquery', KMI, STAMP, '--output=files',
                          'config(set(' + ' '.join(CORE + IMPLICIT) + '), target)'], cwd=work, env=env)
            (run / 'core-paths.txt').write_text(paths)
            execution, core = output_files(work, paths)
            def one(files, name, fragment):
                found = [p for p in files if p.name == name and fragment in str(p)]
                require(len(found) == 1, 'ambiguous/missing artifact: ' + fragment + '/' + name)
                return found[0]
            vendor_core = [p for p in core if '/msm-kernel/fps_gki/' in str(p)]
            # Check both configurations before building the modules: the Image
            # uses the GKI one, the vendor modules are built against the vendor
            # tree's, and both must meet the same policy.
            from . import kernel_config
            policy = load_json(ROOT / 'config/kernel-policy-fp6.json')
            effective = one(core, '.config', '/common/kernel_aarch64_config/')
            for label, config, report in ((profile + ' kernel', effective, 'kernel-config.json'),
                                          (profile + ' vendor kernel', one(vendor_core, '.config', '/fps_gki/'),
                                           'vendor-kernel-config.json')):
                checked = kernel_config.check(config.read_bytes(), policy, profile)
                (run / report).write_bytes(encoded(checked))
                require(checked['status'] == 'PASS', label + ' configuration regressed')
            # QRTR lives in the vendor kernel configuration; the GKI Image
            # deliberately has no QRTR. Check the role guard where it is built.
            vendor_policy = load_json(ROOT / 'config/kernel-vendor-policy-fp6.json')
            checked = kernel_config.check(one(vendor_core, '.config', '/fps_gki/').read_bytes(),
                                          vendor_policy, profile)
            (run / 'vendor-role-kernel-config.json').write_bytes(encoded(checked))
            require(checked['status'] == 'PASS', 'vendor IMS ownership configuration regressed')
            query = 'filter(":fps_gki.*", kind("_kernel_module rule", //vendor/...))'
            found = set(call([work / 'tools/bazel', '--batch', 'query', '--output=label', query], cwd=work, env=env).splitlines())
            require(set(EXCLUDED_MODULE_TARGETS) <= found, 'excluded external target no longer exists')
            targets = sorted(found - set(EXCLUDED_MODULE_TARGETS))
            require(targets and all(re.fullmatch(r'//vendor/[A-Za-z0-9_./-]+:fps_gki[A-Za-z0-9_.-]*', t) for t in targets), 'invalid external target set')
            require(any('/audio-kernel:' in t for t in targets) and any('/wlan/qcacld-3.0:' in t for t in targets), 'missing audio/WLAN target')
            (run / 'module-targets.txt').write_text('\n'.join(targets) + '\n')
            bazel('external-modules', ['build', *flags, *targets])
            warnings = [w for name in ('core-build', 'external-modules')
                        for w in kernel_layout.visibility_warnings((run / (name + '.log')).read_bytes())]
            require(not warnings, 'struct declared inside a parameter list (-Wvisibility): ' + '; '.join(warnings[:5]))
            paths = call([work / 'tools/bazel', '--batch', 'cquery', KMI, STAMP, '--output=files',
                          'config(set(' + ' '.join(targets) + '), target)'], cwd=work, env=env)
            (run / 'module-paths.txt').write_text(paths)
            _, external = output_files(work, paths)
            kit = run / 'kit'; kit.mkdir()
            base = run / 'base-dts'; base.mkdir()
            for name in ('.config', 'Module.symvers'):
                shutil.copyfile(one(vendor_core, name, '/fps_gki/'), kit / name)
            for p in vendor_core:
                if p.suffix in ('.dtb', '.dtbo'): shutil.copyfile(p, base / p.name)
            require((base / 'fp6.dtb').is_file(), 'FP6 base DT missing')
            output = run / 'dt-output'; output.mkdir()
            dt_env = dict(env, KERNEL_DIR='msm-kernel', BUILD_CONFIG='msm-kernel/build.config.msm.fps', VARIANT='gki',
                          OUT_DIR=str(output / 'kernel_platform'), KERNEL_KIT=str(kit), TARGET_BOARD_PLATFORM='FP6',
                          MAKEFLAGS='-j' + str(jobs), EXT_MODULES='')
            command('dt-environment', ['bash', 'build/build_module.sh', '-j' + str(jobs)], dt_env)
            command('dt-compiler', ['bash', '-e', '-c', 'source build/_setup_env.sh; compile_external_dtc'], dt_env)
            require(sha(output / 'kernel_platform/msm-kernel/.config') == sha(kit / '.config'), 'DT config differs from vendor kernel')
            dt_trees = sorted(p for p in root.glob('vendor/*/*/*-devicetree') if p.is_dir() and not p.is_symlink())
            require(dt_trees, 'vendor device-tree projects missing')
            for tree in dt_trees:
                dt_env['EXT_MODULES'] = os.path.relpath(tree, work)
                command('dt-' + tree.name, ['bash', 'build/build_module.sh', '-j' + str(jobs), 'dtbs'], dt_env)
            dtc = output / 'kernel_platform/external/dtc'
            host = run / 'host'; (host / 'bin').mkdir(parents=True); (host / 'lib').mkdir()
            for name in ('dtc', 'fdtget', 'fdtput', 'fdtoverlay', 'fdtoverlaymerge'):
                shutil.copy2(dtc / name, host / 'bin' / name)
            lib = dtc / 'libfdt/libfdt-1.6.0.so'; shutil.copy2(lib, host / 'lib' / lib.name)
            (host / 'lib/libfdt.so.1').symlink_to(lib.name)
            merge_env = dict(env, PATH=str(host / 'bin') + ':' + str(work / 'prebuilts/kernel-build-tools/linux-x86/bin') + ':' +
                             str(work / 'build/kernel/build-tools/path/linux-x86') + ':/usr/bin:/bin',
                             LD_LIBRARY_PATH=str(host / 'lib') + ':' + str(work / 'prebuilts/kernel-build-tools/linux-x86/lib64'))
            merged = run / 'merged'; merged.mkdir()
            command('merge-dt', ['python3', 'build/android/merge_dtbs.py', '--base', str(base), '--techpack', str(output / 'vendor'), '--out', str(merged)], merge_env)
            dtbs = sorted(merged.glob('*.dtb')); dtbos = sorted(merged.glob('*.dtbo'))
            require(len(dtbs) == recipe['dtb_count'] and len(dtbos) == recipe['dtbo_count'], 'merged DT inventory changed')
            command('pack-dtbo', [work / 'prebuilts/kernel-build-tools/linux-x86/bin/mkdtboimg', 'create', merged / 'dtbo.img', '--page_size=4096', *dtbos], merge_env)
            wanted = set().union(*map(set, recipe['partitions'].values()))
            # A denied module that is no longer built was renamed or dropped;
            # review the deny list rather than let a renamed copy back in.
            built = {module_key(p.name) for p in core + vendor_core + external if p.suffix == '.ko'}
            stale = sorted(n for n in denied if module_key(n) not in built)
            require(not stale, 'denied module no longer built (update the deny list): ' + ', '.join(stale))
            selected = {}
            for name in sorted(wanted):
                pool = core if name in recipe['partitions']['system_dlkm'] else vendor_core + external
                choices = [p for p in pool if p.name == name]
                if name in recipe['partitions']['system_dlkm']:
                    choices = [p for p in choices if '/common/' in str(p)]
                require(choices and len({sha(p) for p in choices}) == 1, 'missing/ambiguous selected module: ' + name)
                selected[name] = choices[0]
            # Every built module and both kernels' debug objects, from the output
            # tree of the packaged Image (Bazel also keeps other configurations).
            vmlinux = one(core, 'vmlinux', '/common/kernel_aarch64/')
            bin_dir = next(p for p in vmlinux.parents if p.name == 'bin')
            debug = sorted(bin_dir.rglob('unstripped/*.ko'))
            require({p.name for p in debug} >= set(selected), 'unstripped module missing for the layout scan')
            debug += [vmlinux, bin_dir / 'msm-kernel/fps_gki_kbuild_mixed_tree/vmlinux']
            require(debug[-1].is_file(), 'vendor kernel vmlinux missing for the layout scan')
            result['phase'] = 'layout-scan'; save(); print('layout-scan', flush=True)
            layout = kernel_layout.scan(work / 'prebuilts/kernel-build-tools/linux-x86/bin/pahole', debug, min(jobs, 4))
            layout['objects_scanned'] = [p.relative_to(execution).as_posix() for p in debug]
            (run / 'layout-scan.json').write_bytes(encoded(layout))
            require(not layout['errors'], 'layout scan could not read ' + str(len(layout['errors'])) + ' objects')
            require(not layout['mismatches'], 'RANDSTRUCT layout differs between compilation units: ' +
                    ', '.join(m['name'] for m in layout['mismatches']))
            shutil.copyfile(effective, run / 'gki.config')
            shutil.copyfile(kit / '.config', run / 'vendor.config')
            image = one(core, 'Image', '/common/kernel_aarch64/')
            # The GKI build's own key and signing tool, next to its Image.
            gki = image.parent
            for name in ('certs/signing_key.pem', 'certs/signing_key.x509', 'scripts/sign-file'):
                require((gki / name).is_file() and (gki / name).resolve().is_relative_to(work / 'out'),
                        'kernel signing input missing: ' + name)
            require(re.search(rb'^CONFIG_MODULE_SIG_FORCE=y$', effective.read_bytes(), re.M) and
                    re.search(rb'^CONFIG_MODULE_SIG_HASH="sha256"$', effective.read_bytes(), re.M),
                    'kernel does not enforce sha256 module signatures')
            candidate = run / 'candidate'
            render_package(candidate, selected, merged, image, recipe,
                           clang_bin(work) / 'llvm-strip', work,
                           (gki / 'scripts/sign-file', gki / 'certs/signing_key.pem',
                            gki / 'certs/signing_key.x509', 'sha256'))
            # Check the packaged (stripped and signed) modules against the Image's
            # built-in certificate, symbol CRCs and namespaces.
            from .kernel_interfaces import verify_built
            verify_built(work, run, {name: candidate / 'modules' / name for name in selected},
                         core + external, one(core, 'vmlinux', '/common/kernel_aarch64/'),
                         module_metadata, call, require)
            # Symbol rules moved from the per-build checks: which modules may
            # import a symbol, and symbols that must not exist in the Image.
            nm = clang_bin(work) / 'llvm-nm'
            check_module_imports({name: candidate / 'modules' / name for name in selected}, allowlist,
                                 lambda path: undefined_symbols(call([nm, '-u', path], cwd=work)))
            system_map = vmlinux.parent / 'System.map'
            require(system_map.is_file(), 'System.map missing next to the packaged kernel')
            check_forbidden_symbols(system_map.read_text(errors='replace'), forbidden)
            verify_source(root, plan, preparation['submodules'], preparation['prebuilts'])
            for p in candidate.rglob('*'):
                if p.is_file(): p.chmod(0o640)
            inventory = [{'path': p.relative_to(candidate).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                         for p in sorted(candidate.rglob('*')) if p.is_file()]
            (run / 'artifacts.json').write_bytes(encoded(inventory))
            result.update(status='PASS', module_count=len(selected), denied_module_count=len(denied),
                          dtb_count=len(dtbs), dtbo_count=len(dtbos),
                          import_rules_checked=len(allowlist), forbidden_symbols_checked=len(forbidden),
                          inventory_sha256=sha(run / 'artifacts.json'),
                          interfaces_sha256=sha(run / 'module-interfaces.json'),
                          layout_scan_sha256=sha(run / 'layout-scan.json'),
                          scope='Development build and packaging; installed-image, runtime and production qualification are separate.')
            save()
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=root) as temp:
                link = Path(temp) / 'current'; link.symlink_to(os.path.relpath(candidate, root))
                os.replace(link, root / 'current')
        except (Exception, KeyboardInterrupt) as exc:
            result.update(status='FAIL', error=str(exc)); save(); raise
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'build'])
    parser.add_argument('--workspace', type=Path, required=True,
                        help='kernel source workspace: the kernel repository checkout and its build runs')
    parser.add_argument('--jobs', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=7200, help='maximum seconds per compilation command')
    parser.add_argument('--config-profile', choices=CONFIG_PROFILES, default='production',
                        help='build: kernel configuration policy to check; development checks only the '
                             'baseline. It does not change the kernel configuration, which comes from '
                             'the pinned defconfig')
    args = parser.parse_args(argv)
    try:
        require(1 <= args.jobs <= 64 and 60 <= args.timeout <= 21600, 'invalid build resource limits')
        require(args.operation == 'build' or args.config_profile == 'production', 'config profile is a build option')
        with process.interrupt_on_termination():
            result = (prepare(args.workspace.absolute()) if args.operation == 'prepare' else
                      build(args.workspace.absolute(), args.jobs, args.timeout, args.config_profile))
        print(json.dumps(result, indent=2)); return 0
    except KeyboardInterrupt:
        print('ERROR: kernel ' + args.operation + ' interrupted', file=sys.stderr); return 130
    except (KernelError, VendorError, OSError, ValueError, KeyError) as exc:
        print('ERROR: kernel ' + args.operation + ' failed: ' + str(exc), file=sys.stderr); return 2
