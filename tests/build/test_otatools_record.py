"""Synthetic archive records and stale-cache refusals without a build or native tools."""
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw

LEGACY = 'host/linux-x86/obj/PACKAGING/otatools_intermediates/otatools.zip'
MODERN = 'soong/.intermediates/build/make/tools/otatools_package/otatools-package/{variant}/gen/otatools.zip'


class ArchiveFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = bw.Workspace(self.root)
        self.out = self.root / 'out'
        self.out.mkdir()
        self.ctx = SimpleNamespace(out=self.out, workspace=self.workspace, official=False)

    def archive(self, relative):
        path = self.out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('bin/fixture-tool', b'synthetic tool bytes')
        return path

    def record(self, path):
        return {'file': str(path.relative_to(self.root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def cached_outputs(self, tool=True, official=False):
        target = self.archive('target/product/FP6/target-files.zip')
        result = {'target_files': str(target.relative_to(self.root)),
                  'target_files_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'official': official}
        if tool:
            result['otatools'] = self.record(self.archive(LEGACY))
        return result


class OtatoolsRecordTests(ArchiveFixtures):
    def test_nonofficial_without_archive_returns_none(self):
        self.assertIsNone(steps.otatools_record(self.ctx))

    def test_official_without_archive_refuses(self):
        self.ctx.official = True
        with self.assertRaisesRegex(bw.BuildStepError, 'exactly one otatools archive'):
            steps.otatools_record(self.ctx)

    def test_each_supported_location_records_exact_relative_path_and_hash(self):
        for relative in (LEGACY, MODERN.format(variant='android_common')):
            with self.subTest(location=relative):
                archive = self.archive(relative)
                for official in (False, True):
                    self.ctx.official = official
                    self.assertEqual(self.record(archive), steps.otatools_record(self.ctx))
                archive.unlink()

    def test_multiple_archive_locations_or_variants_are_ambiguous(self):
        for locations in ((LEGACY, MODERN.format(variant='android_common')),
                          (MODERN.format(variant='android_common'), MODERN.format(variant='linux_glibc'))):
            with self.subTest(locations=locations):
                files = [self.archive(location) for location in locations]
                for official in (False, True):
                    self.ctx.official = official
                    with self.assertRaisesRegex(bw.BuildStepError, 'exactly one otatools archive'):
                        steps.otatools_record(self.ctx)
                for path in files:
                    path.unlink()

    def test_symlink_archives_are_excluded_instead_of_hashed(self):
        target = self.archive('unlisted/archive.zip')
        link = self.out / LEGACY
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        with patch.object(steps.bw, 'sha_file') as digest:
            self.assertIsNone(steps.otatools_record(self.ctx))
            self.ctx.official = True
            with self.assertRaises(bw.BuildStepError):
                steps.otatools_record(self.ctx)
            digest.assert_not_called()

    def test_a_symlink_does_not_make_a_regular_archive_ambiguous(self):
        regular = self.archive(LEGACY)
        link = self.out / MODERN.format(variant='android_common')
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(regular)
        self.ctx.official = True
        self.assertEqual(self.record(regular), steps.otatools_record(self.ctx))

    def test_broken_links_directories_and_unlisted_locations_do_not_count(self):
        link = self.out / LEGACY
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(self.out / 'missing.zip')
        folder = self.out / MODERN.format(variant='android_common')
        folder.mkdir(parents=True)
        self.archive('otatools.zip')
        self.assertIsNone(steps.otatools_record(self.ctx))
        self.ctx.official = True
        with self.assertRaises(bw.BuildStepError):
            steps.otatools_record(self.ctx)

    def test_unreadable_or_disappearing_archive_hash_raises_build_step_error(self):
        path = self.archive(LEGACY)
        for failure in (PermissionError('fixture unreadable archive'), FileNotFoundError('fixture removed archive')):
            for official in (False, True):
                with self.subTest(error=type(failure).__name__, official=official):
                    self.ctx.official = official
                    with patch.object(steps.bw, 'sha_file', side_effect=failure) as digest:
                        with self.assertRaisesRegex(bw.BuildStepError, 'unable to record the build tool archive') as caught:
                            steps.otatools_record(self.ctx)
                        self.assertIs(failure, caught.exception.__cause__)
                        digest.assert_called_once_with(path)


class AndroidOutputsMatchTests(ArchiveFixtures):
    def test_matching_target_and_tool_records_are_reusable(self):
        for official in (False, True):
            with self.subTest(official=official):
                self.assertTrue(steps.android_outputs_match(self.workspace, self.cached_outputs(official=official)))

    def test_only_nonofficial_records_may_omit_a_tool_archive(self):
        outputs = self.cached_outputs(tool=False)
        self.assertTrue(steps.android_outputs_match(self.workspace, outputs))
        outputs['otatools'] = None
        self.assertTrue(steps.android_outputs_match(self.workspace, outputs))
        outputs['official'] = True
        self.assertFalse(steps.android_outputs_match(self.workspace, outputs))

    def test_changed_or_missing_archive_bytes_are_stale(self):
        for member in ('target_files', 'otatools'):
            with self.subTest(member=member):
                outputs = self.cached_outputs()
                relative = outputs['target_files'] if member == 'target_files' else outputs['otatools']['file']
                path = self.root / relative
                path.write_bytes(b'changed synthetic bytes')
                self.assertFalse(steps.android_outputs_match(self.workspace, outputs))
                path.unlink()
                self.assertFalse(steps.android_outputs_match(self.workspace, outputs))

    def test_malformed_cached_tool_entries_are_stale(self):
        outputs = self.cached_outputs()
        record = outputs['otatools']
        entries = ({}, [], '', False, 0, True, 1, 'archive.zip', [record],
                   {'file': record['file']}, {'sha256': record['sha256']},
                   {'file': None, 'sha256': record['sha256']},
                   {'file': [], 'sha256': record['sha256']},
                   {'file': record['file'], 'sha256': None})
        for entry in entries:
            with self.subTest(entry=entry):
                changed = dict(outputs, otatools=entry)
                self.assertFalse(steps.android_outputs_match(self.workspace, changed))

    def test_malformed_outputs_and_target_paths_are_stale(self):
        outputs = self.cached_outputs()
        for record in (None, [], 'invalid', 1, {}, {'target_files': None},
                       dict(outputs, target_files=[]), dict(outputs, target_files_sha256=None),
                       {key: value for key, value in outputs.items() if key != 'target_files_sha256'}):
            with self.subTest(record=record):
                self.assertFalse(steps.android_outputs_match(self.workspace, record))

    def test_unreadable_target_or_tool_hash_failure_returns_stale(self):
        outputs = self.cached_outputs()
        original = steps.bw.sha_file
        for relative in (outputs['target_files'], outputs['otatools']['file']):
            unreadable = self.root / relative

            def hash_or_refuse(path):
                if path == unreadable:
                    raise PermissionError('fixture unreadable cache archive')
                return original(path)

            with self.subTest(member=relative), patch.object(steps.bw, 'sha_file', side_effect=hash_or_refuse):
                self.assertFalse(steps.android_outputs_match(self.workspace, outputs))

    def test_symlinked_target_files_are_stale_even_when_target_hash_matches(self):
        # The same for a linked tool archive.
        for member in ('target_files', 'otatools'):
            with self.subTest(member=member):
                outputs = self.cached_outputs()
                record = outputs if member == 'target_files' else outputs['otatools']
                key = 'target_files' if member == 'target_files' else 'file'
                real = self.root / record[key]
                link = real.with_name('linked-' + real.name)
                link.symlink_to(real)
                record[key] = str(link.relative_to(self.root))
                with patch.object(steps.bw, 'sha_file', wraps=steps.bw.sha_file) as digest:
                    self.assertFalse(steps.android_outputs_match(self.workspace, outputs))
                if member == 'target_files':
                    digest.assert_not_called()


if __name__ == '__main__':
    unittest.main()
