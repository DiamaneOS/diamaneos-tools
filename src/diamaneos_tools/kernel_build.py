"""Compile the FP6 kernel set on the 6.18 common kernel.

The common kernel (DiamaneOS/kernel_common-6.18 with the FP6 commits) is
built twice from one tree: the GKI core build (Image, GKI modules, the signing
key) and the FP6 device kernel build, which adds the drivers the 6.18 GKI build
does not make (cfg80211, mac80211, BAM DMA, cqhci). The vendor module packages
of the vendor tree build as external modules against the device kernel build,
in the order config/fp6-kernel-build.json lists.
"""
import os
from pathlib import Path
import re
import shutil
import tarfile
import time

from . import kernel_config, kernel_kbuild
from .kernel_util import call, git, require
from .vendor_extract import sha

VISIBILITY_WARNING = re.compile(rb'^.*\[-Wvisibility\]$', re.M)
SYMVERS = 'Module.symvers'


def visibility_warnings(log_bytes):
    """Clang's -Wvisibility names a struct first declared inside a parameter list.
    It is on by default, so any occurrence in a build log is new."""
    return [m.decode(errors='replace').strip() for m in VISIBILITY_WARNING.findall(log_bytes)]


def toolchain(root, plan, farm, epoch, maps=()):
    """The environment and make arguments of every kernel build: the pinned toolchain
    through a small farm of links, and nothing from the caller's environment.
    maps are the directories whose names must not end up in the Image or a module:
    C file names, Rust paths and the compilation directory of the debug information lose
    their prefix."""
    paths = {key: root / value for key, value in plan['toolchain'].items() if isinstance(value, str)}
    for key in ('clang', 'rust', 'bindgen', 'kernel_build_tools', 'build_tools', 'bison_data', 'host_libraries',
                'zlib_include'):
        require(paths[key].exists(), 'toolchain missing: ' + plan['toolchain'][key])
    bin_dir, include, lib = farm / 'bin', farm / 'include', farm / 'lib'
    for directory in (bin_dir, include, lib):
        directory.mkdir(parents=True, exist_ok=True)

    def link(directory, name, target):
        path = directory / name
        if path.is_symlink() or path.exists():
            path.unlink()
        path.symlink_to(target)
    tools = paths['build_tools']
    for name, target in (('bison', tools / 'bison'), ('flex', tools / 'flex'), ('m4', tools / 'm4'),
                         ('bc', tools / 'gavinhoward-bc'), ('bindgen', paths['bindgen'])):
        require(target.exists(), 'toolchain tool missing: ' + target.name)
        link(bin_dir, name, target)
    pahole = paths['kernel_build_tools'] / 'bin/pahole'
    require(pahole.exists(), 'toolchain tool missing: pahole')
    single_thread_pahole(bin_dir / 'pahole', pahole)
    for name in ('elfutils', 'gelf.h', 'libelf.h', 'dwarf.h', 'nlist.h'):
        link(include, name, paths['kernel_build_tools'] / 'include' / name)
    link(include, 'zlib.h', paths['zlib_include'] / 'zlib.h')
    link(include, 'zconf.h', paths['zlib_include'] / 'x86_64-linux-gnu/zconf.h')
    link(lib, 'libz.so', paths['host_libraries'] / 'lib64/libz-host.so')
    for soname in plan['toolchain']['system_libraries']:
        found = [Path(d) / soname for d in ('/usr/lib/x86_64-linux-gnu', '/usr/lib64', '/lib/x86_64-linux-gnu')
                 if (Path(d) / soname).exists()]
        require(found, 'host library missing: ' + soname)
        link(lib, soname.split('.so')[0] + '.so', found[0])
    host = paths['host_libraries']
    env = {'PATH': ':'.join(str(p) for p in (paths['clang'] / 'bin', paths['rust'] / 'bin', bin_dir)) + ':/usr/bin:/bin',
           'LIBCLANG_PATH': str(paths['clang'] / 'lib'),
           'BISON_PKGDATADIR': str(paths['bison_data']), 'M4': str(tools / 'm4'),
           'SOURCE_DATE_EPOCH': epoch,
           'KBUILD_BUILD_TIMESTAMP': time.strftime('%a %b %e %H:%M:%S UTC %Y', time.gmtime(int(epoch))),
           'KBUILD_BUILD_USER': plan['make']['build_user'], 'KBUILD_BUILD_HOST': plan['make']['build_host'],
           'KCFLAGS': ' '.join([*plan['make']['kcflags'], *('-ffile-prefix-map=%s=' % m for m in maps)]),
           'KRUSTFLAGS': ' '.join('--remap-path-prefix=%s=' % m for m in maps),
           'LC_ALL': 'C', 'TZ': 'UTC', 'HOME': str(farm),
           # setlocalversion finds no repository and adds no version of its own: the release takes its commit
           # from the localversion file, as a Kleaf build does.
           'GIT_DIR': str(farm / 'no-repository')}
    make = ['make', *plan['make']['arguments'],
            'HOSTCFLAGS=-I%s -I%s' % (host / 'include', include),
            'HOSTLDFLAGS=-L%s -L%s -Wl,-rpath,%s' % (lib, host / 'lib64', host / 'lib64')]
    return env, make


