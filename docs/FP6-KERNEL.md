# FP6 kernel build and capability contract

The native entry points, checks and deliberate settings behind the
`kernel prepare`, `kernel build` and `build inputs` workflow in
[the build reference](BUILD.md#generated-inputs-step-by-step), which
reconstructs and installs the whole set. Terms are explained in the [threat model](THREAT_MODEL.md#terms).

## Sources and workspace

The kernel follows Qualcomm's CodeLinaro release for this chip
(`LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0` and the kernel-platform and
techpack releases it names), with the GrapheneOS `kernel_common-6.1` release
merged into the vendor kernel. Fairphone's FP6 changes (the `fps` target,
panel, touch, camera and sensor drivers) are DiamaneOS patches; only the device
trees, which Qualcomm does not publish for this chip, come from Fairphone. The
source set is separate from the platform checkout: `config/fp6-sources.json`
names upstream families and [`config/patches.json`](../config/patches.json)
binds downstream changes, including the matched devfreq header exported to the
graphics package and the common/vendor configuration and ABI changes. Never
substitute Pixel kernel sources or disable strict KMI, module protection or
sandbox checks.

[`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json) records
the resolved workspace projects and link exports. Fetch each project from its
declared URL at its exact revision, apply only the downstream revisions in
`config/patches.json`, and keep the resolved manifest before building.
Preserve link exports except the two absent legacy `kernel/build` entry points
`build.sh` and `build_abi.sh` at `f19534bc201764082056c886279fb69aeb423641`;
use the actual Bazel entry point. The `vendor` link must resolve to the pinned
sibling vendor tree, and `build/msm_kernel_extensions.bzl` and
`build/abl_extensions.bzl` to their matching projects. A missing source or
wrong revision is an error.

## Build

From `kernel_platform`, with `KLEAF_REPO_MANIFEST` naming the resolved manifest
(paths relative to that directory), run the non-consolidate targets:

```sh
tools/bazel build --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom \
  //common:kernel_aarch64 //msm-kernel:fps_gki \
  //msm-kernel:fps_gki_abi //common:kernel_aarch64_abi
tools/bazel run --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom \
  //common:kernel_aarch64_abi_dist -- --dist_dir "$ABI_OUTPUT"
tools/bazel query --output=label \
  'filter(":fps_gki.*", kind("_kernel_module rule", //vendor/...))'
```

Keep the queried list, require the audio and qcacld WLAN targets, and build
exactly that list with the same flag, bounded concurrency and wall time, the
workspace lock held and commands, revisions and exit status recorded. The
vendor ABI rule has no STG baseline at this pin, so an empty vendor diff is no
ABI comparison; the common GKI ABI comparison and selected vendor
symbol/CRC/namespace checks are required separately. Collect top-level
configured outputs and declared implicit config/module targets (Bazel output
sets may include source files and report directories; never read stale
transition outputs), merge DTs with the pinned vendor rules, reconcile
bootloader selectors against stock, and keep effective common and vendor
configurations, built-in module lists, Module.symvers, the public certificate
and all module signatures.

## Module packaging

[`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json)
defines the reviewed development module selection, partition placement and load
lists. Overlaps between system DLKM, vendor DLKM and the vendor ramdisk are
intentional (normal/recovery availability); each placement is hash-bound.
Stripping debug sections from unsigned modules must keep module metadata and
symbol versions; signed GKI modules stay byte for byte. Compare final image
contents, not just intermediate directories.

Its `denied_modules` lists, with reasons, what FP6 never ships: the CAN,
802.15.4/6LoWPAN, kernel NFC, PPTP/L2TP, GenieZone and kheaders GKI modules, the
in-kernel Bluetooth stack, the HDMI bridge and codecs, other chips' WLAN
drivers, the WCD938x codec, FM radio, the TrustZone log reader, the SPSS loader
and bridge, the FocalTech touch driver and the kretprobe-based FunctionFS
logger. It survives regeneration of the Fairphone-derived lists. `kernel build`
fails when a denied module is back in any list (`-` and `_` match) or no longer
built (renamed or dropped: review the entry). A cut needs no remaining importer,
enforced by the build's dependency check (`module-interfaces.json`), since
libmodprobe loads dependencies a list omits. Device-tree references count too:
`eud` stays because the USB controller node takes its extcon from it,
`qcom_glink_spss` because `glink_probe` imports it, `coresight` because KGSL is
built with CoreSight support, and `wcd937x`, `wcd939x` and `wsa883x` because the audio machine driver imports them; those need a
configuration or device-tree change first.

Two symbol rules in the same file run on every build. `module_import_allowlist`
names the only modules that may import a symbol (today only `dwc3-msm.ko` may
import `register_kretprobe`, the reason KPROBES stays on), and
`forbidden_symbols` lists symbols that must not exist in the built kernel's
`System.map` (an out-of-line `param_name_len` would let init call freed code).
Each rule carries its reason.

## Structure layout checks

The hardened kernel enables `RANDSTRUCT_FULL`. Clang randomizes a
function-pointer-only structure only if every struct or enum its members name
is already declared; a tag first mentioned in a callback's return type silently
keeps declaration order in that unit, so one callback table gets two layouts
and calls land on the wrong callback (a CFI panic at boot). Clang warns
(`-Wvisibility`) only for the parameter-list case. So `kernel build`:

- fails on any `-Wvisibility` warning in the core or external-module build log
  (cached Bazel actions do not repeat warnings; a clean build does);
- scans every DWARF definition of every function-pointer-only structure in both
  kernels' `vmlinux` and every unstripped module
  (`src/diamaneos_tools/kernel_layout.py`), writes `layout-scan.json` and fails
  if a structure has more than one member order;
- with `kernel prepare`, requires the DRM headers in both the common and vendor
  trees (`shared_headers` in
  [`config/kernel-workspace-fp6.json`](../config/kernel-workspace-fp6.json)) to
  be byte-identical, as their structures cross the Image/module boundary.

Fix a split at its source by declaring the tag first (include or forward
declaration), never by disabling RANDSTRUCT; any upstream kernel, GrapheneOS or
Qualcomm merge can reintroduce one.

These native checks establish strict common KMI/ABI, selected provider
CRC/namespace coverage, stage dependency planning, GKI certificate/signature
binding and image payload preservation; the consumed UFS BSG layout agrees with
the kernel (zero/nonzero reply handling is not general signed-type
equivalence). None proves module insertion, firmware execution or boot.

## Configuration policy

Known gap: the effective device-tree boot arguments need production review, as the pinned
source includes `kpti=0` and debugging/tuning options that configuration checks
do not see.

KMI deviation: the hardened kernel uses a 48-bit virtual address space
(`CONFIG_ARM64_VA_BITS_48`, as in the GrapheneOS release), where the GKI
defconfig in Qualcomm's android14-6.1 vendor tree defaults to 39 bits. The
platform's hardened memory allocator needs it
([build reference](BUILD.md#native-fp6-product-integration)). The address-space
layout and page-table depth (four levels instead of three) are compiled into
the kernel and every module, so modules built for a standard GKI kernel,
Fairphone's stock modules among them, do not fit it: every module is built from
the pinned trees with this kernel. The kernel policy requires 48 bits in both
configurations.

Right after the core build, before any module, `kernel build` checks both the
GKI configuration (the Image) and the vendor tree's (the modules) against
[`config/kernel-policy-fp6.json`](../config/kernel-policy-fp6.json), by default
with the production profile: no SELinux development mode and dmesg restricted
from boot, plus the baseline hardening. The baseline keeps userfaultfd (ART's
garbage collector; unprivileged users get user-mode-only descriptors), io_uring
(compressed OTA updates), Unicode casefolding for f2fs and forced lockdown in
confidentiality mode, and pins settings hardware support needs without failing
loudly: kprobes and kretprobes, the firmware loader's user-helper fallback (the
device init sets `force_sysfs_fallback`, so ueventd loads firmware) and the
debugfs API, which kernel code keeps while mounts are refused. The settings
live in `arch/arm64/configs/gki_defconfig`, identical in the common and vendor
forks and kept in `savedefconfig` form because the GKI build runs
`check_defconfig`.

`--config-profile development` checks only the baseline and records the
profile; the configuration itself always comes from the pinned fork commits.
Neither profile has SELinux development mode, so the kernel cannot go
permissive: `setenforce 0` fails, and on userdebug
`androidboot.selinux=permissive` makes init stop fatally. Never combine it with
the permissive diagnostic vendor_boot images made for the 2026-09-26
development build. The only fallbacks are reflashing those images or building
from different
sources (the defconfig commit reverted, other derived revisions in
`config/patches.json` and a regenerated kernel manifest).

debugfs keeps its in-kernel API but cannot be mounted
(`CONFIG_DEBUG_FS_DISALLOW_MOUNT`): the filesystem is never registered and
`/sys/kernel/debug` does not exist, so not even root can mount it. Upstream
broke this mode in Linux 5.12: `debugfs_init()` returned before marking debugfs
ready, so every `debugfs_create_*` failed, the display driver failed to bind
(no display, boot never completed) and a recovery module's init failed,
stopping recovery's first-stage module loading. Both forks carry the fix in
`fs/debugfs/inode.c`. No vendor or recovery init script mounts debugfs and the
device policy gives no service access to debugfs files; user builds never
mounted it, and on debuggable builds AOSP's `init-debug.rc` mount at early-init
and dumpstate's mount for the dumpstate HAL now fail harmlessly.

KPROBES stays on. The USB glue (`dwc3-msm`) implements thirteen controller hooks
(pull-up, connection-done, GSI event buffers, stop handling and others) as
kretprobes on the built-in dwc3 core and ignores registration failures, so
without kprobes they silently vanish; turning KPROBES off first needs them as
explicit calls in both trees. Lockdown blocks user-space kprobes (tracefs, perf)
and BPF kernel reads, so only signed kernel code places probes; confidentiality
level also empties tracefs (no perfetto or atrace) and denies BPF kernel-memory
reads, so per-UID CPU time (per-app CPU in Battery usage) and the memevents OOM
listener do not start. Integrity level would restore both but let root probe
through tracefs; the level is an open decision. Enforcing USER policy replaces
none of these settings. Current artifacts use development AVB identities, not
release, relock or production-signing inputs.

## Boot logs

Boot parameters the kernel does not use pass to init, and the bootloader's can
carry device identifiers, so both trees log them by name only (command line,
unknown-parameter line and init's environment listing); kernel parameters keep
their values.

pstore/ramoops keeps the previous boot's kernel console and pmsg across a warm
reboot in a 4 MiB region placed at boot in `/reserved-memory` of the FP6 device
tree (2 MiB console, 2 MiB pmsg, no dump records, no ftrace), from the DiamaneOS
fork of Fairphone's SoC device-tree project. Reboots and kernel crashes are cold
by default (`/sys/kernel/reboot/mode` is `cold`, Qualcomm download mode off):
the PMIC's hard reset clears RAM and the region. A one-off warm reboot on the
2026-09-27 development build kept both zones, so the bootloader does not clear
RAM; reboots stay cold. Device-tree bootargs set loglevel=6, so the console zone
holds notice-level and worse (warnings, errors, panic output), not info lines.
ramoops finds the dynamically placed region via the reserved-memory lookup; it
stays put while the device tree and memory map are unchanged.
