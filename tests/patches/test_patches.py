"""The patch series: applied only to the pinned file contents, and verified without trusting any state file."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import patches


def run(repo, *arguments):
    return subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, files, message):
    for name, content in files.items():
        path = Path(repo, name)
        if content is None:
            run(repo, 'rm', '-q', name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        run(repo, 'add', name)
    run(repo, 'commit', '-q', '-m', message)
    return run(repo, 'rev-parse', 'HEAD')


LINES = ''.join(f'line {n}\n' for n in range(1, 41))


class SeriesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        config = base / 'gitconfig'
        config.write_text('[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n'
                          '[commit]\n\tgpgsign = false\n')
        patcher = mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = base / 'src'
        self.revisions = {}
        self.lib = self.project('lib/a', {'code.c': LINES, 'tool.sh': '#!/bin/sh\necho one\n', 'old.txt': 'old\n'})
        self.queue = self.project(patches.PROJECT, {'README': 'patches\n'})
        self.entries = []

    def project(self, path, files):
        repo = self.source / path
        repo.mkdir(parents=True)
        run(repo, 'init', '-q')
        self.revisions[path] = commit(repo, files, 'base')
        run(repo, 'checkout', '-q', '--detach')
        return repo

    def rows(self):
        """The resolved manifest: every project at the commit the manifest names, wherever its checkout is."""
        return sorted((path, path, 'origin', revision) for path, revision in self.revisions.items())

    def upstream(self, path, files, message):
        """A new manifest commit for a project, checked out as a sync leaves it."""
        self.revisions[path] = commit(self.source / path, files, message)
        return self.revisions[path]

    def make_patch(self, repo, name, files, on='HEAD'):
        """A patch file in the queue that makes FILES out of ON, without moving the project's checkout."""
        with tempfile.TemporaryDirectory() as scratch:
            run(repo, 'worktree', 'add', '-q', '--detach', scratch + '/w', on)
            tree = Path(scratch, 'w')
            for path, content in files.items():
                target = tree / path
                if content is None:
                    target.unlink()
                elif content == 'EXEC':
                    target.chmod(0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
            run(tree, 'add', '-A')
            diff = subprocess.run(['git', '-C', str(tree), 'diff', '--cached', '--binary', '--full-index', on],
                                  check=True, capture_output=True).stdout
            run(repo, 'worktree', 'remove', '--force', scratch + '/w')
        target = self.queue / 'patches' / f'{name}.patch'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(diff)
        return target

    def entry(self, name, files, project='lib/a', on='HEAD', **extra):
        repo = self.source / project
        patch = self.make_patch(repo, name, files, on)
        entry = {'id': name, 'project': project, 'file': f'patches/{name}.patch',
                 'sha256': hashlib.sha256(patch.read_bytes()).hexdigest(), 'title': f'Change {name}', 'kind': 'fix',
                 'files': patches.pins(repo, on, patch), **extra}
        self.entries.append(entry)
        return entry

    def publish(self, entries=None):
        """Commit the series into the queue project, as a sync would deliver it."""
        (self.queue / 'series.json').write_text(json.dumps(
            {'schema': 1, 'patches': self.entries if entries is None else entries}, indent=1))
        run(self.queue, 'add', '-A')
        run(self.queue, 'commit', '-q', '-m', 'series')
        self.revisions[patches.PROJECT] = self.head(self.queue)

    def head(self, repo=None):
        return run(repo or self.lib, 'rev-parse', 'HEAD')

    def apply(self, **options):
        return patches.apply(self.source, self.rows(), echo=lambda _line: None, **options)


class ValidateTests(unittest.TestCase):
    GOOD = {'id': '0001', 'project': 'lib/a', 'file': 'patches/0001.patch', 'sha256': 'a' * 64, 'title': 'Fix it',
            'kind': 'fix', 'files': {'code.c': ['100644 ' + '1' * 40, '100644 ' + '2' * 40]}}

    def series(self, **changes):
        entry = copy.deepcopy(self.GOOD)
        entry.update(changes)
        return {'schema': 1, 'patches': [entry]}

    def test_accepts_a_plain_entry_and_new_and_deleted_files(self):
        self.assertEqual(len(patches.validate(self.series())), 1)
        patches.validate(self.series(files={'new.c': [None, '100755 ' + '3' * 40], 'gone.c': ['120000 ' + '4' * 40, None]}))
        patches.validate(self.series(kind='import', upstream='Little CMS 2.19'))

    def test_refuses_what_it_does_not_know(self):
        bad = [
            self.series(extra='x'),
            self.series(id='has space'),
            self.series(project='../outside'),
            self.series(project='.repo/manifests'),
            self.series(project=patches.PROJECT),
            self.series(file='patches/0001.diff'),
            self.series(file='/abs/0001.patch'),
            self.series(sha256='A' * 64),
            self.series(title='two\nlines'),
            self.series(kind='hack'),
            self.series(kind='backport'),
            self.series(files={}),
            self.series(files={'code.c': ['100644 ' + '1' * 40, '100644 ' + '1' * 40]}),
            self.series(files={'code.c': ['160000 ' + '1' * 40, None]}),
            self.series(files={'code.c': ['100644 ' + '1' * 39, None]}),
            self.series(files={'a/../b': [None, '100644 ' + '1' * 40]}),
            self.series(files={'.git/config': [None, '100644 ' + '1' * 40]}),
            {'schema': 2, 'patches': []},
            {'schema': 1, 'patches': [self.GOOD, self.GOOD]},
            {'schema': 1, 'patches': [], 'more': 1},
        ]
        for document in bad:
            with self.subTest(document=json.dumps(document)[:80]), self.assertRaises(patches.PatchError):
                patches.validate(document)

    def test_refuses_duplicate_keys_in_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'series.json').write_text('{"schema": 1, "schema": 1, "patches": []}')
            with self.assertRaises(patches.PatchError):
                patches.load_series(Path(directory))


