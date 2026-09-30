import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from diamaneos_tools import product_inputs as subject
from diamaneos_tools.vendor_extract import sha
from diamaneos_tools.vendor import VendorError

class ProductInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source';(self.source/'.repo').mkdir(parents=True)
        self.vendor=self.root/'vendor';self.kernel=self.root/'kernel'
        vtree=self.vendor/'generations'/('a'*64);vtree.mkdir(parents=True)
        ktree=self.kernel/'runs/example/candidate';ktree.mkdir(parents=True)
        for tree in (vtree,ktree):
            (tree/'input').write_bytes(b'fixture');(tree/'input').chmod(0o640)
        records={'input':{'bytes':7,'sha256':sha(vtree/'input')}}
        (self.vendor/'inventories').mkdir();(self.vendor/'inventories'/('a'*64+'.json')).write_text(json.dumps(records))
        (self.vendor/'current').symlink_to('generations/'+'a'*64)
        inventory=ktree.parent/'artifacts.json';inventory.write_text(json.dumps([dict(path='input',**records['input'])]))
        (ktree.parent/'result.json').write_text(json.dumps(dict(status='PASS',inventory_sha256=sha(inventory))))
        (self.kernel/'current').symlink_to('runs/example/candidate')
    def install(self):return subject.install(self.source,self.vendor,self.kernel)
    def test_install_and_verify_existing(self):
        result=self.install();self.assertEqual('installed',result['vendor']);self.assertEqual('installed',result['kernel'])
        result=self.install();self.assertEqual('verified-existing',result['vendor'])
        self.assertTrue((self.source/'.repo/diamaneos-generated-inputs.json').is_file())
    def test_existing_edit_preserved(self):
        self.install();p=self.source/'vendor/fairphone/FP6/input';p.write_bytes(b'owner edit')
        self.assertRaises(VendorError,self.install);self.assertEqual(b'owner edit',p.read_bytes())
    def test_wrong_candidate_inventory_rejected_before_install(self):
        (self.kernel/'runs/example/artifacts.json').write_text('[]')
        self.assertRaises(ValueError,self.install);self.assertFalse((self.source/'vendor/fairphone/FP6').exists())
    def test_symlink_destination_parent_rejected(self):
        (self.source/'vendor').symlink_to(self.vendor,target_is_directory=True)
        self.assertRaises(ValueError,self.install)
    def test_copy_failure_preserves_absent_destination(self):
        with patch.object(subject.shutil,'copytree',side_effect=OSError('interrupted copy')):
            self.assertRaises(OSError,self.install)
        self.assertFalse((self.source/'vendor/fairphone/FP6').exists())
        self.assertEqual('PASS',self.install()['status'])
    def test_undeclared_payload_rejected(self):
        (self.vendor/'current/unrecorded').write_text('extra')
        self.assertRaises(VendorError,self.install)
    def test_escaped_current_pointer_rejected(self):
        (self.vendor/'current').unlink();(self.vendor/'current').symlink_to('../escape')
        self.assertRaises(ValueError,self.install)

    def test_carrier_asset_names_install_with_exact_bytes(self):
        tree=(self.vendor/'current').resolve()
        name='carrier-assets/device-carrier-config/carrier_config_carrierid_1881_遠傳電信 AT&T.xml'
        p=tree/name;p.parent.mkdir(parents=True);p.write_bytes(b'<carrier_config/>');p.chmod(0o640)
        inventory=self.vendor/'inventories'/('a'*64+'.json')
        records=json.loads(inventory.read_text());records[name]={'bytes':p.stat().st_size,'sha256':sha(p)}
        inventory.write_text(json.dumps(records))
        self.assertEqual('PASS',self.install()['status'])
        self.assertEqual(p.read_bytes(),(self.source/'vendor/fairphone/FP6'/name).read_bytes())

    def test_generated_names_reject_traversal_and_controls(self):
        for name in ('/outside','../outside','a/../b','a//b','a/./b','a/','a\\b','a\nb','a\x00b'):
            with self.subTest(name=name):
                records={name:{'bytes':7,'sha256':sha(self.vendor/'current/input')}}
                with self.assertRaisesRegex(ValueError,'invalid generated input path'):
                    subject.install_one((self.vendor/'current').resolve(),records,self.root/'destination')
                self.assertFalse((self.root/'destination').exists())

    def test_descriptor_binds_environment_recipes_and_trees(self):
        self.install()
        descriptor=json.loads((self.source/'.repo/diamaneos-generated-inputs.json').read_text())
        self.assertEqual(2,descriptor['schema_version'])
        self.assertEqual(sha(subject.DEFAULT_ENVIRONMENT),descriptor['environment_sha256'])
        self.assertEqual(subject.recipe_hashes(),descriptor['recipes'])
        self.assertEqual({'vendor','kernel'},set(descriptor['inputs']))
        self.assertEqual('runs/example/candidate',descriptor['inputs']['kernel']['run'])
        accepted=subject.verify_descriptor(self.source,descriptor['environment_sha256'])
        self.assertEqual({'vendor/fairphone/FP6','device/fairphone/FP6-kernel'},accepted)

    def test_descriptor_rejects_other_environment_stale_recipes_and_edits(self):
        self.install()
        with self.assertRaisesRegex(ValueError,'another build environment'):
            subject.verify_descriptor(self.source,'0'*64)
        with patch.object(subject,'recipe_hashes',return_value={'kernel_policy':'1'*64}):
            with self.assertRaisesRegex(ValueError,'other recipes'):
                subject.verify_descriptor(self.source)
        (self.source/'device/fairphone/FP6-kernel/input').write_bytes(b'edited!')
        with self.assertRaises(ValueError):
            subject.verify_descriptor(self.source)

    def test_descriptor_rejects_extra_file_and_legacy_record(self):
        self.install()
        extra=self.source/'vendor/fairphone/FP6/extra';extra.write_bytes(b'x');extra.chmod(0o640)
        with self.assertRaisesRegex(ValueError,'inventory mismatch'):
            subject.verify_descriptor(self.source)
        extra.unlink()
        (self.source/'.repo/diamaneos-generated-inputs.json').write_text(json.dumps({'status':'PASS'}))
        with self.assertRaisesRegex(ValueError,'predates'):
            subject.verify_descriptor(self.source)

    def test_self_consistent_unbound_tree_is_not_accepted(self):
        # Review R4: a single-file tree with a matching inventory must not pass
        # without the descriptor that binds it to the environment and recipes.
        (self.source/'vendor/fairphone/FP6').mkdir(parents=True)
        with self.assertRaises(ValueError):
            subject.verify_descriptor(self.source)

    def test_replace_moves_the_differing_tree_aside(self):
        self.install();p=self.source/'vendor/fairphone/FP6/input';p.write_bytes(b'old tree')
        result=subject.install(self.source,self.vendor,self.kernel,replace=True)
        self.assertEqual('replaced',result['vendor']);self.assertEqual('verified-existing',result['kernel'])
        self.assertEqual(b'fixture',p.read_bytes())
        self.assertEqual(b'old tree',(self.source/'.repo/diamaneos-previous-inputs/vendor/input').read_bytes())

if __name__=='__main__':unittest.main()
