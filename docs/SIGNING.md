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
| Android certificates | RSA-4096, SHA-256 X.509, `/CN=DiamaneOS/` for releases |
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

## Signing modes

`prepare`, `command`, `factory` and `verify` take the same mode. The plan records it, and each
later command must name the same one.

| Mode | Flag | Build | Keys | `build.json` of the factory ZIP |
| --- | --- | --- | --- | --- |
| Release | `--release` | Official `user` | The DiamaneOS release keys | `release` true, `release-keys`, relock |
| Own keys | none | Any verified build, also by hand or with local changes | Any but the AOSP test keys | `release` false, `own-keys`, relock |
| Qualification | `--qualification` | Any clean verified build the tools ran | Any | `release` false, `qualification-keys`, never locked |

[`config/release-keys.json`](../config/release-keys.json) pins the public half of each release key.
`--release` refuses, and names, every key that differs from its pin:

- the inventory's nine certificates, AVB key and factory signer, before the factory signer
  authenticates anything,
- the certificates of the App Store, Auditor and Camera in the signing policy,
- the certificates in the key directory `signing command` uses,
- the OTA certificates in `otacerts.zip` of system and recovery, after signing,
- the catalog key the App Store APK carries,
- the attestation release-list signer, in `attestation-releases prepare` of a production list and in
  `attach` and `verify` with `--release`.

A set made without `--release` is never a DiamaneOS release.

## Prepare and sign

`prepare` requires the build record, complete PASS report, report signature, checksums,
resolved manifest, target-files and otatools of a clean build. Release mode requires an official
`user` build. Every applicable device rule must pass against the clean tools revision selected by
the resolved manifest.

Own keys also sign a build with local changes or other source deviations. The plan keeps them.

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

Use `--previous-result` with `--previous-signature` for subsequent runs, one pair for
each earlier release an incremental comes from, at most 3, with the signatures in the same
order. Each externally verified result must show successful artifact verification in a
compatible mode, from an older build.

Artifact names must differ between releases.
Each plan reports changed signing projects against the newest previous result.
The first includes every signing project.

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

- Run the printed lines with the unpacked otatools' `bin` and the source's JDK on `PATH`, and with
  decrypted keys, as GrapheneOS's `generate-release.sh` does.
- `--ota FILE` adds a second line: the full OTA of the signed target-files, signed with
  `releasekey`. It uses the build-bound `ota_from_target_files` next to the signer.

## Assemble factory images

`signing factory` uses only public trust material. It authenticates the build report before
loading otatools, verifies signed packages and partitions, checks that the logical images fit in
super, and copies the admitted firmware, reset and wipe images. Existing outputs are never replaced.

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
partition, each logical image as its own file, immutable passthrough images, `build.json`,
`SHA256SUMS`, `resolved-manifest.xml`, `flash-all.sh`, `flash-all.bat` and `avb_pkmd.bin`.

It carries no `super.img`: the install writes `super_empty.img` and then each logical image in
fastbootd.

The original build record and report remain unchanged. The derived record binds their identities,
the signing plan, signed target-files and actual image hashes. Its public AVB key must exactly
match the external authority and the embedded vbmeta key.

`avb_rollback_indexes` in the derived record holds the rollback index that vbmeta and each chained
image carry. Each must be above 0.

Checksums cover every contained file except the checksum list itself. Scripts are regenerated
from the plan-bound source generator and derived record. Installs use fastbootd for logical
partitions, cancel a pending snapshot first, and stop in the bootloader after installing a production AVB key.

Before entering fastbootd, scripts write the empty misc image, select the admitted slot and
require a successful `current-slot` check. Selection or verification failure stops the install.
The manual sequence requires the same check before continuing.

An unlock leaves an erase request in misc. Recovery would run it instead of fastbootd, so misc is
written before fastbootd and again with the wipe images.

Before starting Android, lock as the scripts' closing text describes. The scripts run no lock
themselves. The order is the stock relock's:

- `fastboot flashing get_unlock_ability` must report 1.
- `fastboot flashing lock_critical`. It wipes and restarts: hold Volume down to come back to the
  bootloader.
- `fastboot flashing get_unlock_ability` must report 1 again.
- `fastboot flashing lock`.

Archive verification does not establish device installation or relock acceptance.

The batch script requires PowerShell to verify every listed SHA-256 before writing images.

The ZIP contains no final result or its own archive hash. The external release record and final
independent result bind the completed ZIP. Full and incremental OTA generation, factory signing
and release-record signing require the offline signing environment.

## Your own keys

