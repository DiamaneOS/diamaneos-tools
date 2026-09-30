# DiamaneOS Threat Model and Product Boundaries

Revision: 2026-09-30 (rewritten for readability; no change in substance); 2026-09-30 (the stock input moved to FP6.QREL.16.111.0, selected before Fairphone published its checksum; firmware images hashed per image; the firmware update path designed in FIRMWARE.md; the step 7 forks: Clock, Calculator, recovery titles, the Files archive code and the keyboard privacy fork, with their step 8 security review; -140 to -150; Home's live tallies); 2026-09-29 (Recents' Stop in Launcher3 of step 4b, the Settings homepage of step 6a, Home and All apps of step 4a, Settings search of step 6c and the switches and rows of step 6b, and the motion of step 5 in WM Shell and Launcher3, with their security reviews); 2026-09-28 (the Tally shell: the privacy-indicator row widened to privacy indicators and disclosures, a shell fork rebase lag row and the shell's commit rules in the Fresh-UI boundary; the prototype's privacy chip at stock's sizes; the native privacy indicators of step 3.1: which of them take a tap, the location lamp by the lens and the limits of the area rule; the lock screen of step 3.3, the SettingsLib switches and the system label of step 3.5; the shade and Quick Settings of step 3.2; Recents' Stop of step 3.6; the step 3.1 fixes and an indicator bug fixed before any build; the step 3.3 fixes: the bouncer's visual-only code, the always-on strip, the per-user hint count, the Tally clock in the shared clock library; the volume panel, power menu and toasts of step 3.4; the step 3.1 security review; the Tally switch views of step 3.5; the shade's animated lamps and heads-up; the step 3.6 security review; the step 3.3 reviews A and B); 2026-09-27 (r9t: the production kernel configuration and module deny list, KPROBES and debugfs kept as on r9s, boot parameters logged by name only, the previous boot's logs kept by pstore/ramoops, the stock LPA's eSIM service off again, the touch controller firmware, what confidentiality lockdown costs at runtime, the saved console's log level, pstore phone results with cold reboots kept; the Tally privacy-indicator row; remote key provisioning failing in the TEE on r9t); 2026-09-26 (endpoint additions from the branding inventory, the approved shell rework, the network location and geocoding decisions, the keyboard privacy gap, and the bring-up and r9s updates; the full refresh of 2026-09-25 replaced the initial model of 2026-09-14).

> Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project.

## How to read this document

This document says what DiamaneOS protects, against whom, how, and what is
still missing. It covers one device, the Fairphone 6 (FP6).

- DiamaneOS is an OS under development. Every protection here is a
  requirement unless its status says otherwise. Nothing is qualified on a
  release candidate yet, and no hardware claim is a demonstrated protection
  until its validation passes.
- Start with [Biggest remaining risks](#biggest-remaining-risks). The
  [asset summary](#threats-by-asset) then lists every threat with its status,
  and each asset has its own section with the same six points.
- Status words (Assumption, Designed, Bring-up, Observed and so on) are
  defined in [Evidence states](#evidence-states).
- `FP6-nnn` is a task in the project plan. A number such as -144 is a finding
  in the project's private findings register (see
  [How this document is maintained](#how-this-document-is-maintained)).
  r9s, r9t and "build 8" are private test builds.
- Acronyms and platform terms are explained in [Terms](#terms) at the end.
- The Revision line above lists changes by date; the full
  [revision history](#revision-history) is near the end. Entries before this
  rewrite call each asset section a "row".

## Biggest remaining risks

A short summary. The linked sections hold the detail and the evidence.

1. **Development builds are not protected devices.** Every build so far is a
   private bring-up build: debuggable, with an unlocked bootloader and public
   test keys, and SELinux enforcement not yet validated per subsystem. See
   [Current development state](#current-development-state).
2. **No memory tagging, and a large closed code base.** The FP6 has no MTE, so
   memory-safety bugs are not contained as on current Pixels. The 720 closed
   Qualcomm and Fairphone files (about 369 MB) include large parsers of
   untrusted input, and the closed GPU driver and shader compilers run in
   every app process. See
   [App data from remote exploit](#app-data-from-remote-exploit),
   [Kernel integrity and all data](#kernel-integrity-and-all-data) and
   [Camera, microphone, sensor streams, device integrity](#camera-microphone-sensor-streams-device-integrity).
3. **Firmware the project cannot patch.** Boot, TrustZone, hypervisor, modem,
   DSP, Wi-Fi and Bluetooth firmware come from Fairphone and Qualcomm and lag
   Android Security Bulletins. DiamaneOS has no firmware update path yet. See
   [Firmware security](#firmware-security).
4. **Credential protection rests on the TEE.** No StrongBox or Weaver is
   exposed, so guess throttling rests on Qualcomm's TEE, and its timing and
   persistence are unverified. The mandatory generated passphrase is not
   implemented yet, and the fingerprint's declared strong class is not
   measured. See [BFU user data](#bfu-user-data) and
   [AFU unlock, auth-bound keys](#afu-unlock-auth-bound-keys).
5. **The modem is outside Android's control.** Its isolation is assumed, not
   verified; its own traffic bypasses Android's network controls; fake base
   stations cannot be detected; and the 2G protection and LTE-only controls
   currently fail silently. See
   [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location),
   [Application processor and user data](#application-processor-and-user-data)
   and [Traffic outside Android network policy](#traffic-outside-android-network-policy).
6. **USB-C port control is not wired up**, so charging-only-when-locked and
   deny-new-USB are missing. See
   [Locked-device data, kernel integrity](#locked-device-data-kernel-integrity).
7. **Hardware attestation fails, and verified boot will show yellow.** Remote
   key provisioning fails in the TEE on r9t, and a locked custom-key build
   reports yellow, never green. See
   [Hardware limits and evidence](#hardware-limits-and-evidence).
8. **Supply chain and review capacity.** One signing authority and one-person
   review; FP6 builds are not yet network-denied or built twice
   independently; closed vendor inputs are authenticated only by a hash from
   the vendor's own web estate. See
   [Source and build outputs](#source-and-build-outputs),
   [Closed vendor inputs](#closed-vendor-inputs) and
   [Release signing keys](#release-signing-keys).
9. **Keyboard privacy gap until the keyboard fork ships.** The inherited
   keyboard keeps learned and personal words readable before first unlock,
   allows them in backups and ignores the no-learning flag. See
   [Typed text and personal words](#typed-text-and-personal-words).

## Product scope

### Current product scope

DiamaneOS targets daily use by privacy- and security-focused users. This
threat model covers the GrapheneOS-based Fairphone 6 port: one device, one
variant.

- Release requirements include a locked bootloader on a custom AVB root and
  monthly releases.
- The launch scope below must pass qualification before public release.
- Not offered: root, microG, signature spoofing, Magisk accommodation, daily
  use with an unlocked bootloader, or a bundled cloud account.
- The UI aims to be clear and expressive, with measured maintenance.
  Accessibility and localization are acceptance criteria, not polish.
- No global security score, no promise of parity with stock, and no
  superiority claim without dated like-for-like evidence.

"GrapheneOS-derived" describes where the source comes from. It does not mean
security equal to GrapheneOS on its supported devices: the FP6 lacks several
hardware features GrapheneOS relies on (see
[Hardware limits and evidence](#hardware-limits-and-evidence)).

Passphrase-first is mandatory (passphrase onboarding and credential-policy
enforcement); an optional PIN is not in v1. Allowing a PIN with a warning
later needs an explicit scope decision and a renewed compatibility review; it
is recorded only as a condition to revisit.

### v1 scope (standalone summary)

Public launch waits for all of these on the final candidate, plus the release
gates (cadence, beta, qualification):

- DE/EU carrier configurations
- eSIM LPA
- camera at LineageOS parity
- passphrase-first setup, enforced for all users
- on-device security status
- encrypted microSD
- tracker alerts
- cellular alerts, or an honest "unsupported": on the current Qualcomm radio
  software this resolves to "unsupported" (see
  [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location))
- offline OTA update from SD card
- a UnifiedPush recommendation
- WebUSB and command-line installers

On 2026-09-26 the owner moved these after public v1: install-time privacy
presets, the battery suite (charge ceiling, health, cycle count and export),
DNS filtering, the privacy dashboard, per-app routing and filtering, panic
reboot to BFU, scheduled reboot, hardware diagnostics and removing metadata at
share time. Until they land, the inherited GrapheneOS protections cover their
safety role: inactivity auto-reboot, lockdown, VPN lockdown and the per-app
Network permission. Post-release ideas and conditional kernel or repair
research are explicitly not v1.

Several planned features (v1 and later) add code that parses untrusted input
or needs privilege. They are threats in their own right; see
[Device integrity and the data each feature handles](#device-integrity-and-the-data-each-feature-handles).

## Current development state

Everything that runs today is a private bring-up build (early work to make
the hardware function) on a development device. No build has been
distributed. Bring-up builds deliberately trade away protections to make
hardware work. A release candidate that fails any gate below is not released.

Rule: bring-up builds are never handed out and must not be used as a daily
phone by other people. Their signing and SELinux state gives no verified-boot,
signature-permission or SELinux protection. A device running a bring-up build
is not a protected device: personal accounts and sensitive data do not belong
on it until SELinux is enforcing.

**Build type**

- Today: `userdebug`, ADB on by default, `adb root` available. From r9s, ADB
  asks on the phone before trusting a computer and no developer key is
  pre-trusted (earlier development builds accepted ADB from any computer).
  Recovery still accepts ADB without asking on unlocked or debuggable phones
  (AOSP design).
- Release: `user` build, `ro.debuggable=0`, ADB off and authenticated, no
  pre-trusted keys, no ADB over the network.
- Gate: production-form user candidate (FP6-047), debug exposure audit
  (FP6-060).

**SELinux**

- Today: up to r9r the whole system is permissive. From r9s, debuggable builds
  boot enforcing with a per-domain policy built from the r9r denial captures:
  narrow rules only, generic proc and sysfs nodes relabelled before any write
  is granted, no dontaudit rules, nothing new for apps or shell. Untested on
  the phone.
- Release: enforcing, no permissive domains.
- Gate: enforcing runs per subsystem (no owning task yet), user candidate
  (FP6-047).

**Bootloader and keys**

- Today: unlocked; images signed with public AOSP test keys. The OS has no
  OEM unlocking control yet, so the bootloader's unlock-ability flag stays as
  the stock OS left it.
- Release: locked on a DiamaneOS AVB key; release keys only; relocking never
  enrols a public test key. The OS shows and manages OEM unlocking, so users
  can turn it off after relocking and back on before unlocking.
- Gate: custom-key relock (FP6-050), signing roles and equipment (FP6-035,
  FP6-036).

**Network endpoints**

- Today: inherited GrapheneOS services (connectivity, time, CT list,
  provisioning proxies, app catalog, SUPL proxy).
- Release: DiamaneOS EU endpoints with visible standard-server choices.
- Gate: endpoint contracts (FP6-100), endpoint implementations (FP6-102 to
  FP6-112).

**Closed vendor code**

- Today: 720 stock Qualcomm and Fairphone files (about 369 MB), selected
  stock-first and all hash-pinned. The per-file purpose is still generic for
  part of the set.
- Release: each file justified; layers replaced by source builds where
  possible.
- Gate: component removal and closure gate (FP6-060, FP6-061, FP6-208),
  per-component source replacement (FP6-200 to FP6-207).

**Debug interfaces**

- Today: userspace Qualcomm diag removed; debug USB functions still to be
  removed.
- Release: no debug USB functions, diag or trace sinks in user builds.
- Gate: debug exposure audit (FP6-060).

**Firmware**

- Today: whatever stock release was last flashed.
- Release: a dated firmware patch level shown; a firmware update path defined.
- Gate: firmware review (FP6-206).

### IMS status (2026-09-28)

IMS carries carrier voice, SMS and Wi-Fi calling over IP.

- The enforcing development image has shown ordinary voice, SMS and mobile
  data on the tested subscriptions, including basic Wi-Fi calling and
  coexistence with VPN lockdown.
- Reconnecting can still leave IMS unavailable for roughly ten minutes.
- An observer/address discrepancy on a second network is still under
  investigation.
- The passing simulated telephony tests do not validate emergency calls or
  carrier location delivery.
- The new `de.diamaneos` app and interface identities need their own build
  and device validation. Results from earlier images do not accept the
  namespace migration.

[IMS integration notes](#ims-integration-notes) record how the current
containment evolved.

## What DiamaneOS aims to defend against

- **Remote and proximity attack surface:** the network (including IP traffic
  over cellular data), Bluetooth, NFC, USB and media parsing. The defence is
  the inherited GrapheneOS hardening plus a reduced vendor surface. The FP6
  has no hardware memory tagging, so memory-safety bugs are not contained the
  way they are on current Pixels.
- **Malicious or over-permissioned apps:** sandboxing, the Network and Sensors
  permissions, Storage and Contact Scopes, install-time presets.
- **Persistence after compromise:** verified boot with rollback protection on a
  locked bootloader.
- **Opportunistic physical access to a powered-off (BFU) device:** file-based
  encryption with hardware-wrapped keys plus a strong generated passphrase.
- **Data exfiltration by the OS vendor:** no telemetry, no Google Mobile
  Services, self-hosted or user-selectable endpoints (documented EU endpoints
  with visible alternatives).

## What DiamaneOS does not defend against

- **Offline brute force of a weak credential.** Throttling is expected to
  exist: Gatekeeper enforces retry backoff in the Qualcomm TEE, with unverified
  timing and persistence on the FP6. No discrete secure element is exposed to
  Android, so throttling rests on the TEE, a larger attack surface with a
  public history of key-extraction attacks (see [BFU user data](#bfu-user-data)).
  A 6 to 8 word generated passphrase (required; not implemented yet) makes
  guessing much harder. It does not replace a secure element and does not fix
  TEE, firmware, AFU or credential-capture weaknesses.
- **Sophisticated forensic extraction** from an unlocked device, or from a
  powered-on locked (AFU) device.
- **Firmware-level compromise.** Boot, TrustZone, hypervisor, modem, DSP, Wi-Fi
  and Bluetooth firmware come from Fairphone and Qualcomm; the project cannot
  build or patch them.
- **Baseband exploitation** beyond what SoC isolation provides. On the FP6 that
  isolation is assumed, not verified yet.
- **Detection of fake base stations, null-cipher sessions or identity
  requests.** The current Qualcomm radio software is reported not to support
  these notifications (see
  [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location)).
- **Traffic the modem sends by itself** (IMS, SUPL and control-plane
  location). Android's network controls do not see it (see
  [Traffic outside Android network policy](#traffic-outside-android-network-policy)).
- **Removal by factory reset** of eSIM profiles, modem state or factory
  calibration. These live outside the user data partition.
- **Vendor-controlled unlock.** Installing, and recovering from a bad flash,
  depend on Fairphone's online unlock service. This is a dependency, not a
  security gap.
- **A determined adversary** with unlimited physical access and time.

## Hardware limits and evidence

- **No MTE:** the stock CPU feature set does not expose memory tagging, and its
  control properties are absent.
- **No StrongBox or Weaver exposed:** stock declares only default TEE KeyMint
  and RKP instances, and no Weaver HAL. Stock does ship disabled software for
  a Secure Processing Unit (SPU), but the FP6 device tree describes no such
  unit and the images carry no SPU firmware or provisioning, so none is
  usable; whether the chip itself has one is unknown. The accurate statement
  is "stock exposes no StrongBox or Weaver", not "the phone has no secure
  hardware". The eUICC, NFC controller and TEE are separate boundaries.
- **Attestation is TEE-only.** A locked custom-key build reports a yellow
  verified boot state, never green. The GrapheneOS Auditor app does not
  support the FP6.
  - Builds before r9s did not enable remote key provisioning, so hardware
    attestation was expected to fail.
  - From r9s the device sets the provisioning properties stock sets. Requests
    go through the inherited GrapheneOS proxy until DiamaneOS runs its own.
  - On r9t provisioning reaches the server, but the TEE's certificate request
    fails on every attempt, so no attestation keys are provisioned and
    hardware attestation still fails (cause open).
  - From r9s the device also reports the factory attestation IDs, and a wipe
    asks the TEE to delete all old keys (untested).
- **No pKVM:** on current firmware the kernel runs under Qualcomm's Gunyah
  hypervisor, not KVM, so Android protected VMs are unavailable (observed on a
  bring-up build). Gunyah and its trusted VMs are closed firmware.
- **USB-C port control** is not wired up on the FP6; see
  [Locked-device data, kernel integrity](#locked-device-data-kernel-integrity).
- **Fingerprint:** the HAL declares the Class 3 (strong) level without spoof
  testing; see [AFU unlock, auth-bound keys](#afu-unlock-auth-bound-keys).
- **Radio isolation:** SoC-level isolation of the modem from the application
  processor is assumed and unverified on the FP6.
- **Firmware patch lag:** firmware lags Android Security Bulletins, and
  DiamaneOS has no firmware update path yet; see
  [Firmware security](#firmware-security).
- **Vendor horizon:** the Android 14-level vendor (VINTF level 8) should still
  run on Android 18, but Android 19 will very likely drop level 8, HIDL and
  kernel 6.1. Once Fairphone moves the FP6 to a newer vendor, fixes for the
  current vendor code and firmware stop, so DiamaneOS has to follow that move.
- **Kernel:** the kernel line is 6.1 (android14 KMI), with a recorded,
  intentional deviation for a 48-bit virtual address space.
- **Still to be tested on a candidate:** TEE KeyMint, Gatekeeper and
  attestation behaviour, custom-key relock, A/B failure recovery and rollback
  behaviour.

## Evidence states

Each asset carries one of these states. A state changes only with new
evidence.

- **Assumption**: plan text; no design or device evidence yet.
- **Designed**: design or configuration reviewed; not running on a device.
- **Bring-up (not qualified)**: present in a private bring-up build; the
  security property itself is untested, and SELinux is not enforcing.
- **Observed (bring-up)**: a narrow behaviour was seen on a bring-up build.
- **Recorded**: stated in a project record, not rechecked for this revision.
- **Observed gap**: the protection is confirmed missing or not working today.
- **Accepted limitation**: a documented limit the project does not plan to
  remove.
- **Qualified**: the named validation passed on a release candidate. Nothing
  is qualified yet.

Rule: an asset's state is the weakest state among its listed mitigations. Its
status names that weakest state first; stronger items follow.

## Threats by asset

An asset is something worth protecting. Assets are grouped by where the
attacker is: remote, cellular, location, malicious apps, DiamaneOS-added
features, proximity, physical access to a powered-on locked phone (AFU),
physical access to a powered-off phone (BFU), boot and firmware, supply chain,
and everyday use. Each asset section has the same points:

- **Who could attack:** the attacker and what they can do.
- **Where:** the entry point.
- **How we protect it:** the intended mitigation.
- **What remains:** limits that stay even with the mitigation.
- **Validation:** the check that must pass, with the owning plan task in
  parentheses, or "no owning task yet".
- **Status:** the evidence state, weakest first.

| Group | Asset | Status (weakest first) |
| --- | --- | --- |
| Remote | [Service metadata and OS identity](#service-metadata-and-os-identity) | Assumption |
| Remote | [DNS privacy](#dns-privacy) | Assumption |
| Remote | [App data from remote exploit](#app-data-from-remote-exploit) | Assumption |
| Remote | [Traffic outside Android network policy](#traffic-outside-android-network-policy) | Accepted limitation |
| Remote | [Certificate validity checks at boot](#certificate-validity-checks-at-boot) | Bring-up (not qualified) |
| Cellular | [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location) | Observed gap (bring-up) |
| Cellular | [SMS, SIM applets, broadcast alerts, carrier configuration](#sms-sim-applets-broadcast-alerts-carrier-configuration) | Accepted limitation (inherited) |
| Cellular | [Application processor and user data](#application-processor-and-user-data) | Assumption |
| Cellular | [Telephony control and subscriber data](#telephony-control-and-subscriber-data) | Bring-up (not qualified) |
| Cellular | [eSIM profiles and download sessions](#esim-profiles-and-download-sessions) | Bring-up (not qualified) |
| Cellular | [Radio-off expectation, location privacy](#radio-off-expectation-location-privacy) | Assumption |
| Location | [Location history](#location-history) | Assumption |
| Apps | [App and user data](#app-and-user-data) | Assumption |
| Apps | [Per-app destination history (dashboard)](#per-app-destination-history-dashboard) | Assumption |
| Apps | [Kernel integrity and all data](#kernel-integrity-and-all-data) | Built for r9t, not phone-tested; Observed (bring-up) |
| Apps | [Camera, microphone, sensor streams, device integrity](#camera-microphone-sensor-streams-device-integrity) | Bring-up (not qualified) |
| Apps | [Persistent hardware identifiers](#persistent-hardware-identifiers) | Observed gap (bring-up, permissive) |
| Apps | [Secondary-profile data](#secondary-profile-data) | Assumption |
| Added features | [Device integrity and the data each feature handles](#device-integrity-and-the-data-each-feature-handles) | Assumption |
| Proximity | [Device integrity, paired-device data, Bluetooth address](#device-integrity-paired-device-data-bluetooth-address) | Bring-up (not qualified) |
| Proximity | [SIM applets, presence, NFC services](#sim-applets-presence-nfc-services) | Bring-up (not qualified) |
| Proximity | [Wi-Fi MAC (tracking), device integrity](#wi-fi-mac-tracking-device-integrity) | Assumption |
| Proximity | [Locked-device data, kernel integrity](#locked-device-data-kernel-integrity) | Observed gap |
| AFU | [AFU user data](#afu-user-data) | Observed gap |
| AFU | [Previous boot's kernel log and event logs](#previous-boots-kernel-log-and-event-logs) | Assumption |
| AFU | [AFU unlock, auth-bound keys](#afu-unlock-auth-bound-keys) | Observed gap |
| AFU | [AFU data under coercion or seizure](#afu-data-under-coercion-or-seizure) | Assumption |
| BFU | [BFU user data](#bfu-user-data) | Observed gap |
| BFU | [Device-persistent data outside userdata](#device-persistent-data-outside-userdata) | Observed gap |
| BFU | [Removable media](#removable-media) | Assumption |
| Boot and firmware | [OS integrity on a released, locked build](#os-integrity-on-a-released-locked-build) | Observed gap (bring-up images) |
| Boot and firmware | [Firmware security](#firmware-security) | Observed gap |
| Boot and firmware | [Keys, Gatekeeper throttling, fingerprint templates](#keys-gatekeeper-throttling-fingerprint-templates) | Observed gap (builds before r9s) |
| Supply chain | [Source and build outputs](#source-and-build-outputs) | Observed gap (FP6 path) |
| Supply chain | [Closed vendor inputs](#closed-vendor-inputs) | Observed gap |
| Supply chain | [Release signing keys](#release-signing-keys) | Designed |
| Supply chain | [Repositories, domain, install page](#repositories-domain-install-page) | Assumption |
| Supply chain | [Exposure window for known bugs](#exposure-window-for-known-bugs) | Observed gap |
| Supply chain | [Shell fork rebase lag](#shell-fork-rebase-lag-timely-grapheneos-security-fixes-under-the-tally-shell) | Assumption |
| Supply chain | [Install-time trust](#install-time-trust) | Assumption |
| Everyday use | [Correct user decisions](#correct-user-decisions) | Assumption |
| Everyday use | [Typed text and personal words](#typed-text-and-personal-words) | Designed |
| Everyday use | [Privacy indicators and disclosures under the Tally shell](#privacy-indicators-and-disclosures-under-the-tally-shell) | Assumption |
| Everyday use | [Files archive handling (browse, extract, create)](#files-archive-handling-browse-extract-create) | Designed, fuzzed on the host |

### Remote attackers (network and content)

#### Service metadata and OS identity

- **Who could attack:** a network observer; an endpoint operator.
- **Where:** connectivity checks, captive-portal probes, DNS checks, HTTPS
  time, the CT list, key and DRM provisioning, app and browser updates,
  network location and geocoding (when turned on), Auditor remote
  verification and sample submission (opt-in); on the phone side, eSIM
  (SM-DP+/SM-DS) and carrier entitlement connections.
- **How we protect it:**
  - No telemetry or GMS.
  - From r9s the build properties keep Fairphone's stock product identity
    (brand, product, device, model), as GrapheneOS keeps Google's.
  - EU-primary DiamaneOS endpoints with documented upstreams. Each endpoint
    has a visible standard-server alternative.
  - Network location is off by default, and the setup wizard never turns it
    on silently. The opt-in choices are a DiamaneOS EU relay to Apple (no
    cache, no per-request logs, device IP hidden from Apple), Apple directly,
    or Apple China directly.
  - No DiamaneOS geocoder: geocoding is off by default. Opting in goes
    directly to OpenStreetMap's public Nominatim, under a DiamaneOS user
    agent.
  - eSIM and carrier hosts are listed as non-project endpoints. They are
    ordinary app traffic and visible to Android's network controls.
- **What remains:**
  - Hostnames used by a small user base are a fingerprint.
  - The HTTPS time bootstrap resolves outside Private DNS.
  - The DNS resolver and the browser put GrapheneOS probe names in DNS
    queries even when connectivity checks are turned off. The browser runs its
    own connectivity checks, which only a rebuilt browser can repoint.
  - When network location is on (the setup wizard's location switch turns it
    on), nearby Wi-Fi and cell identifiers go to GrapheneOS's relay for
    Apple's location service, and geocoding queries go to GrapheneOS's server
    under a GrapheneOS user agent.
  - With the direct choices, Apple sees the device IP with the Wi-Fi and cell
    list, and OpenStreetMap sees the device IP with the searched text or
    coordinates.
  - Auditor's opt-in remote features talk to GrapheneOS's attestation
    service, and sample submission sends the full system property list.
  - Non-EU upstreams are disclosed per endpoint.
  - Certificate-transparency enforcement fails open when the CT list is more
    than 70 days old, so CT mirror freshness is a security dependency.
  - "No telemetry" in the closed vendor files rests on a static scan; runtime
    egress is not measured yet.
- **Validation:** endpoint contracts (FP6-100); time, connectivity,
  provisioning, CT and catalog endpoints (FP6-102 to FP6-112); network
  location and geocoding (FP6-103); attestation (FP6-106); runtime egress
  capture with SELinux enforcing (no owning task yet).
- **Status:**
  - Assumption: 13 of 16 endpoint contracts are not specified yet (the Info
    release feed was retired on 2026-09-26 with the Info app). eSIM and
    carrier egress are not in the contract yet.
  - Designed: 3 of 16 contracts specified, none deployed. The network
    location and geocoding choices are decided; both contracts are still
    blocked.
  - Bring-up builds use the inherited GrapheneOS endpoints.
  - Observed (static): no contacted host found in the selected closed files,
    apart from GNSS cloud hosts removed by pinned configuration.

#### DNS privacy

- **Who could attack:** a network observer; an on-path resolver.
- **Where:** DNS queries.
- **How we protect it:** a visible encrypted-DNS default (DoT) with no silent
  fallback to plaintext.
- **What remains:** the time bootstrap, apps with their own DoH/DoT, and modem
  traffic bypass it.
- **Validation:** encrypted-DNS default and DNS filter (FP6-079).
- **Status:** Assumption: not implemented; the inherited Android default is in
  force.

#### App data from remote exploit

- **Who could attack:** a remote attacker using network, media or web content.
- **Where:** carrier and Wi-Fi data, media files and streams, the browser and
  WebView, captive portals, web content reaching the GPU shader compiler,
  hardware video decode (planned).
- **How we protect it:**
  - Inherited sandbox, hardened_malloc and exec spawning.
  - The Vanadium browser and WebView as mirrored, unmodified APKs.
  - The hardware codec service is to keep the stock seccomp sandbox and the
    platform codec domain.
  - Codec device nodes are to be separated from camera nodes and from display
    configuration.
- **What remains:** no MTE on the FP6; the closed GPU driver and shader
  compilers run in every app process; closed codec and video firmware (not
  integrated yet); codec seccomp installation is unverified and can fail
  open; the captive-portal WebView is inherited; browser component updates
  still point upstream.
- **Validation:** inherited hardening (FP6-046), debug exposure and component
  removal (FP6-060, FP6-061), hardware media integration (no owning task yet),
  DRM limits (FP6-065), browser delivery (FP6-105, FP6-111).
- **Status:** Assumption: exec spawning, sandbox permissions, the codec
  sandbox and browser delivery are unvalidated on the FP6. Observed
  (bring-up): hardened_malloc active (48-bit VA kernel).

#### Traffic outside Android network policy

- **Who could attack:** the carrier; a network observer.
- **Where:** modem IMS signalling and media, SUPL and control-plane location,
  network-initiated location.
- **How we protect it:** documented; carrier hosts listed as non-project
  endpoints; the privacy dashboard and per-app routing state that they do not
  cover this traffic.
- **What remains:** invisible to the Network permission, VPN, Private DNS, the
  DNS filter and the dashboard.
- **Validation:** endpoint contracts (FP6-100), assisted GNSS (FP6-103),
  carrier configuration review per supported carrier (FP6-090).
- **Status:** Accepted limitation. Carrier egress is not yet listed in the
  endpoint contract.

#### Certificate validity checks at boot

- **Who could attack:** an on-path network attacker holding an expired
  certificate, or blocking time sources.
- **Where:** the system clock between boot and the first network time. The
  PMIC RTC cannot be set from Linux.
- **How we protect it:**
  - DiamaneOS's own `timekeepd` saves how far the clock is ahead of the RTC
    counter whenever the time is set, and at least hourly.
  - At post-fs-data, before zygote starts, a one-shot step sets the clock from
    the counter plus that offset: only if nothing has set the clock since
    boot, and only to a time before 2100.
  - Only that step holds CAP_SYS_TIME; the part that keeps running holds no
    capability.
  - Each part has its own SELinux domain, with no modem, network or app
    access. Together they replace Qualcomm's time daemon and time app.
  - Network time is HTTPS, with certificate expiry checked against the build
    time.
- **What remains:**
  - The clock starts at the build date on first boot and after a factory
    reset.
  - After the PMIC loses power (battery removed), the clock starts at the last
    saved time. That can be up to about an hour older than the last time the
    phone was awake, plus however long it was off.
  - Until network time arrives, certificates that expired since then still
    validate, and blocking time sources prolongs that.
  - RTC drift between saves (1 s resolution).
- **Validation:** the clock across reboot, suspend, manual and network time
  changes and battery removal, with SELinux enforcing (no owning task yet).
- **Status:** Bring-up (not qualified): implemented for r9s; untested on the
  phone.

### Cellular and baseband

#### Call and SMS content, subscriber identity, coarse location

- **Who could attack:** a fake base station, IMSI catcher, downgrading network
  or null-cipher network.
- **Where:** 2G fallback, identity requests before authentication, radio
  security events.
- **How we protect it:** opt-in "2G network protection" and LTE-only controls.
  Planned: the security status view shows cellular alerts as unsupported.
- **What remains:**
  - 2G stays allowed by default (the AOSP and GrapheneOS default), so a
    downgrade to 2G works unless the user opts in to 2G network protection.
  - The opt-in controls are visible today but fail open without any signal:
    the selected mode does not reach the modem. The planned default-mode fix
    covers new subscriptions only, so stored settings need a reset step and a
    regression check.
  - No detection: null-cipher/integrity control and security notifications
    are reported unsupported by the current radio software.
  - LTE/NR identity exposure remains.
- **Validation:** telephony bring-up (FP6-044), cellular security
  notifications (FP6-084), security status view (FP6-073); a radio-policy
  regression check has no owning task yet.
- **Status:** Observed gap (bring-up): the controls are visible and silently
  ineffective; they must be fixed or hidden before any build leaves
  development. The null-cipher control is reported unsupported (bring-up
  notes; the radio daemon's response is not captured yet).

#### SMS, SIM applets, broadcast alerts, carrier configuration

- **Who could attack:** a network or SIM-side sender; the carrier.
- **Where:** SMS and class-0/type-0 messages, SIM toolkit proactive commands,
  cell broadcast, SIM access rules that grant carrier privileges.
- **How we protect it:** inherited AOSP handling; no project change.
- **What remains:** SIM applets act partly outside OS control; network
  "silent" SMS are acknowledged without display under 3GPP rules; SMS are
  parsed in the modem and the framework; apps the SIM grants carrier
  privileges can change carrier configuration.
- **Validation:** telephony bring-up (FP6-044).
- **Status:** Accepted limitation (inherited).

#### Application processor and user data

- **Who could attack:** a remote over-the-air attacker; compromised modem
  firmware.
- **Where:** modem protocol parsers (RRC/NAS, SMS, modem IMS), then shared
  memory, the IPA data path, QMI/QRTR services, and modem remote-storage and
  file services.
- **How we protect it:** the modem image is authenticated by the stock
  secure-boot chain; the modem services on the application processor (AP) are
  narrowed and confined to their own SELinux domains; modem crash dumps are
  off.
- **What remains:** modem firmware cannot be patched by the project and lags
  ASB; SoC isolation (SMMU and memory protection) is assumed, not verified on
  the FP6; modem restarts happen silently, so a crash-and-retry attack is
  invisible; modem state persists across factory reset.
- **Validation:** modem and telephony bring-up (FP6-042, FP6-044), debug
  exposure and component removal (FP6-060, FP6-061).
- **Status:** Assumption. Bring-up runs the modem and its AP services under
  permissive SELinux; no protection claim.

#### Telephony control and subscriber data

- **Who could attack:** a malicious app through binder; a compromised modem
  through QMI.
- **Where:** the closed radio daemon (QCRIL), the Qualcomm IMS app,
  DiamaneOS's own call-audio bridge, the eSIM LPA.
- **How we protect it:**
  - Explicit privileged-permission allowlists with denials; own SELinux
    domains.
  - No radio daemon socket; the radio daemon is not a binder service; diag
    access removed.
  - The call-audio bridge that replaces Qualcomm's call-audio app holds only
    the normal audio-settings permission (no audio-routing or capture
    permission, no network). Its domain reaches only the radio daemon's
    call-audio service and the audio server.
- **What remains:** closed code parses untrusted input; locking the update
  path of presigned vendor apps is planned, not implemented; VoLTE work is
  expected to add more closed, network-capable daemons and a presigned
  privileged app, so the closed surface is still growing; enforcing mode is
  untested.
- **Validation:** telephony bring-up (FP6-044), eSIM selection and delivery
  (FP6-207, FP6-089), enforcing runs (no owning task yet).
- **Status:** Bring-up (not qualified): allowlists and domains reviewed;
  runtime and enforcing unverified; the call-audio bridge is implemented for
  r9s, untested on the phone.

#### eSIM profiles and download sessions

- **Who could attack:** a TLS interceptor; a thief or examiner after a reset; a
  coercer.
- **Where:** LPA connections to eSIM servers; profiles kept in the eUICC.
- **How we protect it:**
  - Installed eSIM profiles work as SIMs without an LPA (the GrapheneOS
    baseline).
  - From r9t the stock Qualcomm LPA stays installed with its services off by
    default (stock leaves its eSIM service on), and its JNI library is no
    longer shipped.
  - Planned: our own source-built LPA (list, enable, disable and delete over
    the platform's logical channels; download later) behind an "eSIM support"
    switch that is off by default (owner decision 2026-09-27).
  - OpenEUICC excluded (GrapheneOS os-issue-tracker #6275 and #2631).
  - GSMA SGP.22 mutual authentication.
- **What remains:** the stock LPA cannot list profiles on the FP6 (the stock
  radio daemon cannot decode the modem's reply), so Android treats an eSIM as
  a physical SIM. No eSIM can be added, switched or deleted from Android, and
  factory reset and duress cannot reach the eUICC. The stock LPA's TLS trust
  configuration does not meet the production trust rule; that matters again
  only if it is turned on.
- **Validation:** eSIM selection and delivery (FP6-207, FP6-089).
- **Status:** Bring-up (not qualified): on r9s the service was on and could
  not list profiles (phone test 2026-09-27); r9t turns it off again, not yet
  built.

#### Radio-off expectation, location privacy

- **Who could attack:** a network or passive observer.
- **Where:** the modem attaching on its own; airplane mode.
- **How we protect it:** airplane mode puts the modem in low-power mode.
- **What remains:** it is not verified that the modem stays silent in airplane
  mode or without the radio daemon; a device setting keeps the SIM powered in
  airplane mode.
- **Validation:** telephony bring-up (FP6-044).
- **Status:** Assumption.

### Location

#### Location history

- **Who could attack:** the vendor cloud; an assistance-server operator; the
  carrier; a local log reader.
- **Where:** GNSS HAL configuration, SUPL, PSDS, control-plane positioning,
  logs.
- **How we protect it:** Qualcomm cloud, XTRA and crowdsourcing paths excluded
  by pinned configuration; PSDS off until an endpoint is bound; SUPL
  user-selectable (Off, proxy, standard); network location and geocoder off
  by default; IMS geolocation has no network geocoder.
- **What remains:**
  - SUPL in the image defaults to the GrapheneOS proxy until the DiamaneOS
    SUPL endpoint exists.
  - "Standard" SUPL goes directly to the carrier-configured server (on the
    carrier tested so far, Google's server).
  - SUPL requests carry cell information.
  - Control-plane positioning runs in the modem.
  - GNSS engine state on the persist partition survives reset.
  - The location logging level is still verbose.
  - The radio log records the serving-cell identity on every registration
    poll (today about twice a second on one SIM), readable over adb and in bug
    reports.
- **Validation:** assisted GNSS (FP6-103), GNSS bring-up tests (FP6-045).
- **Status:** Assumption: SUPL default not decided yet; no GNSS fix recorded.
  Bring-up (not qualified): cloud paths absent and pinned.

### Malicious and over-permissioned apps

#### App and user data

- **Who could attack:** a malicious or over-permissioned app.
- **Where:** permissions, background listeners, IPC/URI grants, sensors.
- **How we protect it:** per-app Network and Sensors permissions, Storage and
  Contact Scopes (inherited); install presets (Untrusted, Standard, Trusted);
  per-app routing and filtering; a privacy dashboard with bounded history in
  CE storage only.
- **What remains:** the "Trusted" preset is a policy the user chooses, never an
  audit; routing attribution has limits (shared UIDs, apps' own DoH/DoT,
  system and modem traffic); v1 features are not implemented yet.
- **Validation:** inherited hardening (FP6-046), privacy presets (FP6-072),
  DNS architecture and filter (FP6-078, FP6-079), per-app network controls
  (FP6-080), privacy dashboard (FP6-081).
- **Status:** Assumption: inherited controls are present in source,
  unvalidated on the FP6; v1 features not implemented.

#### Per-app destination history (dashboard)

- **Who could attack:** an AFU forensic examiner; a malicious app.
- **Where:** dashboard storage, backups, bug reports.
- **How we protect it:** CE-only storage, aggregate by default, 7-day default,
  cleared on reboot by default, no backup, not included in bug reports.
- **What remains:** the dashboard is itself the most sensitive log on the
  device.
- **Validation:** privacy dashboard (FP6-081).
- **Status:** Assumption: plan rules only.

#### Kernel integrity and all data

- **Who could attack:** a malicious app; a compromised app or renderer process.
- **Where:** the GPU driver node (reachable by every app, as on stock), binder,
  loaded network-protocol modules and socket families, the DSP remote-call
  driver (limited to named groups).
- **How we protect it:**
  - GrapheneOS kernel hardening configuration merged into the vendor kernel.
  - Kernel lockdown (confidentiality).
  - SELinux socket and device restrictions.
  - Module set reduced to what the product uses.
  - Rule: profiling work must never weaken lockdown or tracing restrictions in
    user builds.
- **What remains:**
  - No MTE; a large vendor driver surface; hardening hand-merged into a vendor
    kernel with an intentional KMI deviation.
  - From r9t (prepared for r9t, not yet built): the protocol modules with no
    product use (CAN, 802.15.4/6LoWPAN, the kernel NFC socket family,
    PPTP/L2TP and the in-kernel Bluetooth stack) are no longer shipped, and
    the kernel has no SELinux development mode and restricts dmesg to root.
  - debugfs stays built in and mountable, as on r9s: user builds never mount
    it, debuggable builds only until boot completes, and a stricter kernel
    change is open (see the [debugfs decision](#decision-record)).
  - USB host-class drivers and the USB debug and trace drivers still load.
  - KPROBES stays on (owner-accepted), because the USB controller glue
    implements its hooks with kretprobes. Lockdown keeps user space from
    placing probes.
  - TIPC is re-enabled (GrapheneOS disables it) because Qualcomm's
    mobile-data stack needs it. It is built as a module without network
    bearers or crypto. Under enforcing SELinux neither user-installed nor
    privileged apps can reach it: its sockets are limited to two radio
    daemons, and its configuration interface to system components that hold
    the network-admin capability, including the network stack module.
  - Confidentiality lockdown also turns kernel tracing off and withholds
    kernel-memory reads from BPF programs. So Android's per-app CPU time
    accounting (per-app CPU use in Battery usage) and the memory-event OOM
    listener do not start.
  - The lockdown level (confidentiality or integrity) is an open decision.
  - Every app can reach the kernel's userfaultfd code, which ART's garbage
    collector needs. The kernel forces apps into user-mode-only mode
    (`vm.unprivileged_userfaultfd=0`, `/dev/userfaultfd` root-only and
    SELinux-denied), so they cannot freeze the kernel mid-copy to widen race
    windows. Bugs in that code stay ordinary kernel attack surface, fixed
    through 6.1 LTS updates.
- **Validation:** kernel build (FP6-041), debug exposure and component removal
  (FP6-060, FP6-061), enforcing runs (no owning task yet).
- **Status:** module reduction and the production kernel configuration are
  built for r9t (kernel check 2026-09-27: the configuration matches the
  policy, and no loaded module depends on a removed one), not yet
  phone-tested; r9s runs enforcing. Observed (bring-up): lockdown
  confidentiality active; tracing empty.

#### Camera, microphone, sensor streams, device integrity

- **Who could attack:** a malicious app, or remote content reaching closed
  vendor code through a platform service.
- **Where:** camera, media, display, audio, sensors, GNSS, NFC and Bluetooth
  HAL interfaces; GPU libraries in the same process; persist and vendor data
  files parsed by closed code.
- **How we protect it:**
  - A per-file allowlisted, hash-pinned stock selection.
  - SELinux grants bound to each service domain.
  - Internal service endpoints of a closed HAL reachable only from that HAL's
    own process (audio from r9s).
  - Fewer service users, groups and capabilities; tight device-node
    permissions.
  - Qualcomm diagnostics, telemetry and factory services excluded.
  - Layers replaced by source builds over time.
- **What remains:** large closed parsers (camera about 185 MB) without MTE and
  mostly without seccomp; closed performance and thermal daemons run as root;
  Android 14 ABI vendor code on Android 17; the per-file purpose is still
  generic for part of the set; closed code updates only through Fairphone
  stock releases.
- **Validation:** debug exposure and component removal (FP6-060, FP6-061),
  closure gate (FP6-208), camera (FP6-045, FP6-203), audio (FP6-044,
  FP6-205), hardware media integration (no owning task yet), enforcing runs
  (no owning task yet).
- **Status:** Bring-up (not qualified): the selection is reviewed per
  subsystem and running permissive; enforcing untested.

#### Persistent hardware identifiers

- **Who could attack:** any app.
- **Where:** system properties, persist files, sysfs, logs.
- **How we protect it:** vendor-internal property types with no read grant;
  narrowed sysfs labels; a random Bluetooth address per install (intended);
  Wi-Fi MAC randomization; traceability services excluded; boot parameters
  handed to user space logged by name only (r9t, not yet built); enforcing
  SELinux.
- **What remains:**
  - Identifiers are readable on any permissive build.
  - Which Bluetooth address path runs is unrecorded (the HAL tries a factory
    address first), and the address appears in logs and bug reports.
  - The stock camera HAL writes the camera module serial numbers to the log
    at every start.
  - The SoC serial number (sysfs `soc0/serial_number`) is readable by 16
    system and vendor domains through platform sysfs read grants and imported
    Qualcomm rules. No app domain can read it, and the r9s policy gives the
    camera HAL and nicmd only the public SoC id.
  - The factory Wi-Fi MAC in persist is not read under enforcing, so the
    driver falls back to the chip's own MAC or one derived from its serial.
  - Wi-Fi MAC randomization is unverified.
  - A flash dump reveals persist data.
- **Validation:** enforcing runs and an identifier probe test (no owning task
  yet), Wi-Fi and Bluetooth bring-up (FP6-043).
- **Status:** Observed gap (bring-up, permissive): some hardware serials are
  exposed as system properties; an enforcing denial is not yet shown.

#### Secondary-profile data

- **Who could attack:** another user or profile on the same device.
- **Where:** the user switch, stopped and running profiles, unified versus
  separate challenge.
- **How we protect it:** the same credential policy on every independent
  challenge, through a real service boundary; CE/DE separation; ending a
  session is not deletion.
- **What remains:** unified-challenge profiles follow platform semantics, with
  no invented independent key; a stopped session still holds data until it is
  deleted.
- **Validation:** encryption and hardening validation (FP6-046),
  credential-policy enforcement (FP6-071).
- **Status:** Assumption.

### DiamaneOS-added features

#### Device integrity and the data each feature handles

- **Who could attack:** remote content, a malicious list or network source, a
  nearby BLE device, a malicious update file.
- **Where:** the DNS filter (network bytes, downloaded lists), metadata
  removal at share time (image parsers), tracker alerts (BLE payloads),
  offline SD OTA (file input), per-app routing and battery controls
  (privileged hooks).
- **How we protect it:** unprivileged, isolated and resource-bounded parsing
  workers; scoped URI grants; signed, simple list formats; offline OTA uses
  the same signature checks as online updates; least-privilege hooks;
  optional features off by default where the plan says so.
- **What remains:** every feature adds code and privilege; platform image APIs
  can invoke native parsers; none is implemented yet.
- **Validation:** DNS filter (FP6-078, FP6-079), per-app controls (FP6-080),
  dashboard (FP6-081), tracker alerts (FP6-083), offline SD OTA (FP6-086),
  metadata stripping (FP6-087), battery (FP6-074, FP6-075).
- **Status:** Assumption.

### Proximity attackers

#### Device integrity, paired-device data, Bluetooth address

- **Who could attack:** a nearby attacker over Bluetooth Classic (BR/EDR) or
  Low Energy (LE); a tracker.
- **Where:** closed controller firmware, the closed stock Bluetooth HCI HAL,
  the GrapheneOS host stack.
- **How we protect it:** the GrapheneOS host stack; Bluetooth off by default;
  profiles gated by consent; SIM access profile off; a random per-install
  address (intended); the closed HAL confined (no network, no persist write,
  no diag); unknown-tracker alerts (v1).
- **What remains:** the controller firmware and HAL cannot be patched by the
  project; no MTE; the live address path is unverified and the address
  appears in logs; the classic address is stable per install; tracker alerts
  are not built.
- **Validation:** Bluetooth and audio bring-up (FP6-043, FP6-044), debug
  exposure and component removal (FP6-060, FP6-061).
- **Status:** Bring-up (not qualified): pairing and audio work on a bring-up
  build; security properties and enforcing untested.

#### SIM applets, presence, NFC services

- **Who could attack:** a nearby contactless reader, including against a
  locked, screen-off or switched-off phone; a malicious tag.
- **Where:** NFC reader and listen modes, controller-to-SIM routing.
- **How we protect it:** the default route is pinned to the host (no SIM
  routing unless an app registers it); no UICC or embedded secure element
  features declared; the controller firmware file is under verified boot once
  locked.
- **What remains:** NFC is on by default (decision pending); registered SIM
  routes stay reachable while locked or off; secure-NFC support is
  unverified; the controller firmware is closed, and the HAL can also update
  it.
- **Validation:** NFC bring-up tests (FP6-045), eSIM/SIM (FP6-089).
- **Status:** Bring-up (not qualified): the reader works; routing tests not
  run.

#### Wi-Fi MAC (tracking), device integrity

- **Who could attack:** a passive Wi-Fi observer; a malicious access point; a
  LAN peer.
- **Where:** probe requests, association, the Wi-Fi driver and firmware,
  wake-on-LAN.
- **How we protect it:** only station features are exposed to Android (no
  hotspot, Wi-Fi Direct or Aware); source-built Wi-Fi HAL and driver; MAC
  randomization.
- **What remains:** MAC randomization is unverified on this HAL and firmware;
  closed Wi-Fi firmware sits on the over-the-air path; a LAN peer can wake the
  device with a magic packet; on Wi-Fi networks used for Android Auto the
  device sends a DHCP hostname derived from the device name (inherited
  default).
- **Validation:** connectivity bring-up (FP6-043).
- **Status:** Assumption: unverified.

#### Locked-device data, kernel integrity

- **Who could attack:** a malicious USB device or host; a forensic tool; a
  malicious charger.
- **Where:** the USB-C data lines (host and gadget roles), USB descriptors, USB
  PD and charger firmware.
- **How we protect it (intended):** GrapheneOS USB-C port control
  (charging-only when locked, deny new USB devices); standard gadget functions
  only; debug USB functions removed from user builds; a reduced USB driver
  set.
- **What remains:** port control is not wired up on the FP6 yet; the
  deny-new-USB hook is present in the merged kernel source, but whether the
  built kernel exposes it is unchecked; there is no evidence of a hardware
  USB data-line cutoff; broad USB host drivers are loaded; the USB descriptor
  carries the device serial; the charger firmware is closed.
- **Validation:** debug exposure audit (FP6-060), USB modes (FP6-045),
  hardware USB data disable (no owning task yet).
- **Status:** Observed gap: port control absent. Bring-up (not qualified): the
  userspace gadget configuration is reviewed; debug USB functions are still
  to be removed from user builds.

### Physical access to a powered-on, locked device (AFU)

#### AFU user data

- **Who could attack:** someone with physical access to a locked, powered-on
  phone; forensic tools.
- **Where:** USB, the lock screen, firmware download and dump modes.
- **How we protect it:** inactivity auto-reboot (inherited); USB port control;
  the OS refuses firmware-download and dump reboots; panic RAM dumps off.
- **What remains:** port control is not wired up on the FP6; the OS refusal of
  firmware-download reboots is not implemented; the kernel keeps a minidump
  on panic, and where it can be retrieved is unverified; the early-boot
  setting that also turns dump mode off needs a policy grant before
  enforcing; EDL stays reachable with physical access and a signed
  programmer; no forensic-proof claim.
- **Validation:** debug exposure audit (FP6-060), credential policy (FP6-071),
  scheduled reboot (FP6-085).
- **Status:** Observed gap: port control absent; download-reboot refusal not
  implemented. Observed (bring-up): panic dump mode off by kernel default.

#### Previous boot's kernel log and event logs

- **Who could attack:** root or system code after a reboot; anyone with USB
  debugging on and an authorized computer; a forensic examiner with the
  phone.
- **Where:** a RAM region kept across a soft reboot (pstore/ramoops); its copy
  in the system's crash-report store; Qualcomm minidumps.
- **How we protect it:**
  - Not readable by apps. Readable by the log group (which adb's shell user
    has) and a few system domains, and the crash-report copy through dumpsys.
  - Boot parameters that can carry device identifiers are logged by name
    only.
  - dmesg is restricted to root from boot.
  - On release builds init takes all permissions off the event-log device, so
    only processes with the DAC-override capability could still write it.
    Debuggable builds keep it writable.
- **What remains:**
  - At the next boot the kernel part is copied to device-encrypted storage,
    which the system can read before first unlock. Moving it into
    credential-encrypted storage at first unlock and clearing the RAM copy is
    open.
  - The RAM copy stays until overwritten.
  - Qualcomm's minidump driver registers the log areas, so a collected
    minidump would carry them.
  - Reboots and kernel crashes reset cold, which powers the RAM off, so in
    practice nothing survives a reboot. A one-off warm reboot on r9t kept both
    logs (the bootloader does not clear RAM); cold reboots stay the default
    (owner decision 2026-09-27).
  - Every domain holds the platform's write grant on the event-log device, so
    its file mode is the only control.
- **Validation:** debug exposure audit (FP6-060).
- **Status:**
  - Assumption: the region and the release-build rule are built for r9t with
    GrapheneOS's Pixel layout, not yet phone-tested (r9t is a debuggable
    build, so the release rule stays inactive there). No build before r9t
    registered the region.
  - Kernel check (2026-09-27): the kernel places the region at boot without a
    fixed address, and the saved console keeps only notice-level and more
    severe messages (the kernel's console log level), not the full log.
  - Observed on r9t (2026-09-27): the region registers at the same address on
    every boot. After a normal reboot pstore is empty. After a warm reboot
    both logs were there, readable by the shell user by exact name (listing
    the directory is denied), and the system's copy appeared only after the
    first unlock. The boot-parameter value appeared in none of them.

#### AFU unlock, auth-bound keys

- **Who could attack:** an attacker with a lifted or spoofed fingerprint.
- **Where:** the side fingerprint sensor.
- **How we protect it:** strong authentication after reboot and timeouts;
  lockout in the trusted app; the vendor debug service is never registered
  (kept inside the HAL process) and unreachable by policy; the biometric class
  is not claimed above what testing shows.
- **What remains:** the DiamaneOS fingerprint HAL currently declares the
  Class 3 (strong) biometric level before any spoof testing. That is a
  declaration, not a measurement. A release must measure spoof resistance or
  declare a lower class, and the project does not treat the fingerprint as
  strong authentication until spoof testing passes.
- **Validation:** fingerprint bring-up (FP6-045), credential validation
  (FP6-046); spoof testing has no owning task yet.
- **Status:** Observed gap: class declared, not measured. Bring-up (not
  qualified): unlock works; the first enforcing boot showed the module needs
  its debug service registered, which the HAL now answers in-process (r9s,
  not built).

#### AFU data under coercion or seizure

- **Who could attack:** a coercer; seizure followed by extraction.
- **Where:** the lock screen, buttons.
- **How we protect it:** duress credential and wipe (inherited), panic reboot
  to BFU, scheduled reboot.
- **What remains:** duress key destruction relies on TEE key deletion and
  flash erase, not a secure element; eSIM erase depends on the LPA; eSIM
  profiles may survive.
- **Validation:** duress test with synthetic data (FP6-046), eSIM erase
  (FP6-089), panic-to-BFU (FP6-082), scheduled reboot (FP6-085).
- **Status:** Assumption: inherited code present, untested on the FP6; panic
  and scheduled reboot not implemented.

### Physical access to a powered-off device (BFU) and data outside userdata

#### BFU user data

- **Who could attack:** opportunistic physical access to a powered-off phone;
  flash readout (EDL, chip-off).
- **Where:** flash contents, the TEE.
- **How we protect it:** FBE with metadata encryption and hardware-wrapped
  keys; a mandatory 6 to 8 word passphrase from a CSPRNG for the owner and
  every independent user or profile; no weak-credential path; TEE Gatekeeper
  backoff (expected).
- **What remains:** no StrongBox or Weaver exposed, so throttling rests on the
  TEE; backoff timing, and whether it survives reboot and image restore, are
  unverified; the passphrase policy is not implemented yet.
- **Validation:** encryption and hardening validation (FP6-046), passphrase
  onboarding (FP6-070), credential policy (FP6-071).
- **Status:** Observed gap: passphrase not implemented (the development device
  uses a PIN); throttling unverified. Observed (bring-up): FBE v2 with wrapped
  keys and metadata encryption running.

#### Device-persistent data outside userdata

- **Who could attack:** a physical attacker with flash access; an app on a
  permissive build.
- **Where:** the persist partition, modem file systems, the eUICC, hypervisor
  VM storage.
- **How we protect it:** SELinux labels; unsafe stock permissions closed;
  reset residue documented.
- **What remains:** factory reset clears none of these; hardware identifiers
  and calibration are readable from a flash dump; modem and GNSS state
  persists.
- **Validation:** debug exposure audit (FP6-060), eSIM erase (FP6-089).
- **Status:** Observed gap: the residue inventory is not written; some stock
  permissions are still to be tightened.

#### Removable media

- **Who could attack:** anyone who takes the card.
- **Where:** microSD, USB storage.
- **How we protect it:** encrypted microSD (v1).
- **What remains:** not encrypted today.
- **Validation:** encrypted microSD design and workflow (FP6-076, FP6-077).
- **Status:** Assumption.

### Boot chain, firmware and trusted execution

#### OS integrity on a released, locked build

- **Who could attack:** an attacker with write access to partitions.
- **Where:** the boot chain, OTA, recovery, sideload, the inactive A/B slot.
- **How we protect it:** a locked bootloader on a custom AVB root, with vbmeta
  flags 0; SHA-256 hashtrees; rollback indexes set only by signed releases;
  signed full and incremental OTAs from the same pipeline; recovery accepts
  release keys only; relocking locks both lock states.
- **What remains:** yellow boot (never green); downgrade-brick risk; the
  dependency on Fairphone's unlock service; firmware not in OTAs; whether
  slot changes are refused while locked is untested.
- **Validation:** custom-key relock (FP6-050), OTA install and
  interrupted-update tests, signing verifier (FP6-035).
- **Status:** Observed gap (bring-up images): SHA-1 hashtrees, release-style
  rollback indexes and a public test key contradict three listed mitigations.
  Assumption: locked behaviour untested. Observed (bring-up): the AVB chain is
  built and parsed.

#### Firmware security

- **Who could attack:** anyone exploiting an already fixed firmware bug.
- **Where:** XBL, TrustZone, hypervisor, modem, DSP, Wi-Fi and Bluetooth
  firmware; peripheral controller firmware files in `/vendor/firmware`.
- **How we protect it:**
  - A per-image firmware inventory with hashes
    ([`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json)).
  - Firmware delivery in OTAs from verified Fairphone releases (designed in
    [FIRMWARE.md](FIRMWARE.md), not implemented).
  - Separate dated patch levels for platform, kernel, vendor and firmware.
  - Controller firmware files are exact, hash-pinned stock files under
    verified boot once locked.
- **What remains:**
  - The project cannot build or sign firmware; there is no firmware update
    path yet; firmware lags ASB.
  - The Gunyah hypervisor and its trusted VMs are closed.
  - As on stock, the NFC HAL may update its controller from its firmware
    file.
  - From r9t the touch driver writes its file to the touch controller
    whenever the versions differ or it cannot read the controller's version,
    checking only the file's header. Verified boot is what keeps that file
    authentic.
- **Validation:** firmware review and update path (FP6-206), stock input
  verification (FP6-040).
- **Status:** Observed gap: firmware stays at the last flashed stock release.

#### Keys, Gatekeeper throttling, fingerprint templates

- **Who could attack:** a compromised system or HAL process; a malicious app
  reaching TEE clients.
- **Where:** the TEE driver and client services, the TEE listener daemon, the
  fingerprint HAL.
- **How we protect it:** TEE access only for named HAL domains; unused
  userspace TEE proxy services removed; RPMB access limited; a wipe asks the
  TEE to delete all old keys.
- **What remains:** a closed TEE with a public history of key-extraction bugs;
  no StrongBox or Weaver; KeyMint key deletion on wipe (rollback resistance)
  is set from r9s and unverified.
- **Validation:** TEE and credential validation (FP6-046, FP6-050),
  attestation limits (FP6-065, FP6-106), security status reporting
  (FP6-073).
- **Status:** Observed gap (builds before r9s): an unused userspace TEE proxy
  ran; removed from r9s, not yet built. Bring-up (not qualified): services
  run; enforcing and throttling persistence unverified.

### Supply chain, signing and development process

#### Source and build outputs

- **Who could attack:** a compromised upstream host or project, build
  dependency or build host.
- **Where:** source fetch, prebuilt toolchains, the build account.
- **How we protect it:** signed GrapheneOS tags; immutable commit pins; signed
  downstream commits verified before build; compilation with the network
  denied; two independent builds with final-content comparison.
- **What remains:** Qualcomm/CodeLinaro and Fairphone sources and toolchains
  carry no upstream signatures; common inputs are common-mode (a bad shared
  input affects both builds alike).
- **Validation:** reproducible environment, dual build, release comparison.
- **Status:** Observed gap (FP6 path): compiled with the network available, on
  one host, with private scripts; the build identity is not yet separated
  from its own verification inputs; only a few projects are re-verified per
  build. Recorded (generic target): a network-denied build path.

#### Closed vendor inputs

- **Who could attack:** a compromised vendor download or support page; a
  network attacker at first download.
- **Where:** the stock factory package.
- **How we protect it:** the exact archive hash pinned everywhere; per-file
  hash, component and purpose; fail-closed extraction; every library loaded
  by name declared (required; no build check yet).
- **What remains:** the archive and its published hash come from the same
  vendor web estate, and a new package can be selected before Fairphone
  publishes its hash (FP6.QREL.16.111.0 was); no vendor signature check yet;
  the per-file purpose is generic for part of the set; name-loaded libraries
  are declared for few files (a missing one broke a bring-up boot).
- **Validation:** stock input verification (FP6-040), firmware review
  (FP6-206), closure gate (FP6-208).
- **Status:** Observed gap: per-file necessity and name-loaded dependencies
  incomplete. Bring-up (not qualified): extraction is fail-closed and
  hash-bound.

#### Release signing keys

- **Who could attack:** key theft or misuse; a targeted signed build.
- **Where:** the signer host, hardware tokens, signing media, maintainers.
- **How we protect it:** an offline signer, token roles, 3-of-5 recovery, a
  public release log before distribution.
- **What remains:** a single signing authority; the AVB root cannot be revoked
  without unlock and wipe; inclusion in the log does not mean a build is
  benign; no signing role for kernel modules yet.
- **Validation:** signing qualification (FP6-035, FP6-036).
- **Status:** Designed: generic dummy-key qualification only; no FP6 signing
  profile yet.

#### Repositories, domain, install page

- **Who could attack:** phishing; a compromised workstation or assistant
  session; a malicious contribution.
- **Where:** code hosting, registrar, DNS, mail, developer keys.
- **How we protect it:** hardware MFA; separate signing and authentication
  keys; protected branches; human review of security-relevant changes;
  least-privilege assistant access; no untrusted code on release hosts.
- **What remains:** one-person review capacity.
- **Validation:** account and workstation hardening, branch protection.
- **Status:** Assumption: not yet qualified.

#### Exposure window for known bugs

- **Who could attack:** n-day exploitation.
- **Where:** platform, kernel, vendor code, firmware, browser.
- **How we protect it:** monthly releases within 7 days of GrapheneOS; urgent
  fixes within 48 hours; per-layer patch levels shown.
- **What remains:** vendor and firmware fixes depend on Fairphone and Qualcomm
  and lag; open-source libraries bundled inside closed vendor files do not get
  platform fixes.
- **Validation:** update cadence, patch dashboard.
- **Status:** Observed gap: bring-up platform and vendor patch levels lag
  available upstream releases; no automated upstream intake yet.

#### Shell fork rebase lag: timely GrapheneOS security fixes under the Tally shell

- **Who could attack:** n-day exploitation of a fix DiamaneOS has not shipped
  yet, or one lost while rebasing.
- **Where:** DiamaneOS's Tally changes to frameworks/base (the SystemUI shell
  first; WM Shell, SettingsLib and core resources later), carried onto each
  GrapheneOS release.
- **How we protect it:**
  - Tally lands in per-area commit series, mostly new files wired in through
    SystemUI's dependency injection, so upstream files change little.
  - Tally code sits behind one build-time flag (fixed read-only, on in
    DiamaneOS's release config). An area can be dropped, or the flag turned
    off, to ship a GrapheneOS security release on time.
  - The planned release gate requires the shipped fork revision to contain the
    adopted GrapheneOS release's frameworks/base revision.
- **What remains:**
  - The flag helps only while the Tally code still builds on the new release,
    and it switches off a resource only where the resource names it.
    SystemUI, WM Shell (step 5) and the shared clock library read the flag.
  - If a later GrapheneOS release moves WM Shell's activity transitions to its
    new transition planner, Tally's page motion falls back to stock until it
    is hooked in there.
  - Conflicts in keyguard, privacy-indicator or biometric files still need a
    careful manual merge.
  - frameworks/base is not in the tools' fork tracker yet.
  - One-person review capacity.
  - The bouncer reads Tally token resources when it is built, so a build with
    broken tokens would break PIN and password entry. Every build is
    therefore tested by unlocking with a PIN and a password with the flag on.
  - The foundation's colour fixes (SystemUI's "already applied" check and the
    Sodium fallback) apply with the flag off too. They change colours only,
    so the flag-off build is GrapheneOS's in behaviour, not byte for byte.
- **Validation:** rebase and build of each GrapheneOS release with the flag on
  and off (FP6-211); update cadence, patch dashboard.
- **Status:** Assumption: the flag and its release-config value are on local
  branches, not built.

#### Install-time trust

- **Who could attack:** an impersonating site or mirror; a malicious
  installer.
- **Where:** browser download, the WebUSB and CLI installers, the first AVB key
  enrolment.
- **How we protect it:** an out-of-band key fingerprint in 3 independent
  places, plus a comparison with the fingerprint the bootloader displays
  before locking; the `get_unlock_ability=1` gate refuses on 0; downloads are
  verified before flashing; relocking never enrols a public test key.
- **What remains:** the first install has no prior key; a page-controlled
  checkbox is not evidence; a compromised origin can substitute installer and
  key; stock restore inputs are authenticated by the vendor's published hash,
  not a signature.
- **Validation:** recovery preflight, unlock and stock restoration, CLI and
  WebUSB installers, independent verification guidance.
- **Status:** Assumption.

### Everyday use

#### Correct user decisions

- **Who could attack:** user error, a confusing warning, an inaccessible flow.
- **Where:** setup, permissions, updates, backup and restore, recovery.
- **How we protect it:** plain outcomes; progressive disclosure; scoped grants;
  explicit confirmation of destructive actions; a first-boot assistive path
  before setup needs it; fluent review of critical wording, or a disclosed
  fallback to the source language; honest "unsupported" states instead of
  hidden gaps; a control that is shown must work.
- **What remains:** bootloader and firmware screens may stay inaccessible; an
  English fallback alone is no proof of usability.
- **Validation:** interface design, localization and accessibility, passphrase
  onboarding, accessible journey validation.
- **Status:** Assumption.

#### Typed text and personal words

- **Who could attack:** someone with the device before first unlock, a copy of
  a backup, or a view of the screen.
- **Where:** the keyboard's learned, personal-dictionary and contact word
  lists; backups; fields that ask the keyboard not to learn (incognito).
- **How we protect it**, in the DiamaneOS keyboard fork:
  - Learned, personal and contact words only in credential-encrypted storage,
    loaded after unlock.
  - Old device-encrypted copies deleted when the keyboard first sets up its
    dictionaries or at the first unlock (an update never sends
    MY_PACKAGE_REPLACED).
  - Backups off.
  - Fields with the no-learning flag, and password fields, are never learned
    from and never shown learned words.
  - No typed or personal data in logs.
  - Unused network, account and sync permissions and dormant entry points
    removed; no network permission (already true).
- **What remains:** until the fork ships, the inherited keyboard keeps these
  words in storage available before first unlock, allows them in backups and
  ignores the no-learning flag. After it ships, the recent-emoji list stays
  readable before first unlock. On builds 6 and 7 the old copies stayed on the
  update's first boot until the keyboard started or the owner unlocked (-150,
  fixed for build 8 by deleting them at LOCKED_BOOT_COMPLETED).
- **Validation:** keyboard fork tests and phone checks before merge (no owning
  task yet).
- **Status:** Designed. The fix is on a local keyboard branch in test builds 6
  and 7; its phone check was inconclusive (learned words are off by default,
  and password fields never compose).

#### Privacy indicators and disclosures under the Tally shell

Protects: knowing when the camera, microphone or location is in use or the
screen is captured, which app is asking, whether the device is managed or on a
VPN, and what a locked phone shows.

- **Who could attack:** an app using a sensor or capturing the screen while the
  user reads its own interface or while it runs fullscreen; an app asking for
  a biometric or credential; someone looking at a locked phone.
- **Where:** the status bar privacy chip and lamps, capture chips, the
  fullscreen dots and the lens ring, the lock strip and indication line,
  Quick Settings' security footer, notification rows, toasts and the
  biometric prompt, all restyled by the Tally shell (plan FP6-211).
- **How we protect it:**
  - *Data and presence.*
    - Tally changes how indicators and disclosures are drawn, never their data
      or presence. The platform's privacy and app-op tracking stays the only
      source, with no filtering by foreground app, quiet or decluttered views
      or the lock strip.
    - Camera, microphone, location (whenever the platform shows it) and screen
      sharing show in every state: app in front, immersive and fullscreen,
      lock screen, shade, Do Not Disturb, battery saver.
    - Stock SystemUI keeps the indicator timings (the 5 s and 10 s holds).
    - A lamp appears with no animation delay; only its disappearance may
      animate. The privacy dot's colour follows the area under it without ever
      delaying the dot.
    - Tally lock views draw an empty or partial state without failing, since a
      SystemUI crash removes every privacy indicator until it restarts.
  - *Drawing.*
    - Tally changes only how the stock indicators are drawn: colours with a
      2 dp edge, padding, radius, a 16 dp dot drawn as a lamp 16 dp from the
      top and side (the status icons keep clear of it), one chip for every
      sensor in the shade, screen capture alone in the capture colour, and
      disappearance on the fill spring. It adds the lens ring and the location
      lamp by the lens. Its tokens and overlays never set the stock indicator
      resources.
    - Stock SystemUI keeps the chip height and icon size in sp, so chips grow
      with the text size.
    - Indicator colours are fixed SystemUI resources outside the palette. They
      keep at least 3:1 contrast against both a light and a dark bar, taking
      the variant from the area under them: the status icons' tint over the
      app, the lock screen or the shade; dark under the bouncer, a SystemUI
      dialog or the dozing screen, and wherever the area is not known to be
      light; never from night mode.
    - Every Tally status bar indicator draws its 2 dp edge in every state.
      Where no edge can be drawn or no area is known, it takes the variant
      that keeps 3:1 on its own.
    - The overlay check refuses product overlays of SystemUI privacy-indicator
      resources and of every Tally token or indicator resource in SystemUI
      (only a device overlay sets the lens config).
    - The Tally prototype follows the same indicator rules, checked in every
      configuration (stock's sp sizes at 100, 150 and 200 % text; colours from
      the area under the indicator with a 2 dp edge, so dims over the status
      bar keep 3:1).
  - *Taps and touch.* As in stock, tapping the privacy chip in the shade opens
    the stock privacy dialog, and tapping a capture chip opens its stock stop
    dialog; the status bar privacy chip and the dot take no tap of their own.
    The lens ring and the location lamp by the lens take no touch at all, so
    they never block the status bar or the app under them.
  - *Lens ring and lamp by the lens.*
    - They are drawn on active pixels at least 2 dp outside the display
      cutout, above apps and the lock screen.
    - The lens ring lights while an app uses a camera once the front camera
      has opened, as the camera service reports it. It counts the front camera
      as used for the longest privacy hold (10 s) after it closes, so it keeps
      the camera privacy item's hold whatever order the close and the privacy
      item arrive in. A switch to the back camera ends it after that window.
      A camera whose facing cannot be read counts as the front one, and
      without the open and close reports any camera lights it. The chip and
      the dot show every camera.
    - On the FP6 the device overlay declares the camera hole as measured on
      the panel (a circle of 37 px), so the lens ring is a true ring 2 dp
      outside it and the lamp by the lens sits level with it. The layout
      rectangle keeps the status bar and app insets at 110 px and takes the
      hole's full width, so no status bar content sits under it.
  - *Lock strip.*
    - While locked, the lock strip says only "Camera in use" or "Microphone in
      use". Stock's lock screen shows only the untyped privacy dot, so naming
      the sensor is a deliberate difference.
    - It takes its sensor items from the privacy chip's own source (holds
      included) and its other items from SystemUI's existing controllers. It
      takes no touch and shows no app, network or device names.
    - It shows an alarm only when the alarm is still to come and rings within
      12 hours (GrapheneOS's condition; the stock lock screen's 12 hours), and
      only while the current user's lock screen shows notifications, which is
      off unless set (GrapheneOS's smartspace line likewise shows nothing with
      them off).
    - It shows Bluetooth only while a device is connected, as stock's status
      bar icon does, and the battery percentage only while charging or with
      the user's setting on.
    - It is hidden on the always-on display.
  - *Always-on strip.* Only while the phone dozes, a Tally always-on strip
    shows what stock's always-on date line showed: the next alarm within 12
    hours, a Do Not Disturb icon, and the playing media's title, with its
    artist only beside the media notification's icon. Media shows only while
    the current user's lock screen shows notifications and "Show media on
    lock screen" is on, with GrapheneOS's defaults, as GrapheneOS's always-on
    line shows them. It never shows lamps, sensors, network or device names,
    app icons or the battery, and shows nothing at all while lock-screen
    notifications are off.
  - *Clock and indication line.*
    - The Tally lock clock, and the date line under other clocks, show only
      the time and date. The date line under other clocks shows only while
      the lock screen shows notifications (as GrapheneOS's smartspace line).
    - The indication line keeps stock's messages, including its
      strong-authentication, lockdown, administrator and trust-agent messages,
      and shows fingerprint failure and help messages in the error colour.
    - It adds a 3-second side-sensor hint in stock's words, only while
      fingerprint unlock is allowed, on at most five wakes per user. The count
      (0 to 5) is kept only in SystemUI's own device-protected storage, not
      backed up and removed with the user. It is read at screen-on, also
      before the first unlock, and written only when the hint shows, which
      needs fingerprint unlock to be allowed. It never affects unlocking, and
      if it cannot be read the hint stays away.
  - *Footer, notifications, prompts and toasts.*
    - The security footer (managed device, VPN, monitoring certificate) and
      the VPN icon show whenever stock shows them.
    - Lock-screen notifications follow the user's lock-screen settings and
      work-profile redaction.
    - Notification rows keep the app's label and icon; the biometric prompt
      keeps the app's name and icon.
    - Toasts change position and shape only. They keep their admission rules,
      app attribution and GrapheneOS's secure-paste notices, and no new kind
      of toast appears on the secure lock screen.
- **What remains:**
  - A new layout could still cover or clip an indicator without touching its
    data; only review and phone tests catch that.
  - A preinstalled overlay outside the checked roots could still change the
    stock resources.
  - SystemUI cannot see a scrim an app draws inside its own window (as opposed
    to a dimming window). Over the mid-tone it leaves, an indicator in its
    light variant can fall to about 2.3:1 with its white edge.
  - The status bar chip and the capture chips sit in the status bar window,
    under the shade, the lock screen and SystemUI dialogs. The dot, the lens
    ring and the lamp by the lens stay above everything.
- **Validation:** shell security review at roadmap steps 3.1 and 3.3
  (FP6-211); prototype privacy checks at 100, 150 and 200 % text; a phone test
  of every listed state before merge.
- **Status:** Assumption: the SystemUI indicators of roadmap step 3.1 and the
  lock screen of step 3.3 are on the local tally-status and tally-lock
  branches, compiled only against stubs of the pinned signatures, with their
  resources linked by aapt2; not built, not run on the phone.

#### Files archive handling (browse, extract, create)

- **Who could attack:** a malicious archive the user opens or extracts, or an
  app holding MANAGE_DOCUMENTS.
- **Where:** ArchivesProvider (exported, MANAGE_DOCUMENTS) parsing untrusted
  ZIP, 7z and TAR with AOSP commons-compress 1.19; DocumentsUI extraction
  (UnpackJob).
- **How we protect it:**
  - Entry paths are rooted, and `.` and `..` collapsed
    (Archive.getEntryPath). Extraction goes through the storage framework,
    which re-roots and sanitises each name.
  - Symlink and hardlink entries are written as ordinary files, never
    followed.
  - Extraction checks free space against the declared sizes, caps each file at
    its declared size and removes partial files.
  - Parser exceptions end in a failed load, never a crash.
  - The new archive code (zip_ng) adds read-path size and CRC checks, so it is
    turned on (-142).
- **What remains:** commons-compress 1.19 is old (a debug log line on a ZIP
  0x0017 extra field, -143; a malformed TAR header throws a caught
  NullPointerException); browsing parses archives whether or not zip_ng is
  on; a large honest archive that fits the free space still costs time to
  extract (cancellable); no recursive extraction.
- **Validation:** host review and fuzzing on 2026-09-30 (20 million path
  inputs and about 154 million mutated archives: no crash, hang or escape);
  phone extraction and picker tests with the next build.
- **Status:** Designed and fuzzed on the host with the pinned library. zip_ng
  is on in vendor_diamaneos local `tally` (merge 770d976 of `files-flags`),
  in test builds 6 and 7. Files trash stays off until its wording (-140) and
  phone tests.

## FP6 source and firmware boundary

**Where the code comes from**

- Kernel and platform HAL sources follow Qualcomm's CodeLinaro release
  `LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS
  `kernel_common-6.1` release merged into the vendor kernel.
- The FP6 device trees, the Samsung NFC sources and the stock images still
  come from Fairphone.
- Having the source does not prove that GrapheneOS or Pixel patches apply,
  that modules meet the selected KMI/UAPI, or that the binaries reproduce
  stock.

**Closed components**

- The camera, radio and IMS, secure-world, sensor, DRM and much of the
  graphics and media runtime still depend on proprietary userspace or
  firmware.
- The current bring-up selection holds 720 closed stock files (about 369 MB).
  By purpose: camera 213, radio and IMS 150, sensors 84, display and GPU 82,
  audio 41, credentials 36, power and thermal 29, remote-processor services
  28, plus smaller Bluetooth, NFC, GNSS and fingerprint sets.
- Each closed layer is to be replaced by a source build where one exists.

**Stock input and vendor generation**

- The selected EU stock input is `FP6.QREL.16.111.0` (`FP6.QREL.16.100.0`
  until 2026-09-30). Its verified factory package is the authoritative
  extraction input.
- Vendor generation uses an explicit per-file recipe with partition, path,
  hash, component and purpose. Purposes are still generic for part of the
  set, and consumer bindings are partial.
- The generator excludes per-device identity, modem NV/EFS, calibration,
  provisioning, DRM, attestation, keystore and userdata material.
- A candidate removal must remove the complete reachable service and
  declaration path and pass subsystem tests.
- A candidate open-source replacement needs exact licence compliance and must
  preserve security and capability. A software fallback is not automatically
  safer than proprietary hardware-backed code.
- Firmware images are hashed per image in
  [`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json),
  but DiamaneOS does not deliver them yet.

**Licences and records**

- Open-source code the project imports or modifies stays fail-closed on
  per-file licence, notice, attribution and corresponding-source obligations.
- Exact source revisions, interfaces and experiments are in
  [`config/fp6-sources.json`](../config/fp6-sources.json) and
  [`config/fp6-capabilities.json`](../config/fp6-capabilities.json).

**Regions**

- The intended US path needs a real US-region FP6 operated by a second
  maintainer; its availability has not been evidenced.
- `FP6.QREL.16.104.0` is a comparison and validation input, not an EU restore
  input.
- A shared release is allowed only after the EU/US difference in partitions,
  AVB, firmware, VINTF, init/policy and carrier configuration is recorded and
  the US device passes candidate hardware and telephony tests. Until then, EU
  is the supported target and US is unverified.

**Vendor compatibility**

- The stock vendor declares VINTF target level 8 with vendor API 34. Bring-up
  builds combine it with the Android 17 framework.
- Product assembly must still pass `checkvintf` and applicable VTS checks
  without weakened enforcement or broad compatibility shims.

## Fresh-UI boundary

This section limits what interface work, including the Tally shell, may
change:

- Shared visual tokens and components carry no platform authority.
  Credential, update, backup, network and content-processing authorities stay
  separate.
- Each nontrivial Settings, launcher or SystemUI change needs a demonstrated
  benefit, an owner, a measured rebase cost and regression checks.
- On 2026-09-26 the project owner approved a substantial shell rework: lock
  screen, shade and Quick Settings, Home, Recents and app transitions, through
  Launcher3, SystemUI and WM Shell. Framework authorities are not replaced,
  and no shared visual controller gains platform authority.

Every Tally shell commit (roadmap step 3, FP6-211) also keeps the rules below.
The privacy-indicator and disclosure rules are under
[Privacy indicators and disclosures under the Tally shell](#privacy-indicators-and-disclosures-under-the-tally-shell),
and the rebase rule under
[Shell fork rebase lag](#shell-fork-rebase-lag-timely-grapheneos-security-fixes-under-the-tally-shell).

### Lock screen and biometric prompt

- The bouncer's window, state machine and input handling, and the system
  keyboard, stay as upstream.
- Its look changes only through resources and visual-only code behind the
  Tally flag, each such change with two reviewers. That code only draws: the
  keys' shape and corner radius, the typeface of the digits and messages, and
  how the password field and buttons look, including the field's fade while
  entry is off.
- Input, the system keyboard, the key order and GrapheneOS's PIN scrambling,
  lockout, throttling, the duress and wipe counters and screenshot protection
  stay GrapheneOS's. The bouncer stays protected from screenshots and screen
  recording.
- No custom keyboard and no show-passphrase key without an owner decision and
  a review.
- The biometric prompt keeps the credential fallback, confirm-required,
  cancel on back and on an outside tap, and its protection against obscured
  touches.
- The power menu keeps Lockdown and its lock-screen filtering.
- The lock-screen camera key opens only the secure camera.
- Commits to keyguard, bouncer and biometric code get two reviewers and the
  keyguard and biometric CTS sets on the FP6.

### Transitions

Tested at animation scales 1x and 0.5x:

- While the phone is locked or an app shows over the lock screen, back and
  close lead only to the lock screen, never to Home or an app snapshot
  (tested from the secure camera and a ringing alarm).
- The shell's input consumer takes the catch gesture, so touches never reach
  an animating app.
- Permission-grant, restricted-setting, package-installer, USB, biometric and
  bouncer windows keep stock or shorter enter animations.

### Quick Settings and the shade

- Tally tiles, including the first pull's keycaps, reuse the upstream click,
  secondary-click and long-click handlers, so a tile that needs an unlock
  still asks for one.
- The security footer shows under upstream's conditions, with its VPN or
  management icon.
- The shade header's privacy container keeps the platform's privacy data and
  its tap to the stock privacy dialog.
- Notification cards change drawing only. Which notifications show,
  private-content redaction and work-profile redaction are untouched, and the
  live lamp is never drawn on a redacted notification.
- The media card no longer shows album art (less on the lock screen, not
  more).
- Heads-up notifications change only their motion, edge and shadow. What a
  heads-up shows, when and for how long, its gestures, full-screen intents and
  lock-screen redaction stay stock. A heads-up with a status bar chip keeps
  stock's reveal so it never covers the chip, and a leaving heads-up is fully
  off screen before it is removed.
- The camera and microphone access tiles light their lamps at once, like
  sensor lamps, but keep the tile colours: sensor green stays reserved for
  indicators of a sensor in use.
- Side effects are visual only: Quick Settings dialogs, including the privacy
  dialog, get a 20 dp corner radius.

### Recents' Stop (steps 3.6 and 4b)

SystemUI side (step 3.6):

- Launcher3 gets no new permission.
- SystemUI tells only the current user's recents app, checked by its uid,
  which apps the Active apps dialog would let the user stop, as package and
  user for the current user and its profiles, kept in memory only.
- Every Stop re-runs the dialog's checks at that moment: the lock screen is
  dismissed, a foreground service is running, the user is in the current
  profiles, and the platform gives the app no exemption that hides the
  dialog's Stop button (the system uid, the system allow-list, device and
  profile owners, protected and device-admin packages, persistent processes,
  the default dialer, system modules; the platform's own list of stoppable
  system apps is the exception). Only then does SystemUI ask the platform to
  stop the app, as the dialog does.
- The two new SystemUI-proxy methods accept calls only from the recents app's
  uid in the current user, and do nothing with the Tally flag off.

Limits:

- SystemUI cannot tell whether the user tapped Stop, or on which app, and
  trusts the recents app for that.
- The recents app can already stop apps in its own user with its own
  permission. Through SystemUI it can also stop apps in the current user's
  work profile or private space that the dialog would stop, and it learns
  which of them run a foreground service.
- An app with two exemption reasons (for example a carrier-privileged device
  admin) can get a Stop button, in the dialog and in Recents alike.
- Recents' Stop sits in the launcher's window, where app overlays can cover
  it.

Launcher3 side (step 4b):

- Launcher3 sets SystemUI's listener only while Recents is open and removes it
  on close, and keeps the list in memory only.
- It offers Stop only for a card the user swiped away, named by that card's
  own task (package and user), and never stops an app itself.
- Launcher3's manifests, permissions and privileged-app allow-list stay as
  upstream.
- The Stop key and its row refuse touches that come through another window
  over them, and ignore a tap within 0.5 s of the row appearing or changing.

Limits:

- A window over another part of the launcher (picture-in-picture, a chat
  bubble) does not block a tap on Stop, since refusing such touches would
  break Stop whenever one shows.
- The listener binder is not caller-checked, as with Launcher's other
  SystemUI listeners. A forged list only decides which swiped apps get a row,
  and SystemUI re-checks every Stop.

### Settings switches

- Switches are restyled only through their layouts and drawables, never their
  logic (protection against obscured touches, administrator- and
  restricted-setting-disabled states), and those states are tested.
- SettingsLib's restyle changes its theme resources and adds Tally switch
  views: subclasses of the switch classes the preferences already bind, and a
  Compose twin for its Compose pages. They change only drawing and motion;
  checking, touch handling, accessibility and the enabled state stay the
  parent classes'.
- Disabled switches draw at 38 % in their real position.
- The SettingsLib restyle is not behind the SystemUI Tally flag. It reaches
  every app built on SettingsLib, including the emergency alerts app, whose
  switch logic is untouched (presidential alerts stay on).
- SystemUI's own switch screens take the Tally switch only through flagged
  layout twins and, on its Compose screens, through calls behind the Tally
  flag with the same arguments and accessibility semantics as Material's
  switch. With the flag off, SystemUI is unchanged.
- Step 6b lets a Tally switch wait for the system. The thumb moves at the tap,
  and the lamp lights only while the switch is checked and the setting's own
  state reports on (Wi-Fi, Bluetooth, hotspot and tethering, NFC, Battery
  Saver, from the state their controllers already read), so a lamp never
  shows on for something that is off. Turning off darkens it at once, and a
  failed change goes back unlit, as stock. Accessibility still reports the
  checked state, as stock.
- Switch rows lose their own ripple and focus highlight (the switch shows
  both).
- Restricted switches keep their code, including the window-wide filtering of
  obscured touches.
- SettingsLib's rows and cards change only through layouts and drawables.

### Home and All apps (step 4a)

- Keycap LEDs and the tallies row come only from Launcher's existing
  notification listener (the one behind notification dots), kept in memory.
  They use flags, category, channel, importance, the shade's own visibility
  rules, and the progress bar and chronometer the system draws; never titles
  or texts.
- An LED lights only for a live or a failed notification, and respects the
  app's dot setting. The Notification dots switch turns both off.
  - Live: a running notification (a foreground service, a Live Update or
    ongoing) that shows a live readout or an activity in progress: the system
    chronometer, a progress bar, a call, navigation, a stopwatch, a Live
    Update or playing media. Permanent background services give nothing.
  - Failed: an error notification.
- Nothing shows that the shade hides (a suspended app, Do Not Disturb's list
  suppression, with SystemUI's own exemptions).
- The OS's own notices (the android and SystemUI packages, which only
  system-uid or SystemUI code can post as) give no tally or LED.
- One narrow attribution exception: the preinstalled system Clock
  (com.android.deskclock with FLAG_SYSTEM or FLAG_UPDATED_SYSTEM_APP, checked
  on its ApplicationInfo, never the package name alone) shows "Timer" or
  "Stopwatch" instead of its name while a count-down or count-up time shows,
  decided from structure only. Screen readers still name the app, and every
  other app keeps its own name, so no app can pose as the system timer.
- The row shows app names on the unlocked Home only. A work app shows its name
  and lamp but no readout, since a separately locked work profile is redacted
  in the shade and Launcher cannot see that lock without a new permission.
  The private space never appears there.
- Sensors in use, screen capture and requested or on states are left out:
  Launcher would need new privileges to see them.
- The All apps letter rail takes its sections from stock's list, so a hidden
  private space gets no slot.
- No permission, manifest or allow-list change.

### Settings search (step 6c)

- SettingsIntelligence's panel is restyled, and its target SDK goes from 31 to
  37 with predictive back.
- Every platform behaviour change in between was checked against the app: 173
  checked, 22 apply, none changes what the app can do. The explicit-intent
  filter checks are either not tied to the caller's target or disabled at the
  pin.
- No permission or component change. The search index, its providers and
  their permissions are untouched, and results show what they showed.

### Clock (step 7)

The DeskClock fork: 182 reviewed LineageOS lineage-24.0 commits and 19 of
DiamaneOS's own, targetSdk 37.

- The exported surface goes from 7 components to 4: the launcher entry, the
  SET_ALARM-guarded API activities and the screensaver. The alarm init
  receiver and both widget providers are no longer exported.
- The new widget set-up activity is not exported and accepts only this app's
  digital-clock widget ids. Widget intents are explicit, and every runtime
  receiver is RECEIVER_NOT_EXPORTED.
- GrapheneOS's e16191c4 (no foreign snooze or dismiss) and 9cde4084 stay.
- Permissions are the approved set: notifications, promoted notifications,
  USE_EXACT_ALARM and one foreground-service type. DISABLE_KEYGUARD,
  READ_EXTERNAL_STORAGE and the legacy ones are dropped; POWER_OFF_ALARM and
  MODIFY_AUDIO_SETTINGS never come in.
- An expired timer no longer asks to dismiss the keyguard.
- Backup and device transfer stay off. Alarms rely on GrapheneOS's power-save
  allowlist, as before.
- Timer and stopwatch are promoted MetricStyle notifications (status bar
  chips). Being public, they show on the always-on display under the
  lock-screen notification filter; see -144 for chips over an occluded lock
  screen.

Residual: the imported series was reviewed on its security paths (manifest,
receivers, services, the public intent API, widgets, notifications), not every
UI line. Four imported commits are marked as tool-assisted (notification
styling, a channel ID, the promoted-notification permission and icons; no
security effect).

The step 8 review found two issues, both fixed for build 8:

- -148 (medium if shipped; in test builds 6 and 7): the fork treats audio
  modes that any app with the normal MODIFY_AUDIO_SETTINGS permission can set
  as a phone call, so another app could stop a ringing alarm or turn alarms
  and timers down to the in-call tone. Fix: only audio modes that need
  MODIFY_PHONE_STATE count as a call, so a call that only rings no longer
  silences an alarm.
- -149: the alarms database moved from version 8 to 12, which GrapheneOS's
  Clock cannot open again, so going back would silently stop alarms. Fix: the
  database stays at version 8, and earlier builds' databases go back to it
  with their alarms.

### Calculator (step 7)

The ExactCalculator fork changes styling (DayNight with dynamic colour, Tally
keycaps from the token library, resources only), adds the AC and "( )" keys,
goes edge-to-edge and has key labels that grow with the text size. The
manifest is unchanged: no permission, component, backup (allowBackup stays
false) or storage change. The keycap drawable is inflated by class name only
from the app's own resources.

### Recovery and fastbootd titles (step 7)

The bootable_recovery fork (option A) changes exactly two title literals
("DiamaneOS Recovery", "DiamaneOS Fastboot"); the compiled code is otherwise
identical to the pin. Recovery runs as root and wipes, sideloads and verifies
packages, so every GrapheneOS rebase of this fork is reviewed as a diff that
must stay those two literals. Signature checks against otacerts and
GrapheneOS's recovery restrictions (no SD-card entry, serialno-constrained
updates rejected, no serial number shown) are unchanged.

### Motion: WM Shell (step 5)

Behind the Tally flag:

- Pages opening and closing inside and between apps, predictive Back between
  activities and tasks, and the cold-start splash change how they move, never
  what opens or closes.
- Tally's page motion applies only where stock would play the framework's
  default animation for every moving window of a full-screen transition.
- Stock keeps: every transition with a keyguard or always-on-display flag
  (locked, occluding, unoccluding, appearing, going away); Recents, dreams,
  wallpaper and Home transitions (Launcher's); translucent windows (every
  permission, install and USB dialog); multi-window, freeform, letterboxed and
  embedded activities; apps' own transitions; and the consent and credential
  screens named in the code (the package installer, VPN and credential
  dialogs, all of SystemUI's activities, the permission grant, restricted
  settings, permission review and role request, device-admin activation and
  credential confirmation).
- Tally's motion is shorter than stock's: open about 310 ms, close about
  190 ms, against 450 ms.
- The splash shows the app's keycap and a static lamp, and goes only once the
  app has drawn, never leaving a blank window.
- The page transitions are measured as jank-monitor interactions. These write
  an event-log line with the interaction type, times and a constant tag, with
  no app identity, as stock's other interactions do.

### Motion: Launcher (step 5)

- Launching from a key, the return to Home and Back to Home change how the
  window moves (it grows out of its key and flies back into it on the Tally
  springs, Home dims and the key's neighbours part). They never change what
  opens, closes or goes Home, or when: the gesture's end target and the Back
  trigger stay GrapheneOS's.
- The Tally paths apply only to an upright phone with gesture navigation and
  one full-screen task. Split screen, desktop windows, a trackpad,
  picture-in-picture and landscape keep stock.
- The recents input consumer, the keyguard paths and Launcher's start paths
  (quiet-mode profiles, the private space, disabled apps) are unchanged.
- A tally now opens its app through Launcher's own start path with the app's
  launcher intent (never the notification's), so it is logged on the device
  like any Home launch (Launcher's standard launch event). No new log or
  permission.

### Settings homepage (step 6a)

- Regrouped and restyled only. Every top-level entry keeps its page,
  controller, conditions and restrictions (the pin's 30 entries, compared by
  key and attribute), including GrapheneOS's own, work profile, private space
  and every security and privacy entry.
- State lamps show only what the entry's controller already reads (location
  on, a mode active), beside the summary that says it in words and never
  beside an administrator's restriction text, with no new read or listener.
- The bottom search opens the same Settings search with stock's conditions
  (hidden before setup, in setup flows and where search is off for the user).
- About phone gains a static mark and a "based on GrapheneOS" line.
- The homepage keeps stock's hiding of non-system overlays, and nothing new is
  logged or stored.

### Branding

The system's own label is the literal "DiamaneOS" (apps can read it; it
reveals only the OS name, as the GrapheneOS label did). The fallback boot
logo, shown only when no boot animation is installed, is the DiamaneOS mark.
Neither carries any state.

### Rules learned from findings

- Tally code must read SystemUI and token resources only through SystemUI's
  own contexts, never a view inflated from another package (from -135).
- Tally code in Launcher must not open credential-encrypted storage on paths
  that run before the first unlock (from -139).
- Every value Home reads from notification extras is app-controlled and must
  be read without a throwing path (from -145).

## Decision record

- **Scope.** Daily use by privacy- and security-focused users; v1 scope as
  decided on 2026-09-26 (ten original entries moved after public v1; see
  [v1 scope](#v1-scope-standalone-summary)).
- **Passphrase.** Passphrase-first is mandatory now; an optional PIN comes only
  through a later explicit decision.
- **Claims and identity.** No scores or unsupported parity claims. Keep
  upstream attribution and a clear, separate project identity.
- **2G (2026-09-25).** 2G stays allowed by default (the AOSP and GrapheneOS
  default). "2G network protection" and LTE-only are offered as opt-in
  hardening.
- **Cellular security alerts (2026-09-25).** The plan's v1 item allows an
  honest "unsupported" state; on the current radio software that is the
  outcome. The radio software limitation is recorded as known.
- **eSIM.**
  - 2026-09-25: eSIM uses a maintained hardware-layer LPA with least privilege
    and hardening; OpenEUICC is not used. Candidate: the Qualcomm LPA.
  - 2026-09-26: reasons in GrapheneOS os-issue-tracker #6275 and #2631; from
    r9s the LPA's eSIM service is on by default.
  - 2026-09-27: the stock LPA cannot list profiles on the FP6, so its service
    is off again from r9t (stock leaves it on). eSIM follows GrapheneOS
    (installed profiles keep working), and our own source LPA behind an
    off-by-default "eSIM support" switch is planned for its own build.
- **Build identity (2026-09-26).** The build properties and fingerprint keep
  Fairphone's stock product identity, as GrapheneOS keeps Google's, after
  Google blocked the DiamaneOS-branded identity as an uncertified device.
  DiamaneOS stays the name people see.
- **Enforcing policy for r9s (2026-09-26).** The audio HAL may use QRTR sockets
  for DSP restart notifications (SELinux cannot limit which QMI service it
  reaches); nicmd may bind the ephemeral ports the modem reserves; the modem's
  study partition keeps its stock label; SystemUI cannot read the
  screen-decorations switch, so the camera/microphone privacy dot cannot be
  turned off over adb; the camera HAL's display-configuration hint stays
  denied.
- **userfaultfd and KPROBES.**
  - 2026-09-26: the production kernel keeps CONFIG_USERFAULTFD for ART's
    garbage collector, as GKI, Pixel and GrapheneOS do, with the
    user-mode-only restriction (see
    [Kernel integrity and all data](#kernel-integrity-and-all-data)), and
    turns KPROBES off (added back only if something needs it).
  - 2026-09-27: something needs it. The USB controller glue implements its
    controller hooks with kretprobes, so KPROBES stays on until those hooks
    are explicit calls; lockdown blocks probes from user space. The owner
    accepted this.
- **debugfs (2026-09-27).** debugfs stays as on r9s: built in and mountable.
  User builds never mount it; debuggable builds mount it until boot
  completes; SELinux governs access. In this kernel, turning mounts off also
  removes the in-kernel interface the display driver and recovery need. A
  kernel change that keeps the interface but refuses mounts is the stricter
  option and open.
- **pstore/ramoops and reboots (2026-09-27).** pstore/ramoops is on for
  bring-up builds with GrapheneOS's Pixel layout, keeping the previous boot's
  kernel log and event logs in RAM across a soft reboot. Boot parameters
  handed to user space are logged by name only, and release builds make the
  event-log device unwritable (a device init rule from r9t). Reboots stay cold
  (the kernel default), so the saved logs do not survive a reboot or a kernel
  crash in practice. A warm reboot would keep them but also keep all of RAM;
  not switching, since few kernel changes are still expected. Details and the
  open items are under
  [Previous boot's kernel log and event logs](#previous-boots-kernel-log-and-event-logs).
- **VoLTE and IMS.**
  - 2026-09-25: VoLTE is required. The closed Qualcomm IMS stack is used with
    an explicit permission allowlist.
  - 2026-09-26: the IMS data connection is to be brought up by DiamaneOS's own
    code (a small modem-facing service and a one-permission app) instead of
    Qualcomm's connectivity engine; closed code there is a fallback only.
  - Replacing the IMS stack itself with source is plan direction, not a dated
    decision.
- **Call audio (2026-09-26).** The call-audio messenger keeps the one
  audio-routing permission it needs; replacing it with our own app is an open
  item. Then (also 2026-09-26): replaced from r9s by DiamaneOS's own
  call-audio bridge with only the normal audio-settings permission; the
  audio-routing grant is gone.

Requirements added by the 2026-09-25 review (not separate decisions): a
cellular hardening control is offered only once it is verified to reach the
modem, and one that is shown must not fail silently; the candidate LPA must
meet the production TLS trust rule before it ships.

## Architecture and verification review

These are intended protections. Device-dependent claims stay unverified until
their named validation produces evidence.

The review traced these attack paths:

- **Remote path:** network observer, DNS and captive portal, EU endpoint
  policy. The EU endpoint policy is not implemented in any build yet (see
  [Service metadata and OS identity](#service-metadata-and-os-identity)).
- **Remote media path:** remote media, the hardware codec service, video
  device nodes. The codec must not share device-node access with the camera
  stack or reach display configuration, and its seccomp sandbox must be shown
  to install (see [App data from remote exploit](#app-data-from-remote-exploit)).
- **Malicious-app path:** permission and background listener, presets plus
  per-app routing and filtering. Also: the camera permission, the camera
  service and the closed camera provider (grants bound to its domain, fewer
  groups, no network once SELinux is enforcing). The flashlight controls reach
  the closed provider without the camera permission, with simple on/off and
  strength values only (AOSP design).
- **Cellular path:** fake base station, 2G fallback, the user's hardening
  choice, the modem. Reviewed on a bring-up build: the hardening choice did
  not reach the modem, and the interface did not show the failure.
- **Cross-profile and physical path:** secondary user or profile, user-switch
  challenge, real service-boundary enforcement (FP6-046, FP6-071); and AFU and
  BFU, USB, EDL and the lock screen, reboot to BFU and duress. The gaps are
  listed under [Secondary-profile data](#secondary-profile-data) and
  [AFU user data](#afu-user-data).

## Next validation

1. Enforcing SELinux runs per subsystem (camera, audio, telephony, sensors,
   Bluetooth, NFC, GNSS, fingerprint, media), with denials recorded by domain.
2. User-build gate: `user` variant, not debuggable, ADB authenticated and off,
   no pre-trusted ADB keys, no ADB over the network, enforcing, no debug USB
   functions, trace sinks or debug modules, release keys only, no inherited
   GrapheneOS endpoint defaults, test apps absent.
3. Cellular: the selected network mode reaches the modem on every
   subscription, including subscriptions with stored settings, and a failure
   is surfaced; null-cipher support state from the radio daemon;
   airplane-mode modem state.
4. USB port-control matrix: locked and unlocked, host and gadget roles,
   charging kept; the built kernel exposes deny-new-USB.
5. Firmware-download and dump reboots refused on a user build; a forced panic
   produces no download device; minidump retention checked.
6. Relock with a project dummy key (both lock states), rollback-index policy,
   SHA-256 hashtrees, inactive-slot downgrade check.
7. Gatekeeper backoff and its persistence across reboot and image restore;
   KeyMint key deletion; duress on synthetic data; eSIM erase.
8. NFC routing tests, the Bluetooth address path, Wi-Fi MAC randomization, a
   GNSS fix with SUPL settings, runtime network egress capture with SELinux
   enforcing.
9. Firmware inventory with per-partition hashes and a firmware update path;
   stock AVB key verification.
10. FP6 signing profile and presigned-app inventory; a second independent FP6
    build; binding of generated inputs to the public environment;
    network-denied FP6 compilation.
11. TEE KeyMint, attestation and reported security levels on the EU
    candidate; fingerprint spoof testing and the declared biometric class; a
    regional matrix on an actual US candidate before claiming US support.
12. Hardware media: codec seccomp installs (no fail-open), codec nodes
    separated from camera nodes, no display-configuration access.
13. Kernel: module list reduced to product use; lockdown kept in user builds.
14. Name-loaded library check for every selected closed file; specific
    per-file purposes.

## IMS integration notes

These notes record how the IMS containment evolved. The current state is in
[IMS status](#ims-status-2026-09-28).

### Candidate update (2026-09-27)

- The source DCM broker and the stock Qualcomm IWLAN and certificate frontend
  are prepared on an integration branch.
- The certificate helper adds a modem-facing QRTR client. It and IWLAN share
  their own application UID, keep the stock signer and receive no privileged
  Android permission grants.
- QRTR has no per-QMI-service isolation, so a compromised allowed client
  stays a modem trust risk. A device-owned domain inventory prevents silent
  additions.
- The two stock packages need scoped hidden-API exceptions for platform IPC.
  This does not grant Android signature permissions or bypass their SELinux
  domain.
- Carrier configuration is extracted as data for a source-built service,
  including a pinned repair of malformed stock no-SIM XML. The stock carrier
  APK is not installed.
- These are source-level mitigations awaiting native policy, package and
  carrier checks; see [carrier integration](CARRIER-INTEGRATION.md).
- AML stays deferred and unselected.
- Android already includes a privileged IMS entitlement client through its
  telephony product. The candidate replaces it with a source fork that
  removes Google push libraries, bounds carrier responses, restricts exported
  entry points and keeps provisioning state on failed queries. The inspected
  stock profiles and the current configuration do not enable that flow;
  activation stays carrier-driven. Carriers that depend on push stay a
  compatibility limit, not a reason to invent approval flags.

### Native integration follow-up

- Generated carrier-ID filenames use numeric IDs and a fixed suffix, so stock
  display labels cannot become shell syntax in Soong's asset-copy rules.
  Provenance still binds the XML bytes and the original names.
- The DCM service's policy separates inherited platform service discovery
  from invocation: system diagnostics can find its name, but inbound Binder
  calls stay restricted to the broker (and userdebug `su`), and the daemon
  validates the caller's UID.
- Product selection also installs the generated vendor user and group
  databases; a build-time AID declaration alone does not give init a runtime
  user mapping. Final-image checks require that mapping and the dedicated
  domains.
- These controls still need phone checks.

### First phone boot

- The broker reached its dedicated domain under enforcing policy, but the
  vendor daemon's lifecycle gate referenced a platform-internal property.
- The gate is now owned by system_ext init. The daemon stays vendor-confined
  and non-lazy, which keeps the persistent kill switch without widening
  property access.
- The Android 17 full SDK version is explicitly labelled as a public build
  property, instead of granting the vendor IWLAN app access to default_prop.
- These startup fixes are pending a new native build and phone test; IMS is
  not yet accepted.

### Second phone boot

- The DCM daemon started automatically under UID/GID 2990, with SELinux
  enforcing, seccomp and no-new-privileges, and with no effective or
  permitted capabilities.
- Both cellular IMS bearers connected, but the broker saw a background
  firewall block, because Android leaves apps without INTERNET out of those
  rule updates.
- The broker now declares INTERNET so its UID is tracked correctly. On this
  base INTERNET is a runtime-revocable Network permission, granted
  automatically by the base default; the broker does not bypass revocation.
  Its neverallows for direct IP, raw and modem sockets, and its blocked-state
  handling, are unchanged.
- This widens Android permission authorization and must be reviewed together
  with possible indirect IPC paths. It is not a claim that every
  network-capable system service is unreachable. No Internet socket grant or
  policy bypass is added.
- IWLAN gets the thermal-service lookup that the platform PowerManager
  constructor needs; unrelated service probes stay denied.
- These changes await native and phone qualification.

## How this document is maintained

- Every security or privacy finding updates this document and the project's
  private findings register at the same time as the work that found it. The
  two live in different repositories, so they cannot share one commit.
- Each finding gets an ID of the form `TM-YYYYMMDD-NN` in the private
  register, with evidence, severity for a released build, status, next step,
  a public-safety marking and the public wording it maps to.
- A finding is described here once it is fixed, or once it is safe to state
  without giving an attacker a working path. Until then the affected asset
  names the gap in general terms.
- This document never contains device identifiers or unfixed exploit detail.
- An evidence state changes only when new evidence exists. Wording alone never
  turns an assumption into a claim.
- Each revision is dated at the top.

### Revision history

- **2026-09-14:** initial threat model.
- **2026-09-25:** full refresh from six internal reviews (cellular, proximity,
  vendor userspace, network, physical, supply chain) and two independent
  checks. Added development-state, cellular, SMS, location, kernel, closed
  vendor userspace, DiamaneOS-added features, identifier, TEE, firmware,
  supply-chain and signing rows; added attestation and kernel limits;
  corrected the USB, pKVM, fingerprint, secure-hardware, egress and
  kernel-source statements; applied a weakest-state rule to every evidence
  state.
- **2026-09-26:** from the GrapheneOS branding inventory, added network
  location, geocoding and attestation services, the browser's own
  connectivity checks, probe names sent while checks are off, and the Android
  Auto DHCP hostname; endpoint contracts now number 17. Later the same day
  the plan gave network location and geocoding to FP6-103, and the Fresh-UI
  boundary recorded the owner-approved shell rework. The owner then chose
  network location with a DiamaneOS EU relay to Apple, Apple directly or
  Apple China directly, all opt-in and off by default, and no DiamaneOS
  geocoder: geocoding stays off unless the user opts in to OpenStreetMap's
  public Nominatim. A source review of the inherited keyboard found that it
  keeps personal-dictionary words and contact names in storage available
  before first unlock, allows them in backups and ignores the no-learning
  flag; the Everyday use rows record it. Swipe typing is not planned. The fix
  is written in a DiamaneOS keyboard fork and waits for a build and phone
  test.
- **2026-09-26:** rows updated as bring-up findings landed (TIPC in the
  kernel, VoLTE and call audio, logging, adb authorization on debuggable
  builds, OEM unlocking, key provisioning and deletion, the source-versus-stock
  review); added the certificate-validity-at-boot row for DiamaneOS's own
  clock daemon; recorded the v1 scope change, the OpenEUICC no-go, the eSIM
  service default and JNI library, the retired Info release feed, the build
  identity, the r9s enforcing policy and the SoC serial exposure. On
  2026-09-27 the fingerprint row recorded that the vendor debug service is
  never registered (kept inside the HAL process), and the kernel row recorded
  the r9t kernel: the module deny list, the production configuration and why
  KPROBES stays on. After the owner's decisions the same day, KPROBES is
  owner-accepted, debugfs stays as on r9s, boot parameters handed to user
  space are logged by name only, and a row covers the previous boot's logs
  kept by pstore/ramoops.
- **2026-09-27:** for r9t, the eSIM row and decision record: the stock LPA
  cannot list profiles on the FP6, so its service is off again and its JNI
  library is gone; eSIM follows GrapheneOS until our own LPA ships. The
  firmware row records that the touch driver writes its stock firmware file
  to the controller when the versions differ, as on stock. The closed-file
  count stays at 720: the touch firmware came in and the JNI library went
  out. At integration the LPA wording was corrected (stock leaves its eSIM
  service on), the firmware row gained the touch driver's read-failure case,
  and the pstore row the device rule that makes the event-log device
  unwritable on release builds.
- **2026-09-27:** added the privacy-indicator row for the Tally shell from the
  design-token review: the prototype's privacy chip did not grow with the
  text size, and its night-mode indicator colours fell below 3:1 over a light
  app bar. The native spec keeps stock's sp sizes and picks indicator colours
  by the area under them.
- **2026-09-28:** the Tally prototype now draws the privacy chip at stock's sp
  sizes and takes indicator colours from the area under them, with a 2 dp
  edge; the prototype residual is removed from the privacy-indicator row.
- **2026-09-28:** for the Tally shell's first commits (roadmap step 3), the
  privacy-indicator row becomes "Privacy indicators and disclosures under the
  Tally shell" and takes the plan's commit rules for indicators and for what
  the lock screen, prompts, footer, rows and toasts must show; a "Shell fork
  rebase lag" row covers carrying the shell onto each GrapheneOS release
  behind one build flag; the Fresh-UI boundary lists the lock-screen,
  transition, tile and settings-switch rules.
- **2026-09-28:** the lock screen of roadmap step 3.3 (local tally-lock: the
  lock strip's sources and what it shows, the Tally clock, the indication
  line's fingerprint hint and error colour, the bouncer restyled by flagged
  resources) and step 3.5 (the SettingsLib switch restyle, the system label
  and the fallback boot logo) join the privacy row and the shell rules.
- **2026-09-28:** the SystemUI privacy indicators of roadmap step 3.1 (local
  branch, not built): the privacy-indicator row now says that only the
  shade's privacy chip opens the stock privacy dialog, as in stock, while the
  lens ring and the new location lamp by the lens take no touch. It names the
  lamp by the lens beside the ring, lists the one-chip shade header and the
  16 dp dot among the drawing changes, says how the area under an indicator
  is read, and records the limits: an app's own in-window scrim is not seen,
  the status bar chips sit under SystemUI's dialogs and shade, and the FP6's
  ring follows the declared rectangular cutout.
- **2026-09-28:** security review B of step 3.3 (local tally-lock, not built)
  found that the lamp strip could crash SystemUI before its first items
  arrived (high if shipped: a crash takes every privacy indicator down), and
  that the strip showed an alarm more than 12 hours away, Bluetooth when on
  but not connected and the battery percentage always, and the always-on
  strip briefly after dozing with doze off, all more than stock's lock screen
  shows. The branch now fixes all of them, matching stock, with tests that
  fail on the old code.
- **2026-09-28:** security review A of step 3.3 (local tally-lock, not
  built): the bouncer's Tally code only draws, and the fingerprint hint's
  count cannot affect unlocking; its background read and write are to be
  guarded, so a bad value only hides the hint instead of crashing SystemUI
  (fixed on the branch).
- **2026-09-28:** the security review of step 3.6 (local tally-recents, not
  built) found that a Stop from Recents did not check that the lock screen
  was dismissed, as the Active apps dialog does, and two robustness gaps: a
  listener binder owned by SystemUI crashed it, and reports re-read every
  app's policy on the thread the privacy indicators use. The branch now
  refuses a Stop while the lock screen shows (occluded and dozing included),
  refuses SystemUI's own binders as the listener and survives a listener that
  throws, runs the work on SystemUI's long-running thread, and drops the
  listener when SystemUI lets go of Launcher.
- **2026-09-28:** the security review of step 3.1 (local tally-status, not
  built) found that the area signal restarted the privacy dot's update delay,
  so an app flipping its status bar appearance fast enough could keep the dot
  from showing (critical if shipped); that the capture chips' edge was
  clipped, so their dark colour fell below 3:1 on light areas; and that the
  overlay check did not cover the Tally indicator resources. The check now
  refuses them, and the branch fixes the rest: the dot's colour no longer
  passes through its update delay, the capture chips' edge is their own
  border (light variants where no area is known), the VPN icon never
  overflows because of the dot's room, and the dot's touch area stays within
  its own column. Residuals: like the stock dot, the lens ring and the lamp
  by the lens are left out of screenshots, and on a locked phone and the
  always-on display they show whether the camera or location is in use.
- **2026-09-28:** the volume panel, power menu and toasts of roadmap step 3.4
  (local branches volume-left and tally-popups, not built): drawing, layout
  and motion only. Ringer, Do Not Disturb, the safe-volume warning, stream
  muting and every accessibility action stay stock. The power menu keeps its
  actions, Lockdown, Emergency, its lock-screen filtering and the emergency
  affordance; its new notes under Restart and Lockdown name the credential
  type in the lock screen's own words (the bouncer already shows it, so
  nothing new is disclosed). Toasts change look and motion only. The volume
  dialog can sit on the left edge by a device value (FP6, as stock FP6).
- **2026-09-28:** the step 3.3 fixes (local tally-lock, not built; two
  security reviews pending): the bouncer's PIN keys, typeface, emergency key
  and password field change by visual-only code behind the Tally flag (input,
  the keyboard, lockout, throttling, the duress and wipe counters and the
  emergency key's action are untouched); an always-on strip replaces what
  stock's always-on date line showed and shows nothing more; the fingerprint
  hint's count is kept per user in SystemUI's device-protected storage; the
  Tally clock moves into the shared clock library, which now reads the Tally
  flag itself, so Wallpaper & style lists it (ThemePicker and WallpaperPicker2
  link the flag and token libraries; no new data reaches them).
- **2026-09-28:** the step 3.1 fixes (local tally-status, not built): the
  privacy dot sits 16 dp from the top and side and the status icons keep
  clear of it, screen capture alone takes the capture colour, indicators go
  out on the fill spring (appearance stays instant), and a lens config lets a
  device place the lamp by the lens. Found and fixed before any build: the
  first round's instant dot show did not cancel a running fade-out, whose end
  then hid the dot and removed its window while a sensor was still in use;
  showing the dot now cancels it, as stock's fade-in does.
- **2026-09-28:** Recents' Stop of roadmap step 3.6 (local branch
  tally-recents, not built; security review pending) joins the shell rules: a
  new SystemUI-proxy surface for the recents app only, with the Active apps
  checks re-run on every Stop.
- **2026-09-28:** the shade and Quick Settings of roadmap step 3.2 (local
  branch tally-shade, not built) join the shell rules (see
  [Quick Settings and the shade](#quick-settings-and-the-shade)).
- **2026-09-29:** the security reviews of roadmap steps 4b and 6a (local
  Launcher3 tally-recents and Settings tally-home, not built) found no issue
  (see [Recents' Stop](#recents-stop-steps-36-and-4b) and
  [Settings homepage](#settings-homepage-step-6a)).
- **2026-09-29:** the security review of roadmap step 4a (local Launcher3
  tally-home, not built) found three low issues, fixed on the branch before
  any build: the tallies row showed a work app's progress or timer while a
  separately locked work profile was redacted in the shade (work items now
  show no readout); the LEDs and the row showed notifications the shade hides
  (now SystemUI's own filters); and a tally tap could crash Home when its app
  had just gone away (the shade opens instead). The review of step 6c
  (SettingsIntelligence tally, not built) found no issue: the target SDK bump
  to 37 adds platform protections and changes nothing the app can do.
- **2026-09-29:** the security review of roadmap step 6b (local
  frameworks_base tally-build and Settings tally-switches, not built) found no
  issue (see [Settings switches](#settings-switches)).
- **2026-09-29:** the security review of roadmap step 5's WM Shell half
  (local frameworks_base tally-build, not built) found no issue (see
  [Motion: WM Shell](#motion-wm-shell-step-5)).
- **2026-09-29:** the security review of roadmap step 5's Launcher half
  (local Launcher3 tally-motion, not built) found no issue: motion only, with
  no permission, manifest or allow-list change (see
  [Motion: Launcher](#motion-launcher-step-5)).
- **2026-09-29:** after the phone test of `tally.r9t.20260929.3` (local
  frameworks_base tally-build, not built): the Tally lock strip and always-on
  strip now show beside GrapheneOS's built-in smartspace, which hides its one
  line under them (952388b3eb35, 143dad85dd8c); before they showed, the
  always-on strip was made to show media only where GrapheneOS's always-on
  line does (-134). The lens ring lights only for the front camera
  (4bc99973aff3); the chip and the dot still show every camera.
- **2026-09-29:** the owner's phone test of `tally.r9t.20260929.3` found that
  any app's decorated custom-view notification (a Clock timer) crash-looped
  SystemUI, taking the status bar, the shade and every privacy indicator with
  it, because the Tally card colour was read through the posting app's
  context (-135, high if shipped; fixed on frameworks_base tally-build
  de8a9edb4265). This set the context rule under
  [Rules learned from findings](#rules-learned-from-findings); a review of
  that rule across the Tally code follows.
- **2026-09-29:** with lock-screen notifications off (off unless set),
  GrapheneOS's smartspace line shows nothing; the Tally lock strip now drops
  its alarm and the always-on strip everything (frameworks_base tally-build
  941eaab3f3be). The lamps for Wi-Fi, Bluetooth, Calm and the battery stay,
  as the keyguard status bar icons they mirror show whatever that setting.
- **2026-09-29:** the lens ring keeps the camera privacy item's hold once the
  front camera has opened (frameworks_base tally-build f889e76a544e), and the
  date line under other clocks hides with lock-screen notifications off, as
  GrapheneOS's line (42cbc1c00780).
- **2026-09-29:** the security review of the Tally contexts and fixes found no
  second crash of the -135 kind (no Tally code reads SystemUI, Launcher or
  token resources through a context another app controls, and no
  app-controlled data was found to crash SystemUI, WM Shell, Launcher,
  Settings or the pickers), and three low findings, fixed before any build:
  the lens ring's close/privacy-item race (-136) and a facing lookup that
  could throw on the main thread (-137) (frameworks_base tally-build
  2e27b4dfdd12), and tallies for a profile Launcher's user cache did not know
  yet shown as the main user's (-138, Launcher3 tally-home 1ebdf19f1d). The
  lock strip's alarm now uses GrapheneOS's exact condition (cf4c47dc0240).
- **2026-09-29:** Home's tallies band and search slot can be removed like a
  widget and restored from Home settings (Launcher3 tally-home 47aa5e6690):
  two booleans in Launcher's own backed-up preferences, no manifest,
  permission or allow-list change; removing the band only hides it (the
  listener keeps serving the key LEDs). The OS's own notices (the android and
  SystemUI packages) give no tally or LED (27e5352e94); they keep their status
  bar icons, and screen recording keeps its capture chip. Their security
  review (paused) found that the removable items' settings were read from
  credential-encrypted preferences through a real app context while the
  taskbar builds its device profile before the first unlock. That would
  crash-loop Launcher on every boot until then and, since Launcher's taskbar
  is the phone's navigation bar in this build, leave no Home or Recents before
  the first unlock, even from the emergency dialer (-139, high if shipped,
  never built; fixed on tally-home f885161fcb and Launcher3 android17
  240e56d46f, which read them through the device profile's own injected
  preferences). The rest of the review found no issue: no path reaches the
  model, the database, uninstall or app info; nothing new is exported; no
  manifest or permission change; only system-uid or SystemUI code can post as
  the android or SystemUI packages. This set the credential-encrypted storage
  rule under [Rules learned from findings](#rules-learned-from-findings).
- **2026-09-30:** the Files review (step 7) fuzzed the archive code and
  reviewed trash (-140 to -143): the new archive code goes on
  (vendor_diamaneos local files-flags); trash stays off until DiamaneOS can
  say at trash time that files stay on the phone until emptied. Home's
  tallies and keycap LEDs count only things with a live readout or an
  activity in progress (owner, 30 September), never permanent background
  services.
- **2026-09-30:** step 7's Calculator fork and recovery title fork (local
  tally branches, not built): no manifest change in Calculator; recovery
  changes two string literals only. Notification rows get themed app icons
  (vendor_diamaneos release config, the flag
  android.app.notifications_redesign_themed_app_icons, read only by
  SystemUI's notification icon provider).
- **2026-09-30:** the Clock fork (local tally branch, emulator-checked, in
  build 6) and -144 (stock SystemUI: promoted chips show private content over
  an occluded lock screen; open).
- **2026-09-30:** -144 reproduced on the FP6 with a test app (a Live Update's
  private chip text shows over the emergency dialer on a locked phone;
  GrapheneOS's chip code, not Tally's); a fix is being written as an upstream
  patch. Home's tallies read Clock's MetricStyle time with plain Bundle
  getters only, and the media-session read no longer throws on a malformed
  value (-145, fixed before any build). This set the notification-extras rule
  under [Rules learned from findings](#rules-learned-from-findings).
- **2026-09-30:** -144 fixed (frameworks_base tally-build 7ea6b864d39e; the
  same change on the owner's fork, branch chip_leak, for GrapheneOS): a
  promoted chip whose notification the lock screen redacts shows only the
  public version's short text or the icon, also while the lock screen is
  occluded; notifications the lock screen hides give no chip. Its sweep left
  two low stock items open (-146: status bar notification icons are never
  redacted; -147: always-on promoted notifications with a trust agent and a
  separately locked work profile, and dream chips upstream).
- **2026-09-30:** step 8 security review of the step 7 forks. Calculator,
  recovery and vendor_diamaneos are clean, and the keyboard's password and
  no-learning handling holds. Three findings, fixes being written: -148
  (medium if shipped) and -149 (low) in the Clock fork (see
  [Clock](#clock-step-7)), and -150 (low) in the keyboard (see
  [Typed text and personal words](#typed-text-and-personal-words)). The Clock
  bullet and the keyboard and Files rows are corrected. All three are fixed in
  build 8; -149 and -150 are verified on the FP6 (the database went back to
  version 8 with its alarms; planted word lists were deleted at the locked
  boot, before the first unlock).
- **2026-09-30:** -146 reproduced on the FP6 (build 8): over the emergency
  dialer on a locked phone, a priority conversation's status bar icon showed
  the contact's avatar while the lock screen showed the app's icon. A
  SystemUI fix makes the status bar and chip icons follow the lock screen's
  redaction; verified on the FP6 in build 9 (the chat icon over the dialer,
  the avatar again after unlock), and ready for GrapheneOS upstream.
- **2026-09-30:** rewritten for readability, with no change in substance:
  each threat row became a section with fixed points, and summaries of the
  biggest risks and of every asset's status, an IMS integration notes section
  and a list of terms were added. The rules learned from -135, -139 and -145
  moved from these entries to the Fresh-UI boundary.

## Terms

- **A/B slots:** two copies of the system, so an update can fall back.
- **adb:** Android Debug Bridge, the USB debugging interface.
- **AFU / BFU:** after / before the first unlock since boot. Before it, user
  data stays encrypted; after it, most keys are in memory.
- **AML:** Advanced Mobile Location.
- **AOSP:** the Android Open Source Project.
- **ART:** the Android runtime that runs app code.
- **ASB:** the monthly Android Security Bulletin.
- **Attestation:** a TEE-signed statement about the device's keys and boot
  state; GrapheneOS's **Auditor** app checks it.
- **AVB:** Android Verified Boot, which checks partitions against a signing
  key at boot. A **custom AVB root** is a project key enrolled instead of the
  phone maker's; **vbmeta** holds its metadata and flags; a **hashtree** holds
  per-block hashes; a **rollback index** blocks older signed images. **Green**
  means the phone maker's key, **yellow** a user-enrolled key.
- **Binder:** Android's inter-process communication system.
- **Bouncer, keyguard:** the PIN or password screen, and the lock screen.
- **BPF:** small verified programs the kernel runs, used for monitoring.
- **BR/EDR, LE (BLE):** Bluetooth Classic and Low Energy. **HCI** links the
  Bluetooth host stack to the controller.
- **Bring-up:** early work to make the hardware function. r9r, r9s and r9t are
  successive private bring-up builds; "build 6" to "build 9" and names such
  as `tally.r9t.20260929.3` are private test builds.
- **CE / DE storage:** credential-encrypted storage (readable only after
  unlock) and device-encrypted storage (readable from boot).
- **CodeLinaro:** Qualcomm's public source hosting.
- **CSPRNG:** a cryptographically secure random number generator.
- **CT:** certificate transparency, public logs of issued TLS certificates;
  Android checks certificates against a list of logs (the CT list).
- **CTS / VTS:** Android's compatibility and vendor test suites.
- **DCM broker and daemon:** the source-built service that brings up IMS data
  connections; the broker is its Android-side app, the daemon its vendor-side
  service.
- **debugfs:** a kernel file system for debugging data.
- **diag:** Qualcomm's diagnostic interface to the modem and chip.
- **DoT / DoH:** DNS over TLS / HTTPS. **Private DNS** is Android's DoT
  setting.
- **DSP:** a digital signal processor (audio, sensor and similar
  co-processors).
- **EDL:** Qualcomm's emergency download mode, a low-level flashing mode that
  needs a signed "programmer".
- **eSIM, eUICC, LPA:** a downloadable SIM profile; the chip that stores
  profiles; and the local profile assistant app that manages them. **SM-DP+ /
  SM-DS** are the download and discovery servers; **GSMA SGP.22** is the eSIM
  standard.
- **Exec spawning:** GrapheneOS starts each app from a fresh process image for
  stronger address randomization.
- **FBE:** file-based encryption. **Metadata encryption** also covers file
  system metadata; **hardware-wrapped keys** reach software only in wrapped
  form.
- **FP6-nnn:** a task in the project plan.
- **Gatekeeper:** the TEE service that checks the lock-screen credential and
  slows repeated guesses.
- **GKI:** Google's generic kernel image.
- **GMS:** Google Mobile Services.
- **GNSS:** satellite positioning. **SUPL** is an assistance server reached
  over the mobile network; **PSDS** is downloaded predicted satellite data;
  **XTRA** is Qualcomm's assistance service; **control-plane positioning** is
  run by the network through the modem.
- **Gunyah, pKVM:** Qualcomm's hypervisor; Android's protected-VM hypervisor.
- **HAL:** hardware abstraction layer, the vendor service between Android and
  a driver. **HIDL** is an older HAL interface language.
- **hardened_malloc:** GrapheneOS's hardened memory allocator.
- **IMS:** IP Multimedia Subsystem, carrier voice (VoLTE), SMS and Wi-Fi
  calling over IP. **IWLAN** is IMS over Wi-Fi.
- **IMSI catcher:** a fake base station that collects subscriber identities.
- **IPA:** Qualcomm's hardware data path between modem and application
  processor.
- **JNI:** the interface between Java code and native libraries.
- **KeyMint:** Android's key store in the TEE. **StrongBox** is KeyMint in a
  separate secure chip; **Weaver** is a secure-chip service that throttles
  credential guesses; **RKP** (remote key provisioning) fetches attestation
  certificates from a server.
- **Kernel lockdown:** stops even root from changing the running kernel
  (integrity) or also reading its memory (confidentiality).
- **KMI / UAPI:** the kernel's interface to vendor modules / to user space.
- **KPROBES / kretprobes:** kernel probe points placed at run time.
- **LTS:** long-term-support kernel releases.
- **MTE:** Memory Tagging Extension, a CPU feature that catches many
  memory-safety bugs.
- **n-day:** an attack on a bug that is public and fixed upstream.
- **Null cipher:** a cellular connection without encryption.
- **OTA:** an over-the-air update.
- **PMIC RTC:** the clock in the power-management chip.
- **pstore/ramoops:** keeps kernel logs in RAM across a reboot. A
  **minidump** is Qualcomm's small crash dump.
- **QCRIL:** Qualcomm's radio interface daemon (the "radio daemon").
- **QMI / QRTR:** Qualcomm's message protocol and its transport between the
  application processor, modem and DSPs.
- **Roadmap steps** (3.1, 4a, 7 and so on): stages of the Tally work in plan
  task FP6-211.
- **RPMB:** a replay-protected flash area the TEE uses.
- **RRC/NAS:** cellular protocol layers for the radio link and network
  registration.
- **seccomp:** a kernel filter on the system calls a process may make.
- **SELinux:** the kernel's mandatory access control. **Enforcing** blocks
  what the policy denies; **permissive** only logs it. A **domain** is a
  process's label; **neverallow** forbids a grant at build time;
  **dontaudit** hides denials from logs.
- **SMMU:** the unit that limits which memory a device such as the modem can
  reach.
- **SoC:** system on chip, the Qualcomm processor package.
- **Soong:** Android's build system.
- **sp / dp:** Android size units; sp grows with the text size, dp does not.
- **SystemUI, WM Shell, Launcher3, SettingsLib, frameworks/base:** status bar,
  shade and lock screen; window transitions; Home and Recents; shared Settings
  UI code; and the core framework source tree.
- **Tally:** DiamaneOS's user interface; the **Tally flag** is the build-time
  flag that turns its code on.
- **TEE:** Trusted Execution Environment, here Qualcomm's TrustZone secure
  world.
- **TIPC:** a kernel inter-process messaging protocol.
- **userdebug / user:** debuggable build (allows `adb root`) / production
  build.
- **userfaultfd:** lets a process handle its own page faults.
- **Vanadium:** GrapheneOS's browser and WebView.
- **VINTF:** the compatibility contract between the Android framework and
  vendor code; levels track Android versions.
- **XBL:** Qualcomm's first-stage bootloader.
- **zygote:** the Android process that starts apps.
- **-nnn** (for example -144): a finding in the private findings register.
