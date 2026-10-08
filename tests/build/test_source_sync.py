"""Reproducing a recorded resolved manifest, and the shallow prefetch of the
large prebuilt projects. Local repositories only; nothing uses the network."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_workspace as bw, source_sync as ss

COMMIT = 'a' * 40
PROJECT_COMMIT = 'b' * 40


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                           '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def resolved_manifest(revision=PROJECT_COMMIT, fetch='https://example.invalid/', extra=''):
    return (f'<manifest><remote name="aosp" fetch="{fetch}"/><default remote="aosp" revision="refs/tags/r1"/>'
            f'<project name="platform/a" path="a" revision="{revision}" upstream="refs/tags/r1" '
            f'dest-branch="refs/tags/r1"/>{extra}</manifest>').encode()


class PinnedManifestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        self.environment_sha256 = 'e' * 64
        self.data = resolved_manifest()
        self.resolved = self.root / 'resolved-manifest.xml'
        self.resolved.write_bytes(self.data)
        sha = hashlib.sha256(self.data).hexdigest()
        manifest = self.environment['manifest']
        self.record = {
            'build_id': '20261005-userdebug-0123456789', 'environment': {'sha256': self.environment_sha256},
            'tools': {'commit': 'c' * 40},
            'manifest': {'url': manifest['url'], 'branch': manifest['branch'], 'commit': COMMIT,
                         'file': 'resolved-manifest.xml', 'resolved_sha256': sha},
            'source': {'manifest_commit': COMMIT, 'resolved_manifest_sha256': sha, 'project_map_sha256': 'p' * 64,
                       'project_count': 1, 'kernel_prebuilts_commit': None, 'shallow': True}}
        self.build_json = self.root / 'build.json'

    def load(self, build_json=True, commit=None, tools='c' * 40):
        if build_json:
            self.build_json.write_text(json.dumps(self.record))
        return ss.load_pinned(self.resolved, self.build_json if build_json else None, commit, self.environment,
                              self.environment_sha256, tools)

    def test_build_json_gives_the_manifest_commit_and_binds_the_file(self):
        pinned = self.load()
        self.assertEqual(COMMIT, pinned.manifest_commit)
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), pinned.sha256)
        self.assertEqual(self.data, pinned.data)
        self.assertEqual({'project_map_sha256': 'p' * 64, 'project_count': 1, 'kernel_prebuilts_commit': None},
                         pinned.expected_source)
        self.assertEqual((), pinned.notes)
        self.assertEqual({'resolved_manifest_sha256': pinned.sha256, 'manifest_commit': COMMIT,
                          'build_id': '20261005-userdebug-0123456789',
                          'build_json_sha256': bw.sha_file(self.build_json)}, pinned.record())

    def test_a_file_whose_hash_differs_from_build_json_is_refused(self):
        self.resolved.write_bytes(resolved_manifest(revision='d' * 40))
        with self.assertRaisesRegex(bw.UsageError, 'does not match .*build.json records ' + '[0-9a-f]{64}'):
            self.load()
        self.resolved.write_bytes(self.data)
        self.record['source']['resolved_manifest_sha256'] = 'f' * 64
        with self.assertRaisesRegex(bw.UsageError, 'does not match'):
            self.load()

    def test_the_manifest_commit_must_be_consistent(self):
        self.record['source']['manifest_commit'] = 'f' * 40
        with self.assertRaisesRegex(bw.UsageError, 'no consistent manifest commit'):
            self.load()
        self.record['source']['manifest_commit'] = COMMIT
        with self.assertRaisesRegex(bw.UsageError, 'differs from the manifest commit'):
            self.load(commit='f' * 40)
        self.assertEqual(COMMIT, self.load(commit=COMMIT).manifest_commit)

    def test_a_commit_alone_is_enough_and_one_of_them_is_required(self):
        pinned = self.load(build_json=False, commit=COMMIT)
        self.assertEqual((COMMIT, None, {}), (pinned.manifest_commit, pinned.build_json_sha256, pinned.expected_source))
        with self.assertRaisesRegex(bw.UsageError, 'needs --build-json'):
            self.load(build_json=False)
        with self.assertRaisesRegex(bw.UsageError, 'full 40-character'):
            self.load(build_json=False, commit=COMMIT[:12])

    def test_a_build_from_another_manifest_is_refused(self):
        self.record['manifest']['url'] = 'https://example.invalid/other_manifest.git'
        with self.assertRaisesRegex(bw.UsageError, 'another manifest URL or branch'):
            self.load()
        self.record['manifest']['url'] = self.environment['manifest']['url']
        self.record['manifest']['branch'] = 'topic'
        with self.assertRaisesRegex(bw.UsageError, 'another manifest URL or branch'):
            self.load()

    def test_an_environment_pinned_to_another_manifest_commit_is_refused(self):
        self.environment['manifest']['revision'] = 'f' * 40
        with self.assertRaisesRegex(bw.UsageError, 'pins manifest revision'):
            self.load()
        self.environment['manifest']['revision'] = COMMIT
        self.load()

    def test_another_environment_or_tools_commit_is_noted(self):
        self.record['environment']['sha256'] = 'f' * 64
        notes = self.load(tools='d' * 40).notes
        self.assertEqual(2, len(notes))
        self.assertIn('another build environment', notes[0])
        self.assertIn('records tools commit ' + 'c' * 40 + ', this checkout is ' + 'd' * 40, notes[1])

    def test_an_official_build_is_noted(self):
        self.record['official'] = True
        self.assertEqual(('build.json records an official build; build it with --official to make the same images',),
                         self.load().notes)

    def test_broken_records_are_refused(self):
        for text in ('{"manifest": {}, "manifest": {}}', '[]', 'not json', '{"manifest": {}}'):
            with self.subTest(text=text):
                self.build_json.write_text(text)
                with self.assertRaisesRegex(bw.UsageError, 'build.json|no manifest'):
                    ss.load_pinned(self.resolved, self.build_json, None, self.environment, 'e' * 64)

    def test_a_resolved_manifest_must_pin_commits_over_https_without_composition(self):
        for data, message in ((resolved_manifest(revision='android17'), 'moving or non-commit'),
                              (resolved_manifest(fetch='http://example.invalid/'), 'without HTTPS'),
                              (resolved_manifest(extra='<include name="other.xml"/>'), 'includes or edits'),
                              (b'<manifest>', 'not valid XML')):
            with self.subTest(message=message):
                self.resolved.write_bytes(data)
                with self.assertRaisesRegex(bw.UsageError, message):
                    self.load(build_json=False, commit=COMMIT)


class ManifestShapeTests(unittest.TestCase):
    # What "repo manifest" prints for the commit resolved_manifest() came from.
    plain = (b'<manifest><remote name="aosp" fetch="https://example.invalid/"/>'
             b'<default remote="aosp" revision="refs/tags/r1"/><project name="platform/a" path="a"/></manifest>')

    def test_only_project_revisions_may_differ(self):
        ss.check_against_manifest(resolved_manifest(), self.plain, COMMIT)
        ss.check_against_manifest(resolved_manifest().replace(b'><', b'>\n  <'), self.plain, COMMIT)

    def test_projects_remotes_and_settings_must_be_the_commits(self):
        extra = resolved_manifest(extra=f'<project name="platform/b" path="b" revision="{COMMIT}"/>')
        with self.assertRaisesRegex(bw.BuildStepError, f'does not belong to manifest commit {COMMIT}: '
                                                       'projects not in that manifest: b'):
            ss.check_against_manifest(extra, self.plain, COMMIT)
        for data in (resolved_manifest(fetch='https://mirror.example.invalid/'),
                     resolved_manifest().replace(b'dest-branch', b'clone-depth="1" dest-branch')):
            with self.assertRaisesRegex(bw.BuildStepError, 'remotes, defaults or project settings differ'):
                ss.check_against_manifest(data, self.plain, COMMIT)


class ManifestCommitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        git(self.remote, 'init', '-q', '-b', 'android17')
        git(self.remote, 'commit', '-q', '--allow-empty', '-m', 'recorded')
        self.recorded = git(self.remote, 'rev-parse', 'HEAD')
        git(self.remote, 'checkout', '-q', '-b', 'side')
        git(self.remote, 'commit', '-q', '--allow-empty', '-m', 'off the branch')
        self.side = git(self.remote, 'rev-parse', 'HEAD')
        git(self.remote, 'checkout', '-q', 'android17')
        git(self.remote, 'commit', '-q', '--allow-empty', '-m', 'newer')
        self.manifests = self.root / 'src/.repo/manifests'
        # A shallow workspace's manifest checkout: depth 1, the branch only.
        subprocess.run(['git', 'clone', '-q', '--depth=1', '-b', 'android17', 'file://' + str(self.remote),
                        str(self.manifests)], check=True, capture_output=True)
        self.url = 'file://' + str(self.remote)
        self.lines = []

    def test_a_shallow_checkout_gets_the_branch_history_and_the_recorded_commit(self):
        self.assertEqual('true', git(self.manifests, 'rev-parse', '--is-shallow-repository'))
        ss.checkout_manifest_commit(self.root / 'src', self.url, 'android17', self.recorded, self.lines.append)
        self.assertEqual(self.recorded, git(self.manifests, 'rev-parse', 'HEAD'))
        self.assertEqual(['    fetching the history of android17 to find the recorded manifest commit'], self.lines)
        self.lines.clear()
        ss.checkout_manifest_commit(self.root / 'src', 'file:///nonexistent', 'android17', self.recorded,
                                    self.lines.append)
        self.assertEqual([], self.lines)

    def test_a_commit_off_the_branch_is_not_fetched_by_its_id(self):
        for commit in (self.side, 'f' * 40):
            with self.assertRaisesRegex(bw.BuildStepError, 'is not in the history of android17'):
                ss.checkout_manifest_commit(self.root / 'src', self.url, 'android17', commit, self.lines.append)
        self.assertNotEqual(self.side, git(self.manifests, 'rev-parse', 'HEAD'))

    def test_branch_and_tag_refspecs(self):
        self.assertEqual('+refs/heads/android17:refs/remotes/origin/android17', ss.branch_refspec('android17'))
        self.assertEqual('+refs/tags/2026100500:refs/tags/2026100500', ss.branch_refspec('refs/tags/2026100500'))


class PrefetchSettingsTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config/fp6-build.json').read_text())

    def test_the_committed_settings_are_valid(self):
        settings = ss.prefetch_settings(self.config)
        self.assertIn('prebuilts/clang/host/linux-x86', settings['projects'])
        self.assertTrue(settings['reason'])

    def test_invalid_settings_are_refused(self):
        for key, value in (('projects', ['../outside']), ('projects', ['a', 'a']), ('attempts', 0),
                           ('jobs', True), ('low_speed_time_seconds', '300'), ('unknown', 1)):
            with self.subTest(key=key, value=value):
                config = json.loads(json.dumps(self.config))
                config['shallow_prefetch'][key] = value
                with self.assertRaises(bw.UsageError):
                    ss.prefetch_settings(config)
        del self.config['shallow_prefetch']
        with self.assertRaises(bw.UsageError):
            ss.prefetch_settings(self.config)


class PrefetchTargetTests(unittest.TestCase):
    manifest = (b'<manifest><remote name="aosp" fetch="https://android.googlesource.com"/>'
                b'<remote name="diamaneos" fetch="https://github.com/DiamaneOS/" revision="android17"/>'
                b'<default remote="aosp" revision="refs/tags/android-17.0.0_r1"/>'
                b'<project name="platform/prebuilts/clang/host/linux-x86" path="prebuilts/clang/host/linux-x86" '
                b'clone-depth="1"/>'
                b'<project name="platform/prebuilts/misc" path="prebuilts/misc" revision="' + PROJECT_COMMIT.encode()
                + b'" upstream="refs/tags/android-17.0.0_r1"/>'
                b'<project name="device_example" path="device/example" remote="diamaneos"/></manifest>')

    def test_each_project_is_fetched_at_its_commit_or_tag(self):
        lines = []
        targets = ss.prefetch_targets(self.manifest, ['prebuilts/clang/host/linux-x86', 'prebuilts/misc',
                                                      'device/example', 'prebuilts/gone'], lines.append)
        self.assertEqual([
            ss.PrefetchTarget('prebuilts/clang/host/linux-x86', 'platform/prebuilts/clang/host/linux-x86',
                              'https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86',
                              '+refs/tags/android-17.0.0_r1:refs/tags/android-17.0.0_r1',
                              'refs/tags/android-17.0.0_r1^{commit}'),
            ss.PrefetchTarget('prebuilts/misc', 'platform/prebuilts/misc',
                              'https://android.googlesource.com/platform/prebuilts/misc', PROJECT_COMMIT,
                              PROJECT_COMMIT + '^{commit}')], targets)
        self.assertIn('device/example: follows android17', lines[0])
        self.assertIn('prebuilts/gone: not in the manifest', lines[1])

    def test_a_remote_without_https_is_refused(self):
        with self.assertRaisesRegex(bw.BuildStepError, 'no HTTPS fetch URL'):
            ss.prefetch_targets(self.manifest.replace(b'https://android', b'http://android'), ['prebuilts/misc'])


class PrefetchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        git(self.remote, 'init', '-q', '-b', 'main')
        (self.remote / 'clang').write_text('old\n')
        git(self.remote, 'add', 'clang')
        git(self.remote, 'commit', '-q', '-m', 'old')
        (self.remote / 'clang').write_text('new\n')
        git(self.remote, 'commit', '-q', '-am', 'release')
        git(self.remote, 'tag', '-a', 'r1', '-m', 'release')
        self.commit = git(self.remote, 'rev-parse', 'HEAD')
        self.src = self.root / 'src'
        self.settings = json.loads((ROOT / 'config/fp6-build.json').read_text())['shallow_prefetch']
        self.url = 'file://' + str(self.remote)
        self.lines = []

    def target(self, refspec, want):
        return ss.PrefetchTarget('prebuilts/clang/host/linux-x86', 'platform/prebuilts/clang/host/linux-x86',
                                 self.url, refspec, want)

    def test_a_new_project_is_fetched_shallow_into_its_object_directory(self):
        target = self.target('+refs/tags/r1:refs/tags/r1', 'refs/tags/r1^{commit}')
        ss.fetch_target(self.src, target, self.settings, self.lines.append)
        gitdir, objdir = ss.git_directories(self.src, target)
        self.assertFalse(gitdir.exists())
        self.assertTrue((objdir / 'shallow').is_file())
        self.assertEqual(self.commit, git(objdir, 'rev-parse', 'refs/tags/r1^{commit}'))
        self.assertEqual('1', git(objdir, 'rev-list', '--count', '--all'))
        self.assertFalse((objdir / 'hooks').exists())
        self.lines.clear()
        ss.fetch_target(self.src, target, self.settings, self.lines.append, run=self.refuse_fetch)
        self.assertEqual(['    prebuilts/clang/host/linux-x86: already fetched'], self.lines)

    def refuse_fetch(self, argv, timeout=3600):
        if 'fetch' in argv:
            raise AssertionError('fetched again')
        return ss.git(argv, timeout)

    def test_an_existing_git_directory_gets_the_fetch(self):
        # repo's layout: the git directory shares the object directory's objects.
        target = self.target(self.commit, self.commit + '^{commit}')
        gitdir, objdir = ss.git_directories(self.src, target)
        subprocess.run(['git', 'init', '-q', '--bare', str(objdir)], check=True)
        subprocess.run(['git', 'init', '-q', '--bare', str(gitdir)], check=True)
        shutil.rmtree(gitdir / 'objects')
        (gitdir / 'objects').symlink_to(objdir / 'objects')
        ss.fetch_target(self.src, target, self.settings, self.lines.append)
        self.assertTrue((gitdir / 'shallow').is_file())
        self.assertFalse((objdir / 'shallow').exists())
        self.assertEqual('commit', git(objdir, 'cat-file', '-t', self.commit))

    def fake_git(self, failures):
        calls, done = [], []

        def run(argv, timeout=3600):
            argv = [str(a) for a in argv]
            calls.append(argv)
            if 'fetch' in argv:
                if len([c for c in calls if 'fetch' in c]) <= failures:
                    pack = Path(argv[1]) / 'objects/pack'
                    pack.mkdir(parents=True, exist_ok=True)
                    (pack / 'tmp_pack_broken').write_bytes(b'partial')
                    return subprocess.CompletedProcess(argv, 128, '', 'error: RPC failed; curl 28 too slow')
                done.append(True)
                return subprocess.CompletedProcess(argv, 0, '', '')
            if 'rev-parse' in argv:
                return subprocess.CompletedProcess(argv, 0 if done else 1, '', '')
            return ss.git(argv, timeout)
        return run, calls

    def test_a_broken_off_fetch_is_retried_with_a_low_speed_abort(self):
        run, calls = self.fake_git(failures=2)
        waits = []
        target = self.target(self.commit, self.commit + '^{commit}')
        ss.fetch_target(self.src, target, self.settings, self.lines.append, run=run,
                        wait=lambda seconds: waits.append(seconds) or False)
        fetches = [c for c in calls if 'fetch' in c]
        self.assertEqual(3, len(fetches))
        for option in ('http.version=HTTP/1.1', 'http.lowSpeedLimit=1000', 'http.lowSpeedTime=300', 'gc.auto=0'):
            self.assertIn(option, fetches[0])
        self.assertEqual(['fetch', '--depth=1', '--no-tags', '--quiet', self.url, self.commit],
                         fetches[0][fetches[0].index('fetch'):])
        self.assertEqual([60, 60], waits)
        _gitdir, objdir = ss.git_directories(self.src, target)
        self.assertEqual([], list((objdir / 'objects/pack').glob('tmp_pack_*')))
        self.assertIn('    prebuilts/clang/host/linux-x86: fetching at depth 1 (attempt 3 of 8)', self.lines)

    def test_the_attempts_are_bounded(self):
        run, calls = self.fake_git(failures=100)
        waits = []
        with self.assertRaisesRegex(bw.BuildStepError, 'did not finish in 8 attempts: error: RPC failed'):
            ss.fetch_target(self.src, self.target(self.commit, self.commit + '^{commit}'), self.settings,
                            self.lines.append, run=run, wait=lambda seconds: waits.append(seconds) or False)
        self.assertEqual(8, len([c for c in calls if 'fetch' in c]))
        self.assertEqual(7, len(waits))

    def test_every_failed_project_is_reported(self):
        good = self.target(self.commit, self.commit + '^{commit}')
        bad = ss.PrefetchTarget('prebuilts/misc', 'platform/prebuilts/misc', 'file:///nonexistent', self.commit,
                                self.commit + '^{commit}')
        settings = dict(self.settings, attempts=2, retry_delay_seconds=0)
        with self.assertRaisesRegex(bw.BuildStepError, 'prebuilts/misc: the depth-1 fetch did not finish in 2'):
            ss.prefetch(self.src, [bad, good], settings, self.lines.append)
        self.assertTrue((ss.git_directories(self.src, good)[1] / 'shallow').is_file())


class RenamedProjectTests(unittest.TestCase):
    """A project the manifest now takes from another repository at the same path."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.src = Path(temp.name).resolve()
        self.projects = ss.manifest_projects(
            b'<manifest><remote name="r" fetch="https://example.invalid/"/><default remote="r" revision="main"/>'
            b'<project name="platform_external_icu" path="external/icu"/>'
            b'<project name="platform/system/core" path="system/core"/>'
            b'<project name="vendor_x" path="vendor/x"/><project name="vendor_x_sub" path="vendor/x/sub"/></manifest>')

    def checkout(self, path, name):
        """repo's layout: the git directory's objects link to the named project's object directory."""
        gitdir = self.src / '.repo/projects' / (path + '.git')
        objdir = self.src / '.repo/project-objects' / (name + '.git')
        (objdir / 'objects').mkdir(parents=True)
        gitdir.mkdir(parents=True)
        (gitdir / 'objects').symlink_to(os.path.relpath(objdir / 'objects', gitdir))
        (self.src / path).mkdir(parents=True, exist_ok=True)
        (self.src / path / 'file.c').write_text('x\n')
        return gitdir, objdir

    def status(self, output):
        calls = []
        def run(argv, timeout=3600):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, output, '')
        return run, calls

    def test_clean_checkout_of_the_old_project_is_removed(self):
        gitdir, old_objects = self.checkout('external/icu', 'platform/external/icu')
        self.checkout('system/core', 'platform/system/core')
        run, calls = self.status('')
        removed = ss.clear_renamed(self.src, self.projects, run=run)
        self.assertEqual([('external/icu', 'platform/external/icu', 'platform_external_icu')], removed)
        self.assertFalse(gitdir.exists() or (self.src / 'external/icu').exists())
        self.assertTrue(old_objects.is_dir())
        self.assertTrue((self.src / 'system/core/file.c').is_file())
        self.assertEqual(1, len(calls))
        self.assertEqual([], ss.clear_renamed(self.src, self.projects, run=run))

    def test_changed_or_nesting_checkout_stops_the_sync(self):
        self.checkout('external/icu', 'platform/external/icu')
        run, _ = self.status(' M file.c\n')
        with self.assertRaisesRegex(bw.BuildStepError, 'external/icu: .* has changes'):
            ss.clear_renamed(self.src, self.projects, run=run)
        self.assertTrue((self.src / 'external/icu/file.c').is_file())
        (self.src / 'external/icu').rename(self.src / 'kept')
        self.checkout('vendor/x', 'old_vendor_x')
        with self.assertRaisesRegex(bw.BuildStepError, 'vendor/x: .* nested'):
            ss.clear_renamed(self.src, {k: v for k, v in self.projects.items() if k != 'external/icu'},
                             run=self.status('')[0])


