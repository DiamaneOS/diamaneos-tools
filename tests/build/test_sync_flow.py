"""build sync end to end with a stand-in repo tool and local repositories.

Covers the move from a workspace made with the old overlay flow: its local
manifest and its generated kernel tree must be moved aside before repo checks
out the kernel prebuilts project at the same path.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build, build_steps as steps, build_workspace as bw

FAKE_REPO = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Enough of repo for build sync: init, sync [-m FILE] and manifest [-r] on local repositories."""
    import json, os, subprocess, sys
    import xml.etree.ElementTree as ET
    urls = json.loads(os.environ['FAKE_REPO_URLS'])

    def git(*args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def manifest():
        if os.path.isdir('.repo/local_manifests') and os.listdir('.repo/local_manifests'):
            sys.exit('error: local manifest names a project the manifest does not have')
        return ET.parse('.repo/manifests/default.xml').getroot()

    command, args = sys.argv[1], sys.argv[2:]
    if command == 'init':
        url, branch = args[args.index('-u') + 1], args[args.index('-b') + 1]
        option = lambda name: next(a.split('=', 1)[1] for a in args if a.startswith(name + '='))
        for path, origin in (('.repo/manifests', url), ('.repo/repo', option('--repo-url'))):
            if not os.path.isdir(path + '/.git'):
                os.makedirs(path, exist_ok=True)
                git('init', '-q', cwd=path)
                git('remote', 'add', 'origin', origin, cwd=path)
            git('remote', 'set-url', 'origin', origin, cwd=path)
        git('fetch', '-q', urls[url], '+refs/heads/%s:refs/remotes/origin/%s' % (branch, branch), cwd='.repo/manifests')
        git('checkout', '-q', '--detach', 'refs/remotes/origin/' + branch, cwd='.repo/manifests')
        git('fetch', '-q', '--tags', urls[option('--repo-url')], cwd='.repo/repo')
        git('checkout', '-q', '--detach', option('--repo-rev'), cwd='.repo/repo')
    elif command == 'sync':
        root = ET.parse(args[args.index('-m') + 1]).getroot() if '-m' in args else manifest()
        fetch = {r.get('name'): r for r in root.findall('remote')}
        default = root.find('default')
        for project in root.findall('project'):
            remote = fetch[project.get('remote', default.get('remote'))]
            revision = project.get('revision', remote.get('revision'))
            path = project.get('path', project.get('name'))
            if not os.path.exists(path + '/.git'):
                if os.path.isdir(path) and os.listdir(path):
                    sys.exit('error: cannot initialize work tree in non-empty directory ' + path)
                os.makedirs(path, exist_ok=True)
                git('init', '-q', cwd=path)
            git('fetch', '-q', urls[remote.get('fetch') + project.get('name')],
                '+refs/heads/*:refs/remotes/%s/*' % remote.get('name'), cwd=path)
            commit = len(revision) == 40 and all(c in '0123456789abcdef' for c in revision)
            target = revision if commit else 'refs/remotes/%s/%s' % (remote.get('name'), revision)
            git('checkout', '-q', '--detach', target, cwd=path)
    elif command == 'manifest':
        root = manifest()
        if '-r' in args:
            for project in root.findall('project'):
                project.set('revision', git('rev-parse', 'HEAD', cwd=project.get('path', project.get('name'))))
        sys.stdout.write(ET.tostring(root, encoding='unicode'))
    ''')


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                           '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def remote(path, files, branch='android17'):
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', branch)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, 'add', '-A')
    git(path, 'commit', '-qm', 'fixture')
    return git(path, 'rev-parse', 'HEAD')


class SyncFlowTests(unittest.TestCase):
    def setUp(self):
        self.lines = []
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        remotes = self.root / 'remotes'
        base = 'https://example.invalid/'
        self.example = remotes / 'device_example'
        remote(self.example, {'example.mk': 'PRODUCT := example\n'})
        self.kernel_commit = remote(remotes / 'kernels', {'Image': 'kernel\n', 'BoardConfigKernel.mk': '\n'})
        manifest = (f'<manifest><remote name="diamaneos" fetch="{base}" revision="android17"/>'
                    '<default remote="diamaneos"/>'
                    '<project name="device_example" path="device/example"/>'
                    '<project name="device_fairphone_FP6-kernels" path="device/fairphone/FP6-kernel" '
                    'clone-depth="1"/></manifest>')
        self.manifest_repository = remotes / 'manifest'
        self.manifest_commit = remote(self.manifest_repository, {'default.xml': manifest})
        tool = remotes / 'git-repo'
        remote(tool, {'repo': 'repo tool\n'}, branch='main')
        git(tool, 'tag', '-a', 'v-test', '-m', 'test release')
        urls = {base + 'platform_manifest.git': str(remotes / 'manifest'), base + 'git-repo': str(tool),
                base + 'device_example': str(self.example), base + 'device_fairphone_FP6-kernels': str(remotes / 'kernels')}
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        environment['manifest']['url'] = base + 'platform_manifest.git'
        environment['upstream']['repo_tool'].update(url=base + 'git-repo', release_tag='v-test',
                                                    tag_object=git(tool, 'rev-parse', 'v-test^{tag}'),
                                                    peeled_commit=git(tool, 'rev-parse', 'v-test^{}'))
        self.environment = self.root / 'environment.json'
        self.environment.write_text(json.dumps(environment))
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'repo').write_text(FAKE_REPO)
        (bin_dir / 'repo').chmod(0o755)
        for patcher in (mock.patch.dict(os.environ, {'FAKE_REPO_URLS': json.dumps(urls),
                                                     'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']}),
                        mock.patch.object(build, '_run', side_effect=self.run_without_signatures)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ctx = self.context()
        self.src = self.ctx.workspace.src

    def context(self, **kw):
        values = dict(workspace=str(self.root / 'ws'), environment=str(self.environment), variant=None, jobs=1,
                      allow_network=True, factory_zip=None, shallow=False)
        values.update(kw)
        return steps.make_context(argparse.Namespace(**values), self.lines.append)

    original_run = staticmethod(build._run)

    def run_without_signatures(self, command, cwd=None, env=None, timeout=120):
        # The stand-in repo tool's tag is unsigned; everything else runs for real.
        if 'verify-tag' in command:
            return subprocess.CompletedProcess(command, 0, b'', b'Good signature\n')
        return self.original_run(command, cwd=cwd, env=env, timeout=timeout)

    def sync(self, ctx=None):
        self.ctx = ctx or self.context()
        plan = steps.plan_sync(self.ctx)
        runner = bw.Runner(True, lambda *a: None)
        self.ctx.workspace.logs.mkdir(parents=True, exist_ok=True)
        log = self.ctx.workspace.new_log('sync')
        for action in plan.actions:
            runner.run(action, log)
        return plan.outputs()

    def old_overlay_workspace(self):
        """What the old flow left: its overlay and a generated kernel tree."""
        local = self.src / '.repo/local_manifests'
        local.mkdir(parents=True)
        (local / 'diamaneos.xml').write_text('<manifest><remove-project name="old"/></manifest>')
        kernel = self.src / 'device/fairphone/FP6-kernel'
        kernel.mkdir(parents=True)
        (kernel / 'Image').write_text('generated kernel\n')
        (self.src / '.repo/diamaneos-generated-inputs.json').write_text(json.dumps({'schema_version': 2}))

    def test_sync_moves_the_old_layout_aside_and_records_the_resolved_manifest(self):
        self.old_overlay_workspace()
        outputs = self.sync()
        self.assertEqual(self.kernel_commit, outputs['kernel_prebuilts_commit'])
        self.assertEqual(self.manifest_commit, outputs['manifest_commit'])
        self.assertEqual(2, outputs['project_count'])
        self.assertEqual(bw.sha_file(self.ctx.resolved_manifest), outputs['resolved_manifest_sha256'])
        self.assertTrue((self.src / 'device/fairphone/FP6-kernel/.git').exists())
        self.assertEqual('generated kernel\n',
                         (self.src / '.repo/diamaneos-previous-inputs/kernel/Image').read_text())
        self.assertTrue((self.src / '.repo/diamaneos-previous-local-manifests/diamaneos.xml').is_file())
        self.assertFalse((self.src / '.repo/local_manifests').exists())

    def test_a_new_branch_commit_changes_the_sync_outputs(self):
        first = self.sync()
        (self.example / 'example.mk').write_text('PRODUCT := changed\n')
        git(self.example, 'commit', '-qam', 'change')
        second = self.sync()
        self.assertNotEqual(first['project_map_sha256'], second['project_map_sha256'])
        self.assertEqual(first['manifest_commit'], second['manifest_commit'])
        self.assertEqual(git(self.example, 'rev-parse', 'HEAD'), git(self.src / 'device/example', 'rev-parse', 'HEAD'))

    def test_a_changed_checkout_fails_the_tree_check(self):
        self.sync()
        (self.src / 'device/example/example.mk').write_text('local edit\n')
        with self.assertRaisesRegex(build.BuildError, 'dirty or untracked content: device/example'):
            build.verify_branch_checkout(json.loads(self.environment.read_text()), self.src)

    def move_on(self):
        """The branches move after a build: a project and the manifest get new commits."""
        (self.example / 'example.mk').write_text('PRODUCT := changed\n')
        git(self.example, 'commit', '-qam', 'change')
        (self.manifest_repository / 'README').write_text('newer manifest\n')
        git(self.manifest_repository, 'add', 'README')
        git(self.manifest_repository, 'commit', '-qm', 'newer manifest')
        return git(self.manifest_repository, 'rev-parse', 'HEAD')

    def recorded_build(self):
        """An image set's resolved manifest and build.json, as build package writes them."""
        outputs = self.sync()
        resolved = self.root / 'image/resolved-manifest.xml'
        resolved.parent.mkdir()
        resolved.write_bytes(self.ctx.resolved_manifest.read_bytes())
        record = {'build_id': '20261005-userdebug-0123456789',
                  'environment': {'sha256': self.ctx.environment_sha256}, 'tools': {'commit': None},
                  'manifest': {'url': outputs['manifest_url'], 'branch': outputs['manifest_branch'],
                               'commit': outputs['manifest_commit'], 'file': 'resolved-manifest.xml',
                               'resolved_sha256': outputs['resolved_manifest_sha256']},
                  'source': outputs}
        (self.root / 'image/build.json').write_text(json.dumps(record))
        return outputs, resolved

    def test_a_pinned_sync_reproduces_a_recorded_build_after_the_branches_moved(self):
        recorded, resolved = self.recorded_build()
        old_example = git(self.src / 'device/example', 'rev-parse', 'HEAD')
        newer = self.move_on()
        self.lines.clear()
        record = self.root / 'image/build.json'
        outputs = self.sync(self.context(resolved_manifest=str(resolved), build_json=str(record)))
        for key in steps.SOURCE_KEYS + ('project_count', 'manifest_url', 'manifest_branch'):
            self.assertEqual(recorded[key], outputs[key], key)
        self.assertEqual({'resolved_manifest_sha256': recorded['resolved_manifest_sha256'],
                          'manifest_commit': self.manifest_commit, 'build_id': '20261005-userdebug-0123456789',
                          'build_json_sha256': bw.sha_file(record)},
                         outputs['pinned_manifest'])
        self.assertEqual(old_example, git(self.src / 'device/example', 'rev-parse', 'HEAD'))
        manifests = self.src / '.repo/manifests'
        self.assertEqual(self.manifest_commit, git(manifests, 'rev-parse', 'HEAD'))
        self.assertEqual(newer, git(manifests, 'rev-parse', 'refs/remotes/origin/android17'))
        self.assertTrue(any('the check that it is at the head of android17 does not apply' in line
                            for line in self.lines))
        self.assertEqual(resolved.read_bytes(), self.ctx.pinned_manifest.read_bytes())
        # The android preflight checks the recorded commit too; the branch-head check would fail here.
        environment = json.loads(self.environment.read_text())
        with self.assertRaisesRegex(build.BuildError, 'not at android17'):
            build.verify_branch_checkout(environment, self.src)
        build.verify_branch_checkout(environment, self.src, manifest_commit=self.manifest_commit)
        # A plain sync moves on to the branch heads again.
        latest = self.sync()
        self.assertEqual(newer, latest['manifest_commit'])
        self.assertIsNone(latest['pinned_manifest'])
        self.assertNotEqual(recorded['project_map_sha256'], latest['project_map_sha256'])
        self.assertFalse(self.ctx.pinned_manifest.exists())

    def test_a_pinned_manifest_of_another_manifest_commit_stops_before_repo_sync(self):
        _recorded, resolved = self.recorded_build()
        self.move_on()
        extra = resolved.read_text().replace('</manifest>', f'<project name="device_example" path="device/extra" '
                                                            f'revision="{"a" * 40}" /></manifest>')
        resolved.write_text(extra)
        with self.assertRaisesRegex(bw.BuildStepError, 'does not belong to manifest commit .*device/extra'):
            self.sync(self.context(resolved_manifest=str(resolved), manifest_commit=self.manifest_commit))
        self.assertFalse((self.src / 'device/extra').exists())

    def test_a_tree_that_is_not_the_pinned_manifest_byte_for_byte_fails(self):
        _recorded, resolved = self.recorded_build()
        resolved.write_text(resolved.read_text().replace('><', '>\n<'))
        with self.assertRaisesRegex(bw.BuildStepError, 'is not the pinned resolved manifest'):
            self.sync(self.context(resolved_manifest=str(resolved), manifest_commit=self.manifest_commit))

    def test_another_local_manifest_stops_the_sync_before_repo_runs(self):
        local = self.src / '.repo/local_manifests'
        local.mkdir(parents=True)
        (local / 'mine.xml').write_text('<manifest/>')
        with self.assertRaisesRegex(bw.UsageError, 'mine.xml'):
            self.sync()
        self.assertFalse((self.src / '.repo/manifests').exists())


if __name__ == '__main__':
    unittest.main()
