"""Fail-closed signing-role, target-files and dummy-proof verification.

The module never creates keys and never signs an artifact.  It validates the
reviewed role contract, derives the concrete package/AVB inventory from an
Android target-files archive and independently checks a dummy qualification
record.  Actual signing is confined to the offline/build qualification
procedure documented in ``docs/SIGNING.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import zipfile

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "signing-roles.json"
DEFAULT_ENVIRONMENT = ROOT / "config" / "build-environment.json"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_ZIP_MEMBERS = 250_000
MAX_ZIP_UNCOMPRESSED_BYTES = 64 * 1024 * 1024 * 1024
MAX_ERRORS = 40
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_KEYS = {
    "releasekey", "platform", "shared", "media", "networkstack",
    "bluetooth", "sdk_sandbox", "gmscompat_lib", "nfc", "avb",
    "factory",
}
ANDROID_CERT_KEYS = EXPECTED_KEYS - {"avb", "factory"}
EXPECTED_ARTIFACT_ROLES = {
    "android-apk-certificates", "apex-container-certificates",
    "apex-payload-verified-boot", "avb-root-and-chains", "ota-payload",
    "ota-package", "update-channel-metadata", "factory-archive",
    "release-record",
}
EXPECTED_PROOFS = {
    "target-files-transform", "apk-certificate-transform",
    "apex-container-transform", "apex-payload-transform",
    "avb-root-and-chains", "full-ota", "incremental-ota",
    "factory-archive-signature", "release-record-signature",
    "valid-key-verification", "wrong-key-rejection", "restart-recovery",
}
METADATA_MEMBERS = (
    "META/apkcerts.txt", "META/apexkeys.txt", "META/misc_info.txt",
)
FORBIDDEN_NORMALIZED_KEYS = {
    "privatekey", "privatekeydata", "password", "pin", "puk", "secret",
    "seed", "mnemonic", "recoveryphrase", "tokenvalue",
}


class SigningError(ValueError):
    """Bounded diagnostic which never includes key material."""


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SigningError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise SigningError("non-finite JSON number")


def _privacy_guard(value, *, max_nodes=200_000, max_depth=24):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise SigningError("document exceeds structural limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise SigningError("JSON object key is not text")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in FORBIDDEN_NORMALIZED_KEYS:
                    raise SigningError("private signing material field is prohibited")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_METADATA_BYTES:
                raise SigningError("document contains an oversized string")
            if "-----BEGIN " in item and "PRIVATE KEY-----" in item:
                raise SigningError("private signing material is prohibited")
        elif not isinstance(item, (int, float, bool, type(None))):
            raise SigningError("document contains a non-JSON value")


def load_json(path, *, limit=MAX_CONFIG_BYTES):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise SigningError("unable to read signing input") from None
    if len(raw) > limit:
        raise SigningError("signing input exceeds its byte limit")
    try:
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except SigningError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise SigningError("signing input is not valid unique-key UTF-8 JSON") from None
    _privacy_guard(value)
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise SigningError("unable to hash signing artifact") from None
    return digest.hexdigest()


def canonical_sha256(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _schema_errors(value, schema_name):
    if Draft7Validator is None:
        return ["missing jsonschema; install requirements-dev.txt in a virtual environment"]
    try:
        schema = json.loads((ROOT / "schemas" / schema_name).read_text(
            encoding="utf-8"))
        Draft7Validator.check_schema(schema)
    except (OSError, ValueError):
        return ["signing schema is unavailable or invalid"]
    errors = []
    for error in Draft7Validator(schema).iter_errors(value):
        errors.append("schema constraint failed: " + str(error.validator))
        if len(errors) == MAX_ERRORS:
            break
    return errors


def validate_config(config, environment):
    errors = _schema_errors(config, "signing-roles.schema.json")
    if errors:
        return errors[:MAX_ERRORS]
    try:
        _privacy_guard(environment)
    except SigningError as error:
        return [str(error)]

    binding = config["source_binding"]
    upstream = environment.get("upstream", {})
    expected = {
        "environment_id": environment.get("environment_id"),
        "release_tag": upstream.get("release_tag"),
        "manifest_commit": upstream.get("peeled_commit"),
        "project_map_sha256": upstream.get("project_map_sha256"),
    }
    if any(binding.get(field) != expected_value
           for field, expected_value in expected.items()):
        errors.append("signing source binding does not match build environment")

    project_values = binding["projects"]
    if set(project_values) != {
            "script", "build/make", "development", "external/avb",
            "prebuilts/jdk/jdk21", "system/update_engine", "tools/apksig"}:
        errors.append("signing source project set is incomplete")
    if any(not isinstance(value, str) or not HEX40_RE.fullmatch(value)
           for value in project_values.values()):
        errors.append("signing source project revision is invalid")

    paths = [entry["path"] for entry in binding["files"]]
    if len(paths) != len(set(paths)):
        errors.append("duplicate signing source-file binding")
    required_paths = {
        "script/common.sh", "script/generate-release.sh",
        "script/generate-delta.sh", "script/generate-metadata",
        "script/generate-keys", "script/finalize.sh",
        "development/tools/make_key",
        "external/avb/avbtool.py",
        "build/make/tools/releasetools/check_ota_package_signature.py",
        "prebuilts/jdk/jdk21/linux-x86/bin/java",
        "system/update_engine/scripts/brillo_update_payload",
        "tools/apksig/src/apksigner/java/com/android/apksigner/ApkSignerTool.java",
    }
    if set(paths) != required_paths:
        errors.append("signing source-file binding set is incomplete")

    key_roles = config["key_roles"]
    key_ids = [entry["id"] for entry in key_roles]
    if set(key_ids) != EXPECTED_KEYS or len(key_ids) != len(set(key_ids)):
        errors.append("signing key-role set is incomplete or duplicated")
    for entry in key_roles:
        if "offline" not in entry["storage"] or "pending" not in entry["storage"]:
            errors.append("key role lacks explicit offline pending-integration state")
        if entry["id"] in ANDROID_CERT_KEYS and entry["algorithm"] != \
                "RSA-4096 with SHA-256":
            errors.append("Android certificate role algorithm mismatch")
        if entry["id"] == "avb" and entry["algorithm"] != "SHA256_RSA4096":
            errors.append("AVB algorithm mismatch")
        if entry["id"] == "factory" and not entry["algorithm"].startswith("Ed25519"):
            errors.append("factory signature algorithm mismatch")

    artifact_roles = config["artifact_roles"]
    artifact_ids = [entry["id"] for entry in artifact_roles]
    if (set(artifact_ids) != EXPECTED_ARTIFACT_ROLES
            or len(artifact_ids) != len(set(artifact_ids))):
        errors.append("signing artifact-role set is incomplete or duplicated")
    for entry in artifact_roles:
        if not set(entry["key_ids"]) <= set(key_ids):
            errors.append("artifact role refers to an unknown key role")

    profiles = config["target_profiles"]
    profile_ids = [entry["id"] for entry in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        errors.append("duplicate signing target profile")
    if set(profile_ids) != {
            "generic-x86_64-qualification",
            "generic-x86_64-ota-qualification",
            "fp6-release",
    }:
        errors.append("signing target-profile set is incomplete")
    profiles_by_id = {entry["id"]: entry for entry in profiles}
    generic = profiles_by_id.get("generic-x86_64-qualification", {})
    if generic.get("build_target") != environment["build"][
            "generic_qualification_target"]:
        errors.append("generic signing target does not match build environment")
    ota = profiles_by_id.get("generic-x86_64-ota-qualification", {})
    if ota.get("build_target") != "aosp_cf_x86_64_phone-cur-userdebug":
        errors.append("generic OTA signing target is not the reviewed product")
    for profile in profiles:
        allowlist = profile["presigned_allowlist"]
        if allowlist != sorted(allowlist, key=lambda item: item.encode("utf-8")):
            errors.append("presigned allowlist is not bytewise sorted")
        if any(any(token in item for token in ("*", "?", "[", "]", "/"))
               for item in allowlist):
            errors.append("presigned allowlist must use exact package names")
        metadata_only = profile["presigned_metadata_only"]
        if metadata_only != sorted(
                metadata_only, key=lambda item: item.encode("utf-8")):
            errors.append("presigned metadata-only list is not bytewise sorted")
        artifacts = profile["presigned_artifacts"]
        artifact_order = [
            (entry["metadata_name"], entry["path"]) for entry in artifacts
        ]
        if artifact_order != sorted(
                artifact_order,
                key=lambda item: (item[0].encode("utf-8"),
                                  item[1].encode("utf-8"))):
            errors.append("presigned artifact bindings are not bytewise sorted")
        if len(artifact_order) != len(set(artifact_order)):
            errors.append("duplicate presigned artifact binding")
        artifact_names = {entry["metadata_name"] for entry in artifacts}
        if set(metadata_only) & artifact_names:
            errors.append("presigned package has conflicting presence policies")
        if set(allowlist) != set(metadata_only) | artifact_names:
            errors.append("presigned policy does not partition the allowlist")
        for entry in artifacts:
            pure = PurePosixPath(entry["path"])
            if (pure.is_absolute() or ".." in pure.parts
                    or pure.name != entry["artifact_basename"]):
                errors.append("presigned artifact binding path is invalid")
        qualified_hash = profile["qualified_unsigned_target_files_sha256"]
        qualified_otatools = profile["qualified_otatools_sha256"]
        evidence = profile["qualification_evidence"]
        if profile["inventory_status"] == "qualified":
            if (not qualified_hash or not qualified_otatools
                    or evidence is None or not allowlist):
                errors.append("qualified signing profile lacks review bindings")
        elif (qualified_hash is not None or qualified_otatools is not None
              or evidence is not None or allowlist
              or metadata_only or artifacts):
            errors.append("unqualified signing profile contains review bindings")

    proofs = config["required_dummy_proofs"]
    if set(proofs) != EXPECTED_PROOFS or len(proofs) != len(set(proofs)):
        errors.append("dummy proof set is incomplete or duplicated")
    return errors[:MAX_ERRORS]


def _zip_metadata(archive):
    try:
        handle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile):
        raise SigningError("target-files archive is unavailable or invalid") from None
    infos = handle.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        handle.close()
        raise SigningError("target-files archive has too many members")
    total = 0
    names = set()
    for info in infos:
        total += info.file_size
        if total > MAX_ZIP_UNCOMPRESSED_BYTES:
            handle.close()
            raise SigningError("target-files archive exceeds the expanded-size limit")
        name = PurePosixPath(info.filename)
        if (name.is_absolute() or ".." in name.parts
                or info.filename in names):
            handle.close()
            raise SigningError("target-files archive contains an unsafe member")
        names.add(info.filename)
    missing = set(METADATA_MEMBERS) - names
    if missing:
        handle.close()
        raise SigningError("target-files archive lacks required signing metadata")
    return handle


def _read_member(archive, name):
    try:
        info = archive.getinfo(name)
        if info.file_size > MAX_METADATA_BYTES:
            raise SigningError("target-files signing metadata exceeds its limit")
        raw = archive.read(info)
        if len(raw) != info.file_size:
            raise SigningError("target-files signing metadata is truncated")
        return raw.decode("utf-8")
    except SigningError:
        raise
    except (KeyError, UnicodeError, OSError, RuntimeError, zipfile.BadZipFile):
        raise SigningError("unable to read target-files signing metadata") from None


def _attribute_lines(text, label):
    records = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fields = shlex.split(line, posix=True)
        except ValueError:
            raise SigningError(f"{label} contains malformed quoting") from None
        record = {}
        for field in fields:
            if "=" not in field:
                raise SigningError(f"{label} contains a malformed field")
            key, value = field.split("=", 1)
            if key in record:
                raise SigningError(f"{label} contains a duplicate field")
            record[key] = value
        if "name" not in record or not record["name"]:
            raise SigningError(f"{label} record lacks a name")
        records.append(record)
    names = [entry["name"] for entry in records]
    if len(names) != len(set(names)):
        raise SigningError(f"{label} contains a duplicate package")
    return records


def _basename_role(value):
    if value in {"PRESIGNED", "EXTERNAL"}:
        return value
    name = PurePosixPath(value).name
    for suffix in (".x509.pem", ".pk8", ".pem", ".avbpubkey"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


def _misc_info(text):
    value = {}
    identical_duplicates = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SigningError("misc_info contains a malformed field")
        key, item = line.split("=", 1)
        if key in value:
            if value[key] != item:
                raise SigningError(
                    "misc_info contains a conflicting duplicate field")
            identical_duplicates.add(key)
            continue
        value[key] = item
    return value, sorted(identical_duplicates,
                         key=lambda field: field.encode("utf-8"))


def _zip_member_sha256(archive, name):
    digest = hashlib.sha256()
    try:
        with archive.open(name, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
        raise SigningError("unable to hash a bound presigned artifact") from None
    return digest.hexdigest()


def _presigned_artifact_errors(archive, profile):
    """Verify exact archive presence/absence and identity bindings.

    The parsed metadata name is retained separately from the literal ZIP
    basename so the policy does not infer an archive path from metadata.
    """
    errors = []
    members_by_basename = {}
    for info in archive.infolist():
        basename = PurePosixPath(info.filename).name
        members_by_basename.setdefault(basename, []).append(info.filename)

    expected_by_basename = {}
    for entry in profile["presigned_artifacts"]:
        expected_by_basename.setdefault(
            entry["artifact_basename"], set()).add(entry["path"])
        try:
            info = archive.getinfo(entry["path"])
        except KeyError:
            errors.append("target-files lacks a bound presigned artifact")
            continue
        if info.file_size != entry["bytes"]:
            errors.append("target-files presigned artifact size mismatch")
            continue
        if _zip_member_sha256(archive, entry["path"]) != entry["sha256"]:
            errors.append("target-files presigned artifact hash mismatch")

    for basename, expected in expected_by_basename.items():
        observed = set(members_by_basename.get(basename, []))
        if observed != expected:
            errors.append("target-files presigned artifact path set mismatch")

    for metadata_name in profile["presigned_metadata_only"]:
        if "\\" in metadata_name:
            errors.append("metadata-only presigned name has an unsupported escape")
            continue
        if members_by_basename.get(metadata_name):
            errors.append("metadata-only presigned package is present in archive")
    return errors


def _signed_source_plan(source_inventory, profile, config, profile_id):
    """Validate and plan against the accepted unsigned inventory.

    The pinned Android signer deliberately copies ``META/apkcerts.txt`` and
    ``META/apexkeys.txt`` from the input archive. Those files describe the
    source labels used to select keys; they do not become a record of the
    destination keys. A signed archive therefore needs its exact accepted
    unsigned inventory as the metadata reference, while independent artifact
    verification proves the destination certificates and AVB keys.
    """
    if not isinstance(source_inventory, dict):
        raise SigningError("signed target-files requires a source inventory")
    from diamaneos_tools import signing_qualification
    try:
        observed_hash = source_inventory["inventory_sha256"]
        canonical = dict(source_inventory)
        canonical.pop("inventory_sha256")
        if (not HEX64_RE.fullmatch(observed_hash)
                or canonical_sha256(canonical) != observed_hash):
            raise SigningError("source inventory self-hash mismatch")
        if (source_inventory["status"] != "PASS"
                or source_inventory["stage"] != "unsigned"
                or source_inventory["inventory_id"] != config["inventory_id"]
                or source_inventory["profile_id"] != profile_id):
            raise SigningError("source inventory is not an accepted unsigned input")
        qualified_hash = profile["qualified_unsigned_target_files_sha256"]
        if (qualified_hash is not None
                and source_inventory["target_files_sha256"] != qualified_hash):
            raise SigningError("source inventory does not bind the qualified input")
        plan = signing_qualification.explicit_role_map(source_inventory)
    except SigningError:
        raise
    except (KeyError, TypeError, signing_qualification.QualificationPlanError):
        raise SigningError("source inventory cannot produce a signing plan") from None
    return plan


def _role_index(records, fields, label):
    try:
        indexed = {
            record["name"]: tuple(record[field] for field in fields)
            for record in records
        }
    except (KeyError, TypeError):
        raise SigningError(f"{label} inventory is malformed") from None
    if len(indexed) != len(records):
        raise SigningError(f"{label} inventory contains duplicate packages")
    return indexed


def inspect_target_files(path, config, profile_id, *, stage,
                         source_inventory=None):
    if stage not in {"unsigned", "signed"}:
        raise SigningError("target-files stage must be unsigned or signed")
    profiles = {entry["id"]: entry for entry in config["target_profiles"]}
    if profile_id not in profiles:
        raise SigningError("unknown signing target profile")
    profile = profiles[profile_id]
    if stage == "unsigned" and source_inventory is not None:
        raise SigningError("source inventory is valid only for signed target-files")
    plan = None
    source_apks = None
    source_apex = None
    if stage == "signed":
        plan = _signed_source_plan(
            source_inventory, profile, config, profile_id)
        source_apks = _role_index(
            source_inventory.get("apk_roles"),
            ("certificate_role",), "source APK")
        source_apex = _role_index(
            source_inventory.get("apex_roles"),
            ("container_certificate_role", "payload_public_key",
             "payload_private_key_role"),
            "source APEX")
    target_files_sha256 = sha256_file(path)
    with _zip_metadata(path) as archive:
        apks = _attribute_lines(_read_member(
            archive, "META/apkcerts.txt"), "apkcerts")
        apex = _attribute_lines(_read_member(
            archive, "META/apexkeys.txt"), "apexkeys")
        misc, misc_duplicates = _misc_info(_read_member(
            archive, "META/misc_info.txt"))
        artifact_errors = _presigned_artifact_errors(archive, profile)

    apk_inventory = []
    apex_inventory = []
    presigned = set()
    unknown_roles = set()
    for record in apks:
        certificate = _basename_role(record.get("certificate", ""))
        if certificate in {"PRESIGNED", "EXTERNAL"}:
            presigned.add(record["name"])
        record_out = {
            "name": record["name"],
            "certificate_role": certificate,
        }
        if stage == "signed":
            planned = plan.get(record["name"])
            if planned is None or planned["kind"] != "apk":
                record_out["expected_certificate_role"] = "missing"
            else:
                record_out["expected_certificate_role"] = planned["container"]
        apk_inventory.append(record_out)

    for record in apex:
        container = _basename_role(record.get("container_certificate", ""))
        payload_public = _basename_role(record.get("public_key", ""))
        payload_private = _basename_role(record.get("private_key", ""))
        if container in {"PRESIGNED", "EXTERNAL"}:
            presigned.add(record["name"])
        record_out = {
            "name": record["name"],
            "container_certificate_role": container,
            "payload_public_key": payload_public,
            "payload_private_key_role": payload_private,
        }
        if stage == "signed":
            planned = plan.get(record["name"])
            if planned is None or planned["kind"] != "apex":
                record_out["expected_container_certificate_role"] = "missing"
                record_out["expected_payload_private_key_role"] = "missing"
            else:
                record_out["expected_container_certificate_role"] = \
                    planned["container"]
                record_out["expected_payload_private_key_role"] = \
                    planned["payload"]
        apex_inventory.append(record_out)

    avb = []
    for key in sorted(misc):
        match = re.fullmatch(r"avb_(.+)_key_path", key)
        if not match:
            continue
        chain = match.group(1)
        algorithm = misc.get(f"avb_{chain}_algorithm")
        key_role = _basename_role(misc[key])
        if stage == "signed" and (key_role != "avb"
                                  or algorithm != "SHA256_RSA4096"):
            unknown_roles.add(key_role or "missing-avb-key")
        avb.append({
            "chain": chain,
            "key_role": key_role,
            "algorithm": algorithm,
        })

    allowed = set(profile["presigned_allowlist"])
    missing_allowlist = allowed - presigned
    unlisted_presigned = presigned - allowed
    errors = list(artifact_errors)
    if stage == "signed":
        observed_apks = _role_index(
            apk_inventory, ("certificate_role",), "signed APK")
        observed_apex = _role_index(
            apex_inventory,
            ("container_certificate_role", "payload_public_key",
             "payload_private_key_role"),
            "signed APEX")
        if observed_apks != source_apks or observed_apex != source_apex:
            errors.append(
                "signed target-files metadata differs from accepted unsigned input")
        if set(plan) != set(observed_apks) | set(observed_apex):
            errors.append("signed target-files package set differs from signing plan")
    qualified_hash = profile["qualified_unsigned_target_files_sha256"]
    if (stage == "unsigned" and qualified_hash is not None
            and target_files_sha256 != qualified_hash):
        errors.append("unsigned target-files hash does not match qualified input")
    if unknown_roles:
        errors.append("target-files contains an unlisted signing role")
    if unlisted_presigned:
        errors.append("target-files contains an unlisted presigned package")
    if profile["inventory_status"] == "qualified" and missing_allowlist:
        errors.append("target-files is missing an allowlisted presigned package")
    if not avb:
        errors.append("target-files contains no AVB role metadata")

    inventory = {
        "schema_version": 1,
        "inventory_id": config["inventory_id"],
        "profile_id": profile_id,
        "stage": stage,
        "target_files_sha256": target_files_sha256,
        "apk_count": len(apk_inventory),
        "apex_count": len(apex_inventory),
        "avb_chain_count": len(avb),
        "apk_roles": apk_inventory,
        "apex_roles": apex_inventory,
        "avb_roles": avb,
        "identical_misc_info_duplicate_fields": misc_duplicates,
        "presigned_packages": sorted(presigned, key=lambda item: item.encode("utf-8")),
        "unlisted_presigned_count": len(unlisted_presigned),
        "unknown_role_count": len(unknown_roles),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    if stage == "signed":
        inventory.update({
            "metadata_role_model": "accepted-unsigned-labels-plus-explicit-plan",
            "source_inventory_sha256": source_inventory["inventory_sha256"],
            "planned_role_count": len(plan),
        })
    inventory["inventory_sha256"] = canonical_sha256(inventory)
    return inventory


def _safe_artifact(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise SigningError("dummy evidence contains an invalid artifact path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise SigningError("dummy evidence contains an unsafe artifact path")
    candidate = root.joinpath(*pure.parts)
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise SigningError("dummy evidence artifact is unavailable or not regular")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except SigningError:
        raise
    except (OSError, ValueError):
        raise SigningError("dummy evidence artifact escapes its root") from None
    return resolved


def _ssh_verify(manifest, signature, allowed_signers, identity, namespace):
    command = [
        "ssh-keygen", "-Y", "verify", "-f", str(allowed_signers),
        "-I", identity, "-n", namespace, "-s", str(signature),
    ]
    try:
        return subprocess.run(
            command, input=manifest.read_bytes(), capture_output=True,
            timeout=30).returncode
    except (OSError, subprocess.TimeoutExpired):
        raise SigningError("unable to run the release-record verifier") from None


def _verify_ssh_proof(root, record, label, errors):
    try:
        manifest = _safe_artifact(root, record["manifest_path"])
        signature = _safe_artifact(root, record["signature_path"])
        allowed = _safe_artifact(root, record["allowed_signers_path"])
        wrong = _safe_artifact(root, record["wrong_allowed_signers_path"])
        if _ssh_verify(manifest, signature, allowed, record["identity"],
                       record["namespace"]) != 0:
            errors.append(f"dummy {label} failed declared-key verification")
        if _ssh_verify(manifest, signature, wrong, record["identity"],
                       record["namespace"]) == 0:
            errors.append(f"dummy {label} accepted the wrong key")
    except SigningError as error:
        errors.append(str(error))


def verify_dummy_result(result_path, artifact_root, config):
    result = load_json(result_path, limit=MAX_RESULT_BYTES)
    errors = _schema_errors(result, "signing-dummy-result.schema.json")
    if errors:
        return errors[:MAX_ERRORS]
    if result["inventory_id"] != config["inventory_id"]:
        errors.append("dummy result uses a different signing inventory")
    if result["source_binding"] != config["source_binding"]:
        errors.append("dummy result uses a different source binding")
    if (not result["dummy_keys_only"]
            or result["production_material_present"]):
        errors.append("dummy result crosses the production-material boundary")
    proof_ids = [entry["id"] for entry in result["proofs"]]
    if set(proof_ids) != set(config["required_dummy_proofs"]):
        errors.append("dummy result proof set is incomplete")
    if len(proof_ids) != len(set(proof_ids)):
        errors.append("dummy result contains duplicate proofs")
    if any(entry["status"] != "PASS" for entry in result["proofs"]):
        errors.append("dummy result contains an unsuccessful proof")
    key_ids = [entry["key_id"] for entry in result["key_public_fingerprints"]]
    if set(key_ids) != EXPECTED_KEYS or len(key_ids) != len(set(key_ids)):
        errors.append("dummy result key fingerprint set is incomplete")

    root = Path(artifact_root)
    artifact_ids = set()
    for artifact in result["artifacts"]:
        if artifact["id"] in artifact_ids:
            errors.append("dummy result contains duplicate artifacts")
            continue
        artifact_ids.add(artifact["id"])
        try:
            path = _safe_artifact(root, artifact["path"])
            if path.stat().st_size != artifact["bytes"]:
                errors.append("dummy artifact byte count mismatch")
            if sha256_file(path) != artifact["sha256"]:
                errors.append("dummy artifact hash mismatch")
        except SigningError as error:
            errors.append(str(error))
        if len(errors) >= MAX_ERRORS:
            return errors[:MAX_ERRORS]

    for proof in result["proofs"]:
        if not set(proof["evidence_refs"]) <= artifact_ids:
            errors.append("dummy proof refers to an unknown artifact")

    _verify_ssh_proof(root, result["factory_archive_proof"],
                      "factory archive", errors)
    _verify_ssh_proof(root, result["release_record_proof"],
                      "release record", errors)

    if result["status"] != "PASS":
        errors.append("dummy result is not PASS")
    return errors[:MAX_ERRORS]


def _write_json(path, value):
    destination = Path(path)
    if destination.exists():
        raise SigningError("refusing to overwrite signing output")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")
    except OSError:
        raise SigningError("unable to write signing output") from None


def _parser():
    parser = argparse.ArgumentParser(prog="diamaneos signing")
    subparsers = parser.add_subparsers(dest="action", required=True)
    roles = subparsers.add_parser("roles", help="validate the role contract")
    roles.add_argument("--config", default=str(DEFAULT_CONFIG))
    roles.add_argument("--environment", default=str(DEFAULT_ENVIRONMENT))

    inventory = subparsers.add_parser(
        "inventory", help="derive roles from a target-files archive")
    inventory.add_argument("--config", default=str(DEFAULT_CONFIG))
    inventory.add_argument("--environment", default=str(DEFAULT_ENVIRONMENT))
    inventory.add_argument("--profile", required=True)
    inventory.add_argument("--stage", choices=("unsigned", "signed"), required=True)
    inventory.add_argument("--target-files", required=True)
    inventory.add_argument(
        "--source-inventory",
        help="accepted unsigned inventory required for the signed stage")
    inventory.add_argument("--output")

    verify = subparsers.add_parser(
        "verify", help="verify a retained dummy signing result")
    verify.add_argument("--config", default=str(DEFAULT_CONFIG))
    verify.add_argument("--environment", default=str(DEFAULT_ENVIRONMENT))
    verify.add_argument("--result", required=True)
    verify.add_argument("--artifact-root", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        config = load_json(args.config)
        environment = load_json(args.environment)
        errors = validate_config(config, environment)
        if errors:
            for error in errors:
                print("ERROR: " + error, file=sys.stderr)
            return 2
        if args.action == "roles":
            print(json.dumps({
                "schema_version": 1,
                "status": "VALID",
                "inventory_id": config["inventory_id"],
                "key_role_count": len(config["key_roles"]),
                "artifact_role_count": len(config["artifact_roles"]),
                "dummy_proof_count": len(config["required_dummy_proofs"]),
                "production_key_operations": 0,
            }, indent=2, sort_keys=True))
            return 0
        if args.action == "inventory":
            source_inventory = None
            if args.source_inventory:
                source_inventory = load_json(
                    args.source_inventory, limit=64 * 1024 * 1024)
            result = inspect_target_files(
                args.target_files, config, args.profile, stage=args.stage,
                source_inventory=source_inventory)
            if args.output:
                _write_json(args.output, result)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["status"] == "PASS" else 3
        errors = verify_dummy_result(args.result, args.artifact_root, config)
        if errors:
            for error in errors:
                print("ERROR: " + error, file=sys.stderr)
            return 3
        print(json.dumps({
            "schema_version": 1,
            "status": "PASS",
            "result_sha256": sha256_file(args.result),
            "artifact_root_verified": True,
            "valid_key_verification": "PASS",
            "wrong_key_rejection": "PASS",
        }, indent=2, sort_keys=True))
        return 0
    except SigningError as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
