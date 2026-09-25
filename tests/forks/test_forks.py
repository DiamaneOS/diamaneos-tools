"""Fork status and update preparation never move the fork branch or push."""
import datetime
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
        forked = {r['slug'] for r in repositories if r['state'] == 'active' and r['upstream_url']}
        self.assertEqual(forked - {'platform_manifest'}, slugs)
        self.assertIn('grapheneos-platform', {s['id'] for s in sources})

    def test_kernel_forks_follow_the_source_plan_upstreams(self):
        plan = json.loads((ROOT / 'config/kernel-sources-fp6.json').read_text())
        urls = {p['path']: p.get('url') or plan['source_url'] + p['project'] for p in plan['projects']}
        for fork in forks.load():
            if fork.get('workspace') == 'kernel':
                with self.subTest(fork=fork['id']):
                    self.assertEqual(urls[fork['path']].removesuffix('.git'), fork['upstream']['url'])

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

    def test_unreachable_upstream_is_an_error_not_a_crash(self):
        self.fork['upstream']['url'] = str(Path(self.tmp.name) / 'missing')
        result = forks.check(self.root, [self.fork], [], self.lister())[0]
        self.assertEqual('error', result['state'])


if __name__ == '__main__':
    unittest.main()
