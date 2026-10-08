# FP6 kernel build and capability contract

The native entry points, checks and deliberate settings behind
`kernel prepare`, `kernel build` and `kernel publish`. The Android build does
not build the kernel: it uses the published kernel prebuilts, which the
DiamaneOS manifest checks out at `device/fairphone/FP6-kernel`. These commands
make and publish that set. Terms are explained in the [threat model](THREAT_MODEL.md#terms).

## Sources and workspace

The kernel follows Qualcomm's CodeLinaro release for this chip
(`LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0` and the kernel-platform and
techpack releases it names), with the GrapheneOS `kernel_common-6.1` release
merged into the vendor kernel. Fairphone's FP6 changes (the `fps` target,
panel, touch, camera and sensor drivers) are part of the imported sources; only
the device trees, which Qualcomm does not publish for this chip, come from
Fairphone. Never substitute Pixel kernel sources or disable strict KMI, module
protection or sandbox checks.

All of it is one repository,
[`kernel_qcom-6.1`](https://github.com/DiamaneOS/kernel_qcom-6.1), in the
layout of Qualcomm's kernel workspace (`kernel_platform/`, `vendor/`) so the
Qualcomm build files work unchanged. The workspace links are committed as
symlinks. `kernel_platform/common` is a submodule,
[`kernel_common-6.1`](https://github.com/DiamaneOS/kernel_common-6.1). The
toolchains (Clang, the kernel build tools, Bazel, the JDK and others) are
listed in `prebuilts.json` with exact revisions and fetched into
`kernel_platform/prebuilts`, never committed; `sync_prebuilts.sh` in the
repository fetches them for a plain clone. Each upstream project was imported
once on the repository's `upstream` branch; DiamaneOS changes are commits on
`android17`.
[`config/kernel-upstream-fp6.json`](../config/kernel-upstream-fp6.json) records
the upstream commit and release of the imports `forks check` follows
([upstream tracking](BUILD.md#upstream-tracking)).

[`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json) pins the
repository and one commit. `kernel prepare --workspace DIR`:

- checks out that commit in `DIR` (one commit, no history; `DIR` must be empty,
  a workspace an earlier preparation made, or a clone of the repository);
- checks out each submodule at the commit the tree records and each toolchain
  in `prebuilts.json` at its revision, fetching exact commits over HTTPS with
  HTTP/1.1 and retries;
- refuses edited sources, never resetting them, and any untracked input; Git
  ignores the tools' own files in the workspace through the clone's
  `.git/info/exclude`;
- checks the shared headers (below) and writes `preparation.json` (the source
  commit, the submodule and toolchain revisions) and `resolved-manifest.xml`,
  the trees Kleaf stamps the kernel version from (`KLEAF_REPO_MANIFEST`).

`kernel build` runs only on a preparation of the pinned commit and checks the
checkout again before and after the build.

## Build

From `kernel_platform`, with `KLEAF_REPO_MANIFEST` naming the resolved manifest
(paths relative to that directory), run the non-consolidate targets:

```sh
tools/bazel build --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom --config=stamp \
  //common:kernel_aarch64 //msm-kernel:fps_gki \
  //msm-kernel:fps_gki_abi //common:kernel_aarch64_abi
tools/bazel run --user_kmi_symbol_lists=//msm-kernel:android/abi_gki_aarch64_qcom \
  //common:kernel_aarch64_abi_dist -- --dist_dir "$ABI_OUTPUT"
tools/bazel query --output=label \
  'filter(":fps_gki.*", kind("_kernel_module rule", //vendor/...))'
```

`--config=stamp` makes the version name the source commit (`-g<hash>`) and
the build date the commit's (reproducible); without it Kleaf reports
`-maybe-dirty` and 1970. The output queries use the same flag. Keep the
queried list, require the audio and qcacld WLAN targets, and build
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
and bridge, the FocalTech touch driver, the kretprobe-based FunctionFS logger
and the EUD debugger. It survives regeneration of the Fairphone-derived lists.
`kernel build` fails when a denied module is back in any list (`-` and `_`
match) or no longer built (renamed or dropped: review the entry). A cut needs no
remaining importer, enforced by the build's dependency check
(`module-interfaces.json`), since libmodprobe loads dependencies a list omits.
Device-tree references count as importers too. Kept: `qcom_glink_spss` because
`glink_probe` imports it, `coresight` because KGSL is built with CoreSight
support, and `wcd937x`, `wcd939x` and `wsa883x` because the audio machine driver
imports them; those need a configuration or device-tree change first.

Three symbol rules in the same file run on every build, each with its reason:

- `module_import_allowlist`: the only modules that may import a symbol (today
  only `dwc3-msm.ko` may import `register_kretprobe`, the reason KPROBES stays
  on).
- `forbidden_symbols`: symbols that must not exist in the built kernel's
  `System.map` (empty today: `param_name_len`, which `run_init_process` calls
  after init memory is freed, is no longer an `__init` function).
- `required_symbols`: symbols that must exist there, marking changes a rebase
  could drop (`names_command_line`, the names-only /proc/cmdline; ThinLTO's
  `.llvm.` suffix counts).

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
- with `kernel prepare`, requires the DRM headers (`include/drm`,
  `include/uapi/drm`) in the common and vendor kernel trees to be
  byte-identical as committed, as their structures cross the Image/module
  boundary.

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
(compressed OTA updates), Unicode casefolding for f2fs and forced lockdown at
integrity level (confidentiality fails the check), and pins settings hardware
support needs without failing loudly: kprobes and kretprobes, the firmware
loader's user-helper fallback (the device init sets `force_sysfs_fallback`, so
ueventd loads firmware) and the debugfs API, which kernel code keeps while
mounts are refused. The settings live in `arch/arm64/configs/gki_defconfig`,
identical in the common kernel and the vendor kernel
(`kernel_platform/msm-kernel`) and kept in `savedefconfig` form because the GKI
build runs `check_defconfig`.

The vendor configuration also meets
[`config/kernel-vendor-policy-fp6.json`](../config/kernel-vendor-policy-fp6.json),
for options only the vendor tree builds: the IMS QRTR ownership guard, no QRTR
tunnel, and Qualcomm's download mode off by default
(`CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT`), which alone decides whether a
panic dumps RAM, since the running system cannot turn dumps on.

`--config-profile development` checks only the baseline and records the
profile; the configuration itself always comes from the pinned source commit.
Neither profile has SELinux development mode, so the kernel cannot go
permissive: `setenforce 0` fails, and on userdebug
`androidboot.selinux=permissive` makes init stop fatally. Never combine it with
the permissive diagnostic vendor_boot images made for the 2026-09-26
development build. The only fallbacks are reflashing those images or building
from different sources (the defconfig commit reverted in `kernel_qcom-6.1` and
`kernel_common-6.1`, and the source pin moved to that commit).

debugfs keeps its in-kernel API but cannot be mounted
(`CONFIG_DEBUG_FS_DISALLOW_MOUNT`): the filesystem is never registered and
`/sys/kernel/debug` does not exist, so not even root can mount it. Upstream
broke this mode in Linux 5.12: `debugfs_init()` returned before marking debugfs
ready, so every `debugfs_create_*` failed, the display driver failed to bind
(no display, boot never completed) and a recovery module's init failed,
stopping recovery's first-stage module loading. Both kernel trees carry the fix
in `fs/debugfs/inode.c`. No vendor or recovery init script mounts debugfs and
the device policy gives no service access to debugfs files; user builds never
mounted it, and on debuggable builds AOSP's `init-debug.rc` mount at early-init
and dumpstate's mount for the dumpstate HAL now fail harmlessly.

KPROBES stays on. The USB glue (`dwc3-msm`) implements thirteen controller hooks
(pull-up, connection-done, GSI event buffers, stop handling and others) as
kretprobes on the built-in dwc3 core and ignores registration failures, so
without kprobes they silently vanish; turning KPROBES off first needs them as
explicit calls in both trees.

Lockdown is forced at integrity level (owner decision, 2026-10-05): the
features that let user space modify the running kernel stay off, but kernel
memory is not hidden from privileged processes. Confidentiality level, used
before, also empties tracefs (no perfetto or atrace) and denies BPF programs
kernel-memory reads, so per-UID CPU time (per-app CPU in Battery usage) and
lmkd's memevents OOM listener could not start; integrity restores both. The
cost is kprobes from user space: with KPROBES on, a process that may write
tracefs's `kprobe_events` can place probes whose arguments read kernel memory
(perf's kprobe events also need CAP_PERFMON or CAP_SYS_ADMIN). SELinux decides
who may. `kprobe_events` has no label of its own, so it carries tracefs's
default, `debugfs_tracing_debug`:

- user builds: only init may write it (an AOSP neverallow keeps every other
  domain out, and the device policy adds no grant; vendor_init may only open
  and read it), and only root may write the file (0640). Only `bpfloader` and
  `uprobestats` hold one of those capabilities together with perf access;
- userdebug builds: atrace's userdebug init script makes `kprobe_events`
  world-writable, the adb root shell runs in the permissive `su` domain, and
  AOSP lets shell (adb without root), atrace, Perfetto's `traced_probes`,
  Traceur, `simpleperf_boot`, `profcollectd` and the atrace HAL write it.

BPF programs may now read kernel memory, but only `bpfloader` may load them.
IPsec keys stay blanked in XFRM state dumps: integrity lockdown does not cover
LOCKDOWN_XFRM_SECRET, so both trees make `xfrm_redact()` always true
(kernel_common-6.1 9f7417fb1da2, which the `kernel_platform/common` submodule
points at for the GKI image; msm-6.1 a3da4d2). SELinux allows the dump only to
netd, system_server, the network stack, `netutils_wrapper`, dumpstate and
Qualcomm's nicmd, which needs only SPIs to delete its own states. Enforcing
USER policy replaces none of these settings.
Current artifacts use development AVB identities, not release, relock or
production-signing inputs.

## Boot logs

Boot parameters the kernel does not use pass to init, and the bootloader's can
carry device identifiers, so both trees log them by name only (command line,
unknown-parameter line and init's environment listing); kernel parameters keep
their values.

/proc/cmdline, which bug reports copy, shows names only once init starts:

- Module options (`module.param=value`) keep their values: Android's modprobe
  reads them there (the display driver gets its panel this way).
- Every other value goes, init's environment and arguments included. The
  kernel has read them all by then, in the initcalls and sysctl setup too.
- The device tree's copy (`/proc/device-tree/chosen/bootargs`) keeps the full
  line; no bug report or shipped program reads it.

pstore/ramoops keeps the previous boot's kernel console and pmsg across a warm
reboot in a 4 MiB region placed at boot in `/reserved-memory` of the FP6 device
tree (2 MiB console, 2 MiB pmsg, no dump records, no ftrace), a DiamaneOS change
to Fairphone's SoC device tree. Reboots and kernel crashes are cold
by default (`/sys/kernel/reboot/mode` is `cold`, Qualcomm download mode off):
the PMIC's hard reset clears RAM and the region. A one-off warm reboot on the
2026-09-27 development build kept both zones, so the bootloader does not clear
RAM; reboots stay cold. Device-tree bootargs set loglevel=6, so the console zone
holds notice-level and worse (warnings, errors, panic output), not info lines.
ramoops finds the dynamically placed region via the reserved-memory lookup; it
stays put while the device tree and memory map are unchanged.

## Download modes and EUD

- Reboots ignore the `edl` and `qcom_dload` reasons and restart normally.
- The download-mode module parameters and the `/sys/kernel/dload` files are
  read-only, so a panic follows the build default (dumps off).
- EDL through the hardware keys is unaffected.
- The EUD debugger's device-tree node is disabled and `eud.ko` is denied. The
  USB controller takes connect and role events from UCSI through its role
  switch and port graph; EUD's extcon reported only EUD's own connects.

## Publish a kernel build

```sh
diamaneos kernel publish --run "$KERNEL_WORKSPACE/runs/<run>" \
  --to /path/to/device_fairphone_FP6-kernels
```

`kernel publish` copies a passed run's candidate (`Image`, `dtbo.img`, `dtbs/`,
`modules/`, `BoardConfigKernel.mk`, `device-kernel.mk` and the
`*-modules.blocklist` files) into a clean checkout of
[`device_fairphone_FP6-kernels`](https://github.com/DiamaneOS/device_fairphone_FP6-kernels),
replacing the previous set. It refuses to publish when:

- a file differs from the run's `artifacts.json`, or the candidate holds a file
  that is not part of a kernel set;
- a file contains private key material, a home or `/var/lib` path, or this
  machine's user or host name (add more strings with `--forbid`);
- the run was built from a modified tools checkout or does not record its
  source commit.

It rewrites the README's "This build" lines (the `kernel_qcom-6.1` commit and
Linux version, the tools commit and configuration profile, the module, device
tree and overlay counts, the build date). It does not commit or push: review
the change, add a test note to the README once the set has been tested on a
phone, commit and push. The next Android build that syncs the manifest picks
up the new commit.
