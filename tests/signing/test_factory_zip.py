"""Factory construction and hostile publication inputs; native tools are controlled seams."""
import copy
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from diamaneos_tools import flash_all, signing_factory as factory, signing_layout as layout
from diamaneos_tools import signing_inputs as si, signing_verify as api
from .fixtures import TemporaryInputs, write_zip


VBMETA_INFO = ('Descriptors:\n    Chain Partition descriptor:\n      Partition Name: boot\n'
               '      Rollback Index Location: 30\n'
               "    Prop: com.android.build.vendor.security_patch -> '2026-09-05'\n")
BOOT_INFO = ('Descriptors:\n    Hash descriptor:\n      Partition Name: boot\n'
             "    Prop: com.android.build.init_boot.os_version -> '17'\n"
             "    Prop: com.android.build.init_boot.security_patch -> '2026-10-05'\n")


class FactoryInputs(TemporaryInputs):
    def setUp(self):
        super().setUp()
        self.original = self.root / 'original'; self.original.mkdir()
        self.public = self.root / 'trust'; self.public.mkdir()
        self.source = self.root / 'source'
        generator = self.source / factory.GENERATOR
        generator.parent.mkdir(parents=True)
        generator.write_bytes(Path(flash_all.__file__).read_bytes())
        self.partitions = {name: ('signed fixture ' + name).encode()
                           for name in ('boot', 'recovery', 'vbmeta', 'system', 'vendor')}
        self.passthrough = {name: ('immutable fixture ' + name).encode() for name in
                            ('abl.elf', 'modemst1.img', 'modemst2.img', 'frp.img', 'misc.img', 'super_empty.img')}
        for name, data in self.passthrough.items(): (self.original / name).write_bytes(data)
        self.images = {name + '.img': data for name, data in self.partitions.items()}
        self.images.update(self.passthrough, **{'super.img': b'fixture signed super'})
        self.signed = self.root / 'signed.zip'
        write_zip(self.signed, {'IMAGES/' + name + '.img': data for name, data in self.partitions.items()})
        self.manifest = self.original / 'resolved-manifest.xml'; self.manifest.write_bytes(b'<manifest/>\n')
        self.key = b'independently exported binary public key fixture'
        (self.public / 'avb.pub').write_bytes(b'independent public PEM fixture')
        self.keys = SimpleNamespace(avb=lambda: self.public / 'avb.pub')
        provenance = {'product': 'FP6', 'build_id': 'fixture-user', 'build_number': 'fixture.1',
                      'build_datetime': 1700000000, 'build_identity': 'b' * 64,
                      'record_sha256': 'c' * 64, 'verification_sha256': 'd' * 64,
                      'target_files_sha256': 'e' * 64, 'manifest_sha256': si.sha256_file(self.manifest),
                      'projects': {'tools/diamaneos': 'a' * 40},
                      'source_files': {factory.GENERATOR: si.sha256_file(generator)}}
        self.plan = {'schema_version': 2, 'mode': 'production', 'provenance': provenance,
                     'partitions': list(self.partitions), 'logical_partitions': ['system', 'vendor'],
                     'super_layout': {'kind': 'slot-suffixed', 'active_slot': 'a'},
                     'passthrough_images': {n: hashlib.sha256(b).hexdigest() for n, b in self.passthrough.items()},
                     'chains': {}, 'packages': {}, 'roles': {}, 'metadata': {}, 'mac_permissions': {}}
        payload = lambda name: {'file': name, 'sha256': self.plan['passthrough_images'][name]}
        record = {'schema_version': 1, **{n: provenance[n] for n in
                   ('product', 'build_id', 'build_number', 'build_datetime', 'build_identity')},
                  'variant': 'user', 'official': True,
                  'device': {'name': 'Fairphone 6', 'maker': 'Fairphone', 'bootloader_keys': 'Power and Volume down'},
                  'manifest': {'file': self.manifest.name, 'resolved_sha256': si.sha256_file(self.manifest)},
                  'flash': {'slot': 'a', 'bootloader': ['boot', 'recovery', 'vbmeta'], 'logical': ['system', 'vendor']},
                  'firmware': {'release': 'fixture-firmware', 'validated': True,
                               'images': {'abl.elf': self.plan['passthrough_images']['abl.elf']},
                               'steps': [{'partition': 'abl_a', 'image': 'abl.elf'},
                                         {'partition': 'abl_b', 'image': 'abl.elf'}],
                               'reset': {'modemst1': payload('modemst1.img'), 'modemst2': payload('modemst2.img')}},
                  'wipe': {'validated': False, 'images': {'frp': payload('frp.img'), 'misc': payload('misc.img')}}}
        self.record_path = self.original / 'build.json'; self.record_path.write_bytes(factory.encoded(record))
        provenance['record_sha256'] = si.sha256_file(self.record_path)
        self.plan['factory_input'] = factory.input_record(record, self.plan)
        self.plan['plan_sha256'] = si.canonical_sha256(self.plan)
        self.images_zip = self.root / 'images.zip'; write_zip(self.images_zip, self.images)
        self.factory_zip = self.root / 'factory.zip'
        self.tools = SimpleNamespace(scratch=self.root, source=self.source,
                                     run=Mock(side_effect=self.native_run),
                                     avb_binary_authority=Mock(side_effect=self.native_authority))

    def native_run(self, command, arguments):
        if command == 'avbtool' and arguments[0] == 'info_image':
            return VBMETA_INFO if Path(arguments[2]).name == 'vbmeta.img' else BOOT_INFO
        if command == 'avbtool' and arguments[0] == 'calculate_vbmeta_digest':
            Path(arguments[-1]).write_text('ab' * 32 + '\n')
        elif command == 'avbtool':
            self.assertEqual(['extract_public_key', '--key', self.keys.avb(), '--output'], arguments[:-1])
            Path(arguments[-1]).write_bytes(self.key)
        elif command == 'build_super_image':
            self.assertEqual(self.signed, arguments[0]); Path(arguments[1]).write_bytes(self.images['super.img'])
        elif command == 'lpunpack':
            self.assertEqual(self.images['super.img'], Path(arguments[0]).read_bytes())
            for name in self.plan['logical_partitions']:
                (Path(arguments[1]) / (name + '_a.img')).write_bytes(self.partitions[name])
                (Path(arguments[1]) / (name + '_b.img')).write_bytes(b'')
        else:
            self.fail('Unexpected native fixture operation')

    def native_authority(self, image, expected):
        if Path(image).read_bytes() != self.partitions['vbmeta'] or Path(expected).read_bytes() != self.key:
            raise si.SigningError('Controlled embedded AVB authority mismatch')

    def make(self):
        return factory.write_factory(self.factory_zip, self.images_zip, self.signed, self.manifest,
                                     self.plan, self.tools, self.keys)

    def verify(self):
        return factory.verify_factory(self.factory_zip, self.images_zip, self.signed,
                                      self.plan, self.tools, self.keys)

    def change(self, change, repair_sums=False):
        with zipfile.ZipFile(self.factory_zip) as handle:
            entries = [(i, handle.read(i)) for i in handle.infolist()]
        entries = change(entries)
        if repair_sums:
            top = factory.directory_name(self.plan)
            digests = {i.filename.split('/', 1)[1]: hashlib.sha256(b).hexdigest()
                       for i, b in entries if i.filename != top + '/SHA256SUMS'}
            entries = [(i, factory.sums(digests) if i.filename == top + '/SHA256SUMS' else b) for i, b in entries]
        write_zip(self.factory_zip, entries)

    def change_bytes(self, name, data, repair_sums=True):
        top = factory.directory_name(self.plan)
        self.change(lambda rows: [(i, data if i.filename == top + '/' + name else b) for i, b in rows], repair_sums)


