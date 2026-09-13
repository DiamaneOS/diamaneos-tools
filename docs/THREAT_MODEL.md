# DiamaneOS Threat Model and Product Boundaries — Initial

Status: initial threat model for an OS under development. Protections below are
requirements, not validated properties of a DiamaneOS build.
No hardware claim below is demonstrated protection until its validation work passes.

> Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project.

## Current product scope

DiamaneOS targets daily use by privacy- and security-focused users. This threat
model covers the current GrapheneOS-based Fairphone 6 port (one device, one
variant), which is under development. Its requirements include a locked
bootloader on a custom AVB root and monthly releases. The launch scope below must pass
qualification before public release. No root, no microG, no signature spoofing, no Magisk
accommodation, no unlocked-bootloader daily use, no bundled cloud account.
A clear, expressive UI with measured maintenance; accessibility and localization are
acceptance criteria, not polish. No global security score, no stock-parity
promise, no superiority claim without dated like-for-like evidence.

GrapheneOS-derived describes source lineage; it does not establish equivalent
security to GrapheneOS on its supported devices.

PIN-optional is not in v1. Passphrase-first is mandatory (passphrase onboarding / credential-policy enforcement).
A future PIN-optional-with-warning change needs an explicit scope decision
and renewed compatibility review; it is recorded only as a revisit condition.

## v1 scope (standalone summary)

Public launch waits for all of these on the final candidate plus the release
gates (cadence, beta, qualification): DE/EU carrier configs; eSIM LPA;
LineageOS-parity camera; passphrase-first setup and all-user enforcement;
install-time privacy presets; on-device security status; battery suite
(ceiling, health, cycle/export); encrypted microSD; DNS filtering; privacy
dashboard; per-app routing/filtering; panic-to-BFU reboot; tracker alerts;
cellular alerts-or-honest-unsupported; scheduled reboot; offline SD OTA;
hardware diagnostics; share-time metadata stripping; UnifiedPush
recommendation; WebUSB + CLI installers. Post-release ideas and conditional
kernel/repair/AML research are explicitly not v1.

## Threat rows

Columns: asset | attacker capability | entry point | intended mitigation |
remaining limit | validation work | evidence state.

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| User data in transit | Network observer / active proxy | Wi-Fi, carrier data, DNS, captive portals | No telemetry/GMS; EU-primary endpoints with documented upstreams; user-visible resolver + standard-server alternative | Small-user-base endpoint fingerprinting; non-EU upstreams disclosed per endpoint | endpoint contract design / time and connectivity integration / assisted-GNSS integration / provisioning integration | Assumption |
| App data from remote exploit | Remote attacker via network/media/parser | Carrier/Wi-Fi data, DNS, media, browser/WebView | Inherited sandbox, Network/Sensors permissions, Storage/Contact Scopes, hardened_malloc, exec spawning; mirrored Vanadium via own infra | Parser/network bugs remain; FP6 has no MTE to contain memory errors | encryption and hardening validation / debug exposure auditing / browser APK delivery | Assumption |
| Proximity access | Nearby attacker with radio/physical proximity | Bluetooth, NFC, USB modes, charging ports | Inherited sandbox + permission prompts; USB software-only control; Bluetooth pairing/audio reviewed on harness; NFC/sensor handling per bring-up matrix | No hardware USB-data disable; radio bugs remain without MTE; malicious accessory risk stays | display and connectivity bring-up / camera and peripheral bring-up / debug exposure auditing | Assumption |
| App overreach | Malicious or over-permissioned app | Permissions, background listeners, IPC/URI grants | Per-app Network/Sensors/Scopes, install presets (Untrusted/Standard/Trusted), per-app routing/filtering, privacy dashboard with CE-only bounded history | Preset Trusted means user-chosen policy, never audited-safe; attribution limits proven in DNS architecture | app privacy presets / DNS architecture / DNS filtering / per-app network controls / privacy dashboard | Assumption |
| Persistence after compromise | Attacker with write to partitions | Boot chain, OTA, recovery | Verified boot locked on custom AVB root, Flags 0, rollback protection, signed full/incremental OTA via same trusted pipeline | Yellow boot (never green); downgrade-brick risk; Fairphone unlock service dependency | unlock and stock restoration testing / custom-key relock validation / OTA installation testing / interrupted-update recovery testing | Assumption |
| Powered-off (BFU) data | Opportunistic physical access, powered off | Flash readout | FBE + mandatory 6–8-word CSPRNG passphrase (owner + every independent user/profile), no weak-credential path | TEE Gatekeeper backoff only, no SE/Weaver; brute-force resistance rests on passphrase strength, not hardware | encryption and hardening validation / passphrase onboarding / credential-policy enforcement | Assumption |
| Powered-on/locked (AFU) data | Physical access to AFU device, forensics | USB, lockscreen, sensors, EDL | Auto-reboot/inactivity reboot, USB software control, panic-to-BFU reboot, duress logical reset; EDL `adb reboot edl`/sys.powerctl refused | No hardware USB disable; EDL reachable via keys/test points with signed programmer; duress is flash key-destruction + reset, not SE erase; no forensic-proof claim | debug exposure auditing / credential-policy enforcement / panic reboot / scheduled reboot | Assumption |
| Secondary-profile data | Other user/profile on same device | User switch, stopped/running profiles, unified vs separate challenge | Same credential policy on every independent challenge via real service boundary; CE/DE separation; session-end is not deletion | Unified-challenge profiles follow platform semantics, no invented independent key; stopped session still data-bearing until deletion | encryption and hardening validation / credential-policy enforcement  | Assumption |
| Vendor/firmware trust | Firmware or baseband compromise | XBL/TZ/modem/ABL, closed HALs, provisioning | Ship one matching Fairphone image set only, record hashes/support gaps, narrow HAL manifest, no permissive domains; RKP/Widevine via bounded EU relay | Firmware unpatchable by project; lags ASB by months; stock exposes TEE KeyMint/Gatekeeper but no StrongBox or Weaver HAL; L1/L3 remain device-test questions | kernel and module integration / encryption and hardening validation / DRM and attestation validation / provisioning integration | Source and stock-interface inventory complete; candidate behavior unverified |
| Install-time trust | Impersonating site/mirror, malicious installer | Browser download, WebUSB/CLI installer, first AVB enrollment | Out-of-band fingerprint in 3 independent places + bootloader-displayed comparison before lock; `get_unlock_ability=1` gate refuses on 0; verified downloads before flash | First install has no prior key; page-controlled checkbox is not evidence; compromised origin can substitute installer + key | recovery preflight / unlock and stock restoration testing / CLI installer integration / WebUSB installer integration / independent verification guidance | Assumption |
| Everyday-use traps | User error, confusing warning, inaccessible flow | Setup, permissions, updates, backup/restore, recovery | Plain outcomes, progressive disclosure, scoped grants, explicit destructive confirmations; first-boot assistive path before setup needs it; critical wording gets fluent review or disclosed source-language fallback | Bootloader/firmware screens may stay inaccessible; English fallback alone is not usability proof | interface design / localization and accessibility / passphrase onboarding / accessible journey validation | Assumption |

