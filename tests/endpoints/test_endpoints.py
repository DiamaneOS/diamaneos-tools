"""endpoint contract design acceptance: endpoint/hosting contracts.

Validates tools:config/endpoints.json + infra:config/services.json against
schemas/endpoint-contract.schema.json with a stdlib hand-rolled validator
(no jsonschema dependency, per toolchain reuse).

Covers packet PLUS adversarial paths (working contract point 3):
malformed, oversized, hostile (credential URLs, unknown fields, duplicates),
and whole-envelope privacy (no secrets/identifiers anywhere in public files).
Green tests never imply contract compliance on their own; the Done checklist
maps each AC/V-case to evidence separately.
"""
import copy
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
WORK_ROOT = os.path.dirname(TOOLS)

ENDPOINTS_PATH = os.path.join(TOOLS, "config", "endpoints.json")
SCHEMA_PATH = os.path.join(TOOLS, "schemas", "endpoint-contract.schema.json")
SERVICES_PATH = os.path.join(WORK_ROOT, "infrastructure", "config",
                             "services.json")

# Appendix B (packet ref-section-30): every host must appear exactly once .
APPENDIX_B_HOSTS = [
    "releases.grapheneos.org",
    "apps.grapheneos.org",
    "update.vanadium.app",
    "dl.vanadium.app",
    "time.grapheneos.org",
    "connectivitycheck.grapheneos.network",
    "grapheneos.online",
    "qualcomm.psds.grapheneos.org",
    "supl.grapheneos.org",
    "remoteprovisioning.grapheneos.org",
    "widevineprovisioning.grapheneos.org",
    "gstatic.grapheneos.org",
    "dnscheck.grapheneos.org",
    "grapheneos.org/releases.atom",
]

# Upstream-dependent endpoints that MUST route via the EU proxy (R11.7).
PROXY_REQUIRED = {
    "psds-cache": "qualcomm.psds.grapheneos.org",
    "supl": "supl.grapheneos.org",
    "rkp-proxy": "remoteprovisioning.grapheneos.org",
    "widevine-proxy": "widevineprovisioning.grapheneos.org",
    "ct-mirror": "gstatic.grapheneos.org",
}

TASK_RE = re.compile(r"^FP6-[0-9]{3}$")
ID_RE = re.compile(r"^[a-z0-9-]+$")
MAX_FILE_BYTES = 262144  # 256 KiB hygiene bound for a config inventory

# Field names that must never appear in a public contract file (working
# contract point 4: secrets/identifiers/custody stay out of public repos).
FORBIDDEN_KEYS = {
    "private_key", "private-key", "password", "token", "share", "pin",
    "custody", "custodian", "address", "secret", "seed", "mnemonic",
    "serial", "imei", "imsi", "iccid",
}