class HalfInitialisedTests(unittest.TestCase):
    """Git directories an interrupted sync created but never filled."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.src = Path(temp.name).resolve()
        names = {'a': 'a', 'shallow': 'shallow', 'loose': 'loose', 'packed': 'packed', 'shared1': 'shared',
                 'shared2': 'shared', 'checkout': 'checkout', 'missing': 'missing', 'n': 'n', 'n/sub': 'sub'}
        projects = ''.join(f'<project name="{name}" path="{path}"/>' for path, name in names.items())
        self.projects = ss.manifest_projects(
            f'<manifest><remote name="r" fetch="https://example.invalid/"/><default remote="r" revision="main"/>'
            f'{projects}</manifest>'.encode())

    def gitdir(self, path, name=None):
        """The layout repo leaves when its first fetch breaks off."""
        gitdir = self.src / '.repo/projects' / (path + '.git')
        objdir = self.src / '.repo/project-objects' / ((name or path) + '.git')
        (objdir / 'objects/pack').mkdir(parents=True, exist_ok=True)
        (objdir / 'objects/pack/tmp_pack_abc').write_bytes(b'partial')
        for part in ('refs/heads', 'refs/tags'):
            (gitdir / part).mkdir(parents=True)
        (gitdir / 'packed-refs').write_text('# pack-refs with: peeled fully-peeled sorted \n')
        (gitdir / 'objects').symlink_to(objdir / 'objects')
        return gitdir, objdir

    def test_only_git_directories_without_refs_or_shallow_file_are_removed(self):
        a, a_objects = self.gitdir('a')
        worktree = self.src / 'a'
        worktree.mkdir()
        (worktree / '.git').symlink_to(a)
        (self.gitdir('shallow')[0] / 'shallow').write_text(COMMIT + '\n')
        loose = self.gitdir('loose')[0]
        (loose / 'refs/tags/r1').write_text(COMMIT + '\n')
        packed = self.gitdir('packed')[0]
        (packed / 'packed-refs').write_text(f'# pack-refs with: peeled\n{COMMIT} refs/tags/r1\n')
        shared, shared_objects = self.gitdir('shared1', 'shared')
        self.gitdir('checkout')
        (self.src / 'checkout').mkdir()
        (self.src / 'checkout/file.c').write_text('kept\n')
        n = self.gitdir('n')[0]
        (self.gitdir('n/sub', 'sub')[0] / 'shallow').write_text(COMMIT + '\n')
        (self.src / 'n/sub').mkdir(parents=True)
        (self.src / 'n/sub/file.c').write_text('nested project\n')

        cleared = ss.clear_half_initialised(self.src, self.projects)

        self.assertEqual(['a', 'n', 'shared1'], cleared)
        self.assertFalse(a.exists() or a_objects.exists() or worktree.exists())
        self.assertFalse(shared.exists())
        self.assertTrue(shared_objects.is_dir())  # shared2 has the same object directory
        self.assertFalse(n.exists())
        self.assertTrue((self.src / 'n/sub/file.c').is_file())
        for path in ('shallow', 'loose', 'packed', 'checkout', 'n/sub'):
            self.assertTrue((self.src / '.repo/projects' / (path + '.git')).is_dir(), path)
        self.assertEqual([], ss.clear_half_initialised(self.src, self.projects))

    def test_refs_are_read_without_git(self):
        gitdir = self.gitdir('a')[0]
        self.assertFalse(ss.has_refs(gitdir))
        (gitdir / 'packed-refs').write_text('# header\n^' + COMMIT + '\n')
        self.assertFalse(ss.has_refs(gitdir))
        (gitdir / 'refs/heads/main').write_text(COMMIT + '\n')
        self.assertTrue(ss.has_refs(gitdir))


if __name__ == '__main__':
    unittest.main()
