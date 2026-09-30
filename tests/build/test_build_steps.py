import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw


def arguments(workspace, **kw):
    values = dict(workspace=str(workspace), environment=None, variant=None, jobs=None, allow_network=False,
                  objects_from=None, factory_zip=None, shallow=False)
    values.update(kw)
    return argparse.Namespace(**values)


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = b'factory package bytes' * 100
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.requests = []

    def opener(self, request, timeout):
        self.requests.append(request.headers.get('Range'))
        start = int(request.headers['Range'].split('=')[1].rstrip('-')) if request.headers.get('Range') else 0
        return Response(self.data[start:], 206 if start else 200)

    def test_download_resumes_and_publishes_only_the_pinned_bytes(self):
        target = self.root / 'factory.zip'
        (self.root / 'factory.zip.partial').write_bytes(self.data[:100])
        steps.download('https://example.invalid/f.zip', target, len(self.data), self.sha, lambda *a: None, self.opener)
        self.assertEqual(['bytes=100-'], self.requests)
        self.assertEqual(self.data, target.read_bytes())
        steps.download('https://example.invalid/f.zip', target, len(self.data), self.sha, lambda *a: None, self.opener)
        self.assertEqual(1, len(self.requests))

    def test_wrong_bytes_are_discarded(self):
        target = self.root / 'factory.zip'
        with self.assertRaisesRegex(bw.BuildStepError, 'pinned size and SHA-256'):
            steps.download('https://example.invalid/f.zip', target, len(self.data), '0' * 64, lambda *a: None, self.opener)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / 'factory.zip.partial').exists())

    def test_plain_http_is_refused(self):
        with self.assertRaisesRegex(bw.UsageError, 'HTTPS'):
            steps.download('http://example.invalid/f.zip', self.root / 'f', 1, self.sha)

    def test_pinned_archive_comes_from_the_official_host(self):
        recipe = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_text())
        stock = json.loads((ROOT / 'config/stock-inputs.json').read_text())
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        archive = steps.selected_factory_archive(recipe, stock, config)
        self.assertTrue(archive['download_url'].startswith('https://android-builds.fairphone.com/'))
        config['factory_host'] = 'mirror.example.invalid'
        with self.assertRaisesRegex(bw.UsageError, 'must come from'):
            steps.selected_factory_archive(recipe, stock, config)


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines = []

    def context(self, **kw):
        return steps.make_context(arguments(self.root / 'ws', **kw), self.lines.append)

    def test_dry_run_prints_every_step_and_writes_nothing(self):
        self.assertEqual(0, steps.main(['all', '--dry-run', '--workspace', str(self.root / 'ws')], self.lines.append))
        text = '\n'.join(self.lines)
        for step in bw.STEPS:
            self.assertIn(step + ':', text)
        self.assertIn('repo init -u https://github.com/GrapheneOS/platform_manifest.git -b refs/tags/2026091000', text)
        self.assertIn('FP6-cur-user', text)
        self.assertIn('unshare --user --map-current-user --net --', text)
        self.assertFalse((self.root / 'ws').exists())

    def test_default_variant_is_user_and_userdebug_is_explicit(self):
        self.assertEqual('user', self.context().variant)
        self.assertEqual('userdebug', self.context(variant='userdebug').variant)
        with self.assertRaisesRegex(bw.UsageError, 'unknown variant'):
            self.context(variant='eng')

    def test_shallow_sync_fetches_only_pinned_commits(self):
        plan = steps.plan_sync(self.context(shallow=True))
        commands = [a.argv for a in plan.actions if a.argv]
        self.assertIn('--depth=1', commands[0])
        self.assertIn('-c', commands[1])
        self.assertTrue(all(a.network for a in plan.actions if a.argv))

    def test_allow_network_is_recorded_as_an_input(self):
        ctx = self.context(allow_network=True)
        self.assertFalse(bw.Runner(ctx.allow_network).isolated(steps.plan_kernel(ctx).actions[1]))

    def test_step_options_are_checked(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(2, steps.main(['android', '--shallow', '--dry-run', '--workspace', str(self.root)],
                                           self.lines.append))
            self.assertEqual(2, steps.main(['sync', '--factory-zip', 'x', '--dry-run', '--workspace', str(self.root)],
                                           self.lines.append))
        self.assertIn('--shallow is an option of: sync, all', errors.getvalue())

    def test_build_number_override_is_validated(self):
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        self.assertEqual('test.0123456789ab', steps.build_number('0123456789abcdef', config))
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'ims.r9t.20261003.10'}):
            self.assertEqual('ims.r9t.20261003.10', steps.build_number('0' * 64, config))
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'a b'}):
            with self.assertRaises(bw.UsageError):
                steps.build_number('0' * 64, config)

    def test_objects_from_needs_an_index_of_existing_bundles(self):
        directory = self.root / 'objects'
        directory.mkdir()
        with self.assertRaisesRegex(bw.UsageError, 'objects.json'):
            steps.read_objects(directory)
        (directory / 'device.bundle').write_bytes(b'bundle')
        (directory / 'objects.json').write_text(json.dumps({'device/fairphone/FP6': 'device.bundle'}))
        self.assertEqual({'device/fairphone/FP6': directory / 'device.bundle'}, steps.read_objects(directory))
        (directory / 'objects.json').write_text(json.dumps({'../escape': 'device.bundle'}))
        with self.assertRaises(bw.UsageError):
            steps.read_objects(directory)