def release_localversion(common, revision, android_release):
    """The localversion of a GKI release, -<android release>-<KMI generation>-g<12 hex>, from the BRANCH and
    KMI_GENERATION of the common tree's build.config.constants. libvintf reads the kernel's FCM level from it."""
    values = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', (common / 'build.config.constants').read_text(), re.M))
    branch, generation = values.get('BRANCH', ''), values.get('KMI_GENERATION', '')
    require(branch.startswith(android_release + '-') and re.fullmatch(r'[0-9]+', generation),
            'the common tree is not an %s branch with a KMI generation: BRANCH %s, KMI_GENERATION %s' % (
                android_release, branch or 'unset', generation or 'unset'))
    return '-%s-%s-g%s' % (android_release, generation, revision[:12])


def single_thread_pahole(wrapper, pahole):
    """pahole run with one thread. Its parallel BTF encoding orders types differently on each run,
    which changes the Image and every module with it."""
    wrapper.unlink(missing_ok=True)
    wrapper.write_text('#!/bin/bash\n'
                       'arguments=()\n'
                       'for argument in "$@"; do\n'
                       '    case "$argument" in -j|-j[0-9]*|--jobs|--jobs=*) ;; *) arguments+=("$argument") ;; esac\n'
                       'done\n'
                       'exec %s -j1 "${arguments[@]}"\n' % pahole)
    wrapper.chmod(0o755)


KCONFIG_SYMBOL = re.compile(r'^[ \t]*(?:menu)?config[ \t]+([A-Za-z0-9_]+)[ \t]*$', re.M)


def kconfig_symbols(common, minimum=5000):
    """The CONFIG_ names the Kconfig files of the common tree declare. A declared symbol that the kernel
    configuration does not mention is hidden behind an option that is off, so it is n."""
    found = set()
    for top, directories, files in os.walk(common):
        directories[:] = [d for d in directories if d != '.git']
        for name in files:
            if name.startswith('Kconfig') or name.endswith('.kconfig'):
                found |= {'CONFIG_' + s for s in KCONFIG_SYMBOL.findall(Path(top, name).read_text(errors='replace'))}
    require(len(found) >= minimum, 'the common tree declares too few Kconfig symbols')
    return found


def vendor_conflicts(kernel_values, vendor_values, declared=()):
    """Vendor values that differ from the kernel configuration: symbol -> [vendor, kernel]. A symbol the kernel
    configuration lacks but the Kconfig files declare counts as n. The vendor modules compile with the kernel's
    values plus these, so each one is a decision."""
    found = {}
    for symbol, value in sorted(vendor_values.items()):
        kernel = kernel_values.get(symbol, 'n' if symbol in declared else None)
        if kernel is not None and kernel != value:
            found[symbol] = [value, kernel]
    return found


def check_overrides(ctx, step, vendor_values):
    """Vendor values that differ from the kernel's must be the reviewed overrides of the build plan, each with
    the vendor and kernel value the review saw."""
    found = vendor_conflicts(ctx['kernel_values'], vendor_values, ctx['declared'])
    stray = sorted(set(found) - set(ctx['overrides']))
    require(not stray, '%s sets kernel symbols to other values than the kernel configuration: %s' %
            (step['name'], ', '.join('%s=%s (kernel %s)' % (k, found[k][0], found[k][1]) for k in stray[:5])))
    changed = sorted(k for k in found if found[k] != ctx['overrides'][k])
    require(not changed, '%s: reviewed overrides now have other values: %s' % (
        step['name'], ', '.join('%s is vendor %s kernel %s, reviewed vendor %s kernel %s' % (k, *found[k], *ctx['overrides'][k])
                                for k in changed[:5])))
    ctx['conflicts'].update(found)


