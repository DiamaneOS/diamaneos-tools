"""Exercise direct attachment through the trial orchestration boundary."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / 'src'))
from diamaneos_tools import compatibility as api


class DirectUsbTest(unittest.TestCase):
    def run_trial(self, devices):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            args = SimpleNamespace(run_id='direct-test', target='synthetic',
                device_role='harness', device_map='map', rig_config=None,
                direct_usb=True, output=str(Path(temp) / 'runs'),
                package_root=temp, archive='archive', setup_record='setup',
                profile='stock16-harness-trial', adb='adb', rerun_from=None,
                timeout_seconds=60)
            config = api._load_json(TOOLS / 'config/test-suites.json')
            for name, value in [('inspect_package', {}),
                                ('inspect_host', {'status': 'PASS'}),
                                ('_setup_record', {})]:
                stack.enter_context(patch.object(api, name, return_value=value))
            stack.enter_context(patch.object(api.test_runner, 'load_device_map',
                                            return_value={'disposable': True}))
            stack.enter_context(patch.object(api.device, 'authorized_devices', side_effect=devices))
            stack.enter_context(patch.object(api.test_runner, 'run_bounded', side_effect=[
                {'transport': 'ok', 'stdout': value}
                for value in ('16', 'user', 'synthetic-device', 'synthetic-fingerprint')]))
            guard = stack.enter_context(patch.object(api.rig, 'acquire_test_start_guard',
                side_effect=AssertionError('direct attachment must never use hub control')))
            process = stack.enter_context(patch.object(api, '_bounded_process', return_value={
                'transport': 'timeout', 'returncode': -15, 'duration_ms': 60000,
                'stdout': b'retained timeout diagnostics', 'stderr': b''}))
            try:
                result = api.execute_trial(args, config)
            except api.CompatibilityError:
                process.assert_not_called()
                guard.assert_not_called()
                raise
            guard.assert_not_called()
            report = json.loads(result[1].read_text())
            self.assertEqual(b'retained timeout diagnostics',
                (result[1].parent / 'raw/tradefed.stdout.txt').read_bytes())
            return result[0], report

    def test_direct_timeout_retains_report_without_hub_access(self):
        code, report = self.run_trial([['synthetic'], ['synthetic']])
        self.assertEqual(4, code)
        self.assertEqual('INCOMPLETE', report['status'])
        self.assertEqual('direct-usb', report['connection_mode'])
        self.assertEqual('timeout', report['tradefed']['transport'])

    def test_direct_rechecks_target_after_lock_before_launch(self):
        for observed in ([], ['wrong'], ['synthetic', 'other']):
            with self.subTest(observed=observed), self.assertRaises(api.CompatibilityError):
                self.run_trial([['synthetic'], observed])
