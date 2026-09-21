"""Fail-closed verification of the declared Android build environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from . import process


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "build-environment.json"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
MAX_CONFIG_BYTES = 262_144
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024


class BuildError(RuntimeError):
    """A bounded build-input verification failure."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise BuildError(f"unable to read required file: {path}") from None
    return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BuildError("configuration contains a duplicate JSON key")
        result[key] = value
    return result


def load_config(path: Path) -> tuple[dict, bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        raise BuildError("unable to read build-environment configuration") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise BuildError("build-environment configuration exceeds its byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=lambda _value: (_ for _ in ()).throw(
                               BuildError("configuration contains a non-finite number")))
    except BuildError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise BuildError("build-environment configuration is invalid") from None
    if not isinstance(value, dict):
        raise BuildError("build-environment configuration must be an object")
    return value, raw


def _require_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BuildError(f"{label} fields do not match the supported schema")


def _require_sha(value, label, expression=SHA256_RE):
    if not isinstance(value, str) or expression.fullmatch(value) is None:
        raise BuildError(f"{label} is not an immutable digest")


def validate_config(config: dict) -> None:
    expected_root = {
        "schema_version", "environment_id", "scope", "upstream", "host",
        "workspace", "project_inputs", "device_inputs", "build",
    }
    if "composition" in config:
        expected_root.add("composition")
        composition = config["composition"]
        _require_keys(composition, {"overlay_sha256", "overlay_revision",
                                   "project_count", "project_map_sha256"}, "composition")
        for key in ("overlay_sha256", "project_map_sha256"):
            if not isinstance(composition[key], str) or not SHA256_RE.fullmatch(composition[key]):
                raise BuildError("invalid composition digest")
        if not isinstance(composition["overlay_revision"], str) or not SHA1_RE.fullmatch(
                composition["overlay_revision"]):
            raise BuildError("invalid overlay revision")
        if type(composition["project_count"]) is not int or composition["project_count"] < 1:
            raise BuildError("invalid composed project count")
    _require_keys(config, expected_root, "configuration")
    if config["schema_version"] != 1:
        raise BuildError("unsupported build-environment schema version")
    if not isinstance(config["environment_id"], str) or not SAFE_ID_RE.fullmatch(
            config["environment_id"]):
        raise BuildError("invalid environment id")
    if not isinstance(config["scope"], str) or not config["scope"]:
        raise BuildError("environment scope is missing")

    upstream_keys = {
        "manifest_url", "release_tag", "release_ref", "tag_object",
        "peeled_commit", "default_manifest_sha256", "project_count",
        "project_map_sha256", "project_map_format", "allowed_signers_url",
        "allowed_signers_sha256", "signer_identity", "signer_key_fingerprint",
        "repo_tool", "release_scope", "verified_on",
    }
    upstream = config["upstream"]
    _require_keys(upstream, upstream_keys, "upstream")
    if upstream["release_ref"] != "refs/tags/" + upstream["release_tag"]:
        raise BuildError("release ref is not bound to the declared tag")
    for field in ("tag_object", "peeled_commit"):
        _require_sha(upstream[field], f"upstream {field}", SHA1_RE)
    for field in ("default_manifest_sha256", "project_map_sha256",
                  "allowed_signers_sha256"):
        _require_sha(upstream[field], f"upstream {field}")
    if (not isinstance(upstream["project_count"], int)
            or upstream["project_count"] < 1):
        raise BuildError("upstream project count is invalid")
    if not upstream["manifest_url"].startswith("https://"):
        raise BuildError("manifest URL must use HTTPS")
    if not upstream["allowed_signers_url"].startswith("https://"):
        raise BuildError("allowed-signers URL must use HTTPS")
    repo_tool = upstream["repo_tool"]
    _require_keys(repo_tool, {
        "url", "release_tag", "tag_object", "peeled_commit", "verification",
    }, "repo tool")
    if not repo_tool["url"].startswith("https://"):
        raise BuildError("repo tool URL must use HTTPS")
    if not isinstance(repo_tool["release_tag"], str) or not SAFE_ID_RE.fullmatch(
            repo_tool["release_tag"]):
        raise BuildError("repo tool release tag is invalid")
    for field in ("tag_object", "peeled_commit"):
        _require_sha(repo_tool[field], f"repo tool {field}", SHA1_RE)
    if repo_tool["verification"] != "repo-launcher-gpg-required":
        raise BuildError("repo tool verification policy is invalid")

    host_keys = {
        "architecture", "os_id", "os_version_id", "upstream_support_status",
        "minimum_memory_bytes", "minimum_source_free_bytes",
        "minimum_build_free_bytes", "required_packages", "external_tools",
    }
    host = config["host"]
    _require_keys(host, host_keys, "host")
    for field in ("minimum_memory_bytes", "minimum_source_free_bytes",
                  "minimum_build_free_bytes"):
        if not isinstance(host[field], int) or host[field] <= 0:
            raise BuildError(f"host {field} is invalid")
    packages = host["required_packages"]
    if (not isinstance(packages, dict) or not packages
            or any(not isinstance(key, str) or not isinstance(value, str)
                   or not key or not value for key, value in packages.items())):
        raise BuildError("required package pins are invalid")
    tools = host["external_tools"]
    if not isinstance(tools, dict) or set(tools) != {"node", "npm", "corepack", "yarn"}:
        raise BuildError("external tool pins are invalid")
    _require_keys(tools["node"], {"version", "archive_sha256"}, "Node.js pin")
    _require_keys(tools["npm"], {"version"}, "npm pin")
    _require_keys(tools["corepack"], {"version"}, "Corepack pin")
    _require_keys(tools["yarn"], {
        "version", "npm_integrity", "source_lockfile_sha256",
    }, "Yarn pin")
    for name in ("node", "npm", "corepack", "yarn"):
        if not isinstance(tools[name]["version"], str) or not tools[name]["version"]:
            raise BuildError(f"external tool version is invalid: {name}")
    _require_sha(tools["node"].get("archive_sha256"), "Node.js archive")
    _require_sha(tools["yarn"].get("source_lockfile_sha256"), "Yarn lockfile")
    if not isinstance(tools["yarn"]["npm_integrity"], str) or not tools["yarn"][
            "npm_integrity"].startswith("sha512-"):
        raise BuildError("Yarn registry integrity is invalid")

    workspace_keys = {
        "source_subdirectory", "cache_subdirectory", "output_subdirectory",
        "roots_must_be_distinct", "source_and_output_must_not_be_nested",
        "cache_policy", "clean_build_policy",
    }
    workspace = config["workspace"]
    _require_keys(workspace, workspace_keys, "workspace")
    for field in ("source_subdirectory", "cache_subdirectory", "output_subdirectory"):
        part = Path(workspace[field])
        if part.is_absolute() or ".." in part.parts or not part.parts:
            raise BuildError(f"workspace {field} must be a safe relative path")
    if workspace["roots_must_be_distinct"] is not True:
        raise BuildError("workspace roots must be distinct")
    nesting_prohibited = workspace["source_and_output_must_not_be_nested"]
    if not isinstance(nesting_prohibited, bool):
        raise BuildError("source/output nesting policy must be boolean")
    source_part = Path(workspace["source_subdirectory"])
    output_part = Path(workspace["output_subdirectory"])
    output_nested = _is_within(output_part, source_part)
    if nesting_prohibited and output_nested:
        raise BuildError("declared output contradicts the nesting policy")
    if not nesting_prohibited and not output_nested:
        raise BuildError("declared output must be nested below the source root")

    inputs = config["project_inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise BuildError("project inputs must be a non-empty list")
    ids = set()
    for item in inputs:
        _require_keys(item, {"id", "repository_path", "sha256"}, "project input")
        if not isinstance(item["id"], str) or not SAFE_ID_RE.fullmatch(item["id"]):
            raise BuildError("project input id is invalid")
        if item["id"] in ids:
            raise BuildError("project input ids are not unique")
        ids.add(item["id"])
        path = Path(item["repository_path"])
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise BuildError("project input path is unsafe")
        _require_sha(item["sha256"], "project input hash")

    device = config["device_inputs"]
    _require_keys(device, {
        "selected_stock_build", "selected_stock_factory_sha256",
        "generated_input_manifest_required", "generated_input_manifest_status",
        "device_unique_material_allowed",
    }, "device inputs")
    _require_sha(device["selected_stock_factory_sha256"], "stock factory input")
    if device["generated_input_manifest_required"] is not True:
        raise BuildError("generated device-input manifest must be required")
    if device["device_unique_material_allowed"] is not False:
        raise BuildError("device-unique input material must be prohibited")

    build = config["build"]
    _require_keys(build, {
        "shell", "generic_qualification_target", "generic_qualification_command",
        "fp6_release_target", "production_signing_material_allowed",
    }, "build")
    if build["production_signing_material_allowed"] is not False:
        raise BuildError("production signing material must be prohibited")


def declared_identity(config: dict, raw: bytes, project_root: Path) -> dict:
    input_hashes = {}
    for item in config["project_inputs"]:
        path = project_root / item["repository_path"]
        observed = sha256_file(path)
        if observed != item["sha256"]:
            raise BuildError(f"project input hash mismatch: {item['id']}")
        input_hashes[item["id"]] = observed
    identity = {
        "environment_config_sha256": sha256_bytes(raw),
        "environment_id": config["environment_id"],
        "manifest_tag_object": config["upstream"]["tag_object"],
        "manifest_commit": config["upstream"]["peeled_commit"],
        "repo_tool_tag_object": config["upstream"]["repo_tool"]["tag_object"],
        "repo_tool_commit": config["upstream"]["repo_tool"]["peeled_commit"],
        "project_map_sha256": config["upstream"]["project_map_sha256"],
        "project_inputs": input_hashes,
        "selected_stock_factory_sha256": config["device_inputs"][
            "selected_stock_factory_sha256"],
    }
    if "composition" in config:
        identity["composed_project_map_sha256"] = config["composition"]["project_map_sha256"]
        identity["source_overlay_sha256"] = config["composition"]["overlay_sha256"]
        identity["source_overlay_revision"] = config["composition"]["overlay_revision"]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    identity["declared_build_identity_sha256"] = sha256_bytes(encoded)
    return identity


def _run(command, cwd=None, env=None, timeout=120) -> subprocess.CompletedProcess:
    result = process.run(command, timeout, MAX_COMMAND_OUTPUT_BYTES, cwd=cwd, env=env)
    if result["transport"] != "ok":
        raise BuildError(f"command failed closed: {command[0]}")
    return subprocess.CompletedProcess(command, result["returncode"],
                                       result["stdout"], result["stderr"])


def _read_os_release(path=Path("/etc/os-release")) -> dict:
    result = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise BuildError("unable to read host OS identity") from None
    for line in lines:
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def verify_host(config: dict, thermal_check: Path) -> dict:
    host = config["host"]
    os_release = _read_os_release()
    if platform.machine() != host["architecture"]:
        raise BuildError("host architecture does not match the pin")
    if os_release.get("ID") != host["os_id"] or os_release.get("VERSION_ID") != host["os_version_id"]:
        raise BuildError("host operating system does not match the pin")

    package_names = sorted(host["required_packages"])
    query = _run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n",
                  *package_names])
    observed_packages = {}
    for raw_line in query.stdout.decode("utf-8", "strict").splitlines():
        package, version, status = raw_line.split("\t")
        if not status.startswith("ii"):
            raise BuildError(f"required package is not installed: {package}")
        observed_packages[package] = version
    if observed_packages != host["required_packages"]:
        raise BuildError("installed package versions do not match the environment pin")

    commands = {
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "corepack": ["corepack", "--version"],
        "yarn": ["yarn", "--version"],
    }
    observed_tools = {}
    for name, command in commands.items():
        expected = host["external_tools"][name]["version"]
        result = _run(command)
        observed = result.stdout.decode("utf-8", "strict").strip()
        if observed != expected:
            raise BuildError(f"external tool version mismatch: {name}")
        observed_tools[name] = observed

    memory_line = _run(["awk", "/^MemTotal:/ {print $2}", "/proc/meminfo"])
    try:
        memory_bytes = int(memory_line.stdout.decode().strip()) * 1024
    except ValueError:
        raise BuildError("unable to parse host memory") from None
    if memory_bytes < host["minimum_memory_bytes"]:
        raise BuildError("host memory is below the declared minimum")

    if (not thermal_check.is_absolute() or not thermal_check.is_file() or
            not os.access(thermal_check, os.X_OK)):
        raise BuildError("builder thermal-safety preflight is unavailable")
    resolved_thermal_check = thermal_check.resolve()
    for controlled_path in (resolved_thermal_check,
                            *resolved_thermal_check.parents):
        mode = controlled_path.stat().st_mode
        if controlled_path.stat().st_uid != 0 or mode & 0o022:
            raise BuildError(
                "builder thermal-safety path is not root-controlled")
    _run([str(resolved_thermal_check)], timeout=30)

    package_set = _run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"])
    package_lines = sorted(package_set.stdout.decode("utf-8", "strict").splitlines())
    package_set_bytes = ("\n".join(package_lines) + "\n").encode()
    return {
        "architecture": platform.machine(),
        "os_id": os_release.get("ID"),
        "os_version_id": os_release.get("VERSION_ID"),
        "kernel": platform.release(),
        "memory_bytes": memory_bytes,
        "required_packages": observed_packages,
        "external_tools": observed_tools,
        "installed_package_set_sha256": sha256_bytes(package_set_bytes),
        "thermal_safety_preflight": "PASS",
    }


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def verify_workspace(config: dict, source: Path, cache: Path, output: Path,
                     require_empty_output: bool = False) -> dict:
    roots = [path.resolve() for path in (source, cache, output)]
    if len(set(roots)) != 3:
        raise BuildError("source, cache and output roots must be distinct")
    if _is_within(roots[0], roots[2]):
        raise BuildError("source root must not be nested below output")
    output_nested = _is_within(roots[2], roots[0])
    nesting_prohibited = config["workspace"][
        "source_and_output_must_not_be_nested"]
    if nesting_prohibited and output_nested:
        raise BuildError("source and output roots must not be nested")
    if not nesting_prohibited:
        source_part = Path(config["workspace"]["source_subdirectory"])
        output_part = Path(config["workspace"]["output_subdirectory"])
        nested_relative = output_part.relative_to(source_part)
        if roots[2] != (roots[0] / nested_relative).resolve():
            raise BuildError(
                "output root does not match its declared source-local path")
    for path in roots:
        if not path.is_dir() or path.is_symlink():
            raise BuildError(f"workspace root is absent or unsafe: {path}")
    if require_empty_output and next(roots[2].iterdir(), None) is not None:
        raise BuildError("clean-build output root is not empty")
    source_usage = shutil.disk_usage(roots[0])
    output_usage = shutil.disk_usage(roots[2])
    host = config["host"]
    if source_usage.free < host["minimum_source_free_bytes"]:
        raise BuildError("source filesystem free space is below the declared minimum")
    if output_usage.free < host["minimum_build_free_bytes"]:
        raise BuildError("output filesystem free space is below the declared minimum")
    if os.stat(roots[0]).st_dev == os.stat(roots[2]).st_dev:
        combined = host["minimum_source_free_bytes"] + host["minimum_build_free_bytes"]
        if source_usage.free < combined:
            raise BuildError("shared source/output filesystem lacks combined free space")
    return {
        "source_root": str(roots[0]),
        "cache_root": str(roots[1]),
        "output_root": str(roots[2]),
        "source_free_bytes": source_usage.free,
        "output_free_bytes": output_usage.free,
        "source_output_same_filesystem": os.stat(roots[0]).st_dev == os.stat(roots[2]).st_dev,
    }


