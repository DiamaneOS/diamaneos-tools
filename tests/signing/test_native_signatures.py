"""Real platform-tool fixtures, generated in temporary storage with disposable keys."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from diamaneos_tools import build_workspace as bw, image_verify
from diamaneos_tools import signing_archive as za, signing_inputs as inputs, signing_native as native
from diamaneos_tools import signing_verify as api
from .fixtures import apk_plan, apex_plan, publication_description, publication_plan, release_record, write_zip

SOURCE = os.environ.get('DIAMANEOS_SIGNING_NATIVE_SOURCE')
OTATOOLS = os.environ.get('DIAMANEOS_SIGNING_OTATOOLS')


def require_native_inputs():
    missing = [name for name, value in (
        ('DIAMANEOS_SIGNING_NATIVE_SOURCE', SOURCE), ('DIAMANEOS_SIGNING_OTATOOLS', OTATOOLS)) if not value]
    if not missing:
        return
    message = 'Native signature fixtures require explicit platform paths: ' + ', '.join(missing)
    if sys.platform.startswith('linux') or os.environ.get('DIAMANEOS_SIGNING_REQUIRE_NATIVE') == '1':
        raise RuntimeError(message + '. Required native verification cannot be skipped')
    raise unittest.SkipTest(message)


class NativeSignatureTests(unittest.TestCase):
    def setUp(self):
        self.tools.denied_apk_public_keys = set()

    @classmethod
    def setUpClass(cls):
        require_native_inputs()
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
        cls.inventory = {'schema_version': 2, 'certificate_roles': {r: r + '.x509.pem' for r in sorted(inputs.CERT_ROLES)},
                         'avb_public_key': 'avb.pub.pem', 'factory': {'allowed_signers': 'factory.allowed_signers', 'identity': 'fixture'}}
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
        cls.hashtree(content, cls.system, 'system')
        apex_unsigned = cls.root / 'unsigned.apex'
        cls.apex_archive(apex_unsigned, 'apex_payload.img', cls.system)
        cls.apex = cls.sign_apk(apex_unsigned, 'releasekey', cls.root / 'Fixture.apex')
        compressed_unsigned = cls.root / 'unsigned.capex'
        cls.apex_archive(compressed_unsigned, 'original_apex', cls.apex)
        cls.capex = cls.sign_apk(compressed_unsigned, 'releasekey', cls.root / 'Fixture.capex')
        (content / 'apex').mkdir(); shutil.copyfile(cls.apex, content / 'apex/Fixture.apex')
        cls.mac_member = 'SYSTEM/etc/selinux/plat_mac_permissions.xml'
        der = cls.tools.openssl(['x509', '-in', cls.keys_dir / 'releasekey.x509.pem', '-outform', 'DER'])
        cls.mac_xml = '<policy><signer signature="' + der.hex() + '"><seinfo value="default"/></signer></policy>'
        mac_path = content / 'etc/selinux/plat_mac_permissions.xml'; mac_path.parent.mkdir(parents=True)
        mac_path.write_text(cls.mac_xml)
        cls.system.unlink()
        cls.hashtree(content, cls.system, 'system', blocks=8192, size=67108864)
        cls.boot = cls.root / 'boot.img'; cls.boot.write_bytes(b'fixture' + bytes(16 * 1024 - 7))
        cls.tools.run('avbtool', ['add_hash_footer', '--image', cls.boot, '--partition_size', '131072',
                                '--partition_name', 'boot', '--algorithm', 'SHA256_RSA4096', '--key', cls.private / 'avb.pem'])
        cls.vbmeta = cls.root / 'vbmeta.img'
        cls.tools.run('avbtool', ['make_vbmeta_image', '--output', cls.vbmeta, '--algorithm', 'SHA256_RSA4096',
                                '--key', cls.private / 'avb.pem', '--include_descriptors_from_image', cls.boot,
                                '--include_descriptors_from_image', cls.system])
        cls.target = cls.root / 'signed-target-files.zip'
        write_zip(cls.target, {
            **{'IMAGES/' + p.name: p for p in (cls.boot, cls.vbmeta, cls.system)},
            'SYSTEM/app/Fixture/Fixture.apk': cls.apk, 'SYSTEM/apex/Fixture.apex': cls.apex,
            cls.mac_member: cls.mac_xml,
            'META/apkcerts.txt': 'name="Fixture.apk" certificate="keys/testkey.x509.pem" private_key="keys/testkey.pk8"\n',
            'META/apexkeys.txt': 'name="Fixture.apex" container_certificate="module.x509.pem" public_key="module.avbpubkey" private_key="module.pem"',
            'META/misc_info.txt': 'ab_update=true\n', 'META/ab_partitions.txt': 'boot\n'})
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
    def hashtree(cls, content, image, partition, *, role='avb', blocks=2048, size=16777216):
        cls.tools.run('mke2fs', ['-t', 'ext4', '-b', '4096', '-d', content, image, str(blocks)])
        cls.tools.run('avbtool', ['add_hashtree_footer', '--image', image, '--partition_size', str(size),
                                '--partition_name', partition, '--algorithm', 'SHA256_RSA4096',
                                '--key', cls.private / (role + '.pem'), '--do_not_generate_fec'])

    @classmethod
    def apex_archive(cls, output, member, payload):
        with zipfile.ZipFile(cls.unsigned_apk) as apk:
            manifest = apk.read('AndroidManifest.xml')
        write_zip(output, {'AndroidManifest.xml': manifest, member: payload})

    @classmethod
    def make_ota(cls, payload, role, output):
        properties = cls.root / ('properties-' + output.name + '.txt')
        cls.tools.run('delta_generator', ['--in_file=' + str(payload), '--properties_file=' + str(properties)])
        unsigned = cls.root / ('unsigned-' + output.name)
        write_zip(unsigned, {'payload.bin': payload, 'payload_properties.txt': properties,
                            'META-INF/com/android/metadata': 'ota-type=AB\npost-build-incremental=fixture\npost-timestamp=1234\npre-device=FP6\n'})
        cls.tools.run('signapk', ['-w', cls.keys_dir / (role + '.x509.pem'), cls.private / (role + '.pk8'), unsigned, output])
        return output

    def fixture_directory(self, prefix):
        temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=self.root)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_native_prepare_cli_authenticates_snapshots_and_emits_bound_plan(self):
        root = self.fixture_directory('prepare-cli-')
        source = root / 'source'; source.mkdir()
        jdk_project = str(self.tools.java_home.parent.relative_to(self.source))
        projects = sorted(inputs.PROJECTS | {jdk_project})
        for project in projects: (source / project).mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.tools.java_home, source / jdk_project / 'linux-x86', symlinks=True)
        for name in ('generate-release.sh', 'generate-delta.sh', 'common.sh'):
            (source / 'script' / name).write_text('#!/bin/sh\n--extra_apks Fixture.apk=$KEY_DIR/releasekey\n')
        checker = Path('build/make/tools/releasetools/check_ota_package_signature.py')
        (source / checker).parent.mkdir(parents=True)
        shutil.copyfile(self.source / checker, source / checker)
        shutil.copyfile(self.source / 'external/avb/avbtool.py', source / 'external/avb/avbtool.py')
        rules_path = source / 'tools/diamaneos/config/fp6-image-checks.json'
        rules_path.parent.mkdir()
        shutil.copyfile(inputs.ROOT / 'config/fp6-image-checks.json', rules_path)
        certificate = source / 'build/make/target/product/security/testkey.x509.pem'
        certificate.parent.mkdir(parents=True)
        shutil.copyfile(self.keys_dir / 'releasekey.x509.pem', certificate)

        # Only Git is a fixture: native tools, public-key validation, report authentication,
        # both verified_build calls, source-byte checks and plans.prepare remain real.
        def blob(raw):
            return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        trees = {}
        for project in projects:
            rows = []
            for path in sorted((source / project).rglob('*')):
                if path.is_symlink(): mode, raw = '120000', os.fsencode(os.readlink(path))
                elif path.is_file(): mode, raw = '100755' if path.stat().st_mode & 0o111 else '100644', path.read_bytes()
                else: continue
                name = str(path.relative_to(source / project))
                rows.append((name, f'{mode} blob {blob(raw)}\t{name}'.encode()))
            trees[project] = rows
        def source_git(path, argv, *, data=None):
            if argv[0] == 'rev-parse': return ('a' * 40).encode()
            if argv[0] == 'status': return b''
            if argv[0] == 'hash-object':
                return blob(data if '--stdin' in argv else Path(argv[-1]).read_bytes()).encode()
            self.assertEqual('ls-tree', argv[0])
            wanted = argv[argv.index('--') + 1:]
            project = str(Path(path).relative_to(source))
            return b''.join(row + b'\0' for name, row in trees[project]
                            if any(part == '.' or name == part or name.startswith(part + '/') for part in wanted))

        target, otatools = root / 'target-files.zip', root / 'otatools.zip'
        shutil.copyfile(self.target, target); shutil.copyfile(OTATOOLS, otatools)
        manifest = root / 'resolved-manifest.xml'
        manifest.write_text('<manifest><remote name="up" fetch="https://example.invalid"/><default remote="up"/>' +
            ''.join(f'<project name="{name}" path="{name}" revision="{"a" * 40}"/>' for name in projects) + '</manifest>')
        record = root / 'build-record.json'
        record.write_text(json.dumps({'build_id': 'fixture', 'build_identity': 'b' * 64,
            'product': 'FP6', 'build_number': 'fixture', 'build_datetime': 1234,
            'variant': 'user', 'official': True, 'modified': [], 'tools': {'clean': True},
            'target_files': {'file': target.name, 'sha256': inputs.sha256_file(target)},
            'otatools': {'file': otatools.name, 'sha256': inputs.sha256_file(otatools)},
            'manifest': {'file': manifest.name, 'resolved_sha256': inputs.sha256_file(manifest)},
            'images': {path.stem: inputs.sha256_file(path) for path in (self.boot, self.system, self.vbmeta)}}))
        sums = root / 'SHA256SUMS'
        sums.write_text(''.join(inputs.sha256_file(path) + '  ' + path.name + '\n'
                               for path in (record, target, otatools, manifest)))
        rules = inputs.load_json(rules_path)
        checks = [{'id': name, 'status': 'PASS'} for name in sorted(set(image_verify.REQUIRED_CHECKS) |
            {rule['id'] for rule in image_verify.validate_rules(rules) if image_verify.rule_applies(rule, 'user', True)})]
        report = root / 'verification-report.json'
        report.write_text(json.dumps({'schema_version': 1, 'build_id': 'fixture', 'build_identity': 'b' * 64,
            'sums_sha256': inputs.sha256_file(sums), 'rule_set_sha256': bw.digest(rules),
            'checks': checks, 'status': 'PASS', 'checked': len(checks), 'failed': 0}))
        policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory',
                        '-n', policy['verification_namespace'], report], env=self.tools.env)
        before = inputs.input_hashes((record, report, sums, manifest, target, otatools, self.inventory_path))
        scratch, output = root / 'scratch', root / 'plan.json'
        arguments = ['prepare', '--qualification', '--build-record', str(record),
            '--verification-report', str(report), '--verification-signature', str(report) + '.sig',
            '--target-files', str(target), '--resolved-manifest', str(manifest), '--source-root', str(source),
            '--otatools', str(otatools), '--keys', str(self.inventory_path), '--scratch', str(scratch), '--output', str(output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(inputs, 'git_read', side_effect=source_git), \
             patch('sys.stdout', new=stdout), patch('sys.stderr', new=stderr):
            self.assertEqual(0, api.main(arguments), stderr.getvalue())
        self.assertIn('Signing prepare: PASS', stdout.getvalue())
        plan = inputs.load_json(output); api.validate_plan(plan, policy)
        self.assertEqual('qualification', plan['mode'])
        for name, path in (('record', record), ('verification', report), ('manifest', manifest),
                           ('target_files', target), ('otatools', otatools)):
            self.assertEqual(inputs.sha256_file(path), plan['provenance'][name + '_sha256'])
        self.assertEqual(inputs.canonical_sha256(self.keys.files), plan['public_material_sha256'])
        self.assertEqual(self.keys.fingerprint('releasekey', ''),
                         plan['packages']['SYSTEM/app/Fixture/Fixture.apk']['certificate_sha256'])
        self.assertEqual(inputs.sha256_file(rules_path),
                         plan['provenance']['source_files']['tools/diamaneos/config/fp6-image-checks.json'])
        inputs.recheck_hashes(before)
        self.assertEqual([], list(scratch.iterdir()))

    def tamper_image(self, path, offset):
        with path.open('r+b') as stream:
            stream.seek(offset)
            byte = stream.read(1)
            self.assertEqual(1, len(byte), 'Mutation must hit existing signed bytes')
            stream.seek(offset)
            stream.write(bytes([byte[0] ^ 1]))

    def apex_fixture(self, *, container_role='releasekey', payload_role='avb', enclosed_apk=None,
                     tamper_payload=False):
        """Sign fresh payload and container. Expected roles stay in external fixture keys."""
        root = self.fixture_directory('apex-negative-')
        content = root / 'content'
        enclosed = content / 'app/Fixture/Fixture.apk'
        enclosed.parent.mkdir(parents=True)
        shutil.copyfile(enclosed_apk or self.apk, enclosed)
        payload = root / 'apex_payload.img'
        self.hashtree(content, payload, 'fixture_payload', role=payload_role)
        payload_public = self.keys.avb()
        if payload_role != 'avb':
            payload_public = root / 'independent-wrong-payload.pub.pem'
            self.tools.openssl(['pkey', '-in', self.private / (payload_role + '.pem'), '-pubout', '-out', payload_public])
        self.tools.avb(payload, payload_public, payload=True)
        if tamper_payload:
            self.tamper_image(payload, 1024 * 1024)
        unsigned = root / 'unsigned.apex'
        self.apex_archive(unsigned, 'apex_payload.img', payload)
        apex = self.sign_apk(unsigned, container_role, root / 'Fixture.apex')
        self.tools.apk(apex, self.keys.fingerprints[container_role])
        target = root / 'target-files.zip'
        logical = 'SYSTEM/apex/Fixture.apex'
        write_zip(target, {logical: apex, 'META/apkcerts.txt': '',
                          'META/apexkeys.txt': 'name="Fixture.apex" container_certificate="releasekey.x509.pem"\n'})
        plan = {'metadata': za.package_metadata_shape(za.metadata(target)), 'packages': {
            logical: apex_plan(self.keys.fingerprints['releasekey'], self.keys.avb()),
            logical + '!/app/Fixture/Fixture.apk': apk_plan('releasekey', self.keys.fingerprints['releasekey'],
                                                          'de.diamaneos.signingfixture')}}
        return target, plan

    def assert_apex_rejected_at(self, target, plan, boundary):
        """Spy on actual native calls so an unrelated fixture failure cannot pass."""
        with patch.object(self.tools, 'apk', wraps=self.tools.apk) as apk, \
                patch.object(self.tools, 'avb', wraps=self.tools.avb) as avb:
            with self.assertRaises(inputs.SigningError):
                api.verify_packages(target, plan, self.tools, self.keys)
            self.assertEqual('Fixture.apex', apk.call_args_list[0].args[0].name)
            self.assertEqual(self.keys.fingerprints['releasekey'], apk.call_args_list[0].args[1])
            if boundary == 'container':
                self.assertEqual(1, apk.call_count)
                avb.assert_not_called()
            else:
                avb.assert_called_once()
                self.assertEqual(self.keys.avb(), avb.call_args.args[1])
                self.assertEqual({'payload': True}, avb.call_args.kwargs)
                if boundary == 'payload':
                    self.assertEqual(1, apk.call_count)
                else:
                    self.assertEqual(2, apk.call_count)
                    self.assertEqual('Fixture.apk', apk.call_args_list[1].args[0].name)
                    self.assertEqual(self.keys.fingerprints['releasekey'], apk.call_args_list[1].args[1])

    def image_fixture(self):
        root = self.fixture_directory('avb-negative-') / 'images'
        root.mkdir()
        for path in (self.boot, self.system, self.vbmeta):
            shutil.copyfile(path, root / path.name)
        return root

    def write_chain_child(self, root, role='avb'):
        child = root / 'vbmeta_system.img'
        self.tools.run('avbtool', ['make_vbmeta_image', '--output', child, '--algorithm', 'SHA256_RSA4096',
                                 '--key', self.private / (role + '.pem'), '--rollback_index', '7',
                                 '--include_descriptors_from_image', root / 'system.img'])
        return child

    def chain_fixture(self, location=1):
        root = self.image_fixture()
        self.write_chain_child(root)
        public = root.parent / 'independent-chain.avbpubkey'
        self.tools.run('avbtool', ['extract_public_key', '--key', self.keys.avb(), '--output', public])
        self.tools.run('avbtool', ['make_vbmeta_image', '--output', root / 'vbmeta.img', '--algorithm', 'SHA256_RSA4096',
                                 '--key', self.private / 'avb.pem', '--include_descriptors_from_image', root / 'boot.img',
                                 '--chain_partition', 'vbmeta_system:' + str(location) + ':' + str(public)])
        expected = ['boot', 'system', 'vbmeta', 'vbmeta_system']
        chains = {'vbmeta_system': location}
        self.assertEqual(expected, native.verify_images(self.tools, self.keys, root, expected, chains))
        return root, expected, chains

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
        write_zip(archive, {'SYSTEM/apex/Fixture.capex': self.capex})
        counts = {'apk': 0, 'apex': 0, 'payload': 0}
        for kind, logical, path in native.package_members(archive, self.tools):
            if kind == 'payload': self.tools.avb(path, self.keys.avb(), payload=True)
            else: self.tools.apk(path, self.keys.fingerprints['releasekey'])
            counts[kind] += 1
        self.assertEqual({'apk': 1, 'apex': 2, 'payload': 1}, counts)

    def test_native_apex_wrong_container_key_is_rejected(self):
        target, plan = self.apex_fixture(container_role='platform')
        self.assert_apex_rejected_at(target, plan, 'container')

    def test_native_apex_fresh_container_payload_and_enclosed_apk_verify(self):
        target, plan = self.apex_fixture()
        self.assertEqual({'apk': 1, 'apex': 1, 'payload': 1},
                         api.verify_packages(target, plan, self.tools, self.keys))

    def test_native_apex_wrong_payload_key_is_rejected(self):
        target, plan = self.apex_fixture(payload_role='platform')
        self.assert_apex_rejected_at(target, plan, 'payload')

    def presigned_apex_fixture(self, *, payload_role='avb', container_role='releasekey', tamper=False, missing=False):
        root = self.fixture_directory('presigned-apex-')
        content = root / 'content'; content.mkdir(); (content / 'fixture.txt').write_text('no enclosed APK')
        payload = root / 'apex_payload.img'
        self.hashtree(content, payload, 'presigned_fixture', role=payload_role)
        if tamper:
            with payload.open('r+b') as stream:
                stream.seek(8192); byte = stream.read(1); stream.seek(8192); stream.write(bytes([byte[0] ^ 1]))
        unsigned = root / 'unsigned.apex'; self.apex_archive(unsigned, 'apex_payload.img', payload)
        container = self.sign_apk(unsigned, container_role, root / 'Fixture.apex')
        external = self.keys_dir / ('presigned-' + root.name + '.avbpubkey')
        # Expected authority is generated independently from the disposable key,
        # never enrolled from the submitted APEX's embedded public key.
        self.tools.run('avbtool', ['extract_public_key', '--key', self.private/'avb.pem', '--output', external])
        logical = 'VENDOR/apex/Fixture.apex'
        policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        policy['presigned_apexes'] = [{'metadata_name': 'Fixture.apex', 'artifact_path': logical,
            'artifact_sha256': inputs.sha256_file(container), 'certificate_sha256': [self.keys.fingerprints['releasekey']],
            'payload_key_sha256': inputs.sha256_file(external)}]
        inventory = dict(self.inventory)
        if not missing: inventory['presigned_apex_payload_keys'] = {'Fixture.apex': external.name}
        inventory_path = self.keys_dir / (root.name + '.json'); inventory_path.write_text(json.dumps(inventory))
        keys = native.PublicKeys(inventory_path, self.tools, production=False, policy=policy)
        target = root / 'target.zip'
        write_zip(target, {logical: container, 'META/apkcerts.txt': '',
            'META/apexkeys.txt': 'name="Fixture.apex" container_certificate="PRESIGNED" private_key="PRESIGNED"',
            'META/misc_info.txt': 'ab_update=true\n'})
        plan = {'metadata': za.package_metadata_shape(za.metadata(target)), 'packages': {logical: {
            'kind': 'apex', 'container': 'PRESIGNED', 'payload': 'PRESIGNED',
            'certificate_sha256': self.keys.fingerprints['releasekey'], 'payload_key_sha256': inputs.sha256_file(external),
            'presigned_sha256': inputs.sha256_file(container)}}}
        return target, plan, keys

    def test_native_presigned_apex_binary_authority_verifies_without_modifying_container(self):
        target, plan, keys = self.presigned_apex_fixture()
        before = inputs.sha256_file(target)
        self.assertEqual({'apk': 0, 'apex': 1, 'payload': 1}, api.verify_packages(target, plan, self.tools, keys))
        self.assertEqual(before, inputs.sha256_file(target))

    def test_native_presigned_apex_wrong_container_signer_refuses(self):
        target, plan, keys = self.presigned_apex_fixture(container_role='platform')
        with self.assertRaisesRegex(inputs.SigningError, 'certificate does not match'):
            api.verify_packages(target, plan, self.tools, keys)

    def test_native_presigned_apex_valid_crypto_but_wrong_embedded_payload_key_refuses(self):
        target, plan, keys = self.presigned_apex_fixture(payload_role='platform')
        with self.assertRaises(inputs.SigningError): api.verify_packages(target, plan, self.tools, keys)

    def test_native_presigned_apex_missing_external_authority_refuses(self):
        target, plan, keys = self.presigned_apex_fixture(missing=True)
        with self.assertRaisesRegex(inputs.SigningError, 'independent external public authority'):
            api.verify_packages(target, plan, self.tools, keys)

    def test_native_presigned_apex_tampered_payload_refuses_before_binary_comparison(self):
        target, plan, keys = self.presigned_apex_fixture(tamper=True)
        with self.assertRaises(inputs.SigningError): api.verify_packages(target, plan, self.tools, keys)

    def test_native_apex_tampered_payload_in_correctly_signed_container_is_rejected(self):
        target, plan = self.apex_fixture(tamper_payload=True)
        self.assert_apex_rejected_at(target, plan, 'payload')

    def test_native_apex_corrupt_enclosed_apk_with_valid_outer_signatures_is_rejected(self):
        root = self.fixture_directory('corrupt-enclosed-apk-')
        corrupt = root / 'Fixture.apk'
        shutil.copyfile(self.apk, corrupt)
        with zipfile.ZipFile(corrupt, 'a') as archive:
            archive.writestr('unsigned-enclosed-mutation', b'changed after APK signing')
        with self.assertRaises(inputs.SigningError):
            self.tools.apk(corrupt, self.keys.fingerprints['releasekey'])
        target, plan = self.apex_fixture(enclosed_apk=corrupt)
        self.assert_apex_rejected_at(target, plan, 'enclosed-apk')

    def test_native_apex_wrong_role_enclosed_apk_with_valid_outer_signatures_is_rejected(self):
        root = self.fixture_directory('wrong-role-enclosed-apk-')
        platform = self.sign_apk(self.unsigned_apk, 'platform', root / 'Fixture.apk')
        self.tools.apk(platform, self.keys.fingerprints['platform'])
        target, plan = self.apex_fixture(enclosed_apk=platform)
        self.assert_apex_rejected_at(target, plan, 'enclosed-apk')

    def test_native_avb_valid_corrupt_unsigned_wrong_key_and_wrong_role(self):
        self.tools.avb(self.vbmeta, self.keys.avb())
        corrupt = self.root / 'corrupt-vbmeta.img'; shutil.copyfile(self.vbmeta, corrupt)
        self.tamper_image(corrupt, 512)
        with self.assertRaises(inputs.SigningError): self.tools.avb(corrupt, self.keys.avb())
        unsigned = self.root / 'unsigned-vbmeta.img'
        self.tools.run('avbtool', ['make_vbmeta_image', '--output', unsigned])
        with self.assertRaises(inputs.SigningError): self.tools.avb(unsigned, self.keys.avb())
        other = self.root / 'platform.pub.pem'
        self.tools.openssl(['pkey', '-in', self.private / 'platform.pem', '-pubout', '-out', other])
        with self.assertRaises(inputs.SigningError): self.tools.avb(self.vbmeta, other)

    def test_native_avb_boot_and_system_partition_tampering_is_rejected(self):
        expected = ['boot', 'system', 'vbmeta']
        for partition, offset in (('boot', 8192), ('system', 1024 * 1024)):
            with self.subTest(partition=partition):
                root = self.image_fixture()
                self.assertEqual(expected, native.verify_images(self.tools, self.keys, root, expected, {}))
                image = root / (partition + '.img')
                self.tools.avb(image, self.keys.avb())
                self.tamper_image(image, offset)
                with self.assertRaises(inputs.SigningError):
                    self.tools.avb(image, self.keys.avb())
                with self.assertRaises(inputs.SigningError):
                    native.verify_images(self.tools, self.keys, root, expected, {})

    def test_native_apex_unnamed_hashtree_payload_keeps_signature_and_data_checks(self):
        path = self.root / 'unnamed-apex-payload.img'
        path.write_bytes(b'disposable apex data' + bytes(16384 - 20))
        self.tools.run('avbtool', ['add_hashtree_footer', '--image', path, '--partition_size', '262144',
                                 '--partition_name', '', '--algorithm', 'SHA256_RSA4096',
                                 '--hash_algorithm', 'sha256', '--do_not_generate_fec', '--key', self.private / 'avb.pem'])
        info = self.tools.avb(path, self.keys.avb(), payload=True)
        self.assertEqual([('', None)], native.avb_descriptors(info, allow_empty_payload=True))
        with self.assertRaises(inputs.SigningError): native.avb_descriptors(info)
        other = self.root / 'wrong-apex-key.pub.pem'
        self.tools.openssl(['pkey', '-in', self.private / 'platform.pem', '-pubout', '-out', other])
        with self.assertRaises(inputs.SigningError): self.tools.avb(path, other, payload=True)
        self.tamper_image(path, 0)
        with self.assertRaises(inputs.SigningError): self.tools.avb(path, self.keys.avb(), payload=True)
        self.assertFalse((path.parent / '.img').exists())

    def test_native_avb_chained_vbmeta_wrong_key_is_rejected(self):
        root, expected, chains = self.chain_fixture()
        child = self.write_chain_child(root, role='platform')
        other = root.parent / 'independent-wrong-chain.pub.pem'
        self.tools.openssl(['pkey', '-in', self.private / 'platform.pem', '-pubout', '-out', other])
        self.tools.avb(child, other)
        with self.assertRaises(inputs.SigningError):
            self.tools.avb(child, self.keys.avb())
        with self.assertRaises(inputs.SigningError):
            native.verify_images(self.tools, self.keys, root, expected, chains)

    def test_native_avb_chained_vbmeta_tampering_is_rejected(self):
        root, expected, chains = self.chain_fixture()
        child = root / 'vbmeta_system.img'
        self.tamper_image(child, 512)
        with self.assertRaises(inputs.SigningError):
            self.tools.avb(child, self.keys.avb())
        with self.assertRaises(inputs.SigningError):
            native.verify_images(self.tools, self.keys, root, expected, chains)

    def test_native_avb_signed_chain_rollback_location_and_missing_chain_are_rejected(self):
        root, expected, _ = self.chain_fixture(location=2)
        for chains in ({'vbmeta_system': 1}, {}, {'vbmeta_system': 2, 'vbmeta_vendor': 3}):
            with self.subTest(chains=chains), self.assertRaisesRegex(inputs.SigningError, 'rollback chain|every declared partition and chain'):
                native.verify_images(self.tools, self.keys, root, expected, chains)

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
        certificate_digest = self.tools.certificate(certificate)[0]
        self.tools.apk(apk, certificate_digest)
        policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        policy['presigned_packages'] = [{'metadata_name': 'Fixture.apk', 'artifact_basename': 'Fixture.apk',
                                        'parent_apex': None, 'certificate_sha256': [certificate_digest]}]
        accepted = native.PublicKeys(self.inventory_path, self.tools, production=False, policy=policy)
        self.assertEqual(certificate_digest, accepted.fingerprint('PRESIGNED', 'SYSTEM/app/Fixture/Fixture.apk'))
        with self.assertRaisesRegex(inputs.SigningError, 'Production refuses AOSP public test certificates'):
            native.PublicKeys(self.inventory_path, self.tools, production=True, policy=policy)

    def test_public_avb_test_key_is_rejected_in_production(self):
        key = self.source / 'external/avb/test/data/testkey_rsa4096.pem'
        public = self.keys_dir / 'aosp-avb.pub.pem'
        self.tools.openssl(['pkey', '-in', key, '-pubout', '-out', public])
        path = self.keys_dir / 'aosp-avb-inventory.json'
        path.write_text(json.dumps(dict(self.inventory, avb_public_key=public.name)))
        with self.assertRaisesRegex(inputs.SigningError, 'Production refuses AOSP public AVB test keys'):
            native.PublicKeys(path, self.tools, production=True)

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
        write_zip(images, {p.name: p for p in (self.boot, self.vbmeta, self.system)})
        factory = publication_root / 'factory.tar'
        with tarfile.open(factory, 'w') as t: t.add(images, arcname=images.name)
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory', '-n', 'factory images', factory], env=self.tools.env)
        policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        logical = 'SYSTEM/app/Fixture/Fixture.apk'
        packages = {logical: apk_plan('releasekey', self.keys.fingerprints['releasekey'], 'de.diamaneos.signingfixture'),
                    'SYSTEM/apex/Fixture.apex': apex_plan(self.keys.fingerprints['releasekey'], self.keys.avb()),
                    'SYSTEM/apex/Fixture.apex!/app/Fixture/Fixture.apk':
                        apk_plan('releasekey', self.keys.fingerprints['releasekey'], 'de.diamaneos.signingfixture')}
        plan = publication_plan(self.target, packages, self.keys, self.provenance, policy,
            roles={'Fixture.apk': {'kind': 'apk', 'container': 'releasekey', 'payload': None},
                   'Fixture.apex': {'kind': 'apex', 'container': 'releasekey', 'payload': 'avb'}},
            mac_permissions={self.mac_member: [{'certificates': [self.keys.fingerprints['releasekey']],
                                                'seinfo': 'default', 'packages': {}}]},
            partitions=['boot', 'system', 'vbmeta'])
        publication = publication_description()
        record = release_record(publication_root, plan, publication)
        inputs.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', self.private / 'factory', '-n', 'diamaneos-release-record', record], env=self.tools.env)
        # Source checkout verification has its own Git fixtures. Cryptography here remains native.
        with patch.object(api, 'source_files', return_value={}):
            result = api.verify_publication(plan, publication, publication_root, policy, self.tools, self.keys)
            self.assertIs(True, result['artifact_signatures_verified'])
            with (publication_root / 'ota.zip').open('ab') as stream: stream.write(b'changed')
            with self.assertRaises(inputs.SigningError): api.verify_publication(plan, publication, publication_root, policy, self.tools, self.keys)


class NativeFixtureGateTests(unittest.TestCase):
    """Exercise input gating without unpacking archives or executing platform tools."""

    def setup_gate(self, platform, required, source, otatools, error):
        with patch.object(sys, 'platform', platform), \
                patch.dict(os.environ, {'DIAMANEOS_SIGNING_REQUIRE_NATIVE': required}), \
                patch.dict(globals(), {'SOURCE': source, 'OTATOOLS': otatools}), \
                patch.object(tempfile, 'TemporaryDirectory') as temporary:
            with self.assertRaisesRegex(error, 'require explicit platform paths'):
                NativeSignatureTests.setUpClass()
            temporary.assert_not_called()

    def test_linux_missing_either_input_fails_before_fixture_creation(self):
        missing = ((None, None), ('/fixture/source', None), (None, '/fixture/otatools.zip'))
        # Linux always needs both inputs. Elsewhere a run that is explicitly required fails
        # without them, and an optional one may skip.
        for platform, required, absent, error in (
                ('linux', '0', missing + (('', ''),), RuntimeError),
                ('darwin', '1', missing, RuntimeError), ('win32', '1', missing, RuntimeError),
                ('darwin', '0', missing, unittest.SkipTest), ('win32', '0', missing, unittest.SkipTest)):
            for source, otatools in absent:
                with self.subTest(platform=platform, required=required, source=source, otatools=otatools):
                    self.setup_gate(platform, required, source, otatools, error)

    def test_supplied_inputs_pass_only_the_gate_without_executing_tools(self):
        for platform, required in (('linux', '0'), ('darwin', '0'), ('darwin', '1')):
            with self.subTest(platform=platform, required=required), \
                    patch.object(sys, 'platform', platform), \
                    patch.dict(os.environ, {'DIAMANEOS_SIGNING_REQUIRE_NATIVE': required}), \
                    patch.dict(globals(), {'SOURCE': '/fixture/source', 'OTATOOLS': '/fixture/otatools.zip'}), \
                    patch.object(za, 'unpack_tools') as unpack:
                require_native_inputs()
                unpack.assert_not_called()

    def test_native_fixtures_have_no_unconditional_skip_decorators(self):
        self.assertFalse(getattr(NativeSignatureTests, '__unittest_skip__', False))
        for name in unittest.defaultTestLoader.getTestCaseNames(NativeSignatureTests):
            with self.subTest(test=name):
                self.assertFalse(getattr(getattr(NativeSignatureTests, name), '__unittest_skip__', False))


if __name__ == '__main__':
    unittest.main()
