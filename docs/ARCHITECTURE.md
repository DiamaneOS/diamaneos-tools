# DiamaneOS architecture

Component ownership, allowed dependencies and the main source decisions.

## Ownership and allowed dependencies

| Component | Inputs | Owned state | Authority boundary |
| --- | --- | --- | --- |
| tools | Pinned manifests and immutable inputs | Build workspaces and their bounded records | No signing or release-promotion credentials |
| manifest | GrapheneOS release manifest, DiamaneOS forks and pinned upstream projects | The DiamaneOS manifest branch | Verify GrapheneOS's signed tag when merging a release; every build records its resolved manifest |
| device/product | Product base and device descriptors | Product configuration and overlays | Device/vendor policy only |
| vendor/firmware | Exact stock inputs and extraction recipe | Generated inputs | Never hand-edit generated content |
| kernel/modules/dt | Qualcomm CodeLinaro release with GrapheneOS `kernel_common-6.1` merged in; FP6 device trees from Fairphone | Kernel/module/devicetree integration | Preserve verification and upstream grouping |
| apps/build | Supported platform APIs | Feature-specific state | No umbrella privileged application |

- Platform code owns credential, permission, update and hardware enforcement; shared visual
  components only coordinate presentation.
- Dependencies run one way: tools and manifests define source inputs, and generated and device
  integration feeds builds.

## Sources and generated vendor content

The DiamaneOS manifest is GrapheneOS's manifest at its base release, plus the DiamaneOS forks, the
Fairphone and CodeLinaro projects, the kernel prebuilts and the tools, minus other devices' kernels.

- FP6 builds follow its `android17` branch, check the whole tree and record the resolved manifest.
- Build preflight and overlay release checks read `config/build-environment-fp6.json`; resolved
  manifests record the concrete source revisions for each build.
- The kernel sources are one repository (`kernel_qcom-6.1`), built separately into the published
  prebuilts.
- Device policy and hardware setup are source-owned by `device/fairphone/FP6`.
- Add repositories only when needed; remove unused dependencies and regenerate derived content
  from reviewed inputs.
- `config/fp6-sources.json` pins the commit of Fairphone's source manifest and describes the
  families of stock files the build selects.

**Stock input.** Generated vendor content comes from an exact, hash-pinned stock input:

- The factory package selected in `config/stock-inputs.json` (`selected_generator_input`), recorded
  there as the EU input, with its firmware hashed per image in
  `config/fp6-firmware-inventory.json` ([FIRMWARE.md](FIRMWARE.md)).
- The US package that file lists as excluded is never an input ([TESTING.md](TESTING.md#regions)).
- The generator reads the factory package only, never a phone: calibration, identity and
  provisioning state must never enter a generated tree.

**Generator.** Manifest-driven and fail-closed:

- Each retained input records source build, region, partition, path, expected hash, component role,
  source/prebuilt class and consumer.
- It builds a new temporary tree, verifies completeness and hashes, emits a provenance and integrity
  report, and only then replaces the previous tree.
- Unexpected versions, missing files and substitutions from a Pixel or another FP6 release are
  rejected.
- Device-unique partitions and credentials (persistent calibration/provisioning, modem NV/EFS, IMEI,
  DRM, attestation, keystore, userdata) are excluded by policy.

- **Minimization** uses an explicit allowlist and demonstrated dependency closure, never hand edits.
- Removing a component also removes or adapts its init, VINTF, permission, feature, SELinux and
  client declarations.
- Core radio/IMS, camera, GPU, secure-world, fingerprint, NFC, Wi-Fi/Bluetooth and DSP inputs stay
  until a maintained alternative passes the same compatibility, security, power and hardware tests.
- An open-source substitute wins only once its exact licence and those properties are established; a
  weaker software fallback for a hardware-backed service is no attack-surface reduction.

## Kernel and vendor compatibility

- Sharing an ACK (Android Common Kernel) family never makes a Pixel patch portable; each retained
  change needs its ancestry, KMI/UAPI and device tests bound ([FP6-KERNEL.md](FP6-KERNEL.md)).
- The selected stock image declares device VINTF target level 8, vendor API/VNDK 34 and a 6.1
  Android GKI runtime: the real vendor-side boundary, not a reason to pin the framework forever.
- Every newer GrapheneOS assembly must pass `assemble_vintf`/`checkvintf`, boot and the applicable
  VTS interface checks.
- Disabling VINTF enforcement or a broad shim is a port failure, not a recovery path.

## Shared host mechanisms

- `process` owns bounded subprocess streams, deadlines and cleanup of same-group descendants, even
  after the leader exits.
- Small commands return bounded buffers, signing commands stream to exclusive logs with a diagnostic
  tail, and limits are explicit at the call site.
- A child starting a new session escapes the process group: these tools are no sandbox for hostile
  executables.