def parse_project_map(xml_bytes: bytes) -> tuple[list[tuple[str, str, str, str]], str]:
    if len(xml_bytes) > MAX_MANIFEST_BYTES:
        raise BuildError("resolved manifest exceeds its byte limit")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise BuildError("resolved manifest is not valid XML") from None
    default = root.find("default")
    default_remote = default.get("remote") if default is not None else None
    rows = []
    paths = set()
    for project in root.findall("project"):
        name = project.get("name")
        path = project.get("path", name)
        remote = project.get("remote", default_remote)
        revision = project.get("revision")
        if not all(isinstance(value, str) and value for value in
                   (name, path, remote, revision)):
            raise BuildError("resolved manifest project is incomplete")
        if SHA1_RE.fullmatch(revision) is None:
            raise BuildError("resolved manifest contains a moving or non-commit revision")
        if path.startswith("/") or ".." in Path(path).parts or path in paths:
            raise BuildError("resolved manifest contains an unsafe or duplicate path")
        paths.add(path)
        rows.append((path, name, remote, revision))
    if not rows:
        raise BuildError("resolved manifest contains no projects")
    rows.sort()
    encoded = "".join("\t".join(row) + "\n" for row in rows).encode()
    return rows, sha256_bytes(encoded)


def _manifest_exports(xml_bytes: bytes) -> list[tuple[str, str, str, str]]:
    """Bind repo-created files as well as project commits for the flat manifest."""
    if len(xml_bytes) > MAX_MANIFEST_BYTES:
        raise BuildError("manifest exceeds its byte limit")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise BuildError("manifest is not valid XML") from None
    if any(root.find(tag) is not None for tag in
           ("include", "extend-project", "remove-project")):
        raise BuildError("manifest composition needs an explicit export binding")
    exports, destinations = [], set()
    for project in root.findall("project"):
        project_path = project.get("path", project.get("name"))
        for entry in project:
            if entry.tag not in ("copyfile", "linkfile"):
                continue
            origin, destination = entry.get("src"), entry.get("dest")
            for value in (project_path, destination):
                if not _source_relative_path(value):
                    raise BuildError("manifest export has an unsafe path")
            if not (origin == "." and entry.tag == "linkfile") and not _source_relative_path(origin):
                raise BuildError("manifest export has an unsafe source")
            if destination in destinations:
                raise BuildError("manifest export destination is duplicated")
            destinations.add(destination)
            exports.append((entry.tag, project_path, origin, destination))
    return sorted(exports)


