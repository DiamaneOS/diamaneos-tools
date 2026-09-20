"""Execute the source-sync directory preparation block with declared v4 paths."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class SyncWorkspaceTest(unittest.TestCase):
    def test_fresh_retry_existing_checkout_and_unexpected_content(self):
        source = (ROOT / "deploy/builder/sync-pinned-source").read_text()
        begin = source.index('install -d -m 0750 "$source_root"')
        end = source.index('\nPATH=', begin)
        block = 'set -eu\nfail() { echo "$*" >&2; exit 1; }\n' + source[begin:end]
        paths = json.loads((ROOT / "config/build-environment.json").read_text())["workspace"]
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ)
            for key, value in (("source_root", "source"), ("cache_root", "cache"), ("output_root", "output")):
                env[key] = str(Path(temp) / paths[value + "_subdirectory"])
            def invoke():
                return subprocess.run(["bash", "-c", block], env=env, capture_output=True)
            self.assertEqual(0, invoke().returncode)
            self.assertEqual(0, invoke().returncode)
            unexpected = Path(env["source_root"]) / "unexpected"
            unexpected.touch()
            self.assertNotEqual(0, invoke().returncode)
            unexpected.unlink()
            (Path(env["source_root"]) / ".repo").mkdir()
            self.assertEqual(0, invoke().returncode)
