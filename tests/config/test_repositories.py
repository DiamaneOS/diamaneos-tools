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
                self.assertIn(row["state"], {"active", "planned", "retired"})
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
                self.assertEqual("github.com", parsed.hostname)
                self.assertEqual(self.mapping["github_owner"], parsed.path.split("/")[1])
                self.assertIn(row["publication"], {"public-source", "generated"})

    def test_existing_public_repositories_are_marked_active(self):
        states = {row["id"]: row["state"] for row in self.rows}
        active = ("tools", "manifest", "infra", "installer", "device", "product",
                  "kernel-sources", "kernel-common", "kernel-prebuilts")
        self.assertEqual({key: "active" for key in active}, {key: states[key] for key in active})

    def test_kernel_component_forks_are_gone_and_the_kernel_repository_is_active(self):
        # The kernel components are folders of kernel_qcom-6.1; their one-project forks were deleted.
        rows = {row["slug"]: row for row in self.rows}
        self.assertEqual(set(), {slug for slug in rows if slug.startswith("kernel_qcom_")} | ({"kernel_manifest-fp6"} & set(rows)))
        self.assertEqual(("active", "https://github.com/DiamaneOS/kernel_qcom-6.1.git"),
                         (rows["kernel_qcom-6.1"]["state"], rows["kernel_qcom-6.1"]["remote_url"]))

if __name__ == "__main__":
    unittest.main()
