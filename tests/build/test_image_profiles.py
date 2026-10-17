import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from diamaneos_tools import image_profiles as subject, image_verify
from diamaneos_tools.build_workspace import CheckFailed

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'config/fp6-build.json').read_text())


def openssl(*args):
    return subprocess.run(['/usr/bin/openssl', *map(str, args)], capture_output=True, check=True).stdout


def certificate(directory, name, key=None):
    """A self-signed certificate, with a new key or the given one."""
    cert = directory / (name + '.x509.pem')
    if key is None:
        key = directory / (name + '.key')
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-subj', f'/CN={name}/',
                '-days', '1', '-out', cert)
    else:
        openssl('req', '-new', '-x509', '-key', key, '-subj', f'/CN={name}/', '-days', '1', '-out', cert)
    return key, cert


def avb_blob(modulus: int, bits: int = 2048) -> bytes:
    n0inv = -pow(modulus, -1, 2 ** 32) % 2 ** 32
    return (bits.to_bytes(4, 'big') + n0inv.to_bytes(4, 'big') + modulus.to_bytes(bits // 8, 'big')
            + pow(2, 2 * bits, modulus).to_bytes(bits // 8, 'big'))


SET_KEY = avb_blob((1 << 2047) | 12345)
TEST_KEY = avb_blob((1 << 2047) | 67891)


def info(rollback=0, flags=0, descriptors=''):
    return f'Rollback Index:           {rollback}\nFlags:                    {flags}\nDescriptors:\n{descriptors}'


def layout(flags=None):
    """avbtool info_image texts of the published FP6 chain."""
    flags = flags or {}
    tree = '    Hashtree descriptor:\n      Hash Algorithm:        sha256\n      Partition Name:        {}\n'
    chains = ''.join(f'    Chain Partition descriptor:\n      Partition Name:          {name}\n'
                     f'      Rollback Index Location: {location}\n      Flags:                   0\n'
                     for name, location in CONFIG['avb']['chains'].items())
    vbmeta = (f'Rollback Index:           0\nFlags:                    {flags.get("vbmeta", 0)}\n'
              'Rollback Index Location:  0\nDescriptors:\n' + chains
              + ''.join(tree.format(n) for n in ('vendor', 'odm', 'vendor_dlkm', 'system_dlkm')))
    system = '    Hash descriptor:\n      Partition Name:        pvmfw\n' + ''.join(
        tree.format(n) for n in ('system', 'system_ext', 'product'))
    return {'vbmeta': vbmeta, 'vbmeta_system': info(1791158400, flags.get('vbmeta_system', 0), system),
            'boot': info(1791158400, flags.get('boot', 0)), 'init_boot': info(1791158400),
            'recovery': info(1)}


class FakeTools:
    """avbtool and the otatools validators, as far as the signed checks run them."""

    def __init__(self, blob=SET_KEY, texts=None, verify_fails=False):
        self.blob, self.texts, self.verify_fails = blob, texts or layout(), verify_fails
        self.calls = []

    def scratch(self):
        return tempfile.TemporaryDirectory()

    def run(self, name, args, cwd=None, check=True):
        args = [str(a) for a in args]
        self.calls.append((name, args))
        if args[0] == 'extract_public_key':
            key = Path(args[2])
            Path(args[4]).write_bytes(self.blob if key.name == 'avb.pem' else TEST_KEY)
            if key.name == 'avb.pem':
                self.pem = key.read_bytes()
        elif args[0] == 'info_image':
            return self.texts[Path(args[2]).stem]
        elif args[0] == 'verify_image' and self.verify_fails:
            raise CheckFailed('avbtool failed: Embedded public key does not match given key.')
        return ''


PROPS = {name: b'ro.build.tags=release-keys\nro.vendor.build.fingerprint=Fairphone/FP6/FP6:17/X/1:user/release-keys\n'
         for name in subject.BUILD_PROPS}


class SignedSet:
    """A re-signed image set: build.json, SHA256SUMS, target-files, otatools and the AVB key."""

    def __init__(self, root: Path, blob=SET_KEY, members=None, record=None, tools=None):
        self.images = root / 'images'
        self.images.mkdir(parents=True)
        (self.images / 'avb_pkmd.bin').write_bytes(blob)
        (self.images / 'vbmeta.img').write_bytes(b'vbmeta')
        with zipfile.ZipFile(self.images / 'otatools.zip', 'w') as archive:
            archive.writestr('bin/validate_target_files', b'')
        files = {'META/misc_info.txt': b'avb_vbmeta_key_path=keys/avb.pem\navb_boot_key_path=keys/avb.pem\n'
                                       b'avb_vbmeta_system_key_path=/signer/keys/avb.pem\n', **PROPS, **(members or {})}
        with zipfile.ZipFile(self.images / 'target-files.zip', 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        sums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.images.iterdir()}
        (self.images / 'SHA256SUMS').write_text(''.join(f'{d}  {n}\n' for n, d in sorted(sums.items())))
        self.record = {'signing': 'throwaway-test-keys', 'release': False, 'never_lock': False, 'official': True,
                       'avb_custom_key': {'file': 'avb_pkmd.bin', 'sha256': sums['avb_pkmd.bin']},
                       'otatools': {'file': 'otatools.zip', 'sha256': sums['otatools.zip']},
                       'target_files': {'file': 'target-files.zip', 'sha256': sums['target-files.zip']},
                       'images': {'vbmeta': sums['vbmeta.img']}, **(record or {})}
        self.src = root / 'src'
        (self.src / 'external/avb/test/data').mkdir(parents=True)
        (self.src / CONFIG['avb']['test_key']).write_text('AOSP test key')
        self.tf = image_verify.TargetFiles(self.images / 'target-files.zip')
        self.tools, self.config = tools or FakeTools(), CONFIG


class ProfileTests(unittest.TestCase):
    def test_signing_value_selects_the_profile_and_fails_closed(self):
        self.assertEqual(('public-test-keys', image_verify.GENERIC),
                         subject.generic_checks({'signing': 'public-test-keys'}))
        ids = [check_id for check_id, _, _ in image_verify.GENERIC]
        self.assertLessEqual(set(subject.COUNTERPARTS), set(ids))
        for signing in subject.SIGNED:
            name, checks = subject.generic_checks({'signing': signing})
            self.assertEqual(('signed', ids), (name, [check_id for check_id, _, _ in checks]))
            for (check_id, _, func), (_, _, default) in zip(checks, image_verify.GENERIC):
                self.assertEqual(check_id in subject.COUNTERPARTS, func is not default, check_id)
        for record in ({}, {'signing': None}, {'signing': 'test-keys'}, {'signing': 'Release-Keys'}):
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, 'selects no image check profile'):
                subject.generic_checks(record)

    def test_unknown_signing_stops_before_any_check(self):
        with tempfile.TemporaryDirectory() as temp:
            images = Path(temp)
            (images / 'build.json').write_text(json.dumps({'signing': 'own-keys', 'target_files': {'file': 'none'}}))
            with self.assertRaises(ValueError):
                image_verify.verify(images, CONFIG, {'schema_version': 1, 'rules': []}, None, None, None, None, images)
            report = images.parent / (images.name + '.json')
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(2, subject.main([str(images), '--report', str(report), '--workspace', temp]))
                self.assertEqual(2, subject.main([str(images), '--report', str(images / 'report.json')]))
            self.assertFalse(report.exists())
            self.assertIn('the report must be outside the image set', errors.getvalue())

    def test_avb_key_becomes_the_same_public_key_in_pem(self):
        with tempfile.TemporaryDirectory() as temp:
            key, _ = certificate(Path(temp), 'avb')
            modulus = int(openssl('rsa', '-in', key, '-noout', '-modulus').decode().strip().split('=')[1], 16)
            pem = Path(temp) / 'avb.pem'
            pem.write_bytes(subject.avb_key_pem(avb_blob(modulus)))
            self.assertEqual(openssl('pkey', '-in', key, '-pubout', '-outform', 'DER'),
                             openssl('pkey', '-pubin', '-in', pem, '-outform', 'DER'))
        for blob in (b'', SET_KEY[:-1], b'\0\0\0\0' + SET_KEY[4:]):
            with self.assertRaises(CheckFailed):
                subject.avb_key_pem(blob)


class SignedCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def signed(self, **kw):
        v = SignedSet(Path(tempfile.mkdtemp(dir=self.root)), **kw)
        self.addCleanup(v.tf.close)
        return v

    def test_relock_record(self):
        with mock.patch.object(subject, 'vbmeta_key_problem', return_value=''):
            self.assertEqual((True, ''), subject.check_relock_record(self.signed()))
            for record, problem in (({'never_lock': True}, 'relock record'),
                                    ({'release': True}, 'without release keys'),
                                    ({'avb_custom_key': None}, 'no common avb_custom_key'),
                                    ({'avb_custom_key': {'file': 'avb_pkmd.bin', 'sha256': '0' * 64}},
                                     'no common avb_custom_key')):
                with self.subTest(record=record):
                    ok, detail = subject.check_relock_record(self.signed(record=record))
                    self.assertFalse(ok)
                    self.assertIn(problem, detail)
            ok, detail = subject.check_relock_record(self.signed(blob=TEST_KEY, tools=FakeTools(TEST_KEY)))
            self.assertFalse(ok)
            self.assertIn('is the AOSP AVB test key testkey_rsa4096.pem', detail)
            ok, detail = subject.check_relock_record(self.signed(tools=FakeTools(TEST_KEY)))
            self.assertIn('not an AVB RSA public key with exponent 65537', detail)
        with mock.patch.object(subject, 'vbmeta_key_problem', return_value='vbmeta.img is not signed with x'):
            self.assertEqual((False, 'vbmeta.img is not signed with x'), subject.check_relock_record(self.signed()))

    def test_release_keys_everywhere_and_no_test_certificate(self):
        keys = self.root / 'keys'
        keys.mkdir()
        testkey, test_cert = certificate(keys, 'testkey')
        _, own_cert = certificate(keys, 'releasekey')
        _, reissued = certificate(keys, 'reissued', key=testkey)

        def check(ota=own_cert, mac=own_cert, props=None):
            otacerts = io.BytesIO()
            with zipfile.ZipFile(otacerts, 'w') as archive:
                archive.writestr('keys/releasekey.x509.pem', ota.read_bytes())
            der = openssl('x509', '-in', mac, '-outform', 'DER').hex()
            v = self.signed(members={'SYSTEM/etc/security/otacerts.zip': otacerts.getvalue(),
                                     'SYSTEM/etc/selinux/plat_mac_permissions.xml':
                                         f'<policy><signer signature="{der}"/></policy>', **(props or {})})
            security = v.src / 'build/make/target/product/security'
            security.mkdir(parents=True)
            (security / 'testkey.x509.pem').write_bytes(test_cert.read_bytes())
            return subject.check_release_keys(v)
        self.assertEqual((True, ''), check())
        for kw, problem in (({'ota': test_cert}, 'otacerts.zip holds the public test key of testkey.x509.pem'),
                            ({'mac': reissued}, 'mac_permissions.xml holds the public test key of testkey.x509.pem'),
                            ({'props': {'VENDOR/build.prop': b'ro.vendor.build.fingerprint=F/FP6:user/test-keys\n'}},
                             'VENDOR/build.prop: ro.vendor.build.fingerprint does not end in release-keys'),
                            ({'props': {'ODM/etc/build.prop': b'ro.odm.build.tags=dev-keys\n'}},
                             'ro.odm.build.tags is not release-keys'),
                            ({'props': {'META/misc_info.txt': b'avb_boot_add_hash_footer_args=--prop f:test-keys\n'}},
                             'META/misc_info.txt names test-keys')):
            with self.subTest(problem=problem):
                ok, detail = check(**kw)
                self.assertFalse(ok)
                self.assertIn(problem, detail)

    def test_signed_avb_chain(self):
        v = self.signed()
        ok, detail = subject.check_signed_avb(v)
        self.assertTrue(ok, detail)
        self.assertIn('boot 1791158400', detail)
        verify = next(args for name, args in v.tools.calls if args[0] == 'verify_image')
        self.assertEqual(subject.avb_key_pem(SET_KEY), v.tools.pem)
        chains = {f'{name}:{location}:{v.images / "avb_pkmd.bin"}' for name, location in CONFIG['avb']['chains'].items()}
        self.assertEqual(chains, {verify[i + 1] for i, arg in enumerate(verify) if arg == '--expected_chain_partition'})
        self.assertIn('--follow_chain_partitions', verify)
        for kw, problem in (({'blob': TEST_KEY, 'tools': FakeTools(TEST_KEY)}, 'AOSP AVB test key'),
                            ({'tools': FakeTools(verify_fails=True)}, 'does not match given key'),
                            ({'tools': FakeTools(texts=layout({'boot': 1}))}, 'boot does not keep AVB flags 0'),
                            ({'tools': FakeTools(texts=layout({'vbmeta': 2}))}, 'vbmeta does not keep AVB flags 0')):
            with self.subTest(problem=problem):
                v = self.signed(**kw)
                try:
                    ok, detail = subject.check_signed_avb(v)
                except CheckFailed as error:
                    ok, detail = False, str(error)
                self.assertFalse(ok)
                self.assertIn(problem, detail)

    def test_validators_run_from_the_sets_otatools_with_its_public_key(self):
        runs = []

        class Otatools:
            def run(self, name, args, cwd=None):
                args = [str(a) for a in args]
                runs.append((name, args, {Path(a).read_bytes() for a in args[2::2] if a.endswith('.pem')}))
                return ''
        with mock.patch.object(subject, 'otatools', return_value=Otatools()):
            v = self.signed()
            self.assertEqual((True, 'otatools.zip of the set'), subject.check_otatools_validators(v))
            (validate, args, pems), (vintf, vintf_args, _) = runs
            self.assertEqual(('validate_target_files', 'check_target_files_vintf'), (validate, vintf))
            self.assertEqual([str(v.images / 'target-files.zip')], vintf_args)
            self.assertEqual(['--verity_key', '--avb_boot_key_path', '--avb_vbmeta_system_key_path'], args[1::2])
            self.assertEqual({subject.avb_key_pem(SET_KEY)}, pems)
            for kw in ({'record': {'otatools': None}},
                       {'record': {'otatools': {'file': 'otatools.zip', 'sha256': '0' * 64}}}):
                with self.subTest(kw=kw):
                    ok, detail = subject.check_otatools_validators(self.signed(**kw))
                    self.assertEqual((False, 'build.json and SHA256SUMS name no common otatools.zip'), (ok, detail))
            with self.assertRaisesRegex(CheckFailed, 'AOSP AVB test key'):
                subject.check_otatools_validators(self.signed(blob=TEST_KEY, tools=FakeTools(TEST_KEY)))


if __name__ == '__main__':
    unittest.main()
