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

if __name__=='__main__':unittest.main()
