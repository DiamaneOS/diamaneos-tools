"""Real platform-tool fixtures, generated in temporary storage with disposable keys."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from diamaneos_tools import signing_archive as za, signing_inputs as inputs, signing_native as native
from diamaneos_tools import signing_verify as api

SOURCE = os.environ.get('DIAMANEOS_SIGNING_NATIVE_SOURCE')
OTATOOLS = os.environ.get('DIAMANEOS_SIGNING_OTATOOLS')


@unittest.skipUnless(SOURCE and OTATOOLS, 'set native source and otatools paths for platform fixtures')
class NativeSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='signing-native-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = Path(SOURCE)
        root = za.unpack_tools(Path(OTATOOLS), cls.root / 'otatools')
        cls.tools = native.NativeTools(root, cls.source, cls.root)
        cls.keys_dir = cls.root / 'public'; cls.keys_dir.mkdir(mode=0o700)
        cls.private = cls.root / 'fixture-keys'; cls.private.mkdir(mode=0o700)
        for role in sorted(inputs.CERT_ROLES | {'avb'}):
            key = cls.private / (role + '.pem')
            cls.tools.openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:4096', '-out', key])
            key.chmod(0o600)
            if role == 'avb':
                cls.tools.openssl(['pkey', '-in', key, '-pubout', '-out', cls.keys_dir / 'avb.pub.pem'])
            else:
                cls.tools.openssl(['req', '-new', '-x509', '-sha256', '-key', key, '-subj', '/CN=DiamaneOS/',
                                   '-days', '1', '-out', cls.keys_dir / (role + '.x509.pem')])
                cls.tools.openssl(['pkcs8', '-topk8', '-nocrypt', '-in', key, '-outform', 'DER',
                                   '-out', cls.private / (role + '.pk8')])
                (cls.private / (role + '.pk8')).chmod(0o600)
        for name in ('factory', 'wrong-factory'):
            inputs.command(['/usr/bin/ssh-keygen', '-t', 'ed25519', '-N', '', '-f', cls.private / name], env=cls.tools.env)
        (cls.keys_dir / 'factory.allowed_signers').write_text('fixture ' +
                    ' '.join((cls.private / 'factory.pub').read_text().split()[:2]) + '\n')
        cls.inventory = {'schema_version': 1, 'certificate_roles': {r: r + '.x509.pem' for r in sorted(inputs.CERT_ROLES)},
                         'avb_public_key': 'avb.pub.pem', 'factory': {'allowed_signers': 'factory.allowed_signers', 'identity': 'fixture'},
                         'presigned': {}, 'metadata_only': []}
        cls.inventory_path = cls.keys_dir / 'inventory.json'; cls.inventory_path.write_text(json.dumps(cls.inventory))
        cls.keys = native.PublicKeys(cls.inventory_path, cls.tools, production=False)
        manifest = cls.root / 'AndroidManifest.xml'
        manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                            'package="de.diamaneos.signingfixture" android:versionCode="1"><uses-sdk '
                            'android:minSdkVersion="29" android:targetSdkVersion="35"/><application/></manifest>')
        cls.unsigned_apk = cls.root / 'unsigned.apk'
        cls.tools.run('aapt2', ['link', '-I', cls.source / 'prebuilts/sdk/current/public/android.jar',
                              '--manifest', manifest, '-o', cls.unsigned_apk])
        cls.apk = cls.sign_apk(cls.unsigned_apk, 'releasekey', cls.root / 'valid.apk')
        content = cls.root / 'system-content'; (content / 'app/Fixture').mkdir(parents=True)
        shutil.copyfile(cls.apk, content / 'app/Fixture/Fixture.apk')
        cls.system = cls.root / 'system.img'
        cls.tools.run('mke2fs', ['-t', 'ext4', '-b', '4096', '-d', content, cls.system, '2048'])
        cls.tools.run('avbtool', ['add_hashtree_footer', '--image', cls.system, '--partition_size', '16777216',
                                '--partition_name', 'system', '--algorithm', 'SHA256_RSA4096', '--key', cls.private / 'avb.pem',
                                '--do_not_generate_fec'])
        apex_unsigned = cls.root / 'unsigned.apex'
        with zipfile.ZipFile(apex_unsigned, 'w') as z:
            with zipfile.ZipFile(cls.unsigned_apk) as apk: z.writestr('AndroidManifest.xml', apk.read('AndroidManifest.xml'))
            z.write(cls.system, 'apex_payload.img')
        cls.apex = cls.sign_apk(apex_unsigned, 'releasekey', cls.root / 'Fixture.apex')
        compressed_unsigned = cls.root / 'unsigned.capex'
        with zipfile.ZipFile(compressed_unsigned, 'w') as z:
            with zipfile.ZipFile(cls.unsigned_apk) as apk: z.writestr('AndroidManifest.xml', apk.read('AndroidManifest.xml'))
            z.write(cls.apex, 'original_apex')
        cls.capex = cls.sign_apk(compressed_unsigned, 'releasekey', cls.root / 'Fixture.capex')
        (content / 'apex').mkdir(); shutil.copyfile(cls.apex, content / 'apex/Fixture.apex')
        cls.system.unlink()
        cls.tools.run('mke2fs', ['-t', 'ext4', '-b', '4096', '-d', content, cls.system, '8192'])
        cls.tools.run('avbtool', ['add_hashtree_footer', '--image', cls.system, '--partition_size', '67108864',
                                '--partition_name', 'system', '--algorithm', 'SHA256_RSA4096', '--key', cls.private / 'avb.pem',
                                '--do_not_generate_fec'])
        cls.boot = cls.root / 'boot.img'; cls.boot.write_bytes(b'fixture' + bytes(16 * 1024 - 7))
        cls.tools.run('avbtool', ['add_hash_footer', '--image', cls.boot, '--partition_size', '131072',
                                '--partition_name', 'boot', '--algorithm', 'SHA256_RSA4096', '--key', cls.private / 'avb.pem'])
        cls.vbmeta = cls.root / 'vbmeta.img'
        cls.tools.run('avbtool', ['make_vbmeta_image', '--output', cls.vbmeta, '--algorithm', 'SHA256_RSA4096',
                                '--key', cls.private / 'avb.pem', '--include_descriptors_from_image', cls.boot,
                                '--include_descriptors_from_image', cls.system])
        cls.target = cls.root / 'signed-target-files.zip'
        with zipfile.ZipFile(cls.target, 'w') as z:
            z.write(cls.boot, 'IMAGES/boot.img'); z.write(cls.vbmeta, 'IMAGES/vbmeta.img'); z.write(cls.system, 'IMAGES/system.img')
            z.write(cls.apk, 'SYSTEM/app/Fixture/Fixture.apk')
            z.write(cls.apex, 'SYSTEM/apex/Fixture.apex')
            z.writestr('META/apkcerts.txt', 'name="Fixture.apk" certificate="keys/testkey.x509.pem" private_key="keys/testkey.pk8"\n')
            z.writestr('META/apexkeys.txt', 'name="Fixture.apex" container_certificate="module.x509.pem" public_key="module.avbpubkey" private_key="module.pem"')
            z.writestr('META/misc_info.txt', 'ab_update=true\n')
            z.writestr('META/ab_partitions.txt', 'boot\n')
        cls.payload = cls.root / 'payload.bin'
        cls.tools.run('delta_generator', ['--partition_names=boot', '--new_partitions=' + str(cls.boot),
                                         '--out_file=' + str(cls.payload), '--private_key=' + str(cls.private / 'releasekey.pem')])
        cls.provenance = {'build_number': 'fixture', 'build_datetime': 1234, 'product': 'FP6',
                          'projects': {}, 'source_files': {}, 'java_sha256': inputs.sha256_file(cls.tools.java_home / 'bin/java')}
        cls.ota = cls.make_ota(cls.payload, 'releasekey', cls.root / 'valid-ota.zip')

    @classmethod
    def sign_apk(cls, source, role, output):
        cls.tools.run('apksigner', ['sign', '--key', cls.private / (role + '.pk8'), '--cert', cls.keys_dir / (role + '.x509.pem'),
                                  '--v4-signing-enabled', 'false', '--out', output, source])
        return output

    @classmethod
    def make_ota(cls, payload, role, output):
        properties = cls.root / ('properties-' + output.name + '.txt')
        cls.tools.run('delta_generator', ['--in_file=' + str(payload), '--properties_file=' + str(properties)])
        unsigned = cls.root / ('unsigned-' + output.name)
        with zipfile.ZipFile(unsigned, 'w') as z:
            z.write(payload, 'payload.bin'); z.write(properties, 'payload_properties.txt')
            z.writestr('META-INF/com/android/metadata', 'ota-type=AB\npost-build-incremental=fixture\npost-timestamp=1234\npre-device=FP6\n')
        cls.tools.run('signapk', ['-w', cls.keys_dir / (role + '.x509.pem'), cls.private / (role + '.pk8'), unsigned, output])
        return output

    def test_native_apk_valid_corrupt_unsigned_wrong_key_and_wrong_role(self):
        self.tools.apk(self.apk, self.keys.fingerprints['releasekey'])
        with self.assertRaises(inputs.SigningError): self.tools.apk(self.unsigned_apk, self.keys.fingerprints['releasekey'])
        platform = self.sign_apk(self.unsigned_apk, 'platform', self.root / 'platform.apk')
        with self.assertRaises(inputs.SigningError): self.tools.apk(platform, self.keys.fingerprints['releasekey'])
        with self.assertRaises(inputs.SigningError): self.tools.apk(self.apk, self.keys.fingerprints['platform'])
        corrupt = self.root / 'corrupt.apk'; shutil.copyfile(self.apk, corrupt)
        with zipfile.ZipFile(corrupt, 'a') as z: z.writestr('new-entry', b'unsigned mutation')
        with self.assertRaises(inputs.SigningError): self.tools.apk(corrupt, self.keys.fingerprints['releasekey'])

    def test_native_apex_compressed_container_payload_and_enclosed_apk(self):
        archive = self.root / 'capex-target.zip'
        with zipfile.ZipFile(archive, 'w') as z: z.write(self.capex, 'SYSTEM/apex/Fixture.capex')
        counts = {'apk': 0, 'apex': 0, 'payload': 0}
        for kind, logical, path in native.package_members(archive, self.tools):
            if kind == 'payload': self.tools.avb(path, self.keys.avb(), payload=True)
            else: self.tools.apk(path, self.keys.fingerprints['releasekey'])
            counts[kind] += 1
        self.assertEqual({'apk': 1, 'apex': 2, 'payload': 1}, counts)

    def test_native_avb_valid_corrupt_unsigned_wrong_key_and_wrong_role(self):
        self.tools.avb(self.vbmeta, self.keys.avb())
        corrupt = self.root / 'corrupt-vbmeta.img'; data = bytearray(self.vbmeta.read_bytes())
        data[512] ^= 1; corrupt.write_bytes(data)
        with self.assertRaises(inputs.SigningError): self.tools.avb(corrupt, self.keys.avb())
        unsigned = self.root / 'unsigned-vbmeta.img'
        self.tools.run('avbtool', ['make_vbmeta_image', '--output', unsigned])
        with self.assertRaises(inputs.SigningError): self.tools.avb(unsigned, self.keys.avb())
        other = self.root / 'platform.pub.pem'
        self.tools.openssl(['pkey', '-in', self.private / 'platform.pem', '-pubout', '-out', other])
        with self.assertRaises(inputs.SigningError): self.tools.avb(self.vbmeta, other)

    def test_native_ota_package_payload_and_metadata_signatures(self):
        self.tools.ota(self.ota, self.keys.cert('releasekey', ''), self.provenance)
        with self.assertRaises(inputs.SigningError):
            self.tools.ota(self.root / ('unsigned-' + self.ota.name), self.keys.cert('releasekey', ''), self.provenance)
        with self.assertRaises(inputs.SigningError): self.tools.ota(self.ota, self.keys.cert('platform', ''), self.provenance)
        wrong = self.make_ota(self.payload, 'platform', self.root / 'wrong-ota.zip')
        with self.assertRaises(inputs.SigningError): self.tools.ota(wrong, self.keys.cert('releasekey', ''), self.provenance)
        unsigned_payload = self.root / 'unsigned-payload.bin'
        self.tools.run('delta_generator', ['--partition_names=boot', '--new_partitions=' + str(self.boot),
                                         '--out_file=' + str(unsigned_payload)])
        unsigned = self.make_ota(unsigned_payload, 'releasekey', self.root / 'unsigned-payload-ota.zip')
        with self.assertRaises(inputs.SigningError): self.tools.ota(unsigned, self.keys.cert('releasekey', ''), self.provenance)
        corrupt = self.root / 'bad-payload.bin'; value = bytearray(self.payload.read_bytes()); value[-1] ^= 1; corrupt.write_bytes(value)
        bad = self.make_ota(corrupt, 'releasekey', self.root / 'bad-payload-ota.zip')
        with self.assertRaises(inputs.SigningError): self.tools.ota(bad, self.keys.cert('releasekey', ''), self.provenance)

    def test_public_test_key_artifacts_are_rejected_even_with_an_expected_certificate(self):
        test = self.source / 'build/make/target/product/security/testkey'
        apk = self.root / 'aosp-testkey.apk'
        self.tools.run('apksigner', ['sign', '--key', test.with_suffix('.pk8'), '--cert', test.with_suffix('.x509.pem'),
                                  '--v4-signing-enabled', 'false', '--out', apk, self.unsigned_apk])
        certificate = self.keys_dir / 'aosp.x509.pem'; shutil.copyfile(test.with_suffix('.x509.pem'), certificate)
        self.tools.apk(apk, self.tools.certificate(certificate)[0])
        inventory = dict(self.inventory, presigned={'SYSTEM/app/Fixture/Fixture.apk': {'certificate': certificate.name, 'avb_public_key': None}})
        path = self.keys_dir / 'aosp-inventory.json'; path.write_text(json.dumps(inventory))
        with self.assertRaises(inputs.SigningError): native.PublicKeys(path, self.tools, production=True)

    def test_public_avb_test_key_is_rejected_in_production(self):
        key = self.source / 'external/avb/test/data/testkey_rsa4096.pem'
        public = self.keys_dir / 'aosp-avb.pub.pem'
        self.tools.openssl(['pkey', '-in', key, '-pubout', '-out', public])
        path = self.keys_dir / 'aosp-avb-inventory.json'
        path.write_text(json.dumps(dict(self.inventory, avb_public_key=public.name)))
        with self.assertRaises(inputs.SigningError): native.PublicKeys(path, self.tools, production=True)

    def test_native_factory_signatures_reject_corrupt_unsigned_wrong_key_and_wrong_namespace(self):
        payload = self.root / 'factory.data'; payload.write_bytes(b'factory fixture')
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory', '-n', 'factory images', payload], env=self.tools.env)
        allowed = self.keys_dir / 'factory.allowed_signers'; signature = payload.with_name(payload.name + '.sig')
        self.tools.ssh_signature(payload, signature, allowed, 'fixture', 'factory images')
        with self.assertRaises(inputs.SigningError): self.tools.ssh_signature(payload, signature, allowed, 'fixture', 'diamaneos-release-record')
        wrong = self.keys_dir / 'wrong.allowed_signers'
        wrong.write_text('fixture ' + ' '.join((self.private / 'wrong-factory.pub').read_text().split()[:2]) + '\n')
        with self.assertRaises(inputs.SigningError): self.tools.ssh_signature(payload, signature, wrong, 'fixture', 'factory images')
        with self.assertRaises(inputs.SigningError): self.tools.ssh_signature(payload, self.root / 'missing.sig', allowed, 'fixture', 'factory images')
        payload.write_bytes(b'changed factory fixture')
        with self.assertRaises(inputs.SigningError): self.tools.ssh_signature(payload, signature, allowed, 'fixture', 'factory images')

    def test_complete_final_publication_and_tamper_refusal(self):
        publication_root = self.root / 'publication'; publication_root.mkdir()
        for source, name in [(self.target, 'target-files.zip'), (self.ota, 'ota.zip')]: shutil.copyfile(source, publication_root / name)
        images = publication_root / 'images.zip'
        with zipfile.ZipFile(images, 'w') as z:
            z.write(self.boot, 'boot.img'); z.write(self.vbmeta, 'vbmeta.img'); z.write(self.system, 'system.img')
        factory = publication_root / 'factory.tar'
        with tarfile.open(factory, 'w') as t: t.add(images, arcname=images.name)
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory', '-n', 'factory images', factory], env=self.tools.env)
        policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        metadata = za.metadata(self.target)
        logical = 'SYSTEM/app/Fixture/Fixture.apk'
        plan = {'schema_version': 2, 'mode': 'qualification', 'policy_sha256': inputs.canonical_sha256(policy),
                'key_inventory_sha256': inputs.canonical_sha256(self.inventory),
                'public_material_sha256': inputs.canonical_sha256(self.keys.files), 'public_identities': self.keys.public_ids,
                'provenance': self.provenance, 'changed_projects': [], 'metadata': {k: metadata[k] for k in ('apk', 'apex')},
                'packages': {logical: {'kind': 'apk', 'container': 'releasekey', 'payload': None,
                                      'certificate_sha256': self.keys.fingerprints['releasekey']},
                             'SYSTEM/apex/Fixture.apex': {'kind': 'apex', 'container': 'releasekey', 'payload': 'avb',
                                      'certificate_sha256': self.keys.fingerprints['releasekey'],
                                      'payload_key_sha256': inputs.sha256_file(self.keys.avb())},
                             'SYSTEM/apex/Fixture.apex!/app/Fixture/Fixture.apk': {'kind': 'apk', 'container': 'releasekey', 'payload': None,
                                      'certificate_sha256': self.keys.fingerprints['releasekey']}},
                'partitions': ['boot', 'system', 'vbmeta'], 'chains': {}, 'passthrough_images': {}, 'previous_artifacts': {}}
        plan['plan_sha256'] = inputs.canonical_sha256(plan)
        names = ['target-files.zip', 'images.zip', 'ota.zip', 'factory.tar', 'factory.tar.sig']
        record = publication_root / 'release.json'
        record.write_text(json.dumps({'schema_version': 1, 'plan_sha256': plan['plan_sha256'],
                                      'artifacts': {name: inputs.sha256_file(publication_root / name) for name in names}}))
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory', '-n', 'diamaneos-release-record', record], env=self.tools.env)
        publication = {'schema_version': 1, 'signed_target_files': 'target-files.zip', 'images_archive': 'images.zip',
                       'ota': [{'file': 'ota.zip', 'source_target_files': None}], 'factory_archive': 'factory.tar',
                       'factory_signature': 'factory.tar.sig', 'release_record': 'release.json', 'release_signature': 'release.json.sig'}
        # Source checkout verification has its own Git fixtures; cryptography here remains native.
        with patch.object(api, 'source_files', return_value={}):
            result = api.verify_publication(plan, publication, publication_root, policy, self.tools, self.keys)
            self.assertIs(True, result['artifact_signatures_verified'])
            with (publication_root / 'ota.zip').open('ab') as stream: stream.write(b'changed')
            with self.assertRaises(inputs.SigningError): api.verify_publication(plan, publication, publication_root, policy, self.tools, self.keys)


if __name__ == '__main__':
    unittest.main()
