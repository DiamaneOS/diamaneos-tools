# DiamaneOS Threat Model and Product Boundaries

Revision: 2026-09-25 (full refresh; replaces the initial model of 2026-09-14).

Status: DiamaneOS is an OS under development. The protections below are
requirements unless a row's evidence state says otherwise. No row is qualified
on a release candidate yet. No hardware claim is a demonstrated protection until
its validation passes.

> Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project.

## Current product scope

DiamaneOS targets daily use by privacy- and security-focused users. This threat
model covers the GrapheneOS-based Fairphone 6 port (one device, one variant).
Release requirements include a locked bootloader on a custom AVB root and
monthly releases. The launch scope below must pass qualification before public
release. No root, no microG, no signature spoofing, no Magisk accommodation, no
unlocked-bootloader daily use, no bundled cloud account.

A clear, expressive UI with measured maintenance; accessibility and
localization are acceptance criteria, not polish. No global security score, no
stock-parity promise, no superiority claim without dated like-for-like
evidence.

GrapheneOS-derived describes source lineage. It does not establish security
equivalent to GrapheneOS on its supported devices. The Fairphone 6 lacks
several hardware features GrapheneOS relies on (see "Hardware limits").

PIN-optional is not in v1. Passphrase-first is mandatory (passphrase onboarding
and credential-policy enforcement). A future PIN-optional-with-warning change
needs an explicit scope decision and a renewed compatibility review; it is
recorded only as a revisit condition.

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

On the current Qualcomm radio software, "cellular alerts-or-honest-unsupported"
resolves to "honest unsupported" (see the cellular rows).

Several v1 features add code that parses untrusted input or needs privilege.
They are threats in their own right (see "DiamaneOS-added features").

## Current development state

Everything that runs today is a private bring-up build on a development
device. No build has been distributed. Bring-up builds deliberately trade away
protections to make hardware work. The table separates what bring-up builds do
from what a release must do. A release candidate that fails any gate in the
last column is not released.

| Area | Private bring-up builds today | Release requirement | Gate |
| --- | --- | --- | --- |
| Build type | `userdebug`, ADB on by default, `adb root` available; non-user builds pre-trust a developer ADB key when one is provided | `user` build, `ro.debuggable=0`, ADB off and authenticated, no pre-trusted keys, no adb over network | Production-form user candidate (FP6-047), debug exposure audit (FP6-060) |
| SELinux | Whole system permissive; no MAC protection is enforced | Enforcing, no permissive domains | Enforcing runs per subsystem (no owning task yet), user candidate (FP6-047) |
| Bootloader and keys | Unlocked; images signed with public AOSP test keys | Locked on a DiamaneOS AVB key; release keys only; relock never enrols a public test key | Custom-key relock (FP6-050), signing roles and equipment (FP6-035, FP6-036) |
| Network endpoints | Inherited GrapheneOS services (connectivity, time, CT list, provisioning proxies, app catalog, SUPL proxy) | DiamaneOS EU endpoints with visible standard-server choices | Endpoint contracts (FP6-100), endpoint implementations (FP6-102 to FP6-112) |
| Closed vendor code | 741 stock Qualcomm/Fairphone files (about 369 MB) selected stock-first, all hash-pinned; per-file purpose still generic for part of the set | Each file justified; layers replaced by source builds where possible | Component removal and closure gate (FP6-060, FP6-061, FP6-208), per-component source replacement (FP6-200 to FP6-207) |
| Debug interfaces | Userspace Qualcomm diag removed; debug USB functions still to be removed | No debug USB functions, diag or trace sinks in user builds | Debug exposure audit (FP6-060) |
| Firmware | Whatever stock release was last flashed | Dated firmware patch level shown; firmware update path defined | Firmware review (FP6-206) |

Rule: bring-up builds are never handed out and must not be used as a daily
phone for other people. The bring-up signing and SELinux state provides no
verified-boot, signature-permission or SELinux protection. A device running a
bring-up build is not a protected device: personal accounts and sensitive data
do not belong on it until SELinux is enforcing.

## What DiamaneOS aims to defend against

- Remote and proximity attack surface: network (including IP traffic over
  cellular data), Bluetooth, NFC, USB and media parsing. The defence is the
  inherited GrapheneOS hardening plus a reduced vendor surface. FP6 has no
  hardware memory tagging, so memory-safety bugs are not contained the way they
  are on current Pixels.
- Malicious or over-permissioned apps: sandboxing, Network and Sensors
  permissions, Storage and Contact Scopes, install-time presets.
- Persistence after compromise: verified boot with rollback protection on a
  locked bootloader.
- Opportunistic physical access to a powered-off (BFU) device: file-based
  encryption with hardware-wrapped keys plus a strong generated passphrase.
- Data exfiltration by the OS vendor: no telemetry, no Google Mobile Services,
  self-hosted or user-selectable endpoints (documented EU endpoints with visible
  alternatives).

## What DiamaneOS does not defend against

- Offline brute force of a weak credential. Throttling is expected to exist:
  Gatekeeper enforces retry backoff in the Qualcomm TEE. On FP6 its timing and
  whether it survives reboot and image restore are unverified. There is no
  discrete secure element exposed to Android, so throttling rests on the TEE, a
  larger attack surface with a public history of key-extraction attacks. A 6 to
  8 word generated passphrase (required; not implemented yet) makes guessing
  much harder. It is not a secure-element replacement and does not fix TEE,
  firmware, AFU or credential-capture weaknesses.
- Sophisticated forensic extraction from an unlocked or powered-on locked (AFU)
  device.
