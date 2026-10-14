import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_workspace as bw, flash_all, image_package, image_verify, release
from tests.build.test_image_package import FSTAB, fixture_workspace, run_plan

CLEAN = {'commit': 'a' * 40, 'clean': True}
KEY = release.PublicTestKeys.key
NUMBER, DATE = 'test.ffffffffffff', 1790000000
# Stands in for otatools' ota_from_target_files: an OTA package with the
# metadata the release tools write for the target-files it is given, and the
# certificate of the -k key (FAKE_OTA_DEVICE changes the device it names).
FAKE_OTA = '''
import os, sys, zipfile
args, options = sys.argv[1:], {}
while args[0].startswith('-'):
    options[args[0]], args = args[1], args[2:]
def props(path):
    with zipfile.ZipFile(path) as archive:
        lines = archive.read('SYSTEM/build.prop').decode().splitlines()
    return dict(line.split('=', 1) for line in lines if '=' in line)
new = props(args[0])
metadata = {'ota-type': 'AB', 'post-build': new['ro.build.fingerprint'],
            'post-build-incremental': new['ro.build.version.incremental'], 'post-timestamp': new['ro.build.date.utc'],
            'pre-device': os.environ.get('FAKE_OTA_DEVICE', new['ro.product.device']),
            'ota-streaming-property-files': 'payload.bin:679:7,payload_properties.txt:100:20'}
if '-i' in options:
    old = props(options['-i'])
    metadata.update({'pre-build': old['ro.build.fingerprint'],
                     'pre-build-incremental': old['ro.build.version.incremental']})
with zipfile.ZipFile(args[1], 'w') as archive:
    archive.writestr('META-INF/com/android/metadata', ''.join(f'{k}={v}\\n' for k, v in sorted(metadata.items())))
    archive.writestr('META-INF/com/android/otacert', open(options['-k'] + '.x509.pem', 'rb').read())
    archive.writestr('payload.bin', b'payload')
    archive.writestr('payload_properties.txt', 'FILE_SIZE=7\\n')
'''


def fingerprint(number):
    return f'Fairphone/FP6/FP6:17/BP4A.000/{number}:user/test-keys'


def official_set(root: Path, number=NUMBER, date=DATE, identity='f' * 64):
    """A packaged official user image set with a passing verify report, whose
    otatools.zip holds the public test key and a stand-in OTA tool."""
    ctx = fixture_workspace(root)
    ws = ctx.workspace
    android = ws.passed('android')
    out = android['outputs']
    target = ws.root / out['target_files']
    with zipfile.ZipFile(target, 'w') as archive:
        for name in image_package.image_names(ctx.config):
            archive.writestr(f'IMAGES/{name}.img', f'{name} image {number}'.encode())
        archive.writestr('VENDOR/etc/fstab.qcom', FSTAB)
        archive.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={number}\nro.build.date.utc={date}\n'
                                              f'ro.product.device=FP6\nro.build.fingerprint={fingerprint(number)}\n')
        archive.writestr('META/misc_info.txt', f'default_system_dev_certificate={KEY}\n')
    tools = ws.root / out['otatools']['file']
    with zipfile.ZipFile(tools, 'w') as archive:
        tool = zipfile.ZipInfo('bin/ota_from_target_files')
        tool.external_attr = 0o100755 << 16
        archive.writestr(tool, f'#!{sys.executable}\n' + FAKE_OTA)
        archive.writestr(KEY + '.x509.pem', 'public test certificate')
        archive.writestr(KEY + '.pk8', 'public test key')
    out.update(official=True, tools=CLEAN, build_number=number, build_datetime=date, build_identity=identity,
               target_files_sha256=bw.sha_file(target),
               otatools={'file': out['otatools']['file'], 'sha256': bw.sha_file(tools)})
    ws.write_state('android', android)
    with patch.object(image_package.product_inputs, 'tools_identity', return_value=CLEAN):
        outputs = run_plan(ctx, image_package.plan(ctx))
    directory = ws.root / outputs['directory']
    write_report(directory)
    return ws, directory


