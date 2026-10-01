# Signing roles and offline release boundary

How DiamaneOS signs builds and qualifies signing with disposable keys; this
authorizes no production key ceremony, token import, boot-key enrollment or
release. Terms: see the [threat model](THREAT_MODEL.md#terms).

## Role contract and trust split

Signing is a typed release transformation, not "sign this path".
[`config/signing-roles.json`](../config/signing-roles.json), the public role
contract, is bound to the selected GrapheneOS manifest and its exact release,
delta, metadata and key-generation scripts; changing any bound revision,
script, algorithm, target-files inventory or approved presigned package
invalidates the qualification result.

The online builder produces unsigned target-files and an otatools package and
never holds production private keys. A separate offline signer turns approved
target-files into: target-files with the reviewed APK and APEX certificate
mapping; APEX payloads and AVB metadata signed by the declared AVB role; a full
OTA and, given an approved old target-files input, an incremental OTA;
release/channel metadata derived from the internally signed OTA; and an
externally signed factory archive and append-only release record. It returns
only signed outputs, public certificates, hashes and the verification record.
Online packaging may build the outer factory container only from approved
signed target-files, without a release private key. Production storage and
token assignments wait until the real offline equipment proves every intended
provider/algorithm/tool combination ("the token supports RSA" is no end-to-end
Android signing result); a role that cannot use its provider gets a reviewed
offline software-held alternative or stays a blocker.

## Pinned roles

The selected `2026091000` manifest resolves these signing authorities:

```text
script                639cdf6558e7401f8bab1cb8f53546ff4a0c8fef
build/make            7f0398241bc8c4ef5255a8063befa5045e5cfab4
development           bf1857fd7d886218a1ea456b94eb7eb15d5c1338
external/avb          ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee
prebuilts/jdk/jdk21   ef5bcc92586b839ae3dbacc154127092fa4002ec
system/update_engine  79f478a4f89e701e85fa15dec340bf445342abbd
tools/apksig          ba4d984e1a360d427307d669d2f789212130e9e8
```

`make_key` creates RSA-4096 keys and SHA-256 X.509 certificates. The GrapheneOS
script defines nine Android certificate roles: `releasekey`, `platform`,
`shared`, `media`, `networkstack`, `bluetooth`, `sdk_sandbox`, `gmscompat_lib`
and `nfc`. The AVB and APEX-payload role uses `SHA256_RSA4096`, the outer
factory archive an Ed25519 OpenSSH signature in the `factory images` namespace,
OTA package and payload signing the release key; channel files derive from
verified OTA metadata with no key of their own. References: the
[GrapheneOS build guide](https://grapheneos.org/build), the pinned
`script/generate-release.sh` and `script/generate-delta.sh`, and AOSP
`sign_target_files_apks`; file hashes in the contract stop a moving branch
replacing the selected release.

## Target-files inventory

The inventory comes from `META/apkcerts.txt` (APK certificates),
`META/apexkeys.txt` (APEX container certificates and payload keys) and
`META/misc_info.txt` (emitted AVB key/algorithm chains). The verifier rejects
duplicate ZIP names, path traversal, missing or oversized metadata, duplicate
package records, unknown signed-output roles and any presigned package not
named exactly by the target profile (no globs). A qualified profile also binds
the exact unsigned target-files hash; each present presigned package's literal
member path, basename, byte count and SHA-256; and the absence of basenames for
build/test metadata with no member. Metadata tokens stay apart from archive
basenames (no path inferred), and public development keys in an unsigned input
are not approved for the signed output.

```sh
bin/diamaneos signing roles
```

checks the static contract without output, keys or a device.

```sh
bin/diamaneos signing inventory \
  --profile generic-x86_64-qualification \
  --stage unsigned \
  --target-files "$TARGET_FILES" \
  --output "$OUTPUT_JSON"
```

writes a new, non-overwriting inventory. The signer keeps the source labels in
`META/apkcerts.txt` and `META/apexkeys.txt` (they select inputs, they are no
destination-key receipt), so `--stage signed` also needs the accepted unsigned
inventory behind the signing plan:

```sh
bin/diamaneos signing inventory \
  --profile generic-x86_64-qualification \
  --stage signed \
  --source-inventory "$UNSIGNED_INVENTORY_JSON" \
  --target-files "$SIGNED_TARGET_FILES" \
  --output "$SIGNED_INVENTORY_JSON"
```

It requires identical package/APEX metadata, records each package's
destination role and requires transformed AVB metadata; the dummy
qualification then verifies representative APK/APEX certificates, APEX/AVB
payload keys, OTA signatures and wrong-key rejection from the artifacts, never
from the labels.

APK role coverage spans the union of the accepted SDK and Cuttlefish signed
archives. Their `bluetooth`, `nfc` and `sdk_sandbox` records have no APK
payload, so those roles are metadata-only and each signs a standalone probe
(the smallest deterministic real APK from that union) with its fresh key,
proving key, certificate and pinned `apksigner` path without claiming a
transformation. Every other role needs a real transformed APK; another
missing-role set fails closed pending review, and the FP6 product must
regenerate its own coverage. Roles, profile pair and metadata-only exceptions
come only from `config/signing-roles.json`; an upstream role missing there is
an inventory error, never auto-enrolled.

A package in a discovery report is not allowlisted: review why it stays
presigned, bind its archive identity or reviewed metadata-only absence and
update the exact profile first. On the accepted builder,
`deploy/builder/run-signing-discovery` does this as the unprivileged build
identity without network, building only `target-files-package` and
`otatools-package` with no key or signing; the default service uses the
generic SDK profile and `diamaneos-builder-ota-signing-discovery.service` a
fixed Cuttlefish Virtual A/B profile, rejecting any other. The immutable result
is `NEEDS_REVIEW` when the only discrepancy is an exact unlisted `PRESIGNED`
package; other inventory errors fail. Presigned entries are never
auto-approved.

The generic SDK x86_64 profile qualifies APK, APEX and AVB transformations
(with no A/B partition inventory or recovery image it cannot generate OTAs);
the Cuttlefish x86_64 phone profile, a real Virtual A/B target, qualifies the
full and incremental OTA path, so a relabelled SDK archive cannot pose as OTA
evidence. Neither is FP6 compatibility, release, update-semantics or hardware
evidence; the FP6 profile must be regenerated and reviewed from the actual FP6
`user` target-files. The SDK target publishes target-files and otatools via
legacy product output paths, Android 17 Cuttlefish via Soong module
intermediates; discovery accepts one regular artifact per exact module root,
never searches the tree or falls back to an older package, and resolves Soong
paths from the declared top-level output root, not `$OUT` (the product
directory after `lunch`).

## Disposable-key qualification

A run uses fresh disposable keys in its own private directory and keeps: the
exact unsigned and signed target-files hashes and inventories; every role's
public fingerprint; representative verification of APK, APEX container, APEX
payload and every AVB chain; full and incremental OTAs with ZIP and payload
verification; factory-archive and release-record signatures; a wrong-key
rejection per verifier class; and interruption/restart recovery proving an
incomplete run cannot be promoted.

`deploy/builder/run-dummy-signing-qualification` takes no arguments and no
operator-selected artifact or key path. APK/APEX/AVB proofs bind the accepted
SDK target-files and OTA proofs the Virtual A/B target-files; it requires the
reviewed tools checkout, source-project revisions, source-file hashes, both
unsigned target-files hashes and the otatools hash, maps every package to a role
explicitly and avoids the global APK/APEX key overrides. The Cuttlefish
inventory has custom chained-vbmeta images `vbmeta_system_dlkm` and
`vbmeta_vendor_dlkm` that releasetools cannot override, so the runner requires
the exact reviewed `system_dlkm` and `vendor_dlkm` custom-vbmeta set and makes
a transient copy replacing only those two key-path and algorithm pairs with the
disposable AVB key and `SHA256_RSA4096` (not adding them to the
custom-data-image list). All other ZIP members stay unchanged, both archive
hashes and the four changed field names are recorded, the transient input is
removed before evidence promotion, a missing, duplicate or extra field fails
closed, and the signed images must still pass AVB verification.

Private material lives under `/dev/shm`, is removed before independent
verification and never enters evidence. `make_key`'s cleanup trap can return 1
after success, so the runner accepts 0 or 1 from that exact helper, then
requires non-empty, non-symlink certificate and PKCS#8 outputs that OpenSSL
parses, recording status and results in `key-generation.json`.

The incremental proof deliberately uses the same Cuttlefish archive as old and
new (a no-op delta): it exercises generation and package/payload signing, not
changed-build update semantics, which belong to an FP6 old/new release-pair
qualification. The generic image archive proves the Ed25519 `factory images`
role, not an FP6 factory package's structure or installability; FP6 packaging
must be requalified on real output.

The release manifest hashes every artifact and is signed in the
`diamaneos-dummy-release-record` namespace. Verification re-hashes, accepts the
declared public key and requires failure against a different one:

```sh
bin/diamaneos signing verify \
  --result "$RUN_ROOT/result.json" \
  --artifact-root "$RUN_ROOT"
```

It refuses absolute or traversing paths, symlinks, missing files, duplicate
proof/artifact identifiers, altered hashes, incomplete proofs,
production-material claims and a mismatched source binding. A PASS covers only
that disposable run.

## Offline signer qualification

The signer is prepared while no production secret exists. Before production
custody, repeat the full disposable flow on the offline host and prove: no
active or unexpected network interface or required download; tools and input
media match reviewed hashes; inbound and outbound media have distinct, enforced
purposes; unrecognized media stops the procedure; the real token model,
firmware, quantity, origin, algorithm, slot and touch behaviour are
inventoried; every token-backed operation works through the real Android tool
adapter, including cancellation and restart; and unsupported operations stay
visible blockers or use a reviewed offline software-held role. PINs, recovery
material, device identifiers, custody locations and raw offline logs never
enter this repository; public records hold only role, algorithm, public
fingerprint, tool binding and sanitized result.

## Change and recovery rules

Key reuse and rotation are per role: APK shared-user/privileged permissions,
APEX, OTA and AVB trust cannot share one rotation statement. The AVB root is an
especially durable trust anchor whose replacement may need a bootloader unlock
and data loss. No production key is generated before the complete recoverable
key set, wrapping/backup procedure, replacement-token path and restoration
rehearsal have their own approved ceremony. Keep the unsigned input, signed
target-files, full/incremental OTAs, public identities and verification record
needed to reproduce a transformation; never keep a private key in build logs,
result JSON or public evidence.

## Diagnose retained outputs before another signing run

APK certificate pinning accepts legacy `Signer #1 certificate` and
`V3.0 Signer: certificate` output, but verification must exit zero, declare one
signer and give exactly the expected certificate SHA-256 (public-key
fingerprints, missing output or extra certificates fail). The full
qualification first signs and verifies one small APK with the same JDK and
verifier, catching tool, runtime and output-format failures early.

If a failed run kept its signed outputs and public keys, replay only
independent verification, as the build identity, from a clean reviewed
checkout into a new directory on the same filesystem:

```sh
export DIAMANEOS_EXPECTED_TOOLS_COMMIT=REVIEWED_40_HEX_COMMIT
"$TOOLS_ROOT/deploy/builder/recheck-dummy-signing" \
  --workspace "$WORK_ROOT" \
  --run "$WORK_ROOT/evidence/dummy-signing/RETAINED_RUN.failed" \
  --output "$WORK_ROOT/evidence/dummy-signing/NEW_DIAGNOSTIC_DIRECTORY"
```

It takes the workspace lock, checks kept artifact hashes and certificate
fingerprints, hardlinks large signed outputs (immutable) into a fresh
diagnostic tree for new logs and samples, and copies only named public inputs,
never old results, logs or secrets; the failure record stays unchanged. It uses
the qualified otatools, pinned JDK and the full run's APK/APEX, AVB, OTA and
OpenSSH verification but creates no keys, signs nothing and repeats no
transformation or OTA generation (hashing and extraction still take time and
disk). Every diagnostic report says `qualification_accepted: false`. Replay
cannot replace failed signing, cleanup, a missing output or any incomplete
proof, which need a new complete qualification; there is no resumable signing
state or persistent disposable secret. If a complete run failed only because
its final result schema rejected the runner's own metadata, a separate reviewed
acceptance record may bind the unchanged result hash, execution revision,
corrected validator revision and all proof checks; keep the failure record,
and never treat a passing diagnostic as that record.

AVB verification uses the images each archive emits. A `no_boot=true` product
may keep boot key metadata without `IMAGES/boot.img`; that role is
metadata-only and must be exercised by an emitted boot image in the other
profile, and any other missing declared image fails. `avb-image-coverage.json`
records this apart from the signing inventories. Sibling images are extracted
together so hash and hashtree descriptors can be checked; chain descriptors
must match the declared rollback index location and the kept disposable AVB
public key, and each signed image gets its own valid-key and wrong-key check.