- Firmware-level compromise. Boot, TrustZone, hypervisor, modem, DSP, Wi-Fi and
  Bluetooth firmware come from Fairphone and Qualcomm; the project cannot build
  or patch them.
- Baseband exploitation beyond what SoC isolation provides. On FP6 that
  isolation is assumed, not yet verified.
- Detection of fake base stations, null-cipher sessions or identity requests.
  The current Qualcomm radio software is reported not to support these
  notifications (bring-up notes; the radio daemon's own response has not been
  captured yet).
- Traffic the modem sends by itself (IMS, SUPL and control-plane location,
  emergency location). Android's network controls do not see it.
- Removal of eSIM profiles, modem state or factory calibration by a factory
  reset. These live outside the user data partition.
- Vendor-controlled unlock. Installing, and recovering from a bad flash, depend
  on Fairphone's online unlock service. This is a dependency, not a security
  gap.
- A determined adversary with unlimited physical access and time.

## Hardware limits and evidence

- No MTE: the stock CPU feature set does not expose memory tagging, and its
  control properties are absent.
- No StrongBox or Weaver exposed: stock declares only default TEE KeyMint and
  RKP instances and no Weaver HAL. Stock does ship disabled software for a
  Secure Processing Unit; whether the FP6 has a usable, provisioned SPU is
  unknown. The accurate statement is "stock exposes no StrongBox or Weaver",
  not "the phone has no secure hardware". The eUICC, NFC controller and TEE are
  separate boundaries.
- Attestation is TEE-only. A locked custom-key build reports a yellow verified
  boot state, never green. The GrapheneOS Auditor app does not support FP6. Key
  provisioning goes through the inherited GrapheneOS proxy on bring-up builds.
- No pKVM: on current firmware the kernel runs under Qualcomm's Gunyah
  hypervisor, not KVM, so Android protected VMs are unavailable (observed on a
  bring-up build). Gunyah and its trusted VMs are closed firmware.
- USB-C port control: the GrapheneOS port-control feature is not wired up on
  FP6. The merged kernel source contains the deny-new-USB hook; whether the
  built FP6 kernel exposes it is unchecked. There is no evidence of a hardware
  USB data-line cutoff.
- Fingerprint: the DiamaneOS fingerprint HAL currently declares the Class 3
  (strong) biometric level. That is a declaration, not a measurement: spoof
  resistance has not been tested. The project does not treat the fingerprint as
  strong authentication until spoof testing passes; a release must measure it
  or declare a lower class.
- Radio isolation: SoC-level isolation of the modem from the application
  processor is assumed and unverified on FP6.
- Firmware patch lag: firmware lags Android Security Bulletins, and DiamaneOS
  has no firmware update path yet.
- Kernel: the kernel line is 6.1 (android14 KMI) with a recorded, intentional
  deviation for a 48-bit virtual address space.
- TEE KeyMint, Gatekeeper and attestation behaviour, custom-key relock, A/B
  failure recovery and rollback behaviour still require candidate tests.

## Evidence states

Each row carries one of these states. A state changes only with new evidence.

- **Assumption**: plan text; no design or device evidence yet.
- **Designed**: design or configuration reviewed; not running on a device.
- **Bring-up (not qualified)**: present in a private bring-up build; the
  security property itself is untested, and SELinux is not enforcing.
- **Observed (bring-up)**: a narrow behaviour was seen on a bring-up build.
- **Recorded**: stated in a project record, not rechecked for this revision.
- **Observed gap**: the protection is confirmed missing or not working today.
- **Accepted limitation**: a documented limit the project does not plan to
  remove.
- **Qualified**: the named validation passed on a release candidate. No row is
  qualified yet.

Rule: a row's state is the weakest state among its listed mitigations. The
evidence cell names that weakest state first; stronger items follow it.

## Threat rows

Columns: asset | attacker capability | entry point | intended mitigation |
remaining limit | validation | evidence state. Rows are grouped by attacker
position: remote, cellular, malicious app, proximity, physical AFU, physical
BFU, boot and firmware, supply chain, everyday use. Validation names a plan
task in parentheses where one owns the check, or says that none does yet.

