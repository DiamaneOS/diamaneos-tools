"""Select, strip, sign and place the modules of the FP6 kernel set.

The packaging recipe (config/fp6-kernel-packaging.json) lists the modules of
vendor_boot, vendor_dlkm and system_dlkm and their load order. This takes those
modules from the vendor and device kernel builds (or the GKI core build for the
common ones), strips and signs them with the Image's own key, checks that
neither changed a module's metadata or symbol versions, and writes the files
the Android build reads (BoardConfigKernel.mk, device-kernel.mk, blocklists).
"""
import os
import re
import shutil

from .kernel_util import call, require

SIGNATURE_FIELDS = (b'sig_id', b'signer', b'sig_key', b'sig_hashalgo', b'signature')
VARIABLES = {'vendor_boot': 'VENDOR_RAMDISK', 'vendor_dlkm': 'VENDOR', 'system_dlkm': 'SYSTEM'}
ARGUMENT = re.compile(r'[a-z][a-z0-9_.-]*(=[A-Za-z0-9_.,:-]+)?')


def module_key(name):
    """Module names as the loader compares them: '-' and '_' are the same."""
    return name.removesuffix('.ko').replace('-', '_')


def signature(values):
    return [(k, v) for k, v in values if k in SIGNATURE_FIELDS]


def unsigned(values):
    return [(k, v) for k, v in values if k not in SIGNATURE_FIELDS]


def select(wanted, vendor_pool, core_pool):
    """name -> file for every wanted module: from the vendor and device builds, else the
    core build. A name found in both is ambiguous."""
    selected = {}
    for name in sorted(wanted):
        require(not (name in vendor_pool and name in core_pool), 'module built by the vendor and the core build: ' + name)
        path = vendor_pool.get(name) or core_pool.get(name)
        require(path is not None, 'listed module was not built: ' + name)
        selected[name] = path
    return selected


def check_placement(recipe, depends):
    """The lists are complete: every load list names only modules of its partition, vendor_boot
    holds the dependencies of its modules (from vendor_boot or system_dlkm, which the build
    places together) and no module lacks a dependency."""
    partitions = {p: set(names) for p, names in recipe['partitions'].items()}
    everything = set().union(*partitions.values())
    keys = {module_key(n): n for n in everything}
    for key, names in recipe['load_lists'].items():
        partition = key.split('/')[0]
        require(set(names) <= partitions[partition], 'load list escapes partition: ' + key)
    problems = []
    for partition, names in partitions.items():
        for name in sorted(names):
            for dependency in depends(name):
                other = keys.get(module_key(dependency))
                if other is None:
                    problems.append('%s needs %s, which no partition lists' % (name, dependency))
                elif other not in names and not (other in partitions['system_dlkm'] and partition != 'vendor_boot'):
                    problems.append('%s (%s) needs %s, which is not in the same partition' % (name, partition, other))
    require(not problems, 'incomplete module placement: ' + ', '.join(problems[:5]))


def required_modules(recipe):
    """The modules that userspace in the vendor image needs: the module, its partition, the vendor file that needs
    it and why. The module must be in the partition's load list, because nothing else loads it."""
    rows = recipe.get('required_modules', [])
    require(isinstance(rows, list), 'invalid required module list')
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'module', 'partition', 'needed_by', 'reason'}
                and all(isinstance(row[k], str) and row[k].strip() for k in row)
                and row['module'].endswith('.ko') and row['partition'] in VARIABLES, 'invalid required module')
    require(len({r['module'] for r in rows}) == len(rows), 'required module listed twice')
    return rows


def check_required_modules(recipe, shipped):
    """Every module a shipped vendor file needs is in its partition and in the partition's load list. Rows whose
    vendor file is not shipped do not apply. Returns the modules checked."""
    checked = []
    for row in required_modules(recipe):
        if row['needed_by'] not in shipped:
            continue
        name, partition = row['module'], row['partition']
        require(name in recipe['partitions'][partition],
                '%s is needed by %s and is not in the %s module list' % (name, row['needed_by'], partition))
        require(name in recipe['load_lists'].get(partition + '/modules.load', []),
                '%s is needed by %s and is not in the %s load list' % (name, row['needed_by'], partition))
        checked.append(name)
    return checked


