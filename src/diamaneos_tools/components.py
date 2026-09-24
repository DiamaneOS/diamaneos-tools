"""Validate the FP6 component decision model and generated artifact closure.

This command is deliberately read-only.  Model validation establishes source
inventory coverage and decision consistency; it does not qualify a component,
build an image or promote a release.  Optional closure validation consumes a
generated artifact inventory and fails closed on unmapped outputs or missing
dependencies.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None


ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 67108864
MAX_DEPTH = 24
MAX_NODES = 2000000
MAX_ERRORS = 40
SOURCE_SECTIONS = (
    "device_sources",
    "module_families",
    "device_tree_families",
    "userspace_hal_families",
    "firmware_families",
)
REQUIRED_CATEGORIES = {
    "device-platform-kernel",
    "oss-vendor-libraries",
    "public-hal-services",
    "camera",
    "graphics-display-media",
    "audio",
    "radio-ims-data",
    "connectivity-peripherals",
    "credentials-security",
    "firmware-trusted-boot",
    "esim",
    "optional-vendor-services",
    # Userspace daemons and libraries that start and serve the remote
    # processors; kept apart from the firmware they talk to.
    "remote-processor-services",
}
INVESTIGATION_STATES = {
    "unknown", "candidate", "private-bringup", "qualification-failed",
}
REQUIRED_PUBLIC_EVIDENCE = {
    "source-built": {
        "exact-source", "build", "interface", "security", "functional",
        "maintenance", "update-recovery",
    },
    "necessary-prebuilt": {
        "exact-artifact", "necessity", "alternatives", "integrity",
        "security", "functional", "maintenance", "exposure",
        "update-recovery",
    },
    "removed": {"absence", "functional", "security", "update-recovery"},
    "research-only": {"absence"},
}
FORBIDDEN_KEYS = {
    "password", "privatekey", "token", "secret", "seed", "mnemonic",
    "imei", "imsi", "iccid", "serial", "custody",
}


class ComponentError(ValueError):
    """Safe diagnostic which never includes model-provided values."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ComponentError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ComponentError("non-finite JSON number")