def step_values(ctx, step, stage):
    """The kernel symbols the vendor tree sets for one step: the base and package defconfigs, the extra
    configuration and the symbols the package selects, or the merged defconfigs of a group step."""
    modules = stage / 'modules'
    if step['kind'] == 'base':
        return kernel_kbuild.read_config(str(modules / step['package'] / 'defconfig'))
    if step['kind'] == 'groups':
        merged = {}
        configs = [modules / d / 'defconfig' for d in step['defconfigs']]
        for path in [*configs, *(modules / g / 'defconfig' for g in ('display', 'gpu', 'touch'))]:
            merged.update(kernel_kbuild.read_config(str(path)))
        return merged
    extra = [str(ctx['tools_root'] / step['extra_defconfig'])] if step.get('extra_defconfig') else []
    return kernel_kbuild.package_config(str(modules / step['package']), str(modules / 'base'),
                                        str(ctx['device'] / 'include/config/auto.conf'), extra)[0]


HOST_TOOLS = {'make': ['--version'], 'perl': ['-e', 'print "$^V\\n"'], 'openssl': ['version'], 'python3': ['--version'],
              'git': ['--version'], 'bash': ['--version'], 'rsync': ['--version'], 'tar': ['--version'],
              'modinfo': ['--version'], 'modprobe': ['--version'], 'nm': ['--version'], 'readelf': ['--version']}


def host_tools(farm, libraries):
    """The host programs and libraries the build uses outside the pinned toolchain: where they are, a
    SHA-256 of the file and the first line of their version output."""
    found = {}
    for name, arguments in sorted(HOST_TOOLS.items()):
        path = shutil.which(name, path='/usr/bin:/bin:/usr/sbin:/sbin')
        require(path is not None, 'host tool missing: ' + name)
        real = Path(path).resolve()
        output = call([real, *arguments], cwd=farm, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}).strip().splitlines()
        found[name] = {'path': str(real), 'sha256': sha(real), 'version': output[0] if output else ''}
    for soname in libraries:
        link = farm / 'lib' / (soname.split('.so')[0] + '.so')
        require(link.exists(), 'host library missing: ' + soname)
        real = link.resolve()
        found[soname] = {'path': str(real), 'sha256': sha(real)}
    return found


def merge_config(parts, destination):
    """The configuration to start from: the defconfig, then each fragment, later lines winning."""
    texts = []
    for part in parts:
        require(part.is_file(), 'configuration file missing: ' + part.name)
        texts.append(part.read_text())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('\n'.join(texts) + '\n')


def requested_values(fragments):
    """The value each fragment asks for, the last request of a symbol winning."""
    wanted = {}
    for text in fragments:
        for line in text.splitlines():
            line = line.strip()
            match = re.fullmatch(r'(CONFIG_[A-Za-z0-9_]+)=(.*)', line)
            if match:
                wanted[match.group(1)] = match.group(2)
                continue
            match = re.fullmatch(r'# (CONFIG_[A-Za-z0-9_]+) is not set', line)
            if match:
                wanted[match.group(1)] = 'n'
    return wanted


def unmet_requests(fragments, final):
    """Fragment requests the final configuration does not honour (a symbol Kconfig
    turned back, dropped or never knew)."""
    values = kernel_config.parse_config(final)
    return {s: [w, values.get(s)] for s, w in sorted(requested_values(fragments).items()) if values.get(s, 'n') != w}


def unmet_reason(symbol, found, declared):
    """Why a request is not in the final configuration. A symbol that the .config lacks is either one the
    Kconfig files do not declare or one hidden behind an option that is off."""
    if found is not None:
        return 'it ends up ' + found
    if symbol in declared:
        return 'the pinned common kernel declares it, but an option it depends on is off'
    return 'the pinned common kernel does not declare it'


def device_config_differences(core, device, allowed):
    """Symbols whose value differs between the GKI and the device kernel configuration.
    Both builds share one source tree and one Image, so any other difference could
    change a structure the modules and the Image disagree on."""
    core, device = kernel_config.parse_config(core), kernel_config.parse_config(device)
    changed = {s: [core.get(s, 'n'), device.get(s, 'n')] for s in sorted(set(core) | set(device))
               if core.get(s, 'n') != device.get(s, 'n')}
    stray = sorted(set(changed) - set(allowed))
    require(not stray, 'the device kernel build changes the GKI configuration: ' + ', '.join(stray[:5]))
    return changed


def vmlinux_exports(symvers_text):
    return sorted(line for line in symvers_text.splitlines() if line.split('\t')[2:3] == ['vmlinux'])


