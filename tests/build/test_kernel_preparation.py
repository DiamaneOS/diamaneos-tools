import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from diamaneos_tools import kernel


class KernelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reference=self.root/'reference';self.repo=self.reference/'kernel_platform/common'
        self.repo.mkdir(parents=True)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        (self.repo/'file').write_text('base\n');self.git('add','file');self.git('-c','commit.gpgsign=false','commit','-qm','base')
        base=self.git('rev-parse','HEAD')
        (self.repo/'file').write_text('derived\n');self.git('commit','-qam','derived','--no-gpg-sign')
        revision=self.git('rev-parse','HEAD')
        diff=subprocess.check_output(['git','-C',str(self.repo),'diff','--full-index',base,revision])
        self.plan={'source_url':'https://example.invalid/','projects':[dict(project='kernel/common',path='kernel_platform/common',revision=base,linkfiles=[dict(src='.',dest='kernel_platform/common-link')])]}
        self.changes=[dict(path='kernel_platform/common',base_revision=base,derived_revision=revision,repository='https://example.invalid/common',canonical_diff_sha256=hashlib.sha256(diff).hexdigest(),changed_files=['file'])]
        self.adaptation={'excluded_linkfiles':[],'generated_links':[]}
        self.workspace=self.root/'workspace'
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],text=True,stderr=subprocess.PIPE).strip()
    def prepare(self):
        with patch.object(kernel,'configuration',return_value=(self.plan,self.changes,self.adaptation)):
            return kernel.prepare(self.workspace,self.reference)
    def test_new_shared_clone_and_repeat_with_directory_link(self):
        result=self.prepare();self.assertEqual('PASS',result['status'])
        self.assertEqual('derived\n',(self.workspace/'kernel_platform/common/file').read_text())
        self.assertEqual(self.changes[0]['derived_revision'],kernel.git(self.workspace/'kernel_platform/common','rev-parse','HEAD'))
        self.assertTrue((self.workspace/'kernel_platform/common-link').is_symlink())
        self.assertEqual(result,self.prepare())
    def test_standalone_fetch_without_reference(self):
        self.changes[0]['repository'] = str(self.repo)
        with patch.object(kernel,'configuration',return_value=(self.plan,self.changes,self.adaptation)):
            result = kernel.prepare(self.workspace)
        self.assertFalse(result['local_object_reference'])
        self.assertEqual('derived\n',(self.workspace/'kernel_platform/common/file').read_text())
    def test_dirty_source_preserved(self):
        self.prepare();p=self.workspace/'kernel_platform/common/file';p.write_text('owner edit\n')
        self.assertRaisesRegex(kernel.KernelError,'tracked source changes',self.prepare)
        self.assertEqual('owner edit\n',p.read_text())
    def test_untracked_source_rejected(self):
        self.prepare();(self.workspace/'kernel_platform/common/unowned').write_text('extra')
        self.assertRaisesRegex(kernel.KernelError,'untracked source input',self.prepare)
    def test_patch_digest_mismatch_rejected(self):
        self.changes[0]['canonical_diff_sha256']='0'*64
        self.assertRaisesRegex(kernel.KernelError,'patch bytes',self.prepare)
        self.assertFalse((self.workspace/'preparation.json').exists())
    def test_link_escape_and_occupied_destination_rejected(self):
        self.prepare();p=self.workspace/'kernel_platform/common-link';p.unlink();p.symlink_to(self.root)
        self.assertRaisesRegex(kernel.KernelError,'link differs',self.prepare)
    def test_undeclared_missing_link_rejected(self):
        self.plan['projects'][0]['linkfiles'].append(dict(src='missing',dest='kernel_platform/missing'))
        self.assertRaisesRegex(kernel.KernelError,'missing or escaped',self.prepare)
    def test_workspace_symlink_rejected(self):
        self.workspace.symlink_to(self.reference,target_is_directory=True)
        self.assertRaisesRegex(kernel.KernelError,'symlink',self.prepare)

if __name__=='__main__':unittest.main()
