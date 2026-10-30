"""Prepare and build the pinned FP6 kernel, modules and device tree.

The kernel is DiamaneOS/kernel_common-6.18 with the FP6 commits
(DiamaneOS/kernel_common-6.18), built with the pinned Clang and Rust toolchain.
The Qualcomm drivers are the vendor tree DiamaneOS/kernel_qcom-6.18, built as
external modules against it. Both repositories, the toolchain and the base
device tree are pinned in config/kernel-sources-fp6.json and
config/fp6-kernel-dt.json. The result is the kernel set in the layout of
device_fairphone_FP6-kernels, which the Android build reads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from . import kernel_build, kernel_config, kernel_dt, kernel_kbuild, kernel_package, process
from .kernel_util import (KernelError, SHA1, SPARSE_PATTERN, call, check_url, checkout_project, commit_time, locked,
                          require, verify_project)
from .vendor_extract import sha, relative
from .vendor import ROOT, load_json, VendorError, encoded

SOURCE_PLAN = ROOT / 'config/kernel-sources-fp6.json'
BUILD_PLAN = ROOT / 'config/fp6-kernel-build.json'
DT_PLAN = ROOT / 'config/fp6-kernel-dt.json'
PACKAGING = ROOT / 'config/fp6-kernel-packaging.json'
VENDOR_FILES = ROOT / 'config/fp6-minimal/vendor-files.json'
POLICY = ROOT / 'config/kernel-policy-fp6.json'
VENDOR_POLICY = ROOT / 'config/kernel-vendor-policy-fp6.json'
CONFIG_PROFILES = ('production', 'development', 'debug')
MODULE_NAME = re.compile(r'[A-Za-z0-9_.-]+\.ko')
DTB_NAME = re.compile(r'[A-Za-z0-9_-]+\.dtb')
SYMBOL_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.]{0,127}')
PROJECT_NAME = re.compile(r'[a-z][a-z0-9-]{0,30}')
# Files the tools keep in the workspace next to the sources.
OWN_FILES = ('.preparation.lock', 'preparation.json', 'runs', 'current')
DEBUG_README = """# Debug build of the FP6 kernel set

This set was built with the debug profile. The Image has the debug aids on (tracing, kprobes and uprobes, KFENCE, UBSAN
reports, lock and stall detectors, mountable debugfs, no lockdown, dmesg not restricted), the vendor_boot command line
adds loglevel=8, log_buf_len, panic_on_rcu_stall, panic=5, the download mode arguments and reboot=panic_warm, and the
debug modules the production deny list drops are in the module lists (not the clock debug module, whose device
tree node stays disabled).

