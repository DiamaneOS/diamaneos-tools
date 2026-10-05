# Changelog

## Unreleased

- `config/repositories.json` marks the EmergencyLocation fork retired; the
  DiamaneOS manifest does not select it. The threat model's statuses follow the
  2026-10-05 build and phone tests, and the README, the threat model and the
  contribution guide use one disclaimer that names GrapheneOS and Fairphone.
- Stop selecting Qualcomm's perf2 daemon, its init and VINTF files, its nine
  configuration files and the ten client, plugin and interface libraries only
  it and its client used (22 files). The device builds LineageOS's libperfmgr
  power HAL instead, running as system with CAP_SYS_NICE only, and a no-op
  `libqti-perfd-client` for the stock camera and SDM extension, which load the
  client by name. The renderer no longer derives the perf configuration. Image
  checks require the HAL, its init override, sched_boost triggers, labels,
  property contexts, node grants and the stub's exports, and the absence of the
  perf2 stack, its declarations and the CodeLinaro power HAL.
- Require the FP6 CarrierConfig overlay in the image checks. It is read after
  the stock carrier data and turns off the stock world-phone flag, so Settings
  shows Android's network-type list with GrapheneOS's LTE-only and 5G-only
  options. The threat model and carrier notes record the source review of the
  cellular hardening controls: the 2G and LTE-only settings reach the modem's
  network preference; the radio software answers "not supported" to the
  null-cipher control and predates security notifications, so Settings hides
  both.
- Stop selecting Qualcomm's tftp_server, its init file and socket library, and
  its pd-mapper. The device builds the open-source linux-msm tqftpserv (our
  fork) and pd-mapper instead (manifest `vendor/qcom/opensource`); Qualcomm's
  libqrtr.so stays for the stock libraries that link it, and pd-mapper loads
  the linux-msm library as libqrtr_linux_msm.so. `config/forks.json` and
  `config/repositories.json` list the tqftpserv and qrtr forks and the pinned
  pd-mapper. Image checks require the new daemons, their users, labels, init
  lines and file links, the forked tqftpserv with libqrtr linked in, the
  unchanged stock libqrtr.so, and no stock tftp_server, capability or network
  grant for either daemon.
- Follow the kernel's imported upstream projects through `kernel_qcom-6.1`.
  The vendor kernel, techpack drivers, two device trees and edk2 are sources in
  `config/forks.json` instead of forks, pinned in the new
  `config/kernel-upstream-fp6.json`: the upstream commit each folder was
  imported from, the newest release tag it contains and whether it carries
  Fairphone's FP6 changes. `forks check` reports a moved followed branch and
  release tags after the recorded one (`newer_tags` with a `release_field` pin).
  `config/repositories.json` marks the 21 one-project kernel forks retired.
- Derive the sensors multi-HAL configuration (`hals.conf`) with only the
  Qualcomm sub-HAL. The AOSP dynamic-sensor sub-HAL parses HID sensor
  descriptors from Bluetooth and USB devices but has no hidraw access here;
  the device stops installing it and declaring the head-tracker feature. Image
  checks require the derived list, the absence of the sub-HAL and feature
  file, and no /dev listing or mock-sensor property grants for the sensors HAL.
- Stop selecting the stock Bluetooth HCI service and the eight FM, ANT, SAR,
  config-store and TPI libraries it links but never registers. The device's
  own service (device `bluetooth/service.cpp`) registers the stock HCI
  implementation, now a runtime root. Image checks require our service, its
  label and init line, and the absence of the stock service and those
  libraries.
- Stop installing the stock display colour service and its two interface
  libraries. It was a lazy service any platform app could start, and nothing in
  the build is its client; the composer still loads the colour libraries and
  `libsdm-disp-vndapis` itself. Image checks require the service and its init
  file to be gone and no init file to offer IDisplayColor or IDisplayPostproc.
- Stop declaring the camera provider's offline camera service. Nothing uses
  it; its library stays because the CHI override links it, and the provider
  logs the refused registration and carries on. Image checks require the
  declaration, the provider's interface line and the registration grant to be
  gone.
- Take the vendor patch level from the stock vendor image. The vendor step
  reads `ro.vendor.build.security_patch` from the stock `vendor/build.prop`,
  which `vendor-files.json` pins under `build_properties`, instead of a fixed
  value. It must be set once, to a real date no later than the factory
  package's release date; the generation's provenance and the vendor step's
  state record it. `build verify` checks that target-files and `vendor.img`
  report it.
