"""External APK certificate policy with controlled native output, not cryptographic proof."""
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock

from diamaneos_tools import signing_archive as archive, signing_inputs as inputs
from diamaneos_tools import signing_native as native, signing_verify as verifier
from .fixtures import TemporaryInputs, apk_plan, write_zip


CERTIFICATES = {
    'AppStore.apk': '559ffb104edd9386a39d512063cb7458c5ec0e29c9b52a91068ce950dd768a47',
    'Camera.apk': '285a091a913f1485ce16f280f06b2609dc1af6cca786e45b6f5356aa7e86fa61',
    'Auditor.apk': '427bfd360586b829475f37abffd2548d93db3618e45c994de416469ef04bc2ec',
    # Guards its preview service with a signature permission the Camera holds: the Camera certificate.
    'CameraProcessor.apk': '285a091a913f1485ce16f280f06b2609dc1af6cca786e45b6f5356aa7e86fa61',
}
UPSTREAM = {
    'AppStore.apk': '3384c31fce4a7c008a8f7b2652bc48cc4321f1d2c877b18e30a4ed619af12f6b',
    'Camera.apk': '6436b155b917c2f9a9ed1d15c4993a5968ffabc94947c13f2aeee14b7b27ed59',
    'Auditor.apk': '990e04f0864b19f14f84e0e432f7a393f297ab105a22c1e1b10b442a4a62c42c',
    'CameraProcessor.apk': '6436b155b917c2f9a9ed1d15c4993a5968ffabc94947c13f2aeee14b7b27ed59',
}
PACKAGES = {'AppStore.apk': 'app.grapheneos.apps', 'Camera.apk': 'app.grapheneos.camera',
            'Auditor.apk': 'de.diamaneos.auditor', 'CameraProcessor.apk': 'app.grapheneos.camera.processor'}


def verifier_output(certificate, public_key='b' * 64):
    return ('Verifies\nNumber of signers: 1\n'
            f'V3.0 Signer: certificate SHA-256 digest: {certificate}\n'
            f'V3.0 Signer: public key SHA-256 digest: {public_key}\n')


