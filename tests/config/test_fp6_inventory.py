import copy
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_MODULE_FAMILIES = {
    "kernel-qcom",
    "camera-kernel",
    "dataipa",
    "display-drivers",
    "eva-kernel",
    "mm-drivers",
    "mm-sys-kernel",
    "mmrm-driver",
    "synx-kernel",
    "touch-drivers",
    "video-driver",
    "bt-kernel",
    "graphics-kernel",
    "securemsm-kernel",
    "spu-kernel",
    "nfc",
    "wlan-qcacld-3.0",
    "audio-techpack",
}

REQUIRED_DEVICE_TREE_FAMILIES = {
    "qcom-base",
    "audio",
    "bt",
    "camera",
    "data",
    "display",
    "dsp",
    "ese",
    "eva",
    "graphics",
    "mm",
    "mmrm",
    "mm-sys",
    "nfc",
}

REQUIRED_WIFI_SOURCE_PATHS = {
    "vendor/qcom/opensource/wlan/fw-api",
    "vendor/qcom/opensource/wlan/platform",
    "vendor/qcom/opensource/wlan/qca-wifi-host-cmn",
    "vendor/qcom/opensource/wlan/qcacld-3.0",
}

REQUIRED_VENDOR_MANIFEST_FIELDS = {
    "stock_build",
    "region",
    "partition",
    "source_path",
    "destination_path",
    "sha256",
    "component_role",
    "source_or_prebuilt",
    "consumer",
    "provenance_profile",
}

PROPRIETARY_ORIGINS = {
    "proprietary-prebuilt",
    "proprietary-prebuilt-userspace-and-firmware",
    "proprietary-prebuilt-userspace-with-published-kernel-source",
    "proprietary-prebuilt-runtime-with-published-generic-integration-source",
}


