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
        return '\n'.join(f'# {key} is not set' if value in ('n', 'off', 'not-built-in') else f'{key}={value}'
                         for key, value in rules.items()).encode()

    def test_production_rejects_each_debug_regression(self):
        data = self.config()
        self.assertEqual(kernel_config.check(data, self.policy, 'production')['status'], 'PASS')
        for key, expected in self.policy['production'].items():
            with self.subTest(symbol=key):
                old = f'# {key} is not set' if expected == 'n' else f'{key}={expected}'
                new = f'{key}=y' if expected == 'n' else f'# {key} is not set'
                result = kernel_config.check(data.replace(old.encode(), new.encode()), self.policy, 'production')
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual([r['symbol'] for r in result['failures']], [key])

    def test_off_and_not_built_in_rules_accept_absent_symbols_and_name_what_they_refuse(self):
        policy = {'schema_version': 1, 'id': 'x', 'baseline': {'CONFIG_A': 'off', 'CONFIG_B': 'not-built-in'}, 'production': {'CONFIG_C': 'y'}}
        failures = lambda data: [r['symbol'] for r in kernel_config.check(data, policy, 'development')['failures']]
        self.assertEqual([], failures(b'# CONFIG_A is not set\n# CONFIG_B is not set\nCONFIG_C=y\n'))
        self.assertEqual([], failures(b'CONFIG_C=y\nCONFIG_B=m\n# CONFIG_A is not set\n'))
        self.assertEqual([], failures(b'CONFIG_C=y\nCONFIG_X=y\n'))
        self.assertEqual(['CONFIG_A'], failures(b'CONFIG_A=m\nCONFIG_C=y\n'))
        self.assertEqual(['CONFIG_A', 'CONFIG_B'], failures(b'CONFIG_A=y\nCONFIG_B=y\nCONFIG_C=y\n'))
        policy['baseline']['CONFIG_D'] = 'sometimes'
        self.assertRaisesRegex(ValueError, 'invalid kernel policy requirement', kernel_config.validate_policy, policy)

    def debug_config(self):
        rules = {**self.policy['baseline'], **self.policy['debug']}
        return '\n'.join(f'# {k} is not set' if v in ('n', 'off', 'not-built-in') else f'{k}={v}' for k, v in rules.items()).encode()

    def test_the_debug_profile_swaps_the_rules_the_aids_need_and_keeps_the_rest(self):
        data = self.debug_config()
        result = kernel_config.check(data, self.policy, 'debug')
        self.assertEqual('PASS', result['status'], result['failures'])
        # The production tier is recorded, not enforced.
        self.assertIn('CONFIG_SECURITY_DMESG_RESTRICT', [r['symbol'] for r in result['production_differences']])
        for profile in ('development', 'production'):
            failing = {r['symbol'] for r in kernel_config.check(data, self.policy, profile)['failures']}
            self.assertLessEqual({'CONFIG_DEBUG_FS_ALLOW_ALL', 'CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE',
                                  'CONFIG_DEBUG_FS_DISALLOW_MOUNT'}, failing)

    def test_the_debug_profile_still_enforces_the_hardening_it_does_not_touch(self):
        data = self.debug_config()
        for old, new, symbol in ((b'CONFIG_CFI=y', b'# CONFIG_CFI is not set', 'CONFIG_CFI'),
                                 (b'CONFIG_MODULE_SIG_FORCE=y', b'# CONFIG_MODULE_SIG_FORCE is not set', 'CONFIG_MODULE_SIG_FORCE'),
                                 (b'CONFIG_TRIM_UNUSED_KSYMS=y', b'# CONFIG_TRIM_UNUSED_KSYMS is not set', 'CONFIG_TRIM_UNUSED_KSYMS')):
            with self.subTest(symbol=symbol):
                result = kernel_config.check(data.replace(old, new), self.policy, 'debug')
                self.assertEqual([symbol], [r['symbol'] for r in result['failures']])

    def test_the_debug_profile_needs_its_aids_on(self):
        data = self.debug_config()
        for symbol in ('CONFIG_FUNCTION_TRACER', 'CONFIG_KFENCE', 'CONFIG_PANIC_ON_OOPS', 'CONFIG_PSTORE_FTRACE'):
            with self.subTest(symbol=symbol):
                result = kernel_config.check(data.replace(f'{symbol}=y'.encode(), f'# {symbol} is not set'.encode()), self.policy, 'debug')
                self.assertEqual([symbol], [r['symbol'] for r in result['failures']])
        self.assertEqual('5', self.policy['debug']['CONFIG_PANIC_TIMEOUT'])

    def test_a_debug_tier_may_not_name_a_production_rule(self):
        self.policy['debug']['CONFIG_SECURITY_DMESG_RESTRICT'] = 'n'
        self.assertRaisesRegex(ValueError, 'debug policy cannot name production', kernel_config.validate_policy, self.policy)

    def test_the_cli_takes_the_debug_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.config'
            path.write_bytes(self.debug_config())
            result = subprocess.run([str(ROOT / 'bin/diamaneos'), 'build', 'kernel-config', '--config', str(path), '--policy',
                                     str(ROOT / 'config/kernel-policy-fp6.json'), '--profile', 'debug'], text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('debug', json.loads(result.stdout)['profile'])

    def test_the_shipped_policies_keep_tipc_and_the_debug_options_out(self):
        self.assertEqual({'off'}, {self.policy['baseline'][k] for k in
                                   ('CONFIG_TIPC_CRYPTO', 'CONFIG_TIPC_DIAG', 'CONFIG_TIPC_MEDIA_UDP')})
        # TIPC is a local-only module in the baseline, so no run that publishes or tests can lack it. Only the debug
        # profile may leave the local-only option off.
        self.assertEqual(('m', 'y'), (self.policy['baseline']['CONFIG_TIPC'], self.policy['baseline']['CONFIG_TIPC_LOCAL_ONLY']))
        self.assertEqual({'CONFIG_SECURITY_DMESG_RESTRICT'}, set(self.policy['production']))
        self.assertEqual('off', self.policy['debug']['CONFIG_TIPC_LOCAL_ONLY'])
        vendor = json.loads((ROOT / 'config/kernel-vendor-policy-fp6.json').read_text())['baseline']
        self.assertEqual('not-built-in', vendor['CONFIG_CORESIGHT'])
        for key in ('CONFIG_USB_CONFIGFS_F_QDSS', 'CONFIG_USB_F_FS_IPC_LOGGING', 'CONFIG_QCOM_EUD'):
            self.assertEqual('off', vendor[key])

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
        no_lockdown = 'CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE'
        rows = [
            disabled('CONFIG_CFI'),
            # Rust and the Rust binder replace RANDSTRUCT and the C binder.
            disabled('CONFIG_RUST'), disabled('CONFIG_ANDROID_BINDER_IPC_RUST'), disabled('CONFIG_RANDSTRUCT_NONE'),
            enabled('CONFIG_ANDROID_BINDER_IPC'),
            # The 48-bit address space.
            (flipped((b'CONFIG_ARM64_VA_BITS_48=y', b'CONFIG_ARM64_VA_BITS_39=y'),
                     (b'CONFIG_ARM64_VA_BITS=48', b'CONFIG_ARM64_VA_BITS=39')),
             ['CONFIG_ARM64_VA_BITS', 'CONFIG_ARM64_VA_BITS_48']),
            # ART's garbage collector (userfaultfd), compressed OTAs (io_uring) and casefolded
            # /data (unicode, f2fs).
            *map(disabled, ('CONFIG_USERFAULTFD', 'CONFIG_IO_URING', 'CONFIG_UNICODE', 'CONFIG_F2FS_FS')),
            # Lockdown at integrity level. Confidentiality breaks the BPF
            # program that reads DMA-BUF statistics, and no lockdown lets privileged processes
            # modify the running kernel. Either choice fails the check.
            disabled(integrity),
            enabled(confidentiality),
            # Qualcomm's data stack needs TIPC only as a local-only module. Its bearer, crypto and diag stay out.
            disabled('CONFIG_TIPC', 'm'), disabled('CONFIG_TIPC_LOCAL_ONLY'),
            enabled('CONFIG_TIPC_MEDIA_UDP'), enabled('CONFIG_TIPC_CRYPTO'), enabled('CONFIG_TIPC_DIAG', 'm'),
            # Exports are trimmed to the symbols modules import.
            disabled('CONFIG_TRIM_UNUSED_KSYMS'),
            (flipped((on(integrity), off(integrity)), (off(no_lockdown), on(no_lockdown))), [integrity, no_lockdown]),
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
        # QRTR stays a vendor module and the QRTR tunnel off. The download-mode driver no
        # longer lets the running system turn dumps on, so its built-in default alone
        # decides what a panic does.
        policy = json.loads((ROOT / 'config/kernel-vendor-policy-fp6.json').read_text())
        good = (b'CONFIG_QRTR=m\n# CONFIG_QRTR_TUN is not set\n'
                b'# CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT is not set\n'
                b'CONFIG_SECURITY_SELINUX_DEVELOP=y\nCONFIG_SECURITY_DMESG_RESTRICT=y\n# CONFIG_STM is not set\n')
        self.assertEqual(kernel_config.check(good, policy, 'production')['status'], 'PASS')
        qrtr, tunnel = 'CONFIG_QRTR', 'CONFIG_QRTR_TUN'
        dumps = 'CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT'
        unset = lambda symbol: f'# {symbol} is not set'.encode()
        # Each symbol missing, and each with the wrong value.
        for symbol, old, new in ((qrtr, b'CONFIG_QRTR=m\n', b''),
                                 (qrtr, b'CONFIG_QRTR=m', unset(qrtr)),
                                 (tunnel, unset(tunnel) + b'\n', b''),
                                 (tunnel, unset(tunnel), b'CONFIG_QRTR_TUN=m'),
                                 (dumps, unset(dumps) + b'\n', b''),
                                 (dumps, unset(dumps), b'CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT=y')):
            with self.subTest(symbol=symbol, new=new):
                report = kernel_config.check(good.replace(old, new), policy, 'production')
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual([row['symbol'] for row in report['failures']], [symbol])
        # The system trace module stays unbuilt.
        for symbol in ('CONFIG_STM',):
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
        self.policy['production']['CONFIG_CFI'] = 'n'
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
