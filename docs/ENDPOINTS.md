# Endpoint contracts

How to read and maintain the contracts for the network services DiamaneOS
inherits from GrapheneOS. For anyone implementing, hosting or reviewing one of
these services. The contracts are design inputs for implementation, not
deployed configuration.

## Files

| File | Role |
| --- | --- |
| `config/endpoints.json` | Source of truth for the 16 inherited endpoint contracts. |
| `schemas/endpoint-contract.schema.json` | Defines its format. |
| `config/services.json` (infrastructure repository) | Assigns every contract that DiamaneOS hosts to an authority role. |
| `schemas/services.schema.json` | Validates that selection. |

The geocoder contract is the one exception: DiamaneOS hosts no geocoder, so it
has no service entry (see below). The Info release feed was retired on
2026-09-26, when the owner removed GrapheneOS's Info app from every build.

Validate with the setup and explicit integration command in
[TESTING.md](TESTING.md#endpoint-contracts). A valid result means both documents
satisfy their schemas and reference/authority checks. It does not mean that a
server, native client, account, signature ceremony or physical device has
passed testing. Every service stays `blocked-pending-implementation` until its
implementation acceptance evidence exists.

The `owner_task` and `verify_at` fields carry maintainer tracking metadata and
stay required wherever their schemas specify them. Their values stay stable for
tooling compatibility; the neighbouring purpose, contract and blocker text
defines the work for public readers. Those IDs do not change a service's
authority or acceptance state.

## Reading the evidence

Each endpoint has `source_refs` into the `sources` array. An entry records the
repository, full commit, actual file path, symbol locators and SHA-256 of the
retrieved file; open `repository/blob/revision/path` to inspect it. The
observations were retrieved over HTTPS on 2026-09-11, and on 2026-09-26 for the
network-location, geocoder and attestation contracts and for Vanadium's
(GrapheneOS's browser's) own connectivity checks. File hashes make this review
reproducible but do not authenticate a future shipping release.

| Source kind | Meaning |
| --- | --- |
| `manifest-pin` | The revision agrees with the reviewed source manifest. |
| `research-pin` | A separately resolved AppStore or Vanadium source revision. It is **not** proof that a particular prebuilt APK was built from that revision. |
| `server-reference` | Upstream routing and upstream destinations, not a contract we blindly copy. |

The relevant app or device implementation must bind the final binary and
recheck these observations when the pin changes. The frameworks tree API was
truncated, so the relevant services files were fetched directly; this is not a
claim to have searched every upstream file.

## Protocol boundaries

The pinned sources establish these boundaries:

- **`update.vanadium.app` and `dl.vanadium.app`** serve Chromium components.
  Browser APK delivery belongs to the AppStore catalog. Version 2 renames their
  IDs to `browser-component-check` and `browser-component-download`; service
  consumers must migrate together.
- **`grapheneos.online`** supplies the fallback HTTP connectivity URLs. It is
  not an unknown attestation endpoint.
- **`dnscheck.grapheneos.org`** is a DNS probe suffix. It needs authoritative
  wildcard records and a chosen resolver, not an HTTP service on the release
  VPS.
- **`gstatic.grapheneos.org`** serves Android platform CT (Certificate
  Transparency) lists. Its pinned verifier allowlists the public key
  independently and verifies each list's signature; the downloaded public key
  alone is not a trust root.
- **HTTPS time** uses an integer `X-Time` in Unix **milliseconds**. The Android
  client does not send a nonce or implement a six-source quorum. Its bootstrap
  hostname lookup explicitly bypasses Private DNS; do not claim the configured
  Swiss DoT (DNS over TLS) default covers that path. The project time server
  owns authenticated upstream sampling and agreement.
- **`gs-loc.apple.grapheneos.org`** is GrapheneOS's relay for Apple's Wi-Fi and
  cell positioning, used only when network location is on.
  - The request lists nearby access points and cells, so a relay sees
    approximate location; it must keep no cache or per-request logs.
  - As in GrapheneOS, DiamaneOS will offer three opt-in choices, with network
    location off by default (owner decision 2026-09-26): its own EU relay,
    which hides the device IP from Apple; Apple's service directly; and Apple's
    service for China directly. `client_defaults` discloses the direct choices.
    The setup wizard's location switch must not turn any of them on silently.
  - The network position seeds the GNSS (satellite positioning) engine. In an
    indoor owner test on the FP6 the same day, GPS alone gave no fix within
    several minutes, while with Apple directly the first network fix arrived
    about 35 s after the app's request and GPS locked about 1 s later.
- **`nominatim.grapheneos.org`** answers Android Geocoder queries (place names
  and coordinates) when geocoding is on.
  - DiamaneOS hosts no geocoder for now (owner decision 2026-09-26): geocoding
    is off by default, and users may opt in to OpenStreetMap's public Nominatim
    directly, which then sees the device IP and the searched text or
    coordinates.
  - `client_defaults` discloses this as a non-EU direct exception, like the
    Swiss Private DNS default. The validator requires that entry and rejects a
    relay or a service entry for the geocoder. `geocoder.diamaneos.de` is only
    reserved in case self-hosting is revisited.
  - The client's User-Agent names GrapheneOS, so the NetworkLocation fork must
    send a DiamaneOS one, and use must stay within OpenStreetMap's
    [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/):
    identify the app, keep a low request rate and show attribution.
  - `GEOCODER-HOSTING` records what remains, including asking the OpenStreetMap
    Foundation before the option becomes anything more than an opt-in.
- **`attestation.app`** serves Auditor's (the hardware attestation app's)
  opt-in remote verification and sample submission. It is the only stateful
  contract (paired accounts and history), and it needs DiamaneOS key pins and
  FP6 support that GrapheneOS's server does not have.
