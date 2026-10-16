"""Full factory ZIPs derived from an admitted build and signed partition set."""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from . import flash_all, signing_archive as za, signing_layout as layout
from .signing_inputs import (MAX_JSON, SHA256, fields, require, safe_file,
                             sha256_file, source_files)

GENERATOR = 'tools/diamaneos/src/diamaneos_tools/flash_all.py'
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
PARTITION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
INPUT_FIELDS = {'schema_version', 'product', 'build_id', 'build_identity', 'build_number',
                'build_datetime', 'variant', 'official', 'manifest', 'flash', 'firmware', 'wipe'}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def file_name(name):
    require(isinstance(name, str) and TOKEN.fullmatch(name), 'Unsafe factory file name')
    return name


def input_record(record, plan):
    """Keep only install metadata; the complete original record is bound by hash."""
    require(isinstance(record, dict) and INPUT_FIELDS <= set(record), 'Build lacks factory install metadata')
    value = {name: copy.deepcopy(record[name]) for name in INPUT_FIELDS}
    validate_input(value, plan)
    return value


def validate_input(value, plan):
    fields(value, INPUT_FIELDS)
    provenance = plan['provenance']
    for name in ('product', 'build_id', 'build_number'):
        file_name(value[name])
        require(value[name] == provenance[name], 'Factory build identity differs from the signing plan')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['build_identity'] == provenance['build_identity']
            and value['build_datetime'] == provenance['build_datetime']
            and type(value['build_datetime']) is int and value['build_datetime'] >= 0,
            'Factory build metadata differs from the signing plan')
    require(value['variant'] in {'user', 'userdebug', 'eng'} and type(value['official']) is bool,
            'Invalid factory build type')
    if plan['mode'] == 'production':
        require(value['variant'] == 'user' and value['official'] is True,
                'Production factory requires an official user build')
    manifest = value['manifest']
    require(isinstance(manifest, dict) and manifest.get('file') == 'resolved-manifest.xml'
            and manifest.get('resolved_sha256') == provenance['manifest_sha256'],
            'Factory manifest differs from the admitted build')
    flash = value['flash']
    fields(flash, {'slot', 'bootloader', 'logical'})
    require(flash['slot'] in {'a', 'b'}, 'Invalid factory install slot')
    names = []
    for kind in ('bootloader', 'logical'):
        group = flash[kind]
        require(isinstance(group, list) and all(isinstance(n, str) and PARTITION.fullmatch(n) for n in group),
                'Invalid factory partition inventory')
        names.extend(group)
    require(len(names) == len(set(names)) and set(names) == set(plan['partitions'])
            and set(flash['logical']) == set(plan.get('logical_partitions', []))
            and 'vbmeta' in flash['bootloader'], 'Factory flash layout differs from signed partitions')
    if flash['logical']:
        require('recovery' in flash['bootloader'] and 'super_empty.img' in plan['passthrough_images'],
                'Fastbootd install lacks recovery or empty super metadata')
    carried = {}
    firmware = value['firmware']
    require(isinstance(firmware, dict) and isinstance(firmware.get('images'), dict)
            and isinstance(firmware.get('steps'), list) and isinstance(firmware.get('reset'), dict),
            'Invalid factory firmware inventory')
    if firmware['images']:
        file_name(firmware.get('release'))
    carried.update(firmware['images'])
    for step in firmware['steps']:
        require(isinstance(step, dict) and {'image', 'partition'} <= set(step)
                and set(step) <= {'image', 'partition', 'wipe_only'}
                and isinstance(step['partition'], str) and PARTITION.fullmatch(step['partition'])
                and step['image'] in firmware['images'], 'Invalid factory firmware step')
    wipe = value['wipe']
    require(isinstance(wipe, dict) and isinstance(wipe.get('images'), dict), 'Invalid factory wipe inventory')
    for group in (firmware['reset'], wipe['images']):
        for partition, image in group.items():
            require(isinstance(partition, str) and PARTITION.fullmatch(partition)
                    and isinstance(image, dict) and set(image) == {'file', 'sha256'},
                    'Invalid factory reset or wipe image')
            require(image['file'] not in carried, 'Factory payload names overlap')
            carried[image['file']] = image['sha256']
    if flash['logical']:
        carried['super_empty.img'] = plan['passthrough_images']['super_empty.img']
    require(carried == plan['passthrough_images'], 'Factory passthrough inventory differs from the signing plan')
    all_members = layout.full_image_members(plan)
    require(len(all_members) == len(plan['partitions']) + len(carried) + bool(flash['logical']),
            'Factory image roles overlap')
    for name in all_members:
        file_name(name)
    require(all(isinstance(d, str) and SHA256.fullmatch(d) for d in carried.values()),
            'Invalid factory payload digest')


