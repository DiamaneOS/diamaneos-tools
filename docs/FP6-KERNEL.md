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
- C file names and Rust paths lose the prefix of every build directory (`-ffile-prefix-map` and
  `--remap-path-prefix`), so no build directory ends up in the Image or a module.
- pahole runs with one thread, because its parallel BTF encoding orders types differently on each
  run and changes the Image and every module with it.
- `result.json` records the host programs and libraries the build uses outside the pinned toolchain
  (make, perl, openssl, python3, git, bash, rsync, tar, modinfo, modprobe, nm, readelf, libdw and
  libelf), each with its path, a SHA-256 and the first line of its version.
- The vendor tree is extracted from the pinned commit (`git archive`) into the run directory.
  The generated `Kbuild` files go there, never into the checkout.

[`config/fp6-kernel-build.json`](../config/fp6-kernel-build.json) lists the stages. In each run:

1. The GKI core build (`core/`): `gki_defconfig`, the vendor tree's `configs/diamaneos_defconfig`
   and the profile's fragment, then `make Image modules`. This makes the Image, the GKI modules
   and the key the Image trusts for modules (`MODULE_SIG_FORCE`).
   - The Image exports only the kernel symbols the GKI symbol lists and the reviewed exceptions
     name, and what the modules of the common tree use (`CONFIG_TRIM_UNUSED_KSYMS`, as GKI
     builds do). The tools write the symbol list for the run (`kmi-whitelist`).
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
     on top of the base layer's and the kernel's. A missing configuration file is an error.
   - A vendor value that differs from the kernel configuration must be one of the reviewed
     overrides of the plan: drivers the vendor tree builds as modules where the kernel has them
     off. A listed override that no longer differs fails too (`vendor-overrides.json`).
   - The modules built must be exactly the ones the registry names.
   - qcacld-3.0 builds with the effective configuration in
     [`config/fp6-kernel-qcacld.config`](../config/fp6-kernel-qcacld.config), which must hold every
     value of the vendor's `fps_gki_qca6750_defconfig`. It holds no kernel symbol.
   - The cfg80211 feature probes of the plan read the common sources, and each declares the result
     it expects. A change fails the build. The header the probes make is saved in the run.
4. The module set (below), the device tree (below), the checks (below) and the candidate.

- A struct declared inside a parameter list (`-Wvisibility`) in any build log fails the build.
- The core header copies listed in the vendor tree's `core-headers.json` files must stay byte for
  byte equal to the common kernel, because their structures cross the Image and module boundary.
- `--config-profile production` is the default and the one `diamaneos build kernel` builds. It
  enforces the whole policy, and only a production run is published.
- `--config-profile development` builds the same vendor profile and enforces the baseline only.
- `--config-profile debug` builds with the debug aids on (see Debug profile below).
- Every profile builds TIPC as a module (`config/fp6-kernel-tipc.config`) without the UDP bearer,
  crypto and diag options. All but debug ask for `CONFIG_TIPC_LOCAL_ONLY`.
- The files of `tool_fragments` in the build plan come after the vendor profile's fragment. The
  build fails on a request the configuration does not honour, and says when the pinned common
  kernel does not declare the symbol.

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
- `required_modules` names a module that a vendor image file needs:
  - `tipc.ko` is in system_dlkm and in its load list, as on 6.1, because `nicmd` opens AF_TIPC
    sockets to reach the radio data service and every data call fails without it.
  - The module declares no `net-pf-30` alias, so nothing autoloads it and the load list is what
    loads it.
  - The build fails while `vendor/bin/nicmd` ships (`config/fp6-minimal/vendor-files.json`) and the
    module is missing from the partition or from its load list.
  - A reference comparison reports such a module the reference lacks as policy.

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
- A denied name the build does not make fails, unless the group marks it `absent` (the 6.18 sources
  do not build it). A name marked absent that the build makes fails, and the mark must go. So a
  renamed copy cannot slip past the list.
- A cut needs no remaining importer, enforced by the build's dependency check
  (`module-interfaces.json`), since libmodprobe loads dependencies a list omits.
- `result.json` records the absent denied modules (`denied_modules_not_built`).
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
  - The development and debug profiles report it in `result.json`. The production profile counts it
    as missing.
  - A pending symbol that is present fails the build, so a stale reason cannot stay.
  - `kernel publish` refuses a run that reports pending symbols.
- `kmi_exceptions`: kernel symbols modules import although no GKI symbol list keeps them. Each names
  its importers, and the modules found must be exactly those.

