"""Materialize the reviewed FP6 native Android integration from authenticated files."""
import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from . import components, vendor_files
from .vendor import VendorError, encoded

ROOT = Path(__file__).resolve().parents[2]
SOURCE_INTERFACES = {
    'libdrm',
    'libkeymaster_messages',
    'android.hardware.gatekeeper-V1-ndk',
    'android.hardware.graphics.allocator-V1-ndk',
    'android.hardware.graphics.composer3-V2-ndk',
    'android.hardware.keymaster@3.0',
    'android.hardware.keymaster@4.0',
    'android.hardware.keymaster@4.1',
    'android.hardware.security.keymint-V3-ndk',
    'android.hardware.security.rkp-V3-ndk',
    'android.hardware.security.secureclock-V1-ndk',
    'android.hardware.security.sharedsecret-V1-ndk',
}
ACTIVATION={
 'android.hardware.gatekeeper-service-qti':('android.hardware.gatekeeper-service-qti.rc',None),
 'android.hardware.security.keymint-service-qti':('android.hardware.security.keymint-service-qti.rc','android.hardware.security.keymint-service-qti.xml'),
 'vendor.qti.hardware.display.allocator-service':('vendor.qti.hardware.display.allocator-service.rc','vendor.qti.hardware.display.allocator-service.xml'),
 'vendor.qti.hardware.display.composer-service':('vendor.qti.hardware.display.composer-service.rc','vendor.qti.hardware.display.composer-service.xml'),
 'vendor.qti.hardware.display.color-service':('vendor.qti.hardware.display.color-service.rc',None),
 'vendor.qti.hardware.memtrack-service':('memtrack_qti.rc','memtrack_qti.xml'),
 'vendor.qti.hardware.qseecom@1.0-service':('vendor.qti.hardware.qseecom@1.0-service.rc','vendor.qti.hardware.qseecom@1.0-service.xml'),
 'qseecomd':('qseecomd.rc',None),
 'thermal-engine-v2':('init_thermal-engine-v2.rc',None),
 'vendor.qti.hardware.perf2-hal-service':('vendor.qti.hardware.perf2-hal-service.rc','vendor.qti.hardware.perf2.xml'),
}


def module(path):
    return 'fp6_stock_' + path.replace('/', '_').removesuffix('.so')


def blueprint(kind, properties):
    def value(item):
        if isinstance(item, dict):
            return '{ ' + ', '.join(k + ': ' + value(v) for k, v in item.items()) + ', }'
        return json.dumps(item)
    return kind + ' {\n' + ''.join('    ' + k + ': ' + value(v) + ',\n'
                                  for k, v in properties.items()) + '}\n\n'


RUNTIME_EDGE = 'selected-stock-runtime'


def reachable(selection):
    """Keep explicit static/dynamic roots and their transitive ELF and dlopen providers."""
    paths = {r['path'] for r in selection['files']}
    roots = selection['roots']
    if not roots or len(roots) != len(set(roots)) or not set(roots) <= paths:
        raise VendorError('invalid native runtime roots')
    kept, pending = set(), list(roots)
    while pending:
        path = pending.pop()
        if path in kept:
            continue
        kept.add(path)
        pending.extend(e['provider'] for e in selection['edges']
                       if e['consumer'] == path and e['kind'] in ('selected-stock', RUNTIME_EDGE))
    if not kept <= paths:
        raise VendorError('runtime dependency has no selected provider')
    return kept


