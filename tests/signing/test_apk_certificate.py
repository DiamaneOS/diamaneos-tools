"""Native signer output and selected-source byte authentication."""
import hashlib
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from diamaneos_tools.signing_verify import apk_certificate_digests, SigningError
from diamaneos_tools import signing_inputs as inputs, signing_native as native
from .fixtures import TemporaryInputs


class ApkCertificateTest(unittest.TestCase):
    def output(self, label="V3.0 Signer:", digest="a" * 64, public="b" * 64):
        return ("Verifies\nVerified using v3 scheme (APK Signature Scheme v3): true\n"
                "Number of signers: 1\n" + label + " certificate DN: O=fixture\n"
                + label + " certificate SHA-256 digest: " + digest + "\n"
                + label + " public key SHA-256 digest: " + public + "\n")

    def test_scheme_and_legacy_certificate_labels(self):
        for label in ("Signer #1", "V1 Signer:", "V2 Signer:", "V3.0 Signer:", "V3.1 Signer:", "V3.2 Signer:"):
            with self.subTest(label=label):
                self.assertEqual(["a" * 64], apk_certificate_digests(self.output(label), "a" * 64))

    def test_wrong_key_missing_malformed_and_extra_signer_are_rejected(self):
        variants = [self.output(digest="b" * 64), self.output(digest="abc"),
                    self.output().replace("Number of signers: 1", "Number of signers: 2"),
                    self.output().replace("Number of signers: 1\n", ""),
                    self.output().replace("certificate SHA-256 digest:", "unsupported digest:"),
                    self.output() + "Signer #2 certificate SHA-256 digest: " + "b" * 64 + "\n",
                    self.output("Unknown Signer:")]
        for output in variants:
            with self.subTest(output=output):
                with self.assertRaises(SigningError):
                    apk_certificate_digests(output, "a" * 64)

    def test_matching_public_key_digest_does_not_replace_certificate_pin(self):
        with self.assertRaises(SigningError):
            apk_certificate_digests(self.output(), "b" * 64)

    def production(self, output, expected="a" * 64):
        tools = native.NativeTools.__new__(native.NativeTools)
        tools.run = Mock(return_value=output)
        tools.denied_apk_public_keys = {"c" * 64, "d" * 64}
        result = tools.apk(Path('/controlled-fixture.apk'), expected)
        tools.run.assert_called_once_with('apksigner', ['verify', '--verbose', '--print-certs', Path('/controlled-fixture.apk')])
        return result

    def test_production_accepts_scheme_legacy_and_sdk_restricted_signers(self):
        for label in ('Signer #1', 'Signer #1:', 'V1 Signer:', 'V2 Signer:', 'V3.0 Signer:',
                      'V3.1 Signer:', 'V3.2 Signer:',
                      'V3.1 Signer: (minSdkVersion=33, maxSdkVersion=2147483647)',
                      'V3.1 Signer: (minSdkVersion=33 (dev release=true), maxSdkVersion=2147483647)'):
            with self.subTest(label=label):
                self.assertEqual(['a' * 64], self.production(self.output(label)))

    def test_production_checks_every_scheme_public_key_not_only_current_signer(self):
        current = self.output('V3.1 Signer: (minSdkVersion=33, maxSdkVersion=2147483647)')
        old_label = 'V3.0 Signer: (minSdkVersion=28, maxSdkVersion=32)'
        old = self.output(old_label).replace('Number of signers: 1\n', '')
        self.assertEqual(['a' * 64], self.production(current + old))
        for denied in ('c' * 64, 'd' * 64):
            for output in (current + old.replace('b' * 64, denied),
                           current.replace('b' * 64, denied) + old):
                with self.subTest(denied=denied, output=output), self.assertRaisesRegex(SigningError, 'public APK test keys'):
                    self.production(output)

    def test_production_refuses_unknown_malformed_uncovered_and_repeated_spki_output(self):
        valid = self.output()
        variants = [valid.replace('V3.0 Signer: public key', 'Unknown Signer: public key'),
                    valid.replace('public key SHA-256 digest: ' + 'b' * 64, 'public key SHA-256 digest: abc'),
                    valid.replace('V3.0 Signer: public key', 'V3.1 Signer: public key'),
                    valid.replace('V3.0 Signer: public key SHA-256 digest: ' + 'b' * 64 + '\n', ''),
                    valid + 'V3.0 Signer: public key SHA-256 digest: ' + 'b' * 64 + '\n',
                    valid + 'Signer #2 public key SHA-256 digest: ' + 'b' * 64 + '\n']
        for output in variants:
            with self.subTest(output=output), self.assertRaises(SigningError):
                self.production(output)

    def test_production_cannot_substitute_matching_spki_for_external_certificate(self):
        with self.assertRaisesRegex(SigningError, 'certificate does not match'):
            self.production(self.output(digest='e' * 64))