def trusted_scripts(record, plan, tools):
    expected = plan['provenance']['source_files'].get(GENERATOR)
    require(expected is not None and sha256_file(Path(flash_all.__file__)) == expected
            and sha256_file(safe_file(tools.source, GENERATOR)) == expected,
            'Factory script generator differs from the plan source')
    return {name: text.encode('utf-8') for name, text in flash_all.scripts(record).items()}


def directory_name(plan):
    require('factory_input' in plan, 'Signing plan lacks factory install metadata; prepare a new plan')
    value = plan['factory_input']
    return file_name(value['product'] + '-factory-' + value['build_number'])


def derived_record(plan, signed_hash, image_hashes, key_hash):
    original = plan.get('factory_input')
    require(original is not None, 'Signing plan lacks factory install metadata; prepare a new plan')
    validate_input(original, plan)
    require(set(image_hashes) == layout.full_image_members(plan), 'Factory image membership is incomplete')
    production = plan['mode'] == 'production'
    value = copy.deepcopy(original)
    value.update(release=production, signing='release-keys' if production else 'qualification-keys',
                 never_lock=not production, reproducible=False,
                 target_files={'file': 'target-files.zip', 'sha256': signed_hash},
                 images={name: image_hashes[name + '.img'] for name in
                         [*plan['partitions'], *(['super', 'super_empty'] if plan.get('logical_partitions') else [])]},
                 avb_custom_key={'file': 'avb_pkmd.bin', 'sha256': key_hash},
                 packaging_tools={'commit': plan['provenance']['projects']['tools/diamaneos'], 'clean': True},
                 signing_input={'plan_sha256': plan['plan_sha256'],
                                'build_record_sha256': plan['provenance']['record_sha256'],
                                'verification_sha256': plan['provenance']['verification_sha256'],
                                'target_files_sha256': plan['provenance']['target_files_sha256'],
                                'manifest_sha256': plan['provenance']['manifest_sha256']})
    # Scripts and installer commands must use the order read back from canonical JSON.
    return json.loads(encoded(value))


def image_hashes(handle, plan):
    names = set(handle.namelist())
    require(names == layout.full_image_members(plan), 'Factory requires the complete flat image set')
    for info in handle.infolist():
        require(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG},
                'Factory image is not a regular file')
    hashes = {name: za.digest_member(handle, name) for name in sorted(names)}
    require(all(hashes[name] == digest for name, digest in plan['passthrough_images'].items()),
            'Factory passthrough bytes differ from the admitted build')
    return hashes


def public_key(tools, keys, destination):
    tools.run('avbtool', ['extract_public_key', '--key', keys.avb(), '--output', destination])
    require(destination.is_file() and 0 < destination.stat().st_size <= 1024 * 1024,
            'Invalid external AVB public key export')
    return destination.read_bytes()


def expected_metadata(plan, signed, hashes, key, manifest_hash, tools):
    require(manifest_hash == plan['provenance']['manifest_sha256'], 'Factory resolved manifest changed')
    record = derived_record(plan, sha256_file(signed), hashes, hashlib.sha256(key).hexdigest())
    files = {'build.json': encoded(record), 'avb_pkmd.bin': key, **trusted_scripts(record, plan, tools)}
    return record, files


def sums(hashes):
    return ''.join(f'{digest}  {name}\n' for name, digest in sorted(hashes.items())).encode('ascii')


def zip_info(name, size, timestamp):
    when = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    date = ((when.year, when.month, when.day, when.hour, when.minute, when.second)
            if 1980 <= when.year <= 2107 else (1980, 1, 1, 0, 0, 0))
    info = zipfile.ZipInfo(name, date)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (0o755 if name.endswith('/flash-all.sh') else 0o644)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    info.file_size = size
    return info


def write_factory(destination, images, signed, manifest, plan, tools, keys):
    with za.archive(images, allow_links=False) as handle, tempfile.TemporaryDirectory(dir=tools.scratch) as td:
        hashes = image_hashes(handle, plan)
        key_path = Path(td) / 'avb_pkmd.bin'
        key = public_key(tools, keys, key_path)
        vbmeta = za.extract(handle, 'vbmeta.img', Path(td) / 'vbmeta.img')
        tools.avb_binary_authority(vbmeta, key_path)
        manifest_data = Path(manifest).read_bytes()
        require(len(manifest_data) <= MAX_JSON, 'Factory manifest exceeds its bound')
        record, metadata = expected_metadata(plan, signed, hashes, key,
                                             hashlib.sha256(manifest_data).hexdigest(), tools)
        metadata['resolved-manifest.xml'] = manifest_data
        digests = {**hashes, **{name: hashlib.sha256(data).hexdigest() for name, data in metadata.items()}}
        metadata['SHA256SUMS'] = sums(digests)
        top = directory_name(plan)
        with zipfile.ZipFile(destination, 'x', allowZip64=True) as output:
            for name in sorted(hashes):
                info = zip_info(top + '/' + name, handle.getinfo(name).file_size, record['build_datetime'])
                digest = hashlib.sha256()
                with handle.open(name) as source, output.open(info, 'w', force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk); target.write(chunk)
                require(digest.hexdigest() == hashes[name], 'Image changed while writing the factory')
            for name, data in sorted(metadata.items()):
                output.writestr(zip_info(top + '/' + name, len(data), record['build_datetime']), data)
    return record


