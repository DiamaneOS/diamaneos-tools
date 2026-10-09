"""Build a DiamaneOS test image for the Fairphone 6 in one workspace.

The steps are sync, vendor, android, package and verify; "all" runs them in
order and skips steps whose inputs did not change. Every step checks what it
consumes and records what it produced. The kernel comes from the kernel
prebuilts in the manifest; "build kernel" builds it from source for
maintainers. --dry-run prints the plan.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

from . import build, product_inputs, source_sync
from . import build_workspace as bw
from .build_workspace import Action, BuildStepError, CheckFailed, HostError, UsageError

ROOT = bw.ROOT
DIAMANEOS = ROOT / 'bin' / 'diamaneos'
HOST_BIN = Path('host/linux-x86/bin')
NAME = re.compile(r'[A-Za-z0-9_.+-]{1,128}')
BUILD_NUMBER = re.compile(r'[A-Za-z0-9._-]{1,64}')
# Build number of the host image tools (aapt2 and friends), fixed so their
# bytes do not depend on the build day.
HOST_TOOLS_BUILD_NUMBER = 'diamaneos-host-tools'
# lunch and m run in their own shell so build/envsetup.sh cannot change the
# environment of later checks (it sets T, for example). Values come in
# through environment variables, never through the script text.
HOST_TOOLS_SCRIPT = ('source build/envsetup.sh >/dev/null && lunch "$DIAMANEOS_LUNCH" '
                     '&& m $DIAMANEOS_JOBS $DIAMANEOS_TARGETS')
ANDROID_SCRIPT = ('source build/envsetup.sh >/dev/null && lunch "$DIAMANEOS_LUNCH" '
                  '&& { [ ! -d "$OUT_DIR/target/product/$DIAMANEOS_PRODUCT" ] || m $DIAMANEOS_JOBS installclean; } '
                  '&& m $DIAMANEOS_JOBS $DIAMANEOS_TARGETS')
KERNEL_RECIPES = ('kernel-sources-fp6.json', 'fp6-kernel-packaging.json', 'kernel-policy-fp6.json',
                  'kernel-vendor-policy-fp6.json')
KERNEL_CODE = ('kernel.py', 'kernel_config.py', 'kernel_interfaces.py', 'kernel_layout.py', 'process.py')
VENDOR_RECIPES = ('fp6-stock-image-recipe.json', 'stock-inputs.json', 'fp6-minimal/vendor-files.json',
                  'fp6-minimal/vendor-elf.json', 'fp6-image-tools.json', 'fp6-firmware-inventory.json')
VENDOR_CODE = ('vendor.py', 'vendor_extract.py', 'vendor_files.py', 'vendor_product.py',
               'carrier_data.py', 'firmware_release.py', 'safe_json.py')
# The parts of config/fp6-build.json each step depends on; editing another
# part (flash texts, say) does not rebuild anything.
ANDROID_CONFIG = ('product', 'release_config', 'variants', 'out_dir', 'make_targets', 'build_identity',
                  'target_files')
# Earlier steps each step consumes; a single step refuses stale ones.
DEPENDS = {'sync': (), 'kernel': (), 'vendor': ('sync',), 'android': ('sync', 'vendor'),
           'package': ('sync', 'vendor', 'android'),
           'verify': ('sync', 'vendor', 'android', 'package')}
# What the Android build takes from the sync: the exact source tree, the
# manifest commit and the kernel prebuilts commit.
SOURCE_KEYS = ('project_map_sha256', 'resolved_manifest_sha256', 'manifest_commit', 'kernel_prebuilts_commit')
MAX_UNKNOWN_DOWNLOAD = 16 * 1024 * 1024
# An official build (DIAMANEOS_OFFICIAL_BUILD=true, read by vendor/diamaneos
# product.mk) includes the Updater, which checks DiamaneOS's update server. The
# workspace remembers --official in this state file, so the builder's workspace
# stays official; --no-official removes it.
OFFICIAL_FLAG = 'DIAMANEOS_OFFICIAL_BUILD'
OFFICIAL_MARKER = 'official'
# Long transfers from android.googlesource.com break off over HTTP/2 ("bytes of
# body are still expected"), and repo then retries the project with every
# branch, which for the large prebuilt repositories never finishes. HTTP/1.1
# and a few retries of the pinned fetch get through.
GIT_HTTP = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.version', 'GIT_CONFIG_VALUE_0': 'HTTP/1.1'}
# The manifest's copy of these tools. The vendor selection and the image checks
# must match the device tree the same manifest selects, so the running tools
# must contain the commit the sync checked out there.
TOOLS_PROJECT = 'tools/diamaneos'
# The steps that use the tools' recipes and checks against the synced source.
TOOLS_CHECKED = ('vendor', 'android', 'package', 'verify')
# Set for a command that starts again because its sync moved the running tools.
RESTARTED = 'DIAMANEOS_TOOLS_RESTARTED'


class ToolsUpdated(Exception):
    """The sync moved the tools checkout this command runs from."""

    def __init__(self, message: str, commit: str):
        super().__init__(message)
        self.commit = commit


def code_hashes(names):
    return {name: bw.sha_file(ROOT / 'src/diamaneos_tools' / name) for name in names}


def config_hashes(names):
    return {name: bw.sha_file(ROOT / 'config' / name) for name in names}


def config_subset(config: dict, keys) -> str:
    return bw.digest({key: config.get(key) for key in keys})


@dataclass
class Context:
    workspace: bw.Workspace
    environment_path: Path
    environment: dict
    environment_raw: bytes
    config: dict
    config_raw: bytes
    variant: str
    jobs: int | None = None
    allow_network: bool = False
    factory_zip: Path | None = None
    shallow: bool = False
    echo: object = print
    dry_run: bool = False
    host: dict | None = None
    # False when the variant is the default because the command named none.
    variant_given: bool = True
    # An official build: the Android step sets DIAMANEOS_OFFICIAL_BUILD=true.
    official: bool = False
    # False when the choice comes from the workspace because the command named none.
    official_given: bool = True
    # Set only to check a prerequisite with the build number it was built with.
    build_number: str | None = None
    # A recorded resolved manifest to reproduce (build sync --resolved-manifest).
    pinned: source_sync.PinnedManifest | None = None
    # The stock firmware inventory (default: config/fp6-firmware-inventory.json).
    firmware_inventory: dict | None = None
    # The tools commit this command started with (its code is that commit's).
    tools_commit: str | None = None
    # The command started again after its sync moved the running tools.
    restarted: bool = False
    cache: dict = field(default_factory=dict)

    @property
    def environment_sha256(self) -> str:
        return hashlib.sha256(self.environment_raw).hexdigest()

    @property
    def out(self) -> Path:
        return self.workspace.src / self.config['out_dir']

    @property
    def host_bin(self) -> Path:
        return self.out / HOST_BIN

    @property
    def resolved_manifest(self) -> Path:
        """The resolved manifest (repo manifest -r) the last sync recorded."""
        return self.workspace.state_dir / 'resolved-manifest.xml'

    @property
    def pinned_manifest(self) -> Path:
        """Where a pinned sync keeps the resolved manifest it gives repo sync -m."""
        return self.workspace.state_dir / 'pinned-manifest.xml'

    @property
    def follows_branch(self) -> bool:
        """The manifest is a branch without a pinned manifest commit."""
        manifest = self.environment['manifest']
        return 'revision' not in manifest and not manifest['branch'].startswith('refs/tags/')

    def jobs_for(self, cap=None) -> int:
        memory = (self.host or {}).get('memory_bytes')
        return bw.default_jobs(self.jobs, memory, cap)


@dataclass
class StepPlan:
    name: str
    inputs: dict | None
    actions: list
    outputs: object
    valid: object
    waiting_for: str | None = None


# ----------------------------------------------------------------- helpers

def run_git(argv, cwd=None, timeout=3600, check=True):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *map(str, argv)], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout,
                            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if check and result.returncode:
        raise BuildStepError('git ' + ' '.join(map(str, argv[:3])) + ' failed: ' + result.stderr.strip()[-500:])
    return result


class SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same host over HTTPS."""

    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme != 'https' or target.hostname != self.host:
            raise BuildStepError(f'refusing a redirect to {newurl}: downloads stay on {self.host} over HTTPS')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener(url: str):
    return urllib.request.build_opener(SameHostRedirects(urllib.parse.urlparse(url).hostname)).open


