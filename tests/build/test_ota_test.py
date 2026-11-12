"""The OTA test build N+1 of a build N and the incremental from N to it, with fixture image sets and a
stand-in ota_from_target_files. No build, phone or network."""
import argparse
import contextlib
from dataclasses import replace
import io
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
from diamaneos_tools import build_steps as steps, build_workspace as bw, image_package, ota_test
from tests.build.test_image_package import fixture_workspace, run_plan

BASE_DATE = 1794470400
RESOLVED = 'e' * 64


def target_files(path: Path, number: str, date: int) -> Path:
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={number}\nro.build.date.utc={date}\n')
    return path


def image_set(directory: Path, number: str, date: int, variant='userdebug', official=False, mark=None,
              build_id=None, signing='public-test-keys') -> Path:
    """An image set with build.json, SHA256SUMS, target-files and an otatools.zip that can make packages."""
    directory.mkdir(parents=True)
    tf = target_files(directory / 'target-files.zip', number, date)
    with zipfile.ZipFile(directory / 'otatools.zip', 'w') as archive:
        otatools_archive_into(archive)
    record = {'schema_version': 1, 'build_id': build_id or f'20261111-{variant}-{number[-4:]}', 'product': 'FP6',
              'build_number': number, 'build_datetime': date, 'build_identity': number.ljust(64, '0')[:64],
              'variant': variant, 'official': official, 'release': False, 'signing': signing,
              'manifest': {'file': 'resolved-manifest.xml', 'resolved_sha256': RESOLVED},
              'target_files': {'file': 'target-files.zip', 'sha256': bw.sha_file(tf)},
              'otatools': {'file': 'otatools.zip', 'sha256': bw.sha_file(directory / 'otatools.zip')}}
    if mark:
        record['ota_test'] = mark
    (directory / 'build.json').write_bytes(bw.encoded(record))
    image_package.write_sums(directory)
    return directory


# A stand-in ota_from_target_files: an A/B package whose metadata comes from the two target-files.
FAKE_OTA = r'''#!/usr/bin/env python3
import sys, zipfile
args = sys.argv[1:]
source, target, out = args[args.index('-i') + 1], args[-2], args[-1]
key = args[args.index('-k') + 1]
def props(path):
    with zipfile.ZipFile(path) as z:
        return dict(l.split('=', 1) for l in z.read('SYSTEM/build.prop').decode().splitlines())
old, new = props(source), props(target)
with zipfile.ZipFile(out, 'w') as z:
    z.writestr('META-INF/com/android/metadata', 'ota-type=AB\npre-device=FP6\npre-build-incremental=%s\n'
               'post-build-incremental=%s\npost-timestamp=%s\nkey=%s\n' % (old['ro.build.version.incremental'],
               new['ro.build.version.incremental'], new['ro.build.date.utc'], key))
    z.writestr('payload_properties.txt', 'FILE_HASH=a\nFILE_SIZE=1\nMETADATA_HASH=b\nMETADATA_SIZE=2\n')
    z.writestr(zipfile.ZipInfo('payload.bin'), b'CrAU', compress_type=zipfile.ZIP_STORED)
'''


def otatools_archive_into(archive):
    from diamaneos_tools import image_verify
    for name in image_verify.OTATOOLS_TOOLS + ('ota_from_target_files',):
        info = zipfile.ZipInfo('bin/' + name)
        info.external_attr = 0o100755 << 16
        archive.writestr(info, FAKE_OTA if name == 'ota_from_target_files' else '#!/bin/sh\n')
    for suffix in ('.pk8', '.x509.pem'):
        archive.writestr(ota_test.TEST_KEY + suffix, b'public test key')


class NumbersAndDates(unittest.TestCase):
    def test_successor_is_later_and_higher(self):
        self.assertEqual('2026111100.1', ota_test.successor_number('2026111100'))
        self.assertEqual(BASE_DATE + 60, ota_test.successor_datetime(BASE_DATE, BASE_DATE))
        # Never earlier than the newest commit of the tree either.
        self.assertEqual(BASE_DATE + 100 + 60, ota_test.successor_datetime(BASE_DATE, BASE_DATE + 100))
        for base in ('2026111100', 'test.0123456789ab', 'eng.x', 'test.2026111100.1'):
            with self.subTest(base=base):
                self.assertTrue(ota_test.newer_number(ota_test.successor_number(base), base))
                self.assertFalse(ota_test.newer_number(base, ota_test.successor_number(base)))
        self.assertFalse(ota_test.newer_number('2026111100', '2026111100'))
        with self.assertRaisesRegex(bw.UsageError, 'too long'):
            ota_test.successor_number('9' * 63)

    def test_a_successor_number_is_never_a_release_number(self):
        self.assertIsNone(steps.RELEASE_NUMBER.fullmatch(ota_test.successor_number('2026111100')))


