"""Check a generated kernel configuration against an explicit build-stage policy."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from . import components

MAX_CONFIG_BYTES = 2 * 1024 * 1024
SYMBOL = re.compile(r'CONFIG_[A-Za-z0-9_]+')
VALUE = re.compile(r'(?:y|m|n|-?[0-9]+|0x[0-9a-fA-F]+|"(?:[^"\\\n]|\\.)*")')


def parse_config(data):
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError('kernel configuration exceeds size limit')
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeError:
        raise ValueError('kernel configuration is not UTF-8') from None
    result = {}
    for line in lines:
        disabled = re.fullmatch(r'# (CONFIG_[A-Za-z0-9_]+) is not set', line)
        if disabled:
            key, value = disabled[1], 'n'
        elif line.startswith('CONFIG_'):
            key, separator, value = line.partition('=')
            if not separator or not SYMBOL.fullmatch(key) or not VALUE.fullmatch(value):
                raise ValueError('malformed kernel configuration assignment')
        elif not line or line.startswith('#'):
            continue
        else:
            raise ValueError('unexpected kernel configuration line')
        if key in result:
            raise ValueError('duplicate kernel configuration assignment')
        result[key] = value
    if not result:
        raise ValueError('empty kernel configuration')
    return result


def validate_policy(policy):
    if (not isinstance(policy, dict)
            or set(policy) != {'schema_version', 'id', 'baseline', 'production'}
            or type(policy['schema_version']) is not int or policy['schema_version'] != 1
            or not isinstance(policy['id'], str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', policy['id'])):
        raise ValueError('invalid kernel policy')
    for group in ('baseline', 'production'):
        rules = policy[group]
        if not isinstance(rules, dict) or not 1 <= len(rules) <= 256:
            raise ValueError('invalid kernel policy requirements')
        for key, value in rules.items():
            if not isinstance(key, str) or not SYMBOL.fullmatch(key) or not isinstance(value, str) or not VALUE.fullmatch(value):
                raise ValueError('invalid kernel policy requirement')
    if set(policy['baseline']) & set(policy['production']):
        raise ValueError('production policy cannot override baseline requirements')


def check(data, policy, profile):
    if profile not in ('development', 'production'):
        raise ValueError('unknown kernel policy profile')
    validate_policy(policy)
    values = parse_config(data)
    def differences(rules):
        return [{'symbol': key, 'expected': expected, 'observed': values.get(key)}
                for key, expected in sorted(rules.items()) if values.get(key) != expected]
    baseline = differences(policy['baseline'])
    production = differences(policy['production'])
    failures = baseline + (production if profile == 'production' else [])
    return {'schema_version': 1, 'operation': 'kernel-config-check',
            'status': 'FAIL' if failures else 'PASS', 'profile': profile,
            'policy_id': policy['id'], 'config_sha256': hashlib.sha256(data).hexdigest(),
            'policy_sha256': hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'failures': failures, 'production_differences': production,
            'kernel_accepted': False,
            'scope': 'Declared compile-time settings only; not complete hardening, effective boot policy, module compatibility or device acceptance'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--profile', required=True, choices=('development', 'production'))
    args = parser.parse_args(argv)
    try:
        with args.config.open('rb') as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
        result = check(data, components.load_json(args.policy), args.profile)
    except (OSError, ValueError) as error:
        message = 'unable to read kernel configuration or policy' if isinstance(error, OSError) else str(error)
        print(json.dumps({'operation': 'kernel-config-check', 'status': 'FAIL', 'error': message}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1