def _source_relative_path(value) -> bool:
    return (isinstance(value, str) and len(value) <= 4096
            and re.fullmatch(r"[A-Za-z0-9._+@/-]+", value) is not None
            and all(part not in ("", ".", "..") for part in value.split("/")))


def compose_source_manifest(config: dict, source: Path, signed_xml: bytes) -> bytes:
    """Authenticate one additive overlay; upstream replacements need separate review."""
    directory = source / ".repo/local_manifests"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise BuildError("local manifest directory is invalid")
    entries = sorted(directory.iterdir()) if directory.exists() else []
    composition = config.get("composition")
    if composition is None:
        if entries:
            raise BuildError("local manifests are not declared by this environment")
        return signed_xml
    path = directory / "diamaneos.xml"
    if entries != [path] or path.is_symlink() or not path.is_file():
        raise BuildError("expected exactly the declared diamaneos.xml overlay")
    with path.open("rb") as stream:
        overlay = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(overlay) > MAX_MANIFEST_BYTES or sha256_bytes(overlay) != composition["overlay_sha256"]:
        raise BuildError("declared overlay content mismatch")
    if b"<!DOCTYPE" in overlay.upper() or b"<!ENTITY" in overlay.upper():
        raise BuildError("overlay declarations are not supported")
    try:
        base, addition = ET.fromstring(signed_xml), ET.fromstring(overlay)
    except ET.ParseError:
        raise BuildError("source composition is not valid XML") from None
    if addition.tag != "manifest" or addition.attrib:
        raise BuildError("invalid overlay root")
    remotes = {entry.get("name") for entry in base.findall("remote")}
    paths = {entry.get("path", entry.get("name")) for entry in base.findall("project")}
    names = {entry.get("name") for entry in base.findall("project")}
    for entry in addition:
        if entry.tag == "remote":
            if (set(entry.attrib) != {"name", "fetch"} or len(entry)
                    or not SAFE_ID_RE.fullmatch(entry.get("name", ""))
                    or not entry.get("fetch", "").startswith("https://")
                    or entry.get("name") in remotes):
                raise BuildError("invalid or redefined overlay remote")
            remotes.add(entry.get("name"))
        elif entry.tag == "project":
            if set(entry.attrib) != {"name", "path", "remote", "revision"} or len(entry):
                raise BuildError("overlay projects must be explicit and contain no exports")
            name, project_path = entry.get("name"), entry.get("path")
            if (not _source_relative_path(name) or not _source_relative_path(project_path)
                    or not SHA1_RE.fullmatch(entry.get("revision", ""))
                    or entry.get("remote") not in remotes or name in names
                    or any(project_path == old or project_path.startswith(old + "/")
                           or old.startswith(project_path + "/") for old in paths)):
                raise BuildError("invalid, overlapping or replaced overlay project")
            if project_path.split("/")[0] in (".repo", "out"):
                raise BuildError("overlay project overlaps metadata or output")
            paths.add(project_path)
            names.add(name)
        else:
            raise BuildError("only additive remote and project entries are supported")
        base.append(entry)
    composed = ET.tostring(base, encoding="utf-8")
    rows, digest = parse_project_map(composed)
    if len(rows) != composition["project_count"] or digest != composition["project_map_sha256"]:
        raise BuildError("composed manifest does not match its declared project map")
    return composed