def stage_modules(destination, selected, strip, signing, metadata, work, library_path):
    """Strip every selected module and sign it. signing is (sign-file, key, certificate, hash)."""
    destination.mkdir(parents=True)
    sign_file, key, certificate, algorithm = signing
    for name, source in sorted(selected.items()):
        target = destination / name
        before = metadata(source)
        call([strip, '--strip-debug', '-o', target, source], cwd=work)
        require(unsigned(metadata(target)) == unsigned(before), 'stripping changed module metadata: ' + name)
        call([sign_file, algorithm, key, certificate, target], cwd=work, env=dict(os.environ, LD_LIBRARY_PATH=str(library_path)))
        after = metadata(target)
        require(unsigned(after) == unsigned(before) and signature(after), 'signing changed module metadata: ' + name)
        require(call(['modprobe', '--dump-modversions', source], cwd=work) ==
                call(['modprobe', '--dump-modversions', target], cwd=work), 'stripping changed symbol CRCs: ' + name)
    keys = {tuple(r for r in signature(metadata(p)) if r[0] != b'signature') for p in destination.iterdir()}
    require(len(keys) == 1, 'modules are not all signed with the kernel build key')


def argument_rows(rows, forbidden):
    """The words of a list of {argument, reason} rows, checked against the forbidden ones."""
    require(isinstance(rows, list), 'invalid kernel command line')
    found = []
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'argument', 'reason'} and isinstance(row['argument'], str)
                and ARGUMENT.fullmatch(row['argument']) and isinstance(row['reason'], str) and row['reason'].strip(),
                'invalid kernel command line argument')
        require(row['argument'].split('=', 1)[0] not in forbidden, 'forbidden kernel argument: ' + row['argument'])
        found.append(row['argument'])
    require(len(set(found)) == len(found), 'duplicate kernel command line argument')
    return found


def cmdline_arguments(recipe, forbidden):
    """The kernel command line words of the recipe, checked against the forbidden ones."""
    return argument_rows(recipe.get('kernel_cmdline', []), forbidden)


def debug_arguments(recipe):
    """The words the debug profile adds. They are the ones a production command line must not have, so
    nothing is forbidden, but none may repeat a production word."""
    section = recipe.get('debug')
    require(isinstance(section, dict) and set(section) == {'kernel_cmdline', 'modules', 'kmi_symbols'}, 'invalid debug packaging')
    added = argument_rows(section['kernel_cmdline'], set())
    names = {w.split('=', 1)[0] for w in cmdline_arguments(recipe, set())}
    require(not names & {w.split('=', 1)[0] for w in added}, 'a debug argument repeats a production argument')
    return added