It is for test builds that reproduce a fault with the aids on. Never publish it: kernel publish refuses it.
"""
BUILD_TOOLS = ('modinfo', 'modprobe', 'nm', 'readelf', 'openssl', 'perl', 'rsync', 'make', 'git', 'tar', 'python3', 'bash')


def module_key(name):
    """Module names as the loader compares them: '-' and '_' are the same."""
    return name.removesuffix('.ko').replace('-', '_')


def projects(plan, dt_plan):
    """Every pinned checkout of the workspace: name, role, path, URL, revision."""
    rows = []
    for role in ('vendor', 'common'):
        entry = plan[role]
        rows.append(dict(entry, name=role, role=role))
    for entry in plan['toolchain']:
        rows.append(dict(entry, role='toolchain'))
    base = dt_plan['base']
    rows.append({'name': 'base-dt', 'role': 'device-tree', 'path': 'base-dt', 'url': base['repository'],
                 'revision': base['revision']})
    return rows


def validate_plan(plan):
    """The source plan: two repositories and the toolchain, each at an exact commit."""
    require(isinstance(plan, dict) and set(plan) == {'schema_version', 'vendor', 'common', 'toolchain'}
            and plan['schema_version'] == 2, 'invalid kernel source plan')
    rows = [plan['vendor'], plan['common']] + (plan['toolchain'] if isinstance(plan['toolchain'], list) else [])
    require(isinstance(plan['toolchain'], list) and plan['toolchain'], 'the source plan lists no toolchain')
    names, paths = [], []
    for entry in rows:
        allowed = {'path', 'url', 'revision'} | ({'name', 'sparse'} if entry in plan['toolchain'] else set())
        require(isinstance(entry, dict) and {'path', 'url', 'revision'} <= set(entry) <= allowed,
                'invalid source plan entry')
        relative(entry['path'])
        check_url(entry['url'])
        require(isinstance(entry['revision'], str) and SHA1.fullmatch(entry['revision']), 'invalid revision: ' + entry['path'])
        if 'name' in entry:
            require(isinstance(entry['name'], str) and PROJECT_NAME.fullmatch(entry['name']), 'invalid project name')
            names.append(entry['name'])
        if 'sparse' in entry:
            require(isinstance(entry['sparse'], list) and entry['sparse'] and
                    all(isinstance(p, str) and SPARSE_PATTERN.fullmatch(p) and '..' not in p for p in entry['sparse']),
                    'invalid sparse patterns: ' + entry['path'])
        paths.append(entry['path'])
    paths.append('base-dt')
    require(len(names) == len(set(names)) and not {'vendor', 'common', 'base-dt'} & set(names), 'duplicate project names')
    require(len(paths) == len(set(paths)) and not any(a != b and b.startswith(a + '/') for a in paths for b in paths),
            'duplicate or nested project paths')
    return plan


def validate_dt_plan(dt):
    require(isinstance(dt, dict) and set(dt) == {'schema_version', 'scope', 'base', 'fixups', 'disabled_nodes', 'forbidden_properties'}
            and dt['schema_version'] == 1 and isinstance(dt['forbidden_properties'], list)
            and all(isinstance(n, str) and re.fullmatch(r'[a-zA-Z0-9,._+#-]+', n) for n in dt['forbidden_properties']),
            'invalid device tree plan')
    rows = dt['disabled_nodes']
    require(isinstance(rows, list) and rows and all(
        isinstance(r, dict) and set(r) == {'compatible', 'reason'} and isinstance(r['compatible'], str)
        and re.fullmatch(r'[a-zA-Z0-9,._+-]+', r['compatible']) and isinstance(r['reason'], str) and r['reason'].strip()
        for r in rows) and len({r['compatible'] for r in rows}) == len(rows), 'invalid disabled device tree nodes')
    base = dt['base']
    require(isinstance(base, dict) and set(base) == {'repository', 'revision', 'files', 'overlay_count'}
            and SHA1.fullmatch(str(base['revision'])) and type(base['overlay_count']) is int
            and isinstance(base['files'], dict) and set(base['files']) == {'dtbs/fp6.dtb', 'dtbo.img'}
            and all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in base['files'].values()),
            'invalid base device tree')
    check_url(base['repository'])
    return dt


def base_digest(dt_plan):
    """The digest of what preparation fetches for the device tree. The fix-ups edit the files after."""
    return hashlib.sha256(json.dumps(dt_plan['base'], sort_keys=True).encode()).hexdigest()


def configuration():
    """The validated source and device tree plans."""
    return validate_plan(load_json(SOURCE_PLAN)), validate_dt_plan(load_json(DT_PLAN))


def resolve_overrides(rows, overrides):
    """name -> where to fetch from. An override replaces the URL of a fetch only, never the pin."""
    names = {r['name'] for r in rows}
    require(set(overrides) <= names, 'unknown source to override: ' + ', '.join(sorted(set(overrides) - names)))
    for name, where in overrides.items():
        location = Path(where)
        require(location.is_absolute() and ((location / '.git').exists() or (location / 'HEAD').is_file()),
                'not an absolute path to a Git repository: ' + where)
    return dict(overrides)


def core_header_copies(vendor_root, common_root):
    """Core headers a vendor module set carries a copy of (core-headers.json: copy -> path in the
    common kernel) must stay byte-identical to the common kernel, or module and core disagree on
    the structures in them."""
    checked = []
    for listing in sorted((vendor_root / 'modules').rglob('core-headers.json')):
        rows = load_json(listing)
        require(isinstance(rows, dict) and rows, 'invalid core header list: ' + str(listing))
        for copy, original in sorted(rows.items()):
            mine, theirs = listing.parent / relative(copy), common_root / relative(original)
            require(mine.is_file() and theirs.is_file(), 'core header copy or original missing: ' + copy)
            require(mine.read_bytes() == theirs.read_bytes(),
                    'core header copy differs from the common kernel: ' + str(mine.relative_to(vendor_root)))
            checked.append(str(mine.relative_to(vendor_root)))
    return checked


def prepare(root, overrides=None):
    plan, dt_plan = configuration()
    rows = projects(plan, dt_plan)
    where = resolve_overrides(rows, overrides or {})
    with locked(root):
        top = {r['path'].split('/')[0] for r in rows}
        stray = sorted(p.name for p in root.iterdir() if p.name not in OWN_FILES and p.name not in top)
        require(not stray, 'the kernel workspace holds other files (' + ', '.join(stray[:5]) +
                '). Prepare an empty directory or move them aside')
        for row in rows:
            dest = root / row['path']
            for parent in [dest, *dest.parents]:
                if parent == root:
                    break
                require(not parent.is_symlink(), 'source directory is a symlink: ' + row['path'])
            checkout_project(dest, row, where.get(row['name']))
        for row in rows:
            verify_project(root / row['path'], row)
        copies = core_header_copies(root / 'vendor', root / 'common')
        kernel_dt.base_files(root / 'base-dt', dt_plan['base'])
        result = {'status': 'PASS', 'operation': 'kernel-source-preparation',
                  'source_plan_sha256': sha(SOURCE_PLAN), 'device_tree_base_sha256': base_digest(dt_plan),
                  'projects': {r['name']: {'path': r['path'], 'url': r['url'], 'revision': r['revision']} for r in rows},
                  'fetched_from_overrides': sorted(where), 'core_header_copies_checked': len(copies),
                  'device_commands_executed': 0}
        (root / 'preparation.json').write_bytes(encoded(result))
        return result


def prepared(root, plan, dt_plan):
    """The preparation record, checked against the pins and the checkouts."""
    preparation = load_json(root / 'preparation.json')
    require(preparation.get('status') == 'PASS' and preparation.get('source_plan_sha256') == sha(SOURCE_PLAN)
            and preparation.get('device_tree_base_sha256') == base_digest(dt_plan),
            'the kernel sources were prepared from other pins. Run "diamaneos kernel prepare" again')
    rows = projects(plan, dt_plan)
    require(set(preparation.get('projects', {})) == {r['name'] for r in rows},
            'the preparation record is incomplete. Run "diamaneos kernel prepare" again')
    for row in rows:
        require(preparation['projects'][row['name']]['revision'] == row['revision'],
                'the preparation is not at the pin: ' + row['name'])
        verify_project(root / row['path'], row)
    return preparation


# Recipe checks

def denied_modules(recipe):
    """The modules FP6 never ships, each with its reason. The partition and load lists may be
    regenerated. This deny list survives that, and a denied module in any list fails the build.
    Whether a remaining module still needs a denied one is checked on the built set
    (module-interfaces.json). A group may name the modules the 6.18 sources do not build (absent)."""
    denied = {}
    for group in recipe.get('denied_modules', []):
        require(isinstance(group, dict) and set(group) in ({'modules', 'reason'}, {'modules', 'absent', 'reason'}) and
                isinstance(group['reason'], str) and group['reason'].strip() and
                isinstance(group['modules'], list) and group['modules'], 'invalid denied module group')
        absent = group.get('absent', [])
        require(isinstance(absent, list) and set(absent) <= set(group['modules']), 'invalid absent denied modules')
        for name in group['modules']:
            require(isinstance(name, str) and MODULE_NAME.fullmatch(name), 'invalid denied module name')
            require(module_key(name) not in {module_key(n) for n in denied}, 'duplicate denied module: ' + name)
            denied[name] = group['reason']
    keys = {module_key(n) for n in denied}
    listed = set().union(*map(set, recipe['partitions'].values()), *map(set, recipe['load_lists'].values()))
    back = sorted(n for n in listed if module_key(n) in keys)
    require(not back, 'denied module in the packaging recipe: ' + ', '.join(back))
    return denied


def check_denied_built(recipe, built):
    """The deny list names what the build makes. A name the build does not make was renamed or dropped,
    and a renamed copy would slip past the list, unless the recipe marks the name absent. A name marked
    absent that the build makes needs its mark removed. Returns the absent names."""
    denied = {module_key(n): n for n in denied_modules(recipe)}
    absent = {module_key(n): n for g in recipe.get('denied_modules', []) for n in g.get('absent', [])}
    built = {module_key(n) for n in built}
    unmarked = sorted(denied[k] for k in set(denied) - built - set(absent))
    require(not unmarked, 'denied module no longer built, review the entry: ' + ', '.join(unmarked))
    marked = sorted(absent[k] for k in set(absent) & built)
    require(not marked, 'denied module marked absent but built, remove the mark: ' + ', '.join(marked))
    return sorted(absent.values())


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


def symbol_rules(recipe, key, label, extra=()):
    rules = recipe.get(key, [])
    require(isinstance(rules, list), 'invalid ' + label + ' list')
    for rule in rules:
        require(isinstance(rule, dict) and set(rule) <= {'symbol', 'reason', *extra} and {'symbol', 'reason'} <= set(rule)
                and isinstance(rule['symbol'], str) and SYMBOL_NAME.fullmatch(rule['symbol'])
                and isinstance(rule['reason'], str) and rule['reason'].strip(), 'invalid ' + label)
        if 'pending' in rule:
            require(isinstance(rule['pending'], str) and rule['pending'].strip(), 'invalid pending reason: ' + rule['symbol'])
    return rules


def forbidden_symbols(recipe):
    """Kernel symbols that must not exist in the built Image, with the reason."""
    return [rule['symbol'] for rule in symbol_rules(recipe, 'forbidden_symbols', 'forbidden symbol')]


def required_symbols(recipe):
    """Kernel symbols the built Image must have, with the reason: each marks a DiamaneOS change that a
    rebase or a merge could drop without notice. A rule with a pending reason names a change the 6.18
    sources do not carry. The development profile reports it, the production profile counts it as
    missing, and publishing a run refuses it. When the symbol is there, the build asks for the reason
    to be removed."""
    rules = symbol_rules(recipe, 'required_symbols', 'required symbol', extra=('pending',))
    return [rule['symbol'] for rule in rules if 'pending' not in rule], \
        {rule['symbol']: rule['pending'] for rule in rules if 'pending' in rule}


def undefined_symbols(nm_output):
    """Names `nm -u` lists as undefined."""
    return {line.split()[-1] for line in nm_output.splitlines() if line.split()[:1] == ['U']}


def check_module_imports(modules, allowlist, undefined):
    """Fail unless each allowlisted symbol is imported by exactly its modules."""
    for symbol, allowed in allowlist.items():
        importers = sorted(name for name, path in modules.items() if symbol in undefined(path))
        require(importers == allowed, 'unexpected importers of ' + symbol + ': ' + (', '.join(importers) or 'none'))


def kmi_symbols(common):
    """The symbols of the GKI symbol lists the common tree's build lists (gki/aarch64/symbols)."""
    named = set(re.findall(r'"gki/aarch64/symbols/([a-z_0-9]+)"', (common / 'BUILD.bazel').read_text())) | {'base'}
    symbols = set()
    for name in sorted(named):
        path = common / 'gki/aarch64/symbols' / name
        require(path.is_file(), 'KMI symbol list missing: ' + name)
        symbols |= {line.strip() for line in path.read_text().splitlines()
                    if line.strip() and not line.strip().startswith(('#', '['))}
    require(len(symbols) > 1000, 'the KMI symbol lists look empty')
    return symbols


