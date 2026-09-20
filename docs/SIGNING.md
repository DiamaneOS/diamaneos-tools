# Signing roles and offline release boundary

DiamaneOS treats Android signing as a typed release transformation, not as a
generic “sign this path” operation. The public role contract is
[`config/signing-roles.json`](../config/signing-roles.json). It is bound to the
selected GrapheneOS manifest and to the exact release, delta, metadata and key
generation scripts used by that manifest. Changing any bound revision, script,
algorithm, target-files inventory or approved presigned package invalidates the
qualification result.

This document describes disposable-key qualification. It does not authorize a
production key ceremony, token import, boot-key enrollment or release.

## Trust split

The online builder produces unsigned target-files and an otatools package. It
never receives production private keys. A separate offline signer transforms
an approved target-files input into:

- target-files with the reviewed APK and APEX certificate mapping;
- APEX payloads and Android Verified Boot metadata signed by the declared AVB
  role;
- a full OTA and, when an approved old target-files input exists, an
  incremental OTA;
- release/channel metadata derived from the internally signed OTA; and
- an externally signed factory archive and append-only release record.

The offline signer returns only signed outputs, public certificates, hashes and
the verification record. Online packaging may create the outer factory
container only from the approved signed target-files. It must not require a
release private key.

Production storage and token assignments remain pending until the actual
offline equipment proves every intended provider/algorithm/tool combination.
“The token supports RSA” is not an end-to-end Android signing result. A role
which cannot use the intended provider must be assigned an explicitly reviewed
offline software-held alternative or remain a blocker.

## Pinned role derivation

The selected `2026091000` manifest resolves these signing authorities:

- `script` commit `639cdf6558e7401f8bab1cb8f53546ff4a0c8fef`;
- `build/make` commit `7f0398241bc8c4ef5255a8063befa5045e5cfab4`;
- `development` commit `bf1857fd7d886218a1ea456b94eb7eb15d5c1338`;
- `external/avb` commit `ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee`;
- `prebuilts/jdk/jdk21` commit
  `ef5bcc92586b839ae3dbacc154127092fa4002ec`;
- `system/update_engine` commit
  `79f478a4f89e701e85fa15dec340bf445342abbd`; and
- `tools/apksig` commit `ba4d984e1a360d427307d669d2f789212130e9e8`.

The pinned Android `make_key` helper creates RSA-4096 keys and SHA-256 X.509
certificates. The GrapheneOS script defines nine Android certificate roles:
`releasekey`, `platform`, `shared`, `media`, `networkstack`, `bluetooth`,
`sdk_sandbox`, `gmscompat_lib` and `nfc`. It uses `SHA256_RSA4096` for the AVB
and APEX-payload role and an Ed25519 OpenSSH signature in the `factory images`
namespace for the outer factory archive. OTA package and payload signing use
the release key. Channel files are derived from verified OTA metadata and do
not silently gain a separate private-key role.