def verify_source_layout(config: dict, source: Path, rows, signed_xml: bytes,
                         resolved_xml: bytes) -> None:
    """Reject undeclared inputs outside projects without traversing build output.

    Git status covers each project's files. This covers the gaps between those
    projects and authenticates the manifest's copy/link files, which the project
    commit map alone does not describe.
    """
    source = source.resolve(strict=True)
    signed_xml = compose_source_manifest(config, source, signed_xml)
    exports = _manifest_exports(signed_xml)
    if exports != _manifest_exports(resolved_xml):
        raise BuildError("resolved manifest exports differ from the signed manifest")

    projects = {row[0] for row in rows}
    if any(not _source_relative_path(path) for path in projects):
        raise BuildError("source project path is unsafe")
    # All output generations in the declared top-level output container are
    # build artifacts. In particular a VTS subdirectory does not turn sibling
    # generic-build output into source input.
    excluded = {".repo"}
    workspace = config["workspace"]
    declared_source = Path(workspace["source_subdirectory"])
    declared_output = Path(workspace["output_subdirectory"])
    if _is_within(declared_output, declared_source):
        relative_output = declared_output.relative_to(declared_source)
        if not relative_output.parts:
            raise BuildError("source and output roots must be distinct")
        excluded.add(relative_output.parts[0])
    exported = {entry[3] for entry in exports}
    if any(Path(path).parts[0] in excluded for path in projects | exported):
        raise BuildError("declared source overlaps metadata or build output")
    containers = {parent.as_posix() for path in projects | exported
                  for parent in Path(path).parents if parent != Path(".")}

    for project in projects:
        current = source / project
        while current != source:
            if current.is_symlink() or not current.is_dir():
                raise BuildError("source project has a missing or redirected directory")
            current = current.parent
    pending = [source]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            relative = entry.relative_to(source).as_posix()
            if relative in excluded or relative in projects:
                if entry.is_symlink() or not entry.is_dir():
                    raise BuildError("source directory is redirected or not a directory")
            elif relative in exported:
                continue  # Contents and exact targets checked below.
            elif relative in containers and entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            else:
                raise BuildError("source tree contains an undeclared input outside its projects")

    for kind, project, origin, destination in exports:
        expected = source / project / origin
        target = source / destination
        if not expected.exists() or not target.exists():
            raise BuildError("manifest export source or destination is missing")
        if not _is_within(expected.resolve(strict=True), source):
            raise BuildError("manifest export source escapes the checkout")
        if not _is_within(target.resolve(strict=True), source):
            raise BuildError("manifest export destination escapes the checkout")
        if kind == "linkfile":
            if not target.is_symlink() or target.resolve(strict=True) != expected.resolve(strict=True):
                raise BuildError("manifest linkfile target mismatch")
        elif (target.is_symlink() or not target.is_file() or not expected.is_file()
              or sha256_file(target) != sha256_file(expected)):
            raise BuildError("manifest copyfile content mismatch")


