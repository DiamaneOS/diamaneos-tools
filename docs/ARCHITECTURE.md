# Architecture

Which component owns what, which way dependencies run, and the main source decisions behind the
build. For people who change the tools or add a component. Terms are explained in
[TERMS.md](TERMS.md).

## Components

| Component | Inputs | Owns | Boundary |
| --- | --- | --- | --- |
| Tools | Pinned manifests and fixed inputs | Build workspaces and their records | No signing or release credentials |
| Manifest | GrapheneOS release manifest, DiamaneOS forks and pinned upstream projects | The DiamaneOS manifest branch | Verify GrapheneOS's signed tag when taking a release. Every build records its resolved manifest |
| Device and product | Product base and device descriptors | Product configuration and overlays | Device and vendor policy only |
| Vendor files and firmware | Exact stock inputs and the extraction recipe | Generated inputs | Never edit generated content by hand |
| Kernel, modules and device tree | `kernel_common-6.18` with the FP6 commits, Qualcomm's drivers as external modules, Fairphone's device tree | Their integration | Keep verification and upstream grouping |
| Apps | Supported platform APIs | Feature-specific state | No umbrella privileged app |

- Platform code owns credential, permission, update and hardware enforcement. Shared visual
  components only coordinate presentation.
- Dependencies run one way: the tools and the manifest define the source inputs, and the generated
  and device integration feeds the builds.

## Sources

The DiamaneOS manifest is GrapheneOS's manifest at its base release, plus the DiamaneOS forks, the
device projects, the kernel prebuilts and the tools, minus other devices' kernels
([SOURCES.md](SOURCES.md)).

- Builds follow its `android17` branch, check the whole tree and record the resolved manifest.
- The build checks and the overlay check read the device's build environment
  (`config/build-environment-fp6.json` for the FP6).
- The FP6 kernel sources are `kernel_common-6.18` and `kernel_qcom-6.18`, built separately into the
  published prebuilts ([FP6-KERNEL.md](FP6-KERNEL.md)).
- The device tree (`device/fairphone/FP6`) owns the device policy and hardware setup.
- `config/fp6-sources.json` pins the commit of Fairphone's source manifest and describes the families
  of stock files the build selects.

Add repositories only when needed. Remove unused dependencies, and regenerate derived content from
reviewed inputs.

## Generated vendor content

Generated vendor content comes from one exact, hash-pinned factory package, read from the archive and
never from a phone ([FP6-VENDOR.md](FP6-VENDOR.md)). The generator is driven by its recipes and fails
closed:

- Each retained input records its stock build, region, partition, path, expected hash, role and
  consumer.
- It builds a new tree, verifies it completely, writes a provenance report, and only then replaces the
  previous tree.
- Unexpected versions, missing files and files from a Pixel or another stock release are refused.
- Device-unique state never enters it: calibration, provisioning, modem NV and EFS data, IMEI, DRM
  keys, attestation keys, keystore and user data.

**Minimisation** uses an explicit allowlist and proven dependencies, never hand edits. Removing a
component also removes or adapts its init, VINTF, permission, feature, SELinux and client declarations.

Core radio and IMS, camera, GPU, secure world, fingerprint, NFC, Wi-Fi, Bluetooth and signal
processor inputs stay until a maintained alternative passes the same compatibility, security, power
and hardware tests.

An open-source substitute wins only once its licence and those properties are established. A weaker
software fallback for a hardware-backed service is no reduction of the attack surface.

## Kernel and vendor compatibility

- A Pixel patch is not portable just because it shares the Android Common Kernel family. Each change
  kept needs its origin, interface and device tests ([FP6-KERNEL.md](FP6-KERNEL.md)).
- The FP6's stock vendor image declares VINTF target level 8, vendor API level 34 and a 6.1 GKI
  runtime. DiamaneOS runs Linux 6.18 on that vendor image, which keeps level 8.
- Every newer GrapheneOS base must pass `assemble_vintf` and `checkvintf`, boot and the relevant VTS
  interface checks.
- Turning off VINTF enforcement, or a broad shim, is a failed port, not a way out.

## Shared host code

- `process` owns subprocesses: bounded output streams, deadlines and cleanup of the whole process
  group, even after the leader exits.
- Small commands return bounded buffers. Signing commands stream to their own logs and keep a tail for
  the error message.
- A child that starts a new session leaves the process group. These tools are no sandbox for hostile
  programs.
