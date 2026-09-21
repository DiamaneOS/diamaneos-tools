import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from diamaneos_tools import vendor


class StockStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'factory.zip'
        self.output = self.root / 'output'
        self.data = b'synthetic partition bytes'
        self.make_archive()

    def make_archive(self, kind='regular'):
        with zipfile.ZipFile(self.archive, 'w') as z:
            info = zipfile.ZipInfo('factory/images/boot.img')
            info.external_attr = (0o120777 if kind == 'symlink' else 0o100644) << 16
            z.writestr(info, self.data)
            z.writestr('factory/images/userdata.img', b'must not be extracted')
        self.recipe = {'schema_version': 1, 'stock_build': 'SYNTHETIC', 'region': 'EU',
                       'archive_sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                       'archive_bytes': self.archive.stat().st_size,
                       'images': [{'partition': 'boot', 'member': 'factory/images/boot.img',
                                   'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}]}

    def stage(self, recipe=None):
        return vendor.stage(recipe or self.recipe, self.archive, self.output)

    def test_repeat_is_identical_and_excludes_undeclared_state(self):
        first = self.stage()
        current = self.output / 'current'
        before = current.lstat().st_mtime_ns
        second = self.stage()
        self.assertEqual(first, second)
        self.assertEqual(before, current.lstat().st_mtime_ns)
        self.assertEqual({'boot.img', 'provenance.json'}, {p.name for p in current.iterdir()})
        self.assertEqual(self.data, (current / 'boot.img').read_bytes())
        self.assertFalse(first['vendor_tree_generated'])

    def test_bad_inputs_preserve_previous_generation(self):
        self.stage()
        previous = os.readlink(self.output / 'current')
        for change in ('archive', 'image', 'missing', 'prohibited', 'path', 'duplicate'):
            recipe = copy.deepcopy(self.recipe)
            if change == 'archive': recipe['archive_sha256'] = '0' * 64
            if change == 'image': recipe['images'][0]['sha256'] = '0' * 64
            if change == 'missing': recipe['images'][0]['member'] = 'missing/boot.img'
            if change == 'prohibited': recipe['images'][0]['partition'] = 'userdata'
            if change == 'path': recipe['images'][0]['member'] = '../boot.img'
            if change == 'duplicate': recipe['images'].append(recipe['images'][0])
            with self.subTest(change=change), self.assertRaises(vendor.VendorError):
                self.stage(recipe)
            self.assertEqual(previous, os.readlink(self.output / 'current'))
            self.assertEqual(self.data, (self.output / 'current/boot.img').read_bytes())
            self.assertFalse(list((self.output / 'generations').glob('.stage-*')))

    def test_authentication_precedes_output(self):
        self.recipe['archive_sha256'] = '0' * 64
        with self.assertRaises(vendor.VendorError): self.stage()
        self.assertFalse(self.output.exists())

    def test_archive_symlink_and_output_symlink_rejected(self):
        self.make_archive('symlink')
        with self.assertRaises(vendor.VendorError): self.stage()
        self.make_archive()
        other = self.root / 'other'; other.mkdir()
        self.output.symlink_to(other)
        with self.assertRaises(vendor.VendorError): self.stage()
        self.assertEqual([], list(other.iterdir()))

    def test_existing_generation_tampering_rejected(self):
        self.stage()
        (self.output / 'current/boot.img').write_bytes(b'changed')
        with self.assertRaises(vendor.VendorError): self.stage()

    def test_wrong_type_and_bounds_rejected(self):
        for field,value in [('schema_version', True), ('archive_bytes', True), ('archive_bytes', 2**50)]:
            recipe = copy.deepcopy(self.recipe); recipe[field] = value
            with self.subTest(field=field,value=value), self.assertRaises(vendor.VendorError):
                vendor.validate_recipe(recipe)

    def test_lock_prevents_concurrent_publication(self):
        import fcntl
        self.output.mkdir()
        with (self.output / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.stage()
        self.assertFalse((self.output / 'current').exists())

    def test_cli_reports_failure_without_private_values(self):
        import contextlib,io,json
        self.recipe['images'][0]['member'] = '../private-device-id/boot.img'
        path = self.root / 'recipe.json'; path.write_text(json.dumps(self.recipe))
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = vendor.main(['stage','--recipe',str(path),'--archive',str(self.archive),'--output',str(self.output)])
        self.assertEqual(2,status)
        self.assertNotIn('private-device-id',error.getvalue())
        self.assertFalse(self.output.exists())