class FactoryZipTests(FactoryInputs, unittest.TestCase):
    def test_generated_zip_is_complete_and_has_no_hash_cycle(self):
        before = si.sha256_file(self.record_path)
        record = self.make(); self.verify()
        self.assertEqual(before, si.sha256_file(self.record_path))
        self.assertEqual(self.plan['plan_sha256'], record['signing_input']['plan_sha256'])
        self.assertEqual(before, record['signing_input']['build_record_sha256'])
        self.assertEqual(si.sha256_file(self.signed), record['target_files']['sha256'])
        self.assertEqual(False, record['never_lock'])
        self.assertNotIn('result', record)
        self.assertNotIn(si.sha256_file(self.factory_zip), json.dumps(record))
        top = factory.directory_name(self.plan)
        with zipfile.ZipFile(self.factory_zip) as z:
            wanted = set(self.images) | layout.FACTORY_METADATA
            self.assertEqual({top + '/' + n for n in wanted}, set(z.namelist()))
            expected = {n: hashlib.sha256(z.read(top + '/' + n)).hexdigest() for n in wanted - {'SHA256SUMS'}}
            self.assertEqual(factory.sums(expected), z.read(top + '/SHA256SUMS'))
            self.assertEqual(self.key, z.read(top + '/avb_pkmd.bin'))
            self.assertEqual(0o100755, z.getinfo(top + '/flash-all.sh').external_attr >> 16)
        self.tools.avb_binary_authority.assert_called()

    def test_production_scripts_stop_before_first_boot_and_check_all_files(self):
        record = self.make()
        commands = flash_all.commands(record)
        self.assertEqual([['flash', 'avb_custom_key', 'avb_pkmd.bin'], ['--set-active=a'], ['reboot-bootloader']], commands[-3:])
        self.assertLess(commands.index(['flash', 'frp', 'frp.img']), commands.index(commands[-3]))
        self.assertNotIn(['reboot'], commands)
        self.assertNotIn(['flashing', 'lock'], commands)
        scripts = flash_all.scripts(record)
        self.assertIn('sha256sum --quiet -c SHA256SUMS', scripts['flash-all.sh'])
        self.assertIn('Get-FileHash -Algorithm SHA256', scripts['flash-all.bat'])
        self.assertIn('get_unlock_ability (it must report 1)', scripts['flash-all.sh'])
        self.assertIn('Never lock the critical partitions', scripts['flash-all.bat'])
        broken = copy.deepcopy(record); broken.pop('avb_custom_key')
        with self.assertRaises(ValueError): flash_all.scripts(broken)

    def test_qualification_zip_cannot_enable_relock(self):
        self.plan['mode'] = 'qualification'
        self.plan['plan_sha256'] = si.canonical_sha256({k: v for k, v in self.plan.items() if k != 'plan_sha256'})
        record = self.make(); self.verify()
        self.assertTrue(record['never_lock']); self.assertFalse(record['release'])
        self.assertNotIn(['flash', 'avb_custom_key', 'avb_pkmd.bin'], flash_all.commands(record))

    def test_json_object_order_cannot_change_the_installer_sequence(self):
        self.make(); original = self.factory_zip.read_bytes(); self.factory_zip.unlink()
        for group in [self.plan['factory_input']['wipe'], self.plan['factory_input']['firmware']]:
            key = 'images' if group is self.plan['factory_input']['wipe'] else 'reset'
            group[key] = dict(reversed(list(group[key].items())))
        self.make(); self.verify()
        self.assertEqual(original, self.factory_zip.read_bytes())

    def test_every_missing_member_and_unknown_file_is_rejected(self):
        for name in set(self.images) | layout.FACTORY_METADATA:
            with self.subTest(missing=name):
                self.make()
                self.change(lambda rows: [(i, b) for i, b in rows if not i.filename.endswith('/' + name)])
                with self.assertRaises(si.SigningError): self.verify()
                self.factory_zip.unlink()
        self.make()
        self.change(lambda rows: rows + [(zipfile.ZipInfo(factory.directory_name(self.plan) + '/extra'), b'unknown')])
        with self.assertRaises(si.SigningError): self.verify()

    def test_repaired_checksums_cannot_authorize_changed_script_key_record_or_manifest(self):
        for name, data in [('flash-all.sh', b'#!/bin/sh\nexit 0\n'), ('flash-all.bat', b'@echo off\r\n'),
                           ('avb_pkmd.bin', b'another public key'), ('resolved-manifest.xml', b'<changed/>'),
                           ('build.json', b'{"release":true,"never_lock":false}')]:
            with self.subTest(file=name):
                self.make(); self.change_bytes(name, data)
                with self.assertRaises(si.SigningError): self.verify()
                self.factory_zip.unlink()

    def test_image_tampering_is_not_hidden_by_matching_sums(self):
        for name in self.images:
            with self.subTest(image=name):
                self.make(); self.change_bytes(name, b'changed image')
                with self.assertRaises(si.SigningError): self.verify()
                self.factory_zip.unlink()

    def test_exact_checksum_membership_and_order_are_required(self):
        self.make()
        self.change_bytes('SHA256SUMS', b'', repair_sums=False)
        with self.assertRaisesRegex(si.SigningError, 'checksums'): self.verify()

    def test_duplicate_link_special_directory_and_escaping_members_are_rejected(self):
        for name, kind in [('duplicate', None), ('folder/', stat.S_IFDIR),
                           ('../escape', None), ('/absolute', None), ('nested/extra', None),
                           ('back\\slash', None), ('link', stat.S_IFLNK), ('fifo', stat.S_IFIFO)]:
            with self.subTest(member=name):
                self.make()
                def change(rows):
                    if name == 'duplicate': return rows + [rows[0]]
                    info = zipfile.ZipInfo(name if name[0] in './' else factory.directory_name(self.plan) + '/' + name)
                    info.create_system = 3
                    if kind: info.external_attr = (kind | 0o644) << 16
                    return rows + [(info, b'fixture')]
                self.change(change)
                with self.assertRaises(si.SigningError): self.verify()
                self.factory_zip.unlink()

    def test_incomplete_image_archive_cannot_generate_a_factory(self):
        for name in self.images:
            with self.subTest(missing=name):
                write_zip(self.images_zip, {n: b for n, b in self.images.items() if n != name})
                with self.assertRaisesRegex(si.SigningError, 'complete flat image set'): self.make()
                self.assertFalse(self.factory_zip.exists())

    def test_generator_drift_and_unbound_generator_refuse(self):
        for change in ('working-source', 'plan'):
            with self.subTest(change=change):
                if change == 'working-source': (self.source / factory.GENERATOR).write_bytes(b'changed generator')
                else:
                    (self.source / factory.GENERATOR).write_bytes(Path(flash_all.__file__).read_bytes())
                    self.plan['provenance']['source_files'].pop(factory.GENERATOR)
                with self.assertRaisesRegex(si.SigningError, 'script generator'): self.make()
                self.assertFalse(self.factory_zip.exists())

    def test_input_layout_cannot_inject_shell_tokens_or_omit_signed_partitions(self):
        changes = [lambda v: v['flash'].update(slot='a;false'),
                   lambda v: v['flash']['bootloader'].remove('vbmeta'),
                   lambda v: v['flash']['logical'].pop(),
                   lambda v: v.update(build_id='name$(false)'),
                   lambda v: v['firmware']['steps'][0].update(partition='abl_a;false'),
                   lambda v: v['device'].update(maker="Fairphone'; false; '"),
                   lambda v: v.pop('device')]
        for change in changes:
            value = copy.deepcopy(self.plan['factory_input']); change(value)
            with self.assertRaises(si.SigningError): factory.validate_input(value, self.plan)


