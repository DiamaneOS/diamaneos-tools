"""The vendor recipe and ELF selection of a build with the open data stack (DIAMANEOS_DATAD=true).

The stock data stack is nicmd with its libraries. The open one is the daemon `datad` and two libraries
that take the place of `libnicm.so` and `libnicm_dsi.so`, built from source with the Android tree. So the
selection that is rendered into the vendor tree is the stock one without the twelve stock files, and with the
two edges of `libdsi_netctrl.so` to the replaced libraries turned into edges of kind `source-module`. The
renderer accepts a source module only when its reviewed list names it (SOURCE_MODULE_DEPENDENCIES in
vendor_product.py, which names `libnicm` and `libnicm_dsi`).

The transformation fails when the stock files are not the ones it was written for: a file to drop is missing,
another file needs a dropped one, or `libdsi_netctrl.so` no longer links both libraries. A stock update that
changes the closure of nicmd is then looked at before the next build. With the switch off the recipe and the
selection are the device's own files, unchanged.
"""
import copy

from . import safe_json
from .vendor import VendorError

# The twelve stock files of nicmd and its libraries that the open stack replaces or makes useless.
DROPPED = (
    'vendor/bin/nicmd',
    'vendor/etc/init/nicmd.rc',
    'vendor/etc/data/nicm_config.xml',
    'vendor/lib64/libnicm.so',
    'vendor/lib64/libnicm_dsi.so',
    'vendor/lib64/libnicm_internal.so',
    'vendor/lib64/libnicm_modemproxy.so',
    'vendor/lib64/libnicm_netlink.so',
    'vendor/lib64/libnicm_recovery.so',
    'vendor/lib64/libnicm_utils.so',
    'vendor/lib64/librmnetctl.so',
    'vendor/lib64/libshsc.so',
)
# The two that the closed libdsi_netctrl.so links and that the tree builds from source instead.
REPLACED = ('vendor/lib64/libnicm.so', 'vendor/lib64/libnicm_dsi.so')
CONSUMER = 'vendor/lib64/libdsi_netctrl.so'
ROOT = 'vendor/bin/nicmd'
# Files that are not ELF objects, so the ELF selection does not list them.
NOT_ELF = ('vendor/etc/init/nicmd.rc', 'vendor/etc/data/nicm_config.xml')
# The kind the renderer gives a library that is loaded with dlopen and installed with its consumer.
RUNTIME_EDGE = 'selected-stock-runtime'
SOURCE_EDGE = 'source-module'


def transform(recipe, selection):
    """The (recipe, selection) of the open data stack from the pair of the stock one. The arguments are not changed."""
    recipe = copy.deepcopy(recipe)
    selection = copy.deepcopy(selection)
    dropped = set(DROPPED)
    for name, rows, want in (('recipe', recipe['files'], dropped),
                             ('selection', selection['files'], dropped - set(NOT_ELF))):
        missing = want - {r['path'] for r in rows}
        if missing:
            raise VendorError(f'the {name} lacks {sorted(missing)}: the stock files changed')
    if ROOT not in selection['roots']:
        raise VendorError(f'{ROOT} is not a root of the selection')
    if any(s['path'] in dropped or s.get('target') in dropped for s in recipe.get('symlinks', [])):
        raise VendorError('a symlink names a dropped file')
    # nothing outside the dropped set may need a dropped file, except the consumer and the two it links
    for row in recipe['files']:
        if row['path'] in dropped:
            continue
        needs = (set(row.get('dependencies', [])) | {r['path'] for r in row.get('runtime_dependencies', [])}) & dropped
        allowed = set(REPLACED) if row['path'] == CONSUMER else set()
        if needs - allowed:
            raise VendorError(f"{row['path']} needs {sorted(needs - allowed)}")
        if row['path'] == CONSUMER and needs != allowed:
            raise VendorError(f'{CONSUMER} no longer links both libraries: {sorted(needs)}')
    # the recipe: drop the files, and the notices only they used
    recipe['files'] = [r for r in recipe['files'] if r['path'] not in dropped]
    for row in recipe['files']:
        if row['path'] == CONSUMER:
            row['dependencies'] = [d for d in row['dependencies'] if d not in REPLACED]
    used = {n for r in recipe['files'] + recipe.get('symlinks', []) for n in r['notices']}
    recipe['notices'] = [n for n in recipe['notices'] if n['sha256'] in used]
    # the selection
    selection['files'] = [r for r in selection['files'] if r['path'] not in dropped]
    if any(r['path'] in NOT_ELF for r in selection['files']):
        raise VendorError('a file that was not an ELF object is in the selection')
    selection['roots'] = [r for r in selection['roots'] if r != ROOT]
    if not selection['roots']:
        raise VendorError('no root left')
    edges, converted = [], set()
    for edge in selection['edges']:
        if edge['consumer'] in dropped:
            continue
        if edge.get('provider') in dropped:
            if edge['consumer'] != CONSUMER or edge['provider'] not in REPLACED:
                raise VendorError(f"{edge['consumer']} links {edge['provider']}")
            edges.append({'consumer': CONSUMER, 'needed': edge['needed'], 'kind': SOURCE_EDGE})
            converted.add(edge['provider'])
            continue
        edges.append(edge)
    if converted != set(REPLACED):
        raise VendorError(f'{CONSUMER} links {sorted(converted)}, not both libraries')
    selection['edges'] = edges
    if any(item.get('consumer') in dropped for item in selection.get('firmware_inputs', [])):
        raise VendorError('a firmware input belongs to a dropped file')
    return recipe, selection


def unreachable(selection):
    """Paths in the selection that no root reaches (the renderer leaves them out of the image)."""
    kept, pending = set(), list(selection['roots'])
    while pending:
        path = pending.pop()
        if path in kept:
            continue
        kept.add(path)
        pending.extend(e['provider'] for e in selection['edges']
                       if e['consumer'] == path and e['kind'] in ('selected-stock', RUNTIME_EDGE))
    return sorted({r['path'] for r in selection['files']} - kept)


def pair(device, datad=False):
    """The recipe and the ELF selection the vendor tree of this device is made from.

    Without ``datad`` they are the device's files as they are. With it they are the same files after
    `transform`."""
    recipe = safe_json.load_json(device.path('vendor_files'))
    selection = safe_json.load_json(device.path('vendor_elf'))
    return transform(recipe, selection) if datad else (recipe, selection)
