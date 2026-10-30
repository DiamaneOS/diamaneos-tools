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


def cmdline_arguments(recipe, forbidden):
    """The kernel command line words of the recipe, checked against the forbidden ones."""
    words = recipe.get('kernel_cmdline', [])
    require(isinstance(words, list), 'invalid kernel command line')
    found = []
    for row in words:
        require(isinstance(row, dict) and set(row) == {'argument', 'reason'} and isinstance(row['argument'], str)
                and ARGUMENT.fullmatch(row['argument']) and isinstance(row['reason'], str) and row['reason'].strip(),
                'invalid kernel command line argument')
        require(row['argument'].split('=', 1)[0] not in forbidden, 'forbidden kernel argument: ' + row['argument'])
        found.append(row['argument'])
    require(len(set(found)) == len(found), 'duplicate kernel command line argument')
    return found


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
