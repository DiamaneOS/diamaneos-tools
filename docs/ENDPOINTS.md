# Endpoint contracts

`config/endpoints.json` is the source of truth for the 14 inherited endpoint contracts. `schemas/endpoint-contract.schema.json` defines its format. Infrastructure's `config/services.json` assigns these contracts to authority roles; `schemas/services.schema.json` validates that selection. These are design inputs for implementation, not deployed configuration.

Use the setup and explicit integration command in [TESTING.md](TESTING.md). A valid result means both documents satisfy their schemas and reference/authority checks. It does not mean that a server, native client, account, signature ceremony or physical device has passed testing. Every service remains `blocked-pending-implementation` until its implementation acceptance evidence exists.

The existing `owner_task` and `verify_at` fields carry maintainer tracking metadata and remain required wherever their schemas specify them. Their values stay stable for tooling compatibility; the adjacent purpose, contract and blocker text defines the work for public readers. Those IDs do not change a service's authority or acceptance state.

## Reading the evidence

Each endpoint has `source_refs` into the `sources` array. An entry records the repository, full commit, actual file path, symbol locators and SHA-256 of the retrieved file. Open `repository/blob/revision/path` to inspect it. The observations were retrieved over HTTPS on 2026-09-11; file hashes make this review reproducible but do not authenticate a future shipping release.

`manifest-pin` means the revision agrees with the reviewed source manifest. `research-pin` means a separately resolved AppStore, Info or Vanadium source revision; it is **not** proof that a particular prebuilt APK was built from that revision. `server-reference` describes upstream routing and upstream destinations, not a contract we blindly copy. The relevant app/device implementation must bind the final binary and recheck these observations when the pin changes. The frameworks tree API was truncated, so relevant services files were fetched directly; this is not a claim to have searched every upstream file.

The pinned sources establish the following protocol boundaries:

- `update.vanadium.app` and `dl.vanadium.app` serve Chromium components. Browser APK delivery belongs to the AppStore catalog. Version 2 renames their IDs to `browser-component-check` and `browser-component-download`; service consumers must migrate together.
- `grapheneos.online` supplies the fallback HTTP connectivity URLs. It is not an unknown attestation endpoint.
- `dnscheck.grapheneos.org` is a DNS probe suffix. It needs authoritative wildcard records and a chosen resolver, not an HTTP service on the release VPS.
- `gstatic.grapheneos.org` serves Android platform CT lists. Its pinned verifier allowlists the public key independently and verifies each list's signature; the downloaded public key alone is not a trust root.
- HTTPS time uses an integer `X-Time` in Unix **milliseconds**. The Android client does not send a nonce or implement a six-source quorum. Its bootstrap hostname lookup explicitly bypasses Private DNS; do not claim the configured Swiss DoT default covers that path. The project time server owns authenticated upstream sampling and agreement.
- TLS proxies can see the outer RKP, Widevine and SUPL protocol. Some fields may be encrypted, but saying the entire payload is opaque would overstate privacy. The contract names each actual upstream and what it receives.

## Bounds and freshness

`limits_profiles` are selected **project staging policy**, in bytes and seconds. They are not claims that upstream clients currently enforce those limits. `timeout_retry` separately records observed client behavior; each `implementation_blockers` entry identifies unbound native inputs. Do not replace a native protocol with the schematic example or silently enlarge a ceiling to get an implementation accepted.

HTTP/TCP deadlines are total monotonic deadlines, including streaming; idle and connect bounds are additional limits. Body limits apply to streamed bytes even without Content-Length. Count decompressed bytes separately where decompression occurs; catalog-declared gzip and decoded APK sizes must both match and remain within their limits. Never buffer an entire maximum-size artifact in RAM. Reserve quota before writing a staging file and remove partial files on failure/cancel/restart. The response ceiling for artifact profiles is an object ceiling; OTA selector metadata is separately capped at 4096 bytes and signed app catalog metadata at 8 MiB.

For project HTTP/TCP services, a profile allows one attempt, zero redirects, zero queued requests, 64 active operations, 120 requests/s globally, and 60 requests/minute per source IP with a burst allowance of 20. Use a bounded in-memory rate table (4096 entries, idle expiry 60 s, reject new entries when full); retain no per-IP history. Apply host-level aggregate caps as specified in the infrastructure runbook. These are initial measurable ceilings; NAT/mobile burst compatibility and upstream payload sizes must be qualified before service activation. DNS provider policy is external: the DNS profile's 512-byte/10-second values bound our synthetic health probe, not INWX's global DNS service or every native DNS response.

`max_verified_age_seconds` measures age since the last successful check of the **authoritative current publication or upstream state**, not age since a local copy/cache hit, and not merely the release's signed build date. A valid unchanged upstream conditional response can refresh the check only against an already verified exact object/ETag. A local rehash cannot prove upstream freshness. Do not rewrite signed timestamps to make old data look new. Published releases may legitimately be older than the freshness window. Freshness of selection, trustworthiness of immutable bytes, and the client's own expiry/rollback checks are separate facts.

When selection is stale, use each endpoint's `failure_fallback`: no fresh selector or live response is manufactured. Existing verified immutable artifact URLs can remain available for recovery. A client may retain old state even after the server starts returning errors; the inventory explicitly describes those cases instead of claiming a nonexistent native expiry check. Pinned Conscrypt marks a list non-compliant when its timestamp is in the future or its age is strictly greater than 70 days. `CertificateTransparency.checkCT` skips CT enforcement for a non-compliant/unusable store: this is a security degradation, not a guarantee that TLS connections stop. `CT-BIND` owns native verification of that behavior on the shipped build. The project 48-hour server refresh limit is a separate policy and cannot prevent client fail-open by itself.

## Examples and activation gates

`example.kind=wire-shape` freezes the reviewed transport shape, including empty 204 bodies and time units. Example timestamps, device tokens and release IDs are illustrative. `schematic-not-conformance` explicitly has no valid cryptographic/native acceptance fixture yet. Do not treat it as one. Signed catalog/OTA, CBOR, ASN.1, CRX, CT and proprietary device samples must come from the pinned consumer/test producer under the listed implementation owner; invented successful payloads would conceal the missing evidence.

The ten unique implementation gates are defined once by ID in the endpoint rows (a shared gate may be referenced by both component rows). None can be cleared by changing `status` alone. A gate closes with the actual pin/key/config, valid native fixture, corrupt/truncated/oversized case, measured timeout and stale/outage result in the implementing task. Source-only and fixture-only tests cannot clear device eligibility or hardware behavior.

`COMPONENT-BIND` is a real compatibility conflict: the observed Vanadium patch hardcodes the component download host. Mirroring the unmodified APK and its package repository does not prove that this host can be repointed. CT and browser-component delivery and browser APK delivery must demonstrate a supported configuration or return the decision to the owner. Rebuilding/resigning, TLS interception, leaving inherited traffic or disabling components is not an authorized automatic resolution.

## Maintenance

Service monitoring consumes endpoint ID, host, owner, limits profile, refresh age and failure behavior for health checks and aggregate alerts. Run schema and cross-repository validation on every inventory/service change. A changed consumer pin requires rechecking the cited paths, byte bounds, trust and jurisdiction before accepting the new contract. Provider capabilities and prices are dated observations and are rechecked by infrastructure staging before an owner orders anything.
