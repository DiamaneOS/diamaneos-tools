# Build reference

To build DiamaneOS, follow [BUILDING.md](BUILDING.md). This page explains how
the build works and why: the build commands and their records, the pinned
environment, the generated inputs and packaging. It also covers developing the
host tools themselves.

The host tools are Python 3 programs and do not require an Android source
checkout or compilation. Schema conformance tests use the pinned development
dependencies in `requirements-dev.txt`.

## The build commands

`diamaneos build all` runs six steps in one workspace directory
(`~/diamaneos-build`, or `--workspace`, or `DIAMANEOS_WORKSPACE`). Each step
can also run on its own; it then always runs again, and it refuses to start
while an earlier step it consumes has not run or is out of date.

The environment pins the current development line: the latest pushed
`android17` commit of every DiamaneOS repository, composed on the signed
GrapheneOS release. It is not a release and not phone-tested as a whole.

| Step | What it does | What it checks and records |
| --- | --- | --- |
| `sync` | Downloads GrapheneOS's signer list, runs `repo init` at the signed release tag with the pinned `repo` tool, installs the DiamaneOS manifest overlay at its pinned revision, runs `repo sync` and detaches every DiamaneOS project at its pinned commit. | The signer list hash, the `repo` tool and release tag signatures before any source is fetched, then the full source preflight: the composed project map, clean trees, no undeclared files. Records the project map. |
| `kernel` | `kernel prepare` (network) and `kernel build` (network off). | Everything `kernel build` checks (source pins, patch diffs, the kernel policy, module placement, the deny list, symbol rules, signatures). Records the kernel run. |
| `vendor` | Builds `aapt2`, `simg2img`, `lpunpack` and `debugfs_static` from the synced source (generic lunch target, network off), downloads the Fairphone factory package from its official host, then `vendor stage`, `vendor extract` and `vendor product`. | The package's size and SHA-256, each staged image and each extracted file against the recipes. The image tools are accepted because they come from the pinned source; their hashes are recorded in the extraction identity. |
| `android` | Installs the generated vendor and kernel trees, then `lunch FP6-cur-<variant>` and `m` with network off. | The full preflight before and after the build, including the generated-input descriptor. Records the target-files archive and the build identity. |
| `package` | Exports the partition images from the target-files archive, builds `super.img` from the same archive, makes the wipe images and writes `build.json` and `SHA256SUMS`. | The target-files hash, the wipe images against the device fstab, the stock FRP image and the stock partition table. |
| `verify` | Checks the exported set. | See below. Writes `<build>.verify.json` next to the image directory. |

**State and resume.** Each step writes `state/<step>.json` with the digest of
its inputs (the hashes of the configs, and only the parts of
`config/fp6-build.json`, it reads, its code, and the outputs of earlier
steps), its outputs and its log. `build all` skips a step whose input digest
is unchanged and whose outputs still verify; a later step runs again only
when an earlier step's outputs changed. Each step prints its log path when it
starts. A second command on the same workspace fails at once
(`.workspace.lock`). `--dry-run` prints every command and changes nothing.
Any failure is recorded in the step's state with a plain message.

**Shallow sync.** `build sync --shallow` fetches only the pinned commits. The
workspace remembers the choice (`state/shallow`), so a later `build all` stays
shallow; the tree and its preflight are the same, so the choice is not part of
the digest.

**Host check.** Before running, the command checks the host for the steps it
will run: Linux on x86_64, Python 3.11 with `jsonschema` (vendor generation
validates its recipes with it), the commands each step uses (`modinfo` and
`modprobe` are also looked up in `/usr/sbin` and `/sbin`), RAM (the 32 GiB
floor allows 2 GiB for what firmware and the kernel reserve), disk (only for
steps whose output does not exist yet), a case-sensitive filesystem and, for
compile steps, unprivileged user namespaces with util-linux 2.38 or newer.
Differences from the environment's pinned package versions are recorded, not
fatal.

**Network.** Only `repo`, `git fetch`, the kernel preparation and the two
downloads use the network. Downloads use HTTPS only, stop at the expected
size, and refuse redirects to another host or to plain HTTP. Compilation (the
kernel, the image tools and Android) runs inside `unshare --user
--map-current-user --net`, so the build keeps its own user id and has no
network, and compile commands lose variables that lead to local agents and
buses (`SSH_AUTH_SOCK`, `DBUS_*`, `XDG_RUNTIME_DIR`, `DOCKER_HOST` and
similar). Unix sockets in the filesystem stay reachable; see the threat model.
If the host has no unprivileged user namespaces the command stops;
`--allow-network` builds anyway and records `network_isolation: off` for the
kernel, vendor and Android steps. If `DIAMANEOS_THERMAL_CHECK` names a program,
it runs before every compile. Temporary files go to the workspace (`TMPDIR`),
never to `/tmp`.