def render(recipe, selection, notice_kind):
    """Bind every ELF and dependency to the selected recipe before rendering."""
    if not re.fullmatch(r'[A-Za-z0-9_]+', notice_kind):
        raise VendorError('invalid Android notice classification')
    rows = {r['path']: r for r in recipe['files']}
    elfs = {r['path']: r for r in selection['files']}
    if len(elfs) != len(selection['files']) or not elfs:
        raise VendorError('empty or duplicate ELF selection')
    for path, row in elfs.items():
        if path not in rows or any(row[k] != rows[path][k] for k in ('sha256', 'bytes')):
            raise VendorError('ELF selection identity differs from stock recipe')
        if not path.startswith(('vendor/lib64/', 'vendor/bin/')):
            raise VendorError('unsupported native install partition')
    dependencies = {path: [] for path in elfs}
    # dlopen providers are installed with their consumer but never linked.
    required = {path: [] for path in elfs}
    edge_keys = set()
    for edge in selection['edges']:
        if edge['consumer'] not in elfs:
            raise VendorError('undeclared ELF consumer')
        key = (edge['consumer'], edge['needed'])
        if key in edge_keys:
            raise VendorError('duplicate or ambiguous ELF dependency')
        edge_keys.add(key)
        if edge['kind'] == 'selected-stock':
            provider = edge['provider']
            if provider not in elfs or provider not in rows[edge['consumer']]['dependencies']:
                raise VendorError('undeclared ELF dependency')
            stem = Path(provider).name.removesuffix('.so')
            dep = stem if stem in SOURCE_INTERFACES else module(provider)
        elif edge['kind'] == RUNTIME_EDGE:
            provider = edge['provider']
            declared = {r['path']: r['soname'] for r in rows[edge['consumer']].get('runtime_dependencies', [])}
            if (provider not in elfs or declared.get(provider) != edge['needed']
                    or Path(provider).name != edge['needed']):
                raise VendorError('undeclared runtime dependency')
            stem = Path(provider).name.removesuffix('.so')
            required[edge['consumer']].append(stem if stem in SOURCE_INTERFACES else module(provider))
            continue
        elif edge['kind'] == 'platform-or-vndk34':
            if not re.fullmatch(r'[A-Za-z0-9_.@+-]+\.so', edge['needed']):
                raise VendorError('invalid platform dependency')
            if not edge['export_lists'] or any(p not in {
                    '/etc/llndk.libraries.34.txt', '/etc/vndkcore.libraries.34.txt',
                    '/etc/vndksp.libraries.34.txt', '/etc/vndkprivate.libraries.34.txt'}
                    for p in edge['export_lists']):
                raise VendorError('unreviewed platform export namespace')
            # The export lists record the stock VNDK 34 interface a blob was
            # built against. The vendor is an Android 17 vendor without a VNDK
            # version, so the dependency is the current vendor variant.
            dep = edge['needed'].removesuffix('.so')
        else:
            raise VendorError('unresolved ELF dependency')
        dependencies[edge['consumer']].append(dep)
    for path in elfs:
        declared = set(rows[path]['dependencies'])
        observed = {e['provider'] for e in selection['edges']
                    if e['consumer'] == path and e['kind'] == 'selected-stock'}
        if declared != observed:
            raise VendorError('incomplete selected ELF dependency edges')
        declared_runtime = {r['path'] for r in rows[path].get('runtime_dependencies', [])}
        observed_runtime = {e['provider'] for e in selection['edges']
                            if e['consumer'] == path and e['kind'] == RUNTIME_EDGE}
        if declared_runtime != observed_runtime:
            raise VendorError('incomplete selected runtime dependency edges')
    firmware = selection.get('firmware_inputs', [])
    firmware_paths = {r['path'] for r in firmware}
    if len(firmware_paths) != len(firmware):
        raise VendorError('duplicate firmware input')
    for item in firmware:
        path = item['path']
        if (not path.startswith('vendor/firmware/') or path not in rows
                or rows[path]['component_id'] != 'firmware-trusted-boot'
                or any(rows[path][k] != item[k] for k in ('sha256', 'bytes'))
                or not item.get('consumer') or not item.get('source')):
            raise VendorError('missing or inconsistent firmware dependency')
    text = '// Generated from the selected stock recipe; do not edit.\n'
    text += blueprint('package', {'default_applicable_licenses': ['fp6_selected_stock_notices']})
    text += blueprint('license', {'name': 'fp6_selected_stock_notices',
                                 'license_kinds': [notice_kind], 'license_text': ['NOTICE.xml']})
    kept = reachable(selection)
    names, consumed = [], set(elfs) - kept
    for path in sorted(elfs):
        if path not in kept:
            continue
        stem = Path(path).name.removesuffix('.so')
        if stem in SOURCE_INTERFACES:
            consumed.add(path)
            continue
        name = module(path)
        names.append(name)
        consumed.add(path)
        library = path.endswith('.so')
        base = Path('vendor/lib64' if library else 'vendor/bin')
        relative = Path(path).parent.relative_to(base).as_posix()
        props = {'name': name, 'vendor': True, 'compile_multilib': '64',
                 'srcs': ['files/' + path], 'stem': stem, 'strip': {'none': True},
                 'shared_libs': sorted(set(dependencies[path])), 'system_shared_libs': []}
        if required[path]:
            props['required'] = sorted(set(required[path]))
        if relative != '.':
            props['relative_install_path'] = relative
        if not library:
            if stem not in ACTIVATION:
                raise VendorError('native executable lacks reviewed activation')
            rc, fragment = ACTIVATION[stem]
            for key, directory, filename in [('init_rc', 'init', rc),
                                              ('vintf_fragments', 'vintf/manifest', fragment)]:
                if filename:
                    config = 'vendor/etc/' + directory + '/' + filename
                    if config not in rows:
                        raise VendorError('missing service activation file')
                    props[key] = ['files/' + config]
                    consumed.add(config)
        text += blueprint('cc_prebuilt_library_shared' if library else 'cc_prebuilt_binary', props)
    for link in recipe.get('symlinks', []):
        destination = vendor_files.link_destination(link)
        if destination not in elfs:
            raise VendorError('alias has no native provider')
        name = module(link['path']) + '_alias'
        names.append(name)
        text += blueprint('install_symlink', dict(name=name, vendor=True,
                          installed_location=link['path'].removeprefix('vendor/'),
                          symlink_target=link['target'], required=[module(destination)]))
    make = '# Generated from the authenticated selection.\nPRODUCT_PACKAGES += ' + ' '.join(names)
    make += '\nPRODUCT_VENDOR_PROPERTIES += ro.hardware.egl=adreno ro.hardware.vulkan=adreno\n'
    for path in sorted(set(rows) - consumed):
        if path.startswith('vendor/etc/lm/'):
            # No learning plugin is installed or enabled in this composition.
            continue
        if (path not in firmware_paths and not path.startswith('vendor/etc/')) or path.startswith(('vendor/etc/init/', 'vendor/etc/vintf/')):
            raise VendorError('unclassified Android installation input')
        make += 'PRODUCT_COPY_FILES += vendor/fairphone/FP6/files/' + path + ':$(TARGET_COPY_OUT_VENDOR)/' + path.removeprefix('vendor/') + '\n'
    return {'Android.bp': text.encode(), 'device-vendor.mk': make.encode(),
            'BoardConfigVendor.mk': b'# Selected stock vendor patch level.\nVENDOR_SECURITY_PATCH := 2026-08-05\n',
            'modules.json': encoded(names)}