def load_json(name):
    with (ROOT / "config" / name).open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_sources(data):
    errors = []
    if data.get("schema_version") != 1:
        errors.append("unsupported schema_version")

    modules = data.get("module_families", [])
    module_by_id = {entry.get("id"): entry for entry in modules}
    if len(module_by_id) != len(modules):
        errors.append("duplicate module family id")
    missing_modules = REQUIRED_MODULE_FAMILIES - set(module_by_id)
    if missing_modules:
        errors.append("missing module families: " + ", ".join(sorted(missing_modules)))

    trees = data.get("device_tree_families", [])
    tree_ids = {entry.get("id") for entry in trees}
    if len(tree_ids) != len(trees):
        errors.append("duplicate device-tree family id")
    missing_trees = REQUIRED_DEVICE_TREE_FAMILIES - tree_ids
    if missing_trees:
        errors.append("missing device-tree families: " + ", ".join(sorted(missing_trees)))

    for section in (data.get("device_sources", []), modules, trees):
        for entry in section:
            if section is modules:
                for field in ("function", "origin", "licence_profile", "abi_uapi", "load_path"):
                    if not entry.get(field):
                        errors.append(f"{entry.get('id')}: missing {field}")
            sources = entry.get("sources", [entry])
            if not sources:
                errors.append(f"{entry.get('id')}: no source or explicit prebuilt")
            for source in sources:
                if not COMMIT_RE.fullmatch(str(source.get("revision", ""))):
                    errors.append(f"{entry.get('id')}: revision is not immutable")

    wifi = module_by_id.get("wlan-qcacld-3.0", {})
    wifi_paths = {source.get("path") for source in wifi.get("sources", [])}
    missing_wifi = REQUIRED_WIFI_SOURCE_PATHS - wifi_paths
    if missing_wifi:
        errors.append("incomplete Wi-Fi source set: " + ", ".join(sorted(missing_wifi)))

    hal_families = data.get("userspace_hal_families", [])
    hal_ids = {entry.get("id") for entry in hal_families}
    if len(hal_ids) != len(hal_families):
        errors.append("duplicate userspace HAL family id")
    for entry in hal_families:
        for source in entry.get("source_revisions", []):
            if not COMMIT_RE.fullmatch(str(source.get("revision", ""))):
                errors.append(f"{entry.get('id')}: HAL source revision is not immutable")
        if entry.get("origin") in PROPRIETARY_ORIGINS:
            if entry.get("rebuilt_from_source") is not False:
                errors.append(f"{entry.get('id')}: proprietary prebuilt called source-built")
            if entry.get("provenance_profile") != "exact-stock-non-source":
                errors.append(
                    f"{entry.get('id')}: proprietary prebuilt lacks its provenance profile"
                )

    for entry in data.get("firmware_families", []):
        if entry.get("origin") == "proprietary-prebuilt":
            if entry.get("rebuilt_from_source") is not False:
                errors.append(f"{entry.get('id')}: proprietary firmware called source-built")

    blockers = data.get("blockers", [])
    blocker_ids = {entry.get("id") for entry in blockers}
    if len(blocker_ids) != len(blockers):
        errors.append("duplicate blocker id")
    for blocker in blockers:
        if not blocker.get("next_experiment") or not blocker.get("owner"):
            errors.append(f"{blocker.get('id')}: blocker lacks owner/experiment")

    inputs = data.get("inputs", {})
    manifest = inputs.get("fairphone_manifest", {})
    for field in ("target_manifest_sha256", "qssi_manifest_sha256"):
        if not SHA256_RE.fullmatch(str(manifest.get(field, ""))):
            errors.append(f"fairphone_manifest.{field}: invalid SHA-256")
    if inputs.get("published_binary_packages", {}).get("selected_stock_match") is not False:
        errors.append("mismatched public binary package is not excluded")
    if not SHA256_RE.fullmatch(
        str(inputs.get("selected_stock", {}).get("runtime_interface_capture_sha256", ""))
    ):
        errors.append("selected stock runtime capture lacks a valid SHA-256")

    vendor_strategy = data.get("proprietary_input_strategy", {})
    if "FP6.QREL.16.100.0" not in vendor_strategy.get("primary_eu_source", ""):
        errors.append("EU vendor generator is not bound to QREL.16.100.0")
    missing_fields = REQUIRED_VENDOR_MANIFEST_FIELDS - set(
        vendor_strategy.get("manifest_required_fields", [])
    )
    if missing_fields:
        errors.append(
            "vendor manifest lacks fields: " + ", ".join(sorted(missing_fields))
        )
    if not vendor_strategy.get("device_unique_exclusions"):
        errors.append("vendor generator has no device-unique exclusion policy")
    for field in (
        "supplemental_source",
        "generation_rule",
        "minimization_rule",
        "open_source_replacement_rule",
    ):
        if not vendor_strategy.get(field):
            errors.append(f"vendor strategy lacks {field}")

    regional = data.get("regional_support_strategy", {})
    if regional.get("initial_supported_region") != "EU":
        errors.append("EU is not bound as the initial supported target")
    if regional.get("eu_stock_build") != "FP6.QREL.16.100.0":
        errors.append("EU regional input is not QREL.16.100.0")
    if regional.get("us_stock_build") != "FP6.QREL.16.104.0":
        errors.append("US regional comparison input is not QREL.16.104.0")
    if not SHA256_RE.fullmatch(str(regional.get("us_factory_expected_sha256", ""))):
        errors.append("US regional input lacks an expected SHA-256")
    if not regional.get("comparison_scope"):
        errors.append("EU-US comparison scope is missing")
    for field in (
        "desired_shape",
        "selection_rule",
        "validation_resource",
        "claim_boundary",
    ):
        if not regional.get(field):
            errors.append(f"regional strategy lacks {field}")

    paths = data.get("resolved_integration_paths", {})
    official = paths.get("official_fairphone_device_configuration", {})
    reference = paths.get("lineage_reference_only", {})
    for field in ("product_entry_points", "kernel_entry_points", "fstab_candidates"):
        if not official.get(field):
            errors.append(f"official integration paths lack {field}")
    if "not present" not in official.get("vintf_and_device_init_status", ""):
        errors.append("missing public VINTF/init boundary is not explicit")
    for field in ("vintf", "fstab_and_init", "module_lists"):
        if not reference.get(field):
            errors.append(f"reference integration paths lack {field}")
    stock_runtime = paths.get("selected_stock_runtime", {})
    if stock_runtime.get("parsed_xml_files", 0) < 1 or stock_runtime.get("parse_errors") != 0:
        errors.append("selected stock VINTF was not parsed completely")

    nfc = module_by_id.get("nfc", {})
    if nfc.get("selected_stock_source", {}).get("path") != "vendor/samsung_slsi/nfc":
        errors.append("stock NFC source selection is not bound")

    return errors


