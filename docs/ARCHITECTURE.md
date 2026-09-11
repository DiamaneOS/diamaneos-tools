# DiamaneOS Architecture

This map describes component responsibilities and permitted dependencies. The proposed GrapheneOS 17 base is not yet proven compatible with the Fairphone 6 vendor input. Repository layout and source selection remain subject to actual integration evidence.

## Ownership and allowed dependencies

| Component (repository ID) | Responsible role | Inputs | Owned state | Authority boundary |
| --- | --- | --- | --- | --- |
| tools | Host-tooling maintainer | Pinned manifests and immutable inputs | Host maps and read-only collection | No signing or release-promotion credentials |
| manifest | Source-integration maintainer | Reviewed upstream manifest and fork pins | Checkout identity at sync | Preserve imported project definitions |
| device/product | Device-integration maintainer | Product base and device descriptors | Product configuration and overlays | Device/vendor policy only |
| vendor/firmware | Reproducible input generator | Exact stock inputs and extraction recipe | Generated inputs | Never hand-edit generated content |
| kernel/modules/dt | Kernel maintainer | ACK branch and Fairphone sources | Kernel/module/devicetree integration | Preserve verification and upstream grouping |
| apps/build | Owning feature maintainer | Supported platform APIs | Feature-specific state | No umbrella privileged application |
| infrastructure | Service maintainer | Endpoint contracts | Bounded serving, staging and monitoring | Privately injected credentials; no release signing keys |
| site/installer | Documentation and installer maintainers | Verified release metadata and recovery requirements | Public guidance and installer flow | Independent verification before destructive operations |
| app-repository/Apps | App-delivery maintainer | Signed catalog and package contracts | Catalog, acquisition and delivery | Preserve package identity and signer validation |
| Auditor/attestation | Attestation maintainer | Reviewed upstream client/protocol/server | Verification policy and authenticated reports | No invented hardware guarantees |

Platform code owns credential, permission, update and hardware enforcement. Shared visual components coordinate presentation without duplicating those authorities. Keep dependencies directed: tools and manifests define source inputs; generated/device integration feeds builds; release evidence derives from the resulting candidate.

## Release path (planned)

A pinned manifest and endpoint contract feed a reproducible build. The build produces unsigned target-files, the isolated release signer creates full/incremental OTAs, and independent final-content comparison checks the result. The device verifies through the existing trusted update pipeline.

## Source-layout decisions

The manifest delta uses generated revision-only overrides for the selected fork commits and preserves imported project definitions. Add repositories or services only when actual integration needs them. Remove unused reference dependencies with a recorded rationale; regenerate derived content from its reviewed inputs.
