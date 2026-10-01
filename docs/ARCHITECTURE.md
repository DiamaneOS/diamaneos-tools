# DiamaneOS architecture

Which component owns what, which dependencies are allowed, and the main design
decisions for sources and generated vendor content. For contributors deciding
where a change belongs. The proposed GrapheneOS 17 base (the hardened Android
DiamaneOS builds on) is not yet proven compatible with the Fairphone 6 (FP6)
vendor input; repository layout and source selection stay subject to actual
integration evidence.

## Ownership and allowed dependencies

| Component (repository ID) | Responsible role | Inputs | Owned state | Authority boundary |
| --- | --- | --- | --- | --- |
| tools | Host-tooling maintainer | Pinned manifests, reviewed suites and immutable inputs | Private target maps, per-target locks and bounded run evidence | No signing or release-promotion credentials; destructive recipes remain in the installer/runbook |
| manifest | Source-integration maintainer | Reviewed upstream manifest and fork pins | Checkout identity at sync | Authenticate upstream; bind downstream composition when present |
| device/product | Device-integration maintainer | Product base and device descriptors | Product configuration and overlays | Device/vendor policy only |
| vendor/firmware | Reproducible input generator | Exact stock inputs and extraction recipe | Generated inputs | Never hand-edit generated content |
| kernel/modules/dt | Kernel maintainer | ACK branch and Fairphone sources | Kernel/module/devicetree integration | Preserve verification and upstream grouping |
| apps/build | Owning feature maintainer | Supported platform APIs | Feature-specific state | No umbrella privileged application |
| infrastructure | Service maintainer | Endpoint contracts | Bounded serving, staging and monitoring | Privately injected credentials; no release signing keys |
| site/installer | Documentation and installer maintainers | Verified release metadata and recovery requirements | Public guidance and installer flow | Independent verification before destructive operations |
| app-repository/Apps | App-delivery maintainer | Signed catalog and package contracts | Catalog, acquisition and delivery | Preserve package identity and signer validation |
| Auditor/attestation | Attestation maintainer | Reviewed upstream client/protocol/server | Verification policy and authenticated reports | No invented hardware guarantees |

ACK is the Android Common Kernel. Platform code owns credential, permission,
update and hardware enforcement; shared visual components coordinate
presentation without duplicating those authorities. Keep dependencies
directed: tools and manifests define source inputs, generated/device
integration feeds builds, and release evidence derives from the resulting
candidate.

## Hardware-runner boundary