## FP6 source and firmware boundary

Fairphone publishes the Android 16 FP6 kernel, required external-module and
device-tree repositories, including the WLAN and audio families. That source
availability does not prove GrapheneOS/Pixel patches apply, that modules meet
the selected KMI/UAPI, or that the resulting binaries reproduce stock. The
camera, radio/IMS, secure-world, sensor, DRM and substantial graphics/media
runtime still depends on proprietary userspace or firmware.

The selected and installed EU stock input is `FP6.QREL.16.100.0`. The public
Android 16 binary packages observed during inventory were `FP6.QREL.16.61.0`.
They are not interchangeable inputs. Product assembly must fail closed until
one complete source/blob/firmware set is version-aligned. Exact source revisions, interfaces and resolving
experiments are in `config/fp6-sources.json` and
`config/fp6-capabilities.json`.

The selected locked stock build declares VINTF target level 8 with vendor
API/VNDK 34. Its current system/vendor pair boots, but that does not establish
compatibility with the selected newer GrapheneOS framework. Product assembly
must still pass `checkvintf` and applicable VTS checks without weakened
enforcement or broad compatibility shims.

## Explicit hardware gaps and remaining device checks

The stock arm64 CPU feature set does not expose MTE, and its memtag control
properties are absent. Stock also advertises no StrongBox feature, declares
only default KeyMint/RKP instances and has no Weaver HAL. These Pixel-class
hardware features are therefore unsupported for the initial FP6 port; the
candidate must not falsely expose or depend on them. TEE KeyMint/Gatekeeper and
attestation behavior still require candidate testing. Firmware ASB lag
remains; GKI 6.1 is the oldest permitted launch candidate. Custom-key relock,
A/B failure recovery, pKVM and hardware USB data disable remain unverified;
SoC-level radio isolation is the current boundary.

## Fresh-UI boundary

Shared visual tokens/components carry no platform authority. Credential,
update, backup, network, and content-processing authorities stay separate.
Each nontrivial Settings/launcher/SystemUI change needs demonstrated benefit,
owner, measured rebase cost, and regression checks. No shell rewrite presumed.

## Decision record

- Daily-driver scope for privacy/security-focused users, full v1 retained.
- Passphrase-first mandatory now; PIN-optional only via later explicit decision.
- No scores or unsupported parity claims. Keep upstream attribution and a clear
  separate project identity in the relevant overview and distribution pages;
  component documentation follows the contribution guidance.

## Architecture and verification review

- Remote/app path reviewed: network-observer → DNS/captive-portal → EU endpoint
  policy, and malicious-app → permission/background-listener → preset +
  per-app routing/filtering boundary. No new trust boundary added.
- Cross-profile/physical path reviewed: secondary user/profile → user-switch
  challenge → real service-boundary enforcement (encryption and hardening validation / credential-policy enforcement), and AFU/BFU →
  USB/EDL/lockscreen → reboot-to-BFU/duress boundary. Session-end is not
  deletion; unified-challenge limits preserved.
- Verification: these are intended protections. Device-dependent claims remain unverified until their named validation work produces evidence.

## Next validation

Complete the consumed-file open-source licence review, bind the generated
inputs to the selected stock build, verify the full source manifest at builder sync, and run interface/compatibility
qualification. Validate credential enforcement, reported hardware security
levels, locked boot and attestation on the actual candidate before claiming
those protections.
