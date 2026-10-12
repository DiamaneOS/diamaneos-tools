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

The public inventory uses schema version 2. `certificate_roles` maps every role to
an external certificate; `avb_public_key` names its RSA public PEM; `factory` contains
`allowed_signers` and an exact SSH identity. File names are relative to the inventory.
Private key material is rejected.

The optional `presigned_apex_payload_keys` object maps each reviewed presigned
APEX metadata name to an independently supplied AVB **binary** public-key file.
For the exact stock Widevine package this is, for example:

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
in `source_files`; unsupported native APIs refuse.

Prepare verifies container and payload before accepting preservation.
Publication verification repeats both checks and requires unchanged complete
APEX bytes.

`--source-root` must match the actual clean revisions in the resolved manifest.
Updating its tools checkout makes an older packet's source check fail. Qualify a
new archive with truthful packaging/build metadata and fresh authenticated
verification on the updated source basis.

Never edit an old manifest, report or source identity to make comparison pass.
New policy and public inventory hashes require a fresh signing plan; an earlier
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
path changes refuse publication; copying does not make publication-controlled
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

Use `--previous-result` with `--previous-signature` for subsequent runs. The externally
verified result must contain successful artifact verification in a compatible mode.
Each plan reports changed signing projects; the first includes every signing project.

Release-script overrides supply package roles; archive contents determine absent metadata.
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

## Verify publication

Keep the inventory, plan, result and scratch outside the dedicated publication directory.
The publication description has `schema_version`, `signed_target_files`, `images_archive`,
`ota`, `factory_archive`, `factory_signature`, `release_record` and `release_signature`.
Paths are relative; unknown files, duplicates and links are rejected.

Each `ota` entry has `file` and `source_target_files`; a full OTA uses `null`.
Delta sources must match previous verified artifact hashes. A full OTA is mandatory.

The release record contains `schema_version`, `plan_sha256` and the exact `artifacts`
hash map for target-files, images, OTAs, factory archive and factory signature.

Installed host OpenSSH verifies the release record and its plan binding before
archive tools run. Plan, publication, record, signature and authority reads use
their frozen copies throughout verification.

The authenticated plan binds the otatools snapshot. Expected
public authority stays outside the publication; native artifact checks still run
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
- Flat image membership includes every declared firmware, wipe and modem-reset file. Unknown members and partial logical sets are rejected.
- Native AVB verification covers every declared partition and rollback chain. Disabled verification and algorithm changes are rejected.
- The platform OTA checker verifies whole-file, payload and metadata signatures. Build metadata and payload properties bind the accepted build and final bytes.
- Native payload application reproduces signed target-files partitions, using the verified source for deltas.
- Factory and release signatures use the external authority and distinct namespaces. The factory contains exactly one flat member: the verified image archive.
- Closing checks bind source, public material and publication bytes throughout verification. A changed input cannot produce a successful result.

Success records `artifact_signatures_verified: true`, public identities, artifact hashes
and run provenance. Production rejects AOSP public test keys even when supplied as
expected identities. Qualification requires the explicit `--qualification` flag.

## Fixtures

Fixtures create disposable material in temporary storage. Native signature cases require
explicit source and otatools inputs; Linux or `DIAMANEOS_SIGNING_REQUIRE_NATIVE=1` refuses
missing inputs. They exercise corrupt, unsigned, wrong-key, wrong-role and test-key artifacts.

```sh
DIAMANEOS_SIGNING_NATIVE_SOURCE="$SOURCE" \
DIAMANEOS_SIGNING_OTATOOLS="$IMAGES/otatools.zip" \
PYTHONPATH=src "$VENV/bin/python" -m unittest tests.signing.test_native_signatures
```

`apksigner`, `avbtool`, `check_ota_package_signature`, `delta_generator` and payload tooling
perform cryptographic verification. Policy code compares native results with external trust;
it contains no signature parser. Controlled orchestration fixtures do not establish
cryptographic or device acceptance.
