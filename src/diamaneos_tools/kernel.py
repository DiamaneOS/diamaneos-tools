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
import xml.etree.ElementTree as ET

from . import process
from .vendor_extract import sha, relative
from .vendor import ROOT, load_json, VendorError, encoded

KMI = '--user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom'
CORE = ['//common:kernel_aarch64', '//msm-kernel:fps_gki',
        '//msm-kernel:fps_gki_abi', '//common:kernel_aarch64_abi']
IMPLICIT = ['//common:kernel_aarch64_modules', '//common:kernel_aarch64_config']


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
        url = patch['repository'] if patch else plan['source_url'] + row['project']
        created = prepare and not (dest / '.git').exists()
        if created:
            require(not dest.exists() or not any(dest.iterdir()), 'unowned source directory is occupied')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if reference and (reference / row['path'] / '.git').exists():
                # Local object reuse is optional; all revisions and patch preimages are still verified.
                call(['git', 'clone', '--no-checkout', '--shared', reference / row['path'], dest], cwd=root)
            else:
                dest.mkdir(exist_ok=True)
                git(dest, 'init', '-q')
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
            diff = process.run(['git', '-C', str(dest), 'diff', '--full-index', row['revision'], revision], 120, cwd=root)
            require(diff['transport'] == 'ok' and hashlib.sha256(diff['stdout']).hexdigest() == patch['canonical_diff_sha256'],
                    'downstream patch bytes differ')
            require(git(dest, 'diff', '--name-only', row['revision'], revision).splitlines() == patch['changed_files'],
                    'downstream patch file set differs')
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


def prepare(root, reference=None):
    plan, changes, adaptation = configuration()
    with locked(root):
        rows = sources(root, plan, changes, prepare=True, reference=reference)
        links(root, rows, adaptation)
        verify_untracked(root, rows, adaptation)
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


def render_package(candidate, selected, merged, image, recipe, strip, work):
    candidate.mkdir()
    (candidate / 'modules').mkdir()
    signed = []
    for name, source in sorted(selected.items()):
        dest = candidate / 'modules' / name
        before = module_metadata(source)
        if any(k == b'signer' and v for k, v in before):
            shutil.copyfile(source, dest); signed.append(name)
            require(sha(dest) == sha(source), 'signed module changed')
        else:
            call([strip, '--strip-debug', '-o', dest, source], cwd=work)
        require(module_metadata(dest) == before, 'stripping changed module metadata')
        require(call(['modprobe', '--dump-modversions', source], cwd=work) ==
                call(['modprobe', '--dump-modversions', dest], cwd=work), 'stripping changed symbol CRCs')
    require(set(signed) == set(recipe['partitions']['system_dlkm']), 'signed module placement differs')
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
    plan, changes, adaptation = configuration()
    recipe = load_json(ROOT / 'config/fp6-kernel-packaging.json')
    with locked(root):
        rows = sources(root, plan, changes)
        links(root, rows, adaptation)
        verify_untracked(root, rows, adaptation)
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
            bazel('common-abi', ['run', *flags, '//common:kernel_aarch64_abi_dist', '--', '--dist_dir', str(run / 'abi')])
            # Capture only top-level configured outputs; other transitions can be unbuilt.
            paths = call([work / 'tools/bazel', '--batch', 'cquery', KMI, '--output=files',
                          'config(set(' + ' '.join(CORE + IMPLICIT) + '), target)'], cwd=work, env=env)
            (run / 'core-paths.txt').write_text(paths)
            execution, core = output_files(work, paths)
            # run :kernel_aarch64_abi_dist enforces failure; also inspect the rule's saved comparison code.
            exit_files = set(p.parent / 'exit_code_file.txt' for p in
                             (execution / 'bazel-out').glob('*/bin/common/kernel_aarch64_abi_diff/abi_stgdiff'))
            require(exit_files and all(p.is_file() and p.read_text().strip() == '0' for p in exit_files), 'common ABI comparison failed')
            query = 'filter(":fps_gki.*", kind("_kernel_module rule", //vendor/...))'
            targets = sorted(set(call([work / 'tools/bazel', '--batch', 'query', '--output=label', query], cwd=work, env=env).splitlines()))
            require(targets and all(re.fullmatch(r'//vendor/[A-Za-z0-9_./-]+:fps_gki[A-Za-z0-9_.-]*', t) for t in targets), 'invalid external target set')
            require(any('/audio-kernel:' in t for t in targets) and any('/wlan/qcacld-3.0:' in t for t in targets), 'missing audio/WLAN target')
            (run / 'module-targets.txt').write_text('\n'.join(targets) + '\n')
            bazel('external-modules', ['build', *flags, *targets])
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
            from . import kernel_config
            effective = one(core, '.config', '/common/kernel_aarch64_config/')
            config_report = kernel_config.check(effective.read_bytes(), load_json(ROOT / 'config/kernel-policy-fp6.json'), 'development')
            (run / 'kernel-config.json').write_bytes(encoded(config_report))
            require(config_report['status'] == 'PASS', 'development kernel configuration regressed')
            shutil.copyfile(effective, run / 'gki.config')
            shutil.copyfile(kit / '.config', run / 'vendor.config')
            image = one(core, 'Image', '/common/kernel_aarch64/')
            from .kernel_interfaces import verify_built
            interfaces = verify_built(work, run, selected, core + external,
                                      one(core, 'vmlinux', '/common/kernel_aarch64/'),
                                      module_metadata, call, require)
            candidate = run / 'candidate'
            render_package(candidate, selected, merged, image, recipe,
                           work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin/llvm-strip', work)
            sources(root, plan, changes); verify_untracked(root, rows, adaptation)
            for p in candidate.rglob('*'):
                if p.is_file(): p.chmod(0o640)
            inventory = [{'path': p.relative_to(candidate).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                         for p in sorted(candidate.rglob('*')) if p.is_file()]
            (run / 'artifacts.json').write_bytes(encoded(inventory))
            result.update(status='PASS', module_count=len(selected), dtb_count=len(dtbs), dtbo_count=len(dtbos),
                          inventory_sha256=sha(run / 'artifacts.json'),
                          interfaces_sha256=sha(run / 'module-interfaces.json'),
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
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--reference', type=Path, help='optional existing source workspace for Git object reuse only')
    parser.add_argument('--jobs', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=7200, help='maximum seconds per compilation command')
    args = parser.parse_args(argv)
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
