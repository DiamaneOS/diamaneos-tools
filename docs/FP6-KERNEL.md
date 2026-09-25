# FP6 kernel build and capability contract

Use the public `kernel prepare`, `kernel build` and `build inputs` workflow in
[FP6 preparation](FP6-PREPARATION.md) to reconstruct and install the entire set.
The native entrypoints and compatibility boundaries below explain that workflow.

The development kernel follows Qualcomm's CodeLinaro release for this chip
(`LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0` and the kernel-platform and
techpack releases it names), with the GrapheneOS `kernel_common-6.1` release
merged into the vendor kernel. Fairphone's FP6 hardware changes (the `fps`
target, panel, touch, camera and sensor drivers) are carried as DiamaneOS
patches; only the FP6 device trees, which Qualcomm does not publish for this
chip, still come from Fairphone. The source set is separate from the platform
checkout. `config/fp6-sources.json` identifies the upstream
families; [`config/patches.json`](../config/patches.json) binds the downstream
changes, including the matched devfreq header exported to the graphics package
and the common/vendor kernel configuration and ABI changes. Do not substitute
Pixel kernel sources or disable strict KMI, module protection or sandbox checks.

[`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json) records the
resolved kernel-workspace projects and link exports. Fetch each project from its
declared source URL, check out its exact revision, then apply only the downstream revisions in
`config/patches.json`. Retain the resulting resolved manifest before building. Preserve link exports, except the two absent legacy `kernel/build`
entrypoints `build.sh` and `build_abi.sh` at
`f19534bc201764082056c886279fb69aeb423641`. Use the actual Bazel entrypoint.
The workspace's `vendor` link must resolve to the pinned sibling vendor tree;
`build/msm_kernel_extensions.bzl` and `build/abl_extensions.bzl` resolve to the
matching source projects. A missing source or mismatched revision is an error.

From `kernel_platform`, with `KLEAF_REPO_MANIFEST` naming the resolved manifest
whose paths are relative to that directory, run the non-consolidate targets:

```sh
tools/bazel build --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom \
  //common:kernel_aarch64 //msm-kernel:fps_gki \
  //msm-kernel:fps_gki_abi //common:kernel_aarch64_abi
tools/bazel run --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom \
  //common:kernel_aarch64_abi_dist -- --dist_dir "$ABI_OUTPUT"
tools/bazel query --output=label \
  'filter(":fps_gki.*", kind("_kernel_module rule", //vendor/...))'
```

Retain the queried target list, require audio and qcacld WLAN targets, and build
that exact list with the same symbol-list flag. Bound concurrency and wall time,
hold the workspace lock, and record commands, source revisions and exit status.
The vendor ABI rule has no STG baseline at this pin; an empty vendor diff is not
an ABI comparison. The explicit common GKI ABI comparison and selected vendor
symbol/CRC/namespace checks are required independently.

Collect top-level configured outputs and declared implicit config/module targets.
Bazel output sets may include source files and report directories; do not assume
every returned path is a generated regular file or read stale transition outputs.
Merge DTs using the pinned vendor rules and reconcile bootloader selectors against
stock. Retain effective common and vendor configurations, built-in module lists,
Module.symvers, public certificate and all module signatures.

[`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json) defines
the reviewed development module selection, partition placement and load lists.
Overlaps between system DLKM, vendor DLKM and the vendor ramdisk are intentional
for normal/recovery availability. Each placement is hash-bound. Stripping debug
sections from unsigned modules must preserve module metadata and symbol versions. Preserve signed GKI modules byte
for byte. Compare the final image contents, not only intermediate directories.

The hardened kernel enables `RANDSTRUCT_FULL`. Clang randomizes a structure of
only function pointers only when every struct or enum its members name is
already declared; if a callback's return type is the first mention of a tag,
that compilation unit silently keeps declaration order. The same callback table
then has two layouts depending on include order, and a call through it lands on
the wrong callback (a CFI panic at boot). Clang reports the parameter-list case
as `-Wvisibility`, never the return-type case. `kernel build` therefore:

- fails on any `-Wvisibility` warning in the core or external-module build log
  (a cached Bazel action does not repeat its warnings; a clean build does);
- scans every DWARF definition of every function-pointer-only structure in both
  kernels' `vmlinux` and every unstripped module (`src/diamaneos_tools/kernel_layout.py`),
  writes `layout-scan.json` into the run and fails if one structure has more
  than one member order;
- with `kernel prepare`, requires the DRM headers that both the common and the
  vendor kernel tree carry (`shared_headers` in
  [`config/kernel-workspace-fp6.json`](../config/kernel-workspace-fp6.json)) to
  be byte-identical, since their structures cross the Image/module boundary.

Fix a reported split at its source by declaring the tag before the structure
(an include or a forward declaration), never by disabling RANDSTRUCT. Any
upstream kernel, GrapheneOS or Qualcomm merge can reintroduce one.

Native checks establish strict common KMI/ABI, selected provider CRC/namespace
coverage, stage dependency planning, GKI certificate/signature binding and image
payload preservation. The consumed UFS BSG layout agrees with the kernel;
zero/nonzero reply handling does not establish general signed-type equivalence.
No check here proves actual module insertion, firmware execution or device boot.

The development baseline is Linux 6.1.129, while the selected stock reports
6.1.138. This gap remains a maintenance and device-compatibility obligation.
Effective device-tree boot arguments also require production review: the pinned
source includes `kpti=0` and debugging/tuning options. Configuration-file checks
do not validate the resulting command line.
Production configuration validation deliberately rejects four inherited settings:
SELinux development support, unrestricted debugfs, debugfs mount availability
and unrestricted dmesg. Enforcing USER policy does not make those kernel settings
production-safe. A production candidate must resolve and revalidate them, then
repeat affected device tests. The current artifacts use development AVB identities
and are not release, relock or production-signing inputs.
