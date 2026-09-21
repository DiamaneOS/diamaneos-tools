# DiamaneOS Architecture

This map describes component responsibilities and permitted dependencies. The proposed GrapheneOS 17 base is not yet proven compatible with the Fairphone 6 vendor input. Repository layout and source selection remain subject to actual integration evidence.

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

Platform code owns credential, permission, update and hardware enforcement. Shared visual components coordinate presentation without duplicating those authorities. Keep dependencies directed: tools and manifests define source inputs; generated/device integration feeds builds; release evidence derives from the resulting candidate.

## Hardware-runner boundary

The host-tooling maintainer owns the suite parser, adapter allowlist and run
schema. The private deployment owns exact device-role mappings and raw output;
public reports use non-identifying roles. Suite configuration cannot introduce
an arbitrary command, select an unmapped target or grant destructive authority.

Each physical role has one lock owner at a time. The run directory owns
checkpoint state from creation through atomic finalization; interruption keeps
completed cases and produces an explicit rerun set rather than resuming an
unverified command. Retry verifies the suite/candidate identity, installed
build identity and referenced raw-file hashes before executing any retry case.

Inspect, smoke and security stages currently use a narrow read-only ADB
adapter. Destructive cases cross a distinct installer/runbook boundary: an
explicit flag and disposable role are necessary but not sufficient, and this
runner does not implement flashing. Unit fixtures prove parsing and failure
paths; only an explicitly labelled real-device run proves a physical result.

The current executable source binding is the official ADB command-line
interface over the accepted USB path. Fastboot and UI-automation adapters are
not implemented; they require their own reviewed target, timeout, cleanup and
evidence contracts when a later suite genuinely needs them.

## Release path (planned)

A pinned manifest and endpoint contract feed a reproducible build. The build produces unsigned target-files, the isolated release signer creates full/incremental OTAs, and independent final-content comparison checks the result. The device verifies through the existing trusted update pipeline.

## Source-layout decisions

The accepted build authenticates the upstream release and resolved project map directly. The local-manifest repository now pins the initial FP6 device and shared product projects; environment v4 remains upstream-only. A consuming environment must bind the overlay commit, digest and composed map. Build preflight supports explicitly declared additive composition while preserving upstream signature and source-layout checks. Add repositories or services only when actual integration needs them. Remove unused reference dependencies with a recorded rationale; regenerate derived content from its reviewed inputs.

The FP6 port consumes two separate upstream trees: QSSI for the common system
side and the Fairphone target tree for device, kernel, module and vendor-side
integration. `config/fp6-sources.json` is the source/prebuilt/partition map;
`config/fp6-capabilities.json` records what may be inherited, must be adapted,
is unsupported or is still unverified. They deliberately do not flatten the
published module repositories into one invented source project. The later
kernel checkout may preserve those upstream boundaries while exposing one
reproducible build entry point.

Generated vendor content is derived from an exact, hash-pinned stock input.
For the EU port, the primary extraction source is the verified
`FP6.QREL.16.100.0` factory package; the phone running that build verifies
runtime declarations and may supplement ordinary mounted files, but is not the
sole extraction source. A stock production ADB session cannot provide every
partition, and a device also contains calibration, identity and provisioning
state that must never enter a generated vendor tree.

The generator is manifest-driven and fail-closed. Each retained input records
its source build, region, partition, path, expected hash, component role,
source/prebuilt classification and consuming module or service. It writes a
new temporary tree, verifies completeness and hashes, emits a provenance and
integrity report, and only then replaces the prior valid generated tree.
It rejects unexpected versions, missing files and substitutions from a Pixel
or different FP6 release. Device-unique partitions and credentials, including
persistent calibration/provisioning, modem NV/EFS, IMEI, DRM, attestation,
keystore and userdata material, are excluded by policy.

Minimization is performed against an explicit allowlist and demonstrated
dependency closure, not by hand-editing generated output. Removing an active
component also removes or adapts its init, VINTF, permissions, feature,
SELinux and client declarations. Core radio/IMS, camera, GPU, secure-world,
fingerprint, NFC, Wi-Fi/Bluetooth and DSP inputs remain until a maintained
alternative passes the same compatibility, security, power and hardware
tests. An open-source substitute is preferred only when its exact licence and
those properties are established; replacing a hardware-backed service with a
weaker software fallback is not accepted as attack-surface reduction.

The intended regional architecture is one product when the evidence permits
it. `FP6.QREL.16.100.0` is the EU baseline; `FP6.QREL.16.104.0` is a US
comparison/validation input, not an EU restore input. Only byte-identical files
may enter a common generated set without further adaptation. Any real regional
delta must be isolated and selected using an observed trustworthy hardware or
boot SKU property, never locale or mutable location. Boot-critical differences
require separately bound variants. A US-region FP6 operated by the second
maintainer is the required US device-validation path; its availability and
state have not yet been evidenced. Until its stock comparison and candidate
tests pass, the US target is unverified.

Sharing an ACK family never establishes that a Pixel patch is portable; the
kernel owner must bind ancestry, KMI/UAPI and device tests for each retained
change.

The selected stock image declares device VINTF target level 8, vendor
API/VNDK 34 and a 6.1 Android GKI runtime. This is the actual vendor-side
compatibility boundary, not a reason to pin the framework indefinitely. Every
newer GrapheneOS assembly must pass `assemble_vintf`/`checkvintf`, boot and the
applicable VTS interface checks. Disabling VINTF enforcement or adding a broad
shim is a port failure, not a recovery path.

## Shared host mechanisms

`process` owns bounded subprocess streams, deadlines and same-group descendant
cleanup, including when the leader has exited. Small commands return bounded
buffers; signing commands stream to exclusive logs with a retained diagnostic
tail. Limits are explicit at the call site. A child that deliberately creates a
new session escapes a POSIX process group; deployed services additionally own
a systemd cgroup. These tools are not a sandbox for hostile host executables.

`evidence` owns bounded JSON input, atomic report writes and referenced-file
hashes. `device` owns ADB enumeration and identity capture. Domain workflows
keep their own state and acceptance rules; they do not call another workflow's
private IO/device helpers. `rig` owns physical-role locks and persistent leases.
A start guard can create an inhibitor under its existing lock before release.

Source sync, generic build, signing discovery and dummy qualification share
`$WORK_ROOT/.workspace.lock` for the entire operation, including evidence
finalization. A competing operation fails immediately. This is independent of
physical-device locks. Do not delete a lock file to clear a busy operation.
Compatibility packages have separate generated results/logs; every other file
and executable bit must match the approved ZIP before execution.
