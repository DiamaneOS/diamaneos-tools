"""Validate the public carrier-test matrix without touching a device or network.

The matrix separates carrier/plan eligibility from observations made on a
specific installed build.  Subscriber identifiers and raw telephony evidence
belong in the caller-selected private run directory, never in this config.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 262_144
MAX_ERRORS = 20
ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,95}$")
URL_RE = re.compile(r"^https://[^\s/@]+(?:/[^\s]*)?$")
CAPABILITIES = {"voice", "sms", "data", "volte", "vowifi", "5g",
                "esim-lifecycle"}
PLAN_STATES = {"eligible", "ineligible", "unverified", "not-applicable"}
OBSERVATION_STATES = {
    "NOT_RUN", "PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE",
}
AVAILABILITY_STATES = {"active", "pending-test-input"}
FORBIDDEN_KEYS = {
    "phone", "phone_number", "msisdn", "imei", "imsi", "iccid", "eid",
    "account", "account_id", "password", "pin", "puk", "token", "secret",
    "adb_serial", "serial_number",
}
FORBIDDEN_NORMALIZED_KEYS = {
    re.sub(r"[^a-z0-9]", "", key) for key in FORBIDDEN_KEYS
}


class MatrixError(ValueError):
    """Safe validation failure that never includes an input value."""


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MatrixError("duplicate JSON key")
        value[key] = item
    return value


def load_matrix(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise MatrixError("unable to read carrier matrix") from None
    if len(raw) > MAX_FILE_BYTES:
        raise MatrixError("carrier matrix exceeds its byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(
                               MatrixError("non-finite JSON number")))
    except MatrixError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise MatrixError("carrier matrix is not valid unique-key UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise MatrixError("carrier matrix must be an object")
    return value


def _keys(value, required, allowed, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    if required - set(value):
        errors.append(f"{label} is missing required fields")
    if set(value) - allowed:
        errors.append(f"{label} contains unknown fields")
    return not (required - set(value) or set(value) - allowed)


def _text(value, maximum=500):
    return isinstance(value, str) and 1 <= len(value) <= maximum


def _identifier(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _privacy_guard(value, errors):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 16_000 or depth > 24:
            errors.append("carrier matrix exceeds structural limits")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    errors.append("carrier matrix keys must be strings")
                    return
                if re.sub(r"[^a-z0-9]", "", key.lower()) in FORBIDDEN_NORMALIZED_KEYS:
                    errors.append("private subscriber or credential field is not allowed")
                    return
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > MAX_FILE_BYTES:
                errors.append("carrier matrix contains an oversized string")
                return
            if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item)
                    and re.search(r"\b(?:\+?\d[\s().-]*){7,16}\b", item)):
                errors.append("phone-like or subscriber-like value is not allowed")
                return
            if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", item):
                errors.append("account-like value is not allowed")
                return
        elif not isinstance(item, (int, float, bool, type(None))):
            errors.append("carrier matrix contains a non-JSON value")
            return


def validate_matrix(matrix: object) -> list[str]:
    """Return bounded, non-echoing contract errors."""
    errors: list[str] = []
    required = {
        "schema_version", "matrix_id", "scope", "evidence_policy", "safety",
        "sources", "profiles", "capability_rows", "dual_sim", "peer",
    }
    if not _keys(matrix, required, required, "carrier matrix", errors):
        return errors[:MAX_ERRORS]
    _privacy_guard(matrix, errors)
    if errors:
        return errors[:MAX_ERRORS]
    if matrix["schema_version"] != 1:
        errors.append("unsupported carrier-matrix schema version")
    if not _identifier(matrix["matrix_id"]):
        errors.append("invalid matrix id")
    if not _text(matrix["scope"], 1000) or not _text(matrix["evidence_policy"], 1000):
        errors.append("scope and evidence policy must be bounded text")

    safety_required = {
        "emergency_calls", "test_destinations", "subscriber_data",
        "esim_destructive_actions",
    }
    safety = matrix["safety"]
    if _keys(safety, safety_required, safety_required, "safety", errors):
        expected = {
            "emergency_calls": "prohibited",
            "test_destinations": "private-allowlist-only",
            "subscriber_data": "private-raw-evidence-only",
            "esim_destructive_actions": "separate-explicit-authorization-required",
        }
        if safety != expected:
            errors.append("carrier-matrix safety policy is not fail-closed")

    sources = matrix["sources"]
    source_ids = set()
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list")
    else:
        fields = {"id", "url", "checked_on", "claim"}
        for source in sources:
            if not _keys(source, fields, fields, "source", errors):
                continue
            if not _identifier(source["id"]):
                errors.append("source has an invalid id")
            elif source["id"] in source_ids:
                errors.append("duplicate source id")
            else:
                source_ids.add(source["id"])
            if not isinstance(source["url"], str) or not URL_RE.fullmatch(source["url"]):
                errors.append("source must use a credential-free HTTPS URL")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(source["checked_on"])):
                errors.append("source check date must be YYYY-MM-DD")
            if not _text(source["claim"], 1000):
                errors.append("source claim must be bounded text")

    profiles = matrix["profiles"]
    profile_ids = set()
    profile_types = {}
    profile_fields = {
        "id", "device_role", "device_model", "carrier", "network", "sim_type",
        "subscription_type", "plan_name", "availability", "firmware_scope",
        "apn_context", "ims_context",
    }
    if not isinstance(profiles, list) or len(profiles) < 2:
        errors.append("at least two FP6 carrier profiles are required")
    else:
        for profile in profiles:
            if not _keys(profile, profile_fields, profile_fields, "profile", errors):
                continue
            profile_id = profile["id"]
            if not _identifier(profile_id):
                errors.append("profile has an invalid id")
                continue
            if profile_id in profile_ids:
                errors.append("duplicate profile id")
            profile_ids.add(profile_id)
            if profile["device_role"] != "harness":
                errors.append("FP6 carrier profile must bind the harness role")
            if not isinstance(profile["sim_type"], str) or profile["sim_type"] not in {
                    "esim", "physical"}:
                errors.append("profile has an invalid SIM type")
            else:
                profile_types[profile_id] = profile["sim_type"]
            if (not isinstance(profile["availability"], str)
                    or profile["availability"] not in AVAILABILITY_STATES):
                errors.append("profile has an invalid availability state")
            for field in ("device_model", "carrier", "network",
                          "subscription_type", "firmware_scope", "apn_context",
                          "ims_context"):
                if not _text(profile[field]):
                    errors.append("profile text field is invalid")
            if profile["plan_name"] is not None and not _text(profile["plan_name"]):
                errors.append("profile plan name must be null or bounded text")

    rows = matrix["capability_rows"]
    row_ids = set()
    coverage = set()
    row_fields = {
        "id", "profile_id", "capability", "plan_eligibility", "source_refs",
        "observation_status", "evidence_refs", "notes",
    }
    if not isinstance(rows, list) or not rows:
        errors.append("capability rows must be a non-empty list")
    else:
        for row in rows:
            if not _keys(row, row_fields, row_fields, "capability row", errors):
                continue
            if not _identifier(row["id"]):
                errors.append("capability row has an invalid id")
            elif row["id"] in row_ids:
                errors.append("duplicate capability-row id")
            else:
                row_ids.add(row["id"])
            if (not _identifier(row["profile_id"])
                    or row["profile_id"] not in profile_ids):
                errors.append("capability row references an unknown profile")
            valid_capability = (isinstance(row["capability"], str)
                                and row["capability"] in CAPABILITIES)
            if not valid_capability:
                errors.append("capability row has an invalid capability")
            elif _identifier(row["profile_id"]):
                pair = (row["profile_id"], row["capability"])
                if pair in coverage:
                    errors.append("duplicate profile/capability row")
                coverage.add(pair)
            if (not isinstance(row["plan_eligibility"], str)
                    or row["plan_eligibility"] not in PLAN_STATES):
                errors.append("capability row has an invalid plan-eligibility state")
            refs = row["source_refs"]
            refs_valid = (isinstance(refs, list)
                          and all(isinstance(ref, str) for ref in refs))
            if (not refs_valid or len(set(refs)) != len(refs)
                    or any(ref not in source_ids for ref in refs)):
                errors.append("capability row has invalid source references")
            if row["plan_eligibility"] == "eligible" and not refs:
                errors.append("eligible capability row requires a source")
            observation_status = row["observation_status"]
            valid_observation_status = (
                isinstance(observation_status, str)
                and observation_status in OBSERVATION_STATES)
            if not valid_observation_status:
                errors.append("capability row has an invalid observation status")
            evidence = row["evidence_refs"]
            if (not isinstance(evidence, list)
                    or any(not _text(ref) for ref in evidence)):
                errors.append("capability row has invalid evidence references")
            if (valid_observation_status and observation_status in {"PASS", "FAIL"}
                    and not evidence):
                errors.append("observed PASS/FAIL requires an evidence reference")
            if not _text(row["notes"], 1000):
                errors.append("capability row notes must be bounded text")

    common = {"voice", "sms", "data", "volte", "vowifi", "5g"}
    for profile_id in profile_ids:
        missing = common - {capability for owner, capability in coverage
                            if owner == profile_id}
        if missing:
            errors.append("FP6 profile is missing required capability rows")
        if profile_types.get(profile_id) == "esim" and (
                profile_id, "esim-lifecycle") not in coverage:
            errors.append("eSIM profile lacks an eSIM lifecycle row")

    dual_fields = {
        "status", "reason", "required_recorded_defaults", "voice_default",
        "data_default", "sms_default",
    }
    dual = matrix["dual_sim"]
    if _keys(dual, dual_fields, dual_fields, "dual-SIM contract", errors):
        dual_status = dual["status"]
        valid_dual_status = (isinstance(dual_status, str)
                             and dual_status in OBSERVATION_STATES)
        if not valid_dual_status:
            errors.append("dual-SIM contract has an invalid status")
        defaults = dual["required_recorded_defaults"]
        if (not isinstance(defaults, list)
                or not all(isinstance(item, str) for item in defaults)
                or set(defaults) != {"voice", "data", "sms"}):
            errors.append("dual-SIM contract must record voice, data and SMS defaults")
        default_values = [dual[field] for field in
                          ("voice_default", "data_default", "sms_default")]
        if any(not _text(value) for value in default_values):
            errors.append("dual-SIM default values must be bounded text")
        elif valid_dual_status and dual_status in {"NOT_RUN", "BLOCKED"} and any(
                value != "UNRECORDED" for value in default_values):
            errors.append("unrun dual-SIM defaults must remain UNRECORDED")
        elif valid_dual_status and dual_status in {"PASS", "FAIL"} and any(
                value == "UNRECORDED" for value in default_values):
            errors.append("observed dual-SIM defaults must all be recorded")
        if not _text(dual["reason"]):
            errors.append("dual-SIM reason must be bounded text")

    peer_fields = {
        "device_role", "device_model", "carrier", "sim_type", "availability",
        "required_capabilities", "optional_capabilities",
        "not_required_capabilities", "limitations",
    }
    peer = matrix["peer"]
    if _keys(peer, peer_fields, peer_fields, "peer", errors):
        if peer["device_role"] != "telephony-peer":
            errors.append("peer must bind the telephony-peer role")
        if peer["sim_type"] != "physical":
            errors.append("selected peer SIM type does not match the rig contract")
        if (not isinstance(peer["availability"], str)
                or peer["availability"] not in AVAILABILITY_STATES):
            errors.append("peer has an invalid availability state")
        required_capabilities = peer["required_capabilities"]
        if (not isinstance(required_capabilities, list)
                or not all(isinstance(cap, str) for cap in required_capabilities)
                or set(required_capabilities) != {"voice", "sms"}):
            errors.append("peer must require ordinary voice and SMS")
        groups = (peer["required_capabilities"], peer["optional_capabilities"],
                  peer["not_required_capabilities"])
        if any(not isinstance(group, list)
               or not all(isinstance(cap, str) for cap in group)
               or len(set(group)) != len(group)
               or any(cap not in CAPABILITIES for cap in group) for group in groups):
            errors.append("peer capability groups are invalid")
        elif (set(groups[0]) & set(groups[1]) or set(groups[0]) & set(groups[2])
              or set(groups[1]) & set(groups[2])):
            errors.append("peer capability groups overlap")
        for field in ("device_model", "carrier", "limitations"):
            if not _text(peer[field], 1000):
                errors.append("peer text field is invalid")

    return errors[:MAX_ERRORS]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the public carrier matrix; no device or network access")
    parser.add_argument("--matrix", type=Path,
                        default=ROOT / "config" / "carrier-matrix.json")
    args = parser.parse_args(argv)
    try:
        matrix = load_matrix(args.matrix)
        errors = validate_matrix(matrix)
    except MatrixError as error:
        errors = [str(error)]
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    profiles = len(matrix["profiles"])
    rows = len(matrix["capability_rows"])
    pending = sum(row["observation_status"] in {"NOT_RUN", "BLOCKED"}
                  for row in matrix["capability_rows"])
    print(f"VALID carrier matrix: {profiles} FP6 profiles; {rows} capability rows; "
          f"{pending} pending observations.")
    print("Plan eligibility is not device evidence; no telephony action was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
