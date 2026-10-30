# FP6 kernel build and capability contract

- The native entry points, checks and deliberate settings behind `kernel prepare`, `kernel build`,
  `kernel compare` and `kernel publish`.
- The Android build does not build the kernel: it uses the published kernel prebuilts, which the
  DiamaneOS manifest checks out at `device/fairphone/FP6-kernel`.
- These commands make and publish that set.
- Terms are explained in [TERMS.md](TERMS.md).

## Sources and workspace

- The kernel is `kernel_common-6.18` (Google's `android17-6.18` common kernel with hardening)
  plus the FP6 commits, in
  [`kernel_common-6.18`](https://github.com/DiamaneOS/kernel_common-6.18).
- The Qualcomm drivers are the vendor tree
  [`kernel_qcom-6.18`](https://github.com/DiamaneOS/kernel_qcom-6.18). It holds no copy of the core
  kernel. Its modules build as external modules against the common kernel.
  - `modules/` holds the packages, each with a module registry (`modules.bzl`).
  - `configs/` holds the kernel configuration profiles.
  - `kernel/` holds the device kernel fragments.
- The device tree is the 6.1 one with fix-ups (below).
- Never substitute Pixel kernel sources or disable strict KMI, forced module signatures or sandbox checks.
- [`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json) pins both repositories and
  every toolchain part at exact commits. [`config/fp6-kernel-dt.json`](../config/fp6-kernel-dt.json)
  pins the base device tree. Their URLs are plain values and may change without a new pin.

`kernel prepare --workspace DIR`:

- Checks out each pinned project in its own directory of `DIR` (`vendor`, `common`, `toolchain/*`
  and `base-dt`), one commit with no history. Toolchain parts are partial, sparse clones of their
  repositories, fetched over HTTPS with HTTP/1.1 and retries.
- `DIR` must be empty or a workspace an earlier preparation made. Edited sources are refused and
  never reset, and so is any untracked input.
- `--source NAME=PATH` fetches the pinned commit of one project from a local Git repository instead
  of its URL. The commit stays the pin, so the checkout is the same either way.
- Checks the core header copies (below) and the base device tree files against their digests, and
  writes `preparation.json`.

`kernel build` runs only on a preparation of the pinned commits and checks every checkout again
before the build.

## Build

The toolchain is Clang `r584948c` and Rust `1.91.1.p3` from the pinned prebuilts, the kernel build
tools and the host libraries the kernel's own host tools link. The build starts from an empty
environment, so nothing of the calling shell reaches it.

Reproducibility:

- `SOURCE_DATE_EPOCH` and `KBUILD_BUILD_TIMESTAMP` are the pinned common commit's time, and the
  build user and host are fixed.
- The vendor modules compile with `-ffile-prefix-map` for the copy of the vendor tree they build
  from, so no build directory ends up in a module.
- The vendor tree is extracted from the pinned commit (`git archive`) into the run directory.
  The generated `Kbuild` files go there, never into the checkout.

[`config/fp6-kernel-build.json`](../config/fp6-kernel-build.json) lists the stages. In each run:

1. The GKI core build (`core/`): `gki_defconfig`, the vendor tree's `configs/diamaneos_defconfig`
   and the profile's fragment, then `make Image modules`. This makes the Image, the GKI modules
   and the key the Image trusts for modules (`MODULE_SIG_FORCE`).
   - Every request of a fragment must be in the final `.config` (`fragment-check.json`).
   - The configuration meets the policy below before anything is compiled
     (`kernel-config.json`).
2. The device kernel build (`device/`): the same tree and configuration plus the vendor tree's
   `kernel/wireless_defconfig` and `kernel/platform_defconfig`, then `make modules`. The 6.18 GKI
   build makes no cfg80211, mac80211, BAM DMA engine or cqhci. This build makes them, and every
   vendor module builds against it.
   - It may differ from the GKI configuration only in the listed symbols (`device-config.json`).
   - Its exports of the kernel itself must equal the GKI build's.
3. The vendor packages, in the order of the plan, each as external modules against the device
   build: the base layer, thermal and DCVS, audio and sensors, modem and data, the display, GPU and
   touch groups, camera and video, Wi-Fi and Bluetooth, the peripherals and qcacld-3.0.
   - A package reads the symbol tables of the earlier packages it names, and only those.
   - The `Kbuild` of a package is made from its DDK registry by
     `src/diamaneos_tools/kernel_kbuild.py`, with the vendor values from the package `defconfig`
     on top of the base layer's and the kernel's.
   - qcacld-3.0 builds with the effective configuration in
     [`config/fp6-kernel-qcacld.config`](../config/fp6-kernel-qcacld.config), which must hold every
     value of the vendor's `fps_gki_qca6750_defconfig`, and with the cfg80211 feature probes of the
     plan read from the common sources.
4. The module set (below), the device tree (below), the checks (below) and the candidate.

- A struct declared inside a parameter list (`-Wvisibility`) in any build log fails the build.
- The core header copies listed in the vendor tree's `core-headers.json` files must stay byte for
  byte equal to the common kernel, because their structures cross the Image and module boundary.
- `--config-profile development` and `production` build the same configuration (the vendor tree's
  production profile). They differ in the policy that is enforced (below).

## Module packaging

- [`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json) defines the reviewed
  module selection, partition placement and load lists, the kernel command line, the device trees
  that ship and the overlay count.
- Only `fp6.dtb` ships.
- A module comes from the vendor or device builds, else from the core build. A name both make is
  refused.
- Every module is stripped of debug sections and signed with the Image's key. Stripping and signing
  must keep module metadata and symbol versions.
- The lists must be complete: a load list names only modules of its partition, and a module's
  dependencies sit in its own partition. vendor_dlkm may depend on system_dlkm.
- vendor_boot holds the first-stage modules, the recovery list (first stage plus second stage) and
  the common modules they depend on (`libarc4`, `rfkill`).

Its `denied_modules` lists, with reasons, what FP6 never ships:

- The CAN, 802.15.4/6LoWPAN, kernel NFC, PPTP/L2TP, GenieZone and kheaders GKI modules.
- The in-kernel Bluetooth stack, the HDMI bridge and codecs, other chips' WLAN drivers, the
  WCD938x codec and FM radio.
- The TrustZone log reader, the legacy QSEECom driver, the HDCP client module, the SPSS loader and
  bridge and the FocalTech touch driver.
- The Hexagon DSP remote debugger (`rdbg`).
- The PCIe host and MHI drivers with QRTR over MHI (the FP6 has no PCIe device), and the USB CCID
  function.
- The debug modules of the vendor tree: clock measurement (`debugcc`), the data capture engine,
  the IOMMU, cpufreq and SPMI debug drivers, the interconnect test, the CPU dump table and the
  subsystem stats.
- CoreSight. The 6.18 GKI configuration builds it as modules, where the 6.1 policy turned it off.

The list survives regeneration of the other lists.

- `kernel build` fails when a denied module is in any list (`-` and `_` match).
- A cut needs no remaining importer, enforced by the build's dependency check
  (`module-interfaces.json`), since libmodprobe loads dependencies a list omits.
- `result.json` records the denied modules the build did not make (`denied_modules_not_built`).
- Kept: `qcom_glink_spss` because `glink_probe` imports it, and `wcd937x`, `wcd939x` and `wsa883x`
  because the audio machine driver imports them.
- `sg` stays: the TEE reaches the UFS replay-protected memory through `/dev/sg*`.

How the phone loads the modules:

- First-stage init loads `modules.load` (or `modules.load.recovery` in recovery) from the vendor
  ramdisk and skips modules named in that ramdisk's `modules.blocklist`.
- The recovery list names debug modules, such as `llcc_perfmon`, that only this blocklist keeps
  out, so the vendor ramdisk must hold the same blocklist as `vendor_dlkm`.
- Second-stage init loads `vendor_dlkm` in parallel streams, one `modprobe` per
  `modules.load.<stream>` list from `device/fairphone/FP6/boot/modules`. When the kernel prebuilts
  change `modules.load`, change those lists with them.

Kernel command line. `BoardConfigKernel.mk` adds the words of `kernel_cmdline` to
`BOARD_KERNEL_CMDLINE`, each with its reason. A word may not be one of the arguments
`config/fp6-build.json` forbids. The set holds one word:

- `scsi_mod.scan=sync`. The 6.18 GKI scans SCSI devices asynchronously, which numbers the UFS
  logical units in a different order on each boot. The device files, the boot control HAL and the
  ueventd rules name the disks by letter, so scanning stays synchronous.

Symbol rules in the same file run on every build, each with its reason:

- `module_import_allowlist`: the only modules that may import a symbol (`dwc3-msm.ko` alone may
  import `register_kretprobe`, the reason KPROBES stays on).
- `forbidden_symbols`: symbols that must not exist in the built kernel's `System.map`.
- `required_symbols`: symbols that must exist there, marking changes a rebase could drop. A
  ThinLTO `.llvm.` suffix counts.
  - A rule with a `pending` reason names a change that the pinned common kernel does not carry.
  - The build reports it in `result.json`. Once the symbol is there, it fails until the reason is
    removed.
- `kmi_exceptions`: kernel symbols modules may import although no GKI symbol list keeps them.

## Module checks

- Every module carries the Image's release and the same remaining vermagic.
- The built-in certificate of the Image is read from `vmlinux`. Every packaged module must carry a
  signature that verifies against it.
- Every symbol version (CRC) a module requires and every namespace it imports must match one
  provider in the packaged set or the Image. A module may not export a symbol another selected
  module exports, and the dependency graph must have no cycle (`module-interfaces.json`).
- Strict KMI: every kernel symbol a Qualcomm module imports must be in the GKI symbol lists the
  common tree's `BUILD.bazel` names (`gki/aarch64/symbols`) or in `kmi_exceptions`
  (`kmi` in `result.json`). An exception that is no longer needed fails the build.
- The modules of the common tree itself (the GKI modules and the device kernel's four) are not held
  to the lists.
- These checks establish provider CRC and namespace coverage, dependency order, certificate and
  signature binding and the symbol rules. None proves module insertion, firmware execution or
  boot.

## Device tree

The sources do not hold a 6.18 device tree yet. The set ships the 6.1 base device tree and overlay
image, pinned in `config/fp6-kernel-dt.json` by commit and SHA-256, and `kernel build` edits the
base through the fix-ups of that file (`src/diamaneos_tools/kernel_dt.py`):

- Every edit states the value it expects. A different base stops the build.
- The result must differ from the base in exactly the edited properties and in no node
  (`device-tree-fixups.json` lists every change).
- A new property goes first in its node, as `fdtput` adds it.
- The overlay image is the base one and must hold the number of overlays the packaging recipe
  names.
- The kernel build tools' `dtc` must decompile the result.

The fix-ups:

- `nvmem-modem-cpucp`:
  - The 6.18 nvmem core rejects a bit range past its one-byte cell and a zero-width range, and
    with them the whole PMIC SDAM, so UFS waits for its cell.
  - The 5.0 modem remoteproc takes its CMA area at memory-region index 1 where 6.1 took index 2.
  - The 5.0 CPUCP driver looks its regions up by name where 6.1 used the index.
- `explicit-cells`: parents without `#address-cells` or `#size-cells` get both as properties, with
  the value the walk up the tree resolves to in the base tree. The build fails when a node resolves
  to another value, so every parse stays the same and 6.18 stops warning.
- `static-ramoops`: 6.18 no longer supports a dynamically placed ramoops region, so it is fixed at
  `0x9fe000000` (4 MiB), where 6.1 and every 6.18 boot placed it.

## Configuration policy

The kernel configuration comes from the vendor tree: `gki_defconfig`, then
`configs/diamaneos_defconfig` (the DiamaneOS hardening that applies on 6.18, in every profile) and
the profile's fragment (`configs/production_defconfig`).

[`config/kernel-policy-fp6.json`](../config/kernel-policy-fp6.json) is checked against the effective
Image configuration right after configuring, by default with the production profile (report
`kernel-config.json`):

- The `baseline` always applies:
  - CFI, shadow call stack, hardened usercopy, fortify, stack and heap initialization and KASLR.
  - Forced module signatures with SHA-256, SELinux with development mode and lockdown at integrity
    level.
  - The Rust binder without the C binder, debugfs not mountable and pstore with ramoops.
  - No `/dev/mem`, kexec, KGDB or hibernation.
- The `production` tier adds dmesg restricted from boot and TIPC as a local-only module for
  Qualcomm's data stack.
- `--config-profile development` checks only the baseline and records the production
  differences. The configuration itself is the same.
- `build kernel-config` runs the same check on any effective `.config`:

```sh
bin/diamaneos build kernel-config --config "$KERNEL_CONFIG" \
  --policy config/kernel-policy-fp6.json --profile production
```

- Missing required symbols and duplicate assignments fail. The report binds the configuration and
  the policy by hash.

[`config/kernel-vendor-policy-fp6.json`](../config/kernel-vendor-policy-fp6.json) is checked
against the device kernel configuration with the vendor defconfigs on top, which is what the vendor
modules build with (`vendor-role-kernel-config.json`):

- QRTR as a module without the tunnel, SELinux development mode and no STM.
- Qualcomm's download mode off by default (`CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT`), which
  alone decides whether a panic dumps RAM.

Settings that differ from the 6.1 kernel:

| 6.1 | 6.18 |
| --- | --- |
| `CONFIG_CFI_CLANG` | `CONFIG_CFI` |
| `CONFIG_RANDSTRUCT_FULL` | `CONFIG_RANDSTRUCT_NONE`: 6.18 builds Rust only without RANDSTRUCT. The Rust binder (`CONFIG_ANDROID_BINDER_IPC_RUST`, no C binder) replaces the most attacked IPC driver with memory-safe code. The structure layout scan is gone with RANDSTRUCT |
| lockdown at confidentiality level | lockdown at integrity level: Android reads DMA-BUF statistics with a BPF program, which confidentiality breaks |
| `CONFIG_DEBUG_CREDENTIALS`, `CONFIG_CRYPTO_SHA1_ARM64_CE` | gone from 6.18 |
| TIPC in the baseline | `production` tier: the GKI build does not declare `tipc.ko` and `CONFIG_TIPC_LOCAL_ONLY` is a DiamaneOS patch, so TIPC is off |
| `CONFIG_CORESIGHT` off | the GKI configuration builds it as modules, so the deny list keeps `coresight*.ko` out |
| `CONFIG_QRTR_IMSDCM_OWNERSHIP` | the vendor tree declares it and no source implements the IMS role guard, so it is not checked |
| `CONFIG_USB_CONFIGFS_F_QDSS`, `CONFIG_USB_F_FS_IPC_LOGGING`, `CONFIG_QCOM_EUD` | the symbols do not exist in the 6.18 sources |
| Kleaf KMI trimming and ABI comparison | the KMI import check above |

- `CONFIG_DEBUG_FS_DISALLOW_MOUNT` is a baseline setting. It makes `debugfs_create_*` fail since
  Linux 5.12 unless `fs/debugfs/inode.c` carries the DiamaneOS fix, which the 6.18 common kernel
  does not carry yet, so a kernel from this configuration can fail to bind vendor drivers that
  need debugfs.
- KPROBES stays on. The USB glue (`dwc3-msm`) implements its controller hooks as kretprobes on the
  built-in dwc3 core and ignores registration failures, so without kprobes they silently vanish.
- SELinux development mode is on so that an update's first boot can hand the snapshot daemon over
  before enforcing. User builds never go permissive (init refuses it).
- Module signatures are forced with the Image's own key. GKI module protection (`MODULE_SIG_PROTECT`)
  is off, so every module must carry the signature.
- The 48-bit virtual address space (`CONFIG_ARM64_VA_BITS_48`) is required.
  - The platform's hardened memory allocator reserves an isolated address region per size class
    when a process starts. That does not fit the 512 GiB of a 39-bit kernel.
  - The page-table depth is compiled into every module, so every module is built from the pinned
    trees with this kernel.

## Compare two builds

```sh
diamaneos kernel compare --run "$WORKSPACE_A/runs/<run>" --against "$WORKSPACE_B/runs/<run>"
```

- Two builds of one pin, each in a workspace of its own, must package the same files with the same
  bytes.
- Each build makes its own module signing key, so a module may differ only in its appended
  signature, and the Image only in its built-in certificate and the build ID that hashes it.
- Any other difference, a file only one build has or runs of different sources fail the check.

## Compare with a reference set

```sh
diamaneos kernel compare --run "$KERNEL_WORKSPACE/runs/<run>" --reference /path/to/set --dtc /path/to/dtc
```

The reference is a directory in the layout of the prebuilts repository.

- A `vendor_cmdline` file holds its kernel command line and a `config` file its Image configuration.
- The modules of two separate builds differ in bytes, so the report compares what the Android build
  and the kernel read:

- The module names, and per module the fields that decide loading (`depends` as a set, `alias`,
  `firmware`, `license`, `softdep`, `import_ns` and the parameters).
- Every module and load list of `BoardConfigKernel.mk`, with the load order.
- The device tree after decompiling, and the overlay image byte for byte.
- The command line, with the bring-up arguments it carries.
- The Image configuration, symbol by symbol.

Modules the reference ships and the deny list leaves out are reported as policy, anything else
fails the comparison.

## Publish a kernel build

```sh
diamaneos kernel publish --run "$KERNEL_WORKSPACE/runs/<run>" \
  --to /path/to/device_fairphone_FP6-kernels
```

`kernel publish` copies a passed run's candidate (`Image`, `dtbo.img`, `dtbs/`, `modules/`,
`BoardConfigKernel.mk`, `device-kernel.mk` and the `*-modules.blocklist` files) into a clean
checkout of
[`device_fairphone_FP6-kernels`](https://github.com/DiamaneOS/device_fairphone_FP6-kernels),
replacing the previous set. It refuses to publish when:

- A file differs from the run's `artifacts.json`, or the candidate holds a file that is not part of
  a kernel set.
- A file contains private key material, a home or `/var/lib` path, or this machine's user or host
  name (add more strings with `--forbid`).
- The candidate carries a denied module, as a file or in a board makefile list.
- The run was built from a modified tools checkout or does not record its source commits.

- It rewrites the README's "This build" lines (the two source commits and the Linux version, the
  tools commit and configuration profile, the module, device tree and overlay counts, the build
  date).
- It does not commit or push: review the change, then commit and push.
- The next Android build that syncs the manifest picks up the new commit.
