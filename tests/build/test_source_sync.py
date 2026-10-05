"""Reproducing a recorded resolved manifest. Local repositories only; nothing
uses the network."""
import hashlib
import json
from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()