Anyone who builds DiamaneOS can sign the build with their own keys and lock the phone with them.
The steps follow GrapheneOS's build guide. Run them from the source root, after `build all` or
`build package`.

Like GrapheneOS's, they sign a build by hand too. Its record names no tools for the Android build,
so the tools that recorded and checked it must be clean.

Generate the keys once and keep them. Another key set needs a new install, which erases the phone.

```sh
mkdir -p keys/FP6 && cd keys/FP6
CN=YourName
for role in releasekey platform shared media networkstack bluetooth sdk_sandbox gmscompat_lib nfc; do
  ../../development/tools/make_key "$role" "/CN=$CN/"
done
openssl genrsa 4096 | openssl pkcs8 -topk8 -scrypt -out avb.pem
../../external/avb/avbtool.py extract_public_key --key avb.pem --output avb_pkmd.bin
ssh-keygen -t ed25519 -f id_ed25519
cd ../..
```

- Give all keys the same passphrase. GrapheneOS's `script/decrypt-keys` expects that.
- `signing inventory` writes the public inventory from the certificates, `avb_pkmd.bin` and
  `id_ed25519.pub`. It reads no private key.
- It takes the payload key of each presigned APEX from the build's target-files, only when its hash
  matches the signing policy.

```sh
bin/diamaneos signing inventory --key-dir keys/FP6 --identity "$CN" \
  --target-files "$IMAGES/target-files.zip" --output "$TRUST"
ssh-keygen -Y sign -n diamaneos-build-verification -f keys/FP6/id_ed25519 "$VERIFY_REPORT"
unzip -q "$IMAGES/otatools.zip" -d "$OTATOOLS"
KEY_DIR=$(mktemp -d /dev/shm/keys.XXXXXXXXXX)
cp keys/FP6/* "$KEY_DIR" && script/decrypt-keys "$KEY_DIR"
```

Then run `prepare`, `command` with `--ota` and `factory` as above, without a mode flag, and with
`--keys "$TRUST/inventory.json"`. Sign the factory ZIP and remove the decrypted keys:

```sh
ssh-keygen -Y sign -n "factory images" -f keys/FP6/id_ed25519 "$PUBLICATION/FP6-factory.zip"
rm -rf "$KEY_DIR"
```

- The ZIP's `build.json` says `own-keys` and `release` false. `--release` refuses its plan.
- Its `flash-all.sh` installs your AVB key and stops in the bootloader for the lock.
- A build of changed source keeps its changes in the ZIP's `build.json`: `modified` lists the
  projects with local changes, `deviations` the other differences. `--release` and `--official`
  refuse such a build.
- A build without `--official` has no Updater. Install the full OTA of a newer build with
  `adb sideload` in recovery, as on GrapheneOS.
- An update server of your own is a source change, as on GrapheneOS:
  - `vendor/diamaneos/download-policy/client/de/diamaneos/downloads/DownloadPolicy.java` builds
    the update URLs in `url()`.
  - `vendor/diamaneos/overlay/DiamaneOSUpdaterOverlay/res/xml/network_security_config.xml` names the
    server domains and their TLS pins.
  - The `updater-server` and `updater-pins` image checks in `config/fp6-image-checks.json` hold
    exactly these hosts, domains and pins.
  - `vendor/diamaneos/product.mk` adds the Updater only when `DIAMANEOS_OFFICIAL_BUILD` is `true`.

## Check a signed image set

`images check` runs the checks of `build verify` on an image set outside the build, also one
re-signed with its own keys (`signing` `release-keys`, `own-keys`, `qualification-keys` or
`throwaway-test-keys` in `build.json`). It writes only the report and temporary files.

```sh
bin/diamaneos images check "$SIGNED_IMAGES" --workspace "$WORKSPACE" \
  --report "$SCRATCH/signed.verify.json" --scratch "$SCRATCH"
```

Factory images leave out the target-files and `otatools.zip`. Unpack the ZIP and give the signed
target-files and the build record it was signed from. Write the report next to the set, where
`flash-steps` reads it:

```sh
unzip -q "$PUBLICATION/FP6-factory.zip" -d "$SCRATCH/factory"
SIGNED_IMAGES=$(echo "$SCRATCH"/factory/*)
bin/diamaneos images check "$SIGNED_IMAGES" --workspace "$WORKSPACE" \
  --target-files "$SIGNED_TARGET_FILES" --build-record "$IMAGES/build.json" \
  --report "$SCRATCH/factory/BUILD_ID.verify.json" --scratch "$SCRATCH"
```

- The workspace gives the source and the vendor generation. Its kernel prebuilts checkout must be
  at the commit `build.json` names. The host tools come from the set's own `otatools.zip`.
