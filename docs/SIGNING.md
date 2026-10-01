# Signing roles and offline release boundary

How DiamaneOS signs Android builds: the key roles, how the signing contract is
checked, and how signing is qualified with throwaway (disposable) keys. For
maintainers working on signing or the builder. It does not authorize a
production key ceremony, token import, boot-key enrollment or release.

| Term | Meaning |
| --- | --- |
| target-files | The unsigned build output archive that signing turns into release images. |
| otatools | The package of Android host tools used for signing and OTA generation. |
| APK, APEX | An app package; an updatable system module package. |
| AVB | Android Verified Boot: signed metadata (vbmeta) that the bootloader checks before booting the OS partitions. |
| OTA | An update package: full, or incremental (a delta from an older build). |
| Presigned package | A package that arrives already signed and keeps that signature. |
| Token | A hardware security key that holds private keys. |

## The role contract

DiamaneOS treats signing as a typed release transformation, not a generic "sign
this path" operation. The public role contract is
[`config/signing-roles.json`](../config/signing-roles.json). It is bound to the
selected GrapheneOS manifest and to the exact release, delta, metadata and key
generation scripts that manifest uses. Changing any bound revision, script,
algorithm, target-files inventory or approved presigned package invalidates the
qualification result.

## Trust split

The online builder produces unsigned target-files and an otatools package. It
never receives production private keys. A separate offline signer turns an
approved target-files input into:

- target-files with the reviewed APK and APEX certificate mapping;
- APEX payloads and AVB metadata signed by the declared AVB role;
- a full OTA and, when an approved old target-files input exists, an
  incremental OTA;
- release/channel metadata derived from the internally signed OTA;
- an externally signed factory archive and an append-only release record.

The offline signer returns only signed outputs, public certificates, hashes and
the verification record. Online packaging may build the outer factory container
only from the approved signed target-files, and must not need a release private
key.

Production storage and token assignments stay pending until the actual offline
equipment proves every intended provider/algorithm/tool combination. "The token
supports RSA" is not an end-to-end Android signing result. A role that cannot
use the intended provider gets an explicitly reviewed offline software-held
alternative or stays a blocker.

## Pinned roles

The selected `2026091000` manifest resolves these signing authorities:

| Project | Commit |
| --- | --- |
| `script` | `639cdf6558e7401f8bab1cb8f53546ff4a0c8fef` |
| `build/make` | `7f0398241bc8c4ef5255a8063befa5045e5cfab4` |
| `development` | `bf1857fd7d886218a1ea456b94eb7eb15d5c1338` |
| `external/avb` | `ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee` |
| `prebuilts/jdk/jdk21` | `ef5bcc92586b839ae3dbacc154127092fa4002ec` |
| `system/update_engine` | `79f478a4f89e701e85fa15dec340bf445342abbd` |
| `tools/apksig` | `ba4d984e1a360d427307d669d2f789212130e9e8` |

- The pinned Android `make_key` helper creates RSA-4096 keys and SHA-256 X.509
  certificates.
- The GrapheneOS script defines nine Android certificate roles: `releasekey`,
  `platform`, `shared`, `media`, `networkstack`, `bluetooth`, `sdk_sandbox`,
  `gmscompat_lib` and `nfc`.
- The AVB and APEX-payload role uses `SHA256_RSA4096`. The outer factory
  archive uses an Ed25519 OpenSSH signature in the `factory images` namespace.
- OTA package and payload signing use the release key. Channel files are derived
  from verified OTA metadata and do not silently gain their own private-key
  role.