## Module checks

- Every module carries the Image's release and the same remaining vermagic.
- The built-in certificate of the Image is read from `vmlinux`. Every packaged module must carry a
  signature that verifies against it.
- Every symbol version (CRC) a module requires and every namespace it imports must match one
  provider in the packaged set or the Image. A module may not export a symbol another selected
  module exports, and the dependency graph must have no cycle (`module-interfaces.json`).
- Strict KMI: every kernel symbol a Qualcomm module or one of the device kernel's four modules
  imports must be in the GKI symbol lists or in `kmi_exceptions` (`kmi` in `result.json`).
  - The lists are the ones the common tree's `BUILD.bazel` names (`gki/aarch64/symbols`).
  - An exception that is no longer needed fails the build.
- The kernel is trimmed to the same list: the GKI lists and the exceptions are the symbol list of
  `CONFIG_TRIM_UNUSED_KSYMS`. A kernel symbol a shipped module imports must be among the exports in
  the Image's symbol table.
- The GKI modules of the common tree are not held to the lists. The build trims the Image to what
  they use.
- These checks establish provider CRC and namespace coverage, dependency order, certificate and
  signature binding and the symbol rules. None proves module insertion, firmware execution or
  boot.

## Device tree

The sources hold no 6.18 device tree. The set ships the 6.1 base device tree and overlay
image, pinned in `config/fp6-kernel-dt.json` by commit and SHA-256, and `kernel build` edits the
base through the fix-ups of that file (`src/diamaneos_tools/kernel_dt.py`):

- Every edit states the value it expects. A different base stops the build.
- The result must differ from the base in exactly the edited properties and in no node
  (`device-tree-fixups.json` lists every change).
- A new property goes first in its node, as `fdtput` adds it.
- The overlay image is the base one and must hold the number of overlays the packaging recipe
  names.
- No property named in `forbidden_properties` may be anywhere in the result (`qcom,disable-lpm`,
  which UFS low power made obsolete).
- The kernel build tools' `dtc` must decompile the result.
- Nodes of drivers that do not ship must be disabled (`disabled_nodes` in the plan, each with its
  reason, found by compatible): the EUD debugger, the clock debug, the FM radio and the crypto
  engine. An enabled node without its driver keeps its suppliers from sync_state.
  - The check covers the fixed base and every overlay for the base's SoC, as the bootloader applies
    them. An overlay without a SoC list applies to all.
  - It also fails when an overlay enables a listed base node that it targets by path, phandle or
    symbol.

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
- `crypto-engine-off`:
  - 6.18 has no qcedev driver, so the crypto engine node never binds.
  - Both interconnect providers then wait for it in sync_state and keep their boot votes, and the
    SoC never reaches AOSD or CXSD.
  - No domain may open `/dev/qce` on these builds, so the node is disabled.
- `sdhc-supplies`:
  - The 5.0 `sdhci-msm` takes the card supply and the I/O supply as `vmmc` and `vqmmc`. The 6.1 tree
    names them `vdd` and `vdd-io`, so the driver finds none and never powers the card.
  - The node gets `vmmc-supply` (the pm7550 l13 regulator) and `vqmmc-supply` (l23).
  - The edit asserts the node's compatible and reg, both regulator phandles and that neither
    property exists.

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
  - Exports trimmed to the symbol lists (`CONFIG_TRIM_UNUSED_KSYMS`).
  - TIPC as a local-only module for Qualcomm's data stack, without the UDP bearer, crypto and diag.
- The `production` tier adds dmesg restricted from boot.
- `--config-profile development` checks only the baseline and records the production
  differences.
- The debug tier replaces baseline rules the debug aids need and may not name a production rule.
- A rule value `off` accepts `n` or a symbol the configuration does not mention (hidden by an
  option that is off). `not-built-in` also accepts `m`.
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
- CoreSight not built in, and no QDSS USB function, FunctionFS IPC logging or EUD.
- Qualcomm's download mode off by default (`CONFIG_POWER_RESET_QCOM_DOWNLOAD_MODE_DEFAULT`), which
  alone decides whether a panic dumps RAM.

Settings that differ from the 6.1 kernel:

