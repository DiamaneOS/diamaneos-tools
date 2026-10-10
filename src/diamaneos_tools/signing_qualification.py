"""Build-bound signing plans; revisions and fingerprints are recorded per run."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from . import signing_archive as za
from .signing_inputs import (CERT_ROLES, NAME, canonical_sha256, changed_projects,
                             load_json, require, sha256_file, source_files, verified_build)
from .signing_native import avb_descriptors, package_members


def release_overrides(source, policy):
    text = (Path(source) / 'script/generate-release.sh').read_text()
    result = {}
    for match in re.finditer(r'--extra_apks\s+([A-Za-z0-9._,+-]+)="?\$KEY_DIR/([a-z0-9_]+)', text):
        require(match[2] in CERT_ROLES, 'Release script uses an unknown certificate role')
        for name in match[1].split(','):
            require(name not in result and NAME.fullmatch(name), 'Duplicate release-script package override')
            result[name] = match[2]
    for match in re.finditer(r'--extra_apex_payload_key\s+\S+=\S*\$KEY_DIR/(\S+)', text):
        require(match[1].strip('"') == 'avb.pem', 'Release script uses an unknown APEX payload role')
    require(result, 'Release script has no supported package overrides')
    return result


def role_map(metadata, policy, overrides):
    result = {}
    for kind in ('apk', 'apex'):
        for name, record in metadata[kind].items():
            require(name not in result, 'APK/APEX metadata names overlap')
            label = za.basename_role(record.get('certificate' if kind == 'apk' else 'container_certificate', ''))
            if name in overrides:
                role = overrides[name]
            elif label in {'PRESIGNED', 'EXTERNAL'}:
                role = 'PRESIGNED'
            elif kind == 'apex':
                require(label and NAME.fullmatch(label), 'APEX has no usable source certificate selector')
                role = policy['apex_container_role']
            else:
                role = policy['apk_selectors'].get(label)
                require(role, 'Unknown APK signing selector: ' + name)
            result[name] = {'kind': kind, 'container': role,
                            'payload': ('PRESIGNED' if role == 'PRESIGNED' else 'avb') if kind == 'apex' else None}
    require(result, 'Target-files has no signing packages')
    return result


def member_role(mapping, kind, logical):
    name = Path(logical).name.removesuffix('.capex')
    if logical.endswith('.capex'):
        name += '.apex'
    record = mapping.get(name)
    require(record and record['kind'] == ('apk' if kind == 'apk' else 'apex'), 'Unlisted packaged signing role')
    return record


def prepare(record_path, report_path, target_files, manifest, policy, keys, tools, otatools, previous=None, *, production):
    provenance = verified_build(record_path, report_path, target_files, manifest, production=production, otatools=otatools)
    provenance['source_files'] = source_files(tools.source, provenance['projects'])
    provenance['otatools_sha256'] = sha256_file(otatools)
    provenance['java_sha256'] = sha256_file(tools.java_home / 'bin/java')
    provenance['signer_sha256'] = sha256_file(tools.path('sign_target_files_apks'))
    record = load_json(record_path)
    metadata = za.metadata(target_files)
    roles = role_map(metadata, policy, release_overrides(tools.source, policy))
    packages, present, presigned = {}, set(), set()
    for kind, logical, path in package_members(target_files, tools):
        role = member_role(roles, kind, logical)
        if kind == 'payload':
            continue
        present.add(Path(logical).name if kind == 'apk' else Path(logical).name.removesuffix('.capex') +
                    ('.apex' if logical.endswith('.capex') else ''))
        cert = keys.cert(role['container'], logical)
        if logical in packages:
            continue
        packages[logical] = {'kind': role['kind'], 'container': role['container'], 'payload': role['payload'],
                             'certificate_sha256': tools.certificate(cert)[0]}
        if role['container'] == 'PRESIGNED':
            packages[logical]['presigned_sha256'] = sha256_file(path)
            presigned.add(logical)
        if kind == 'apex':
            key = keys.avb(logical if role['payload'] == 'PRESIGNED' else None)
            packages[logical]['payload_key_sha256'] = sha256_file(key)
    absent = set(roles) - present
    require(absent == set(keys.inventory['metadata_only']), 'Metadata-only exception set does not match package absence')
    require(presigned == set(keys.inventory['presigned']), 'Presigned inventory has missing or unused entries')
    partitions = sorted(set(record['images']) - {'super', 'super_empty'})
    require('vbmeta' in partitions, 'Verified build has no AVB root')
    chains, pending, visited = {}, ['vbmeta'], set()
    with za.archive(target_files) as handle:
        while pending:
            name = pending.pop()
            require(name not in visited, 'Unsigned AVB chain is cyclic')
            visited.add(name)
            path = za.extract(handle, 'IMAGES/' + name + '.img', tools.scratch / ('input-' + name + '.img'))
            for child, location in avb_descriptors(tools.run('avbtool', ['info_image', '--image', path])):
                require(child in partitions, 'Unsigned AVB references an undeclared partition')
                if location is not None:
                    require(child not in chains, 'Duplicate unsigned AVB chain')
                    chains[child] = location
                    pending.append(child)
    result = {'schema_version': 2, 'mode': 'production' if production else 'qualification',
              'policy_sha256': canonical_sha256(policy), 'key_inventory_sha256': canonical_sha256(keys.inventory),
              'public_material_sha256': canonical_sha256(keys.files),
              'public_identities': {**keys.public_ids, 'avb': keys.avb_id},
              'provenance': provenance, 'changed_projects': changed_projects(provenance['projects'], previous),
              'metadata': {'apk': metadata['apk'], 'apex': metadata['apex']},
              'packages': packages, 'roles': roles, 'partitions': partitions, 'chains': chains}
    result['passthrough_images'] = dict(record.get('firmware', {}).get('images', {}))
    result['passthrough_images'].update({value['file']: value['sha256'] for value in record.get('wipe', {}).get('images', {}).values()})
    if 'super_empty' in record['images']:
        result['passthrough_images']['super_empty.img'] = record['images']['super_empty']
    if previous is not None:
        result['previous_artifacts'] = previous['artifacts']
    else:
        result['previous_artifacts'] = {}
    result['plan_sha256'] = canonical_sha256(result)
    return result


def signing_command(plan, *, signer, key_dir, source, destination):
    """Return explicit native signing arguments only for the exact accepted input."""
    require(sha256_file(source) == plan['provenance']['target_files_sha256'],
            'Signing input differs from the build that passed build verify')
    require(not Path(destination).exists() and Path(source).resolve() != Path(destination).resolve(),
            'Signing output already exists or replaces the input')
    require(Path(signer).name == 'sign_target_files_apks' and sha256_file(signer) == plan['provenance']['signer_sha256'],
            'Signer is not from the build-bound otatools archive')
    body = dict(plan)
    digest = body.pop('plan_sha256')
    require(canonical_sha256(body) == digest, 'Signing plan was changed')
    grouped = defaultdict(list)
    for name, role in plan['roles'].items():
        require(NAME.fullmatch(name), 'Unsafe signing package name')
        grouped[role['container']].append(name)
    argv = [str(signer), '-o', '-d', str(key_dir)]
    for role, names in sorted(grouped.items()):
        require(role in CERT_ROLES | {'PRESIGNED'}, 'Unknown signing role')
        key = '' if role == 'PRESIGNED' else str(Path(key_dir) / role)
        for start in range(0, len(names), 256):
            argv += ['--extra_apks', ','.join(sorted(names)[start:start + 256]) + '=' + key]
    for name, role in sorted(plan['roles'].items()):
        if role['kind'] == 'apex':
            key = '' if role['payload'] == 'PRESIGNED' else str(Path(key_dir) / 'avb.pem')
            argv += ['--extra_apex_payload_key', name + '=' + key]
    supported = {'boot', 'init_boot', 'recovery', 'system', 'system_other', 'vendor',
                 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor'}
    for name in sorted({'vbmeta'} | set(plan['chains'])):
        require(name in supported, 'Signing tools do not expose this AVB chain override')
        argv += [f'--avb_{name}_key', str(Path(key_dir) / 'avb.pem'),
                 f'--avb_{name}_algorithm', 'SHA256_RSA4096']
    return argv + [str(source), str(destination)]