class FactoryAssemblyTests(FactoryInputs, unittest.TestCase):
    def assemble(self, images=None, output=None):
        return factory.assemble(images or self.root / 'publish/images.zip', output or self.root / 'publish/factory.zip',
                                self.signed, self.manifest, self.original, self.plan, self.tools, self.keys,
                                api.publication_images)

    def test_assembly_rebuilds_super_checks_images_and_preserves_inputs(self):
        before = {p: si.sha256_file(p) for p in self.original.iterdir()}
        with patch.object(api, 'verify_images', return_value=list(self.partitions)) as avb, \
                patch.object(api, 'verify_installed_packages') as packages:
            self.assemble()
        avb.assert_called_once(); packages.assert_called_once()
        self.assertEqual(before, {p: si.sha256_file(p) for p in self.original.iterdir()})
        self.assertTrue((self.root / 'publish/factory.zip').is_file())
        self.assertFalse(list((self.root / 'publish').glob('.factory-*')))

    def test_native_image_refusal_never_exposes_outputs(self):
        with patch.object(api, 'verify_images', side_effect=si.SigningError('controlled signature refusal')):
            with self.assertRaisesRegex(si.SigningError, 'signature refusal'): self.assemble()
        self.assertEqual([], list((self.root / 'publish').iterdir()))

    def test_changed_passthrough_and_changed_signed_image_refuse(self):
        (self.original / 'abl.elf').write_bytes(b'changed firmware')
        with self.assertRaisesRegex(si.SigningError, 'passthrough'): self.assemble()
        self.assertEqual([], list((self.root / 'publish').iterdir()))

    def test_output_reuse_and_symlink_do_not_overwrite_anything(self):
        out = self.root / 'publish'; out.mkdir()
        images, output = out / 'images.zip', out / 'factory.zip'
        images.write_bytes(b'preserve existing output')
        with self.assertRaises(si.SigningError): self.assemble(images, output)
        self.assertEqual(b'preserve existing output', images.read_bytes())
        images.unlink(); images.symlink_to(self.signed)
        with self.assertRaises(si.SigningError): self.assemble(images, output)

    def test_second_output_race_removes_only_our_first_link(self):
        left, right = self.root / 'left', self.root / 'right'
        left.write_bytes(b'left'); right.write_bytes(b'right')
        first, second = self.root / 'first', self.root / 'second'
        second.write_bytes(b'concurrent output')
        with self.assertRaises(FileExistsError): factory.publish_outputs(((left, first), (right, second)))
        self.assertFalse(first.exists()); self.assertEqual(b'concurrent output', second.read_bytes())

    def test_publication_version_is_explicit_and_production_refuses_legacy(self):
        from .fixtures import publication_description
        value = publication_description()
        self.assertEqual(1, layout.publication_schema(value, 'qualification'))
        with self.assertRaisesRegex(si.SigningError, 'Production requires'): layout.publication_schema(value, 'production')
        value['schema_version'] = 2
        self.assertEqual(2, layout.publication_schema(value, 'production'))
        value['extra'] = 'arbitrary'
        with self.assertRaises(si.SigningError): layout.publication_schema(value, 'production')


