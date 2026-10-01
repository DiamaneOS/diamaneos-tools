# DiamaneOS Threat Model and Product Boundaries

Revision: 2026-10-01 (shortened; revision history and IMS integration notes moved to [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md), Tally shell rules to [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md); no change in substance); 2026-09-30 (public build commands: what they enforce and their limits); 2026-09-30 (rewritten for readability; no change in substance). Earlier revisions: [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md).

> Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project.

## How to read this document

What DiamaneOS protects on the Fairphone 6 (FP6), against whom, how, and what is
missing. DiamaneOS is under development: each protection is a requirement unless
its status says otherwise, nothing is qualified on a release candidate yet, and
no hardware claim is a demonstrated protection until its validation passes.
Start with the risks below, then the [asset table](#threats-by-asset). Status
words are defined in [Evidence states](#evidence-states), other terms in
[Terms](#terms). Development builds are named by date: the 2026-09-26 build is
the first SELinux-enforcing one, the 2026-09-27 build the next. `FP6-nnn` is a
plan task; -nnn (for example -144) is a finding in the private findings
register. Companion files: [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md)
(history) and [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) (Tally shell
rules).

## Biggest remaining risks

1. **Development builds are unprotected:** debuggable, unlocked, public test
   keys, SELinux enforcement not validated per subsystem
   ([details](#current-development-state)).
2. **No MTE, much closed code:** memory-safety bugs are not contained as on
   current Pixels; the 720 closed files (about 369 MB) include large parsers of
   untrusted input, and the closed GPU driver and shader compilers run in every
   app ([details](#camera-microphone-sensor-streams-device-integrity)).
3. **Unpatchable firmware** that lags Android Security Bulletins, with no update
   path yet ([details](#firmware-security)).
4. **Credentials rest on the TEE:** no StrongBox or Weaver; throttling
   unverified; passphrase not implemented; fingerprint class not measured
   ([details](#bfu-user-data)).
5. **The modem is outside Android's control:** isolation assumed; its traffic
   bypasses network controls; no fake-base-station detection; the 2G and
   LTE-only controls fail silently
   ([details](#call-and-sms-content-subscriber-identity-coarse-location)).
6. **No USB-C port control** ([details](#locked-device-data-kernel-integrity)).
7. **Attestation fails; verified boot shows yellow**
   ([details](#hardware-limits-and-evidence)).
8. **Supply chain:** one signing authority and one-person review; FP6 builds not
   yet network-denied or built twice; vendor inputs checked only against the
   vendor's own published hash ([details](#source-and-build-outputs)).
9. **Keyboard words readable before first unlock** until the keyboard fork ships
   ([details](#typed-text-and-personal-words)).

## Current product scope

Daily use by privacy- and security-focused users, on one device and variant: the
GrapheneOS-based FP6 port. A release needs a locked bootloader on a custom AVB
root and monthly releases; the launch scope must pass qualification first. No
root, microG, signature spoofing, Magisk accommodation, unlocked-bootloader
daily use or bundled cloud account. A clear, expressive UI with measured
maintenance; accessibility and localization are acceptance criteria, not polish.
No global security score, stock-parity promise, or superiority claim without
dated like-for-like evidence.

"GrapheneOS-derived" describes source lineage, not equal security: the FP6 lacks
several hardware features GrapheneOS relies on ([hardware
limits](#hardware-limits-and-evidence)).

Passphrase-first is mandatory (passphrase onboarding and credential-policy
enforcement); PIN-optional is not in v1. A PIN-optional-with-warning change
would need an explicit scope decision and a renewed compatibility review; it is
recorded only as a revisit condition.

### v1 scope (standalone summary)

Public launch needs the release gates (cadence, beta, qualification) and, on the
final candidate: DE/EU carrier configs; eSIM LPA; LineageOS-parity camera;
passphrase-first setup enforced for all users; on-device security status;
encrypted microSD; tracker alerts; cellular alerts or an honest "unsupported"
(on the current Qualcomm radio software: "unsupported"); offline SD OTA;
UnifiedPush recommendation; WebUSB and CLI installers.

On 2026-09-26 the owner moved after public v1: install-time privacy presets, the
battery suite (ceiling, health, cycle/export), DNS filtering, the privacy
dashboard, per-app routing and filtering, panic reboot to BFU, scheduled reboot,
hardware diagnostics, share-time metadata stripping. Until they land, inherited
GrapheneOS protections cover their safety role (inactivity auto-reboot,
lockdown, VPN lockdown, the per-app Network permission). Post-release ideas and
conditional kernel or repair research are not v1. Planned features that parse
untrusted input or need privilege are threats themselves ([added
features](#device-integrity-and-the-data-each-feature-handles)).

## Current development state

Every build so far is a private bring-up build on a development device; none was
distributed. Bring-up builds deliberately trade away protections to make
hardware work. A release candidate failing any gate below is not released.
Bring-up builds are never handed out or used as anyone else's daily phone; their
signing and SELinux state gives no verified-boot, signature-permission or
SELinux protection, and personal accounts and sensitive data stay off them until
SELinux is enforcing.

- **Build type.** Today: `userdebug`, ADB on by default, `adb root`; since the
  2026-09-26 build ADB asks on the phone before trusting a computer and no
  developer key is pre-trusted (earlier builds accepted any computer); recovery
  accepts ADB unasked on unlocked or debuggable phones (AOSP design). Release:
  `user`, `ro.debuggable=0`, ADB off and authenticated, no pre-trusted keys, no
  network ADB. Gate: production-form user candidate (FP6-047), debug exposure
  audit (FP6-060).
- **SELinux.** Today: earlier builds fully permissive; since the 2026-09-26
  build debuggable builds boot enforcing with a per-domain policy from the
  denials captured on the last permissive build (narrow rules only, generic proc
  and sysfs nodes relabelled before any write grant, no dontaudit rules, nothing
  new for apps or shell), untested on the phone. Release: enforcing, no
  permissive domains. Gate: enforcing runs per subsystem (no owning task yet),
  user candidate (FP6-047).
- **Bootloader and keys.** Today: unlocked, public AOSP test keys; no OEM
  unlocking control in the OS, so the unlock-ability flag stays as stock left
  it. Release: locked on a DiamaneOS AVB key, release keys only, relock never
  enrols a public test key; the OS shows and manages OEM unlocking (off after
  relock, on before unlock). Gate: custom-key relock (FP6-050), signing roles
  and equipment (FP6-035, FP6-036).
- **Network endpoints.** Today: inherited GrapheneOS services (connectivity,
  time, CT list, provisioning proxies, app catalog, SUPL proxy). Release:
  DiamaneOS EU endpoints with visible standard-server choices. Gate: endpoint
  contracts (FP6-100), implementations (FP6-102 to FP6-112).
- **Closed vendor code.** Today: 720 stock Qualcomm/Fairphone files (about 369
  MB), stock-first, all hash-pinned, purpose partly generic. Release: each file
  justified, source builds where possible. Gate: component removal and closure
  gate (FP6-060, FP6-061, FP6-208), per-component source replacement (FP6-200 to
  FP6-207).
- **Debug interfaces.** Today: userspace Qualcomm diag removed; debug USB
  functions remain. Release: no debug USB functions, diag or trace sinks in user
  builds. Gate: debug exposure audit (FP6-060).
- **Firmware.** Today: the last flashed stock release. Release: dated firmware
  patch level shown, update path defined. Gate: firmware review (FP6-206).

### IMS status (2026-09-28)

IMS (carrier voice, SMS and Wi-Fi calling over IP) on the enforcing development
image: ordinary voice, SMS and mobile data work on the tested subscriptions,
including basic Wi-Fi calling and coexistence with VPN lockdown. Reconnection
can leave IMS unavailable for about ten minutes; a second-network
observer/address discrepancy is under investigation. The passing simulated
telephony tests do not validate emergency calls or carrier location delivery.
The new `de.diamaneos` app and interface identities need separate build and
device validation; earlier image results are not acceptance of the namespace
migration. The [IMS integration
notes](THREAT_MODEL-HISTORY.md#ims-integration-notes) record how the containment
evolved.

## What DiamaneOS aims to defend against

- Remote and proximity attack surface (network including IP over cellular,
  Bluetooth, NFC, USB, media parsing): inherited GrapheneOS hardening and a
  reduced vendor surface; without hardware memory tagging, memory-safety bugs
  are not contained as on current Pixels.
- Malicious or over-permissioned apps: sandboxing, Network and Sensors
  permissions, Storage and Contact Scopes, install-time presets.
- Persistence after compromise: verified boot with rollback protection on a
  locked bootloader.
- Opportunistic physical access to a powered-off (BFU) device: file-based
  encryption with hardware-wrapped keys and a strong generated passphrase.
- Data exfiltration by the OS vendor: no telemetry or Google Mobile Services;
  self-hosted or user-selectable endpoints (documented EU endpoints with visible
  alternatives).

## What DiamaneOS does not defend against

- Offline brute force of a weak credential: Gatekeeper backoff in the Qualcomm
  TEE is expected but unverified, with no discrete secure element exposed to
  Android ([BFU user data](#bfu-user-data)); the required 6 to 8 word generated
  passphrase (not implemented yet) makes guessing much harder.
- Sophisticated forensic extraction from an unlocked or powered-on locked (AFU)
  device.
- Firmware compromise: boot, TrustZone, hypervisor, modem, DSP, Wi-Fi and
  Bluetooth firmware come from Fairphone and Qualcomm; the project cannot build
  or patch it.
- Baseband exploitation beyond what SoC isolation provides; on the FP6 that
  isolation is assumed, not verified.
- Detection of fake base stations, null-cipher sessions or identity requests,
  reported unsupported by the current Qualcomm radio software.
- Traffic the modem sends by itself (IMS, SUPL, control-plane location), which
  Android's network controls do not see.
- Removal of eSIM profiles, modem state or factory calibration by factory reset;
  they live outside userdata.
- Vendor-controlled unlock: installing, and recovering from a bad flash, need
  Fairphone's online unlock service (a dependency, not a security gap).
- A determined adversary with unlimited physical access and time.

## Hardware limits and evidence

- **No MTE:** the stock CPU feature set does not expose memory tagging; its
  control properties are absent.
- **No StrongBox or Weaver exposed:** stock declares only default TEE KeyMint
  and RKP instances and no Weaver HAL. Stock ships disabled Secure Processing
  Unit (SPU) software, but the FP6 device tree describes no such unit and the
  images carry no SPU firmware or provisioning, so none is usable; whether the
  chip has one is unknown. Accurate: "stock exposes no StrongBox or Weaver", not
  "the phone has no secure hardware". The eUICC, NFC controller and TEE are
  separate boundaries.
- **Attestation is TEE-only:** a locked custom-key build reports yellow, never
  green; GrapheneOS's Auditor does not support the FP6. Earlier builds did not
  enable remote key provisioning, so attestation was expected to fail. Since the
  2026-09-26 build the device sets stock's provisioning properties (requests via
  the inherited GrapheneOS proxy until DiamaneOS runs its own) and reports the
  factory attestation IDs. On the 2026-09-27 build provisioning reaches the
  server but the TEE's certificate request always fails, so no attestation keys
  are provisioned and attestation still fails (cause open).
- **No pKVM:** on current firmware the kernel runs under Qualcomm's Gunyah
  hypervisor, not KVM, so Android protected VMs are unavailable (observed on a
  bring-up build).
- **Radio isolation:** SoC-level isolation of the modem from the application
  processor is assumed and unverified. USB-C port control, the fingerprint class
  and firmware patch lag are under [Locked-device
  data](#locked-device-data-kernel-integrity), [AFU
  unlock](#afu-unlock-auth-bound-keys) and [Firmware
  security](#firmware-security).
- **Vendor horizon:** the Android 14-level vendor (VINTF level 8) should still
  run on Android 18; Android 19 will very likely drop level 8, HIDL and kernel
  6.1. Once Fairphone moves the FP6 to a newer vendor, fixes for the current
  vendor code and firmware stop, so DiamaneOS has to follow.
- **Kernel:** 6.1 (android14 KMI), with a recorded, intentional deviation for a
  48-bit virtual address space.
- **Still needing candidate tests:** TEE KeyMint, Gatekeeper and attestation
  behaviour, custom-key relock, A/B failure recovery, rollback behaviour.

## Evidence states

A state changes only with new evidence. An asset's state is the weakest among
its mitigations; its status names it first, stronger items after.

- **Assumption:** plan text; no design or device evidence.
- **Designed:** design or configuration reviewed; not running on a device.
- **Bring-up (not qualified):** in a private bring-up build; the security
  property is untested and SELinux not enforcing.
- **Observed (bring-up):** a narrow behaviour seen on a bring-up build.
- **Recorded:** stated in a project record, not rechecked this revision.
- **Observed gap:** confirmed missing or not working today.
- **Accepted limitation:** a documented limit the project does not plan to
  remove.
- **Qualified:** the named validation passed on a release candidate (none yet).

## Threats by asset

Assets are grouped by attacker position. Each lists the threat (attacker and
entry point), intended protection, what remains, validation (owning plan task,
or "no owning task yet") and status.

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
| Apps | [Kernel integrity and all data](#kernel-integrity-and-all-data) | Built for the 2026-09-27 build, not phone-tested; Observed (bring-up) |
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
| Boot and firmware | [Keys, Gatekeeper throttling, fingerprint templates](#keys-gatekeeper-throttling-fingerprint-templates) | Observed gap (builds before the 2026-09-26 build) |
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

- **Threat:** network observer or endpoint operator, via connectivity,
  captive-portal and DNS checks, HTTPS time, the CT list, key and DRM
  provisioning, app and browser updates, network location and geocoding (when
  on), Auditor remote verification and sample submission (opt-in), and
  phone-side eSIM (SM-DP+/SM-DS) and carrier entitlement connections.
- **Protection:** no telemetry or GMS; since the 2026-09-26 build the build
  properties keep Fairphone's stock product identity (brand, product, device,
  model), as GrapheneOS keeps Google's; EU-primary DiamaneOS endpoints with
  documented upstreams, each with a visible standard-server alternative. Network
  location is off by default, never silently enabled by the setup wizard; opt-in
  choices: a DiamaneOS EU relay to Apple (no cache, no per-request logs, device
  IP hidden from Apple), Apple directly or Apple China directly. No DiamaneOS
  geocoder: geocoding off by default, opt-in directly to OpenStreetMap's public
  Nominatim under a DiamaneOS user agent. eSIM and carrier hosts are listed as
  non-project endpoints (ordinary app traffic, visible to Android's network
  controls).
- **Remaining:**
  - Small-user-base hostnames are a fingerprint; the HTTPS time bootstrap
    resolves outside Private DNS.
  - The DNS resolver and browser send GrapheneOS probe names even with
    connectivity checks off; the browser's own checks can be repointed only by
    rebuilding it.
  - With network location on (the setup wizard's location switch turns it on),
    nearby Wi-Fi and cell identifiers go to GrapheneOS's relay for Apple's
    location service, geocoding queries to GrapheneOS's server under a
    GrapheneOS user agent. Direct choices show Apple the device IP with the
    Wi-Fi and cell list, OpenStreetMap the IP with the searched text or
    coordinates.
  - Auditor's opt-in remote features use GrapheneOS's attestation service;
    sample submission sends the full system property list.
  - Non-EU upstreams are disclosed per endpoint.
  - CT enforcement fails open once the CT list is over 70 days old, so mirror
    freshness is a security dependency.
  - "No telemetry" in closed vendor files rests on a static scan; runtime egress
    is unmeasured.
- **Validation:** endpoint contracts (FP6-100); time, connectivity,
  provisioning, CT and catalog endpoints (FP6-102 to FP6-112); network location
  and geocoding (FP6-103); attestation (FP6-106); runtime egress capture with
  SELinux enforcing (no owning task yet).
- **Status:** Assumption: 13 of 16 endpoint contracts unspecified (the Info
  release feed retired on 2026-09-26 with the Info app); eSIM and carrier egress
  not in the contract. Designed: 3 of 16 specified, none deployed; network
  location and geocoding choices decided, both contracts blocked. Observed
  (static): no contacted host in the selected closed files except GNSS cloud
  hosts removed by pinned configuration.

#### DNS privacy

- **Threat:** network observer or on-path resolver, via DNS queries.
- **Protection:** visible encrypted-DNS default (DoT), no silent plaintext
  fallback.
- **Remaining:** the time bootstrap, apps with their own DoH/DoT and modem
  traffic bypass it.
- **Validation:** encrypted-DNS default and DNS filter (FP6-079).
- **Status:** Assumption: not implemented; the inherited Android default
  applies.

#### App data from remote exploit

- **Threat:** remote network, media or web content, via carrier and Wi-Fi data,
  media files and streams, browser and WebView, captive portals, web content
  reaching the GPU shader compiler, hardware video decode (planned).
- **Protection:** inherited sandbox, hardened_malloc, exec spawning; Vanadium
  browser and WebView as mirrored unmodified APKs; the hardware codec service to
  keep the stock seccomp sandbox and platform codec domain; codec device nodes
  to be separated from camera nodes and display configuration.
- **Remaining:** no MTE; closed GPU driver and shader compilers in every app
  process; closed codec and video firmware (not integrated yet); codec seccomp
  installation unverified and can fail open; inherited captive-portal WebView;
  browser component updates still point upstream.
- **Validation:** inherited hardening (FP6-046), debug exposure and component
  removal (FP6-060, FP6-061), hardware media integration (no owning task yet),
  DRM limits (FP6-065), browser delivery (FP6-105, FP6-111).
- **Status:** Assumption: exec spawning, sandbox permissions, codec sandbox and
  browser delivery unvalidated on the FP6. Observed (bring-up): hardened_malloc
  active (48-bit VA kernel).

#### Traffic outside Android network policy

- **Threat:** carrier or network observer, via modem IMS signalling and media,
  SUPL, and control-plane and network-initiated location.
- **Protection:** documented; carrier hosts listed as non-project endpoints; the
  privacy dashboard and per-app routing say they do not cover it.
- **Remaining:** invisible to the Network permission, VPN, Private DNS, DNS
  filter and dashboard.
- **Validation:** endpoint contracts (FP6-100), assisted GNSS (FP6-103),
  per-carrier configuration review (FP6-090).
- **Status:** Accepted limitation; carrier egress not yet in the endpoint
  contract.

#### Certificate validity checks at boot

- **Threat:** on-path attacker with an expired certificate, or blocking time
  sources, via the clock between boot and first network time (the PMIC RTC
  cannot be set from Linux).
- **Protection:** DiamaneOS's `timekeepd` saves the clock's lead over the RTC
  counter whenever time is set and at least hourly; at post-fs-data, before
  zygote, a one-shot step sets the clock from counter plus offset, only if
  nothing set it since boot and only to a time before 2100. Only that step holds
  CAP_SYS_TIME; the long-running part holds no capability. Each has its own
  SELinux domain, with no modem, network or app (replacing Qualcomm's time
  daemon and time app). Network time is HTTPS, certificate expiry checked
  against build time.
- **Remaining:** the clock starts at the build date on first boot and after
  factory reset; after PMIC power loss (battery removed), at the last saved
  time: up to about an hour before the phone's last wake, plus the time off.
  Until network time arrives, certificates expired since then still validate,
  longer if time sources are blocked. RTC drift between saves (1 s resolution).
- **Validation:** clock across reboot, suspend, manual and network time changes
  and battery removal, SELinux enforcing (no owning task yet).
- **Status:** Bring-up (not qualified): implemented for the 2026-09-26 build;
  untested on the phone.

### Cellular and baseband

#### Call and SMS content, subscriber identity, coarse location

- **Threat:** fake base station, IMSI catcher, downgrading or null-cipher
  network, via 2G fallback, pre-authentication identity requests and radio
  security events.
- **Protection:** opt-in "2G network protection" and LTE-only; planned: the
  security status view shows cellular alerts as unsupported.
- **Remaining:** 2G stays allowed by default ([decision](#decision-record)), so
  a downgrade works unless the user opts in. The opt-in controls are visible but
  fail open silently: the mode never reaches the modem; the planned default-mode
  fix covers new subscriptions only, so stored settings need a reset step and a
  regression check. No detection: null-cipher/integrity control and security
  notifications are reported unsupported by the radio software. LTE/NR identity
  exposure remains.
- **Validation:** telephony bring-up (FP6-044), cellular security notifications
  (FP6-084), security status view (FP6-073), radio-policy regression check (no
  owning task yet).
- **Status:** Observed gap (bring-up): controls visible and silently
  ineffective; fix or hide them before any build leaves development. Null-cipher
  control reported unsupported (bring-up notes; radio daemon response not
  captured).

#### SMS, SIM applets, broadcast alerts, carrier configuration

- **Threat:** network or SIM-side sender, or carrier, via SMS and class-0/type-0
  messages, SIM toolkit proactive commands, cell broadcast, and SIM access rules
  granting carrier privileges.
- **Protection:** inherited AOSP handling; no project change.
- **Remaining:** SIM applets act partly outside OS control; network "silent" SMS
  are acknowledged without display (3GPP rules); SMS are parsed in modem and
  framework; apps given carrier privileges by the SIM can change carrier
  configuration.
- **Validation:** telephony bring-up (FP6-044).
- **Status:** Accepted limitation (inherited).

#### Application processor and user data

- **Threat:** over-the-air attacker or compromised modem firmware, via modem
  parsers (RRC/NAS, SMS, modem IMS), then shared memory, the IPA data path,
  QMI/QRTR services, modem remote-storage and file services.
- **Protection:** modem image authenticated by the stock secure-boot chain;
  AP-side modem services narrowed and confined to own SELinux domains; modem
  crash dumps off.
- **Remaining:** modem firmware unpatchable by the project and behind ASB; SoC
  isolation (SMMU, memory protection) assumed, not verified; silent modem
  restarts hide crash-and-retry attacks; modem state survives factory reset.
- **Validation:** modem and telephony bring-up (FP6-042, FP6-044), debug
  exposure and component removal (FP6-060, FP6-061).
- **Status:** Assumption; bring-up runs the modem and its AP services
  permissive, so no protection claim.

#### Telephony control and subscriber data

- **Threat:** malicious app via binder or compromised modem via QMI, reaching
  the closed radio daemon (QCRIL), Qualcomm IMS app, DiamaneOS's call-audio
  bridge or the eSIM LPA.
- **Protection:** explicit privileged-permission allowlists with denials; own
  SELinux domains; no radio daemon socket; radio daemon not a binder service;
  diag access removed. The call-audio bridge replacing Qualcomm's call-audio app
  holds only the normal audio-settings permission (no audio-routing or capture
  permission, no network); its domain reaches only the radio daemon's call-audio
  service and the audio server.
- **Remaining:** closed code parses untrusted input; locking presigned vendor
  apps' update path planned, not implemented; VoLTE work is expected to add
  closed, network-capable daemons and a presigned privileged app, so the closed
  surface still grows; enforcing untested.
- **Validation:** telephony bring-up (FP6-044), eSIM selection and delivery
  (FP6-207, FP6-089), enforcing runs (no owning task yet).
- **Status:** Bring-up (not qualified): allowlists and domains reviewed; runtime
  and enforcing unverified; call-audio bridge implemented for the 2026-09-26
  build, untested on the phone.

#### eSIM profiles and download sessions

- **Threat:** TLS interceptor, thief or examiner after reset, or coercer, via
  LPA connections to eSIM servers and profiles retained in the eUICC.
- **Protection:** installed profiles work as SIMs without an LPA (GrapheneOS
  baseline). Since the 2026-09-27 build the stock Qualcomm LPA stays installed
  with services off by default (stock leaves its eSIM service on), its JNI
  library unshipped. Planned: our own source LPA (list, enable, disable, delete
  over the platform's logical channels; download later) behind an off-by-default
  "eSIM support" switch (owner decision 2026-09-27). OpenEUICC excluded
  (GrapheneOS os-issue-tracker #6275 and #2631). GSMA SGP.22 mutual
  authentication.
- **Remaining:** the stock LPA cannot list profiles on the FP6 (the stock radio
  daemon cannot decode the modem's reply), so Android treats an eSIM as a
  physical SIM: none can be added, switched or deleted from Android, and factory
  reset and duress cannot reach the eUICC. The stock LPA's TLS trust
  configuration fails the production trust rule, relevant only if it is turned
  on again.
- **Validation:** eSIM selection and delivery (FP6-207, FP6-089).
- **Status:** Bring-up (not qualified): on the 2026-09-26 build the service was
  on and could not list profiles (phone test 2026-09-27); the 2026-09-27 build
  turns it off again, not yet built.

#### Radio-off expectation, location privacy

- **Threat:** network or passive observer, via the modem attaching on its own,
  or airplane mode.
- **Protection:** airplane mode puts the modem in low-power mode.
- **Remaining:** modem silence in airplane mode or without the radio daemon is
  unverified; a device setting keeps the SIM powered in airplane mode.
- **Validation:** telephony bring-up (FP6-044).
- **Status:** Assumption.

### Location

#### Location history

- **Threat:** vendor cloud, assistance-server operator, carrier or local log
  reader, via GNSS HAL configuration, SUPL, PSDS, control-plane positioning and
  logs.
- **Protection:** Qualcomm cloud, XTRA and crowdsourcing paths excluded by
  pinned configuration; PSDS off until an endpoint is bound; SUPL
  user-selectable (Off, proxy, standard); network location and geocoder off by
  default; IMS geolocation has no network geocoder.
- **Remaining:** SUPL defaults to the GrapheneOS proxy until the DiamaneOS SUPL
  endpoint exists; "standard" SUPL goes straight to the carrier-configured
  server (Google's, on the carrier tested so far); SUPL requests carry cell
  information; control-plane positioning runs in the modem; GNSS engine state on
  persist survives reset; location logging still verbose; the radio log records
  the serving-cell identity on every registration poll (today about twice a
  second on one SIM), readable over adb and in bug reports.
- **Validation:** assisted GNSS (FP6-103), GNSS bring-up tests (FP6-045).
- **Status:** Assumption: SUPL default undecided; no GNSS fix recorded. Bring-up
  (not qualified): cloud paths absent and pinned.

### Malicious and over-permissioned apps

#### App and user data

- **Threat:** malicious or over-permissioned app, via permissions, background
  listeners, IPC/URI grants and sensors.
- **Protection:** per-app Network and Sensors permissions, Storage and Contact
  Scopes (inherited); install presets (Untrusted/Standard/Trusted); per-app
  routing and filtering; privacy dashboard with CE-only bounded history.
- **Remaining:** "Trusted" is user-chosen policy, never an audit; routing
  attribution limits (shared UIDs, own DoH/DoT, system and modem traffic); v1
  features not implemented.
- **Validation:** inherited hardening (FP6-046), privacy presets (FP6-072), DNS
  architecture and filter (FP6-078, FP6-079), per-app network controls
  (FP6-080), privacy dashboard (FP6-081).
- **Status:** Assumption: inherited controls in source, unvalidated on the FP6;
  v1 features not implemented.

#### Per-app destination history (dashboard)

- **Threat:** AFU forensic examiner or malicious app, via dashboard storage,
  backups and bug reports.
- **Protection:** CE-only storage, aggregate by default, 7-day default, cleared
  on reboot by default, no backup, not in bug reports.
- **Remaining:** the dashboard is itself the most sensitive log on the device.
- **Validation:** privacy dashboard (FP6-081).
- **Status:** Assumption: plan rules only.

#### Kernel integrity and all data

- **Threat:** malicious or compromised app or renderer process, via the GPU
  driver node (reachable by every app, as on stock), binder, loaded
  network-protocol modules and socket families, and the DSP remote-call driver
  (named groups only).
- **Protection:** GrapheneOS kernel hardening configuration merged into the
  vendor kernel; lockdown (confidentiality); SELinux socket and device
  restrictions; modules reduced to product use. Profiling work must never weaken
  lockdown or tracing restrictions in user builds.
- **Remaining:**
  - No MTE; large vendor driver surface; hardening hand-merged into a vendor
    kernel with an intentional KMI deviation.
  - For the 2026-09-27 build (prepared, not yet built): no protocol modules
    without product use (CAN, 802.15.4/6LoWPAN, the kernel NFC socket family,
    PPTP/L2TP, the in-kernel Bluetooth stack); no SELinux development mode;
    dmesg root-only.
  - debugfs built in and mountable as in the 2026-09-26 build; KPROBES on,
    owner-accepted, as the USB controller glue's hooks use kretprobes (both
    under [Decision record](#decision-record)).
  - USB host-class, debug and trace drivers still load.
  - TIPC re-enabled (GrapheneOS disables it) for Qualcomm's mobile-data stack,
    as a module without network bearers or crypto; under enforcing SELinux no
    user-installed or privileged app reaches it (sockets only for two radio
    daemons, configuration only for system components with the network-admin
    capability, including the network stack module).
  - Confidentiality lockdown also disables kernel tracing and kernel-memory
    reads by BPF programs, so Android's per-app CPU time accounting (per-app CPU
    use in Battery usage) and the memory-event OOM listener do not start. The
    lockdown level (confidentiality or integrity) is open.
  - Every app reaches the kernel's userfaultfd code (ART's garbage collector
    needs it), but apps are forced into user-mode-only mode
    (`vm.unprivileged_userfaultfd=0`, `/dev/userfaultfd` root-only and
    SELinux-denied), so they cannot freeze the kernel mid-copy to widen race
    windows; bugs there remain ordinary kernel surface, fixed through 6.1 LTS
    updates.
- **Validation:** kernel build (FP6-041), debug exposure and component removal
  (FP6-060, FP6-061), enforcing runs (no owning task yet).
- **Status:** module reduction and production configuration built for the
  2026-09-27 build (kernel check 2026-09-27: configuration matches policy, no
  loaded module depends on a removed one), not phone-tested; the 2026-09-26
  build runs enforcing. Observed (bring-up): lockdown confidentiality active;
  tracing empty.

#### Camera, microphone, sensor streams, device integrity

- **Threat:** malicious app or remote content reaching closed vendor code
  through a platform service: camera, media, display, audio, sensors, GNSS, NFC
  and Bluetooth HAL interfaces; same-process GPU libraries; persist and vendor
  data files parsed by closed code.
- **Protection:** per-file allowlisted, hash-pinned stock selection; SELinux
  grants bound to each service domain; a closed HAL's internal endpoints
  reachable only from its own process (audio since the 2026-09-26 build); fewer
  service users, groups and capabilities; tight device-node permissions;
  Qualcomm diagnostics, telemetry and factory services excluded; layers replaced
  by source builds over time.
- **Remaining:** large closed parsers (camera about 185 MB) without MTE and
  mostly without seccomp; closed performance and thermal daemons as root;
  Android 14 ABI vendor code on Android 17; per-file purpose partly generic;
  closed code updated only through Fairphone stock releases.
- **Validation:** debug exposure and component removal (FP6-060, FP6-061),
  closure gate (FP6-208), camera (FP6-045, FP6-203), audio (FP6-044, FP6-205),
  hardware media integration and enforcing runs (no owning task yet).
- **Status:** Bring-up (not qualified): selection reviewed per subsystem,
  running permissive; enforcing untested.

#### Persistent hardware identifiers

- **Threat:** any app, via system properties, persist files, sysfs and logs.
- **Protection:** vendor-internal property types with no read grant; narrowed
  sysfs labels; random Bluetooth address per install (intended); Wi-Fi MAC
  randomization; traceability services excluded; boot parameters handed to user
  space logged by name only (for the 2026-09-27 build, not yet built); enforcing
  SELinux.
- **Remaining:** readable on any permissive build; the Bluetooth address path in
  use is unrecorded (the HAL tries a factory address first) and the address
  appears in logs and bug reports; the stock camera HAL logs camera module
  serial numbers at every start; the SoC serial (sysfs `soc0/serial_number`) is
  readable by 16 system and vendor domains through platform sysfs read grants
  and imported Qualcomm rules (no app domain; the 2026-09-26 build's policy
  gives the camera HAL and nicmd only the public SoC id); the factory Wi-Fi MAC
  in persist is not read under enforcing, so the driver falls back to the chip's
  own MAC or one derived from its serial; Wi-Fi MAC randomization unverified; a
  flash dump reveals persist data.
- **Validation:** enforcing runs and an identifier probe test (no owning task
  yet), Wi-Fi and Bluetooth bring-up (FP6-043).
- **Status:** Observed gap (bring-up, permissive): some hardware serials exposed
  as system properties; enforcing denial not yet shown.

#### Secondary-profile data

- **Threat:** another user or profile, via user switch, stopped and running
  profiles, unified vs separate challenge.
- **Protection:** the same credential policy on every independent challenge
  through a real service boundary; CE/DE separation; session end is not
  deletion.
- **Remaining:** unified-challenge profiles follow platform semantics, no
  invented independent key; a stopped session holds data until deleted.
- **Validation:** encryption and hardening validation (FP6-046),
  credential-policy enforcement (FP6-071).
- **Status:** Assumption.

### DiamaneOS-added features

#### Device integrity and the data each feature handles

- **Threat:** remote content, malicious list or network source, nearby BLE
  device or malicious update file, via the DNS filter (network bytes, downloaded
  lists), share-time metadata stripping (image parsers), tracker alerts (BLE
  payloads), offline SD OTA (file input), per-app routing and battery controls
  (privileged hooks).
- **Protection:** unprivileged, isolated, resource-bounded parsing workers;
  scoped URI grants; signed simple list formats; offline OTA uses the online
  update signature checks; least-privilege hooks; optional features off by
  default where the plan says so.
- **Remaining:** each feature adds code and privilege; platform image APIs can
  invoke native parsers; none is implemented yet.
- **Validation:** DNS filter (FP6-078, FP6-079), per-app controls (FP6-080),
  dashboard (FP6-081), tracker alerts (FP6-083), offline SD OTA (FP6-086),
  metadata stripping (FP6-087), battery (FP6-074, FP6-075).
- **Status:** Assumption.

### Proximity attackers

#### Device integrity, paired-device data, Bluetooth address

- **Threat:** BR/EDR or LE attacker, or tracker, via closed controller firmware,
  the closed stock Bluetooth HCI HAL and the GrapheneOS host stack.
- **Protection:** GrapheneOS host stack; Bluetooth off by default; consent-gated
  profiles; SIM access profile off; random per-install address (intended);
  closed HAL confined (no network, persist write or diag); unknown-tracker
  alerts (v1).
- **Remaining:** controller firmware and HAL unpatchable by the project; no MTE;
  live address path unverified and the address appears in logs; classic address
  stable per install; tracker alerts not built.
- **Validation:** Bluetooth and audio bring-up (FP6-043, FP6-044), debug
  exposure and component removal (FP6-060, FP6-061).
- **Status:** Bring-up (not qualified): pairing and audio work; security
  properties and enforcing untested.

#### SIM applets, presence, NFC services

- **Threat:** contactless reader (also against a locked, screen-off or
  switched-off phone) or malicious tag, via NFC reader and listen modes and
  controller-to-SIM routing.
- **Protection:** default route pinned to the host (no SIM routing unless an app
  registers it); no UICC or embedded secure element features declared;
  controller firmware file under verified boot once locked.
- **Remaining:** NFC on by default (decision pending); registered SIM routes
  reachable while locked or off; secure-NFC support unverified; closed
  controller firmware, which the HAL can also update.
- **Validation:** NFC bring-up tests (FP6-045), eSIM/SIM (FP6-089).
- **Status:** Bring-up (not qualified): reader works; routing tests not run.

#### Wi-Fi MAC (tracking), device integrity

- **Threat:** passive Wi-Fi observer, malicious access point or LAN peer, via
  probe requests, association, Wi-Fi driver and firmware, and wake-on-LAN.
- **Protection:** station-only features exposed to Android (no hotspot, Wi-Fi
  Direct or Aware); source-built Wi-Fi HAL and driver; MAC randomization.
- **Remaining:** MAC randomization unverified on this HAL and firmware; closed
  Wi-Fi firmware on the over-the-air path; a LAN peer can wake the device with a
  magic packet; on Wi-Fi networks used for Android Auto the device sends a DHCP
  hostname derived from the device name (inherited default).
- **Validation:** connectivity bring-up (FP6-043).
- **Status:** Assumption: unverified.

#### Locked-device data, kernel integrity

- **Threat:** malicious USB device or host, forensic tool or malicious charger,
  via USB-C data lines (host and gadget roles), USB descriptors, USB PD and
  charger firmware.
- **Protection (intended):** GrapheneOS USB-C port control (charging-only when
  locked, deny new USB devices); standard gadget functions only; debug USB
  functions removed from user builds; reduced USB driver set.
- **Remaining:** port control not wired up on the FP6; the deny-new-USB hook is
  in the merged kernel source, but whether the built kernel exposes it is
  unchecked; no evidence of a hardware USB data-line cutoff; broad USB host
  drivers loaded; the USB descriptor carries the device serial; closed charger
  firmware.
- **Validation:** debug exposure audit (FP6-060), USB modes (FP6-045), hardware
  USB data disable (no owning task yet).
- **Status:** Observed gap: port control absent. Bring-up (not qualified):
  userspace gadget configuration reviewed; debug USB functions still to be
  removed from user builds.

### Physical access to a powered-on, locked device (AFU)

#### AFU user data

- **Threat:** forensic tools, via USB, the lock screen, and firmware download
  and dump modes.
- **Protection:** inactivity auto-reboot (inherited); USB port control; the OS
  refuses firmware-download and dump reboots; panic RAM dumps off.
- **Remaining:** port control not wired up; download-reboot refusal not
  implemented; the kernel keeps a minidump on panic, retrieval path unverified;
  the early-boot setting that also turns dump mode off needs a policy grant
  before enforcing; EDL stays reachable with physical access and a signed
  programmer; no forensic-proof claim.
- **Validation:** debug exposure audit (FP6-060), credential policy (FP6-071),
  scheduled reboot (FP6-085).
- **Status:** Observed gap: port control absent; download-reboot refusal not
  implemented. Observed (bring-up): panic dump mode off by kernel default.

#### Previous boot's kernel log and event logs

- **Threat:** root or system code after a reboot, anyone with USB debugging on
  and an authorized computer, or a forensic examiner, via a RAM region kept
  across a soft reboot (pstore/ramoops), its copy in the system's crash-report
  store, and Qualcomm minidumps.
- **Protection:** unreadable by apps; readable by the log group (which adb's
  shell user has) and a few system domains, the crash-report copy through
  dumpsys; identifier-carrying boot parameters logged by name only; dmesg
  root-only from boot; on release builds init removes all permissions from the
  event-log device, so only DAC-override holders could write it (debuggable
  builds keep it writable).
- **Remaining:** the next boot copies the kernel part to device-encrypted
  storage, readable by the system before first unlock (moving it to
  credential-encrypted storage at first unlock and clearing the RAM copy is
  open); the RAM copy stays until overwritten; Qualcomm's minidump driver
  registers the log areas, so a collected minidump carries them. Reboots and
  kernel crashes reset cold, powering RAM off, so in practice nothing survives;
  a one-off warm reboot on the 2026-09-27 build kept both logs (the bootloader
  does not clear RAM); cold reboots stay the default (owner decision
  2026-09-27). Every domain holds the platform's write grant on the event-log
  device, so its file mode is the only control.
- **Validation:** debug exposure audit (FP6-060).
- **Status:** Assumption: region and release-build rule built for the 2026-09-27
  build with GrapheneOS's Pixel layout, not phone-tested (that build is
  debuggable, so the release rule is inactive); no earlier build registered the
  region. Kernel check (2026-09-27): placed at boot without a fixed address; the
  saved console keeps only notice-level and more severe messages (the console
  log level), not the full log. Observed on the 2026-09-27 build: same address
  every boot; pstore empty after a normal reboot; after a warm reboot both logs
  present, readable by the shell user by exact name (directory listing denied),
  the system's copy only after first unlock; the boot-parameter value in none of
  them.

#### AFU unlock, auth-bound keys

- **Threat:** lifted or spoofed fingerprint, via the side fingerprint sensor.
- **Protection:** strong authentication after reboot and timeouts; lockout in
  the trusted app; the vendor debug service never registered (kept inside the
  HAL process) and unreachable by policy; biometric class never claimed above
  what testing shows.
- **Remaining:** the DiamaneOS fingerprint HAL declares Class 3 (strong) before
  any spoof testing: a declaration, not a measurement. A release must measure
  spoof resistance or declare a lower class; until spoof testing passes, the
  fingerprint is not treated as strong authentication.
- **Validation:** fingerprint bring-up (FP6-045), credential validation
  (FP6-046), spoof testing (no owning task yet).
- **Status:** Observed gap: class declared, not measured. Bring-up (not
  qualified): unlock works; the first enforcing boot showed the module needs its
  debug service registered, which the HAL now answers in-process (in the source;
  not yet in a tested build).

#### AFU data under coercion or seizure

- **Threat:** coercer, or seizure then extraction, via the lock screen and
  buttons.
- **Protection:** duress credential and wipe (inherited), panic reboot to BFU,
  scheduled reboot.
- **Remaining:** duress key destruction relies on TEE key deletion and flash
  erase, not a secure element; eSIM erase depends on the LPA; eSIM profiles may
  survive.
- **Validation:** duress test with synthetic data (FP6-046), eSIM erase
  (FP6-089), panic-to-BFU (FP6-082), scheduled reboot (FP6-085).
- **Status:** Assumption: inherited code present, untested on the FP6; panic and
  scheduled reboot not implemented.

### Physical access to a powered-off device (BFU) and data outside userdata

#### BFU user data

- **Threat:** opportunistic access or flash readout (EDL, chip-off), via flash
  contents and the TEE.
- **Protection:** FBE with metadata encryption and hardware-wrapped keys;
  mandatory 6 to 8 word CSPRNG passphrase for the owner and every independent
  user or profile; no weak-credential path; TEE Gatekeeper backoff (expected).
- **Remaining:** no StrongBox or Weaver, so throttling rests on the TEE, a
  larger surface with a public history of key-extraction attacks; backoff timing
  and survival across reboot and image restore unverified; passphrase policy not
  implemented. The passphrase replaces no secure element and fixes no TEE,
  firmware, AFU or credential-capture weakness.
- **Validation:** encryption and hardening validation (FP6-046), passphrase
  onboarding (FP6-070), credential policy (FP6-071).
- **Status:** Observed gap: passphrase not implemented (the development device
  uses a PIN); throttling unverified. Observed (bring-up): FBE v2 with wrapped
  keys and metadata encryption running.

#### Device-persistent data outside userdata

- **Threat:** physical attacker with flash access, or an app on a permissive
  build, via the persist partition, modem file systems, eUICC and hypervisor VM
  storage.
- **Protection:** SELinux labels; unsafe stock permissions closed; reset residue
  documented.
- **Remaining:** factory reset clears none of these; hardware identifiers and
  calibration readable from a flash dump; modem and GNSS state persists.
- **Validation:** debug exposure audit (FP6-060), eSIM erase (FP6-089).
- **Status:** Observed gap: residue inventory not written; some stock
  permissions still to be tightened.

#### Removable media

- **Threat:** anyone who takes the microSD card or USB storage.
- **Protection:** encrypted microSD (v1). **Remaining:** not encrypted today.
- **Validation:** encrypted microSD design and workflow (FP6-076, FP6-077).
- **Status:** Assumption.

### Boot chain, firmware and trusted execution

#### OS integrity on a released, locked build

- **Threat:** write access to partitions, via the boot chain, OTA, recovery,
  sideload and the inactive A/B slot.
- **Protection:** locked bootloader on a custom AVB root, vbmeta flags 0;
  SHA-256 hashtrees; rollback indexes set only by signed releases; signed full
  and incremental OTAs from one pipeline; recovery accepts release keys only;
  relock locks both lock states. Public test builds are signed with the public
  AOSP/AVB test keys, carry test-keys in the fingerprint and a never-lock
  record, and `flash-steps` prints the never-lock rule first.
- **Remaining:** yellow boot (never green); downgrade-brick risk; Fairphone
  unlock service dependency; firmware not in OTAs; refusal of slot changes while
  locked untested. A test-key build must stay unlocked, since anyone can sign
  images with the public test keys. The `flash-steps` wipe writes Fairphone's
  factory FRP image (clearing factory reset protection and keeping OEM unlocking
  allowed) and zeros misc, as Fairphone's own factory flash does; `flash-steps`
  prints commands only for an image set whose verify report matches it and
  passed.
- **Validation:** custom-key relock (FP6-050), OTA install and
  interrupted-update tests, signing verifier (FP6-035).
- **Status:** Observed gap (bring-up images): SHA-1 hashtrees, release-style
  rollback indexes and a public test key contradict three listed mitigations.
  Assumption: locked behaviour untested. Observed (bring-up): AVB chain built
  and parsed.

#### Firmware security

- **Threat:** exploits of already fixed bugs in XBL, TrustZone, hypervisor,
  modem, DSP, Wi-Fi and Bluetooth firmware, or peripheral controller firmware
  files in `/vendor/firmware`.
- **Protection:** per-image firmware inventory with hashes
  ([`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json));
  firmware in OTAs from verified Fairphone releases (designed in
  [FIRMWARE.md](FIRMWARE.md), not implemented); separate dated patch levels for
  platform, kernel, vendor and firmware; controller firmware files are exact,
  hash-pinned stock files under verified boot once locked.
- **Remaining:** the project cannot build or sign firmware; no update path yet;
  firmware lags ASB; Gunyah and its trusted VMs are closed. As on stock, the NFC
  HAL may update its controller from its firmware file. Since the 2026-09-27
  build the touch driver writes its file to the touch controller whenever
  versions differ or the controller's version is unreadable, checking only the
  header, so verified boot is what keeps that file authentic.
- **Validation:** firmware review and update path (FP6-206), stock input
  verification (FP6-040).
- **Status:** Observed gap: firmware stays at the last flashed stock release.

#### Keys, Gatekeeper throttling, fingerprint templates

- **Threat:** compromised system or HAL process, or a malicious app reaching TEE
  clients, via the TEE driver and client services, TEE listener daemon and
  fingerprint HAL.
- **Protection:** TEE access only for named HAL domains; unused userspace TEE
  proxy services removed; RPMB access limited; a wipe asks the TEE to delete all
  old keys.
- **Remaining:** closed TEE with a public history of key-extraction bugs; no
  StrongBox or Weaver; KeyMint key deletion on wipe (rollback resistance) set
  since the 2026-09-26 build, unverified.
- **Validation:** TEE and credential validation (FP6-046, FP6-050), attestation
  limits (FP6-065, FP6-106), security status reporting (FP6-073).
- **Status:** Observed gap (builds before the 2026-09-26 build): an unused
  userspace TEE proxy ran; removed in the source, not yet in a tested build.
  Bring-up (not qualified): services run; enforcing and throttling persistence
  unverified.

### Supply chain, signing and development process

#### Source and build outputs

- **Threat:** compromised upstream host or project, build dependency or build
  host, via source fetch, prebuilt toolchains, the build account and the image
  tools built from source.
- **Protection:**
  - Signed GrapheneOS tags checked before sync; immutable commit pins; signed
    downstream commits verified before build (in the private builds); a full
    source preflight before and after every Android build.
  - Generated vendor and kernel trees are installed only when the recipe digests
    their generations recorded (vendor provenance; the kernel run's preparation,
    packaging recipe and policy reports) equal the checkout's recipes, and the
    preflight accepts them only while a descriptor binds them to the environment
    file and those digests and every file matches its inventory.
  - Network-denied compilation: the public build commands compile in an
    unprivileged network namespace, with agent and bus socket variables removed
    from the compile environment.
  - Image tools built from the pinned source with their hashes recorded; one
    build/make fork, whose single change keeps the device's boot header fields
    when images are rebuilt, pinned like the other forks; every build recorded
    in `build.json`; two independent builds with final-content comparison.
- **Remaining:**
  - Qualcomm/CodeLinaro and Fairphone sources and toolchains carry no upstream
    signatures; common inputs are common-mode.
  - The public build runs as an ordinary user on an unmanaged host, without the
    separate build account, root-owned tools checkout or service sandbox the
    reference builder keeps.
  - The network namespace blocks IP networking only: Unix sockets in the
    filesystem stay reachable, so a socket to a service with network access (a
    container daemon, for example) is a way out, and the build can read whatever
    the user can.
  - Host packages are recorded, not pinned; a build with `--allow-network`
    compiles with network access (recorded for the kernel, vendor and Android
    steps); the kernel workspace link adaptation is not among the recorded
    recipe digests; a modified tools checkout is recorded as not reproducible.
  - The tools checkout is only as trustworthy as the maintainer keys used to
    check it, which are not published yet.
- **Validation:** reproducible environment, dual build, release comparison.
- **Status:** Observed gap (FP6 path): every FP6 build so far compiled with
  network available, on one host, with private scripts. Designed and
  unit-tested, not yet run on a build: the public build commands with
  network-off compilation, the full preflight and bound generated inputs (an
  open project review item). Recorded (generic target): a network-denied build
  path.

#### Closed vendor inputs

- **Threat:** compromised vendor download or support page, or a network attacker
  at first download, via the stock factory package.
- **Protection:** exact archive hash pinned everywhere; the public build
  downloads it only over HTTPS from Fairphone's official host and uses it only
  if size and SHA-256 match; per-file hash, component and purpose; fail-closed
  extraction; every name-loaded library declared (required; no build check yet).
- **Remaining:** the archive and its published hash come from the same vendor
  web estate, and a new package can be selected before Fairphone publishes its
  hash (FP6.QREL.16.111.0 was); no vendor signature check yet; per-file purpose
  partly generic; name-loaded libraries declared for few files (a missing one
  broke a bring-up boot).
- **Validation:** stock input verification (FP6-040), firmware review (FP6-206),
  closure gate (FP6-208).
- **Status:** Observed gap: per-file necessity and name-loaded dependencies
  incomplete. Bring-up (not qualified): extraction fail-closed and hash-bound.

#### Release signing keys

- **Threat:** key theft or misuse, or a targeted signed build, via the signer
  host, hardware tokens, signing media and maintainers.
- **Protection:** offline signer, token roles, 3-of-5 recovery, public release
  log before distribution.
- **Remaining:** single signing authority; the AVB root cannot be revoked
  without unlock and wipe; log inclusion does not mean benign; no signing role
  for kernel modules yet.
- **Validation:** signing qualification (FP6-035, FP6-036).
- **Status:** Designed: generic dummy-key qualification only; no FP6 signing
  profile yet.

#### Repositories, domain, install page

- **Threat:** phishing, compromised workstation or assistant session, or
  malicious contribution, via code hosting, registrar, DNS, mail and developer
  keys.
- **Protection:** hardware MFA; separate signing and authentication keys;
  protected branches; human review of security-relevant changes; least-privilege
  assistant access; no untrusted code on release hosts.
- **Remaining:** one-person review capacity.
- **Validation:** account and workstation hardening, branch protection.
- **Status:** Assumption: not yet qualified.

#### Exposure window for known bugs

- **Threat:** n-day exploitation of platform, kernel, vendor code, firmware or
  browser.
- **Protection:** monthly releases within 7 days of GrapheneOS; urgent fixes
  within 48 hours; per-layer patch levels shown.
- **Remaining:** vendor and firmware fixes depend on Fairphone and Qualcomm and
  lag; open-source libraries bundled in closed vendor files miss platform fixes.
- **Validation:** update cadence, patch dashboard.
- **Status:** Observed gap: bring-up platform and vendor patch levels lag
  available upstream releases; no automated upstream intake yet.

#### Shell fork rebase lag: timely GrapheneOS security fixes under the Tally shell

- **Threat:** n-day exploitation of a fix not yet shipped or lost while
  rebasing, via DiamaneOS's Tally changes to frameworks/base (SystemUI first; WM
  Shell, SettingsLib and core resources later) carried onto each GrapheneOS
  release.
- **Protection:** per-area commit series, mostly new files wired in through
  SystemUI's dependency injection, so upstream files change little; Tally code
  behind one build-time flag (fixed read-only, on in DiamaneOS's release
  config), so an area can be dropped or the flag turned off to ship a GrapheneOS
  security release on time; the planned release gate requires the shipped fork
  revision to contain the adopted GrapheneOS release's frameworks/base revision.
- **Remaining:** the flag helps only while Tally code still builds on the new
  release, and switches a resource off only where the resource names it
  (SystemUI, WM Shell (step 5) and the shared clock library read it). If a later
  GrapheneOS release moves WM Shell's activity transitions to its new transition
  planner, Tally's page motion falls back to stock until hooked there. Keyguard,
  privacy-indicator and biometric conflicts need careful manual merges;
  frameworks/base is not in the tools' fork tracker yet; one-person review
  capacity. The bouncer reads Tally token resources when built, so broken tokens
  would break PIN and password entry: every build is tested by unlocking with a
  PIN and a password, flag on. The foundation's colour fixes (SystemUI's
  "already applied" check, the Sodium fallback) also apply with the flag off;
  they change colours only, so the flag-off build is GrapheneOS's in behaviour,
  not byte for byte.
- **Validation:** rebase and build of each GrapheneOS release with the flag on
  and off (FP6-211); update cadence, patch dashboard.
- **Status:** Assumption: the flag and its release-config value are on local
  branches, not built.

#### Install-time trust

- **Threat:** impersonating site or mirror, or malicious installer, via browser
  download, WebUSB and CLI installers and the first AVB key enrolment.
- **Protection:** out-of-band key fingerprint in 3 independent places, compared
  with the bootloader-displayed fingerprint before lock; the
  `get_unlock_ability=1` gate refuses on 0; downloads verified before flash;
  relock never enrols a public test key.
- **Remaining:** first install has no prior key; a page-controlled checkbox is
  not evidence; a compromised origin can substitute installer and key; stock
  restore inputs are authenticated by the vendor's published hash, not a
  signature.
- **Validation:** recovery preflight, unlock and stock restoration, CLI and
  WebUSB installers, independent verification guidance.
- **Status:** Assumption.

### Everyday use

#### Correct user decisions

- **Threat:** user error, confusing warnings or inaccessible flows, in setup,
  permissions, updates, backup and restore, and recovery.
- **Protection:** plain outcomes, progressive disclosure, scoped grants,
  explicit destructive confirmations; a first-boot assistive path before setup
  needs it; fluent review of critical wording or a disclosed source-language
  fallback; honest "unsupported" states instead of hidden gaps; a shown control
  must work.
- **Remaining:** bootloader and firmware screens may stay inaccessible; English
  fallback alone is not usability proof.
- **Validation:** interface design, localization and accessibility, passphrase
  onboarding, accessible journey validation.
- **Status:** Assumption.

#### Typed text and personal words

- **Threat:** someone with the device before first unlock, a backup copy or a
  view of the screen, via the keyboard's learned, personal-dictionary and
  contact word lists, backups, and fields that ask not to be learned from
  (incognito).
- **Protection (DiamaneOS keyboard fork):** learned, personal and contact words
  only in credential-encrypted storage, loaded after unlock; old
  device-encrypted copies deleted at first dictionary setup or first unlock (an
  update never sends MY_PACKAGE_REPLACED); backups off; no-learning and password
  fields never learned from or shown learned words; no typed or personal data in
  logs; unused network, account and sync permissions and dormant entry points
  removed; no network permission (already true).
- **Remaining:** until the fork ships, the inherited keyboard keeps these words
  in storage available before first unlock, allows them in backups and ignores
  the no-learning flag. After it, the recent-emoji list stays readable before
  first unlock; on two 2026-09-30 test builds old copies stayed on the update's
  first boot until the keyboard started or the owner unlocked (-150, fixed in a
  later 2026-09-30 test build by deleting them at LOCKED_BOOT_COMPLETED).
- **Validation:** keyboard fork tests and phone checks before merge (no owning
  task yet).
- **Status:** Designed; the fix is on a local keyboard branch in two 2026-09-30
  test builds; its phone check was inconclusive (learned words are off by
  default, password fields never compose).

#### Privacy indicators and disclosures under the Tally shell

Protects: knowing when the camera, microphone or location is in use or the
screen is captured, which app is asking, whether the device is managed or on a
VPN, and what a locked phone shows.

- **Threat:** an app using a sensor or capturing the screen while the user reads
  its interface or while fullscreen; an app asking for a biometric or
  credential; someone looking at a locked phone. Surfaces, all restyled by the
  Tally shell (plan FP6-211): status bar privacy chip and lamps, capture chips,
  fullscreen dots, lens ring, lock strip, indication line, Quick Settings'
  security footer, notification rows, toasts, biometric prompt.
- **Protection:**
  - *Data:* Tally changes drawing, never data or presence; the platform's
    privacy and app-op tracking is the only source, unfiltered by foreground
    app, quiet or decluttered views or the lock strip. Camera, microphone,
    location (whenever the platform shows it) and screen sharing show in every
    state (app in front, immersive and fullscreen, lock screen, shade, Do Not
    Disturb, battery saver). Stock SystemUI keeps the indicator timings (5 s and
    10 s holds) and chip height and icon size in sp, so chips grow with text
    size. Lamps appear with no animation delay; only disappearance may animate.
    The dot's colour follows the area under it without ever delaying the dot.
    Tally lock views draw empty or partial states without failing: a SystemUI
    crash removes every privacy indicator until restart.
  - *Drawing:* only colours with a 2 dp edge, padding, radius, a 16 dp dot drawn
    as a lamp 16 dp from top and side (status icons keep clear), one shade chip
    for every sensor, screen capture alone in the capture colour, and
    disappearance on the fill spring; added are the lens ring and the location
    lamp by the lens. Tally tokens and overlays never set stock indicator
    resources. Indicator colours are fixed SystemUI resources outside the
    palette, at least 3:1 on light and dark bars, the variant taken from the
    area underneath (status icons' tint over the app, lock screen or shade; dark
    under the bouncer, a SystemUI dialog, the dozing screen or any area not
    known to be light; never from night mode). Every Tally status bar indicator
    draws its 2 dp edge in every state; where no edge can be drawn or no area is
    known, it takes the variant that keeps 3:1 alone. The overlay check refuses
    product overlays of SystemUI privacy-indicator resources and of every Tally
    token or indicator resource in SystemUI (only a device overlay sets the lens
    config). The prototype follows these rules, checked in every configuration
    (stock sp sizes at 100, 150 and 200 % text; area colours with a 2 dp edge,
    so dims over the status bar keep 3:1).
  - *Touch:* as in stock, the shade's privacy chip opens the stock privacy
    dialog and a capture chip its stop dialog; the status bar chip and dot take
    no tap. The lens ring and lamp take no touch, so never block the status bar
    or the app beneath.
  - *Lens ring and lamp:* on active pixels at least 2 dp outside the display
    cutout, above apps and the lock screen. The ring lights while an app uses a
    camera once the front camera has opened (as the camera service reports) and
    counts the front camera as used for the longest privacy hold (10 s) after it
    closes, keeping the camera privacy item's hold whatever order close and item
    arrive in; a back-camera switch ends it after that window. A camera with
    unreadable facing counts as front; without open and close reports any camera
    lights it. Chip and dot show every camera. On the FP6 the device overlay
    declares the camera hole as measured on the panel (a 37 px circle), so the
    ring is a true ring 2 dp outside it and the lamp sits level with it; the
    layout rectangle keeps status bar and app insets at 110 px and spans the
    hole's full width, so no status bar content sits under it.
  - *Lock strip:* while locked, only "Camera in use" or "Microphone in use"
    (stock shows the untyped dot; naming the sensor is deliberate). Sensor items
    from the privacy chip's own source (holds included), others from SystemUI's
    existing controllers; no touch; no app, network or device names. An alarm
    only if still to come and within 12 hours (GrapheneOS's condition; stock's
    12 hours), and only while the current user's lock screen shows
    notifications, which is off unless set (GrapheneOS's smartspace line then
    shows nothing either). Bluetooth only while a device is connected, as
    stock's status bar icon; battery percentage only while charging or with the
    user's setting on. Hidden on the always-on display.
  - *Always-on strip:* only while dozing, what stock's always-on date line
    showed: next alarm within 12 hours, a Do Not Disturb icon, the playing
    media's title (artist only beside the media notification's icon). Media only
    while the current user's lock screen shows notifications and "Show media on
    lock screen" is on, with GrapheneOS's defaults, as GrapheneOS's always-on
    line. Never lamps, sensors, network or device names, app icons or battery;
    nothing while lock-screen notifications are off.
  - *Clock and indication line:* the Tally lock clock and the date line under
    other clocks show only time and date; that date line only while the lock
    screen shows notifications (as GrapheneOS's smartspace line). The indication
    line keeps stock's messages (strong-authentication, lockdown, administrator,
    trust-agent), shows fingerprint failure and help in the error colour, and
    adds a 3-second side-sensor hint in stock's words while fingerprint unlock
    is allowed, on at most five wakes per user. The count (0 to 5) lives only in
    SystemUI's device-protected storage, not backed up, removed with the user;
    read at screen-on (also before first unlock), written only when the hint
    shows; it never affects unlocking, and if unreadable the hint stays away.
  - *Footer, notifications, prompts, toasts:* the security footer (managed
    device, VPN, monitoring certificate) and VPN icon show whenever stock shows
    them; lock-screen notifications follow the user's lock-screen settings and
    work-profile redaction; notification rows keep the app's label and icon, the
    biometric prompt its name and icon; toasts change only position and shape,
    keeping admission rules, app attribution and GrapheneOS's secure-paste
    notices, with no new kind of toast on the secure lock screen.
- **Remaining:** a new layout could cover or clip an indicator without touching
  its data (only review and phone tests catch it); a preinstalled overlay
  outside the checked roots could change stock resources; SystemUI cannot see a
  scrim an app draws inside its own window (as opposed to a dimming window),
  over whose mid-tone a light-variant indicator can fall to about 2.3:1 with its
  white edge; the status bar and capture chips sit in the status bar window,
  under the shade, lock screen and SystemUI dialogs, while the dot, lens ring
  and lamp stay above everything.
- **Validation:** shell security review at roadmap steps 3.1 and 3.3 (FP6-211);
  prototype privacy checks at 100, 150 and 200 % text; phone test of every
  listed state before merge.
- **Status:** Assumption: step 3.1's SystemUI indicators and step 3.3's lock
  screen are on the local tally-status and tally-lock branches, compiled only
  against stubs of the pinned signatures with resources linked by aapt2; not
  built, not run on the phone.

#### Files archive handling (browse, extract, create)

- **Threat:** a malicious archive the user opens or extracts, or an app holding
  MANAGE_DOCUMENTS, via ArchivesProvider (exported, MANAGE_DOCUMENTS) parsing
  untrusted ZIP, 7z and TAR with AOSP commons-compress 1.19, and DocumentsUI
  extraction (UnpackJob).
- **Protection:** entry paths rooted, `.` and `..` collapsed
  (Archive.getEntryPath); extraction through the storage framework, which
  re-roots and sanitises each name; symlink and hardlink entries written as
  ordinary files, never followed; free space checked against declared sizes,
  each file capped at its declared size, partial files removed; parser
  exceptions end in a failed load, never a crash; the new archive code (zip_ng)
  adds read-path size and CRC checks, so it is on (-142).
- **Remaining:** commons-compress 1.19 is old (a debug log line on a ZIP 0x0017
  extra field, -143; a malformed TAR header throws a caught
  NullPointerException); browsing parses archives with or without zip_ng; a
  large honest archive that fits the free space still takes time to extract
  (cancellable); no recursive extraction.
- **Validation:** host review and fuzzing, 2026-09-30 (20 million path inputs,
  about 154 million mutated archives: no crash, hang or escape); phone
  extraction and picker tests with the next build.
- **Status:** Designed and fuzzed on the host with the pinned library; zip_ng on
  in vendor_diamaneos local `tally` (merge 770d976 of `files-flags`), in two
  2026-09-30 test builds. Files trash stays off until its wording (-140) and
  phone tests.

## FP6 source and firmware boundary

- **Sources:** kernel and platform HAL sources follow Qualcomm's CodeLinaro
  release `LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS
  `kernel_common-6.1` release merged into the vendor kernel; FP6 device trees,
  the Samsung NFC sources and stock images come from Fairphone. Source
  availability does not prove that GrapheneOS or Pixel patches apply, that
  modules meet the selected KMI/UAPI, or that binaries reproduce stock.
- **Closed components:** the camera, radio and IMS, secure-world, sensor, DRM
  and much of the graphics and media runtime depend on proprietary userspace or
  firmware. The bring-up selection holds 720 closed stock files (about 369 MB):
  camera 213, radio and IMS 150, sensors 84, display and GPU 82, audio 41,
  credentials 36, power and thermal 29, remote-processor services 28, plus
  smaller Bluetooth, NFC, GNSS and fingerprint sets. Each closed layer is to be
  replaced by a source build where one exists.
- **Stock input:** `FP6.QREL.16.111.0` for the EU (`FP6.QREL.16.100.0` until
  2026-09-30); its verified factory package is the authoritative extraction
  input. Vendor generation uses an explicit per-file recipe (partition, path,
  hash, component, purpose); purposes are partly generic and consumer bindings
  partial. The generator excludes per-device identity, modem NV/EFS,
  calibration, provisioning, DRM, attestation, keystore and userdata material.
- **Removals and replacements:** a removal must remove the complete reachable
  service and declaration path and pass subsystem tests; an open-source
  replacement needs exact licence compliance and must preserve security and
  capability (a software fallback is not automatically safer than proprietary
  hardware-backed code).
- **Firmware:** hashed per image in
  [`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json),
  not yet delivered by DiamaneOS.
- **Licences and records:** imported or modified open-source code stays
  fail-closed on per-file licence, notice, attribution and corresponding-source
  obligations. Exact source revisions, interfaces and experiments are in
  [`config/fp6-sources.json`](../config/fp6-sources.json) and
  [`config/fp6-capabilities.json`](../config/fp6-capabilities.json).
- **Regions:** the intended US path needs a real US-region FP6 run by a second
  maintainer (availability not evidenced); `FP6.QREL.16.104.0` is a comparison
  and validation input, not an EU restore input. A shared release needs the
  EU/US partition, AVB, firmware, VINTF, init/policy and carrier-configuration
  delta recorded and the US device passing candidate hardware and telephony
  tests; until then EU is the supported target and US unverified.
- **Vendor compatibility:** the stock vendor declares VINTF target level 8 with
  vendor API 34; bring-up builds combine it with the Android 17 framework.
  Product assembly must pass `checkvintf` and applicable VTS checks without
  weakened enforcement or broad compatibility shims.

## Fresh-UI boundary

- Shared visual tokens and components carry no platform authority; credential,
  update, backup, network and content-processing authorities stay separate.
- Each nontrivial Settings, launcher or SystemUI change needs demonstrated
  benefit, an owner, measured rebase cost and regression checks.
- The owner approved a substantial shell rework on 2026-09-26 (lock screen,
  shade and Quick Settings, Home, Recents and app transitions, through
  Launcher3, SystemUI and WM Shell). Framework authorities are not replaced; no
  shared visual controller gains platform authority.

Every Tally shell commit (roadmap step 3, FP6-211) also keeps the indicator and
disclosure rules under [Privacy
indicators](#privacy-indicators-and-disclosures-under-the-tally-shell), the
rebase rule under [Shell fork rebase
lag](#shell-fork-rebase-lag-timely-grapheneos-security-fixes-under-the-tally-shell),
and the per-component rules in [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md):
lock screen and biometric prompt, transitions, Quick Settings and the shade,
Recents' Stop, Settings switches, Home and All apps, Settings search, Clock,
Calculator, recovery titles, motion, the Settings homepage, branding, and rules
learned from findings.

## Decision record

- **Scope:** daily use by privacy- and security-focused users; v1 scope as
  decided on 2026-09-26 (ten original entries after public v1).
- **Passphrase:** passphrase-first is mandatory now; PIN-optional only via a
  later explicit decision.
- **Claims:** no scores or unsupported parity claims; keep upstream attribution
  and a clear, separate project identity.
- **2G (2026-09-25):** 2G stays allowed by default (AOSP/GrapheneOS default);
  "2G network protection" and LTE-only are offered as opt-in hardening.
- **Cellular alerts (2026-09-25):** the plan's v1 item allows an honest
  "unsupported" state, which is the outcome on the current radio software; the
  limitation is recorded as known.
- **eSIM:** 2026-09-25: a maintained hardware-layer LPA with least privilege and
  hardening; OpenEUICC not used; candidate: the Qualcomm LPA. 2026-09-26:
  reasons in GrapheneOS os-issue-tracker #6275 and #2631; from the 2026-09-26
  build the LPA's eSIM service is on by default. 2026-09-27: it cannot list
  profiles on the FP6, so its service is off again from the 2026-09-27 build
  (stock leaves it on); eSIM follows GrapheneOS (installed profiles keep
  working); our own source LPA behind an off-by-default "eSIM support" switch is
  planned for its own build.
- **Build identity (2026-09-26):** build properties and fingerprint keep
  Fairphone's stock product identity, as GrapheneOS keeps Google's, after Google
  blocked the DiamaneOS-branded identity as an uncertified device; DiamaneOS
  stays the name people see.
- **Enforcing policy of the 2026-09-26 build:** the audio HAL may use QRTR
  sockets for DSP restart notifications (SELinux cannot limit which QMI service
  it reaches); nicmd may bind the ephemeral ports the modem reserves; the
  modem's study partition keeps its stock label; SystemUI cannot read the
  screen-decorations switch, so the camera/microphone privacy dot cannot be
  turned off over adb; the camera HAL's display-configuration hint stays denied.
- **userfaultfd and KPROBES:** 2026-09-26: the production kernel keeps
  CONFIG_USERFAULTFD for ART's garbage collector, as GKI, Pixel and GrapheneOS
  do, with the user-mode-only restriction, and turns KPROBES off (back only if
  something needs it). 2026-09-27: something does: the USB controller glue
  implements its controller hooks with kretprobes, so KPROBES stays on until
  those hooks are explicit calls; lockdown blocks probes from user space; the
  owner accepted this.
- **debugfs (2026-09-27):** stays as in the 2026-09-26 build, built in and
  mountable: user builds never mount it, debuggable builds until boot completes,
  SELinux governs access. In this kernel, turning mounts off also removes the
  in-kernel interface the display driver and recovery need; a kernel change
  keeping the interface but refusing mounts is the stricter, open option.
- **pstore/ramoops (2026-09-27):** on for bring-up builds with GrapheneOS's
  Pixel layout; reboots stay cold (the kernel default). A warm reboot would keep
  the saved logs but also all of RAM; not switching, since few kernel changes
  are still expected. Details under [Previous boot's kernel log and event
  logs](#previous-boots-kernel-log-and-event-logs).
- **VoLTE:** 2026-09-25: required, with the closed Qualcomm IMS stack under an
  explicit permission allowlist. 2026-09-26: the IMS data connection is to be
  brought up by DiamaneOS's own code (a small modem-facing service and a
  one-permission app) instead of Qualcomm's connectivity engine; closed code
  there is a fallback only. Replacing the IMS stack itself with source is plan
  direction, not a dated decision.
- **Call audio (2026-09-26):** the call-audio messenger keeps the one
  audio-routing permission it needs; replacing it with our own app is open. Then
  (also 2026-09-26): replaced, from the 2026-09-26 build, by DiamaneOS's own
  call-audio bridge with only the normal audio-settings permission; the
  audio-routing grant is gone.

Requirements added by the 2026-09-25 review (not separate decisions): a cellular
hardening control is offered only once verified to reach the modem, and a shown
one must not fail silently; the candidate LPA must meet the production TLS trust
rule before it ships.

## Architecture and verification review

Intended protections; device-dependent claims stay unverified until their named
validation produces evidence. Paths reviewed: remote (network observer, DNS and
captive portal, EU endpoint policy, in no build yet); remote media (remote
media, hardware codec service, video device nodes); malicious app (permission
and background listener, presets plus per-app routing and filtering; camera
permission, camera service and closed camera provider, with grants bound to its
domain, reduced groups and no network once SELinux is enforcing; flashlight
controls reach the closed provider without the camera permission, with simple
on/off and strength values only, as AOSP designs it); cellular (fake base
station, 2G fallback, the user's hardening choice, the modem; on a bring-up
build the choice did not reach the modem and the interface did not show the
failure); cross-profile and physical (secondary user or profile, user-switch
challenge, real service-boundary enforcement (FP6-046, FP6-071); AFU and BFU,
USB, EDL and lock screen, reboot to BFU and duress).

## Next validation

1. Enforcing SELinux runs per subsystem (camera, audio, telephony, sensors,
   Bluetooth, NFC, GNSS, fingerprint, media), denials recorded by domain.
2. User-build gate: `user` variant, not debuggable, ADB authenticated and off,
   no pre-trusted ADB keys, no ADB over the network, enforcing, no debug USB
   functions, trace sinks or debug modules, release keys only, no inherited
   GrapheneOS endpoint defaults, no test apps.
3. Cellular: the selected network mode reaches the modem on every subscription,
   including stored settings, and a failure is surfaced; null-cipher support
   state from the radio daemon; airplane-mode modem state.
4. USB port-control matrix: locked and unlocked, host and gadget roles, charging
   kept; the built kernel exposes deny-new-USB.
5. Firmware-download and dump reboots refused on a user build; a forced panic
   gives no download device; minidump retention checked.
6. Relock with a project dummy key (both lock states), rollback-index policy,
   SHA-256 hashtrees, inactive-slot downgrade check.
7. Gatekeeper backoff and its persistence across reboot and image restore;
   KeyMint key deletion; duress on synthetic data; eSIM erase.
8. NFC routing, Bluetooth address path, Wi-Fi MAC randomization, a GNSS fix with
   SUPL settings, runtime egress capture with SELinux enforcing.
9. Firmware inventory with per-partition hashes and an update path; stock AVB
   key verification.
10. FP6 signing profile and presigned-app inventory; second independent FP6
    build; the public build commands on a real host: network-denied FP6
    compilation, the bound generated inputs passing the full preflight, and the
    image checks on a real image set.
11. TEE KeyMint, attestation and reported security levels on the EU candidate;
    fingerprint spoof testing and the declared biometric class; regional matrix
    on an actual US candidate before claiming US support.
12. Hardware media: codec seccomp installs (no fail-open), codec nodes separate
    from camera nodes, no display-configuration access.
13. Kernel: modules reduced to product use; lockdown kept in user builds.
14. Name-loaded library check for every selected closed file; specific per-file
    purposes.

## How this document is maintained

- Every security or privacy finding updates this document and the private
  findings register together with the work that found it (different
  repositories, so not one commit).
- Every security or privacy change updates this file and
  [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md) (revision history, IMS
  integration notes) in the same commit, and
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) when a Tally shell rule
  changes.
- Register IDs have the form `TM-YYYYMMDD-NN`, with evidence, severity for a
  released build, status, next step, a public-safety marking and the public
  wording it maps to.
- A finding is described here once fixed, or once safe to state without giving
  an attacker a working path; until then the affected asset names the gap in
  general terms.
- No device identifiers or unfixed exploit detail.
- An evidence state changes only with new evidence; wording alone never turns an
  assumption into a claim.
- Each revision is dated at the top.

## Terms

- **AFU / BFU:** after / before the first unlock since boot; before it, user
  data stays encrypted.
- **AVB:** Android Verified Boot. A custom AVB root is a project key enrolled
  instead of the maker's; such builds report **yellow**, maker-key builds
  **green**.
- **CE / DE storage:** readable only after unlock / from boot.
- **TEE:** Qualcomm's TrustZone secure world. **KeyMint** keeps keys there,
  **Gatekeeper** checks the lock credential and throttles guesses, **RKP**
  provisions attestation keys; **StrongBox** and **Weaver** are secure-chip
  equivalents.
- **MTE:** CPU memory tagging that catches many memory-safety bugs.
- **HAL:** the vendor service between Android and a hardware driver.
- **QCRIL:** Qualcomm's radio daemon; **QMI/QRTR:** its messages and transport
  to the modem and DSPs.
- **IMS:** carrier voice (VoLTE), SMS and Wi-Fi calling over IP; **IWLAN:** IMS
  over Wi-Fi; **DCM** broker and daemon: the source-built service that brings up
  IMS data connections (Android-side app, vendor-side service).
- **eUICC / LPA:** the eSIM chip / the app that manages its profiles.
- **SUPL, PSDS, XTRA:** GNSS assistance from a network server, predicted
  satellite data, Qualcomm's assistance service.
- **EDL:** Qualcomm's low-level flashing mode, needing a signed programmer.
- **pstore/ramoops:** kernel logs kept in RAM across a reboot.
- **SELinux:** **enforcing** blocks what policy denies, **permissive** only logs
  it; a **domain** is a process's label.
- **KMI / VINTF:** kernel module interface / framework-vendor compatibility
  level.
- **Gunyah / pKVM:** Qualcomm's hypervisor / Android's protected-VM hypervisor.
- **userdebug / user:** debuggable / production build.
- **n-day:** an attack on a publicly fixed bug.
- **Tally:** DiamaneOS's interface; the **Tally flag** turns its code on at
  build time; **roadmap steps** (3.1, 7 and so on) are stages of plan task
  FP6-211.
- **Bring-up:** early work to make the hardware function; bring-up and test
  builds are private development images, named here by date.