def _guard(value):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ComponentError("document exceeds structural limits")
        if isinstance(item, (dict, list)) and len(item) > MAX_NODES:
            raise ComponentError("document exceeds structural limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ComponentError("JSON object keys must be strings")
                if re.sub(r"[-_]", "", key).lower() in FORBIDDEN_KEYS:
                    raise ComponentError("private field is not allowed")
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif not isinstance(item, (str, int, float, bool, type(None))):
            raise ComponentError("value is not JSON data")
        if isinstance(item, str):
            if len(item) > MAX_FILE_BYTES:
                raise ComponentError("document exceeds string limit")
            if re.search(r"[a-z][a-z0-9+.-]*://[^\s/]*@", item, re.I):
                raise ComponentError("credential-bearing URL is not allowed")
            if re.search(r"-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----", item):
                raise ComponentError("private key material is not allowed")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ComponentError("document is not finite UTF-8 JSON") from None
    if len(encoded) > MAX_FILE_BYTES:
        raise ComponentError("document exceeds byte limit")


def loads(data):
    if not isinstance(data, bytes):
        raise ComponentError("JSON input must be bytes")
    if len(data) > MAX_FILE_BYTES:
        raise ComponentError("document exceeds byte limit")
    try:
        value = json.loads(data.decode("utf-8"),
                           object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except ComponentError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ComponentError("invalid UTF-8 JSON") from None
    _guard(value)
    return value


def load_json(path):
    try:
        with Path(path).open("rb") as stream:
            return loads(stream.read(MAX_FILE_BYTES + 1))
    except OSError:
        raise ComponentError("unable to read component input") from None


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise ComponentError("unable to hash component input") from None


def _schema_errors(value, filename):
    try:
        _guard(value)
    except ComponentError as error:
        return [str(error)]
    if Draft7Validator is None:
        return ["missing jsonschema; install requirements-dev.txt in a virtual environment"]
    try:
        schema = json.loads((ROOT / "schemas" / filename).read_text(
            encoding="utf-8"))
        Draft7Validator.check_schema(schema)
    except (OSError, ValueError):
        return ["component schema is unavailable or invalid"]
    errors = []
    for error in Draft7Validator(schema).iter_errors(value):
        # jsonschema messages may echo untrusted or private values.
        errors.append("schema constraint failed: " + str(error.validator))
        if len(errors) == MAX_ERRORS:
            break
    return errors


def _source_refs(sources):
    refs = {}
    for section in SOURCE_SECTIONS:
        for entry in sources.get(section, []):
            identifier = entry.get("id") if isinstance(entry, dict) else None
            if isinstance(identifier, str):
                refs[f"{section}:{identifier}"] = entry
    return refs


def _has_cycle(components):
    graph = {entry["id"]: entry["dependencies"] for entry in components}
    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in graph.get(node, []):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def validate_model(model, sources, build_environment, *, source_sha256=None):
    errors = _schema_errors(model, "component-model.schema.json")
    if errors:
        return errors[:MAX_ERRORS]
    try:
        _guard(sources)
        _guard(build_environment)
    except ComponentError as error:
        return [str(error)]

    inputs = model["fp6_model"]["inputs"]
    source_binding = inputs["fp6_source_inventory"]
    if source_sha256 is not None and source_binding["sha256"] != source_sha256:
        errors.append("FP6 source inventory hash mismatch")
    if source_binding["schema_version"] != sources.get("schema_version"):
        errors.append("FP6 source inventory schema mismatch")

    stock = sources.get("inputs", {}).get("selected_stock", {})
    stock_binding = inputs["selected_stock"]
    for field, source_field in (
            ("build", "build"), ("factory_sha256", "archive_sha256"),
            ("runtime_vintf_sha256", "runtime_interface_capture_sha256")):
        if stock_binding[field] != stock.get(source_field):
            errors.append("selected stock binding mismatch")
            break
    if stock_binding["region"] != sources.get(
            "regional_support_strategy", {}).get("initial_supported_region"):
        errors.append("selected stock region mismatch")

    manifest = sources.get("inputs", {}).get("fairphone_manifest", {})
    manifest_binding = inputs["fairphone_source_manifest"]
    for field in ("revision_observed", "target_manifest_sha256",
                  "qssi_manifest_sha256"):
        if manifest_binding[field] != manifest.get(field):
            errors.append("Fairphone source manifest binding mismatch")
            break

    platform = inputs["platform_environment"]
    upstream = build_environment.get("upstream", {})
    expected = {
        "environment_id": build_environment.get("environment_id"),
        "release_tag": upstream.get("release_tag"),
        "manifest_commit": upstream.get("peeled_commit"),
        "project_map_sha256": upstream.get("project_map_sha256"),
    }
    if any(platform[field] != value for field, value in expected.items()):
        errors.append("platform environment binding mismatch")

    environment_device = build_environment.get("device_inputs", {})
    if (environment_device.get("selected_stock_build") !=
            stock_binding["build"]
            or environment_device.get("selected_stock_factory_sha256") !=
            stock_binding["factory_sha256"]):
        errors.append("build environment stock binding mismatch")

    components = model["fp6_components"]
    by_id = {entry["id"]: entry for entry in components}
    if len(by_id) != len(components):
        errors.append("duplicate FP6 component identifier")
    if Counter(entry["category"] for entry in components) != Counter(
            REQUIRED_CATEGORIES):
        errors.append("FP6 component category coverage mismatch")

    known_refs = _source_refs(sources)
    source_entry_count = sum(len(sources.get(section, []))
                             for section in SOURCE_SECTIONS)
    if len(known_refs) != source_entry_count:
        errors.append("FP6 source inventory contains duplicate identifiers")
    ref_counts = Counter(ref for entry in components
                         for ref in entry["inventory_refs"])
    if set(ref_counts) != set(known_refs):
        errors.append("FP6 source inventory coverage mismatch")
    if any(count != 1 for count in ref_counts.values()):
        errors.append("FP6 source inventory reference has multiple owners")

    known_blockers = {
        entry.get("id") for entry in sources.get("blockers", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    blocker_counts = Counter(ref for entry in components
                             for ref in entry["blocker_refs"])
    if set(blocker_counts) != known_blockers:
        errors.append("FP6 blocker coverage mismatch")
    if any(count != 1 for count in blocker_counts.values()):
        errors.append("FP6 blocker has multiple decision owners")

    identifiers = set(by_id)
    for entry in components:
        if not set(entry["dependencies"]) <= identifiers:
            errors.append("component dependency is missing")
        if entry["id"] in entry["dependencies"]:
            errors.append("component cannot depend on itself")
        state = entry["decision_state"]
        disposition = entry["public_disposition"]
        public_prebuilt_refs = set(entry.get("public_prebuilt_refs", []))
        if disposition is None and state not in INVESTIGATION_STATES:
            errors.append("component without disposition is not investigative")
        if disposition is not None and state != "accepted":
            errors.append("public disposition is not accepted")
        if not public_prebuilt_refs <= set(entry["inventory_refs"]):
            errors.append("public prebuilt reference is outside component")
        if disposition == "necessary-prebuilt":
            if not public_prebuilt_refs:
                errors.append("necessary prebuilt has no exact allowed references")
        elif public_prebuilt_refs:
            errors.append(
                "non-prebuilt disposition carries public prebuilt references")

        private = entry["private_bringup"]
        required_refs = set(private["required_prebuilt_refs"])
        if not required_refs <= set(entry["inventory_refs"]):
            errors.append("private bring-up prebuilt reference is outside component")
        if private["eligible"]:
            if (state not in {"private-bringup", "accepted"}
                    or (state == "accepted"
                        and disposition != "necessary-prebuilt")
                    or not private["verified_necessary_for"]
                    or not required_refs):
                errors.append("private bring-up eligibility lacks necessary evidence")
        elif required_refs or private["verified_necessary_for"]:
            errors.append("ineligible private bring-up carries prebuilt approval")

        candidates = entry["source_candidates"]
        candidate_ids = [candidate["id"] for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            errors.append("duplicate source candidate identifier")
        for candidate in candidates:
            if (candidate["compatibility"] == "mismatched"
                    and candidate["assessment"] == "qualified"):
                errors.append("mismatched source candidate is called qualified")
        evidence_kinds = {
            evidence["kind"] for evidence in entry["qualification_evidence"]
        }
        if disposition is not None:
            missing_evidence = REQUIRED_PUBLIC_EVIDENCE[disposition] - \
                evidence_kinds
            if missing_evidence:
                errors.append(
                    "public disposition lacks required evidence classes")
        if disposition == "source-built":
            qualified = [candidate for candidate in candidates
                         if candidate["assessment"] == "qualified"
                         and candidate["compatibility"] == "exact"]
            if not qualified:
                errors.append("source-built disposition lacks exact qualification")
        if disposition in {"removed", "research-only"} and private["eligible"]:
            errors.append("absent public disposition cannot be private-bringup eligible")

        for ref in required_refs:
            origin = known_refs.get(ref, {}).get("origin", "")
            if not any(marker in origin
                       for marker in ("prebuilt", "firmware", "opaque")):
                errors.append(
                    "private bring-up reference is not classified non-source/prebuilt")
        for ref in public_prebuilt_refs:
            origin = known_refs.get(ref, {}).get("origin", "")
            if not any(marker in origin
                       for marker in ("prebuilt", "firmware", "opaque")):
                errors.append(
                    "public reference is not classified non-source/prebuilt")

    if _has_cycle(components):
        errors.append("component dependency graph contains a cycle")

    project_ids = [entry["id"] for entry in model["components"]]
    if len(project_ids) != len(set(project_ids)):
        errors.append("duplicate project dependency identifier")
    if set(project_ids) & identifiers:
        errors.append("project and FP6 component identifiers overlap")
    return errors[:MAX_ERRORS]


def validate_closure(model, closure, *, model_sha256, public=False):
    errors = _schema_errors(closure, "component-closure.schema.json")
    if errors:
        return errors[:MAX_ERRORS]
    if closure["model_sha256"] != model_sha256:
        errors.append("component closure model hash mismatch")
    stock = model["fp6_model"]["inputs"]["selected_stock"]
    if (closure["stock_build"] != stock["build"]
            or closure["region"] != stock["region"]):
        errors.append("component closure stock identity mismatch")

    components = {entry["id"]: entry for entry in model["fp6_components"]}
    results = closure["component_results"]
    result_ids = [entry["component_id"] for entry in results]
    if Counter(result_ids) != Counter(components.keys()):
        errors.append("component closure coverage mismatch")

    artifacts = closure["artifacts"]
    paths = [entry["path"] for entry in artifacts]
    if len(paths) != len(set(paths)):
        errors.append("duplicate component artifact path")
    path_set = set(paths)
    artifact_by_path = {entry["path"]: entry for entry in artifacts}
    for artifact in artifacts:
        component = components.get(artifact["component_id"])
        if component is None:
            errors.append("artifact maps to unknown component")
        elif artifact["inventory_ref"] not in component["inventory_refs"]:
            errors.append("artifact source reference is not owned by component")
        if not set(artifact["dependencies"]) <= path_set:
            errors.append("artifact dependency is absent from closure")
        if artifact["path"] in artifact["dependencies"]:
            errors.append("artifact cannot depend on itself")
        if component is not None:
            allowed_components = set(component["dependencies"]) | {
                component["id"]}
            for dependency in artifact["dependencies"]:
                target = artifact_by_path.get(dependency)
                if (target is not None
                        and target["component_id"] not in allowed_components):
                    errors.append("artifact dependency violates component graph")

    for result in results:
        component = components.get(result["component_id"])
        if component is None:
            continue
        result_paths = set(result["artifact_paths"])
        actual_paths = {path for path, artifact in artifact_by_path.items()
                        if artifact["component_id"] == result["component_id"]}
        if result_paths != actual_paths:
            errors.append("component result artifact mapping mismatch")
        if (result["presence"] == "present") != bool(result_paths):
            errors.append("component presence disagrees with artifact mapping")

        disposition = component["public_disposition"]
        if public:
            if component["decision_state"] != "accepted" or disposition is None:
                errors.append("public closure contains unaccepted component")
            elif disposition in {"removed", "research-only"} and result_paths:
                errors.append("publicly absent component has artifacts")
            elif disposition in {"source-built", "necessary-prebuilt"} and not result_paths:
                errors.append("publicly shipped component has no artifact")
            has_prebuilt = False
            for path in result_paths:
                artifact = artifact_by_path.get(path)
                if artifact is None:
                    continue
                kind = artifact["source_or_prebuilt"]
                has_prebuilt = has_prebuilt or kind == "prebuilt"
                if disposition == "source-built" and kind != "source-built":
                    errors.append("source-built component contains a prebuilt artifact")
                if (disposition == "necessary-prebuilt" and kind == "prebuilt"
                        and artifact["inventory_ref"] not in
                        component.get("public_prebuilt_refs", [])):
                    errors.append(
                        "public prebuilt is not an exact allowed reference")
            if (disposition == "necessary-prebuilt" and result_paths
                    and not has_prebuilt):
                errors.append(
                    "necessary-prebuilt component has no prebuilt artifact")
        else:
            for path in result_paths:
                artifact = artifact_by_path.get(path)
                if artifact is None:
                    continue
                if artifact["source_or_prebuilt"] == "prebuilt":
                    private = component["private_bringup"]
                    accepted = (
                        disposition == "necessary-prebuilt"
                        and artifact["inventory_ref"] in
                        component.get("public_prebuilt_refs", []))
                    temporary = (
                        private["eligible"]
                        and private["verified_necessary_for"]
                        and artifact["inventory_ref"] in
                        private["required_prebuilt_refs"])
                    if not accepted and not temporary:
                        errors.append("private prebuilt is not declared necessary")
    return errors[:MAX_ERRORS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path,
                        default=ROOT / "config" / "components.json")
    parser.add_argument("--sources", type=Path,
                        default=ROOT / "config" / "fp6-sources.json")
    parser.add_argument("--build-environment", type=Path,
                        default=ROOT / "config" / "build-environment.json")
    parser.add_argument("--closure", type=Path)
    parser.add_argument("--public", action="store_true",
                        help="require every closure component to have an accepted public disposition")
    args = parser.parse_args(argv)
    if args.public and not args.closure:
        print("--public requires --closure", file=sys.stderr)
        return 2
    try:
        closure = None
        model = load_json(args.model)
        sources = load_json(args.sources)
        build_environment = load_json(args.build_environment)
        model_sha256 = file_sha256(args.model)
        errors = validate_model(
            model, sources, build_environment,
            source_sha256=file_sha256(args.sources))
        if not errors and args.closure:
            closure = load_json(args.closure)
            errors = validate_closure(
                model, closure, model_sha256=model_sha256,
                public=args.public)
    except ComponentError as error:
        errors = [str(error)]
        model_sha256 = "unavailable"
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print("VALID component model: "
          f"{len(model['fp6_components'])} FP6 categories; "
          f"{len(model['components'])} project dependencies; "
          f"sha256={model_sha256}.")
    if args.closure:
        scope = "public-promotion" if args.public else "private-bringup"
        print(f"VALID {scope} closure: {len(closure['artifacts'])} artifacts.")
    else:
        print("No artifact closure, build result or public qualification is implied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