class ProductionPublicationTests(FactoryInputs, unittest.TestCase):
    """Exercise the production caller through ZIP checks, with native crypto isolated."""
    def setUp(self):
        super().setUp()
        from .fixtures import publication_description, release_record
        self.publication = self.root / 'publication'; self.publication.mkdir()
        self.description = publication_description()
        self.description.update(schema_version=2, factory_archive='factory.zip', factory_signature='factory.zip.sig')
        self.tools.java_home = self.root / 'jdk'; (self.tools.java_home / 'bin').mkdir(parents=True)
        (self.tools.java_home / 'bin/java').write_bytes(b'controlled JDK fixture')
        self.plan['provenance']['java_sha256'] = si.sha256_file(self.tools.java_home / 'bin/java')
        self.keys.root = self.public
        (self.public / 'allowed').write_bytes(b'independent factory authority fixture')
        self.keys.inventory = {'factory': {'allowed_signers': 'allowed', 'identity': 'fixture'}}
        self.keys.files = {'allowed': si.sha256_file(self.public / 'allowed')}
        self.keys.file = lambda name: self.public / name
        self.keys.cert = lambda *_args: self.public / 'allowed'
        self.plan.update(policy_sha256=si.canonical_sha256(si.load_json(si.DEFAULT_CONFIG)),
                         key_inventory_sha256=si.canonical_sha256(self.keys.inventory),
                         public_material_sha256=si.canonical_sha256(self.keys.files), public_identities={},
                         changed_projects=[], previous_artifacts={})
        self.plan['packages'] = {'SYSTEM/app/Fixture.apk': {'kind': 'apk'}}
        self.plan['plan_sha256'] = si.canonical_sha256({k: v for k, v in self.plan.items() if k != 'plan_sha256'})
        self.make()
        for source, name in [(self.signed, 'target-files.zip'), (self.images_zip, 'images.zip'),
                             (self.factory_zip, 'factory.zip')]:
            (self.publication / name).write_bytes(source.read_bytes())
        for name in ['factory.zip.sig', 'release.json.sig']:
            (self.publication / name).write_bytes(b'controlled signature fixture')
        write_zip(self.publication / 'ota.zip', {'payload.bin': b'controlled full payload',
                                                'META-INF/com/android/metadata': 'ota-type=AB\n'})
        self.tools.ssh_signature = Mock()
        self.tools.ota = Mock()
        self.tools.apply_payload = Mock()
        self.policy = si.load_json(si.DEFAULT_CONFIG)
        release_record(self.publication, self.plan, self.description)

    def publication_verify(self):
        with patch.object(api, 'source_files', return_value=self.plan['provenance']['source_files']), \
                patch.object(api, 'verify_packages', return_value={'apk': 1, 'apex': 0, 'payload': 0}), \
                patch.object(api, 'verify_images', return_value=list(self.partitions)), \
                patch.object(api, 'verify_installed_packages'), \
                patch.object(api.signing_mac, 'verify_mac_permissions', return_value={}):
            return api.verify_publication(self.plan, self.description, self.publication,
                                          self.policy, self.tools, self.keys)

    def test_production_dispatch_checks_complete_zip_and_returns_its_final_hash(self):
        result = self.publication_verify()
        self.assertTrue(result['artifact_signatures_verified'])
        self.assertEqual(si.sha256_file(self.publication / 'factory.zip'), result['artifacts']['factory.zip'])
        self.assertEqual({'device': 'FP6', 'verifiedBootKey': hashlib.sha256(self.key).hexdigest().upper(),
                          'release': {'build': 'fixture.1', 'verifiedBootHash': 'AB' * 32, 'osVersion': 170000,
                                      'osPatchLevel': 202610, 'vendorPatchLevel': 20260905, 'bootPatchLevel': 20261005,
                                      'attestationVersion': 300, 'keyMintVersion': 300}},
                         result['attestation_release'])
        self.assertEqual(2, self.tools.ssh_signature.call_count)
        self.tools.ota.assert_called_once(); self.tools.apply_payload.assert_called_once()

    def test_authenticated_outer_hash_does_not_authorize_an_altered_factory_script(self):
        from .fixtures import release_record
        self.change_bytes('flash-all.sh', b'#!/bin/sh\nexit 0\n')
        (self.publication / 'factory.zip').write_bytes(self.factory_zip.read_bytes())
        release_record(self.publication, self.plan, self.description)
        with self.assertRaisesRegex(si.SigningError, 'metadata differs'): self.publication_verify()
        self.tools.ota.assert_not_called(); self.tools.apply_payload.assert_not_called()

    def test_unknown_directory_or_special_file_refuses_before_native_verification(self):
        for kind in ('directory', 'fifo', 'link'):
            with self.subTest(kind=kind):
                extra = self.publication / 'unknown'
                if kind == 'directory': extra.mkdir()
                elif kind == 'fifo': os.mkfifo(extra)
                else: extra.symlink_to(self.signed)
                with self.assertRaisesRegex(si.SigningError, 'Publication contains'): self.publication_verify()
                self.tools.ssh_signature.assert_not_called()
                if kind == 'directory': extra.rmdir()
                else: extra.unlink()

    def test_late_unknown_file_refuses_after_native_checks(self):
        self.tools.apply_payload.side_effect = lambda *_: (self.publication / 'late-extra').write_bytes(b'late')
        with self.assertRaisesRegex(si.SigningError, 'Publication contains'): self.publication_verify()


