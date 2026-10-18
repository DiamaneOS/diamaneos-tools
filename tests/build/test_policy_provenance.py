import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import policy_provenance


class MismatchTests(unittest.TestCase):
    def test_each_row_is_hashed_inside_the_policy_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            policy = src / policy_provenance.POLICY_DIR
            policy.mkdir(parents=True)
            (policy / 'a.te').write_text('a\n')
            (policy / 'b.te').write_text('b\n')
            os.symlink(policy / 'a.te', policy / 'link.te')
            digest = hashlib.sha256(b'a\n').hexdigest()

            def record(*paths):
                rows = [{'path': path, 'sha256': digest} for path in paths]
                (policy / 'provenance.json').write_text(json.dumps({'schema_version': 1, 'files': rows}))

            record('a.te', 'b.te', 'link.te', 'gone.te')
            self.assertEqual(['b.te', 'link.te', 'gone.te'], policy_provenance.mismatches(src))
            for rows in (['../a.te'], ['/a.te'], ['./a.te'], ['a.te', 'a.te'], []):
                record(*rows)
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    policy_provenance.mismatches(src)
            (policy / 'provenance.json').unlink()
            self.assertRaises(ValueError, policy_provenance.mismatches, src)


if __name__ == '__main__':
    unittest.main()