**Build identity.** The source identity is a digest of the environment file,
the source project map, the vendor and kernel inventories, the variant and the
build parts of `config/fp6-build.json`. `BUILD_NUMBER` is `test.` and its
first 12 digits; `BUILD_DATETIME` is the newest committer time among the
pinned sources (the build stops if it cannot read them); `BUILD_USERNAME` and
`BUILD_HOSTNAME` are fixed. The image set's build identity adds the build
number, the network isolation, the tools commit and the target-files hash.
The image directory is `<date>-<variant>-<build identity>`, and an existing
directory is reused only when its record names the same target-files and
identity.

**Generated-input descriptor.** `build inputs` (called by the `android` step)
writes `.repo/diamaneos-generated-inputs.json`. It binds
`vendor/fairphone/FP6` and `device/fairphone/FP6-kernel` to the environment
file's hash, the recipe digests the generations recorded themselves (the
vendor provenance, and the kernel run's preparation, packaging recipe and
policy reports) and their complete inventories. Installation and the full
preflight require those digests to equal this checkout's recipes, and accept
the two directories only while every file matches its inventory; any other
file outside the projects still fails the preflight. A tree that no longer
matches is replaced explicitly (the old one moves to
`.repo/diamaneos-previous-inputs/`); `sync` moves stale trees aside.

**Packaging.** The target-files archive is the image authority: its `IMAGES/`
were made together by the build, so the AVB descriptors match them, and
`super.img` is built from the same archive. Nothing is repacked. DiamaneOS
builds with its own fork of GrapheneOS's `build/make`, which differs in one
change: when the Android release tools rebuild boot, init_boot and recovery
from target-files, they keep the device's zero OS version and patch level in
the image headers, as the Make rules do (the versions live in the AVB
properties). `verify` checks the headers. The target-files archive is also the
unsigned input for the offline signer ([SIGNING.md](SIGNING.md)).

**Wipe images.** `userdata.img` is 4 MiB of zeros, as in Fairphone's factory
package: it destroys the old filesystem and first boot formats `/data` with the
phone's own size and settings. `metadata.img` is an empty f2fs filesystem of
the partition's size, made with the synced source's `make_f2fs` with a fixed
UUID, time and seed. `frp.img` equals Fairphone's `frp_for_factory.img`.
`misc.img` is zeros of the stock partition table's misc size, which clears
misc as Fairphone's factory flash does; `build package` checks the size
against the factory package's partition table when the package is in the
workspace. Nothing is cleared with `fastboot erase`. The wipe has not yet been
tested on a phone (`wipe.validated` in `config/fp6-build.json`).

**Verify.** Generic checks: `SHA256SUMS`; the record (a test build that must
never be locked, matching hashes); test-keys in every fingerprint; the AVB
chain with the test key and the published layout (recovery 1, vbmeta_system 2,
boot 3, init_boot 4, flags 0, pvmfw in vbmeta_system); zero OS fields in the
boot-family headers with the versions in AVB properties; `super.img` holding
exactly the logical images; `validate_target_files` and
`check_target_files_vintf`; the kernel run named in `build.json` in boot,
vendor_boot and dtbo; module placement and load lists against that run, no
denied or unsigned module; every selected stock file arriving with its
generated bytes; no permissive domain beyond the variant's; the bootconfig; no
pre-trusted adb key; the wipe images. Then every rule in
`config/fp6-image-checks.json`: the device checks the private build scripts
used to carry, each with the reason it exists and, where it matters, the
variants it applies to. The report carries the image set's `SHA256SUMS`
digest.

**Flash steps.** `diamaneos flash-steps` prints commands only for a test build
whose set matches its `SHA256SUMS` and whose verify report belongs to that set
and passed. The flash order, slot and wipe images come from
`config/fp6-build.json`. The fastbootd fallback writes every logical
partition, because a stalled `super` flash may already have written the new
layout.

**Reproducibility.** The same tools commit gives the same source map,
generated inputs and source identity. Kernel images and modules differ between
builds, because each kernel build makes a new module-signing key: the kernel
embeds its certificate and every module carries a signature, so boot,
vendor_boot, the DLKM images and the vbmeta images that describe them differ
too. The other partitions are expected to be identical; a second host's build
has to show it. A modified tools checkout is recorded as not reproducible.

