# DiamaneOS host tooling

The host tools are Python 3 programs and do not require an Android source
checkout or compilation. The device runner itself uses only the Python
standard library. Schema conformance tests use the pinned development
dependencies in `requirements-dev.txt`.

## Prepare a development checkout

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
```

The accepted test host runs a detached, root-owned revision from
`/opt/diamaneos/tools`; the unprivileged runner must not be able to modify that
checkout. Deploy an exact reviewed commit using the procedure in
`deploy/test-host/README.md`, then run the same tests as the runner account.

OS compilation uses a separate online build host and unprivileged build
identity. The builder setup and trust boundary are documented in
[`deploy/builder/README.md`](../deploy/builder/README.md). Production release
keys never enter that host. Host acceptance requires an actual clean build in
addition to hardware, capacity and thermal checks. Remote-power features are
deployment-specific.

The builder's resource qualification is intentionally narrower than a clean
build. Its passing report establishes the observed host resources, bounded
load behavior, ECC counters, storage health and management configuration. It
does not establish source compatibility, reproducibility or release
eligibility. Whole-system AC power and acoustic results also remain unmeasured
unless their dedicated external meters were actually used.

## FP6 component decisions

The FP6 component model and generated artifact-closure gate are documented in
[`COMPONENTS.md`](COMPONENTS.md). The model assigns every known source family
and blocker to one component owner, keeps investigation states separate from
accepted public dispositions, and rejects unmapped build outputs. It is a
prerequisite record for later device-input generation, not evidence that a
device build exists or works.

## Pinned Android environment

`config/build-environment.json` is the build-input authority for FP6-033. It
binds the selected stable GrapheneOS tag, tag object, peeled manifest commit,
official signer-list hash, signer identity, tagged `default.xml`, canonical
1,057-project commit map, the GPG-verified `repo` v2.65 tag object/commit, host
packages, external tools and project/device input hashes. The Debian `repo`
2.54 package is only the launcher; the self-updating implementation is a
separate input and is pinned to commit
`35bbf701d04de5c6a71937279bc3d16f6ce36808` instead of its moving `stable`
branch. The selected `2026091000` release is explicitly published for generic
and other targets. A branch name, a GitHub verification badge or an existing
download cache is not a substitute for the local signature checks.

The project-selected Debian 13 host is newer than the operating systems listed
by the upstream build guide. This is a declared compatibility deviation. The
reference builder closed that deviation for this exact environment with clean
generic qualification run `generic-qualification-20260919T170522Z`. The run
used signed tools commit
`744c347cbbacb8d24ed9e985078798d0e2a2a5e5`, environment
`fp6-android17-grapheneos-2026091000-debian13-v4`, target
`sdk_phone64_x86_64-cur-userdebug` and an initially empty source-local `out`
directory. Preflight and postflight retained the same 1,057-project map and
runtime build identity; the result and 66 top-level products were hash-bound.
This acceptance proves only the reference host and generic source path. It does
not prove that the unfinished FP6 target builds, boots or meets the device
requirements, and another host or changed environment still needs its own
qualification.

Full preflight also verifies the gaps between Git projects: undeclared files or
symlinked source directories cannot supply optional Make includes. Manifest
`copyfile` contents and `linkfile` targets must match the signed release, and
the manifest checkout itself must be at that release commit. The declared
output container and `.repo` metadata are outside this source-layout traversal;
individual project content is still checked separately with Git. The current
flat-manifest environment does not permit local-manifest overlays. A future
composed environment must bind its exports as well as its project commits.

The portable cold-environment check requires no source tree or private cache:

```sh
bin/diamaneos build preflight --inputs-only
```

It validates all committed input records and emits a declared build identity.
Changing a tag, project-map pin, tool record, stock input or patch inventory
changes that identity. It deliberately reports the generated FP6 device-input
manifest as pending until the generator has produced and verified it.

Run source synchronization as the unprivileged build identity from an exact,
reviewed tools checkout:

```sh
export DIAMANEOS_EXPECTED_TOOLS_COMMIT=REPLACE_WITH_REVIEWED_40_HEX_COMMIT
export DIAMANEOS_THERMAL_CHECK=/absolute/path/to/qualified-thermal-check
deploy/builder/sync-pinned-source /opt/diamaneos/tools
```

The script downloads the current official signer list, verifies its pinned
hash, initializes only `refs/tags/2026091000`, fixes the internal `repo`
implementation to the signed v2.65 tag commit, verifies the `repo` tag through
the launcher's GPG keyring, verifies the GrapheneOS tag with OpenSSH, runs
`repo sync -j8` without a fallback, and then invokes the full preflight. Any
fetch, signature, revision, clean-tree or package mismatch terminates the
operation. The initial recipe also requires an empty output root. Preserve the
complete stdout/stderr and its SHA-256 as private build evidence.

Before downloading source, the same script runs `--host-only` to enforce the
exact package/tool pins, memory and free-space floors, separated workspace,
empty clean-build output and the configured live thermal-safety preflight. A
host mismatch therefore fails before consuming a large sync.

The source revision uses a Yarn v1 lockfile. Prepare Yarn as the build identity
with the pinned Node/Corepack on PATH, before source sync:

```sh
"$TOOLS_ROOT/deploy/builder/prepare-yarn" "$WORK_ROOT"
```

This derives the version and SHA-512 from the environment's npm integrity,
requires a new Corepack cache, and passes the hash to Corepack's acquisition
check. It refuses an existing cache rather than trusting previously extracted
bytes. Preserve a failed cache as evidence and investigate before retrying with
a clean workspace. The helper installs shims in `$WORK_ROOT/toolbin`; builds
use `$WORK_ROOT/.cache/corepack`. The source lockfile remains unchanged.
See [Corepack's integrity-qualified package-manager references](https://github.com/nodejs/corepack#when-authoring-packages).

The accepted workspace separates source-controlled, cache and generated state
as follows:

- `src/grapheneos-2026091000` contains only the repo checkout;
- `cache` contains reusable downloads and compiler cache, which may improve
  performance but cannot define a release input;
- `src/grapheneos-2026091000/out` is Android's standard clean output directory
  and is never imported from another host.

Use `OUT_DIR` and `CCACHE_DIR` to enforce those boundaries. The first generic
qualification run starts with an empty output root. Invoke the committed
`deploy/builder/run-generic-qualification` wrapper through its reviewed service
rather than exporting an absolute or parent-relative output path by hand.
Soong rejects paths which escape the source root and the pinned Siso resolves
its configuration repository relative to that root. The wrapper therefore
uses Android's standard source-local `out` path:

```sh
SOURCE_ROOT="$WORK_ROOT/src/grapheneos-2026091000"
OUTPUT_ROOT="$SOURCE_ROOT/out"
cd "$SOURCE_ROOT"
export OUT_DIR="$(realpath --relative-to="$SOURCE_ROOT" "$OUTPUT_ROOT")"
export CCACHE_DIR="$WORK_ROOT/cache/ccache"
source build/envsetup.sh
lunch sdk_phone64_x86_64-cur-userdebug
m
```

`OUT_DIR` must resolve exactly to the declared source-local `OUTPUT_ROOT` before
the build starts. Do not replace it with an absolute or `../` path. The service
also binds the exact reviewed tools commit through
`DIAMANEOS_EXPECTED_TOOLS_COMMIT`, requires the pinned source-sync unit, invokes
the configured `DIAMANEOS_THERMAL_CHECK`, and denies network access during
compilation.

Before this or any later build, the full preflight and configured thermal-safety
check must pass. A clean generic result does not authorize
production signing material on the online builder. The FP6 release-purpose
preflight remains fail-closed until the generated exact-stock device-input
manifest and FP6 product target are verified.

## Downstream manifest overlay

The accepted environment initializes the authenticated GrapheneOS release tag
directly and contains no DiamaneOS overlay projects. The separate
`platform_manifest` repository is a minimal local-manifest overlay; it does not
copy the upstream `default.xml` or repeat upstream project revisions.

When the first real DiamaneOS repository or fork is ready, create a new build
environment that binds the reviewed overlay commit and file digest, installs
that overlay under `.repo/local_manifests` before `repo sync`, and records the
new resolved project-map digest. Re-run the affected source and build
qualification. Do not modify this accepted environment in place, add planned
empty repositories or use the overlay to freeze revisions already supplied by
the signed GrapheneOS release.

## Signing handoff

The online builder stops at unsigned target-files and otatools. Signing roles,
target-files inventory validation and the disposable qualification path are
defined in [`SIGNING.md`](SIGNING.md). Never copy a production key or signer
token to the builder to make a release command convenient. Full and
incremental OTA generation are signing operations because they sign both the
payload and package; they run in the reviewed offline signing workflow.

The accepted generic build may be extended to produce a generic target-files
package for disposable role qualification. That downstream run must preserve
the existing pinned source identity, use only newly generated dummy keys and
state explicitly that it proves neither FP6 support nor release eligibility.
Its package inventory cannot substitute for the future FP6 `user` target-files
inventory.

## Device-suite interface

Official Android compatibility suites use the separate version-bound adapter
documented in [`COMPATIBILITY.md`](COMPATIBILITY.md). It shares the private
role map and rig interlocks but does not treat a staged smoke suite, stock
harness trial, debug VTS companion or CTS-on-GSI run as final compatibility
evidence.

Device suites are reviewed JSON data under `tests/device/suites/`. They select
only adapters implemented in `src/diamaneos_tools/test_runner.py`; suite text is
never evaluated as a shell command. The initial `smoke` suite can invoke only
the runner's exact read-only ADB allowlist.

Every case declares:

- a stable case ID and affected requirement IDs;
- one stage: `inspect`, `smoke`, `security` or `destructive`;
- preconditions, an oracle and the expected result;
- required or optional applicability with a reason for an optional skip; and
- a bounded per-command timeout.

Adding an ADB command requires changing the source allowlist and tests, not
only editing suite JSON. The separate `adb-temp-file-roundtrip` adapter uses a
generated path under `/data/local/tmp`, verifies the generated payload and
attempts removal plus absence verification even after timeout or interruption.
Its host-side input is retained as raw evidence; its device copy is test data
and must not remain. The committed `smoke` suite does not select this mutating
adapter.

Destructive entries use the `installer-runbook` adapter. The v1 runner enforces
explicit destructive mode and a disposable target but deliberately does not
execute that adapter. Flash and wipe recipes remain owned by the reviewed
installer/runbook.

The current executable source binding is the official `adb` command-line
interface over the accepted USB path. Fastboot and UI-automation adapters are
intentionally absent until a reviewed suite defines their target, timeout,
cleanup and evidence contracts.

The run envelope is defined by `schemas/test-run.schema.json`. Changing a
required field or meaning requires a schema-version change and migration or
explicit rejection of old retry checkpoints.

## Private device mapping

Real execution binds the non-identifying public role to exactly one private ADB
serial. Keep this JSON outside public repositories with directory mode `0750`
and file mode `0640` or stricter:

```json
{
  "schema_version": 1,
  "devices": [
    {
      "role": "harness",
      "adb_serial": "<private-adb-serial>",
      "disposable": true
    }
  ]
}
```

Roles and serials must both be unique. `disposable` authorizes only the
runner's destructive-stage gate; it does not itself flash, wipe or approve a
particular candidate.

## Lifecycle and extension rules

One non-blocking file lock serializes each role beneath the selected private
output root. A run ID creates `<run-id>.partial`, checkpoints `result.json`
atomically after each completed case, then renames the directory to `<run-id>`.
Existing partial or final names are immutable collisions. A failed or
interrupted case stops later execution; retained results name the cases to
rerun. `--rerun-from` verifies the suite/candidate identity, target/build
identity and referenced raw-file hashes before selecting unresolved cases.

Raw command streams can contain private device state and stay in the private
run directory. The structured report contains the target role and relevant
model/build/firmware data but no ADB serial. Fixture, emulator, static-review
and real-device results are explicitly different evidence kinds; `userdebug`
results are labelled diagnostic evidence and never substitute for a final
`user` build.

Do not install a scheduled `device-regression` timer merely because the local
command exists. A timer becomes appropriate only after its exact suite,
candidate trigger, maximum runtime, private paths and owner failure route have
been exercised on the accepted host. Destructive stages always retain their
operator gate.

VTS source discovery is not package qualification. The pinned VTS build target
and launcher are documented in [compatibility preparation](COMPATIBILITY.md#source-bound-vts-preparation);
retain a built archive hash and generated inventory before approving execution.

## Stage stock images for vendor discovery

Use the pinned Fairphone device/platform sources as the primary hardware input.
Reference-ROM trees are optional investigation aids and are not inherited by
default. The stock image staging step authenticates the selected factory ZIP
and individual image hashes from `config/fp6-stock-image-recipe.json`:

```sh
bin/diamaneos vendor stage --archive /absolute/path/to/factory.zip \
  --output /absolute/path/to/private-image-workspace
