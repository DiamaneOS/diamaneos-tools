import copy
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FAMILY_SECTIONS = ("userspace_hal_families", "firmware_families")

PROPRIETARY_ORIGINS = {
    "proprietary-prebuilt",
    "proprietary-prebuilt-userspace-and-firmware",
    "proprietary-prebuilt-userspace-with-published-kernel-source",
    "proprietary-prebuilt-runtime-with-published-generic-integration-source",
}


def load_json(name):
    with (ROOT / "config" / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def pointer(data, path):
    for part in path.lstrip("/").split("/"):
        data = data[part]
    return data


def validate_sources(data, selected_refs):
    """Errors in the source inventory, given the inventory_ref values of the selected stock files."""
    errors = []
    if data.get("schema_version") != 2:
        errors.append("unsupported schema_version")

    manifest = data.get("inputs", {}).get("fairphone_manifest", {})
    if not COMMIT_RE.fullmatch(str(manifest.get("revision_observed", ""))):
        errors.append("fairphone_manifest.revision_observed is not a commit")

    profiles = data.get("provenance_profiles", {})
    families = set()
    for section in FAMILY_SECTIONS:
        entries = data.get(section, [])
        ids = {entry.get("id") for entry in entries}
        if len(ids) != len(entries):
            errors.append(f"duplicate id in {section}")
        families |= {f"{section}:{family}" for family in ids}
        for entry in entries:
            for field in ("origin", "abi_uapi", "load_path"):
                if not entry.get(field):
                    errors.append(f"{entry.get('id')}: missing {field}")
            if entry.get("origin") in PROPRIETARY_ORIGINS:
                if entry.get("rebuilt_from_source") is not False:
                    errors.append(f"{entry.get('id')}: proprietary prebuilt called source-built")
                if entry.get("provenance_profile") != "exact-stock-non-source":
                    errors.append(f"{entry.get('id')}: proprietary prebuilt lacks its provenance profile")
            if "provenance_profile" in entry and entry["provenance_profile"] not in profiles:
                errors.append(f"{entry.get('id')}: unknown provenance profile")

    missing = set(selected_refs) - families
    if missing:
        errors.append("selected files name unknown families: " + ", ".join(sorted(missing)))
    unused = families - set(selected_refs)
    if unused:
        errors.append("families no selected file names: " + ", ".join(sorted(unused)))

    return errors


class FP6InventoryTests(unittest.TestCase):
    def setUp(self):
        self.sources = load_json("fp6-sources.json")
        recipe = load_json("fp6-minimal/vendor-files.json")
        self.refs = {row["inventory_ref"] for row in recipe["files"] + recipe["symlinks"]}

    def test_inventory_is_complete_and_fail_closed(self):
        self.assertEqual([], validate_sources(self.sources, self.refs))

    def test_proprietary_prebuilt_is_not_called_rebuilt(self):
        mutated = copy.deepcopy(self.sources)
        camera = next(
            entry for entry in mutated["userspace_hal_families"]
            if entry["id"] == "camera"
        )
        camera["rebuilt_from_source"] = True
        self.assertTrue(
            any("proprietary prebuilt called source-built" in item
                for item in validate_sources(mutated, self.refs))
        )

    def test_every_selected_file_names_a_family_and_every_family_is_used(self):
        mutated = copy.deepcopy(self.sources)
        mutated["userspace_hal_families"] = [
            entry for entry in mutated["userspace_hal_families"] if entry["id"] != "camera"
        ]
        self.assertTrue(
            any("unknown families: userspace_hal_families:camera" in item
                for item in validate_sources(mutated, self.refs))
        )
        self.assertTrue(
            any("families no selected file names" in item
                for item in validate_sources(self.sources, self.refs - {"userspace_hal_families:camera"}))
        )

    def test_fork_registry_reads_the_fairphone_manifest_pin_here(self):
        registry = load_json("forks.json")
        pin = next(source["pin"] for source in registry["sources"] if source["id"] == "fairphone-manifest")
        self.assertEqual("config/fp6-sources.json", pin["file"])
        self.assertRegex(pointer(self.sources, pin["pointer"]), COMMIT_RE)


if __name__ == "__main__":
    unittest.main()
