# Signing

How a verified build is signed with keys other than the public test keys, assembled into factory
images and checked again before publication. For anyone who signs their own builds, and for
DiamaneOS's offline release signing. Terms are explained in [TERMS.md](TERMS.md).

The build host stops at unsigned target-files and `otatools.zip`. Signing runs elsewhere, with keys
that never reach the build host.

Native platform tools (`apksigner`, `avbtool`, the OTA checker) verify every signature against
public keys you supply separately. A signed artifact never chooses the key it is checked against.

## The flow

1. `signing prepare` binds a signing plan to a build that passed `build verify`.
2. `signing command` prints the native signing commands for that plan, which you run with the keys.
3. `signing factory` assembles the factory ZIP from the signed target-files.
4. `images check` runs the image checks on the signed set.
5. `signing verify` checks every published signature again, independently.
6. `attestation-releases` adds the release's attestation values to the signed release list.

## Signing modes

`prepare`, `command`, `factory` and `verify` take the same mode. The plan records it, and each later
command must name the same one.

| Mode | Flag | Build | Keys | Factory ZIP's `build.json` |
| --- | --- | --- | --- | --- |
| Own keys | none | Any verified build, also one built by hand or with local changes | Any but the AOSP test keys | `release` false, `own-keys`, locks |
| Release | `--release` | An official `user` build | The DiamaneOS release keys | `release` true, `release-keys`, locks |
| Qualification | `--qualification` | A clean verified build the tools ran | Any | `release` false, `qualification-keys`, never locked |

A set made without `--release` is never a DiamaneOS release.
[`config/release-keys.json`](../config/release-keys.json) pins the public half of each release key, and
`--release` refuses and names every key that differs from its pin:

- the inventory's nine certificates, AVB key and factory signer, before the factory signer
  authenticates anything
- the certificates of the App Store, Auditor and Camera in the signing policy, and those in the key
  directory
- the OTA certificates in `otacerts.zip` of system and recovery, after signing
- the catalog key the App Store carries, and the attestation release-list signer

## Your own keys

Anyone who builds DiamaneOS can sign the build with their own keys and lock the phone with them. Run
these steps from the source root, after `build all` or `build package`.

They also sign a build by hand, as long as the tools that recorded and checked it were clean.

**Generate the keys once and keep them.** Another key set needs a new install, which erases the phone.
Give all keys the same passphrase, because `script/decrypt-keys` expects that.

```sh
mkdir -p keys/FP6 && cd keys/FP6
CN=YourName
for role in releasekey platform shared media networkstack bluetooth sdk_sandbox gmscompat_lib nfc
do
  ../../development/tools/make_key "$role" "/CN=$CN/"
done
openssl genrsa 4096 | openssl pkcs8 -topk8 -scrypt -out avb.pem
../../external/avb/avbtool.py extract_public_key --key avb.pem --output avb_pkmd.bin
ssh-keygen -t ed25519 -f id_ed25519
cd ../..
```

**Prepare the public inputs.** `signing inventory` writes the public key inventory from the
certificates, `avb_pkmd.bin` and `id_ed25519.pub`. It reads no private key. It takes the payload key
of each presigned APEX from the target-files, only when its hash matches the signing policy.

`SOURCE` is the source root. `TRUST`, `OTATOOLS`, `SCRATCH`, `PLAN`, `SIGNED_TARGET_FILES` and
`PUBLICATION` are paths of your choice outside the source tree.

```sh
IMAGES=~/diamaneos-build/images/<build_id>
VERIFY_REPORT=$IMAGES.verify.json
tools/diamaneos/bin/diamaneos signing inventory --key-dir keys/FP6 --identity "$CN" \
  --target-files "$IMAGES/target-files.zip" --output "$TRUST"
ssh-keygen -Y sign -n diamaneos-build-verification -f keys/FP6/id_ed25519 "$VERIFY_REPORT"
VERIFY_REPORT_SIGNATURE=$VERIFY_REPORT.sig
unzip -q "$IMAGES/otatools.zip" -d "$OTATOOLS"
KEY_DIR=$(mktemp -d /dev/shm/keys.XXXXXXXXXX)
cp keys/FP6/* "$KEY_DIR" && script/decrypt-keys "$KEY_DIR"
```

