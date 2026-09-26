import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from diamaneos_tools import kernel_config

ROOT = Path(__file__).resolve().parents[2]


class KernelConfigTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'config/kernel-policy-fp6.json').read_text())

    def config(self, production=True):
        rules = dict(self.policy['baseline'])
        if production:
            rules.update(self.policy['production'])
        return '\n'.join(f'# {key} is not set' if value == 'n' else f'{key}={value}'
                         for key, value in rules.items()).encode()

    def test_production_rejects_each_debug_regression(self):
        data = self.config()
        self.assertEqual(kernel_config.check(data, self.policy, 'production')['status'], 'PASS')
        for key, expected in self.policy['production'].items():
            with self.subTest(symbol=key):
                old = f'# {key} is not set' if expected == 'n' else f'{key}=y'
                new = f'{key}=y' if expected == 'n' else f'# {key} is not set'
                result = kernel_config.check(data.replace(old.encode(), new.encode()), self.policy, 'production')
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual([r['symbol'] for r in result['failures']], [key])

    def test_development_still_enforces_hardening(self):
        data = self.config(False)
        self.assertEqual(kernel_config.check(data, self.policy, 'development')['status'], 'PASS')
        data = data.replace(b'CONFIG_CFI_CLANG=y', b'# CONFIG_CFI_CLANG is not set')
        self.assertEqual(kernel_config.check(data, self.policy, 'development')['status'], 'FAIL')

    def test_development_requires_48_bit_address_space(self):
        data = self.config(False)
        data = data.replace(b'CONFIG_ARM64_VA_BITS_48=y', b'CONFIG_ARM64_VA_BITS_39=y')
        data = data.replace(b'CONFIG_ARM64_VA_BITS=48', b'CONFIG_ARM64_VA_BITS=39')
        result = kernel_config.check(data, self.policy, 'development')
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual([r['symbol'] for r in result['failures']],
                         ['CONFIG_ARM64_VA_BITS', 'CONFIG_ARM64_VA_BITS_48'])

    def test_tipc_stays_a_local_only_module(self):
        # Qualcomm's data stack needs TIPC; its network bearer, crypto and diag must stay out.
        data = self.config(False)
        for old, new in [(b'CONFIG_TIPC=m', b'# CONFIG_TIPC is not set'),
                         (b'# CONFIG_TIPC_MEDIA_UDP is not set', b'CONFIG_TIPC_MEDIA_UDP=y'),
                         (b'# CONFIG_TIPC_CRYPTO is not set', b'CONFIG_TIPC_CRYPTO=y'),
                         (b'# CONFIG_TIPC_DIAG is not set', b'CONFIG_TIPC_DIAG=m')]:
            with self.subTest(change=new):
                self.assertEqual(kernel_config.check(data.replace(old, new), self.policy, 'development')['status'], 'FAIL')

    def test_missing_disabled_symbol_is_not_assumed_safe(self):
        data = self.config().replace(b'# CONFIG_MODULE_FORCE_LOAD is not set', b'')
        result = kernel_config.check(data, self.policy, 'production')
        self.assertEqual(result['status'], 'FAIL')
        self.assertIsNone(result['failures'][0]['observed'])

    def test_valid_mixed_case_kconfig_symbol(self):
        self.assertEqual(kernel_config.parse_config(b'CONFIG_FONT_8x16=y'), {'CONFIG_FONT_8x16': 'y'})

    def test_invalid_configs(self):
        for data in (b'', b'CONFIG_X=y\nCONFIG_X=n',
                     b'CONFIG_X=y\n# CONFIG_X is not set', b'CONFIG_X=$(unsafe)',
                     b'CONFIG_X="unclosed', b'\xff', b' ' * (kernel_config.MAX_CONFIG_BYTES + 1)):
            with self.subTest(data=data[:40]):
                with self.assertRaises(ValueError):
                    kernel_config.parse_config(data)

    def test_policy_cannot_override_baseline(self):
        self.policy['production']['CONFIG_CFI_CLANG'] = 'n'
        with self.assertRaisesRegex(ValueError, 'cannot override'):
            kernel_config.validate_policy(self.policy)

    def test_cli_rejects_incomplete_production_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.config'
            path.write_bytes(self.config(False))
            result = subprocess.run([str(ROOT / 'bin/diamaneos'), 'build', 'kernel-config',
                                     '--config', str(path), '--policy',
                                     str(ROOT / 'config/kernel-policy-fp6.json'),
                                     '--profile', 'production'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'FAIL')
            self.assertEqual(len(report['production_differences']), 4)
            self.assertNotIn(directory, result.stdout)
