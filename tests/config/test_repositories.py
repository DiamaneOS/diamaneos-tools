"""Public repository discovery-map invariants."""

import json
from pathlib import Path
import unittest
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "config" / "repositories.json"


class RepositoryMapTests(unittest.TestCase):
    def setUp(self):
        self.mapping = json.loads(MAP.read_text(encoding="utf-8"))
        self.rows = self.mapping["repositories"]

    def test_map_is_discovery_metadata_not_a_stale_lockfile(self):
        self.assertIn("not a release lock", self.mapping["revision_policy"])
        self.assertTrue(self.rows)
        for row in self.rows:
            with self.subTest(repository=row["id"]):
                self.assertIn(row["state"], {"active", "planned"})
                self.assertIsNone(row["revision"])

    def test_checkout_paths_are_portable_and_repositories_are_public_https(self):
        ids = set()
        paths = set()
        for row in self.rows:
            with self.subTest(repository=row["id"]):
                self.assertNotIn(row["id"], ids)
                self.assertNotIn(row["checkout_path"], paths)
                ids.add(row["id"])
                paths.add(row["checkout_path"])
                self.assertTrue(row["checkout_path"].startswith("WORK_ROOT/"))
                parsed = urlparse(row["remote_url"])
                self.assertEqual("https", parsed.scheme)
                self.assertEqual("codeberg.org", parsed.hostname)
                self.assertIn(row["publication"], {"public-source", "generated"})

    def test_existing_public_repositories_are_marked_active(self):
        states = {row["id"]: row["state"] for row in self.rows}
        self.assertEqual(
            {"tools": "active", "manifest": "active",
             "infra": "active", "installer": "active"},
            {key: states[key] for key in
             ("tools", "manifest", "infra", "installer")},
        )


if __name__ == "__main__":
    unittest.main()