ENDPOINT_REQUIRED = [
    "id", "upstream_host", "replacement_host", "consumer",
    "consumer_version", "purpose", "protocol", "protocol_status",
    "path_method", "request_fields", "response_fields",
    "verification_root", "timeout_retry", "cache_freshness",
    "failure_fallback", "exposed_data", "retention", "jurisdiction",
    "owner_task", "status",
]


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def iter_keys(obj):
    """Yield every dict key in the inventory, recursively."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            yield key
            for sub in iter_keys(val):
                yield sub
    elif isinstance(obj, list):
        for item in obj:
            for sub in iter_keys(item):
                yield sub


def norm_key(key):
    return key.lower().replace("-", "").replace("_", "")


def check_endpoint(ep, errors, idx):
    where = "endpoints[%d]" % idx
    if not isinstance(ep, dict):
        errors.append(where + ": not an object")
        return
    for key in ENDPOINT_REQUIRED:
        if key not in ep:
            errors.append(where + ": missing required field '" + key + "'")
    allowed = set(ENDPOINT_REQUIRED) | {"blocker"}
    for key in ep:
        if key not in allowed:
            errors.append(where + ": unknown field '" + key + "'")
    if not isinstance(ep.get("id"), str) or not ID_RE.match(ep.get("id", "")):
        errors.append(where + ": bad id")
    elif len(ep["id"]) > 64:
        errors.append(where + ": id over 64 chars")
    for key in ("purpose", "protocol", "verification_root", "timeout_retry",
                "cache_freshness", "failure_fallback", "exposed_data",
                "retention"):
        val = ep.get(key)
        if val is not None and (not isinstance(val, str) or not val.strip()):
            errors.append(where + ": empty '" + key + "'")
        elif isinstance(val, str) and len(val) > 2000:
            errors.append(where + ": '" + key + "' over 2000 chars")
    if ep.get("protocol_status") not in ("specified", "blocked"):
        errors.append(where + ": bad protocol_status")
    if ep.get("status") not in ("specified", "blocked-contract"):
        errors.append(where + ": bad status")
    if ep.get("jurisdiction") not in ("eu-primary",
                                      "eu-proxy-to-non-eu-upstream",
                                      "non-eu-mirror"):
        errors.append(where + ": bad jurisdiction")
    if not isinstance(ep.get("owner_task"), str) or \
            not TASK_RE.match(ep.get("owner_task", "")):
        errors.append(where + ": bad owner_task")
    for key in ("request_fields", "response_fields"):
        val = ep.get(key)
        if not isinstance(val, list) or len(val) > 64 or \
                not all(isinstance(v, str) for v in val):
            errors.append(where + ": bad '" + key + "'")
    # Blocker discipline: blocked work carries an owned blocker; specified
    # work carries none (unknowns are explicit, never silent).
    blocked = ep.get("protocol_status") == "blocked" or \
        ep.get("status") == "blocked-contract"
    if blocked:
        blk = ep.get("blocker")
        if not isinstance(blk, dict) or not blk.get("description") or \
                not isinstance(blk.get("owner_task"), str) or \
                not TASK_RE.match(blk.get("owner_task", "")):
            errors.append(where + ": blocked without owned blocker")
    elif "blocker" in ep:
        errors.append(where + ": specified contract must not carry blocker")
    # exact stale/failure behavior, never a generic retry-forever.
    fb = ep.get("failure_fallback", "")
    if isinstance(fb, str) and "retry forever" in fb.replace("-", " ").lower():
        errors.append(where + ": generic retry-forever fallback")
    # replacement is never a GrapheneOS host.
    rep = ep.get("replacement_host", "")
    if isinstance(rep, str) and "grapheneos" in rep.lower():
        errors.append(where + ": replacement still on GrapheneOS")
    # Hostile: no credential-bearing URLs anywhere in string fields.
    for key, val in ep.items():
        vals = val if isinstance(val, list) else [val]
        for item in vals:
            if not isinstance(item, str):
                continue
            if re.search(r"://[^/]*@", item):
                errors.append(where + ": credential-bearing URL in '"
                              + key + "'")
            low = item.lower()
            if "begin private key" in low or "begin rsa private key" in low:
                errors.append(where + ": private key material in '"
                              + key + "'")


def validate_inventory(inv):
    """Return a list of error strings; empty means valid."""
    errors = []
    if not isinstance(inv, dict):
        return ["top level is not an object"]
    for key in ("schema_version", "note", "canonical_domain", "providers",
                "topology", "endpoints", "client_defaults"):
        if key not in inv:
            errors.append("missing top-level '" + key + "'")
    allowed_top = {"schema_version", "note", "canonical_domain",
                   "providers", "topology", "endpoints", "client_defaults"}
    for key in inv:
        if key not in allowed_top:
            errors.append("unknown top-level '" + key + "'")
    if inv.get("schema_version") != 1:
        errors.append("bad schema_version")
    if inv.get("canonical_domain") != "diamaneos.de":
        errors.append("canonical domain must be diamaneos.de (R11.1)")
    eps = inv.get("endpoints", [])
    if not isinstance(eps, list) or not eps:
        errors.append("endpoints must be a non-empty array")
        return errors
    seen_ids, seen_hosts = set(), set()
    for i, ep in enumerate(eps):
        check_endpoint(ep, errors, i)
        if isinstance(ep, dict):
            if ep.get("id") in seen_ids:
                errors.append("duplicate endpoint id '" +
                              str(ep.get("id")) + "'")
            seen_ids.add(ep.get("id"))
            if ep.get("upstream_host") in seen_hosts:
                errors.append("duplicate upstream_host '" +
                              str(ep.get("upstream_host")) + "'")
            seen_hosts.add(ep.get("upstream_host"))
    # Appendix B coverage is exact — every host once, no extras.
    if set(seen_hosts) != set(APPENDIX_B_HOSTS):
        errors.append("Appendix B coverage mismatch: missing=" +
                      str(sorted(set(APPENDIX_B_HOSTS) - set(seen_hosts))) +
                      " extra=" +
                      str(sorted(set(seen_hosts) - set(APPENDIX_B_HOSTS))))
    # R11.7: upstream-dependent endpoints route via the EU proxy.
    by_id = {e["id"]: e for e in eps
             if isinstance(e, dict) and "id" in e}
    for eid, host in PROXY_REQUIRED.items():
        if eid in by_id and \
                by_id[eid].get("jurisdiction") != \
                "eu-proxy-to-non-eu-upstream":
            errors.append(eid + ": must be eu-proxy-to-non-eu-upstream")
    # Providers are candidates only (no signup/cost implied).
    for i, prov in enumerate(inv.get("providers", [])):
        if not isinstance(prov, dict) or prov.get("status") != "candidate":
            errors.append("providers[%d]: must be status=candidate" % i)
        if not isinstance(prov.get("verify_at"), str) or \
                not TASK_RE.match(prov.get("verify_at", "")):
            errors.append("providers[%d]: bad verify_at" % i)
    # community/release separation is stated with distinct credentials.
    topo = inv.get("topology", {})
    if isinstance(topo, dict):
        for key in topo:
            if key not in ("eu_primary", "non_eu_mirror",
                           "community_separation", "management_path"):
                errors.append("topology: unknown field '" + key + "'")
        sep = topo.get("community_separation", "")
        if "distinct credentials" not in sep and \
                "distinct hosts" not in sep:
            errors.append("topology must state distinct community/release "
                          "hosts AND credentials")
    # Whole-envelope privacy: forbidden names must not appear as any key
    # at any depth (substring match catches api_token-style evasions).
    norm_forbidden = [norm_key(f) for f in FORBIDDEN_KEYS]
    for key in iter_keys(inv):
        if not isinstance(key, str):
            errors.append("non-string key in inventory")
            continue
        nkey = norm_key(key)
        for forb in norm_forbidden:
            if forb and forb in nkey:
                errors.append("forbidden key in public file: '" + key + "'")
                break
    return errors


class EndpointContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = load_json(ENDPOINTS_PATH)
        cls.schema = load_json(SCHEMA_PATH)
        cls.services = load_json(SERVICES_PATH)

    # --- schema file itself ---
    def test_schema_is_draft07_closed(self):
        self.assertIn("draft-07", self.schema.get("$schema", ""))
        self.assertFalse(self.schema.get("additionalProperties", True))
        self.assertIn("endpoint", self.schema.get("definitions", {}))

    def test_schema_required_covers_packet_contract(self):
        required = self.schema["definitions"]["endpoint"]["required"]
        for field in ("verification_root", "timeout_retry",
                      "cache_freshness", "failure_fallback", "exposed_data",
                      "retention", "jurisdiction", "owner_task"):
            self.assertIn(field, required)

    # --- full Appendix B coverage, nothing left on GrapheneOS ---
    def test_inventory_valid(self):
        self.assertEqual(validate_inventory(self.inv), [])

    def test_fourteen_endpoints_exact(self):
        self.assertEqual(len(self.inv["endpoints"]), 14)

    def test_no_endpoint_left_on_grapheneos(self):
        for ep in self.inv["endpoints"]:
            self.assertNotIn("grapheneos",
                             ep["replacement_host"].lower(), ep["id"])

    # --- Swiss DNS + non-EU upstreams disclosed distinctly ---
    def test_swiss_dns_disclosed_as_exception(self):
        defaults = {d["name"]: d for d in self.inv["client_defaults"]}
        self.assertIn("private-dns", defaults)
        dns = defaults["private-dns"]
        self.assertIn("Quad9", dns["default"])
        self.assertEqual(dns["jurisdiction"], "non-eu-direct-exception")

    def test_proxy_endpoints_disclosed(self):
        self.assertEqual(len([e for e in self.inv["endpoints"]
                              if e["jurisdiction"] ==
                              "eu-proxy-to-non-eu-upstream"]), 5)

    # --- candidates only, no signup implied ---
    def test_all_providers_candidates(self):
        for prov in self.inv["providers"]:
            self.assertEqual(prov["status"], "candidate", prov)

    # --- compromise separation ---
    def test_community_release_separation(self):
        sep = self.inv["topology"]["community_separation"]
        self.assertIn("distinct credentials", sep)
        self.assertIn("no release secrets on community hosts", sep)

    # --- every contract states exact failure behavior ---
    def test_every_failure_fallback_exact(self):
        for ep in self.inv["endpoints"]:
            self.assertTrue(len(ep["failure_fallback"]) > 40, ep["id"])
            self.assertNotIn("retry forever",
                             ep["failure_fallback"].replace("-", " ").lower(),
                             ep["id"])

    # --- services.json: planned roles, no addresses/secrets ---
    def test_services_hosts_planned_no_addresses(self):
        for host in self.services["hosts"]:
            self.assertIn(host["status"], ("planned", "planned-conditional"),
                          host["id"])
            self.assertIsNone(host["address"], host["id"])

    def test_services_reference_known_hosts(self):
        known = {h["id"] for h in self.services["hosts"]}
        for svc in self.services["services"]:
            self.assertIn(svc["host"], known, svc["id"])
            for eid in svc["endpoints"]:
                self.assertIn(eid,
                              {e["id"] for e in self.inv["endpoints"]},
                              svc["id"])

    def test_services_no_secret_keys(self):
        blob = json.dumps(self.services).lower()
        for key in ("private_key", "password", "token", "secret",
                    "address\""):
            if key == "address\"":
                continue  # address keys exist but must be null (checked above)
            self.assertNotIn('"%s"' % key, blob, key)

    # --- adversarial: malformed / hostile inputs must FAIL validation ---
    def test_missing_required_field_rejected(self):
        bad = copy.deepcopy(self.inv)
        del bad["endpoints"][0]["verification_root"]
        self.assertTrue(validate_inventory(bad))

    def test_unknown_field_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][0]["admin_password"] = "hunter2"
        errs = validate_inventory(bad)
        self.assertTrue(errs, "unknown field must fail closed")

    def test_duplicate_id_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][1]["id"] = bad["endpoints"][0]["id"]
        self.assertTrue(validate_inventory(bad))

    def test_blocked_without_owner_rejected(self):
        bad = copy.deepcopy(self.inv)
        del bad["endpoints"][0]["blocker"]
        self.assertTrue(validate_inventory(bad))

    def test_specified_with_blocker_rejected(self):
        bad = copy.deepcopy(self.inv)
        for ep in bad["endpoints"]:
            if ep["status"] == "specified":
                ep["blocker"] = {"description": "x",
                                 "owner_task": "FP6-102"}
                break
        self.assertTrue(validate_inventory(bad))

    def test_credential_url_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][3]["replacement_host"] = \
            "https://admin:s3cret@mirror.example.com/dl"
        errs = validate_inventory(bad)
        self.assertTrue(any("credential" in e for e in errs), errs)

    def test_oversized_field_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][0]["purpose"] = "x" * 2001
        self.assertTrue(validate_inventory(bad))

    def test_oversized_id_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][0]["id"] = "a" * 65
        self.assertTrue(validate_inventory(bad))

    def test_retry_forever_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][0]["failure_fallback"] = \
            "on failure just retry forever until it works"
        errs = validate_inventory(bad)
        self.assertTrue(any("retry-forever" in e for e in errs), errs)

    def test_grapheneos_replacement_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"][0]["replacement_host"] = "releases.grapheneos.org"
        errs = validate_inventory(bad)
        self.assertTrue(any("GrapheneOS" in e for e in errs), errs)

    def test_missing_appendix_b_host_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["endpoints"] = [e for e in bad["endpoints"]
                            if e["upstream_host"] != "supl.grapheneos.org"]
        errs = validate_inventory(bad)
        self.assertTrue(any("coverage mismatch" in e for e in errs), errs)

    def test_forbidden_key_rejected(self):
        bad = copy.deepcopy(self.inv)
        bad["topology"]["management_path"] += " token ABCDEF"
        bad["topology"]["api_token"] = "ABCDEF"
        self.assertTrue(validate_inventory(bad))

    def test_file_size_bound(self):
        size = os.path.getsize(ENDPOINTS_PATH)
        self.assertLess(size, MAX_FILE_BYTES, size)

    def test_public_envelope_has_no_identity_or_secret(self):
        for path in (ENDPOINTS_PATH, SERVICES_PATH):
            with open(path, encoding="utf-8") as fh:
                blob = fh.read()
            low = blob.lower()
            self.assertNotIn("begin private key", low, path)
            self.assertNotIn("begin rsa private key", low, path)
            self.assertNotRegex(blob, r"://[^/\s]*@",
                                "credential-bearing URL in " + path)


if __name__ == "__main__":
    unittest.main()
