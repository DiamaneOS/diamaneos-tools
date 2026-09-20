"""AVB archive context and emitted-role coverage at the runner boundary."""
from pathlib import Path
import json
import runpy
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
RUNNER = runpy.run_path(str(ROOT / 'deploy/builder/run-dummy-signing-qualification'))


class AvbImageTest(unittest.TestCase):
    def source(self, root, profile, *, no_boot=False, missing=None):
        target = root / f'{profile}.zip'
        images = {'boot', 'vbmeta', 'system', 'product'}
        if no_boot:
            images.remove('boot')
        if missing:
            images.discard(missing)
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('META/misc_info.txt',
                ('no_boot=true\n' if no_boot else '') +
                'avb_boot_rollback_index_location=2\n'
                'avb_system_rollback_index_location=1\n')
            for name in images:
                archive.writestr(f'IMAGES/{name}.img', name.encode())
        inventory = {'status': 'PASS', 'stage': 'signed', 'avb_roles': [
            {'chain': name, 'key_role': 'avb'} for name in ('boot', 'system', 'vbmeta')]}
        return profile, target, inventory

    def test_no_boot_is_covered_and_descriptor_siblings_are_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [self.source(root, 'sdk', no_boot=True), self.source(root, 'ota')]
            samples = RUNNER['prepare_avb_samples'](root, sources, 'sdk')
            self.assertEqual(5, len(samples))
            self.assertEqual(b'product', (root / 'samples/avb/sdk/product.img').read_bytes())
            self.assertEqual(b'boot', (root / 'samples/avb/ota/boot.img').read_bytes())
            sdk = next(sample for sample in samples if sample[0] == 'avb-vbmeta')
            self.assertEqual(['--expected_chain_partition',
                f'system:1:{root}/public-keys/avb_pkmd.bin'], sdk[2])
            ota = next(sample for sample in samples if sample[0] == 'ota-avb-vbmeta')
            self.assertIn(f'boot:2:{root}/public-keys/avb_pkmd.bin', ota[2])
            report = json.loads((root / 'avb-image-coverage.json').read_text())
            self.assertEqual(['boot'], report['profiles'][0]['metadata_only'])

    def test_unexpected_missing_image_fails(self):
        for missing in ('boot', 'system', 'vbmeta'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sources = [self.source(root, 'sdk', missing=missing)]
                with self.assertRaisesRegex(ValueError, 'lacks declared AVB image'):
                    RUNNER['prepare_avb_samples'](root, sources, 'sdk')

    def test_metadata_only_without_other_product_coverage_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [self.source(root, 'sdk', no_boot=True)]
            with self.assertRaisesRegex(RUNNER['Failure'], 'lacks an emitted image'):
                RUNNER['prepare_avb_samples'](root, sources, 'sdk')

    def test_contradictory_no_boot_and_invalid_slot_fail(self):
        for extra, message in [('no_boot=true\n', 'contradicts'),
                               ('avb_system_rollback_index_location=-1\n', 'invalid AVB chain')]:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.source(root, 'sdk')
                # Rebuild rather than adding a duplicate ZIP entry.
                with zipfile.ZipFile(source[1]) as archive:
                    contents = {name: archive.read(name) for name in archive.namelist()}
                if 'rollback_index' in extra:
                    contents['META/misc_info.txt'] = extra.encode()
                else:
                    contents['META/misc_info.txt'] += extra.encode()
                with zipfile.ZipFile(source[1], 'w') as archive:
                    for name, data in contents.items():
                        archive.writestr(name, data)
                with self.assertRaisesRegex(ValueError, message):
                    RUNNER['prepare_avb_samples'](root, [source], 'sdk')
