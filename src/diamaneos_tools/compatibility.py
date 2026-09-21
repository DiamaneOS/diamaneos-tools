"""Version-bound compatibility-suite discovery, execution and result parsing."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import locale
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import signal
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from . import process, evidence

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None

from diamaneos_tools import rig
from diamaneos_tools import device
from diamaneos_tools import test_runner


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "test-suites.json"
BUILD_ENVIRONMENT = ROOT / "config" / "build-environment.json"
SCHEMA = ROOT / "schemas" / "test-suites.schema.json"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_RESULT_XML_BYTES = 64 * 1024 * 1024
MAX_TREE_FILES = 100_000
MAX_TREE_BYTES = 64 * 1024 * 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILTER_RE = re.compile(r"^[A-Za-z0-9_$#.:-]{1,512}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
SETUP_ATTESTATIONS = {
    "device_changes_authorized",
    "device_contains_no_daily_data",
    "result_storage_is_private",
    "teardown_understood",
}


class CompatibilityError(ValueError):
    """Bounded diagnostic with a stable CLI exit category."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompatibilityError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path, limit: int = MAX_CONFIG_BYTES):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise CompatibilityError("compatibility input is unreadable") from None
    if len(raw) > limit:
        raise CompatibilityError("compatibility input exceeds its byte limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except CompatibilityError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise CompatibilityError("compatibility input is not valid unique-key JSON") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise CompatibilityError("compatibility artifact is unreadable") from None
    return digest.hexdigest()


def _validate_registry(config: dict) -> list[str]:
    if Draft7Validator is None:
        return ["missing jsonschema; install the declared host dependency"]
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft7Validator.check_schema(schema)
    except (OSError, ValueError):
        return ["compatibility registry schema is unavailable or invalid"]
    errors = ["schema constraint failed: " + str(item.validator)
              for item in Draft7Validator(schema).iter_errors(config)]
    if errors:
        return errors[:40]
    try:
        environment = _load_json(BUILD_ENVIRONMENT)
    except CompatibilityError:
        return ["build environment is unavailable or invalid"]
    if config["target"]["candidate_environment_id"] != \
            environment.get("environment_id"):
        errors.append("compatibility candidate does not match build environment")
    for field in ("official_sources", "packages", "trial_profiles",
                  "fixtures", "applicability"):
        ids = [item["id"] for item in config[field]]
        if len(ids) != len(set(ids)):
            errors.append(f"compatibility registry has duplicate {field} ids")
    packages = {item["id"] for item in config["packages"]}
    fixtures = {item["id"] for item in config["fixtures"]}
    for profile in config["trial_profiles"]:
        if profile["package_id"] is not None and profile["package_id"] not in packages:
            errors.append("compatibility trial refers to an unknown package")
        for key in ("module", "test"):
            value = profile[key]
            if value is not None and not SAFE_FILTER_RE.fullmatch(value):
                errors.append("compatibility trial contains an unsafe filter")
        if not set(profile["required_fixture_ids"]).issubset(fixtures):
            errors.append("compatibility trial refers to an unknown fixture")
    expected = {"cts", "cts-verifier", "vts", "cts-on-gsi", "mts",
                "sts-autorepro"}
    if {item["id"] for item in config["applicability"]} != expected:
        errors.append("compatibility applicability decision set is incomplete")
    return errors[:40]


def _package(config: dict, package_id: str) -> dict:
    matches = [item for item in config["packages"] if item["id"] == package_id]
    if len(matches) != 1:
        raise CompatibilityError("unknown compatibility package")
    return matches[0]


def _profile(config: dict, profile_id: str) -> dict:
    matches = [item for item in config["trial_profiles"] if item["id"] == profile_id]
    if len(matches) != 1:
        raise CompatibilityError("unknown compatibility trial profile")
    return matches[0]


def _setup_record(path: Path, config: dict, profile: dict,
                  fingerprint_sha256: str) -> dict:
    try:
        metadata = path.lstat()
    except OSError:
        raise CompatibilityError("private compatibility setup record is unavailable", 3) from None
    if (path.is_symlink() or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o027):
        raise CompatibilityError("private compatibility setup record is not owner-controlled", 3)
    value = _load_json(path, MAX_REPORT_BYTES)
    expected_keys = {
        "schema_version", "profile_id", "candidate_fingerprint_sha256",
        "prepared_at_utc", "fixture_ids", "operator_attestations",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise CompatibilityError("private compatibility setup record has an invalid shape", 3)
    if (value.get("schema_version") != 1
            or value.get("profile_id") != profile["id"]
            or value.get("candidate_fingerprint_sha256") != fingerprint_sha256
            or not isinstance(value.get("prepared_at_utc"), str)
            or not UTC_RE.fullmatch(value["prepared_at_utc"])):
        raise CompatibilityError("private compatibility setup identity does not match", 3)
    fixture_ids = value.get("fixture_ids")
    known = {item["id"] for item in config["fixtures"]}
    if (not isinstance(fixture_ids, list)
            or len(fixture_ids) != len(set(fixture_ids))
            or any(not isinstance(item, str) or item not in known
                   for item in fixture_ids)
            or not set(profile["required_fixture_ids"]).issubset(fixture_ids)):
        raise CompatibilityError("private compatibility setup fixture record is incomplete", 3)
    attestations = value.get("operator_attestations")
    if (not isinstance(attestations, dict)
            or set(attestations) != SETUP_ATTESTATIONS
            or any(attestations[item] is not True for item in SETUP_ATTESTATIONS)):
        raise CompatibilityError("private compatibility setup attestations are incomplete", 3)
    return {
        "record_sha256": _sha256(path),
        "prepared_at_utc": value["prepared_at_utc"],
        "fixture_ids": fixture_ids,
        "operator_attestations": sorted(SETUP_ATTESTATIONS),
    }


def _safe_tree(root: Path, *, excluded_roots=()) -> tuple[list[dict], str]:
    try:
        resolved = root.resolve(strict=True)
        metadata = root.lstat()
    except OSError:
        raise CompatibilityError("compatibility package root is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
        raise CompatibilityError("compatibility package root must be a real directory")
    records = []
    total = 0
    for base, directories, files in os.walk(resolved, followlinks=False):
        directories.sort(key=lambda item: item.encode("utf-8"))
        files.sort(key=lambda item: item.encode("utf-8"))
        for name in directories:
            path = Path(base) / name
            if path.is_symlink():
                raise CompatibilityError("compatibility package contains a symbolic link")
        if Path(base) == resolved:
            directories[:] = [name for name in directories if name not in excluded_roots]
        for name in files:
            if Path(base) == resolved and name in excluded_roots:
                raise CompatibilityError("reserved output location is not a directory")
            path = Path(base) / name
            item = path.lstat()
            if path.is_symlink():
                raise CompatibilityError("compatibility package contains a symbolic link")
            if not stat.S_ISREG(item.st_mode):
                raise CompatibilityError("compatibility package contains a non-regular file")
            total += item.st_size
            if len(records) >= MAX_TREE_FILES or total > MAX_TREE_BYTES:
                raise CompatibilityError("compatibility package exceeds inventory limits")
            relative = path.relative_to(resolved).as_posix()
            if (PurePosixPath(relative).is_absolute()
                    or ".." in PurePosixPath(relative).parts):
                raise CompatibilityError("compatibility package contains an unsafe path")
            records.append({"path": relative, "bytes": item.st_size,
                            "sha256": _sha256(path),
                            "executable": bool(item.st_mode & 0o111)})
    if not records:
        raise CompatibilityError("compatibility package is empty")
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return records, hashlib.sha256(encoded).hexdigest()


# Only Tradefed's top-level generated output directories are outside input identity.
PACKAGE_OUTPUTS = {"results", "logs"}


def _verified_archive(config, package_id, archive):
    package = _package(config, package_id)
    if package["delivery"] != "official-download":
        raise CompatibilityError("selected package must be verified as a source build")
    if package["acquisition_status"] != "verified" or not package["archive_sha256"]:
        raise CompatibilityError("selected official package hash is not yet approved", 3)
    try:
        metadata = archive.lstat()
    except OSError:
        raise CompatibilityError("compatibility archive is unavailable") from None
    if (not stat.S_ISREG(metadata.st_mode) or archive.is_symlink()
            or archive.name != package["archive_name"]):
        raise CompatibilityError("compatibility archive identity is invalid")
    if _sha256(archive) != package["archive_sha256"]:
        raise CompatibilityError("compatibility archive hash mismatch", 3)
    return package


def _archive_inputs(archive, package, destination=None):
    """Validate ZIP shape before extraction; hash every consumed member."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            seen = set()
            files = []
            total = 0
            if len(bundle.infolist()) > MAX_TREE_FILES:
                raise CompatibilityError("compatibility archive exceeds inventory limits")
            for member in bundle.infolist():
                name = member.filename
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (path.is_absolute() or ".." in path.parts or "\\" in name
                        or path.as_posix() != name.rstrip("/") or not path.parts
                        or path.parts[0] != package["extracted_directory"]
                        or path.as_posix() in seen or member.flag_bits & 1
                        or kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK)):
                    raise CompatibilityError("compatibility archive has an unsafe member")
                seen.add(path.as_posix())
                if member.is_dir():
                    if kind == stat.S_IFREG:
                        raise CompatibilityError("compatibility archive member type mismatch")
                    continue
                if len(path.parts) < 2 or kind == stat.S_IFDIR:
                    raise CompatibilityError("compatibility archive member type mismatch")
                relative = PurePosixPath(*path.parts[1:])
                if relative.parts[0] in PACKAGE_OUTPUTS:
                    raise CompatibilityError("archive contains reserved generated outputs")
                total += member.file_size
                if total > MAX_TREE_BYTES:
                    raise CompatibilityError("compatibility archive exceeds byte limit")
                files.append((member, relative, bool(mode & 0o111)))
            file_names = {relative.as_posix() for _, relative, _ in files}
            if any(parent.as_posix() in file_names for _, path, _ in files
                   for parent in path.parents if parent != PurePosixPath(".")):
                raise CompatibilityError("compatibility archive has a file/directory collision")
            # Official bundled JDKs share license text through relative links.
            # Materialize only these notices; never create filesystem symlinks.
            resolved_files = []
            expanded = 0
            for member, relative, executable in files:
                if stat.S_ISLNK(member.external_attr >> 16):
                    link_root = next((prefix for prefix in (
                        "jdk/legal/", "android-cts-v-host/jdk/legal/",
                        "CameraITS/tests/")
                        if relative.as_posix().startswith(prefix)), None)
                    if link_root is None or member.file_size > 4096:
                        raise CompatibilityError("archive symlink is outside supported bounded subtrees")
                    try:
                        link = bundle.read(member).decode("utf-8")
                    except UnicodeError:
                        raise CompatibilityError("invalid archive link") from None
                    target = posixpath.normpath(posixpath.join(str(relative.parent), link))
                    if (not link or link.startswith("/") or "\\" in link
                            or not target.startswith(link_root)):
                        raise CompatibilityError("archive link escapes its approved subtree")
                    try:
                        member = bundle.getinfo(package["extracted_directory"] + "/" + target)
                    except KeyError:
                        raise CompatibilityError("archive link target is missing") from None
                    if (member.is_dir() or stat.S_IFMT(member.external_attr >> 16)
                            not in (0, stat.S_IFREG)):
                        raise CompatibilityError("archive link target is not a regular file")
                    executable = bool((member.external_attr >> 16) & 0o111)
                expanded += member.file_size
                if expanded > MAX_TREE_BYTES:
                    raise CompatibilityError("materialized archive exceeds byte limit")
                resolved_files.append((member, relative, executable))
            records = []
            for member, relative, executable in resolved_files:
                digest = hashlib.sha256()
                length = 0
                output = None
                if destination is not None:
                    target = destination / package["extracted_directory"] / relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                    output = target.open("xb")
                try:
                    with bundle.open(member) as stream:
                        while chunk := stream.read(1024 * 1024):
                            length += len(chunk)
                            if length > member.file_size:
                                raise CompatibilityError("archive member exceeds declared size")
                            digest.update(chunk)
                            if output:
                                output.write(chunk)
                    if length != member.file_size:
                        raise CompatibilityError("archive member is truncated")
                finally:
                    if output:
                        output.close()
                if destination is not None:
                    target.chmod(0o750 if executable else 0o640)
                records.append({"path": relative.as_posix(), "bytes": length,
                                "sha256": digest.hexdigest(), "executable": executable})
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise CompatibilityError("compatibility archive cannot be read safely") from None
    if not records:
        raise CompatibilityError("compatibility archive has no input files")
    return sorted(records, key=lambda item: item["path"].encode())


def inspect_package(config: dict, package_id: str, archive: Path,
                    package_root: Path) -> dict:
    package = _verified_archive(config, package_id, archive)
    expected = _archive_inputs(archive, package)
    expected_root = package_root / package["extracted_directory"]
    records, _ = _safe_tree(expected_root, excluded_roots=PACKAGE_OUTPUTS)
    records.sort(key=lambda item: item["path"].encode())
    if records != expected:
        raise CompatibilityError("extracted package does not match authenticated archive", 3)
    launcher = package["launcher"]
    if launcher is not None and not any(
            item["path"] == launcher and item["executable"] for item in records):
        raise CompatibilityError("compatibility launcher is not executable")
    tree_hash = hashlib.sha256(json.dumps(
        records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 1, "status": "PASS", "package_id": package_id,
        "version": package["version"], "archive_name": archive.name,
        "archive_sha256": package["archive_sha256"],
        "extracted_root": package["extracted_directory"], "tree_sha256": tree_hash,
        "file_count": len(records), "tree_bytes": sum(item["bytes"] for item in records),
    }


def extract_package(config, package_id, archive, package_root):
    """Publish a new extraction atomically, never mutate an existing input tree."""
    package = _verified_archive(config, package_id, archive)
    package_root.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if package_root.exists() or package_root.is_symlink():
        raise CompatibilityError("extracted package destination already exists", 3)
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=package_root.parent) as temp:
        staging = Path(temp) / "tree"
        staging.mkdir(mode=0o750)
        _archive_inputs(archive, package, staging)
        proof = inspect_package(config, package_id, archive, staging)
        if package_root.exists() or package_root.is_symlink():
            raise CompatibilityError("extracted package destination collision", 3)
        staging.rename(package_root)
    return proof


def parse_tradefed_result(result_root: Path, expected_version: str,
                          expected_module=None, expected_test=None) -> dict:
    try:
        root = result_root.resolve(strict=True)
    except OSError:
        raise CompatibilityError("Tradefed result directory is unavailable") from None
    if result_root.is_symlink() or not root.is_dir():
        raise CompatibilityError("Tradefed result root is not a real directory")
    xml_files = sorted(root.rglob("test_result.xml"))
    if len(xml_files) != 1:
        return {"status": "INCOMPLETE", "reason": "expected exactly one test_result.xml"}
    xml_path = xml_files[0]
    if xml_path.is_symlink():
        raise CompatibilityError("Tradefed result XML must not be a symbolic link")
    try:
        with xml_path.open("rb") as stream:
            raw = stream.read(MAX_RESULT_XML_BYTES + 1)
    except OSError:
        raise CompatibilityError("Tradefed result XML is unreadable") from None
    if len(raw) > MAX_RESULT_XML_BYTES:
        raise CompatibilityError("Tradefed result XML exceeds its byte limit")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise CompatibilityError("Tradefed result XML contains prohibited declarations")
    try:
        document = ET.fromstring(raw)
    except ET.ParseError:
        return {"status": "HARNESS_ERROR", "reason": "Tradefed result XML is truncated or malformed"}
    version = (document.attrib.get("suite_version")
               or document.attrib.get("version"))
    if not version:
        return {"status": "INCOMPLETE", "reason": "Tradefed result lacks suite version"}
    if version != expected_version:
        return {"status": "HARNESS_ERROR", "reason": "Tradefed suite version mismatch",
                "observed_version": version}
    if document.tag != "Result":
        return {"status": "HARNESS_ERROR", "reason": "unexpected Tradefed result structure"}
    tests = list(document.iter("Test"))
    modules = list(document.iter("Module"))
    if not tests:
        return {"status": "INCOMPLETE", "reason": "Tradefed result contains no tests",
                "suite_version": version}
    normalized = [item.attrib.get("result", "").lower() for item in tests]
    counts = {name: normalized.count(name) for name in sorted(set(normalized))}
    incomplete_modules = sum(item.attrib.get("done", "").lower() != "true"
                             for item in modules)
    if any(not value for value in normalized) or incomplete_modules:
        status = "INCOMPLETE"
        reason = "Tradefed result contains incomplete tests or modules"
    elif any(value not in {"pass", "passed"} for value in normalized):
        status = "FAIL"
        reason = "one or more official tests did not pass"
    else:
        status = "PASS"
        reason = "all recorded official tests passed"
    if status == "PASS":
        summary = document.find("Summary")
        try:
            complete_summary = (summary is not None
                and int(summary.get("pass", "-1")) == len(tests)
                and int(summary.get("failed", "-1")) == 0
                and int(summary.get("modules_done", "-1")) == len(modules)
                and int(summary.get("modules_total", "-1")) == len(modules))
        except ValueError:
            complete_summary = False
        observed = []
        for module in modules:
            for case in module.findall("TestCase"):
                for test in case.findall("Test"):
                    observed.append((module.get("abi", ""), module.get("name"),
                                     case.get("name", "") + "#" + test.get("name", "")))
        coverage = (expected_module is not None and expected_test is not None
                    and len(observed) == len(tests) and len(set(observed)) == len(observed)
                    and all(module == expected_module and test == expected_test
                            for _, module, test in observed))
        if not complete_summary or not coverage:
            status = "INCOMPLETE"
            reason = "requested coverage or explicit completion is not established"
    _, tree_hash = _safe_tree(root)
    return {
        "status": status,
        "reason": reason,
        "suite_version": version,
        "module_count": len(modules),
        "test_count": len(tests),
        "result_counts": counts,
        "incomplete_module_count": incomplete_modules,
        "result_tree_sha256": tree_hash,
        "test_result_sha256": hashlib.sha256(raw).hexdigest(),
    }


_atomic_json = evidence.atomic_json


def _result_directories(root: Path) -> set[str]:
    """Ignore CTS's bounded latest alias and ZIP exports, not real sessions."""
    if root.is_symlink():
        raise CompatibilityError("Tradefed results root is a symbolic link")
    if not root.exists():
        return set()
    sessions = set()
    for item in root.iterdir():
        if item.is_symlink():
            try:
                target = item.resolve(strict=True)
            except (OSError, RuntimeError):
                raise CompatibilityError("Tradefed result alias is invalid") from None
            if (item.name != "latest" or target.parent != root.resolve()
                    or not target.is_dir()):
                raise CompatibilityError("Tradefed result alias escapes its sessions")
        elif item.is_dir():
            sessions.add(item.name)
    return sessions


def _copy_result(source: Path, destination: Path):
    _safe_tree(source)
    shutil.copytree(source, destination, symlinks=False)
    _safe_tree(destination)


def _bounded_process(argv: list[str], timeout_seconds: int, cwd: Path) -> dict:
    environment = os.environ.copy()
    environment["USE_ATS"] = "false"
    environment["ENABLE_XTS_DYNAMIC_DOWNLOADER"] = "false"
    return process.run(argv, timeout_seconds, MAX_STREAM_BYTES, cwd=cwd,
                       env=environment)


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"[0-9]+(?:\.[0-9]+)+", value)
    return tuple(int(item) for item in match.group(0).split(".")) if match else ()


def _normalized_locale(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _evaluate_host(config: dict, observation: dict) -> dict:
    required = config["minimum_host"]
    checks = {
        "architecture": observation["architecture"] == required["architecture"],
        "memory": observation["memory_gib"] >= required["memory_gib"],
        "free_disk": observation["free_disk_gib"] >= required["free_disk_gib"],
        "glibc": (_version_tuple(observation["glibc"])
                  >= _version_tuple(required["glibc_minimum"])),
        "english_locale": (observation["locale"].lower().startswith("en_")
                           or observation["locale"].lower().startswith("en."))
                          and _normalized_locale(observation["locale"])
                          in {_normalized_locale(item)
                              for item in observation["available_locales"]},
        "ffmpeg": (_version_tuple(observation["ffmpeg_version"]) >= (5, 1, 3)),
        "adb": bool(observation["adb_version"]),
        "aapt2": bool(observation["aapt2_version"]),
        "aapt": bool(observation.get("aapt_version")),
    }
    return {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "requirements": required,
        "observed": observation,
        "checks": checks,
        "device_commands_executed": 0,
    }


def inspect_host(config: dict, host_root: Path) -> dict:
    try:
        disk = shutil.disk_usage(host_root.resolve(strict=True))
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        raise CompatibilityError("compatibility host resources are unavailable") from None

    def version(command: str, argument: str) -> str:
        executable = shutil.which(command)
        if executable is None:
            return ""
        result = _bounded_process([executable, argument], 20, host_root)
        if result["transport"] != "ok":
            return ""
        combined = (result["stdout"] + result["stderr"]).decode(
            "utf-8", errors="replace").splitlines()
        return combined[0][:500] if combined else ""

    try:
        glibc = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (OSError, ValueError):
        glibc = " ".join(platform.libc_ver())
    language = os.environ.get("LC_ALL") or os.environ.get("LANG") or \
        (locale.getlocale()[0] or "")
    locale_executable = shutil.which("locale")
    available_locales = []
    if locale_executable is not None:
        locale_result = _bounded_process(
            [locale_executable, "-a"], 20, host_root)
        if locale_result["transport"] == "ok":
            available_locales = locale_result["stdout"].decode(
                "utf-8", errors="replace").splitlines()
    observation = {
        "architecture": platform.machine(),
        "memory_gib": round((page_size * pages) / (1024 ** 3), 2),
        "free_disk_gib": round(disk.free / (1024 ** 3), 2),
        "glibc": glibc,
        "locale": language,
        "available_locales": available_locales,
        "ffmpeg_version": version("ffmpeg", "-version"),
        "adb_version": version("adb", "version"),
        "aapt2_version": version("aapt2", "version"),
        "aapt_version": version("aapt", "version"),
    }
    return _evaluate_host(config, observation)


def _require_single_attached_target(devices: list[str], target: str) -> None:
    if len(devices) != 1 or devices[0] != target:
        raise CompatibilityError(
            "authorized USB target is missing or not uniquely attached", 3)


def _result_parent(path_value: str | None, profile_id: str,
                   package_proof: dict) -> str | None:
    if path_value is None:
        return None
    value = _load_json(Path(path_value), MAX_REPORT_BYTES)
    if (not isinstance(value, dict) or value.get("operation") != "compatibility-trial"
            or value.get("profile_id") != profile_id
            or value.get("package") != package_proof
            or value.get("status") not in {"FAIL", "INCOMPLETE", "HARNESS_ERROR"}
            or not isinstance(value.get("run_id"), str)):
        raise CompatibilityError("retry parent is incompatible with this trial")
    return value["run_id"]


def execute_trial(args, config: dict) -> tuple[int, Path]:
    started_at_utc = _utc_now()
    if not args.run_id or not ID_RE.fullmatch(args.run_id):
        raise CompatibilityError("trial requires a valid immutable run id")
    if not all((args.target, args.device_role, args.device_map,
                args.output, args.package_root, args.archive,
                args.setup_record)):
        raise CompatibilityError("trial is missing a required target, package or output")
    direct_usb = getattr(args, "direct_usb", False)
    if bool(args.rig_config) == bool(direct_usb):
        raise CompatibilityError("select exactly one connection: rig configuration or direct USB", 3)
    profile = _profile(config, args.profile)
    if (profile["package_id"] is None or profile["module"] is None
            or profile["test"] is None or not profile["status"].startswith("ready")):
        raise CompatibilityError("selected compatibility trial is not approved", 3)
    package = _package(config, profile["package_id"])
    package_proof = inspect_package(config, package["id"], Path(args.archive),
                                    Path(args.package_root))
    map_entry = test_runner.load_device_map(
        Path(args.device_map), args.device_role, args.target)
    if not map_entry["disposable"]:
        raise CompatibilityError("compatibility trial requires the disposable harness role", 3)
    devices = device.authorized_devices(args.adb)
    _require_single_attached_target(devices, args.target)

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o750)
    metadata = output.stat()
    if (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o027
            or not stat.S_ISDIR(metadata.st_mode)):
        raise CompatibilityError("private output root must be owner-controlled", 3)
    host = inspect_host(config, output.parent)
    if host["status"] != "PASS":
        raise CompatibilityError("compatibility host gate did not pass", 3)
    partial = output / f"{args.run_id}.partial"
    if any(item.name == args.run_id or item.name.startswith(args.run_id + ".")
           for item in output.iterdir()):
        raise CompatibilityError("immutable compatibility output collision", 3)
    locks = output / ".locks"
    locks.mkdir(mode=0o750, exist_ok=True)
    lock_fd = os.open(locks / f"{args.device_role}.lock",
                      os.O_RDWR | os.O_CREAT, 0o640)
    controller = None
    lease_id = "compat-" + hashlib.sha256(args.run_id.encode()).hexdigest()[:24]
    lease_active = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CompatibilityError("physical target is already locked", 3) from None
        if direct_usb:
            # Direct attachment has no authorized hub power controller.
            _require_single_attached_target(device.authorized_devices(args.adb), args.target)
            partial.mkdir(mode=0o750)
            (partial / "raw").mkdir(mode=0o750)
        else:
            guard = rig.acquire_test_start_guard(
                args.rig_config, args.device_role, args.device_map, args.target)
            try:
                partial.mkdir(mode=0o750)
                (partial / "raw").mkdir(mode=0o750)
                controller = guard.controller
                guard.acquire_inhibitor(lease_id, "compatibility-trial")
                lease_active = True
            finally:
                guard.release()

        identity = {}
        for name, prop in (("release", "ro.build.version.release"),
                           ("build_type", "ro.build.type"),
                           ("device", "ro.product.device"),
                           ("fingerprint", "ro.build.fingerprint")):
            observed = test_runner.run_bounded(
                [args.adb, "-s", args.target, "shell", "getprop", prop], 20)
            if observed.get("transport") != "ok" or not observed.get("stdout", "").strip():
                raise CompatibilityError("compatibility device identity capture failed", 3)
            identity[name] = observed["stdout"].strip()
        if (identity["release"] != profile["android_release"]
                or identity["build_type"] != profile["build_type"]):
            raise CompatibilityError("device release or build type does not match the trial", 3)
        fingerprint_sha256 = hashlib.sha256(
            identity["fingerprint"].encode()).hexdigest()
        setup = _setup_record(Path(args.setup_record), config, profile,
                              fingerprint_sha256)

        parent = _result_parent(args.rerun_from, args.profile, package_proof)
        suite_root = Path(args.package_root) / package["extracted_directory"]
        results_root = suite_root / "results"
        before = _result_directories(results_root)
        launcher = suite_root / package["launcher"]
        command = [str(launcher), "run", "commandAndExit", package["plan"],
                   "-s", args.target, "-m", profile["module"],
                   "-t", profile["test"],
                   "--abi", config["target"]["architecture"],
                   "--enable-parameterized-modules", "false"]
        previous_handlers = {
            item: signal.getsignal(item)
            for item in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        }

        def interrupt(_signum, _frame):
            raise KeyboardInterrupt

        try:
            for item in previous_handlers:
                signal.signal(item, interrupt)
            process_result = _bounded_process(
                command, args.timeout_seconds, suite_root)
        finally:
            for item, handler in previous_handlers.items():
                signal.signal(item, handler)
        for name in ("stdout", "stderr"):
            path = partial / "raw" / f"tradefed.{name}.txt"
            path.write_bytes(process_result[name])
            os.chmod(path, 0o640)
        after = _result_directories(results_root)
        new_results = sorted(after - before)
        if len(new_results) == 1:
            source_result = results_root / new_results[0]
            _copy_result(source_result, partial / "raw" / "tradefed-result")
            parsed = parse_tradefed_result(
                partial / "raw" / "tradefed-result", package["version"],
                profile["module"], profile["test"])
        else:
            parsed = {"status": "INCOMPLETE",
                      "reason": "Tradefed did not create exactly one new result directory"}
        if process_result["transport"] != "ok" and parsed["status"] == "PASS":
            parsed = {**parsed, "status": "HARNESS_ERROR",
                      "reason": "Tradefed transport failed despite a parseable report"}

        report = {
            "schema_version": 1,
            "operation": "compatibility-trial",
            "run_id": args.run_id,
            "status": parsed["status"],
            "connection_mode": "direct-usb" if direct_usb else "rig",
            "profile_id": args.profile,
            "registry_id": config["registry_id"],
            "package": package_proof,
            "candidate": {"device": identity["device"],
                          "release": identity["release"],
                          "build_type": identity["build_type"],
                          "fingerprint_sha256": fingerprint_sha256},
            "target": {"role": args.device_role, "serial": "<redacted>"},
            "host": host,
            "setup": setup,
            "module": profile["module"],
            "test": profile["test"],
            "retry_parent": parent,
            "started_at_utc": started_at_utc,
            "ended_at_utc": _utc_now(),
            "tradefed": {"transport": process_result["transport"],
                         "returncode": process_result.get("returncode"),
                         "duration_ms": process_result["duration_ms"]},
            "parsed_result": parsed,
        }
        _atomic_json(partial / "result.json", report)
        try:
            if lease_active:
                controller.release_inhibitor(args.device_role, lease_id)
                lease_active = False
        except rig.RigError:
            report["status"] = "HARNESS_ERROR"
            report["parsed_result"] = {
                **parsed,
                "status": "HARNESS_ERROR",
                "reason": "persistent rig inhibitor could not be released",
            }
            _atomic_json(partial / "result.json", report)
        suffix = "" if report["status"] == "PASS" else "." + report["status"].lower()
        final = output / f"{args.run_id}{suffix}"
        os.rename(partial, final)
        return (0 if report["status"] == "PASS" else 4), final / "result.json"
    except (CompatibilityError, rig.RigError, OSError, KeyboardInterrupt) as error:
        if partial.is_dir():
            release_failed = False
            if lease_active and controller is not None:
                try:
                    controller.release_inhibitor(args.device_role, lease_id)
                    lease_active = False
                except rig.RigError:
                    release_failed = True
            reason = ("compatibility trial interrupted"
                      if isinstance(error, KeyboardInterrupt) else str(error))
            if release_failed:
                reason += "; persistent rig inhibitor could not be released"
            report = {
                "schema_version": 1,
                "operation": "compatibility-trial",
                "run_id": args.run_id,
                "status": "HARNESS_ERROR",
                "connection_mode": "direct-usb" if direct_usb else "rig",
                "profile_id": args.profile,
                "registry_id": config["registry_id"],
                "package": package_proof,
                "target": {"role": args.device_role, "serial": "<redacted>"},
                "host": host,
                "started_at_utc": started_at_utc,
                "ended_at_utc": _utc_now(),
                "parsed_result": {"status": "HARNESS_ERROR", "reason": reason},
            }
            _atomic_json(partial / "result.json", report)
            final = output / f"{args.run_id}.harness_error"
            os.rename(partial, final)
            return 5, final / "result.json"
        raise
    finally:
        if lease_active and controller is not None:
            try:
                controller.release_inhibitor(args.device_role, lease_id)
            except rig.RigError:
                pass
        os.close(lock_fd)


def _dry_run(config: dict, profile_id: str | None) -> dict:
    pending = [item["id"] for item in config["packages"]
               if item["acquisition_status"] != "verified"]
    blocked_fixtures = [item["id"] for item in config["fixtures"]
                        if item["status"] in {"missing", "unverified"}]
    selected = _profile(config, profile_id) if profile_id else None
    return {
        "schema_version": 1,
        "operation": "compatibility-harness",
        "status": "BLOCKED" if pending else "VALID",
        "registry_id": config["registry_id"],
        "target": config["target"],
        "selected_profile": selected,
        "pending_package_ids": pending,
        "unresolved_fixture_ids": blocked_fixtures,
        "device_commands_executed": 0,
        "writes": "none",
        "target_interlock": "exact private disposable role and serial; it must be the only authorized attached USB target",
        "incomplete_result_policy": "INCOMPLETE or HARNESS_ERROR; never PASS",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="diamaneos test compatibility")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--inspect-package", action="store_true")
    parser.add_argument("--extract-package", action="store_true")
    parser.add_argument("--check-host", action="store_true")
    parser.add_argument("--host-root", default="/var/lib/diamaneos-test")
    parser.add_argument("--parse-result")
    parser.add_argument("--package-id")
    parser.add_argument("--package-root")
    parser.add_argument("--archive")
    parser.add_argument("--setup-record")
    parser.add_argument("--run-trial", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--target")
    parser.add_argument("--device-role")
    parser.add_argument("--device-map")
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument("--rig-config")
    connection.add_argument("--direct-usb", action="store_true",
                            help="identity-bound direct attachment without hub power control")
    parser.add_argument("--output")
    parser.add_argument("--rerun-from")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = _load_json(Path(args.config))
        errors = _validate_registry(config)
        if errors:
            for error in errors:
                print("ERROR: " + error, file=sys.stderr)
            return 2
        selected = sum((args.dry_run, args.inspect_package, args.extract_package, args.check_host,
                        args.parse_result is not None, args.run_trial))
        if selected != 1:
            raise CompatibilityError("select exactly one compatibility operation")
        if args.dry_run:
            print(json.dumps(_dry_run(config, args.profile), indent=2,
                             sort_keys=True))
            return 0
        if args.inspect_package or args.extract_package:
            if not all((args.package_id, args.package_root, args.archive)):
                raise CompatibilityError("package inspection requires id, archive and root")
            operation = extract_package if args.extract_package else inspect_package
            print(json.dumps(operation(
                config, args.package_id, Path(args.archive),
                Path(args.package_root)), indent=2, sort_keys=True))
            return 0
        if args.check_host:
            result = inspect_host(config, Path(args.host_root))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASS" else 3
        if args.parse_result is not None:
            if not args.package_id:
                raise CompatibilityError("result parsing requires a package id")
            package = _package(config, args.package_id)
            if not args.profile:
                raise CompatibilityError("result parsing requires an expected trial profile")
            profile = _profile(config, args.profile)
            if profile["package_id"] != args.package_id:
                raise CompatibilityError("result profile does not match the selected package")
            result = parse_tradefed_result(Path(args.parse_result), package["version"],
                                           profile["module"], profile["test"])
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASS" else 4
        if not (60 <= args.timeout_seconds <= 172800):
            raise CompatibilityError("trial timeout must be between 60 and 172800 seconds")
        code, result_path = execute_trial(args, config)
        print(f"result={result_path}")
        return code
    except CompatibilityError as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return error.exit_code
    except rig.RigError as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return error.exit_code
    except (OSError, KeyboardInterrupt):
        print("ERROR: compatibility harness operation did not complete", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