def verify_factory(factory, images, signed, plan, tools, keys):
    """Check every ZIP member against independently derived metadata and image bytes."""
    with za.archive(images, allow_links=False) as source, za.archive(factory, allow_links=False) as handle, \
            tempfile.TemporaryDirectory(dir=tools.scratch) as td:
        hashes = image_hashes(source, plan)
        top = directory_name(plan)
        names = layout.full_image_members(plan) | layout.FACTORY_METADATA
        require(set(handle.namelist()) == {top + '/' + name for name in names},
                'Factory ZIP has unknown or missing members')
        require(all(not i.is_dir() and stat.S_IFMT(i.external_attr >> 16) in {0, stat.S_IFREG}
                    for i in handle.infolist()), 'Factory ZIP contains a nonregular member')
        key_path = Path(td) / 'avb_pkmd.bin'
        key = public_key(tools, keys, key_path)
        vbmeta = za.extract(handle, top + '/vbmeta.img', Path(td) / 'vbmeta.img')
        tools.avb_binary_authority(vbmeta, key_path)
        manifest = za.read_text(handle, top + '/resolved-manifest.xml').encode('utf-8')
        _record, metadata = expected_metadata(plan, signed, hashes, key, hashlib.sha256(manifest).hexdigest(), tools)
        metadata['resolved-manifest.xml'] = manifest
        for name, data in metadata.items():
            require(handle.getinfo(top + '/' + name).file_size <= MAX_JSON
                    and handle.read(top + '/' + name) == data, 'Factory metadata differs from the signing plan')
        for name, digest in hashes.items():
            require(za.digest_member(handle, top + '/' + name) == digest, 'Factory image differs from verified images')
        digests = {**hashes, **{name: hashlib.sha256(data).hexdigest() for name, data in metadata.items()}}
        require(za.read_text(handle, top + '/SHA256SUMS').encode('utf-8') == sums(digests),
                'Factory checksums do not cover exactly the final files')


def write_images(destination, signed, original, plan, tools):
    """Rebuild super from signed target-files and retain only admitted passthroughs."""
    with tempfile.TemporaryDirectory(dir=tools.scratch) as td, za.archive(signed) as source, \
            zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for name in sorted(plan['partitions']):
            info = source.getinfo('IMAGES/' + name + '.img')
            require(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG},
                    'Signed partition is not a regular file')
            with source.open(info) as origin, output.open(name + '.img', 'w', force_zip64=True) as target:
                shutil.copyfileobj(origin, target, 1024 * 1024)
        if plan.get('logical_partitions'):
            super_image = Path(td) / 'super.img'
            tools.run('build_super_image', [signed, super_image])
            output.write(super_image, 'super.img')
        for name, digest in sorted(plan['passthrough_images'].items()):
            path = safe_file(original, name)
            require(sha256_file(path) == digest, 'Factory source passthrough changed')
            output.write(path, name)
            require(sha256_file(path) == digest, 'Factory source passthrough changed while copying')


def assemble(images_output, factory_output, signed, manifest, original, plan, tools, keys, verify_images):
    """Publish two completed, verified archives without replacing existing files."""
    images_output, factory_output = map(Path, (images_output, factory_output))
    require(images_output.parent.resolve() == factory_output.parent.resolve()
            and images_output.name != factory_output.name, 'Factory outputs need distinct names in one directory')
    require(not images_output.exists() and not images_output.is_symlink()
            and not factory_output.exists() and not factory_output.is_symlink(), 'Factory output already exists')
    parent = factory_output.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix='.factory-') as td:
        work = Path(td)
        images, factory = work / images_output.name, work / factory_output.name
        write_images(images, signed, original, plan, tools)
        verify_images(images, signed, plan, tools, keys)
        write_factory(factory, images, signed, manifest, plan, tools, keys)
        verify_factory(factory, images, signed, plan, tools, keys)
        publish_outputs(((images, images_output), (factory, factory_output)))


def publish_outputs(pairs):
    published = []
    try:
        for staged, output in pairs:
            os.link(staged, output)
            published.append((staged, output))
    except BaseException:
        for staged, output in published:
            if output.exists() and os.path.samestat(staged.stat(), output.lstat()):
                output.unlink()
        raise
