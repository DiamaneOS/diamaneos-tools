# FP6 kernel

How the Fairphone 6 kernel set is built from source, checked and published, and the settings it holds
to. For people who change the kernel, its modules or its device tree. The commands are
`kernel prepare`, `kernel build`, `kernel compare` and `kernel publish`.

The Android build does not compile the kernel. It uses the published kernel prebuilts, which the
manifest checks out at `device/fairphone/FP6-kernel`. These commands make and publish that set, and
`build kernel` runs the first two for you ([building the kernel](BUILD.md#building-the-kernel)).

Run the commands from a checkout of these tools, or as `tools/diamaneos/bin/diamaneos` in a source
checkout. Terms are explained in [TERMS.md](TERMS.md).

## Sources

| Repository | Holds |
| --- | --- |
| [`kernel_common-6.18`](https://github.com/DiamaneOS/kernel_common-6.18) | The kernel: Google's `android17-6.18` common kernel with hardening, plus the FP6 commits. |
| [`kernel_qcom-6.18`](https://github.com/DiamaneOS/kernel_qcom-6.18) | The vendor tree: Qualcomm's drivers as external modules, with no copy of the core kernel. `modules/` holds the module packages, each with its registry (`modules.bzl`), `configs/` the configuration profiles, `kernel/` the device kernel fragments and `dts/` the device tree sources. |

[`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json) pins both repositories and
every toolchain part at exact commits. The URLs there are plain values that may change without a new
pin.

Never substitute Pixel kernel sources or turn off strict KMI, forced module signatures or the
sandbox checks.

## Prepare

```sh
bin/diamaneos kernel prepare --workspace "$KERNEL_WORKSPACE"
```

This checks out each pinned project in its own directory of the workspace (`vendor`, `common` and
`toolchain/*`), one commit with no history. Toolchain parts are partial, sparse clones. It checks the
core header copies (below) and writes `preparation.json`.

- The workspace must be empty or one an earlier preparation made. Edited sources and untracked inputs
  are refused, never reset.
- `--source NAME=PATH` fetches the pinned commit of one project from a local Git repository instead
  of its URL. The commit stays the pin, so the result is the same.

## Build

```sh
bin/diamaneos kernel build --workspace "$KERNEL_WORKSPACE" --jobs 16 --timeout 7200
```

`kernel build` runs only on a preparation of the pinned commits and checks every checkout again first.
It uses the Clang and Rust toolchain of the pins (`toolchain` in
[`config/fp6-kernel-build.json`](../config/fp6-kernel-build.json)) and starts from an empty
environment, so nothing of the calling shell reaches it.

- `--jobs` bounds the parallelism, and `--timeout` limits each command, not the whole run.
- Each run gets its own directory in `runs/`, with its logs and `result.json`. `current` moves to a
  run's `candidate` only after every stage passed. A retry is a new run.
- A second build in the same workspace is refused, and stopping the command stops every child process.

**Kernel release.** The release has the GKI form, for example
`6.18.38-android17-5-ge92c3f16af73-4k`.

`android17` and the KMI generation come from the common tree's `build.config.constants`, the commit
is the pinned one and `-4k` is the configuration's local version. libvintf reads the kernel's FCM
level from the `android17` part, so the build fails on another branch.

**Reproducibility.** The build time is the pinned common commit's time, and the build user and host
are fixed.

Build directory paths are stripped from the Image and the modules. pahole runs with one thread,
because its parallel BTF encoding orders types differently on each run. `result.json` records each
host program the build uses outside the toolchain, with its path, hash and version.

### Stages

`config/fp6-kernel-build.json` lists the stages. The vendor tree is extracted from the pinned commit
into the run directory, and generated files go there, never into the checkout.

1. **GKI core** (`core/`): `gki_defconfig`, the vendor tree's `configs/diamaneos_defconfig` and the
   profile's fragment, then `make Image modules`.

   This makes the Image, the GKI modules and the key the Image trusts for modules. The Image exports
   only the symbols the GKI symbol lists and the reviewed exceptions name
   (`CONFIG_TRIM_UNUSED_KSYMS`).
2. **Device kernel** (`device/`): the same configuration plus the vendor tree's
   `kernel/wireless_defconfig` and `kernel/platform_defconfig`, then `make modules`.

   It makes the cfg80211, mac80211, BAM DMA and cqhci modules the GKI build lacks, and every vendor
   module builds against it. It may differ from the GKI configuration only in the listed symbols.
3. **Vendor packages**, in the plan's order, each as external modules against the device kernel. The
   `Kbuild` of each package is made from its registry by `kernel_kbuild.py`, and the modules built must
   be exactly the ones the registry names.
4. **Packaging**, the device tree, the checks and the candidate, as described below.

Within those stages:

- Every request of a configuration fragment must be in the final `.config`, and the configuration must
  meet the [policy](#configuration-policy) before anything compiles.
- A vendor value that differs from the kernel configuration must be one of the plan's reviewed
  overrides, each stating the values the review saw. A change of either value fails the build.
- qcacld-3.0 builds with [`config/fp6-kernel-qcacld.config`](../config/fp6-kernel-qcacld.config), which
  must hold every value of the vendor's own defconfig. Its cfg80211 feature probes must give the
  results the plan expects.
- The core header copies listed in the vendor tree's `core-headers.json` files must equal the common
  kernel's byte for byte, because their structures cross the boundary between Image and modules.
- A struct declared inside a parameter list (`-Wvisibility`) in any build log fails the build.

### Profiles

| `--config-profile` | Configuration | Policy checked | Published |
| --- | --- | --- | --- |
| `production` (default) | The vendor tree's production profile | Baseline and production tiers | Yes |
| `development` | The same as production | Baseline only, with the production differences recorded | No |
| `debug` | The vendor tree's `configs/bringup_defconfig`, with the debug aids on | Baseline with the debug tier | No |

The build fails on any request the configuration does not honour, and says whether the common kernel
lacks the symbol or a symbol it depends on is off.

## Module packaging

[`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json) defines the module
selection, the partition of each module, the load lists, the kernel command line, the device trees
that ship (only `fp6.dtb`) and the overlay count. Each entry has its reason.

- A module comes from the vendor or device build, else from the core build. A name both make is
  refused.
- Every module is stripped of debug sections and signed with the Image's key. The run deletes its
  signing keys when it ends, pass or fail.
- A load list names only modules of its partition, and a module's dependencies sit in its own
  partition. vendor_dlkm may depend on system_dlkm.
- vendor_boot holds the first-stage modules, the recovery list and the common modules they depend on.
- `second_stage_modules` load from vendor_dlkm and the recovery list, never in the first stage. One is
  `dwc3-msm.ko`, so user builds start it with USB data off, while recovery and fastbootd keep USB.
- `required_modules` names a module that a vendor image file needs. The build fails while the file
  ships and the module is missing, or the other way round. The list is empty.

**Denied modules.** `denied_modules` lists what the FP6 never ships, in groups, each with its reason.
They cover hardware the FP6 lacks (CAN, 802.15.4, PCIe and MHI, HDMI), unused protocols and stacks,
other chips' drivers, debug and test interfaces, and CoreSight tracing.

- `kernel build` fails when a denied module is in any list (`-` and `_` match).
- A denied name the build does not make fails, unless its group marks it `absent`. A name marked
  absent that the build makes fails too, so a renamed copy cannot slip past.
- A removed module may have no remaining importer, because libmodprobe loads dependencies a list
  omits.
- Some modules stay because others import them, such as `qcom_glink_spss` for `glink_probe`. `sg`
  stays because the TEE reaches the UFS replay-protected memory through `/dev/sg*`.

**Kernel command line.** `BoardConfigKernel.mk` adds the words of `kernel_cmdline`, each with its
reason, and none may be an argument that `config/fp6-build.json` forbids.

The one word is `scsi_mod.scan=sync`: asynchronous scanning would number the UFS logical units
differently on each boot, and the device names its disks by letter.

**Symbol rules,** checked on every build:

| Rule | Meaning |
| --- | --- |
| `module_import_allowlist` | The only modules that may import a symbol. Only `dwc3-msm.ko` may import `register_kretprobe`. |
| `forbidden_symbols` | Symbols that must not exist in the built kernel's `System.map`. |
| `required_symbols` | Symbols that must exist, marking changes a rebase could drop. One with a `pending` reason names a change the pinned common kernel does not carry: the production profile counts it as missing, and `kernel publish` refuses such a run. |
| `kmi_exceptions` | Kernel symbols modules import although no GKI symbol list keeps them, each with its importers. |

### How the phone loads modules

First-stage init loads `modules.load` (or `modules.load.recovery` in recovery) from the vendor ramdisk
and skips the modules in that ramdisk's `modules.blocklist`. The recovery list names debug modules
that only this blocklist keeps out, so the vendor ramdisk must hold the same blocklist as
`vendor_dlkm`.

Second-stage init loads `vendor_dlkm` in parallel streams, one `modprobe` per
`modules.load.<stream>` list in `device/fairphone/FP6/boot/modules`. When the kernel prebuilts change
`modules.load`, change those lists with them.

## Module checks

- Every module carries the Image's release and vermagic, and a signature that verifies against the
  Image's built-in certificate.
- Every symbol version and namespace a module needs must match one provider in the set or the Image.
  No two modules export the same symbol, and the dependency graph has no cycle.
- **Strict KMI.** Every kernel symbol a vendor module or a device kernel module imports must be in the
  GKI symbol lists or in `kmi_exceptions`. An exception no longer needed fails the build.
- The Image is trimmed to the same list, so a symbol a shipped module imports must be among the
  Image's exports.

These checks prove symbol coverage, load order and signatures. None proves that a module loads, that
firmware runs or that the phone boots.

## Device tree

`kernel build` builds `fp6.dtb` and the overlay image `dtbo.img` from the vendor tree's `dts/`, with
its `scripts/build-fp6.sh`, the pinned Clang as preprocessor and the common tree's `dtc`.
[`config/fp6-kernel-dt.json`](../config/fp6-kernel-dt.json) holds the result to the tested pair:

- It pins the tested `fp6.dtb` and `dtbo.img` by SHA-256. A change to the device tree sources stops
  the build until the new pair is tested on a phone and the pins are updated.
- `dtbo.img` holds the overlays in name order, as many as the packaging recipe names. Qualcomm's boards
  stay next to the FP6 ones, so the entry numbers never move.
- `forbidden_properties` may not appear in the tree or in any overlay for its SoC. The one exemption is
  an emulator board overlay that no FP6 board matches.
- `disabled_nodes` must stay disabled, found by compatible: the EUD debugger, the clock debug driver,
  the FM radio and the crypto engine. An enabled node without its driver keeps its suppliers from
  sync_state, which keeps the SoC out of deep sleep.

The disabled-node check covers the tree and every overlay the bootloader can apply to its SoC. It
also fails when an overlay enables a listed node, or targets a node the tree does not resolve.

**Changes from Fairphone's device tree.** Fairphone's source targets its own kernel, so the vendor
tree's device tree differs where 6.18 drivers need it:

| Change | Why |
| --- | --- |
| `nvmem-modem-cpucp` | The 6.18 nvmem core rejects the stock bit ranges, the modem takes its memory region at another index, and the CPUCP driver looks its regions up by name. |
| `explicit-cells` | Parents without `#address-cells` or `#size-cells` get the values that were implied, so parsing stays the same without warnings. |
| `static-ramoops` | 6.18 no longer places ramoops dynamically, so the region is fixed at `0x9fe000000` (4 MiB). |
| `crypto-engine-off` | 6.18 has no qcedev driver, so the crypto engine node is disabled, and the interconnects reach sync_state. |
| `sdhc-supplies`, `sdhc-voltages` | The SD card driver takes its supplies as `vmmc` and `vqmmc`, held to 2.96 V. |
| `usb-ipa-path` | The USB to IPA path names the `cnoc_main` interconnect provider. |

## Configuration policy

The configuration is `gki_defconfig`, then `configs/diamaneos_defconfig` (the DiamaneOS hardening, in
every profile) and the profile's fragment. [`config/kernel-policy-fp6.json`](../config/kernel-policy-fp6.json)
is checked against the Image configuration right after configuring (report `kernel-config.json`).

The **baseline** tier always applies. It requires, among others:

- CFI, shadow call stack, kernel PAC and BTI, strong per-task stack protector, random kernel stack
  offset and KASLR.
- Hardened usercopy, fortify, stack and heap initialization, page table check and zeroed call-used
  registers.
- A hardened heap: random kmalloc caches, slab buckets and canaries, freelist hardening, no slab
  merging, and KFENCE.
- List hardening that stops on corruption, a strict IOMMU, sealed system mappings, UBSAN bounds checks
  and panic on oops.
- The lockdown LSM from early boot at integrity level, forced SHA-256 module signatures and no user
  namespaces.
- The Rust binder without the C binder, debugfs not mountable, pstore with ramoops, trimmed exports.
- Gunyah VM memory lent before the VM starts (`GUNYAH_LEND_UPFRONT`), since the FP6 resource manager has no
  demand paging. Its VM functions (irqfd, ioeventfd) serve Android's protected VMs.
- No `/dev/mem`, kexec, KGDB, hibernation or TIPC.

The **production** tier adds dmesg restricted from boot and `CONFIG_UBSAN_TRAP`, so UBSAN stops at
the fault, and refuses lockdep and the lock debug options. The **debug** tier replaces the baseline
rules the debug aids need and may not name a production rule.

A rule value `off` accepts `n` or a symbol the configuration does not mention. `not-built-in` also
accepts `m`. Missing required symbols and duplicate assignments fail. To check any `.config`:

```sh
bin/diamaneos build kernel-config --config "$KERNEL_CONFIG" \
  --policy config/kernel-policy-fp6.json --profile production
```

[`config/kernel-vendor-policy-fp6.json`](../config/kernel-vendor-policy-fp6.json) is checked against
the configuration the vendor modules build with (`vendor-role-kernel-config.json`).

It requires QRTR as a module without the tunnel, no STM, no CoreSight built in, no QDSS USB function
or EUD, and Qualcomm's download mode off by default, which alone decides whether a panic dumps RAM.

### Deliberate settings

| Setting | Why |
| --- | --- |
| Lockdown at integrity level | Android reads DMA-BUF statistics with a BPF program, which confidentiality level breaks. |
| CoreSight `not-built-in` | The GKI configuration builds it as modules, so the deny list keeps the modules out. |
| `CONFIG_DEBUG_FS_DISALLOW_MOUNT` | debugfs cannot be mounted. Kernel drivers keep the debugfs API, through a fix in `kernel_common-6.18`. |
| KPROBES on | The USB glue (`dwc3-msm`) hooks the built-in dwc3 core with kretprobes and ignores registration failures, so without kprobes its hooks silently vanish. |
| SELinux development mode | An update's first boot can hand the snapshot daemon over before enforcing. User builds never go permissive, because init refuses it. |
| Forced module signatures | Every module must carry a signature from the Image's own key. GKI module protection (`MODULE_SIG_PROTECT`) is off. |
| `CONFIG_ARM64_VA_BITS_48` | The hardened memory allocator reserves an isolated region per size class, which does not fit the 512 GiB of a 39-bit address space. Every module is built for this page-table depth. |
| `CONFIG_QRTR_IMSDCM_OWNERSHIP` not checked | The vendor tree declares it, but no source implements it. |
| `CONFIG_TRIM_UNUSED_KSYMS` | Trims the exports to the GKI symbol lists and the reviewed exceptions, with the import check above. |

## Debug profile

`--config-profile debug` builds a test kernel from the same tree, so a regression found on a
production build can be reproduced with the debug aids on.

The debug aids are tracing, kprobes and uprobes, KFENCE, UBSAN, the lock, stall and hung task
detectors that panic, mountable debugfs and no lockdown.

- The vendor_boot command line adds the `debug` arguments of the packaging file: a full log level, a
  4 MiB log buffer, panic on RCU stalls, restart 5 seconds after a panic and Qualcomm's download mode.
- vendor_boot, vendor_dlkm and the load lists keep five of the debug modules the production deny list
  drops. The clock debug module stays out, because its device tree node stays disabled.
- The strict KMI check also accepts the symbols the debug checks make modules import, as long as some
  module imports each.
- The candidate gets its own `README.md`, and `result.json` records `config_profile` and `debug_build`.

`kernel publish` refuses a debug run, so debug output never reaches the prebuilts.

## Compare builds

Two builds of one pin, each in its own workspace, must package the same files with the same bytes:

```sh
bin/diamaneos kernel compare --run "$WORKSPACE_A/runs/<run>" \
  --against "$WORKSPACE_B/runs/<run>"
```

Each build makes its own module signing key, so a module may differ only in its appended signature,
and the Image only in its built-in certificate and the build ID that hashes it.

To compare with a kernel set built another way, in the layout of the prebuilts repository plus a
`vendor_cmdline` file and a `config` file:

```sh
bin/diamaneos kernel compare --run "$KERNEL_WORKSPACE/runs/<run>" \
  --reference /path/to/set --dtc /path/to/dtc
```

Separate builds differ in module bytes, so this compares what the Android build and the kernel read:

- the module names and their loading metadata, the lists and their order
- the decompiled device trees, and the overlay image byte for byte
- the command line and the Image configuration

Modules the reference ships and the deny list leaves out are reported as policy, and anything else
fails.

## Publish a kernel build

```sh
bin/diamaneos kernel publish --run "$KERNEL_WORKSPACE/runs/<run>" \
  --to /path/to/device_fairphone_FP6-kernels
```

`kernel publish` copies a passed run's candidate (`Image`, `dtbo.img`, `dtbs/`, `modules/`,
`BoardConfigKernel.mk`, `device-kernel.mk` and the module blocklists) into a clean checkout of
[`device_fairphone_FP6-kernels`](https://github.com/DiamaneOS/device_fairphone_FP6-kernels),
replacing the previous set. It refuses when:

- a file differs from the run's `artifacts.json`, or is not part of a kernel set
- a file holds private key material, a home or `/var/lib` path, or this machine's user or host name
  (`--forbid` adds more strings)
- the candidate carries a denied module
- the run came from a modified tools checkout or does not record its source commits
- the run is not of the production profile, or reports pending required symbols

It updates the "This build" lines of the README and does not commit or push. Review the change, then
commit and push it. The next Android build that syncs the manifest takes the new commit.
