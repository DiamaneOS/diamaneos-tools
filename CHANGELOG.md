# Changelog

## Unreleased

- The Moments switch's kernel floor for the cameras: the switch driver loads
  in recovery too (the laser range finder's driver needs it). Image checks:
  the camera driver and the range finder register with it
  (`privacy-switch-camera-client`, `privacy-switch-tof-client`), and
  camera.ko has no CDM reach probe (`camera-no-cdm-reach-probe`). The threat
  model's Tally rules describe the camera floor and its residuals.
- The Moments switch's kernel floor for the microphones: the kernel packaging
  loads the privacy switch driver (`diamaneos_privsw.ko`) from the vendor
  ramdisk. Image checks: the switch's device-tree node belongs to the driver
  and cannot be disabled from sysfs (`privacy-switch-devicetree`), the driver
  and both audio codec clients are built (`privacy-switch-module`,
  `privacy-switch-lpass-client`, `privacy-switch-wcd-client`), init seals the
  policy once per boot (`privacy-switch-sealed-at-boot`), and only init writes
  it and only system_server sets its property (`privacy-switch-policy-writers`,
  `privacy-switch-policy-property`). The threat model's Tally rules describe
  it.
- CamX camera core dumps are off (`camxoverridesettings.txt`: text, binary and
  their offline logging). Each camera recovery wrote 3–4 MB of camera state,
  metadata and logs to `/data/vendor/camera/coredump`, and writing one could
  hang the provider. Image check `camera-no-core-dumps`. CamX still created
  an empty folder there for every `dumpsys media.camera`; image check
  `camera-core-dump-dir-closed` requires the device to close the directory
  to the provider.
- The camera provider joins init's `cameraWatchdog` class, so it restarts
  whenever cameraserver restarts (cameraserver.rc), as AOSP intends: a
  provider left with a hung camera session no longer outlives a cameraserver
  restart. Image check `camera-provider-restarts-with-cameraserver`.
- Canvas (the screenshot editor) is now a fork (`canvas`, replacing the
  pinned source `lineage-canvas`): for a screenshot from SystemUI's Edit, Done
  replaces Save (Save, Copy and delete, Delete). The threat model's Tally rules
  cover it and the screenshot preview's new Delete.
- Register LineageOS's Glimpse (the gallery) as a fork without network
  access (`glimpse`) and Canvas (the screenshot editor) as a pinned source
  used unmodified through a DiamaneOS mirror (`lineage-canvas`). The overlay
  allowlist lets the product's SystemUI overlay name Canvas as the preferred
  screenshot editor (`config_preferredScreenshotEditor`). Image checks
  require Glimpse, its system gallery overlay, its libraries' notices and its
  exact permissions, without INTERNET (`gallery`, `gallery-role`,
  `gallery-permissions`), Gallery2 reduced to its crop screen
  (`gallery2-crop-only`), and Canvas with its overlay and no permission
  beyond AndroidX's own (`screenshot-editor`, `screenshot-editor-overlay`,
  `screenshot-editor-permissions`).
- Threat model statuses: items built and phone-checked on 2026-10-06 are marked
  so (eSIM manager, boot control, SHA-256 hashtrees, serials, factory
  Bluetooth address, thermal HAL, Updater build).
- Stop selecting the stock primary audio HAL, PAL, AGM with its HIDL
  service and ALSA plugins, audioadsprpcd and its init file: the device
  builds them from Fairphone's published FP6 sources (DiamaneOS forks
  `hardware_qcom_audio-ar`, `vendor_qcom_opensource_arpal-lx` and
  `vendor_qcom_opensource_agm`, followed in `config/forks.json` and
  `config/repositories.json`; the graph service headers are a pinned source).
  The PAL HIDL service, memory logger, dynamic logging library and the AOSP
  libraries only the stock blobs linked leave too (closed audio files 36 to
  17). The closed libraries the source build links (graph services, tuning
  server, voice UI interface) are rendered under their library name
  (`SOURCE_LINKED_STOCK`) and are roots, as is the deadline manager PAL
  loads. Image checks: `audio-source-dropped`, a build-ID check per
  source-built file (`audio-*-not-stock`), exports (`audio-hal-exports`,
  `audio-pal-exports`, the plugin entry points), links (`audio-pal-links`,
  `audio-agm-links`, `audio-hal-links`), speaker protection on in the
  resource manager configuration (`audio-speaker-protection-on`), CFI in
  every source-built audio binary (`audio-*-cfi`), and
  `audio-declared` now requires that PAL is not declared.