def kmi_exceptions(recipe, profile='production'):
    """Kernel symbols modules import although no GKI symbol list keeps them: symbol -> importers. A group names
    its symbols, its importers and the reason. Together they are the vendor symbol list the Image keeps
    exporting. The debug profile adds the symbols its debug aids make any module import (importers None)."""
    groups = recipe.get('kmi_exceptions', [])
    require(isinstance(groups, list), 'invalid KMI exception list')
    found = {}
    for group in groups:
        require(isinstance(group, dict) and set(group) == {'symbols', 'modules', 'reason'} and isinstance(group['reason'], str)
                and group['reason'].strip() and isinstance(group['symbols'], list) and group['symbols']
                and isinstance(group['modules'], list) and group['modules'] and len(set(group['modules'])) == len(group['modules'])
                and all(isinstance(m, str) and MODULE_NAME.fullmatch(m) for m in group['modules'])
                and all(isinstance(n, str) and SYMBOL_NAME.fullmatch(n) for n in group['symbols']), 'invalid KMI exception')
        for symbol in group['symbols']:
            require(symbol not in found, 'KMI exception listed twice: ' + symbol)
            found[symbol] = sorted(group['modules'])
    if profile == 'debug':
        for symbol in kernel_package.debug_kmi_symbols(recipe):
            require(symbol not in found, 'KMI exception listed twice: ' + symbol)
            found[symbol] = None
    return found


