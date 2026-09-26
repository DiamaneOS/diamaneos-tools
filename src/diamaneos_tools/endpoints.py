"""Read-only endpoint design validation. No network, provider access or deployment.

The JSON Schemas define shape/type/bounds. Additional checks enforce coverage,
references and authority separation across the two independently cloned repos.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None

ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 262144
MAX_DEPTH = 24
MAX_NODES = 16000
MAX_ERRORS = 20
APPENDIX_B = {
    'os-updates': 'releases.grapheneos.org',
    'apps-catalog': 'apps.grapheneos.org',
    'browser-component-check': 'update.vanadium.app',
    'browser-component-download': 'dl.vanadium.app',
    'time': 'time.grapheneos.org',
    'connectivity-check': 'connectivitycheck.grapheneos.network',
    'online-probe': 'grapheneos.online',
    'psds-cache': 'qualcomm.psds.grapheneos.org',
    'supl': 'supl.grapheneos.org',
    'rkp-proxy': 'remoteprovisioning.grapheneos.org',
    'widevine-proxy': 'widevineprovisioning.grapheneos.org',
    'ct-mirror': 'gstatic.grapheneos.org',
    'dns-check': 'dnscheck.grapheneos.org',
    'info-release-feed': 'grapheneos.org/releases.atom',
    'network-location': 'gs-loc.apple.grapheneos.org',
    'geocoder': 'nominatim.grapheneos.org',
    'attestation': 'attestation.app',
}
PROVIDER_LAYERS = {'registrar', 'authoritative-dns', 'vps-primary',
                   'artifact-mirror-non-eu', 'git-primary', 'git-backup',
                   'git-mirror', 'email', 'monitoring', 'cdn', 'object-storage'}
PUBLIC_MIRRORS = {'os-updates', 'apps-catalog', 'info-release-feed'}
FORBIDDEN_KEYS = {'password', 'privatekey', 'token', 'secret', 'seed',
                  'mnemonic', 'imei', 'imsi', 'iccid', 'serial', 'custody'}


class ContractError(ValueError):
    """Safe diagnostic: never contains user input or private values."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ContractError('non-finite JSON number')


def _walk(value):
    """Bound traversal before jsonschema; also guard direct Python API inputs."""
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ContractError('document exceeds structural limits')
        if isinstance(item, (dict, list)) and len(item) > MAX_NODES:
            raise ContractError('document exceeds structural limits')
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ContractError('JSON object keys must be strings')
                normalized = re.sub(r'[-_]', '', key).lower()
                if normalized in FORBIDDEN_KEYS:
                    raise ContractError('private field is not allowed')
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif not isinstance(item, (str, int, float, bool, type(None))):
            raise ContractError('value is not JSON data')
        if isinstance(item, str):
            if len(item) > MAX_FILE_BYTES:
                raise ContractError('document exceeds string limit')
            if re.search(r'[a-z][a-z0-9+.-]*://[^\s/]*@', item, re.I):
                raise ContractError('credential-bearing URL is not allowed')
            if re.search(r'-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----', item):
                raise ContractError('private key material is not allowed')
        yield item


def _guard(value):
    for _ in _walk(value):
        pass
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8'))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('document is not finite UTF-8 JSON') from None
    if size > MAX_FILE_BYTES:
        raise ContractError('document exceeds byte limit')


def loads(data):
    if not isinstance(data, bytes):
        raise ContractError('JSON input must be bytes')
    if len(data) > MAX_FILE_BYTES:
        raise ContractError('document exceeds byte limit')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except ContractError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ContractError('invalid UTF-8 JSON') from None
    _guard(value)
    return value


def load_json(path):
    try:
        with Path(path).open('rb') as stream:
            return loads(stream.read(MAX_FILE_BYTES + 1))
    except OSError:
        raise ContractError('unable to read contract file') from None


def _schema_errors(value, filename):
    try:
        _guard(value)
    except ContractError as error:
        return [str(error)]
    if Draft7Validator is None:
        return ['missing jsonschema; install requirements-dev.txt in a virtual environment']
    schema = json.loads((ROOT / 'schemas' / filename).read_text(encoding='utf-8'))
    Draft7Validator.check_schema(schema)
    # Do not use jsonschema's message: it can echo an entire secret-bearing value.
    errors = []
    for error in Draft7Validator(schema).iter_errors(value):
        errors.append('schema constraint failed: ' + str(error.validator))
        if len(errors) == MAX_ERRORS:
            break
    return errors


def _duplicates(items, key):
    values = [item[key] for item in items]
    return len(values) != len(set(values))


