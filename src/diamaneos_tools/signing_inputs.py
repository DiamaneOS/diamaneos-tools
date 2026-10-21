"""Public signing policy and bounded, independently supplied run inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from . import build, build_workspace as bw, image_verify, process, safe_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'config/signing-roles.json'
CERT_ROLES = {'releasekey', 'platform', 'shared', 'media', 'networkstack',
              'bluetooth', 'sdk_sandbox', 'gmscompat_lib', 'nfc'}
PROJECTS = {'script', 'build/make', 'development', 'external/avb',
            'system/update_engine', 'tools/apksig', 'system/apex', 'tools/diamaneos'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')
NAME = re.compile(r'^[A-Za-z0-9._+-]{1,256}$')
MAX_JSON = 16 * 1024 * 1024
# Seconds before a native tool is stopped.
NATIVE_TIMEOUT = 1200
# Applying an incremental onto its source partitions takes longer. A whole verify with one incremental took
# 1794 s on a 48-thread builder, and six times that leaves room for a laptop with 8 threads and 7.6 GB RAM.
DELTA_APPLY_TIMEOUT = 10800
# release: a DiamaneOS release, with the pinned release keys. production: a lockable
# set with any private keys. qualification: a set that is never locked.
MODES = ('release', 'production', 'qualification')


class SigningError(ValueError):
    """A signing input or native verification failed."""


def require(condition, message):
    if not condition:
        raise SigningError(message)


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected), 'Unsupported signing fields')


def load_json(path, limit=MAX_JSON):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, 'Signing document exceeds its limit')
        value = safe_json.loads(raw)
        stack = [(value, 0)]
        nodes = 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            require(depth < 32 and nodes < 250_000, 'Signing document exceeds structural limits')
            if isinstance(item, dict):
                require(not any(re.sub('[^a-z]', '', key.lower()) in
                        {'privatekey', 'password', 'pin', 'secret', 'seed', 'mnemonic'} for key in item),
                        'Secret fields are prohibited')
                stack.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list):
                stack.extend((v, depth + 1) for v in item)
            elif isinstance(item, str):
                require('PRIVATE KEY-----' not in item and 'AGE-SECRET-KEY-' not in item,
                        'Private key material is prohibited')
        return value
    except (OSError, ValueError, RecursionError) as error:
        if isinstance(error, SigningError):
            raise
        raise SigningError('Unable to read unique-key signing JSON') from None


def sha256_file(path):
    return build.sha256_file(Path(path))


def sha256_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def file_stamp(info, *, ownership=False):
    fields = ('dev', 'ino', 'mode', 'size', 'mtime_ns', 'ctime_ns')
    if ownership:
        fields += ('uid', 'gid', 'nlink')
    return tuple(getattr(info, 'st_' + field) for field in fields)


def snapshot_file(path, destination, limit=MAX_JSON, *, stamps=None):
    """Copy one bounded regular input through a protected descriptor, never a path reopen."""
    path, destination = Path(path), Path(destination)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
            'Unsafe or oversized signing snapshot input')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
        require(file_stamp(before, ownership=True) == file_stamp(os.fstat(source.fileno()), ownership=True),
                'Signing snapshot input changed')
        data = source.read(limit + 1)
        require(len(data) == before.st_size and len(data) <= limit
                and file_stamp(before, ownership=True) == file_stamp(os.fstat(source.fileno()), ownership=True)
                == file_stamp(path.lstat(), ownership=True), 'Signing snapshot input changed')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open('xb') as output: output.write(data)
    destination.chmod(0o400)
    if stamps is not None: stamps[str(path)] = file_stamp(before, ownership=True)
    return destination


def recheck_stamps(expected):
    require(all(file_stamp(Path(path).lstat(), ownership=True) == stamp for path, stamp in expected.items()),
            'Signing metadata paths changed while they were being processed')


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def input_hashes(paths):
    return {str(Path(path).resolve()): sha256_file(path) for path in paths}


def recheck_hashes(expected):
    require(all(sha256_file(path) == digest for path, digest in expected.items()),
            'Signing inputs changed while they were being processed')


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')


def safe_file(root, relative):
    require(isinstance(relative, str) and relative, 'Missing relative artifact path')
    part = PurePosixPath(relative)
    require(not part.is_absolute() and '..' not in part.parts and str(part) == relative
            and '\\' not in relative, 'Unsafe artifact path')
    root = Path(root).resolve()
    path = root / relative
    components = [path, *list(path.parents)[:len(part.parts) - 1]]
    require(path.is_file() and not any(p.is_symlink() for p in components)
            and path.resolve().is_relative_to(root), 'Artifact is missing or symlinked')
    return path


def command(argv, *, env=None, cwd=None, data=None, timeout=NATIVE_TIMEOUT):
    result = process.run(list(map(str, argv)), timeout, 4 * 1024 * 1024,
                         env=env, cwd=cwd, input_data=data)
    if result['transport'] != 'ok':
        error = SigningError('Native tool failed: ' + Path(str(argv[0])).name)
        error.diagnostic = result['stderr'][-4000:].decode('utf-8', 'replace')
        raise error
    return result['stdout']


def validate_config(config):
    require(isinstance(config, dict), 'Signing policy must be a JSON object')
    errors = safe_json.schema_errors(config, 'signing-roles.schema.json')
    require(not errors, errors[0] if errors else 'Signing policy schema failed')
    require(set(config['certificate_roles']) == CERT_ROLES, 'Unknown or missing certificate role')
    require(config['apk_selectors'].get('testkey') == 'releasekey', 'Invalid default certificate mapping')
    require(set(config['apk_selectors'].values()) <= CERT_ROLES, 'Unknown destination role')
    # Policy contains algorithms and role names, never release-specific provenance.
    require(not re.search(r'(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])|\b20[0-9]{8}\b',
                          json.dumps(config)), 'Signing policy contains a revision or release pin')
    rules = config['presigned_packages']
    identities = [(rule['parent_apex'], rule['artifact_basename']) for rule in rules]
    require(len(identities) == len(set(identities)), 'Duplicate presigned package policy')
    require(len({rule['metadata_name'] for rule in rules}) == len(rules),
            'Duplicate presigned metadata policy')
    apexes = config.get('presigned_apexes', [])
    require(len({r['metadata_name'] for r in apexes}) == len(apexes)
            and len({r['artifact_path'] for r in apexes}) == len(apexes), 'Duplicate presigned APEX policy')
    require(all(Path(r['artifact_path']).name == r['metadata_name'] for r in apexes),
            'Presigned APEX metadata and artifact identity differ')
    require(not {r['metadata_name'] for r in apexes} & {r['metadata_name'] for r in rules},
            'Presigned APK/APEX policy identities overlap')


def presigned_apex_rule(policy, logical):
    matches = [r for r in policy.get('presigned_apexes', []) if r['artifact_path'] == logical]
    require(len(matches) <= 1, 'Ambiguous presigned APEX policy')
    return matches[0] if matches else None


def public_inventory_fields(inventory):
    names = {'schema_version', 'certificate_roles', 'avb_public_key', 'factory'}
    require(isinstance(inventory, dict) and set(inventory) in
            (names, names | {'presigned_apex_payload_keys'}), 'Unknown public inventory fields')
    require(type(inventory['schema_version']) is int and inventory['schema_version'] == 2,
            'Unknown public key inventory format')
    require(isinstance(inventory.get('presigned_apex_payload_keys', {}), dict),
            'Malformed presigned APEX public authority inventory')


def presigned_rule(policy, logical):
    parent, separator, inner = logical.partition('!/')
    apex = Path(parent).name.removesuffix('.capex') + ('.apex' if parent.endswith('.capex') else '') if separator else None
    name = Path(inner if separator else logical).name
    matches = [rule for rule in policy['presigned_packages']
               if rule['parent_apex'] == apex and rule['artifact_basename'] == name]
    require(len(matches) <= 1, 'Ambiguous presigned package policy')
    return matches[0] if matches else None


def verified_build(record_path, report_path, target_files, resolved_manifest, *, mode, source_root, otatools=None):
    record = load_json(record_path)
    require(isinstance(record, dict), 'Build record must be a JSON object')
    root = Path(record_path).parent
    sums = safe_file(root, 'SHA256SUMS')
    report = load_json(report_path)
    require(isinstance(report, dict), 'Build verification report must be a JSON object')
    problem = image_verify.report_problem(report, record.get('build_id'), sha256_file(sums))
    require(not problem, problem or 'Build verification failed')
    require(report.get('build_identity') == record.get('build_identity')
            and isinstance(record.get('build_identity'), str) and SHA256.fullmatch(record['build_identity']),
            'Build verification identity mismatch')
    pairs = [line.split('  ', 1) for line in sums.read_text().splitlines()]
    require(all(len(pair) == 2 and SHA256.fullmatch(pair[0]) for pair in pairs), 'Invalid build checksums')
    checksums = {name: digest for digest, name in pairs}
    require(len(checksums) == len(pairs), 'Duplicate build checksum name')
    target = record.get('target_files', {})
    manifest = record.get('manifest', {})
    require(isinstance(target, dict) and isinstance(manifest, dict), 'Build archive records must be objects')
    require(checksums.get(Path(record_path).name) == sha256_file(record_path), 'Build record checksum mismatch')
    require(sha256_file(target_files) == target.get('sha256') == checksums.get(target.get('file')),
            'Target-files does not match the build that passed build verify')
    require(sha256_file(resolved_manifest) == manifest.get('resolved_sha256') == checksums.get(manifest.get('file')),
            'Resolved manifest does not match the verified build')
    tools = record.get('otatools') or {}
    require(isinstance(tools, dict), 'Build tool archive record must be an object')
    require(otatools is not None and sha256_file(otatools) == tools.get('sha256') == checksums.get(tools.get('file')),
            'Otatools does not match the build that passed build verify')
    modified, deviations = record.get('modified'), (record.get('source') or {}).get('deviations') or []
    require(isinstance(modified, list) and isinstance(deviations, list)
            and all(isinstance(item, str) for item in modified + deviations), 'Invalid recorded source changes')
    # Own keys may sign a source with local changes, as GrapheneOS's builders change sources freely.
    # The plan and the factory record keep them.
    require(isinstance(record.get('tools'), dict) and record['tools'].get('clean') is True
            and not record.get('tools_skew') and (mode == 'production' or not modified and not deviations),
            'Signing requires a clean recorded build')
    if mode == 'release':
        require(record.get('variant') == 'user' and record.get('official') is True,
                'Release signing requires an official user build')
    rows, _ = build.parse_project_map(Path(resolved_manifest).read_bytes())
    revisions = {path: revision for path, _name, _remote, revision in rows}
    require(PROJECTS <= set(revisions), 'Resolved manifest lacks signing projects')
    revisions = {path: revision for path, revision in revisions.items()
                 if path in PROJECTS or path.startswith('prebuilts/jdk/')}
    require(any(path.startswith('prebuilts/jdk/') for path in revisions), 'Resolved manifest lacks a JDK')
    verifier = Path(source_root) / 'tools/diamaneos'
    require(git_read(verifier, ['rev-parse', 'HEAD']).decode().strip() == revisions['tools/diamaneos']
            and not git_read(verifier, ['status', '--porcelain', '--untracked-files=all']),
            'Build verifier source differs from the clean resolved revision')
    rules = load_json(safe_file(verifier, 'config/fp6-image-checks.json'))
    require(report.get('rule_set_sha256') == bw.digest(rules), 'Build verification device rules differ or lack a binding')
    require(isinstance(rules, dict) and rules.get('schema_version') == 1
            and isinstance(rules.get('rules'), list) and rules['rules'], 'Invalid resolved device rule set')
    ids = []
    for rule in rules['rules']:
        require(isinstance(rule, dict) and isinstance(rule.get('id'), str)
                and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', rule['id'])
                and ('official' not in rule or isinstance(rule['official'], bool))
                and ('variants' not in rule or isinstance(rule['variants'], list)
                     and set(rule['variants']) <= {'user', 'userdebug', 'eng'}), 'Invalid resolved device rule')
        ids.append(rule['id'])
    require(len(ids) == len(set(ids)), 'Duplicate resolved device rule')
    # The resolved verifier implements rule types. Signing requires their complete PASS coverage.
    required = set(image_verify.REQUIRED_CHECKS) | {
        rule['id'] for rule in rules['rules']
        if image_verify.rule_applies(rule, record.get('variant'), record.get('official') is True)}
    require(required <= {check['id'] for check in report['checks']}, 'Build verification lacks applicable device checks')
    return {'record_sha256': sha256_file(record_path), 'verification_sha256': sha256_file(report_path),
            'manifest_sha256': sha256_file(resolved_manifest), 'projects': revisions,
            'otatools_sha256': tools['sha256'],
            'target_files_sha256': target['sha256'], 'build_id': record['build_id'],
            'verification_rules_sha256': report['rule_set_sha256'],
            'build_identity': record['build_identity'], 'product': record['product'],
            'build_number': record['build_number'], 'build_datetime': record['build_datetime'],
            'modified': sorted(modified), 'deviations': list(deviations)}


def git_read(root, arguments, *, data=None):
    return command(['git', '--no-replace-objects', '--literal-pathspecs', '-c', 'core.fsmonitor=false',
                    '-c', 'core.hooksPath=/dev/null', '-C', root, *arguments],
                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': '/dev/null',
                        'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}, data=data)


def clean_source_projects(source, projects):
    for path, revision in projects.items():
        require(isinstance(path, str) and not PurePosixPath(path).is_absolute()
                and '..' not in PurePosixPath(path).parts and str(PurePosixPath(path)) == path
                and '\\' not in path and isinstance(revision, str)
                and re.fullmatch('[0-9a-f]{40}', revision), 'Invalid selected source project')
        head = git_read(source / path, ['rev-parse', 'HEAD']).decode().strip()
        require(head == revision, 'Signing source differs from the resolved manifest: ' + path)
        require(not git_read(source / path, ['status', '--porcelain', '--untracked-files=all']),
                'Signing source has unrecorded changes: ' + path)


def source_files(source, projects):
    """Bind consumed bytes to selected Git blobs, regardless of index stat flags."""
    source = Path(source).resolve()
    clean_source_projects(source, projects)
    paths = {'script/generate-release.sh', 'script/generate-delta.sh', 'script/common.sh',
             'external/avb/avbtool.py',
             'build/make/tools/releasetools/check_ota_package_signature.py',
             'tools/diamaneos/src/diamaneos_tools/flash_all.py',
             'tools/diamaneos/config/fp6-image-checks.json'}
    trees = {}
    for project, revision in projects.items():
        wanted = [name[len(project) + 1:] for name in paths if name.startswith(project + '/')]
        prefix = ('linux-x86' if project.startswith('prebuilts/jdk/') else
                  'target/product/security' if project == 'build/make' else
                  'test/data' if project == 'external/avb' else None)
        if prefix:
            wanted.append(prefix)
        if project == 'system/apex':
            wanted.append('.')
        if not wanted:
            continue
        raw = git_read(source / project, ['ls-tree', '-r', '-z', revision, '--', *wanted])
        for row in raw.split(b'\0'):
            if not row:
                continue
            header, name = row.decode('utf-8').split('\t', 1)
            mode, kind, blob = header.split()
            require(kind == 'blob' and mode in {'100644', '100755', '120000'}
                    and re.fullmatch('[0-9a-f]{40}', blob), 'Unsupported source Git object')
            logical = project + '/' + name
            trees[logical] = (mode, blob)
            if (project.startswith('prebuilts/jdk/') and name.startswith('linux-x86/')
                    or project == 'build/make' and name.startswith('target/product/security/') and name.endswith('.x509.pem')
                    or project == 'system/apex' and name.endswith('.x509.pem')
                    or project == 'external/avb' and name.startswith('test/data/testkey_rsa') and name.endswith('.pem')):
                paths.add(logical)
        # Include ignored/untracked runtime inputs too, so none can bypass Git binding.
        root = source / project
        candidates = ((root / 'linux-x86').rglob('*') if project.startswith('prebuilts/jdk/') else
                      (root / 'target/product/security').glob('*.x509.pem') if project == 'build/make' else
                      root.rglob('*.x509.pem') if project == 'system/apex' else
                      (root / 'test/data').glob('testkey_rsa*.pem') if project == 'external/avb' else ())
        paths.update(str(path.relative_to(source)) for path in candidates if path.is_file() or path.is_symlink())
    result, snapshots, active = {}, {}, set()

    def check(logical):
        if logical in result:
            return
        require(logical not in active, 'Source symlink cycle')
        active.add(logical)
        owners = [name for name in projects if logical.startswith(name + '/')]
        require(owners, 'Consumed source file has no selected project')
        owner = max(owners, key=len)
        if logical not in trees:
            relative = logical[len(owner) + 1:]
            row = git_read(source / owner, ['ls-tree', '-z', projects[owner], '--', relative]).rstrip(b'\0')
            require(row and b'\0' not in row, 'Consumed source file is absent from selected Git tree')
            header, name = row.decode('utf-8').split('\t', 1)
            mode, kind, blob = header.split()
            require(name == relative and kind == 'blob' and mode in {'100644', '100755', '120000'}
                    and re.fullmatch('[0-9a-f]{40}', blob), 'Unsupported consumed source Git object')
            trees[logical] = (mode, blob)
        mode, expected = trees[logical]
        if mode == '120000':
            path = source / logical
            # Authenticate a declared link and its regular target in selected source.
            require(not any(parent.is_symlink() for parent in path.parents if parent != source
                            and parent.is_relative_to(source)), 'Source link has a symlink ancestor')
            before = path.lstat()
            require(stat.S_ISLNK(before.st_mode), 'Source file type differs from Git')
            text = os.fsencode(os.readlink(path))
            actual = git_read(source / owner, ['hash-object', '--stdin', '--no-filters'], data=text).decode().strip()
            target = Path(os.path.abspath(path.parent / os.fsdecode(text)))
            require(target.is_relative_to(source), 'Source link leaves selected files')
            check(str(target.relative_to(source)))
            require(file_stamp(before) == file_stamp(path.lstat()) and text == os.fsencode(os.readlink(path)),
                    'Source link changed during verification')
            value = hashlib.sha256(text).hexdigest()
        else:
            path = safe_file(source, logical)
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_size <= 1024 ** 3,
                        'Consumed source file is not bounded and regular')
                value = sha256_stream(stream)
                actual = git_read(source / owner, ['hash-object', '--no-filters', '--', path]).decode().strip()
                stream.seek(0)
                require(value == sha256_stream(stream) and file_stamp(before) == file_stamp(os.fstat(stream.fileno()))
                        == file_stamp(safe_file(source, logical).stat()), 'Source bytes changed during verification')
        require(actual == expected, 'Consumed source bytes differ from selected Git blob: ' + logical)
        result[logical] = value
        snapshots[logical] = file_stamp(path.lstat())
        active.remove(logical)

    for logical in sorted(paths):
        check(logical)
    clean_source_projects(source, projects)
    for logical, value in result.items():
        path = source / logical
        closing = (hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest() if trees[logical][0] == '120000'
                   else sha256_file(safe_file(source, logical)))
        require(closing == value and file_stamp(path.lstat()) == snapshots[logical],
                'Consumed source changed before closing verification')
    return result


def changed_projects(projects, previous=None, *, mode='qualification'):
    if previous is None:
        old = {}
    else:
        require(isinstance(previous, dict), 'Previous signing result must be a JSON object')
        require(previous.get('status') == 'PASS' and previous.get('artifact_signatures_verified') is True,
                'Previous signing result did not verify artifacts')
        require(previous.get('mode') in MODES and mode in (previous['mode'], 'qualification'),
                'Previous signing result mode is incompatible')
        require(isinstance(previous.get('provenance'), dict)
                and isinstance(previous['provenance'].get('projects'), dict)
                and isinstance(previous.get('artifacts'), dict), 'Previous signing result is incomplete')
        old = previous['provenance']['projects']
    return [{'project': path, 'previous': old.get(path), 'current': projects.get(path)}
            for path in sorted(set(old) | set(projects)) if old.get(path) != projects.get(path)]