- For factory images:
  - `BUILD_ID` is `build_id` in their `build.json`.
  - The target-files must have the hash `build.json` names, and the build record the hash in
    `signing_input`.
  - The host tools are the `otatools.zip` that record and its `SHA256SUMS` name.
  - The record also gives the build's fields that factory images leave out: kernel prebuilts,
    vendor generation and stock build.
- Every check of `build verify` runs. Four that assume the public test keys have signed
  counterparts with the same ids:
  - `record`: `avb_custom_key` naming `avb_pkmd.bin`, which `SHA256SUMS` lists and vbmeta embeds
    byte for byte, and `release` true only with release keys.
  - `record` also reads `never_lock`. An official set signed with qualification keys must never be
    locked (`true`), other official sets carry the relock record (`false`). Other sets may record
    either.
  - `test-keys`: `release-keys` in every build tag and fingerprint, `test-keys` in no property file
    and not in `META/misc_info.txt`.
  - `test-keys` also refuses an `otacerts.zip` or `mac_permissions.xml` certificate with the key of
    a public test certificate (`build/make/target/product/security`, `system/apex`).
  - `avb-chain`: `avbtool verify_image` of vbmeta and every chain with `avb_pkmd.bin`, chain
    descriptors with the same key, the FP6 layout and flags 0.
  - `avb-chain` also requires the rollback index of vbmeta and of each chained image to equal
    `avb_rollback_indexes` in `build.json`. On an official or release-keys set each must be above 0.
  - `validators`: `validate_target_files` and `check_target_files_vintf` from the same
    `otatools.zip`, with the set's public key for the signing keys the archive names.
- `avb_pkmd.bin` must be an RSA AVB key that `avbtool` reproduces exactly, and none of the AOSP AVB
  test keys (`external/avb/test/data/testkey_rsa*.pem`).
- A `release-keys` set must carry the pinned release keys: the AVB key in `record`, and the OTA
  certificates and App Store catalog key in `test-keys`.
- `flash-steps` reads the report as `<build_id>.verify.json` next to the set. It prints the relock
  only after `record` and `avb-chain` passed ([BUILD.md](BUILD.md#flashing)).

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
Delta sources must match previous verified artifact hashes, one incremental per source.
A full OTA is mandatory.

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
- Published partitions match signed target-files, including unpacked logical images. Where an image archive carries `super`, direct logical images must match its contents.
- An unpacked super must contain every expected active logical image. Only known empty inactive counterparts are allowed. Slots come from the admitted install record. Qualification layouts explicitly bind slot suffixes or unsuffixed platform output.
- Flat image membership includes every declared firmware, wipe and modem-reset file. Unknown members and partial logical sets are rejected.
- Native AVB verification covers every declared partition and rollback chain. Disabled verification and algorithm changes are rejected. vbmeta and every chained image need a rollback index above 0.
- The signer keeps the rollback indexes: `sign_target_files_apks` replaces only the AVB key and algorithm in `META/misc_info.txt` and rebuilds the images with the build's `--rollback_index` arguments.
- The platform OTA checker verifies whole-file, payload and metadata signatures. Build metadata and payload properties bind the accepted build and final bytes.
- Native payload application reproduces signed target-files partitions, using the verified source for deltas.
- Incremental pre-build metadata must exactly match the authenticated signed base's platform BuildInfo identity. The base archive hash is rechecked before verification returns.
- Factory and release signatures use the external authority and distinct namespaces. Factory ZIP membership, metadata, scripts, public key and every image must match the admitted plan and verified images.
- Closing checks bind source, public material and publication bytes throughout verification. A changed input cannot produce a successful result.

Success records `artifact_signatures_verified: true`, public identities, artifact hashes
and run provenance. Schema version 2 results also record the attestation release values.

Release and own-key modes reject AOSP public test keys even when supplied as expected identities.
Qualification requires the explicit `--qualification` flag, and a release `--release`.

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
- Production lists take only a release signing result with its signature in the
  `diamaneos-signing-result` namespace, by the pinned release factory key.
- A production list's signer must be the pinned release-list key. `attach` and `verify` check it with
  `--release`.
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
bin/diamaneos attestation-releases attach --release --previous "$LISTS/releases.json" \
  --payload "$LISTS/payload.json" --signature "$LISTS/payload.json.sig" \
  --public-key "$TRUST/release-list.pub.pem" --output "$LISTS/releases-next.json"
bin/diamaneos attestation-releases verify --release --list "$LISTS/releases-next.json" \
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