def performance_config(data):
    """Pinned correction of optional startup gates; preserve every other byte."""
    if hashlib.sha256(data).hexdigest() != 'bc2c287db99b1d184ee281429cd8703e8b8976fe9e0a951378d454cfb69e60db':
        raise VendorError('performance configuration differs from reviewed input')
    import xml.etree.ElementTree as ET
    disabled = {'vendor.debug.enable.lm', 'vendor.debug.enable.memperfd', 'ro.vendor.perf.enable.prekill'}
    def replace(match):
        token = match[0]
        if token.startswith(b'<!--'):
            return token
        node = ET.fromstring(token)
        if node.attrib.get('Name') in disabled:
            return token.replace(b'Value="true"', b'Value="false"')
        return token
    result = re.sub(rb'<!--.*?-->|<Prop\s[^>]*?/>', replace, data, flags=re.S)
    if hashlib.sha256(result).hexdigest() != '960b5b4088af3601279297e9169e82168dd931e5b901701924bc9db47651873b':
        raise VendorError('derived performance configuration differs from reviewed result')
    return result


def generate(recipe, selection, inputs, output, *, notice_kind, **policy):
    closure = vendor_files.selection(recipe, public=False, **policy)
    rendered = render(recipe, selection, notice_kind)
    provenance = {'operation': 'fp6-native-product-generation',
                  'scope': 'private-bringup',
                  'recipe_sha256': hashlib.sha256(encoded(recipe)).hexdigest(),
                  'elf_selection_sha256': hashlib.sha256(encoded(selection)).hexdigest(),
                  'renderer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'notice_kind': notice_kind, 'native_or_device_accepted': False}
    kept = reachable(selection)
    provenance['uninstalled_optional_libraries'] = sorted(
        r['path'] for r in selection['files'] if r['path'] not in kept)
    provenance['source_interface_replacements'] = sorted(
        r['path'] for r in selection['files']
        if r['path'] in kept and Path(r['path']).name.removesuffix('.so') in SOURCE_INTERFACES)
    identity = hashlib.sha256(encoded(provenance)).hexdigest()
    output = Path(output)
    if output.is_symlink():
        raise VendorError('output root cannot be a symlink')
    output.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = os.open(output / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        import stat
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise VendorError('invalid product generation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        generations = output / 'generations'
        if generations.is_symlink():
            raise VendorError('generation root cannot be a symlink')
        generations.mkdir(exist_ok=True, mode=0o750)
        with tempfile.TemporaryDirectory(prefix='.product-', dir=generations) as temporary:
            tree = Path(temporary) / 'tree'
            tree.mkdir()
            for item in recipe.get('symlinks', []):
                vendor_files.verify_symlink(inputs, item)
            for item in recipe['files']:
                vendor_files.copy_verified(inputs, item, tree / 'files' / item['path'])
            for item in recipe['notices']:
                vendor_files.copy_verified(inputs, item, tree / 'notices' / item['sha256'])
            if len(recipe['notices']) != 1:
                raise VendorError('FP6 product requires its reviewed stock notice archive')
            with gzip.GzipFile(fileobj=io.BytesIO((tree / 'notices' / recipe['notices'][0]['sha256']).read_bytes())) as stream:
                notice = stream.read(64 * 1024**2 + 1)
            if len(notice) > 64 * 1024**2:
                raise VendorError('expanded notice exceeds size limit')
            rendered['NOTICE.xml'] = notice
            config = tree / 'files/vendor/etc/perf/perfconfigstore.xml'
            original = config.read_bytes()
            derived = performance_config(original)
            config.write_bytes(derived)
            provenance['derived_files'] = [{'path': 'vendor/etc/perf/perfconfigstore.xml',
                'source_sha256': hashlib.sha256(original).hexdigest(),
                'sha256': hashlib.sha256(derived).hexdigest(),
                'reason': 'Disable optional learning, memory plugin and prekill startup gates'}]
            rendered.update({'provenance.json': encoded(provenance), 'recipe.json': encoded(recipe),
                             'component-closure.json': encoded(closure)})
            for name, content in rendered.items():
                (tree / name).write_bytes(content)
            records = {}
            for p in tree.rglob('*'):
                if p.is_file():
                    p.chmod(0o640)
                    records[str(p.relative_to(tree))] = {'bytes': p.stat().st_size,
                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            # Closure records describe authenticated source bytes. Derivations
            # explicitly bind changed output; neither claims runtime acceptance.
            vendor_files.verify_tree(tree, records)
            final = generations / identity
            if final.exists() or final.is_symlink():
                vendor_files.verify_tree(final, records)
            else:
                tree.rename(final)
        inventories = output / 'inventories'
        if inventories.is_symlink():
            raise VendorError('inventory directory cannot be a symlink')
        inventories.mkdir(exist_ok=True, mode=0o750)
        inventory = inventories / (identity + '.json')
        inventory_bytes = encoded(records)
        if inventory.exists() or inventory.is_symlink():
            if inventory.is_symlink() or not inventory.is_file() or inventory.read_bytes() != inventory_bytes:
                raise VendorError('existing product inventory differs')
        else:
            with tempfile.TemporaryDirectory(prefix='.inventory-', dir=inventories) as temporary:
                staged = Path(temporary) / 'inventory.json'
                staged.write_bytes(inventory_bytes)
                staged.rename(inventory)
        target = 'generations/' + identity
        current = output / 'current'
        if not current.is_symlink() or os.readlink(current) != target:
            with tempfile.TemporaryDirectory(prefix='.publish-', dir=output) as temporary:
                link = Path(temporary) / 'current'
                link.symlink_to(target)
                os.replace(link, current)
    return dict(operation='fp6-native-product-generation', status='PASS',
                generation_sha256=identity, inventory_sha256=hashlib.sha256(inventory_bytes).hexdigest(), scope='private-bringup', native_or_device_accepted=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe', type=Path, default=ROOT / 'config/fp6-minimal/vendor-files.json')
    parser.add_argument('--selection', type=Path, default=ROOT / 'config/fp6-minimal/vendor-elf.json')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notice-kind', required=True, help='reviewed Android notice classification')
    args = parser.parse_args(argv)
    try:
        model_data = (ROOT / 'config/components.json').read_bytes()
        source_data = (ROOT / 'config/fp6-sources.json').read_bytes()
        result = generate(components.load_json(args.recipe), components.load_json(args.selection),
            args.inputs, args.output, notice_kind=args.notice_kind,
            model=components.loads(model_data), sources=components.loads(source_data),
            environment=components.load_json(ROOT / 'config/build-environment.json'),
            model_sha256=hashlib.sha256(model_data).hexdigest(),
            source_sha256=hashlib.sha256(source_data).hexdigest())
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, EOFError, components.ComponentError):
        print('ERROR: unable to authenticate or publish native product inputs')
        return 2
