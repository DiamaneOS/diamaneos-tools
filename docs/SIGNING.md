# Signing and publication verification

A signing plan binds certificate, APEX payload, AVB, OTA and factory roles to a build
that passed `build verify`. Native platform tools verify signatures against an
independently supplied public key inventory. Artifacts never select their expected authority.

## Policy and trust inputs

[`config/signing-roles.json`](../config/signing-roles.json) defines algorithms,
certificate selectors and presigned-package identities. Revisions and tool hashes belong
to each run. Unknown roles and presigned exceptions are rejected.

| Role | Required authority |
| --- | --- |
| Android certificates | RSA-4096, SHA-256 X.509, `/CN=DiamaneOS/` |
| APEX payloads and AVB | `SHA256_RSA4096` |
| OTA package, payload and metadata | `releasekey` |
| Factory archive | Ed25519 SSH, `factory images` namespace |
| Release record | External SSH authority, `diamaneos-release-record` namespace |
| Build verification report | External SSH authority, `diamaneos-build-verification` namespace |
| Previous signing result | External SSH authority, `diamaneos-signing-result` namespace |
| Attestation release list | External ECDSA P-256 signer, SHA-256 |

The public inventory uses schema version 2. `certificate_roles` maps every role to
an external certificate, `avb_public_key` names its RSA public PEM, and `factory` contains
`allowed_signers` and an exact SSH identity. File names are relative to the inventory.
Private key material is rejected.

The optional `presigned_apex_payload_keys` object maps each reviewed presigned
APEX metadata name to an independently supplied AVB **binary** public-key file.
For the FP6's stock Widevine package, for example:

```json
"presigned_apex_payload_keys": {
  "com.google.android.widevine.nonupdatable.apex": "widevine.avbpubkey"
}
```

The external file must match the policy's SHA-256 and remain outside publication.
Missing, unknown, changed or publication-controlled authorities refuse. Expected
authority is never enrolled from a submitted APEX.

`presigned_apexes` binds the exact partition/path, complete APEX hash, container
signer and payload key hash. It is not a wildcard exemption or release provenance
record. Release-script overrides cannot reassign these APEXes.

Platform `avbtool verify_image` verifies payload signatures and descriptors.
Its PEM `--key` interface cannot directly consume AVB binaries. The recorded
`external/avb/avbtool.py` API obtains embedded key bytes for exact comparison to
the independent external binary.

Header/footer decoding stays in the platform parser. No signature crypto or AVB
parser is implemented here. The parser's Git blob and consumed bytes are bound
in `source_files`. Unsupported native APIs refuse.

Prepare verifies container and payload before accepting preservation.
Publication verification repeats both checks and requires unchanged complete
APEX bytes.

`--source-root` must match the actual clean revisions in the resolved manifest.
Updating its tools checkout makes an older packet's source check fail. Qualify a
new archive with truthful packaging/build metadata and fresh authenticated
verification on the updated source basis.

Never edit an old manifest, report or source identity to make comparison pass.
New policy and public inventory hashes require a fresh signing plan. An earlier
plan cannot be reused.

## Prepare and sign

`prepare` requires the build record, complete PASS report, report signature, checksums,
resolved manifest, target-files and otatools. Production mode requires a clean, official
`user` build. Every applicable device rule must pass against the clean tools revision
selected by the resolved manifest.

Installed host OpenSSH authenticates the verification report and previous result
before incoming tools are unpacked or executed. Its environment has no archive
library, executable or JDK paths.

Authentication and plan preparation consume
the same bounded private metadata copies. A private otatools snapshot must match
that authenticated provenance before native verification begins.

The private copies include the policy, build record, checksums, resolved manifest,
report and signatures, previous result, and external public inventory/material.
Original filenames and relative public-key references are preserved. Original
path changes refuse publication. Copying does not make publication-controlled
keys an external authority.

```sh
bin/diamaneos signing roles
bin/diamaneos signing prepare \
  --build-record "$IMAGES/build.json" \
  --verification-report "$VERIFY_REPORT" \
  --verification-signature "$VERIFY_REPORT_SIGNATURE" \
  --target-files "$IMAGES/target-files.zip" \
  --resolved-manifest "$IMAGES/resolved-manifest.xml" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" --output "$PLAN"
```

