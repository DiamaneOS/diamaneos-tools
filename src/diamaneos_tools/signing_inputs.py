"""Public signing policy and bounded, independently supplied run inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from . import build, image_verify, process

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'config/signing-roles.json'
CERT_ROLES = {'releasekey', 'platform', 'shared', 'media', 'networkstack',
              'bluetooth', 'sdk_sandbox', 'gmscompat_lib', 'nfc'}
PROJECTS = {'script', 'build/make', 'development', 'external/avb',
            'system/update_engine', 'tools/apksig', 'system/apex'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')
NAME = re.compile(r'^[A-Za-z0-9._+-]{1,256}$')
MAX_JSON = 16 * 1024 * 1024


class SigningError(ValueError):
    """A signing input or native verification failed."""


def require(condition, message):
    if not condition:
        raise SigningError(message)


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected), 'Unsupported signing fields')


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def load_json(path, limit=MAX_JSON):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, 'Signing document exceeds its limit')
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: require(False, 'Non-finite JSON number'))
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


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


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


def command(argv, *, env=None, cwd=None, data=None):
    result = process.run(list(map(str, argv)), 1200, 4 * 1024 * 1024,
                         env=env, cwd=cwd, input_data=data)
    if result['transport'] != 'ok':
        error = SigningError('Native tool failed: ' + Path(str(argv[0])).name)
        error.diagnostic = result['stderr'][-4000:].decode('utf-8', 'replace')
        raise error
    return result['stdout']


def validate_config(config):
    from jsonschema import Draft7Validator
    schema = load_json(ROOT / 'schemas/signing-roles.schema.json')
    require(Draft7Validator(schema).is_valid(config), 'Signing policy schema failed')
    require(set(config['certificate_roles']) == CERT_ROLES, 'Unknown or missing certificate role')
    require(config['apk_selectors'].get('testkey') == 'releasekey', 'Invalid default certificate mapping')
    require(set(config['apk_selectors'].values()) <= CERT_ROLES, 'Unknown destination role')
    # Policy contains algorithms and role names, never release-specific provenance.
    require(not re.search(r'(?<![0-9a-f])[0-9a-f]{40,64}(?![0-9a-f])|\b20[0-9]{8}\b',
                          json.dumps(config)), 'Signing policy contains a revision or release pin')


def verified_build(record_path, report_path, target_files, resolved_manifest, *, production, otatools=None):
    record = load_json(record_path)
    root = Path(record_path).parent
    sums = safe_file(root, 'SHA256SUMS')
    report = load_json(report_path)
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
    require(checksums.get(Path(record_path).name) == sha256_file(record_path), 'Build record checksum mismatch')
    require(sha256_file(target_files) == target.get('sha256') == checksums.get(target.get('file')),
            'Target-files does not match the build that passed build verify')
    require(sha256_file(resolved_manifest) == manifest.get('resolved_sha256') == checksums.get(manifest.get('file')),
            'Resolved manifest does not match the verified build')
    tools = record.get('otatools') or {}
    require(otatools is not None and sha256_file(otatools) == tools.get('sha256') == checksums.get(tools.get('file')),
            'Otatools does not match the build that passed build verify')
    require(record.get('modified') == [] and record.get('tools', {}).get('clean') is True,
            'Signing requires a clean recorded build')
    if production:
        require(record.get('variant') == 'user' and record.get('official') is True,
                'Production signing requires an official user build')
    rows, _ = build.parse_project_map(Path(resolved_manifest).read_bytes())
    revisions = {path: revision for path, _name, _remote, revision in rows}
    require(PROJECTS <= set(revisions), 'Resolved manifest lacks signing projects')
    revisions = {path: revision for path, revision in revisions.items()
                 if path in PROJECTS or path.startswith('prebuilts/jdk/')}
    require(any(path.startswith('prebuilts/jdk/') for path in revisions), 'Resolved manifest lacks a JDK')
    return {'record_sha256': sha256_file(record_path), 'verification_sha256': sha256_file(report_path),
            'manifest_sha256': sha256_file(resolved_manifest), 'projects': revisions,
            'otatools_sha256': tools['sha256'],
            'target_files_sha256': target['sha256'], 'build_id': record['build_id'],
            'build_identity': record['build_identity'], 'product': record['product'],
            'build_number': record['build_number'], 'build_datetime': record['build_datetime']}


def source_files(source, projects):
    source = Path(source)
    for path, revision in projects.items():
        head = command(['git', '-C', source / path, 'rev-parse', 'HEAD']).decode().strip()
        require(head == revision, 'Signing source differs from the resolved manifest: ' + path)
        require(not command(['git', '-C', source / path, 'status', '--porcelain', '--untracked-files=all']),
                'Signing source has unrecorded changes: ' + path)
    paths = ['script/generate-release.sh', 'script/generate-delta.sh', 'script/common.sh',
             'build/make/tools/releasetools/check_ota_package_signature.py']
    return {path: sha256_file(safe_file(source, path)) for path in paths}


def changed_projects(projects, previous=None):
    if previous is None:
        old = {}
    else:
        require(previous.get('status') == 'PASS' and previous.get('artifact_signatures_verified') is True,
                'Previous signing result did not verify artifacts')
        old = previous['provenance']['projects']
    return [{'project': path, 'previous': old.get(path), 'current': projects.get(path)}
            for path in sorted(set(old) | set(projects)) if old.get(path) != projects.get(path)]
