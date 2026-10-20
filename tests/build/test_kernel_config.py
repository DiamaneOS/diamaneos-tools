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
        # Development passes without the production settings.
        self.assertEqual(kernel_config.check(data, self.policy, 'development')['status'], 'PASS')

        def flipped(*pairs):
            changed = data
            for old, new in pairs:
                self.assertIn(old, changed)
                changed = changed.replace(old, new)
            return changed
        on = lambda symbol, value='y': f'{symbol}={value}'.encode()
        off = lambda symbol: f'# {symbol} is not set'.encode()
        disabled = lambda symbol, value='y': (flipped((on(symbol, value), off(symbol))), [symbol])
        enabled = lambda symbol, value='y': (flipped((off(symbol), on(symbol, value))), [symbol])
        confidentiality = 'CONFIG_LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY'
        integrity = 'CONFIG_LOCK_DOWN_KERNEL_FORCE_INTEGRITY'
        no_confidentiality = (on(confidentiality), off(confidentiality))
        rows = [
            disabled('CONFIG_CFI_CLANG'),
            # The 48-bit address space.
            (flipped((b'CONFIG_ARM64_VA_BITS_48=y', b'CONFIG_ARM64_VA_BITS_39=y'),
                     (b'CONFIG_ARM64_VA_BITS=48', b'CONFIG_ARM64_VA_BITS=39')),
             ['CONFIG_ARM64_VA_BITS', 'CONFIG_ARM64_VA_BITS_48']),
            # Qualcomm's data stack needs TIPC. Its network bearer, crypto and diag must stay out.
            disabled('CONFIG_TIPC', 'm'), disabled('CONFIG_TIPC_LOCAL_ONLY'), enabled('CONFIG_TIPC_MEDIA_UDP'),
            enabled('CONFIG_TIPC_CRYPTO'), enabled('CONFIG_TIPC_DIAG', 'm'),
            # ART's garbage collector (userfaultfd), compressed OTAs (io_uring), casefolded
            # /data (unicode, f2fs) and confidentiality lockdown, which keeps user space
            # from modifying or reading the running kernel.
            *map(disabled, ('CONFIG_USERFAULTFD', 'CONFIG_IO_URING', 'CONFIG_UNICODE', 'CONFIG_F2FS_FS',
                            confidentiality)),
            # As on GrapheneOS: integrity lockdown lets privileged processes read kernel
            # memory (kprobes, BPF, XFRM secrets), no lockdown lets them modify it. Either
            # choice fails the check.
            (flipped(no_confidentiality, (off(integrity), on(integrity))), [confidentiality, integrity]),
            (flipped(no_confidentiality) + b'\nCONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y', [confidentiality]),
            # These interfaces would read or replace kernel memory. Lockdown refuses them
            # at run time. Leaving them out of the build keeps them away from a bypass.
            *map(enabled, ('CONFIG_PROC_KCORE', 'CONFIG_KGDB', 'CONFIG_KEXEC', 'CONFIG_KEXEC_FILE',
                           'CONFIG_HIBERNATION')),
            # The USB controller glue's kretprobe hooks, ueventd's firmware fallback and
            # the debugfs API that the display driver and a recovery module need.
            *map(disabled, ('CONFIG_KPROBES', 'CONFIG_KRETPROBES', 'CONFIG_FW_LOADER_USER_HELPER',
                            'CONFIG_DEBUG_FS', 'CONFIG_DEBUG_FS_DISALLOW_MOUNT')),
            # debugfs keeps its in-kernel API but refuses mounts (the forks fix that
            # mode). Going back to a mountable debugfs fails the check.
            (flipped((off('CONFIG_DEBUG_FS_ALLOW_ALL'), on('CONFIG_DEBUG_FS_ALLOW_ALL')),
                     (on('CONFIG_DEBUG_FS_DISALLOW_MOUNT'), off('CONFIG_DEBUG_FS_DISALLOW_MOUNT'))),
             ['CONFIG_DEBUG_FS_ALLOW_ALL', 'CONFIG_DEBUG_FS_DISALLOW_MOUNT'])]
        # Without the production settings, the production profile also names those.
        production = [r['symbol'] for r in kernel_config.check(data, self.policy, 'production')['failures']]
        for changed, failing in rows:
            for profile, other in (('development', []), ('production', production)):
                with self.subTest(failing=failing, profile=profile):
                    result = kernel_config.check(changed, self.policy, profile)
                    self.assertEqual(result['status'], 'FAIL')
                    self.assertEqual([r['symbol'] for r in result['failures'] if r['symbol'] not in other], failing)

    def test_vendor_role_policy_rejects_missing_or_disabled_ownership(self):
        # QRTR ownership stays on and the QRTR tunnel off. The download-mode driver no
        # longer lets the running system turn dumps on, so its built-in default alone
        # decides what a panic does.
        policy = json.loads((ROOT / 'config/kernel-vendor-policy-fp6.json').read_text())
        good = (b'CONFIG_QRTR=m\nCONFIG_QRTR_IMSDCM_OWNERSHIP=y\n# CONFIG_QRTR_TUN is not set\n'
                b'# CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT is not set\n'
                b'CONFIG_SECURITY_SELINUX_DEVELOP=y\nCONFIG_SECURITY_DMESG_RESTRICT=y\n'
                b'# CONFIG_CORESIGHT is not set\n# CONFIG_STM is not set\n# CONFIG_USB_CONFIGFS_F_QDSS is not set\n'
                b'# CONFIG_USB_F_FS_IPC_LOGGING is not set\n# CONFIG_QCOM_EUD is not set\n')
        self.assertEqual(kernel_config.check(good, policy, 'production')['status'], 'PASS')
        ownership, tunnel = 'CONFIG_QRTR_IMSDCM_OWNERSHIP', 'CONFIG_QRTR_TUN'
        dumps = 'CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT'
        unset = lambda symbol: f'# {symbol} is not set'.encode()
        # Each symbol missing, and each with the wrong value.
        for symbol, old, new in ((ownership, b'CONFIG_QRTR_IMSDCM_OWNERSHIP=y', b''),
                                 (ownership, b'CONFIG_QRTR_IMSDCM_OWNERSHIP=y', unset(ownership)),
                                 (tunnel, unset(tunnel) + b'\n', b''),
                                 (tunnel, unset(tunnel), b'CONFIG_QRTR_TUN=m'),
                                 (dumps, unset(dumps) + b'\n', b''),
                                 (dumps, unset(dumps), b'CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT=y')):
            with self.subTest(symbol=symbol, new=new):
                report = kernel_config.check(good.replace(old, new), policy, 'production')
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual([row['symbol'] for row in report['failures']], [symbol])
        # The debug trace and USB debug modules stay unbuilt.
        for symbol in ('CONFIG_CORESIGHT', 'CONFIG_STM', 'CONFIG_USB_CONFIGFS_F_QDSS',
                       'CONFIG_USB_F_FS_IPC_LOGGING', 'CONFIG_QCOM_EUD'):
            for new in (b'', f'{symbol}=m'.encode()):
                with self.subTest(symbol=symbol, new=new):
                    report = kernel_config.check(good.replace(unset(symbol), new), policy, 'production')
                    self.assertEqual([row['symbol'] for row in report['failures']], [symbol])

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