def download(url: str, destination: Path, size: int | None, sha256: str, echo=print, opener=None):
    """HTTPS download that resumes and publishes only the pinned bytes.

    ``size`` may be None for small files pinned only by their hash; such a
    download may not exceed MAX_UNKNOWN_DOWNLOAD. Reads stop at the expected
    size, and redirects must stay on the same host over HTTPS.
    """
    if urllib.parse.urlparse(url).scheme != 'https':
        raise UsageError('downloads must use HTTPS: ' + url)
    opener = opener or default_opener(url)
    if (destination.is_file() and (size is None or destination.stat().st_size == size)
            and bw.sha_file(destination) == sha256):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial')
    offset = partial.stat().st_size if partial.is_file() and size is not None else 0
    if size is not None and offset > size:
        partial.unlink()
        offset = 0
    if size is None or offset < size:
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        with opener(request, timeout=120) as response:
            if offset and getattr(response, 'status', 200) != 206:
                offset = 0
            limit = (size - offset) if size is not None else MAX_UNKNOWN_DOWNLOAD
            with partial.open('ab' if offset else 'wb') as stream:
                reported = received = 0
                while True:
                    chunk = response.read(min(4 * 1024 * 1024, limit - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > limit:
                        stream.close()
                        partial.unlink()
                        raise BuildStepError('the download is larger than expected: ' + url)
                    stream.write(chunk)
                    reported += len(chunk)
                    if reported >= 512 * 1024 * 1024:
                        echo(f'    {stream.tell() // (1024 * 1024)} MiB of {(size or 0) // (1024 * 1024)} MiB')
                        reported = 0
    if (size is not None and partial.stat().st_size != size) or bw.sha_file(partial) != sha256:
        partial.unlink()
        raise BuildStepError('the download does not match its pinned size and SHA-256: ' + url)
    partial.replace(destination)
    return destination


def newest_commit_time(ctx: 'Context', sync: dict) -> int:
    """BUILD_DATETIME: the newest committer time among the synced projects and
    the manifest commit, so the same sources give the same build date on
    every host."""
    key = ('datetime', sync['outputs'].get('resolved_manifest_sha256'))
    if key not in ctx.cache:
        src = ctx.workspace.src
        rows, _ = build.parse_project_map(synced_manifest(ctx, sync))
        times = [int(run_git(['-C', src / '.repo/manifests', 'log', '-1', '--format=%ct',
                              sync['outputs']['manifest_commit']]).stdout.strip())]
        for path, _name, _remote, commit in rows:
            times.append(int(run_git(['-C', src / path, 'log', '-1', '--format=%ct', commit]).stdout.strip()))
        ctx.cache[key] = max(times)
    return ctx.cache[key]


def project_revision(resolved: bytes, path: str) -> str | None:
    """The resolved commit of the project at ``path``, or None."""
    rows, _ = build.parse_project_map(resolved)
    return next((row[3] for row in rows if row[0] == path), None)


def retire_overlay(src: Path) -> list:
    """Move the manifest overlay the tools used to install out of the way.

    The full DiamaneOS manifest replaces it; left in place, repo would apply
    it on top. Other local manifests are the user's and stop the sync.
    """
    directory = src / '.repo/local_manifests'
    if not directory.exists() and not directory.is_symlink():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise UsageError(f'{directory} is not a directory')
    entries = sorted(p.name for p in directory.iterdir())
    if not entries:
        return []
    if entries != ['diamaneos.xml'] or (directory / 'diamaneos.xml').is_symlink():
        raise UsageError(f'{directory} holds local manifests the build does not use ({", ".join(entries)}); '
                         'move them away and run the sync again')
    previous = src / '.repo/diamaneos-previous-local-manifests'
    previous.mkdir(exist_ok=True)
    os.replace(directory / 'diamaneos.xml', previous / 'diamaneos.xml')
    directory.rmdir()
    return ['the old manifest overlay']


def build_number(identity: str, config: dict) -> str:
    override = os.environ.get('DIAMANEOS_BUILD_NUMBER')
    if override:
        if not BUILD_NUMBER.fullmatch(override):
            raise UsageError('DIAMANEOS_BUILD_NUMBER may use letters, digits, ".", "_" and "-"')
        return override
    return config['build_identity']['number_prefix'] + '.' + identity[:12]


# ------------------------------------------------------------------- steps

def pinned_sync(ws: bw.Workspace) -> dict | None:
    """What the passed sync recorded about the resolved manifest it reproduced, if any."""
    return ((ws.passed('sync') or {}).get('outputs') or {}).get('pinned_manifest')


def plan_sync(ctx: Context) -> StepPlan:
    ws, env, pinned = ctx.workspace, ctx.environment, ctx.pinned
    manifest, repo = env['manifest'], env['upstream']['repo_tool']
    # A shallow checkout holds the same tree and passes the same checks, so
    # the choice is remembered in the workspace, not digested. A pinned
    # manifest is recorded in the outputs, so later steps see the same inputs.
    inputs = {'environment_sha256': ctx.environment_sha256}
    jobs = ctx.jobs_for(cap=16)
    settings = source_sync.prefetch_settings(ctx.config) if ctx.shallow else None
    memo = {}

    def create():
        ws.src.mkdir(parents=True, exist_ok=True)
        if ctx.shallow:
            ws.state_dir.mkdir(parents=True, exist_ok=True)
            (ws.state_dir / 'shallow').write_text('Fetch only the resolved commits in this workspace.\n')
        if pinned:
            bw.write_atomic(ctx.pinned_manifest, pinned.data)
        else:
            ctx.pinned_manifest.unlink(missing_ok=True)

    def clear():
        # Before repo sync: a generated tree where the manifest now has a
        # project (the kernel prebuilts) would stop the checkout.
        moved = product_inputs.retire_stale(ws.src, ctx.environment_sha256) + retire_overlay(ws.src)
        if moved:
            ctx.echo('    moved aside: ' + ', '.join(moved))

    def check_manifest():
        build.verify_repo_tool(env, ws.src)
        build.verify_manifest_repository(env, ws.src)

    def repo_manifest() -> bytes:
        """The checked-out manifest as repo reads it ("repo manifest", no network)."""
        if 'manifest' not in memo:
            memo['manifest'] = build.repo_manifest(ws.src, resolved=False)
        return memo['manifest']

    def checkout_recorded():
        source_sync.checkout_manifest_commit(ws.src, manifest['url'], manifest['branch'], pinned.manifest_commit,
                                             ctx.echo)

    def check_recorded():
        ctx.echo(f'    reproducing a pinned resolved manifest: the manifest checkout must be at the recorded '
                 f'commit {pinned.manifest_commit}, in the history of {manifest["branch"]}; the check that it is '
                 f'at the head of {manifest["branch"]} does not apply')
        build.verify_manifest_repository(env, ws.src, pinned.manifest_commit)
        source_sync.check_against_manifest(pinned.data, repo_manifest(), pinned.manifest_commit)

    def clear_empty():
        cleared = source_sync.clear_half_initialised(ws.src, source_sync.manifest_projects(repo_manifest()))
        if cleared:
            ctx.echo(f'    removed the empty git directories of {len(cleared)} projects: ' + ', '.join(cleared[:10])
                     + (' and more' if len(cleared) > 10 else ''))

    def clear_renamed():
        removed = source_sync.clear_renamed(ws.src, source_sync.manifest_projects(repo_manifest()))
        for path, old, new in removed:
            ctx.echo(f'    {path}: now {new} (was {old}); the clean old checkout was removed')

    def prefetch():
        targets = source_sync.prefetch_targets(pinned.data if pinned else repo_manifest(), settings['projects'],
                                               ctx.echo)
        source_sync.prefetch(ws.src, targets, settings, ctx.echo)

    def retire():
        moved = product_inputs.retire_stale(ws.src, ctx.environment_sha256)
        if moved:
            ctx.echo('    moved stale generated inputs aside: ' + ', '.join(moved))

    def verify():
        result = build.verify_branch_checkout(env, ws.src, ctx.environment_sha256,
                                              resolved_path=ctx.resolved_manifest,
                                              manifest_commit=pinned.manifest_commit if pinned else None,
                                              allow_modified=not ctx.official)
        warn_modified(ctx, result['modified'])
        if pinned and result['resolved_manifest_sha256'] != pinned.sha256:
            raise BuildStepError('the synced source is not the pinned resolved manifest: "repo manifest -r" '
                                 f'gives SHA-256 {result["resolved_manifest_sha256"]}, the pinned manifest has '
                                 f'{pinned.sha256}')
        ctx.cache['sync'] = result

    actions = [
        Action('Prepare the workspace', func=create,
               detail='create the source directory' + (', shallow checkout' if ctx.shallow else '')
               + (', keep the pinned resolved manifest' if pinned else '')),
        Action('Clear old inputs', func=clear,
               detail='move generated inputs and the old manifest overlay out of the way'),
        Action('Initialise the checkout', detail=f'the DiamaneOS manifest on {manifest["branch"]}',
               argv=['repo', 'init', '-u', manifest['url'], '-b', manifest['branch'],
                     '--repo-url=' + repo['url'], '--repo-rev=' + repo['peeled_commit']]
                    + (['--depth=1'] if ctx.shallow else []),
               cwd=ws.src, network=True, env=GIT_HTTP),
        Action('Check the repo tool and manifest', func=check_manifest,
               detail='the pinned repo tool and its tag, the manifest checkout at the head of the branch'),
    ]
    if pinned:
        actions += [
            Action('Check out the recorded manifest', func=checkout_recorded, network=True,
                   detail=f'the recorded manifest commit {pinned.manifest_commit}; a shallow manifest checkout '
                          f'first fetches the history of {manifest["branch"]}'),
            Action('Check the recorded manifest', func=check_recorded,
                   detail=f'the manifest checkout is at the recorded commit, in the history of '
                          f'{manifest["branch"]}, and the pinned manifest has that commit\'s projects and remotes; '
                          'the branch-head check does not apply to a pinned manifest'),
        ]
    if settings:
        actions += [
            Action('Clear interrupted fetches', func=clear_empty,
                   detail='remove project git directories an interrupted sync left without data, so repo fetches '
                          'them shallow again'),
            Action('Fetch large prebuilts', func=prefetch, network=True,
                   detail=f'the large prebuilt projects first, one revision each at depth 1, {settings["jobs"]} at a '
                          f'time; a transfer below {settings["low_speed_limit_bytes"]} bytes/s for '
                          f'{settings["low_speed_time_seconds"]} s stops, up to {settings["attempts"]} attempts each: '
                          + ', '.join(settings['projects'])),
        ]
    actions += [
        Action('Clear moved projects', func=clear_renamed,
               detail='remove clean checkouts of projects the manifest now takes from another repository at the '
                      'same path, so repo checks out the new one'),
        Action('Download the source', detail='at the pinned resolved manifest' if pinned else '',
               argv=['repo', 'sync', '--no-manifest-update', '--optimized-fetch', f'-j{jobs}', '--retry-fetches=4']
               + (['-c', '--no-tags'] if ctx.shallow else []) + (['-m', ctx.pinned_manifest] if pinned else []),
               cwd=ws.src, network=True, env=GIT_HTTP),
        Action('Clear stale inputs', func=retire, detail='move stale generated inputs aside'),
        Action('Verify the source tree', func=verify,
               detail='every project at its resolved commit, nothing undeclared; record the resolved manifest'),
    ]

    def outputs():
        result = ctx.cache['sync']
        resolved = ctx.resolved_manifest.read_bytes()
        if build.sha256_bytes(resolved) != result['resolved_manifest_sha256']:
            raise BuildStepError('the recorded resolved manifest changed during the sync')
        values = {'project_map_sha256': result['resolved_project_map_sha256'],
                  'project_count': result['resolved_project_count'],
                  'manifest_url': result['manifest_url'], 'manifest_branch': result['manifest_branch'],
                  'manifest_commit': result['manifest_commit'],
                  'resolved_manifest_sha256': result['resolved_manifest_sha256'],
                  'kernel_prebuilts_commit': project_revision(resolved, product_inputs.KERNEL_PREBUILTS),
                  'shallow': ctx.shallow,
                  'pinned_manifest': pinned.record() if pinned else None,
                  'modified': result['modified'], 'modified_sha256': result['modified_sha256']}
        different = sorted(k for k, v in (pinned.expected_source if pinned else {}).items() if values.get(k) != v)
        if different:
            raise BuildStepError('the synced source differs from the one build.json records: ' + ', '.join(different))
        return values

    def valid(state):
        path = ctx.resolved_manifest
        return ((ws.src / '.repo').is_dir() and path.is_file()
                and bw.sha_file(path) == state['outputs'].get('resolved_manifest_sha256'))

    return StepPlan('sync', inputs, actions, outputs, valid)


def warn_modified(ctx: Context, modified) -> None:
    """Name the projects with local changes a build accepts (not an official one)."""
    if modified:
        shown = ', '.join(modified[:20]) + (f' and {len(modified) - 20} more' if len(modified) > 20 else '')
        ctx.echo(f'    warning: local changes in {shown}; build.json records them, and --official refuses them')


def synced_manifest(ctx: Context, sync: dict) -> bytes:
    """The resolved manifest recorded by the passed sync step."""
    resolved = ctx.resolved_manifest.read_bytes()
    if build.sha256_bytes(resolved) != sync['outputs'].get('resolved_manifest_sha256'):
        raise BuildStepError('the recorded resolved manifest changed; run "diamaneos build sync" again')
    return resolved


def _opener(ctx):
    return ctx.cache.get('opener')


def plan_kernel(ctx: Context) -> StepPlan:
    ws = ctx.workspace
    inputs = {'recipes': config_hashes(KERNEL_RECIPES), 'code': code_hashes(KERNEL_CODE)}
    prepare = [sys.executable, DIAMANEOS, 'kernel', 'prepare', '--workspace', ws.kernel]
    actions = [
        Action('Prepare the kernel sources', detail='at their pinned revisions', argv=prepare, network=True),
        Action('Build the kernel', detail='build and package the kernel, modules and device trees',
               argv=[sys.executable, DIAMANEOS, 'kernel', 'build', '--workspace', ws.kernel,
                     '--jobs', str(ctx.jobs_for(cap=64))], compile=True),
    ]

    def current():
        link = ws.kernel / 'current'
        run = os.readlink(link)
        result = json.loads((ws.kernel / run).parent.joinpath('result.json').read_bytes())
        return run, result

    def outputs():
        run, result = current()
        if result.get('status') != 'PASS':
            raise BuildStepError('the kernel build did not pass')
        return {'run': run, 'result_sha256': bw.sha_file((ws.kernel / run).parent / 'result.json'),
                'network_isolation': 'off' if ctx.allow_network else 'on',
                'inventory_sha256': result['inventory_sha256'], 'module_count': result.get('module_count'),
                'denied_module_count': result.get('denied_module_count'),
                'dtb_count': result.get('dtb_count'), 'dtbo_count': result.get('dtbo_count')}

    def valid(state):
        try:
            run, result = current()
        except (OSError, ValueError):
            return False
        return run == state['outputs']['run'] and result.get('status') == 'PASS'

    return StepPlan('kernel', inputs, actions, outputs, valid)


def selected_factory_archive(recipe: dict, stock: dict, config: dict) -> dict:
    matches = [a for a in stock['archives'] if a.get('sha256') == recipe['archive_sha256']]
    if len(matches) != 1 or not matches[0].get('download_url'):
        raise UsageError('the stock inventory has no download for the pinned factory package')
    archive = matches[0]
    url = urllib.parse.urlparse(archive['download_url'])
    if url.scheme != 'https' or url.hostname != config['factory_host']:
        raise UsageError('the factory package must come from ' + config['factory_host'])
    if archive['size_bytes'] != recipe['archive_bytes']:
        raise UsageError('the stock inventory and image recipe disagree on the package size')
    return archive


def plan_vendor(ctx: Context) -> StepPlan:
    ws, config = ctx.workspace, ctx.config
    sync = ws.passed('sync')
    recipe, _ = bw.load_config('fp6-stock-image-recipe.json')
    stock, _ = bw.load_config('stock-inputs.json')
    archive = selected_factory_archive(recipe, stock, config)
    zip_path = ctx.factory_zip or ws.cache / archive['filename']
    targets = config['host_tools']
    if not all(NAME.fullmatch(t) for t in targets):
        raise UsageError('invalid host tool target')
    inputs = None if sync is None else {
        'sync': sync['outputs']['project_map_sha256'], 'recipes': config_hashes(VENDOR_RECIPES),
        'code': code_hashes(VENDOR_CODE), 'notice_kind': config['notice_kind'], 'host_tools': targets,
        'host_tools_build_number': HOST_TOOLS_BUILD_NUMBER,
        'firmware_release': config_subset(config, ('firmware_release',))}
    # A fixed build number and the sources' own date: without them the build
    # stamps the current date into the tools (aapt2's version string), their
    # hashes reach the vendor inventory, and the same sources would give a
    # different build identity on another day.
    tool_env = {'OUT_DIR': config['out_dir'], 'DIAMANEOS_LUNCH': ctx.environment['build']['generic_qualification_target'],
                'DIAMANEOS_TARGETS': ' '.join(targets), 'DIAMANEOS_JOBS': f'-j{ctx.jobs_for()}',
                'BUILD_NUMBER': HOST_TOOLS_BUILD_NUMBER}
    if sync is not None:
        try:
            tool_env['BUILD_DATETIME'] = str(newest_commit_time(ctx, sync))
        except (BuildStepError, build.BuildError, ValueError, KeyError, OSError,
                subprocess.SubprocessError) as error:
            if not ctx.dry_run:
                raise BuildStepError('cannot read the commit times of the synced sources for the image tools '
                                     f'({error}); run "diamaneos build sync" again') from error
    host_tools = Action(
        'Build the image tools', detail='aapt2, simg2img, lpunpack and debugfs_static from the synced source',
        argv=['bash', '-c', HOST_TOOLS_SCRIPT], cwd=ws.src, compile=True, unset=('OFFICIAL_BUILD', OFFICIAL_FLAG),
        env=tool_env)

    def fetch():
        if ctx.factory_zip:
            if ctx.factory_zip.stat().st_size != recipe['archive_bytes'] or bw.sha_file(ctx.factory_zip) != recipe['archive_sha256']:
                raise BuildStepError('the factory package does not match its pinned size and SHA-256')
            return
        download(archive['download_url'], zip_path, recipe['archive_bytes'], recipe['archive_sha256'],
                 ctx.echo, opener=_opener(ctx))

    def stage():
        from . import vendor
        ctx.cache['stage'] = vendor.stage(recipe, zip_path, ws.stock_images)

    def extract():
        from . import vendor_extract
        selection, _ = bw.load_config('fp6-minimal/vendor-files.json')
        pins, _ = bw.load_config('fp6-image-tools.json')
        ctx.cache['extract'] = vendor_extract.extract(
            (ws.stock_images / 'current' / 'super.img').resolve(), ctx.host_bin.resolve(), ws.stock_files,
            recipe, selection, pins, 'recorded')

    def product():
        from . import firmware_release, safe_json, vendor_product
        firmware = firmware_release.table(safe_json.load_json(ROOT / 'config/fp6-firmware-inventory.json'),
                                          config['firmware_release'])
        ctx.cache['product'] = vendor_product.generate(
            safe_json.load_json(ROOT / 'config/fp6-minimal/vendor-files.json'),
            safe_json.load_json(ROOT / 'config/fp6-minimal/vendor-elf.json'),
            (ws.stock_files / 'current').resolve(), ws.vendor, notice_kind=config['notice_kind'],
            aapt2=ctx.host_bin / 'aapt2', stock=recipe, firmware_releases=firmware,
            release_date=archive.get('release_date'))

    def install():
        # So a plain "m" after "build vendor" finds the vendor files; the
        # android step installs (or re-verifies) the same tree again.
        product_inputs.install(ws.src, ws.vendor, ctx.environment_path, replace=True)

    actions = [host_tools,
               Action('Download the factory package', func=fetch, network=True,
                      detail=f'Fairphone\'s {archive["filename"]}, checked against its pinned size and SHA-256'),
               Action('Stage the factory images', func=stage, detail='each image checked against the recipe'),
               Action('Extract the stock files', func=extract, detail='the stock files the recipe selects'),
               Action('Generate the vendor tree', func=product, detail='the vendor product from the stock files'),
               Action('Install the vendor tree', func=install,
                      detail=f'at {product_inputs.DESTINATIONS["vendor"]} in the source tree')]

    def outputs():
        product = ctx.cache['product']
        return {'factory_sha256': recipe['archive_sha256'], 'factory_zip': str(zip_path),
                'stage': ctx.cache['stage']['recipe_sha256'],
                'extraction': ctx.cache['extract']['generation'],
                'image_tools': ctx.cache['extract']['image_tools'],
                'generation': product['generation_sha256'], 'inventory_sha256': product['inventory_sha256'],
                'vendor_security_patch': product['vendor_security_patch'],
                'aapt2_sha256': bw.sha_file(ctx.host_bin / 'aapt2'),
                'network_isolation': 'off' if ctx.allow_network else 'on'}

    def valid(state):
        link = ws.vendor / 'current'
        return link.is_symlink() and os.readlink(link) == 'generations/' + state['outputs']['generation']

    return StepPlan('vendor', inputs, actions, outputs, valid, waiting_for=None if sync else 'sync')


def source_record(sync: dict) -> dict:
    record = {key: sync['outputs'].get(key) for key in SOURCE_KEYS}
    # Local changes are a source input only when there are any, so a clean
    # tree keeps the source identity it had before they were allowed.
    if sync['outputs'].get('modified_sha256'):
        record['modified_sha256'] = sync['outputs']['modified_sha256']
    return record


def android_identity(ctx: Context, sync: dict) -> str:
    selected = product_inputs.selected_inputs(ctx.workspace.vendor)
    return bw.digest({
        'environment_sha256': ctx.environment_sha256, **source_record(sync),
        'vendor_records_sha256': product_inputs.records_sha256(selected['vendor']['records']),
        'variant': ctx.variant, 'build_config': config_subset(ctx.config, ANDROID_CONFIG),
        **official_input(ctx)})


def official_input(ctx: Context) -> dict:
    """The official choice as a step input: only when set, so the digests of
    other builds stay as they were."""
    return {'official': True} if ctx.official else {}


def find_target_files(ctx: Context) -> Path:
    product_out = ctx.out / 'target' / 'product' / ctx.config['product']
    matches = sorted(p for p in product_out.glob(ctx.config['target_files']) if p.is_file())
    if len(matches) != 1:
        raise BuildStepError('expected exactly one target-files archive in ' + str(product_out))
    return matches[0]


def plan_android(ctx: Context) -> StepPlan:
    ws, config = ctx.workspace, ctx.config
    sync, vendor = ws.passed('sync'), ws.passed('vendor')
    waiting = next((n for n, s in (('sync', sync), ('vendor', vendor)) if s is None), None)
    targets = config['make_targets']
    if not all(NAME.fullmatch(t) for t in targets):
        raise UsageError('invalid make target')
    lunch = f'{config["product"]}-{config["release_config"]}-{ctx.variant}'
    inputs = identity = number = datetime = None
    if waiting is None:
        identity = android_identity(ctx, sync)
        number = ctx.build_number or build_number(identity, config)
        inputs = {'environment_sha256': ctx.environment_sha256, 'sync': source_record(sync),
                  'vendor': vendor['outputs'], 'variant': ctx.variant,
                  'build_config': config_subset(config, ANDROID_CONFIG), 'build_number': number,
                  'network_isolation': not ctx.allow_network, **official_input(ctx)}
        try:
            datetime = newest_commit_time(ctx, sync)
        except (BuildStepError, build.BuildError, ValueError, KeyError, OSError,
                subprocess.SubprocessError) as error:
            if not ctx.dry_run:
                raise BuildStepError('cannot read the commit times of the synced sources for BUILD_DATETIME '
                                     f'({error}); run "diamaneos build sync" again') from error
            datetime = None

    def install():
        ctx.cache['inputs'] = product_inputs.install(ws.src, ws.vendor, ctx.environment_path, replace=True)

    def preflight(key):
        def check():
            if ctx.environment['device_inputs']['generated_input_manifest_status'] != 'verified':
                raise BuildStepError('the build environment does not require bound generated inputs')
            if not sync['outputs'].get('kernel_prebuilts_commit'):
                raise BuildStepError('the manifest has no kernel prebuilts at ' + product_inputs.KERNEL_PREBUILTS)
            # A sync that reproduced a pinned resolved manifest left the
            # manifest checkout at the recorded commit, not the branch head.
            commit = sync['outputs'].get('manifest_commit') if sync['outputs'].get('pinned_manifest') else None
            if commit and key == 'preflight':
                ctx.echo(f'    the sync reproduced a pinned resolved manifest: checking the manifest checkout is at '
                         f'its recorded commit {commit}, in the branch history, not at the branch head')
            result = build.verify_branch_checkout(ctx.environment, ws.src, ctx.environment_sha256,
                                                  manifest_commit=commit, allow_modified=not ctx.official)
            if result['resolved_manifest_sha256'] != sync['outputs']['resolved_manifest_sha256']:
                raise BuildStepError('the source tree is not the one "diamaneos build sync" recorded; '
                                     'run "diamaneos build sync" again')
            if result['modified_sha256'] != sync['outputs'].get('modified_sha256'):
                raise BuildStepError('the local changes are not the ones "diamaneos build sync" recorded; '
                                     'run "diamaneos build sync" again')
            if key == 'preflight':
                warn_modified(ctx, result['modified'])
            ctx.cache[key] = result
        return check

    # GrapheneOS's OFFICIAL_BUILD never reaches the build; DIAMANEOS_OFFICIAL_BUILD
    # only as --official sets it, never from the caller's environment.
    compile_action = Action(
        f'Build Android ({lunch}{", official" if ctx.official else ""})', argv=['bash', '-c', ANDROID_SCRIPT],
        cwd=ws.src, compile=True,
        unset=('OFFICIAL_BUILD',) + (() if ctx.official else (OFFICIAL_FLAG,)),
        env={'OUT_DIR': config['out_dir'], 'DIAMANEOS_LUNCH': lunch, 'DIAMANEOS_PRODUCT': config['product'],
             'DIAMANEOS_TARGETS': ' '.join(targets), 'DIAMANEOS_JOBS': f'-j{ctx.jobs_for()}',
             'BUILD_NUMBER': number or '<from the build identity>',
             'BUILD_DATETIME': datetime if datetime is not None else '<newest source commit time>',
             'BUILD_USERNAME': config['build_identity']['username'],
             'BUILD_HOSTNAME': config['build_identity']['hostname'],
             **({OFFICIAL_FLAG: 'true'} if ctx.official else {})})
    actions = [Action('Install the vendor tree', func=install),
               Action('Verify the source tree', func=preflight('preflight'),
                      detail='the source tree and the generated inputs'),
               compile_action,
               Action('Check the source is unchanged', func=preflight('postflight'),
                      detail='after the build')]

    def outputs():
        before, after = ctx.cache['preflight'], ctx.cache['postflight']
        if any(before[key] != after[key] for key in ('resolved_project_map_sha256', 'modified_sha256')):
            raise BuildStepError('the source tree changed during the build')
        target_files = find_target_files(ctx)
        target_sha256 = bw.sha_file(target_files)
        isolation = 'off' if ctx.allow_network else 'on'
        # The image set's identity: what the build was made from and how. The
        # tools that built it are kept, so packaging records them, not its own.
        tools = product_inputs.tools_identity()
        build_identity = bw.digest({'source_identity': identity, 'build_number': number,
                                    'network_isolation': isolation, 'tools': tools,
                                    'target_files_sha256': target_sha256})
        return {'target_files': str(target_files.relative_to(ws.root)), 'target_files_sha256': target_sha256,
                'source_identity': identity, 'build_identity': build_identity, 'build_number': number,
                'tools': tools,
                'build_datetime': datetime,
                'variant': ctx.variant, 'lunch': lunch, 'official': ctx.official,
                'modified': after['modified'],
                'descriptor_sha256': after['generated_input_descriptor_sha256'],
                'network_isolation': isolation}

    def valid(state):
        path = ws.root / state['outputs']['target_files']
        return path.is_file() and bw.sha_file(path) == state['outputs']['target_files_sha256']

    return StepPlan('android', inputs, actions, outputs, valid, waiting_for=waiting)


def plan_package(ctx: Context) -> StepPlan:
    from . import image_package
    return image_package.plan(ctx)


def plan_verify(ctx: Context) -> StepPlan:
    from . import image_verify
    return image_verify.plan(ctx)


PLANS = {'sync': plan_sync, 'kernel': plan_kernel, 'vendor': plan_vendor, 'android': plan_android,
         'package': plan_package, 'verify': plan_verify}


# ------------------------------------------------------------------ runner

def step_status(ctx: Context, name: str, plan: StepPlan | None = None) -> str:
    """'current', 'stale', 'missing' or 'waiting' for one step."""
    plan = plan or PLANS[name](ctx)
    passed = ctx.workspace.passed(name)
    if plan.inputs is None:
        return 'waiting'
    if passed is None:
        return 'missing'
    return 'current' if passed.get('inputs_sha256') == bw.digest(plan.inputs) and plan.valid(passed) else 'stale'


def recorded_context(ctx: Context, name: str) -> Context:
    """The options a passed step was built with: network isolation always, the
    variant and build number unless this command sets them. So "build verify"
    after "build all --variant userdebug" checks the userdebug build."""
    inputs = (ctx.workspace.passed(name) or {}).get('inputs') or {}
    changes = {}
    if isinstance(inputs.get('network_isolation'), bool):
        changes['allow_network'] = not inputs['network_isolation']
    if not ctx.variant_given and inputs.get('variant') in ctx.config['variants']:
        changes['variant'] = inputs['variant']
    if not os.environ.get('DIAMANEOS_BUILD_NUMBER') and isinstance(inputs.get('build_number'), str):
        changes['build_number'] = inputs['build_number']
    if not ctx.official_given and inputs.get('official') is True:
        changes['official'] = True
    return replace(ctx, **changes) if changes else ctx


def check_prerequisites(ctx: Context, name: str, steps) -> None:
    """A step run on its own needs every earlier step it consumes to be current."""
    for dependency in DEPENDS[name]:
        if dependency in steps:
            continue
        recorded = recorded_context(ctx, dependency)
        status = step_status(recorded, dependency)
        if status == 'current':
            continue
        built = ((ctx.workspace.passed(dependency) or {}).get('inputs') or {}).get('variant')
        if status in ('missing', 'waiting'):
            reason = 'has not run'
        elif built and built != recorded.variant:
            reason = f'was built as {built}, not {recorded.variant}'
        else:
            reason = 'is out of date (its inputs changed)'
        option = '' if recorded.variant == ctx.config['default_variant'] else ' --variant ' + recorded.variant
        raise UsageError(f'{dependency} {reason}; run "diamaneos build {dependency}{option}" '
                         f'or "diamaneos build all{option}"')


def synced_tools(ctx: Context) -> str | None:
    """The commit of the manifest's tools project in the passed sync, or None."""
    sync = ctx.workspace.passed('sync')
    if sync is None:
        return None
    try:
        return project_revision(synced_manifest(ctx, sync), TOOLS_PROJECT)
    except (BuildStepError, build.BuildError, OSError):
        return None


def tools_skew(ctx: Context, synced_now: bool, root: Path = ROOT) -> tuple[str, str] | None:
    """How the running tools relate to the commit the sync checked out at
    tools/diamaneos: None when they are that commit (or the source has no
    tools project); otherwise a kind and a message. 'moved': the sync of this
    command moved the checkout these tools run from. 'newer': they contain the
    commit. 'unknown': their commit is unknown. 'stale': they do not contain it,
    so their recipes and checks may not match the device tree."""
    synced, running = synced_tools(ctx), ctx.tools_commit
    if synced is None or running == synced:
        return None
    checkout = ctx.workspace.src / TOOLS_PROJECT
    if running is None:
        return 'unknown', f'cannot tell the commit of these tools ({root}); the source\'s {TOOLS_PROJECT} is at {synced}'
    if synced_now and checkout.resolve() == root.resolve():
        return 'moved', f'the sync moved these tools from {running[:12]} to {synced[:12]}'
    if run_git(['-C', root, 'merge-base', '--is-ancestor', synced, running], check=False).returncode == 0:
        return 'newer', (f'these tools ({running[:12]}) are newer than the source\'s {TOOLS_PROJECT} '
                         f'({synced[:12]}); build.json records the tools commit')
    return 'stale', (f'these tools ({root}, commit {running[:12]}) do not contain the source\'s {TOOLS_PROJECT} '
                     f'commit {synced[:12]}, so their vendor selection and image checks may not match the device '
                     f'tree. Run {checkout / "bin/diamaneos"} instead, or update this checkout')


def check_tools(ctx: Context, synced_now: bool, dry_run: bool = False) -> None:
    """Stop before a step that uses the tools' recipes when the tools are not
    the source's; start again with the new tools when the sync moved them."""
    skew = tools_skew(ctx, synced_now)
    if skew is None:
        return
    kind, message = skew
    if kind == 'moved':
        if ctx.restarted:
            raise UsageError(message + '; run the same command again')
        raise ToolsUpdated(message, synced_tools(ctx))
    if kind == 'stale' and not dry_run:
        raise UsageError(message)
    ctx.echo('note: ' + message)


def run_steps(ctx: Context, steps, force=(), dry_run=False) -> None:
    ws = ctx.workspace
    runner = bw.Runner(ctx.allow_network, ctx.echo, ws.work / 'tmp')
    earlier_runs = None
    synced_now = tools_checked = False
    for name in steps:
        if not dry_run:
            check_prerequisites(ctx, name, steps)
        if name in TOOLS_CHECKED and not tools_checked:
            tools_checked = True
            check_tools(ctx, synced_now, dry_run)
        plan = PLANS[name](ctx)
        passed = ws.passed(name)
        if plan.inputs is None:
            status, fresh = f'waiting for {plan.waiting_for}', True
        else:
            fresh = (name in force or passed is None or passed.get('inputs_sha256') != bw.digest(plan.inputs)
                     or not plan.valid(passed))
            status = 'to run' if fresh else 'up to date'
            if dry_run and not fresh and earlier_runs:
                status = f'up to date unless {earlier_runs} changes its outputs'
        if dry_run:
            ctx.echo(f'{name}: {status}')
            if fresh or earlier_runs:
                for action in plan.actions:
                    ctx.echo('  - ' + action.text(runner.isolated(action)))
            if fresh and earlier_runs is None:
                earlier_runs = name
            continue
        if plan.inputs is None:
            raise UsageError(f'run "diamaneos build {plan.waiting_for}" first')
        if not fresh:
            ctx.echo(f'{name}: up to date')
            continue
        log = ws.new_log(name)
        ctx.echo(f'{name}: running (log: {log})')
        record = {'inputs': plan.inputs, 'inputs_sha256': bw.digest(plan.inputs), 'log': str(log)}
        ws.write_state(name, dict(record, status='RUNNING'))
        ctx.cache['log'] = log
        try:
            for action in plan.actions:
                runner.run(action, log)
            outputs = plan.outputs()
        except KeyboardInterrupt:
            ws.write_state(name, dict(record, status='FAIL', error='interrupted'))
            raise
        except Exception as error:  # noqa: BLE001 - every failure is recorded and reported plainly
            message = str(error) if isinstance(error, (BuildStepError, build.BuildError)) else \
                f'{type(error).__name__}: {error}'
            ws.write_state(name, dict(record, status='FAIL', error=message))
            if isinstance(error, BuildStepError):
                raise
            raise BuildStepError(f'{name} failed: {message}\nFull log: {log}') from error
        ws.write_state(name, dict(record, status='PASS', outputs=outputs))
        ctx.echo(f'{name}: done')
        synced_now = synced_now or name == 'sync'


def remember_official(ws: bw.Workspace, official: bool) -> None:
    """Keep --official for later commands in this workspace, or forget it."""
    marker = ws.state_dir / OFFICIAL_MARKER
    if official:
        ws.state_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text('Build official images in this workspace (DIAMANEOS_OFFICIAL_BUILD=true); '
                          '"diamaneos build all --no-official" turns it off.\n')
    else:
        marker.unlink(missing_ok=True)


def output_present(ctx: Context, name: str) -> bool:
    """Whether a step's output already takes its disk space (resume)."""
    ws = ctx.workspace
    return {'sync': (ws.src / '.repo').is_dir(), 'kernel': (ws.kernel / 'current').is_symlink(),
            'vendor': (ws.vendor / 'current').is_symlink(),
            'android': (ctx.out / 'target' / 'product' / ctx.config['product']).is_dir(),
            'package': False, 'verify': False}[name]


def steps_to_run(ctx: Context, steps, force) -> list:
    """The steps that will run: forced ones, and from the first step that is
    not current on (a rerun can change later steps' inputs)."""
    for index, name in enumerate(steps):
        if name in force:
            return list(steps[index:])
        try:
            status = step_status(ctx, name)
        except (BuildStepError, ValueError, OSError, KeyError):
            status = 'stale'
        if status != 'current':
            return list(steps[index:])
    return []


def make_context(args, echo=print) -> Context:
    environment_path = Path(args.environment) if args.environment else ROOT / 'config/build-environment-fp6.json'
    environment_raw = environment_path.read_bytes()
    environment = json.loads(environment_raw)
    build.validate_config(environment)
    if 'manifest' not in environment:
        raise UsageError('the build environment names no source manifest')
    # What the environment declares about its inputs must be what the tools
    # use: the project input files and the stock release the vendor step takes.
    try:
        build.declared_identity(environment, environment_raw, ROOT)
    except build.BuildError as error:
        raise UsageError(f'{environment_path.name}: {error}') from None
    recipe, _ = bw.load_config('fp6-stock-image-recipe.json')
    device = environment['device_inputs']
    if (device['selected_stock_build'], device['selected_stock_factory_sha256']) != \
            (recipe['stock_build'], recipe['archive_sha256']):
        raise UsageError(f'{environment_path.name} selects stock {device["selected_stock_build"]}, but the vendor '
                         f'step uses {recipe["stock_build"]} (config/fp6-stock-image-recipe.json)')
    config, config_raw = bw.load_config('fp6-build.json')
    declared = environment['workspace']
    source, output = Path(declared['source_subdirectory']), Path(declared['output_subdirectory'])
    if not output.is_relative_to(source) or output.relative_to(source).parts[:1] != (config['out_dir'],):
        raise UsageError('config/fp6-build.json out_dir does not match the environment\'s output directory')
    variant = args.variant or config['default_variant']
    if variant not in config['variants']:
        raise UsageError('unknown variant: ' + variant)
    if args.jobs is not None and not 1 <= args.jobs <= 1024:
        raise UsageError('--jobs must be between 1 and 1024')
    workspace = bw.Workspace(args.workspace or bw.default_workspace())
    # A workspace synced shallow stays shallow; a plain "build all" must not
    # turn it into a full download.
    shallow = args.shallow or (workspace.state_dir / 'shallow').is_file()
    # Likewise a workspace that built with --official keeps building official images.
    given = getattr(args, 'official', None)
    official = given if given is not None else (workspace.state_dir / OFFICIAL_MARKER).is_file()
    pinned = None
    tools_commit = product_inputs.tools_identity().get('commit')
    if getattr(args, 'resolved_manifest', None):
        record = getattr(args, 'build_json', None)
        pinned = source_sync.load_pinned(
            Path(args.resolved_manifest), Path(record) if record else None, getattr(args, 'manifest_commit', None),
            environment, hashlib.sha256(environment_raw).hexdigest(), tools_commit if record else None)
    return Context(workspace=workspace, tools_commit=tools_commit,
                   environment_path=environment_path, environment=environment, environment_raw=environment_raw,
                   config=config, config_raw=config_raw, variant=variant, variant_given=bool(args.variant),
                   official=official, official_given=given is not None, jobs=args.jobs,
                   allow_network=args.allow_network,
                   factory_zip=Path(args.factory_zip).absolute() if args.factory_zip else None,
                   shallow=shallow, pinned=pinned, echo=echo, dry_run=getattr(args, 'dry_run', False))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog='diamaneos build', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run "diamaneos build all --variant userdebug" for a test build with adb and root debugging. '
               '"diamaneos build kernel" builds the kernel from source (maintainers; not part of build all).')
    result.add_argument('step', choices=('all',) + bw.STEPS + bw.MAINTAINER_STEPS)
    result.add_argument('--workspace', help='build directory (default: $DIAMANEOS_WORKSPACE or ~/diamaneos-build)')
    result.add_argument('--dry-run', action='store_true', help='print the plan and change nothing')
    result.add_argument('--variant', help='user (default) or userdebug')
    result.add_argument('--official', action=argparse.BooleanOptionalAction, default=None,
                        help='build official images (DIAMANEOS_OFFICIAL_BUILD=true): they include the Updater, '
                             'which checks DiamaneOS\'s update server. For DiamaneOS\'s own builder; leave it off '
                             'for other builds. The workspace remembers the choice; --no-official turns it off')
    result.add_argument('--jobs', type=int, help='parallel jobs (default: CPU count, limited by RAM)')
    result.add_argument('--allow-network', action='store_true',
                        help='compile with network access instead of without it. Only for hosts where '
                             'unprivileged user namespaces are unavailable; recorded in build.json')
    result.add_argument('--shallow', action='store_true',
                        help='sync: fetch only the resolved commits, not their history. Saves roughly '
                             'half of the source download and disk, but the checkout has no history '
                             'and moving to a new release fetches more')
    result.add_argument('--resolved-manifest', metavar='FILE',
                        help='sync: reproduce a recorded build: check out every project at the commit this '
                             'resolved manifest (an image set\'s resolved-manifest.xml) records, with the manifest '
                             'checkout at the recorded manifest commit instead of the branch head')
    result.add_argument('--build-json', metavar='FILE',
                        help='with --resolved-manifest: the image set\'s build.json. The resolved manifest must '
                             'match the SHA-256 it records, and it gives the manifest commit')
    result.add_argument('--manifest-commit', metavar='COMMIT',
                        help='with --resolved-manifest and no --build-json: the manifest commit the build recorded')
    result.add_argument('--from', dest='from_step', choices=bw.STEPS, help='all: rerun from this step')
    result.add_argument('--environment', help=argparse.SUPPRESS)
    result.add_argument('--factory-zip', help='vendor: use this Fairphone factory package instead of downloading it')
    return result


STEP_OPTIONS = {'shallow': ('sync', 'all'), 'factory_zip': ('vendor', 'all'), 'official': ('android', 'all'),
                'from_step': ('all',), 'resolved_manifest': ('sync', 'all'), 'build_json': ('sync', 'all'),
                'manifest_commit': ('sync', 'all')}


def main(argv=None, echo=print) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(argv)
    # Set only for the command that starts again after its sync moved the tools.
    restarted = os.environ.pop(RESTARTED, None)
    try:
        for option, steps in STEP_OPTIONS.items():
            value = getattr(args, option)
            # --no-official is given too, as False.
            given = value is not None if option == 'official' else bool(value)
            if given and args.step not in steps:
                raise UsageError(f'--{option.replace("_", "-")} is an option of: ' + ', '.join(steps))
        if (args.build_json or args.manifest_commit) and not args.resolved_manifest:
            raise UsageError('--build-json and --manifest-commit go with --resolved-manifest')
        if args.resolved_manifest and args.from_step not in (None, 'sync'):
            raise UsageError('--resolved-manifest runs the sync step; it does not go with --from ' + args.from_step)
        ctx = make_context(args, echo)
        ctx.restarted = restarted is not None
        for note in ctx.pinned.notes if ctx.pinned else ():
            echo('note: ' + note)
        steps = bw.STEPS if args.step == 'all' else (args.step,)
        force = ()
        if args.from_step:
            steps = bw.STEPS[bw.STEPS.index(args.from_step):]
            force = (args.from_step,)
        elif args.step != 'all':
            force = (args.step,)
        elif ctx.pinned:
            force = ('sync',)
        elif ctx.follows_branch and pinned_sync(ctx.workspace):
            # A reproduction keeps its source until "build sync" moves on.
            commit = pinned_sync(ctx.workspace).get('manifest_commit')
            echo(f'note: the last sync reproduced a pinned resolved manifest (manifest commit {commit}); '
                 '"build all" builds that source. Run "diamaneos build sync" to move to the head of '
                 f'{ctx.environment["manifest"]["branch"]}.')
        elif ctx.follows_branch:
            # Like repo sync before a build: move to the branch head. Later
            # steps rerun only if the synced tree changed.
            force = ('sync',)
        if restarted is not None and restarted == ctx.tools_commit:
            # The sync that moved these tools has just passed; keep it.
            force = tuple(step for step in force if step != 'sync')
        if ctx.official and not ctx.official_given:
            echo('note: this workspace builds official images (DIAMANEOS_OFFICIAL_BUILD=true), which include the '
                 'Updater; --no-official turns that off')
        if args.dry_run:
            echo(f'Workspace: {ctx.workspace.root} (variant {ctx.variant}{", official" if ctx.official else ""})')
            run_steps(ctx, steps, force, dry_run=True)
            return 0
        pending = steps_to_run(ctx, steps, force)
        need_isolation = not ctx.allow_network and any(s in ('kernel', 'vendor', 'android') for s in pending)
        with ctx.workspace.lock():
            if ctx.official_given:
                remember_official(ctx.workspace, ctx.official)
            present = {s for s in pending if output_present(ctx, s)}
            ctx.host = bw.check_host(ctx.workspace, pending, ctx.environment, ctx.config, need_isolation,
                                     present=present)
            for warning in ctx.host['warnings']:
                echo('note: ' + warning)
            from . import process
            with process.interrupt_on_termination():
                run_steps(ctx, steps, force)
        if 'package' in steps or 'verify' in steps:
            latest = ctx.workspace.images / 'latest'
            if latest.is_symlink():
                echo(f'Images: {latest.resolve()}\nNext: diamaneos flash-steps --workspace {ctx.workspace.root}')
        return 0
    except ToolsUpdated as update:
        # Like repo after it updates itself: run the command again with the
        # new tools, so no step mixes their code with the old.
        echo(f'note: {update}; starting again with the new tools')
        os.environ[RESTARTED] = update.commit
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execv(sys.executable, [sys.executable, str(DIAMANEOS), 'build', *argv])
        except OSError as error:
            print(f'ERROR: cannot start again ({error}); run the same command again', file=sys.stderr)
            return 2
        return 0
    except KeyboardInterrupt:
        echo('interrupted; run the same command again to continue')
        return 130
    except BuildStepError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return error.exit_code
    except (build.BuildError, ValueError, OSError, KeyError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - report, never a traceback
        print(f'ERROR: unexpected {type(error).__name__}: {error}', file=sys.stderr)
        return 4