The upstream role and command references are the
[GrapheneOS build guide](https://grapheneos.org/build), the pinned
`script/generate-release.sh`, `script/generate-delta.sh` and AOSP
`sign_target_files_apks` implementation. File hashes in the machine-readable
contract prevent a moving branch from substituting for the selected release.

## Target-files inventory

The concrete inventory is derived from these archive members:

- `META/apkcerts.txt` for every APK certificate assignment;
- `META/apexkeys.txt` for every APEX container certificate and payload key;
- `META/misc_info.txt` for every emitted AVB key/algorithm chain.

The verifier rejects duplicate ZIP names, path traversal, missing or oversized
metadata, duplicate package records, unknown signed-output roles and any
presigned package not named exactly by the selected target profile. Globs are
not accepted in the presigned allowlist. A qualified profile also binds the
exact unsigned target-files hash. For a presigned package which is present in
the archive, it binds the literal member path, basename, byte count and
SHA-256; for build/test metadata which has no archive member, it requires that
basename to remain absent. The exact metadata token is retained separately
from the literal archive basename; the verifier does not infer an archive path
from metadata. An unsigned input may contain public development keys; that
observation is not approval of those keys in the signed output.

Validate the static contract without creating output or contacting a device:

```sh
bin/diamaneos signing roles
```

Inspect an actual target-files archive and write a new, non-overwriting
inventory record:

```sh
bin/diamaneos signing inventory \
  --profile generic-x86_64-qualification \
  --stage unsigned \
  --target-files "$TARGET_FILES" \
  --output "$OUTPUT_JSON"
```

The pinned Android signer preserves the source labels in `META/apkcerts.txt`
and `META/apexkeys.txt`; those labels select inputs and are not a destination-
key receipt. Therefore `--stage signed` also requires the exact accepted
unsigned inventory used to create the explicit signing plan:

```sh
bin/diamaneos signing inventory \
  --profile generic-x86_64-qualification \
  --stage signed \
  --source-inventory "$UNSIGNED_INVENTORY_JSON" \
  --target-files "$SIGNED_TARGET_FILES" \
  --output "$SIGNED_INVENTORY_JSON"
```

The signed inventory requires the package/APEX metadata to remain identical
to that accepted input, records the expected destination role for every
package, and independently requires transformed AVB metadata. The dummy
qualification then verifies representative APK/APEX certificates, APEX/AVB
payload keys, OTA signatures and wrong-key rejection from the artifacts; it
never treats the preserved source labels as cryptographic proof.

APK role coverage is evaluated over the union of the accepted SDK and
Cuttlefish signed target-files archives, not by assuming that one product
emits every package named by its certificate metadata. The exact accepted
inputs declare `bluetooth`, `nfc` and `sdk_sandbox` records but contain no APK
payload for those roles. The disposable qualification therefore records those
three roles as metadata-only and signs a standalone APK probe with each role's
fresh key. The probe input is the smallest deterministic real APK selected
from the accepted signed-archive union, rather than a synthetic installable
package claim. That proves the key, certificate and pinned `apksigner` path
without claiming a target-files transformation which did not occur. Every other
Android certificate role must have a real transformed APK in one of the two
accepted archives. A different missing-role set fails closed and requires a
new review; the future FP6 product must regenerate its own real artifact
coverage rather than inheriting these generic limitations.
The runner derives its Android certificate roles, profile pair and reviewed
metadata-only exception set from `config/signing-roles.json`; it does not
maintain a second permissive role list. An upstream role absent from that
signed contract remains an inventory error rather than being auto-enrolled.

A package appearing in a discovery report does not add it to the allowlist.
Review why it remains presigned, bind its archive identity or reviewed
metadata-only absence, and update the exact profile before a qualification
run may pass.

On the accepted builder, `deploy/builder/run-signing-discovery` performs that
checkpoint as the unprivileged build identity with network access denied. It
builds only `target-files-package` and `otatools-package`, creates no key and
performs no signing operation. The default service selects the generic SDK
profile. The separate `diamaneos-builder-ota-signing-discovery.service` uses
the same bounded runner with a fixed Cuttlefish Virtual A/B profile; arbitrary
profile or target selection is rejected. The immutable result is
`NEEDS_REVIEW` when the only discrepancy is an exact unlisted `PRESIGNED`
package; every other inventory error fails the job. Presigned entries are
never auto-approved.

The generic SDK x86_64 profile qualifies APK, APEX and AVB role
transformations. That product has neither an A/B partition inventory nor a
recovery image, so it is not an OTA-generation input. The distinct Cuttlefish
x86_64 phone profile is a real Virtual A/B target and qualifies the full and
incremental OTA tool path. Keeping these inputs separate prevents a synthetic
or relabelled SDK archive from being presented as OTA evidence. Neither result
is FP6 compatibility, release, update-semantics or hardware evidence. The FP6
profile must be regenerated and reviewed from the actual FP6 `user`
target-files package; a generic package list cannot be copied over as proof.

The SDK target publishes target-files and otatools through the legacy product
output paths. The Android 17 Cuttlefish product publishes both through Soong
module intermediates instead. Discovery accepts only one regular artifact
inside each exact selected module root; it never searches the complete output
tree or falls back to an older product's package. Soong module paths are
resolved from the declared top-level output root, not Android's `$OUT`
variable, which names the selected product output directory after `lunch`.

## Disposable-key qualification

A qualification run must use newly generated disposable keys under its own
private run directory and must retain:

1. the exact unsigned and signed target-files hashes and inventories;
2. the public fingerprint for every key role;
3. representative cryptographic verification for APK, APEX container, APEX
   payload and every AVB chain;
4. full and incremental OTA outputs with both ZIP and payload verification;
5. factory-archive and release-record signatures;
6. a wrong-key rejection for each verifier class; and
7. interruption/restart recovery showing that an incomplete run cannot be
   promoted.

`deploy/builder/run-dummy-signing-qualification` is the no-argument builder
entry point. It accepts no operator-selected artifact or key path. Before that
entry point may pass, APK/APEX/AVB proofs must bind the accepted SDK
target-files while full and incremental OTA proofs bind the separately
accepted Virtual A/B target-files. The runner must require the exact reviewed
tools checkout, source-project revisions, source-file hashes, both unsigned
target-files hashes and the otatools hash. It enumerates every accepted package
name into an explicit role mapping and deliberately does not use the global
APK/APEX key-override options.
The accepted Cuttlefish AVB inventory includes the custom chained-vbmeta images
`vbmeta_system_dlkm` and `vbmeta_vendor_dlkm`. The pinned releasetools command
line has no custom chained-vbmeta key override. The runner therefore requires
the source metadata's exact reviewed `system_dlkm` and `vendor_dlkm` custom-
vbmeta partition set, then prepares a transient copy by replacing only those
two existing key-path and algorithm pairs with the disposable AVB key and
`SHA256_RSA4096`. It does not add them to the distinct custom-data-image list.
Every other ZIP member must remain unchanged; both archive hashes and the four
changed field names are recorded, and the transient input is removed before
evidence promotion. Any missing, duplicate or additional custom-vbmeta field
fails closed. The resulting
signed images must still pass independent AVB verification.
Fresh private material lives under `/dev/shm`, is removed before independent
verification begins and is never retained in the evidence directory.
The pinned Android `make_key` helper's cleanup trap can return status 1 after
creating a key successfully. The runner accepts only status 0 or 1 from that
exact source-bound helper, then requires nonempty, nonsymlink certificate and
PKCS#8 outputs and independently parses both with OpenSSL. The observed helper
status and both parse results are retained in `key-generation.json`; a missing
or malformed artifact still fails the run.

The Virtual A/B qualification profile's incremental proof uses the same signed
Cuttlefish target-files archive as its old and new input. This deliberately
exercises the complete incremental OTA generation, package-signature and
payload-signature path as a no-op delta. It does not claim changed-build update
semantics; that remains part of an actual FP6 old/new release-pair
qualification.

The generic image archive similarly proves the pinned outer Ed25519 `factory
images` signature role, not the structure or installability of a future FP6
factory package. The FP6 packaging path must be requalified against the real
product output.

The retained release manifest hashes every qualification artifact and is
signed in the `diamaneos-dummy-release-record` namespace. Independent
verification re-hashes the artifacts, accepts the declared public key and
requires the same signature to fail against a different public key:

```sh
bin/diamaneos signing verify \
  --result "$RUN_ROOT/result.json" \
  --artifact-root "$RUN_ROOT"
```

The command refuses absolute/traversing paths, symlinks, missing files,
duplicate proof/artifact identifiers, altered hashes, an incomplete proof set,
production-material claims and a mismatched source binding. A PASS describes
only the disposable run named by that result.

## Offline signer qualification

The signer is prepared while no production secret exists. Before production
custody can begin, repeat the complete disposable flow on the offline host and
prove all of the following:

- no active or unexpected network interface and no required download;
- installed tools and input media match the reviewed hashes;
- inbound and outbound media have distinct, enforced purposes;
- unrecognized media content stops the procedure;
- actual token model, firmware, quantity, origin, algorithm, slot and touch
  behavior are inventoried;
- every intended token-backed operation works through the real Android tool
  adapter, including cancellation and restart; and
- unsupported operations remain visible blockers or use an explicitly reviewed
  offline software-held role.

Private PINs, recovery material, device identifiers, custody locations and raw
offline logs never belong in this repository. Public records contain only the
technical role, algorithm, public fingerprint, tool binding and sanitized
result.

## Change and recovery rules

Key reuse and rotation are role-specific. APK shared-user/privileged
permissions, APEX, OTA and AVB trust cannot be collapsed into one generic key
rotation statement. The AVB root is an especially durable device trust anchor;
replacement may require bootloader unlock and data loss. No production key is
generated until the complete recoverable key set, wrapping/backup procedure,
replacement-token path and restoration rehearsal have their own approved
ceremony.

Preserve the unsigned input, signed target-files, full/incremental OTAs, public
identities and verification record needed to reproduce the transformation.
Never preserve a private key in build logs, result JSON or public evidence.
