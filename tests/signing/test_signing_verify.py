"""Build bindings, archive boundaries and native verification orchestration."""
import copy
import base64
from contextlib import contextmanager
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

    def verify(self, mode='release'):
        root = self.root
        with patch.object(inputs, 'git_read', side_effect=lambda _root, args:
                          ('a' * 40).encode() if args[0] == 'rev-parse' else b''):
            return inputs.verified_build(root / 'build.json', root / 'verify.json', root / 'target-files.zip',
                                         root / 'resolved-manifest.xml', mode=mode,
                                         source_root=self.source, otatools=root / 'otatools.zip')

    def test_committed_policy_has_no_release_tag_or_commit(self):
        inputs.validate_config(self.policy)
        changes = [lambda p, pin=pin: p.update(source_binding=pin) for pin in ('2026100600', 'a' * 40, 'a' * 64)]
        # Unknown roles and selectors and another AVB algorithm refuse too.
        changes += [lambda p: p['certificate_roles'].append('other'),
                    lambda p: p.update(avb_algorithm='NONE'),
                    lambda p: p['apk_selectors'].update(other='other')]
        for index, change in enumerate(changes):
            value = copy.deepcopy(self.policy); change(value)
            with self.subTest(change=index), self.assertRaises(inputs.SigningError): inputs.validate_config(value)
        self.assertNotIn('source_binding', self.policy)

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

        def device_checks_missing(report):
            report['checks'] = [c for c in report['checks'] if c['id'] in image_verify.REQUIRED_CHECKS]
            report['checked'] = len(report['checks'])
        changes = [lambda r: r.update(status='FAIL'), lambda r: r['checks'].pop(),
                   lambda r: r['checks'].append(r['checks'][0]), lambda r: r.update(build_id='other'),
                   lambda r: r.update(build_identity='c' * 64), lambda r: r['checks'][0].update(status='FAIL'),
                   # Other rules than the ones the report names, and a report without the device checks.
                   lambda r: r.update(rule_set_sha256='0' * 64), device_checks_missing]
        for index, change in enumerate(changes):
            value = copy.deepcopy(original); change(value); report_path.write_text(json.dumps(value))
            with self.subTest(change=index), self.assertRaises(inputs.SigningError): self.verify()

    def test_dirty_or_nonrelease_build_does_not_get_release_signing(self):
        record = self.fixture()
        changed = (lambda r: r.update(modified=['packages/apps/Updater']),
                   lambda r: r.update(source=dict(r.get('source') or {}, deviations=['local manifests'])))
        for change in (lambda r: r.update(variant='userdebug'), lambda r: r.update(official=False),
                       lambda r: r['tools'].update(clean=False), lambda r: r.update(tools_skew='stale'), *changed):
            value = copy.deepcopy(record); change(value)
            (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
            with self.assertRaises(inputs.SigningError): self.verify()
        value = dict(record, variant='userdebug', official=False)
        (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
        bound = self.verify('production')
        self.assertEqual(([], []), (bound['modified'], bound['deviations']))
        # Own keys sign a source with local changes and keep them. Release and qualification do not.
        for change in changed:
            value = copy.deepcopy(record); change(value)
            (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
            bound = self.verify('production')
            self.assertEqual((value['modified'], (value.get('source') or {}).get('deviations', [])),
                             (bound['modified'], bound['deviations']))
            with self.assertRaises(inputs.SigningError): self.verify('qualification')
        value = dict(record, modified=['script'], tools=dict(record['tools'], clean=False))
        (self.root / 'build.json').write_text(json.dumps(value)); self.refresh_report()
        with self.assertRaisesRegex(inputs.SigningError, 'clean recorded build'): self.verify('production')

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
        with self.assertRaises(inputs.SigningError): inputs.changed_projects({'script': 'c' * 40}, previous, mode='production')
        self.assertEqual(1, len(inputs.changed_projects({'script': 'c' * 40}, previous)))
        for invalid in ([], None, {'mode': 'production'}):
            if invalid is not None:
                with self.assertRaises(inputs.SigningError): inputs.changed_projects({}, invalid, mode='production')

    def test_json_rejects_duplicate_keys_nonfinite_and_secret_material(self):
        path = self.root / 'input.json'
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"password":"x"}', '{"x":"-----BEGIN PRIVATE KEY-----"}'):
            path.write_text(text)
            with self.assertRaises(inputs.SigningError): inputs.load_json(path)
        path.write_bytes(b'x' * 100)
        with self.assertRaises(inputs.SigningError): inputs.load_json(path, 20)

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
        with self.assertRaises(inputs.SigningError): inputs.changed_projects({}, [], mode='production')

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

    def bootstrap_inputs(self):
        self.fixture()
        authority = self.root / 'authority'
        authority.mkdir()
        identity = authority / 'disposable'
        native.command(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', identity],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        public = ' '.join(identity.with_suffix('.pub').read_text().split()[:2])
        (authority / 'allowed').write_text('fixture ' + public + '\n')
        inventory = authority / 'inventory.json'
        inventory.write_text(json.dumps({'schema_version': 2,
            'certificate_roles': {role: role + '.x509.pem' for role in inputs.CERT_ROLES},
            'avb_public_key': 'avb.pem', 'factory': {'allowed_signers': 'allowed', 'identity': 'fixture'}}))
        for name in [*(role + '.x509.pem' for role in inputs.CERT_ROLES), 'avb.pem']:
            (authority / name).write_bytes(b'public material fixture')
        signature = self.root / 'verify.sig'
        signature.write_text('invalid signature')
        arguments = ['prepare', '--build-record', str(self.root / 'build.json'),
            '--verification-report', str(self.root / 'verify.json'), '--verification-signature', str(signature),
            '--target-files', str(self.root / 'target-files.zip'), '--resolved-manifest', str(self.root / 'resolved-manifest.xml'),
            '--source-root', str(self.source), '--otatools', str(self.root / 'otatools.zip'),
            '--keys', str(inventory), '--scratch', str(self.root / 'scratch'), '--output', str(self.root / 'plan.json')]
        return arguments, identity, signature

    def test_substituted_bundle_is_refused_before_unpack_or_archive_execution(self):
        arguments, _identity, _signature = self.bootstrap_inputs()
        (self.root / 'otatools.zip').write_bytes(b'untrusted substituted executable archive')
        self.refresh_report()  # Self-consistent replacement hashes are not authentication.
        with patch.object(api.za, 'unpack_tools') as unpack, patch.object(api, 'NativeTools') as tools, \
             patch.object(api, 'PublicKeys') as keys, patch.object(api, 'verified_build') as verify, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        for call in (unpack, tools, keys, verify): call.assert_not_called()
        self.assertFalse((self.root / 'plan.json').exists())

    def test_authenticated_metadata_swap_cannot_select_replacement_archive(self):
        arguments, identity, signature = self.bootstrap_inputs()
        native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity,
                        '-n', self.policy['verification_namespace'], self.root / 'verify.json'],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        signature.write_bytes((self.root / 'verify.json.sig').read_bytes())
        original_verify = api.verified_build
        def replaced(*args, **kwargs):
            # This hook is reached only after successful clean-host authentication.
            (self.root / 'otatools.zip').write_bytes(b'replacement archive code')
            record = inputs.load_json(self.root / 'build.json')
            record['otatools']['sha256'] = inputs.sha256_file(self.root / 'otatools.zip')
            (self.root / 'build.json').write_text(json.dumps(record))
            self.refresh_report()
            return original_verify(*args, **kwargs)
        with patch.object(api, 'verified_build', side_effect=replaced) as verified, \
             patch.object(inputs, 'git_read', side_effect=lambda _root, args:
                          ('a' * 40).encode() if args[0] == 'rev-parse' else b''), \
             patch.object(api, 'source_files', return_value={}), \
             patch.object(api.za, 'unpack_tools', side_effect=inputs.SigningError('archive execution sentinel')) as unpack, \
             patch.object(api, 'NativeTools') as tools, patch.object(api, 'PublicKeys') as keys, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        verified.assert_called_once()
        unpack.assert_not_called(); tools.assert_not_called(); keys.assert_not_called()
        self.assertFalse((self.root / 'plan.json').exists())

    def frozen_prepare_swap(self, stage):
        arguments, identity, signature = self.bootstrap_inputs()
        policy_path = self.root / 'policy.json'; policy_path.write_text(json.dumps(self.policy))
        arguments = ['--config', str(policy_path), *arguments]
        previous = self.root / 'previous.json'
        previous.write_text(json.dumps({'status': 'PASS', 'original': 'fixture'}))
        previous_signature = self.root / 'previous.sig'
        for path, namespace, destination in ((self.root / 'verify.json', self.policy['verification_namespace'], signature),
                                             (previous, self.policy['result_namespace'], previous_signature)):
            native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity, '-n', namespace, path],
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            destination.write_bytes(Path(str(path) + '.sig').read_bytes())
        arguments += ['--previous-result', str(previous), '--previous-signature', str(previous_signature)]
        names = ['policy.json', 'build.json', 'verify.json', 'verify.sig', 'SHA256SUMS', 'resolved-manifest.xml', 'previous.json', 'previous.sig',
                 'authority/inventory.json', 'authority/allowed', 'authority/avb.pem']
        names += ['authority/' + role + '.x509.pem' for role in inputs.CERT_ROLES]
        expected = {name: (self.root / name).read_bytes() for name in names}
        archive_bytes = (self.root / 'otatools.zip').read_bytes()
        def swap():
            for name in names: (self.root / name).write_bytes(b'original path replaced')
        original_native = api.native_inputs
        @contextmanager
        def native_entry(*args, **kwargs):
            if stage == 'native_inputs': swap()
            with original_native(*args, **kwargs) as value: yield value
        def make_tools(root, source, scratch, **kwargs):
            self.assertEqual(archive_bytes, (scratch / 'otatools.zip').read_bytes())
            return Mock(source=source, scratch=scratch)
        def make_keys(path, *args, **kwargs):
            for name in names:
                if name.startswith('authority/'):
                    self.assertEqual(expected[name], (path.parent / name.removeprefix('authority/')).read_bytes())
            return Mock()
        def prepared(record, report, target, manifest, policy, keys, tools, otatools, before, **kwargs):
            if stage == 'plans.prepare': swap()
            for name, path in (('build.json', record), ('verify.json', report), ('resolved-manifest.xml', manifest),
                               ('SHA256SUMS', record.parent / 'SHA256SUMS')):
                self.assertEqual(expected[name], path.read_bytes()); self.assertEqual(Path(name).name, path.name)
                self.assertNotEqual(self.root / name, path)
            self.assertEqual([json.loads(expected['previous.json'])], before)
            self.assertEqual(self.policy, policy)
            self.assertEqual(archive_bytes, otatools.read_bytes())
            self.assertNotEqual(self.root / 'otatools.zip', otatools)
            return {'changed_projects': [], 'provenance': {'source_files': {}}}
        with patch.object(api, 'native_inputs', side_effect=native_entry), \
             patch.object(inputs, 'git_read', side_effect=lambda _root, args:
                          ('a' * 40).encode() if args[0] == 'rev-parse' else b''), \
             patch.object(api, 'source_files', return_value={}), patch.object(api.za, 'unpack_tools', return_value=self.root), \
             patch.object(api, 'NativeTools', side_effect=make_tools), patch.object(api, 'PublicKeys', side_effect=make_keys), \
             patch.object(api.plans, 'prepare', side_effect=prepared) as prepare, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))  # Original-path change cannot publish a PASS.
        prepare.assert_called_once()
        self.assertFalse((self.root / 'plan.json').exists())
        self.assertEqual([], list((self.root / 'scratch').iterdir()))

    def test_native_entry_uses_frozen_metadata_and_external_public_material(self):
        self.frozen_prepare_swap('native_inputs')

    def test_plan_preparation_cannot_reopen_replaced_authenticated_metadata(self):
        self.frozen_prepare_swap('plans.prepare')

    def previous_pairs(self, identity, count, namespace=None, name='previous'):
        arguments = []
        for index in range(count):
            result = self.root / f'{name}-{index}.json'
            result.write_text(json.dumps({'status': 'PASS', 'index': index}))
            native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity,
                            '-n', namespace or self.policy['result_namespace'], result],
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            arguments += ['--previous-result', str(result), '--previous-signature', str(result) + '.sig']
        return arguments

    def test_up_to_three_previous_results_each_with_its_signature(self):
        arguments, identity, signature = self.bootstrap_inputs()
        native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity,
                        '-n', self.policy['verification_namespace'], self.root / 'verify.json'],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        signature.write_bytes((self.root / 'verify.json.sig').read_bytes())
        three = self.previous_pairs(identity, 3)
        # The middle pair is signed as a release record, not as a signing result.
        wrong = self.previous_pairs(identity, 1, 'diamaneos-release-record', 'wrong')
        cases = {'at most 3': self.previous_pairs(identity, 4, name='four'),
                 'requires its external signature': three[:-2],
                 'its external signature': three[2:4],
                 'Native tool failed': three[:4] + wrong + three[8:]}
        for message, previous in cases.items():
            with self.subTest(message), patch.object(api, 'verified_build') as verified, \
                 patch('sys.stderr', new=io.StringIO()) as error:
                self.assertEqual(2, api.main(arguments + previous))
                self.assertIn(message, error.getvalue()); verified.assert_not_called()
        # Three good pairs pass every signature and reach the build checks.
        with patch.object(api, 'verified_build', side_effect=inputs.SigningError('reached')) as verified, \
             patch('sys.stderr', new=io.StringIO()) as error:
            self.assertEqual(2, api.main(arguments + three))
        verified.assert_called_once(); self.assertIn('reached', error.getvalue())

    def test_snapshot_refuses_symlinks_hardlinks_oversize_and_descriptor_swap(self):
        source, destination = self.root / 'source.txt', self.root / 'snapshot.txt'
        source.write_bytes(b'public fixture'); link = self.root / 'link'; link.symlink_to(source)
        with self.assertRaises(inputs.SigningError): inputs.snapshot_file(link, destination)
        with self.assertRaises(inputs.SigningError): inputs.snapshot_file(source, destination, 2)
        import os
        hardlink = self.root / 'hardlink'; os.link(source, hardlink)
        with self.assertRaises(inputs.SigningError): inputs.snapshot_file(source, destination)
        hardlink.unlink()
        original_open = os.open
        def swapped(path, flags, *args, **kwargs):
            replacement = self.root / 'replacement'; replacement.write_bytes(b'replacement fixture')
            replacement.replace(source)
            return original_open(path, flags, *args, **kwargs)
        with patch.object(inputs.os, 'open', side_effect=swapped):
            with self.assertRaises(inputs.SigningError): inputs.snapshot_file(source, destination)
        self.assertFalse(destination.exists())

    def test_unchanged_snapshots_preserve_authenticated_build_basenames_and_checksums(self):
        arguments, identity, signature = self.bootstrap_inputs()
        report = self.root / 'verify.json'
        native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity,
                        '-n', self.policy['verification_namespace'], report],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        signature.write_bytes(Path(str(report) + '.sig').read_bytes())
        args = api.parser().parse_args(arguments)
        with api.metadata_inputs(args) as frozen:
            authority, signer = native.factory_authority(frozen.keys, self.policy)
            native.host_ssh_signature(frozen.verification_report, frozen.verification_signature,
                                     authority, signer, self.policy['verification_namespace'], args.scratch)
            for original, snapshot in ((args.build_record, frozen.build_record),
                                       (args.verification_report, frozen.verification_report),
                                       (args.resolved_manifest, frozen.resolved_manifest)):
                self.assertEqual(original.name, snapshot.name)
                self.assertNotEqual(original.parent, snapshot.parent)
                self.assertEqual(original.read_bytes(), snapshot.read_bytes())
            self.assertEqual((self.root / 'SHA256SUMS').read_bytes(),
                             (frozen.build_record.parent / 'SHA256SUMS').read_bytes())
            copied_tools = args.scratch / 'otatools.zip'
            copied_tools.write_bytes(args.otatools.read_bytes())
            self.addCleanup(copied_tools.unlink)
            with patch.object(inputs, 'git_read', side_effect=lambda _root, argv:
                              ('a' * 40).encode() if argv[0] == 'rev-parse' else b''):
                bound = inputs.verified_build(frozen.build_record, frozen.verification_report,
                    args.target_files, frozen.resolved_manifest, mode='release',
                    source_root=args.source_root, otatools=copied_tools)
            for name, path in (('record', args.build_record), ('verification', report),
                               ('manifest', args.resolved_manifest), ('otatools', args.otatools)):
                self.assertEqual(inputs.sha256_file(path), bound[name + '_sha256'])
            inputs.recheck_stamps(frozen.metadata_stamps)

    def test_bootstrap_signature_has_only_host_paths_and_no_inherited_loader_environment(self):
        with patch.object(native, 'command') as run:
            native.host_ssh_signature('report', 'signature', 'allowed', 'fixture', 'namespace', self.root)
        argv = run.call_args.args[0]; env = run.call_args.kwargs['env']
        self.assertEqual('/bin/sh', argv[0]); self.assertIn('/usr/bin/ssh-keygen', argv)
        self.assertEqual({'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL'}, set(env))
        self.assertEqual('/usr/bin:/bin', env['PATH'])
        self.assertNotIn('LD_LIBRARY_PATH', env); self.assertNotIn('JAVA_HOME', env)
        tools = native.NativeTools.__new__(native.NativeTools)
        tools.scratch = self.root; tools.env = {'LD_LIBRARY_PATH': 'untrusted', 'PATH': 'untrusted'}
        with patch.object(native, 'command') as run:
            tools.ssh_signature('report', 'signature', 'allowed', 'fixture', 'namespace')
        self.assertEqual(env, run.call_args.kwargs['env'])

    def test_tool_snapshot_refuses_changed_bytes_before_unpack(self):
        archive = self.root / 'tools.zip'; archive.write_bytes(b'replaced')
        inventory = self.root / 'inventory'; inventory.write_text('{}')
        with patch.object(api.za, 'unpack_tools') as unpack, patch.object(api, 'NativeTools') as tools, \
             self.assertRaisesRegex(inputs.SigningError, 'snapshot differs'):
            with api.native_inputs(archive, self.root, inventory, self.root, True, {},
                                   expected_otatools=hashlib.sha256(b'expected').hexdigest()):
                self.fail('Unverified tools admitted')
        unpack.assert_not_called(); tools.assert_not_called()

    def verify_bootstrap_inputs(self):
        _arguments, identity, _signature = self.bootstrap_inputs()
        publication_root = self.root / 'publication'; publication_root.mkdir()
        plan = {'schema_version': 2, 'mode': 'qualification', 'policy_sha256': inputs.canonical_sha256(self.policy),
                'provenance': {'otatools_sha256': inputs.sha256_file(self.root / 'otatools.zip'), 'projects': {}},
                'packages': {}, 'roles': {}, 'metadata': {}, 'mac_permissions': {}}
        plan['plan_sha256'] = inputs.canonical_sha256(plan)
        (self.root / 'plan-input.json').write_text(json.dumps(plan))
        record = publication_root / 'record.json'
        record.write_text(json.dumps({'schema_version': 1, 'plan_sha256': plan['plan_sha256'], 'artifacts': {}}))
        signature = publication_root / 'record.sig'; signature.write_text('invalid signature')
        publication = {'schema_version': 1, 'signed_target_files': 'target.zip', 'images_archive': 'images.zip',
                       'ota': [{'file': 'ota.zip', 'source_target_files': None}],
                       'factory_archive': 'factory.zip', 'factory_signature': 'factory.sig',
                       'release_record': 'record.json', 'release_signature': 'record.sig'}
        (self.root / 'publication.json').write_text(json.dumps(publication))
        arguments = ['verify', '--qualification', '--plan', str(self.root / 'plan-input.json'), '--publication', str(self.root / 'publication.json'),
            '--artifact-root', str(publication_root), '--source-root', str(self.source), '--otatools', str(self.root / 'otatools.zip'),
            '--keys', str(self.root / 'authority/inventory.json'), '--scratch', str(self.root / 'scratch'),
            '--output', str(self.root / 'result.json')]
        return arguments, identity, record, signature

    def test_verify_authenticates_release_plan_before_incoming_tooling(self):
        arguments, _identity, _record, _signature = self.verify_bootstrap_inputs()
        with patch.object(api.za, 'unpack_tools') as unpack, patch.object(api, 'NativeTools') as tools, \
             patch.object(api, 'PublicKeys') as keys, patch.object(api, 'source_files') as source, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        for call in (unpack, tools, keys, source): call.assert_not_called()
        self.assertFalse((self.root / 'result.json').exists())

    def test_verify_authenticates_and_parses_same_release_record_snapshot(self):
        arguments, identity, record, signature = self.verify_bootstrap_inputs()
        original_record = record.read_bytes()
        native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity,
                        '-n', self.policy['record_namespace'], record],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        signature.write_bytes(Path(str(record) + '.sig').read_bytes())
        Path(str(record) + '.sig').unlink()
        host_verify, load = api.host_ssh_signature, api.load_json
        observed = []
        def authenticated(path, *args):
            self.assertEqual(original_record, path.read_bytes()); self.assertNotEqual(record, path)
            host_verify(path, *args)
            record.write_text(json.dumps({'schema_version': 1, 'plan_sha256': 'f' * 64, 'artifacts': {}}))
            (self.root / 'plan-input.json').write_bytes(b'replaced plan')
            (self.root / 'authority/allowed').write_bytes(b'replaced authority')
        def parsed(path, *args, **kwargs):
            if Path(path).name == 'record.json': observed.append(Path(path).read_bytes())
            return load(path, *args, **kwargs)
        with patch.object(api, 'host_ssh_signature', side_effect=authenticated), \
             patch.object(api, 'load_json', side_effect=parsed), patch.object(api.za, 'unpack_tools') as unpack, \
             patch.object(api, 'NativeTools') as tools, patch.object(api, 'PublicKeys') as keys, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        self.assertEqual([original_record], observed)
        unpack.assert_not_called(); tools.assert_not_called(); keys.assert_not_called()
        self.assertFalse((self.root / 'result.json').exists())

    def test_external_authority_boundary_is_checked_before_snapshot_relocation(self):
        arguments, _identity, record, _signature = self.verify_bootstrap_inputs()
        authority = self.root / 'publication/keys'; authority.mkdir()
        inventory = self.root / 'authority/inventory.json'
        moved = authority / inventory.name; moved.write_bytes(inventory.read_bytes())
        arguments[arguments.index('--keys') + 1] = str(moved)
        with patch.object(api, 'host_ssh_signature') as authenticate, patch.object(api.za, 'unpack_tools') as unpack, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        authenticate.assert_not_called(); unpack.assert_not_called()
        external = self.root / 'external.json'; value = inputs.load_json(inventory)
        value['certificate_roles'] = {role: 'authority/' + name for role, name in value['certificate_roles'].items()}
        value['factory']['allowed_signers'] = 'authority/allowed'; value['avb_public_key'] = 'publication/avb.pem'
        (self.root / 'publication/avb.pem').write_bytes(b'publication-controlled public key')
        external.write_text(json.dumps(value)); arguments[arguments.index('--keys') + 1] = str(external)
        with patch.object(api, 'host_ssh_signature') as authenticate, patch.object(api.za, 'unpack_tools') as unpack, \
             patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        authenticate.assert_not_called(); unpack.assert_not_called()

    def test_authenticated_record_for_other_plan_refuses_before_incoming_tooling(self):
        arguments, identity, record, signature = self.verify_bootstrap_inputs()
        value = json.loads(record.read_text()); value['plan_sha256'] = 'f' * 64
        record.write_text(json.dumps(value))
        native.command(['/usr/bin/ssh-keygen', '-Y', 'sign', '-f', identity, '-n', self.policy['record_namespace'], record],
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        signature.write_bytes(record.with_suffix('.json.sig').read_bytes())
        with patch.object(api.za, 'unpack_tools') as unpack, patch.object(api, 'NativeTools') as tools, \
             patch.object(api, 'PublicKeys') as keys, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(2, api.main(arguments))
        for call in (unpack, tools, keys): call.assert_not_called()
        self.assertFalse((self.root / 'result.json').exists())

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
        tools.avb_rollback_index.return_value = 1791158400
        keys = Mock(); keys.avb.return_value = Path('/external/avb.pub.pem')
        with patch.object(native, 'safe_file', side_effect=lambda root, name: Path(root) / name):
            self.assertEqual(['boot', 'vbmeta'], native.verify_images(tools, keys, Path('/images'), ['boot', 'vbmeta'], {}))
            with self.assertRaises(inputs.SigningError): native.verify_images(tools, keys, Path('/images'), ['boot', 'vbmeta', 'system'], {})
            tools.avb_rollback_index.return_value = 0
            with self.assertRaisesRegex(inputs.SigningError, 'rollback index of vbmeta is 0'):
                native.verify_images(tools, keys, Path('/images'), ['boot', 'vbmeta'], {})
        real = native.NativeTools.__new__(native.NativeTools); real.run = Mock(return_value=info.replace('Flags: 0', 'Flags: 2'))
        real.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        with self.assertRaises(inputs.SigningError): real.avb(Path('/image'), Path('/external/key'))

    def test_avb_refuses_a_header_field_the_release_string_repeats(self):
        # An unsigned image whose release string carries its own algorithm line.
        real = native.NativeTools.__new__(native.NativeTools)
        real.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        algorithm = real.policy['avb_algorithm']
        signed = (f"Algorithm:                {algorithm}\nRollback Index:           0\n"
                  "Flags:                    0\nRelease String:           'avbtool 1.3.0'\nDescriptors:\n")
        unsigned = signed.replace(algorithm, 'NONE').replace("'avbtool 1.3.0'", f"'x'\nAlgorithm: {algorithm}\n''")
        for field in ('Algorithm', 'Flags'):
            real.run = Mock(return_value=unsigned if field == 'Algorithm' else signed + 'Flags: 0\n')
            with self.assertRaisesRegex(inputs.SigningError, 'repeats a header field'):
                real.avb(Path('/image'), Path('/external/key'))
            real.run.assert_called_once()
        real.run = Mock(return_value=signed)
        real.avb(Path('/image'), Path('/external/key'))
        self.assertEqual(['info_image', 'verify_image'], [call.args[1][0] for call in real.run.call_args_list])


if __name__ == '__main__':
    unittest.main()