def write_report(directory: Path, **changes) -> Path:
    record = json.loads((directory / 'build.json').read_text())
    rules = json.loads(release.IMAGE_CHECKS.read_text())
    ids = list(image_verify.REQUIRED_CHECKS) + [rule['id'] for rule in image_verify.validate_rules(rules)
                                                if image_verify.rule_applies(rule, record['variant'], True)]
    checks = [{'id': check, 'status': 'PASS'} for check in ids]
    report = {'schema_version': 1, 'build_id': record['build_id'], 'variant': record['variant'],
              'sums_sha256': bw.sha_file(directory / 'SHA256SUMS'), 'build_identity': record['build_identity'],
              'rule_set_sha256': bw.digest(rules), 'status': 'PASS', 'checked': len(checks), 'failed': 0,
              'checks': checks, **changes}
    path = directory.parent / (record['build_id'] + '.verify.json')
    path.write_text(json.dumps(report))
    return path


def writable(directory: Path) -> None:
    for path in directory.iterdir():
        path.chmod(0o640)


def rewrite(directory: Path, change) -> None:
    """Change build.json and keep SHA256SUMS in step with it."""
    writable(directory)
    record = json.loads((directory / 'build.json').read_text())
    change(record)
    (directory / 'build.json').write_bytes(bw.encoded(record))
    image_package.write_sums(directory)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ws, self.images = official_set(self.root / 'one')
        for target, name, value in ((release, 'host_problem', None),
                                    (release.product_inputs, 'tools_identity', CLEAN)):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def release(self, *argv, images=None, workspace=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = release.main([str(images or self.images), '--workspace', str(workspace or self.ws.root), *argv],
                                echo=lambda line: out.write(line + '\n'))
        return code, out.getvalue(), err.getvalue()

    def made(self, *argv, **kw):
        code, out, err = self.release(*argv, **kw)
        self.assertEqual(0, code, err)
        return Path(out.splitlines()[-2].removeprefix('Release: '))

    def test_a_release_holds_the_packages_channel_files_and_provenance(self):
        directory = self.made('--channel', 'alpha', '--channel', 'stable')
        self.assertEqual(self.ws.root / 'releases' / f'FP6-{NUMBER}', directory)
        build_id = json.loads((self.images / 'build.json').read_text())['build_id']
        self.assertEqual(sorted([
            f'FP6-target_files-{NUMBER}.zip', 'provenance/SHA256SUMS', 'provenance/build.json',
            f'provenance/{build_id}.verify.json', 'provenance/release.json', 'provenance/resolved-manifest.xml',
            'publish/FP6-alpha', f'publish/FP6-factory-{NUMBER}.zip', f'publish/FP6-ota_update-{NUMBER}.zip',
            'publish/FP6-stable', 'release.log']),
            sorted(str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()))
        # The line the Updater reads at FP6-alpha: build number, build date, device, channel.
        alpha = (directory / 'publish/FP6-alpha').read_text()
        self.assertEqual(f'{NUMBER} {DATE} FP6 alpha\n', alpha)
        self.assertRegex(alpha.strip(), release.CHANNEL_LINE)
        self.assertEqual(f'{NUMBER} {DATE} FP6 stable\n', (directory / 'publish/FP6-stable').read_text())
        for name in ('build.json', 'resolved-manifest.xml', 'SHA256SUMS'):
            self.assertEqual((self.images / name).read_bytes(), (directory / 'provenance' / name).read_bytes())
        record = json.loads((directory / 'provenance/release.json').read_text())
        self.assertEqual({'keys': 'public-test-keys', 'signatures': {}}, {k: record['signing'][k]
                                                                         for k in ('keys', 'signatures')})
        self.assertEqual((NUMBER, DATE, 'FP6', ['alpha', 'stable']),
                         (record['build_number'], record['build_datetime'], record['device'], record['channels']))
        self.assertEqual(fingerprint(NUMBER), record['fingerprint'])
        self.assertEqual({'wipe': False, 'firmware': True}, record['validated_on_a_phone'])
        self.assertEqual({'manifest': 'c' * 40, 'kernel_prebuilts': 'b' * 40, 'tools_build': 'a' * 40,
                          'tools_packaging': 'a' * 40, 'tools_release': 'a' * 40}, record['commits'])
        self.assertEqual(image_package.read_sums(self.images), record['image_set']['files'])
        self.assertEqual([{'file': f'publish/FP6-ota_update-{NUMBER}.zip', 'source_build_number': None}], record['ota'])
        self.assertEqual(set(record['artifacts']), {str(p.relative_to(directory)) for p in directory.rglob('*')
                                                    if p.is_file() and p.name not in ('release.json', 'release.log')})
        for relative, identity in record['artifacts'].items():
            self.assertEqual(identity['sha256'], bw.sha_file(directory / relative), relative)
        with zipfile.ZipFile(directory / f'publish/FP6-ota_update-{NUMBER}.zip') as ota:
            self.assertEqual(b'public test certificate', ota.read('META-INF/com/android/otacert'))

    def test_the_factory_package_is_the_set_as_the_user_flashes_it(self):
        directory = self.made('--channel', 'alpha')
        record = json.loads((self.images / 'build.json').read_text())
        top = f'FP6-factory-{NUMBER}/'
        with zipfile.ZipFile(directory / f'publish/FP6-factory-{NUMBER}.zip') as archive:
            names = archive.namelist()
            self.assertTrue(all(name.startswith(top) for name in names))
            expected = set(image_package.read_sums(self.images)) - {'target-files.zip', 'otatools.zip'}
            self.assertEqual(expected | {'SHA256SUMS'}, {name[len(top):] for name in names})
            # flash-all.sh checks every file against this SHA256SUMS.
            lines = archive.read(top + 'SHA256SUMS').decode().splitlines()
            self.assertEqual(sorted(expected), [line.split('  ')[1] for line in lines])
            for line in lines:
                digest, name = line.split('  ')
                self.assertEqual(digest, release.sha_bytes(archive.read(top + name)), name)
            for name, text in flash_all.scripts(record).items():
                self.assertEqual(text.encode(), archive.read(top + name))
            self.assertEqual(0o100755, archive.getinfo(top + 'flash-all.sh').external_attr >> 16)

    def test_the_same_set_gives_the_same_factory_package_and_a_release_is_made_once(self):
        first = self.made('--channel', 'alpha', '--output', str(self.root / 'a'))
        second = self.made('--channel', 'beta', '--output', str(self.root / 'b'))
        name = f'publish/FP6-factory-{NUMBER}.zip'
        self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        code, _, err = self.release('--channel', 'alpha', '--output', str(self.root / 'a'))
        self.assertEqual(2, code)
        self.assertIn('a release is never made twice', err)

    def test_an_incremental_package_comes_from_an_earlier_release(self):
        first = self.made('--channel', 'alpha', '--output', str(self.root / 'out'))
        ws, images = official_set(self.root / 'two', number='test.eeeeeeeeeeee', date=DATE + 100, identity='e' * 64)
        second = self.made('--channel', 'alpha', '--output', str(self.root / 'out'), '--incremental-from', str(first),
                           images=images, workspace=ws.root)
        incremental = second / f'publish/FP6-incremental-{NUMBER}-test.eeeeeeeeeeee.zip'
        metadata, _ = release.ota_metadata(incremental)
        self.assertEqual((NUMBER, fingerprint(NUMBER)), (metadata['pre-build-incremental'], metadata['pre-build']))
        record = json.loads((second / 'provenance/release.json').read_text())
        self.assertEqual([None, NUMBER], [ota['source_build_number'] for ota in record['ota']])
        self.assertEqual(f'test.eeeeeeeeeeee {DATE + 100} FP6 alpha\n', (second / 'publish/FP6-alpha').read_text())
        # The Updater never installs an older build.
        code, _, err = self.release('--channel', 'alpha', '--output', str(self.root / 'older'),
                                    '--incremental-from', str(second))
        self.assertEqual(2, code)
        self.assertIn('is not older than this build', err)

    def test_check_writes_nothing(self):
        code, out, err = self.release('--check')
        self.assertEqual(0, code, err)
        self.assertIn(f'publish/FP6-ota_update-{NUMBER}.zip', out)
        self.assertFalse((self.ws.root / 'releases').exists())

    def test_a_release_needs_a_channel(self):
        code, _, err = self.release()
        self.assertEqual(2, code)
        self.assertIn('--channel', err)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            release.main([str(self.images), '--channel', 'testing'])

    def test_a_failed_ota_package_leaves_no_release(self):
        with patch.dict(os.environ, {'FAKE_OTA_DEVICE': 'FP5'}):
            code, _, err = self.release('--channel', 'alpha')
        self.assertEqual(2, code)
        self.assertIn("pre-device is 'FP5', not 'FP6'", err)
        self.assertFalse((self.ws.root / 'releases' / f'FP6-{NUMBER}').exists())

    def test_refusals(self):
        def remove_boot(directory):
            writable(directory)
            (directory / 'boot.img').unlink()
            image_package.write_sums(directory)

        def add_signature(directory):
            (directory / f'FP6-factory-{NUMBER}.zip.sig').write_text('signature')

        def change_boot(directory):
            writable(directory)
            (directory / 'boot.img').write_bytes(b'other boot image')

        def other_script(directory):
            writable(directory)
            (directory / 'flash-all.sh').write_text('#!/bin/sh\nfastboot -w\n')
            image_package.write_sums(directory)

        def failed(directory):
            report = json.loads(write_report(directory).read_text())
            report['checks'][-1]['status'] = 'FAIL'
            write_report(directory, checks=report['checks'], status='FAIL', failed=1)

        def without_rule(directory):
            report = json.loads(write_report(directory).read_text())
            checks = report['checks'][:-1]
            write_report(directory, checks=checks, checked=len(checks))

        cases = [
            ('not official', lambda d: rewrite(d, lambda r: r.update(official=False)), 'not an official build'),
            ('userdebug', lambda d: rewrite(d, lambda r: r.update(variant='userdebug')), 'a release is a user build'),
            ('not reproducible', lambda d: rewrite(d, lambda r: r.update(reproducible=False)), 'not reproducible'),
            ('signed', lambda d: rewrite(d, lambda r: r.update(signing='release-keys')),
             "signed with 'release-keys', but no signature result"),
            ('release', lambda d: rewrite(d, lambda r: r.update(release=True)), 'for a release, but no signature'),
            ('no report', lambda d: (d.parent / (d.name + '.verify.json')).unlink(), 'no verify report'),
            ('failed', failed, 'does not show that this image set passed: verification failed'),
            ('other set', lambda d: write_report(d, sums_sha256='0' * 64), 'belongs to another image set'),
            ('other checks', lambda d: write_report(d, rule_set_sha256='0' * 64), 'other image checks'),
            ('without a rule', without_rule, 'lacks the image checks'),
            ('missing file', remove_boot, 'lacks boot.img'),
            ('unknown file', add_signature, f'does not name: FP6-factory-{NUMBER}.zip.sig'),
            ('changed file', change_boot, 'do not match SHA256SUMS: boot.img'),
            ('record hash', lambda d: rewrite(d, lambda r: r['images'].update(boot='0' * 64)),
             'other hashes for boot.img'),
            ('script', other_script, 'flash-all.sh differs from the scripts'),
        ]
        for number, (name, change, message) in enumerate(cases):
            with self.subTest(name):
                ws, images = official_set(self.root / str(number))
                change(images)
                code, _, err = self.release('--channel', 'alpha', images=images, workspace=ws.root)
                self.assertEqual(2, code)
                self.assertRegex(err, message)
                self.assertFalse((ws.root / 'releases').exists())

    def test_tools_with_local_changes_make_no_release(self):
        with patch.object(release.product_inputs, 'tools_identity', return_value={'commit': 'a' * 40, 'clean': False}):
            code, _, err = self.release('--check')
        self.assertEqual(2, code)
        self.assertIn('committed tools', err)

    def test_an_ota_package_must_pass_the_updaters_checks(self):
        image_set = release.load_image_set(self.images)
        tools = self.root / 'tools'
        (tools / KEY).parent.mkdir(parents=True)
        (tools / (KEY + '.x509.pem')).write_text('public test certificate')
        good = {'ota-type': 'AB', 'post-build': fingerprint(NUMBER), 'post-build-incremental': NUMBER,
                'post-timestamp': str(DATE), 'pre-device': 'FP6', 'ota-streaming-property-files': 'payload.bin:679:7'}

        def package(metadata, certificate='public test certificate'):
            path = self.root / 'ota.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(release.OTA_METADATA, ''.join(f'{k}={v}\n' for k, v in metadata.items()))
                archive.writestr(release.OTA_CERTIFICATE, certificate)
                archive.writestr('payload.bin', b'payload')
                archive.writestr('payload_properties.txt', '')
            return path

        self.assertEqual(good, release.check_ota(package(good), image_set, tools))
        cases = [({'ota-type': 'BLOCK'}, 'ota-type'), ({'post-build-incremental': 'other'}, 'post-build-incremental'),
                 ({'post-timestamp': str(DATE - 1)}, 'post-timestamp'), ({'pre-device': 'FP6|FP5'}, 'pre-device'),
                 ({'serialno': '1234'}, 'serialno'), ({'pre-build-incremental': 'old'}, 'not a full one'),
                 ({'post-build': fingerprint('other')}, 'not one of build'),
                 ({'post-build': fingerprint(NUMBER).replace('test-keys', 'release-keys')}, 'public test keys'),
                 ({'ota-streaming-property-files': 'payload_properties.txt:1:2'}, 'no payload offset')]
        for change, message in cases:
            with self.subTest(change):
                with self.assertRaisesRegex(release.ReleaseError, message):
                    release.check_ota(package(dict(good, **change)), image_set, tools)
        with self.assertRaisesRegex(release.ReleaseError, 'another certificate'):
            release.check_ota(package(good, 'release certificate'), image_set, tools)
        with self.assertRaisesRegex(release.ReleaseError, 'lacks payload.bin'):
            path = package(good)
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(release.OTA_METADATA, '')
                archive.writestr(release.OTA_CERTIFICATE, '')
                archive.writestr('payload_properties.txt', '')
            release.check_ota(path, image_set, tools)

    def test_only_the_public_test_key_signs(self):
        self.assertEqual(KEY, release.PublicTestKeys().ota_key({'default_system_dev_certificate': KEY}))
        with self.assertRaisesRegex(release.ReleaseError, 'not the public test key'):
            release.PublicTestKeys().ota_key({'default_system_dev_certificate': 'keys/releasekey'})

    def test_channel_files_are_what_the_updater_reads(self):
        metadata = {'post-build-incremental': '2026010100', 'post-timestamp': '1770000000', 'pre-device': 'FP6'}
        self.assertEqual(b'2026010100 1770000000 FP6 stable\n', release.channel_file(metadata, 'stable'))
        for change in ({'post-build-incremental': 'https://evil.invalid/'}, {'post-timestamp': '9' * 19},
                       {'pre-device': 'FP6 extra'}):
            with self.subTest(change), self.assertRaisesRegex(release.ReleaseError, 'not one the Updater reads'):
                release.channel_file(dict(metadata, **change), 'stable')


if __name__ == '__main__':
    unittest.main()
