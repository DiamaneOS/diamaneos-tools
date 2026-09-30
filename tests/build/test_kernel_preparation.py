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
    def test_link_exclusion_follows_the_upstream_revision_of_a_fork(self):
        # The excluded link names the plan (upstream) revision; the workspace holds the fork's commit.
        self.plan['projects'][0]['linkfiles'].append(dict(src='legacy.sh',dest='kernel_platform/legacy.sh'))
        self.adaptation['excluded_linkfiles'].append(dict(project='kernel/common',revision=self.changes[0]['base_revision'],
                                                          src='legacy.sh',dest='kernel_platform/legacy.sh'))
        self.assertEqual('PASS',self.prepare()['status'])
        self.assertFalse((self.workspace/'kernel_platform/legacy.sh').exists())

    def test_undeclared_missing_link_rejected(self):
        self.plan['projects'][0]['linkfiles'].append(dict(src='missing',dest='kernel_platform/missing'))
        self.assertRaisesRegex(kernel.KernelError,'missing or escaped',self.prepare)
    def test_workspace_symlink_rejected(self):
        self.workspace.symlink_to(self.reference,target_is_directory=True)
        self.assertRaisesRegex(kernel.KernelError,'symlink',self.prepare)

if __name__=='__main__':unittest.main()


class KernelDenyListTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_recipe_keeps_denied_modules_out(self):
        denied = kernel.denied_modules(self.recipe)
        for name in ('can.ko', 'nfc.ko', 'bluetooth.ko', 'focaltech_fts.ko', 'f_fs_ipc_log.ko'):
            self.assertIn(name, denied)
        # Kept on purpose: USB waits for the EUD extcon, glink_probe imports
        # qcom_glink_spss, KGSL imports coresight and the audio machine driver wcd937x.
        for name in ('eud.ko', 'qcom_glink_spss.ko', 'coresight.ko', 'wcd937x_dlkm.ko'):
            self.assertNotIn(name, denied)
            self.assertTrue(any(name in names for names in self.recipe['partitions'].values()))

    def test_regenerated_list_with_a_denied_module_is_rejected(self):
        for key in ('partitions', 'load_lists'):
            with self.subTest(key=key):
                recipe = copy.deepcopy(self.recipe)
                target = 'system_dlkm' if key == 'partitions' else 'system_dlkm/modules.load'
                recipe[key][target].append('can.ko')
                self.assertRaisesRegex(kernel.KernelError, 'denied module in the packaging recipe: can.ko',
                                       kernel.denied_modules, recipe)

    def test_dash_and_underscore_spellings_match(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['partitions']['vendor_dlkm'].append('snd_soc_hdmi_codec.ko')
        self.assertRaisesRegex(kernel.KernelError, 'snd_soc_hdmi_codec.ko', kernel.denied_modules, recipe)
        recipe = copy.deepcopy(self.recipe)
        recipe['denied_modules'].append({'modules': ['snd_soc_hdmi_codec.ko'], 'reason': 'again'})
        self.assertRaisesRegex(kernel.KernelError, 'duplicate denied module', kernel.denied_modules, recipe)

    def test_every_denied_group_states_a_reason(self):
        for group in ({'modules': ['x.ko'], 'reason': ' '}, {'modules': [], 'reason': 'r'},
                      {'modules': ['x.ko'], 'reason': 'r', 'extra': 1}, {'modules': ['../x.ko'], 'reason': 'r'}):
            with self.subTest(group=group):
                recipe = copy.deepcopy(self.recipe)
                recipe['denied_modules'] = [group]
                self.assertRaises(kernel.KernelError, kernel.denied_modules, recipe)


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


class KernelSymbolRuleTests(unittest.TestCase):
    def setUp(self):
        self.recipe = kernel.load_json(kernel.ROOT / 'config/fp6-kernel-packaging.json')

    def test_committed_rules_parse(self):
        self.assertEqual({'register_kretprobe': ['dwc3-msm.ko']}, kernel.import_allowlist(self.recipe))
        self.assertEqual(['param_name_len'], kernel.forbidden_symbols(self.recipe))

    def test_import_allowlist_requires_exact_importers(self):
        undefined = {'dwc3-msm.ko': {'register_kretprobe', 'printk'}, 'other.ko': {'printk'}}
        modules = {name: name for name in undefined}
        kernel.check_module_imports(modules, {'register_kretprobe': ['dwc3-msm.ko']}, undefined.get)
        undefined['other.ko'].add('register_kretprobe')
        with self.assertRaisesRegex(kernel.KernelError, 'other.ko'):
            kernel.check_module_imports(modules, {'register_kretprobe': ['dwc3-msm.ko']}, undefined.get)
        with self.assertRaisesRegex(kernel.KernelError, 'none'):
            kernel.check_module_imports(modules, {'missing_symbol': ['dwc3-msm.ko']}, undefined.get)

    def test_nm_output_and_system_map_parsing(self):
        self.assertEqual({'register_kretprobe', '__stack_chk_fail'},
                         kernel.undefined_symbols('                 U register_kretprobe\n'
                                                  '                 U __stack_chk_fail\n'))
        kernel.check_forbidden_symbols('ffffffc080000000 T _text\n', ['param_name_len'])
        with self.assertRaisesRegex(kernel.KernelError, 'param_name_len'):
            kernel.check_forbidden_symbols('ffffffc080001000 t param_name_len\n', ['param_name_len'])

    def test_rules_need_reasons_and_safe_names(self):
        for rules in ({'register_kretprobe': {'modules': ['x.ko'], 'reason': ' '}},
                      {'bad symbol': {'modules': ['x.ko'], 'reason': 'r'}},
                      {'s': {'modules': ['../x.ko'], 'reason': 'r'}}):
            with self.subTest(rules=rules):
                self.assertRaises(kernel.KernelError, kernel.import_allowlist, {'module_import_allowlist': rules})
        self.assertRaises(kernel.KernelError, kernel.forbidden_symbols,
                          {'forbidden_symbols': [{'symbol': 'x', 'reason': ''}]})

    def test_clang_comes_from_the_pinned_build_constants(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'common').mkdir()
            (work / 'common/build.config.constants').write_text('BRANCH=android14-6.1\nCLANG_VERSION=r487747c\n')
            with self.assertRaisesRegex(kernel.KernelError, 'clang-r487747c'):
                kernel.clang_bin(work)
            (work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin').mkdir(parents=True)
            (work / 'prebuilts/clang/host/linux-x86/clang-r999/bin').mkdir(parents=True)
            self.assertEqual(work / 'prebuilts/clang/host/linux-x86/clang-r487747c/bin', kernel.clang_bin(work))
