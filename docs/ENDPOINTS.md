# Endpoint contracts

Contracts for the network services DiamaneOS inherits: design inputs, not
deployed configuration.

- `config/endpoints.json` holds the 16 inherited endpoint contracts (format:
  `schemas/endpoint-contract.schema.json`).
- The infrastructure repository's `config/services.json` (validated by
  `schemas/services.schema.json`) assigns each contract DiamaneOS hosts to an
  authority role; the geocoder has none (below).
- There is no Info release feed: GrapheneOS's Info app is in no build.

Validate as in [TESTING.md](TESTING.md#endpoint-contracts). A valid result
means the schema and reference/authority checks pass; no server, native client,
account, signature ceremony or device was tested. Every service stays
`blocked-pending-implementation` until implementation acceptance evidence
exists. `owner_task` and `verify_at` are maintainer tracking metadata, required
where the schemas say and kept stable for tooling; they change no authority or
acceptance state, and the purpose, contract and blocker text defines the work.

## Reading the evidence

Each endpoint's `source_refs` point into `sources`: repository, full commit,
file path, symbol locators and the retrieved file's SHA-256 (open
`repository/blob/revision/path`).

- Retrieved over HTTPS on 2026-09-11; on 2026-09-26 for the network-location,
  geocoder and attestation contracts and Vanadium's (the GrapheneOS browser's)
  own connectivity checks; on 2026-10-06 for the DiamaneOS Updater fork. Hashes
  make the review reproducible, not a future release authentic.
- `manifest-pin`: the revision agrees with the reviewed source manifest.
- `research-pin`: a separately resolved AppStore or Vanadium revision, **not**
  proof that a given prebuilt APK came from it.
- `server-reference`: upstream routing and destinations, not a contract to
  copy.
- The app or device implementation must bind the final binary and recheck these
  observations when a pin changes.
- The frameworks tree API was truncated, so the relevant services files were
  fetched directly; this claims no search of every upstream file.

## Protocol boundaries

- `releases.diamaneos.de` is the OS update server. Only official builds
  (`DIAMANEOS_OFFICIAL_BUILD=true`) include its client, the DiamaneOS Updater
  fork, which asks for `/{DEVICE}-{channel}` (`FP6-stable` by default) over TLS
  pinned to the ISRG roots. The server is not live: the name has no address
  record (a TXT-only placeholder), so the lookup fails and nothing is contacted
  (2026-10-06). Every check fails, and the Updater backs off up to its six-hour
  interval and installs nothing; an answer that is not update information also
  fails the check. A build signed with public test keys never downloads or
  installs an update, whatever the server offers.
- `update.vanadium.app` and `dl.vanadium.app` serve Chromium components;
  browser APKs come from the AppStore catalog. Their IDs are
  `browser-component-check` and `browser-component-download` (renamed in
  version 2; consumers migrate together).
- `grapheneos.online` supplies fallback HTTP connectivity URLs, not an unknown
  attestation endpoint.
- `dnscheck.grapheneos.org` is a DNS probe suffix needing authoritative
  wildcard records and a chosen resolver, not an HTTP service on the release
  VPS.
- `gstatic.grapheneos.org` serves Android platform CT lists. The pinned
  verifier allowlists the public key independently and checks each list's
  signature, so the downloaded key alone is no trust root.
- HTTPS time uses an integer `X-Time` in Unix **milliseconds**. The client sends
  no nonce and has no six-source quorum, and its bootstrap hostname lookup
  bypasses Private DNS, so the configured Swiss DoT default does not cover it.
  The project time server owns authenticated upstream sampling and agreement.
- `gs-loc.apple.grapheneos.org` relays Apple Wi-Fi/cell positioning, used only
  with network location on. Requests list nearby access points and cells, so a
  relay sees approximate location and must keep no cache or per-request logs;
  `client_defaults` discloses direct (unrelayed) choices. The network position
  seeds GNSS: in an indoor FP6 test on 2026-09-26, GPS alone had no fix for
  several minutes; with Apple directly, the first network fix came about 35 s
  after the request and GPS locked about 1 s later.
- `nominatim.grapheneos.org` answers Android Geocoder queries; DiamaneOS hosts
  no geocoder. `client_defaults` discloses direct use of OpenStreetMap's public
  Nominatim, which sees the device IP and searched text or coordinates, as a
  non-EU direct exception like the Swiss Private DNS default. The validator
  requires that entry and rejects a geocoder relay or service entry. Direct use
  must stay within OpenStreetMap's [Nominatim usage
  policy](https://operations.osmfoundation.org/policies/nominatim/) (identify
  the app, low request rate, attribution).
- `attestation.app` serves Auditor's opt-in remote verification and sample
  submission: the only stateful contract (paired accounts and history), needing
  DiamaneOS key pins and FP6 support GrapheneOS's server lacks.
