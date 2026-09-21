import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build


class CompositionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name)
        self.directory = self.source / '.repo/local_manifests'
        self.directory.mkdir(parents=True)
        self.path = self.directory / 'diamaneos.xml'
        self.base = ('<manifest><remote name="upstream" fetch="https://example.invalid/base/"/>'
                     '<project name="base" path="build/make" remote="upstream" revision="' + 'a'*40 + '"/></manifest>').encode()
        self.overlay = ('<manifest><remote name="downstream" fetch="https://example.invalid/device/"/>'
                        '<project name="phone" path="device/example/phone" remote="downstream" revision="' + 'b'*40 + '"/></manifest>').encode()
        self.config, _ = build.load_config(ROOT / 'config/build-environment.json')
        self.config['workspace'].update(source_subdirectory='src/phone', output_subdirectory='src/phone/out')
        self.configure(self.overlay)

    def configure(self, overlay):
        self.path.write_bytes(overlay)
        combined = ET.fromstring(self.base)
        combined.extend(ET.fromstring(overlay))
        self.combined = ET.tostring(combined)
        rows, digest = build.parse_project_map(self.combined)
        self.config['composition'] = dict(overlay_revision='c'*40,
            overlay_sha256=hashlib.sha256(overlay).hexdigest(),
            project_count=len(rows), project_map_sha256=digest)

    def test_declared_overlay_reaches_source_layout_verification(self):
        build.validate_config(self.config)
        rows, _ = build.parse_project_map(self.combined)
        for path, *_ in rows:
            (self.source/path).mkdir(parents=True)
        build.verify_source_layout(self.config, self.source, rows, self.base, self.combined)
        (self.source/'device/rogue.mk').write_text('undeclared input')
        with self.assertRaisesRegex(build.BuildError, 'undeclared input'):
            build.verify_source_layout(self.config, self.source, rows, self.base, self.combined)

    def test_legacy_environment_still_rejects_overlay(self):
        del self.config['composition']
        with self.assertRaisesRegex(build.BuildError, 'not declared'):
            build.compose_source_manifest(self.config, self.source, self.base)

    def test_content_extra_files_and_symlinks_fail(self):
        self.path.write_bytes(self.overlay+b' ')
        with self.assertRaisesRegex(build.BuildError, 'content mismatch'):
            build.compose_source_manifest(self.config, self.source, self.base)
        self.path.write_bytes(self.overlay)
        extra=self.directory/'extra.xml';extra.write_text('<manifest/>')
        with self.assertRaisesRegex(build.BuildError, 'exactly'):
            build.compose_source_manifest(self.config, self.source, self.base)
        extra.unlink();self.path.unlink();self.path.symlink_to(self.source/'elsewhere')
        with self.assertRaisesRegex(build.BuildError, 'exactly'):
            build.compose_source_manifest(self.config, self.source, self.base)

    def test_digest_bound_overlay_cannot_redefine_or_extend_upstream(self):
        changes = [
            self.overlay.replace(b'name="downstream"',b'name="upstream"'),
            self.overlay.replace(b'path="device/example/phone"',b'path="build/make/child"'),
            self.overlay.replace(b'path="device/example/phone"',b'path="build"'),
            self.overlay.replace(b'path="device/example/phone"',b'path=".repo/evil"'),
            self.overlay.replace(b'https://example.invalid/device/',b'file:///untrusted/'),
            self.overlay.replace(b'</manifest>',b'<remove-project name="base"/></manifest>'),
            self.overlay.replace(b'</manifest>',b'<include name="other.xml"/></manifest>'),
            self.overlay.replace(b'<project name="phone"',b'<project groups="extra" name="phone"'),
        ]
        for overlay in changes:
            with self.subTest(overlay=overlay):
                self.path.write_bytes(overlay)
                self.config['composition']['overlay_sha256']=hashlib.sha256(overlay).hexdigest()
                with self.assertRaises(build.BuildError):
                    build.compose_source_manifest(self.config,self.source,self.base)

    def test_changed_composed_map_and_moving_revision_fail(self):
        self.config['composition']['project_map_sha256']='d'*64
        with self.assertRaisesRegex(build.BuildError, 'project map'):
            build.compose_source_manifest(self.config,self.source,self.base)
        overlay=self.overlay.replace(('b'*40).encode(),b'main')
        self.path.write_bytes(overlay)
        self.config['composition']['overlay_sha256']=hashlib.sha256(overlay).hexdigest()
        with self.assertRaises(build.BuildError):
            build.compose_source_manifest(self.config,self.source,self.base)

    def test_composition_schema_rejects_unknown_fields(self):
        changed=copy.deepcopy(self.config);changed['composition']['allow_dirty']=True
        with self.assertRaises(build.BuildError):build.validate_config(changed)

    def branch_overlay(self):
        overlay = self.overlay.replace(b'fetch="https://example.invalid/device/"',
            b'fetch="https://example.invalid/device/" revision="android17"')
        overlay = overlay.replace(b' revision="' + b'b'*40 + b'"', b'')
        self.path.write_bytes(overlay)
        self.config['composition']['overlay_sha256'] = hashlib.sha256(overlay).hexdigest()
        self.config['composition']['resolved_revisions'] = {'device/example/phone': 'b'*40}
        return overlay

    def test_remote_branch_inheritance_is_resolved_from_environment(self):
        self.branch_overlay()
        build.validate_config(self.config)
        composed = build.compose_source_manifest(self.config, self.source, self.base)
        self.assertEqual(build.parse_project_map(self.combined), build.parse_project_map(composed))

    def test_branch_requires_exact_resolution(self):
        self.branch_overlay()
        del self.config['composition']['resolved_revisions']
        with self.assertRaisesRegex(build.BuildError, 'lacks an exact'):
            build.compose_source_manifest(self.config, self.source, self.base)

    def test_advanced_branch_commit_requires_new_map(self):
        self.branch_overlay()
        self.config['composition']['resolved_revisions']['device/example/phone'] = 'e'*40
        with self.assertRaisesRegex(build.BuildError, 'project map'):
            build.compose_source_manifest(self.config, self.source, self.base)

    def test_unused_resolution_is_rejected(self):
        self.branch_overlay()
        self.config['composition']['resolved_revisions']['device/unexpected'] = 'e'*40
        with self.assertRaisesRegex(build.BuildError, 'unused resolved'):
            build.compose_source_manifest(self.config, self.source, self.base)

    def test_resolution_cannot_itself_be_a_branch(self):
        self.branch_overlay()
        self.config['composition']['resolved_revisions']['device/example/phone'] = 'android17'
        with self.assertRaisesRegex(build.BuildError, 'immutable digest'):
            build.validate_config(self.config)

    def test_explicit_project_branch_overrides_remote_default(self):
        overlay = self.branch_overlay().replace(b'remote="downstream"',
            b'remote="downstream" revision="integration"')
        self.path.write_bytes(overlay)
        self.config['composition']['overlay_sha256'] = hashlib.sha256(overlay).hexdigest()
        composed = build.compose_source_manifest(self.config, self.source, self.base)
        self.assertEqual(build.parse_project_map(self.combined), build.parse_project_map(composed))