def validate_inventory(inv):
    errors = _schema_errors(inv, 'endpoint-contract.schema.json')
    if errors:
        return errors
    for section, key in [('endpoints', 'id'), ('providers', 'layer'),
                         ('sources', 'id'), ('limits_profiles', 'id'),
                         ('client_defaults', 'name')]:
        if _duplicates(inv[section], key):
            errors.append('duplicate identifier in ' + section)
    if {e['id']: e['upstream_host'] for e in inv['endpoints']} != APPENDIX_B:
        errors.append('inherited endpoint identity/coverage mismatch')
    if {p['layer'] for p in inv['providers']} != PROVIDER_LAYERS:
        errors.append('provider layer coverage mismatch')
    sources = {s['id']: s for s in inv['sources']}
    limits = {p['id']: p for p in inv['limits_profiles']}
    blockers = {}
    for e in inv['endpoints']:
        if not set(e['source_refs']) <= sources.keys():
            errors.append('endpoint source reference is missing')
        if e['limits_profile'] not in limits:
            errors.append('endpoint limits profile is missing')
        replacement = e['replacement_host']
        if replacement != inv['canonical_domain'] and not replacement.endswith('.' + inv['canonical_domain']):
            errors.append('endpoint replacement is outside canonical domain')
        for b in e['implementation_blockers']:
            if b['id'] in blockers and blockers[b['id']] != b:
                errors.append('shared blocker has conflicting definitions')
            blockers[b['id']] = b
        if any(h['jurisdiction'] in ('US', 'CA') for h in e['upstreams']) and e['jurisdiction'] != 'eu-proxy-to-non-eu-upstream':
            errors.append('non-EU upstream disclosure is missing')
        if any(h['mode'] == 'device-default' for h in e['upstreams']) and e['id'] != 'dns-check':
            errors.append('unapproved direct-device upstream exception')
    for s in inv['sources']:
        if any(part in ('', '.', '..') for part in s['path'].split('/')):
            errors.append('source path must be repository-relative')
    for p in inv['limits_profiles']:
        if max(p['connect_seconds'], p['idle_seconds']) > p['deadline_seconds']:
            errors.append('connection/idle timeout exceeds total deadline')
        if p['cache_bytes'] and p['refresh_seconds'] > p['max_verified_age_seconds']:
            errors.append('cache refresh exceeds allowed age')
        if p['transport'] == 'dns' and p['cache_bytes']:
            errors.append('DNS records are not a project response cache')
    defaults = {d['name']: d for d in inv['client_defaults']}
    if 'private-dns' not in defaults or defaults['private-dns']['jurisdiction'] != 'non-eu-direct-exception':
        errors.append('Swiss resolver default exception is missing')
    if 'authenticated-time' not in defaults or defaults['authenticated-time']['jurisdiction'] != 'eu-primary':
        errors.append('time source jurisdiction must distinguish EU operators')
    return errors[:MAX_ERRORS]


def validate_services(inv, services):
    errors = validate_inventory(inv) + _schema_errors(services, 'services.schema.json')
    if errors:
        return errors[:MAX_ERRORS]
    if _duplicates(services['hosts'], 'id') or _duplicates(services['services'], 'id'):
        errors.append('duplicate host or service identifier')
    hosts = {h['id']: h for h in services['hosts']}
    providers = {p['layer']: p for p in inv['providers']}
    endpoints = {e['id']: e for e in inv['endpoints']}
    if Counter(h['role'] for h in hosts.values()) != Counter(['release','mirror','dns','community']):
        errors.append('exactly one release, mirror, DNS and conditional community role required')
    if len({h['credential_class'] for h in hosts.values()}) != len(hosts):
        errors.append('administration credentials must be distinct per host role')
    for h in hosts.values():
        if h['provider_layer'] not in providers:
            errors.append('host provider reference is missing')
        if h['role'] in ('release', 'mirror') and h['management'] != 'operator-vpn-and-provider-console':
            errors.append('release/mirror protected management path is missing')
        if h['role'] == 'community' and (h['management'] != 'separate-community-path' or h['status'] != 'conditional'):
            errors.append('community must remain conditional and isolated')
        required = {'release':('DE','vps-primary'), 'mirror':('IS','artifact-mirror-non-eu'), 'dns':('DE','authoritative-dns')}.get(h['role'])
        if required and (h['jurisdiction'],h['provider_layer']) != required:
            errors.append('selected host jurisdiction/provider role mismatch')
    roles = {h['role']: h for h in hosts.values()}
    if 'release' in roles and 'mirror' in roles:
        first = providers.get(roles['release']['provider_layer'])
        second = providers.get(roles['mirror']['provider_layer'])
        if first and second and first['candidate'] == second['candidate']:
            errors.append('mirror operator must be independent of primary')
    assigned = []
    for service in services['services']:
        h = hosts.get(service['host'])
        m = hosts.get(service['mirror']) if service['mirror'] else None
        if h is None:
            errors.append('service host reference is missing')
        if service['mirror'] and (m is None or m['role'] != 'mirror' or m['id'] == service['host']):
            errors.append('service mirror must resolve to the independent mirror role')
        for eid in service['endpoints']:
            assigned.append(eid)
            if eid not in endpoints:
                errors.append('service endpoint reference is missing')
                continue
            if service['owner_task'] != endpoints[eid]['owner_task']:
                errors.append('service and endpoint owner disagree')
            if h and h['role'] != ('dns' if eid == 'dns-check' else 'release'):
                errors.append('endpoint assigned to wrong authority role')
            if bool(service['mirror']) != (eid in PUBLIC_MIRRORS):
                errors.append('endpoint mirror selection does not match reviewed static scope')
    if Counter(assigned) != Counter(endpoints.keys()):
        errors.append('each endpoint must be assigned exactly once')
    return errors[:MAX_ERRORS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=ROOT/'config/endpoints.json')
    parser.add_argument('--services', type=Path, help='explicit infrastructure config/services.json; no implicit sibling lookup')
    args = parser.parse_args(argv)
    try:
        inv = load_json(args.inventory)
        if args.services:
            errors = validate_services(inv, load_json(args.services))
        else:
            errors = validate_inventory(inv)
    except ContractError as error:
        errors = [str(error)]
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    blockers = {b['id'] for e in inv['endpoints'] for b in e['implementation_blockers']}
    scope = 'inventory and explicit service selection' if args.services else 'inventory only; service integration not checked'
    print(f'VALID design: {len(inv["endpoints"])} endpoints; {len(blockers)} owned implementation gates; {scope}.')
    print('No service activation, native-client acceptance or deployment proof implied.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