- **`connectivitycheck.grapheneos.network`** is also probed by Vanadium itself,
  and its DNS-over-HTTPS probe uses that name even when the OS setting turns
  checks off. Only a rebuilt browser can repoint it (FP6-111).
- **RKP, Widevine and SUPL** (remote key provisioning, DRM and assisted GPS):
  TLS proxies can see their outer protocol. Some fields may be encrypted, but
  calling the whole payload opaque would overstate privacy. The contract names
  each actual upstream and what it receives.

## Bounds and freshness

**Limits.** `limits_profiles` are selected **project staging policy**, in bytes
and seconds, not claims that upstream clients currently enforce those limits.
`timeout_retry` separately records observed client behaviour, and each
`implementation_blockers` entry names unbound native inputs. Do not replace a
native protocol with the schematic example, or silently enlarge a ceiling to
get an implementation accepted.

- HTTP/TCP deadlines are total monotonic deadlines, including streaming; idle
  and connect bounds are extra limits.
- Body limits apply to streamed bytes even without Content-Length. Count
  decompressed bytes separately where decompression happens; catalog-declared
  gzip and decoded APK sizes must both match and stay within their limits.
- Never buffer a whole maximum-size artifact in RAM. Reserve quota before
  writing a staging file, and remove partial files on failure, cancel or
  restart.
- The response ceiling for artifact profiles is an object ceiling. OTA selector
  metadata is separately capped at 4096 bytes and signed app catalog metadata
  at 8 MiB.

**Rates.** For project HTTP/TCP services a profile allows one attempt, zero
redirects, zero queued requests, 64 active operations, 120 requests/s globally,
and 60 requests/minute per source IP with a burst allowance of 20.

- Use a bounded in-memory rate table (4096 entries, idle expiry 60 s, reject new
  entries when full) and keep no per-IP history.
- Apply host-level aggregate caps as specified in the infrastructure runbook.
- These are initial measurable ceilings. NAT/mobile burst compatibility and
  upstream payload sizes must be qualified before service activation.
- DNS provider policy is external: the DNS profile's 512-byte/10-second values
  bound our synthetic health probe, not INWX's global DNS service or every
  native DNS response.

**Freshness.** `max_verified_age_seconds` measures the time since the last
successful check of the **authoritative current publication or upstream
state**: not since a local copy or cache hit, and not merely since the
release's signed build date.

- A valid unchanged upstream conditional response can refresh the check only
  against an already verified exact object/ETag. A local rehash cannot prove
  upstream freshness.
- Do not rewrite signed timestamps to make old data look new. Published
  releases may legitimately be older than the freshness window.
- Freshness of selection, trustworthiness of immutable bytes, and the client's
  own expiry/rollback checks are separate facts.

**When selection is stale**, use each endpoint's `failure_fallback`: no fresh
selector or live response is manufactured. Existing verified immutable artifact
URLs can stay available for recovery. A client may keep old state even after
the server starts returning errors; the inventory describes those cases instead
of claiming a native expiry check that does not exist.

- Pinned Conscrypt (Android's TLS library) marks a CT list non-compliant when
  its timestamp is in the future or its age is strictly greater than 70 days.
- `CertificateTransparency.checkCT` skips CT enforcement for a
  non-compliant/unusable store. This is a security degradation, not a guarantee
  that TLS connections stop. `CT-BIND` owns native verification of that
  behaviour on the shipped build.
- The project's 48-hour server refresh limit is a separate policy and cannot
  prevent client fail-open by itself.

## Examples and activation gates

**Examples.** `example.kind=wire-shape` freezes the reviewed transport shape,
including empty 204 bodies and time units; example timestamps, device tokens
and release IDs are illustrative. `schematic-not-conformance` explicitly has no
valid cryptographic/native acceptance fixture yet; do not treat it as one.
Signed catalog/OTA, CBOR, ASN.1, CRX, CT and proprietary device samples must
come from the pinned consumer/test producer under the listed implementation
owner; invented successful payloads would hide the missing evidence.

**Gates.** The twelve unique implementation gates are defined once by ID in the
endpoint rows; a shared gate may be referenced by more than one row, such as
`COMPONENT-BIND` and `NETLOC-SCOPE`. None can be cleared by changing `status`
alone. A gate closes with the actual pin/key/config, a valid native fixture, a
corrupt/truncated/oversized case, a measured timeout and a stale/outage result
in the implementing task. Source-only and fixture-only tests cannot clear
device eligibility or hardware behaviour.

**`COMPONENT-BIND`** is a real compatibility conflict: the observed Vanadium
patch hardcodes the component download host. Mirroring the unmodified APK and
its package repository does not prove that this host can be repointed. CT and
browser-component delivery and browser APK delivery must demonstrate a
supported configuration, or return the decision to the responsible maintainer.
Rebuilding/resigning, TLS interception, leaving inherited traffic or disabling
components is not an authorized automatic resolution.

## Maintenance

- Service monitoring uses the endpoint ID, host, owner, limits profile, refresh
  age and failure behaviour for health checks and aggregate alerts.
- Run schema and cross-repository validation on every inventory or service
  change.
- A changed consumer pin requires rechecking the cited paths, byte bounds,
  trust and jurisdiction before accepting the new contract.
- Provider capabilities and prices are dated observations; infrastructure
  staging rechecks them before the owner orders anything.
