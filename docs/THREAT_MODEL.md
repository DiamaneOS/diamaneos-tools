# DiamaneOS Threat Model and Product Boundaries

Revision: 2026-10-05 (cellular: 2G protection and LTE-only checked on the phone; camera: the stock camera provider runs under a seccomp filter set up before its main(), so before CamX loads: threads but no child processes, Unix and QRTR sockets only, no writable and executable memory; for now calls outside the list are logged, not blocked; implemented, not yet built); 2026-10-05 (camera: the Adreno OpenCL runtime and compiler, about 34 MB selected only for the camera, and the software chromatic-aberration library that links the runtime are no longer shipped; implemented, not yet built); 2026-10-05 (public-text pass: statuses brought up to date: Bluetooth pairing, music and a headset call work with our own HCI service; calls with audio work through the call-audio bridge, which also holds the call-audio control permission; compressed music is decoded in software; the -153 fix, the debugfs mount refusal, the location logging changes, the vendor patch level and the public build commands are in builds; the IMS evidence states what exists instead of release gates); 2026-10-05 (Qualcomm's closed perf2 daemon, which ran as root with a broad policy, and its client libraries are no longer shipped: LineageOS's open libperfmgr power HAL replaces it and the CodeLinaro power HAL, running as system with CAP_SYS_NICE only in a narrow domain that may write only the CPU and GPU frequency limits it is handed; a no-op perf client ends the camera's perf2 lookups; implemented, not yet built); 2026-10-05 (cellular hardening, source review: the 2G and LTE-only settings reach the modem's network preference, not yet re-checked on the phone; the radio software answers "not supported" to the null-cipher control and predates cellular security notifications, so Settings hides both; Settings offers Android's network-type list instead of the stock list of CDMA world modes, implemented, not yet built); 2026-10-05 (the modem's TFTP file server and the protection-domain mapper are the open-source linux-msm tqftpserv and pd-mapper instead of Qualcomm's closed daemons, which started as root: own users, no capabilities, narrow SELinux domains; the file server is our fork with upstream's pending memory and path fixes, deletes only files in its own directory and follows no symbolic links there; its read-write data moves from persist to /data, so a factory reset clears it; Qualcomm's QRTR library stays for its stock users; implemented, not yet built); 2026-10-05 (unused closed vendor entry points: the camera provider no longer declares or registers the offline camera service, the display colour service is no longer installed, our own Bluetooth HCI service replaces the stock one, so its eight unregistered FM, ANT, SAR, config-store and TPI libraries are gone, and the dynamic-sensor (HID) sub-HAL is no longer shipped or loaded; built and checked on the phone: camera, Bluetooth on/off and sensors work, Bluetooth pairing not yet tested); 2026-10-05 (the vendor patch level is read from the stock vendor image and checked in every image set; built and shown in Settings); 2026-10-05 (-170: Wi-Fi joined networks with the hardware MAC address because no Wi-Fi capability overlay was installed; overlay and image check added, randomised address seen on the phone; QRTR: local senders kept off the IMS DCM port, per-client lookup cap, remote DCM records refused, QRTR tunnel device kept out of the kernel; DCM daemon error handling and publication on lookup timeout; in-call TTY, hearing-aid, voice-quality and Bluetooth voice keys limited to their platform owners; entitlement applies every provisioning setting and caps Retry-After; built; IMS calls over cellular and Wi-Fi passed on the phone); 2026-10-04 (source and build outputs: builds follow the DiamaneOS manifest branch and record the resolved manifest, the maintainer verifies GrapheneOS's signed tag when merging a release, and the kernel comes from the published prebuilts); 2026-10-03 (the Colour icon style: keys only for listed system apps, the style setting; the Paper wallpaper service); 2026-10-01 (-153: a vendor-private colour sensor readable without the Sensors permission; fix implemented, not yet built); 2026-10-01 (SELinux triage of the remaining denials, implemented, not yet built); 2026-10-01 (kernel, implemented, not yet built: debugfs no longer mountable, -114; the Wi-Fi platform driver logs no MAC address, -112); 2026-10-01 (location logging, -95: warning-level GNSS engine logs and the serving cell redacted in the radio log, implemented, not yet built); 2026-10-01 (decision and test notes stated as plain facts, review notes shortened; no change in substance); 2026-10-01 (location's own privacy chip; -151 and -152 fixed: a camera or microphone start during a location-only dot and a sensor joining during a chip now show; the system font); 2026-10-01 (trimmed to the current state); 2026-10-01 (contradictions resolved with current facts; statuses updated from the 2026-09-26 and 2026-09-27 phone tests); 2026-10-01 (shortened; revision history and IMS integration notes moved to [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md), Tally shell rules to [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md); no change in substance); 2026-09-30 (public build commands: what they enforce and their limits); 2026-09-30 (rewritten for readability; no change in substance). Earlier revisions: [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md).

> DiamaneOS is based on GrapheneOS. It is not made or endorsed by GrapheneOS or Fairphone.

## How to read this document

What DiamaneOS protects on the Fairphone 6 (FP6) today, against whom, how, and
what is missing. It shows only what current development builds contain: each
asset lists the protections that are built, and a missing protection is stated
as a gap. Nothing is qualified yet, and no hardware claim is a demonstrated
protection until it is verified. "GrapheneOS-derived" describes source lineage,
not equal security: the FP6 lacks several hardware features GrapheneOS relies on
([hardware limits](#hardware-limits-and-evidence)).

Start with the risks below, then the [asset table](#threats-by-asset). Status
words are defined in [Evidence states](#evidence-states), other terms in
[Terms](#terms). Development builds are named by date: the 2026-09-26 build is
the first SELinux-enforcing one, the 2026-09-27 build the next. `FP6-nnn` is a
project task; -nnn (for example -144) is a finding in the private findings
register. Companion files: [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md)
(history) and [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) (Tally shell
rules).

## Biggest remaining risks

1. **Development builds are unprotected:** debuggable, unlocked, public test
   keys, SELinux enforcement not validated per subsystem
   ([details](#current-development-state)).
2. **No MTE, much closed code:** memory-safety bugs are not contained as on
   current Pixels; the selected closed files recorded in `config/fp6-minimal/vendor-files.json` include large parsers of
   untrusted input, and the closed GPU driver and shader compilers run in every
   app ([details](#camera-microphone-sensor-streams-device-integrity)).
3. **Unpatchable firmware** that lags Android Security Bulletins: no firmware
   update path exists, and firmware stays at the last flashed stock release
   ([details](#firmware-security)).
4. **Credentials rest on the TEE:** no StrongBox or Weaver; throttling
   unverified; no passphrase policy; fingerprint class not measured
   ([details](#bfu-user-data)).
5. **The modem is outside Android's control:** isolation assumed; its traffic
   bypasses network controls; no fake-base-station detection or null-cipher
   control; the 2G and LTE-only controls reach the modem (checked on the phone)
   ([details](#call-and-sms-content-subscriber-identity-coarse-location)).
6. **No USB-C port control** ([details](#locked-device-data-kernel-integrity)).
7. **Attestation fails** ([details](#hardware-limits-and-evidence)).
8. **Supply chain:** one-person review; builds take the DiamaneOS manifest
   branch without checking a signature; every FP6 build so far ran on one host,
   and builds made with the earlier build scripts compiled with network access;
   no FP6 release signing; vendor inputs checked
   only against the vendor's own published hash
   ([details](#source-and-build-outputs)).
9. **Keyboard words readable before first unlock** in builds without the
   keyboard fork (in the manifest since 2026-09-27)
   ([details](#typed-text-and-personal-words)).

## Current development state

Every build so far is a private bring-up build on a development device; none was
distributed. Bring-up builds deliberately trade away protections to make
hardware work. Bring-up builds are never handed out or used as anyone else's
daily phone; their test-key signing gives no verified-boot or
signature-permission protection and their SELinux policy is not qualified, so
personal accounts and sensitive data stay off them.

- **Build type:** `userdebug`, ADB on by default, `adb root`; since the
  2026-09-26 build, ADB asks on the phone before trusting a computer and no
  developer key is pre-trusted (earlier builds accepted any computer); recovery
  accepts ADB unasked on unlocked or debuggable phones (AOSP design).
- **SELinux:** earlier builds fully permissive; since the 2026-09-26 build,
  debuggable builds boot enforcing with a per-domain policy from the denials
  captured on the last permissive build (narrow rules only, generic proc and
  sysfs nodes relabelled before any write grant, nothing new for apps or shell,
  one dontaudit rule: the sensors HAL's writes of a factory proximity value
  that nothing reads stay denied without flooding the audit log); the
  2026-09-26 build passed the phone feature tests
  enforcing, and every build since runs enforcing. Enforcing runs per subsystem
  are unverified (no owning task yet).
- **Bootloader and keys:** unlocked, public AOSP test keys; no OEM unlocking
  control in the OS, so the unlock-ability flag stays as stock left it.
- **Network endpoints:** inherited GrapheneOS services (connectivity, time, CT
  list, provisioning proxies, app catalog, SUPL proxy).
- **Closed vendor code:** selected Qualcomm/Fairphone inputs are hash-pinned in
  `config/fp6-minimal/vendor-files.json`; source replacements and remaining closed
  components require their recorded review and qualification.
- **Debug interfaces:** userspace Qualcomm diag removed; debug USB functions
  remain.
- **Firmware:** the last flashed stock release.

### IMS development evidence

The tested enforcing development image has ordinary voice, SMS and mobile data on
both test subscriptions, including incoming/outgoing Wi-Fi-only calls with VPN
lockdown. One immediately observed reporter restart preserved both registrations
at the sampled points; this does not establish universal or long-term recovery.
Native self-target carrier activation/cache/job tests passed without carrier traffic.
No real emergency call has been made; the emergency framework tests are simulations.

The source integration separates the modem-facing DCM, the restricted-network
metadata broker and passive Wi-Fi observer/reporter. The broker has no Internet
permission and does not forward application traffic or bypass app VPN policy.
Carrier checks have bounded physical workers and a whole-request deadline, native
Android retries, and durable carrier-delay fences. Audio control requires the
current unique bridge identity and a platform-owned permission, beyond the normal
Modify audio settings permission. The paired kernel reserves the DCM publisher
role and restricts TIPC to local IPC. Since the 2026-10-05 build, only the DCM
role and kernel sockets may queue local packets to the DCM port,
local client closures are no longer relayed to it, the name service refuses DCM
records from remote nodes and caps lookups at 32 per client, and the image check
requires the QRTR tunnel device to be off. The DCM daemon in that build no longer
exits on QRTR buffer exhaustion, an unreachable node, a single failed broker
call, a full Binder queue or a failed rebind (unknown errors still stop it); it
publishes its service when the name-service lookup goes unanswered (local
exclusivity comes from the kernel role), ends modem sessions at once when the
broker is lost (a 15 s grace period was tried and removed: the modem's IMS
registration then failed at its next refresh), and tells a stalled modem client its sessions ended before releasing the Android
requests. The Wi-Fi observer keeps its last default-route answer while a VPN has
no known underlying network. Further in-call audio keys (TTY, hearing aid, HD
voice, slow talk, volume boost, device mute, CRS volume and the Bluetooth voice
link and suspend keys) can only be set or read by their platform owners (system,
radio, audioserver, and the Bluetooth stack for its keys), not by any app with
the normal Modify audio settings permission. Entitlement applies every IMS
provisioning setting even when one fails, repairs from the stored carrier result
without a new SIM authentication, caps a carrier Retry-After at 24 hours and
delivers a portal's finishing callback once. These later controls are in the
2026-10-05 build, where IMS calls over cellular and Wi-Fi passed on the phone;
none is qualified.

The proprietary IMS/IWLAN/certificate components, modem firmware and selected
network-control daemon remain part of the trusted computing base. Presigned OEM
apps retain OEM signature verification. The inherited GrapheneOS installer policy
also blocks unknown-source system-app updates: production allows its first-party
installer and explicitly authorized shell installs; a debuggable-only override is
not a production allowance. OEM signatures alone therefore do not permit ordinary
apps to install these updates. The first-party catalog uses GrapheneOS's update
sources; production signing and locked verified boot do not exist, and development
test keys are public. eSIM profile management is not available; an inactive LPA
is omitted.
The [IMS integration notes](THREAT_MODEL-HISTORY.md#ims-integration-notes) preserve
historical evidence without making it acceptance of this later source cut.

## What DiamaneOS aims to defend against

- Remote and proximity attack surface (network including IP over cellular,
  Bluetooth, NFC, USB, media parsing): inherited GrapheneOS hardening and a
  reduced vendor surface; without hardware memory tagging, memory-safety bugs
  are not contained as on current Pixels.
- Malicious or over-permissioned apps: sandboxing, Network and Sensors
  permissions, Storage and Contact Scopes (inherited).
- Opportunistic physical access to a powered-off (BFU) device: file-based
  encryption with hardware-wrapped keys; no passphrase policy.
- Data exfiltration by the OS vendor: no telemetry or Google Mobile Services.

## What DiamaneOS does not defend against

- Persistence after compromise: development builds run unlocked with public test
  keys, so verified boot does not hold ([OS
  integrity](#os-integrity-on-a-released-locked-build)).
- Offline brute force of a weak credential: Gatekeeper backoff in the Qualcomm
  TEE is expected but unverified, no discrete secure element is exposed to
  Android, and no passphrase policy is implemented ([BFU user
  data](#bfu-user-data)).
- Sophisticated forensic extraction from an unlocked or powered-on locked (AFU)
  device.
- Firmware compromise: boot, TrustZone, hypervisor, modem, DSP, Wi-Fi and
  Bluetooth firmware come from Fairphone and Qualcomm; the project cannot build
  or patch it.
- Baseband exploitation beyond what SoC isolation provides; on the FP6 that
  isolation is assumed, not verified.
- Detection of fake base stations, null-cipher sessions or identity requests:
  the current Qualcomm radio software does not support it.
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
- **Attestation is TEE-only:** with a custom key, a locked FP6 reports yellow,
  never green; GrapheneOS's Auditor does not support the FP6. Earlier builds did
  not enable remote key provisioning, so attestation was expected to fail. Since
  the 2026-09-26 build, the device sets stock's provisioning properties
  (requests via the inherited GrapheneOS proxy) and reports the factory
  attestation IDs. On the 2026-09-27 build provisioning reaches the server but
  the TEE's certificate request always fails, so no attestation keys are
  provisioned and attestation still fails (cause open); unchanged on the
  2026-10-05 build, whose vendor patch level matches stock.
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
  vendor code and firmware stop.
- **Kernel:** 6.1 (android14 KMI), with a recorded, intentional deviation for a
  48-bit virtual address space.
- **Unverified:** TEE KeyMint, Gatekeeper and attestation behaviour, A/B failure
  recovery, rollback behaviour.

## Evidence states

A state changes only with new evidence. An asset's state is the weakest among
its protections; its status names it first, stronger items after, then what is
still unverified with its owning project task.

- **Not implemented:** not in current builds.
- **Bring-up (not qualified):** in current development builds; the security
  property is untested.
- **Observed (bring-up):** a narrow behaviour seen on a development build.
- **Observed gap:** confirmed missing or not working today.
- **Recorded:** stated in a project record, not rechecked this revision.
- **Accepted limitation:** a documented limit that stays.
- **Qualified:** verified on a production (`user`) build; none yet.

## Threats by asset

Assets are grouped by attacker position. Each lists the threat (attacker and
entry point), the protections in current builds, what remains, and the status.

| Group | Asset | Status (weakest first) |
| --- | --- | --- |
| Remote | [Service metadata and OS identity](#service-metadata-and-os-identity) | Not implemented (DiamaneOS endpoints); Observed (static) |
| Remote | [DNS privacy](#dns-privacy) | Not implemented |
| Remote | [App data from remote exploit](#app-data-from-remote-exploit) | Bring-up (not qualified) |
| Remote | [Traffic outside Android network policy](#traffic-outside-android-network-policy) | Accepted limitation |
| Remote | [Certificate validity checks at boot](#certificate-validity-checks-at-boot) | Bring-up (not qualified) |
| Cellular | [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location) | Observed gap (bring-up) |
| Cellular | [SMS, SIM applets, broadcast alerts, carrier configuration](#sms-sim-applets-broadcast-alerts-carrier-configuration) | Accepted limitation (inherited) |
| Cellular | [Application processor and user data](#application-processor-and-user-data) | Bring-up (not qualified) |
| Cellular | [Telephony control and subscriber data](#telephony-control-and-subscriber-data) | Bring-up (not qualified) |
| Cellular | [eSIM profiles and download sessions](#esim-profiles-and-download-sessions) | Bring-up (not qualified) |
| Cellular | [Radio-off expectation, location privacy](#radio-off-expectation-location-privacy) | Bring-up (not qualified) |
| Location | [Location history](#location-history) | Bring-up (not qualified) |
| Apps | [App and user data](#app-and-user-data) | Bring-up (not qualified) |
| Apps | [Kernel integrity and all data](#kernel-integrity-and-all-data) | Observed (bring-up) |
| Apps | [Camera, microphone, sensor streams, device integrity](#camera-microphone-sensor-streams-device-integrity) | Bring-up (not qualified) |
| Apps | [Persistent hardware identifiers](#persistent-hardware-identifiers) | Observed gap (bring-up) |
| Apps | [Secondary-profile data](#secondary-profile-data) | Bring-up (not qualified) |
| Proximity | [Device integrity, paired-device data, Bluetooth address](#device-integrity-paired-device-data-bluetooth-address) | Bring-up (not qualified) |
| Proximity | [SIM applets, presence, NFC services](#sim-applets-presence-nfc-services) | Bring-up (not qualified) |
| Proximity | [Wi-Fi MAC (tracking), device integrity](#wi-fi-mac-tracking-device-integrity) | Bring-up (not qualified) |
| Proximity | [Locked-device data, kernel integrity](#locked-device-data-kernel-integrity) | Observed gap |
| AFU | [AFU user data](#afu-user-data) | Observed gap |
| AFU | [Previous boot's kernel log and event logs](#previous-boots-kernel-log-and-event-logs) | Observed (bring-up) |
| AFU | [AFU unlock, auth-bound keys](#afu-unlock-auth-bound-keys) | Observed gap |
| AFU | [AFU data under coercion or seizure](#afu-data-under-coercion-or-seizure) | Bring-up (not qualified) |
| BFU | [BFU user data](#bfu-user-data) | Observed gap |
| BFU | [Device-persistent data outside userdata](#device-persistent-data-outside-userdata) | Observed gap |
| BFU | [Removable media](#removable-media) | Not implemented |
| Boot and firmware | [OS integrity on a released, locked build](#os-integrity-on-a-released-locked-build) | Observed gap (bring-up images) |
| Boot and firmware | [Firmware security](#firmware-security) | Observed gap |
| Boot and firmware | [Keys, Gatekeeper throttling, fingerprint templates](#keys-gatekeeper-throttling-fingerprint-templates) | Observed gap (builds before the 2026-09-26 build) |
| Supply chain | [Source and build outputs](#source-and-build-outputs) | Observed gap (FP6 path) |
| Supply chain | [Closed vendor inputs](#closed-vendor-inputs) | Observed gap |
| Supply chain | [Release signing keys](#release-signing-keys) | Not implemented |
| Supply chain | [Repositories, domain, install page](#repositories-domain-install-page) | Recorded |
| Supply chain | [Exposure window for known bugs](#exposure-window-for-known-bugs) | Observed gap |
| Supply chain | [Shell fork rebase lag](#shell-fork-rebase-lag-grapheneos-security-fixes-under-the-tally-shell) | Bring-up (not qualified) |
| Supply chain | [Install-time trust](#install-time-trust) | Not implemented |
| Everyday use | [Correct user decisions](#correct-user-decisions) | Not implemented |
| Everyday use | [Typed text and personal words](#typed-text-and-personal-words) | Bring-up (not qualified) |
| Everyday use | [Privacy indicators and disclosures under the Tally shell](#privacy-indicators-and-disclosures-under-the-tally-shell) | Bring-up (not qualified) |
| Everyday use | [Files archive handling (browse, extract, create)](#files-archive-handling-browse-extract-create) | Bring-up (not qualified) |

### Remote attackers (network and content)

#### Service metadata and OS identity

- **Threat:** network observer or endpoint operator, via connectivity,
  captive-portal and DNS checks, HTTPS time, the CT list, key and DRM
  provisioning, app and browser updates, network location and geocoding (when
  on), Auditor remote verification and sample submission (opt-in), and
  phone-side eSIM (SM-DP+/SM-DS) and carrier entitlement connections.
- **Protection:** no telemetry or GMS; since the 2026-09-26 build, the build
  properties keep Fairphone's stock product identity (brand, product, device,
  model), as GrapheneOS keeps Google's.
- **Remaining:**
  - DiamaneOS endpoints are not implemented: builds use the inherited GrapheneOS
    services ([development state](#current-development-state)).
  - Small-user-base hostnames are a fingerprint; the HTTPS time bootstrap
    resolves outside Private DNS.
  - The DNS resolver and browser send GrapheneOS probe names even with
    connectivity checks off; the browser's own checks can be repointed only by
    rebuilding it.
  - With network location on (the setup wizard's location switch turns it on),
    nearby Wi-Fi and cell identifiers go to GrapheneOS's relay for Apple's
    location service, geocoding queries to GrapheneOS's server under a
    GrapheneOS user agent.
  - Auditor's opt-in remote features use GrapheneOS's attestation service;
    sample submission sends the full system property list.
  - CT enforcement fails open once the CT list is over 70 days old, so mirror
    freshness is a security dependency.
  - "No telemetry" in closed vendor files rests on a static scan; runtime egress
    is unmeasured.
- **Status:** Not implemented: DiamaneOS endpoints (FP6-100, FP6-102 to FP6-112,
  FP6-103, FP6-106). Observed (static): no contacted host in the selected closed
  files except GNSS cloud hosts removed by pinned configuration. Unverified:
  runtime egress with SELinux enforcing (no owning task yet).

#### DNS privacy

- **Threat:** network observer or on-path resolver, via DNS queries.
- **Protection:** the inherited Android default only.
- **Remaining:** no encrypted-DNS default; the time bootstrap, apps with their
  own DoH/DoT and modem traffic bypass Private DNS.
- **Status:** Not implemented (FP6-079); the inherited Android default applies.

#### App data from remote exploit

- **Threat:** remote network, media or web content, via carrier and Wi-Fi data,
  media files and streams, browser and WebView, captive portals, and web content
  reaching the GPU shader compiler.
- **Protection:** inherited sandbox, hardened_malloc, exec spawning; Vanadium
  browser and WebView as mirrored unmodified APKs.
- **Remaining:** no MTE; closed GPU driver and shader compilers in every app
  process; hardware video decode is not integrated; inherited captive-portal
  WebView; browser component updates still point upstream.
- **Status:** Bring-up (not qualified): exec spawning, sandbox permissions and
  browser delivery unvalidated on the FP6. Observed (bring-up): hardened_malloc
  active (48-bit VA kernel). Unverified: inherited hardening (FP6-046), debug
  exposure and component removal (FP6-060, FP6-061), DRM limits (FP6-065),
  browser delivery (FP6-105, FP6-111).

#### Traffic outside Android network policy

- **Threat:** carrier or network observer, via modem IMS signalling and media,
  SUPL, and control-plane and network-initiated location.
- **Protection:** none; this document records the traffic.
- **Remaining:** invisible to the Network permission, VPN and Private DNS.
- **Status:** Accepted limitation. Unverified: per-carrier configuration review
  (FP6-090).

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
- **Status:** Bring-up (not qualified): on the 2026-09-26 build the clock was
  right after an offline reboot (phone test 2026-09-27). Unverified: suspend,
  manual and network time changes and battery removal, with SELinux enforcing
  (no owning task yet).

### Cellular and baseband

#### Call and SMS content, subscriber identity, coarse location

- **Threat:** fake base station, IMSI catcher, downgrading or null-cipher
  network, via 2G fallback, pre-authentication identity requests and radio
  security events.
- **Protection:** opt-in "2G network protection" and the LTE-only, 5G-only and
  4G-or-5G network types. Both set Android's allowed network types; the radio
  daemon turns them into the modem's radio-technology preference, one bit per
  technology, so GSM is left out when 2G is off and LTE-only allows LTE alone.
  Since the 2026-09-26 build the default network type is set, so the stored 2G
  setting keeps LTE and 5G. Settings offers Android's network-type list, filtered
  by what the modem supports, not the stock list of 34 modes with CDMA-only
  entries. Checked on the phone: with 2G protection on, the modem's own setting
  read back has no GSM technologies, and with LTE-only it has LTE alone; calls
  over LTE still work.
- **Remaining:** 2G stays allowed by default ([decision](#decision-record)), so
  a downgrade works unless the user opts in. No detection: the radio software
  answers "not supported" to the null-cipher/integrity control, and its radio
  network interface is version 2, while cellular security notifications need
  version 3, so Settings hides both. LTE/NR identity exposure remains.
- **Status:** Observed gap (radio software): no null-cipher control or security
  notifications (FP6-084). Bring-up (not qualified): the source review of
  2026-10-05 traces both settings to the modem's preference request; a build
  before 2026-09-26 did not pass the choice to the modem, and the controls have
  not been re-checked on the phone since. Unverified: the modem's behaviour once
  the preference is applied (FP6-044), a radio-policy regression check (no
  owning task yet).

#### SMS, SIM applets, broadcast alerts, carrier configuration

- **Threat:** network or SIM-side sender, or carrier, via SMS and class-0/type-0
  messages, SIM toolkit proactive commands, cell broadcast, and SIM access rules
  granting carrier privileges.
- **Protection:** inherited AOSP handling; no project change.
- **Remaining:** SIM applets act partly outside OS control; network "silent" SMS
  are acknowledged without display (3GPP rules); SMS are parsed in modem and
  framework; apps given carrier privileges by the SIM can change carrier
  configuration.
- **Status:** Accepted limitation (inherited). Unverified: telephony bring-up
  (FP6-044); broadcast alert delivery (the radio daemon implements the channel
  setup and delivery calls; no alert received yet).

#### Application processor and user data

- **Threat:** over-the-air attacker or compromised modem firmware, via modem
  parsers (RRC/NAS, SMS, modem IMS), then shared memory, the IPA data path,
  QMI/QRTR services, modem remote-storage and file services.
- **Protection:** modem image authenticated by the stock secure-boot chain;
  AP-side modem services narrowed and confined to own SELinux domains; modem
  crash dumps off. The modem's file server (TFTP over QRTR) and the
  protection-domain mapper are open-source linux-msm builds (tqftpserv,
  pd-mapper) in place of Qualcomm's closed daemons, which started as root: each
  runs as its own user with no capabilities and no network, reads only the
  modem partition and the remote processors' firmware names, and only the file
  server writes, to its own directory in /data, where it deletes only its own
  files and follows no symbolic links. The file server is a fork carrying
  upstream's pending fixes for memory errors and a path escape the modem could
  trigger, with host tests for them (implemented, not yet built).
- **Remaining:** modem firmware unpatchable by the project and behind ASB; SoC
  isolation (SMMU, memory protection) assumed, not verified; silent modem
  restarts hide crash-and-retry attacks; modem state in its own partitions
  survives factory reset, its file-server data in /data does not (implemented,
  not yet built). Both daemons parse modem-supplied data in C, contained by
  their domains; QRTR has no per-service access control, so either can reach
  any QMI service; pd-mapper is pinned to an older upstream commit because later
  ones do not build for Android.
- **Status:** Bring-up (not qualified): no protection claim. Unverified: modem
  and telephony bring-up (FP6-042, FP6-044), debug exposure and component
  removal (FP6-060, FP6-061).

#### Telephony control and subscriber data

- **Threat:** malicious app via binder or compromised modem via QMI, reaching
  the closed radio daemon (QCRIL), Qualcomm IMS app, DiamaneOS's call-audio
  bridge or the eSIM LPA.
- **Protection:** explicit privileged-permission allowlists with denials; own
  SELinux domains; no radio daemon socket; radio daemon not a binder service;
  diag access removed. The call-audio bridge replacing Qualcomm's call-audio app
  holds the normal audio-settings permission and DiamaneOS's platform-owned
  call-audio control permission (no audio-routing or capture permission, no
  network); its domain reaches only the radio daemon's call-audio service and the
  audio server.
- **Remaining:** closed code parses untrusted input; the update path of
  presigned vendor apps is not locked; enforcing since the 2026-09-26 build
  (both SIMs in service), not qualified per subsystem.
- **Status:** Bring-up (not qualified): allowlists and domains reviewed; runtime
  and enforcing unverified; calls with audio work through the call-audio bridge
  on development builds (last on the 2026-10-05 build). Unverified: telephony
  bring-up (FP6-044), enforcing runs (no owning task yet).

#### eSIM profiles and download sessions

- **Threat:** TLS interceptor, thief or examiner after reset, or coercer, via
  LPA connections to eSIM servers and profiles retained in the eUICC.
- **Protection:** installed profiles work as SIMs without an LPA (GrapheneOS
  baseline). Since the 2026-09-27 build, the stock Qualcomm LPA stays installed
  with services off by default (stock leaves its eSIM service on), its JNI
  library unshipped. OpenEUICC is not included (GrapheneOS os-issue-tracker
  #6275 and #2631). GSMA SGP.22 mutual authentication.
- **Remaining:** the stock LPA cannot list profiles on the FP6 (the stock radio
  daemon cannot decode the modem's reply), so Android treats an eSIM as a
  physical SIM: none can be added, switched or deleted from Android, and factory
  reset and duress cannot reach the eUICC. The stock LPA's TLS trust
  configuration fails the production trust rule, relevant only if it is turned
  on again.
- **Status:** Bring-up (not qualified): on the 2026-09-26 build the service was
  on and could not list profiles (phone test 2026-09-27); from the 2026-09-27
  build it is off again (phone check 2026-09-27: no eSIM service registered).

#### Radio-off expectation, location privacy

- **Threat:** network or passive observer, via the modem attaching on its own,
  or airplane mode.
- **Protection:** airplane mode puts the modem in low-power mode.
- **Remaining:** modem silence in airplane mode or without the radio daemon is
  unverified; a device setting keeps the SIM powered in airplane mode.
- **Status:** Bring-up (not qualified). Unverified: telephony bring-up
  (FP6-044).

### Location

#### Location history

- **Threat:** vendor cloud, assistance-server operator, carrier or local log
  reader, via GNSS HAL configuration, SUPL, PSDS, control-plane positioning and
  logs.
- **Protection:** Qualcomm cloud, XTRA and crowdsourcing paths excluded by
  pinned configuration; PSDS off; SUPL user-selectable (Off, proxy, standard);
  network location and geocoder off by default; IMS geolocation has no network
  geocoder.
- **Remaining:** SUPL defaults to the GrapheneOS proxy; "standard" SUPL goes
  straight to the carrier-configured server (Google's, on the carrier tested so
  far); SUPL requests carry cell information; control-plane positioning runs in
  the modem; GNSS engine state on persist survives reset. Builds before the
  2026-10-01 build logged the GNSS engine at info level and wrote the
  serving-cell identity to the radio log on every registration poll (about twice
  a second on one SIM), readable over adb and in bug reports. With the
  redaction, the radio log still shows the
  physical cell ID and channel, as AOSP does, and development builds show the
  redacted fields as unsalted hashes (AOSP behaviour; user builds hide them).
- **Status:** Bring-up (not qualified): cloud paths absent and pinned; no GNSS
  fix recorded; warning-level GNSS engine logs and a radio log that redacts the
  serving-cell identity like the framework's other cell logs are in builds
  since the 2026-10-01 build, not phone-checked. Unverified: assisted GNSS
  (FP6-103), GNSS bring-up tests (FP6-045).

### Malicious and over-permissioned apps

#### App and user data

- **Threat:** malicious or over-permissioned app, via permissions, background
  listeners, IPC/URI grants and sensors.
- **Protection:** per-app Network and Sensors permissions, Storage and Contact
  Scopes (inherited).
- **Remaining:** no DiamaneOS-specific app controls are implemented.
- **Status:** Bring-up (not qualified): inherited controls in the build,
  unvalidated on the FP6. Unverified: inherited hardening (FP6-046).

#### Kernel integrity and all data

- **Threat:** malicious or compromised app or renderer process, via the GPU
  driver node (reachable by every app, as on stock), binder, loaded
  network-protocol modules and socket families, and the DSP remote-call driver
  (named groups only).
- **Protection:** GrapheneOS kernel hardening configuration merged into the
  vendor kernel; lockdown (confidentiality); SELinux socket and device
  restrictions; modules reduced to product use (since the 2026-09-27 build no
  protocol modules without product use: CAN, 802.15.4/6LoWPAN, the kernel NFC
  socket family, PPTP/L2TP, the in-kernel Bluetooth stack); no SELinux
  development mode; dmesg root-only. Profiling work must never weaken lockdown
  or tracing restrictions in user builds.
- **Remaining:**
  - No MTE; large vendor driver surface; hardening hand-merged into a vendor
    kernel with an intentional KMI deviation.
  - KPROBES on, as the USB controller glue's hooks use kretprobes; debugfs
    built in for kernel code, not mountable since the 2026-10-01 build (both
    under [Decision record](#decision-record)).
  - USB host-class, debug and trace drivers still load.
  - TIPC re-enabled (GrapheneOS disables it) for Qualcomm's mobile-data stack,
    as a module without network bearers or crypto; under enforcing SELinux no
    user-installed or privileged app reaches it (sockets only for two radio
    daemons, configuration only for system components with the network-admin
    capability, including the network stack module).
  - Confidentiality lockdown also disables kernel tracing and kernel-memory
    reads by BPF programs, so Android's per-app CPU time accounting (per-app CPU
    use in Battery usage) and the memory-event OOM listener do not start.
  - Every app reaches the kernel's userfaultfd code (ART's garbage collector
    needs it), but apps are forced into user-mode-only mode
    (`vm.unprivileged_userfaultfd=0`, `/dev/userfaultfd` root-only and
    SELinux-denied), so they cannot freeze the kernel mid-copy to widen race
    windows; bugs there remain ordinary kernel surface, fixed through 6.1 LTS
    updates.
- **Status:** Observed (bring-up): module reduction and production configuration
  run on the phone since the 2026-09-27 build (kernel check: the configuration
  matches policy, no shipped module depends on a removed one; phone check
  2026-09-27: no removed module loaded, no symbol errors, configuration values
  as specified); debuggable builds run enforcing since the 2026-09-26 build;
  lockdown confidentiality active; tracing empty. Unverified: kernel build
  (FP6-041), debug exposure and component removal (FP6-060, FP6-061), enforcing
  runs (no owning task yet).

#### Camera, microphone, sensor streams, device integrity

- **Threat:** malicious app or remote content reaching closed vendor code
  through a platform service: camera, media, display, audio, sensors, GNSS, NFC
  and Bluetooth HAL interfaces; same-process GPU libraries; persist and vendor
  data files parsed by closed code.
- **Protection:** per-file allowlisted, hash-pinned stock selection; SELinux
  grants bound to each service domain; a closed HAL's internal endpoints
  reachable only from its own process (audio since the 2026-09-26 build); fewer
  service users, groups and capabilities; tight device-node permissions;
  Qualcomm diagnostics, telemetry and factory services excluded. Closed
  services with no client are not shipped or not registered: the display
  colour service, which any platform app could start, is not installed; the
  camera provider's offline camera service has no VINTF declaration and no
  registration grant, so servicemanager refuses it (its library stays, because
  the CHI override links it) (since the 2026-10-05 build). Compressed music is
  decoded by Android's sandboxed software codecs: the audio policy has no
  compressed-offload output, so apps cannot hand MP3, AAC, FLAC or other
  bitstreams to the closed DSP decoders (since the 2026-10-05 build; music
  playback works, owner test 2026-10-05). Qualcomm's closed
  perf2 daemon is not shipped: it ran as root with setuid, kill and sys_nice,
  could read and write every app's `/proc` files and change the scheduling of
  apps and the compositor. The power HAL is LineageOS's open libperfmgr, run
  as system with CAP_SYS_NICE only: it may write only the CPU and GPU frequency
  limits, the GPU wake trigger and the tap-to-wake switch that init hands to
  it, sets uclamp on the threads of performance hint sessions, and reaches the
  kernel's scheduler boost only through fixed init property triggers. The stock
  camera and composition extension load a no-op stand-in for the closed perf
  client, so they no longer look up the perf2 service (implemented, not yet
  built).
  The Adreno OpenCL runtime and its compiler (about 34 MB), selected only for
  the camera, are not shipped, nor the software chromatic-aberration library
  that links them (implemented, not yet built).
  The stock camera provider runs under a seccomp filter that our loader
  library installs before the provider's main(), so before CamX is loaded:
  threads but no child processes, Unix and QRTR sockets only, no memory both
  writable and executable; the provider does not start without it. For now
  calls outside the list are logged and allowed (implemented, not yet
  built).
- **Remaining:** large closed parsers (camera about 185 MB) without MTE; most
  closed daemons without seccomp, and the camera provider's filter does not
  block yet; the closed thermal daemon runs as root; the power
  HAL may change the scheduling (uclamp) of any app's threads, as on Pixels;
  Android 14 ABI vendor code on Android 17; per-file purpose partly generic;
  closed code updated only through Fairphone stock releases. The closed
  Qualcomm sensors sub-HAL lists a vendor-private ambient colour (RGB) sensor
  with no required permission (-153). GrapheneOS's Sensors permission covers the
  standard sensor types and takes a vendor type's permission from the HAL, and
  an empty one meant none, so any app could read the sensor with the Sensors
  permission off (shown on the FP6, 2026-10-01). The frameworks/native fork
  keeps the Sensors permission when the HAL names none (since the 2026-10-01
  build; the post-flash checks of the 2026-10-01 and 2026-10-02 builds found
  the colour sensor refused without the Sensors permission).
- **Status:** Bring-up (not qualified): selection reviewed per subsystem;
  enforcing per subsystem untested. Observed (bring-up): the colour sensor
  needs the Sensors permission (-153). Unverified: debug exposure and component
  removal (FP6-060, FP6-061), closure gate (FP6-208), camera (FP6-045,
  FP6-203), audio (FP6-044, FP6-205), enforcing runs (no owning task yet).

#### Persistent hardware identifiers

- **Threat:** any app, via system properties, persist files, sysfs and logs.
- **Protection:** vendor-internal property types with no read grant; narrowed
  sysfs labels; Wi-Fi MAC randomization; traceability services excluded; boot
  parameters handed to user space logged by name only (since the 2026-09-27
  build; on the phone the Wi-Fi MAC appeared in no log, pstore or DropBox
  entry); since the 2026-10-02 kernel the Wi-Fi platform
  driver logs no MAC address the modem or the device supplies; enforcing
  SELinux.
- **Remaining:** readable on any permissive build; a random per-install
  Bluetooth address is unverified: the address path in use is unrecorded (the
  HAL tries a factory address first) and the address appears in logs and bug
  reports; the stock camera HAL logs camera module serial numbers at every
  start; the SoC serial (sysfs `soc0/serial_number`) is readable by 16 system
  and vendor domains through platform sysfs read grants and imported Qualcomm
  rules (no app domain; the 2026-09-26 build's policy gives the camera HAL and
  nicmd only the public SoC id); the factory Wi-Fi MAC in persist is not read
  under enforcing, so the driver falls back to the chip's own MAC or one derived
  from its serial; since the 2026-10-05 build Wi-Fi joins networks with a
  randomised address (-170: earlier builds used the hardware address); a flash
  dump reveals persist data.
- **Status:** Observed gap (bring-up): some hardware serials exposed as system
  properties on permissive builds; whether enforcing builds deny the read is not
  yet shown. Unverified: enforcing
  runs and an identifier probe test (no owning task yet), Wi-Fi and Bluetooth
  bring-up (FP6-043).

#### Secondary-profile data

- **Threat:** another user or profile, via user switch, stopped and running
  profiles, unified vs separate challenge.
- **Protection:** CE/DE separation (inherited).
- **Remaining:** no credential policy is enforced across independent challenges;
  unified-challenge profiles follow platform semantics, no invented independent
  key; a stopped session holds data until deleted (session end is not deletion).
- **Status:** Bring-up (not qualified): inherited behaviour, unvalidated.
  Unverified: encryption and hardening validation (FP6-046). Not implemented:
  credential-policy enforcement (FP6-071).

### Proximity attackers

#### Device integrity, paired-device data, Bluetooth address

- **Threat:** BR/EDR or LE attacker, or tracker, via closed controller firmware,
  the closed stock Bluetooth HCI HAL and the GrapheneOS host stack.
- **Protection:** GrapheneOS host stack; Bluetooth off by default; consent-gated
  profiles; SIM access profile off; closed HAL confined (no network, persist
  write or diag). Our own small service hosts the closed HCI implementation in
  place of the stock service, so the FM, ANT, SAR, config-store and TPI
  libraries the stock service linked (but never registered) are not shipped,
  and the sensors HAL no longer loads the dynamic-sensor sub-HAL, which parsed
  HID sensor descriptors from paired Bluetooth and USB devices (head trackers
  are therefore not supported) (since the 2026-10-05 build).
- **Remaining:** controller firmware and HAL unpatchable by the project; no MTE;
  a random per-install address is unverified (live address path unknown) and the
  address appears in logs; classic address stable per install.
- **Status:** Bring-up (not qualified): with our own HCI service, pairing,
  music playback and a headset call work (2026-10-05 build, owner test
  2026-10-05); security properties and enforcing untested. Unverified:
  Bluetooth and audio bring-up (FP6-043, FP6-044), debug exposure and component
  removal (FP6-060, FP6-061).

#### SIM applets, presence, NFC services

- **Threat:** contactless reader (also against a locked, screen-off or
  switched-off phone) or malicious tag, via NFC reader and listen modes and
  controller-to-SIM routing.
- **Protection:** default route pinned to the host (no SIM routing unless an app
  registers it); no UICC or embedded secure element features declared;
  controller firmware file is an exact, hash-pinned stock file.
- **Remaining:** NFC on by default; registered SIM routes reachable while locked
  or off; secure-NFC support unverified; closed controller firmware, which the
  HAL can also update.
- **Status:** Bring-up (not qualified): reader works; routing tests not run.
  Unverified: NFC bring-up tests (FP6-045), eSIM/SIM (FP6-089).

#### Wi-Fi MAC (tracking), device integrity

- **Threat:** passive Wi-Fi observer, malicious access point or LAN peer, via
  probe requests, association, Wi-Fi driver and firmware, and wake-on-LAN.
- **Protection:** station-only features exposed to Android (no hotspot, since
  hostapd is not shipped; no Wi-Fi Direct or Aware); source-built Wi-Fi HAL and
  driver; MAC randomization.
- **Remaining:** builds before 2026-10-05 had no Wi-Fi capability overlay, so
  the Wi-Fi service treated the hardware as unable to randomise its MAC address
  and the phone joined networks with a globally administered (hardware)
  address (-170, seen on the phone on 2026-10-04); since the 2026-10-05 build the
  FP6 Wi-Fi overlay declares randomisation support, the image check requires
  it, and the phone associates with a locally administered address; closed
  Wi-Fi firmware on the over-the-air path; a LAN peer can wake the device with a
  magic packet; on Wi-Fi networks used for Android Auto the device sends a DHCP
  hostname derived from the device name (inherited default).
- **Status:** Bring-up (not qualified): unverified. Unverified: connectivity
  bring-up (FP6-043).

#### Locked-device data, kernel integrity

- **Threat:** malicious USB device or host, forensic tool or malicious charger,
  via USB-C data lines (host and gadget roles), USB descriptors, USB PD and
  charger firmware.
- **Protection:** not implemented: GrapheneOS USB-C port control is not wired up
  on the FP6.
- **Remaining:** the deny-new-USB hook is in the merged kernel source, but
  whether the built kernel exposes it is unchecked; no evidence of a hardware
  USB data-line cutoff; debug USB functions present; broad USB host drivers
  loaded; the USB descriptor carries the device serial; closed charger firmware.
- **Status:** Observed gap: port control absent. Bring-up (not qualified):
  userspace gadget configuration reviewed. Unverified: debug exposure audit
  (FP6-060), USB modes (FP6-045), hardware USB data disable (no owning task
  yet).

### Physical access to a powered-on, locked device (AFU)

#### AFU user data

- **Threat:** forensic tools, via USB, the lock screen, and firmware download
  and dump modes.
- **Protection:** inactivity auto-reboot (inherited); panic RAM dumps off.
- **Remaining:** no USB-C port control; the OS does not refuse firmware-download
  or dump reboots; the kernel keeps a minidump on panic, retrieval path
  unverified; the early-boot setting that also turns dump mode off needs a
  policy grant; EDL stays reachable with physical access and a signed
  programmer; no forensic-proof claim.
- **Status:** Observed gap: port control absent; download-reboot refusal not
  implemented. Observed (bring-up): panic dump mode off by kernel default.
  Unverified: debug exposure audit (FP6-060).

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
  storage, readable by the system before first unlock; the RAM copy stays until
  overwritten; Qualcomm's minidump driver registers the log areas, so a
  collected minidump carries them. Reboots and kernel crashes reset cold,
  powering RAM off, so in practice nothing survives; a one-off warm reboot on
  the 2026-09-27 build kept both logs (the bootloader does not clear RAM);
  reboots stay cold. Every domain holds the platform's write grant on the
  event-log device, so its file mode is the only control.
- **Status:** Observed (bring-up): the region, with GrapheneOS's Pixel layout,
  registered on every boot of the 2026-09-27 build; the release-build rule is
  built but untested (that build is debuggable, so the rule is inactive); no
  earlier build registered the region. Kernel check (2026-09-27): placed at boot
  without a fixed address; the saved console keeps only notice-level and more
  severe messages (the console log level), not the full log. Observed on the
  2026-09-27 build: same address every boot; pstore empty after a normal reboot;
  after a warm reboot both logs present, readable by the shell user by exact
  name (directory listing denied), the system's copy only after first unlock;
  the boot-parameter value in none of them. Unverified: debug exposure audit
  (FP6-060).

#### AFU unlock, auth-bound keys

- **Threat:** lifted or spoofed fingerprint, via the side fingerprint sensor.
- **Protection:** strong authentication after reboot and timeouts; lockout in
  the trusted app; the vendor debug service never registered (kept inside the
  HAL process) and unreachable by policy.
- **Remaining:** the DiamaneOS fingerprint HAL declares Class 3 (strong) before
  any spoof testing: a declaration, not a measurement; until spoof testing
  passes, the fingerprint is not treated as strong authentication.
- **Status:** Observed gap: class declared, not measured. Bring-up (not
  qualified): unlock works; the first enforcing boot showed the module needs its
  debug service registered, which the HAL answers in-process since the
  2026-09-26 build (enrolment and unlock work, phone test 2026-09-27).
  Unverified: fingerprint bring-up (FP6-045), credential validation (FP6-046),
  spoof testing (no owning task yet).

#### AFU data under coercion or seizure

- **Threat:** coercer, or seizure then extraction, via the lock screen and
  buttons.
- **Protection:** duress credential and wipe (inherited).
- **Remaining:** duress key destruction relies on TEE key deletion and flash
  erase, not a secure element; eSIM erase depends on the LPA; eSIM profiles may
  survive.
- **Status:** Bring-up (not qualified): inherited code present, untested on the
  FP6. Unverified: duress test with synthetic data (FP6-046), eSIM erase
  (FP6-089).

### Physical access to a powered-off device (BFU) and data outside userdata

#### BFU user data

- **Threat:** opportunistic access or flash readout (EDL, chip-off), via flash
  contents and the TEE.
- **Protection:** FBE with metadata encryption and hardware-wrapped keys; TEE
  Gatekeeper backoff (expected).
- **Remaining:** no passphrase policy (the development device uses a PIN); no
  StrongBox or Weaver, so throttling rests on the TEE, a larger surface with a
  public history of key-extraction attacks; backoff timing and survival across
  reboot and image restore unverified.
- **Status:** Observed gap: passphrase policy not implemented (FP6-070,
  FP6-071); throttling unverified. Observed (bring-up): FBE (policy version 2)
  with wrapped keys and metadata encryption running. Unverified: encryption and
  hardening validation (FP6-046).

#### Device-persistent data outside userdata

- **Threat:** physical attacker with flash access, or an app on a permissive
  build, via the persist partition, modem file systems, eUICC and hypervisor VM
  storage.
- **Protection:** SELinux labels; unsafe stock permissions closed.
- **Remaining:** factory reset clears none of these; hardware identifiers and
  calibration readable from a flash dump; modem and GNSS state persists.
- **Status:** Observed gap: no reset-residue inventory; some stock permissions
  still to be tightened. Unverified: debug exposure audit (FP6-060), eSIM erase
  (FP6-089).

#### Removable media

- **Threat:** anyone who takes the microSD card or USB storage.
- **Protection:** none; removable media are not encrypted.
- **Status:** Not implemented (FP6-076, FP6-077).

### Boot chain, firmware and trusted execution

#### OS integrity on a released, locked build

- **Threat:** write access to partitions, via the boot chain, OTA, recovery,
  sideload and the inactive A/B slot.
- **Protection:** public test builds are signed with the public AOSP/AVB test
  keys, carry test-keys in the fingerprint and a never-lock record, and
  `flash-steps` prints the never-lock rule first and prints commands only for an
  image set whose verify report matches it and passed.
- **Remaining:** no locked configuration exists: development images run
  unlocked. A test-key build must stay unlocked, since anyone can sign images
  with the public test keys. The `flash-steps` wipe writes Fairphone's factory
  FRP image (clearing factory reset protection and keeping OEM unlocking
  allowed) and zeros misc, as Fairphone's own factory flash does. Installing and
  recovering depend on Fairphone's unlock service.
- **Status:** Observed gap (bring-up images): SHA-1 hashtrees, release-style
  rollback indexes and a public test key. Observed (bring-up): AVB chain built
  and parsed. Unverified: signing verifier (FP6-035).

#### Firmware security

- **Threat:** exploits of already fixed bugs in XBL, TrustZone, hypervisor,
  modem, DSP, Wi-Fi and Bluetooth firmware, or peripheral controller firmware
  files in `/vendor/firmware`.
- **Protection:** per-image firmware inventory with hashes
  ([`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json));
  controller firmware files are exact, hash-pinned stock files.
- **Remaining:** the project cannot build or sign firmware; no firmware update
  path; firmware lags ASB; Gunyah and its trusted VMs are closed. As on stock,
  the NFC HAL may update its controller from its firmware file. Since the
  2026-09-27 build, the touch driver writes its file to the touch controller
  whenever versions differ or the controller's version is unreadable, checking
  only the header, so verified boot is what keeps that file authentic.
- **Status:** Observed gap: firmware stays at the last flashed stock release.
  Unverified: firmware review (FP6-206), stock input verification (FP6-040).

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
- **Status:** Observed gap (builds before the 2026-09-26 build): an unused
  userspace TEE proxy ran; removed from the 2026-09-26 build on.
  Bring-up (not qualified): services run; enforcing and throttling persistence
  unverified. Unverified: TEE and credential validation (FP6-046, FP6-050),
  attestation limits (FP6-065, FP6-106).

### Supply chain, signing and development process

#### Source and build outputs

- **Threat:** compromised upstream host or project, build dependency or build
  host, via source fetch, prebuilt toolchains, the kernel prebuilts, the build
  account and the image tools built from source.
- **Protection:**
  - Builders trust the DiamaneOS manifest branch. It keeps GrapheneOS and AOSP
    projects at the exact commits of the GrapheneOS release it is based on,
    whose signed tag the maintainer verifies when merging that release (recorded
    in the merge commit); Fairphone and CodeLinaro projects are pinned to exact
    commits; DiamaneOS projects follow their `android17` branches. The pinned
    `repo` tool's tag signature is checked. Each build records the resolved
    manifest (every project's commit) in its image set, and a full source
    preflight (clean projects at their resolved commits, no local manifests,
    nothing undeclared) runs at sync and before and after every Android build.
    Downstream commits are signed, but the public build commands do not verify
    those signatures.
  - The generated vendor tree is installed only when the recipe digests its
    generation recorded in its provenance equal the checkout's recipes, and the
    preflight accepts it only while a descriptor binds it to the environment
    file and those digests and every file matches its inventory.
  - The kernel, modules and device trees come from the kernel prebuilts the
    manifest selects; `build verify` checks boot, vendor_boot, dtbo and the
    module lists against that checkout at the commit `build.json` names.
    `kernel publish` refuses a kernel build whose files differ from its
    inventory, that contains private key material or strings naming the build
    host, or that was built from a modified tools checkout.
  - Network-denied compilation: the public build commands compile in an
    unprivileged network namespace, with agent and bus socket variables removed
    from the compile environment.
  - Image tools built from the pinned source with their hashes recorded; one
    build/make fork, whose single change keeps the device's boot header fields
    when images are rebuilt, pinned like the other forks; every build recorded
    in `build.json`.
- **Remaining:**
  - Qualcomm/CodeLinaro and Fairphone sources and toolchains carry no upstream
    signatures; common inputs are common-mode.
  - The public build runs as an ordinary user on an unmanaged host, without a
    separate build account, root-owned tools checkout or service sandbox.
  - The network namespace blocks IP networking only: Unix sockets in the
    filesystem stay reachable, so a socket to a service with network access (a
    container daemon, for example) is a way out, and the build can read whatever
    the user can.
  - Host packages are recorded, not pinned; a build with `--allow-network`
    compiles with network access (recorded for the kernel, vendor and Android
    steps); a modified tools checkout is recorded as not reproducible.
  - The manifest branch, the DiamaneOS branches it follows and the tools in it
    (`tools/diamaneos`, branch `main`) are not tagged or signature-checked at
    build time: a builder takes what the branches hold when it syncs, so a
    compromised maintainer account or GitHub repository is caught only by
    review of the recorded resolved manifest. Builders do not re-check
    GrapheneOS's signed tag; comparing the manifest's GrapheneOS projects with
    GrapheneOS's signed manifest is a manual step. The maintainer keys that
    sign the commits are not published.
  - The kernel prebuilts are binaries a maintainer built and published; a
    builder checks them against the published commit, not by rebuilding.
    `kernel build` can rebuild them from `kernel_qcom-6.1` for comparison, but
    module signatures and the embedded certificate differ per build.
- **Status:** Observed gap (FP6 path): every FP6 build so far ran on one host;
  builds made with the earlier build scripts compiled with network available; no
  second independent build. Bring-up (not qualified): the 2026-10-05 build ran
  the public build commands (syncing the DiamaneOS manifest branch, network-off
  compilation, the full preflight, bound generated inputs and the kernel
  prebuilts check), and its build record shows network isolation on, a clean
  tools checkout and the resolved manifest. Recorded (generic target): a
  network-denied build path. Unverified: reproducible environment.

#### Closed vendor inputs

- **Threat:** compromised vendor download or support page, or a network attacker
  at first download, via the stock factory package.
- **Protection:** exact archive hash pinned everywhere; the public build
  downloads it only over HTTPS from Fairphone's official host and uses it only
  if size and SHA-256 match; per-file hash, component and purpose; fail-closed
  extraction.
- **Remaining:** the archive and its published hash come from the same vendor
  web estate, and a new package can be selected before Fairphone publishes its
  hash (FP6.QREL.16.111.0 was); no vendor signature check; per-file purpose
  partly generic; name-loaded libraries declared for few files, with no build
  check (a missing one broke a bring-up boot).
- **Status:** Observed gap: per-file necessity and name-loaded dependencies
  incomplete. Bring-up (not qualified): extraction fail-closed and hash-bound.
  Unverified: stock input verification (FP6-040), firmware review (FP6-206),
  closure gate (FP6-208).

#### Release signing keys

- **Threat:** key theft or misuse, or a targeted signed build, via the signer
  host, hardware tokens, signing media and maintainers.
- **Protection:** none for the FP6: development images use public test keys; the
  signing tooling is qualified only with generic dummy keys.
- **Status:** Not implemented for the FP6 (FP6-035, FP6-036).

#### Repositories, domain, install page

- **Threat:** phishing, compromised workstation or assistant session, or
  malicious contribution, via code hosting, registrar, DNS, mail and developer
  keys.
- **Protection:** hardware MFA; separate signing and authentication keys;
  protected branches; human review of security-relevant changes; least-privilege
  assistant access.
- **Remaining:** one-person review capacity.
- **Status:** Recorded. Unverified: account and workstation hardening, branch
  protection.

#### Exposure window for known bugs

- **Threat:** n-day exploitation of platform, kernel, vendor code, firmware or
  browser.
- **Protection:** none; nothing is released.
- **Remaining:** vendor and firmware fixes depend on Fairphone and Qualcomm and
  lag; open-source libraries bundled in closed vendor files miss platform fixes.
- **Status:** Observed gap: bring-up platform and vendor patch levels lag
  available upstream releases; no automated upstream intake. The vendor patch
  level a build reports is the one the stock vendor image of its vendor files
  sets, read at build time and checked in every image set (since the
  2026-10-05 build; Settings shows it as the vendor security update).

#### Shell fork rebase lag: GrapheneOS security fixes under the Tally shell

- **Threat:** n-day exploitation of a fix not yet shipped or lost while
  rebasing, via DiamaneOS's Tally changes to frameworks/base (SystemUI, WM
  Shell, SettingsLib and core resources) carried onto each GrapheneOS release.
- **Protection:** per-area commit series, mostly new files wired in through
  SystemUI's dependency injection, so upstream files change little; Tally code
  behind one build-time flag (fixed read-only, on in DiamaneOS's release
  config), so an area can be dropped or the flag turned off to take a GrapheneOS
  security release without waiting for the Tally changes to be rebased.
- **Remaining:** the flag helps only while Tally code still builds on the new
  release, and switches a resource off only where the resource names it
  (SystemUI, WM Shell and the shared clock library read it). If a later
  GrapheneOS release moves WM Shell's activity transitions to its new transition
  planner, Tally's page motion falls back to stock until hooked there. Keyguard,
  privacy-indicator and biometric conflicts need careful manual merges;
  one-person review capacity.
  The bouncer reads Tally token resources when built, so broken tokens would
  break PIN and password entry: every build is tested by unlocking with a PIN
  and a password, flag on. The foundation's colour fixes (SystemUI's "already
  applied" check, the Sodium fallback) also apply with the flag off; they change
  colours only, so the flag-off build is GrapheneOS's in behaviour, not byte for
  byte.
- **Status:** Bring-up (not qualified): the Tally code and its flag run in the
  2026-09-29 and 2026-09-30 Tally test builds. Unverified: rebase and build of
  each GrapheneOS release with the flag on and off (FP6-211).

#### Install-time trust

- **Threat:** impersonating site or mirror, or a tampered image set, via image
  downloads and the flashing commands.
- **Protection:** `flash-steps` prints commands only for an image set whose
  verify report matches it and passed, and prints the never-lock rule first for
  test-key builds ([OS integrity](#os-integrity-on-a-released-locked-build)).
- **Remaining:** no installer and no key enrolment exist; stock restore inputs
  are authenticated by the vendor's published hash, not a signature.
- **Status:** Not implemented: installers and key enrolment.

### Everyday use

#### Correct user decisions

- **Threat:** user error, confusing warnings or inaccessible flows, in setup,
  permissions, updates, backup and restore, and recovery.
- **Protection:** inherited GrapheneOS flows only.
- **Status:** Not implemented: no DiamaneOS-specific measures.

#### Typed text and personal words

- **Threat:** someone with the device before first unlock, a backup copy or a
  view of the screen, via the keyboard's learned, personal-dictionary and
  contact word lists, backups, and fields that ask not to be learned from
  (incognito).
- **Protection (DiamaneOS keyboard fork):** learned, personal and contact words
  only in credential-encrypted storage, loaded after unlock; old
  device-encrypted copies deleted at the first locked boot after the update
  (LOCKED_BOOT_COMPLETED), or at first dictionary setup or first unlock if those
  come first (an update never sends MY_PACKAGE_REPLACED); backups off;
  no-learning and password fields never learned from or shown learned words; no
  typed or personal data in logs; unused network, account and sync permissions
  and dormant entry points removed; no network permission (already true).
- **Remaining:** the recent-emoji list stays readable before first unlock.
  Builds without the fork (the inherited keyboard) keep these words in storage
  available before first unlock, allow them in backups and ignore the
  no-learning flag.
- **Status:** Bring-up (not qualified): the keyboard fork is in the manifest and
  the test builds. On two 2026-09-30 test builds old copies stayed on the
  update's first boot until the keyboard started or the phone was unlocked
  (-150); a later one deletes them at the locked boot, verified on the FP6
  (planted word lists gone before the first unlock). The rest of the phone check
  was inconclusive (learned words are off by default, password fields never
  compose). Unverified: keyboard fork tests and phone checks (no owning task
  yet).

#### Privacy indicators and disclosures under the Tally shell

Protects: knowing when the camera, microphone or location is in use or the
screen is captured, which app is asking, whether the device is managed or on a
VPN, and what a locked phone shows.

- **Threat:** an app using a sensor or capturing the screen while the user reads
  its interface or while fullscreen; an app asking for a biometric or
  credential; someone looking at a locked phone. Surfaces, all restyled by the
  Tally shell (FP6-211): status bar privacy chip and lamps, capture chips,
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
    crash removes every privacy indicator until restart. Location alone runs
    the status bar chip on each app's first use in 10 minutes (stock's debounce;
    the flag that GrapheneOS leaves off is on), then the dot and the location
    lamp by the lens; a camera or microphone use that starts while location
    alone shows the dot runs the chip (-151, a Tally regression against stock's
    dot colour change, fixed), and a sensor that joins while a chip shows
    updates that chip, so the dot ends with the current items (-152, a stock
    fix). System location use and background use without a foreground service
    show nowhere, as in stock.
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
- **Status:** Bring-up (not qualified): the SystemUI indicators and lock screen
  run in the 2026-09-29 and 2026-09-30 Tally test builds, after security reviews
  and prototype privacy checks at 100, 150 and 200 % text; phone tests of those
  builds found and fixed findings listed in the
  [history](THREAT_MODEL-HISTORY.md). Unverified: a phone test of every listed
  state (FP6-211).

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
- **Status:** Bring-up (not qualified): host review and fuzzing with the pinned
  library, 2026-09-30 (20 million path inputs, about 154 million mutated
  archives: no crash, hang or escape); zip_ng on in vendor_diamaneos android17
  (770d976), in two 2026-09-30 test builds. Files trash is off (-140).
  Unverified: phone extraction and picker tests.

## FP6 source and firmware boundary

- **Sources:** kernel and platform HAL sources follow Qualcomm's CodeLinaro
  release `LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS
  `kernel_common-6.1` release merged into the vendor kernel; FP6 device trees,
  the Samsung NFC sources and stock images come from Fairphone. Source
  availability does not prove that GrapheneOS or Pixel patches apply, that
  modules meet the selected KMI/UAPI, or that binaries reproduce stock.
- **Closed components:** the camera, radio and IMS, secure-world, sensor, DRM
  and much of the graphics and media runtime depend on proprietary userspace or
  firmware. The bring-up selection holds 679 closed stock files (about 335 MB):
  camera 211, radio and IMS 154, sensors 84, display and GPU 74, audio 41,
  credentials 36, remote-processor services 24, thermal 7, plus
  smaller Bluetooth, NFC, GNSS and fingerprint sets.
- **Stock input:** `FP6.QREL.16.111.0` for the EU (`FP6.QREL.16.100.0` until
  2026-09-30); its verified factory package is the authoritative extraction
  input. Vendor generation uses an explicit per-file recipe (partition, path,
  hash, component, purpose); purposes are partly generic and consumer bindings
  partial. The generator excludes per-device identity, modem NV/EFS,
  calibration, provisioning, DRM, attestation, keystore and userdata material.
  `FP6.QREL.16.104.0` (US region) is a comparison and validation input, not an
  EU restore input; US-region devices are unverified.
- **Removals and replacements:** a removal must remove the complete reachable
  service and declaration path and pass subsystem tests; an open-source
  replacement needs exact licence compliance and must preserve security and
  capability (a software fallback is not automatically safer than proprietary
  hardware-backed code).
- **Firmware:** hashed per image in
  [`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json),
  not delivered by DiamaneOS.
- **Licences and records:** imported or modified open-source code stays
  fail-closed on per-file licence, notice, attribution and corresponding-source
  obligations. Exact source revisions, interfaces and experiments are in
  [`config/fp6-sources.json`](../config/fp6-sources.json) and
  [`config/fp6-capabilities.json`](../config/fp6-capabilities.json).
- **Vendor compatibility:** the stock vendor declares VINTF target level 8 with
  vendor API 34; bring-up builds combine it with the Android 17 framework.
  Product assembly must pass `checkvintf` and applicable VTS checks without
  weakened enforcement or broad compatibility shims.

## Fresh-UI boundary

- Shared visual tokens and components carry no platform authority; credential,
  update, backup, network and content-processing authorities stay separate.
- Each nontrivial Settings, launcher or SystemUI change needs demonstrated
  benefit, an owner, measured rebase cost and regression checks.
- Framework authorities are not replaced; no shared visual controller gains
  platform authority.
- The system font is Sofia Sans Tally (Sofia Sans, OFL 1.1, scaled to Roboto's
  metrics), built reproducibly from the pinned upstream file. Every app parses
  it, so it passes OTS sanitising and fontTools decompilation before it ships;
  it is read-only on the verified product partition. Apps can see it (a small OS
  signal among many); WebView keeps the platform fonts, so websites see no
  difference from GrapheneOS.

Every Tally shell commit (FP6-211) also keeps the indicator and disclosure rules
under [Privacy
indicators](#privacy-indicators-and-disclosures-under-the-tally-shell), the
rebase rule under [Shell fork rebase
lag](#shell-fork-rebase-lag-grapheneos-security-fixes-under-the-tally-shell),
and the per-component rules in [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md):
lock screen and biometric prompt, transitions, Quick Settings and the shade,
Recents' Stop, Settings switches, Home and All apps, Settings search, Clock,
Calculator, recovery titles, motion, the Settings homepage, branding, and rules
learned from findings.

## Decision record

Decisions that define current behaviour:

- **2G (2026-09-25):** 2G stays allowed by default (AOSP/GrapheneOS default);
  "2G network protection" and LTE-only are offered as opt-in hardening.
- **eSIM (2026-09-27):** OpenEUICC is not used (GrapheneOS os-issue-tracker
  #6275 and #2631). The stock LPA cannot list profiles on the FP6, so its
  service is off from the 2026-09-27 build (stock leaves it on); eSIM follows
  GrapheneOS (installed profiles keep working).
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
  do, with the user-mode-only restriction. 2026-09-27: KPROBES stays on, since
  the USB controller glue implements its controller hooks with kretprobes;
  lockdown blocks probes from user space.
- **debugfs (2026-09-27):** stays as in the 2026-09-26 build, built in and
  mountable: user builds never mount it, debuggable builds until boot completes,
  SELinux governs access. In this kernel, turning mounts off also removes the
  in-kernel interface the display driver and recovery need. 2026-10-01: both
  kernel forks fix that mode, so kernel code keeps the interface while debugfs
  cannot be mounted at all, not even by root; the kernel policy requires it
  (in builds since the 2026-10-01 build, not phone-tested).
- **pstore/ramoops (2026-09-27):** on for bring-up builds with GrapheneOS's
  Pixel layout; reboots stay cold (the kernel default). A warm reboot would keep
  the saved logs but also all of RAM. Details under [Previous boot's kernel log
  and event logs](#previous-boots-kernel-log-and-event-logs).
- **IMS:** 2026-09-25: the closed Qualcomm IMS stack runs under an explicit
  permission allowlist. 2026-09-26: the IMS data connection is brought up by
  DiamaneOS's own code (a small modem-facing service and a one-permission app)
  instead of Qualcomm's connectivity engine.
- **Call audio (2026-09-26):** since the 2026-09-26 build, DiamaneOS's own
  call-audio bridge, with no audio-routing permission, replaces the call-audio
  messenger; the audio-routing grant is gone.

## Architecture and verification review

Paths reviewed: remote (network observer, DNS and captive portal); remote media
(remote media, video device nodes); malicious app (permission and background
listener; camera permission, camera service and closed camera provider, with
grants bound to its domain, reduced groups and no network under enforcing
SELinux; flashlight controls reach the closed provider without the camera
permission, with simple on/off and strength values only, as AOSP designs it);
cellular (fake base station, 2G fallback, the user's hardening choice, the
modem; a build before 2026-09-26 did not pass the choice to the modem, current
source does, not yet re-checked on the phone); cross-profile and physical
(secondary user or profile, user-switch challenge (FP6-046, FP6-071); AFU and
BFU, USB, EDL and lock screen, duress). Device-dependent claims stay unverified until checked.

## How this document is maintained

- This document shows only the current state. Every feature that is built
  updates it in the same change.
- Every security or privacy finding updates this document and the private
  findings register together with the work that found it (different
  repositories, so not one commit).
- Every security or privacy change updates this file and
  [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md) (revision history, IMS
  integration notes) in the same commit, and
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) when a Tally shell rule
  changes.
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
  build time.
- **Bring-up:** early work to make the hardware function; bring-up and test
  builds are private development images, named here by date.