**Sign.** Run [`prepare`](#prepare), [`command`](#sign-the-target-files) with `--ota` and
[`factory`](#assemble-the-factory-images) as shown below, without a mode flag. Then sign the factory
ZIP and remove the decrypted keys:

```sh
ssh-keygen -Y sign -n "factory images" -f keys/FP6/id_ed25519 "$PUBLICATION/FP6-factory.zip"
rm -rf "$KEY_DIR"
```

**The result.**

- The ZIP's `build.json` says `own-keys` and `release` false. Its `flash-all.sh` installs your AVB key
  and stops in the bootloader for the lock.
- A build of changed source keeps its changes in that `build.json`: `modified` lists the projects with
  local changes, and `deviations` the other differences.
- A build without `--official` has no Updater. Install the full OTA of a newer build with
  `adb sideload` in recovery.

An update server of your own is a source change, as on GrapheneOS:

- `vendor/diamaneos/download-policy/client/de/diamaneos/downloads/DownloadPolicy.java` builds the
  update addresses in `url()`.
- `vendor/diamaneos/overlay/DiamaneOSUpdaterOverlay/res/xml/network_security_config.xml` names the
  server domains and their TLS pins.
- The `updater-server` and `updater-pins` image checks in `config/fp6-image-checks.json` hold exactly
  these hosts, domains and pins.
- `vendor/diamaneos/product.mk` adds the Updater only when `DIAMANEOS_OFFICIAL_BUILD` is `true`.

## Prepare

```sh
tools/diamaneos/bin/diamaneos signing roles
tools/diamaneos/bin/diamaneos signing prepare \
  --build-record "$IMAGES/build.json" \
  --verification-report "$VERIFY_REPORT" \
  --verification-signature "$VERIFY_REPORT_SIGNATURE" \
  --target-files "$IMAGES/target-files.zip" \
  --resolved-manifest "$IMAGES/resolved-manifest.xml" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" --output "$PLAN"
```

`signing roles` checks the signing policy. `prepare` needs the build record, a complete report in
which every check passed, the report's signature, the checksums, the resolved manifest, target-files
and otatools. Every applicable device rule must pass against the tools of the resolved manifest.

- The installed OpenSSH authenticates the report before any incoming tool is unpacked or run.
- The plan binds the archive hashes, the source project revisions it reads, the JDK and the native
  signer. Changed source, tools, reports or build identity are refused.
- `--source-root` must be clean at the revisions of the resolved manifest. Never edit an old manifest,
  report or source identity to make a comparison pass.
- A new policy or public inventory needs a new plan. An earlier plan cannot be reused.

For a later release, `--previous-result` and `--previous-signature` give the signed result of each
earlier release an incremental update starts from, at most 3, in the same order. Each must show a
successful verification from an older build. Artifact names must differ between releases.

## Sign the target-files

```sh
tools/diamaneos/bin/diamaneos signing command --plan "$PLAN" \
  --target-files "$IMAGES/target-files.zip" --signed-target-files "$SIGNED_TARGET_FILES" \
  --signer "$OTATOOLS/bin/sign_target_files_apks" --key-dir "$KEY_DIR" --source-root "$SOURCE" \
  --ota "$PUBLICATION/ota.zip"
```

This prints the signing commands. Run them with the unpacked otatools' `bin` and the source's JDK on
`PATH`, and with the decrypted keys.

`--ota` adds the full OTA of the signed target-files, signed with `releasekey`. The command refuses
an altered plan, changed source or target-files and existing outputs.

What the signer keeps and replaces:

- Presigned packages and APEXes keep their bytes and must match the exact identities of the policy.
- APKs inside APEXes get the same role checks as any other APK.
- SELinux certificates follow the native default key mapping.
- `sign_target_files_apks` replaces only the AVB key and algorithm, and keeps the build's rollback
  indexes.

## Assemble the factory images

```sh
tools/diamaneos/bin/diamaneos signing factory --plan "$PLAN" \
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

`signing factory` uses only public keys. It authenticates the report before it loads otatools,
verifies the signed packages and partitions, checks that the logical images fit in super and copies
the firmware, reset and wipe images. It never replaces an existing output.

The ZIP has one directory named after the product and build number. It holds every signed partition,
each logical image as its own file, the firmware, `build.json`, `SHA256SUMS`,
`resolved-manifest.xml`, `flash-all.sh`, `flash-all.bat` and `avb_pkmd.bin`.

It carries no `super.img`: the install writes `super_empty.img` and then each logical image in
fastbootd.

- The new `build.json` binds the original record and report, the plan, the signed target-files and
  the image hashes. Its AVB key must equal both the external key and the one vbmeta embeds.
- `avb_rollback_indexes` in it holds the rollback index of vbmeta and of each chained image. Each must
  be above 0.
- The scripts are generated again from the plan's source. They select the slot and check
  `current-slot` before fastbootd, and stop on any failure. The batch script needs PowerShell to check
  every SHA-256.

The scripts run no lock themselves. Their closing text gives the stock relock order, the same as in
[locking](BUILD.md#locking). The ZIP contains no final result or its own hash. The signed release
record and the final result bind the finished ZIP.

## Check a signed image set

`images check` runs the checks of `build verify` on any image set, also one signed with other keys
(`signing` `release-keys`, `own-keys`, `qualification-keys` or `throwaway-test-keys` in `build.json`).
It writes only the report and temporary files.

```sh
tools/diamaneos/bin/diamaneos images check "$SIGNED_IMAGES" --workspace "$WORKSPACE" \
  --report "$SCRATCH/signed.verify.json" --scratch "$SCRATCH"
```

The workspace gives the source and the vendor generation. Its kernel prebuilts checkout must be at
the commit `build.json` names.

Factory images leave out the target-files and `otatools.zip`, so unpack the ZIP and give the signed
target-files and the build record it was signed from. Write the report next to the set, where
`flash-steps` reads it:

```sh
unzip -q "$PUBLICATION/FP6-factory.zip" -d "$SCRATCH/factory"
SIGNED_IMAGES=$(echo "$SCRATCH"/factory/*)
tools/diamaneos/bin/diamaneos images check "$SIGNED_IMAGES" --workspace "$WORKSPACE" \
  --target-files "$SIGNED_TARGET_FILES" --build-record "$IMAGES/build.json" \
  --report "$SCRATCH/factory/BUILD_ID.verify.json" --scratch "$SCRATCH"
```

`BUILD_ID` is `build_id` in the factory images' `build.json`. The target-files must have the hash
that `build.json` names, and the build record the hash in `signing_input`. The host tools come from
the `otatools.zip` next to that record.

Every check of `build verify` runs. Four that assume the public test keys have signed versions with
the same ids:

| Check | Signed version |
| --- | --- |
| `record` | `avb_custom_key` names `avb_pkmd.bin`, which vbmeta embeds byte for byte. `release` is true only with release keys. An official set signed with qualification keys has `never_lock` true, other official sets false. |
| `test-keys` | `release-keys` in every build tag and fingerprint, and no certificate in `otacerts.zip` or `mac_permissions.xml` with the key of a public test certificate. |
| `avb-chain` | `avbtool verify_image` of vbmeta and every chain with `avb_pkmd.bin`, the FP6 layout, and the rollback indexes of `build.json`, above 0 on an official or release-keys set. |
| `validators` | `validate_target_files` and `check_target_files_vintf` from the same `otatools.zip`, with the set's public keys. |

`avb_pkmd.bin` must be an RSA AVB key and none of the AOSP AVB test keys. A `release-keys` set must
carry the pinned release keys. `flash-steps` prints the lock only after `record` and `avb-chain`
passed.

## Verify publication

`signing verify` checks a whole publication against the plan and the public keys, with the native
tools. Keep the inventory, plan, result and scratch outside the publication directory.

```sh
tools/diamaneos/bin/diamaneos signing verify --plan "$PLAN" \
  --publication "$PUBLICATION_JSON" --artifact-root "$PUBLICATION" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" --output "$RESULT"
```

The publication description, kept outside the publication directory, names each file by a relative
path. The [publication schema](../schemas/signing-publication.schema.json) defines it:

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

- A full OTA is required, with `source_target_files` null. Each incremental names a source that
  matches a previously verified artifact.
- The release record holds `schema_version`, `plan_sha256` and the exact hashes of the target-files,
  images, OTAs, factory archive and its signature.
- Schema version 1, a single image archive in tar format, is accepted only with `--qualification`.
- Unknown files, duplicates and links are refused.

**What it verifies.**

- Every APK, both compressed APEX containers, their payloads and the APKs inside, against the
  assigned public keys. Package IDs, SELinux certificates and installed policy must match the plan.
- Every published partition against the signed target-files, including the unpacked logical images,
  and the complete membership of the image archive and the factory ZIP.
- AVB on every partition and chain. Disabled verification, changed algorithms and a rollback index of
  0 are refused.
- The OTA signatures with the platform checker. Applying each payload must reproduce the signed
  partitions, using the verified source for incrementals.
- The factory and release signatures, in their own namespaces, against the external authority.

An applied image must equal the original bytes, plus only the zero padding to update_engine's
4096-byte blocks. Any other difference is fatal, and the report names the partition, sizes, hashes and
first differing offset, never image bytes.

On success the result records `artifact_signatures_verified: true`, the public identities, the
artifact hashes and the attestation release values. Release and own-key modes refuse the AOSP public
test keys even when they are given as expected keys.

## Attestation release list

A verifier without a manufacturer-rooted attestation chain accepts a phone at first pairing only if its
attested values match a published release exactly. The release list carries these values per device.

- `signing verify` reads them from the verified vbmeta images and records them as
  `attestation_release`. `verifiedBootHash` is the vbmeta digest (`avbtool calculate_vbmeta_digest`),
  and `verifiedBootKey` the SHA-256 of `avb_pkmd.bin`.
- `config/fp6-attestation.json` names the AVB property behind the OS version and each patch level, and
  holds the KeyMint and attestation versions the secure area firmware reports.

**Format.** The list is a JSON envelope with `format`, `version`, a base64 `payload` and
`signatures`. Each signature is a DER ECDSA P-256 signature with SHA-256 over the exact payload
bytes.

Its `keyId` is the SHA-256 of the signer's public key. The payload holds `sequence`, the UTC
`issued` time and, per device, its `id`, `verifiedBootKey` and `releases`, with hashes in upper-case
hex.

**Commands.**

- `prepare` verifies the previous list, keeps its releases, adds the new one and raises the sequence
  by one. `--withdraw` removes a listed build. A listed build or boot hash, or another boot key for a
  listed device, is refused.
- A production list takes only a release signing result, signed in the `diamaneos-signing-result`
  namespace by the pinned release factory key, and its signer must be the pinned release-list key.
- `--signer detached` writes the payload for the offline signer. `attach` adds the returned
  signature and writes the list only after checking it. `verify --release` checks a list.
- `--signer test --test` signs with a local P-256 test key and takes only qualification results or
  `entry` output. `entry` reads the values from an image directory after checking its AVB chain.

```sh
tools/diamaneos/bin/diamaneos attestation-releases prepare --previous "$LISTS/releases.json" \
  --signing-result "$RESULT" --signing-result-signature "$RESULT.sig" --keys "$TRUST/inventory.json" \
  --signer detached --public-key "$TRUST/release-list.pub.pem" --output "$LISTS/payload.json"
tools/diamaneos/bin/diamaneos attestation-releases attach --release --previous "$LISTS/releases.json" \
  --payload "$LISTS/payload.json" --signature "$LISTS/payload.json.sig" \
  --public-key "$TRUST/release-list.pub.pem" --output "$LISTS/releases-next.json"
tools/diamaneos/bin/diamaneos attestation-releases verify --release --list "$LISTS/releases-next.json" \
  --public-key "$TRUST/release-list.pub.pem" --previous "$LISTS/releases.json"
```

A test list from images signed with a test AVB key:

```sh
tools/diamaneos/bin/diamaneos attestation-releases entry --images "$IMAGES" --build "$BUILD_NUMBER" \
  --avb-public-key "$TEST_KEYS/avb.pub.pem" --avbtool "$OTATOOLS/bin/avbtool" \
  --output "$SCRATCH/entry.json"
tools/diamaneos/bin/diamaneos attestation-releases prepare --first --entry "$SCRATCH/entry.json" \
  --signer test --test --test-key "$TEST_KEYS/release-list.pem" --output "$SCRATCH/releases.json"
```

## Signing policy and trust inputs

[`config/signing-roles.json`](../config/signing-roles.json) defines the algorithms, the certificate
roles and the presigned packages. Unknown roles and unreviewed presigned packages are refused.

| Role | Required key |
| --- | --- |
| Android certificates | RSA-4096, SHA-256 X.509, subject `/CN=DiamaneOS/` for releases |
| APEX payloads and AVB | `SHA256_RSA4096` |
| OTA package, payload and metadata | `releasekey` |
| Factory archive | Ed25519 SSH key, namespace `factory images` |
| Release record | SSH key, namespace `diamaneos-release-record` |
| Build verification report | SSH key, namespace `diamaneos-build-verification` |
| Previous signing result | SSH key, namespace `diamaneos-signing-result` |
| Attestation release list | ECDSA P-256 key, SHA-256 |

**The public inventory** (schema version 2) maps every certificate role to an external certificate,
names the AVB public key, and gives the factory signer's `allowed_signers` and SSH identity. File
names are relative to the inventory. Private key material is refused.

**Presigned APEXes.** `presigned_apexes` in the policy binds each stock APEX that keeps its own
signature to its exact path, hash, container signer and payload key hash.

The inventory's `presigned_apex_payload_keys` maps each one to an AVB public key file supplied
separately, such as the FP6's Widevine package:

```json
"presigned_apex_payload_keys": {
  "com.google.android.widevine.nonupdatable.apex": "widevine.avbpubkey"
}
```

That file must match the policy's hash and stay outside the publication. An expected key is never
taken from a submitted APEX. `avbtool` verifies the payload, and the tools only compare its embedded key
bytes with the external file.

**Preserved MAC signers.** `preserved_mac_signers` approves the signer of an app the App Store
installs, which no package in the build carries. Each entry binds one certificate SHA-256 to one
seinfo, and any other use refuses.

The FP6 entry is the Fairphone Camera's signer with seinfo `fpcamera`, the first signer of the stock
`FPCamera.apk` the vendor recipe pins.

The signing tests and their native fixtures are described in [TESTING.md](TESTING.md#signing).
