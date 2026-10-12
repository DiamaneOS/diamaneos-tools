"""Verify publication artifacts with native tools and an independent role inventory."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import shlex
import shutil
import sys
import tarfile
import tempfile

from . import build, signing_archive as za, signing_qualification as plans, signing_layout as layout, signing_mac
from .signing_inputs import (DEFAULT_CONFIG, SigningError, canonical_sha256, fields,
                             load_json, require, safe_file, sha256_file, source_files,
                             validate_config, verified_build, write_json, input_hashes, recheck_hashes, sha256_stream)
from .signing_inputs import MAX_JSON, snapshot_file, recheck_stamps
from .signing_inputs import public_inventory_fields, presigned_apex_rule
from .signing_native import (NativeTools, PublicKeys, apk_certificate_digests, package_members,
                             raw_image, verify_images, factory_authority, host_ssh_signature)


def validate_plan(plan, policy):
    require(isinstance(plan, dict), 'Signing plan must be a JSON object')
    require(plan.get('schema_version') == 2 and plan.get('mode') in {'production', 'qualification'},
            'Unknown signing plan format')
    require(all(isinstance(plan.get(key), dict) for key in ('provenance', 'packages', 'roles', 'metadata', 'mac_permissions')),
            'Signing plan has malformed structured fields')
    body = dict(plan)
    digest = body.pop('plan_sha256', None)
    require(canonical_sha256(body) == digest and plan.get('policy_sha256') == canonical_sha256(policy),
            'Signing plan or role policy changed')


@contextmanager
def metadata_inputs(args):
    """Authenticate and consume the same private metadata, retaining input basenames."""
    if args.command not in ('prepare', 'verify'):
        yield args
        return
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.scratch, prefix='.signing-metadata-') as directory:
        root = Path(directory); root.chmod(0o700)
        frozen = argparse.Namespace(**vars(args))
        frozen.metadata_stamps = {}
        copied, parents = {}, {}
        def capture(path, limit=MAX_JSON, destination=None):
            path = safe_file(Path(path).parent, Path(path).name)
            if path in copied:
                require(copied[path].stat().st_size <= limit, 'Signing snapshot input exceeds its limit')
                return copied[path]
            if destination is None:
                parents.setdefault(path.parent, root / str(len(parents)))
                destination = parents[path.parent] / path.name
            copied[path] = snapshot_file(path, destination, limit, stamps=frozen.metadata_stamps)
            return copied[path]
        frozen.config = capture(args.config)
        frozen.keys = capture(args.keys, destination=root / 'public' / args.keys.name)
        inventory = load_json(frozen.keys)
        public_inventory_fields(inventory)
        require(isinstance(inventory['certificate_roles'], dict), 'Malformed public certificate inventory')
        fields(inventory['factory'], {'allowed_signers', 'identity'})
        expected_root = args.keys.resolve().parent
        material = [*inventory['certificate_roles'].values(), inventory['avb_public_key'], inventory['factory']['allowed_signers']]
        material += list(inventory.get('presigned_apex_payload_keys', {}).values())
        if args.command == 'verify':
            require(not args.keys.resolve().is_relative_to(args.artifact_root.resolve()),
                    'Expected public keys must be outside the artifacts')
        for relative in material:
            original = safe_file(expected_root, relative)
            if args.command == 'verify':
                require(not original.resolve().is_relative_to(args.artifact_root.resolve()),
                        'Expected public material is inside the publication')
            capture(original, 1024 * 1024, frozen.keys.parent / relative)
        if args.command == 'prepare':
            for name in ('build_record', 'verification_report', 'resolved_manifest'):
                setattr(frozen, name, capture(getattr(args, name)))
            capture(safe_file(args.build_record.parent, 'SHA256SUMS'), za.MAX_TEXT,
                    frozen.build_record.parent / 'SHA256SUMS')
            frozen.verification_signature = capture(args.verification_signature, 1024 * 1024)
            for name in ('previous_result', 'previous_signature'):
                if getattr(args, name):
                    setattr(frozen, name, capture(getattr(args, name), 1024 * 1024 if name.endswith('signature') else MAX_JSON))
        else:
            frozen.plan, frozen.publication = capture(args.plan), capture(args.publication)
            publication = load_json(frozen.publication)
            fields(publication, {'schema_version', 'signed_target_files', 'images_archive', 'ota',
                                 'factory_archive', 'factory_signature', 'release_record', 'release_signature'})
            frozen.release_inputs = (
                capture(safe_file(args.artifact_root, publication['release_record'])),
                capture(safe_file(args.artifact_root, publication['release_signature']), 1024 * 1024))
        yield frozen


@contextmanager
def native_inputs(otatools, source, keys_path, scratch, production, projects, policy=None, artifact_root=None, *, expected_otatools):
    before = input_hashes((otatools, keys_path))
    with tempfile.TemporaryDirectory(dir=scratch, prefix='.signing-') as directory:
        work = Path(directory)
        snapshot = work / 'otatools.zip'
        with snapshot.open('xb') as destination, Path(otatools).open('rb') as origin:
            shutil.copyfileobj(origin, destination)
        snapshot.chmod(0o600)
        require(sha256_file(snapshot) == expected_otatools, 'Otatools snapshot differs from authenticated provenance')
        recheck_hashes(before)
        root = za.unpack_tools(snapshot, work / 'otatools')
        tools = NativeTools(root, source, work, projects=projects, policy=policy)
        keys = PublicKeys(keys_path, tools, production=production, policy=policy, artifact_root=artifact_root)
        yield tools, keys
        recheck_hashes(before)
        require(all(sha256_file(keys.root / name) == digest for name, digest in keys.files.items()),
                'Public verification material changed during processing')


def verify_packages(target_files, plan, tools, keys):
    metadata = za.metadata(target_files)
    require(za.package_metadata_shape(metadata) == plan['metadata'],
            'Signed package membership or storage properties differ from accepted input')
    observed, counts = set(), {'apk': 0, 'apex': 0, 'payload': 0}
    for kind, logical, path in package_members(target_files, tools):
        role = plan['packages'].get(logical)
        require(role and role['kind'] == ('apk' if kind == 'apk' else 'apex'), 'Unlisted final package')
        approved = presigned_apex_rule(keys.policy, logical) if kind in {'apex', 'payload'} else None
        if approved is not None:
            require(role['container'] == role['payload'] == 'PRESIGNED'
                    and role.get('presigned_sha256') == approved['artifact_sha256'],
                    'Reviewed presigned APEX plan changed its authority or bytes')
        if kind == 'payload':
            public = keys.avb(logical if role['payload'] == 'PRESIGNED' else None)
            require(sha256_file(public) == role['payload_key_sha256'], 'APEX payload authority changed')
            tools.avb(path, public, payload=True, **({'binary': True} if role['payload'] == 'PRESIGNED' else {}))
        else:
            if role['container'] == 'PRESIGNED' and logical not in observed:
                require(sha256_file(path) == role['presigned_sha256'], 'Presigned package was changed')
            require(keys.fingerprint(role['container'], logical) == role['certificate_sha256'], 'Package authority changed')
            tools.apk(path, role['certificate_sha256'])
            if kind == 'apk':
                require(tools.package_id(path) == role['package_id'], 'Final APK package identity changed')
            observed.add(logical)
        counts[kind] += 1
    require(observed == set(plan['packages']), 'Final target-files lacks planned packages')
    require(counts['payload'] == sum(r['kind'] == 'apex' for r in plan['packages'].values()),
            'Final package verification lacks an APEX payload')
    return counts


def publication_images(path, target_files, plan, tools, keys):
    """Use the images to be published, including the logical images in super."""
    with tempfile.TemporaryDirectory(dir=tools.scratch) as directory:
        root = Path(directory)
        final = root / 'final'; final.mkdir()
        expected = root / 'expected'; expected.mkdir()
        with za.archive(target_files) as handle:
            for name in plan['partitions']:
                za.extract(handle, 'IMAGES/' + name + '.img', expected / (name + '.img'))
        with za.archive(path, allow_links=False) as handle:
            require(not any(info.is_dir() for info in handle.infolist()), 'Image archive contains an unlisted directory')
            members = [name for name in handle.namelist() if not name.endswith('/')]
            require(all(name == Path(name).name for name in members), 'Image archive members must be flat')
            layout.image_members(plan, set(members))
            require(len({Path(name).name for name in members}) == len(members), 'Duplicate published image names')
            for member in members:
                name = Path(member).name
                image = za.extract(handle, member, final / name)
                if name in plan['passthrough_images']:
                    require(sha256_file(image) == plan['passthrough_images'][name], 'Published firmware or wipe image changed')
        if (final / 'super.img').exists():
            super_image = raw_image(tools, final / 'super.img', root / 'super.raw.img')
            logical = root / 'logical'; logical.mkdir()
            tools.run('lpunpack', [super_image, logical])
            for image in logical.glob('*.img'):
                name = image.stem
                if name.endswith('_b'):
                    require(image.stat().st_size == 0, 'Published super has a populated second slot')
                    continue
                name = name.removesuffix('_a')
                require(name in plan['partitions'], 'Super contains an undeclared partition')
                destination = final / (name + '.img')
                if destination.exists():
                    require(sha256_file(raw_image(tools, destination, root / ('direct-' + name + '.img'))) ==
                            sha256_file(raw_image(tools, image, root / ('super-' + name + '.img'))),
                            'Direct partition differs from its super contents')
                else:
                    shutil.copyfile(image, destination)
        for name in plan['partitions']:
            published = safe_file(final, name + '.img')
            signed = safe_file(expected, name + '.img')
            require(sha256_file(raw_image(tools, published, root / ('published-' + name + '.img'))) ==
                    sha256_file(raw_image(tools, signed, root / ('signed-' + name + '.img'))),
                    'Published partition differs from signed target-files: ' + name)
        partitions = verify_images(tools, keys, final, plan['partitions'], plan['chains'])
        verify_installed_packages(final, target_files, plan, tools)
        return partitions


def verify_installed_packages(images, target_files, plan, tools):
    """Bind verified package bytes to their actual published filesystems."""
    expected = {name for name in plan['packages'] if '!/' not in name} | set(plan['mac_permissions'])
    prefixes = {'system': 'SYSTEM', 'system_ext': 'SYSTEM_EXT', 'product': 'PRODUCT',
                'vendor': 'VENDOR', 'odm': 'ODM'}
    require(all(name.split('/')[0] in {prefixes[p] for p in plan['partitions'] if p in prefixes}
                for name in expected), 'Packages have no corresponding published filesystem')
    observed = set()
    with za.archive(target_files) as archive:
        for partition, prefix in prefixes.items():
            if partition not in plan['partitions']:
                continue
            with tempfile.TemporaryDirectory(dir=tools.scratch) as directory:
                destination = Path(directory) / 'filesystem'
                tools.extract_filesystem(images / (partition + '.img'), destination)
                for path in destination.rglob('*'):
                    if not path.name.endswith(('.apk', '.apex', '.capex', 'mac_permissions.xml')):
                        continue
                    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(destination.resolve()),
                            'Installed package is an unsafe filesystem entry')
                    relative = str(path.relative_to(destination))
                    candidates = {prefix + '/' + relative, prefix + '/' + relative.removeprefix(partition + '/')}
                    matches = candidates & expected
                    require(len(matches) == 1, 'Installed filesystem contains an unlisted package')
                    name = matches.pop()
                    require(name not in observed and sha256_file(path) == za.digest_member(archive, name),
                            'Installed package differs from verified target-files')
                    observed.add(name)
    require(observed == expected, 'Published filesystems lack verified packages')


def verify_factory_contents(factory, image_archive):
    expected = sha256_file(image_archive)
    with tarfile.open(factory, 'r:*') as handle:
        members = handle.getmembers()
        require(len(members) <= za.MAX_MEMBERS and sum(m.size for m in members) <= za.MAX_EXPANDED,
                'Factory archive exceeds its bounds')
        names = [m.name for m in members]
        require(set(names) == layout.factory_members(image_archive)
                and len(set(names)) == len(names) and all(m.isfile() for m in members),
                'Factory archive contains unlisted or missing members')
        with handle.extractfile(members[0]) as stream:
            require(sha256_stream(stream) == expected, 'Factory archive contains different images')


def verify_publication(plan, publication, artifact_root, policy, tools, keys, *, release_inputs=None):
    validate_plan(plan, policy)
    fields(publication, {'schema_version', 'signed_target_files', 'images_archive', 'ota',
                         'factory_archive', 'factory_signature', 'release_record', 'release_signature'})
    require(publication['schema_version'] == 1 and isinstance(publication['ota'], list) and publication['ota'],
            'Publication has no OTA package')
    require(canonical_sha256(keys.inventory) == plan['key_inventory_sha256']
            and canonical_sha256(keys.files) == plan['public_material_sha256'], 'Independent public key inventory changed')
    require(all(not (keys.root / name).resolve().is_relative_to(Path(artifact_root).resolve()) for name in keys.files),
            'Expected public keys must be outside the artifacts')
    require(source_files(tools.source, plan['provenance']['projects']) == plan['provenance']['source_files']
            and sha256_file(tools.java_home / 'bin/java') == plan['provenance']['java_sha256'],
            'Signing tools do not match the run provenance')
    names = [publication[key] for key in ('signed_target_files', 'images_archive', 'factory_archive', 'factory_signature')]
    previous = []
    for record in publication['ota']:
        fields(record, {'file', 'source_target_files'})
        names.append(record['file'])
        if record['source_target_files'] is not None:
            previous.append(record['source_target_files'])
    require(len(set(names)) == len(names), 'Publication reuses an artifact path')
    require(publication['release_record'] not in names and publication['release_signature'] not in names
            and publication['release_record'] != publication['release_signature'], 'Release proof reuses an artifact path')
    final_names = set(names + previous + [publication['release_record'], publication['release_signature']])
    root = Path(artifact_root).resolve()
    require(final_names == {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
            and not any(p.is_symlink() for p in root.rglob('*')), 'Publication contains unlisted files or links')
    files = {name: safe_file(artifact_root, name) for name in names}
    record_path = release_inputs[0] if release_inputs else safe_file(artifact_root, publication['release_record'])
    record = load_json(record_path)
    fields(record, {'schema_version', 'plan_sha256', 'artifacts'})
    require(record['schema_version'] == 1 and record['plan_sha256'] == plan['plan_sha256']
            and set(record['artifacts']) == set(files), 'Release record differs from the accepted publication')
    artifacts = {name: sha256_file(path) for name, path in files.items()}
    require(artifacts == record['artifacts'], 'Final publication file hashes differ from the release record')
    factory = keys.inventory['factory']
    allowed = keys.file(factory['allowed_signers'])
    release_signature = release_inputs[1] if release_inputs else safe_file(artifact_root, publication['release_signature'])
    tools.ssh_signature(record_path, release_signature,
                        allowed, factory['identity'], policy['record_namespace'])
    tools.ssh_signature(files[publication['factory_archive']], files[publication['factory_signature']],
                        allowed, factory['identity'], policy['factory_namespace'])
    require(publication['factory_signature'] != publication['release_signature'], 'Signature reused across roles')
    signed = files[publication['signed_target_files']]
    packages = verify_packages(signed, plan, tools, keys)
    coverage = {'apk': sum(role['kind'] == 'apk' for role in plan['packages'].values()),
                'apex': sum((2 if name.endswith('.capex') else 1) for name, role in plan['packages'].items() if role['kind'] == 'apex'),
                'payload': sum(role['kind'] == 'apex' for role in plan['packages'].values())}
    require(isinstance(packages, dict) and packages == coverage and sum(coverage.values()) > 0,
            'Package verification coverage is incomplete')
    mac = signing_mac.verify_mac_permissions(signed, plan['mac_permissions'])
    partitions = publication_images(files[publication['images_archive']], signed, plan, tools, keys)
    verify_factory_contents(files[publication['factory_archive']], files[publication['images_archive']])
    full = False
    source_artifacts = {}
    for ota in publication['ota']:
        path = files[ota['file']]
        tools.ota(path, keys.cert('releasekey', ''), plan['provenance'])
        with za.archive(path) as handle:
            metadata = za.properties(za.read_text(handle, 'META-INF/com/android/metadata'))
            payload = za.extract(handle, 'payload.bin', tools.scratch / ('payload-' + str(len(previous)) + '.bin'))
        source = None
        old = ota['source_target_files']
        if old is not None:
            source = safe_file(artifact_root, old)
            digest = sha256_file(source)
            require(plan['previous_artifacts'].get(old) == digest, 'Delta source lacks a verified signing result')
            source_artifacts[old] = digest
            identity = tools.target_identity(source)
            require(metadata.get('pre-build') == identity['fingerprint']
                    and metadata.get('pre-build-incremental') == identity['incremental'],
                    'Delta source-build metadata differs from authenticated signed base')
        else:
            require('pre-build' not in metadata, 'OTA is incremental without a verified source')
            full = True
        tools.apply_payload(payload, signed, source)
        payload.unlink()
    require(full, 'Publication lacks a full OTA')
    require(artifacts == {name: sha256_file(path) for name, path in files.items()}, 'Artifacts changed during verification')
    require(source_artifacts == {name: sha256_file(safe_file(artifact_root, name))
                                 for name in source_artifacts}, 'Delta source changed during verification')
    return {'schema_version': 2, 'status': 'PASS', 'artifact_signatures_verified': True,
            'mode': plan['mode'], 'plan_sha256': plan['plan_sha256'], 'provenance': plan['provenance'],
            'changed_projects': plan['changed_projects'], 'public_identities': plan['public_identities'],
            'artifacts': artifacts, 'packages': packages, 'mac_permissions': mac,
            'partitions': partitions, 'ota_count': len(publication['ota'])}


def parser():
    root = argparse.ArgumentParser(prog='diamaneos signing', description='Verified target-files plans and native publication signature checks')
    root.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('roles', help='validate the stable role policy')
    prepare = commands.add_parser('prepare', help='prepare a plan from a verified build')
    for name in ('build-record', 'verification-report', 'target-files', 'resolved-manifest'):
        prepare.add_argument('--' + name, type=Path, required=True)
    prepare.add_argument('--previous-result', type=Path)
    prepare.add_argument('--previous-signature', type=Path)
    prepare.add_argument('--verification-signature', type=Path, required=True)
    prepare.add_argument('--qualification', action='store_true')
    output = commands.add_parser('command', help='print native signing arguments for the accepted input')
    for name in ('plan', 'target-files', 'signed-target-files', 'key-dir', 'source-root'):
        output.add_argument('--' + name, type=Path, required=True)
    output.add_argument('--signer', type=Path, required=True)
    output.add_argument('--qualification', action='store_true')
    verify = commands.add_parser('verify', help='independently verify all publication signatures')
    for name in ('plan', 'publication', 'artifact-root'):
        verify.add_argument('--' + name, type=Path, required=True)
    verify.add_argument('--qualification', action='store_true')
    for sub in (prepare, verify):
        for name in ('source-root', 'otatools', 'keys', 'scratch', 'output'):
            sub.add_argument('--' + name, type=Path, required=True)
    return root


def execute(args):
    policy = load_json(args.config)
    validate_config(policy)
    if args.command == 'roles':
        print('Signing role policy: PASS')
        return 0
    if args.command == 'command':
        plan = load_json(args.plan)
        validate_plan(plan, policy)
        require(plan['mode'] == ('qualification' if args.qualification else 'production'), 'Signing mode differs from the plan')
        command = plans.signing_command(plan, signer=args.signer, key_dir=args.key_dir,
                    source=args.target_files, destination=args.signed_target_files, source_root=args.source_root)
        print('(cd -- ' + shlex.quote(str(args.source_root.resolve())) + ' && exec ' + shlex.join(command) + ')')
        return 0
    require(not args.output.exists(), 'Output already exists')
    args.scratch.mkdir(parents=True, exist_ok=True)
    paths = [args.config, args.keys, args.otatools]
    if args.command == 'prepare':
        paths += [args.build_record, args.verification_report, args.verification_signature,
                  args.target_files, args.resolved_manifest]
        if args.previous_result:
            require(args.previous_signature is not None, 'Previous result requires its external signature')
            paths.append(args.previous_result)
            paths.append(args.previous_signature)
        else:
            require(args.previous_signature is None, 'Previous signature has no result')
    else:
        paths += [args.plan, args.publication]
    before = input_hashes(paths)
    if args.command == 'prepare':
        authority, identity = factory_authority(args.keys, policy)
        before.update(input_hashes((authority,)))
        host_ssh_signature(args.verification_report, args.verification_signature,
                           authority, identity, policy['verification_namespace'], args.scratch)
        if args.previous_result:
            host_ssh_signature(args.previous_result, args.previous_signature,
                               authority, identity, policy['result_namespace'], args.scratch)
        recheck_hashes(before)
        recheck_stamps(args.metadata_stamps)
        production = not args.qualification
        provenance = verified_build(args.build_record, args.verification_report, args.target_files,
                                    args.resolved_manifest, production=production,
                                    source_root=args.source_root, otatools=args.otatools)
        source_files(args.source_root, provenance['projects'])
    else:
        plan = load_json(args.plan)
        validate_plan(plan, policy)
        require(plan['mode'] == ('qualification' if args.qualification else 'production'), 'Verification mode differs from the plan')
        production = plan['mode'] == 'production'
        require(not args.keys.resolve().is_relative_to(args.artifact_root.resolve()),
                'Expected public keys must be outside the artifacts')
        require(not args.output.resolve().is_relative_to(args.artifact_root.resolve())
                and not args.scratch.resolve().is_relative_to(args.artifact_root.resolve()),
                'Verification output and scratch must be outside the publication')
        publication = load_json(args.publication)
        fields(publication, {'schema_version', 'signed_target_files', 'images_archive', 'ota',
                             'factory_archive', 'factory_signature', 'release_record', 'release_signature'})
        record_path, signature = args.release_inputs
        authority, identity = factory_authority(args.keys, policy)
        require(not authority.resolve().is_relative_to(args.artifact_root.resolve()),
                'Expected public authority must be outside the artifacts')
        before.update(input_hashes((authority, record_path, signature)))
        host_ssh_signature(record_path, signature, authority, identity, policy['record_namespace'], args.scratch)
        record = load_json(record_path)
        fields(record, {'schema_version', 'plan_sha256', 'artifacts'})
        require(record['schema_version'] == 1 and record['plan_sha256'] == plan['plan_sha256'],
                'Release record does not authenticate the signing plan')
        recheck_hashes(before)
        recheck_stamps(args.metadata_stamps)
        require(sha256_file(args.otatools) == plan['provenance']['otatools_sha256'], 'Otatools archive changed')
        source_files(args.source_root, plan['provenance']['projects'])
    provenance = provenance if args.command == 'prepare' else plan['provenance']
    with native_inputs(args.otatools, args.source_root, args.keys, args.scratch, production,
                       provenance['projects'], policy, getattr(args, 'artifact_root', None),
                       expected_otatools=provenance['otatools_sha256']) as (tools, keys):
        if args.command == 'prepare':
            previous = load_json(args.previous_result) if args.previous_result else None
            result = plans.prepare(args.build_record, args.verification_report, args.target_files,
                    args.resolved_manifest, policy, keys, tools, tools.scratch / 'otatools.zip', previous, production=production)
            for changed in result['changed_projects']:
                print('Signing source change: ' + changed['project'] + ': ' +
                      (changed['previous'] or 'first run') + ' -> ' + (changed['current'] or 'removed'))
        else:
            result = verify_publication(plan, publication, args.artifact_root, policy, tools, keys,
                                        release_inputs=args.release_inputs)
    recheck_hashes(before)
    recheck_stamps(args.metadata_stamps)
    require(source_files(args.source_root, provenance['projects']) == result['provenance']['source_files'],
            'Signing source changed during processing')
    write_json(args.output, result)
    print('Signing ' + args.command + ': PASS')
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with metadata_inputs(args) as frozen:
            return execute(frozen)
    except (SigningError, build.BuildError, OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError) as error:
        print('ERROR: ' + (str(error) if isinstance(error, (SigningError, build.BuildError)) else 'Invalid signing input'), file=sys.stderr)
        diagnostic = getattr(error, 'diagnostic', '')
        if diagnostic and not any(marker in diagnostic for marker in ('PRIVATE KEY', 'AGE-SECRET-KEY-', 'password=', 'password:')):
            print(diagnostic, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
