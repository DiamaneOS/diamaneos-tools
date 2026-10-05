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
                         (b'CONFIG_TIPC_LOCAL_ONLY=y', b'# CONFIG_TIPC_LOCAL_ONLY is not set'),
                         (b'# CONFIG_TIPC_MEDIA_UDP is not set', b'CONFIG_TIPC_MEDIA_UDP=y'),
                         (b'# CONFIG_TIPC_CRYPTO is not set', b'CONFIG_TIPC_CRYPTO=y'),
                         (b'# CONFIG_TIPC_DIAG is not set', b'CONFIG_TIPC_DIAG=m')]:
            with self.subTest(change=new):
                self.assertEqual(kernel_config.check(data.replace(old, new), self.policy, 'development')['status'], 'FAIL')

    def test_vendor_role_policy_rejects_missing_or_disabled_ownership(self):
        policy = json.loads((ROOT / 'config/kernel-vendor-policy-fp6.json').read_text())
        good = (b'CONFIG_QRTR=m\nCONFIG_QRTR_IMSDCM_OWNERSHIP=y\n# CONFIG_QRTR_TUN is not set\n'
                b'# CONFIG_SECURITY_SELINUX_DEVELOP is not set\n')
        self.assertEqual(kernel_config.check(good, policy, 'production')['status'], 'PASS')
        for changed in (good.replace(b'CONFIG_QRTR_IMSDCM_OWNERSHIP=y', b''),
                        good.replace(b'CONFIG_QRTR_IMSDCM_OWNERSHIP=y',
                                     b'# CONFIG_QRTR_IMSDCM_OWNERSHIP is not set')):
            with self.subTest(config=changed):
                report = kernel_config.check(changed, policy, 'production')
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual([row['symbol'] for row in report['failures']],
                                 ['CONFIG_QRTR_IMSDCM_OWNERSHIP'])

    def test_vendor_role_policy_rejects_the_qrtr_tunnel(self):
        policy = json.loads((ROOT / 'config/kernel-vendor-policy-fp6.json').read_text())
        good = (b'CONFIG_QRTR=m\nCONFIG_QRTR_IMSDCM_OWNERSHIP=y\n# CONFIG_QRTR_TUN is not set\n'
                b'# CONFIG_SECURITY_SELINUX_DEVELOP is not set\n')
        self.assertEqual(kernel_config.check(good, policy, 'production')['status'], 'PASS')
        for changed in (good.replace(b'# CONFIG_QRTR_TUN is not set\n', b''),
                        good.replace(b'# CONFIG_QRTR_TUN is not set', b'CONFIG_QRTR_TUN=m')):
            with self.subTest(config=changed):
                report = kernel_config.check(changed, policy, 'production')
                self.assertEqual([row['symbol'] for row in report['failures']], ['CONFIG_QRTR_TUN'])

    def test_runtime_features_and_lockdown_stay_on(self):
        # ART's garbage collector (userfaultfd), compressed OTAs (io_uring), casefolded
        # /data (unicode, f2fs) and integrity lockdown, which keeps user space from
        # modifying the running kernel.
        data = self.config(False)
        for symbol in ('CONFIG_USERFAULTFD', 'CONFIG_IO_URING', 'CONFIG_UNICODE', 'CONFIG_F2FS_FS',
                       'CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY'):
            with self.subTest(symbol=symbol):
                changed = data.replace(f'{symbol}=y'.encode(), f'# {symbol} is not set'.encode())
                result = kernel_config.check(changed, self.policy, 'development')
                self.assertEqual([r['symbol'] for r in result['failures']], [symbol])

    def test_lockdown_level_is_integrity(self):
        # Confidentiality lockdown empties tracefs and blocks BPF kernel reads, so
        # per-app CPU time and lmkd's memory events stop; no lockdown lets user space
        # modify the running kernel. Either choice fails the check.
        data = self.config(False)
        integrity = b'CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY=y'
        self.assertIn(b'# CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY is not set', data)
        without = data.replace(integrity, b'# CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY is not set')
        confidentiality = without.replace(b'# CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY is not set',
                                          b'CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY=y')
        none = without + b'\nCONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y'
        for changed, failing in ((confidentiality, ['CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY',
                                                    'CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY']),
                                 (none, ['CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY'])):
            for profile in ('development', 'production'):
                with self.subTest(failing=failing, profile=profile):
                    result = kernel_config.check(changed, self.policy, profile)
                    self.assertEqual(result['status'], 'FAIL')
                    self.assertEqual([r['symbol'] for r in result['failures']
                                      if r['symbol'].startswith('CONFIG_LOCK_DOWN_')], failing)

    def test_settings_hardware_support_depends_on_stay_on(self):
        # The USB controller glue's kretprobe hooks, ueventd's firmware fallback and
        # the debugfs API that the display driver and a recovery module need.
        data = self.config(False)
        for symbol in ('CONFIG_KPROBES', 'CONFIG_KRETPROBES', 'CONFIG_FW_LOADER_USER_HELPER',
                       'CONFIG_DEBUG_FS', 'CONFIG_DEBUG_FS_DISALLOW_MOUNT'):
            with self.subTest(symbol=symbol):
                changed = data.replace(f'{symbol}=y'.encode(), f'# {symbol} is not set'.encode())
                result = kernel_config.check(changed, self.policy, 'production')
                self.assertIn(symbol, [r['symbol'] for r in result['failures']])
        # debugfs keeps its in-kernel API but refuses mounts (-114, the forks fix
        # that mode); going back to a mountable debugfs fails the check.
        changed = data.replace(b'# CONFIG_DEBUG_FS_ALLOW_ALL is not set', b'CONFIG_DEBUG_FS_ALLOW_ALL=y')
        changed = changed.replace(b'CONFIG_DEBUG_FS_DISALLOW_MOUNT=y', b'# CONFIG_DEBUG_FS_DISALLOW_MOUNT is not set')
        result = kernel_config.check(changed, self.policy, 'development')
        self.assertEqual([r['symbol'] for r in result['failures']],
                         ['CONFIG_DEBUG_FS_ALLOW_ALL', 'CONFIG_DEBUG_FS_DISALLOW_MOUNT'])

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
            self.assertEqual(len(report['production_differences']), len(self.policy['production']))
            self.assertNotIn(directory, result.stdout)