class SourceBytesTests(TemporaryInputs, unittest.TestCase):
    """Real disposable Git trees; no native signer or Android build executes."""

    def setUp(self):
        super().setUp()
        self.files = {
            'script': {'generate-release.sh': b'release\n', 'generate-delta.sh': b'delta\n', 'common.sh': b'common\n'},
            'build/make': {'tools/releasetools/check_ota_package_signature.py': b'checker\n',
                           'target/product/security/testkey.x509.pem': b'public certificate fixture\n'},
            'tools/diamaneos': {'config/fp6-image-checks.json': b'{"rules":[]}\n'},
            'system/apex': {'test/key.x509.pem': b'apex public certificate fixture\n'},
            'external/avb': {'test/data/testkey_rsa4096.pem': b'public test input fixture\n'},
            'prebuilts/jdk/jdk25': {'linux-x86/bin/java': b'java executable fixture\n',
                                  'linux-x86/lib/modules': b'bytecode fixture\n',
                                  'linux-x86/conf/security/java.security': b'runtime data fixture\n'},
        }
        self.projects = {}
        for project, files in self.files.items():
            root = self.root / project
            root.mkdir(parents=True)
            self.git(root, 'init', '-q')
            for name, value in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            self.git(root, 'add', '--all')
            self.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'fixture')
            self.projects[project] = self.git(root, 'rev-parse', 'HEAD').strip()

    def git(self, root, *args):
        environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': '/dev/null',
                       'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
        return subprocess.run(['git', '-C', str(root), *map(str, args)], env=environment, check=True,
                              capture_output=True, text=True, timeout=20).stdout

    def verify(self):
        return inputs.source_files(self.root, self.projects)

    def test_selected_blobs_include_scripts_rules_keys_runtime_code_and_data(self):
        result = self.verify()
        expected = {project + '/' + name: hashlib.sha256(value).hexdigest()
                    for project, files in self.files.items() for name, value in files.items()}
        self.assertEqual(expected, result)

    def test_assume_unchanged_and_skip_worktree_cannot_hide_bad_consumed_bytes(self):
        for flag in ('--assume-unchanged', '--skip-worktree'):
            for project, relative in (('script', 'generate-release.sh'),
                                      ('prebuilts/jdk/jdk25', 'linux-x86/lib/modules'),
                                      ('prebuilts/jdk/jdk25', 'linux-x86/conf/security/java.security'),
                                      ('build/make', 'target/product/security/testkey.x509.pem')):
                with self.subTest(flag=flag, path=relative):
                    root = self.root / project; path = root / relative
                    before = path.stat(); original = path.read_bytes()
                    self.git(root, 'update-index', flag, '--', relative)
                    changed = bytes([original[0] ^ 1]) + original[1:]
                    with path.open('r+b') as stream: stream.write(changed)
                    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                    self.assertEqual(before.st_ino, path.stat().st_ino)
                    self.assertEqual('', self.git(root, 'status', '--porcelain', '--untracked-files=all'))
                    with self.assertRaisesRegex(SigningError, 'selected Git blob'):
                        self.verify()
                    path.write_bytes(original)
                    self.git(root, 'update-index', '--no-assume-unchanged', '--no-skip-worktree', '--', relative)

    def test_missing_skipped_key_and_ignored_runtime_bytecode_refuse(self):
        root = self.root / 'build/make'; relative = 'target/product/security/testkey.x509.pem'
        path = root / relative; original = path.read_bytes()
        self.git(root, 'update-index', '--skip-worktree', '--', relative)
        path.unlink()
        self.assertEqual('', self.git(root, 'status', '--porcelain', '--untracked-files=all'))
        with self.assertRaises(SigningError): self.verify()
        path.write_bytes(original)
        self.git(root, 'update-index', '--no-skip-worktree', '--', relative)
        root = self.root / 'prebuilts/jdk/jdk25'
        (root / '.git/info/exclude').write_text('*.pyc\n')
        (root / 'linux-x86/lib/injected.pyc').write_bytes(b'ignored bytecode fixture')
        self.assertEqual('', self.git(root, 'status', '--porcelain', '--untracked-files=all'))
        with self.assertRaisesRegex(SigningError, 'absent from selected Git tree'): self.verify()

    def test_wrong_head_dirty_source_and_untracked_consumed_file_refuse(self):
        wrong = dict(self.projects); wrong['script'] = 'a' * 40
        with self.assertRaisesRegex(SigningError, 'resolved manifest'): inputs.source_files(self.root, wrong)
        path = self.root / 'script/common.sh'; path.write_bytes(b'changed source\n')
        with self.assertRaisesRegex(SigningError, 'unrecorded changes'): self.verify()

    def test_replaced_consumed_file_and_changed_bytes_during_hash_refuse(self):
        path = self.root / 'script/common.sh'; original = path.read_bytes()
        self.git(self.root / 'script', 'update-index', '--assume-unchanged', '--', 'common.sh')
        read = inputs.git_read
        def replacing(root, args, **kwargs):
            result = read(root, args, **kwargs)
            if args[0] == 'hash-object' and args[-1] == path:
                replacement = path.with_suffix('.replacement'); replacement.write_bytes(original)
                os.replace(replacement, path)
            return result
        with patch.object(inputs, 'git_read', side_effect=replacing), self.assertRaisesRegex(SigningError, 'changed during verification'):
            self.verify()

    def test_declared_runtime_symlink_binds_link_and_target_bytes(self):
        root = self.root / 'prebuilts/jdk/jdk25'
        link = root / 'linux-x86/lib/link'; link.symlink_to('modules')
        self.git(root, 'add', '--all')
        self.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'link fixture')
        self.projects['prebuilts/jdk/jdk25'] = self.git(root, 'rev-parse', 'HEAD').strip()
        result = self.verify()
        self.assertEqual(hashlib.sha256(b'modules').hexdigest(), result['prebuilts/jdk/jdk25/linux-x86/lib/link'])
        self.git(root, 'update-index', '--assume-unchanged', '--', 'linux-x86/lib/link')
        link.unlink(); link.symlink_to('/etc/passwd')
        with self.assertRaises(SigningError): self.verify()

    def test_closing_source_hash_refuses_late_in_place_mutation(self):
        root = self.root / 'script'; path = root / 'common.sh'
        self.git(root, 'update-index', '--assume-unchanged', '--', 'common.sh')
        read = inputs.git_read
        calls = 0
        def changing(project, args, **kwargs):
            nonlocal calls
            result = read(project, args, **kwargs)
            if project == root and args[0] == 'status':
                calls += 1
                if calls == 2:
                    with path.open('r+b') as stream: stream.write(b'changed')
            return result
        with patch.object(inputs, 'git_read', side_effect=changing), self.assertRaisesRegex(SigningError, 'closing verification'):
            self.verify()
