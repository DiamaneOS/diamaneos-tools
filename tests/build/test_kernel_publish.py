"""kernel publish: a passed kernel run into a kernel prebuilts checkout."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import kernel_publish as subject
from diamaneos_tools.vendor_extract import sha

README = '''# Fairphone 6 kernel prebuilts

The DiamaneOS kernel for the Fairphone 6.

## This build

- Sources: an older build.
- Tested on a Fairphone 6, October 2026.

## Contents

| Path | What it is |
'''

CANDIDATE = {
    'Image': b'kernel image',
    'dtbo.img': b'overlays',
    'dtbs/fp6.dtb': b'device tree',
    'modules/a.ko': b'module a',
    'modules/b.ko': b'module b',
    'BoardConfigKernel.mk': b'FP6_KERNEL_PATH := device/fairphone/FP6-kernel\n',
    'device-kernel.mk': b'PRODUCT_COPY_FILES += device/fairphone/FP6-kernel/Image:kernel\n',
    'vendor_boot-modules.blocklist': b'blocklist c\n',
}


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, text=True).stdout


class PublishTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / 'kernel/runs/20261004T101500Z-123'
        self.write_run(CANDIDATE)
        self.checkout = self.root / 'prebuilts'
        self.checkout.mkdir()
        git(self.checkout, 'init', '-q')
        (self.checkout / 'README.md').write_text(README)
        (self.checkout / 'modules').mkdir()
        (self.checkout / 'modules/old.ko').write_bytes(b'old module')
        (self.checkout / 'Image').write_bytes(b'old image')
        (self.checkout / 'vendor_dlkm-modules.blocklist').write_bytes(b'old\n')
        git(self.checkout, 'add', '-A')
        git(self.checkout, 'commit', '-qm', 'old build')
        self.user = patch.object(subject.getpass, 'getuser', return_value='builderperson')
        self.host = patch.object(subject.socket, 'gethostname', return_value='buildbox.example')
        self.user.start(); self.host.start()
        self.addCleanup(self.user.stop); self.addCleanup(self.host.stop)

    def write_run(self, files, **result):
        candidate = self.run / 'candidate'
        if candidate.exists():
            import shutil
            shutil.rmtree(candidate)
        for name, data in files.items():
            (candidate / name).parent.mkdir(parents=True, exist_ok=True)
            (candidate / name).write_bytes(data)
        inventory = [{'path': name, 'bytes': len(data), 'sha256': sha(candidate / name)}
                     for name, data in sorted(files.items())]
        (self.run / 'artifacts.json').write_text(json.dumps(inventory))
        values = {'status': 'PASS', 'inventory_sha256': sha(self.run / 'artifacts.json'),
                  'repository': 'https://github.com/DiamaneOS/kernel_qcom-6.1', 'source_commit': 'c6429ea3' + '0' * 32,
                  'linux_version': '6.1.177', 'config_profile': 'production',
                  'tools': {'commit': '3fbed18' + '1' * 33, 'clean': True},
                  'module_count': sum(1 for n in files if n.startswith('modules/')), 'denied_module_count': 47,
                  'dtb_count': sum(1 for n in files if n.startswith('dtbs/')), 'dtbo_count': 95}
        values.update(result)
        (self.run / 'result.json').write_text(json.dumps(values))

    def publish(self):
        return subject.publish(self.run, self.checkout)

    def test_publish_replaces_the_build_and_updates_the_readme_without_committing(self):
        head = git(self.checkout, 'rev-parse', 'HEAD')
        result = self.publish()
        self.assertEqual('PASS', result['status'])
        self.assertFalse(result['committed'])
        self.assertEqual(head, git(self.checkout, 'rev-parse', 'HEAD'))
        for name, data in CANDIDATE.items():
            self.assertEqual(data, (self.checkout / name).read_bytes())
        self.assertFalse((self.checkout / 'modules/old.ko').exists())
        self.assertFalse((self.checkout / 'vendor_dlkm-modules.blocklist').exists())
        readme = (self.checkout / 'README.md').read_text()
        self.assertIn('- Sources: [kernel_qcom-6.1](https://github.com/DiamaneOS/kernel_qcom-6.1) at `c6429ea`, '
                      'Linux 6.1.177.\n', readme)
        self.assertIn('at `3fbed18`, production kernel configuration.', readme)
        self.assertIn('- 2 modules (47 left out by policy), 1 device tree, 95 overlays.\n', readme)
        self.assertIn('- Built 4 October 2026.\n\n## Contents', readme)
        self.assertNotIn('Tested on', readme)
        self.assertNotIn('20261004T', readme)
        self.assertEqual([], [p.name for p in self.checkout.iterdir() if p.name.startswith('.diamaneos-publish')])

    def test_changed_candidate_is_refused_and_the_checkout_is_untouched(self):
        (self.run / 'candidate/modules/a.ko').write_bytes(b'tampered')
        self.assertRaisesRegex(subject.PublishError, 'differs from artifacts.json: modules/a.ko', self.publish)
        self.assertEqual(b'old image', (self.checkout / 'Image').read_bytes())
        (self.run / 'candidate/modules/a.ko').write_bytes(b'module a')
        (self.run / 'candidate/modules/extra.ko').write_bytes(b'x')
        self.assertRaisesRegex(subject.PublishError, 'list different files', self.publish)

    def test_private_keys_and_unexpected_files_are_refused(self):
        self.write_run(dict(CANDIDATE, Image=b'x-----BEGIN PRIVATE KEY-----\nMII'))
        self.assertRaisesRegex(subject.PublishError, 'private key material', self.publish)
        self.write_run(dict(CANDIDATE, **{'signing_key.pem': b'key'}))
        self.assertRaisesRegex(subject.PublishError, 'unexpected file in the kernel build: signing_key.pem', self.publish)
        self.assertEqual(b'old image', (self.checkout / 'Image').read_bytes())

    def test_host_strings_are_refused(self):
        for data, message in ((b'built by builderperson', 'builderperson'), (b'on buildbox', 'buildbox'),
                              (b'/var/lib/example-build/ws', '/var/lib/'), (b'/home/someone/src', '/home/'),
                              (b'/Users/someone/src', '/Users/')):
            with self.subTest(data=data):
                self.write_run(dict(CANDIDATE, **{'modules/a.ko': data}))
                self.assertRaisesRegex(subject.PublishError, 'host (string|path).*' + message, self.publish)
        self.write_run(dict(CANDIDATE, **{'modules/a.ko': b'secret-name'}))
        with self.assertRaisesRegex(subject.PublishError, 'secret-name'):
            subject.publish(self.run, self.checkout, ['secret-name'])

    def test_unrecorded_sources_or_modified_tools_are_refused(self):
        self.write_run(CANDIDATE, tools={'commit': '3' * 40, 'clean': False})
        self.assertRaisesRegex(subject.PublishError, 'modified tools checkout', self.publish)
        self.write_run(CANDIDATE, source_commit=None)
        self.assertRaisesRegex(subject.PublishError, 'does not record source_commit', self.publish)
        self.write_run(CANDIDATE, module_count=7)
        self.assertRaisesRegex(subject.PublishError, 'counts', self.publish)

    def test_dirty_checkout_is_refused(self):
        (self.checkout / 'README.md').write_text(README + 'local edit\n')
        self.assertRaisesRegex(subject.PublishError, 'uncommitted changes', self.publish)
        self.assertEqual(b'old image', (self.checkout / 'Image').read_bytes())

    def test_command_line(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, subject.main(['--run', str(self.run), '--to', str(self.checkout)]))
        self.assertEqual('kernel-publish', json.loads(output.getvalue())['operation'])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, subject.main(['--run', str(self.root / 'missing'), '--to', str(self.checkout)]))


if __name__ == '__main__':
    unittest.main()