- Build from the DiamaneOS manifest. The FP6 build environment names the full
  manifest (`platform_manifest`, branch `android17`) instead of an overlay
  with pinned commits; `build sync` runs `repo init` and `repo sync` on it,
  checks the tree and records the resolved manifest, and `build all` syncs to
  the branch head first. The overlay composition code, `--objects-from` and the
  GrapheneOS signer download in the sync step are gone.
- Take the kernel from the published kernel prebuilts in the manifest
  (`device/fairphone/FP6-kernel`). `build all` no longer builds the kernel;
  `build kernel` stays for maintainers. build.json names the manifest commit,
  the resolved manifest (copied into the image set) and the kernel prebuilts
  commit, and `build verify` checks the images against that checkout.
- Prepare the kernel from the `kernel_qcom-6.1` repository: one pinned commit,
  its common-kernel submodule and the toolchains in its `prebuilts.json`. The
  per-project source plan, the kernel patch entries, the workspace link rules
  and `kernel manifest` are gone. Add `kernel publish`, which copies a kernel
  build into a checkout of the kernel prebuilts repository with its checks.

- Carry a frameworks/native fork (`84fc898`): a custom sensor whose HAL names
  no permission keeps the Sensors permission, so the FP6's colour sensor can no
  longer be read with the Sensors permission off (-153).
- Keep `param_name_len` out of init memory in both kernel trees (common
  `4774098`, msm-6.1 `9793cb3`): `run_init_process` calls it after init memory
  is freed. The `forbidden_symbols` rule that guarded the inlined copy goes.
- Pin kernel fork revisions that keep the debugfs interface for kernel code
  while refusing every debugfs mount (kernel policy v8 requires
  `CONFIG_DEBUG_FS_DISALLOW_MOUNT`), stop the Wi-Fi platform driver logging
  MAC addresses, give camera.ko a fixed build banner instead of the build user,
  host and time, and drop a 10 s sleep from the touch driver's probe.

- Move files with no public use out of the public tools: early planning records,
  the carrier test plan with `diamaneos carrier matrix validate`, host-specific
  scripts, the device test stack, the reference build-host deployment with its
  qualification build, the stock phone hardware report and
  config/upstreams.json. `diamaneos baseline`, `rig`, `test run` and
  `test compatibility` are gone; BUILDING.md is the public build path. The
  signing contract and `diamaneos signing` stay public.
- Move the FP6 component decision model (config/components.json, its schemas,
  check, tests and docs/COMPONENTS.md) out of the public tools. The vendor step
  now checks the selected files against the stock image recipe and no longer
  writes component-closure.json; `diamaneos components validate` is gone.
  Generic strict JSON loading moves to safe_json.py.
- Add the public build commands. `diamaneos build all` builds a Fairphone 6
  test image in one workspace as an ordinary user: it syncs the source at the
  pinned commits, builds the kernel, extracts the stock files from Fairphone's
  factory package, builds Android with network access off, packages one
  coherent image set with deterministic wipe images and checks it. Each step
  can run alone, is skipped when its inputs did not change and resumes after
  a failure; `--dry-run` prints the plan. `diamaneos flash-steps` prints the
  fastboot commands for a verified test build. New guide docs/BUILDING.md;
  docs/BUILD.md becomes the reference and absorbs FP6-PREPARATION.md.

- Bind the generated vendor and kernel inputs to the build environment and the
  recipes that made them. The full preflight accepts the two generated
  directories only while that descriptor matches.

- The FP6 build environment now pins the published development line only:
  the public manifest overlay and the android17 head of every DiamaneOS
  project on 2026-09-30, including a build/make fork whose one change keeps
  the device's zero boot header fields when release tools rebuild boot,
  init_boot and recovery.

- The `flash-steps --wipe` steps clear userdata, metadata, FRP and misc by
  flashing images, as Fairphone's factory package does, never with
  `fastboot -w` or `fastboot erase`.

- Move the earlier per-build device checks into config/fp6-image-checks.json,
  the PSTORE and debugfs lines into kernel policy v7, and the kretprobe and
  param_name_len checks into the kernel build. `vendor extract --record-tools`
  accepts image tools built from the pinned source and records their hashes.

