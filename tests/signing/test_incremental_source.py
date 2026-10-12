"""Incremental identity and closing-source guards; controlled native responses."""
import hashlib,json,pathlib,tempfile,unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from diamaneos_tools import signing_inputs as si,signing_verify as api,signing_native as native
from .fixtures import write_zip,publication_description,publication_plan,release_record

class IncrementalGuards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name).resolve()
        self.pub=self.root/'pub';self.pub.mkdir();self.authority=self.root/'keys';self.authority.mkdir()
        (self.authority/'allowed').write_bytes(b'controlled public authority')
        self.keys=Mock();self.keys.root=self.authority;self.keys.public_ids={};self.keys.inventory={'factory':{'allowed_signers':'allowed','identity':'fixture'}}
        self.keys.files={'allowed':si.sha256_file(self.authority/'allowed')};self.keys.file.side_effect=lambda name:self.authority/name
        self.tools=Mock(spec=native.NativeTools);self.tools.source=self.root/'source';self.tools.scratch=self.root
        self.tools.java_home=self.root/'java';(self.tools.java_home/'bin').mkdir(parents=True);(self.tools.java_home/'bin/java').write_bytes(b'fixture java')
        target=self.pub/'target-files.zip';write_zip(target,{'META/apkcerts.txt':'','META/apexkeys.txt':''})
        for name in ('images.zip','factory.tar','factory.tar.sig','release.json.sig'):(self.pub/name).write_bytes(b'controlled '+name.encode())
        self.description=publication_description();self.description['ota'].append({'file':'incremental.zip','source_target_files':'base-target-files.zip'})
        write_zip(self.pub/'ota.zip',{'payload.bin':b'full','META-INF/com/android/metadata':'ota-type=AB\n'})
        self.base=self.pub/'base-target-files.zip';self.base.write_bytes(b'controlled previously verified source archive')
        self.metadata={'pre-build':'Fairphone/FP6/FP6:17/id/base:user/release-keys','pre-build-incremental':'base'}
        self.tools.target_identity.return_value={'fingerprint':self.metadata['pre-build'],'incremental':'base'}
        self.policy={'record_namespace':'fixture record','factory_namespace':'fixture factory'}
        self.plan=publication_plan(target,{'SYSTEM/app/Fixture.apk':{'kind':'apk'}},self.keys,
            {'projects':{},'source_files':{},'java_sha256':si.sha256_file(self.tools.java_home/'bin/java')},self.policy,partitions=['boot'])
        self.plan['previous_artifacts']={'base-target-files.zip':si.sha256_file(self.base)}
        self.incremental()

    def incremental(self):
        write_zip(self.pub/'incremental.zip',{'payload.bin':b'delta','META-INF/com/android/metadata':'\n'.join(k+'='+v for k,v in self.metadata.items())+'\n'})
        release_record(self.pub,self.plan,self.description)

    def verify(self):
        # Direct orchestration calls lack the CLI's per-run temporary scratch.
        for path in self.root.glob('payload-*.bin'): path.unlink()
        with patch.object(api,'validate_plan'),patch.object(api,'source_files',return_value={}),\
             patch.object(api,'verify_packages',return_value={'apk':1,'apex':0,'payload':0}),\
             patch.object(api.signing_mac,'verify_mac_permissions',return_value={}),\
             patch.object(api,'publication_images',return_value=['boot']),patch.object(api,'verify_factory_contents'):
            return api.verify_publication(self.plan,self.description,self.pub,self.policy,self.tools,self.keys)

    def test_exact_source_identity_and_both_payloads_are_checked(self):
        self.assertEqual('PASS',self.verify()['status'])
        self.tools.target_identity.assert_called_once_with(self.base)
        self.assertEqual(2,self.tools.apply_payload.call_count)

    def test_wrong_empty_and_missing_source_fields_refuse_before_delta_apply(self):
        for key in ('pre-build','pre-build-incremental'):
            for value in ('','wrong',None):
                with self.subTest(key=key,value=value):
                    good=dict(self.metadata)
                    if value is None:self.metadata.pop(key)
                    else:self.metadata[key]=value
                    self.incremental();self.tools.apply_payload.reset_mock()
                    with self.assertRaisesRegex(si.SigningError,'source-build metadata differs'):self.verify()
                    self.assertEqual(1,self.tools.apply_payload.call_count)
                    self.metadata=good

    def test_source_lacks_verified_history_refuses_before_identity_read(self):
        self.plan['previous_artifacts']={}
        with self.assertRaisesRegex(si.SigningError,'verified signing result'):self.verify()
        self.tools.target_identity.assert_not_called()

    def test_source_changed_during_delta_apply_is_not_accepted(self):
        def apply(payload,target,source):
            if source:self.base.write_bytes(b'changed source after acceptance')
        self.tools.apply_payload.side_effect=apply
        with self.assertRaisesRegex(si.SigningError,'Delta source changed'):self.verify()

class PlatformIdentity(unittest.TestCase):
    def test_git_bound_snapshot_is_passed_to_platform_buildinfo(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td).resolve();project=root/'build/make';sdk=project/'tools/releasetools';sdk.mkdir(parents=True)
            content=b'# controlled platform module\n';(sdk/'common.py').write_bytes(content)
            tools=native.NativeTools.__new__(native.NativeTools);tools.source=root;tools.scratch=root;tools.projects={'build/make':'a'*40};tools.env={}
            def git(path,args,**kwargs):return b'tools/releasetools/common.py\n' if args[0]=='ls-tree' else content
            def command(args,**kwargs):
                copied=pathlib.Path(args[-2]);self.assertEqual(content,(copied/'common.py').read_bytes())
                self.assertEqual(0o400,(copied/'common.py').stat().st_mode&0o777)
                self.assertIn('common.BuildInfo(common.LoadInfoDict',args[4])
                return json.dumps({'fingerprint':'fixture/fingerprint','incremental':'base'}).encode()
            with patch.object(native,'git_read',side_effect=git),patch.object(native,'command',side_effect=command):
                self.assertEqual({'fingerprint':'fixture/fingerprint','incremental':'base'},tools.target_identity(root/'base.zip'))
            self.assertFalse(any(p.name.startswith('platform-identity-') for p in root.iterdir()))

    def test_malformed_empty_native_identity_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td).resolve();sdk=root/'build/make/tools/releasetools';sdk.mkdir(parents=True);(sdk/'common.py').write_bytes(b'module')
            tools=native.NativeTools.__new__(native.NativeTools);tools.source=root;tools.scratch=root;tools.projects=None;tools.env={}
            with patch.object(native,'git_read',side_effect=[b'tools/releasetools/common.py\n',b'module']),patch.object(native,'command',return_value=b'{"fingerprint":"","incremental":"base"}'):
                with self.assertRaisesRegex(si.SigningError,'missing or malformed'):tools.target_identity(root/'base.zip')

if __name__=='__main__':unittest.main()
