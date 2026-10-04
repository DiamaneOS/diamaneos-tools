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
        self.vendor=self.root/'vendor'
        vtree=self.vendor/'generations'/('a'*64);vtree.mkdir(parents=True)
        (vtree/'input').write_bytes(b'fixture');(vtree/'input').chmod(0o640)
        # The generations record the recipes they were made from, as the real
        # generators do; the fixture uses this checkout's recipes.
        self.recipes=subject.current_recipes()
        provenance=vtree/'provenance.json'
        provenance.write_text(json.dumps({'recipe_sha256':self.recipes['vendor_files'],
                                          'elf_selection_sha256':self.recipes['vendor_elf']}));provenance.chmod(0o640)
        records={name:{'bytes':(vtree/name).stat().st_size,'sha256':sha(vtree/name)} for name in ('input','provenance.json')}
        (self.vendor/'inventories').mkdir();(self.vendor/'inventories'/('a'*64+'.json')).write_text(json.dumps(records))
        (self.vendor/'current').symlink_to('generations/'+'a'*64)
    def install(self):return subject.install(self.source,self.vendor)
    def test_install_and_verify_existing(self):
        result=self.install();self.assertEqual('installed',result['vendor']);self.assertNotIn('kernel',result)
        result=self.install();self.assertEqual('verified-existing',result['vendor'])
        self.assertTrue((self.source/'.repo/diamaneos-generated-inputs.json').is_file())
    def test_existing_edit_preserved(self):
        self.install();p=self.source/'vendor/fairphone/FP6/input';p.write_bytes(b'owner edit')
        self.assertRaises(VendorError,self.install);self.assertEqual(b'owner edit',p.read_bytes())
    def test_wrong_inventory_rejected_before_install(self):
        (self.vendor/'inventories'/('a'*64+'.json')).write_text('{}')
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
        self.assertEqual(3,descriptor['schema_version'])
        self.assertEqual(sha(subject.DEFAULT_ENVIRONMENT),descriptor['environment_sha256'])
        self.assertEqual(subject.current_recipes(),descriptor['recipes'])
        self.assertEqual({'vendor_files','vendor_elf'},set(descriptor['recipes']))
        self.assertEqual({'vendor'},set(descriptor['inputs']))
        accepted=subject.verify_descriptor(self.source,descriptor['environment_sha256'])
        self.assertEqual({'vendor/fairphone/FP6'},accepted)
        self.assertFalse((self.source/'device/fairphone/FP6-kernel').exists())

    def test_descriptor_rejects_other_environment_stale_recipes_and_edits(self):
        self.install()
        with self.assertRaisesRegex(ValueError,'another build environment'):
            subject.verify_descriptor(self.source,'0'*64)
        changed=dict(self.recipes,vendor_elf='1'*64)
        with patch.object(subject,'current_recipes',return_value=changed):
            with self.assertRaisesRegex(ValueError,'other recipes .vendor_elf.'):
                subject.verify_descriptor(self.source)
        (self.source/'vendor/fairphone/FP6/input').write_bytes(b'edited!')
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
        # A single-file tree with a matching inventory must not pass
        # without the descriptor that binds it to the environment and recipes.
        (self.source/'vendor/fairphone/FP6').mkdir(parents=True)
        with self.assertRaises(ValueError):
            subject.verify_descriptor(self.source)

    def test_install_refuses_generations_without_or_with_other_recipes(self):
        # Through install(): a self-consistent tree whose generation
        # does not record the current recipes is refused before anything is installed.
        provenance=(self.vendor/'current').resolve()/'provenance.json'
        provenance.write_text(json.dumps({'recipe_sha256':'0'*64,'elf_selection_sha256':self.recipes['vendor_elf']}))
        inventory=self.vendor/'inventories'/('a'*64+'.json')
        records=json.loads(inventory.read_text());records['provenance.json']={'bytes':provenance.stat().st_size,'sha256':sha(provenance)}
        inventory.write_text(json.dumps(records))
        with self.assertRaisesRegex(ValueError,'other recipes .vendor_files.'):
            self.install()
        self.assertFalse((self.source/'vendor/fairphone/FP6').exists())
        provenance.unlink();del records['provenance.json'];inventory.write_text(json.dumps(records))
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.source/'vendor/fairphone/FP6').exists())

    def test_replace_moves_the_differing_tree_aside(self):
        self.install();p=self.source/'vendor/fairphone/FP6/input';p.write_bytes(b'old tree')
        result=subject.install(self.source,self.vendor,replace=True)
        self.assertEqual('replaced',result['vendor'])
        self.assertEqual(b'fixture',p.read_bytes())
        self.assertEqual(b'old tree',(self.source/'.repo/diamaneos-previous-inputs/vendor/input').read_bytes())

    def test_stale_generated_trees_move_aside(self):
        self.install()
        self.assertEqual([],subject.retire_stale(self.source))
        (self.source/'vendor/fairphone/FP6/input').write_bytes(b'edited!')
        self.assertEqual(['vendor'],subject.retire_stale(self.source))
        self.assertFalse((self.source/'vendor/fairphone/FP6').exists())
        self.assertFalse((self.source/'vendor').exists())
        self.assertFalse((self.source/'.repo/diamaneos-generated-inputs.json').exists())
        self.assertEqual(b'edited!',(self.source/'.repo/diamaneos-previous-inputs/vendor/input').read_bytes())
        self.assertEqual([],subject.retire_stale(self.source))

    def test_kernel_tree_of_earlier_tools_moves_aside_but_the_manifest_project_stays(self):
        # Earlier tools installed a generated kernel where the manifest now
        # has the kernel prebuilts project; the old descriptor names both.
        legacy=self.source/'device/fairphone/FP6-kernel';legacy.mkdir(parents=True);(legacy/'Image').write_bytes(b'old')
        (self.source/'.repo/diamaneos-generated-inputs.json').write_text(json.dumps({'schema_version':2,'status':'PASS'}))
        self.assertEqual(['kernel'],subject.retire_stale(self.source))
        self.assertFalse(legacy.exists());self.assertFalse((self.source/'device').exists())
        self.assertEqual(b'old',(self.source/'.repo/diamaneos-previous-inputs/kernel/Image').read_bytes())
        self.assertFalse((self.source/'.repo/diamaneos-generated-inputs.json').exists())
        legacy.mkdir(parents=True);(legacy/'.git').write_text('gitdir: ../../../.repo/projects/x.git\n')
        self.install()
        self.assertEqual([],subject.retire_stale(self.source))
        self.assertTrue((legacy/'.git').exists())

if __name__=='__main__':unittest.main()