**Development lanes.** Private test builds use the same commands.
`--environment FILE` selects a lane environment (a copy of the FP6 environment
with the lane's resolved commits and project map). `--objects-from DIR` adds
local commits before checkout: `DIR/objects.json` maps project paths, and
`manifest` for the overlay, to git bundles in `DIR`. The pinned commits and
the preflight still decide what is built. `DIAMANEOS_BUILD_NUMBER` overrides
the build number and `DIAMANEOS_KERNEL_REFERENCE` lets `kernel prepare` borrow
Git objects from another workspace.

## Prepare a development checkout

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
```

## FP6 native integration

The FP6 product resolves against the selected Android 17 framework with source
boot control, power, thermal, lights, vibrator, USB and health services. Native
compilation of these services and the selected display/credential ELF inputs has
passed. These are integration checks, not an accepted ROM or runtime result.
The kernel uses 4 KiB pages; the product explicitly selects that page size while
keeping prebuilt alignment and ELF checks enabled.

The FP6 product environment selects `config/patches-fp6.json` for its Android
GPT/UFS, boot-control and power adaptations. It records exact upstream/derived
commits, changed files and canonical full-index diff hashes. The generic
environment and kernel preparation use `config/patches.json`; advancing an
Android HAL must not invalidate an unchanged kernel preparation. The
environment's `project_inputs` identifies its ledger.

The source power HAL dynamically loads the stock performance client. Its
performance/thermal backend is a separate explicit input family in the component
model; allowing that family for private bring-up does not allow replacing the
published HAL wrappers with prebuilts. The generated selection must include its
exact runtime dependencies, configurations, init identities and notices, with
native policy and device behavior checked separately.

## Pinned Android environment

The current metadata identity and input pins are recorded in
[`config/build-environment.json`](../config/build-environment.json). The composed
FP6 candidate identity and its source, device and generated-input recipe pins are
recorded in
[`config/build-environment-fp6.json`](../config/build-environment-fp6.json).
Read `environment_id` and the referenced records from the selected configuration;
these identifiers are build-input identities, not public OS release versions.

The FP6 configuration binds the source-owned device policy, selected native
services, performance inputs and GPU firmware dependencies. The product selects
the generic first-stage ramdisk, GKI v4 headers and the published boot/recovery
AVB chains, builds protected VM firmware (`pvmfw`) from source into the system
AVB chain, installs the vendor module blocklist in both the first-stage ramdisk
and `vendor_dlkm`, and uses a 48-bit virtual address space kernel. UFS access
remains scoped to the boot-control service. Exact revisions and project-map
digests bind the selected workspace.

`config/build-environment.json` is the build-input authority for FP6-033. It
binds the selected stable GrapheneOS tag, tag object, peeled manifest commit,
official signer-list hash, signer identity, tagged `default.xml`, canonical
project commit map, the GPG-verified `repo` v2.65 tag object/commit, host
packages, external tools and project/device input hashes. The Debian `repo`
2.54 package is only the launcher; the self-updating implementation is a
separate input and is pinned to commit
`35bbf701d04de5c6a71937279bc3d16f6ce36808` instead of its moving `stable`
branch. The selected `2026091000` release is explicitly published for generic
and other targets. A branch name, a GitHub verification badge or an existing
download cache is not a substitute for the local signature checks.

The project-selected Debian 13 host is newer than the operating systems listed
by the upstream build guide. This is a declared compatibility deviation.

Full preflight also verifies the gaps between Git projects: undeclared files or
symlinked source directories cannot supply optional Make includes. Manifest
`copyfile` contents and `linkfile` targets must match the signed release, and
the manifest checkout itself must be at that release commit. The declared
output container and `.repo` metadata are outside this source-layout traversal;
individual project content is still checked separately with Git. The current
flat-manifest environment does not permit local-manifest overlays.

The portable cold-environment check requires no source tree or private cache:

```sh
bin/diamaneos build preflight --inputs-only
```

It validates all committed input records and emits a declared build identity.
Changing a tag, project-map pin, tool record, stock input or patch inventory
changes that identity. It deliberately reports the generated FP6 device-input
manifest as pending until the generator has produced and verified it.

The FP6 preflight also requires the generated-input descriptor described under
[the build commands](#the-build-commands).

## Downstream manifest overlay

The accepted environment initializes the authenticated GrapheneOS release tag
directly and contains no DiamaneOS overlay projects. The separate
`platform_manifest` repository is a minimal local-manifest overlay; it does not
copy the upstream `default.xml` or repeat upstream project revisions.

An environment that uses the overlay binds the reviewed overlay commit and file
digest, installs that overlay under `.repo/local_manifests` before `repo sync`,
and records the new resolved project-map digest; re-run the affected source
and build qualification. Do not modify an accepted environment in place, add
empty repositories or use the overlay to freeze revisions already supplied by
the signed GrapheneOS release.

## Upstream tracking

[`config/forks.json`](../config/forks.json) lists every upstream the build uses.
`forks` are the repositories DiamaneOS forks and patches: the manifest overlay
forks and the kernel forks in `config/patches.json`. A fork exists only where
DiamaneOS changes the code; each follows the CodeLinaro release branch of the
selected Qualcomm release. A fork that stops carrying a needed change leaves
both files, its project is pinned unmodified in the source plan, and
`config/repositories.json` marks it `retired` while it is still published.
A retired fork may be deleted; its entry then goes, and builds
whose manifests pinned it can no longer be synced from GitHub (the five kernel forks
retired on 2026-09-27 were deleted that day). A `follow_note` says why a fork that ships nothing
is still needed, for example a target name other projects depend on. `sources` are pinned inputs used unmodified: the
GrapheneOS release, the repo launcher, Fairphone's source manifest, the
Qualcomm SELinux policy, the stock factory image and the platform repositories
the manifest overlay takes straight from CodeLinaro or Fairphone. Each names the file and
field that hold its pin, so the registry never repeats a revision. `newer`
patterns name the branches or tags that would supersede a followed reference,
such as Fairphone's next `odm/rc/target/<android>/fp6` branch or the next
CodeLinaro release tag.

```sh
bin/diamaneos forks check           # remote refs only; exit 1 when something moved
bin/diamaneos forks status --fetch  # commits behind and patches carried, per fork
bin/diamaneos forks update <id>     # rebase our patches into update/<date>-<commit>
```

`check` downloads no history. It reads each remote's branch and tag names and
answers "is this commit already ours?" locally, without fetching missing
objects into partial clones. States: `current`, `update-available` (the followed
branch moved), `newer-release` (a newer branch or tag exists), `pinned-commit`
(the entry follows no branch or tag yet), `manual-check` (not a Git source) and
`error`. Adopting any update is a reviewed, signed change: rebase, update the
pins (`config/patches.json`, the kernel manifest, the build environment), run
the tests and the kernel layout checks, build, then push.

## Signing handoff

The online builder stops at unsigned target-files and otatools. Signing roles,
target-files inventory validation and the disposable qualification path are
defined in [`SIGNING.md`](SIGNING.md). Never copy a production key or signer
token to the builder to make a release command convenient. Full and
incremental OTA generation are signing operations because they sign both the
payload and package.

A generic target-files package for disposable role qualification must preserve
the existing pinned source identity, use only newly generated dummy keys and
state explicitly that it proves neither FP6 support nor release eligibility.
Its package inventory cannot substitute for an FP6 `user` target-files
inventory.

## Generated inputs step by step

The `kernel`, `vendor` and `android` steps run these commands for you. They
stay available for inspection and for work on a single input.

These commands reconstruct the selected factory-derived userspace files and
source-built kernel, modules and device trees. The source repositories contain
recipes and code; generated payloads live in caller-selected work directories.
No command accesses a phone. A passing preparation does not establish a bootable
ROM, production hardening or release acceptance.

Use a Linux x86-64 host as described in [BUILDING.md](BUILDING.md).
Install Python 3 with `jsonschema` (Debian: `python3-jsonschema`), Git, Make, Bash,
Perl, OpenSSL, binutils and kmod (`modinfo`, `modprobe`). Ensure these commands
are on PATH; some distributions install kmod entrypoints under `/usr/sbin`. The kernel workspace
provides its pinned compiler, Bazel, DTC and DT image tools. Keep its source and
outputs on a filesystem supporting case-sensitive names and symbolic links.
Budget at least 100 GiB of free space for fresh kernel preparation and image
extraction, in addition to the full Android checkout/build requirements.

Run commands from the authenticated tools checkout. Set absolute paths:

```sh
TOOLS_ROOT="$PWD"
WORK_ROOT="/absolute/path/to/build-work"
FACTORY_ZIP="/absolute/path/to/FP6.QREL.16.111.0.20260831102426_WS1Q-factory.zip"
IMAGE_TOOLS="/absolute/path/to/extracted-otatools/bin"
SOURCE_ROOT="/absolute/path/to/android-source"
```

`FACTORY_ZIP` is the EU factory archive identified by
`config/fp6-stock-image-recipe.json`. Obtain that exact archive through the
source recorded in `config/fp6-sources.json`. Other regions/builds are not
interchangeable. `IMAGE_TOOLS` is a `bin` directory holding `simg2img`,
`lpunpack` and `debugfs_static` with their sibling `lib64` directory: either an
otatools package whose programs and libraries `config/fp6-image-tools.json`
pins, or the synced source's own host output (`out/host/linux-x86/bin`, as the `vendor` step uses). Pass
`--record-tools` to `vendor extract` for the latter: it records their hashes in
the extraction identity instead of requiring the pins. Every extracted file is
checked against the selected-file recipe either way.

### Extract and generate the vendor product

Set `AAPT2` to the absolute path of the selected Android SDK build-tools `aapt2`.
It decodes the stock carrier configuration resources; its hash is recorded in
the generated provenance. See [carrier integration](CARRIER-INTEGRATION.md).

```sh
"$TOOLS_ROOT/bin/diamaneos" vendor stage --archive "$FACTORY_ZIP" \
  --output "$WORK_ROOT/stock-images"
"$TOOLS_ROOT/bin/diamaneos" vendor extract \
  --super "$WORK_ROOT/stock-images/current/super.img" \
  --image-tools "$IMAGE_TOOLS" --output "$WORK_ROOT/stock-files" [--record-tools]
"$TOOLS_ROOT/bin/diamaneos" vendor product \
  --inputs "$(realpath "$WORK_ROOT/stock-files/current")" \
  --output "$WORK_ROOT/vendor-product" --notice-kind "$NOTICE_KIND" --aapt2 "$AAPT2"
```

`NOTICE_KIND` is the reviewed Android build-system licence classification for
these inputs; the build commands take it from `notice_kind` in
`config/fp6-build.json` (`legacy_proprietary`). It grants no redistribution
right: each user extracts the files from Fairphone's own package.

Staging authenticates the factory ZIP and selected image hashes. Extraction
independently authenticates `super.img`, expands the sparse image, unpacks only
the logical partitions the recipe reads (`vendor_a`, and `system_ext_a` or
`product_a` when stock Java components are selected), and uses the pinned ext4
reader to dump only declared regular files, each from its own partition image.
It checks each file's size/hash, the alias inode/target (an absolute alias
target must stay in its own partition) and the notice archives. It does not
mount a filesystem, run factory scripts or copy device-unique state. The
current extraction contract is specific to these ext4 stock images; it rejects
an unexpected filesystem or another partition (system, odm) rather than
guessing another decoder.

`stock-files/current` contains the regular files, notice file and symlinks
declared in
[`config/fp6-minimal/vendor-files.json`](../config/fp6-minimal/vendor-files.json).
Product generation applies the reviewed source replacements, activation and
configuration derivation. Its complete output inventory is stored in
`vendor-product/inventories/<generation>.json`. Generated provenance distinguishes
original bytes from derived files and retained inputs from installed libraries.
Identical inputs reproduce the same generation; changed or missing inputs fail
before replacing `current`. Scratch raw images are removed after extraction.

### Prepare sources and build the kernel set

```sh
"$TOOLS_ROOT/bin/diamaneos" kernel prepare \
  --workspace "$WORK_ROOT/fp6-kernel"
"$TOOLS_ROOT/bin/diamaneos" kernel build \
  --workspace "$WORK_ROOT/fp6-kernel" --jobs 16 --timeout 7200
```

Preparation uses the source pins in
[`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json), the exact
downstream revisions/diffs in [`config/patches.json`](../config/patches.json),
and the declared link adaptations in
[`config/kernel-workspace-fp6.json`](../config/kernel-workspace-fp6.json). It verifies
tracked and untracked source inputs and writes a resolved Kleaf manifest. It refuses edited
sources or occupied unexpected link destinations. The two absent legacy shell
entrypoints are explicitly excluded; source-directory links using `src="."`
are preserved.