- Kernel hardening, pinned at kernel_qcom-6.1 6fed659 (common kernel
  52f5f55): /proc/cmdline shows parameter names only, keeping module options'
  values; reboots ignore the `edl` and `qcom_dload` reasons and the
  download-mode parameters and files are read-only; the EUD debugger's
  device-tree node is off and the USB controller takes no extcon from it.
  `eud.ko` joins the packaging deny list. New checks: `required_symbols` in the
  packaging recipe (`names_command_line` must be in the System.map), the vendor
  kernel policy requires download mode off by default, image checks
  `download-reboot-refused`, `usb-events-from-ucsi` and
  `usb-events-from-ucsi-volcano` (device-tree checks can now require a
  property's absence). Threat model updated.
- Image checks for the factory Wi-Fi MAC in logs (-180): on user builds netd
  logs warnings and up (`netd-calls-not-logged-user`) and the supplicant info
  and up (`supplicant-debug-not-logged-user`); the WLAN driver logs a peer id
  instead of the MAC when a peer has no receive state
  (`wlan-driver-own-mac-not-logged`). Threat model identifiers row: which logs
  still carry the MAC; the Wi-Fi service no longer logs it.
- Image checks and threat model for the factory Wi-Fi MAC (-180): imeiprovd's
  `--wlan-mac` service and its firmware tree, which init waits for at post-fs
  (`wlan-factory-mac-service`), ueventd's firmware directory
  (`-firmware-dir`), the tree's own label (`-label`), the driver option that
  reads the file (`-driver-config`), and who may read, write and create it
  (`-readers`, `-writers`, `-creators`). `no-wlan-mac-link` now says why
  nothing may shadow the file.
- Threat model: user builds silence the camera HAL's log tag, which logged the
  camera module serials.
- Image check `bluetooth-seccomp-enforced`: the Bluetooth HCI service is built
  in trap mode, so a call outside its seccomp list stops the service. Threat
  model: enforcing filter, factory address in use, ANT/SAR contexts built.
- Stop selecting Qualcomm's GNSS HAL, its IGnss implementation and its six
  location libraries, and the GNSS and health interface libraries, init file
  and VINTF fragment only they used. The device builds them from CodeLinaro
  source (manifest `hardware/qcom/gps`, `vendor/qcom/opensource/location` and
  `vendor/qcom/opensource/qmi-framework` forks, `vendor/qcom/opensource/core-utils`
  pinned); the stock gps.conf, izat.conf (pinned edits) and sap.conf stay.
  `config/forks.json` and `config/repositories.json` list the three forks and
  core-utils. The new `files_not_stock` check type fails when a file still has
  a listed stock SHA-256. Image checks require the source HAL files
  (`gnss-source-hal`, `no-stock-gnss-hal`), CFI and the integer overflow
  sanitizer as in Qualcomm's builds, the library allowlist with dlopen only in
  libgps.utils, the linked-in QMI client, no cloud host names, the service's
  activation, no location socket and the HAL's policy. The threat model
  records the change.
- Image checks and threat model for the factory Bluetooth address: imeiprovd's
  `--bt-address` service (`bluetooth-factory-address-service`), the copy to
  `ro.vendor.bt.boot.macaddr` in the Bluetooth init rc
  (`bluetooth-factory-address-copy`), both properties' own vendor-internal
  types (`bluetooth-factory-address-contexts`), and who may set and read them
  (`bluetooth-factory-address-setter`, `-readers`,
  `bluetooth-hal-address-setter`, `-readers`).
- Image checks and threat model for the device's third hardening batch:
  SHA-256 dm-verity hash trees for every logical partition (the `avb-chain`
  check, `avb.hashtree_algorithm`), the boot control HAL as its own user
  with CAP_SYS_RAWIO only and its nodes (`boot-hal-own-user`,
  `boot-hal-users`, `boot-hal-nodes`), the EUD enable switch's own label
  and writers (`eud-enable-label`, `eud-enable-writers`, `eud-stays-off`;
  the bootconfig check refuses `eud.enable`), no ANT or Bluetooth SAR
  service contexts (`no-bluetooth-value-add-contexts`) and no libubwcp load
  in gralloc (`gralloc-no-libubwcp`). The stock record notes Fairphone's
  published SHA-256 for FP6.QREL.16.111.0, which matches; the capability
  record marks protected VMs unsupported.
- The vendor generator sets `gEnableWoW=2` in the stock QCA6750 driver
  configuration (pinned input and result, `WIFI_CONFIG_REWRITES`): the driver
  no longer wakes the phone on a wake-on-LAN magic packet, which stock left
  on; pattern wake-ups stay. Image checks `wifi-no-magic-packet-wake` and
  `wifi-no-wowlan-triggers`; threat model updated.
- Image checks and threat model for the Bluetooth HCI service's seccomp
  filter: the policy is installed (`bluetooth-seccomp-policy`), the service
  reads it (`bluetooth-service-applies-seccomp`), and its fixed rules hold:
  threads only, Unix sockets only, kill only as SIGKILL, no writable and
  executable mappings, no exec, ptrace or clone3 (`bluetooth-seccomp-rules`).
- Image checks and threat model for DiamaneOS's own eSIM manager: the app
  and its allowlist (`esim-lpa`), its permissions, no network access, the
  package disabled until eSIM support is turned on and no platform signature
  (`esim-lpa-apk`, with the new `manifest_regex` field of `apk` checks), the
  allowlist's two privileged permissions (`esim-lpa-allowlist`) and slot 1 as
  the built-in eUICC (`esim-builtin-slot`). `overlay` checks now read integer
  arrays and several strings on one line. The repository map lists
  DiamaneOS/platform_packages_apps_DiamaneOSEuicc.
- USB-C port control: new check `usb-port-control-data-reenable` (the port
  reports data as force-disabled while a data disable request holds, so a
  re-enable from `svc` or a device owner arrives). Threat model updated.
- USB-C port control: the triggers check now requires the user-build
  controller cut at early-boot (before the charger input) and still refuses
  it on debuggable builds; only the port states and that boot cut may turn
  data off, and only ports_enabled may turn it on. The threat model says
  user builds start every boot with data and charging off.
- USB-C port control: the triggers check also covers the boot cut (user
  builds suspend the charger input once the charger firmware is up and again
  at boot, debuggable builds write 0, nothing at boot cuts the controller or
  lifts the input). The threat model states the boot cut and the two
  differences from Pixels (Type-C and USB PD stay on in Off; the boot cut
  starts once the charger firmware is up).
- Threat model and image checks for USB-C port control Off turning off
  charging: the triggers check now also covers the charger input suspend
  (Off sets it after the data cut; every other state, boot, charger mode and
  shutdown lift it), its own SELinux type and writers
  (`usb-port-control-input-suspend-label`,
  `usb-port-control-input-suspend-writers`), Off applied at boot
  (`usb-port-control-off-at-boot`) and the new Off summary
  (`usb-port-control-off-text`).
- Threat model and image checks for USB-C port control: the framework
  setting (`usb-port-control`), the port-state triggers
  (`usb-port-control-triggers`), the data switch's own SELinux type and its
  writers (`usb-port-control-label`, `usb-port-control-writers`), the
  debuggable-build guards in the framework (`usb-port-control-debug-guards`),
  the FP6 Off summary (`usb-port-control-off-text`) and Android's advanced
  protection USB hook staying off (`usb-data-protection-unset`).
- Stop selecting the stock audio_effects.xml and Qualcomm's four effect
  libraries. The device installs its own effects configuration (AOSP software
  effects, no DSP offload halves) and builds the VoIP pre-processing
  descriptors and the volume listener from CodeLinaro source, which
  `config/forks.json` follows (`qualcomm-audio-effects`); the closed
  offload effect bundle and visualizer leave the image. Image checks require
  their absence (`audio-effects-closed-absent`,
  `audio-effects-closed-unreferenced`), the configuration
  (`audio-effects-config`) and the source-built libraries
  (`audio-effects-source-built`, `audio-voice-processing-exports`,
  `audio-voice-processing-not-stock`, `audio-volume-listener-exports`,
  `audio-volume-listener-source`).
- Image checks for the device's second hardening batch: the UFS serial
  numbers' root-only mode (`ufs-serial-root-only`), the Bluetooth HCI log
  tags (`bluetooth-address-not-logged`), the removed colour service policy
  (`no-display-colour-policy`), no adb over the network
  (`no-adb-over-network`) and the Codec2 service's encoder-only target
  specification (`media-target-variant`, `media-target-variant-readable`).
  Kernel policy v11 also requires PROC_KCORE, KGDB, KEXEC, KEXEC_FILE and
  HIBERNATION off; the published kernel already has them off. The threat
  model records the changes and corrects statements on eSIM, OEM unlocking,
  the 2G controls, the radio log, debugfs and permissive mode.
- Threat model and image checks for the device's privacy hardening: the SoC
  serial number's own SELinux type (`soc-serial-label`), its root-only mode
  (`soc-serial-root-only`) and the thermal HAL running as system
  (`thermal-hal-as-system`, `thermal-hal-trip-nodes`). The new
  `sepolicy_sources` check type expands attributes across the platform,
  mapping and vendor policies and fails when any domain outside a list may
  use a permission on a type; `soc-serial-readers` uses it for the serial.
  The threat model also states why nicmd can read every IPsec state's keys.
- `build all --official` builds official images: the Android build gets
  `DIAMANEOS_OFFICIAL_BUILD=true`, which adds the DiamaneOS Updater fork
  (DiamaneOS/platform_packages_apps_Updater). Other builds run with the
  variable removed. The choice is an Android input, part of the build
  identity, recorded as `official` in `build.json` and remembered by the
  workspace until `--no-official`. Image checks require the Updater, its
  permissions and the DiamaneOS update server in official builds and its
  absence in the others. The OS update endpoint contract names the fork as
  its client.
- Deliver the FP6 firmware with the image set. `build package` copies
  Fairphone's firmware of the vendor files' release (`FP6.QREL.16.111.0`) out
  of the authenticated factory package, every image checked against
  `config/fp6-firmware-inventory.json`, and makes zero images for the modem file
  system. `flash-steps` writes the firmware first, in the stock order and on
  both slots, then empties the modem file system, and only over older
  firmware: the phone's release comes from `--phone-firmware` or `--since`,
  because the FP6 bootloader does not report it; firmware steps need the
  phone's saved bootloader state (`--phone`); a newer release on the phone
  stops the command. `--no-firmware` and `--rewrite-firmware` skip or repeat
  the firmware. `build verify` checks the firmware and refuses an A/B
  partition list that carries only part of it. Not yet built or tested on a
  phone (docs/FIRMWARE.md).
- Threat model and image checks for the camera's performance hints, which
  the device now passes to the power HAL: the camera provider becomes a power
  HAL client. `camera-power-client` checks that it is the only vendor domain
  in hal_power_client, with no server role and no direct rules on the power
  HAL domains; `camera-boosts-config` that the power HAL defines the camera
  boosts and no camera streaming caps. The perf client check expects the power
  HAL lookup and still refuses the perf2 service.
- Build the kernel with integrity lockdown instead of confidentiality
  (kernel_qcom-6.1 2d006c3, common kernel d2d69f8): confidentiality emptied
  tracefs and denied BPF kernel-memory reads, so Android's per-app CPU time
  and lmkd's memory-event listener could not start. Kernel policy v10 requires
  integrity and fails a confidentiality configuration. FP6-KERNEL.md and the
  threat model state what SELinux still allows on tracefs.
- Drop the CodeLinaro power HAL fork (vendor_qcom_opensource_power) from the
  fork list, patch inventories and repository map: the power HAL is
  LineageOS's libperfmgr, and the fork was no longer built.
- Track the Log Viewer fork (DiamaneOS/platform_packages_apps_LogViewer): its
  Report button opens the DiamaneOS issue tracker.
- Drop the 22 retired kernel component forks and the old kernel manifest from
  the repository map: they are deleted, and the kernel builds from
  kernel_qcom-6.1.
- `build sync --resolved-manifest FILE` (also `build all`) syncs the source of
  a recorded image set: every project at the commit its resolved manifest
  names, and the manifest checkout at the commit its `build.json` records
  (`--build-json`, or `--manifest-commit`), which must be in the manifest
  branch's history. The file must match the SHA-256 `build.json` records, and
  that commit's manifest apart from the project commits; afterwards
  `repo manifest -r` must give it byte for byte. The sync and `android` steps
  check the recorded manifest commit instead of the branch head and say so.
  The sync state records the reproduction.
- `build sync --shallow` fetches the largest prebuilt projects itself before
  `repo sync`: one revision each at depth 1, with a low-speed abort and a
  bounded number of attempts (`shallow_prefetch` in `config/fp6-build.json`).
  It also removes project git directories an interrupted sync left without
  data. A broken-off depth-1 fetch of the clang prebuilts had made `repo`
  fetch the project without depth, which did not finish, and those empty
  directories came back with their whole history.
- Build the image tools with a fixed build number and the sources' own date:
  aapt2 carried the build day in its version string, its hash reached the
  vendor inventory, and the same sources gave a different build identity on
  another day. build.json names the factory package by file name instead of
  its workspace path. Found by a second build that otherwise matched bit for
  bit.
- Check that ueventd gives the traceability partition to imeiprovd's group,
  read-only; without it the tool cannot read the IMEIs.
- Check the parallel kernel module streams of the device tree: the
  `modules.load.*` lists in vendor_dlkm must together name every module of
  its `modules.load` once, each in that order, and `modules.load` must match
  the kernel prebuilts. Image checks require the stream lists, one modprobe
  service per list in the vendor_modprobe domain with only CAP_SYS_MODULE,
  init waiting for every stream but touch, and no serial modprobe of the whole
  list.
- Check the device's power stats HAL in the image: the binary, init file
  and IPowerStats V2 VINTF fragment, its own user without other groups or
  capabilities, `/dev/stats` read-only for that user with its own label,
  the HAL's access to it as the only vendor grant, the seven allowed
  qcom_stats ioctls and no write, and the qcom_stats module in the load
  list. The threat model and build notes record the HAL.
- Select the stock Qualcomm Codec2 video service for the phone's SoC variant
  with the hardware encoders only: its codec list loses the decoder section and
  its target specification lists only the five encoders, so decoding stays in
  the software codecs. The stock libcodec2_vndk was built against Android 14,
  where GraphicBuffer is 256 bytes; it is bound to the device's Android 14 sized
  GraphicBuffer (compat/codec2-v34) instead of Android 17's 3376-byte one by
  renaming its libui.so dependency and its six GraphicBuffer symbols (all pinned
  input and output hashes), and the Codec2 HIDL libraries are bound to the
  getHGraphicBufferProducer compat. A new image check disassembles the library
  and confirms the dependency and symbols were renamed and the 256-byte
  allocations bind the 256-byte object (finding -115). The threat model now
  describes the encoder-only service and the GraphicBuffer fix. The
  build-environment pins follow the new selection. The seven Android 14
  Codec2 framework libraries share their names with AOSP vendor-available
  libraries, whose install rules Soong always defines, so they install on
  odm (ODM_LIBRARIES) like libkeymaster_messages; an image check requires
  them there and absent from vendor.
- Rename the stock camera provider's libhardware.so dependency to the
  device's seccomp loader, libcamxjail.so (same length; input and output
  hashes pinned in `NEEDED_REWRITES`, which now records a reason per entry).
  The loader links libhardware and installs the provider's seccomp filter
  before main(). Image checks require the loader, the policy and its fixed
  rules (threads only, Unix and QRTR sockets, no writable and executable
  mappings, no exec, ptrace or clone3), and the renamed dependency.
- Stop selecting the Adreno OpenCL runtime (libOpenCL, libOpenCL_adreno), its
  compiler (libllvm-qcom, libadreno_compiler_cl; about 34 MB together) and the
  software chromatic-aberration library libmmcamera_cac, which links the
  runtime. They were selected only as camera provider runtime dependencies,
  loaded on demand by CamX's GPU and CAC nodes and its OpenCV users.
  An image check requires their absence.
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
