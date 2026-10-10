"""A literal pin in a doc command must equal the config value it stands for."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DocPinTests(unittest.TestCase):
    def test_repo_rev_examples_name_the_pinned_repo_release(self):
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        pinned = environment['upstream']['repo_tool']['release_tag']
        found = []
        for path in sorted([*ROOT.glob('*.md'), *(ROOT / 'docs').glob('*.md')]):
            found += re.findall(r'--repo-rev=(v[0-9][0-9A-Za-z.]*)', path.read_text())
        self.assertTrue(found, 'the build guide no longer shows a repo release')
        self.assertEqual({pinned}, set(found))


if __name__ == '__main__':
    unittest.main()