def exported_symbols(tables, owners=None):
    """Every symbol the Module.symvers tables of the kernel builds export, by the Image or by a module.
    With owners (module names without .ko), only the symbols those modules export."""
    wanted = None if owners is None else {o.replace('-', '_') for o in owners}
    found = set()
    for table in tables:
        for line in Path(table).read_text().splitlines():
            fields = line.split('\t')
            if len(fields) >= 3 and (wanted is None or fields[2].rsplit('/', 1)[-1].replace('-', '_') in wanted):
                found.add(fields[1])
    return found


def stage_vendor_path(vendor, revision, destination, path):
    """One directory of the pinned vendor commit, extracted clean so that nothing the build
    writes touches the checkout."""
    destination.mkdir(parents=True)
    archive = destination.with_suffix('.tar')
    git(vendor, 'archive', '--format=tar', '-o', archive, revision, path)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter='data')
    archive.unlink()


def stage_vendor_modules(vendor, revision, destination):
    """The modules/ tree of the pinned vendor commit, where the generated Kbuild files go."""
    stage_vendor_path(vendor, revision, destination, 'modules')
    require((destination / 'modules/base/Makefile').is_file(), 'the vendor tree has no modules/base')


def device_tree_tools(clang, qcom_dtc, dtc, tools, env):
    """The tools the vendor device tree script takes from TOOLS: the common tree's dtc as the core build made it,
    and Qualcomm's fdtoverlay, fdtoverlaymerge, fdtget and fdtput, built with the pinned Clang against their own
    libfdt. ufdt_apply_overlay, the merge script's check that an overlay applies, runs fdtoverlay as the vendor
    script's own tool build does."""
    require(dtc.is_file(), 'the core build made no dtc')
    obj = tools / 'obj'
    obj.mkdir(parents=True)
    shutil.copyfile(qcom_dtc / 'version_non_gen.h', obj / 'version_gen.h')
    flags = ['-O2', '-w', '-I', qcom_dtc / 'libfdt', '-I', qcom_dtc, '-I', obj]

    def compile_c(source):
        out = obj / (source.parent.name + '-' + source.stem + '.o')
        call([clang, *flags, '-c', source, '-o', out], cwd=tools, env=env)
        return out
    shared = [compile_c(c) for c in sorted((qcom_dtc / 'libfdt').glob('*.c'))] + [compile_c(qcom_dtc / 'util.c')]
    for name in ('fdtoverlay', 'fdtoverlaymerge', 'fdtget', 'fdtput'):
        call([clang, '-fuse-ld=lld', '-o', tools / name, compile_c(qcom_dtc / (name + '.c')), *shared], cwd=tools, env=env)
    shutil.copyfile(dtc, tools / 'dtc')
    (tools / 'ufdt_apply_overlay').write_text('#!/bin/sh\nexec "%s" -i "$1" -o "$3" "$2"\n' % (tools / 'fdtoverlay'))
    for name in ('dtc', 'ufdt_apply_overlay'):
        (tools / name).chmod(0o755)


def package_step(ctx, step, stage, outputs):
    """One vendor package: its Kbuild from the DDK registry, then an external-module build."""
    package = stage / 'modules' / step['package']
    base = stage / 'modules/base'
    out = ctx['out'] / step.get('output', step['name'])
    extra = [str(ctx['tools_root'] / step['extra_defconfig'])] if step.get('extra_defconfig') else []
    if step.get('defconfig_check'):
        check_defconfig(package / step['defconfig_check'], Path(extra[0]))
    if step.get('feature_header'):
        probes = step['feature_probes']
        found = kernel_kbuild.feature_probes(ctx['common'], [tuple(p[:3]) for p in probes])
        expected = {p[2]: p[3] for p in probes}
        changed = sorted(k for k in expected if found[k] != expected[k])
        require(not changed, 'the cfg80211 features of the common kernel changed: ' +
                ', '.join('%s is %s' % (k, 'found' if found[k] else 'missing') for k in changed))
        header = kernel_kbuild.feature_header(found)
        (package / step['feature_header']).write_text(header)
        (ctx['runs'] / (step['name'] + '-features.h')).write_text(header)
    check_overrides(ctx, step, step_values(ctx, step, stage))
    table = kernel_kbuild.generate(str(package), str(base), str(out), str(ctx['device'] / 'include/config/auto.conf'),
                                   only=step.get('modules', ()), extra_defconfig=extra)
    symbols = [ctx['out'] / 'vendor' / SYMVERS] + [ctx['out'] / s / SYMVERS for s in step['symbols']]
    for path in symbols:
        require(path.is_file(), 'symbol table missing for %s: %s' % (step['name'], path.relative_to(ctx['out'])))
    arguments = ['-C', str(ctx['common']), 'O=' + str(ctx['device']), 'M=' + str(package), 'MO=' + str(out),
                 'VENDOR_ROOT=' + str(package), 'VENDOR_OUT=' + str(out),
                 'KBUILD_EXTRA_SYMBOLS=' + ' '.join(map(str, symbols)), '-k', 'modules']
    call([*ctx['make'], *arguments, '-j%d' % ctx['jobs']], cwd=package, env=ctx['env'], timeout=ctx['timeout'],
         log=ctx['logs'] / (step['name'] + '.log'))
    modules = built_modules(out, [row.split('\t')[0] for row in table], step['name'])
    outputs[step['name']] = out
    return modules


