# FP6 kernel build and capability contract

Use the public `kernel prepare`, `kernel build` and `build inputs` workflow in
[the build reference](BUILD.md#generated-inputs-step-by-step) to reconstruct and install the entire set.
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

The same file's `denied_modules` lists the modules FP6 never ships, each group
with its reason: the CAN, 802.15.4/6LoWPAN, kernel NFC, PPTP/L2TP, GenieZone and
kheaders GKI modules, the in-kernel Bluetooth stack, the HDMI bridge and codecs,
other chips' WLAN drivers, the WCD938x codec, FM radio, the TrustZone log reader,
the SPSS loader and bridge, the FocalTech touch driver and the kretprobe-based
FunctionFS logger. The partition and load lists come from Fairphone's lists; the
deny list survives their regeneration. `kernel build` fails when a denied module
is back in any list (`-` and `_` spellings match) or is no longer built (renamed
or dropped: review the entry). A cut is allowed only when no remaining module
imports it; the build's own dependency check (`module-interfaces.json`) enforces
that, because libmodprobe loads a dependency even when a list leaves it out.
Device-tree references count too: `eud` stays because the USB controller node
takes its extcon from it, `qcom_glink_spss` because `glink_probe` imports it,
`coresight` because KGSL is built with its CoreSight support, and `wcd937x`,
`wcd939x` and `wsa883x` because the audio machine driver imports them. Those
need a configuration or device-tree change first.

Two symbol rules in the same file run on every build. `module_import_allowlist`
names the only modules that may import a symbol (today only `dwc3-msm.ko` may
import `register_kretprobe`, the reason KPROBES stays on), and
`forbidden_symbols` lists symbols that must not exist in the built kernel's
`System.map` (an out-of-line `param_name_len` would let init call freed code).
Each rule carries its reason.

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
Right after the core build, before any module is built, `kernel build` checks
both the GKI configuration (the Image) and the vendor tree's configuration (the
modules) against
[`config/kernel-policy-fp6.json`](../config/kernel-policy-fp6.json), by default
with the production profile: no SELinux development mode and dmesg restricted
from boot, plus the baseline hardening. The baseline also keeps userfaultfd
(ART's garbage collector; unprivileged users get user-mode-only descriptors),
io_uring (compressed OTA updates), Unicode casefolding for f2fs and forced
lockdown in confidentiality mode, and pins settings that hardware support
depends on without failing loudly: kprobes and kretprobes (below), the
firmware loader's user-helper fallback (the device init turns on
`force_sysfs_fallback`, so ueventd loads the firmware) and the debugfs API.
The settings themselves live in `arch/arm64/configs/gki_defconfig`, identical in
the common and vendor kernel forks; that file must stay in `savedefconfig` form,
because the GKI build runs `check_defconfig`.

The profile only selects which policy the tools check. `--config-profile
development` checks the baseline alone and records the profile in the run; it
does not change the kernel configuration, which always comes from the pinned
fork commits. A kernel built from these sources has no SELinux development
mode in either profile. It cannot be switched to permissive: `setenforce 0`
fails, and on a userdebug build a permissive request
(`androidboot.selinux=permissive`) makes init stop with a fatal error, so the
permissive diagnostic vendor_boot images made for r9s must not be combined with
it. The only fallbacks are reflashing the r9s images or building a kernel from
different sources (the defconfig commit reverted, other derived revisions in
`config/patches.json` and a regenerated kernel manifest).

debugfs stays as it was on r9s (`CONFIG_DEBUG_FS_ALLOW_ALL`). In this tree
`CONFIG_DEBUG_FS_DISALLOW_MOUNT` also turns off the in-kernel debugfs API: every
`debugfs_create_*` call fails, the display driver then fails to bind (no display,
Android never finishes booting) and a recovery module's init fails, which stops
recovery's first-stage module loading. User builds never mount debugfs;
debuggable builds mount it early in boot and unmount it once boot completes
(AOSP `init-debug.rc`, with `ro.product.debugfs_restrictions.enabled=true`), and
SELinux governs access while it is mounted. A kernel change that keeps the API
but refuses mounts is the stricter option and an open item.

KPROBES stays on (owner decision, 2026-09-27). The USB controller glue
(`dwc3-msm`) implements twelve controller hooks (pull-up, connection-done, GSI
event buffers, stop handling and others) as kretprobes on the built-in dwc3
core and ignores registration failures, so without kprobes those hooks silently
disappear. Lockdown blocks every kprobe created from user space (tracefs and
perf) and BPF kernel reads, so only signed kernel code can place probes.
The same confidentiality level turns tracing off (tracefs stays empty, so
perfetto and atrace cannot trace) and withholds kernel-memory reads from every
BPF program, so Android's per-UID CPU time tracking (per-app CPU use in Battery
usage) and the memevents OOM listener do not start. Integrity level would
restore both but let root place probes through tracefs; the level is an open
decision.
Turning KPROBES off first needs those hooks as explicit calls in both kernel
trees. Enforcing USER policy does not replace any of these kernel settings. The
current artifacts use development AVB identities and are not release, relock or
production-signing inputs.

Boot parameters that the kernel does not use are handed to init, and the
bootloader's can carry device identifiers. Both kernel trees therefore log such
parameters by name only (in the command line, the unknown-parameter line and
init's environment listing); kernel parameters keep their values in the log.
pstore/ramoops has a 4 MiB region placed at boot in `/reserved-memory` of the
FP6 device tree (2 MiB console, 2 MiB pmsg, no dump records, no ftrace), from
the DiamaneOS fork of Fairphone's SoC device-tree project. It keeps the
previous boot's kernel console and pmsg in RAM across a warm reboot. The
kernel reboots cold by default (`/sys/kernel/reboot/mode` is `cold`, and
Qualcomm download mode is off), and so does a kernel crash, so the PMIC does a
hard reset that powers the RAM off and the region comes back empty. A one-off
warm reboot on r9t kept both zones, so the bootloader itself does not clear
RAM. Cold reboots stay the default (owner decision, 2026-09-27). The console zone
gets only what reaches a console: the device tree's bootargs set loglevel=6,
so it holds notice-level and more severe messages (warnings, errors, panic
output), not info lines. The region has no fixed address; the ramoops driver
finds the dynamically placed region through its reserved-memory lookup, and it
lands at the same place on every boot while the device tree and memory map
stay the same.