def kmi_whitelist(common, exceptions):
    """The text of the symbol list the kernel build trims its exports to: the GKI symbol lists and the
    exceptions, one symbol per line."""
    return ''.join(symbol + '\n' for symbol in sorted(kmi_symbols(common) | set(exceptions)))


def check_kmi(imports, exports, kmi, exceptions):
    """Every kernel symbol a Qualcomm or device module imports must be in the GKI symbol lists or be a
    reviewed exception that names its importers, and an exception must still be needed.
    imports: module -> undefined symbols. exceptions: symbol -> importers (None: any module)."""
    used = set().union(*imports.values()) & exports
    outside = used - kmi
    missing = sorted(outside - set(exceptions))
    require(not missing, 'modules import kernel symbols no KMI symbol list keeps: ' + ', '.join(missing[:10]))
    stale = sorted(set(exceptions) - outside)
    require(not stale, 'KMI exception no longer needed: ' + ', '.join(stale))
    wrong = {s: sorted(m for m, u in imports.items() if s in u) for s, bound in exceptions.items() if bound is not None}
    wrong = sorted('%s is imported by %s, not %s' % (s, ', '.join(found) or 'no module', ', '.join(exceptions[s]))
                   for s, found in wrong.items() if found != exceptions[s])
    require(not wrong, 'KMI exception importers differ: ' + ', '.join(wrong[:5]))
    return sorted(outside), len(used)


def shipped_dtbs(recipe):
    names = recipe.get('dtbs')
    require(isinstance(names, list) and names and len(set(names)) == len(names) and
            all(isinstance(n, str) and DTB_NAME.fullmatch(n) for n in names), 'invalid device tree list')
    return names


def map_symbols(system_map, strip_suffix=False):
    present = {line.split()[2] for line in system_map.splitlines() if len(line.split()) >= 3}
    # ThinLTO renames a promoted static symbol to <name>.llvm.<hash>.
    return {s.split('.llvm.')[0] for s in present} if strip_suffix else present


def check_forbidden_symbols(system_map, symbols):
    found = sorted(set(symbols) & map_symbols(system_map))
    require(not found, 'forbidden kernel symbol present: ' + ', '.join(found))


def check_required_symbols(system_map, symbols, pending=None, profile='development'):
    """Fail on a missing required symbol. A pending symbol that is there fails too, so that its reason
    cannot stay. The production profile counts a pending symbol as missing. Returns the pending
    symbols still missing."""
    present = map_symbols(system_map, True)
    pending = pending or {}
    carried = sorted(set(pending) & present)
    require(not carried, 'the kernel has a symbol marked pending, remove its pending reason: ' + ', '.join(carried))
    wanted = set(symbols) | (set(pending) if profile == 'production' else set())
    missing = sorted(wanted - present)
    require(not missing, 'required kernel symbol missing: ' + ', '.join(missing))
    return sorted(pending)


def module_metadata(path):
    raw = process.run(['modinfo', '-0', str(path)], 60)
    require(raw['transport'] == 'ok', 'module metadata unavailable')
    values = []
    for row in raw['stdout'].split(b'\0'):
        if not row:
            continue
        match = re.fullmatch(rb'([a-zA-Z0-9_]+)(?:=|: *)(.*)', row, re.DOTALL)
        require(match, 'unrecognized module metadata')
        if match[1] != b'filename':
            values.append((match[1], match[2]))
    return sorted(values)


def shipped_vendor_files():
    """The paths of the vendor image files the product ships."""
    return {row['path'] for row in load_json(VENDOR_FILES)['files']}


def kernel_release(vmlinux):
    """The release a vmlinux reports, from its Linux version banner."""
    data = Path(vmlinux).read_bytes()
    found = set(re.findall(rb'Linux version ([0-9][!-~]{0,127}) \(', data))
    require(len(found) == 1, 'missing/ambiguous kernel release in ' + Path(vmlinux).name)
    return found.pop().decode('ascii')


def vendor_config(device_config, vendor_root, extras=()):
    """The vendor view of the configuration, as text kernel_config reads: the device kernel build's
    configuration with the values of the vendor defconfigs and the extra configurations on top, which
    is what the vendor modules are built with (vendor_autoconf.h). A vendor n does not switch off
    what the kernel configuration has on, as the generated header defines nothing for it."""
    values = kernel_config.parse_config(device_config)
    set_by = {}
    layers = []
    for path in sorted((vendor_root / 'modules').glob('*/defconfig')):
        for key, value in kernel_kbuild.read_config(str(path)).items():
            require(set_by.setdefault(key, value) == value, 'vendor defconfigs set %s differently' % key)
        layers.append(path)
    for path in [*layers, *extras]:
        for key, value in kernel_kbuild.read_config(str(path)).items():
            if value == 'n' and values.get(key) not in (None, 'n'):
                continue
            values[key] = value
    return ''.join('# %s is not set\n' % k if v == 'n' else '%s=%s\n' % (k, v) for k, v in sorted(values.items())).encode()


