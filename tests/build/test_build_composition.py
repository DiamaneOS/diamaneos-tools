import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from diamaneos_tools import build, build_composition as composition

class CompositionCheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.source=self.root/'source';self.source.mkdir()
        (self.source/'.repo').mkdir();self.overlay=self.root/'overlay';self.overlay.mkdir()
        self.project=self.source/'device/example';self.project.mkdir(parents=True)
        for p in (self.project,self.overlay):
            self.git(p,'init','-q');self.git(p,'config','user.name','Fixture');self.git(p,'config','user.email','fixture@example.invalid')
        (self.project/'file').write_text('first');self.commit(self.project);self.first=self.git(self.project,'rev-parse','HEAD').strip()
        (self.project/'file').write_text('second');self.commit(self.project)
        self.base=('<manifest><remote name="base" fetch="https://example.invalid/base/"/>'
                   '<project name="base" path="build/make" remote="base" revision="'+'a'*40+'"/></manifest>').encode()
        self.data=b'<manifest><remote name="owned" fetch="https://example.invalid/owned/" revision="android17"/><project name="device" path="device/example" remote="owned"/></manifest>'
        (self.overlay/'diamaneos.xml').write_bytes(self.data);self.commit(self.overlay)
        composed=ET.fromstring(self.base);addition=ET.fromstring(self.data);addition.find('project').set('revision',self.first);composed.extend(addition)
        rows,digest=build.parse_project_map(ET.tostring(composed))
        self.config={'composition':dict(overlay_revision=self.git(self.overlay,'rev-parse','HEAD').strip(),overlay_sha256=hashlib.sha256(self.data).hexdigest(),resolved_revisions={'device/example':self.first},project_count=len(rows),project_map_sha256=digest)}
    def git(self,p,*args):
        return subprocess.check_output(['git','-C',str(p),*args],text=True,stderr=subprocess.PIPE)
    def commit(self,p):
        self.git(p,'add','.');self.git(p,'-c','commit.gpgsign=false','commit','-qm','fixture')
    def prepare(self):
        composition.prepare(self.config,self.source,self.overlay,self.base)
    def test_install_is_idempotentent_and_checkout_uses_declared_commit(self):
        self.prepare();self.prepare();composition.checkout(self.config,self.source,self.base)
        self.assertEqual(self.first,self.git(self.project,'rev-parse','HEAD').strip())
        self.assertEqual('first',(self.project/'file').read_text())
        self.assertEqual(self.data,(self.source/'.repo/local_manifests/diamaneos.xml').read_bytes())
    def test_dirty_project_is_preserved(self):
        self.prepare();(self.project/'file').write_text('owner edit')
        with self.assertRaisesRegex(build.BuildError,'local changes'):composition.checkout(self.config,self.source,self.base)
        self.assertEqual('owner edit',(self.project/'file').read_text())
    def test_unknown_overlay_is_preserved(self):
        self.prepare();p=self.source/'.repo/local_manifests/diamaneos.xml';p.write_text('owner edit')
        with self.assertRaisesRegex(build.BuildError,'existing local manifests'):self.prepare()
        self.assertEqual('owner edit',p.read_text())
    def test_dirty_overlay_cannot_be_installed(self):
        (self.overlay/'extra').write_text('unreviewed')
        with self.assertRaisesRegex(build.BuildError,'dirty'):self.prepare()
        self.assertFalse((self.source/'.repo/local_manifests').exists())
    def test_wrong_map_cannot_publish_overlay(self):
        self.config['composition']['project_map_sha256']='f'*64
        with self.assertRaisesRegex(build.BuildError,'project map'):self.prepare()
        self.assertFalse((self.source/'.repo/local_manifests').exists())
    def test_project_symlink_is_rejected(self):
        self.prepare();moved=self.root/'moved';self.project.rename(moved);self.project.symlink_to(moved)
        with self.assertRaisesRegex(build.BuildError,'redirected'):composition.checkout(self.config,self.source,self.base)