### Remote attackers (network and content)

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Service metadata and OS identity | Network observer; endpoint operator | Connectivity checks, captive-portal probes, DNS checks, HTTPS time, CT list, key and DRM provisioning, app and browser updates, release feed; phone-side eSIM (SM-DP+/SM-DS) and carrier entitlement connections | No telemetry or GMS; EU-primary DiamaneOS endpoints with documented upstreams; each endpoint has a visible standard-server alternative; eSIM and carrier hosts listed as non-project endpoints (they are ordinary app traffic and visible to Android network controls) | Small-user-base hostnames are a fingerprint; the HTTPS time bootstrap resolves outside Private DNS; non-EU upstreams disclosed per endpoint; certificate-transparency enforcement fails open when the CT list is more than 70 days old, so CT mirror freshness is a security dependency; "no telemetry" in the closed vendor files rests on a static scan, runtime egress not yet measured | Endpoint contracts (FP6-100), time, connectivity, provisioning, CT and catalog endpoints (FP6-102 to FP6-112), runtime egress capture with SELinux enforcing (no owning task yet) | Assumption: 11 of 14 endpoint contracts are not yet specified; eSIM and carrier egress not yet in the contract. Designed: 3 of 14 contracts specified, none deployed. Bring-up builds use the inherited GrapheneOS endpoints. Observed (static): no contacted host found in the selected closed files apart from GNSS cloud hosts removed by pinned configuration. |
| DNS privacy | Network observer; on-path resolver | DNS queries | Visible encrypted-DNS default (DoT) with no silent plaintext fallback | Time bootstrap, apps with their own DoH/DoT and modem traffic bypass it | Encrypted-DNS default and DNS filter (FP6-079) | Assumption: not implemented; the inherited Android default is in force. |
| App data from remote exploit | Remote attacker via network, media or web content | Carrier and Wi-Fi data, media files and streams, browser and WebView, captive portals, web content reaching the GPU shader compiler, hardware video decode (planned) | Inherited sandbox, hardened_malloc, exec spawning; Vanadium browser and WebView as mirrored unmodified APKs; hardware codec service to keep the stock seccomp sandbox and the platform codec domain; codec device nodes to be separated from camera nodes and from display configuration | No MTE on FP6; closed GPU driver and shader compilers run in every app process; closed codec and video firmware (not yet integrated); codec seccomp installation is unverified and can fail open; captive-portal WebView inherited; browser component updates still point upstream | Inherited hardening (FP6-046), debug exposure and component removal (FP6-060, FP6-061), hardware media integration (no owning task yet), DRM limits (FP6-065), browser delivery (FP6-105, FP6-111) | Assumption: exec spawning, sandbox permissions, codec sandbox and browser delivery unvalidated on FP6. Observed (bring-up): hardened_malloc active (48-bit VA kernel). |
| Traffic outside Android network policy | Carrier; network observer | Modem IMS signalling and media, SUPL and control-plane location, network-initiated location, emergency location | Documented; carrier hosts listed as non-project endpoints; privacy dashboard and per-app routing state that they do not cover this traffic | Invisible to the Network permission, VPN, Private DNS, the DNS filter and the dashboard | Endpoint contracts (FP6-100), assisted GNSS (FP6-103), carrier configuration review per supported carrier (FP6-090) | Accepted limitation. Carrier egress not yet listed in the endpoint contract. |

### Cellular and baseband

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Call and SMS content, subscriber identity, coarse location | Fake base station, IMSI catcher, downgrading network, null-cipher network | 2G fallback, pre-authentication identity requests, radio security events | 2G stays allowed by default (AOSP/GrapheneOS default; protects emergency reachability, especially when roaming); opt-in "2G network protection" and LTE-only controls; planned: the security status view shows cellular alerts as unsupported | The opt-in controls are visible today and fail open without any signal: the selected mode does not reach the modem; the planned default-mode fix covers new subscriptions only, so stored settings need a reset step and a regression check. No detection: null-cipher/integrity control and security notifications are reported unsupported by the current radio software. LTE/NR identity exposure remains; turning 2G off can block emergency calls when roaming | Telephony bring-up (FP6-044), cellular security notifications (FP6-084), security status view (FP6-073); a radio-policy regression check has no owning task yet | Observed gap (bring-up): controls visible and silently ineffective; they must be fixed or hidden before any build leaves development. Null-cipher control reported unsupported (bring-up notes; radio daemon response not yet captured). |
| SMS, SIM applets, broadcast alerts, carrier configuration | Network or SIM-side sender; carrier | SMS and class-0/type-0 messages, SIM toolkit proactive commands, cell broadcast, SIM access rules granting carrier privileges | Inherited AOSP handling; no project change | SIM applets act partly outside OS control; network "silent" SMS are acknowledged without display under 3GPP rules; SMS are parsed in the modem and the framework; apps the SIM grants carrier privileges can change carrier configuration | Telephony bring-up (FP6-044) | Accepted limitation (inherited). |
| Application processor and user data | Remote over-the-air attacker; compromised modem firmware | Modem protocol parsers (RRC/NAS, SMS, modem IMS), then shared memory, IPA data path, QMI/QRTR services, modem remote-storage and file services | Modem image authenticated by the stock secure-boot chain; AP-side modem services narrowed and confined to their own SELinux domains; modem crash dumps off | Modem firmware unpatchable by the project and behind ASB; SoC isolation (SMMU and memory protection) assumed, not verified on FP6; modem restarts happen silently, so a crash-and-retry attack is invisible; modem state persists across factory reset | Modem and telephony bring-up (FP6-042, FP6-044), debug exposure and component removal (FP6-060, FP6-061) | Assumption. Bring-up runs the modem and its AP services under permissive SELinux; no protection claim. |
| Telephony control and subscriber data | Malicious app via binder; compromised modem via QMI | Closed radio daemon (QCRIL), Qualcomm IMS app, call-audio service, eSIM LPA | Explicit privileged-permission allowlists with denials; own SELinux domains; no radio daemon socket; radio daemon not a binder service; diag access removed | Closed code parses untrusted input; locking the update path of presigned vendor apps is planned, not implemented; a call-audio permission decision is open; VoLTE work is expected to add further closed, network-capable daemons and a presigned privileged app, so the closed surface is still growing; enforcing mode untested | Telephony bring-up (FP6-044), eSIM selection and delivery (FP6-207, FP6-089), enforcing runs (no owning task yet) | Bring-up (not qualified): allowlists and domains reviewed; runtime and enforcing unverified. |
| eSIM profiles and download sessions | TLS interceptor; thief or examiner after reset; coercer | LPA connections to eSIM servers; profiles retained in the eUICC | A maintained hardware-layer LPA with least privilege (candidate: the Qualcomm LPA), disabled until the user enables it; GSMA SGP.22 mutual authentication | The candidate LPA's TLS trust configuration does not yet meet the production trust rule (a production blocker; how to meet it is an open decision); no Settings switch or download UI yet; factory reset and duress do not erase eUICC profiles; explicit erase untested | eSIM selection and delivery (FP6-207, FP6-089) | Bring-up (not qualified): installed and disabled; untested. |
| Ability to reach emergency services | None needed (safety failure); also misconfiguration, 2G off, VoLTE failure | Emergency number tables, emergency domain selection, radio technology availability, the emergency data connection on 4G/5G | IMS-first per carrier as on stock; the emergency data connection served by DiamaneOS's own service, built to answer the modem as stock's does and never to report a connection that is not up; 2G kept allowed by default | The closed modem decides domain, retries and fallback; no emergency calling on networks without 2G/3G until the emergency data connection works; no automatic caller location (AML) yet; emergency real-time text not configured yet; LTE-only, 5G-only and 2G-protection modes may limit fallback; no evidence yet from a shielded lab test | Emergency-call validation (FP6-027) | Assumption. |
| Radio-off expectation, location privacy | Network or passive observer | Modem attaching on its own; airplane mode | Airplane mode puts the modem in low-power mode | Not verified that the modem stays silent in airplane mode or without the radio daemon; a device setting keeps the SIM powered in airplane mode | Telephony bring-up (FP6-044) | Assumption. |