- Move the stock input to Fairphone FP6.QREL.16.111.0 (2026-09-05 security
  patch). 13 of the 729 selected files changed (camera, GPU and video
  firmware, the QCRIL database, carrier configuration and APNs); none went
  missing, and no changed library gained or lost a dependency or symbol. Add a
  per-image inventory of the firmware in the 16.100.0 and 16.111.0 factory
  packages.

- Document the FP6 firmware partitions, the stock flash script and the
  rollback rules (docs/FIRMWARE.md).

- Add `fonts check`: read a product `fonts_customization.xml` and its fonts
  as Android does at boot, so a file that would drop every system font, stop
  boot or draw the wrong weight fails before a build. It checks either the
  build module (what it installs to /product, what it requires, the recorded
  SHA-256 of each font) or the files of a built image.

- Add `overlays check`: compare each resource overlay with its target's
  resources at a GrapheneOS release (from a source tree or fetched at the
  pinned commits), including overlayable policies, partitions, signing and
  priorities within a partition, with a JSON report for CI. Product overlays also follow the
  agreed allowlist, denylist and qualifier-coverage rules; target variants that
  always win on the FP6 (API level, density, smallest width) are errors; git
  runs isolated from the caller's environment and configuration; and a tag
  other than the pinned one needs a verified signature.

- Install the stock touch controller firmware, and turn the stock LPA's eSIM
  service off again without its JNI library: it cannot list profiles on the
  FP6, and installed eSIM profiles keep working as SIMs.

- Build the FP6 kernel with the production configuration by default and check
  the vendor configuration too; keep unused modules out with a deny list that
  survives list regeneration; retire the NXP/ST NFC, ST eSE, mm-sys and Kleaf
  build forks.

- Pin the 2026-09-27 kernel commits (no SELinux development mode, dmesg restricted,
  boot parameters handed to user space logged by name only) and a new fork of
  the SoC device tree that gives pstore/ramoops a memory region on the FP6.
  Keep debugfs as before, pin the kprobe, firmware-fallback and debugfs
  settings that hardware support depends on, and correct the docs: the
  development profile only relaxes the configuration check.

- Move the interface design, branding and their bundled assets out of this
  repository.

- Bind explicit FP6 recovery setup and narrower UFS policy in a new product
  environment. Reject missing kernel verification tools before compilation.

- Reconstruct selected vendor files directly from pinned stock images; prepare,
  build and package the complete source kernel/module/DT set through public
  commands. Install generated inputs with inventory verification, preserve
  failed runs and reject edited sources or incompatible modules.

- Bind the native FP6 candidate to source-owned policy, explicit GPU firmware
  dependencies and a reduced optional performance-library selection. Document
  the pinned kernel rebuild and verified development image boundaries.

- Generate the selected FP6 native Android modules, service activation and
  configuration from authenticated stock inputs with atomic publication and
  explicit derived configuration provenance.

- Use GitHub as the authoritative source host and bind the updated FP6 overlay
  in a new candidate environment; retain independent Git backups.

- Authenticate an explicitly pinned additive source overlay while preserving
  upstream release verification and rejecting undeclared source inputs.

- Add selected stock-file generation with exact hashes, component-policy and
  dependency validation, retained image metadata and notices, and atomic
  publication. Reject unsafe paths and preserve prior output after failure.

- Verify source-built compatibility packages with explicit build provenance and
  retain their delivery type in package proofs. Parse bounded literal internal
  entities used by VTS while rejecting external and nested entities. Pin the
  derived ARM64 VTS package and its build/patch evidence.

- Reject undeclared source inputs between manifest projects, altered manifest
  exports and a manifest checkout at a different commit during build preflight.
- Document the Fairphone QSSI, API-level and kernel ABI boundaries for product
  integration without inheriting Pixel device defaults.

- Add authenticated, repeatable stock-image staging with bounded image selection,
  atomic publication and preservation of prior generations on extraction failure.

- Add a declared FP6 software-restart baseline with three monotonic-clock
  samples per repetition, separately retained Android boot milestones and an
  explicit manual-source-media boundary for physical cold power-on timing.
- Accept either the boot-animation exit property or the stopped boot-animation
  service as the recorded restart completion signal used by stock FP6 builds.
- Give the bounded declared connected report enough space for its full thermal
  observation series while retaining a strict report-size limit.
- Add declared FP6 connected and camera measurement modes with exact protocol
  repetitions, series identity, staged original-media registration and explicit
  non-comparable outcomes.
- Add declared FP6 idle measurements with two series-bound eight-hour
  repetitions and an observed reconnect-before-capture flow.
