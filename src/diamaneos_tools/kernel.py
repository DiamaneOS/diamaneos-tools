"""Prepare and build the pinned FP6 development kernel, modules and device trees."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
from urllib.parse import urlparse
from xml.sax.saxutils import quoteattr
import xml.etree.ElementTree as ET

from . import kernel_layout, process
from .vendor_extract import sha, relative
from .vendor import ROOT, load_json, VendorError, encoded

KMI = '--user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom'
# Downstream patch diffs can carry a recorded ABI definition; keep the capture bounded.
MAX_PATCH_DIFF_BYTES = 64 * 1024 * 1024
# The GrapheneOS common kernel changes the GKI configuration and does not keep
# a comparable recorded GKI ABI, so no ABI comparison is run: every module is
# built from source with the kernel and signed with its key (MODULE_SIG_FORCE).
CORE = ['//common:kernel_aarch64', '//msm-kernel:fps_gki', '//msm-kernel:fps_gki_abi']
IMPLICIT = ['//common:kernel_aarch64_modules', '//common:kernel_aarch64_config']
# External module targets left out of the build, with the reason.
EXCLUDED_MODULE_TARGETS = {
    '//vendor/qcom/opensource/mm-sys-kernel/ubwcp:fps_gki_ubwcp':
        'UBWC-P needs ZONE_DEVICE, which the hardened kernel disables; gralloc only uses it '
        'when vendor.gralloc.hw_supports_ubwcp is set, which FP6 does not do.',
}


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
    require(result['transport'] == 'ok', 'command failed: ' + str(argv[0]) +
            ' (' + result['transport'] + '); inspect the command log')
    return result['stdout'].decode()


def git(path, *args):
    return call(['git', '-c', 'core.hooksPath=/dev/null', '-C', path, *args], cwd=path).strip()


HUNK_CONTEXT = re.compile(rb'^(@@ -[0-9,]+ \+[0-9,]+ @@).*$', re.M)


def canonical_diff(diff):
    """A patch diff without the function name Git appends to hunk headers. Which
    name it prints depends on the Git version's diff drivers (the kernel's
    .gitattributes selects cpp and dts), not on the change itself."""
    return HUNK_CONTEXT.sub(rb'\1', diff)


def configuration():
    plan = load_json(ROOT / 'config/kernel-sources-fp6.json')
    changes = [p for p in load_json(ROOT / 'config/patches.json')['patches'] if p['workspace'] == 'kernel']
    adaptation = load_json(ROOT / 'config/kernel-workspace-fp6.json')
    paths = [p['path'] for p in plan['projects']]
    require(len(paths) == len(set(paths)), 'duplicate source path')
    for row in plan['projects']:
        relative(row['path']); relative(row['project'])
        require(re.fullmatch('[a-f0-9]{40}', row['revision']), 'invalid source revision')
    for change in changes:
        matching = [p for p in plan['projects'] if p['path'] == change['path']]
        require(len(matching) == 1 and matching[0]['revision'] == change['base_revision'], 'patch base mismatch')
    return plan, changes, adaptation


def sources(root, plan, changes, *, prepare=False, reference=None):
    expected = {p['path']: dict(p) for p in plan['projects']}
    patches = {p['path']: p for p in changes}
    for row in sorted(plan['projects'], key=lambda r: (r['path'].count('/'), r['path'])):
        dest = root / relative(row['path'])
        require(dest.resolve().is_relative_to(root.resolve()), 'source path escaped workspace')
        require(not dest.is_symlink(), 'source directory is a symlink')
        patch = patches.get(row['path'])
        revision = patch['derived_revision'] if patch else row['revision']
        url = patch['repository'] if patch else row.get('url') or plan['source_url'] + row['project']
        created = prepare and not (dest / '.git').exists()
        if created:
            require(not dest.exists() or not any(dest.iterdir()), 'unowned source directory is occupied')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.mkdir(exist_ok=True)
            git(dest, 'init', '-q')
            if reference and (reference / row['path'] / '.git').exists():
                # Local object reuse is optional; all revisions and patch preimages are still verified.
                # Link the object store directly: a prepared workspace has only detached checkouts, and
                # cloning a repository without refs yields an empty clone without shared objects.
                objects = Path(git(reference / row['path'], 'rev-parse', '--path-format=absolute', '--git-path', 'objects'))
                require(objects.is_dir(), 'reference object store missing: ' + row['path'])
                alternates = dest / '.git/objects/info/alternates'
                alternates.parent.mkdir(parents=True, exist_ok=True)
                alternates.write_text(str(objects) + '\n')
        require((dest / '.git').exists(), 'source project missing: ' + row['path'])
        require(created or (not git(dest, 'diff', '--name-only') and not git(dest, 'diff', '--cached', '--name-only')),
                'tracked source changes: ' + row['path'])
        if prepare:
            # Fetch missing objects only; never force/reset a caller's edits.
            for pin in ([row['revision'], revision] if patch else [revision]):
                found = process.run(['git', '-C', str(dest), 'cat-file', '-e', pin + '^{commit}'], 60, cwd=root)
                if found['transport'] != 'ok':
                    call(['git', '-C', dest, 'fetch', '--depth=1', '--no-tags', url, pin], cwd=root, timeout=5400)
            git(dest, 'checkout', '--detach', revision)
        require(git(dest, 'rev-parse', 'HEAD') == revision, 'source revision mismatch: ' + row['path'])
        if patch:
            diff = process.run(['git', '-C', str(dest), 'diff', '--full-index', '--no-ext-diff', '--no-textconv',
                                '--no-color', row['revision'], revision], 120, MAX_PATCH_DIFF_BYTES, cwd=root)
            require(diff['transport'] == 'ok' and
                    hashlib.sha256(canonical_diff(diff['stdout'])).hexdigest() == patch['canonical_diff_sha256'],
                    'downstream patch bytes differ')
            names = git(dest, 'diff', '--name-only', row['revision'], revision).splitlines()
            if 'changed_files' in patch:
                require(names == patch['changed_files'], 'downstream patch file set differs')
            else:
                # Upstream merges touch thousands of files; bind the list by digest.
                listed = hashlib.sha256(''.join(n + '\n' for n in names).encode()).hexdigest()
                require(listed == patch['changed_files_sha256'], 'downstream patch file set differs')
        expected[row['path']]['revision'] = revision
    return list(expected.values())


def links(root, rows, adaptation):
    for row in rows:
        for link in row['linkfiles']:
            skipped = [e for e in adaptation['excluded_linkfiles'] if
                       e['project'] == row['project'] and e['revision'] == row['revision'] and
                       e['src'] == link['src'] and e['dest'] == link['dest']]
            src = root / row['path'] / (Path('.') if link['src'] == '.' else relative(link['src']))
            if skipped:
                require(not src.exists(), 'excluded legacy link source unexpectedly exists')
                continue
            link_one(root, src, root / relative(link['dest']))
    for link in adaptation['generated_links']:
        link_one(root, root / relative(link['source']), root / relative(link['dest']))


def link_one(root, source, dest):
    require(source.exists() and source.resolve().is_relative_to(root.resolve()), 'missing or escaped link source')
    require(dest.parent.resolve().is_relative_to(root.resolve()), 'link parent escaped workspace')
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = os.path.relpath(source, dest.parent)
    if dest.is_symlink():
        require(os.readlink(dest) == target, 'existing workspace link differs')
    else:
        require(not dest.exists(), 'workspace link destination occupied')
        dest.symlink_to(target)


def verify_untracked(root, rows, adaptation):
    allowed = {r['path'] for r in rows}
    allowed |= {l['dest'] for r in rows for l in r['linkfiles']}
    allowed |= {l['dest'] for l in adaptation['generated_links']}
    for row in rows:
        for name in git(root / row['path'], 'ls-files', '--others', '--exclude-standard').splitlines():
            full = row['path'] + '/' + name.rstrip('/')
            require(any(full == a or full.startswith(a + '/') for a in allowed if a != row['path']),
                    'untracked source input: ' + full)


def shared_headers(root, adaptation):
    """Headers that the core kernel and the vendor kernel tree each carry must stay
    byte-identical: structures in them cross the Image/module boundary, and a
    difference can change a RANDSTRUCT layout on one side only."""
    checked = 0
    for group in adaptation.get('shared_headers', []):
        listings = []
        for tree in group['trees']:
            listing = git(root / relative(tree), 'ls-tree', '-r', '--full-tree', 'HEAD', '--',
                          *[relative(p).as_posix() for p in group['paths']])
            require(listing, 'shared headers missing: ' + tree)
            listings.append(listing)
        require(all(l == listings[0] for l in listings), 'shared headers differ between ' + ' and '.join(group['trees']))
        checked += len(listings[0].splitlines())
    return checked


def prepare(root, reference=None):
    plan, changes, adaptation = configuration()
    with locked(root):
        rows = sources(root, plan, changes, prepare=True, reference=reference)
        links(root, rows, adaptation)
        verify_untracked(root, rows, adaptation)
        shared = shared_headers(root, adaptation)
        manifest = ET.Element('manifest')
        for row in rows:
            ET.SubElement(manifest, 'project', name=row['project'], revision=row['revision'],
                          path=os.path.relpath(root / row['path'], root / 'kernel_platform'))
        (root / 'resolved-manifest.xml').write_bytes(ET.tostring(manifest, encoding='utf-8'))
        borrowed_objects = any((root / row['path'] / git(root / row['path'], 'rev-parse', '--git-path', 'objects/info/alternates')).is_file() for row in rows)
        result = {'status': 'PASS', 'operation': 'kernel-source-preparation', 'project_count': len(rows),
                  'source_plan_sha256': sha(ROOT / 'config/kernel-sources-fp6.json'),
                  'patches_sha256': sha(ROOT / 'config/patches.json'),
                  'resolved_manifest_sha256': sha(root / 'resolved-manifest.xml'),
                  'shared_headers_checked': shared,
                  'local_object_reference': borrowed_objects, 'device_commands_executed': 0}
        (root / 'preparation.json').write_bytes(encoded(result))
        return result


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


def build(root, jobs, timeout):
    missing = [name for name in ('modinfo', 'modprobe', 'nm', 'readelf', 'openssl')
               if shutil.which(name) is None]
    require(not missing, 'kernel verification tools missing from PATH: ' + ', '.join(missing))
    plan, changes, adaptation = configuration()
    recipe = load_json(ROOT / 'config/fp6-kernel-packaging.json')
    with locked(root):
        rows = sources(root, plan, changes)
        links(root, rows, adaptation)
        verify_untracked(root, rows, adaptation)
        shared_headers(root, adaptation)
        preparation = load_json(root / 'preparation.json')
        require(sha(root / 'resolved-manifest.xml') == preparation['resolved_manifest_sha256'], 'resolved source manifest changed')
        require(preparation['source_plan_sha256'] == sha(ROOT / 'config/kernel-sources-fp6.json') and
                preparation['patches_sha256'] == sha(ROOT / 'config/patches.json'), 'source preparation uses different recipes')
        work = root / 'kernel_platform'
        run = root / 'runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
        run.mkdir(parents=True)
        result = {'status': 'RUNNING', 'operation': 'kernel-build-and-package', 'commands': [],
                  'device_commands_executed': 0, 'kernel_accepted': False,
                  'preparation_sha256': sha(root / 'preparation.json'),
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
        flags = ['--jobs=' + str(jobs), KMI]
        save()
        try:
            bazel('core-build', ['build', *flags, *CORE, *IMPLICIT])
            # Capture only top-level configured outputs; other transitions can be unbuilt.
            paths = call([work / 'tools/bazel', '--batch', 'cquery', KMI, '--output=files',
                          'config(set(' + ' '.join(CORE + IMPLICIT) + '), target)'], cwd=work, env=env)
            (run / 'core-paths.txt').write_text(paths)
            execution, core = output_files(work, paths)
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
            paths = call([work / 'tools/bazel', '--batch', 'cquery', KMI, '--output=files',
                          'config(set(' + ' '.join(targets) + '), target)'], cwd=work, env=env)
            (run / 'module-paths.txt').write_text(paths)
            _, external = output_files(work, paths)
            def one(files, name, fragment):
                found = [p for p in files if p.name == name and fragment in str(p)]
                require(len(found) == 1, 'ambiguous/missing artifact: ' + fragment + '/' + name)
                return found[0]
            vendor_core = [p for p in core if '/msm-kernel/fps_gki/' in str(p)]
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
            dt_rows = [r for r in rows if r['path'].startswith('vendor/') and r['path'].endswith('-devicetree')]
            require(dt_rows, 'vendor device-tree projects missing')
            for row in dt_rows:
                dt_env['EXT_MODULES'] = os.path.relpath(root / row['path'], work)
                command('dt-' + Path(row['path']).name, ['bash', 'build/build_module.sh', '-j' + str(jobs), 'dtbs'], dt_env)
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
            from . import kernel_config
            effective = one(core, '.config', '/common/kernel_aarch64_config/')
            config_report = kernel_config.check(effective.read_bytes(), load_json(ROOT / 'config/kernel-policy-fp6.json'), 'development')
            (run / 'kernel-config.json').write_bytes(encoded(config_report))
            require(config_report['status'] == 'PASS', 'development kernel configuration regressed')
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
                           work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin/llvm-strip', work,
                           (gki / 'scripts/sign-file', gki / 'certs/signing_key.pem',
                            gki / 'certs/signing_key.x509', 'sha256'))
            # Check the packaged (stripped and signed) modules against the Image's
            # built-in certificate, symbol CRCs and namespaces.
            from .kernel_interfaces import verify_built
            verify_built(work, run, {name: candidate / 'modules' / name for name in selected},
                         core + external, one(core, 'vmlinux', '/common/kernel_aarch64/'),
                         module_metadata, call, require)
            sources(root, plan, changes); verify_untracked(root, rows, adaptation)
            for p in candidate.rglob('*'):
                if p.is_file(): p.chmod(0o640)
            inventory = [{'path': p.relative_to(candidate).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                         for p in sorted(candidate.rglob('*')) if p.is_file()]
            (run / 'artifacts.json').write_bytes(encoded(inventory))
            result.update(status='PASS', module_count=len(selected), dtb_count=len(dtbs), dtbo_count=len(dtbos),
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


def repo_manifest():
    """Render the kernel source plan as a repo manifest for kernel_manifest-fp6.

    Patched projects point at the DiamaneOS forks at their derived revisions. The
    generated workspace links cannot be expressed in a manifest; `kernel prepare`
    creates them and verifies every revision after `repo sync`.
    """
    plan, changes, adaptation = configuration()
    patches = {p['path']: p for p in changes}
    remotes = {'fairphone': plan['source_url']}

    def remote(url):
        parsed = urlparse(url)
        require(parsed.scheme == 'https', 'unexpected project URL: ' + url)
        path = parsed.path.strip('/').removesuffix('.git')
        if parsed.hostname == 'git.codelinaro.org':
            require(path.startswith('clo/la/'), 'unexpected CodeLinaro project: ' + url)
            remotes.setdefault('codelinaro', 'https://git.codelinaro.org/clo/la/')
            return 'codelinaro', path.removeprefix('clo/la/')
        require(parsed.hostname == 'github.com', 'unexpected project host: ' + url)
        owner, name = path.split('/')
        remotes.setdefault(owner.lower(), 'https://github.com/' + owner + '/')
        return owner.lower(), name

    lines = []
    for row in plan['projects']:
        patch = patches.get(row['path'])
        attrs = [('path', row['path'])]
        if patch:
            where, name = remote(patch['repository'])
            attrs += [('name', name), ('remote', where), ('revision', patch['derived_revision'])]
        elif 'url' in row:
            where, name = remote(row['url'])
            attrs += [('name', name), ('remote', where), ('revision', row['revision'])]
        else:
            attrs += [('name', row['project']), ('revision', row['revision'])]
        kept = [l for l in row['linkfiles'] if not any(
            e['project'] == row['project'] and e['revision'] == row['revision'] and
            e['src'] == l['src'] and e['dest'] == l['dest'] for e in adaptation['excluded_linkfiles'])]
        tag = '  <project ' + ' '.join(k + '=' + quoteattr(v) for k, v in attrs)
        if not kept:
            lines.append(tag + ' />')
            continue
        lines.append(tag + '>')
        lines += ['    <linkfile src=' + quoteattr(l['src']) + ' dest=' + quoteattr(l['dest']) + ' />' for l in kept]
        lines.append('  </project>')
    head = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<!-- Generated by DiamaneOS tools (diamaneos kernel manifest) from',
            '     config/kernel-sources-fp6.json and config/patches.json. Do not edit. -->',
            '<manifest>']
    head += ['  <remote name=' + quoteattr(k) + ' fetch=' + quoteattr(v) + ' />' for k, v in remotes.items()]
    head += ['', '  <default remote="fairphone" sync-j="8" />', '']
    return ('\n'.join(head + lines + ['</manifest>']) + '\n').encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'build', 'manifest'])
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--output', type=Path, help='manifest: write the repo manifest here')
    parser.add_argument('--check', type=Path, help='manifest: fail unless this file matches the generated manifest')
    parser.add_argument('--reference', type=Path, help='optional existing source workspace for Git object reuse only')
    parser.add_argument('--jobs', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=7200, help='maximum seconds per compilation command')
    args = parser.parse_args(argv)
    if args.operation == 'manifest':
        try:
            require(args.workspace is None and args.reference is None, 'manifest takes no workspace')
            require((args.output is None) != (args.check is None), 'manifest needs exactly one of --output or --check')
            rendered = repo_manifest()
            if args.output:
                args.output.write_bytes(rendered)
            else:
                require(args.check.read_bytes() == rendered, 'kernel manifest differs from the source plan: ' + str(args.check))
            print(json.dumps({'status': 'PASS', 'operation': 'kernel-manifest', 'sha256': hashlib.sha256(rendered).hexdigest()}, indent=2))
            return 0
        except (KernelError, VendorError, OSError, ValueError, KeyError) as exc:
            print('ERROR: kernel manifest failed: ' + str(exc), file=sys.stderr); return 2
    if args.workspace is None:
        parser.error('--workspace is required')
    if args.output or args.check:
        parser.error('--output and --check are manifest options')
    try:
        require(1 <= args.jobs <= 64 and 60 <= args.timeout <= 21600, 'invalid build resource limits')
        require(args.operation == 'prepare' or args.reference is None, 'reference is a preparation option')
        with process.interrupt_on_termination():
            result = prepare(args.workspace.absolute(), args.reference) if args.operation == 'prepare' else build(args.workspace.absolute(), args.jobs, args.timeout)
        print(json.dumps(result, indent=2)); return 0
    except KeyboardInterrupt:
        print('ERROR: kernel preparation interrupted', file=sys.stderr); return 130
    except (KernelError, VendorError, OSError, ValueError, KeyError) as exc:
        print('ERROR: kernel preparation failed: ' + str(exc), file=sys.stderr); return 2
