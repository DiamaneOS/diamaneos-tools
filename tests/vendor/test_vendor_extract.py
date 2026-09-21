import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import vendor_extract as subject
from diamaneos_tools.vendor import VendorError


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'super.img'; self.source.write_bytes(b'authenticated sparse image')
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.pins = {'tools': {}, 'libraries': {}}
        for name in ('simg2img', 'lpunpack', 'debugfs_static'):
            p = self.bin / name; p.write_bytes(name.encode()); self.pins['tools'][name] = subject.sha(p)
        self.stock = dict(archive_sha256='0'*64, stock_build='synthetic', region='EU',
                          images=[dict(partition='super', bytes=self.source.stat().st_size, sha256=subject.sha(self.source))])
        self.data = b'selected'
        self.selection = {k:self.stock[k] for k in ('archive_sha256','stock_build','region')}
        self.selection.update(files=[dict(input='vendor/lib64/hal@1.0.so',bytes=len(self.data),sha256=hashlib.sha256(self.data).hexdigest())],notices=[],symlinks=[])
        self.output = self.root / 'output'
        self.bad_inode = False; self.fail = False; self.calls = []
    def native(self, argv, *args, cwd=None, **kwargs):
        self.calls.append(argv)
        tool = Path(argv[0]).name
        if self.fail: return {'transport':'error','stdout':b'','stderr':b''}
        text = b''
        if tool == 'lpunpack':
            data = bytearray(1082); data[1080:] = bytes.fromhex('53ef')
            (Path(argv[-1])/'vendor_a.img').write_bytes(data)
        elif tool == 'debugfs_static':
            command = argv[2]
            if command.startswith('stat '): text = b'Type: directory' if self.bad_inode else b'Type: regular'
            elif command.startswith('dump '): (cwd / command.split()[-1]).write_bytes(self.data)
        return {'transport':'ok','stdout':text,'stderr':b''}
    def extract(self):
        with patch.object(subject.process,'run',side_effect=self.native):
            return subject.extract(self.source,self.bin,self.output,self.stock,self.selection,self.pins)
    def test_real_boundary_repeat_and_selected_bytes(self):
        first=self.extract(); count=len(self.calls)
        self.assertEqual(self.data,(self.output/'current/vendor/lib64/hal@1.0.so').read_bytes())
        self.assertEqual(first,self.extract());self.assertEqual(count,len(self.calls))
        self.assertEqual(0,first['device_commands_executed'])
    def test_wrong_stock_and_tool_reject_before_execution(self):
        self.source.write_bytes(b'wrong');self.assertRaises(VendorError,self.extract);self.assertFalse(self.calls)
        self.source.write_bytes(b'authenticated sparse image');(self.bin/'lpunpack').write_bytes(b'wrong')
        self.assertRaises(VendorError,self.extract);self.assertFalse(self.calls)
    def test_wrong_inode_and_content_do_not_publish(self):
        self.bad_inode=True;self.assertRaises(VendorError,self.extract);self.assertFalse((self.output/'current').exists())
        self.bad_inode=False;self.data=b'bad';self.assertRaises(VendorError,self.extract);self.assertFalse((self.output/'current').exists())
    def test_failed_new_generation_preserves_previous(self):
        self.extract();previous=os.readlink(self.output/'current')
        self.selection['files'][0]['sha256']='1'*64
        self.assertRaises(VendorError,self.extract);self.assertEqual(previous,os.readlink(self.output/'current'))
    def test_edited_generation_rejected(self):
        self.extract();(self.output/'current/vendor/lib64/hal@1.0.so').write_bytes(b'bad')
        self.assertRaises(VendorError,self.extract)
    def test_unsafe_paths_and_wrong_partition(self):
        for name in ('../escape','vendor/../../escape','/absolute','vendor/a\ncommand','userdata/file'):
            with self.subTest(name=name):
                self.selection['files'][0]['input']=name;self.assertRaises(VendorError,self.extract)
        self.assertFalse(self.calls)
    def test_tool_failure_does_not_publish(self):
        self.fail=True;self.assertRaises(VendorError,self.extract);self.assertFalse((self.output/'current').exists())
    def test_stock_identity_mismatch(self):
        self.selection['stock_build']='other';self.assertRaises(VendorError,self.extract);self.assertFalse(self.calls)

if __name__ == '__main__':unittest.main()
