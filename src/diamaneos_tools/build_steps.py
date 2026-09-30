"""Build a DiamaneOS test image for the Fairphone 6 in one workspace.

The steps are sync, kernel, vendor, android, package and verify; "all" runs
them in order and skips steps whose inputs did not change. Every step checks
what it consumes and records what it produced. --dry-run prints the plan.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
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
import xml.etree.ElementTree as ET

from . import build, build_composition, product_inputs
from . import build_workspace as bw
from .build_workspace import Action, BuildStepError, CheckFailed, HostError, UsageError

ROOT = bw.ROOT
DIAMANEOS = ROOT / 'bin' / 'diamaneos'
HOST_BIN = Path('host/linux-x86/bin')
NAME = re.compile(r'[A-Za-z0-9_.+-]{1,128}')
BUILD_NUMBER = re.compile(r'[A-Za-z0-9._-]{1,64}')
# lunch and m run in their own shell so build/envsetup.sh cannot change the
# environment of later checks (it sets T, for example). Values come in
# through environment variables, never through the script text.
HOST_TOOLS_SCRIPT = ('source build/envsetup.sh >/dev/null && lunch "$DIAMANEOS_LUNCH" '
                     '&& m $DIAMANEOS_JOBS $DIAMANEOS_TARGETS')
ANDROID_SCRIPT = ('source build/envsetup.sh >/dev/null && lunch "$DIAMANEOS_LUNCH" '
                  '&& { [ ! -d "$OUT_DIR/target/product/$DIAMANEOS_PRODUCT" ] || m $DIAMANEOS_JOBS installclean; } '
                  '&& m $DIAMANEOS_JOBS $DIAMANEOS_TARGETS')
KERNEL_RECIPES = ('kernel-sources-fp6.json', 'patches.json', 'kernel-workspace-fp6.json',
                  'fp6-kernel-packaging.json', 'kernel-policy-fp6.json')
KERNEL_CODE = ('kernel.py', 'kernel_config.py', 'kernel_interfaces.py', 'kernel_layout.py', 'process.py')
VENDOR_RECIPES = ('fp6-stock-image-recipe.json', 'stock-inputs.json', 'fp6-minimal/vendor-files.json',
                  'fp6-minimal/vendor-elf.json', 'components.json', 'fp6-sources.json',
                  'fp6-image-tools.json', 'build-environment.json')
VENDOR_CODE = ('vendor.py', 'vendor_extract.py', 'vendor_files.py', 'vendor_product.py',
               'carrier_data.py', 'components.py')


def code_hashes(names):
    return {name: bw.sha_file(ROOT / 'src/diamaneos_tools' / name) for name in names}


def config_hashes(names):
    return {name: bw.sha_file(ROOT / 'config' / name) for name in names}


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
    objects_from: Path | None = None
    factory_zip: Path | None = None
    shallow: bool = False
    echo: object = print
    host: dict | None = None
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

    def signers(self) -> Path:
        return self.workspace.trust / ('grapheneos-allowed-signers-' + self.environment['upstream']['release_tag'])

    def jobs_for(self, cap=None) -> int:
        memory = (self.host or {}).get('memory_bytes')
        return bw.default_jobs(self.jobs, memory, cap)

    def signed_manifest(self) -> bytes:
        if 'signed' not in self.cache:
            self.cache['signed'] = build.verify_release_manifest(self.environment, self.workspace.src, self.signers())
        return self.cache['signed']


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


def has_commit(repository: Path, commit: str) -> bool:
    return run_git(['-C', repository, 'cat-file', '-e', commit + '^{commit}'], check=False).returncode == 0


def download(url: str, destination: Path, size: int | None, sha256: str, echo=print,
             opener=urllib.request.urlopen):
    """HTTPS download that resumes and publishes only the pinned bytes.

    ``size`` may be None for small files pinned only by their hash.
    """
    if urllib.parse.urlparse(url).scheme != 'https':
        raise UsageError('downloads must use HTTPS: ' + url)
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
            with partial.open('ab' if offset else 'wb') as stream:
                reported = 0
                while True:
                    chunk = response.read(4 * 1024 * 1024)
                    if not chunk:
                        break
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


def read_objects(directory: Path | None) -> dict:
    """Optional local commits for development builds: DIR/objects.json maps
    project paths (and "manifest") to git bundles in DIR."""
    if directory is None:
        return {}
    index = directory / 'objects.json'
    try:
        value = json.loads(index.read_bytes())
    except (OSError, ValueError):
        raise UsageError('--objects-from needs an objects.json in ' + str(directory)) from None
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise UsageError('objects.json maps project paths to bundle file names')
    result = {}
    for project, name in value.items():
        if project != 'manifest' and not build._source_relative_path(project):
            raise UsageError('unsafe project path in objects.json: ' + project)
        bundle = directory / name
        if not NAME.fullmatch(name) or not bundle.is_file():
            raise UsageError('missing bundle in objects.json: ' + name)
        result[project] = bundle
    return result


def overlay_remotes(overlay: bytes) -> dict:
    root = ET.fromstring(overlay)
    return {p.get('path'): p.get('remote') for p in root.findall('project')}


def newest_commit_time(src: Path, environment: dict) -> int:
    """BUILD_DATETIME: the newest committer time among the pinned sources, so
    the same pins give the same build date on every host."""
    times = [int(run_git(['-C', src / '.repo/manifests', 'log', '-1', '--format=%ct',
                          environment['upstream']['peeled_commit']]).stdout.strip())]
    for path, commit in environment.get('composition', {}).get('resolved_revisions', {}).items():
        times.append(int(run_git(['-C', src / path, 'log', '-1', '--format=%ct', commit]).stdout.strip()))
    return max(times)


def build_number(identity: str, config: dict) -> str:
    override = os.environ.get('DIAMANEOS_BUILD_NUMBER')
    if override:
        if not BUILD_NUMBER.fullmatch(override):
            raise UsageError('DIAMANEOS_BUILD_NUMBER may use letters, digits, ".", "_" and "-"')
        return override
    return config['build_identity']['number_prefix'] + '.' + identity[:12]


# ------------------------------------------------------------------- steps

def plan_sync(ctx: Context) -> StepPlan:
    ws, env = ctx.workspace, ctx.environment
    upstream, repo = env['upstream'], env['upstream']['repo_tool']
    composition = env.get('composition')
    objects = read_objects(ctx.objects_from)
    inputs = {'environment_sha256': ctx.environment_sha256, 'shallow': ctx.shallow,
              'objects': {k: bw.sha_file(v) for k, v in sorted(objects.items())}}
    overlay_dir = ws.cache / 'manifest'
    jobs = ctx.jobs_for(cap=16)

    def fetch_signers():
        download(upstream['allowed_signers_url'], ctx.signers(), None,
                 upstream['allowed_signers_sha256'], ctx.echo, opener=_opener(ctx))

    def get_overlay():
        revision = composition['overlay_revision']
        if 'manifest' in objects:
            if not (overlay_dir / '.git').is_dir():
                overlay_dir.mkdir(parents=True, exist_ok=True)
                run_git(['init', '-q', overlay_dir])
            run_git(['-C', overlay_dir, 'fetch', '-q', objects['manifest'], '+refs/heads/*:refs/remotes/bundle/*'])
        else:
            url = composition.get('overlay_url')
            if not url:
                raise UsageError('the build environment names no overlay URL')
            if not (overlay_dir / '.git').is_dir():
                run_git(['clone', '-q', '--no-checkout', url, overlay_dir])
            if not has_commit(overlay_dir, revision):
                run_git(['-C', overlay_dir, 'fetch', '-q', 'origin', revision])
        run_git(['-C', overlay_dir, 'checkout', '-q', '--detach', revision])

    def install_overlay():
        build_composition.prepare(env, ws.src, overlay_dir, ctx.signed_manifest(), replace=True)

    def add_bundles():
        for project, bundle in sorted(objects.items()):
            if project == 'manifest':
                continue
            if not (ws.src / project / '.git').exists():
                raise UsageError('objects.json names a project the manifest does not have: ' + project)
            run_git(['-C', ws.src / project, 'fetch', '-q', '--no-tags', bundle,
                     '+refs/heads/*:refs/diamaneos-bundles/*'])

    def fetch_pinned():
        remotes = overlay_remotes((ws.src / '.repo/local_manifests/diamaneos.xml').read_bytes())
        for path, commit in sorted(composition.get('resolved_revisions', {}).items()):
            if not has_commit(ws.src / path, commit):
                ctx.echo(f'    fetching {path} {commit[:12]}')
                depth = ['--depth=1'] if ctx.shallow else []
                run_git(['-C', ws.src / path, 'fetch', '-q', '--no-tags', *depth, remotes[path], commit])

    def checkout():
        build_composition.checkout(env, ws.src, ctx.signed_manifest())

    def retire():
        moved = product_inputs.retire_stale(ws.src, ctx.environment_sha256)
        if moved:
            ctx.echo('    moved stale generated inputs aside: ' + ', '.join(moved))

    def preflight():
        ctx.cache['sync'] = build.verify_manifest_checkout(env, ws.src, ctx.signers(), ctx.environment_sha256)

    actions = [
        Action('Create the source directory', func=lambda: ws.src.mkdir(parents=True, exist_ok=True)),
        Action('Download the GrapheneOS signer list and check its hash', func=fetch_signers, network=True),
        Action('Initialise the checkout at the signed release tag',
               argv=['repo', 'init', '-u', upstream['manifest_url'], '-b', 'refs/tags/' + upstream['release_tag'],
                     '--repo-url=' + repo['url'], '--repo-rev=' + repo['peeled_commit']]
                    + (['--depth=1'] if ctx.shallow else []),
               cwd=ws.src, network=True),
        Action('Check the repo tool and the release manifest signatures', func=ctx.signed_manifest),
    ]
    if composition:
        actions += [Action('Get the DiamaneOS manifest overlay at its pinned revision', func=get_overlay, network=True),
                    Action('Install the overlay', func=install_overlay)]
    actions += [
        Action('Download the source', argv=['repo', 'sync', '--no-manifest-update', '--optimized-fetch', f'-j{jobs}']
               + (['-c', '--no-tags'] if ctx.shallow else []), cwd=ws.src, network=True),
        Action('Move stale generated inputs aside', func=retire),
    ]
    if objects:
        actions.append(Action('Add local commits from the bundles', func=add_bundles))
    if composition:
        actions += [Action('Fetch pinned commits not on a branch head', func=fetch_pinned, network=True),
                    Action('Check out the pinned DiamaneOS commits', func=checkout)]
    actions.append(Action('Verify the whole source tree', func=preflight))

    def outputs():
        result = ctx.cache['sync']
        return {'project_map_sha256': result['resolved_project_map_sha256'],
                'project_count': result['resolved_project_count'],
                'manifest_commit': upstream['peeled_commit'],
                'overlay_revision': (composition or {}).get('overlay_revision'),
                'allowed_signers_sha256': upstream['allowed_signers_sha256'], 'shallow': ctx.shallow}

    return StepPlan('sync', inputs, actions, outputs, lambda state: (ws.src / '.repo').is_dir())


def _opener(ctx):
    return ctx.cache.get('opener', urllib.request.urlopen)


def plan_kernel(ctx: Context) -> StepPlan:
    ws = ctx.workspace
    inputs = {'recipes': config_hashes(KERNEL_RECIPES), 'code': code_hashes(KERNEL_CODE)}
    prepare = [sys.executable, DIAMANEOS, 'kernel', 'prepare', '--workspace', ws.kernel]
    reference = os.environ.get('DIAMANEOS_KERNEL_REFERENCE')
    if reference:
        prepare += ['--reference', reference]
    actions = [
        Action('Prepare the kernel sources at their pinned revisions', argv=prepare, network=True),
        Action('Build and package the kernel, modules and device trees',
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
        'code': code_hashes(VENDOR_CODE), 'notice_kind': config['notice_kind'], 'host_tools': targets}
    host_tools = Action(
        'Build the image tools from the synced source',
        argv=['bash', '-c', HOST_TOOLS_SCRIPT], cwd=ws.src, compile=True, unset=('OFFICIAL_BUILD',),
        env={'OUT_DIR': config['out_dir'], 'DIAMANEOS_LUNCH': ctx.environment['build']['generic_qualification_target'],
             'DIAMANEOS_TARGETS': ' '.join(targets), 'DIAMANEOS_JOBS': f'-j{ctx.jobs_for()}'})

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
        from . import components, vendor_product
        model = (ROOT / 'config/components.json').read_bytes()
        sources = (ROOT / 'config/fp6-sources.json').read_bytes()
        ctx.cache['product'] = vendor_product.generate(
            components.load_json(ROOT / 'config/fp6-minimal/vendor-files.json'),
            components.load_json(ROOT / 'config/fp6-minimal/vendor-elf.json'),
            (ws.stock_files / 'current').resolve(), ws.vendor, notice_kind=config['notice_kind'],
            aapt2=ctx.host_bin / 'aapt2', model=components.loads(model), sources=components.loads(sources),
            environment=components.load_json(ROOT / 'config/build-environment.json'),
            model_sha256=hashlib.sha256(model).hexdigest(), source_sha256=hashlib.sha256(sources).hexdigest())

    actions = [host_tools,
               Action(f'Download the Fairphone factory package {archive["filename"]} and check its SHA-256',
                      func=fetch, network=True),
               Action('Check and stage the factory images', func=stage),
               Action('Extract the selected stock files', func=extract),
               Action('Generate the vendor product', func=product)]

    def outputs():
        product = ctx.cache['product']
        return {'factory_sha256': recipe['archive_sha256'], 'stage': ctx.cache['stage']['recipe_sha256'],
                'extraction': ctx.cache['extract']['generation'],
                'image_tools': ctx.cache['extract']['image_tools'],
                'generation': product['generation_sha256'], 'inventory_sha256': product['inventory_sha256'],
                'aapt2_sha256': bw.sha_file(ctx.host_bin / 'aapt2')}

    def valid(state):
        link = ws.vendor / 'current'
        return link.is_symlink() and os.readlink(link) == 'generations/' + state['outputs']['generation']

    return StepPlan('vendor', inputs, actions, outputs, valid, waiting_for=None if sync else 'sync')


def android_identity(ctx: Context, sync: dict) -> str:
    selected = product_inputs.selected_inputs(ctx.workspace.vendor, ctx.workspace.kernel)
    return bw.digest({
        'environment_sha256': ctx.environment_sha256, 'project_map_sha256': sync['outputs']['project_map_sha256'],
        'vendor_records_sha256': product_inputs.records_sha256(selected['vendor']['records']),
        'kernel_records_sha256': product_inputs.records_sha256(selected['kernel']['records']),
        'variant': ctx.variant, 'build_config_sha256': hashlib.sha256(ctx.config_raw).hexdigest()})


def find_target_files(ctx: Context) -> Path:
    product_out = ctx.out / 'target' / 'product' / ctx.config['product']
    matches = sorted(p for p in product_out.glob(ctx.config['target_files']) if p.is_file())
    if len(matches) != 1:
        raise BuildStepError('expected exactly one target-files archive in ' + str(product_out))
    return matches[0]


def plan_android(ctx: Context) -> StepPlan:
    ws, config = ctx.workspace, ctx.config
    sync, kernel, vendor = ws.passed('sync'), ws.passed('kernel'), ws.passed('vendor')
    waiting = next((n for n, s in (('sync', sync), ('kernel', kernel), ('vendor', vendor)) if s is None), None)
    targets = config['make_targets']
    if not all(NAME.fullmatch(t) for t in targets):
        raise UsageError('invalid make target')
    lunch = f'{config["product"]}-{config["release_config"]}-{ctx.variant}'
    inputs = identity = number = datetime = None
    if waiting is None:
        identity = android_identity(ctx, sync)
        number = build_number(identity, config)
        inputs = {'environment_sha256': ctx.environment_sha256, 'sync': sync['outputs']['project_map_sha256'],
                  'kernel': kernel['outputs'], 'vendor': vendor['outputs'], 'variant': ctx.variant,
                  'build_config_sha256': hashlib.sha256(ctx.config_raw).hexdigest(), 'build_number': number,
                  'network_isolation': not ctx.allow_network}
        try:
            datetime = newest_commit_time(ws.src, ctx.environment)
        except (BuildStepError, ValueError, OSError):
            datetime = None

    def install():
        ctx.cache['inputs'] = product_inputs.install(ws.src, ws.vendor, ws.kernel, ctx.environment_path, replace=True)

    def preflight(key):
        def check():
            if ctx.environment['device_inputs']['generated_input_manifest_status'] != 'verified':
                raise BuildStepError('the build environment does not require bound generated inputs')
            ctx.cache[key] = build.verify_manifest_checkout(ctx.environment, ws.src, ctx.signers(),
                                                            ctx.environment_sha256)
        return check

    compile_action = Action(
        f'Build Android ({lunch})', argv=['bash', '-c', ANDROID_SCRIPT], cwd=ws.src, compile=True,
        unset=('OFFICIAL_BUILD',),
        env={'OUT_DIR': config['out_dir'], 'DIAMANEOS_LUNCH': lunch, 'DIAMANEOS_PRODUCT': config['product'],
             'DIAMANEOS_TARGETS': ' '.join(targets), 'DIAMANEOS_JOBS': f'-j{ctx.jobs_for()}',
             'BUILD_NUMBER': number or '<from the build identity>',
             'BUILD_DATETIME': datetime if datetime is not None else '<newest pinned commit time>',
             'BUILD_USERNAME': config['build_identity']['username'],
             'BUILD_HOSTNAME': config['build_identity']['hostname']})
    actions = [Action('Install the generated vendor and kernel inputs', func=install),
               Action('Verify the source tree and the generated inputs', func=preflight('preflight')),
               compile_action,
               Action('Check the source tree is unchanged after the build', func=preflight('postflight'))]

    def outputs():
        if ctx.cache['preflight']['resolved_project_map_sha256'] != ctx.cache['postflight']['resolved_project_map_sha256']:
            raise BuildStepError('the source tree changed during the build')
        target_files = find_target_files(ctx)
        return {'target_files': str(target_files.relative_to(ws.root)), 'target_files_sha256': bw.sha_file(target_files),
                'build_identity': identity, 'build_number': number, 'build_datetime': datetime,
                'variant': ctx.variant, 'lunch': lunch,
                'descriptor_sha256': ctx.cache['postflight']['generated_input_descriptor_sha256'],
                'network_isolation': 'off' if ctx.allow_network else 'on'}

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

def run_steps(ctx: Context, steps, force=(), dry_run=False) -> None:
    ws, runner = ctx.workspace, bw.Runner(ctx.allow_network, ctx.echo)
    rerun_from = None
    for name in steps:
        plan = PLANS[name](ctx)
        state = ws.state(name)
        passed = state if state and state.get('status') == 'PASS' else None
        if plan.inputs is None:
            status = f'waiting for {plan.waiting_for}'
            fresh = True
        else:
            key = bw.digest(plan.inputs)
            fresh = (name in force or rerun_from is not None or passed is None
                     or passed.get('inputs_sha256') != key or not plan.valid(passed))
            status = 'to run' if fresh else 'up to date'
            if dry_run and rerun_from is not None and name not in force:
                status = f'to run after {rerun_from}'
        if dry_run:
            ctx.echo(f'{name}: {status}')
            if fresh:
                for action in plan.actions:
                    ctx.echo('  - ' + action.text(runner.isolated(action)))
                if rerun_from is None:
                    rerun_from = name
            continue
        if plan.inputs is None:
            raise UsageError(f'run "diamaneos build {plan.waiting_for}" first')
        if not fresh:
            ctx.echo(f'{name}: up to date')
            continue
        ctx.echo(f'{name}: running')
        log = ws.new_log(name)
        record = {'inputs': plan.inputs, 'inputs_sha256': bw.digest(plan.inputs), 'log': str(log)}
        ws.write_state(name, dict(record, status='RUNNING'))
        ctx.cache['log'] = log
        try:
            for action in plan.actions:
                runner.run(action, log)
            outputs = plan.outputs()
        except (BuildStepError, build.BuildError, ValueError, OSError, KeyError) as error:
            ws.write_state(name, dict(record, status='FAIL', error=str(error)))
            if isinstance(error, BuildStepError):
                raise
            raise BuildStepError(f'{name} failed: {error}\nFull log: {log}') from error
        except KeyboardInterrupt:
            ws.write_state(name, dict(record, status='FAIL', error='interrupted'))
            raise
        ws.write_state(name, dict(record, status='PASS', outputs=outputs))
        rerun_from = rerun_from or name
        ctx.echo(f'{name}: done')


def make_context(args, echo=print) -> Context:
    environment_path = Path(args.environment) if args.environment else ROOT / 'config/build-environment-fp6.json'
    environment_raw = environment_path.read_bytes()
    environment = json.loads(environment_raw)
    build.validate_config(environment)
    config, config_raw = bw.load_config('fp6-build.json')
    variant = args.variant or config['default_variant']
    if variant not in config['variants']:
        raise UsageError('unknown variant: ' + variant)
    if args.jobs is not None and not 1 <= args.jobs <= 1024:
        raise UsageError('--jobs must be between 1 and 1024')
    return Context(workspace=bw.Workspace(args.workspace or bw.default_workspace()),
                   environment_path=environment_path, environment=environment, environment_raw=environment_raw,
                   config=config, config_raw=config_raw, variant=variant, jobs=args.jobs,
                   allow_network=args.allow_network,
                   objects_from=Path(args.objects_from).absolute() if args.objects_from else None,
                   factory_zip=Path(args.factory_zip).absolute() if args.factory_zip else None,
                   shallow=args.shallow, echo=echo)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog='diamaneos build', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run "diamaneos build all --variant userdebug" for a test build with adb and root debugging.')
    result.add_argument('step', choices=('all',) + bw.STEPS)
    result.add_argument('--workspace', help='build directory (default: $DIAMANEOS_WORKSPACE or ~/diamaneos-build)')
    result.add_argument('--dry-run', action='store_true', help='print the plan and change nothing')
    result.add_argument('--variant', help='user (default) or userdebug')
    result.add_argument('--jobs', type=int, help='parallel jobs (default: CPU count, limited by RAM)')
    result.add_argument('--allow-network', action='store_true',
                        help='compile with network access if this host cannot turn it off (recorded)')
    result.add_argument('--shallow', action='store_true',
                        help='sync: fetch only the pinned commits, not their history. Saves roughly '
                             'half of the source download and disk, but the checkout has no history '
                             'and moving to a new release fetches more')
    result.add_argument('--from', dest='from_step', choices=bw.STEPS, help='all: rerun from this step')
    result.add_argument('--environment', help=argparse.SUPPRESS)
    result.add_argument('--objects-from', help=argparse.SUPPRESS)
    result.add_argument('--factory-zip', help='vendor: use this Fairphone factory package instead of downloading it')
    return result


STEP_OPTIONS = {'shallow': ('sync', 'all'), 'objects_from': ('sync', 'all'), 'factory_zip': ('vendor', 'all'),
                'from_step': ('all',)}


def main(argv=None, echo=print) -> int:
    args = parser().parse_args(argv)
    try:
        for option, steps in STEP_OPTIONS.items():
            if getattr(args, option) and args.step not in steps:
                raise UsageError(f'--{option.replace("_", "-")} is an option of: ' + ', '.join(steps))
        ctx = make_context(args, echo)
        steps = bw.STEPS if args.step == 'all' else (args.step,)
        force = ()
        if args.from_step:
            steps = bw.STEPS[bw.STEPS.index(args.from_step):]
            force = (args.from_step,)
        elif args.step != 'all':
            force = (args.step,)
        if args.dry_run:
            echo(f'Workspace: {ctx.workspace.root} (variant {ctx.variant})')
            run_steps(ctx, steps, force, dry_run=True)
            return 0
        need_isolation = not ctx.allow_network and any(s in ('kernel', 'vendor', 'android') for s in steps)
        pending = [s for s in steps if s in force or not ctx.workspace.passed(s)]
        with ctx.workspace.lock():
            ctx.host = bw.check_host(ctx.workspace, pending, ctx.environment, ctx.config, need_isolation)
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
    except KeyboardInterrupt:
        echo('interrupted; run the same command again to continue')
        return 130
    except BuildStepError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return error.exit_code
    except (build.BuildError, ValueError, OSError, KeyError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2
