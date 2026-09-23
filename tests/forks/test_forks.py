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


if __name__ == '__main__':
    unittest.main()
