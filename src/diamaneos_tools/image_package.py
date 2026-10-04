"""Package a built FP6 test image set: one coherent set from target-files.

The build's target-files archive is the image authority: its IMAGES/ were made
together by the build (the AVB descriptors match them), and super.img is built
from the same archive. Nothing is repacked. The wipe images are generated
deterministically, and build.json records what the set was made from.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import zipfile

from . import product_inputs
from . import build_workspace as bw
from .build_workspace import Action, BuildStepError

SUMS = 'SHA256SUMS'
# The parts of config/fp6-build.json packaging depends on.
PACKAGE_CONFIG = ('product', 'images', 'wipe', 'slot')
RECORD = 'build.json'
TARGET_FILES_COPY = 'target-files.zip'
# The resolved manifest (repo manifest -r) of the source the set was built from.
MANIFEST_COPY = 'resolved-manifest.xml'


def image_names(config: dict) -> list[str]:
    images = config['images']
    return images['bootloader'] + images['logical'] + images['extra']


def build_id(outputs: dict) -> str:
    day = datetime.datetime.fromtimestamp(outputs['build_datetime'], datetime.timezone.utc).strftime('%Y%m%d')
    return f'{day}-{outputs["variant"]}-{outputs["build_identity"][:10]}'


def write_sums(directory: Path) -> str:
    lines = [f'{bw.sha_file(p)}  {p.name}' for p in sorted(directory.iterdir())
             if p.is_file() and p.name != SUMS]
    (directory / SUMS).write_text('\n'.join(lines) + '\n')
    return bw.sha_file(directory / SUMS)


def read_sums(directory: Path) -> dict:
    result = {}
    for line in (directory / SUMS).read_text().splitlines():
        digest, _, name = line.partition('  ')
        if not name or '/' in name or name in result:
            raise BuildStepError('malformed SHA256SUMS in ' + str(directory))
        result[name] = digest
    return result


def check_sums(directory: Path) -> bool:
    try:
        sums = read_sums(directory)
    except (OSError, BuildStepError):
        return False
    files = {p.name for p in directory.iterdir() if p.is_file() and p.name != SUMS}
    return files == set(sums) and all(bw.sha_file(directory / n) == d for n, d in sums.items())


def same_build(directory: Path, android_outputs: dict) -> bool:
    """An existing image set may be reused only if it is intact and was made
    from exactly this build's target-files and identity."""
    if not check_sums(directory):
        return False
    try:
        record = json.loads((directory / RECORD).read_bytes())
    except (OSError, ValueError):
        return False
    return (record.get('target_files', {}).get('sha256') == android_outputs['target_files_sha256']
            and record.get('build_identity') == android_outputs['build_identity'])


def zeros(path: Path, size: int) -> None:
    with path.open('wb') as stream:
        stream.truncate(size)


def frp_image(path: Path, size: int) -> None:
    """Stock frp_for_factory.img: zeros, last byte 1 (OEM unlocking allowed)."""
    data = bytearray(size)
    data[-1] = 1
    path.write_bytes(bytes(data))


def stock_partition_size(factory_zip: Path, label: str) -> int | None:
    """A partition's size in the factory package's partition table
    (images/rawprogram*.xml), or None when the package does not list it."""
    import xml.etree.ElementTree as ET
    sizes = set()
    with zipfile.ZipFile(factory_zip) as archive:
        for name in archive.namelist():
            if PurePosixPath(name).parent.name == 'images' and PurePosixPath(name).name.startswith('rawprogram') \
                    and name.endswith('.xml'):
                for entry in ET.fromstring(archive.read(name)):
                    if entry.get('label') == label and entry.get('num_partition_sectors'):
                        sizes.add(int(entry.get('num_partition_sectors')) * int(entry.get('SECTOR_SIZE_IN_BYTES', '4096')))
    if len(sizes) > 1:
        raise BuildStepError(f'the stock partition table gives {label} more than one size')
    return sizes.pop() if sizes else None


def fstab_entries(text: str) -> dict:
    entries = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 5 and not fields[0].startswith('#'):
            entries[fields[1]] = {'type': fields[2], 'flags': fields[4].split(',')}
    return entries