class BaseRecords(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_base_facts_need_a_listed_record(self):
        base = image_set(self.root / 'n', '2026111100', BASE_DATE)
        facts = ota_test.base_facts(ota_test.load_record(base))
        self.assertEqual(('2026111100', BASE_DATE, 'userdebug', False, RESOLVED),
                         (facts['build_number'], facts['build_datetime'], facts['variant'], facts['official'],
                          facts['resolved_sha256']))
        record = json.loads((base / 'build.json').read_text())
        record['build_datetime'] = BASE_DATE + 1
        (base / 'build.json').write_bytes(bw.encoded(record))
        with self.assertRaisesRegex(bw.UsageError, 'does not match its SHA256SUMS'):
            ota_test.load_record(base)
        with self.assertRaisesRegex(bw.UsageError, 'no build date'):
            ota_test.base_facts(dict(record, build_datetime='1'))

    def test_the_successor_is_built_like_its_base_from_its_tree(self):
        base = ota_test.base_facts(ota_test.load_record(image_set(self.root / 'n', '2026111100', BASE_DATE)))
        ota_test.check_context(base, 'userdebug', False)
        with self.assertRaisesRegex(bw.UsageError, 'is userdebug'):
            ota_test.check_context(base, 'user', False)
        with self.assertRaisesRegex(bw.UsageError, '--no-official'):
            ota_test.check_context(base, 'userdebug', True)
        ota_test.check_same_tree(base, {'resolved_manifest_sha256': RESOLVED})
        with self.assertRaisesRegex(bw.UsageError, 'not the tree of the base build'):
            ota_test.check_same_tree(base, {'resolved_manifest_sha256': 'f' * 64})
        with self.assertRaisesRegex(bw.UsageError, 'local changes'):
            ota_test.check_same_tree(base, {'resolved_manifest_sha256': RESOLVED, 'modified_sha256': 'm'})


class SuccessorBuild(unittest.TestCase):
    """The Android step of an OTA test build: N's tree, a later date, a higher number, the record mark."""

    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('DIAMANEOS_BUILD_NUMBER', None)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base_dir = image_set(self.root / 'n', '2026111100', BASE_DATE, variant='user', official=True)

    def context(self, **kw):
        values = dict(workspace=str(self.root / 'ws'), environment=None, variant=None, jobs=None, allow_network=False,
                      factory_zip=None, shallow=False, ota_successor_of=str(self.base_dir))
        values.update(kw)
        return steps.make_context(argparse.Namespace(**values), lambda *a: None)

    def plan(self, ctx, resolved=RESOLVED):
        for name in ('sync', 'vendor'):
            ctx.workspace.write_state(name, {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {
                'project_map_sha256': 'p', 'kernel_prebuilts_commit': 'k', 'resolved_manifest_sha256': resolved}})
        with patch.object(steps, 'android_identity', return_value='a' * 64), \
                patch.object(steps, 'newest_commit_time', return_value=BASE_DATE):
            return steps.plan_android(ctx)

    def test_the_build_gets_a_later_date_and_a_higher_number(self):
        ctx = self.context()
        self.assertEqual(('user', True), (ctx.variant, ctx.official))
        plan = self.plan(ctx)
        build = next(a for a in plan.actions if a.argv)
        self.assertEqual('2026111100.1', build.env['BUILD_NUMBER'])
        self.assertEqual(str(BASE_DATE + ota_test.SUCCESSOR_SECONDS), str(build.env['BUILD_DATETIME']))
        self.assertEqual('true', build.env['DIAMANEOS_OFFICIAL_BUILD'])
        self.assertEqual('2026111100.1', plan.inputs['build_number'])
        self.assertEqual('2026111100', plan.inputs['ota_successor']['build_number'])

    def test_another_tree_variant_or_build_number_is_refused(self):
        with self.assertRaisesRegex(bw.UsageError, 'not the tree of the base build'):
            self.plan(self.context(), resolved='f' * 64)
        with self.assertRaisesRegex(bw.UsageError, 'is user, so its successor is user too'):
            self.context(variant='userdebug')
        with self.assertRaisesRegex(bw.UsageError, 'with --official'):
            self.context(official=False)
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': '2026111101'}):
            with self.assertRaisesRegex(bw.UsageError, 'Unset DIAMANEOS_BUILD_NUMBER'):
                self.context()

    def test_later_steps_keep_the_successor_from_the_android_record(self):
        ctx = self.context()
        plan = self.plan(ctx)
        ctx.workspace.write_state('android', {'status': 'PASS', 'inputs_sha256': bw.digest(plan.inputs),
                                              'inputs': plan.inputs})
        plain = self.context(ota_successor_of=None, official=True)
        self.assertIsNone(plain.ota_successor)
        recorded = steps.recorded_context(plain, 'android')
        self.assertEqual(ctx.ota_successor, recorded.ota_successor)
        self.assertEqual(plan.inputs, self.plan(recorded).inputs)
        # Other builds keep the inputs they had before the option existed.
        self.assertNotIn('ota_successor', self.plan(replace(plain, official=False)).inputs)

    def test_the_option_belongs_to_android_and_all(self):
        self.assertEqual(('android', 'all'), steps.STEP_OPTIONS['ota_successor_of'])
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(2, steps.main(['package', '--ota-successor-of', str(self.base_dir), '--dry-run',
                                            '--workspace', str(self.root / 'ws')], lambda *a: None))
        self.assertIn('--ota-successor-of is an option of: android, all', errors.getvalue())


class SuccessorRecords(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_the_image_set_marks_an_ota_test_build(self):
        ctx = fixture_workspace(self.root)
        android = ctx.workspace.passed('android')
        base = {'build_id': 'b', 'build_number': '2026111100', 'build_datetime': BASE_DATE, 'build_identity': 'i' * 64}
        android['outputs']['ota_test'] = {'successor_of': base, 'notice': ota_test.NOTICE}
        ctx.workspace.write_state('android', android)
        outputs = run_plan(ctx, image_package.plan(ctx))
        record = json.loads((ctx.workspace.root / outputs['directory'] / 'build.json').read_text())
        self.assertEqual(base, record['ota_test']['successor_of'])
        self.assertIs(False, record['release'])
        self.assertIn('Never a release', record['notice'])


class Incremental(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base_dir = image_set(self.root / 'n', '2026111100', BASE_DATE)
        base = ota_test.base_facts(ota_test.load_record(self.base_dir))
        self.successor_dir = image_set(self.root / 'n1', '2026111100.1', BASE_DATE + 60, mark=ota_test.successor_mark(base))
        self.output = self.root / 'out' / 'n-to-n1-incr.zip'
        self.output.parent.mkdir()
        # The fake ota_from_target_files signs nothing: java only has to be found.
        which = patch.object(ota_test.shutil, 'which', return_value='/usr/bin/java')
        which.start()
        self.addCleanup(which.stop)

    def make(self, **kw):
        return ota_test.make_incremental(self.base_dir, self.successor_dir, self.output, echo=lambda *a: None, **kw)

    def test_the_incremental_and_its_record(self):
        record = self.make()
        self.assertTrue(self.output.is_file())
        self.assertEqual(record, json.loads(self.output.with_suffix('.json').read_text()))
        self.assertEqual(('2026111100', '2026111100.1', str(BASE_DATE + 60)),
                         (record['metadata']['pre-build-incremental'], record['metadata']['post-build-incremental'],
                          record['metadata']['post-timestamp']))
        self.assertEqual(('public-test-keys', False), (record['signing'], record['release']))
        self.assertEqual(bw.sha_file(self.output), record['package']['sha256'])
        # The public test key from the successor's otatools signed it, and nothing is left unpacked.
        meta = ota_test.package_facts(self.output)['metadata']
        self.assertTrue(meta['key'].endswith(ota_test.TEST_KEY))
        self.assertEqual([], list((self.root / 'out' / '.ota-test-tmp').iterdir()))
        self.assertFalse(self.output.with_name(self.output.name + '.partial').exists())
        with self.assertRaisesRegex(bw.UsageError, 'exists already'):
            self.make()

    def test_no_java_stops_before_the_payload(self):
        with patch.object(ota_test.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(bw.UsageError, 'needs java: give --source-root'):
                self.make()
        self.assertFalse((self.root / 'out' / '.ota-test-tmp').exists())
        self.assertFalse(self.output.exists())

    def test_a_successor_of_another_base_is_refused(self):
        other = image_set(self.root / 'other', '2026111000', BASE_DATE - 86400)
        with self.assertRaisesRegex(bw.UsageError, 'not the OTA test build of 2026111000'):
            ota_test.make_incremental(other, self.successor_dir, self.output)
        with self.assertRaisesRegex(bw.UsageError, 'not the OTA test build'):
            ota_test.make_incremental(self.base_dir, self.base_dir, self.output)

    def test_a_successor_that_is_not_later_or_higher_is_refused(self):
        base = ota_test.base_facts(ota_test.load_record(self.base_dir))
        same_date = image_set(self.root / 'same', '2026111100.1', BASE_DATE, mark=ota_test.successor_mark(base))
        with self.assertRaisesRegex(bw.UsageError, 'not later'):
            ota_test.make_incremental(self.base_dir, same_date, self.output)
        lower = image_set(self.root / 'lower', '2026111099', BASE_DATE + 60, mark=ota_test.successor_mark(base))
        with self.assertRaisesRegex(bw.UsageError, 'not higher'):
            ota_test.make_incremental(self.base_dir, lower, self.output)

    def test_signed_copies_need_their_key_and_carry_the_recorded_builds(self):
        with self.assertRaisesRegex(bw.UsageError, 'together'):
            self.make(key='k')
        signed_n = target_files(self.root / 'n-signed.zip', '2026111100', BASE_DATE)
        wrong = target_files(self.root / 'n1-other.zip', '2026111100.1', BASE_DATE)
        with self.assertRaisesRegex(bw.UsageError, 'carries build 2026111100.1 of'):
            self.make(source_tf=signed_n, target_tf=wrong, key=str(self.root / 'releasekey'))
        signed_n1 = target_files(self.root / 'n1-signed.zip', '2026111100.1', BASE_DATE + 60)
        record = self.make(source_tf=signed_n, target_tf=signed_n1, key=str(self.root / 'releasekey'))
        self.assertEqual('own-keys', record['signing'])
        self.assertEqual(str(self.root / 'releasekey'), ota_test.package_facts(self.output)['metadata']['key'])

    def test_a_package_with_a_wipe_or_the_wrong_builds_is_refused(self):
        base = {'build_number': '2026111100', 'build_datetime': BASE_DATE}
        successor = {'build_number': '2026111100.1', 'build_datetime': BASE_DATE + 60}
        good = {'metadata': {'ota-type': 'AB', 'pre-device': 'FP6', 'pre-build-incremental': '2026111100',
                             'post-build-incremental': '2026111100.1', 'post-timestamp': str(BASE_DATE + 60)},
                'payload_properties': {'FILE_HASH': 'a', 'FILE_SIZE': '1', 'METADATA_HASH': 'b', 'METADATA_SIZE': '2'}}
        self.assertEqual([], ota_test.package_problems(good, base, successor))
        for change, expected in (({'ota-wipe': 'yes'}, 'ota-wipe is set'),
                                 ({'pre-build-incremental': '2026111000'}, 'pre-build-incremental'),
                                 ({'post-timestamp': str(BASE_DATE)}, 'post-timestamp')):
            facts = {'metadata': dict(good['metadata'], **change), 'payload_properties': good['payload_properties']}
            self.assertIn(expected, ', '.join(ota_test.package_problems(facts, base, successor)))
        facts = {'metadata': good['metadata'], 'payload_properties': dict(good['payload_properties'], POWERWASH='1')}
        self.assertEqual(['POWERWASH is set'], ota_test.package_problems(facts, base, successor))

    def test_a_failed_package_check_leaves_no_package(self):
        with patch.object(ota_test, 'package_problems', return_value=['post-timestamp is wrong']):
            with self.assertRaisesRegex(bw.CheckFailed, 'post-timestamp is wrong'):
                self.make()
        self.assertEqual(['n-to-n1-incr.log'], sorted(p.name for p in self.output.parent.iterdir() if p.is_file()))

    def test_command_line(self):
        lines = []
        self.assertEqual(0, ota_test.main(['plan', '--base', str(self.base_dir)], lines.append))
        self.assertIn(f'successor: 2026111100.1, date {BASE_DATE + 60}', lines[0])
        self.assertEqual(0, ota_test.main(['incremental', '--base', str(self.base_dir), '--successor',
                                           str(self.successor_dir), '--output', str(self.output)], lines.append))
        self.assertIn('incremental 2026111100 to 2026111100.1', lines[-1])
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(2, ota_test.main(['incremental', '--base', str(self.base_dir), '--successor',
                                               str(self.successor_dir), '--output', str(self.output)], lines.append))
        self.assertIn('exists already', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