| 6.1 | 6.18 |
| --- | --- |
| `CONFIG_CFI_CLANG` | `CONFIG_CFI` |
| `CONFIG_RANDSTRUCT_FULL` | `CONFIG_RANDSTRUCT_NONE`: 6.18 builds Rust only without RANDSTRUCT. The Rust binder (`CONFIG_ANDROID_BINDER_IPC_RUST`, no C binder) replaces the most attacked IPC driver with memory-safe code. The structure layout scan is gone with RANDSTRUCT |
| lockdown at confidentiality level | lockdown at integrity level: Android reads DMA-BUF statistics with a BPF program, which confidentiality breaks |
| `CONFIG_DEBUG_CREDENTIALS`, `CONFIG_CRYPTO_SHA1_ARM64_CE` | gone from 6.18 |
| TIPC as a local-only module | `baseline`: `CONFIG_TIPC_LOCAL_ONLY` is a DiamaneOS patch of the common kernel. Every profile builds TIPC as a module, the debug profile leaves the option off, and the bearer, crypto and diag options stay in the baseline as `off` |
| `CONFIG_CORESIGHT` off | the GKI configuration builds it as modules, so the baseline requires `not-built-in` and the deny list keeps `coresight*.ko` out |
| `CONFIG_QRTR_IMSDCM_OWNERSHIP` | the vendor tree declares it and no source implements the IMS role guard, so it is not checked |
| `CONFIG_USB_CONFIGFS_F_QDSS`, `CONFIG_USB_F_FS_IPC_LOGGING`, `CONFIG_QCOM_EUD` | the symbols do not exist in the 6.18 sources, the baseline requires `off` |
| Kleaf KMI trimming and ABI comparison | `CONFIG_TRIM_UNUSED_KSYMS` with the GKI symbol lists and the reviewed exceptions, and the KMI import check above |

- `CONFIG_DEBUG_FS_DISALLOW_MOUNT` is a baseline setting. It makes `debugfs_create_*` fail since
  Linux 5.12 unless `fs/debugfs/inode.c` carries the DiamaneOS fix. The 6.18 common kernel does
  not carry it, so a kernel from this configuration can fail to bind vendor drivers that need
  debugfs.
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

## Debug profile

`--config-profile debug` builds a test kernel on the same tree, so a regression found on a
production build can be reproduced with the debug aids on.

- The configuration is the same base (`gki_defconfig`, then `configs/diamaneos_defconfig`) with the
  vendor tree's `configs/bringup_defconfig` as the profile fragment.
- The debug tier of the policy checks what the fragment turns on:
  - Tracing, kprobes and uprobes, KFENCE, UBSAN reports and the lock, stall and hung task detectors
    that panic.
  - Mountable debugfs, no lockdown and `CONFIG_PANIC_TIMEOUT=5`.
- A debug rule replaces the baseline rule for the same symbol and never names a production rule.
- The vendor_boot command line adds the arguments in the `debug` section of the packaging file:
  - `loglevel=8` and `log_buf_len=4M`.
  - `sysctl.kernel.panic_on_rcu_stall=1` and `panic=5`.
  - The two download mode arguments and `reboot=panic_warm`.
- TIPC is a module as in the other profiles, and `config/fp6-kernel-tipc-open.config` turns the
  local-only option off, as in the bring-up Image.
- The vendor_boot and vendor_dlkm partitions and the load lists keep five of the debug modules the
  production deny list drops, right after `qrtr-smd.ko`. The clock debug module stays out, because
  its device tree node stays disabled in every profile.
- The strict KMI check also accepts four symbols that the atomic sleep checks and UBSAN reports make
  modules import. They name no importer, and the build fails when no module imports one.
- The candidate has its own `README.md`, `result.json` records `config_profile` and `debug_build`,
  and `kernel compare --reference` accepts the bring-up arguments of a debug run.
- `kernel publish` refuses a debug run, so debug output never reaches the prebuilts.

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

`--dtc` is required.

The reference is a directory in the layout of the prebuilts repository.

- A `vendor_cmdline` file holds its kernel command line and a `config` file its Image configuration.
- The modules of two separate builds differ in bytes, so the report compares what the Android build
  and the kernel read:

- The module names, and per module the fields that decide loading (`depends` as a set, `alias`,
  `firmware`, `license`, `softdep`, `import_ns` and the parameters).
- Every module and load list of `BoardConfigKernel.mk`, with the load order.
- The device trees after decompiling (the sets of names must match), and the overlay image byte for
  byte. A difference in either fails the comparison.
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
- The run is not of the production profile, or reports pending required symbols.

- It rewrites the README's "This build" lines (the two source commits and the Linux version, the
  tools commit and the policy profile it met, the module, device tree and overlay
  counts, the build date).
- It does not commit or push: review the change, then commit and push.
- The next Android build that syncs the manifest picks up the new commit.