The plan binds archive hashes, selected source-project revisions, consumed Git blobs,
JDK bytes and the native signer. Changed source, tools, reports or build identity
are rejected. Git index flags and ignored runtime files cannot bypass source admission.

Super-bearing plans bind `super_layout` before signing. Factory slots come from the admitted
install record. Legacy qualification uses the platform A/B bootstrap or non-A/B naming contract;
older plans without this binding require preparation again. Extraction output never chooses the expected layout.

Use `--previous-result` with `--previous-signature` for subsequent runs. The externally
verified result must contain successful artifact verification in a compatible mode.
Each plan reports changed signing projects. The first includes every signing project.

Release-script overrides supply package roles. Archive contents determine absent metadata.
Presigned bytes remain identical and verify against exact policy identities. Enclosed APKs
receive complete role checks. SELinux certificate replacement follows the native default
key mapping and preserves independently approved certificates.

```sh
bin/diamaneos signing command --plan "$PLAN" \
  --target-files "$IMAGES/target-files.zip" --signed-target-files "$SIGNED_TARGET_FILES" \
  --signer "$OTATOOLS/bin/sign_target_files_apks" --key-dir "$KEY_DIR" --source-root "$SOURCE"
```

The command runs from the verified source root to resolve original certificates. It
requires the build-bound signer and rejects altered plans, changed source or target-files,
input replacement and existing outputs.

## Assemble factory images

`signing factory` uses only public trust material. It authenticates the build report before
loading otatools, verifies signed packages and partitions, rebuilds super from signed target-files,
and copies the admitted firmware, reset and wipe images. Existing outputs are never replaced.

```sh
bin/diamaneos signing factory --plan "$PLAN" \
  --build-record "$IMAGES/build.json" \
  --verification-report "$VERIFY_REPORT" \
  --verification-signature "$VERIFY_REPORT_SIGNATURE" \
  --target-files "$IMAGES/target-files.zip" \
  --resolved-manifest "$IMAGES/resolved-manifest.xml" \
  --signed-target-files "$SIGNED_TARGET_FILES" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" \
  --images-archive "$PUBLICATION/images.zip" \
  --output "$PUBLICATION/FP6-factory.zip"
```

The ZIP has one directory named from the product and build number. It contains every signed
partition, direct logical images and super, immutable passthrough images, `build.json`,
`SHA256SUMS`, `resolved-manifest.xml`, `flash-all.sh`, `flash-all.bat` and `avb_pkmd.bin`.

The original build record and report remain unchanged. The derived record binds their identities,
the signing plan, signed target-files and actual image hashes. Its public AVB key must exactly
match the external authority and the embedded vbmeta key.

Checksums cover every contained file except the checksum list itself. Scripts are regenerated
from the plan-bound source generator and derived record. Installs use fastbootd for logical
partitions, cancel a pending snapshot first, and stop in the bootloader after installing a production AVB key.

Before entering fastbootd, scripts select the admitted slot and require a successful
`current-slot` check. Selection or verification failure stops the install. The manual sequence
requires the same check before continuing.

Before starting Android, check unlock ability and lock as the generated instructions describe.
The scripts never lock critical partitions. Archive verification does not establish device
installation or relock acceptance.

The batch script requires PowerShell to verify every listed SHA-256 before writing images.

The ZIP contains no final result or its own archive hash. The external release record and final
independent result bind the completed ZIP. Full and incremental OTA generation, factory signing
and release-record signing require the offline signing environment.

## Verify publication

Keep the inventory, plan, result and scratch outside the dedicated publication directory.
The publication description has `schema_version`, `signed_target_files`, `images_archive`,
`ota`, `factory_archive`, `factory_signature`, `release_record` and `release_signature`.
Paths are relative. Unknown files, duplicates and links are rejected.