def build(root, jobs, timeout, profile='production'):
    missing = [name for name in BUILD_TOOLS if shutil.which(name) is None]
    require(not missing, 'kernel verification tools missing from PATH: ' + ', '.join(missing))
    require(profile in CONFIG_PROFILES, 'unknown kernel configuration profile')
    plan, dt_plan = configuration()
    recipe = load_json(PACKAGING)
    # Reads every recipe before the workspace is touched, so a broken recipe stops here.
    denied_modules(recipe), import_allowlist(recipe), forbidden_symbols(recipe), required_symbols(recipe), shipped_dtbs(recipe)
    kmi_exceptions(recipe), kmi_exceptions(recipe, 'debug')
    kernel_package.check_required_modules(recipe, shipped_vendor_files())
    kernel_package.cmdline_arguments(recipe, forbidden_arguments())
    kernel_package.debug_recipe(recipe, {module_key(n) for n in denied_modules(recipe)})
    from .product_inputs import tools_identity
    with locked(root):
        preparation = prepared(root, plan, dt_plan)
        common = root / 'common'
        revisions = {n: preparation['projects'][n]['revision'] for n in ('vendor', 'common')}
        run = root / 'runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
        run.mkdir(parents=True)
        result = {'status': 'RUNNING', 'operation': 'kernel-build-and-package', 'commands': [],
                  'device_commands_executed': 0, 'kernel_accepted': False, 'config_profile': profile,
                  'preparation_sha256': sha(root / 'preparation.json'),
                  'repository': plan['vendor']['url'], 'source_commit': revisions['vendor'],
                  'common_repository': plan['common']['url'], 'common_commit': revisions['common'],
                  'linux_version': linux_version(common), 'tools': tools_identity(),
                  'recipe_sha256': sha(Path(__file__)), 'packaging_recipe_sha256': sha(PACKAGING),
                  'build_plan_sha256': sha(BUILD_PLAN), 'device_tree_plan_sha256': sha(DT_PLAN)}

        def save():
            temp = run / 'result.tmp'
            temp.write_bytes(encoded(result))
            temp.replace(run / 'result.json')

        def phase(name):
            result['phase'] = name
            save()
            print(name, flush=True)
        save()
        try:
            execute(Run(root, run, plan, dt_plan, load_json(BUILD_PLAN), recipe, profile, jobs, timeout, preparation,
                        result, phase))
            save()
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=root) as temp:
                link = Path(temp) / 'current'
                link.symlink_to(os.path.relpath(run / 'candidate', root))
                os.replace(link, root / 'current')
        except (Exception, KeyboardInterrupt) as exc:
            result.update(status='FAIL', error=str(exc))
            save()
            raise
        return result


def forbidden_arguments():
    return set(load_json(ROOT / 'config/fp6-build.json')['kernel_arguments']['forbidden'])