def built_modules(out, registered, name):
    """The modules under out, which must be exactly the ones the registry names."""
    modules = sorted(p.name for p in out.rglob('*.ko'))
    different = sorted(set(modules) ^ set(registered))
    require(modules == sorted(registered) and len(set(modules)) == len(modules),
            '%s: the built modules differ from the registry: %s' % (name, ', '.join(different[:5]) or 'a module twice'))
    return modules


def check_defconfig(defconfig, effective):
    """Every value the vendor defconfig sets must hold in the effective configuration."""
    wanted, have = kernel_kbuild.read_config(str(defconfig)), kernel_kbuild.read_config(str(effective))
    wrong = sorted(k for k, v in wanted.items() if have.get(k) != v)
    require(not wrong, 'the effective configuration differs from the vendor defconfig: ' + ', '.join(wrong[:5]))


def base_step(ctx, step, stage, outputs):
    out = ctx['out'] / step['output']
    check_overrides(ctx, step, step_values(ctx, step, stage))
    arguments = ['-C', str(stage / 'modules' / step['package']), 'KERNEL_SRC=' + str(ctx['common']),
                 'KERNEL_OUT=' + str(ctx['device']), 'OUT=' + str(out)]
    call([*ctx['make'], *arguments, '-j%d' % ctx['jobs']], cwd=stage, env=ctx['env'], timeout=ctx['timeout'],
         log=ctx['logs'] / (step['name'] + '.log'))
    outputs[step['name']] = out
    table = [row.split('\t')[0] for row in (out / 'modules.tsv').read_text().splitlines() if row]
    return built_modules(out, table, step['name'])


def groups_step(ctx, step, stage, outputs):
    """The display, GPU and touch groups through the vendor tree's own check script."""
    modules = stage / 'modules'
    out = ctx['out'] / step['output']
    symbols = [ctx['out'] / 'vendor' / SYMVERS] + [ctx['out'] / s / SYMVERS for s in step['symbols']]
    configs = [modules / d / 'defconfig' for d in step['defconfigs']]
    check_overrides(ctx, step, step_values(ctx, step, stage))
    env = dict(ctx['env'], KSRC=str(ctx['common']), KOUT=str(ctx['device']), VINC=str(modules / 'base'),
               VCONFIG=' '.join(map(str, configs)), OUT=str(out), JOBS=str(ctx['jobs']),
               MK=' '.join(ctx['make'][:1] + [a for a in ctx['make'][1:] if not a.startswith('HOST')]),
               BASE_SYMVERS=' '.join(map(str, symbols)))
    script = modules / step['package'] / 'build/check.sh'
    require(script.is_file(), 'the vendor tree has no ' + step['package'] + '/build/check.sh')
    call(['bash', script, *step['groups']], cwd=modules, env=env, timeout=ctx['timeout'],
         log=ctx['logs'] / (step['name'] + '.log'))
    for group in step['groups']:
        require(any((out / 'obj' / group).rglob('*.ko')), step['name'] + ': the group %s built no module' % group)
        outputs['p4/obj/' + group] = out / 'obj' / group
    return sorted(p.name for p in out.rglob('*.ko'))


STEPS = {'base': base_step, 'package': package_step, 'groups': groups_step}


def collect(directories):
    """name -> file of every module built, each name once."""
    pool = {}
    for directory in directories:
        for path in sorted(Path(directory).rglob('*.ko')):
            require(path.name not in pool, 'two builds make the module ' + path.name)
            pool[path.name] = path
    return pool