[TESTING.md](TESTING.md#staged-device-runner) explains how to run the runner.

- The host-tooling maintainer owns the suite parser, adapter allowlist and run
  schema. The private deployment owns exact device-role mappings and raw
  output; public reports use non-identifying roles. Suite configuration cannot
  introduce an arbitrary command, select an unmapped target or grant
  destructive authority.
- Each physical role has one lock owner at a time. The run directory owns
  checkpoint state from creation to atomic finalization. An interruption keeps
  completed cases and produces an explicit rerun set instead of resuming an
  unverified command. A retry verifies the suite/candidate identity, installed
  build identity and referenced raw-file hashes before running any retry case.
- Inspect, smoke and security stages currently use a narrow read-only ADB
  adapter. Destructive cases cross a distinct installer/runbook boundary: an
  explicit flag and a disposable role are necessary but not sufficient, and
  this runner does not implement flashing.
- Unit fixtures prove parsing and failure paths; only an explicitly labelled
  real-device run proves a physical result.
- The current executable source binding is the official ADB command-line
  interface over the accepted USB path. Fastboot and UI-automation adapters are
  not implemented; a later suite that genuinely needs them must first define
  their reviewed target, timeout, cleanup and evidence contracts.

## Release path (planned)

A pinned manifest and endpoint contract feed a reproducible build. The build
produces unsigned target-files (the build output that signing transforms), the
isolated release signer creates full and incremental OTAs (update packages),
and an independent final-content comparison checks the result. The device
verifies them through the existing trusted update pipeline. See
[SIGNING.md](SIGNING.md).

## Source layout

- The accepted build authenticates the upstream release and resolved project
  map directly. The local-manifest repository now pins the initial FP6 device
  and shared product projects; environment v4 stays upstream-only. A consuming
  environment must bind the overlay commit, digest and composed map. Build
  preflight supports explicitly declared additive composition while keeping the
  upstream signature and source-layout checks.
- Add repositories or services only when actual integration needs them. Remove
  unused reference dependencies with a recorded rationale; regenerate derived
  content from its reviewed inputs.
- The FP6 port consumes two separate upstream trees: QSSI (Qualcomm's common
  system side) and the Fairphone target tree for device, kernel, module and
  vendor-side integration. `config/fp6-sources.json` is the
  source/prebuilt/partition map; `config/fp6-capabilities.json` records what may
  be inherited, must be adapted, is unsupported or is still unverified. They
  deliberately do not flatten the published module repositories into one
  invented source project. The later kernel checkout may keep those upstream
  boundaries while exposing one reproducible build entry point.

## Generated vendor content

**Source.** Generated vendor content is derived from an exact, hash-pinned stock
input. For the EU port the primary extraction source is the verified
`FP6.QREL.16.111.0` factory package (selected on 2026-09-30;
`FP6.QREL.16.100.0` before that). A phone running that build verifies runtime
declarations and may supplement ordinary mounted files, but is not the sole
extraction source. The firmware images of the selected package are hashed per
image in `config/fp6-firmware-inventory.json` (see [FIRMWARE.md](FIRMWARE.md)).
A stock production ADB session cannot provide every partition, and a device
also holds calibration, identity and provisioning state that must never enter
a generated vendor tree.

**Generator.** The generator is manifest-driven and fails closed.

- Each retained input records its source build, region, partition, path,
  expected hash, component role, source/prebuilt classification and consuming
  module or service.
- It writes a new temporary tree, verifies completeness and hashes, emits a
  provenance and integrity report, and only then replaces the previous valid
  generated tree.
- It rejects unexpected versions, missing files and substitutions from a Pixel
  or a different FP6 release.
- Device-unique partitions and credentials are excluded by policy: persistent
  calibration/provisioning, modem NV/EFS (the modem's own non-volatile
  storage), IMEI, DRM, attestation, keystore and userdata material.

**Minimization** uses an explicit allowlist and demonstrated dependency closure,
never hand-editing of generated output. Removing an active component also
removes or adapts its init, VINTF, permissions, feature, SELinux and client
declarations. Core radio/IMS, camera, GPU, secure-world, fingerprint, NFC,
Wi-Fi/Bluetooth and DSP inputs stay until a maintained alternative passes the
same compatibility, security, power and hardware tests. An open-source
substitute is preferred only when its exact licence and those properties are
established. Replacing a hardware-backed service with a weaker software
fallback does not count as attack-surface reduction.

**Regions.** The intended architecture is one product when the evidence
permits it. `FP6.QREL.16.111.0` is the EU baseline (Fairphone released it as
one build for all regions); `FP6.QREL.16.104.0` is a US comparison and
validation input, not an EU restore input. The rules for common files,
regional deltas, the US test phone and the `UNVERIFIED` US status are in
[regional FP6 qualification](TESTING.md#regional-fp6-qualification).

## Kernel and vendor compatibility

- Sharing an ACK family never shows that a Pixel patch is portable. The kernel
  owner must bind ancestry, KMI/UAPI (kernel module and user-space interfaces)
  and device tests for each retained change. See [FP6-KERNEL.md](FP6-KERNEL.md).
- The selected stock image declares device VINTF (vendor interface manifest)
  target level 8, vendor API/VNDK 34 and a 6.1 Android GKI runtime. This is the
  actual vendor-side compatibility boundary, not a reason to pin the framework
  indefinitely. Every newer GrapheneOS assembly must pass
  `assemble_vintf`/`checkvintf`, boot and the applicable VTS interface checks.
  Disabling VINTF enforcement or adding a broad shim is a port failure, not a
  recovery path.

## Shared host mechanisms

| Module | Owns |
| --- | --- |
| `process` | Bounded subprocess streams, deadlines and cleanup of same-group descendants, even after the leader has exited |
| `evidence` | Bounded JSON input, atomic report writes and referenced-file hashes |
| `device` | ADB enumeration and identity capture |
| `rig` | Physical-role locks and persistent leases |

- Small commands return bounded buffers; signing commands stream to exclusive
  logs with a kept diagnostic tail. Limits are explicit at the call site.
- A child that deliberately creates a new session escapes a POSIX process
  group; deployed services also own a systemd cgroup. These tools are not a
  sandbox for hostile host executables.
- Domain workflows keep their own state and acceptance rules and do not call
  another workflow's private IO/device helpers.
- A start guard can create an inhibitor under its existing lock before
  release.
- Source sync, generic build, signing discovery and dummy qualification share
  `$WORK_ROOT/.workspace.lock` for the whole operation, including evidence
  finalization. A competing operation fails immediately. This lock is
  independent of physical-device locks. Do not delete a lock file to clear a
  busy operation.
- Compatibility packages have separate generated results/logs; see
  [suite packages](COMPATIBILITY.md#suite-packages) for how the rest of the
  package must match the approved ZIP before execution.