def linux_version(common):
    """VERSION.PATCHLEVEL.SUBLEVEL of the common kernel."""
    values = dict(re.findall(r'^(VERSION|PATCHLEVEL|SUBLEVEL) = ([0-9]+)$',
                             (common / 'Makefile').read_text(errors='replace'), re.M))
    require(set(values) == {'VERSION', 'PATCHLEVEL', 'SUBLEVEL'}, 'Makefile names no kernel version')
    return '.'.join(values[k] for k in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL'))


class Run:
    """One build run: its directories, the toolchain and what the stages hand to each other."""

    def __init__(self, root, run, plan, dt_plan, build_plan, recipe, profile, jobs, timeout, preparation, result, phase):
        self.root, self.run, self.plan, self.dt_plan, self.build_plan = root, run, plan, dt_plan, build_plan
        self.base_recipe, self.profile, self.jobs, self.timeout = recipe, profile, jobs, timeout
        self.preparation, self.result, self.phase = preparation, result, phase
        self.vendor, self.common = root / 'vendor', root / 'common'
        self.core, self.device, self.stage = run / 'core', run / 'device', run / 'vendor-src'
        self.logs, self.out = run / 'logs', run / 'out'
        self.denied = denied_modules(recipe)
        # The debug profile ships the debug modules the deny list drops. The deny list itself is checked on the base recipe.
        self.recipe = kernel_package.debug_recipe(recipe, {module_key(n) for n in self.denied}) if profile == 'debug' else recipe
        self.exceptions = kmi_exceptions(recipe, profile)
        self.llvm = root / build_plan['toolchain']['clang'] / 'bin'
        self.env = self.make = None
        self.outputs, self.built, self.selected = {}, {}, {}
        self.vendor_pool, self.device_pool, self.core_pool = {}, {}, {}

    def record(self, name, started):
        log = self.logs / (name + '.log')
        self.result['commands'].append({'name': name, 'log_sha256': sha(log) if log.is_file() else None,
                                        'seconds': round(time.monotonic() - started), 'status': 'PASS'})

    def make_kernel(self, name, out, goals):
        self.phase(name)
        started = time.monotonic()
        call([*self.make, '-C', self.common, 'O=' + str(out), '-j%d' % self.jobs, *goals], cwd=self.common,
             env=self.env, timeout=self.timeout, log=self.logs / (name + '.log'))
        self.record(name, started)

    def start(self):
        """The toolchain and the host tools. No build directory may end up in the Image or a module."""
        self.logs.mkdir()
        maps = [self.common, self.root / 'toolchain', self.core, self.device, self.stage, self.out]
        epoch = commit_time(self.common, self.preparation['projects']['common']['revision'])
        self.env, self.make = kernel_build.toolchain(self.root, self.build_plan, self.run / 'tools', epoch, maps)
        self.result['host_tools'] = kernel_build.host_tools(self.run / 'tools', self.build_plan['toolchain']['system_libraries'])

    def core_build(self):
        """The GKI core build: gki_defconfig, the DiamaneOS hardening, the profile and the export trimming."""
        plan = self.build_plan['core']
        # The Image exports the symbols of the GKI lists and of the reviewed exceptions, and what its modules use.
        (self.run / 'kmi-whitelist').write_text(kmi_whitelist(self.common, self.exceptions))
        trim = self.run / 'ksyms.config'
        trim.write_text('CONFIG_TRIM_UNUSED_KSYMS=y\nCONFIG_UNUSED_KSYMS_WHITELIST="../kmi-whitelist"\n')
        fragments = [self.vendor / f for f in plan['fragments'] + [plan['profile_fragments'][self.profile]]]
        fragments += [ROOT / f for f in plan['tool_fragments'][self.profile]] + [trim]
        kernel_build.merge_config([self.common / plan['defconfig'], *fragments], self.core / '.config')
        self.make_kernel('core-configure', self.core, ['olddefconfig'])
        config = (self.core / '.config').read_bytes()
        unmet = kernel_build.unmet_requests([p.read_text() for p in fragments], config)
        (self.run / 'fragment-check.json').write_bytes(encoded({'unmet': unmet}))
        require(not unmet, 'configuration requests the build does not honour: ' + ', '.join(list(unmet)[:8]))
        checked = kernel_config.check(config, load_json(POLICY), self.profile)
        (self.run / 'kernel-config.json').write_bytes(encoded(checked))
        require(checked['status'] == 'PASS', self.profile + ' kernel configuration regressed')
        self.make_kernel('core-build', self.core, plan['goals'])

    def device_build(self):
        """The device kernel build: the same tree and configuration plus the drivers the GKI build lacks."""
        plan = self.build_plan['device']
        kernel_build.merge_config([self.core / '.config', *[self.vendor / f for f in plan['fragments']]], self.device / '.config')
        self.make_kernel('device-configure', self.device, ['olddefconfig'])
        changed = kernel_build.device_config_differences((self.core / '.config').read_bytes(),
                                                         (self.device / '.config').read_bytes(), plan['config_symbols'])
        (self.run / 'device-config.json').write_bytes(encoded({'changed': changed, 'reason': plan['reason']}))
        self.make_kernel('device-build', self.device, plan['goals'])
        require(kernel_build.vmlinux_exports((self.core / kernel_build.SYMVERS).read_text()) ==
                kernel_build.vmlinux_exports((self.device / kernel_build.SYMVERS).read_text()),
                'the device kernel build exports other kernel symbols than the GKI build')
        # The vendor policy applies to the device kernel configuration with the vendor defconfigs on top.
        extras = [ROOT / step['extra_defconfig'] for step in self.build_plan['vendor']['steps'] if step.get('extra_defconfig')]
        checked = kernel_config.check(vendor_config((self.device / '.config').read_bytes(), self.vendor, extras),
                                      load_json(VENDOR_POLICY), self.profile)
        (self.run / 'vendor-role-kernel-config.json').write_bytes(encoded(checked))
        require(checked['status'] == 'PASS', 'vendor kernel configuration regressed')

    def vendor_build(self):
        """The vendor module packages, built from a clean copy of the pinned modules tree."""
        plan = self.build_plan['vendor']
        kernel_build.stage_vendor_modules(self.vendor, self.preparation['projects']['vendor']['revision'], self.stage)
        self.out.mkdir()
        overrides = set(plan['kernel_overrides']['symbols'])
        ctx = {'env': self.env, 'make': self.make, 'common': self.common, 'jobs': self.jobs, 'timeout': self.timeout,
               'logs': self.logs, 'runs': self.run, 'out': self.out, 'tools_root': ROOT, 'device': self.device,
               'kernel_values': kernel_kbuild.read_config(str(self.device / '.config')), 'overrides': overrides,
               'conflicts': {}}
        for step in plan['steps']:
            self.phase(step['name'])
            started = time.monotonic()
            self.built[step['name']] = kernel_build.STEPS[step['kind']](ctx, step, self.stage, self.outputs)
            self.record(step['name'], started)
        stale = sorted(overrides - set(ctx['conflicts']))
        require(not stale, 'vendor override no longer differs from the kernel configuration: ' + ', '.join(stale))
        (self.run / 'vendor-overrides.json').write_bytes(encoded(ctx['conflicts']))
        warnings = [w for name in ('core-build', 'device-build', *self.built) for w in
                    kernel_build.visibility_warnings((self.logs / (name + '.log')).read_bytes())]
        require(not warnings, 'struct declared inside a parameter list (-Wvisibility): ' + ', '.join(warnings[:5]))
        (self.run / 'modules-built.json').write_bytes(encoded({k: v for k, v in sorted(self.built.items())}))

    def module_set(self):
        """Every listed module from its build, stripped and signed with the Image's key."""
        recipe = self.recipe
        self.vendor_pool = kernel_build.collect(self.outputs.values())
        for path in sorted(self.core.rglob('*.ko')):
            self.core_pool.setdefault(path.name, path)
        for module in self.build_plan['device']['modules']:
            path = self.device / module
            require(path.is_file(), 'device kernel module missing: ' + module)
            require(path.name not in self.vendor_pool and path.name not in self.device_pool, 'two builds make the module ' + path.name)
            self.device_pool[path.name] = path
        absent = check_denied_built(self.base_recipe, {**self.vendor_pool, **self.device_pool, **self.core_pool})
        self.result['denied_modules_not_built'] = absent
        self.result['denied_module_count'] = len(self.denied) - len(absent)
        wanted = set().union(*map(set, recipe['partitions'].values()))
        self.result['required_modules_checked'] = kernel_package.check_required_modules(recipe, shipped_vendor_files())
        self.selected = kernel_package.select(wanted, {**self.vendor_pool, **self.device_pool}, self.core_pool)
        kernel_package.check_placement(recipe, lambda n: dependencies(self.selected[n]) if n in self.selected else [])
        for name in ('certs/signing_key.pem', 'certs/signing_key.x509', 'scripts/sign-file'):
            require((self.core / name).is_file(), 'kernel signing input missing: ' + name)
        config = (self.core / '.config').read_bytes()
        require(re.search(rb'^CONFIG_MODULE_SIG_FORCE=y$', config, re.M) and
                re.search(rb'^CONFIG_MODULE_SIG_HASH="sha256"$', config, re.M), 'kernel does not enforce sha256 module signatures')
        self.phase('package')
        self.staged = self.run / 'staged-modules'
        kernel_package.stage_modules(self.staged, self.selected, self.llvm / 'llvm-strip',
                                     (self.core / 'scripts/sign-file', self.core / 'certs/signing_key.pem',
                                      self.core / 'certs/signing_key.x509', 'sha256'),
                                     module_metadata, self.common, self.root / self.build_plan['toolchain']['host_libraries'] / 'lib64')

    def device_tree(self):
        """The pinned base with the declared fix-ups."""
        self.phase('device-tree')
        files = kernel_dt.base_files(self.root / 'base-dt', self.dt_plan['base'])
        fixed, changes = kernel_dt.apply_fixups(files['dtbs/fp6.dtb'], self.dt_plan['fixups'], self.dt_plan['forbidden_properties'])
        count = kernel_dt.overlay_count(files['dtbo.img'])
        require(count == self.dt_plan['base']['overlay_count'] == self.recipe['dtbo_count'], 'overlay count changed')
        enabled = kernel_dt.check_disabled_nodes(fixed, kernel_dt.overlays(files['dtbo.img']), self.dt_plan['disabled_nodes'])
        require(not enabled, 'nodes of drivers that do not ship are enabled: ' + ', '.join(enabled[:3]) +
                (' and %d more' % (len(enabled) - 3) if len(enabled) > 3 else ''))
        self.result['disabled_node_rules_checked'] = len(self.dt_plan['disabled_nodes'])
        (self.run / 'device-tree-fixups.json').write_bytes(encoded({'base_sha256': self.dt_plan['base']['files'], 'fixups': changes}))
        (self.run / 'fp6.dtb').write_bytes(fixed)
        call([self.root / self.build_plan['toolchain']['kernel_build_tools'] / 'bin/dtc', '-q', '-I', 'dtb', '-O', 'dts',
              '-o', self.run / 'fp6.dts', self.run / 'fp6.dtb'], cwd=self.run, env=self.env)
        self.dtbo, self.overlays, self.tree = files['dtbo.img'], count, fixed

    def package(self):
        dtbs = shipped_dtbs(self.recipe)
        arguments = kernel_package.cmdline_arguments(self.recipe, forbidden_arguments())
        if self.profile == 'debug':
            arguments += kernel_package.debug_arguments(self.recipe)
        self.candidate = self.run / 'candidate'
        kernel_package.render_package(self.candidate, self.staged, self.core / 'arch/arm64/boot/Image', {dtbs[0]: self.tree},
                                      self.dtbo, self.recipe, arguments)
        if self.profile == 'debug':
            (self.candidate / 'README.md').write_text(DEBUG_README)

    def checks(self):
        """The packaged modules against the Image: certificate, symbol versions, namespaces, imports and symbols."""
        self.phase('module-interfaces')
        recipe, result, core, candidate = self.recipe, self.result, self.core, self.candidate
        vmlinux = core / 'vmlinux'
        release = kernel_release(vmlinux)
        require(release == (core / 'include/config/kernel.release').read_text().strip()
                and release.endswith('-g' + self.preparation['projects']['common']['revision'][:12]),
                'Image release does not name the pinned common kernel: ' + release)
        result['kernel_release'] = release
        from .kernel_interfaces import verify_built
        tables = [core / kernel_build.SYMVERS, self.device / kernel_build.SYMVERS] + [p / kernel_build.SYMVERS for p in self.outputs.values()]
        verify_built(self.common, self.common, self.run, {n: candidate / 'modules' / n for n in self.selected},
                     [t for t in tables if t.is_file()], vmlinux, core / kernel_build.SYMVERS, module_metadata, call, require,
                     release=release)
        nm = self.llvm / 'llvm-nm'
        undefined = {n: undefined_symbols(call([nm, '-u', candidate / 'modules' / n], cwd=self.common)) for n in self.selected}
        check_module_imports({n: candidate / 'modules' / n for n in self.selected}, import_allowlist(recipe),
                             lambda path: undefined[path.name])
        exports = {row.split('\t')[1] for row in kernel_build.vmlinux_exports((core / kernel_build.SYMVERS).read_text())}
        # What the kernel builds export: the Image and the modules of the common tree (the GKI modules and the device kernel's four).
        offered = kernel_build.exported_symbols([core / kernel_build.SYMVERS, self.device / kernel_build.SYMVERS])
        # The Image keeps the exports it was trimmed to, and every module of ours finds its imports among them.
        kept = image_exports(call([nm, '--defined-only', vmlinux], cwd=self.common))
        lost = sorted(exports & set().union(*undefined.values()) - kept)
        require(not lost, 'modules import kernel symbols the Image no longer exports: ' + ', '.join(lost[:10]))
        # The Qualcomm modules and the device kernel's four are held to the symbol lists. The GKI modules are not.
        # The four use each other's exports in the tree, which the build trims by.
        device_made = kernel_build.exported_symbols([self.device / kernel_build.SYMVERS],
                                                    [Path(m).stem for m in self.build_plan['device']['modules']])
        held = {n: u for n, u in undefined.items() if n in self.vendor_pool}
        held.update({n: u - device_made for n, u in undefined.items() if n in self.device_pool})
        outside, used = check_kmi(held, offered, kmi_symbols(self.common), self.exceptions)
        result['kmi'] = {'modules_checked': len(held), 'kernel_symbols_imported': used, 'outside_symbol_lists': outside,
                         'image_exports': len(kept), 'symbol_table_exports': len(exports)}
        system_map = (core / 'System.map').read_text(errors='replace')
        check_forbidden_symbols(system_map, forbidden_symbols(recipe))
        required, pending = required_symbols(recipe)
        result['pending_required_symbols'] = check_required_symbols(system_map, required, pending, self.profile)
        for path in candidate.rglob('*'):
            if path.is_file():
                path.chmod(0o640)
        inventory = [{'path': p.relative_to(candidate).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                     for p in sorted(candidate.rglob('*')) if p.is_file()]
        (self.run / 'artifacts.json').write_bytes(encoded(inventory))
        for name, path in (('core.config', core / '.config'), ('device.config', self.device / '.config'),
                           ('core.symvers', core / kernel_build.SYMVERS), ('System.map', core / 'System.map')):
            shutil.copyfile(path, self.run / name)
        result.update(status='PASS', debug_build=self.profile == 'debug', module_count=len(self.selected), dtb_count=len(shipped_dtbs(recipe)), dtbo_count=self.overlays,
                      import_rules_checked=len(import_allowlist(recipe)), forbidden_symbols_checked=len(forbidden_symbols(recipe)),
                      required_symbols_checked=len(required), inventory_sha256=sha(self.run / 'artifacts.json'),
                      interfaces_sha256=sha(self.run / 'module-interfaces.json'),
                      scope='Kernel build and packaging. Installed-image, runtime and production qualification are separate.')


def execute(run):
    """The stages of one build, in order."""
    run.start()
    run.core_build()
    run.device_build()
    run.vendor_build()
    run.module_set()
    run.device_tree()
    run.package()
    run.checks()


def image_exports(nm_output):
    """The symbols an Image exports, from the __ksymtab_ entries of its symbol table."""
    return {line.split()[-1][len('__ksymtab_'):] for line in nm_output.splitlines()
            if line.split()[-1:] and line.split()[-1].startswith('__ksymtab_')}


def dependencies(path):
    values = dict(module_metadata(path))
    return [d for d in values.get(b'depends', b'').decode().split(',') if d]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'build'])
    parser.add_argument('--workspace', type=Path, required=True,
                        help='kernel workspace: the pinned sources, the toolchain and the build runs')
    parser.add_argument('--jobs', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=7200, help='maximum seconds per compilation command')
    parser.add_argument('--config-profile', choices=CONFIG_PROFILES, default='production',
                        help='build: kernel configuration policy to check. The development profile checks only the '
                             'baseline. Both build the configuration of the vendor tree\'s production profile. The debug '
                             'profile builds its bring-up configuration with the debug aids on, adds the debug '
                             'modules and command line arguments, and is never published')
    parser.add_argument('--source', action='append', default=[], metavar='NAME=PATH',
                        help='prepare: fetch the pinned commit of NAME (vendor, common, a toolchain part or base-dt) '
                             'from this local Git repository instead of its URL. The commit stays the pin')
    args = parser.parse_args(argv)
    try:
        require(1 <= args.jobs <= 64 and 60 <= args.timeout <= 21600, 'invalid build resource limits')
        require(args.operation == 'build' or args.config_profile == 'production', 'config profile is a build option')
        require(args.operation == 'prepare' or not args.source, 'source overrides are a prepare option')
        overrides = dict(s.split('=', 1) for s in args.source if '=' in s)
        require(len(overrides) == len(args.source), 'use --source NAME=PATH')
        with process.interrupt_on_termination():
            result = (prepare(args.workspace.absolute(), overrides) if args.operation == 'prepare' else
                      build(args.workspace.absolute(), args.jobs, args.timeout, args.config_profile))
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print('ERROR: kernel ' + args.operation + ' interrupted', file=sys.stderr)
        return 130
    except (KernelError, kernel_dt.DeviceTreeError, VendorError, OSError, ValueError, KeyError) as exc:
        print('ERROR: kernel ' + args.operation + ' failed: ' + str(exc), file=sys.stderr)
        return 2