The upstream references are the
[GrapheneOS build guide](https://grapheneos.org/build), the pinned
`script/generate-release.sh` and `script/generate-delta.sh`, and the AOSP
`sign_target_files_apks` implementation. File hashes in the machine-readable
contract stop a moving branch from standing in for the selected release.

## Target-files inventory

The inventory is derived from these archive members:

- `META/apkcerts.txt` for every APK certificate assignment;
- `META/apexkeys.txt` for every APEX container certificate and payload key;
- `META/misc_info.txt` for every emitted AVB key/algorithm chain.

The verifier rejects duplicate ZIP names, path traversal, missing or oversized
metadata, duplicate package records, unknown signed-output roles and any
presigned package not named exactly by the selected target profile. The
presigned allowlist accepts no globs.

A qualified profile also binds the exact unsigned target-files hash. For a
presigned package present in the archive, it binds the literal member path,
basename, byte count and SHA-256. For build/test metadata with no archive
member, it requires that basename to stay absent. The exact metadata token is
kept separately from the literal archive basename; the verifier does not infer
an archive path from metadata. An unsigned input may contain public development
keys; seeing them is not approval of those keys in the signed output.

Validate the static contract without creating output or a key, or contacting a
device:

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

The pinned Android signer keeps the source labels in `META/apkcerts.txt` and
`META/apexkeys.txt`; those labels select inputs and are not a receipt for the
destination key. So `--stage signed` also requires the exact accepted unsigned
inventory used to create the explicit signing plan:

```sh
bin/diamaneos signing inventory \
  --profile generic-x86_64-qualification \
  --stage signed \
  --source-inventory "$UNSIGNED_INVENTORY_JSON" \
  --target-files "$SIGNED_TARGET_FILES" \
  --output "$SIGNED_INVENTORY_JSON"
```

The signed inventory requires the package/APEX metadata to stay identical to
that accepted input, records the expected destination role for every package,
and separately requires transformed AVB metadata. The dummy qualification then
verifies representative APK/APEX certificates, APEX/AVB payload keys, OTA
signatures and wrong-key rejection from the artifacts; it never treats the kept
source labels as cryptographic proof.

### Role coverage

APK role coverage is judged over the union of the accepted SDK and Cuttlefish
signed target-files archives, not by assuming one product emits every package
its certificate metadata names.

- The exact accepted inputs declare `bluetooth`, `nfc` and `sdk_sandbox`
  records but contain no APK payload for them. The disposable qualification
  records those three roles as metadata-only and signs a standalone APK probe
  with each role's fresh key. The probe is the smallest deterministic real APK
  from the accepted signed-archive union, not a synthetic installable package.
  This proves the key, certificate and pinned `apksigner` path without claiming
  a target-files transformation that did not happen.
- Every other Android certificate role must have a real transformed APK in one
  of the two archives. A different missing-role set fails closed and needs a
  new review. The future FP6 product must regenerate its own real artifact
  coverage instead of inheriting these generic limits.
- The runner derives its Android certificate roles, profile pair and reviewed
  metadata-only exceptions from `config/signing-roles.json`; there is no second,
  more permissive role list. An upstream role missing from that contract stays
  an inventory error instead of being enrolled automatically.

### Signing discovery

A package showing up in a discovery report is not added to the allowlist.
Review why it stays presigned, bind its archive identity or reviewed
metadata-only absence, and update the exact profile before a qualification run
can pass.

On the accepted builder, `deploy/builder/run-signing-discovery` does that
checkpoint as the unprivileged build identity with network access denied. It
builds only `target-files-package` and `otatools-package`, creates no key and
signs nothing. The default service selects the generic SDK profile; the separate
`diamaneos-builder-ota-signing-discovery.service` runs the same bounded runner
with a fixed Cuttlefish Virtual A/B profile. Any other profile or target is
rejected. The immutable result is `NEEDS_REVIEW` when the only discrepancy is an
exact unlisted `PRESIGNED` package; every other inventory error fails the job.
Presigned entries are never auto-approved.

### The two qualification profiles

| Profile | Qualifies | Why |
| --- | --- | --- |
| Generic SDK x86_64 | APK, APEX and AVB role transformations | It has no A/B partition inventory or recovery image, so it is not an OTA-generation input. |
| Cuttlefish x86_64 phone | Full and incremental OTA tool path | It is a real Virtual A/B target. |

Keeping them apart stops a synthetic or relabelled SDK archive being presented
as OTA evidence. Neither result is FP6 compatibility, release, update-semantics
or hardware evidence. The FP6 profile must be regenerated and reviewed from the
actual FP6 `user` target-files package; a generic package list cannot be copied
over as proof.

The SDK target publishes target-files and otatools through the legacy product
output paths; the Android 17 Cuttlefish product publishes both through Soong
module intermediates. Discovery accepts only one regular artifact inside each
exact selected module root. It never searches the whole output tree or falls
back to an older product's package. Soong module paths are resolved from the
declared top-level output root, not Android's `$OUT` variable (which names the
selected product output directory after `lunch`).

