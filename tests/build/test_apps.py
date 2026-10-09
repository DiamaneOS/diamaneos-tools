"""apps: config, signed-versus-unsigned APK comparison, signer parsing and publish."""
import copy
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import apps as subject
from diamaneos_tools.vendor_extract import sha

OURS = 'd' * 64
GRAPHENEOS = '3384c31fce4a7c008a8f7b2652bc48cc4321f1d2c877b18e30a4ed619af12f6b'


def unsigned_apk():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('AndroidManifest.xml', b'manifest')
        archive.writestr(zipfile.ZipInfo('classes.dex'), b'https://apps.diamaneos.de')
    return data.getvalue()


def sign(unsigned, padding=0, pairs=b'\x10\0\0\0\0\0\0\0' + b'\x1a\x87\x09\x71' + b'x' * 12):
    """unsigned plus an APK Signing Block, as apksigner writes it (optionally zero-padded)."""
    end, offset, _ = subject.eocd(unsigned)
    size = len(pairs) + 8 + 16
    block = struct.pack('<Q', size) + pairs + struct.pack('<Q', size) + b'APK Sig Block 42'
    head = unsigned[:offset] + b'\0' * padding + block
    tail = bytearray(unsigned[offset:])
    struct.pack_into('<I', tail, end - offset + 16, len(head))
    return head + bytes(tail)


def verify_output(digest, v4=True, signers=1):
    lines = ['Verified using v1 scheme (JAR signing): false', 'Verified using v2 scheme (APK Signature Scheme v2): true',
             'Verified using v3 scheme (APK Signature Scheme v3): false',
             f'Verified using v4 scheme (APK Signature Scheme v4): {"true" if v4 else "false"}']
    for n in range(1, signers + 1):
        lines.append(f'Signer #{n} certificate SHA-256 digest: {digest}')
    return '\n'.join(lines) + '\n'


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, text=True).stdout


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config/apps.json').read_text())
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'apps.json'

    def load(self, document):
        self.path.write_text(json.dumps(document))
        return subject.load(self.path)

    def test_committed_config_is_valid(self):
        config = subject.load()
        self.assertEqual({'appstore', 'auditor'}, {a['id'] for a in config['apps']})
        self.assertIn(GRAPHENEOS, config['grapheneos_signers'])
        appstore = subject.app_config(config, 'appstore')
        self.assertEqual('https://apps.diamaneos.de', appstore['environment']['REPO_BASE_URL'])
        # The repository key is never pinned here: it comes from the fork or a test key directory.
        self.assertNotIn('REPO_PUBLIC_KEY', appstore['environment'])

    def test_rejects_unpinned_foreign_or_grapheneos_signed_apps(self):
        for change in (lambda a: a.update(commit='android17'),
                       lambda a: a.update(repository='https://github.com/GrapheneOS/AppStore.git'),
                       lambda a: a.update(release_signer=GRAPHENEOS),
                       lambda a: a.update(prebuilt='../external/AppStore'),
                       lambda a: a['environment'].update(path='/x')):
            document = copy.deepcopy(self.config)
            change(document['apps'][0])
            with self.subTest(change=change), self.assertRaises(subject.AppsError):
                self.load(document)

    def test_rejects_an_unpinned_toolchain(self):
        document = copy.deepcopy(self.config)
        document['toolchain']['jdk']['sha256'] = 'latest'
        with self.assertRaises(subject.AppsError):
            self.load(document)


class ApkTests(unittest.TestCase):
    def test_signed_matches_its_unsigned_build(self):
        unsigned = unsigned_apk()
        self.assertTrue(subject.signed_matches_unsigned(sign(unsigned), unsigned))
        self.assertTrue(subject.signed_matches_unsigned(sign(unsigned, padding=1000), unsigned))

    def test_any_other_change_is_caught(self):
        unsigned = unsigned_apk()
        signed = bytearray(sign(unsigned))
        signed[signed.index(b'apps.diamaneos.de')] = ord('X')
        self.assertFalse(subject.signed_matches_unsigned(bytes(signed), unsigned))
        self.assertFalse(subject.signed_matches_unsigned(unsigned, unsigned))
        # Bytes hidden in the padding before the block are not padding.
        padded = bytearray(sign(unsigned, padding=16))
        padded[subject.eocd(unsigned)[1]] = 1
        self.assertFalse(subject.signed_matches_unsigned(bytes(padded), unsigned))

    def test_signer_digest_needs_one_signer_and_v4(self):
        self.assertEqual(OURS, subject.signer_digest(verify_output(OURS)))
        for output in (verify_output(OURS, v4=False), verify_output(OURS, signers=2), 'nothing'):
            with self.subTest(output=output), self.assertRaises(subject.AppsError):
                subject.signer_digest(output)

    def test_test_catalog_key_is_a_signify_public_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / subject.TEST_CATALOG_KEY
            path.write_text('untrusted comment: test key\nRWRlgoQmgCKJ6boDkenw9LnWZSQeouO95xkWreuiGWu1xZo8+xwKpbkz\n')
            self.assertTrue(subject.test_catalog_key(temp).startswith('RW'))
            path.write_text('untrusted comment: PLACEHOLDER\n')
            with self.assertRaises(subject.AppsError):
                subject.test_catalog_key(temp)


class PublishTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = subject.load()
        self.run = self.root / 'apps/runs/appstore-20261009T101500Z'
        self.run.mkdir(parents=True)
        self.unsigned = unsigned_apk()
        (self.run / 'app-release-unsigned.apk').write_bytes(self.unsigned)
        (self.run / 'app-release.apk').write_bytes(sign(self.unsigned))
        (self.run / 'app-release.apk.idsig').write_bytes(b'v4')
        self.result = {'status': 'PASS', 'operation': 'apps-build', 'app': 'appstore', 'package': 'app.grapheneos.apps',
                       'version_code': 3601, 'repository': 'https://github.com/DiamaneOS/AppStore.git',
                       'commit': '7' * 40, 'tools': {'commit': '8' * 40, 'clean': True},
                       'unsigned_sha256': sha(self.run / 'app-release-unsigned.apk'),
                       'signed': {'key': 'test', 'signer_sha256': OURS, 'sha256': sha(self.run / 'app-release.apk'),
                                  'idsig_sha256': sha(self.run / 'app-release.apk.idsig')}}
        self.write_result()
        self.checkout = self.root / 'external_AppStore'
        (self.checkout / 'prebuilt').mkdir(parents=True)
        (self.checkout / 'Android.bp').write_text('android_app_import {}\n')
        (self.checkout / 'prebuilt/app-release.apk').write_bytes(b'grapheneos build')
        git(self.checkout, 'init', '-q')
        git(self.checkout, 'add', '-A')
        git(self.checkout, 'commit', '-q', '-m', 'version 36')

    def write_result(self):
        (self.run / 'result.json').write_text(json.dumps(self.result))

    def publish(self, digest=OURS, **kw):
        return subject.publish(self.run, self.checkout, config=self.config,
                               apksigner=lambda apk, sig: verify_output(digest), **kw)

    def test_publishes_a_test_build_and_records_it(self):
        summary = self.publish(test=True)
        self.assertEqual(('test', False), (summary['key'], summary['committed']))
        self.assertEqual(sign(self.unsigned), (self.checkout / 'prebuilt/app-release.apk').read_bytes())
        self.assertEqual(b'v4', (self.checkout / 'prebuilt/app-release.apk.idsig').read_bytes())
        readme = (self.checkout / 'README.md').read_text()
        self.assertIn('## This build', readme)
        self.assertIn('test app key (test builds only', readme)
        self.assertIn(self.result['unsigned_sha256'], readme)
        self.assertIn('Built 9 October 2026.', readme)

    def test_refuses_grapheneos_keys_other_builds_and_missing_release_keys(self):
        with self.assertRaisesRegex(subject.AppsError, 'GrapheneOS'):
            self.publish(digest=GRAPHENEOS, test=True)
        with self.assertRaisesRegex(subject.AppsError, '--test'):
            self.publish()
        other = self.root / 'other.apk'
        other.write_bytes(sign(unsigned_apk().replace(b'manifest', b'manifesT')))
        with self.assertRaisesRegex(subject.AppsError, 'no release key'):
            self.publish(signed=other, idsig=self.run / 'app-release.apk.idsig')
        config = copy.deepcopy(self.config)
        subject.app_config(config, 'appstore')['release_signer'] = OURS
        with self.assertRaisesRegex(subject.AppsError, 'unsigned build plus a signature'):
            subject.publish(self.run, self.checkout, signed=other, idsig=self.run / 'app-release.apk.idsig',
                            config=config, apksigner=lambda apk, sig: verify_output(OURS))
        with self.assertRaisesRegex(subject.AppsError, 'release key'):
            subject.publish(self.run, self.checkout, signed=self.run / 'app-release.apk',
                            idsig=self.run / 'app-release.apk.idsig', config=config,
                            apksigner=lambda apk, sig: verify_output('e' * 64))
        self.assertEqual(b'grapheneos build', (self.checkout / 'prebuilt/app-release.apk').read_bytes())

    def test_publishes_an_owner_signed_release(self):
        config = copy.deepcopy(self.config)
        subject.app_config(config, 'appstore')['release_signer'] = OURS
        summary = subject.publish(self.run, self.checkout, signed=self.run / 'app-release.apk',
                                  idsig=self.run / 'app-release.apk.idsig', config=config,
                                  apksigner=lambda apk, sig: verify_output(OURS))
        self.assertEqual('release', summary['key'])
        self.assertIn('DiamaneOS release key', (self.checkout / 'README.md').read_text())

    def test_refuses_a_dirty_checkout_or_a_changed_run(self):
        (self.checkout / 'stray').write_text('x')
        with self.assertRaisesRegex(subject.AppsError, 'uncommitted'):
            self.publish(test=True)
        (self.checkout / 'stray').unlink()
        (self.run / 'app-release.apk').write_bytes(sign(self.unsigned, padding=4))
        with self.assertRaisesRegex(subject.AppsError, 'differs from the run'):
            self.publish(test=True)


if __name__ == '__main__':
    unittest.main()
