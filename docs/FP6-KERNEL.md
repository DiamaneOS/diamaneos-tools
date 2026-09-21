# FP6 kernel build and capability contract

The initial development kernel uses Fairphone's pinned Android 14 / Linux 6.1
GKI/vendor source set with the Android 17 product. The source set is separate
from the platform checkout. `config/fp6-sources.json` identifies the upstream
families; `config/patches.json` binds the two downstream build-rule changes that
expose the matched devfreq header to the graphics package. Do not substitute
Pixel kernel sources or disable strict KMI, module protection or sandbox checks.

`config/kernel-sources-fp6.json` records all 63 resolved kernel-workspace
projects and link exports. Fetch each project from its declared source URL,
check out its exact revision, then apply only the downstream revisions in
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

The reviewed development selection contains 440 distinct modules. Its packaging
has 60 system DLKM, 269 vendor DLKM and 297 vendor-ramdisk modules; overlaps are
intentional for normal/recovery availability. Each placement is hash-bound and
has an explicit load list. Stripping debug sections from unsigned modules must
preserve module metadata and symbol versions. Preserve signed GKI modules byte
for byte. Compare the final image contents, not only intermediate directories.

Native checks establish strict common KMI/ABI, selected provider CRC/namespace
coverage, stage dependency planning, GKI certificate/signature binding and image
payload preservation. The consumed UFS BSG layout agrees with the kernel;
zero/nonzero reply handling does not establish general signed-type equivalence.
No check here proves actual module insertion, firmware execution or device boot.

The development baseline is Linux 6.1.129, while the selected stock reports
6.1.138. This gap remains a maintenance and device-compatibility obligation.
Production configuration validation deliberately rejects four inherited settings:
SELinux development support, unrestricted debugfs, debugfs mount availability
and unrestricted dmesg. Enforcing USER policy does not make those kernel settings
production-safe. A production candidate must resolve and revalidate them, then
repeat affected device tests. The current artifacts use development AVB identities
and are not release, relock or production-signing inputs.