## Disposable-key qualification

A qualification run must use newly generated disposable keys under its own
private run directory, and must keep:

1. the exact unsigned and signed target-files hashes and inventories;
2. the public fingerprint for every key role;
3. representative cryptographic verification for APK, APEX container, APEX
   payload and every AVB chain;
4. full and incremental OTA outputs with both ZIP and payload verification;
5. factory-archive and release-record signatures;
6. a wrong-key rejection for each verifier class;
7. interruption/restart recovery showing that an incomplete run cannot be
   promoted.

`deploy/builder/run-dummy-signing-qualification` is the no-argument builder
entry point; it accepts no operator-selected artifact or key path. Before it
can pass:

- APK/APEX/AVB proofs must bind the accepted SDK target-files, and full and
  incremental OTA proofs the separately accepted Virtual A/B target-files;
- the runner must require the exact reviewed tools checkout, source-project
  revisions, source-file hashes, both unsigned target-files hashes and the
  otatools hash;
- it lists every accepted package name in an explicit role mapping, and
  deliberately does not use the global APK/APEX key-override options.

**Custom chained vbmeta.** The accepted Cuttlefish AVB inventory includes the
custom chained-vbmeta images `vbmeta_system_dlkm` and `vbmeta_vendor_dlkm`, and
the pinned releasetools command line has no key override for them. So the
runner requires the source metadata's exact reviewed `system_dlkm` and
`vendor_dlkm` custom-vbmeta partition set, then makes a transient copy that
replaces only those two existing key-path and algorithm pairs with the
disposable AVB key and `SHA256_RSA4096`. It does not add them to the separate
custom-data-image list. Every other ZIP member stays unchanged; both archive
hashes and the four changed field names are recorded, and the transient input
is removed before evidence promotion. A missing, duplicate or extra
custom-vbmeta field fails closed. The signed images must still pass independent
AVB verification.

**Key material.** Fresh private material lives under `/dev/shm`, is removed
before independent verification begins, and is never kept in the evidence
directory. The pinned `make_key` helper's cleanup trap can return status 1
after creating a key successfully. The runner accepts only status 0 or 1 from
that exact source-bound helper, then requires non-empty, non-symlink
certificate and PKCS#8 outputs and parses both with OpenSSL. The observed
status and both parse results are kept in `key-generation.json`; a missing or
malformed artifact still fails the run.

**What the proofs do not cover.**

- The Virtual A/B profile's incremental proof uses the same signed Cuttlefish
  target-files archive as old and new input. This deliberately exercises the
  complete incremental OTA generation, package-signature and payload-signature
  path as a no-op delta. It does not claim changed-build update semantics; that
  belongs to an actual FP6 old/new release-pair qualification.
- The generic image archive proves the pinned outer Ed25519 `factory images`
  signature role, not the structure or installability of a future FP6 factory
  package. The FP6 packaging path must be requalified against the real product
  output.

**Release record.** The kept release manifest hashes every qualification
artifact and is signed in the `diamaneos-dummy-release-record` namespace.
Independent verification re-hashes the artifacts, accepts the declared public
key and requires the same signature to fail against a different public key:

```sh
bin/diamaneos signing verify \
  --result "$RUN_ROOT/result.json" \
  --artifact-root "$RUN_ROOT"
```

The command refuses absolute or traversing paths, symlinks, missing files,
duplicate proof/artifact identifiers, altered hashes, an incomplete proof set,
production-material claims and a mismatched source binding. A PASS describes
only the disposable run named by that result.

## Offline signer qualification

The signer is prepared while no production secret exists. Before production
custody can begin, repeat the complete disposable flow on the offline host and
prove:

- no active or unexpected network interface and no required download;
- installed tools and input media match the reviewed hashes;
- inbound and outbound media have distinct, enforced purposes;
- unrecognized media content stops the procedure;
- the actual token model, firmware, quantity, origin, algorithm, slot and touch
  behaviour are inventoried;
