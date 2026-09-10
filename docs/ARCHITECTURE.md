# DiamaneOS Architecture — Initial Component Map (manifest integration)

Living map. Updated only when actual design changes (manifest integration establishes it).
Upstream proposal: GrapheneOS branch 17 (source research, unproven vs FP6-16 vendor).
No code modules created here; smallest viable structure until device source inventory / FP6 product integration selects sources.

## Ownership and allowed dependencies

| Component (repo id) | Owner | May depend on | Owns state | Privileged boundary | Removal condition |
| --- | --- | --- | --- | --- | --- |
| tools | host-tooling maintainer | pinned manifests, immutable inputs | host maps, no device truth | host-only, no signing/promotion creds | never (project tooling) |
| manifest | source-integration maintainer | GOS branch-17 proposal | input identity at sync | read-only discovery | superseded by pinned release manifest |
| device/product | TBD (FP6 product integration) | GOS product base, descriptor lists | product config, overlays | device/vendor policy only | unused Lineage deps removed with justification |
| vendor/firmware | generator (FP6 product integration) | exact stock inputs + extraction recipe | generated blobs only, never hand-edited | no manual edits | regenerated on approved input refresh |
| kernel/modules/dt | TBD (kernel and module integration) | ACK android14-6.1 + Fairphone sources | keep upstream grouping unless split justified | no weakened verification | prebuilts listed explicitly if irreducible |
| apps/build | only when genuinely needed | supported APIs | one module per real feature | no umbrella privileged app | removed if gap covered by inherited app |
| infra/site/installer/app-repository | TBD (endpoint contract design / public documentation / CLI installer integration / Apps repository integration) | pinned contracts | see owning task | secrets injected privately, never in git | unused services never deployed |
| Apps/Auditor/attestation | TBD (project attestation / Apps repository integration) | upstream layout preserved | client/protocol/server split kept | report transport authenticated | retired when upstream covers need |

No circular dependencies: tools → manifests → generated/device → build; release evidence derives downstream. Platform (GrapheneOS/AOSP) owns credential/permission/update/hardware truth; owned code coordinates or configures, never duplicates authority.

## Example end-to-end path (proposal, not deployed)

Updater OTA: manifest pins GOS Updater + endpoint config → builder produces unsigned target-files → offline signer creates full/incremental OTAs → final-content comparison (signed-content comparison) → device verifies via existing trusted pipeline. No new verifier, no new authority.

## Material choice (manifest integration)

Remotes-only `diamaneos.xml`, local-only manifest repo, no empty repos — keeps rebase surface minimal until source selection. Revisit when device source inventory / FP6 product integration pins projects.