An optional `--reference /absolute/path/to/existing-kernel-workspace` on
`kernel prepare` borrows Git objects from another checkout. That source must
remain available while the new repositories use it. Revision/diff verification
still runs. It shares no generated kernel output, but is not evidence of an
independent acquisition or a second builder. Omit it for a standalone checkout.

The build command runs the non-consolidate GKI/vendor targets, strict common KMI
and explicit common ABI comparison, all queried FP6 external modules (requiring
WLAN and audio), all declared vendor DT projects, and the pinned DT merger. It
packages the module selection and load lists from
[`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json),
which also declares the required merged DTB and DTBO entry counts.
It preserves signed GKI modules, checks other modules' metadata/CRCs after
stripping, verifies signatures against the built-in GKI certificate and checks
selected providers, namespaces, dependencies and compiled GKI protection lists.
It rejects any module on the packaging deny list. The effective GKI and vendor
configurations must pass the production profile (`kernel-config.json`,
`vendor-kernel-config.json`); `--config-profile development` only relaxes that
check to the baseline and does not change the kernel configuration. See
[FP6-KERNEL.md](FP6-KERNEL.md).

Logs and terminal results are in `fp6-kernel/runs/<run>/`. `current` advances to
that run's `candidate` only after all steps pass. Failed runs remain available.
A retry creates a new run and reuses Bazel's completed work; there is no automatic
retry loop. `--jobs` bounds the requested build parallelism. `--timeout` is a
per-command limit, not a limit on the entire multi-step workflow. Bazel runs in
batch mode so its JVM and workers remain in the owned command group; termination
unwinds that group instead of leaving a detached build server. The workspace
lock rejects a simultaneous preparation/build in the same workspace.

This is source reconstruction and development packaging, not a claim of
independent bit-identical release reproduction. No private retained evidence
directory is an input to these commands.

### Install the generated inputs

Hold the Android workspace's operator/build lock and ensure no build or source
sync is running. Then:

```sh
"$TOOLS_ROOT/bin/diamaneos" build inputs --source "$SOURCE_ROOT" \
  --vendor "$WORK_ROOT/vendor-product" --kernel "$WORK_ROOT/fp6-kernel"
```

Add `--replace` to move differing installed trees aside instead of refusing
them. This verifies inventories and installs complete trees at:

- `vendor/fairphone/FP6`
- `device/fairphone/FP6-kernel`

Manifest synchronization alone does not populate those paths. Installation
refuses symlink destinations and differing existing content. Each tree is
published atomically; if interrupted between the two trees, rerun the command.
It verifies an already installed matching tree and completes the missing one.
It writes the generated-input descriptor `.repo/diamaneos-generated-inputs.json`
(see [the build commands](#the-build-commands)), which the full preflight
checks. Do not hand-edit either generated tree.

The `android` step continues with the Android build, `package` with the image
set and `verify` with its checks. A prepared kernel or vendor tree alone does
not authorize flashing.

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

After filesystem extraction, use a recipe conforming to
`schemas/vendor-files.schema.json`:

```sh
bin/diamaneos vendor generate --recipe /absolute/path/to/selected-files.json \
  --inputs /absolute/path/to/extracted-partitions \
  --output /absolute/path/to/private-generated-vendor
```

The input directory contains partition directories named `vendor`, `odm`,
`system`, `system_ext` or `product`. Each selected regular file has an exact
hash, length, stock origin, component label, inventory reference, dependency
list, purpose and hash-bound notices. The recipe names its stock build, region
and factory archive hash, which must match `config/fp6-stock-image-recipe.json`
(`--stock` selects another). Recipe creation must use the authenticated
stock discovery: this command checks selected bytes, not the origin of an
arbitrary extraction directory or whether the declared dependency list is
complete. Review runtime, linker-namespace, init, VINTF and firmware dependencies
before accepting a product closure.

Generation checks the stock identity and every declared file before
publication. Dependencies that are not other selected files, wrong bytes and
unknown notices fail without replacing the previous `current` generation.

The generation contains `files/`, content-addressed `notices/` and
`manifest.json`. Original UID/GID, mode, SELinux label and file
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

Stock is a consistent VNDK 34 vendor. The DiamaneOS vendor is built from the
pinned Android 17 source and has no VNDK version: stock blobs link the current
vendor variants of their VNDK core and same-process libraries, which are
installed in the vendor partition, and reach LLNDK through the platform's
`/system/etc/llndk.libraries.txt`. Do not set `ro.vndk.version` or add a VNDK
APEX for them. With `ro.vndk.version=34` the linker takes LLNDK from the VNDK
34 APEX, so current vendor libraries such as `libbinder` cannot reach newer
LLNDK dependencies (`libapexsupport.so`), and the display HALs fail to link.
The stock VNDK 34 LLNDK/core/private/same-process lists remain the review gate
for which platform libraries a selected blob may use. A symbol a blob expects
but the current library lacks surfaces as a named link failure on the device.
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

HTTPS remotes and explicitly resolved projects are supported. A fork may replace
exactly one upstream project without root exports, at the same path, using an
explicit `remove-project` followed by its replacement. Optional, ambiguous,
unmatched or incomplete removals are rejected. Reviewed project groups are
preserved; the overlay hash binds this metadata as well as the source choices.
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
Generated hardware inputs must also receive their own declared provenance;
source composition alone does not accept a device build.

### Kernel configuration checks

Check the generated kernel `.config` against an explicit policy before packaging:

```sh
bin/diamaneos build kernel-config --config "$KERNEL_CONFIG" \
  --policy config/kernel-policy-fp6.json --profile development
```

`KERNEL_CONFIG` names the effective configuration produced by the kernel build.
The FP6 policy requires a minimum hardening baseline for both profiles. The
`production` profile additionally rejects the declared permissive/debug settings;
the development profile reports those differences without accepting them for
production. Missing required symbols and duplicate assignments fail closed.
The report binds the configuration and policy by hash.

This is a compile-time regression check, not complete production hardening or
kernel acceptance. Module signatures, selected providers, KMI/UAPI, firmware,
device trees and runtime behavior need their own evidence. GKI module protection
is distinct from requiring every vendor module to use the GKI signing key.


Selected-file recipes may also declare `symlinks`. The generator authenticates
link text through directory descriptors without following any link. Each alias
must name a selected regular file in the same partition, and its source and
output targets must correspond. Cycles, chains, traversal, cross-partition links
and links to writable device state are rejected. Alias records and their target
text hashes enter the component closure and `symlinks.json`; no input symlink is
created or followed in the host output tree. The Android packaging step must
consume these declarations to create the image aliases.

### Native FP6 product integration

`config/fp6-minimal/vendor-files.json` declares the selected stock files and
`vendor-elf.json` binds their reviewed dependencies. Generate the Android
integration using the same extracted partition roots:

`AAPT2` is the absolute path of the selected Android SDK build-tools `aapt2`.
Its hash and the extracted carrier-data hashes enter generation provenance;
see [carrier integration](CARRIER-INTEGRATION.md).

```sh
bin/diamaneos vendor product --inputs "$STOCK_FILES" \
  --output "$VENDOR_GENERATIONS" --notice-kind "$NOTICE_KIND" --aapt2 "$AAPT2"
```

`STOCK_FILES` contains the extracted `vendor/` files and, for the reviewed stock
Java components, the extracted `system_ext/` and `product/` files. `NOTICE_KIND` is the
reviewed Android build-system notice classification for those inputs. The
command checks component policy, hashes, dependency edges and activation files,
then publishes a content-addressed tree through `current`. Copy that complete
generation to the otherwise absent `vendor/fairphone/FP6` workspace path under
the build workspace lock. Keep its provenance and recipe with the build record.
Never hand-edit a generated file or substitute a tree from another stock build.

A selected file may declare `runtime_dependencies`: selected files it loads
with `dlopen` rather than through `NEEDED`, each with the exact soname and a
reviewed reason. `vendor-elf.json` mirrors each one as a
`selected-stock-runtime` edge. Such providers join the component closure and
the reachable set and are rendered as `required` modules: installed with their
consumer, never linked. A declaration without its edge, an edge without its
declaration, or a soname that differs from the provider is rejected. The ELF
closure alone cannot see these loads: qseecomd opens its secure-world listener
libraries (`librpmb.so` and others) this way, and without them it exits, the
QSEE KeyMint cannot serve vold and `/data` never mounts.

The renderer retains native source-built interface libraries where declared,
enables native ELF checks and generates init/VINTF packaging with the provider.
Declared are frozen AIDL and HIDL interfaces, the libraries of the source-built
display stack and a few AOSP libraries that stock blobs link (such as
`libdrm`, `libtinycompress` and `libavservices_minijail`). A C++ implementation
library that only a closed HAL uses stays stock: the stock KeyMint HAL keeps its
`libkeymaster_messages`, because a class-layout change would pass the ELF
checks. The same caveat applies where closed stock code links a source-built
display library, such as `libsdmextension` against the source `libsdm*`
libraries and CamX against `libgralloc.qti`; the ELF checks cannot catch a
layout change there either.
It records a narrowly pinned transformation of the performance configuration
that disables optional learning/memory/prekill gates while preserving core
power hints. Original and derived hashes remain distinct. This generation is
also explicit about the runtime roots: the disabled learning/memory plugins,
their meters library and the learning configuration are not selected at all.
Their protobuf runtime is installed only because the sensor stack links it.
Source-interface replacements and uninstalled optional libraries are listed in
generated provenance; retained authenticated inputs are not an
installed-artifact inventory.
This generation is
for private development: its success does not establish public component
acceptance, runtime compatibility or permission to flash.

Device policy and hardware setup are source-owned by `device/fairphone/FP6`.
Matched kernel outputs remain a separate generated input at
`device/fairphone/FP6-kernel`; source composition must pin that artifact set as
well as the repositories. Native module and enforcing USER policy checks pass
for the combined candidate. Native boot, vendor_boot, DTBO and both DLKM images have been built and their
kernel, DT and module payloads checked against the selected inputs. Module bytes
and load-list order survive packaging, including the 60 signed GKI modules.
These are development-key image checks. A complete ROM/recovery build,
bootloader trust and device behavior require separate verification.

For a candidate export, select one target-files archive as the image authority.
Run the built `check_target_files_vintf` and `validate_target_files` against it
from the source root, so source-relative development key paths resolve. Extract
the partition images from that archive's `IMAGES/` directory and create the
complete super image with the built `build_super_image` using the same archive.
The standalone images in the product output directory can differ because
releasetools repacks images and derives AVB salts separately. Do not mix the two
sets. Verify the exported AVB chain, physical capacities and every unpacked
super payload against the selected archive, and retain their hashes with the
source/input identity. A successful build alone does not establish this binding.

Unpack the actual `init_boot` ramdisk and check its executable ARM64 first-stage
`init`, static linkage, snapuserd and ramdisk build properties. A correctly
sized, signed image can contain an empty ramdisk. Check GKI header OS-version
fields are zero and versions remain in AVB properties. Verify all four FP6
chains: recovery at location 1, vbmeta_system at 2, boot at 3 and init_boot at 4,
with verification flags zero. Inspect recovery runtime dependencies separately.

The FP6 bootloader requests the `pvmfw` partition whenever it exists and does
not load a slot whose verified AVB data omits it, even when unlocked. Export
`pvmfw.img` from the same target-files archive, check that `vbmeta_system`
contains its hash descriptor with the exported image size and digest, and
flash it to the same slot as the other boot-chain images.

First-stage init loads `modules.load` (or `modules.load.recovery` in recovery)
from the vendor ramdisk and skips modules named in that ramdisk's
`modules.blocklist`. The recovery list names debug modules, such as
`llcc_perfmon`, that the stock image excludes only through this blocklist.
Check that the unpacked vendor ramdisk contains the same blocklist as
`vendor_dlkm`.

The platform's hardened memory allocator reserves an isolated address region per
allocation size class when a process starts. That reservation does not fit in
the 512 GiB user address space of a 39-bit (`CONFIG_ARM64_VA_BITS=39`) kernel,
and every process, including first-stage `init`, aborts at its first
allocation. Both the common GKI defconfig and the vendor GKI defconfig select
`CONFIG_ARM64_VA_BITS_48`, and the kernel policy check rejects a 39-bit
configuration. The kernel and all modules must be rebuilt together after
changing it.

The FP6 bootloader appends its own bootconfig keys, including
`androidboot.fstab_suffix`, `androidboot.slot_suffix` and the verified-boot
state. The kernel rejects the entire bootconfig if any key is assigned twice,
leaving userspace without a slot suffix. Do not set bootloader-supplied keys
in `BOARD_BOOTCONFIG`; check the booted `/proc/bootconfig` when changing it.

Every `first_stage_mount` fstab entry must have its mount point in a verified
image: first-stage init cannot create directories on the read-only partitions,
and a failed entry without `nofail` or `formattable` aborts normal boot while
recovery, which skips first-stage mount, still starts. The stock `/odm/persist`
mount exists only to import device-generated product properties from persist;
DiamaneOS does not ship that import and does not mount persist there.

The Gen8.3 GPU firmware dependencies are explicit in the native selection and
are authenticated against the stock recipe. Missing or changed firmware fails
generation without replacing the prior valid tree. Firmware stored in retained
device partitions remains a separate, exact-stock requirement; this generator
does not replace modem, DSP, bootloader or trusted firmware partitions.

See [FP6 kernel build and capability contract](FP6-KERNEL.md) for the native
build commands, interface checks and development/production distinction.

### Patch base and downstream environment identities

In a patch inventory, `base_environment_id` identifies the upstream source
baseline (source-base semantics). It is not the
identity of the current host or composed product environment.
`base_project_map_sha256` binds that baseline's project map. Each patch binds
its workspace, project path, exact base and derived revisions, canonical diff
and changed-file set. Advancing a host, overlay or product input creates a new
build environment identity without silently rewriting an accepted environment.

Freeze these values when selecting a candidate for a recorded build or test.
Ordinary working edits and documentation changes do not each require another
environment ID. When selected build inputs change, update their revisions and
derive the affected file/project-map hashes together, then validate the complete
snapshot. Keep the previous snapshot accessible through its tools commit. The
configuration schema version changes only when its structure or meaning changes;
it is separate from a candidate environment ID and any public OS release version.

A moving development branch in the manifest is resolved to exact commits in the
consuming build environment. Source composition authenticates the overlay bytes,
overlay revision and resolved project map, rejects undeclared overlays and
verifies actual checkout contents. A previous native integration probe is not a
clean build of a later environment.