def verify_manifest_checkout(config: dict, source: Path, allowed_signers: Path) -> dict:
    upstream = config["upstream"]
    if sha256_file(allowed_signers) != upstream["allowed_signers_sha256"]:
        raise BuildError("allowed-signers file hash mismatch")
    manifests = source / ".repo" / "manifests"
    if not manifests.is_dir():
        raise BuildError("source checkout lacks .repo/manifests")
    repo_tool = source / ".repo" / "repo"
    repo_pin = upstream["repo_tool"]
    if not repo_tool.is_dir():
        raise BuildError("source checkout lacks the pinned repo implementation")
    repo_origin = _run(["git", "-C", str(repo_tool), "remote", "get-url", "origin"])
    if repo_origin.stdout.decode("utf-8", "strict").strip() != repo_pin["url"]:
        raise BuildError("repo implementation origin does not match the authoritative URL")
    repo_head = _run(["git", "-C", str(repo_tool), "rev-parse", "HEAD"])
    if repo_head.stdout.decode().strip() != repo_pin["peeled_commit"]:
        raise BuildError("repo implementation commit does not match the pin")
    repo_tag = repo_pin["release_tag"]
    repo_tag_object = _run([
        "git", "-C", str(repo_tool), "rev-parse", f"{repo_tag}^{{tag}}",
    ])
    if repo_tag_object.stdout.decode().strip() != repo_pin["tag_object"]:
        raise BuildError("repo implementation tag object does not match the pin")
    repo_peeled = _run([
        "git", "-C", str(repo_tool), "rev-parse", f"{repo_tag}^{{}}",
    ])
    if repo_peeled.stdout.decode().strip() != repo_pin["peeled_commit"]:
        raise BuildError("repo implementation tag commit does not match the pin")
    repo_status = _run(["git", "-C", str(repo_tool), "status", "--porcelain=v1",
                        "--untracked-files=all"]).stdout
    if repo_status:
        raise BuildError("repo implementation contains dirty or untracked content")
    repo_verify_env = os.environ.copy()
    repo_verify_env["GNUPGHOME"] = str(Path.home() / ".repoconfig" / "gnupg")
    _run(["git", "-C", str(repo_tool), "verify-tag", repo_tag],
         env=repo_verify_env)
    origin = _run(["git", "-C", str(manifests), "remote", "get-url", "origin"])
    if origin.stdout.decode("utf-8", "strict").strip() != upstream["manifest_url"]:
        raise BuildError("manifest origin does not match the authoritative URL")
    tag = upstream["release_tag"]
    tag_object = _run(["git", "-C", str(manifests), "rev-parse", f"{tag}^{{tag}}"])
    if tag_object.stdout.decode().strip() != upstream["tag_object"]:
        raise BuildError("manifest tag object does not match the pin")
    peeled = _run(["git", "-C", str(manifests), "rev-parse", f"{tag}^{{}}"])
    if peeled.stdout.decode().strip() != upstream["peeled_commit"]:
        raise BuildError("manifest tag commit does not match the pin")
    manifest_head = _run(["git", "-C", str(manifests), "rev-parse", "HEAD"])
    if manifest_head.stdout.decode().strip() != upstream["peeled_commit"]:
        raise BuildError("manifest checkout HEAD does not match the signed release")
    signature = _run(["git", "-C", str(manifests), "-c",
                      f"gpg.ssh.allowedSignersFile={allowed_signers}",
                      "verify-tag", tag])
    signature_text = (signature.stdout + signature.stderr).decode("utf-8", "replace")
    if (upstream["signer_identity"] not in signature_text
            or upstream["signer_key_fingerprint"] not in signature_text):
        raise BuildError("manifest signature did not report the pinned signer")
    default_xml = _run(["git", "-C", str(manifests), "show", f"{tag}:default.xml"])
    if sha256_bytes(default_xml.stdout) != upstream["default_manifest_sha256"]:
        raise BuildError("signed tag default manifest hash mismatch")

    resolved = _run(["repo", "manifest", "-r"], cwd=source, timeout=300).stdout
    rows, project_map_sha256 = parse_project_map(resolved)
    declared = config.get("composition", upstream)
    # Independently bind the signed base before authenticating its additions.
    base_rows, base_digest = parse_project_map(default_xml.stdout)
    if len(base_rows) != upstream["project_count"] or base_digest != upstream["project_map_sha256"]:
        raise BuildError("signed upstream project map mismatch")
    composed = compose_source_manifest(config, source, default_xml.stdout)
    if len(rows) != declared["project_count"]:
        raise BuildError("resolved manifest project count mismatch")
    if project_map_sha256 != declared["project_map_sha256"]:
        raise BuildError("resolved manifest project map mismatch")
    # Remote URLs affect where repo obtains source, even when commit pins match.
    remote_attributes = lambda data: sorted(tuple(sorted(e.attrib.items()))
                                           for e in ET.fromstring(data).findall("remote"))
    if remote_attributes(composed) != remote_attributes(resolved):
        raise BuildError("resolved manifest remotes differ from declared sources")
    verify_source_layout(config, source, rows, default_xml.stdout, resolved)

    dirty = []
    for path, _name, _remote, revision in rows:
        checkout = source / path
        if not checkout.is_dir():
            raise BuildError(f"source project is missing: {path}")
        head = _run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.decode().strip()
        if head != revision:
            raise BuildError(f"source project revision mismatch: {path}")
        status = _run(["git", "-C", str(checkout), "status", "--porcelain=v1",
                       "--untracked-files=all"]).stdout
        if status:
            dirty.append(path)
            if len(dirty) >= 20:
                break
    manifest_status = _run(["git", "-C", str(manifests), "status", "--porcelain=v1",
                            "--untracked-files=all"]).stdout
    if manifest_status:
        dirty.append(".repo/manifests")
    if dirty:
        raise BuildError("source checkout contains dirty or untracked content: "
                         + ", ".join(dirty))
    return {
        "repo_tool_release_tag": repo_tag,
        "repo_tool_tag_object": repo_pin["tag_object"],
        "repo_tool_commit": repo_pin["peeled_commit"],
        "repo_tool_signature_verification": "PASS",
        "release_tag": tag,
        "tag_object": upstream["tag_object"],
        "peeled_commit": upstream["peeled_commit"],
        "signature_verification": "PASS",
        "default_manifest_sha256": upstream["default_manifest_sha256"],
        "resolved_project_count": len(rows),
        "resolved_project_map_sha256": project_map_sha256,
        "source_clean": True,
        "source_layout_verified": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="verify pinned build inputs and a clean source checkout")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inputs-only", action="store_true",
                      help="validate declared cold-environment inputs without a host or checkout")
    mode.add_argument("--host-only", action="store_true",
                      help="validate the host and workspace before a source sync")
    parser.add_argument("--purpose", choices=("generic-qualification", "fp6"), default="fp6")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--require-empty-output", action="store_true",
                        help="reject any existing build intermediate or result")
    parser.add_argument("--allowed-signers", type=Path)
    parser.add_argument(
        "--thermal-check", "--fan-check", dest="thermal_check", type=Path,
        default=None,
        help=("absolute executable which exits zero only when the builder's "
              "current thermal and cooling state is safe; required for host "
              "and full preflight; --fan-check is a backwards-compatible alias"),
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        config, raw = load_config(args.config)
        validate_config(config)
        identity = declared_identity(config, raw, args.project_root)
        status = ("INPUTS_IDENTIFIED" if args.inputs_only else
                  "HOST_READY" if args.host_only else "PASS")
        result = {
            "schema_version": 1,
            "status": status,
            "purpose": args.purpose,
            "identity": identity,
            "release_signing_material_present": None,
            "release_signing_material_policy": "prohibited-not-inspected",
        }
        if args.inputs_only:
            result["required_runtime_inputs"] = {
                "source_checkout": "required-for-full-preflight",
                "allowed_signers_file": config["upstream"]["allowed_signers_sha256"],
                "generated_device_input_manifest": config["device_inputs"][
                    "generated_input_manifest_status"],
            }
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        workspace_paths = (args.source_root, args.cache_root, args.output_root)
        if any(value is None for value in workspace_paths):
            raise BuildError("host/full preflight requires source, cache and output paths")
        if args.thermal_check is None:
            raise BuildError(
                "host/full preflight requires an explicit thermal-check path")
        result["host"] = verify_host(config, args.thermal_check)
        result["workspace"] = verify_workspace(
            config, args.source_root, args.cache_root, args.output_root,
            require_empty_output=args.require_empty_output)
        if args.host_only:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.allowed_signers is None:
            raise BuildError("full preflight requires an allowed-signers path")
        if args.purpose == "fp6" and config["device_inputs"][
                "generated_input_manifest_status"] != "verified":
            raise BuildError("FP6 generated device-input manifest is not verified")
        result["source"] = verify_manifest_checkout(
            config, args.source_root, args.allowed_signers)
        runtime_identity = {
            "declared": identity["declared_build_identity_sha256"],
            "host_packages": result["host"]["installed_package_set_sha256"],
            "source_projects": result["source"]["resolved_project_map_sha256"],
            "purpose": args.purpose,
        }
        result["runtime_build_identity_sha256"] = sha256_bytes(
            json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode())
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except BuildError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError):
        print("ERROR: unable to inspect build inputs safely", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