class AppTrustPolicyTests(TemporaryInputs, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.policy = inputs.load_json(inputs.DEFAULT_CONFIG)
        inputs.validate_config(self.policy)
        # Only the PRESIGNED policy lookup is used; no key inventory is opened.
        self.keys = native.PublicKeys.__new__(native.PublicKeys)
        self.keys.policy = self.policy
        self.tools = native.NativeTools.__new__(native.NativeTools)
        self.tools.scratch = self.root
        self.tools.denied_apk_public_keys = {'c' * 64}
        self.observed = dict(CERTIFICATES)
        self.tools.run = Mock(side_effect=self.native_output)

    def logical(self, name):
        directory = 'priv-app' if name == 'AppStore.apk' else 'app'
        return f'PRODUCT/{directory}/{Path(name).stem}/{name}'

    def pin(self, name):
        return self.keys.fingerprint('PRESIGNED', self.logical(name))

    def native_output(self, tool, arguments):
        name = Path(arguments[-1]).name
        if tool == 'apksigner':
            return verifier_output(self.observed[name])
        self.assertEqual('aapt2', tool)
        self.assertEqual(['dump', 'badging'], arguments[:-1])
        return f"package: name='{PACKAGES[name]}' versionCode='1'\n"

    def target_files(self):
        members, packages, metadata = {}, {}, []
        for name, certificate in CERTIFICATES.items():
            logical = self.logical(name)
            data = ('controlled presigned package ' + name).encode()
            path = self.root / name
            path.write_bytes(data)
            members[logical] = data
            packages[logical] = apk_plan('PRESIGNED', certificate, PACKAGES[name])
            packages[logical]['presigned_sha256'] = inputs.sha256_file(path)
            metadata.append(f'name="{name}" certificate="PRESIGNED" private_key="" '
                            'compressed="false" partition="product"\n')
        members['META/apkcerts.txt'] = ''.join(metadata)
        members['META/apexkeys.txt'] = ''
        target = self.root / 'target-files.zip'
        write_zip(target, members)
        return target, {'metadata': archive.package_metadata_shape(archive.metadata(target)),
                        'packages': packages}

    def test_external_certificates_are_the_only_declared_app_authorities(self):
        for name, certificate in CERTIFICATES.items():
            with self.subTest(package=name):
                rule = inputs.presigned_rule(self.policy, self.logical(name))
                self.assertEqual([certificate], rule['certificate_sha256'])
                self.assertEqual(certificate, self.pin(name))
                self.assertEqual([certificate], self.tools.apk(self.root / name, self.pin(name)))

    def test_apps_reject_upstream_other_roles_and_matching_public_key_only(self):
        authorities = {digest for rule in self.policy['presigned_packages']
                       for digest in rule['certificate_sha256']}
        for name, certificate in CERTIFICATES.items():
            for wrong in (authorities | set(UPSTREAM.values()) | {'b' * 64}) - {certificate}:
                with self.subTest(package=name, certificate=wrong):
                    self.tools.run.side_effect = None
                    self.tools.run.return_value = verifier_output(wrong, public_key=certificate)
                    with self.assertRaisesRegex(inputs.SigningError, 'certificate does not match'):
                        self.tools.apk(self.root / name, self.pin(name))

    def test_app_certificates_do_not_authorize_other_presigned_roles(self):
        for rule in self.policy['presigned_packages'] + self.policy['presigned_apexes']:
            if rule['metadata_name'] in CERTIFICATES:
                continue
            for certificate in CERTIFICATES.values():
                with self.subTest(package=rule['metadata_name'], certificate=certificate):
                    self.assertNotIn(certificate, rule['certificate_sha256'])
                    with self.assertRaises(inputs.SigningError):
                        native.apk_certificate_digests(verifier_output(certificate), rule['certificate_sha256'][0])

    def test_app_authorities_do_not_extend_android_roles_or_enclosed_apks(self):
        for name in CERTIFICATES:
            with self.subTest(package=name):
                role = Path(name).stem.lower()
                self.assertNotIn(role, self.policy['certificate_roles'])
                self.assertNotIn(role, self.policy['apk_selectors'].values())
                logical = 'PRODUCT/apex/example.apex!/app/' + name
                self.assertIsNone(inputs.presigned_rule(self.policy, logical))
                with self.assertRaisesRegex(inputs.SigningError, 'no exact reviewed signer'):
                    self.keys.fingerprint('PRESIGNED', logical)

    def test_final_packages_use_the_external_policy_and_preserved_bytes(self):
        target, plan = self.target_files()
        self.assertEqual({'apk': 4, 'apex': 0, 'payload': 0},
                         verifier.verify_packages(target, plan, self.tools, self.keys))
        self.assertEqual(4, sum(call.args[0] == 'apksigner' for call in self.tools.run.call_args_list))

    def test_final_plan_cannot_enroll_the_artifacts_own_signer(self):
        target, plan = self.target_files()
        for name in CERTIFICATES:
            changed = copy.deepcopy(plan)
            changed['packages'][self.logical(name)]['certificate_sha256'] = UPSTREAM[name]
            self.observed[name] = UPSTREAM[name]
            with self.subTest(package=name), self.assertRaisesRegex(inputs.SigningError, 'authority changed'):
                verifier.verify_packages(target, changed, self.tools, self.keys)
            self.observed[name] = CERTIFICATES[name]

    def test_final_packages_reject_another_native_certificate(self):
        target, plan = self.target_files()
        for name in CERTIFICATES:
            self.observed[name] = UPSTREAM[name]
            with self.subTest(package=name), self.assertRaisesRegex(inputs.SigningError, 'certificate does not match'):
                verifier.verify_packages(target, plan, self.tools, self.keys)
            self.observed[name] = CERTIFICATES[name]

    def test_matching_app_certificate_cannot_authorize_changed_presigned_bytes(self):
        for name in CERTIFICATES:
            target, plan = self.target_files()
            plan['packages'][self.logical(name)]['presigned_sha256'] = 'a' * 64
            with self.subTest(package=name), self.assertRaisesRegex(inputs.SigningError, 'Presigned package was changed'):
                verifier.verify_packages(target, plan, self.tools, self.keys)


if __name__ == '__main__':
    unittest.main()