- Vanadium also probes `connectivitycheck.grapheneos.network` itself, and its
  DNS-over-HTTPS probe uses that name even with OS checks off; only a rebuilt
  browser can repoint it (FP6-111).
- TLS proxies see the outer RKP, Widevine and SUPL protocol. Some fields may be
  encrypted, but calling the whole payload opaque would overstate privacy; each
  contract names the actual upstream and what it receives.

## Bounds and freshness

`limits_profiles` are **project staging policy** in bytes and seconds, not
claims about upstream clients; `timeout_retry` records observed client
behaviour and `implementation_blockers` names unbound native inputs. Never
replace a native protocol with the schematic example or raise a ceiling to get
an implementation accepted.

- HTTP/TCP deadlines are total monotonic deadlines, streaming included; idle
  and connect bounds come on top.
- Body limits apply to streamed bytes even without Content-Length. Count
  decompressed bytes separately; catalog-declared gzip and decoded APK sizes
  must both match and stay within limits.
- Never buffer a whole maximum-size artifact in RAM. Reserve quota before
  writing a staging file; remove partial files on failure, cancel or restart.
- Artifact-profile response ceilings are per object; OTA selector metadata is
  capped at 4096 bytes and signed app catalog metadata at 8 MiB.
- A project HTTP/TCP profile allows a single try (no retry), zero redirects,
  zero queued requests, 64 active operations, 120 requests/s globally and 60
  requests/minute per source IP with a burst of 20. It uses a bounded in-memory
  rate table (4096 entries, 60 s idle expiry, new entries rejected when full)
  and no per-IP history. Host-level aggregate caps follow the infrastructure
  runbook. These initial ceilings need NAT/mobile burst and upstream payload
  qualification before activation.
- DNS provider policy is external: the DNS profile's 512-byte/10-second values
  bound our synthetic health probe, not the DNS provider's global service or
  every native response.
- `max_verified_age_seconds` counts from the last successful check of the
  **authoritative current publication or upstream state**, not a local copy,
  cache hit or signed build date. An unchanged conditional response refreshes
  it only against an already verified exact object/ETag; a local rehash proves
  no freshness. Never rewrite signed timestamps (releases may be older than the
  window). Selection freshness, immutable-byte trust and client
  expiry/rollback checks are separate facts.
- When selection is stale, use the endpoint's `failure_fallback`: no fresh
  selector or live response is manufactured, and verified immutable artifact
  URLs may stay available for recovery. Clients may keep old state after server
  errors; the inventory says so rather than claiming a non-existent native
  expiry check.
- Pinned Conscrypt marks a CT list non-compliant when its timestamp is in the
  future or its age strictly exceeds 70 days, and
  `CertificateTransparency.checkCT` then skips CT enforcement: a security
  degradation, not a guarantee that TLS stops. `CT-BIND` owns native
  verification on the shipped build. The project's 48-hour server refresh limit
  cannot by itself prevent client fail-open.

## Examples and activation gates

- `example.kind=wire-shape` freezes the reviewed transport shape, including
  empty 204 bodies and time units; example timestamps, device tokens and
  release IDs are illustrative.
- `schematic-not-conformance` has no valid cryptographic/native acceptance
  fixture yet; never treat it as one. Signed catalog/OTA, CBOR, ASN.1, CRX, CT
  and proprietary device samples must come from the pinned consumer/test
  producer under the listed implementation owner; invented successful payloads
  would hide missing evidence.
- The twelve unique implementation gates are defined once by ID in the endpoint
  rows; a shared gate such as `COMPONENT-BIND` or `NETLOC-SCOPE` may appear in
  several. Changing `status` clears none.
- A gate closes with the actual pin/key/config, a valid native fixture, a
  corrupt/truncated/oversized case, a measured timeout and a stale/outage
  result in the implementing task. Source-only and fixture-only tests cannot
  clear device eligibility or hardware behaviour.
- `COMPONENT-BIND` is a real conflict: the observed Vanadium patch hardcodes the
  component download host, and mirroring the unmodified APK and its package
  repository does not prove it can be repointed.
- CT and browser-component delivery and browser APK delivery must show a
  supported configuration or return the decision to the responsible
  maintainer; rebuilding/resigning, TLS interception, leaving inherited traffic
  or disabling components is no authorized automatic resolution.

## Maintenance

Monitoring uses endpoint ID, host, owner, limits profile, refresh age and
failure behaviour. Run schema and cross-repository validation on every
inventory or service change; a changed consumer pin requires rechecking cited
paths, byte bounds, trust and jurisdiction. Provider capabilities and prices
are dated observations, rechecked before anything is ordered.