class RunnerTests(unittest.TestCase):
    """Skip, rerun and failure handling with stand-in steps."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines = []
        self.ctx = steps.make_context(arguments(self.root / 'ws'), self.lines.append)
        self.inputs = {name: {'value': 1} for name in bw.STEPS}
        self.calls = []
        self.fail = None

    def fake(self, name):
        def plan(ctx):
            def act():
                self.calls.append(name)
                if self.fail == name:
                    raise bw.BuildStepError(name + ' broke')
            upstream = [ctx.workspace.passed(s) for s in bw.STEPS[:bw.STEPS.index(name)]]
            inputs = dict(self.inputs[name], upstream=[u['outputs'] for u in upstream if u])
            return steps.StepPlan(name, inputs, [bw.Action('act', func=act)],
                                  lambda: {'value': self.inputs[name]['value']}, lambda state: True)
        return plan

    def run_all(self, **kw):
        with patch.dict(steps.PLANS, {n: self.fake(n) for n in bw.STEPS}):
            steps.run_steps(self.ctx, bw.STEPS, **kw)

    def test_unchanged_steps_are_skipped_and_changes_rerun_downstream(self):
        self.run_all()
        self.assertEqual(list(bw.STEPS), self.calls)
        self.calls.clear()
        self.run_all()
        self.assertEqual([], self.calls)
        self.inputs['vendor']['value'] = 2
        self.run_all()
        self.assertEqual(['vendor', 'android', 'package', 'verify'], self.calls)

    def test_forced_step_reruns(self):
        self.run_all()
        self.calls.clear()
        self.run_all(force=('package',))
        self.assertEqual(['package', 'verify'], self.calls)

    def test_failure_is_recorded_and_the_next_run_resumes(self):
        self.fail = 'android'
        with self.assertRaisesRegex(bw.BuildStepError, 'android broke'):
            self.run_all()
        self.assertEqual('FAIL', self.ctx.workspace.state('android')['status'])
        self.fail = None
        self.calls.clear()
        self.run_all()
        self.assertEqual(['android', 'package', 'verify'], self.calls)

    def test_single_step_needs_its_prerequisites(self):
        with self.assertRaisesRegex(bw.UsageError, 'build sync'):
            steps.run_steps(self.ctx, ('android',))


if __name__ == '__main__':
    unittest.main()
