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
        raw = path.read_bytes()
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
    if workspace["source_and_output_must_not_be_nested"] is not True:
        raise BuildError("source and output nesting must be prohibited")

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
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    identity["declared_build_identity_sha256"] = sha256_bytes(encoded)
    return identity


def _run(command, cwd=None, env=None, timeout=120) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise BuildError(f"command failed to execute: {command[0]}") from None
    if len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(result.stderr) > MAX_COMMAND_OUTPUT_BYTES:
        raise BuildError(f"command output exceeded its limit: {command[0]}")
    if result.returncode != 0:
        raise BuildError(f"command failed closed: {command[0]}")
    return result


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
    if _is_within(roots[0], roots[2]) or _is_within(roots[2], roots[0]):
        raise BuildError("source and output roots must not be nested")
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
    if len(rows) != upstream["project_count"]:
        raise BuildError("resolved manifest project count mismatch")
    if project_map_sha256 != upstream["project_map_sha256"]:
        raise BuildError("resolved manifest project map mismatch")

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
        default=Path("/usr/local/sbin/diamaneos-builder-thermal-check"),
        help=("absolute executable which exits zero only when the builder's "
              "current thermal and cooling state is safe; --fan-check is a "
              "backwards-compatible alias"),
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
            "release_signing_material_present": False,
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


if __name__ == "__main__":
    sys.exit(main())
