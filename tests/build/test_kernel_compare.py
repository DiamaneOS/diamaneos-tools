"""kernel compare: two builds of one pin match except for their signing keys."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import kernel_compare as subject


def signed(payload, signature):
    info = bytes(8) + len(signature).to_bytes(4, 'big')
    return payload + signature + info + subject.MARKER


class CompareTests(unittest.TestCase):
    def run_dir(self, root, name, key, modules, extra=None):
        run = root / name
        (run / 'candidate/modules').mkdir(parents=True)
        (run / 'result.json').write_text(json.dumps({'status': 'PASS', 'source_commit': 'c',
                                                     'packaging_recipe_sha256': 'p'}))
        (run / 'builtin-cert.der').write_bytes(b'cert-' + key)
        vdso = subject.BUILD_ID + bytes(range(20))
        (run / 'candidate/Image').write_bytes(b'head ' + vdso + b' cert-' + key + b' notes ' +
                                              subject.BUILD_ID + key.ljust(20, b'.') + b' tail')
        for module, payload in modules.items():
            (run / 'candidate/modules' / module).write_bytes(signed(payload, b'sig-' + key))
        for path, data in (extra or {}).items():
            (run / 'candidate' / path).write_bytes(data)
        return run

    def test_key_only_differences_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {'a.ko': b'code'}, {'dtbo.img': b'dt'})
            b = self.run_dir(root, 'b', b'two', {'a.ko': b'code'}, {'dtbo.img': b'dt'})
            result = subject.compare(a, b)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual((result['identical'], result['signature_only'], result['key_only']),
                             (1, 1, ['Image']))

    def test_other_differences_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {'a.ko': b'code', 'b.ko': b'x'}, {'dtbo.img': b'dt'})
            b = self.run_dir(root, 'b', b'two', {'a.ko': b'c0de'}, {'dtbo.img': b'dT'})
            result = subject.compare(a, b)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['different'], ['dtbo.img', 'modules/a.ko'])
            self.assertEqual(result['only_in_run'], ['modules/b.ko'])

    def test_runs_of_other_sources_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self.run_dir(root, 'a', b'one', {})
            b = self.run_dir(root, 'b', b'two', {})
            (b / 'result.json').write_text(json.dumps({'status': 'PASS', 'source_commit': 'd',
                                                       'packaging_recipe_sha256': 'p'}))
            self.assertRaisesRegex(ValueError, 'source_commit', subject.compare, a, b)


if __name__ == '__main__':
    unittest.main()
