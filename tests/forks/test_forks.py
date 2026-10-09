"""Fork status and update preparation never move the fork branch or push."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import forks


def run(repo, *arguments):
    return subprocess.run(['git', '-C', str(repo), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, name, text, message):
    Path(repo, name).write_text(text)
    run(repo, 'add', name)
    run(repo, 'commit', '-q', '-m', message)
    return run(repo, 'rev-parse', 'HEAD')


class ForkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        config = base / 'gitconfig'
        config.write_text('[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n')
        patcher = mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.upstream = base / 'upstream'
        self.upstream.mkdir()
        run(self.upstream, 'init', '-q')
        self.first = commit(self.upstream, 'a.txt', 'one\n', 'upstream one')
        run(self.upstream, 'branch', 'odm/rc')
        self.root = base / 'forks'
        self.root.mkdir()
        self.fork_repo = self.root / 'hal'
        run(self.root, 'clone', '-q', '-b', 'odm/rc', str(self.upstream), 'hal')
        run(self.fork_repo, 'checkout', '-q', '-b', 'android17')
        self.fork = {'id': 'hal', 'slug': 'hal', 'path': 'x/hal', 'branch': 'android17',
                     'upstream': {'url': str(self.upstream), 'ref': 'odm/rc', 'kind': 'branch'}}

    def advance_upstream(self, name='b.txt', text='two\n'):
        run(self.upstream, 'checkout', '-q', 'odm/rc')
        return commit(self.upstream, name, text, 'upstream next')

    def test_status_reports_patches_and_missing_upstream_commits(self):
        commit(self.fork_repo, 'ours.txt', 'patch\n', 'our patch')
        self.advance_upstream()
        result = forks.status(self.root, self.fork, do_fetch=True)
        self.assertEqual((result['state'], result['patches'], result['behind']), ('update-available', 1, 1))

    def test_update_rebases_patches_into_candidate_without_moving_branch(self):
        ours = commit(self.fork_repo, 'ours.txt', 'patch\n', 'our patch')
        new = self.advance_upstream()
        result = forks.update(self.root, self.fork, today=datetime.date(2026, 9, 23))
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(result['rebased_patches'], 1)
        self.assertEqual(run(self.fork_repo, 'rev-parse', 'android17'), ours)
        candidate = result['candidate']
        self.assertTrue(candidate.startswith('update/20260923-'))
        self.assertEqual(run(self.fork_repo, 'rev-parse', candidate + '~1'), new)
        self.assertEqual(run(self.fork_repo, 'show', candidate + ':ours.txt'), 'patch')
        self.assertEqual(run(self.fork_repo, 'worktree', 'list').count('\n'), 0)

    def test_unpatched_fork_candidate_is_upstream(self):
        new = self.advance_upstream()
        result = forks.update(self.root, self.fork, today=datetime.date(2026, 9, 23))
        self.assertEqual((result['rebased_patches'], run(self.fork_repo, 'rev-parse', result['candidate'])), (0, new))

    def test_conflict_is_reported_and_leaves_no_candidate(self):
        commit(self.fork_repo, 'a.txt', 'ours\n', 'our change')
        self.advance_upstream('a.txt', 'theirs\n')
        result = forks.update(self.root, self.fork, today=datetime.date(2026, 9, 23))
        self.assertEqual((result['state'], result['conflicts']), ('conflict', ['a.txt']))
        self.assertNotIn('update/', run(self.fork_repo, 'branch', '--list'))
        self.assertEqual(run(self.fork_repo, 'worktree', 'list').count('\n'), 0)

    def test_recorded_resolution_settles_a_repeated_conflict(self):
        commit(self.fork_repo, 'a.txt', 'ours\n', 'our change')
        self.advance_upstream('a.txt', 'theirs\n')
        records = Path(self.tmp.name) / 'rerere'
        result = forks.update(self.root, self.fork, rerere=records, today=datetime.date(2026, 10, 8))
        self.assertEqual(result['state'], 'conflict')
        # Resolve it once by hand in a scratch worktree; rerere records the resolution.
        scratch = Path(self.tmp.name) / 'scratch'
        run(self.fork_repo, 'worktree', 'add', '-q', '--detach', str(scratch), 'android17')
        subprocess.run(['git', '-C', str(scratch), 'rebase', '-q', 'refs/diamaneos/upstream'], capture_output=True)
        Path(scratch, 'a.txt').write_text('both\n')
        run(scratch, 'add', 'a.txt')
        run(scratch, 'rerere')
        run(scratch, 'rebase', '--abort')
        run(self.fork_repo, 'worktree', 'remove', '--force', str(scratch))
        forks.save_resolutions(self.fork_repo, records / 'hal')
        # A fresh clone gets the resolution only from the records directory.
        run(self.root, 'clone', '-q', '-b', 'android17', str(self.fork_repo), 'hal2')
        clone = dict(self.fork, slug='hal2')
        os.rename(records / 'hal', records / 'hal2')
        result = forks.update(self.root, clone, rerere=records, today=datetime.date(2026, 10, 8))
        self.assertEqual((result['state'], result['rerere_resolved']), ('prepared', ['a.txt']))
        self.assertEqual(run(self.root / 'hal2', 'show', result['candidate'] + ':a.txt'), 'both')

    def test_rewritten_upstream_replays_only_patches_after_the_pin(self):
        # Upstream release v1 carries its own patch; the fork adds ours on v1.
        run(self.upstream, 'checkout', '-q', 'odm/rc')
        commit(self.upstream, 'up.txt', 'upstream patch v1\n', 'upstream own patch')
        run(self.upstream, 'tag', 'v1')
        run(self.fork_repo, 'fetch', '-q', str(self.upstream), 'refs/tags/v1')
        run(self.fork_repo, 'reset', '-q', '--hard', 'FETCH_HEAD')
        commit(self.fork_repo, 'ours.txt', 'patch\n', 'our patch')
        # v2 rewrites history: a new base with the upstream patch applied again, changed.
        run(self.upstream, 'checkout', '-q', '--orphan', 'rewritten', self.first)
        commit(self.upstream, 'b.txt', 'two\n', 'new base')
        commit(self.upstream, 'up.txt', 'upstream patch v2\n', 'upstream own patch')
        run(self.upstream, 'tag', 'v2')
        self.fork['upstream'] = {'url': str(self.upstream), 'ref': 'v1', 'kind': 'tag'}
        result = forks.update(self.root, self.fork, ref='v2', today=datetime.date(2026, 10, 8))
        self.assertEqual((result['state'], result['rebased_patches'], result['commits']), ('prepared', 1, 1))
        self.assertEqual(run(self.fork_repo, 'show', result['candidate'] + ':up.txt'), 'upstream patch v2')

    def test_branch_fork_replays_only_patches_after_its_newest_contained_release_tag(self):
        # Upstream branch with release tag 2026092500 carrying an upstream patch; the fork adds ours.
        run(self.upstream, 'checkout', '-q', 'odm/rc')
        commit(self.upstream, 'up.txt', 'v1\n', 'upstream own patch')
        run(self.upstream, 'tag', '2026092500')
        run(self.fork_repo, 'fetch', '-q', str(self.upstream), 'odm/rc')
        run(self.fork_repo, 'reset', '-q', '--hard', 'FETCH_HEAD')
        commit(self.fork_repo, 'ours.txt', 'patch\n', 'our patch')
        # Upstream rewrites its branch: new base, its patch again.
        run(self.upstream, 'checkout', '-q', '-B', 'odm/rc', self.first)
        commit(self.upstream, 'b.txt', 'two\n', 'new base')
        commit(self.upstream, 'up.txt', 'v2\n', 'upstream own patch')
        self.fork['newer'] = {'tags': '^([0-9]{10})$'}
        result = forks.update(self.root, self.fork, today=datetime.date(2026, 10, 8))
        self.assertEqual((result['state'], result['rebased_patches'], result['commits']), ('prepared', 1, 1))
        self.assertEqual(run(self.fork_repo, 'show', result['candidate'] + ':up.txt'), 'v2')

    def test_blobless_clone_fetches_upstream_blobless(self):
        run(self.upstream, 'config', 'uploadpack.allowfilter', 'true')
        run(self.upstream, 'config', 'uploadpack.allowanysha1inwant', 'true')
        partial = self.root / 'partial'
        run(self.root, 'clone', '-q', '--filter=blob:none', '-b', 'odm/rc', 'file://' + str(self.upstream), 'partial')
        run(partial, 'checkout', '-q', '-b', 'android17')
        commit(partial, 'ours.txt', 'patch\n', 'our patch')
        new = self.advance_upstream('big.txt', 'upstream file\n')
        fork = dict(self.fork, slug='partial', upstream=dict(self.fork['upstream'], url='file://' + str(self.upstream)))
        result = forks.update(self.root, fork, today=datetime.date(2026, 10, 8))
        self.assertEqual((result['state'], result['rebased_patches']), ('prepared', 1))
        self.assertEqual(run(partial, 'config', '--get', 'remote.diamaneos-upstream.promisor'), 'true')
        self.assertEqual(run(partial, 'rev-parse', result['candidate'] + '~1'), new)

    def test_fixup_commits_fold_into_their_feature(self):
        commit(self.fork_repo, 'feature.txt', 'one\n', 'Add the feature')
        commit(self.fork_repo, 'other.txt', 'x\n', 'Another change')
        commit(self.fork_repo, 'feature.txt', 'one fixed\n', 'fixup! Add the feature')
        self.advance_upstream()
        result = forks.update(self.root, self.fork, today=datetime.date(2026, 10, 8))
        self.assertEqual((result['rebased_patches'], result['commits']), (3, 2))
        log = run(self.fork_repo, 'log', '--format=%s', result['candidate'] + '~2..' + result['candidate'])
        self.assertEqual(log.splitlines(), ['Another change', 'Add the feature'])
        self.assertEqual(run(self.fork_repo, 'show', result['candidate'] + ':feature.txt'), 'one fixed')

    def test_commit_reference_must_match(self):
        self.fork['upstream'] = {'url': str(self.upstream), 'ref': self.first, 'kind': 'commit'}
        self.assertEqual(forks.fetch(self.fork_repo, self.fork), self.first)

    def test_invalid_configuration_rejected(self):
        path = Path(self.tmp.name) / 'forks.json'
        bad = dict(self.fork, upstream={'url': 'x', 'ref': 'abc', 'kind': 'commit'})
        path.write_text(json.dumps({'schema_version': 1, 'forks': [bad]}))
        with self.assertRaises(forks.ForkError):
            forks.load(path)

    def test_committed_configuration_is_valid(self):
        loaded = forks.load()
        self.assertEqual(len({f['slug'] for f in loaded}), len(loaded))

    def test_registry_covers_every_forked_and_patched_repository(self):
        registry, sources = forks.load_registry()
        slugs = {f['slug'] for f in registry}
        patches = json.loads((ROOT / 'config/patches.json').read_text())['patches']
        self.assertLessEqual({p['repository'].rstrip('/').split('/')[-1] for p in patches} - {'device_fairphone_FP6'}, slugs)
        repositories = json.loads((ROOT / 'config/repositories.json').read_text())['repositories']
        # A fork can be planned (created locally, not yet published) before a build uses it.
        forked = {r['slug'] for r in repositories if r['state'] in ('active', 'planned') and r['upstream_url']}
        self.assertEqual(forked - {'platform_manifest'}, slugs)
        self.assertIn('grapheneos-platform', {s['id'] for s in sources})

    def test_kernel_imports_match_their_sources(self):
        registry, sources = forks.load_registry()
        # Only the common kernel stays a fork; the other kernel projects are folders of kernel_qcom-6.1.
        self.assertEqual({'kernel-common'}, {f['id'] for f in registry if f.get('workspace') == 'kernel'})
        record = json.loads((ROOT / 'config/kernel-upstream-fp6.json').read_text())
        kernel_sources = json.loads((ROOT / 'config/kernel-sources-fp6.json').read_text())
        self.assertEqual(kernel_sources['repository'], record['repository'])
        imports = {row['path']: row for row in record['imports']}
        self.assertEqual(len(record['imports']), len(imports))
        pinned = [s for s in sources if s['pin']['file'] == 'config/kernel-upstream-fp6.json']
        self.assertEqual(21, len(pinned))
        self.assertEqual(set(imports), {s['pin']['match']['path'] for s in pinned})
        for source in pinned:
            with self.subTest(source=source['id']):
                row = imports[source['pin']['match']['path']]
                self.assertEqual(('tools', 'branch', row['url']),
                                 (source['pin']['repository'], source['follow']['kind'], source['url']))
                self.assertRegex(row['commit'], '^[0-9a-f]{40}$')
                self.assertIsInstance(row['fairphone_changes'], bool)
                self.assertEqual(row['commit'], forks.pinned_value(self.root, source))
                if 'newer_tags' in source['follow']:
                    release = forks.pinned_value(self.root, source, source['pin']['release_field'])
                    self.assertTrue(release is None or re.fullmatch(source['follow']['newer_tags'], release))
                else:
                    self.assertIsNone(row['release'])

    def test_source_release_tags_need_a_recorded_release(self):
        follow = {'kind': 'branch', 'ref': 'main', 'newer_tags': r'^r([0-9]+)$'}
        pin = {'repository': 'kq', 'file': 'imports.json', 'list': '/imports', 'match': {'path': 'k'}, 'field': 'commit'}
        source = lambda follow, pin: {'id': 'k', 'url': 'https://example.invalid', 'follow': follow, 'pin': pin}
        forks.load_sources([source(follow, dict(pin, release_field='release'))])
        for bad in (source(follow, pin),
                    source({'kind': 'branch', 'ref': 'main'}, dict(pin, release_field='release')),
                    source(follow, {'repository': 'kq', 'file': 'x.json', 'pointer': '/commit', 'release_field': 'release'}),
                    source({'kind': 'tags', 'pattern': r'^r([0-9]+)$', 'newer_tags': r'^r([0-9]+)$'},
                           dict(pin, release_field='release'))):
            with self.subTest(source=bad), self.assertRaisesRegex(forks.ForkError, 'release_field'):
                forks.load_sources([bad])

    def test_imported_source_reports_releases_after_the_recorded_one(self):
        record = Path(self.tmp.name) / 'kq' / 'imports.json'
        record.parent.mkdir()
        imported = run(self.upstream, 'rev-parse', 'odm/rc')
        run(self.upstream, 'tag', 'r1')
        source = {'id': 'k', 'url': 'https://example.invalid',
                  'follow': {'kind': 'branch', 'ref': 'odm/rc', 'newer_tags': r'^r([0-9]+)$'},
                  'pin': {'repository': 'kq', 'file': 'imports.json', 'list': '/imports', 'match': {'path': 'k'},
                          'field': 'commit', 'release_field': 'release'}}
        forks.load_sources([source])
        lister = lambda url: forks.remote_refs(str(self.upstream))

        def check(release):
            record.write_text(json.dumps({'imports': [{'path': 'k', 'commit': imported, 'release': release}]}))
            return forks.check(Path(self.tmp.name), [], [source], lister)[0]

        result = check('r1')
        self.assertEqual(('current', imported, 'r1'), (result['state'], result['pin'], result['release']))
        self.assertNotIn('newer', result)
        run(self.upstream, 'tag', 'r2')
        result = check('r1')
        self.assertEqual(('newer-release', ['r2']), (result['state'], result['newer']))
        self.advance_upstream()
        result = check('r1')
        self.assertEqual(('update-available', ['r2']), (result['state'], result['newer']))
        # With no release recorded, the import predates the whole series.
        self.assertEqual(['r1', 'r2'], check(None)['newer'])

    def test_tools_pins_resolve(self):
        _, sources = forks.load_registry()
        for source in sources:
            if source['pin']['repository'] == 'tools':
                with self.subTest(source=source['id']):
                    self.assertTrue(forks.pinned_value(self.root, source))

    def test_newer_pattern_needs_one_group(self):
        path = Path(self.tmp.name) / 'forks.json'
        bad = dict(self.fork, newer={'branches': '^odm/rc/target/16/fp6$'})
        path.write_text(json.dumps({'schema_version': 2, 'forks': [bad]}))
        with self.assertRaisesRegex(forks.ForkError, 'ordering group'):
            forks.load(path)

    def test_newer_refs_orders_numerically(self):
        refs = {'refs/heads/odm/rc/target/9/fp6': 'a', 'refs/heads/odm/rc/target/16/fp6': 'b',
                'refs/heads/odm/rc/target/17/fp6': 'c', 'refs/heads/odm/rc/qssi/17/fp6': 'd'}
        pattern = forks.re.compile(r'^odm/rc/target/([0-9]+)/fp6$')
        self.assertEqual(['odm/rc/target/17/fp6'], forks.newer_refs(refs, 'refs/heads/', pattern, 'odm/rc/target/16/fp6'))
        tags = {'refs/tags/LA.X.r1-09500-lanai.0': 'a', 'refs/tags/LA.X.r1-10200-lanai.0': 'b'}
        pattern = forks.re.compile(r'^LA\.X\.r1-([0-9]+)-lanai\.0$')
        self.assertEqual(['LA.X.r1-10200-lanai.0'], forks.newer_refs(tags, 'refs/tags/', pattern, 'LA.X.r1-09500-lanai.0'))

    def lister(self):
        return lambda url: forks.remote_refs(url)

    def test_check_reports_current_then_moved_branch(self):
        self.fork['newer'] = {'branches': r'^odm/rc([0-9]*)$'}
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual('current', result['state'])
        self.advance_upstream()
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual('update-available', result['state'])
        self.assertEqual(run(self.upstream, 'rev-parse', 'odm/rc'), result['upstream_head'])

    def test_recorded_server_adoption_needs_no_clone_and_preserves_lookup_failure(self):
        server = dict(self.fork, scope='server', upstream_revision=self.first)
        missing = Path(self.tmp.name) / 'no-checkouts'
        result = forks.check(missing, [server], [], self.lister())[0]
        self.assertEqual(('current', 'server', self.first),
                         (result['state'], result['scope'], result['pin']))
        self.advance_upstream()
        self.assertEqual('update-available', forks.check(missing, [server], [], self.lister())[0]['state'])
        def unavailable(url):
            raise forks.ForkError('synthetic lookup failure')
        failed = forks.check(missing, [server], [], unavailable)[0]
        self.assertEqual(('error', 'server'), (failed['state'], failed['scope']))
        for invalid in ('abc', 7):
            with self.subTest(invalid=invalid), self.assertRaises(forks.ForkError):
                forks.load_forks([dict(server, upstream_revision=invalid)])

    def test_check_reports_newer_branch(self):
        self.fork['newer'] = {'branches': r'^release/([0-9]+)$'}
        self.fork['upstream']['ref'] = 'release/16'
        for name in ('release/9', 'release/16', 'release/17'):
            run(self.upstream, 'branch', name, 'odm/rc')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual(('newer-release', ['release/17']), (result['state'], result['newer']))

    def test_branch_fork_reports_only_release_tags_after_the_latest_it_contains(self):
        self.fork['newer'] = {'tags': r'^([0-9]{4})$'}
        run(self.upstream, 'tag', '1000')                      # on an unrelated line
        run(self.upstream, 'checkout', '-q', '--orphan', 'old')
        commit(self.upstream, 'old.txt', 'x\n', 'old line')
        run(self.upstream, 'tag', '0900')
        run(self.upstream, 'checkout', '-q', 'odm/rc')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual(('current', '1000'), (result['state'], result['latest_release_contained']))
        self.advance_upstream()
        run(self.upstream, 'tag', '1100')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual(['1100'], result['newer'])

    def test_fork_older_than_every_release_reports_them_all(self):
        self.fork['newer'] = {'tags': r'^r([0-9]+)$'}
        self.advance_upstream()
        run(self.upstream, 'tag', 'r1'); run(self.upstream, 'tag', 'r2')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual((None, ['r1', 'r2']), (result['latest_release_contained'], result['newer']))

    def test_check_sources_by_pin(self):
        pinfile = Path(self.tmp.name) / 'pins' / 'pins.json'
        pinfile.parent.mkdir()
        head = run(self.upstream, 'rev-parse', 'odm/rc')
        pinfile.write_text(json.dumps({'files': [{'project': 'p', 'revision': head}], 'tag': 'v1.0'}))
        run(self.upstream, 'tag', 'v1.0'); run(self.upstream, 'tag', 'v1.1')
        branch = {'id': 'b', 'url': 'https://example.invalid', 'follow': {'kind': 'branch', 'ref': 'odm/rc'},
                  'pin': {'repository': 'pins', 'file': 'pins.json', 'list': '/files', 'match': {'project': 'p'}, 'field': 'revision'}}
        tags = {'id': 't', 'url': 'https://example.invalid', 'follow': {'kind': 'tags', 'pattern': r'^v([0-9.]+)$'},
                'pin': {'repository': 'pins', 'file': 'pins.json', 'pointer': '/tag'}}
        manual = {'id': 'm', 'url': 'https://example.invalid', 'follow': {'kind': 'manual', 'note': 'look'},
                  'pin': {'repository': 'pins', 'file': 'pins.json', 'pointer': '/tag'}}
        forks.load_sources([branch, tags, manual])
        lister = lambda url: forks.remote_refs(str(self.upstream))
        states = {r['id']: r for r in forks.check(Path(self.tmp.name), [], [branch, tags, manual], lister)}
        self.assertEqual('current', states['b']['state'])
        self.assertEqual(('newer-release', ['v1.1']), (states['t']['state'], states['t']['newer']))
        self.assertEqual('manual-check', states['m']['state'])
        self.advance_upstream()
        self.assertEqual('update-available', forks.check(Path(self.tmp.name), [], [branch], lister)[0]['state'])

    def test_check_never_fetches_missing_objects(self):
        # A partial clone would otherwise download history to answer "is this commit ours?".
        self.advance_upstream()
        with mock.patch.object(forks.subprocess, 'run', wraps=forks.subprocess.run) as spy:
            forks.check(self.root, [self.fork], [], self.lister())
        local = [c for c in spy.call_args_list if c.args[0][:2] == ['git', '-C']]
        self.assertTrue(local)
        self.assertTrue(all(c.kwargs['env']['GIT_NO_LAZY_FETCH'] == '1' for c in local))

    def test_manifest_xml_pin(self):
        root = Path(self.tmp.name)
        (root / 'manifest').mkdir()
        (root / 'manifest' / 'diamaneos.xml').write_text(
            '<manifest><project name="x" path="vendor/x" remote="codelinaro" revision="' + 'a' * 40 + '" /></manifest>')
        source = {'id': 'x', 'url': 'https://example.invalid', 'follow': {'kind': 'branch', 'ref': 'main'},
                  'pin': {'repository': 'manifest', 'file': 'diamaneos.xml', 'xml_project_path': 'vendor/x'}}
        forks.load_sources([source])
        self.assertEqual('a' * 40, forks.pinned_value(root, source))
        source['pin']['xml_project_path'] = 'vendor/missing'
        with self.assertRaises(forks.ForkError):
            forks.pinned_value(root, source)

    def test_unreachable_upstream_is_an_error_not_a_crash(self):
        self.fork['upstream']['url'] = str(Path(self.tmp.name) / 'missing')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual('error', result['state'])


if __name__ == '__main__':
    unittest.main()
