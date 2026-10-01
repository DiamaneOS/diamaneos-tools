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

The online build host produces unsigned target-files and an otatools package and
never holds production private keys. Online packaging may build the outer
factory container only from approved signed target-files, without a release
private key.

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

A presigned package found in an inventory is not allowlisted: review why it
stays presigned, bind its archive identity or reviewed metadata-only absence
and update the exact profile first. Presigned entries are never auto-approved.

The generic SDK x86_64 profile qualifies APK, APEX and AVB transformations
(with no A/B partition inventory or recovery image it cannot generate OTAs);
the Cuttlefish x86_64 phone profile, a real Virtual A/B target, qualifies the
full and incremental OTA path, so a relabelled SDK archive cannot pose as OTA
evidence. Neither is FP6 compatibility, release, update-semantics or hardware
evidence; the FP6 profile must be regenerated and reviewed from the actual FP6
`user` target-files.

## Disposable-key qualification

A run uses fresh disposable keys in its own private directory and keeps: the
exact unsigned and signed target-files hashes and inventories; every role's
public fingerprint; representative verification of APK, APEX container, APEX
payload and every AVB chain; full and incremental OTAs with ZIP and payload
verification; factory-archive and release-record signatures; a wrong-key
rejection per verifier class; and interruption/restart recovery proving an
incomplete run cannot be promoted.

The incremental proof deliberately uses the same Cuttlefish archive as old and
new (a no-op delta): it exercises generation and package/payload signing, not
changed-build update semantics. The generic image archive proves the Ed25519
`factory images` role, not an FP6 factory package's structure or installability;
FP6 packaging must be requalified on real output.

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

## Change and recovery rules

Key reuse and rotation are per role: APK shared-user/privileged permissions,
APEX, OTA and AVB trust cannot share one rotation statement. The AVB root is an
especially durable trust anchor whose replacement may need a bootloader unlock
and data loss. No production key is generated before the complete recoverable
key set, wrapping/backup procedure, replacement-token path and restoration
rehearsal have their own approved ceremony. Keep the unsigned input, signed
target-files, full/incremental OTAs, public identities and verification record
needed to reproduce a transformation; never keep a private key in build logs,
result JSON or public evidence. PINs, recovery material, device identifiers,
custody locations and raw offline logs never enter this repository; public
records hold only role, algorithm, public fingerprint, tool binding and
sanitized result.

## APK certificate pinning

APK certificate pinning accepts legacy `Signer #1 certificate` and
`V3.0 Signer: certificate` output, but verification must exit zero, declare one
signer and give exactly the expected certificate SHA-256 (public-key
fingerprints, missing output or extra certificates fail).