def debug_kmi_symbols(recipe):
    """The kernel symbols the debug aids make modules import. The compiler or the kernel's own macros
    insert them, so no importer list can bind them."""
    section = recipe['debug']['kmi_symbols']
    require(isinstance(section, dict) and set(section) == {'symbols', 'reason'} and isinstance(section['reason'], str)
            and section['reason'].strip() and isinstance(section['symbols'], list) and section['symbols']
            and len(set(section['symbols'])) == len(section['symbols'])
            and all(isinstance(n, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in section['symbols']),
            'invalid debug KMI symbols')
    return section['symbols']


def debug_recipe(recipe, denied):
    """The recipe with the debug modules placed: in their partitions, and in the second-stage load list and the
    recovery list right after the anchor module. denied: the module keys of the deny list, which the
    debug modules must be on."""
    debug_arguments(recipe)
    section = recipe['debug']['modules']
    require(isinstance(section, dict) and set(section) == {'names', 'after', 'partitions', 'reason'}
            and isinstance(section['reason'], str) and section['reason'].strip() and isinstance(section['names'], list)
            and section['names'] and len(set(section['names'])) == len(section['names'])
            and isinstance(section['partitions'], list) and set(section['partitions']) <= set(recipe['partitions']),
            'invalid debug modules')
    placed = set().union(*map(set, recipe['partitions'].values()))
    require(all(module_key(n) in denied and n not in placed for n in section['names']),
            'a debug module is not on the deny list or is already placed')
    result = {**recipe, 'partitions': {k: list(v) for k, v in recipe['partitions'].items()},
              'load_lists': {k: list(v) for k, v in recipe['load_lists'].items()}}
    for partition in section['partitions']:
        result['partitions'][partition] = sorted(result['partitions'][partition] + section['names'])
    for key in ('vendor_boot/modules.load.recovery', 'vendor_dlkm/modules.load'):
        names = result['load_lists'][key]
        require(section['after'] in names, 'the debug modules follow %s, which %s lacks' % (section['after'], key))
        at = names.index(section['after']) + 1
        result['load_lists'][key] = names[:at] + section['names'] + names[at:]
    return result


def board_config(recipe, arguments):
    """BoardConfigKernel.mk: the partitions' modules and load order, the kernel command line and the blocklists."""
    lines = ['# Generated source-built kernel.',
             'FP6_KERNEL_PATH := device/fairphone/FP6-kernel',
             'BOARD_INCLUDE_DTB_IN_BOOTIMG := true',
             'BOARD_PREBUILT_DTBIMAGE_DIR := $(FP6_KERNEL_PATH)/dtbs',
             'BOARD_PREBUILT_DTBOIMAGE := $(FP6_KERNEL_PATH)/dtbo.img',
             'BOARD_DO_NOT_STRIP_VENDOR_MODULES := true',
             'BOARD_DO_NOT_STRIP_VENDOR_RAMDISK_MODULES := true']
    for partition, names in recipe['partitions'].items():
        lines.append('BOARD_' + VARIABLES[partition] + '_KERNEL_MODULES := ' +
                     ' '.join('$(FP6_KERNEL_PATH)/modules/' + n for n in names))
    for key, names in recipe['load_lists'].items():
        partition, filename = key.split('/')
        label = 'VENDOR_RAMDISK_RECOVERY' if filename == 'modules.load.recovery' else VARIABLES[partition]
        lines.append('BOARD_' + label + '_KERNEL_MODULES_LOAD := ' + ' '.join(names))
    if arguments:
        lines.append('BOARD_KERNEL_CMDLINE += ' + ' '.join(arguments))
    for key in recipe['blocklists']:
        partition, filename = key.split('/')
        lines.append('BOARD_' + VARIABLES[partition] + '_KERNEL_MODULES_BLOCKLIST_FILE := $(FP6_KERNEL_PATH)/' +
                     partition + '-' + filename)
    return '\n'.join(lines) + '\n'


def render_package(candidate, staged, image, dtbs, dtbo, recipe, arguments):
    """The kernel set in the layout of device_fairphone_FP6-kernels."""
    candidate.mkdir()
    shutil.copytree(staged, candidate / 'modules')
    shutil.copyfile(image, candidate / 'Image')
    (candidate / 'dtbs').mkdir()
    for name, data in sorted(dtbs.items()):
        (candidate / 'dtbs' / name).write_bytes(data)
    (candidate / 'dtbo.img').write_bytes(dtbo)
    for key, contents in recipe['blocklists'].items():
        partition, filename = key.split('/')
        (candidate / (partition + '-' + filename)).write_text(contents)
    (candidate / 'BoardConfigKernel.mk').write_text(board_config(recipe, arguments))
    (candidate / 'device-kernel.mk').write_text(
        '# Generated source-built kernel.\nPRODUCT_COPY_FILES += device/fairphone/FP6-kernel/Image:kernel\n')
