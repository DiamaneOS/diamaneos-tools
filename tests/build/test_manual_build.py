"""The plain Android flow: "build vendor" after repo sync, "build package" after m."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw, image_package, image_verify, product_inputs
from tests.build.test_image_package import fixture_workspace, run_plan

PROPERTIES = ('ro.build.type=userdebug\nro.build.version.incremental=eng.builder.20261009\n'
              'ro.build.date.utc=1791500000\nro.build.tags=test-keys\n')


class ManualBuildTests(unittest.TestCase):
    """What a plain "m" left in out/, recorded as the Android step."""

    def setUp(self):
        self.lines = []
        for patcher in (patch.object(steps.build, 'verify_branch_checkout', side_effect=lambda *a, **k: self.source),
                        patch.object(image_verify, 'check_vendor', side_effect=lambda v: self.vendor_check),
                        patch.object(steps, 'android_identity', return_value='a' * 64)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fresh()

    def fresh(self):
        """A workspace after "build vendor" and m: no Android step record, out/ newer than the vendor step."""
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        ctx = fixture_workspace(Path(temp.name))
        ws = ctx.workspace
        (ws.state_dir / 'android.json').unlink()
        self.target_files = ctx.out / 'target/product/FP6/obj/PACKAGING/target_files_intermediates/FP6-target_files.zip'
        with zipfile.ZipFile(self.target_files, 'a') as archive:
            archive.writestr('SYSTEM/build.prop', PROPERTIES)
        for tool in steps.MANUAL_HOST_TOOLS:
            (ctx.host_bin / tool).touch()
        vendor = ws.passed('vendor')
        vendor['outputs']['generation'] = 'g' * 64
        ws.write_state('vendor', vendor)
        (ws.src / '.repo').mkdir(parents=True)
        self.ctx = replace(ctx, manual=True, variant_given=False, echo=self.lines.append)
        self.descriptor({'inputs': {'vendor': {'generation': 'g' * 64}}})
        os.utime(ws.state_dir / 'vendor.json', (1, 1))
        self.sync = ws.passed('sync')['outputs']
        self.source = {'resolved_manifest_sha256': self.sync['resolved_manifest_sha256'], 'modified_sha256': None,
                       'modified': [], 'generated_input_descriptor_sha256': 'd' * 64}
        self.vendor_check = (True, '3 files')

    def descriptor(self, value):
        (self.ctx.workspace.src / product_inputs.DESCRIPTOR).write_text(json.dumps(value))

    def record(self, ctx=None):
        ctx = ctx or self.ctx
        return run_plan(ctx, steps.plan_android(ctx))

    def test_out_is_recorded_as_a_manual_build_and_packaged_as_one(self):
        outputs = self.record()
        self.assertEqual({'android_build': 'manual', 'variant': 'userdebug', 'lunch': 'FP6-cur-userdebug',
                          'build_number': 'eng.builder.20261009', 'build_datetime': 1791500000,
                          'network_isolation': 'off', 'official': False, 'modified': [],
                          'tools': {'commit': None, 'clean': None},
                          'target_files_sha256': bw.sha_file(self.target_files)},
                         {key: outputs[key] for key in ('android_build', 'variant', 'lunch', 'build_number',
                                                        'build_datetime', 'network_isolation', 'official',
                                                        'modified', 'tools', 'target_files_sha256')})
        plan = steps.plan_android(self.ctx)
        self.assertEqual('manual', plan.inputs['android_build'])
        self.assertEqual([], [a for a in plan.actions if a.argv])  # Nothing is built.
        self.assertFalse(any('note:' in line for line in self.lines))
        ws = self.ctx.workspace
        ws.write_state('android', {'status': 'PASS', 'inputs': plan.inputs, 'inputs_sha256': bw.digest(plan.inputs),
                                   'outputs': outputs})
        # A single step after it, such as "build verify", takes this record as current.
        plain = replace(self.ctx, manual=False)
        self.assertEqual('current', steps.step_status(steps.recorded_context(plain, 'android'), 'android'))
        self.assertTrue(steps.wants_manual_build(plain))
        packaged = run_plan(plain, image_package.plan(plain))
        record = json.loads((ws.root / packaged['directory'] / 'build.json').read_text())
        self.assertEqual(('manual', False, 'userdebug'), (record['android_build'], record['reproducible'],
                                                          record['variant']))

    def test_out_that_does_not_match_stops_with_one_message(self):
        cases = (
            (lambda: (self.ctx.host_bin / 'validate_target_files').unlink(),
             'out/ lacks validate_target_files; after lunch, build them with "m target-files-package '
             'otatools-package"'),
            (lambda: setattr(self, 'vendor_check', (False, 'differs VENDOR/lib64/libfoo.so')),
             'not built with the vendor tree of "diamaneos build vendor" \\(differs VENDOR/lib64/libfoo.so\\)'),
            (lambda: self.descriptor({'inputs': {'vendor': {'generation': 'h' * 64}}}),
             'the source has not the vendor tree "diamaneos build vendor" made'),
            (lambda: self.source.update(resolved_manifest_sha256='0' * 64),
             'the source changed since "diamaneos build vendor" checked it'),
        )
        for change, message in cases:
            with self.subTest(message=message):
                self.fresh()
                change()
                with self.assertRaisesRegex(bw.BuildStepError, message):
                    self.record()

    def test_a_missing_build_another_variant_and_official_stop_before_anything_runs(self):
        with self.assertRaisesRegex(bw.UsageError, 'out/ holds a userdebug build, not user: run lunch FP6-cur-user '
                                                   'and m, or leave out --variant'):
            steps.plan_android(replace(self.ctx, variant='user', variant_given=True))
        with self.assertRaisesRegex(bw.UsageError, 'official images come only from "diamaneos build all"'):
            steps.plan_android(replace(self.ctx, official=True))
        self.target_files.unlink()
        with self.assertRaisesRegex(bw.UsageError, 'out/ holds no complete FP6 build .*"m target-files-package '
                                                   'otatools-package"'):
            steps.plan_android(self.ctx)

    def test_inputs_newer_than_out_are_a_note(self):
        os.utime(self.ctx.workspace.state_dir / 'vendor.json')
        os.utime(self.target_files, (1, 1))
        self.record()
        self.assertIn('    note: out/ is older than the vendor files; if m has not run since, run it and package '
                      'again', self.lines)


class PlainFlowCommandTests(unittest.TestCase):
    """Which steps "build vendor" and "build package" run on their own."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines, self.calls = [], []

    def main(self, *argv):
        def capture(ctx, names, force=(), dry_run=False):
            self.calls.append((tuple(names), tuple(force), ctx.check_only, ctx.manual))
        with patch.object(steps, 'run_steps', side_effect=capture), \
                patch.object(steps.bw, 'check_host', return_value={'warnings': []}):
            return steps.main([*argv, '--workspace', str(self.root / 'ws')], self.lines.append)

    def test_vendor_checks_the_checkout_and_package_records_out(self):
        for argv in (('vendor',), ('package',), ('android',), ('sync',)):
            self.assertEqual(0, self.main(*argv))
        self.assertEqual([(('sync', 'vendor'), ('sync', 'vendor'), True, False),
                          (('sync', 'android', 'package', 'verify'), ('sync', 'android', 'package', 'verify'),
                           True, True),
                          (('android',), ('android',), False, False),
                          (('sync',), ('sync',), False, False)], self.calls)

    def test_package_keeps_an_android_step_that_is_current(self):
        with patch.object(steps, 'wants_manual_build', return_value=False):
            self.main('package')
        self.assertEqual([(('package',), ('package',), False, False)], self.calls)


if __name__ == '__main__':
    unittest.main()
