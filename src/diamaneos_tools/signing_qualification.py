"""Bounded command planning for disposable Android signing qualification.

This module does not expose a generic signing command.  It converts an already
accepted, exact-hash target-files inventory into the explicit package and AVB
overrides required by the pinned Android release tools.  Every package name is
enumerated; paths supplied by an operator are never interpreted as roles.
"""

from __future__ import annotations

from collections import defaultdict
import re


PACKAGE_RE = re.compile(r"^[A-Za-z0-9._+-]{1,256}$")
ANDROID_CERTIFICATE_ROLES = {
    "releasekey", "platform", "shared", "media", "networkstack",
    "bluetooth", "sdk_sandbox", "gmscompat_lib", "nfc",
}
SUPPORTED_AVB_CHAINS = {
    "boot", "init_boot", "recovery", "system", "system_other", "vendor",
    "dtbo", "vbmeta", "vbmeta_system", "vbmeta_vendor",
}
SUPPORTED_CUSTOM_AVB_CHAINS = {
    "vbmeta_system_dlkm", "vbmeta_vendor_dlkm",
}
PRESIGNED = "PRESIGNED"
MAX_NAMES_PER_ARGUMENT = 256


class QualificationPlanError(ValueError):
    """The accepted inventory cannot be expressed by the bounded planner."""


def _package_name(value):
    if not isinstance(value, str) or not PACKAGE_RE.fullmatch(value):
        raise QualificationPlanError("inventory contains an unsafe package name")
    return value


def apk_destination_role(name, source_role):
    """Return the reviewed destination role for one accepted APK record.

    GrapheneOS extends the normal ``-d`` role map with Bluetooth, NFC,
    networkstack, SDK sandbox and GMS compatibility roles.  Generic-userdebug
    test certificates do not become new release authorities; their exact
    package records are mapped to ``releasekey`` for this disposable tool-path
    qualification.  Presigned records remain presigned.
    """
    _package_name(name)
    if source_role in {"PRESIGNED", "EXTERNAL"}:
        return PRESIGNED
    if source_role in ANDROID_CERTIFICATE_ROLES:
        return source_role
    if source_role == "com.android.bluetooth":
        return "bluetooth"
    return "releasekey"


def explicit_role_map(inventory):
    """Build a complete, exact package-role map from a PASS unsigned inventory."""
    if inventory.get("status") != "PASS" or inventory.get("stage") != "unsigned":
        raise QualificationPlanError("unsigned inventory is not accepted")
    mapping = {}
    for record in inventory.get("apk_roles", []):
        name = _package_name(record.get("name"))
        role = apk_destination_role(name, record.get("certificate_role"))
        if name in mapping:
            raise QualificationPlanError("duplicate package in role map")
        mapping[name] = {"container": role, "payload": None, "kind": "apk"}
    for record in inventory.get("apex_roles", []):
        name = _package_name(record.get("name"))
        if name in mapping:
            raise QualificationPlanError("APK and APEX package names overlap")
        source = record.get("container_certificate_role")
        if source in {"PRESIGNED", "EXTERNAL"}:
            container = PRESIGNED
            payload = PRESIGNED
        else:
            container = "releasekey"
            payload = "avb"
        mapping[name] = {
            "container": container,
            "payload": payload,
            "kind": "apex",
        }
    if not mapping:
        raise QualificationPlanError("accepted inventory contains no packages")
    return mapping


def _chunks(values, size=MAX_NAMES_PER_ARGUMENT):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def signing_command(inventory, *, signer, key_dir, source, destination):
    """Return the fixed ``sign_target_files_apks`` argv for the accepted input."""
    mapping = explicit_role_map(inventory)
    grouped = defaultdict(list)
    apex_payloads = []
    for name in sorted(mapping, key=lambda item: item.encode("utf-8")):
        record = mapping[name]
        grouped[record["container"]].append(name)
        if record["kind"] == "apex":
            apex_payloads.append((name, record["payload"]))

    command = [str(signer), "-o", "-d", str(key_dir)]
    for role in sorted(grouped, key=lambda item: item.encode("utf-8")):
        destination_key = "" if role == PRESIGNED else f"{key_dir}/{role}"
        for names in _chunks(grouped[role]):
            command.extend([
                "--extra_apks", f"{','.join(names)}={destination_key}",
            ])
    for name, role in apex_payloads:
        destination_key = "" if role == PRESIGNED else f"{key_dir}/avb.pem"
        command.extend([
            "--extra_apex_payload_key", f"{name}={destination_key}",
        ])

    chains = inventory.get("avb_roles", [])
    if not chains:
        raise QualificationPlanError("accepted inventory contains no AVB chain")
    observed = set()
    for record in sorted(chains, key=lambda item: item["chain"]):
        chain = record.get("chain")
        if (chain not in SUPPORTED_AVB_CHAINS | SUPPORTED_CUSTOM_AVB_CHAINS
                or chain in observed):
            raise QualificationPlanError("inventory contains an unsupported AVB chain")
        observed.add(chain)
        if chain in SUPPORTED_CUSTOM_AVB_CHAINS:
            command.extend([
                "--avb_extra_custom_image_key",
                f"{chain}={key_dir}/avb.pem",
                "--avb_extra_custom_image_algorithm",
                f"{chain}=SHA256_RSA4096",
            ])
        else:
            command.extend([
                f"--avb_{chain}_key", f"{key_dir}/avb.pem",
                f"--avb_{chain}_algorithm", "SHA256_RSA4096",
            ])
    command.extend([str(source), str(destination)])
    return command


def representative_packages(inventory):
    """Return an exact preferred metadata name for each Android cert role."""
    mapping = explicit_role_map(inventory)
    candidates = defaultdict(list)
    for name, record in mapping.items():
        if record["kind"] == "apk" and record["container"] != PRESIGNED:
            candidates[record["container"]].append(name)
    missing = ANDROID_CERTIFICATE_ROLES - set(candidates)
    if missing:
        raise QualificationPlanError("accepted inventory lacks a certificate role")
    return {
        role: sorted(candidates[role], key=lambda item: item.encode("utf-8"))[0]
        for role in sorted(ANDROID_CERTIFICATE_ROLES)
    }
