"""Selected-file publication exercises real component policy and filesystem I/O."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import components, vendor, vendor_files

TOOLS = Path(__file__).resolve().parents[2]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SelectedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.output = self.root / 'output'
        self.data = b'Synthetic camera input; not a real hardware artifact.'
        self.notice = b'Synthetic notice for test input.\n'
        self.path = 'vendor/lib64/hw/camera.fixture.so'
        for name, data in ((self.path, self.data), ('vendor/etc/NOTICE.txt', self.notice)):
            p = self.inputs / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        model_bytes = (TOOLS / 'config/components.json').read_bytes()
        sources_bytes = (TOOLS / 'config/fp6-sources.json').read_bytes()
        self.kwargs = {'model': components.loads(model_bytes),
                       'sources': components.loads(sources_bytes),
                       'environment': components.load_json(TOOLS / 'config/build-environment.json'),
                       'model_sha256': sha(model_bytes), 'source_sha256': sha(sources_bytes)}
        stock = self.kwargs['model']['fp6_model']['inputs']['selected_stock']
        self.recipe = {'schema_version': 1, 'stock_build': stock['build'], 'region': stock['region'],
                       'archive_sha256': stock['factory_sha256'], 'model_sha256': sha(model_bytes),
                       'notices': [{'input': 'vendor/etc/NOTICE.txt', 'sha256': sha(self.notice), 'bytes': len(self.notice)}],
                       'files': [{'input': self.path, 'path': self.path, 'sha256': sha(self.data), 'bytes': len(self.data),
                                  'component_id': 'camera-stack', 'inventory_ref': 'userspace_hal_families:camera',
                                  'dependencies': [], 'notices': [sha(self.notice)], 'purpose': 'Synthetic I/O fixture',
                                  'metadata': {'uid': 0, 'gid': 2000, 'mode': 0o755, 'selinux': 'u:object_r:vendor_file:s0',
                                               'capabilities_hex': ''}}]}

    def generate(self, **kwargs):
        return vendor_files.generate(self.recipe, self.inputs, self.output, **self.kwargs, **kwargs)

    def test_repeat_authenticates_and_preserves_metadata_without_host_privileges(self):
        first = self.generate()
        current = self.output / 'current'
        before = current.lstat().st_mtime_ns
        self.assertEqual(first, self.generate())
        self.assertEqual(before, current.lstat().st_mtime_ns)
        self.assertEqual(self.data, (current / 'files' / self.path).read_bytes())
        self.assertEqual(0o640, (current / 'files' / self.path).stat().st_mode & 0o777)
        manifest = json.loads((current / 'manifest.json').read_bytes())
        self.assertEqual(self.recipe['files'][0]['metadata'], manifest['recipe']['files'][0]['metadata'])
        closure = json.loads((current / 'component-closure.json').read_bytes())
        self.assertEqual([], components.validate_closure(self.kwargs['model'], closure, model_sha256=self.kwargs['model_sha256']))
        self.assertFalse(first['product_graph_validated'])
        (self.inputs / self.path).write_bytes(b'x' * len(self.data))
        with self.assertRaises(vendor.VendorError):
            self.generate()
        self.assertEqual(before, current.lstat().st_mtime_ns)

    def test_bad_selection_preserves_previous_tree(self):
        self.generate()
        previous = os.readlink(self.output / 'current')
        original = copy.deepcopy(self.recipe)
        for mutation in ('missing', 'wrong-hash', 'wrong-build', 'dependency', 'owner', 'notice', 'traversal', 'duplicate', 'state'):
            self.recipe = copy.deepcopy(original)
            f = self.recipe['files'][0]
            if mutation == 'missing': f['input'] = 'vendor/missing'
            if mutation == 'wrong-hash': f['sha256'] = '0' * 64
            if mutation == 'wrong-build': self.recipe['stock_build'] = 'WRONG'
            if mutation == 'dependency': f['dependencies'] = ['vendor/lib64/missing.so']
            if mutation == 'owner': f['component_id'] = 'missing'
            if mutation == 'notice': f['notices'] = ['0' * 64]
            if mutation == 'traversal': f['input'] = 'vendor/../secret'
            if mutation == 'duplicate': self.recipe['files'].append(copy.deepcopy(f))
            if mutation == 'state': f['input'] = 'persist/key'
            with self.subTest(mutation=mutation), self.assertRaises((vendor.VendorError, OSError)):
                self.generate()
            self.assertEqual(previous, os.readlink(self.output / 'current'))
            self.assertEqual(self.data, (self.output / 'current/files' / self.path).read_bytes())
            self.assertFalse(list((self.output / 'generations').glob('.generate-*')))

    def test_private_allowance_fails_public_validation_before_publication(self):
        with self.assertRaises(vendor.VendorError): self.generate(public=True)
        self.assertFalse(self.output.exists())

    def test_missing_notice_and_altered_existing_output_fail(self):
        self.generate()
        (self.inputs / 'vendor/etc/NOTICE.txt').unlink()
        with self.assertRaises(OSError): self.generate()
        (self.inputs / 'vendor/etc/NOTICE.txt').write_bytes(self.notice)
        (self.output / 'current/files' / self.path).write_bytes(b'x' * len(self.data))
        with self.assertRaises(vendor.VendorError): self.generate()

    def test_symlink_ancestor_leaf_and_fifo_rejected(self):
        directory = self.inputs / 'vendor/lib64'
        moved = self.inputs / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved)
        with self.assertRaises(OSError): self.generate()
        directory.unlink()
        moved.rename(directory)
        file = self.inputs / self.path
        file.unlink()
        file.symlink_to(self.inputs / 'vendor/etc/NOTICE.txt')
        with self.assertRaises(OSError): self.generate()
        file.unlink()
        os.mkfifo(file)
        with self.assertRaises(vendor.VendorError): self.generate()

    def test_lock_and_interrupted_copy_do_not_publish(self):
        import fcntl
        from unittest.mock import patch
        self.output.mkdir()
        with (self.output / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.generate()
        with patch.object(vendor_files, 'copy_verified', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): self.generate()
        self.assertFalse((self.output / 'current').exists())
        self.assertFalse(list((self.output / 'generations').glob('.generate-*')))

    def test_metadata_change_changes_generation(self):
        first = self.generate()
        self.recipe['files'][0]['metadata']['mode'] = 0o644
        second = self.generate()
        self.assertNotEqual(first['recipe_sha256'], second['recipe_sha256'])

    def test_cli_generation_and_redacted_failure(self):
        import subprocess
        recipe = self.root / 'selection.json'
        recipe.write_text(json.dumps(self.recipe))
        command = [sys.executable, str(TOOLS / 'bin/diamaneos'), 'vendor', 'generate',
                   '--recipe', str(recipe), '--inputs', str(self.inputs), '--output', str(self.output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('PASS', json.loads(result.stdout)['status'])
        self.recipe['files'][0]['input'] = 'vendor/private-identifier/missing'
        recipe.write_text(json.dumps(self.recipe))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertNotIn('private-identifier', result.stderr)
        self.assertNotIn(str(self.inputs), result.stderr)

    def test_existing_mode_and_unexpected_directory_rejected(self):
        self.generate()
        file = self.output / 'current/files' / self.path
        file.chmod(0o755)
        with self.assertRaises(vendor.VendorError): self.generate()
        file.chmod(0o640)
        (self.output / 'current/unexpected').mkdir()
        with self.assertRaises(vendor.VendorError): self.generate()

    def test_stale_model_and_wrong_archive_fail_before_generation(self):
        for field in ('model_sha256', 'archive_sha256'):
            original = self.recipe[field]
            self.recipe[field] = '0' * 64
            with self.subTest(field=field), self.assertRaises(vendor.VendorError): self.generate()
            self.assertFalse(self.output.exists())
            self.recipe[field] = original

    def test_file_directory_collision_cannot_hide_behind_sort_order(self):
        first = self.recipe['files'][0]
        first['path'] = 'vendor/lib'
        second = copy.deepcopy(first)
        second.update(path='vendor/lib-extra', input='vendor/second')
        third = copy.deepcopy(first)
        third.update(path='vendor/lib/child', input='vendor/third')
        self.recipe['files'] += [second, third]
        with self.assertRaisesRegex(vendor.VendorError, 'conflicting'):
            self.generate()
        self.assertFalse(self.output.exists())