Production uses publication `schema_version: 2` and the full factory ZIP. The
[publication schema](../schemas/signing-publication.schema.json) defines its fields.
Version 1 retains the single-image-archive tar format only with `--qualification`.
Both formats require the external factory and release signatures.

For example, keep this description outside the publication directory:

```json
{
  "schema_version": 2,
  "signed_target_files": "target-files.zip",
  "images_archive": "images.zip",
  "ota": [{"file": "ota.zip", "source_target_files": null}],
  "factory_archive": "FP6-factory.zip",
  "factory_signature": "FP6-factory.zip.sig",
  "release_record": "release.json",
  "release_signature": "release.json.sig"
}
```

Each `ota` entry has `file` and `source_target_files`, and a full OTA uses `null`.
Delta sources must match previous verified artifact hashes. A full OTA is mandatory.

The release record contains `schema_version`, `plan_sha256` and the exact `artifacts`
hash map for target-files, images, OTAs, factory archive and factory signature.

Installed host OpenSSH verifies the release record and its plan binding before
archive tools run. Plan, publication, record, signature and authority reads use
their frozen copies throughout verification.

The authenticated plan binds the otatools snapshot. Expected
public authority stays outside the publication. Native artifact checks still run
after bootstrap authentication.

```sh
bin/diamaneos signing verify --plan "$PLAN" \
  --publication "$PUBLICATION_JSON" --artifact-root "$PUBLICATION" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" --output "$RESULT"
```

- Native verification checks every APK, both compressed APEX containers, their payloads and enclosed APKs against assigned external identities.
- Package IDs, SELinux certificate selectors, package isolation and installed policy bytes must match the accepted plan and target-files.
- Published partitions match signed target-files, including unpacked logical images. Direct logical images must match their `super` contents.
- Super extraction must contain every expected active logical image. Only known empty inactive counterparts are allowed. Slots come from the admitted install record; qualification layouts explicitly bind slot suffixes or unsuffixed platform output.
- Flat image membership includes every declared firmware, wipe and modem-reset file. Unknown members and partial logical sets are rejected.
- Native AVB verification covers every declared partition and rollback chain. Disabled verification and algorithm changes are rejected.
- The platform OTA checker verifies whole-file, payload and metadata signatures. Build metadata and payload properties bind the accepted build and final bytes.
- Native payload application reproduces signed target-files partitions, using the verified source for deltas.
- Incremental pre-build metadata must exactly match the authenticated signed base's platform BuildInfo identity. The base archive hash is rechecked before verification returns.
- Factory and release signatures use the external authority and distinct namespaces. Factory ZIP membership, metadata, scripts, public key and every image must match the admitted plan and verified images.
- Closing checks bind source, public material and publication bytes throughout verification. A changed input cannot produce a successful result.

Success records `artifact_signatures_verified: true`, public identities, artifact hashes
and run provenance. Schema version 2 results also record the attestation release values.
Production rejects AOSP public test keys even when supplied as expected identities.
Qualification requires the explicit `--qualification` flag.

## Attestation release list

A verifier without a manufacturer-rooted attestation chain for a first pairing accepts a phone
only if its attested values exactly match a published release. The release list carries these
values per device.

- `signing verify` reads the values from the verified vbmeta images of a schema version 2
  publication and records them in its result as `attestation_release`.
- `verifiedBootHash` is the SHA-256 vbmeta digest over vbmeta and its chained partitions
  (`avbtool calculate_vbmeta_digest`). `verifiedBootKey` is the SHA-256 of `avb_pkmd.bin`.
- The device file (`config/fp6-attestation.json` for the FP6) names the AVB property behind the
  OS version and each patch level.
  - Each named property must appear exactly once among the vbmeta images the boot hash covers.
  - The file also holds the KeyMint and attestation versions that the secure area firmware
    reports.

**Format.**

- The list is a JSON envelope with `format`, `version`, a base64 `payload` and `signatures`.
- Each signature is a DER ECDSA P-256 signature with SHA-256 over the exact payload bytes. Its
  `keyId` is the SHA-256 of the signer's SubjectPublicKeyInfo DER.
