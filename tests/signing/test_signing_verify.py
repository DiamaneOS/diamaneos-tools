"""Build bindings, archive boundaries and native verification orchestration."""
import copy
import base64
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from diamaneos_tools import image_verify, signing_archive as za, signing_inputs as inputs
from diamaneos_tools import build_workspace as bw
from diamaneos_tools import signing_native as native, signing_verify as api
from .fixtures import TemporaryInputs, write_zip


class SigningInputsTests(TemporaryInputs, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)

    def fixture(self):
        root = self.root
        self.source = root / 'source'
        rules = self.source / 'tools/diamaneos/config/fp6-image-checks.json'
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_bytes((inputs.ROOT / 'config/fp6-image-checks.json').read_bytes())
        manifest = '<manifest><remote name="up" fetch="https://example.invalid"/><default remote="up"/>'
        for path in sorted(inputs.PROJECTS | {'prebuilts/jdk/jdk25'}):
            manifest += '<project name="' + path + '" path="' + path + '" revision="' + 'a' * 40 + '"/>'
        (root / 'resolved-manifest.xml').write_text(manifest + '</manifest>')
        (root / 'target-files.zip').write_bytes(b'verified unsigned input')
        (root / 'otatools.zip').write_bytes(b'verified native tools')
        record = {'build_id': 'fixture', 'build_identity': 'b' * 64, 'product': 'FP6', 'build_number': 'fixture',
                  'build_datetime': 1234, 'variant': 'user', 'official': True, 'modified': [], 'tools': {'clean': True},
                  'target_files': {'file': 'target-files.zip', 'sha256': inputs.sha256_file(root / 'target-files.zip')},
                  'otatools': {'file': 'otatools.zip', 'sha256': inputs.sha256_file(root / 'otatools.zip')},
                  'manifest': {'file': 'resolved-manifest.xml', 'resolved_sha256': inputs.sha256_file(root / 'resolved-manifest.xml')}}
        (root / 'build.json').write_text(json.dumps(record))
        self.refresh_report()
        return record

    def refresh_report(self):
        record = json.loads((self.root / 'build.json').read_text())
        files = ['build.json', 'resolved-manifest.xml', 'target-files.zip', 'otatools.zip']
        (self.root / 'SHA256SUMS').write_text(''.join(inputs.sha256_file(self.root / name) + '  ' + name + '\n' for name in files))
        rules = inputs.load_json(inputs.ROOT / 'config/fp6-image-checks.json')
        names = list(image_verify.REQUIRED_CHECKS) + [rule['id'] for rule in image_verify.validate_rules(rules)
                 if image_verify.rule_applies(rule, record['variant'], record['official'])]
        checks = [{'id': name, 'status': 'PASS'} for name in names]
        report = {'schema_version': 1, 'build_id': 'fixture', 'build_identity': record['build_identity'],
                  'sums_sha256': inputs.sha256_file(self.root / 'SHA256SUMS'), 'checks': checks,
                  'rule_set_sha256': bw.digest(rules),
                  'status': 'PASS', 'checked': len(checks), 'failed': 0}
        (self.root / 'verify.json').write_text(json.dumps(report))

    def verify(self, production=True):
        root = self.root
        with patch.object(inputs, 'git_read', side_effect=lambda _root, args:
                          ('a' * 40).encode() if args[0] == 'rev-parse' else b''):
            return inputs.verified_build(root / 'build.json', root / 'verify.json', root / 'target-files.zip',
                                         root / 'resolved-manifest.xml', production=production,
                                         source_root=self.source, otatools=root / 'otatools.zip')

    def test_committed_policy_has_no_release_tag_or_commit(self):
        inputs.validate_config(self.policy)
        for pin in ('2026100600', 'a' * 40, 'a' * 64):
            changed = copy.deepcopy(self.policy)
            changed['source_binding'] = pin
            with self.assertRaises(inputs.SigningError):
                inputs.validate_config(changed)
        self.assertNotIn('source_binding', self.policy)

    def test_unknown_roles_and_algorithm_drift_refuse(self):
        for change in (lambda p: p['certificate_roles'].append('other'),
                       lambda p: p.update(avb_algorithm='NONE'),
                       lambda p: p['apk_selectors'].update(other='other')):
            value = copy.deepcopy(self.policy); change(value)
            with self.assertRaises(inputs.SigningError): inputs.validate_config(value)

    def test_build_verify_pass_binds_target_tools_manifest_and_record(self):
        self.fixture()
        self.assertEqual('fixture', self.verify()['build_id'])
        for name in ('target-files.zip', 'otatools.zip', 'resolved-manifest.xml', 'build.json'):
            with self.subTest(name=name):
                path = self.root / name; old = path.read_bytes(); path.write_bytes(old + b' ')
                with self.assertRaises(inputs.SigningError): self.verify()
                path.write_bytes(old)

    def test_failed_partial_duplicate_and_other_build_reports_refuse(self):
        self.fixture()
        report_path = self.root / 'verify.json'
        original = json.loads(report_path.read_text())
        changes = [lambda r: r.update(status='FAIL'), lambda r: r['checks'].pop(),
                   lambda r: r['checks'].append(r['checks'][0]), lambda r: r.update(build_id='other'),
                   lambda r: r.update(build_identity='c' * 64), lambda r: r['checks'][0].update(status='FAIL')]
        for change in changes:
            value = copy.deepcopy(original); change(value); report_path.write_text(json.dumps(value))
            with self.assertRaises(inputs.SigningError): self.verify()

    def test_dirty_or_nonrelease_build_does_not_get_production_signing(self):
        record = self.fixture()
        for change in (lambda r: r.update(variant='userdebug'), lambda r: r.update(official=False),
                       lambda r: r.update(modified=['script']), lambda r: r['tools'].update(clean=False)):
            value = copy.deepcopy(record); change(value)
            (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
            with self.assertRaises(inputs.SigningError): self.verify()
        value = dict(record, variant='userdebug', official=False)
        (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
        self.assertEqual('fixture', self.verify(False)['build_id'])

    def test_changed_project_report_is_complete_and_ignores_unchanged_revisions(self):
        current = {'script': 'a' * 40, 'external/avb': 'b' * 40}
        previous = {'status': 'PASS', 'artifact_signatures_verified': True,
                    'mode': 'production', 'artifacts': {},
                    'provenance': {'projects': {'script': 'c' * 40, 'external/avb': 'b' * 40, 'old': 'd' * 40}}}
        self.assertEqual(['old', 'script'], [r['project'] for r in inputs.changed_projects(current, previous)])
        self.assertEqual(2, len(inputs.changed_projects(current)))
        previous['artifact_signatures_verified'] = False
        with self.assertRaises(inputs.SigningError): inputs.changed_projects(current, previous)

    def test_production_never_uses_qualification_delta_or_source_baselines(self):
        previous = {'status': 'PASS', 'artifact_signatures_verified': True, 'mode': 'qualification',
                    'provenance': {'projects': {'script': 'a' * 40}}, 'artifacts': {'previous.zip': 'b' * 64}}
        with self.assertRaises(inputs.SigningError): inputs.changed_projects({'script': 'c' * 40}, previous, production=True)
        self.assertEqual(1, len(inputs.changed_projects({'script': 'c' * 40}, previous)))
        for invalid in ([], None, {'mode': 'production'}):
            if invalid is not None:
                with self.assertRaises(inputs.SigningError): inputs.changed_projects({}, invalid, production=True)

    def test_json_rejects_duplicate_keys_nonfinite_and_secret_material(self):
        path = self.root / 'input.json'
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"password":"x"}', '{"x":"-----BEGIN PRIVATE KEY-----"}'):
            path.write_text(text)
            with self.assertRaises(inputs.SigningError): inputs.load_json(path)
        path.write_bytes(b'x' * 100)
        with self.assertRaises(inputs.SigningError): inputs.load_json(path, 20)

    def test_changed_rules_or_missing_device_checks_refuse(self):
        self.fixture()
        path = self.root / 'verify.json'
        original = inputs.load_json(path)
        changed = copy.deepcopy(original); changed['rule_set_sha256'] = '0' * 64
        path.write_text(json.dumps(changed))
        with self.assertRaises(inputs.SigningError): self.verify()
        changed = copy.deepcopy(original)
        changed['checks'] = [c for c in changed['checks'] if c['id'] in image_verify.REQUIRED_CHECKS]
        changed['checked'] = len(changed['checks']); path.write_text(json.dumps(changed))
        with self.assertRaises(inputs.SigningError): self.verify()

    def test_malformed_document_roots_have_bounded_refusals(self):
        self.fixture()
        for name in ('build.json', 'verify.json'):
            path = self.root / name; old = path.read_bytes()
            for value in ([], None, 0, 'record'):
                with self.subTest(name=name, value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(inputs.SigningError): self.verify()
            path.write_bytes(old)
        with self.assertRaises(inputs.SigningError): api.validate_plan([], self.policy)
        with self.assertRaises(inputs.SigningError): inputs.changed_projects({}, [], production=True)

    def test_closing_hash_detects_changed_run_input(self):
        path = self.root / 'run-input'; path.write_bytes(b'accepted')
        hashes = inputs.input_hashes([path])
        inputs.recheck_hashes(hashes)
        path.write_bytes(b'changed')
        with self.assertRaises(inputs.SigningError): inputs.recheck_hashes(hashes)

    def test_prepare_requires_a_verification_signature(self):
        root = api.parser()
        arguments = ['prepare', '--build-record', 'build.json', '--verification-report', 'verify.json',
                     '--target-files', 'target.zip', '--resolved-manifest', 'manifest.xml', '--source-root', 'source',
                     '--otatools', 'tools.zip', '--keys', 'inventory.json', '--scratch', 'scratch', '--output', 'plan.json']
        with patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit): root.parse_args(arguments)
        self.assertEqual(Path('report.sig'), root.parse_args(arguments + ['--verification-signature', 'report.sig']).verification_signature)

    def test_paths_refuse_traversal_links_missing_and_absolute_files(self):
        (self.root / 'file').write_text('data')
        (self.root / 'link').symlink_to('file')
        for name in ('../file', '/file', 'link', 'missing', './file', 'a\\b'):
            with self.assertRaises(inputs.SigningError): inputs.safe_file(self.root, name)
        self.assertEqual(self.root.resolve() / 'file', inputs.safe_file(self.root, 'file'))

    def test_output_cannot_overwrite_a_receipt(self):
        path = self.root / 'out.json'
        inputs.write_json(path, {'status': 'PASS'})
        with self.assertRaises(FileExistsError): inputs.write_json(path, {})

    def test_external_inventory_cannot_reference_publication_key_files(self):
        publication = self.root / 'publication'; publication.mkdir()
        (publication / 'key').write_bytes(b'controlled public material fixture')
        keys = native.PublicKeys.__new__(native.PublicKeys)
        keys.root, keys.files, keys.artifact_root = self.root.resolve(), {}, publication.resolve()
        with self.assertRaisesRegex(inputs.SigningError, 'inside the publication'):
            keys.file('publication/key')
        (self.root / 'external-key').write_bytes(b'controlled external public material fixture')
        self.assertEqual((self.root / 'external-key').resolve(), keys.file('external-key'))


class ArchiveTests(unittest.TestCase):
    def test_unsafe_duplicate_and_encrypted_members_are_refused(self):
        for names in (['../x'], ['/x'], ['a\\b'], ['x', 'x'], ['./x']):
            buffer = io.BytesIO()
            write_zip(buffer, [(name, b'x') for name in names])
            buffer.seek(0)
            with self.assertRaises(inputs.SigningError):
                with za.archive(buffer): pass

    def test_unselected_os_links_are_allowed_but_selected_payload_links_refuse(self):
        buffer = io.BytesIO()
        info = zipfile.ZipInfo('SYSTEM/app/test.apk'); info.external_attr = (stat.S_IFLNK | 0o777) << 16
        write_zip(buffer, [(info, '../elsewhere')])
        with tempfile.TemporaryDirectory() as root:
            buffer.seek(0)
            with za.archive(buffer) as handle:
                with self.assertRaises(inputs.SigningError): za.extract(handle, info.filename, Path(root) / 'apk')
            buffer.seek(0)
            with self.assertRaises(inputs.SigningError):
                with za.archive(buffer, allow_links=False): pass

    def test_metadata_rejects_duplicates_conflicts_and_unsafe_names(self):
        for text in ('name="a.apk" name="b.apk"', 'name="../x.apk"', 'name="x.apk"\nname="x.apk"'):
            with self.assertRaises(inputs.SigningError): za.attributes(text)
        with self.assertRaises(inputs.SigningError): za.properties('x=1\nx=2')
        self.assertEqual({'x': '1'}, za.properties('x=1\nx=1'))


class NativeOrchestrationTests(unittest.TestCase):
    def test_unnamed_payload_descriptor_never_becomes_an_ordinary_partition_or_chain(self):
        empty = 'Hashtree descriptor:\n  Partition Name:    \n  Hash Algorithm: sha256\n'
        self.assertEqual([('', None)], native.avb_descriptors(empty, allow_empty_payload=True))
        with self.assertRaises(inputs.SigningError): native.avb_descriptors(empty)
        for value in ('../outside', 'bad/name', 'bad name', '\\outside'):
            invalid = 'Hashtree descriptor:\n Partition Name: ' + value + '\n'
            with self.assertRaises(inputs.SigningError): native.avb_descriptors(invalid, allow_empty_payload=True)
        chain = 'Chain Partition descriptor:\n Partition Name:   \n Rollback Index Location: 1\n'
        with self.assertRaises(inputs.SigningError): native.avb_descriptors(chain, allow_empty_payload=True)
        with self.assertRaises(inputs.SigningError): native.avb_descriptors(empty + empty, allow_empty_payload=True)

    def test_installed_packages_must_match_verified_archive_and_cannot_be_extra(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'target.zip'
            logical = 'SYSTEM/app/Fixture/Fixture.apk'
            with zipfile.ZipFile(target, 'w') as archive: archive.writestr(logical, b'accepted')
            plan = {'packages': {logical: {}}, 'partitions': ['system'], 'mac_permissions': {}}
            tools = Mock(); tools.scratch = root
            def extract(image, destination):
                path = destination / 'app/Fixture/Fixture.apk'; path.parent.mkdir(parents=True); path.write_bytes(b'changed')
            tools.extract_filesystem.side_effect = extract
            with self.assertRaises(inputs.SigningError): api.verify_installed_packages(root, target, plan, tools)
            plan['partitions'] = []
            with self.assertRaises(inputs.SigningError): api.verify_installed_packages(root, target, plan, tools)

    def test_ota_uses_external_certificate_and_requires_matching_build_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'ota.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('payload.bin', b'payload')
                z.writestr('payload_properties.txt', 'FILE_HASH=' + base64.b64encode(hashlib.sha256(b'payload').digest()).decode() +
                           '\nFILE_SIZE=7\nMETADATA_HASH=' + base64.b64encode(hashlib.sha256(b'p').digest()).decode() + '\nMETADATA_SIZE=1')
                z.writestr('META-INF/com/android/metadata', 'ota-type=AB\npost-build-incremental=build\npost-timestamp=123\npre-device=FP6')
            tools = native.NativeTools.__new__(native.NativeTools); tools.run = Mock()
            provenance = {'build_number': 'build', 'build_datetime': 123, 'product': 'FP6'}
            tools.ota(path, Path('/external/release.x509.pem'), provenance)
            tools.run.assert_called_once_with('check_ota_package_signature', [Path('/external/release.x509.pem'), path])
            with self.assertRaises(inputs.SigningError): tools.ota(path, Path('/external/cert'), dict(provenance, build_number='other'))

    def test_verifier_process_environment_excludes_injection_and_agent_variables(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root); (p / 'prebuilts/jdk/jdk25/linux-x86/bin').mkdir(parents=True)
            (p / 'prebuilts/jdk/jdk25/linux-x86/bin/java').touch()
            tools = native.NativeTools(p, p, p)
            for name in ('PYTHONOPTIMIZE', 'PYTHONPATH', 'JAVA_TOOL_OPTIONS', 'BASH_ENV', 'LD_PRELOAD', 'SSH_AUTH_SOCK'):
                self.assertNotIn(name, tools.env)

    def test_avb_requires_algorithm_flags_and_complete_chain(self):
        tools = Mock()
        info = 'Algorithm: SHA256_RSA4096\nFlags: 0\nDescriptors:\n  Hash descriptor:\n    Partition Name: boot\n'
        tools.run.return_value = info; tools.scratch = Path('/scratch')
        keys = Mock(); keys.avb.return_value = Path('/external/avb.pub.pem')
        with patch.object(native, 'safe_file', side_effect=lambda root, name: Path(root) / name):
            self.assertEqual(['boot', 'vbmeta'], native.verify_images(tools, keys, Path('/images'), ['boot', 'vbmeta'], {}))
            with self.assertRaises(inputs.SigningError): native.verify_images(tools, keys, Path('/images'), ['boot', 'vbmeta', 'system'], {})
        real = native.NativeTools.__new__(native.NativeTools); real.run = Mock(return_value=info.replace('Flags: 0', 'Flags: 2'))
        real.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        with self.assertRaises(inputs.SigningError): real.avb(Path('/image'), Path('/external/key'))


if __name__ == '__main__':
    unittest.main()
