# Signing and publication verification

Signing policy assigns certificate, APEX payload, AVB, OTA and factory roles. A signing plan
binds those roles to a build that passed `build verify`. Native platform tools perform signature
verification using an independently supplied public key inventory.

## Stable policy

[`config/signing-roles.json`](../config/signing-roles.json) contains algorithms, role names,
certificate selectors and presigned-package rules. Release revisions and tool hashes are recorded
per run. A routine source rebase requires no signing-policy edit.

- Android certificate roles use RSA-4096 and SHA-256 X.509 certificates with `/CN=DiamaneOS/`.
- APEX payloads and AVB use `SHA256_RSA4096`.
- The OTA package, payload and payload metadata use the `releasekey` role.
- Factory archives use Ed25519 OpenSSH signatures in the `factory images` namespace.
- Release records use the `diamaneos-release-record` namespace.
- Unknown roles, packages and presigned exceptions are rejected.

```sh
bin/diamaneos signing roles
```

## Build-bound plan

`prepare` requires the build record, its complete PASS verification report, checksums, resolved
manifest, target-files and otatools archive. It rejects changed hashes, incomplete verification,
local source changes and tools from different source revisions. Production mode requires an
official `user` build.

- The build output records target-files and otatools hashes together.
- Signing-relevant project revisions come from the build's resolved manifest.
- Source script hashes, the JDK and otatools identities are recorded in the plan.
- Each run reports signing-relevant projects changed since `--previous-result`, before signing.
- A previous result must show successful artifact signature verification.
- The first run reports every signing project.

```sh
bin/diamaneos signing prepare \
  --build-record "$IMAGES/build.json" \
  --verification-report "$VERIFY_REPORT" \
  --target-files "$IMAGES/target-files.zip" \
  --resolved-manifest "$IMAGES/resolved-manifest.xml" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" \
  --output "$PLAN"
```

The public inventory has `schema_version`, `certificate_roles`, `avb_public_key`, `factory`,
`presigned` and `metadata_only` fields. Public files are relative to the inventory's directory.
Private key material is rejected.

| Field | Contents |
| --- | --- |
| `certificate_roles` | Every configured certificate role mapped to an external X.509 certificate file. |
| `avb_public_key` | External RSA public key in PEM format. |
| `factory` | `allowed_signers` file and an exact SSH signer identity. |
| `presigned` | Exact package paths, each with an external `certificate` and an optional `avb_public_key`. |
| `metadata_only` | Exact metadata names with no package bytes in the accepted archive. |

Presigned bytes remain identical to the accepted input and verify against their specified external
public identities. Upstream release-script overrides determine explicit package exceptions. APKs
inside APEX payloads receive the same complete role checks as other packaged APKs.

The native signing command is emitted for the accepted input. Its signer must match the build's
otatools binary. It refuses altered plans, changed target-files and existing output paths.

```sh
bin/diamaneos signing command --plan "$PLAN" \
  --target-files "$IMAGES/target-files.zip" \
  --signed-target-files "$SIGNED_TARGET_FILES" \
  --signer "$OTATOOLS/bin/sign_target_files_apks" --key-dir "$KEY_DIR"
```

## Final publication

`verify` requires only public verification material. Keep the trusted inventory, plan, result and
scratch storage outside the dedicated publication directory. Artifact contents never select their
own expected signing authority.

The publication description has `schema_version`, `signed_target_files`, `images_archive`, `ota`,
`factory_archive`, `factory_signature`, `release_record` and `release_signature` fields. File names
are relative to the publication directory. Unknown files, duplicate paths and links are rejected.

- `images_archive` contains the actual partition images, including logical partitions in `super`.
- `ota` lists objects with `file` and `source_target_files`. A full OTA uses `null` for its source.
- A delta source must match an artifact hash from the previous verified signing result.
- The release record contains `schema_version`, `plan_sha256` and an exact `artifacts` hash map.
- The artifact map covers target-files, images, OTAs, the factory archive and its signature.

```sh
bin/diamaneos signing verify --plan "$PLAN" \
  --publication "$PUBLICATION_JSON" --artifact-root "$PUBLICATION" \
  --otatools "$IMAGES/otatools.zip" --source-root "$SOURCE" \
  --keys "$TRUST/inventory.json" --scratch "$SCRATCH" --output "$RESULT"
```

Verification includes:

- Every APK, APEX container and APEX payload against its assigned external public identity.
- Compressed APEX containers, their original APEX and enclosed APKs.
- Published partition bytes against signed target-files, including unpacked logical partitions.
- Native AVB verification through every declared partition and rollback chain; disabled verification is rejected.
- OTA whole-file, payload and payload-metadata signatures using the platform checker and payload verifier.
- OTA build metadata and payload properties against the accepted build and final bytes.
- Native payload application against signed target-files, including the verified source for deltas.
- Factory and release-record SSH signatures against the external authority and distinct namespaces.
- Factory contents against the verified image archive.

A successful result records `artifact_signatures_verified: true`, public identities, artifact hashes
and per-run provenance. Production verification rejects AOSP public test keys, even when they
appear in the supplied inventory. Qualification requires an explicit `--qualification` flag.

## Verifier fixtures

Tests generate disposable keys in temporary storage and remove them. No certificates, public keys
or private keys are stored in this repository. Native fixtures cover corrupt signatures, unsigned
files, wrong keys, wrong roles and public test-key rejection.

```sh
DIAMANEOS_SIGNING_NATIVE_SOURCE="$SOURCE" \
DIAMANEOS_SIGNING_OTATOOLS="$IMAGES/otatools.zip" \
PYTHONPATH=src "$VENV/bin/python" -m unittest tests.signing.test_native_signatures
```

The cryptographic implementations are the build's `apksigner`, `avbtool`,
`check_ota_package_signature`, `delta_generator` and payload tooling. Policy code compares their
results with external trust material; it contains no signature parser.
