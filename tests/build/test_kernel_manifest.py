"""Kernel repo manifest rendered from the pinned source plan."""

import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from diamaneos_tools import kernel


class KernelManifestTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.changes, self.adaptation = kernel.configuration()
        self.rendered = kernel.repo_manifest()
        self.root = ET.fromstring(self.rendered)

    def test_every_planned_project_appears_once_at_its_pin(self):
        projects = {p.get('path'): p for p in self.root.iter('project')}
        self.assertEqual([r['path'] for r in self.plan['projects']], [p.get('path') for p in self.root.iter('project')])
        patches = {c['path']: c for c in self.changes}
        for row in self.plan['projects']:
            with self.subTest(path=row['path']):
                project = projects[row['path']]
                patch = patches.get(row['path'])
                if patch:
                    owner = urlparse(patch['repository']).path.strip('/').split('/')[0]
                    self.assertEqual(owner.lower(), project.get('remote'))
                    self.assertEqual(patch['repository'].rsplit('/', 1)[1], project.get('name'))
                    self.assertEqual(patch['derived_revision'], project.get('revision'))
                else:
                    self.assertEqual(row['revision'], project.get('revision'))

    def test_remotes_are_declared_and_https(self):
        remotes = {r.get('name'): r.get('fetch') for r in self.root.iter('remote')}
        self.assertEqual(self.plan['source_url'], remotes['fairphone'])
        self.assertEqual('https://github.com/DiamaneOS/', remotes['diamaneos'])
        used = {p.get('remote') or self.root.find('default').get('remote') for p in self.root.iter('project')}
        self.assertLessEqual(used, set(remotes))
        self.assertTrue(all(url.startswith('https://') for url in remotes.values()))

    def test_excluded_legacy_links_are_left_out(self):
        dests = {l.get('dest') for l in self.root.iter('linkfile')}
        for excluded in self.adaptation['excluded_linkfiles']:
            self.assertNotIn(excluded['dest'], dests)
        self.assertIn('kernel_platform/tools/bazel', dests)

    def test_check_accepts_the_rendered_manifest_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'default.xml'
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, kernel.main(['manifest', '--output', str(path)]))
                self.assertEqual(self.rendered, path.read_bytes())
                self.assertEqual(0, kernel.main(['manifest', '--check', str(path)]))
                revision = self.changes[0]['derived_revision'].encode()
                self.assertIn(revision, self.rendered)
                path.write_bytes(self.rendered.replace(revision, b'0' * 40))
                self.assertEqual(2, kernel.main(['manifest', '--check', str(path)]))
                self.assertEqual(2, kernel.main(['manifest']))

    def test_codelinaro_projects_share_one_remote(self):
        remotes = {r.get('name'): r.get('fetch') for r in self.root.iter('remote')}
        self.assertEqual('https://git.codelinaro.org/clo/la/', remotes['codelinaro'])
        projects = {p.get('path'): p for p in self.root.iter('project')}
        patched = {c['path'] for c in self.changes}
        for row in self.plan['projects']:
            if row['path'] not in patched and row.get('url', '').startswith('https://git.codelinaro.org/clo/la/'):
                with self.subTest(path=row['path']):
                    self.assertEqual('codelinaro', projects[row['path']].get('remote'))
                    self.assertEqual(row['url'].removeprefix('https://git.codelinaro.org/clo/la/'),
                                     projects[row['path']].get('name'))


if __name__ == '__main__':
    unittest.main()
