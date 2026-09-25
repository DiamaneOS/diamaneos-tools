# Reconstruct FP6 development build inputs

These commands reconstruct the selected factory-derived userspace files and
source-built kernel, modules and device trees. The source repositories contain
recipes and code; generated payloads live in caller-selected work directories.
No command accesses a phone. A passing preparation does not establish a bootable
ROM, production hardening or release acceptance.

Use a Linux x86-64 host qualified for the pinned [Android build](BUILD.md).
Install Python 3 with the repository's declared dependencies, Git, Make, Bash,
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
FACTORY_ZIP="/absolute/path/to/FP6.QREL.16.100.0.20260727183253_WS1M-factory.zip"
IMAGE_TOOLS="/absolute/path/to/extracted-otatools/bin"
SOURCE_ROOT="/absolute/path/to/android-source"
```

`FACTORY_ZIP` is the EU factory archive identified by
`config/fp6-stock-image-recipe.json`. Obtain that exact archive through the
source recorded in `config/fp6-sources.json`. Other regions/builds are not
interchangeable. `IMAGE_TOOLS` is the `bin` directory of the otatools package
produced by the pinned generic Android build in [BUILD.md](BUILD.md); preserve
its sibling `lib64` directory. `config/fp6-image-tools.json` identifies the
qualified archive and authenticates all three consumed programs and their
Android shared libraries. Do not copy the executables without those libraries.
A different tool build requires a reviewed pin update and affected verification.

## Extract and generate the vendor product

```sh
"$TOOLS_ROOT/bin/diamaneos" vendor stage --archive "$FACTORY_ZIP" \
  --output "$WORK_ROOT/stock-images"
"$TOOLS_ROOT/bin/diamaneos" vendor extract \
  --super "$WORK_ROOT/stock-images/current/super.img" \
  --image-tools "$IMAGE_TOOLS" --output "$WORK_ROOT/stock-files"
"$TOOLS_ROOT/bin/diamaneos" vendor product \
  --inputs "$(realpath "$WORK_ROOT/stock-files/current")" \
  --output "$WORK_ROOT/vendor-product" --notice-kind "$NOTICE_KIND"
```

Set `NOTICE_KIND` to the reviewed Android build-system notice classification for
these inputs, as described in [BUILD.md](BUILD.md#native-fp6-product-integration).

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

## Prepare sources and build the kernel set

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
The effective GKI configuration must pass the development baseline. Production
differences remain explicit in `kernel-config.json`; see [FP6-KERNEL.md](FP6-KERNEL.md).

Logs and terminal results are in `fp6-kernel/runs/<run>/`. `current` advances to
that run's `candidate` only after all steps pass. Failed runs remain available.
A retry creates a new run and reuses Bazel's completed work; there is no automatic
retry loop. `--jobs` bounds the requested build parallelism. `--timeout` is a
per-command limit, not a limit on the entire multi-step workflow. Bazel runs in
batch mode so its JVM and workers remain in the owned command group; termination
unwinds that group instead of leaving a detached build server. The workspace
lock rejects a simultaneous preparation/build in the same workspace.

This is source reconstruction and development packaging, not a claim of
independent bit-identical release reproduction. Build-generated module signing
material and host/environment differences need explicit comparison in release
reproduction. No private retained evidence directory is an input to these commands.

## Install the generated inputs

Hold the Android workspace's operator/build lock and ensure no build or source
sync is running. Then:

```sh
"$TOOLS_ROOT/bin/diamaneos" build inputs --source "$SOURCE_ROOT" \
  --vendor "$WORK_ROOT/vendor-product" --kernel "$WORK_ROOT/fp6-kernel"
```

This verifies inventories and installs complete trees at:

- `vendor/fairphone/FP6`
- `device/fairphone/FP6-kernel`

Manifest synchronization alone does not populate those paths. Installation
refuses symlink destinations and differing existing content. Each tree is
published atomically; if interrupted between the two trees, rerun the command.
It verifies an already installed matching tree and completes the missing one.
It records inventory identities in `.repo/diamaneos-generated-inputs.json`.
Do not hand-edit either generated tree. A changed recipe needs a separately
prepared source/output workspace or an explicit reviewed replacement of old inputs.

Continue with the source-bound Android build and full image/layout/VINTF checks.
A prepared kernel or vendor tree alone does not authorize flashing.