```

The workspace contains immutable-by-contract `generations/<recipe-digest>`
directories and an atomically selected `current` symlink. Identical inputs reuse
and reverify the generation without changing that pointer. Missing/wrong images,
unsafe archive members and edited generated contents fail; a failed extraction
does not replace the previous published generation. Concurrent publishers use
one workspace lock. Store the workspace outside source repositories.

This stage permits only system-container, boot/ramdisk, DTBO and AVB images;
userdata, persist, device-unique provisioning and modem state images are excluded.
It executes no factory script or phone command. Image staging is not a generated
vendor product: filesystem extraction, per-file classification/dependency closure,
notices and actual product-graph verification must follow before accepting one.

## Materialize selected stock files

After filesystem extraction and component review, use a recipe conforming to
`schemas/vendor-files.schema.json`:

```sh
bin/diamaneos vendor generate --recipe /absolute/path/to/selected-files.json \
  --inputs /absolute/path/to/extracted-partitions \
  --output /absolute/path/to/private-generated-vendor
```

The input directory contains partition directories named `vendor`, `odm`,
`system`, `system_ext` or `product`. Each selected regular file has an exact
hash, length, stock origin, component owner, inventory reference, dependency
list, purpose and hash-bound notices. The recipe binds the component-model hash
and its selected factory identity. Recipe creation must use the authenticated
stock discovery: this command checks selected bytes, not the origin of an
arbitrary extraction directory or whether the declared dependency list is
complete. Review runtime, linker-namespace, init, VINTF and firmware dependencies
before accepting a product closure.

Generation validates the source/environment-bound component model and the
complete declared artifact mapping before publication. Missing or undeclared
dependencies, wrong bytes and unclassified outputs fail without replacing the
previous `current` generation. `--public` requires accepted public component
dispositions; private-bringup allowances cannot satisfy it. The optional
`--model`, `--sources` and `--environment` arguments select reviewed input files.

The generation contains `files/`, content-addressed `notices/`, `manifest.json`
and `component-closure.json`. Original UID/GID, mode, SELinux label and file
capabilities remain image metadata in the manifest; host files are ordinary
non-executable files. The command does not apply privileged host ownership or
capabilities. Symlink traversal, symlink inputs and special files are rejected.
A subsequent image/product assembler must explicitly implement symlink and
metadata installation; this regular-file stage does not produce Android build
rules or establish a bootable product. Compare the two generated manifests for
selection, byte, dependency and metadata changes; do not hand-edit outputs.

## FP6 product adaptation boundaries

Use the pinned Fairphone `fps`, common and Qualcomm platform configurations to
identify hardware inputs. Their stock product is the target side of a QSSI
split: `volcano.mk` disables system/product generation and skips OTA packaging.
Those settings cannot serve as a complete GrapheneOS-derived product unchanged.
The stock common file also adds manufacturing and diagnostic services; assess
their init triggers, permissions, HAL declarations and hardware dependencies
before including or removing them.

The selected GrapheneOS `build/make` provides the generic phone inheritance:
`core_64_bit_only.mk`, `generic_system.mk`, `handheld_system_ext.mk`,
`telephony_system_ext.mk`, `aosp_product.mk`, `handheld_vendor.mk` and
`telephony_vendor.mk` under `target/product/`. This is the source binding for
product assembly, not a claim that an FP6 product graph has passed. Do not
inherit the Pixel device-common file: it adds Pixel kernel paths, Trusty,
pVM firmware and device-specific init/overlays. Keep the GrapheneOS
`OFFICIAL_BUILD` flag unset; in this release it adds the upstream OS updater.
A DiamaneOS release identity must not reuse that flag as an update-policy switch.

Preserve distinct API identities from the selected stock input: the device's
first API level is 35, while the board/vendor API and VNDK are 34. A newer
framework version does not advance those hardware compatibility declarations.
Do not copy a platform security-patch value onto unchanged vendor or boot input.

Resolve vendor dependencies by ABI and linker namespace, not filename alone.
Stock contains AArch64 executables alongside DSP firmware ELF files and dormant
init declarations whose executables are absent. A declaration alone does not
justify installing a service or its surrounding factory configuration.

The pinned platform includes `com.android.vndk.v34` from
`packages/modules/vndk` and the matching `prebuilts/vndk/v34` snapshot. The four
stock VNDK 34 LLNDK/core/private/same-process library lists match this snapshot.
Stock camera, graphics, sound-trigger and audio dependencies include libraries
inside that APEX; a scan limited to partition `lib64` directories is incomplete.
The tethering APEX similarly supplies
`libcom.android.tethering.connectivity_native.so`. Bind the selected APEX and
its exported interfaces in the product graph, preserve required notices, and
verify the candidate linker namespaces and ABI. Matching export lists do not
prove binary equivalence or runtime compatibility. Do not import the stock
Google tethering package to satisfy a filename match.

The Fairphone kernel wrapper prepares kernel, module, UAPI and DT artifacts as
a set. Its `consolidate` variant includes test/torture modules and is not a
production configuration by default. The wrapper skips the ABI target, so its
successful exit alone cannot establish KMI acceptance: retain an explicit ABI
check and reconcile the configured module outputs and both boot/recovery load
lists. Source-built output still needs symbol, signature, configuration,
firmware and hardware validation.

## Pinned downstream source composition

An environment may declare an optional `composition` object with the exact
`overlay_revision`, `overlay_sha256`, `project_count` and `project_map_sha256`.
The revision records the reviewed manifest repository commit; the content
hash authenticates the installed `.repo/local_manifests/diamaneos.xml` bytes.
The map/count describe the entire composed checkout. The `upstream` record
continues to bind the independently authenticated GrapheneOS release.

Only additive HTTPS remotes and explicitly resolved projects are supported.
Development overlays may put an Android branch on the owned remote and let
projects inherit it. An immutable environment then records `resolved_revisions`,
a map from each moving project's checkout path to its exact 40-character commit.
Obtain those commits from the reviewed `repo manifest -r` output; changing a
resolution requires a new environment identity and composed project-map digest.
Preflight performs no network resolution and rejects missing, unused or moving
resolution values. Exact upstream revisions need no redundant map entries.
Release manifests pin project revisions; development manifests track branches.
Upstream replacements, nested/overlapping projects, manifest includes,
copy/link exports in the overlay, extra local manifests and symlinks are
rejected. Full preflight checks the composed project revisions, clean trees,
remote definitions and original upstream exports. Environments without this
object still reject local manifests.

A new composition requires a new environment identity and source qualification.
The source-sync adapter accepts a reviewed configuration and overlay repository:

```sh
deploy/builder/sync-pinned-source "$TOOLS_ROOT" "$WORK_ROOT" \
  config/build-environment-fp6.json "$OVERLAY_ROOT"
```

Its ordinary tools-commit and thermal-check environment bindings still apply.
The overlay checkout must be clean at the declared commit. After synchronization,
moving projects are detached at the environment's exact resolutions, refusing
local changes; the original branch-tracking overlay remains installed. Full
preflight checks the resulting clean composed map. This prepares source only;
it does not qualify the product graph or authorize using old build evidence.
`DIAMANEOS_SOURCE_REFERENCE` may name a retained repo workspace for Git object
reuse. Keep that object store available while referenced checkouts depend on it;
an object reference is neither an independent backup nor build evidence.
Generated hardware inputs must also receive their own declared provenance;
source composition alone does not accept a device build.
