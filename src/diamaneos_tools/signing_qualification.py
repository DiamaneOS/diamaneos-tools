"""Build-bound signing plans; revisions and fingerprints are recorded per run."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from . import signing_archive as za
from . import signing_mac
from .signing_inputs import (CERT_ROLES, NAME, canonical_sha256, changed_projects,
                             load_json, require, sha256_file, source_files, verified_build)
from .signing_inputs import presigned_rule
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


def role_map(metadata, policy, overrides, present=None):
    result = {}
    for kind in ('apk', 'apex'):
        for name, record in metadata[kind].items():
            if present is not None and name not in present:
                continue
            require(name not in result, 'APK/APEX metadata names overlap')
            label = za.basename_role(record.get('certificate' if kind == 'apk' else 'container_certificate', ''))
            if name in overrides:
                role = overrides[name]
            elif label in {'PRESIGNED', 'EXTERNAL'}:
                require(any(rule['metadata_name'] == name for rule in policy['presigned_packages']),
                        'Presigned package is outside the reviewed policy: ' + name)
                role = 'PRESIGNED'
            elif kind == 'apex':
                require(label and NAME.fullmatch(label), 'APEX has no usable source certificate selector')
                role = policy['apex_container_role']
            else:
                role = policy['apk_selectors'].get(label)
                require(role, 'Unknown APK signing selector: ' + name)
            result[name] = {'kind': kind, 'container': role,
                            'payload': ('PRESIGNED' if role == 'PRESIGNED' else policy['apex_payload_role']) if kind == 'apex' else None}
    require(result, 'Target-files has no signing packages')
    return result


def member_role(mapping, kind, logical):
    record = mapping.get(za.package_name(logical))
    require(record and record['kind'] == ('apk' if kind == 'apk' else 'apex'), 'Unlisted packaged signing role')
    return record


def prepare(record_path, report_path, target_files, manifest, policy, keys, tools, otatools, previous=None, *, production):
    provenance = verified_build(record_path, report_path, target_files, manifest, production=production,
                                source_root=tools.source, otatools=otatools)
    provenance['source_files'] = source_files(tools.source, provenance['projects'])
    provenance['otatools_sha256'] = sha256_file(otatools)
    provenance['java_sha256'] = sha256_file(tools.java_home / 'bin/java')
    provenance['signer_sha256'] = sha256_file(tools.path('sign_target_files_apks'))
    record = load_json(record_path)
    metadata = za.metadata(target_files)
    overrides = release_overrides(tools.source, policy)
    roles = {}
    packages, present = {}, set()
    for kind, logical, path in package_members(target_files, tools):
        name = Path(logical).name
        approved = presigned_rule(policy, logical) if kind == 'apk' and name not in metadata['apk'] else None
        if approved is not None:
            name = approved['metadata_name']
        name = za.package_name(name)
        if kind != 'payload' and name not in roles:
            roles.update(role_map(metadata, policy, overrides, {name}))
        role = roles.get(name) if approved is not None else member_role(roles, kind, logical)
        require(role is not None, 'Packaged signing role is missing')
        if kind == 'payload':
            continue
        present.add(name)
        if logical in packages:
            continue
        packages[logical] = {'kind': role['kind'], 'container': role['container'], 'payload': role['payload'],
                             'certificate_sha256': keys.fingerprint(role['container'], logical)}
        if kind == 'apk':
            packages[logical]['package_id'] = tools.package_id(path)
        if role['container'] == 'PRESIGNED':
            packages[logical]['presigned_sha256'] = sha256_file(path)
            tools.apk(path, packages[logical]['certificate_sha256'])
        if kind == 'apex':
            key = keys.avb(logical if role['payload'] == 'PRESIGNED' else None)
            packages[logical]['payload_key_sha256'] = sha256_file(key)
    absent = (set(metadata['apk']) | set(metadata['apex'])) - present
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
              'provenance': provenance, 'changed_projects': changed_projects(provenance['projects'], previous, production=production),
              'metadata': za.package_metadata_shape(metadata),
              'absent_metadata_packages': sorted(absent),
              'packages': packages, 'roles': roles, 'partitions': partitions, 'chains': chains}
    result['passthrough_images'] = dict(record.get('firmware', {}).get('images', {}))
    result['passthrough_images'].update({value['file']: value['sha256'] for value in record.get('firmware', {}).get('reset', {}).values()})
    result['passthrough_images'].update({value['file']: value['sha256'] for value in record.get('wipe', {}).get('images', {}).values()})
    if 'super_empty' in record['images']:
        result['passthrough_images']['super_empty.img'] = record['images']['super_empty']
    with za.archive(target_files) as handle:
        misc = za.properties(za.read_text(handle, 'META/misc_info.txt'))
    logical = misc.get('dynamic_partition_list', '').split()
    require(len(logical) == len(set(logical)) and set(logical) <= set(partitions),
            'Dynamic partition list has undeclared partitions')
    result['logical_partitions'] = sorted(logical)
    result['mac_permissions'] = signing_mac.prepare_mac_permissions(target_files, metadata, roles, keys, tools)
    identities = {}
    for package in packages.values():
        if package['kind'] == 'apk':
            name, certificate = package['package_id'], package['certificate_sha256']
            require(name not in identities or identities[name] == certificate, 'APK identity has conflicting signing roles')
            identities[name] = certificate
    for policies in result['mac_permissions'].values():
        for entry in policies:
            for name in entry['packages']:
                require(name not in identities or entry['certificates'] == [identities[name]],
                        'Named MAC selector does not match the APK signing role')
    if previous is not None:
        result['previous_artifacts'] = previous['artifacts']
    else:
        result['previous_artifacts'] = {}
    result['plan_sha256'] = canonical_sha256(result)
    return result


def signing_command(plan, *, signer, key_dir, source, destination, source_root):
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
    require(source_files(source_root, plan['provenance']['projects']) == plan['provenance']['source_files'],
            'Signing source changed after the plan was prepared')
    grouped = defaultdict(list)
    for name, role in plan['roles'].items():
        require(NAME.fullmatch(name), 'Unsafe signing package name')
        grouped[role['container']].append(name)
    key_dir = Path(key_dir).resolve()
    argv = [str(Path(signer).resolve()), '-o', '-d', str(key_dir)]
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
    return argv + [str(Path(source).resolve()), str(Path(destination).resolve())]
