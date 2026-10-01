# DiamaneOS architecture

Component ownership, allowed dependencies and the main source decisions. The
GrapheneOS 17 base is not yet proven compatible with the Fairphone 6 (FP6)
vendor input; layout and source selection follow integration evidence.

## Ownership and allowed dependencies

| Component (repository ID) | Responsible role | Inputs | Owned state | Authority boundary |
| --- | --- | --- | --- | --- |
| tools | Host-tooling maintainer | Pinned manifests, reviewed suites and immutable inputs | Private target maps, per-target locks and bounded run evidence | No signing or release-promotion credentials; destructive recipes remain in the installer/runbook |
| manifest | Source-integration maintainer | Reviewed upstream manifest and fork pins | Checkout identity at sync | Authenticate upstream; bind downstream composition when present |
| device/product | Device-integration maintainer | Product base and device descriptors | Product configuration and overlays | Device/vendor policy only |
| vendor/firmware | Reproducible input generator | Exact stock inputs and extraction recipe | Generated inputs | Never hand-edit generated content |
| kernel/modules/dt | Kernel maintainer | Qualcomm CodeLinaro release with GrapheneOS `kernel_common-6.1` merged in; FP6 device trees from Fairphone | Kernel/module/devicetree integration | Preserve verification and upstream grouping |
| apps/build | Owning feature maintainer | Supported platform APIs | Feature-specific state | No umbrella privileged application |
| infrastructure | Service maintainer | Endpoint contracts | Bounded serving, staging and monitoring | Privately injected credentials; no release signing keys |
| site/installer | Documentation and installer maintainers | Verified release metadata and recovery requirements | Public guidance and installer flow | Independent verification before destructive operations |
| app-repository/Apps | App-delivery maintainer | Signed catalog and package contracts | Catalog, acquisition and delivery | Preserve package identity and signer validation |
| Auditor/attestation | Attestation maintainer | Reviewed upstream client/protocol/server | Verification policy and authenticated reports | No invented hardware guarantees |

Platform code owns credential, permission, update and hardware enforcement;
shared visual components only coordinate presentation. Dependencies run one
way: tools and manifests define source inputs, generated/device integration
feeds builds, release evidence derives from the candidate.

## Hardware-runner boundary

The host-tooling maintainer owns the suite parser, adapter allowlist and run
schema; the private deployment owns device-role mappings and raw output; public
reports use non-identifying roles. Suites cannot add arbitrary commands, select
unmapped targets or grant destructive authority. Each physical role has one
lock owner; the run directory owns checkpoint state until atomic finalization,
so interruption yields an explicit rerun set, never a resumed unverified
command, and retries first verify suite/candidate identity, installed build and
raw-file hashes. Inspect, smoke and security stages use a narrow read-only
adapter over the official ADB command-line interface on the accepted USB path.
Destructive cases cross the installer/runbook boundary (an explicit flag and
disposable role are necessary, not sufficient; the runner never flashes).
Fastboot and UI-automation adapters need reviewed target, timeout, cleanup and
evidence contracts before a suite may use them. Only an explicitly labelled
real-device run proves a physical result; unit fixtures prove parsing and
failure paths. Usage: [TESTING.md](TESTING.md#staged-device-runner).

## Sources and generated vendor content

The accepted build authenticates the upstream release and resolved project map
directly. The local-manifest repository pins the initial FP6 device and shared
product projects; environment v4 stays upstream-only, and a consuming
environment must bind the overlay commit, digest and composed map. Preflight
supports explicitly declared additive composition while keeping upstream
signature and source-layout checks. Add repositories or services only when
needed; remove unused reference dependencies with a recorded rationale and
regenerate derived content from reviewed inputs.

The FP6 port uses QSSI (Qualcomm's common system side) and the Fairphone target
tree (device, kernel, modules, vendor side). `config/fp6-sources.json` maps
sources, prebuilts and partitions; `config/fp6-capabilities.json` records what
may be inherited, must be adapted, is unsupported or unverified. Published
module repositories are not flattened into one invented project.

Generated vendor content comes from an exact, hash-pinned stock input: for the
EU port, the verified `FP6.QREL.16.111.0` factory package (selected 2026-09-30;
`FP6.QREL.16.100.0` before), its firmware hashed per image in
`config/fp6-firmware-inventory.json` ([FIRMWARE.md](FIRMWARE.md)). A phone on
that build verifies runtime declarations and may add ordinary mounted files but
is never the sole source: production ADB cannot read every partition, and
calibration, identity and provisioning state must never enter a generated tree.

The generator is manifest-driven and fails closed. Each retained input records
source build, region, partition, path, expected hash, component role,
source/prebuilt class and consumer. It builds a new temporary tree, verifies
completeness and hashes, emits a provenance and integrity report, and only
then replaces the previous tree; unexpected versions, missing files and
substitutions from a Pixel or another FP6 release are rejected. Device-unique
partitions and credentials (persistent calibration/provisioning, modem NV/EFS,
IMEI, DRM, attestation, keystore, userdata) are excluded by policy.

Minimization uses an explicit allowlist and demonstrated dependency closure,
never hand edits, and removing a component also removes or adapts its init,
VINTF, permission, feature, SELinux and client declarations. Core radio/IMS,
camera, GPU, secure-world, fingerprint, NFC, Wi-Fi/Bluetooth and DSP inputs stay
until a maintained alternative passes the same compatibility, security, power
and hardware tests; an open-source substitute wins only once its exact licence
and those properties are established, and a weaker software fallback for a
hardware-backed service is no attack-surface reduction.

`FP6.QREL.16.111.0` is the EU baseline (Fairphone released it as one build for
all regions) and `FP6.QREL.16.104.0` a US comparison input, not an EU restore
input. Regional rules and the `UNVERIFIED` US status are in
[TESTING.md](TESTING.md#regional-fp6-qualification).

## Kernel and vendor compatibility

Sharing an ACK (Android Common Kernel) family never makes a Pixel patch
portable; the kernel owner binds ancestry, KMI/UAPI and device tests for each
retained change ([FP6-KERNEL.md](FP6-KERNEL.md)). The selected stock image
declares device VINTF target level 8, vendor API/VNDK 34 and a 6.1 Android GKI
runtime: the real vendor-side boundary, not a reason to pin the framework
forever. Every newer GrapheneOS assembly must pass
`assemble_vintf`/`checkvintf`, boot and the applicable VTS interface checks;
disabling VINTF enforcement or a broad shim is a port failure, not a recovery
path.

## Shared host mechanisms

- `process` owns bounded subprocess streams, deadlines and cleanup of
  same-group descendants, even after the leader exits; small commands return
  bounded buffers, signing commands stream to exclusive logs with a diagnostic
  tail, and limits are explicit at the call site. A child starting a new
  session escapes the process group, so deployed services also own a systemd
  cgroup; these tools are no sandbox for hostile executables.
- `evidence` owns bounded JSON input, atomic report writes and referenced-file
  hashes; `device` owns ADB enumeration and identity capture; `rig` owns
  physical-role locks and persistent leases, and a start guard can create an
  inhibitor under its existing lock before release. Domain workflows keep their
  own state and acceptance rules and do not call another workflow's private
  IO/device helpers.
- Source sync and the generic build share `$WORK_ROOT/.workspace.lock` for the
  whole operation, including evidence finalization; a competing operation fails
  immediately. It is independent of physical-device locks. Never delete a lock
  file to clear a busy operation.
- Compatibility packages have separate generated results/logs; the rest must
  match the approved ZIP ([suite packages](COMPATIBILITY.md#suite-packages)).