def validate_capabilities(data):
    errors = []
    allowed = set(data.get("classification_values", []))
    capabilities = data.get("capabilities", [])
    by_id = {entry.get("id"): entry for entry in capabilities}
    if len(by_id) != len(capabilities):
        errors.append("duplicate capability id")
    for entry in capabilities:
        if entry.get("classification") not in allowed:
            errors.append(f"{entry.get('id')}: invalid classification")
        if entry.get("pixel_source_only") is True and entry.get("classification") == "inherited-candidate":
            errors.append(f"{entry.get('id')}: Pixel-only interface inherited blindly")
        if not entry.get("next_experiment") or not entry.get("owner"):
            errors.append(f"{entry.get('id')}: missing owner/experiment")
    runtime = data.get("compatibility", {}).get("stock_runtime_vintf", {})
    if runtime.get("device_target_level") != 8:
        errors.append("stock device VINTF target level is not bound")
    if runtime.get("xml_files_parsed", 0) < 1 or runtime.get("parse_errors") != 0:
        errors.append("stock VINTF parse evidence is incomplete")
    if not SHA256_RE.fullmatch(str(runtime.get("capture_sha256", ""))):
        errors.append("stock VINTF capture lacks a valid SHA-256")
    for capability_id in ("strongbox-and-hardware-weaver", "memory-tagging-extension"):
        if by_id.get(capability_id, {}).get("classification") != "unsupported":
            errors.append(f"{capability_id}: observed hardware gap is not classified unsupported")
    return errors


class FP6InventoryTests(unittest.TestCase):
    def setUp(self):
        self.sources = load_json("fp6-sources.json")
        self.capabilities = load_json("fp6-capabilities.json")

    def test_inventory_is_complete_and_fail_closed(self):
        self.assertEqual([], validate_sources(self.sources))
        self.assertEqual([], validate_capabilities(self.capabilities))

    def test_v1_proprietary_prebuilt_is_not_called_rebuilt(self):
        mutated = copy.deepcopy(self.sources)
        camera = next(
            entry for entry in mutated["userspace_hal_families"]
            if entry["id"] == "camera"
        )
        camera["rebuilt_from_source"] = True
        self.assertTrue(
            any("proprietary prebuilt called source-built" in item for item in validate_sources(mutated))
        )

    def test_v2_missing_wifi_repository_makes_inventory_incomplete(self):
        mutated = copy.deepcopy(self.sources)
        wifi = next(
            entry for entry in mutated["module_families"]
            if entry["id"] == "wlan-qcacld-3.0"
        )
        wifi["sources"] = [
            source for source in wifi["sources"]
            if source["path"] != "vendor/qcom/opensource/wlan/qcacld-3.0"
        ]
        self.assertTrue(
            any("incomplete Wi-Fi source set" in item for item in validate_sources(mutated))
        )

    def test_v3_pixel_only_interface_is_not_inherited_blindly(self):
        mutated = copy.deepcopy(self.capabilities)
        pixel = next(
            entry for entry in mutated["capabilities"]
            if entry.get("pixel_source_only") is True
        )
        pixel["classification"] = "inherited-candidate"
        self.assertTrue(
            any("Pixel-only interface inherited blindly" in item for item in validate_capabilities(mutated))
        )


if __name__ == "__main__":
    unittest.main()
