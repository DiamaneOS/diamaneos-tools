import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_workspace as bw


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ws = bw.Workspace(self.root / 'ws')

    def test_state_round_trip_and_rejects_foreign_records(self):
        self.assertIsNone(self.ws.state('sync'))
        self.ws.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'a'})
        self.assertEqual('PASS', self.ws.passed('sync')['status'])
        (self.ws.state_dir / 'kernel.json').write_text(json.dumps({'schema_version': 9, 'step': 'kernel'}))
        self.assertIsNone(self.ws.state('kernel'))
        self.ws.write_state('vendor', {'status': 'FAIL'})
        self.assertIsNone(self.ws.passed('vendor'))

    def test_second_command_cannot_take_the_workspace(self):
        with self.ws.lock():
            with self.assertRaisesRegex(bw.UsageError, 'another build command'):
                with self.ws.lock():
                    pass
        with self.ws.lock():
            pass

    def test_compile_actions_run_without_network_unless_allowed(self):
        action = bw.Action('Build', argv=['m', 'droid'], compile=True, env={'OUT_DIR': 'out'}, unset=('OFFICIAL_BUILD',))
        self.assertIn('unshare --user --map-current-user --net -- m droid', action.text(True))
        self.assertIn('env -u OFFICIAL_BUILD OUT_DIR=out', action.text(True))
        self.assertNotIn('unshare', bw.Action('Fetch', argv=['repo', 'sync'], network=True).text(True))
        self.assertTrue(bw.Runner(False).isolated(action))
        self.assertFalse(bw.Runner(True).isolated(action))

    def test_runner_logs_output_and_reports_the_tail_on_failure(self):
        log = self.root / 'step.log'
        runner = bw.Runner(allow_network=True, echo=lambda *a: None)
        runner.run(bw.Action('Say hello', argv=['sh', '-c', 'echo hello']), log)
        self.assertIn('hello', log.read_text())
        with self.assertRaisesRegex(bw.BuildStepError, 'broken'):
            runner.run(bw.Action('Fail', argv=['sh', '-c', 'echo broken; exit 3']), log)
        self.assertIn('broken', log.read_text())

    def probe(self, **kw):
        values = dict(system='Linux', machine='x86_64', python=(3, 13), which=lambda c: '/usr/bin/' + c,
                      memory=128 * bw.GIB, free=2000 * bw.GIB, case_sensitive=True, isolation=True,
                      os_release={'ID': 'debian', 'VERSION_ID': '13'}, packages=None, kernel='6.12', cpus=24)
        values.update(kw)
        return values

    def host(self, **kw):
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        return bw.check_host(self.ws, bw.STEPS, environment, config, True, self.probe(**kw))

    def test_host_check_passes_and_records_the_host(self):
        record = self.host()
        self.assertEqual('debian', record['os_id'])
        self.assertEqual([], record['warnings'])

    def test_host_check_lists_every_problem(self):
        with self.assertRaises(bw.HostError) as caught:
            self.host(system='Darwin', memory=16 * bw.GIB, free=10 * bw.GIB, case_sensitive=False,
                      isolation=False, which=lambda c: None if c in ('repo', 'unshare') else '/usr/bin/' + c)
        message = str(caught.exception)
        for part in ('needs Linux', 'missing commands: repo, unshare', 'RAM', 'GiB free', 'case-sensitive',
                     '--allow-network'):
            self.assertIn(part, message)

    def test_host_check_notes_other_distributions_and_package_deviations(self):
        record = self.host(os_release={'ID': 'ubuntu', 'VERSION_ID': '24.04'}, packages={'git': '1:2.43'},
                           memory=40 * bw.GIB)
        self.assertTrue(any('tested on debian 13' in w for w in record['warnings']))
        self.assertTrue(any('recommended' in w for w in record['warnings']))
        self.assertEqual('1:2.43', record['package_deviations']['git']['installed'])

    def test_jobs_follow_cpus_and_memory(self):
        with patch.object(os, 'cpu_count', return_value=48):
            self.assertEqual(16, bw.default_jobs(None, 32 * bw.GIB))
            self.assertEqual(48, bw.default_jobs(None, 256 * bw.GIB))
            self.assertEqual(64, bw.default_jobs(64, 8 * bw.GIB))
            self.assertEqual(16, bw.default_jobs(None, 256 * bw.GIB, cap=16))



if __name__ == '__main__':
    unittest.main()
