"""Generated trees and local manifests in the source layout check."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build


class GeneratedLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name)
        (self.source / '.repo').mkdir()
        self.manifest = ('<manifest><remote name="upstream" fetch="https://example.invalid/base/"/>'
                         '<project name="base" path="build/make" remote="upstream" revision="' + 'a' * 40 + '"/>'
                         '<project name="phone" path="device/example/phone" remote="upstream" revision="'
                         + 'b' * 40 + '"/></manifest>').encode()
        self.config, _ = build.load_config(ROOT / 'config/build-environment-fp6.json')
        self.config['workspace'].update(source_subdirectory='src/phone', output_subdirectory='src/phone/out')
        self.rows, _ = build.parse_project_map(self.manifest)
        for path, *_ in self.rows:
            (self.source / path).mkdir(parents=True)

    def check(self, generated=frozenset()):
        build.verify_source_layout(self.config, self.source, self.rows, self.manifest, self.manifest, generated)

    def test_projects_pass_and_undeclared_files_fail(self):
        self.check()
        (self.source / 'device/rogue.mk').write_text('undeclared input')
        with self.assertRaisesRegex(build.BuildError, 'undeclared input'):
            self.check()

    def test_generated_trees_need_explicit_acceptance(self):
        (self.source / 'vendor/fairphone/FP6').mkdir(parents=True)
        with self.assertRaisesRegex(build.BuildError, 'undeclared input'):
            self.check()
        self.check({'vendor/fairphone/FP6'})
        with self.assertRaisesRegex(build.BuildError, 'overlaps a source project'):
            self.check({'device/example'})

    def test_no_descriptor_accepts_no_generated_tree(self):
        self.assertEqual((set(), None), build.verify_generated_inputs(self.source, None))

    def test_any_local_manifest_is_rejected(self):
        local = self.source / '.repo/local_manifests'
        local.mkdir()
        self.check()
        (local / 'diamaneos.xml').write_text('<manifest/>')
        with self.assertRaisesRegex(build.BuildError, 'local manifests are not declared'):
            self.check()
        (local / 'diamaneos.xml').unlink()
        local.rmdir()
        local.symlink_to(self.source)
        with self.assertRaisesRegex(build.BuildError, 'local manifest directory is invalid'):
            self.check()


if __name__ == '__main__':
    unittest.main()