class FactoryBootstrapTests(FactoryInputs, unittest.TestCase):
    def arguments(self):
        policy = si.load_json(si.DEFAULT_CONFIG)
        self.plan['policy_sha256'] = si.canonical_sha256(policy)
        self.plan['plan_sha256'] = si.canonical_sha256({k: v for k, v in self.plan.items() if k != 'plan_sha256'})
        plan = self.root / 'plan.json'; plan.write_bytes(factory.encoded(self.plan))
        inventory = {'schema_version': 2, 'certificate_roles': {r: r + '.x509.pem' for r in si.CERT_ROLES},
                     'avb_public_key': 'avb.pub', 'factory': {'allowed_signers': 'allowed', 'identity': 'fixture'}}
        (self.public / 'inventory.json').write_bytes(factory.encoded(inventory))
        for name in inventory['certificate_roles'].values(): (self.public / name).write_bytes(b'public certificate fixture')
        (self.public / 'allowed').write_text('fixture ssh-ed25519 AAAA\n')
        for name in ['verify.json', 'verify.sig', 'SHA256SUMS', 'otatools.zip']:
            (self.original / name).write_bytes(b'untrusted incoming fixture')
        return ['factory', '--plan', str(plan), '--build-record', str(self.record_path),
                     '--verification-report', str(self.original / 'verify.json'), '--verification-signature', str(self.original / 'verify.sig'),
                     '--target-files', str(self.signed), '--resolved-manifest', str(self.manifest),
                     '--signed-target-files', str(self.signed), '--source-root', str(self.source),
                     '--otatools', str(self.original / 'otatools.zip'), '--keys', str(self.public / 'inventory.json'),
                     '--scratch', str(self.root / 'scratch'), '--images-archive', str(self.root / 'publish/images.zip'),
                     '--output', str(self.root / 'publish/factory.zip')]

    def test_report_authentication_precedes_unpacking_or_factory_generation(self):
        arguments = self.arguments()
        with patch.object(api, 'host_ssh_signature', side_effect=si.SigningError('controlled report signature refusal')) as auth, \
                patch.object(api, 'native_inputs') as native, patch.object(factory, 'assemble') as assemble, \
                patch('sys.stderr', new=io.StringIO()) as error:
            self.assertEqual(2, api.main(arguments))
        self.assertIn('report signature refusal', error.getvalue())
        auth.assert_called_once(); native.assert_not_called(); assemble.assert_not_called()
        self.assertFalse((self.root / 'publish').exists())

    def test_publication_controlled_authority_is_rejected_before_snapshot_relocation(self):
        arguments = self.arguments()
        controlled = self.root / 'publish/trust'
        shutil.copytree(self.public, controlled)
        arguments[arguments.index('--keys') + 1] = str(controlled / 'inventory.json')
        with patch.object(api, 'host_ssh_signature') as auth, patch.object(api, 'native_inputs') as native, \
                patch('sys.stderr', new=io.StringIO()) as error:
            self.assertEqual(2, api.main(arguments))
        self.assertIn('outside the artifacts', error.getvalue())
        auth.assert_not_called(); native.assert_not_called()

    def test_cli_closing_checks_precede_exposing_completed_outputs(self):
        arguments = self.arguments()
        inventory = si.load_json(self.public / 'inventory.json')
        self.keys.inventory = inventory
        self.keys.files = {'allowed': si.sha256_file(self.public / 'allowed')}
        self.tools.java_home = self.root / 'jdk'; (self.tools.java_home / 'bin').mkdir(parents=True)
        (self.tools.java_home / 'bin/java').write_bytes(b'JDK fixture')
        self.plan['provenance'].update(java_sha256=si.sha256_file(self.tools.java_home / 'bin/java'),
                                       otatools_sha256=si.sha256_file(self.original / 'otatools.zip'))
        self.plan.update(key_inventory_sha256=si.canonical_sha256(inventory),
                         public_material_sha256=si.canonical_sha256(self.keys.files))
        self.plan['plan_sha256'] = si.canonical_sha256({k: v for k, v in self.plan.items() if k != 'plan_sha256'})
        (self.root / 'plan.json').write_bytes(factory.encoded(self.plan))
        @contextmanager
        def admitted(*_args, **_kwargs):
            yield self.tools, self.keys
        def staged(images, output, *_args):
            images.write_bytes(b'controlled complete images')
            output.write_bytes(b'controlled complete factory')
            # Original metadata, rather than its private copy, changes after native work.
            self.record_path.write_bytes(b'changed original after assembly')
        with patch.object(api, 'host_ssh_signature'), patch.object(api, 'native_inputs', side_effect=admitted), \
                patch.object(api, 'verified_build', return_value=self.plan['provenance']), \
                patch.object(api, 'source_files', return_value=self.plan['provenance']['source_files']), \
                patch.object(api, 'verify_packages'), patch.object(api.signing_mac, 'verify_mac_permissions'), \
                patch.object(factory, 'assemble', side_effect=staged) as assembled, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        assembled.assert_called_once()
        self.assertEqual([], list((self.root / 'publish').iterdir()))


if __name__ == '__main__':
    unittest.main()