### Location

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Location history | Vendor cloud; assistance-server operator; carrier; local log reader | GNSS HAL configuration, SUPL, PSDS, control-plane and emergency positioning, logs | Qualcomm cloud, XTRA and crowdsourcing paths excluded by pinned configuration; PSDS off until an endpoint is bound; SUPL user-selectable (Off, proxy, standard); network location and geocoder off by default; IMS geolocation has no network geocoder | SUPL in the image defaults to the GrapheneOS proxy until the DiamaneOS SUPL endpoint exists; "standard" SUPL goes directly to the carrier-configured server (on the carrier tested so far, Google's server); SUPL requests carry cell information; control-plane and emergency positioning run in the modem; GNSS engine state on the persist partition survives reset; location logging level still verbose | Assisted GNSS (FP6-103), GNSS bring-up tests (FP6-045) | Assumption: SUPL default not yet decided; no GNSS fix recorded. Bring-up (not qualified): cloud paths absent and pinned. |

### Malicious and over-permissioned apps

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| App and user data | Malicious or over-permissioned app | Permissions, background listeners, IPC/URI grants, sensors | Per-app Network and Sensors permissions, Storage and Contact Scopes (inherited); install presets (Untrusted/Standard/Trusted); per-app routing and filtering; privacy dashboard with CE-only bounded history | Preset "Trusted" is user-chosen policy, never an audit; routing attribution limits (shared UIDs, own DoH/DoT, system and modem traffic); v1 features not implemented yet | Inherited hardening (FP6-046), privacy presets (FP6-072), DNS architecture and filter (FP6-078, FP6-079), per-app network controls (FP6-080), privacy dashboard (FP6-081) | Assumption: inherited controls present in source, unvalidated on FP6; v1 features not implemented. |
| Per-app destination history (dashboard) | AFU forensic examiner; malicious app | Dashboard storage, backups, bug reports | CE-only storage, aggregate by default, 7-day default, clear on reboot by default, no backup, no bug-report inclusion | The dashboard is itself the most sensitive log on the device | Privacy dashboard (FP6-081) | Assumption: plan rules only. |
| Kernel integrity and all data | Malicious app; compromised app or renderer process | GPU driver node (reachable by every app, as on stock), binder, loaded network-protocol modules and socket families, DSP remote-call driver (limited to named groups) | GrapheneOS kernel hardening configuration merged into the vendor kernel; kernel lockdown (confidentiality); SELinux socket and device restrictions; module set reduced to what the product uses | No MTE; large vendor driver surface; hardening hand-merged into a vendor kernel with an intentional KMI deviation; protocol modules with no product use still loaded; TIPC re-enabled (GrapheneOS disables it) because Qualcomm's mobile-data stack needs it, built as a module without network bearers or crypto and limited by SELinux to two radio daemons; profiling work must never weaken lockdown or tracing restrictions in user builds | Kernel build (FP6-041), debug exposure and component removal (FP6-060, FP6-061), enforcing runs (no owning task yet) | Assumption: module reduction not done, SELinux permissive. Observed (bring-up): lockdown confidentiality active. |
| Camera, microphone, sensor streams, device integrity | Malicious app or remote content reaching closed vendor code through a platform service | Camera, media, display, audio, sensors, GNSS, NFC and Bluetooth HAL interfaces; same-process GPU libraries; persist and vendor data files parsed by closed code | Per-file allowlisted, hash-pinned stock selection; SELinux grants bound to each service domain; reduced service users, groups and capabilities; tight device-node permissions; Qualcomm diagnostics, telemetry and factory services excluded; layers replaced by source builds over time | Large closed parsers (camera about 185 MB) without MTE and mostly without seccomp; closed performance and thermal daemons run as root; Android 14 ABI vendor code on Android 17; per-file purpose still generic for part of the set; closed code updates only through Fairphone stock releases | Debug exposure and component removal (FP6-060, FP6-061), closure gate (FP6-208), camera (FP6-045, FP6-203), audio (FP6-044, FP6-205), hardware media integration (no owning task yet), enforcing runs (no owning task yet) | Bring-up (not qualified): selection reviewed per subsystem and running permissive; enforcing untested. |
| Persistent hardware identifiers | Any app | System properties, persist files, sysfs, logs | Vendor-internal property types with no read grant, narrowed sysfs labels, random Bluetooth address per install (intended), Wi-Fi MAC randomization, traceability services excluded, enforcing SELinux | Readable on any permissive build; which Bluetooth address path runs is unrecorded (the HAL tries a factory address first) and the address appears in logs and bug reports; Wi-Fi MAC randomization unverified; a flash dump reveals persist data | Enforcing runs and an identifier probe test (no owning task yet), Wi-Fi and Bluetooth bring-up (FP6-043) | Observed gap (bring-up, permissive): some hardware serials are exposed as system properties; enforcing denial not yet shown. |
| Secondary-profile data | Other user or profile on the same device | User switch, stopped and running profiles, unified vs separate challenge | Same credential policy on every independent challenge via a real service boundary; CE/DE separation; session end is not deletion | Unified-challenge profiles follow platform semantics, no invented independent key; a stopped session is data-bearing until deleted | Encryption and hardening validation (FP6-046), credential-policy enforcement (FP6-071) | Assumption. |

### DiamaneOS-added features

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Device integrity and the data each feature handles | Remote content, malicious list or network source, nearby BLE device, malicious update file | DNS filter (network bytes, downloaded lists), share-time metadata stripping (image parsers), tracker alerts (BLE payloads), offline SD OTA (file input), per-app routing and battery controls (privileged hooks) | Unprivileged, isolated and resource-bounded parsing workers; scoped URI grants; signed simple list formats; offline OTA uses the same signature checks as online updates; least-privilege hooks; optional features off by default where the plan says so | Every feature adds code and privilege; platform image APIs can invoke native parsers; none is implemented yet | DNS filter (FP6-078, FP6-079), per-app controls (FP6-080), dashboard (FP6-081), tracker alerts (FP6-083), offline SD OTA (FP6-086), metadata stripping (FP6-087), battery (FP6-074, FP6-075) | Assumption. |

### Proximity attackers

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Device integrity, paired-device data, Bluetooth address | Nearby attacker over BR/EDR or LE; tracker | Closed controller firmware, closed stock Bluetooth HCI HAL, GrapheneOS host stack | GrapheneOS host stack; Bluetooth off by default; consent-gated profiles; SIM access profile off; random per-install address (intended); closed HAL confined (no network, no persist write, no diag); unknown-tracker alerts (v1) | Controller firmware and HAL unpatchable by the project; no MTE; live address path unverified and the address appears in logs; classic address stable per install; tracker alerts not built | Bluetooth and audio bring-up (FP6-043, FP6-044), debug exposure and component removal (FP6-060, FP6-061) | Bring-up (not qualified): pairing and audio work on a bring-up build; security properties and enforcing untested. |
| SIM applets, presence, NFC services | Nearby contactless reader, including against a locked, screen-off or switched-off phone; malicious tag | NFC reader and listen modes, controller-to-SIM routing | Default route pinned to the host (no SIM routing unless an app registers it); no UICC or embedded secure element features declared; controller firmware file under verified boot once locked | NFC is on by default (decision pending); registered SIM routes stay reachable while locked or off; secure-NFC support unverified; closed controller firmware, which the HAL can also update | NFC bring-up tests (FP6-045), eSIM/SIM (FP6-089) | Bring-up (not qualified): reader works; routing tests not run. |
| Wi-Fi MAC (tracking), device integrity | Passive Wi-Fi observer; malicious access point; LAN peer | Probe requests, association, Wi-Fi driver and firmware, wake-on-LAN | Station-only features exposed to Android (no hotspot, Wi-Fi Direct or Aware); source-built Wi-Fi HAL and driver; MAC randomization | MAC randomization unverified on this HAL and firmware; closed Wi-Fi firmware on the over-the-air path; a LAN peer can wake the device with a magic packet | Connectivity bring-up (FP6-043) | Assumption: unverified. |
| Locked-device data, kernel integrity | Malicious USB device or host; forensic tool; malicious charger | USB-C data lines (host and gadget roles), USB descriptors, USB PD and charger firmware | (Intended) GrapheneOS USB-C port control (charging-only when locked, deny new USB devices); standard gadget functions only; debug USB functions removed from user builds; reduced USB driver set | Port control not wired on FP6 yet; deny-new-USB hook present in kernel source, built kernel unchecked; no hardware data cutoff; broad USB host drivers loaded; the USB descriptor carries the device serial; charger firmware closed | Debug exposure audit (FP6-060), USB modes (FP6-045), hardware USB data disable (no owning task yet) | Observed gap: port control absent. Bring-up (not qualified): userspace gadget configuration reviewed; debug USB functions still to be removed from user builds. |

### Physical access to a powered-on, locked device (AFU)

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| AFU user data | Physical access to a locked, powered-on phone; forensic tools | USB, lockscreen, firmware download and dump modes | Inactivity auto-reboot (inherited); USB port control; OS refuses firmware-download and dump reboots; panic RAM dumps off | Port control not wired on FP6; OS refusal of firmware-download reboots not implemented; the kernel keeps a minidump on panic and where it can be retrieved is unverified; the early-boot setting that also turns dump mode off needs a policy grant before enforcing; EDL stays reachable with physical access and a signed programmer; no forensic-proof claim | Debug exposure audit (FP6-060), credential policy (FP6-071), scheduled reboot (FP6-085) | Observed gap: port control absent; download-reboot refusal not implemented. Observed (bring-up): panic dump mode off by kernel default. |
| AFU unlock, auth-bound keys | Attacker with a lifted or spoofed fingerprint | Side fingerprint sensor | Strong authentication after reboot and timeouts; lockout in the trusted app; vendor debug interfaces unreachable by policy; biometric class not claimed above what testing shows | The HAL currently declares Class 3 before any spoof testing; a release must measure spoof resistance or declare a lower class; fingerprint is not treated as strong authentication until then | Fingerprint bring-up (FP6-045), credential validation (FP6-046); spoof testing has no owning task yet | Observed gap: class declared, not measured. Bring-up (not qualified): unlock works; enforcing not done. |
| AFU data under coercion or seizure | Coercer; seizure followed by extraction | Lockscreen, buttons | Duress credential and wipe (inherited), panic-to-BFU reboot, scheduled reboot | Duress key destruction relies on TEE key deletion and flash erase, not a secure element; eSIM erase depends on the LPA; eSIM profiles may survive | Duress test with synthetic data (FP6-046), eSIM erase (FP6-089), panic-to-BFU (FP6-082), scheduled reboot (FP6-085) | Assumption: inherited code present, untested on FP6; panic and scheduled reboot not implemented. |

### Physical access to a powered-off device (BFU) and data outside userdata

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| BFU user data | Opportunistic physical access, powered off; flash readout (EDL, chip-off) | Flash contents, TEE | FBE with metadata encryption and hardware-wrapped keys; mandatory 6 to 8 word CSPRNG passphrase for the owner and every independent user or profile; no weak-credential path; TEE Gatekeeper backoff (expected) | No StrongBox or Weaver exposed; throttling rests on the TEE; backoff timing and whether it survives reboot and image restore are unverified; passphrase policy not implemented yet | Encryption and hardening validation (FP6-046), passphrase onboarding (FP6-070), credential policy (FP6-071) | Observed gap: passphrase not implemented (the development device uses a PIN); throttling unverified. Observed (bring-up): FBE v2 with wrapped keys and metadata encryption running. |
| Device-persistent data outside userdata | Physical attacker with flash access; app on a permissive build | Persist partition, modem file systems, eUICC, hypervisor VM storage | SELinux labels; unsafe stock permissions closed; reset residue documented | Factory reset clears none of these; hardware identifiers and calibration are readable from a flash dump; modem and GNSS state persists | Debug exposure audit (FP6-060), eSIM erase (FP6-089) | Observed gap: residue inventory not written; some stock permissions still to be tightened. |
| Removable media | Anyone who takes the card | microSD, USB storage | Encrypted microSD (v1) | Not encrypted today | Encrypted microSD design and workflow (FP6-076, FP6-077) | Assumption. |

### Boot chain, firmware and trusted execution

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| OS integrity on a released, locked build | Attacker with write access to partitions | Boot chain, OTA, recovery, sideload, inactive A/B slot | Locked bootloader on a custom AVB root, vbmeta flags 0; SHA-256 hashtrees; rollback indexes set only by signed releases; signed full and incremental OTAs from the same pipeline; recovery accepts release keys only; relock locks both lock states | Yellow boot (never green); downgrade-brick risk; Fairphone unlock service dependency; firmware not in OTAs; whether slot changes are refused while locked is untested | Custom-key relock (FP6-050), OTA install and interrupted-update tests, signing verifier (FP6-035) | Observed gap (bring-up images): SHA-1 hashtrees, release-style rollback indexes and a public test key contradict three listed mitigations. Assumption: locked behaviour untested. Observed (bring-up): AVB chain built and parsed. |
| Firmware security | Anyone exploiting an already fixed firmware bug | XBL, TrustZone, hypervisor, modem, DSP, Wi-Fi and Bluetooth firmware | Per-partition firmware inventory with hashes; firmware delivery in OTAs from verified Fairphone releases (to be designed); separate dated patch levels for platform, kernel, vendor and firmware | The project cannot build or sign firmware; no firmware update path yet; firmware lags ASB; the Gunyah hypervisor and its trusted VMs are closed | Firmware review and update path (FP6-206), stock input verification (FP6-040) | Observed gap: firmware stays at the last flashed stock release. |
| Keys, Gatekeeper throttling, fingerprint templates | Compromised system or HAL process; malicious app reaching TEE clients | TEE driver and client services, TEE listener daemon, fingerprint HAL | TEE access only for named HAL domains; unused TEE proxy services removed; RPMB access limited | Closed TEE with a public history of key-extraction bugs; no StrongBox or Weaver; an unused TEE proxy service still runs; KeyMint key deletion (rollback resistance) unverified | TEE and credential validation (FP6-046, FP6-050), attestation limits (FP6-065, FP6-106), security status reporting (FP6-073) | Observed gap: unused TEE proxy still running. Bring-up (not qualified): services run; enforcing and throttling persistence unverified. |

### Supply chain, signing and development process

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Source and build outputs | Compromised upstream host or project, build dependency or build host | Source fetch, prebuilt toolchains, build account | Signed GrapheneOS tags; immutable commit pins; signed downstream commits verified before build; network-denied compilation; two independent builds with final-content comparison | Qualcomm/CodeLinaro and Fairphone sources and toolchains carry no upstream signatures; common inputs are common-mode | Reproducible environment, dual build, release comparison | Observed gap (FP6 path): compiled with network available, on one host, with private scripts; the build identity is not yet separated from its own verification inputs; only a few projects are re-verified per build. Recorded (generic target): a network-denied build path. |
| Closed vendor inputs | Compromised vendor download or support page; network attacker at first download | Stock factory package | Exact archive hash pinned everywhere; per-file hash, component and purpose; fail-closed extraction; every name-loaded library declared (required; no build check yet) | The archive and its published hash come from the same vendor web estate; no vendor signature check yet; firmware images not individually hashed; per-file purpose generic for part of the set; name-loaded libraries declared for few files (a missing one broke a bring-up boot) | Stock input verification (FP6-040), firmware review (FP6-206), closure gate (FP6-208) | Observed gap: per-file necessity and name-loaded dependencies incomplete. Bring-up (not qualified): extraction is fail-closed and hash-bound. |
| Release signing keys | Key theft or misuse; targeted signed build | Signer host, hardware tokens, signing media, maintainers | Offline signer, token roles, 3-of-5 recovery, public release log before distribution | Single signing authority; the AVB root cannot be revoked without unlock and wipe; log inclusion does not mean benign; no signing role for kernel modules yet | Signing qualification (FP6-035, FP6-036) | Designed: generic dummy-key qualification only; no FP6 signing profile yet. |
| Repositories, domain, install page | Phishing; compromised workstation or assistant session; malicious contribution | Code hosting, registrar, DNS, mail, developer keys | Hardware MFA; separate signing and authentication keys; protected branches; human review of security-relevant changes; least-privilege assistant access; no untrusted code on release hosts | One-person review capacity | Account and workstation hardening, branch protection | Assumption: not yet qualified. |
| Exposure window for known bugs | n-day exploitation | Platform, kernel, vendor code, firmware, browser | Monthly releases within 7 days of GrapheneOS; urgent fixes within 48 hours; per-layer patch levels shown | Vendor and firmware fixes depend on Fairphone and Qualcomm and lag; bundled open-source libraries inside closed vendor files do not get platform fixes | Update cadence, patch dashboard | Observed gap: bring-up platform and vendor patch levels lag available upstream releases; no automated upstream intake yet. |
| Install-time trust | Impersonating site or mirror; malicious installer | Browser download, WebUSB and CLI installers, first AVB key enrolment | Out-of-band key fingerprint in 3 independent places plus bootloader-displayed comparison before lock; `get_unlock_ability=1` gate refuses on 0; verified downloads before flash; relock never enrols a public test key | First install has no prior key; a page-controlled checkbox is not evidence; a compromised origin can substitute installer and key; stock restore inputs are authenticated by the vendor's published hash, not a signature | Recovery preflight, unlock and stock restoration, CLI and WebUSB installers, independent verification guidance | Assumption. |

### Everyday use

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Correct user decisions | User error, confusing warning, inaccessible flow | Setup, permissions, updates, backup and restore, recovery | Plain outcomes, progressive disclosure, scoped grants, explicit destructive confirmations; first-boot assistive path before setup needs it; critical wording gets fluent review or a disclosed source-language fallback; honest "unsupported" states instead of hidden gaps; a control that is shown must work | Bootloader and firmware screens may stay inaccessible; English fallback alone is not usability proof | Interface design, localization and accessibility, passphrase onboarding, accessible journey validation | Assumption. |

## FP6 source and firmware boundary

Kernel and platform HAL sources follow Qualcomm's CodeLinaro release
`LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS
`kernel_common-6.1` release merged into the vendor kernel. The FP6 device trees,
the Samsung NFC sources and the stock images still come from Fairphone. Source
availability does not prove that GrapheneOS or Pixel patches apply, that
modules meet the selected KMI/UAPI, or that the binaries reproduce stock.

The camera, radio and IMS, secure-world, sensor, DRM and much of the graphics
and media runtime still depend on proprietary userspace or firmware. The
current bring-up selection holds 741 closed stock files (about 369 MB). Grouped
by purpose: camera 213, radio and IMS 151, sensors 84, display and GPU 82,
power and thermal 46, credentials 41, audio 41, plus smaller Bluetooth, NFC,
GNSS and fingerprint sets. Each closed layer is to be replaced by a source build
where one exists.

The selected EU stock input is `FP6.QREL.16.100.0`; its verified factory
package is the authoritative extraction input. Vendor generation uses an
explicit per-file recipe with partition, path, hash, component and purpose.
Purposes are still generic for part of the set and consumer bindings are
partial. The generator excludes per-device identity, modem NV/EFS, calibration,
provisioning, DRM, attestation, keystore and userdata material. Candidate
removals must remove the complete reachable service and declaration path and
pass subsystem tests. Candidate open-source replacements require exact licence
compliance and must preserve security and capability; a software fallback is
not automatically safer than proprietary hardware-backed code. Firmware
partitions are not yet extracted or hashed individually.

Open-source code imported or modified by the project remains fail-closed on
per-file licence, notice, attribution and corresponding-source obligations.
Exact source revisions, interfaces and experiments are in
`config/fp6-sources.json` and `config/fp6-capabilities.json`.

The intended US path requires a real US-region FP6 operated by a second
maintainer; its availability has not been evidenced. `FP6.QREL.16.104.0` is a
comparison and validation input, not an EU restore input. A shared release is
allowed only after the EU/US partition, AVB, firmware, VINTF, init/policy and
carrier-configuration delta is recorded and the US device passes candidate
hardware and telephony tests. Until then, EU is the supported target and US is
unverified.

The stock vendor declares VINTF target level 8 with vendor API 34. Bring-up
builds combine it with the Android 17 framework. Product assembly must still
pass `checkvintf` and applicable VTS checks without weakened enforcement or
broad compatibility shims.

## Fresh-UI boundary

Shared visual tokens and components carry no platform authority. Credential,
update, backup, network and content-processing authorities stay separate. Each
nontrivial Settings, launcher or SystemUI change needs demonstrated benefit, an
owner, measured rebase cost and regression checks. No shell rewrite is
presumed.

## Decision record

- Daily-driver scope for privacy- and security-focused users; full v1 retained.
- Passphrase-first is mandatory now; PIN-optional only via a later explicit
  decision.
- No scores or unsupported parity claims. Keep upstream attribution and a
  clear, separate project identity.
- 2G stays allowed by default (AOSP/GrapheneOS default; emergency
  reachability). "2G network protection" and LTE-only are surfaced as opt-in
  hardening. (2026-09-25)
- Cellular security alerts: the plan's v1 item allows an honest "unsupported"
  state; on the current radio software that is the outcome. The radio software
  limitation is recorded as known. (2026-09-25)
- eSIM uses a maintained hardware-layer LPA with least privilege and
  hardening; OpenEUICC is not used. Candidate: the Qualcomm LPA. (2026-09-25)
- VoLTE is required. The closed Qualcomm IMS stack is used with an explicit
  permission allowlist. (2026-09-25) The IMS data connection is to be brought
  up by DiamaneOS's own code (a small modem-facing service and a one-permission
  app) instead of Qualcomm's connectivity engine; closed code there is a
  fallback only. (2026-09-26) Replacing the IMS stack itself with source is plan
  direction, not a dated decision.
- The call-audio messenger keeps the one audio-routing permission it needs;
  replacing it with our own app is an open item. (2026-09-26)

Requirements added by the 2026-09-25 review (not separate decisions): a
cellular hardening control is offered only once it is verified to reach the
modem, and one that is shown must not fail silently; the candidate LPA must meet
the production TLS trust rule before it ships.

## Architecture and verification review

- Remote path: network observer, DNS and captive portal, EU endpoint policy.
  The EU endpoint policy is not implemented in any build yet; bring-up builds
  use the inherited GrapheneOS endpoints.
- Remote media path: remote media, hardware codec service, video device nodes.
  The codec must not share device-node access with the camera stack or reach
  display configuration, and its seccomp sandbox must be shown to install.
- Malicious-app path: permission and background listener, presets plus per-app
  routing and filtering. Also: camera permission, camera service, closed camera
  provider (grants bound to its domain, reduced groups, no network once SELinux
  is enforcing).
- Cellular path: fake base station, 2G fallback, user hardening choice, modem.
  Reviewed on a bring-up build: the hardening choice did not reach the modem,
  and the interface did not show the failure.
- Cross-profile and physical path: secondary user or profile, user-switch
  challenge, real service-boundary enforcement (FP6-046, FP6-071); and AFU/BFU,
  USB, EDL and lockscreen, reboot-to-BFU and duress. The USB boundary has no
  port-control enforcement yet, and firmware-download reboot refusal is
  pending. Session end is not deletion; unified-challenge limits are preserved.
- These are intended protections. Device-dependent claims stay unverified until
  their named validation produces evidence.

## Next validation

1. Enforcing SELinux runs per subsystem (camera, audio, telephony, sensors,
   Bluetooth, NFC, GNSS, fingerprint, media), with denials recorded by domain.
2. User-build gate: `user` variant, not debuggable, ADB authenticated and off,
   no pre-trusted ADB keys, no adb over network, enforcing, no debug USB
   functions, trace sinks or debug modules, release keys only, no inherited
   GrapheneOS endpoint defaults, test apps absent.
3. Cellular: the selected network mode reaches the modem on every subscription,
   including subscriptions with stored settings, and a failure is surfaced;
   null-cipher support state from the radio daemon; airplane-mode modem state;
   emergency calling (authorised test only).
4. USB port-control matrix: locked and unlocked, host and gadget roles,
   charging kept; the built kernel exposes deny-new-USB.
5. Firmware-download and dump reboots refused on a user build; forced panic
   produces no download device; minidump retention checked.
6. Relock with a project dummy key (both lock states), rollback-index policy,
   SHA-256 hashtrees, inactive-slot downgrade check.
7. Gatekeeper backoff and its persistence across reboot and image restore;
   KeyMint key deletion; duress on synthetic data; eSIM erase.
8. NFC routing tests, Bluetooth address path, Wi-Fi MAC randomization, GNSS fix
   with SUPL settings, runtime network egress capture with SELinux enforcing.
9. Firmware inventory with per-partition hashes and a firmware update path;
   stock AVB key verification.
10. FP6 signing profile and presigned-app inventory; second independent FP6
    build; binding of generated inputs to the public environment; network-denied
    FP6 compilation.
11. TEE KeyMint, attestation and reported security levels on the EU candidate;
    fingerprint spoof testing and the declared biometric class; regional matrix
    on an actual US candidate before claiming US support.
12. Hardware media: codec seccomp installs (no fail-open), codec nodes separated
    from camera nodes, no display-configuration access.
13. Kernel: module list reduced to product use; lockdown kept in user builds.
14. Name-loaded library check for every selected closed file; specific per-file
    purposes.

## How this document is maintained

- Every security or privacy finding updates this document and the project's
  private findings register at the same time as the work that found it. The two
  live in different repositories, so they cannot share one commit.
- Each finding gets an ID of the form `TM-YYYYMMDD-NN` in the private register,
  with evidence, severity for a released build, status, next step, a
  public-safety marking and the public wording it maps to.
- A finding is described here once it is fixed, or once it is safe to state
  without giving an attacker a working path. Until then the affected row
  names the gap in general terms.
- This document never contains device identifiers or unfixed exploit detail.
- An evidence state changes only when new evidence exists. Wording alone never
  turns an assumption into a claim.
- Each revision is dated at the top.

Revision history:

- 2026-09-14: initial threat model.
- 2026-09-25: full refresh from six internal reviews (cellular, proximity,
  vendor userspace, network, physical, supply chain) and two independent
  checks. Added development-state, cellular, SMS, location, kernel, closed
  vendor userspace, DiamaneOS-added features, identifier, TEE, firmware,
  supply-chain and signing rows; added attestation and kernel limits; corrected
  USB, pKVM, fingerprint, secure-hardware, egress and kernel-source statements;
  applied a weakest-state rule to every evidence state.