- The payload holds `sequence`, the UTC `issued` time and, per device, its `id`,
  `verifiedBootKey` and `releases`. Hashes are upper-case hex.

**Prepare and sign.**

- `prepare` verifies the previous list against the signer key, keeps its releases, appends the new
  one and raises the sequence by one. `--withdraw` removes a listed build.
- A listed build or boot hash, or another boot key for a listed device, is refused.
- Production lists take only a production signing result with its external signature in the
  `diamaneos-signing-result` namespace.
- `--signer detached` writes the payload and its SHA-256 for the offline signer. `attach` adds
  the returned DER signature and writes the list only after checking the signature, key ID,
  format and sequence.
- `--signer test` signs with a local P-256 test key. It needs `--test`, which takes only
  qualification results and `entry` output.
- `entry` reads the values from an image directory, after `avbtool verify_image` checks the chain
  against the given AVB public key. It serves test lists.

```sh
bin/diamaneos attestation-releases prepare --previous "$LISTS/releases.json" \
  --signing-result "$RESULT" --signing-result-signature "$RESULT.sig" --keys "$TRUST/inventory.json" \
  --signer detached --public-key "$TRUST/release-list.pub.pem" --output "$LISTS/payload.json"
bin/diamaneos attestation-releases attach --previous "$LISTS/releases.json" \
  --payload "$LISTS/payload.json" --signature "$LISTS/payload.json.sig" \
  --public-key "$TRUST/release-list.pub.pem" --output "$LISTS/releases-next.json"
bin/diamaneos attestation-releases verify --list "$LISTS/releases-next.json" \
  --public-key "$TRUST/release-list.pub.pem" --previous "$LISTS/releases.json"
```

A test list from images signed with a test AVB key:

```sh
bin/diamaneos attestation-releases entry --images "$IMAGES" --build "$BUILD_NUMBER" \
  --avb-public-key "$TEST_KEYS/avb.pub.pem" --avbtool "$OTATOOLS/bin/avbtool" --output "$SCRATCH/entry.json"
bin/diamaneos attestation-releases prepare --first --entry "$SCRATCH/entry.json" --signer test --test \
  --test-key "$TEST_KEYS/release-list.pem" --output "$SCRATCH/releases.json"
```

## Fixtures

OTA reconstruction mismatches remain fatal. Their bounded diagnostic reports
partition names, input format, full raw-image sizes/hashes, first differing
offset, matched prefix and sampled range hashes. It contains no image bytes or
file paths.

Raw targets follow update_engine's 4096-byte `kBlockSize` contract in
`payload_generator/delta_diff_generator.h`. `generate_delta_main.cc::RoundUpPartitions`
extends target working images before loading their sizes.
`scripts/brillo_update_payload` explicitly specifies zero padding for targets.

Verification compares the entire applied image with the original bytes plus
exactly the zero-filled block remainder. It requires full canonical SHA-256
equality, exact rounded length and a zero tail. Diagnostics retain original and
canonical sizes/hashes.

Already aligned images admit no extra bytes. Native sparse normalization must
remain aligned and match exactly. Truncation, nonzero tails, content changes and
unused-block differences are refused. Signature, source attribution, public trust
and production acceptance requirements remain unchanged.

Fixtures create disposable material in temporary storage. Native signature cases require
explicit source and otatools inputs. Linux or `DIAMANEOS_SIGNING_REQUIRE_NATIVE=1` refuses
missing inputs. They exercise corrupt, unsigned, wrong-key, wrong-role and test-key artifacts.

```sh
DIAMANEOS_SIGNING_NATIVE_SOURCE="$SOURCE" \
DIAMANEOS_SIGNING_OTATOOLS="$IMAGES/otatools.zip" \
PYTHONPATH=src "$VENV/bin/python" -m unittest tests.signing.test_native_signatures
```

`apksigner`, `avbtool`, `check_ota_package_signature`, `delta_generator` and payload tooling
perform cryptographic verification. Policy code compares native results with external trust.
It contains no signature parser. Controlled orchestration fixtures do not establish
cryptographic or device acceptance.