def check_wipe_against_fstab(fstab: str, wipe: dict) -> None:
    """The wipe images must suit the device's own mount table."""
    entries = fstab_entries(fstab)
    data, metadata = entries.get('/data'), entries.get('/metadata')
    if data is None or 'formattable' not in data['flags']:
        raise BuildStepError('the device fstab does not let first boot format /data; the zeroed userdata image needs it')
    if metadata is None or metadata['type'] != wipe['images']['metadata']['kind']:
        raise BuildStepError('the metadata image type does not match the device fstab')


def plan(ctx):
    from .build_steps import StepPlan, find_target_files  # noqa: F401  (StepPlan type)
    ws, config = ctx.workspace, ctx.config
    android = ws.passed('android')
    code = bw.sha_file(Path(__file__))
    inputs = None if android is None else {
        'android': android['outputs'], 'build_config': bw.digest({k: config.get(k) for k in PACKAGE_CONFIG}),
        'code': code}
    names = image_names(config)
    wipe = config['wipe']
    state = {}

    def paths():
        out = android['outputs']
        identifier = build_id(out)
        final = ws.images / identifier
        return out, identifier, final, ws.images / (identifier + '.partial')

    def prepare():
        out, identifier, final, partial = paths()
        target_files = ws.root / out['target_files']
        if bw.sha_file(target_files) != out['target_files_sha256']:
            raise BuildStepError('the target-files archive changed after the build; run "diamaneos build android" again')
        state.update(target_files=target_files, identifier=identifier, final=final, partial=partial,
                     reuse=final.is_dir() and same_build(final, out))
        if final.exists() and not state['reuse']:
            raise BuildStepError(f'{final} exists but is not this build (its record or SHA256SUMS differ); '
                                 'move it aside')
        if partial.exists():
            shutil.rmtree(partial)
        if not state['reuse']:
            partial.mkdir(parents=True)

    def export():
        if state['reuse']:
            return
        with zipfile.ZipFile(state['target_files']) as archive:
            members = set(archive.namelist())
            for name in names:
                member = f'IMAGES/{name}.img'
                if member not in members:
                    raise BuildStepError('target-files lacks ' + member)
                with archive.open(member) as source, (state['partial'] / f'{name}.img').open('wb') as target:
                    shutil.copyfileobj(source, target, 16 * 1024 * 1024)
            fstab = archive.read(wipe['fstab']).decode('utf-8', 'replace')
        check_wipe_against_fstab(fstab, wipe)

    def super_image():
        if state['reuse']:
            return
        runner = bw.Runner(ctx.allow_network, ctx.echo, ws.work / 'tmp')
        runner.run(Action('Build super.img from the same target-files',
                          argv=[ctx.host_bin / 'build_super_image', state['target_files'],
                                state['partial'] / 'super.img'],
                          env={'PATH': os.pathsep.join([str(ctx.host_bin), os.environ.get('PATH', '')])}),
                   ctx.cache['log'])

    def wipe_images():
        if state['reuse']:
            return
        images = wipe['images']
        for name, image in images.items():
            if image['kind'] == 'zeros':
                zeros(state['partial'] / image['image'], image['bytes'])
        vendor = ws.passed('vendor')
        factory_name = vendor['outputs'].get('factory_zip') if vendor else None
        factory = Path(factory_name) if factory_name else None
        checked = {}
        for name, image in images.items():
            if image.get('partition_label') and factory and factory.is_file():
                size = stock_partition_size(factory, image['partition_label'])
                if size != image['bytes']:
                    raise BuildStepError(f'the {name} image is {image["bytes"]} bytes but the stock partition '
                                         f'table says {size}')
                checked[name] = True
        unchecked = sorted(n for n, i in images.items() if i.get('partition_label') and n not in checked)
        if unchecked:
            ctx.echo('  note: the factory package is gone, so the size of the ' + ', '.join(unchecked)
                     + ' image was not checked against the stock partition table')
        state['partition_table_checked'] = checked
        frp = state['partial'] / images['frp']['image']
        frp_image(frp, images['frp']['bytes'])
        if bw.sha_file(frp) != images['frp']['sha256']:
            raise BuildStepError('the FRP image differs from the stock factory image')
        metadata = images['metadata']
        runner = bw.Runner(ctx.allow_network, ctx.echo, ws.work / 'tmp')
        runner.run(Action('Make the empty metadata filesystem (fixed UUID, time and seed)',
                          argv=[ctx.host_bin / 'make_f2fs', '-g', 'android', '-r',
                                '-T', str(android['outputs']['build_datetime']), '-U', metadata['uuid'],
                                '-l', metadata['label'], '-S', str(metadata['bytes']),
                                state['partial'] / metadata['image']]), ctx.cache['log'])

    def record():
        if state['reuse']:
            return
        out = android['outputs']
        sync, vendor = ws.passed('sync'), ws.passed('vendor')
        if sync is None:
            raise BuildStepError('the sync step has no passed record; run "diamaneos build sync" again')
        shutil.copy2(state['target_files'], state['partial'] / TARGET_FILES_COPY)
        resolved = ws.state_dir / MANIFEST_COPY
        shutil.copyfile(resolved, state['partial'] / MANIFEST_COPY)
        if bw.sha_file(state['partial'] / MANIFEST_COPY) != sync['outputs'].get('resolved_manifest_sha256'):
            raise BuildStepError('the recorded resolved manifest changed; run "diamaneos build all" again')
        tools = product_inputs.tools_identity()
        value = {
            'schema_version': 1, 'build_id': state['identifier'], 'product': config['product'],
            'release': False, 'signing': 'public-test-keys', 'never_lock': True,
            'notice': 'Test build signed with public test keys. Keep the bootloader unlocked.',
            'variant': out['variant'], 'lunch': out['lunch'], 'build_number': out['build_number'],
            'build_datetime': out['build_datetime'], 'build_identity': out['build_identity'],
            'source_identity': out.get('source_identity'),
            'tools': tools, 'reproducible': bool(tools.get('clean')),
            'environment': {'id': ctx.environment['environment_id'], 'sha256': ctx.environment_sha256},
            'source': sync['outputs'],
            'manifest': {'url': sync['outputs'].get('manifest_url'), 'branch': sync['outputs'].get('manifest_branch'),
                         'commit': sync['outputs'].get('manifest_commit'), 'file': MANIFEST_COPY,
                         'resolved_sha256': sync['outputs'].get('resolved_manifest_sha256')},
            'kernel_prebuilts': {'path': product_inputs.KERNEL_PREBUILTS,
                                 'commit': sync['outputs'].get('kernel_prebuilts_commit')},
            'generated_inputs': {'descriptor_sha256': out['descriptor_sha256'],
                                 'vendor': vendor['outputs'] if vendor else None},
            'stock_build': ctx.environment['device_inputs']['selected_stock_build'],
            'target_files': {'file': TARGET_FILES_COPY, 'sha256': out['target_files_sha256']},
            'network_isolation': out['network_isolation'], 'host': ctx.host,
            'images': {name: bw.sha_file(state['partial'] / f'{name}.img') for name in names + ['super']},
            'wipe': {'validated': wipe['validated'],
                     'partition_table_checked': state.get('partition_table_checked', {}),
                     'images': {k: {'file': v['image'], 'sha256': bw.sha_file(state['partial'] / v['image'])}
                                for k, v in wipe['images'].items()}},
            'flash': {'slot': config['slot'], 'bootloader': config['images']['bootloader'],
                      'logical': config['images']['logical']},
        }
        bw.write_atomic(state['partial'] / RECORD, bw.encoded(value), 0o640)
        write_sums(state['partial'])

    def publish():
        if not state['reuse']:
            for p in state['partial'].iterdir():
                p.chmod(0o440)
            state['partial'].rename(state['final'])
        latest = ws.images / 'latest'
        temporary = ws.images / '.latest'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(state['identifier'])
        os.replace(temporary, latest)

    actions = [Action('Check the target-files archive recorded by the build', func=prepare),
               Action('Export the partition images from target-files', func=export),
               Action('Build super.img', func=super_image),
               Action('Make the wipe images (userdata, metadata, FRP, misc)', func=wipe_images),
               Action('Write build.json and SHA256SUMS', func=record),
               Action('Publish the image directory', func=publish)]

    def outputs():
        return {'build_id': state['identifier'], 'directory': str(state['final'].relative_to(ws.root)),
                'sums_sha256': bw.sha_file(state['final'] / SUMS)}

    def valid(previous):
        directory = ws.root / previous['outputs']['directory']
        return directory.is_dir() and bw.sha_file(directory / SUMS) == previous['outputs']['sums_sha256']

    return StepPlan('package', inputs, actions, outputs, valid, waiting_for=None if android else 'android')
