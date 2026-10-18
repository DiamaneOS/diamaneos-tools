"""Release mode with throwaway keys, the AOSP test keys and the DiamaneOS release keys."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from diamaneos_tools import signing_inputs as inputs, signing_keys as keys, signing_native as native
from diamaneos_tools import signing_qualification as plans, signing_release as release, signing_verify as cli

# The public halves of build/make/target/product/security/testkey and external/avb/test/data/testkey_rsa4096.
AOSP = {'testkey.x509.pem': """
-----BEGIN CERTIFICATE-----
MIIEqDCCA5CgAwIBAgIJAJNurL4H8gHfMA0GCSqGSIb3DQEBBQUAMIGUMQswCQYD
VQQGEwJVUzETMBEGA1UECBMKQ2FsaWZvcm5pYTEWMBQGA1UEBxMNTW91bnRhaW4g
VmlldzEQMA4GA1UEChMHQW5kcm9pZDEQMA4GA1UECxMHQW5kcm9pZDEQMA4GA1UE
AxMHQW5kcm9pZDEiMCAGCSqGSIb3DQEJARYTYW5kcm9pZEBhbmRyb2lkLmNvbTAe
Fw0wODAyMjkwMTMzNDZaFw0zNTA3MTcwMTMzNDZaMIGUMQswCQYDVQQGEwJVUzET
MBEGA1UECBMKQ2FsaWZvcm5pYTEWMBQGA1UEBxMNTW91bnRhaW4gVmlldzEQMA4G
A1UEChMHQW5kcm9pZDEQMA4GA1UECxMHQW5kcm9pZDEQMA4GA1UEAxMHQW5kcm9p
ZDEiMCAGCSqGSIb3DQEJARYTYW5kcm9pZEBhbmRyb2lkLmNvbTCCASAwDQYJKoZI
hvcNAQEBBQADggENADCCAQgCggEBANaTGQTexgskse3HYuDZ2CU+Ps1s6x3i/waM
qOi8qM1r03hupwqnbOYOuw+ZNVn/2T53qUPn6D1LZLjk/qLT5lbx4meoG7+yMLV4
wgRDvkxyGLhG9SEVhvA4oU6Jwr44f46+z4/Kw9oe4zDJ6pPQp8PcSvNQIg1QCAcy
4ICXF+5qBTNZ5qaU7Cyz8oSgpGbIepTYOzEJOmc3Li9kEsBubULxWBjf/gOBzAzU
RNps3cO4JFgZSAGzJWQTT7/emMkod0jb9WdqVA2BVMi7yge54kdVMxHEa5r3b97s
zI5p58ii0I54JiCUP5lyfTwE/nKZHZnfm644oLIXf6MdW2r+6R8CAQOjgfwwgfkw
HQYDVR0OBBYEFEhZAFY9JyxGrhGGBaR0GawJyowRMIHJBgNVHSMEgcEwgb6AFEhZ
AFY9JyxGrhGGBaR0GawJyowRoYGapIGXMIGUMQswCQYDVQQGEwJVUzETMBEGA1UE
CBMKQ2FsaWZvcm5pYTEWMBQGA1UEBxMNTW91bnRhaW4gVmlldzEQMA4GA1UEChMH
QW5kcm9pZDEQMA4GA1UECxMHQW5kcm9pZDEQMA4GA1UEAxMHQW5kcm9pZDEiMCAG
CSqGSIb3DQEJARYTYW5kcm9pZEBhbmRyb2lkLmNvbYIJAJNurL4H8gHfMAwGA1Ud
EwQFMAMBAf8wDQYJKoZIhvcNAQEFBQADggEBAHqvlozrUMRBBVEY0NqrrwFbinZa
J6cVosK0TyIUFf/azgMJWr+kLfcHCHJsIGnlw27drgQAvilFLAhLwn62oX6snb4Y
LCBOsVMR9FXYJLZW2+TcIkCRLXWG/oiVHQGo/rWuWkJgU134NDEFJCJGjDbiLCpe
+ZTWHdcwauTJ9pUbo8EvHRkU3cYfGmLaLfgn9gP+pWA7LFQNvXwBnDa6sppCccEX
31I828XzgXpJ4O+mDL1/dBd+ek8ZPUP0IgdyZm5MTYPhvVqGCHzzTy3sIeJFymwr
sBbmg2OAUNLEMO6nwmocSdN2ClirfxqCzJOLSDE4QyS9BAH6EhY6UFcOaE0=
-----END CERTIFICATE-----
""", 'testkey_rsa4096.pub.pem': """
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA2ASv49OEbH4NiT3CjNMS
VeliyfEPXswWcqtEfCxlSpS1FisAuwbvEwdTTPlkuSh6G4SYiNhnpCP5p0vcSg/3
OhiuVKgV/rCtrDXaO60nvK/o0y83NNZRK2xaJ9eWBq9ruIDK+jC0sYWzTaqqwxY0
Grjnx/r5CXerl5PrRK7PILzwgBHbIwxHcblt1ntgR4cWVpO3wiqasEwBDDDYk4fw
7W6LvjBb9qav3YB8RV6PkZNeRP64ggfuecq/MXNiWOPNxLzCER2hSr/+J32h9jWj
XsrcVy8+8Mldhmr4r2an7c247aFfupuFGtUJrpROO8/LXMl5gPfMpkqoatjTMRH5
9gJjKhot0RpmGxZBvb33TcBK5SdJX39Y4yct5clmDlI4Fjj7FutTP+b96aJeJVnY
eUX/A0wmogBajsJRoRX5e/RcgZsYRzXYLQXprQ81dBWjjovMJ9p8XeT6BNMFC7o6
sklFL0fHDUE/l4BNP8G1u3BfpzevSCISRS71D4eS4oQB+RIPFBUkzomZ7rnEF3Bw
Feq+xmwfYrP0LRaH+1YeRauuMuReke1TZl697a3mEjkNg8noa2wtpe7EWmaujJfX
DWxJx/XEkjGLCe4z2qk3tkkY+A5gRcgzke8gVxC+eC2DJtbKYfkv4L8FMFJaEhwA
p13MfC7FlYujO/BDLl7dANsCAwEAAQ==
-----END PUBLIC KEY-----
"""}
ROLES = sorted(inputs.CERT_ROLES)


def write_aosp(name, path):
    Path(path).write_text(AOSP[name].lstrip())
    return Path(path)


def openssl(*args):
    return subprocess.run(['/usr/bin/openssl', *map(str, args)], capture_output=True, check=True).stdout


def avb_pkmd(pem: Path) -> bytes:
    """avb_pkmd.bin of an RSA key, as avbtool extract_public_key writes it."""
    public = ['-pubin'] if b'PUBLIC KEY' in Path(pem).read_bytes() else []
    modulus = int(openssl('rsa', *public, '-in', pem, '-noout', '-modulus').decode().split('=')[1], 16)
    bits = modulus.bit_length()
    n0inv = -pow(modulus, -1, 2 ** 32) % 2 ** 32
    return (bits.to_bytes(4, 'big') + n0inv.to_bytes(4, 'big') + modulus.to_bytes(bits // 8, 'big')
            + pow(2, 2 * bits, modulus).to_bytes(bits // 8, 'big'))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReleaseKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='release-keys-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.pins = release.pins()
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)

    def throwaway(self, name='keys'):
        """A key directory laid out as GrapheneOS's guide makes it, with throwaway keys."""
        directory = self.root / name
        directory.mkdir()
        openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', directory / 'avb.pem')
        for role in ROLES:
            openssl('req', '-new', '-x509', '-key', directory / 'avb.pem', '-subj', '/CN=throwaway/', '-days', '1',
                    '-out', directory / (role + '.x509.pem'))
        openssl('pkey', '-in', directory / 'avb.pem', '-pubout', '-out', self.root / (name + '-avb.pub.pem'))
        (directory / 'avb_pkmd.bin').write_bytes(avb_pkmd(self.root / (name + '-avb.pub.pem')))
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', directory / 'id_ed25519'],
                       check=True, capture_output=True)
        return directory

    def aosp(self):
        directory = self.root / 'aosp'
        directory.mkdir()
        for role in ROLES:
            write_aosp('testkey.x509.pem', directory / (role + '.x509.pem'))
        return directory

    def test_release_keys_pass(self):
        self.assertEqual([], release.key_problems(self.pins['certificates'], self.pins['avb_pkmd_sha256']))
        self.assertEqual([], release.factory_problems([self.pins['factory_signer']], 'diamaneos'))
        self.assertEqual([], release.policy_problems(self.policy))

    def test_throwaway_keys_fail_loudly(self):
        directory = self.throwaway()
        self.assertEqual(ROLES, release.key_dir_problems(directory))
        self.assertEqual(['avb'], release.key_problems(avb_pkmd_sha256=sha((directory / 'avb_pkmd.bin').read_bytes())))
        with self.assertRaisesRegex(inputs.SigningError, 'not the DiamaneOS release keys: bluetooth, .*shared'):
            release.refuse(release.key_dir_problems(directory))
        public = ' '.join((directory / 'id_ed25519.pub').read_text().split()[:2])
        signer = self.pins['factory_signer']
        for lines, identity in ((['diamaneos ' + public], 'diamaneos'), ([signer], 'other'),
                                ([signer, 'diamaneos ' + public], 'diamaneos')):
            with self.subTest(lines=lines, identity=identity):
                self.assertEqual(['factory'], release.factory_problems(lines, identity))
        policy = copy.deepcopy(self.policy)
        next(r for r in policy['presigned_packages'] if r['metadata_name'] == 'AppStore.apk')['certificate_sha256'] = [
            release.pem_digests((directory / 'releasekey.x509.pem').read_bytes())[0]]
        self.assertEqual(['AppStore.apk'], release.policy_problems(policy))

    def test_aosp_test_keys_fail(self):
        self.assertEqual(ROLES, release.key_dir_problems(self.aosp()))
        blob = avb_pkmd(write_aosp('testkey_rsa4096.pub.pem', self.root / 'avb-test.pub.pem'))
        self.assertEqual(4096 // 4 + 8, len(blob))
        self.assertEqual(['avb'], release.key_problems(avb_pkmd_sha256=sha(blob)))

    def test_release_signing_command_refuses_other_key_directories(self):
        source = self.root / 'unsigned.zip'; source.write_bytes(b'input')
        signer = self.root / 'sign_target_files_apks'; signer.write_bytes(b'fixture signer')
        generator = self.root / 'ota_from_target_files'; generator.write_bytes(b'fixture generator')
        plan = {'schema_version': 2, 'mode': 'release', 'roles': {}, 'chains': {},
                'provenance': {'target_files_sha256': inputs.sha256_file(source),
                               'signer_sha256': inputs.sha256_file(signer),
                               'ota_generator_sha256': inputs.sha256_file(generator),
                               'projects': {}, 'source_files': {}}}
        plan['plan_sha256'] = inputs.canonical_sha256(plan)
        arguments = {'signer': signer, 'source': source, 'destination': self.root / 'signed.zip', 'source_root': self.root}
        with mock.patch.object(plans, 'source_files', return_value={}):
            for directory in (self.throwaway(), self.aosp()):
                with self.subTest(directory.name), self.assertRaisesRegex(inputs.SigningError, 'Release mode refuses'):
                    plans.signing_command(plan, key_dir=directory, **arguments)
            with mock.patch.object(release, 'key_dir_problems', return_value=[]):
                self.assertEqual(str(source), plans.signing_command(plan, key_dir=self.root / 'keys', **arguments)[-2])
            other = dict(plan, mode='production')
            other['plan_sha256'] = inputs.canonical_sha256({k: v for k, v in other.items() if k != 'plan_sha256'})
            plans.signing_command(other, key_dir=self.root / 'keys', **arguments)
        ota = plans.ota_command(plan, generator=generator, key_dir=self.root / 'keys', signed=self.root / 'signed.zip',
                                destination=self.root / 'ota.zip')
        self.assertEqual(['-k', str(self.root / 'keys/releasekey')], ota[1:3])
        generator.write_bytes(b'other generator')
        with self.assertRaisesRegex(inputs.SigningError, 'OTA generator'):
            plans.ota_command(plan, generator=generator, key_dir=self.root, signed=source, destination=self.root / 'ota.zip')

    def target_files(self, otacerts, catalog_key):
        dex = b'dex\n035\0' + catalog_key.encode() + b'\0'
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('classes.dex', dex)
        lists = io.BytesIO()
        with zipfile.ZipFile(lists, 'w') as archive:
            archive.writestr('keys/releasekey.x509.pem', otacerts.read_bytes())
        path = self.root / 'target-files.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name in ('SYSTEM/etc/security/otacerts.zip', 'RECOVERY/RAMDISK/system/etc/security/otacerts.zip'):
                archive.writestr(name, lists.getvalue())
            archive.writestr('PRODUCT/priv-app/AppStore/AppStore.apk', apk.getvalue())
        return path

    def problems(self, path, **kwargs):
        with zipfile.ZipFile(path) as handle:
            return release.archive_problems(handle, **kwargs)

    def test_signed_target_files_trust_only_release_keys(self):
        directory = self.throwaway()
        certificate = directory / 'releasekey.x509.pem'
        catalog = self.pins['catalog']['key']
        path = self.target_files(certificate, catalog)
        self.assertEqual(['RECOVERY/RAMDISK/system/etc/security/otacerts.zip', 'SYSTEM/etc/security/otacerts.zip'],
                         self.problems(path))
        self.assertEqual([], self.problems(path, signed=False))
        pinned = copy.deepcopy(self.pins)
        pinned['certificates']['releasekey'] = release.pem_digests(certificate.read_bytes())[0]
        with mock.patch.object(release, 'pins', return_value=pinned):
            self.assertEqual([], self.problems(path))
            throwaway_catalog = self.target_files(certificate, 'RWT' + 'A' * 53)
            self.assertEqual(['PRODUCT/priv-app/AppStore/AppStore.apk catalog key'], self.problems(throwaway_catalog))

    def test_inventory_of_own_keys_is_public_and_never_the_release_inventory(self):
        directory = self.throwaway()
        payload = b'presigned payload key fixture'
        apex = io.BytesIO()
        with zipfile.ZipFile(apex, 'w') as archive:
            archive.writestr('apex_pubkey', payload)
        rule = self.policy['presigned_apexes'][0]
        target = self.root / 'unsigned-target-files.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr(rule['artifact_path'], apex.getvalue())
        refused = self.root / 'refused'
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(2, cli.main(['inventory', '--key-dir', str(directory), '--identity', 'builder',
                                          '--target-files', str(target), '--output', str(refused)]))
        self.assertIn('differs from the signing policy', errors.getvalue())
        self.assertFalse(refused.exists())
        rule['payload_key_sha256'] = sha(payload)
        output = self.root / 'trust'
        keys.write_inventory(directory, 'builder', target, output, self.policy)
        inventory = json.loads((output / 'inventory.json').read_text())
        inputs.public_inventory_fields(inventory)
        self.assertNotIn(b'PRIVATE KEY', b''.join(p.read_bytes() for p in output.iterdir()))
        self.assertEqual(openssl('pkey', '-in', directory / 'avb.pem', '-pubout', '-outform', 'DER'),
                         openssl('pkey', '-pubin', '-in', output / 'avb.pub.pem', '-outform', 'DER'))
        authority, identity = native.factory_authority(output / 'inventory.json', self.policy)
        self.assertEqual(('factory.allowed_signers', 'builder'), (authority.name, identity))
        with self.assertRaisesRegex(inputs.SigningError, 'release keys: bluetooth, .*, shared, avb, factory$'):
            native.factory_authority(output / 'inventory.json', self.policy, release=True)
        (directory / 'avb_pkmd.bin').write_bytes((directory / 'avb.pem').read_bytes())
        with self.assertRaisesRegex(inputs.SigningError, 'avb_pkmd.bin holds private key material'):
            keys.write_inventory(directory, 'builder', target, self.root / 'private', self.policy)

    def test_release_and_qualification_exclude_each_other(self):
        base = ['factory', '--plan', 'p', '--build-record', 'b', '--verification-report', 'r',
                '--verification-signature', 's', '--target-files', 't', '--resolved-manifest', 'm',
                '--signed-target-files', 'x', '--images-archive', 'i', '--source-root', 'src', '--otatools', 'o',
                '--keys', 'k', '--scratch', 'w', '--output', 'f']
        for flags, mode in (([], 'production'), (['--release'], 'release'), (['--qualification'], 'qualification')):
            self.assertEqual(mode, cli.selected_mode(cli.parser().parse_args(base + flags)))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(base + ['--release', '--qualification'])


class InventoryTools:
    """The host side of NativeTools: openssl, and avbtool's public key export."""
    certificate = native.NativeTools.certificate
    openssl = native.NativeTools.openssl

    def __init__(self, root):
        self.env, self.source, self.scratch = {'PATH': '/usr/bin:/bin'}, root / 'source', root
        for directory, name in (('build/make/target/product/security', 'testkey.x509.pem'),
                                ('external/avb/test/data', 'testkey_rsa4096.pub.pem')):
            (self.source / directory).mkdir(parents=True)
            write_aosp(name, self.source / directory / name.replace('.pub', ''))
        (self.source / 'system/apex').mkdir(parents=True)
        self.denied_apk_public_keys = set()

    def run(self, name, args):
        assert (name, args[0], args[1], args[3]) == ('avbtool', 'extract_public_key', '--key', '--output')
        Path(args[4]).write_bytes(avb_pkmd(args[2]))


class PublicKeysReleaseTests(unittest.TestCase):
    """The inventory a release run loads: throwaway, AOSP test and pinned keys."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='release-inventory-')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        cls.tools = InventoryTools(cls.root)
        cls.trust = cls.root / 'trust'
        cls.trust.mkdir()
        for role in ROLES + ['avb']:
            key = cls.root / (role + '.pem')
            openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:4096', '-out', key)
            if role == 'avb':
                openssl('pkey', '-in', key, '-pubout', '-out', cls.trust / 'avb.pub.pem')
            else:
                openssl('req', '-new', '-x509', '-sha256', '-key', key, '-subj', '/CN=DiamaneOS/', '-days', '1',
                        '-out', cls.trust / (role + '.x509.pem'))
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', cls.root / 'factory'],
                       check=True, capture_output=True)
        cls.signer = 'diamaneos ' + ' '.join((cls.root / 'factory.pub').read_text().split()[:2])
        (cls.trust / 'factory.allowed_signers').write_text(cls.signer + '\n')
        cls.inventory = {'schema_version': 2, 'certificate_roles': {r: r + '.x509.pem' for r in ROLES},
                         'avb_public_key': 'avb.pub.pem',
                         'factory': {'allowed_signers': 'factory.allowed_signers', 'identity': 'diamaneos'}}
        cls.path = cls.trust / 'inventory.json'
        cls.path.write_text(json.dumps(cls.inventory))

    def load(self, path=None, **modes):
        return native.PublicKeys(path or self.path, self.tools, **modes)

    def pinned(self):
        """Pins that name this inventory, as config/release-keys.json names the release inventory."""
        value = release.pins()
        value['certificates'] = {r: self.tools.certificate(self.trust / (r + '.x509.pem'))[0] for r in ROLES}
        value['avb_pkmd_sha256'] = sha(avb_pkmd(self.trust / 'avb.pub.pem'))
        value['avb_public_key_sha256'] = sha(openssl('pkey', '-pubin', '-in', self.trust / 'avb.pub.pem', '-outform', 'DER'))
        value['factory_signer'] = self.signer
        return value

    def test_throwaway_inventory_loads_for_own_keys_and_is_refused_for_a_release(self):
        self.load(production=True)
        with self.assertRaisesRegex(inputs.SigningError, 'release keys: bluetooth, .*, shared, avb, factory$'):
            self.load(production=True, release=True)
        pinned = self.pinned()
        pinned['factory_signer'] = release.pins()['factory_signer']
        with mock.patch.object(release, 'pins', return_value=pinned), \
                self.assertRaisesRegex(inputs.SigningError, 'not the DiamaneOS release keys: factory$'):
            self.load(production=True, release=True)
        pinned = self.pinned()
        pinned['certificates']['platform'] = release.pins()['certificates']['platform']
        with mock.patch.object(release, 'pins', return_value=pinned), \
                self.assertRaisesRegex(inputs.SigningError, 'not the DiamaneOS release keys: platform$'):
            self.load(production=True, release=True)

    def test_aosp_test_keys_are_refused(self):
        # Own keys refuse them too: the AOSP certificate is RSA-2048 with SHA-1, and the AVB key is public.
        for file, test, message, own in (
                ('platform.x509.pem', 'testkey.x509.pem', 'release keys: platform$', 'does not match the role policy'),
                ('avb.pub.pem', 'testkey_rsa4096.pub.pem', 'release keys: avb$', 'refuses AOSP public AVB test keys')):
            with self.subTest(test):
                trust = self.root / test
                shutil.copytree(self.trust, trust)
                write_aosp(test, trust / file)
                with mock.patch.object(release, 'pins', return_value=self.pinned()), \
                        self.assertRaisesRegex(inputs.SigningError, message):
                    self.load(trust / 'inventory.json', production=True, release=True)
                with self.assertRaisesRegex(inputs.SigningError, own):
                    self.load(trust / 'inventory.json', production=True)

    def test_own_keys_carry_any_subject(self):
        trust = self.root / 'subject'
        shutil.copytree(self.trust, trust)
        openssl('req', '-new', '-x509', '-sha256', '-key', self.root / 'platform.pem', '-subj', '/CN=Builder/',
                '-days', '1', '-out', trust / 'platform.x509.pem')
        self.load(trust / 'inventory.json', production=True)
        with mock.patch.object(release, 'pins', return_value=self.pinned()), \
                self.assertRaisesRegex(inputs.SigningError, 'release keys: platform$'):
            self.load(trust / 'inventory.json', production=True, release=True)

    def test_pinned_inventory_is_accepted(self):
        with mock.patch.object(release, 'pins', return_value=self.pinned()):
            keys = self.load(production=True, release=True)
        self.assertEqual(self.pinned()['avb_pkmd_sha256'], sha(keys.avb_pkmd))


if __name__ == '__main__':
    unittest.main()
