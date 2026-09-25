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
    def test_missing_verification_tools_reject_before_workspace_changes(self):
        with patch.dict('os.environ', {'PATH': ''}):
            with self.assertRaisesRegex(kernel.KernelError, 'missing from PATH: modinfo, modprobe'):
                kernel.build(self.workspace, 1, 60)
        self.assertFalse(self.workspace.exists())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reference=self.root/'reference';self.repo=self.reference/'kernel_platform/common'
        self.repo.mkdir(parents=True)
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        (self.repo/'file').write_text('base\n');self.git('add','file');self.git('-c','commit.gpgsign=false','commit','-qm','base')
        base=self.git('rev-parse','HEAD')
        (self.repo/'file').write_text('derived\n');self.git('commit','-qam','derived','--no-gpg-sign')
        revision=self.git('rev-parse','HEAD')
        diff=kernel.canonical_diff(subprocess.check_output(['git','-C',str(self.repo),'diff','--full-index',base,revision]))
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
    def test_large_downstream_patch_is_verified(self):
        # A recorded ABI definition makes a patch diff larger than the default capture bound.
        base=self.changes[0]['base_revision']
        (self.repo/'abi.stg').write_text('symbol line\n'*60000);self.git('add','abi.stg')
        self.git('commit','-qm','record abi','--no-gpg-sign');revision=self.git('rev-parse','HEAD')
        diff=kernel.canonical_diff(subprocess.check_output(['git','-C',str(self.repo),'diff','--full-index',base,revision]))
        self.assertGreater(len(diff),262144)
        self.changes[0].update(derived_revision=revision,canonical_diff_sha256=hashlib.sha256(diff).hexdigest(),changed_files=['abi.stg','file'])
        self.assertEqual('PASS',self.prepare()['status'])
    def test_canonical_diff_ignores_hunk_function_context(self):
        # Git versions name the enclosing function differently; the change is the same.
        a = b'@@ -10,7 +10,8 @@ static int probe(struct device *dev)\n-x\n+y\n'
        b = b'@@ -10,7 +10,8 @@ struct foo {\n-x\n+y\n'
        self.assertEqual(kernel.canonical_diff(a), kernel.canonical_diff(b))
        self.assertEqual(b'@@ -1 +1 @@\n+@@ -2 +2 @@ in content\n',
                         kernel.canonical_diff(b'@@ -1 +1 @@ ctx\n+@@ -2 +2 @@ in content\n'))

    def test_file_list_bound_by_digest(self):
        # Upstream merges record a digest of the changed file list instead of the list.
        del self.changes[0]['changed_files']
        self.changes[0]['changed_files_sha256'] = hashlib.sha256(b'file\n').hexdigest()
        self.assertEqual('PASS', self.prepare()['status'])
    def test_file_list_digest_mismatch_rejected(self):
        del self.changes[0]['changed_files']
        self.changes[0]['changed_files_sha256'] = hashlib.sha256(b'other\n').hexdigest()
        self.assertRaisesRegex(kernel.KernelError, 'file set differs', self.prepare)
    def test_unpatched_project_fetches_from_its_own_url(self):
        # A project taken unmodified from another upstream (GrapheneOS common) names its URL.
        self.plan['projects'][0].update(url=str(self.repo), revision=self.changes[0]['derived_revision'])
        with patch.object(kernel,'configuration',return_value=(self.plan,[],self.adaptation)):
            result = kernel.prepare(self.workspace)
        self.assertEqual('PASS', result['status'])
        self.assertEqual('derived\n',(self.workspace/'kernel_platform/common/file').read_text())
    def test_reference_objects_are_shared_from_a_detached_workspace(self):
        # A reference prepared by tools has no branches, only a detached checkout.
        self.git('checkout','-q','--detach'); self.git('branch','-D','master' if 'master' in self.git('branch') else 'main')
        result=self.prepare()
        self.assertTrue(result['local_object_reference'])
        alternates=(self.workspace/'kernel_platform/common/.git/objects/info/alternates').read_text()
        self.assertIn(str(self.repo/'.git/objects'),alternates)

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


class KernelPackagingBlocklistTests(unittest.TestCase):
    def test_first_stage_ramdisk_uses_the_vendor_blocklist(self):
        recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')
        blocklists = recipe['blocklists']
        self.assertEqual(blocklists['vendor_boot/modules.blocklist'],
                         blocklists['vendor_dlkm/modules.blocklist'])
        self.assertIn('blocklist llcc_perfmon\n', blocklists['vendor_boot/modules.blocklist'])
        recovery = recipe['load_lists']['vendor_boot/modules.load.recovery']
        self.assertIn('llcc_perfmon.ko', recovery)

    def test_rendered_board_config_installs_ramdisk_blocklist(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            merged = temp / 'merged'; merged.mkdir()
            (merged / 'dtbo.img').write_bytes(b'dtbo')
            image = temp / 'Image'; image.write_bytes(b'kernel')
            recipe = {'partitions': {'vendor_boot': [], 'vendor_dlkm': [], 'system_dlkm': []},
                      'load_lists': {},
                      'blocklists': {'vendor_boot/modules.blocklist': 'blocklist a\n',
                                     'vendor_dlkm/modules.blocklist': 'blocklist a\n'}}
            candidate = temp / 'candidate'
            kernel.render_package(candidate, {}, merged, image, recipe, temp / 'strip', temp)
            board = (candidate / 'BoardConfigKernel.mk').read_text()
            self.assertIn('BOARD_VENDOR_RAMDISK_KERNEL_MODULES_BLOCKLIST_FILE := '
                          '$(FP6_KERNEL_PATH)/vendor_boot-modules.blocklist\n', board)
            self.assertIn('BOARD_VENDOR_KERNEL_MODULES_BLOCKLIST_FILE := '
                          '$(FP6_KERNEL_PATH)/vendor_dlkm-modules.blocklist\n', board)
            self.assertEqual((candidate / 'vendor_boot-modules.blocklist').read_text(), 'blocklist a\n')