- every intended token-backed operation works through the real Android tool
  adapter, including cancellation and restart;
- unsupported operations stay visible blockers or use an explicitly reviewed
  offline software-held role.

Private PINs, recovery material, device identifiers, custody locations and raw
offline logs never belong in this repository. Public records contain only the
technical role, algorithm, public fingerprint, tool binding and sanitized
result.

## Change and recovery rules

- Key reuse and rotation are role-specific. APK shared-user/privileged
  permissions, APEX, OTA and AVB trust cannot be collapsed into one generic key
  rotation statement.
- The AVB root is an especially durable device trust anchor; replacing it may
  need a bootloader unlock and data loss.
- No production key is generated until the complete recoverable key set,
  wrapping/backup procedure, replacement-token path and restoration rehearsal
  have their own approved ceremony.
- Keep the unsigned input, signed target-files, full/incremental OTAs, public
  identities and verification record needed to reproduce the transformation.
- Never keep a private key in build logs, result JSON or public evidence.

## Diagnose retained outputs before another signing run

**APK certificate pinning** accepts both legacy `Signer #1 certificate` and
scheme-labelled `V3.0 Signer: certificate` output. Verification must still exit
zero, declare one signer and give exactly the expected certificate SHA-256.
Public-key fingerprints, missing output and extra certificates cannot satisfy
that check. The full qualification now signs and verifies one small APK before
transforming the large target-files packages, with the same pinned JDK and
verifier, to catch tool, runtime and output-format failures early.

**Replay.** If a completed failed attempt kept its signed outputs and public
keys, replay only the independent verification stage, as the build identity:

```sh
export DIAMANEOS_EXPECTED_TOOLS_COMMIT=REVIEWED_40_HEX_COMMIT
"$TOOLS_ROOT/deploy/builder/recheck-dummy-signing" \
  --workspace "$WORK_ROOT" \
  --run "$WORK_ROOT/evidence/dummy-signing/RETAINED_RUN.failed" \
  --output "$WORK_ROOT/evidence/dummy-signing/NEW_DIAGNOSTIC_DIRECTORY"
```

- Use a clean reviewed checkout and a new output directory on the same
  filesystem.
- The command takes the common workspace lock, checks the kept artifact hashes
  and certificate fingerprints, and hardlinks large signed outputs into a fresh
  diagnostic tree. It copies only named public inputs, not old results, logs or
  secrets. Treat the hardlinked artifacts as immutable. Fresh verifier logs and
  samples belong to the diagnostic tree; the original failure record stays
  unchanged.
- Replay uses the exact qualified otatools archive and pinned JDK, and calls the
  same APK/APEX, AVB, OTA and OpenSSH verification function as a full run. It
  creates no keys, signs nothing and does not repeat target-files
  transformations or OTA generation. Hashing, extraction and verification still
  take time and disk space; it is not an instant check.
- Every diagnostic report says `qualification_accepted: false`, even when
  verification passes.

A new complete qualification is still needed when signing, cleanup or any
required proof was incomplete. If signing itself failed or an output is
missing, replay cannot replace that work. No resumable signing state or
persistent disposable secrets are introduced. One exception: if a complete run
failed only because its final result schema rejected the runner's own metadata,
a separate reviewed acceptance record may bind the unchanged result hash,
execution revision, corrected validator revision and all required proof checks.
Keep the original failure record; a passing diagnostic alone never creates that
acceptance record.

**AVB image coverage.** AVB verification uses the images each signed
target-files archive actually emits. A product declaring `no_boot=true` may keep
boot key metadata without `IMAGES/boot.img`; that role is recorded as
metadata-only and must be exercised by an emitted boot image in the other
qualification profile. Any other missing declared image fails verification.
`avb-image-coverage.json` records this coverage separately from the unchanged
signing inventories. Each product's sibling images are extracted together so
AVB hash and hashtree descriptors can be checked. Chain descriptors must match
the declared rollback index location and the kept disposable AVB public key,
and each emitted signed image also gets its own valid-key and wrong-key check.
