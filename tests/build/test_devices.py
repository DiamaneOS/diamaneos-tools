import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, devices, product_inputs, vendor, vendor_extract, vendor_product


class DeviceTests(unittest.TestCase):
    def test_the_fp6_entry_names_the_files_the_official_build_uses(self):
        device = devices.get('fp6')
        self.assertEqual(('FP6', 'FP6', 'vendor/fairphone/FP6'), (device.codename, device.lunch, device.vendor_tree))
        self.assertEqual(('fp6-stock-image-recipe.json', 'stock-inputs.json', 'fp6-minimal/vendor-files.json',
                          'fp6-minimal/vendor-elf.json', 'fp6-image-tools.json', 'fp6-firmware-inventory.json'),
                         device.vendor_recipes)
        self.assertEqual(ROOT / 'config/build-environment-fp6.json', device.path('build_environment'))
        self.assertEqual(ROOT / 'config/fp6-build.json', device.path('build'))

    def test_a_missing_or_unknown_codename_lists_the_supported_devices(self):
        for name in (None, '', 'FP5', '../FP6'):
            with self.subTest(name=name), self.assertRaisesRegex(devices.DeviceError, 'Supported devices: FP6$'):
                devices.get(name)

    def test_an_entry_names_existing_config_files_by_plain_relative_paths(self):
        entry = json.loads((devices.DIRECTORY / 'FP6.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'FP6.json'
            for change in ({'vendor_tree': '../vendor'}, {'files': dict(entry['files'], build='/etc/passwd')},
                           {'files': dict(entry['files'], build='missing.json')}, {'codename': 'FP7'},
                           {'extra': 1}):
                with self.subTest(change=change):
                    path.write_text(json.dumps(dict(entry, **change)))
                    with self.assertRaises(devices.DeviceError):
                        devices.read(path)

    def test_build_vendor_and_the_vendor_commands_need_the_codename(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(2, steps.main(['vendor', '--dry-run', '--workspace', '/nonexistent/ws']))
            for main, argv in ((vendor.main, ['stage', '--archive', 'a', '--output', 'o']),
                               (vendor_extract.main, ['--super', 's', '--image-tools', 't', '--output', 'o']),
                               (vendor_product.main, ['--inputs', 'i', '--output', 'o', '--notice-kind', 'k',
                                                      '--factory-zip', 'f']),
                               (product_inputs.main, ['--source', 's', '--vendor', 'v'])):
                for device in ([], ['FP5']):
                    with self.subTest(main=main.__module__, device=device), self.assertRaises(SystemExit):
                        main(argv[:1] + device + argv[1:] if main is vendor.main else device + argv)
        self.assertEqual(9, errors.getvalue().count('Supported devices: FP6'))

    def test_the_vendor_step_reads_the_device_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            args = steps.parser().parse_args(['vendor', 'FP6', '--workspace', str(Path(temp) / 'ws')])
            ctx = steps.make_context(args, lambda *a: None)
            self.assertEqual(devices.get('FP6'), ctx.device)
            ctx.workspace.write_state('sync', {'status': 'PASS', 'outputs': {'project_map_sha256': 'p'}})
            with patch.object(steps, 'newest_commit_time', return_value=1):
                plan = steps.plan_vendor(ctx)
        self.assertEqual(steps.config_hashes(devices.get('FP6').vendor_recipes), plan.inputs['recipes'])
        self.assertIn('vendor/fairphone/FP6', plan.actions[-1].detail)


if __name__ == '__main__':
    unittest.main()