class ApplyTests(SeriesCase):
    def reset(self, **options):
        return patches.reset(self.source, echo=lambda _line: None, **options)

    def test_stages_the_series_on_the_manifest_commit(self):
        base = self.head()
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        record = self.apply()
        # HEAD stays, so "repo manifest -r" names the same commit with and without the series.
        self.assertEqual(self.head(), base)
        self.assertEqual(record['projects']['lib/a'],
                         {'base': base, 'tree': run(self.lib, 'write-tree'), 'patches': ['0001']})
        self.assertIn('line five', (self.lib / 'code.c').read_text())
        self.assertEqual(run(self.lib, 'diff', '--cached', '--name-only'), 'code.c')
        self.assertEqual(run(self.lib, 'diff', '--name-only'), '')
        self.assertEqual(patches.local_status(self.lib, self.entries), b'')
        # Applying again changes nothing. Off and on again gives the same tree.
        self.assertEqual(self.apply(), record)
        self.assertEqual(list(self.reset()), ['lib/a'])
        self.assertEqual(run(self.lib, 'status', '--porcelain'), '')
        self.assertNotIn('line five', (self.lib / 'code.c').read_text())
        self.assertEqual(self.reset(), {})
        self.assertEqual(self.apply(), record)

    def test_a_file_that_gets_the_same_contents_back_keeps_its_time(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        self.apply()
        old = 1_600_000_000_000_000_000
        os.utime(self.lib / 'code.c', ns=(old, old))
        saved = self.reset()
        self.assertEqual(saved['lib/a']['code.c'][1], old)
        self.assertNotEqual((self.lib / 'code.c').stat().st_mtime_ns, old)
        self.apply(mtimes=saved)
        self.assertEqual((self.lib / 'code.c').stat().st_mtime_ns, old)
        self.assertEqual(patches.local_status(self.lib, self.entries), b'')
        # Another result for the file is new contents: its time is not put back.
        saved['lib/a']['code.c'] = ('100644 ' + 'c' * 40, old)
        self.reset()
        self.apply(mtimes=saved)
        self.assertNotEqual((self.lib / 'code.c').stat().st_mtime_ns, old)

    def test_second_patch_builds_on_the_first(self):
        base = self.head()
        one = LINES.replace('line 5\n', 'line five\n')
        self.entry('0001', {'code.c': one})
        stacked = commit(self.lib, {'code.c': one}, 'first applied')
        self.entry('0002', {'code.c': one.replace('line 30\n', 'line thirty\n'), 'new/added.txt': 'new\n',
                            'old.txt': None, 'tool.sh': 'EXEC'}, on=stacked)
        run(self.lib, 'checkout', '-q', '--detach', base)
        self.publish()
        files = self.entries[1]['files']
        self.assertIsNone(files['new/added.txt'][0])
        self.assertIsNone(files['old.txt'][1])
        self.assertTrue(files['tool.sh'][0].startswith('100644 ') and files['tool.sh'][1].startswith('100755 '))
        record = self.apply()
        self.assertEqual(record['projects']['lib/a']['patches'], ['0001', '0002'])
        self.assertEqual(self.head(), base)
        text = (self.lib / 'code.c').read_text()
        self.assertTrue('line five' in text and 'line thirty' in text)
        self.assertEqual((self.lib / 'new/added.txt').read_text(), 'new\n')
        self.assertFalse((self.lib / 'old.txt').exists())
        self.assertTrue(os.access(self.lib / 'tool.sh', os.X_OK))
        self.assertEqual(patches.verify(self.source, self.rows()), record)
        self.assertEqual(patches.local_status(self.lib, self.entries), b'')
        self.reset()
        self.assertEqual(run(self.lib, 'status', '--porcelain'), '')
        self.assertTrue((self.lib / 'old.txt').exists() and not (self.lib / 'new/added.txt').exists())

    def test_pins_of_one_file_must_continue_through_the_series(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.entry('0002', {'code.c': LINES.replace('line 30\n', 'line thirty\n')})
        self.publish()
        with self.assertRaisesRegex(patches.PatchError, '0002: its pin for code.c does not continue'):
            self.apply()

    def test_without_a_patch_project_nothing_happens(self):
        rows = [row for row in self.rows() if row[0] != patches.PROJECT]
        self.assertIsNone(patches.apply(self.source, rows))
        self.assertEqual(patches.inspect(self.source, rows), (None, {}))
        self.assertEqual(patches.status(self.source, rows), [])

    def test_a_changed_file_under_a_patch_stops_before_anything_moves(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        moved = self.upstream('lib/a', {'code.c': LINES.replace('line 38\n', 'line 38 upstream\n')}, 'upstream change')
        with self.assertRaisesRegex(patches.PatchError, 'not what the patch was pinned to: code.c'):
            self.apply()
        self.assertEqual(self.head(), moved)
        self.assertEqual(run(self.lib, 'status', '--porcelain'), '')

    def test_an_unrelated_upstream_change_does_not_disturb_the_series(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        moved = self.upstream('lib/a', {'old.txt': 'newer\n', 'more.c': 'int more;\n'}, 'upstream elsewhere')
        record = self.apply()
        self.assertEqual(record['projects']['lib/a']['base'], moved)
        self.assertEqual(run(self.lib, 'diff', '--cached', '--name-only'), 'code.c')

    def test_a_tampered_patch_file_is_refused(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        patch = self.queue / 'patches/0001.patch'
        patch.write_bytes(patch.read_bytes().replace(b'line five', b'line FIVE'))
        run(self.queue, 'add', '-A')
        self.upstream(patches.PROJECT, {}, 'tamper')
        with self.assertRaisesRegex(patches.PatchError, 'not the pinned file'):
            self.apply()
        self.assertEqual(run(self.lib, 'status', '--porcelain'), '')

    def test_a_patch_that_does_more_than_its_pins_is_refused(self):
        entry = self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n'), 'old.txt': 'changed\n'})
        del entry['files']['old.txt']
        self.publish()
        with self.assertRaisesRegex(patches.PatchError, 'other files, or other contents, than its pins: old.txt'):
            self.apply()
        self.assertEqual(run(self.lib, 'status', '--porcelain'), '')

    def test_wrong_result_pin_is_refused(self):
        entry = self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        entry['files']['code.c'][1] = '100644 ' + 'b' * 40
        self.publish()
        with self.assertRaisesRegex(patches.PatchError, 'than its pins: code.c'):
            self.apply()

    def test_local_changes_in_the_patch_project_need_allow_modified(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        (self.queue / 'note.txt').write_text('draft\n')
        with self.assertRaisesRegex(patches.PatchError, 'has local changes'):
            self.apply()
        self.assertEqual(self.apply(allow_modified=True)['projects']['lib/a']['patches'], ['0001'])

    def test_local_edits_survive_unless_they_are_in_the_way(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        (self.lib / 'old.txt').write_text('my edit\n')
        (self.lib / 'mine.txt').write_text('untracked\n')
        self.apply()
        self.assertEqual((self.lib / 'old.txt').read_text(), 'my edit\n')
        # The series is staged, the developer's own work is what is left over.
        self.assertEqual(patches.local_status(self.lib, self.entries), b' M old.txt\0?? mine.txt\0')
        self.assertEqual(run(self.lib, 'diff', '--name-only'), 'old.txt')
        self.reset()
        self.assertEqual((self.lib / 'old.txt').read_text(), 'my edit\n')
        (self.lib / 'code.c').write_text(LINES.replace('line 5\n', 'mine\n'))
        with self.assertRaisesRegex(patches.PatchError, 'local changes are in the way'):
            self.apply()
        self.assertEqual(run(self.lib, 'diff', '--cached', '--name-only'), '')
        self.assertIn('mine', (self.lib / 'code.c').read_text())

    def test_an_edit_on_top_of_a_patched_file_is_local_and_blocks_taking_it_off(self):
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()
        self.apply()
        (self.lib / 'code.c').write_text(LINES.replace('line 5\n', 'line five\n') + 'my line\n')
        self.assertEqual(patches.local_status(self.lib, self.entries), b'MM code.c\0')
        self.assertEqual(patches.verify(self.source, self.rows())['projects']['lib/a']['patches'], ['0001'])
        with self.assertRaisesRegex(patches.PatchError, 'local changes are in the way'):
            self.reset()
        self.assertIn('my line', (self.lib / 'code.c').read_text())

    def test_a_series_naming_an_unknown_project_is_refused(self):
        entry = self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        entry['project'] = 'lib/missing'
        self.publish()
        with self.assertRaisesRegex(patches.PatchError, 'projects the manifest does not have: lib/missing'):
            self.apply()


class VerifyTests(SeriesCase):
    def setUp(self):
        super().setUp()
        self.base = self.head()
        self.entry('0001', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.publish()

    def test_unapplied_series_is_reported(self):
        with self.assertRaisesRegex(patches.PatchError, 'not applied to lib/a'):
            patches.verify(self.source, self.rows())
        self.assertEqual(patches.status(self.source, self.rows()), [('lib/a', 'unpatched', 1)])

    def test_other_staged_contents_are_not_the_series(self):
        self.apply()
        self.assertEqual(patches.status(self.source, self.rows()), [('lib/a', 'applied', 1)])
        # One more staged file.
        (self.lib / 'extra.txt').write_text('more\n')
        run(self.lib, 'add', 'extra.txt')
        with self.assertRaisesRegex(patches.PatchError, 'not applied to lib/a'):
            patches.verify(self.source, self.rows())
        self.assertEqual(patches.status(self.source, self.rows()), [('lib/a', 'other', 1)])
        with self.assertRaisesRegex(patches.PatchError, 'staged changes that are not the patch series'):
            patches.reset(self.source, echo=lambda _line: None)
        self.assertEqual(patches.reset(self.source, echo=lambda _line: None, strict=False), {})
        with self.assertRaisesRegex(patches.PatchError, 'staged changes that are not the patch series'):
            self.apply()
        run(self.lib, 'rm', '-q', '--cached', 'extra.txt')
        (self.lib / 'extra.txt').unlink()
        # The pinned file staged with other contents than the pinned ones.
        (self.lib / 'code.c').write_text(LINES.replace('line 5\n', 'line FIVE\n'))
        run(self.lib, 'add', 'code.c')
        with self.assertRaisesRegex(patches.PatchError, 'not applied to lib/a'):
            patches.verify(self.source, self.rows())
        run(self.lib, 'reset', '-q', '--hard')
        self.assertEqual(self.apply()['projects']['lib/a']['base'], self.base)

    def test_verify_checks_the_patch_files_too(self):
        self.apply()
        (self.queue / 'patches/0001.patch').write_text('other\n')
        with self.assertRaisesRegex(patches.PatchError, 'not the pinned file'):
            patches.verify(self.source, self.rows(), allow_modified=True)

    def test_the_source_check_takes_the_series_and_nothing_else(self):
        from diamaneos_tools import build
        manifests = self.source / '.repo/manifests'
        manifests.mkdir(parents=True)
        run(manifests, 'init', '-q')
        rows = self.rows()
        with self.assertRaisesRegex(build.BuildError, 'not applied to lib/a'):
            build.patch_series(self.source, rows)
        self.apply()
        record, patched = build.patch_series(self.source, rows)
        self.assertEqual(record['projects']['lib/a']['patches'], ['0001'])
        self.assertEqual(build.verify_projects(self.source, rows, patched=patched), {})
        # Without knowing the series, its staged files are local changes like any other.
        with self.assertRaisesRegex(build.BuildError, 'dirty or untracked content: lib/a'):
            build.verify_projects(self.source, rows)
        (self.lib / 'old.txt').write_text('my edit\n')
        with self.assertRaisesRegex(build.BuildError, 'dirty or untracked content: lib/a'):
            build.verify_projects(self.source, rows, patched=patched)
        modified = build.verify_projects(self.source, rows, allow_modified=True, patched=patched)
        self.assertEqual(list(modified), ['lib/a'])
        # The digest of the local changes covers the edit only: it is the one a build without the series has.
        patches.reset(self.source, echo=lambda _line: None)
        self.assertEqual(build.verify_projects(self.source, rows, allow_modified=True), modified)


class CheckTests(SeriesCase):
    def verdicts(self):
        return [(name, verdict, moved) for name, _path, verdict, moved in patches.check(self.source, self.rows())]

    def test_fits_refresh_upstream_and_conflict(self):
        change = LINES.replace('line 5\n', 'line five\n')
        self.entry('0001', {'code.c': change})
        self.publish()
        self.assertEqual(self.verdicts(), [('0001', 'fits', [])])
        # The file changes far from the patch: it still applies, its pins are stale.
        self.upstream('lib/a', {'code.c': LINES.replace('line 38\n', 'line 38 upstream\n')}, 'elsewhere')
        self.assertEqual(self.verdicts(), [('0001', 'refresh', ['code.c'])])
        # Upstream makes the same change next to its own: the patch applies in reverse.
        self.upstream('lib/a', {'code.c': change.replace('line 38\n', 'line 38 upstream\n')}, 'upstream took it')
        self.assertEqual(self.verdicts(), [('0001', 'upstream', ['code.c'])])
        # Upstream rewrites the lines another way.
        self.upstream('lib/a', {'code.c': LINES.replace('line 5\n', 'something else\n')}, 'rewritten')
        self.assertEqual(self.verdicts(), [('0001', 'conflict', ['code.c'])])

    def test_exact_result_already_there_is_upstream(self):
        change = LINES.replace('line 5\n', 'line five\n')
        self.entry('0001', {'code.c': change})
        self.publish()
        self.upstream('lib/a', {'code.c': change}, 'upstream took exactly it')
        self.assertEqual(self.verdicts(), [('0001', 'upstream', ['code.c'])])


class CommandTests(SeriesCase):
    def test_pins_prints_what_a_series_entry_needs(self):
        patch = self.make_patch(self.lib, 'x', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(patches.main(['pins', '--source', str(self.source), 'lib/a', str(patch)]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed['sha256'], hashlib.sha256(patch.read_bytes()).hexdigest())
        self.assertEqual(list(printed['files']), ['code.c'])
        self.assertTrue(all(pin.startswith('100644 ') for pin in printed['files']['code.c']))

    def test_pins_of_a_patch_that_does_not_apply_fails(self):
        patch = self.make_patch(self.lib, 'x', {'code.c': LINES.replace('line 5\n', 'line five\n')})
        self.upstream('lib/a', {'code.c': 'entirely different\n'}, 'moved on')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(patches.main(['pins', '--source', str(self.source), 'lib/a', str(patch)]), 2)
        self.assertIn('does not apply', err.getvalue())


if __name__ == '__main__':
    unittest.main()
