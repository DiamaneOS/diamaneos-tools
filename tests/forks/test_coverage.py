"""The coverage check finds DiamaneOS repositories the fork registry does not watch."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import forks

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

FORK = {'id': 'frameworks-base', 'slug': 'platform_frameworks_base', 'path': 'frameworks/base', 'branch': 'android17',
        'upstream': {'url': 'https://example.invalid/upstream/platform_frameworks_base', 'ref': '1', 'kind': 'tag'}}
SERVER = {'id': 'server', 'slug': 'server-config', 'path': 'servers/config', 'branch': 'main', 'scope': 'server',
          'upstream': {'url': 'https://example.invalid/upstream/server', 'ref': 'main', 'kind': 'branch'}}
MIRROR = {'id': 'example-mirror', 'url': 'https://example.invalid/upstream/mirror',
          'follow': {'kind': 'branch', 'ref': 'main'},
          'pin': {'repository': 'manifest', 'file': 'diamaneos.xml', 'xml_project_path': 'hardware/example/mirror'}}
OWN = [{'name': 'device_example', 'reason': 'Device configuration.'},
       {'name': 'example-tools', 'reason': 'These tools.'}]


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def registry(self, **changes):
        data = {'schema_version': 2, 'forks': [FORK, SERVER], 'own_projects': OWN, 'sources': [MIRROR]}
        data.update(changes)
        path = Path(self.tmp.name) / 'forks.json'
        path.write_text(json.dumps(data))
        return path

    def run_main(self, *arguments, config=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = forks.main(['--config', str(config or self.registry()), 'coverage', *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_manifest_reading_follows_includes_removals_and_defaults(self):
        projects = {p['path']: p for p in forks.read_manifest(FIXTURES / 'default.xml')}
        # The fork replaces the upstream project, a removal by path drops one, the default remote fills in.
        self.assertEqual('diamaneos', projects['frameworks/base']['remote'])
        self.assertNotIn('packages/apps/Old', projects)
        self.assertEqual('upstream', projects['external/thing']['remote'])
        self.assertEqual('vendor', projects['vendor/example/hal']['remote'])
        self.assertEqual(1, sum(1 for p in forks.read_manifest(FIXTURES / 'default.xml')
                                if p['path'] == 'frameworks/base'))

    def test_extend_project_can_move_a_project_onto_the_remote(self):
        projects = forks.read_manifest(FIXTURES / 'extended.xml')
        self.assertEqual([('platform_packages_modules_New', 'diamaneos')], [(p['name'], p['remote']) for p in projects])

    def test_every_project_on_the_remote_is_covered(self):
        forks_, sources, own = forks.load_all(self.registry())
        result = forks.coverage(FIXTURES / 'default.xml', forks_, sources, own)
        self.assertEqual([], result['uncovered'])
        self.assertEqual([], result['path_differs'])
        self.assertEqual({'platform_frameworks_base': 'fork', 'hardware_example_mirror': 'source',
                          'device_example': 'own', 'example-tools': 'own'},
                         {c['name']: c['by'] for c in result['covered']})
        self.assertNotIn('not_on_org', result)
        code, out, err = self.run_main('--manifest', str(FIXTURES / 'diamaneos.xml'))
        self.assertEqual(0, code, err)
        self.assertEqual('', err)
        self.assertEqual(4, len(json.loads(out)['covered']))

    def test_an_unwatched_project_fails(self):
        code, out, err = self.run_main('--manifest', str(FIXTURES / 'missing.xml'))
        self.assertEqual(1, code)
        result = json.loads(out)
        self.assertEqual([{'name': 'platform_packages_modules_New', 'path': 'packages/modules/New'}], result['uncovered'])
        self.assertEqual([{'name': 'platform_frameworks_base', 'manifest': 'frameworks/base-moved',
                           'registry': 'frameworks/base'}], result['path_differs'])
        self.assertIn('uncovered: platform_packages_modules_New', err)
        self.assertIn('path_differs: platform_frameworks_base', err)

    def test_a_mirror_needs_its_source_pin(self):
        code, out, _ = self.run_main('--manifest', str(FIXTURES / 'diamaneos.xml'), config=self.registry(sources=[]))
        self.assertEqual(1, code)
        self.assertEqual(['hardware_example_mirror'], [p['name'] for p in json.loads(out)['uncovered']])

    def test_other_remote_name(self):
        # Only the named remote counts. The vendor project is a plain upstream there.
        code, out, _ = self.run_main('--manifest', str(FIXTURES / 'diamaneos.xml'), '--remote', 'vendor')
        self.assertEqual(1, code)
        self.assertEqual(['platform/vendor/hal'], [p['name'] for p in json.loads(out)['uncovered']])

    def test_no_project_on_the_remote_is_an_error(self):
        code, out, _ = self.run_main('--manifest', str(FIXTURES / 'upstream-only.xml'))
        self.assertEqual(2, code)
        self.assertIn('no project of the manifest is on the remote diamaneos', out)
        code, out, _ = self.run_main('--manifest', str(FIXTURES / 'absent.xml'))
        self.assertEqual(2, code)
        self.assertIn('cannot read manifest', out)

    def test_organisation_comparison(self):
        forks_, sources, own = forks.load_all(self.registry())
        org = [{'name': 'platform_frameworks_base', 'fork': True, 'archived': False},
               {'name': 'device_example', 'fork': False, 'archived': False},
               {'name': 'hardware_example_mirror', 'fork': False, 'archived': False},
               {'name': 'platform_packages_modules_New', 'fork': True, 'archived': False},
               {'name': 'kernel_old', 'fork': True, 'archived': True},
               {'name': 'website', 'fork': False, 'archived': False}]
        result = forks.coverage(FIXTURES / 'diamaneos.xml', forks_, sources, own, org_repos=org)
        # The server fork and the tools are missing from the organisation. Only live GitHub forks must be watched.
        self.assertEqual(['example-tools', 'server-config'], result['not_on_org'])
        self.assertEqual(['platform_packages_modules_New'], result['unwatched_org_forks'])
        complete = org[:3] + [{'name': 'example-tools', 'fork': False, 'archived': False},
                              {'name': 'server-config', 'fork': True, 'archived': False}]
        result = forks.coverage(FIXTURES / 'diamaneos.xml', forks_, sources, own, org_repos=complete)
        self.assertEqual(([], []), (result['not_on_org'], result['unwatched_org_forks']))

    def test_organisation_flag_lists_through_gh(self):
        listed = subprocess.CompletedProcess([], 0, stdout='platform_frameworks_base\ttrue\tfalse\n'
                                                           'device_example\tfalse\tfalse\n', stderr='')
        with mock.patch.object(forks.subprocess, 'run', return_value=listed) as run:
            code, out, err = self.run_main('--manifest', str(FIXTURES / 'diamaneos.xml'), '--org', 'Example')
        self.assertEqual(['gh', 'api', '--paginate', 'orgs/Example/repos?per_page=100&type=all'], run.call_args[0][0][:4])
        self.assertEqual(1, code)
        self.assertEqual(['example-tools', 'server-config'], json.loads(out)['not_on_org'])
        self.assertIn('not_on_org: server-config', err)

    def test_organisation_listing_failures(self):
        with mock.patch.object(forks.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(forks.ForkError, 'needs gh'):
                forks.org_repositories('Example')
        failed = subprocess.CompletedProcess([], 1, stdout='', stderr='HTTP 404')
        with mock.patch.object(forks.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(forks.ForkError, 'HTTP 404'):
                forks.org_repositories('Example')
        empty = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with mock.patch.object(forks.subprocess, 'run', return_value=empty):
            with self.assertRaisesRegex(forks.ForkError, 'no repositories'):
                forks.org_repositories('Example')
        with self.assertRaisesRegex(forks.ForkError, 'invalid organisation'):
            forks.org_repositories('a/b')

    def test_invalid_own_projects_rejected(self):
        for own, message in (([{'name': 'device_example'}], 'invalid own project'),
                             ([{'name': 'device_example', 'reason': ' '}], 'invalid own project'),
                             ([{'name': 'bad name', 'reason': 'x'}], 'invalid own project'),
                             (OWN + [OWN[0]], 'invalid own project'),
                             ([{'name': 'platform_frameworks_base', 'reason': 'x'}], 'also a fork'),
                             ({'name': 'device_example'}, 'invalid own projects')):
            with self.subTest(own=own):
                with self.assertRaisesRegex(forks.ForkError, message):
                    forks.load_all(self.registry(own_projects=own))

    def test_committed_own_projects(self):
        forks_, _, own = forks.load_all()
        names = [project['name'] for project in own]
        self.assertEqual(len(set(names)), len(names))
        self.assertIn('diamaneos-tools', names)
        self.assertFalse({f['slug'] for f in forks_} & set(names))


if __name__ == '__main__':
    unittest.main()
