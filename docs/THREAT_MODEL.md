# DiamaneOS Threat Model and Product Boundaries

IMS status (2026-09-28): the enforcing development image has demonstrated
ordinary voice, SMS and mobile data on the tested subscriptions, including basic
Wi-Fi calling and VPN-lockdown coexistence. Reconnection can still leave IMS
unavailable for roughly ten minutes, and a second-network observer/address
discrepancy remains under investigation. Emergency calls and carrier location
delivery are not validated by the passing simulated telephony tests. The new
`de.diamaneos` app/interface identities require separate build/device validation;
previous image results are not acceptance of the namespace migration. The
historical integration notes below describe how the current containment evolved.

IMS/IWLAN candidate update (2026-09-27): the source DCM broker and stock Qualcomm
IWLAN/certificate frontend are prepared on an integration branch. The certificate
helper adds a modem-facing QRTR client; it and IWLAN share their own application
UID, retain the stock signer and receive no privileged Android permission grants.
QRTR has no per-QMI-service isolation, so compromise of an allowed client remains
a modem trust risk. A device-owned domain inventory prevents silent additions.
The two stock packages need scoped hidden-API exceptions for platform IPC; this
does not grant Android signature permissions or bypass their SELinux domain.
Carrier configuration is extracted as data for a source-built service, including
a pinned repair of malformed stock no-SIM XML. The stock carrier APK is not
installed. These are source-level mitigations awaiting native policy, package and
carrier checks; see [carrier integration](CARRIER-INTEGRATION.md). AML remains
deferred and unselected. Android already includes a privileged IMS entitlement
client through its telephony product. The candidate replaces it with a source
fork that removes Google-push libraries, bounds carrier responses, restricts
exported entry points and preserves provisioning state on failed queries. The
inspected stock profiles and current configuration do not enable that flow;
activation remains carrier-driven. Push-dependent carriers remain a compatibility
limit, not a reason to invent approval flags.

Native integration follow-up: generated carrier-ID filenames use numeric IDs and
a fixed suffix so stock display labels cannot become shell syntax in Soong's
asset-copy rules. XML bytes and the original names remain bound by provenance.
The DCM service's policy distinguishes inherited platform service discovery from
invocation: system diagnostics can find its name, but inbound Binder calls remain
restricted to the broker (and userdebug `su`), with caller-UID validation in the
daemon. Product selection also installs the generated vendor user/group databases;
a build-time AID declaration alone does not give init a runtime user mapping.
Final-image checks require that mapping and the dedicated domains. These controls
still require phone checks.

First phone boot: the broker reached its dedicated domain under enforcing policy,
but the vendor daemon's lifecycle gate referenced a platform-internal property.
The gate is now owned by system_ext init; the daemon stays vendor-confined and
non-lazy, preserving the persistent kill switch without widening property access.
The Android 17 full SDK version is explicitly labeled as a public build property
instead of granting the vendor IWLAN app access to default_prop. These startup
fixes are pending a new native build and phone test; IMS is not yet accepted.

Second phone boot: the DCM daemon started automatically under UID/GID 2990,
enforcing SELinux, seccomp and no-new-privileges, with no effective/permitted
capabilities. Both cellular IMS bearers connected, but the broker saw a background
firewall block because Android omits apps without INTERNET from those rule updates.
The broker now declares INTERNET for correct UID tracking. On this base it is a
runtime-revocable Network permission, auto-granted by the base default; the broker
does not bypass revocation. Its direct
IP/raw/modem socket neverallows and blocked-state handling remain unchanged. This
expands Android permission authorization and must be reviewed alongside possible
indirect IPC paths; it is not a claim that every network-capable system service is
unreachable. No Internet socket grant or policy bypass is added. IWLAN gets the
thermal-service lookup required by the platform PowerManager constructor; unrelated
service probes remain denied. These changes await native and phone qualification.

Revision: 2026-09-30 (the stock input moved to FP6.QREL.16.111.0, selected before Fairphone published its checksum; firmware images hashed per image; the firmware update path designed in FIRMWARE.md); 2026-09-29 (Recents' Stop in Launcher3 of step 4b, the Settings homepage of step 6a, Home and All apps of step 4a, Settings search of step 6c and the switches and rows of step 6b, and the motion of step 5 in WM Shell and Launcher3, with their security reviews); 2026-09-28 (the Tally shell: the privacy-indicator row widened to privacy indicators and disclosures, a shell fork rebase lag row and the shell's commit rules in the Fresh-UI boundary; the prototype's privacy chip at stock's sizes; the native privacy indicators of step 3.1: which of them take a tap, the location lamp by the lens and the limits of the area rule; the lock screen of step 3.3, the SettingsLib switches and the system label of step 3.5; the shade and Quick Settings of step 3.2; Recents' Stop of step 3.6; the step 3.1 fixes and an indicator bug fixed before any build; the step 3.3 fixes: the bouncer's visual-only code, the always-on strip, the per-user hint count, the Tally clock in the shared clock library; the volume panel, power menu and toasts of step 3.4; the step 3.1 security review; the Tally switch views of step 3.5; the shade's animated lamps and heads-up; the step 3.6 security review; the step 3.3 reviews A and B); 2026-09-27 (r9t: the production kernel configuration and module deny list, KPROBES and debugfs kept as on r9s, boot parameters logged by name only, the previous boot's logs kept by pstore/ramoops, the stock LPA's eSIM service off again, the touch controller firmware, what confidentiality lockdown costs at runtime, the saved console's log level, pstore phone results with cold reboots kept; the Tally privacy-indicator row; remote key provisioning failing in the TEE on r9t); 2026-09-26 (endpoint additions from the branding inventory, the approved shell rework, the network location and geocoding decisions, the keyboard privacy gap, and the bring-up and r9s updates; the full refresh of 2026-09-25 replaced the initial model of 2026-09-14).

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
on-device security status; encrypted microSD; tracker alerts; cellular
alerts-or-honest-unsupported; offline SD OTA; UnifiedPush recommendation;
WebUSB + CLI installers. On 2026-09-26 the owner moved install-time privacy
presets, the battery suite (ceiling, health, cycle/export), DNS filtering, the
privacy dashboard, per-app routing/filtering, panic-to-BFU reboot, scheduled
reboot, hardware diagnostics and share-time metadata stripping after public
v1; until they land, the inherited GrapheneOS protections (inactivity
auto-reboot, lockdown, VPN lockdown, the per-app Network permission) cover
their safety role. Post-release ideas and conditional kernel/repair
research are explicitly not v1.

On the current Qualcomm radio software, "cellular alerts-or-honest-unsupported"
resolves to "honest unsupported" (see the cellular rows).

Several planned features (v1 and after) add code that parses untrusted input or needs privilege.
They are threats in their own right (see "DiamaneOS-added features").

## Current development state

Everything that runs today is a private bring-up build on a development
device. No build has been distributed. Bring-up builds deliberately trade away
protections to make hardware work. The table separates what bring-up builds do
from what a release must do. A release candidate that fails any gate in the
last column is not released.

| Area | Private bring-up builds today | Release requirement | Gate |
| --- | --- | --- | --- |
| Build type | `userdebug`, ADB on by default, `adb root` available; from r9s ADB asks on the phone before trusting a computer and no developer key is pre-trusted (development builds before r9s accepted adb from any computer); recovery still accepts ADB without asking on unlocked or debuggable phones (AOSP design) | `user` build, `ro.debuggable=0`, ADB off and authenticated, no pre-trusted keys, no adb over network | Production-form user candidate (FP6-047), debug exposure audit (FP6-060) |
| SELinux | Up to r9r the whole system is permissive. From r9s debuggable builds boot enforcing with a per-domain policy from the r9r denial captures: narrow rules only, generic proc and sysfs nodes relabelled before any write is granted, no dontaudit rules, nothing new for apps or shell; untested on the phone | Enforcing, no permissive domains | Enforcing runs per subsystem (no owning task yet), user candidate (FP6-047) |
| Bootloader and keys | Unlocked; images signed with public AOSP test keys; the OS has no OEM unlocking control yet, so the bootloader's unlock-ability flag stays as the stock OS left it | Locked on a DiamaneOS AVB key; release keys only; relock never enrols a public test key; OEM unlocking shown and managed by the OS, so users can turn it off after relocking and back on before unlocking | Custom-key relock (FP6-050), signing roles and equipment (FP6-035, FP6-036) |
| Network endpoints | Inherited GrapheneOS services (connectivity, time, CT list, provisioning proxies, app catalog, SUPL proxy) | DiamaneOS EU endpoints with visible standard-server choices | Endpoint contracts (FP6-100), endpoint implementations (FP6-102 to FP6-112) |
| Closed vendor code | 720 stock Qualcomm/Fairphone files (about 369 MB) selected stock-first, all hash-pinned; per-file purpose still generic for part of the set | Each file justified; layers replaced by source builds where possible | Component removal and closure gate (FP6-060, FP6-061, FP6-208), per-component source replacement (FP6-200 to FP6-207) |
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
- Traffic the modem sends by itself (IMS, SUPL and control-plane location).
  Android's network controls do not see it.
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
  Secure Processing Unit, but the FP6 device tree describes no such unit and
  the images carry no SPU firmware or provisioning, so none is usable; whether
  the chip itself has one is unknown. The accurate statement is "stock exposes
  no StrongBox or Weaver", not "the phone has no secure hardware". The eUICC,
  NFC controller and TEE are separate boundaries.
- Attestation is TEE-only. A locked custom-key build reports a yellow verified
  boot state, never green. The GrapheneOS Auditor app does not support FP6.
  Builds before r9s did not enable remote key provisioning, so hardware
  attestation was expected to fail. From r9s the device sets the provisioning
  properties stock sets; requests go through the inherited GrapheneOS proxy
  until DiamaneOS runs its own. On r9t provisioning reaches the server, but the
  TEE's certificate request fails on every attempt, so no attestation keys are
  provisioned and hardware attestation still fails (cause open). From r9s the device
  also reports the factory attestation IDs, and a wipe asks the TEE to delete
  all old keys (untested).
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
- Vendor horizon: the Android 14-level vendor (VINTF level 8) should still run
  on Android 18, but Android 19 will very likely drop level 8, HIDL and kernel
  6.1. Once Fairphone moves the FP6 to a newer vendor, fixes for the current
  vendor code and firmware stop, so DiamaneOS has to follow that move.
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
| Service metadata and OS identity | Network observer; endpoint operator | Connectivity checks, captive-portal probes, DNS checks, HTTPS time, CT list, key and DRM provisioning, app and browser updates, network location and geocoding (when turned on), Auditor remote verification and sample submission (opt-in); phone-side eSIM (SM-DP+/SM-DS) and carrier entitlement connections | No telemetry or GMS; from r9s the build properties keep Fairphone's stock product identity (brand, product, device, model), as GrapheneOS keeps Google's; EU-primary DiamaneOS endpoints with documented upstreams; each endpoint has a visible standard-server alternative; network location off by default and never turned on silently by the setup wizard, with a DiamaneOS EU relay to Apple (no cache, no per-request logs, device IP hidden from Apple), Apple directly or Apple China directly as opt-in choices; no DiamaneOS geocoder: geocoding off by default, opt-in directly to OpenStreetMap's public Nominatim under a DiamaneOS user agent; eSIM and carrier hosts listed as non-project endpoints (they are ordinary app traffic and visible to Android network controls) | Small-user-base hostnames are a fingerprint; the HTTPS time bootstrap resolves outside Private DNS; the DNS resolver and the browser put GrapheneOS probe names in DNS queries even when connectivity checks are turned off, and the browser runs its own connectivity checks, which only a rebuilt browser can repoint; when network location is on (the setup wizard's location switch turns it on), nearby Wi-Fi and cell identifiers go to GrapheneOS's relay for Apple's location service, and geocoding queries go to GrapheneOS's server under a GrapheneOS user agent; with the direct choices, Apple sees the device IP with the Wi-Fi and cell list, and OpenStreetMap sees the device IP with the searched text or coordinates; Auditor's opt-in remote features talk to GrapheneOS's attestation service, and sample submission sends the full system property list; non-EU upstreams disclosed per endpoint; certificate-transparency enforcement fails open when the CT list is more than 70 days old, so CT mirror freshness is a security dependency; "no telemetry" in the closed vendor files rests on a static scan, runtime egress not yet measured | Endpoint contracts (FP6-100), time, connectivity, provisioning, CT and catalog endpoints (FP6-102 to FP6-112), network location and geocoding (FP6-103), attestation (FP6-106), runtime egress capture with SELinux enforcing (no owning task yet) | Assumption: 13 of 16 endpoint contracts are not yet specified (the Info release feed was retired on 2026-09-26 with the Info app); eSIM and carrier egress not yet in the contract. Designed: 3 of 16 contracts specified, none deployed; network location and geocoding choices decided, both contracts still blocked. Bring-up builds use the inherited GrapheneOS endpoints. Observed (static): no contacted host found in the selected closed files apart from GNSS cloud hosts removed by pinned configuration. |
| DNS privacy | Network observer; on-path resolver | DNS queries | Visible encrypted-DNS default (DoT) with no silent plaintext fallback | Time bootstrap, apps with their own DoH/DoT and modem traffic bypass it | Encrypted-DNS default and DNS filter (FP6-079) | Assumption: not implemented; the inherited Android default is in force. |
| App data from remote exploit | Remote attacker via network, media or web content | Carrier and Wi-Fi data, media files and streams, browser and WebView, captive portals, web content reaching the GPU shader compiler, hardware video decode (planned) | Inherited sandbox, hardened_malloc, exec spawning; Vanadium browser and WebView as mirrored unmodified APKs; hardware codec service to keep the stock seccomp sandbox and the platform codec domain; codec device nodes to be separated from camera nodes and from display configuration | No MTE on FP6; closed GPU driver and shader compilers run in every app process; closed codec and video firmware (not yet integrated); codec seccomp installation is unverified and can fail open; captive-portal WebView inherited; browser component updates still point upstream | Inherited hardening (FP6-046), debug exposure and component removal (FP6-060, FP6-061), hardware media integration (no owning task yet), DRM limits (FP6-065), browser delivery (FP6-105, FP6-111) | Assumption: exec spawning, sandbox permissions, codec sandbox and browser delivery unvalidated on FP6. Observed (bring-up): hardened_malloc active (48-bit VA kernel). |
| Traffic outside Android network policy | Carrier; network observer | Modem IMS signalling and media, SUPL and control-plane location, network-initiated location | Documented; carrier hosts listed as non-project endpoints; privacy dashboard and per-app routing state that they do not cover this traffic | Invisible to the Network permission, VPN, Private DNS, the DNS filter and the dashboard | Endpoint contracts (FP6-100), assisted GNSS (FP6-103), carrier configuration review per supported carrier (FP6-090) | Accepted limitation. Carrier egress not yet listed in the endpoint contract. |
| Certificate validity checks at boot | On-path network attacker holding an expired certificate, or blocking time sources | The system clock between boot and the first network time; the PMIC RTC cannot be set from Linux | DiamaneOS's own `timekeepd` saves how far the clock is ahead of the RTC counter whenever the time is set and at least hourly; at post-fs-data, before zygote starts, a one-shot step sets the clock from the counter plus that offset, only if nothing has set the clock since boot and only to a time before 2100. Only that step holds CAP_SYS_TIME; the part that keeps running holds no capability. Each has its own SELinux domain, with no modem, network or app (they replace Qualcomm's time daemon and time app); network time is HTTPS, with certificate expiry checked against the build time | The clock starts at the build date on first boot and after a factory reset; after the PMIC loses power (battery removed) it starts at the last saved time, which can be up to about an hour older than the last time the phone was awake, plus however long it was off; until network time arrives, certificates that expired since then still validate, and blocking time sources prolongs that; RTC drift between saves (1 s resolution) | Clock across reboot, suspend, manual and network time changes and battery removal, with SELinux enforcing (no owning task yet) | Bring-up (not qualified): implemented for r9s; untested on the phone. |

### Cellular and baseband

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Call and SMS content, subscriber identity, coarse location | Fake base station, IMSI catcher, downgrading network, null-cipher network | 2G fallback, pre-authentication identity requests, radio security events | Opt-in "2G network protection" and LTE-only controls; planned: the security status view shows cellular alerts as unsupported | 2G stays allowed by default (AOSP/GrapheneOS default), so a downgrade to 2G works unless the user opts in to 2G network protection; the opt-in controls are visible today and fail open without any signal: the selected mode does not reach the modem; the planned default-mode fix covers new subscriptions only, so stored settings need a reset step and a regression check. No detection: null-cipher/integrity control and security notifications are reported unsupported by the current radio software. LTE/NR identity exposure remains | Telephony bring-up (FP6-044), cellular security notifications (FP6-084), security status view (FP6-073); a radio-policy regression check has no owning task yet | Observed gap (bring-up): controls visible and silently ineffective; they must be fixed or hidden before any build leaves development. Null-cipher control reported unsupported (bring-up notes; radio daemon response not yet captured). |
| SMS, SIM applets, broadcast alerts, carrier configuration | Network or SIM-side sender; carrier | SMS and class-0/type-0 messages, SIM toolkit proactive commands, cell broadcast, SIM access rules granting carrier privileges | Inherited AOSP handling; no project change | SIM applets act partly outside OS control; network "silent" SMS are acknowledged without display under 3GPP rules; SMS are parsed in the modem and the framework; apps the SIM grants carrier privileges can change carrier configuration | Telephony bring-up (FP6-044) | Accepted limitation (inherited). |
| Application processor and user data | Remote over-the-air attacker; compromised modem firmware | Modem protocol parsers (RRC/NAS, SMS, modem IMS), then shared memory, IPA data path, QMI/QRTR services, modem remote-storage and file services | Modem image authenticated by the stock secure-boot chain; AP-side modem services narrowed and confined to their own SELinux domains; modem crash dumps off | Modem firmware unpatchable by the project and behind ASB; SoC isolation (SMMU and memory protection) assumed, not verified on FP6; modem restarts happen silently, so a crash-and-retry attack is invisible; modem state persists across factory reset | Modem and telephony bring-up (FP6-042, FP6-044), debug exposure and component removal (FP6-060, FP6-061) | Assumption. Bring-up runs the modem and its AP services under permissive SELinux; no protection claim. |
| Telephony control and subscriber data | Malicious app via binder; compromised modem via QMI | Closed radio daemon (QCRIL), Qualcomm IMS app, DiamaneOS's own call-audio bridge, eSIM LPA | Explicit privileged-permission allowlists with denials; own SELinux domains; no radio daemon socket; radio daemon not a binder service; diag access removed; the call-audio bridge that replaces Qualcomm's call-audio app holds only the normal audio-settings permission (no audio-routing or capture permission, no network) and its domain reaches only the radio daemon's call-audio service and the audio server | Closed code parses untrusted input; locking the update path of presigned vendor apps is planned, not implemented; VoLTE work is expected to add further closed, network-capable daemons and a presigned privileged app, so the closed surface is still growing; enforcing mode untested | Telephony bring-up (FP6-044), eSIM selection and delivery (FP6-207, FP6-089), enforcing runs (no owning task yet) | Bring-up (not qualified): allowlists and domains reviewed; runtime and enforcing unverified; call-audio bridge implemented for r9s, untested on the phone. |
| eSIM profiles and download sessions | TLS interceptor; thief or examiner after reset; coercer | LPA connections to eSIM servers; profiles retained in the eUICC | Installed eSIM profiles work as SIMs without an LPA (GrapheneOS baseline); the stock Qualcomm LPA stays installed with its services off by default (from r9t; stock leaves its eSIM service on; its JNI library is no longer shipped); planned: our own source LPA (list, enable, disable, delete over the platform's logical channels; download later) behind an "eSIM support" switch that is off by default (owner decision 2026-09-27); OpenEUICC excluded (GrapheneOS os-issue-tracker #6275 and #2631); GSMA SGP.22 mutual authentication | The stock LPA cannot list profiles on the FP6 (the stock radio daemon cannot decode the modem's reply), so Android treats an eSIM as a physical SIM; no eSIM can be added, switched or deleted from Android, and factory reset and duress cannot reach the eUICC; the stock LPA's TLS trust configuration does not meet the production trust rule, which matters again only if it is turned on | eSIM selection and delivery (FP6-207, FP6-089) | Bring-up (not qualified): on r9s the service was on and could not list profiles (phone test 2026-09-27); r9t turns it off again, not yet built. |
| Radio-off expectation, location privacy | Network or passive observer | Modem attaching on its own; airplane mode | Airplane mode puts the modem in low-power mode | Not verified that the modem stays silent in airplane mode or without the radio daemon; a device setting keeps the SIM powered in airplane mode | Telephony bring-up (FP6-044) | Assumption. |

### Location

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Location history | Vendor cloud; assistance-server operator; carrier; local log reader | GNSS HAL configuration, SUPL, PSDS, control-plane positioning, logs | Qualcomm cloud, XTRA and crowdsourcing paths excluded by pinned configuration; PSDS off until an endpoint is bound; SUPL user-selectable (Off, proxy, standard); network location and geocoder off by default; IMS geolocation has no network geocoder | SUPL in the image defaults to the GrapheneOS proxy until the DiamaneOS SUPL endpoint exists; "standard" SUPL goes directly to the carrier-configured server (on the carrier tested so far, Google's server); SUPL requests carry cell information; control-plane positioning runs in the modem; GNSS engine state on the persist partition survives reset; location logging level still verbose; the radio log records the serving-cell identity on every registration poll (today about twice a second on one SIM), readable over adb and in bug reports | Assisted GNSS (FP6-103), GNSS bring-up tests (FP6-045) | Assumption: SUPL default not yet decided; no GNSS fix recorded. Bring-up (not qualified): cloud paths absent and pinned. |

### Malicious and over-permissioned apps

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| App and user data | Malicious or over-permissioned app | Permissions, background listeners, IPC/URI grants, sensors | Per-app Network and Sensors permissions, Storage and Contact Scopes (inherited); install presets (Untrusted/Standard/Trusted); per-app routing and filtering; privacy dashboard with CE-only bounded history | Preset "Trusted" is user-chosen policy, never an audit; routing attribution limits (shared UIDs, own DoH/DoT, system and modem traffic); v1 features not implemented yet | Inherited hardening (FP6-046), privacy presets (FP6-072), DNS architecture and filter (FP6-078, FP6-079), per-app network controls (FP6-080), privacy dashboard (FP6-081) | Assumption: inherited controls present in source, unvalidated on FP6; v1 features not implemented. |
| Per-app destination history (dashboard) | AFU forensic examiner; malicious app | Dashboard storage, backups, bug reports | CE-only storage, aggregate by default, 7-day default, clear on reboot by default, no backup, no bug-report inclusion | The dashboard is itself the most sensitive log on the device | Privacy dashboard (FP6-081) | Assumption: plan rules only. |
| Kernel integrity and all data | Malicious app; compromised app or renderer process | GPU driver node (reachable by every app, as on stock), binder, loaded network-protocol modules and socket families, DSP remote-call driver (limited to named groups) | GrapheneOS kernel hardening configuration merged into the vendor kernel; kernel lockdown (confidentiality); SELinux socket and device restrictions; module set reduced to what the product uses | No MTE; large vendor driver surface; hardening hand-merged into a vendor kernel with an intentional KMI deviation; from r9t the protocol modules with no product use (CAN, 802.15.4/6LoWPAN, the kernel NFC socket family, PPTP/L2TP and the in-kernel Bluetooth stack) are no longer shipped, and the kernel has no SELinux development mode and restricts dmesg to root (prepared for r9t, not yet built); debugfs stays built in and mountable as on r9s, because turning mounts off in this kernel also removes the in-kernel interface the display driver and recovery need: user builds never mount it, debuggable builds mount it only until boot completes, SELinux governs access, and a kernel change that keeps the interface but refuses mounts is open; USB host-class drivers and the USB debug and trace drivers still load; KPROBES stays on (owner-accepted) because the USB controller glue implements its hooks with kretprobes, and lockdown keeps user space from placing probes; TIPC re-enabled (GrapheneOS disables it) because Qualcomm's mobile-data stack needs it, built as a module without network bearers or crypto; under enforcing SELinux neither user-installed nor privileged apps can reach it, its sockets are limited to two radio daemons and its configuration interface to system components that hold the network-admin capability, including the network stack module; profiling work must never weaken lockdown or tracing restrictions in user builds; confidentiality lockdown also turns kernel tracing off and withholds kernel-memory reads from BPF programs, so Android's per-app CPU time accounting (per-app CPU use in Battery usage) and the memory-event OOM listener do not start; the lockdown level (confidentiality or integrity) is an open decision; every app can reach the kernel's userfaultfd code, which ART's garbage collector needs, but the kernel forces apps into user-mode-only mode (vm.unprivileged_userfaultfd=0, /dev/userfaultfd root-only and SELinux-denied), so it cannot freeze the kernel mid-copy to widen race windows; bugs in that code stay ordinary kernel attack surface, fixed through 6.1 LTS updates | Kernel build (FP6-041), debug exposure and component removal (FP6-060, FP6-061), enforcing runs (no owning task yet) | Module reduction and production kernel configuration built for r9t (kernel check 2026-09-27: configuration matches the policy, no loaded module depends on a removed one), not yet phone-tested; r9s runs enforcing. Observed (bring-up): lockdown confidentiality active; tracing empty. |
| Camera, microphone, sensor streams, device integrity | Malicious app or remote content reaching closed vendor code through a platform service | Camera, media, display, audio, sensors, GNSS, NFC and Bluetooth HAL interfaces; same-process GPU libraries; persist and vendor data files parsed by closed code | Per-file allowlisted, hash-pinned stock selection; SELinux grants bound to each service domain; internal service endpoints of a closed HAL reachable only from that HAL's own process (audio from r9s); reduced service users, groups and capabilities; tight device-node permissions; Qualcomm diagnostics, telemetry and factory services excluded; layers replaced by source builds over time | Large closed parsers (camera about 185 MB) without MTE and mostly without seccomp; closed performance and thermal daemons run as root; Android 14 ABI vendor code on Android 17; per-file purpose still generic for part of the set; closed code updates only through Fairphone stock releases | Debug exposure and component removal (FP6-060, FP6-061), closure gate (FP6-208), camera (FP6-045, FP6-203), audio (FP6-044, FP6-205), hardware media integration (no owning task yet), enforcing runs (no owning task yet) | Bring-up (not qualified): selection reviewed per subsystem and running permissive; enforcing untested. |
| Persistent hardware identifiers | Any app | System properties, persist files, sysfs, logs | Vendor-internal property types with no read grant, narrowed sysfs labels, random Bluetooth address per install (intended), Wi-Fi MAC randomization, traceability services excluded, boot parameters handed to user space logged by name only (r9t, not yet built), enforcing SELinux | Readable on any permissive build; which Bluetooth address path runs is unrecorded (the HAL tries a factory address first) and the address appears in logs and bug reports; the stock camera HAL writes the camera module serial numbers to the log at every start; the SoC serial number (sysfs soc0/serial_number) is readable by 16 system and vendor domains through platform sysfs read grants and imported Qualcomm rules (no app domain; the r9s policy gives the camera HAL and nicmd only the public SoC id); the factory Wi-Fi MAC in persist is not read under enforcing, so the driver falls back to the chip's own MAC or one derived from its serial; Wi-Fi MAC randomization unverified; a flash dump reveals persist data | Enforcing runs and an identifier probe test (no owning task yet), Wi-Fi and Bluetooth bring-up (FP6-043) | Observed gap (bring-up, permissive): some hardware serials are exposed as system properties; enforcing denial not yet shown. |
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
| Wi-Fi MAC (tracking), device integrity | Passive Wi-Fi observer; malicious access point; LAN peer | Probe requests, association, Wi-Fi driver and firmware, wake-on-LAN | Station-only features exposed to Android (no hotspot, Wi-Fi Direct or Aware); source-built Wi-Fi HAL and driver; MAC randomization | MAC randomization unverified on this HAL and firmware; closed Wi-Fi firmware on the over-the-air path; a LAN peer can wake the device with a magic packet; on Wi-Fi networks used for Android Auto the device sends a DHCP hostname derived from the device name (inherited default) | Connectivity bring-up (FP6-043) | Assumption: unverified. |
| Locked-device data, kernel integrity | Malicious USB device or host; forensic tool; malicious charger | USB-C data lines (host and gadget roles), USB descriptors, USB PD and charger firmware | (Intended) GrapheneOS USB-C port control (charging-only when locked, deny new USB devices); standard gadget functions only; debug USB functions removed from user builds; reduced USB driver set | Port control not wired on FP6 yet; deny-new-USB hook present in kernel source, built kernel unchecked; no hardware data cutoff; broad USB host drivers loaded; the USB descriptor carries the device serial; charger firmware closed | Debug exposure audit (FP6-060), USB modes (FP6-045), hardware USB data disable (no owning task yet) | Observed gap: port control absent. Bring-up (not qualified): userspace gadget configuration reviewed; debug USB functions still to be removed from user builds. |

### Physical access to a powered-on, locked device (AFU)

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| AFU user data | Physical access to a locked, powered-on phone; forensic tools | USB, lockscreen, firmware download and dump modes | Inactivity auto-reboot (inherited); USB port control; OS refuses firmware-download and dump reboots; panic RAM dumps off | Port control not wired on FP6; OS refusal of firmware-download reboots not implemented; the kernel keeps a minidump on panic and where it can be retrieved is unverified; the early-boot setting that also turns dump mode off needs a policy grant before enforcing; EDL stays reachable with physical access and a signed programmer; no forensic-proof claim | Debug exposure audit (FP6-060), credential policy (FP6-071), scheduled reboot (FP6-085) | Observed gap: port control absent; download-reboot refusal not implemented. Observed (bring-up): panic dump mode off by kernel default. |
| Previous boot's kernel log and event logs | Root or system code after a reboot; anyone with USB debugging on and an authorized computer; forensic examiner with the phone | RAM region kept across a soft reboot (pstore/ramoops); its copy in the system's crash-report store; Qualcomm minidumps | Not readable by apps; readable by the log group (which adb's shell user has) and a few system domains, and the crash-report copy through dumpsys; boot parameters that can carry device identifiers are logged by name only; dmesg restricted to root from boot; on release builds init takes all permissions off the event-log device, so only processes with the DAC-override capability could still write it (debuggable builds keep it writable) | The kernel part is copied at the next boot to device-encrypted storage, readable by the system before first unlock (moving it into credential-encrypted storage at first unlock and clearing the RAM copy is open); the RAM copy stays until overwritten; Qualcomm's minidump driver registers the log areas, so a collected minidump would carry them; reboots and kernel crashes reset cold, which powers the RAM off, so in practice nothing survives a reboot; a one-off warm reboot on r9t kept both logs (the bootloader does not clear RAM), and cold reboots stay the default (owner decision 2026-09-27); every domain holds the platform's write grant on the event-log device, so its file mode is the only control | Debug exposure audit (FP6-060) | Assumption: region and release-build rule built for r9t with GrapheneOS's Pixel layout, not yet phone-tested (r9t is a debuggable build, so the release rule stays inactive there); no build before r9t registered it. Kernel check (2026-09-27): the kernel places the region at boot without a fixed address, and the saved console keeps only notice-level and more severe messages (the kernel's console log level), not the full log. Observed on r9t (2026-09-27): the region registers at the same address on every boot; after a normal reboot pstore is empty; after a warm reboot both logs were there, readable by the shell user by exact name (listing the directory is denied), and the system's copy appeared only after the first unlock; the boot-parameter value appeared in none of them. |
| AFU unlock, auth-bound keys | Attacker with a lifted or spoofed fingerprint | Side fingerprint sensor | Strong authentication after reboot and timeouts; lockout in the trusted app; the vendor debug service never registered (kept inside the HAL process) and unreachable by policy; biometric class not claimed above what testing shows | The HAL currently declares Class 3 before any spoof testing; a release must measure spoof resistance or declare a lower class; fingerprint is not treated as strong authentication until then | Fingerprint bring-up (FP6-045), credential validation (FP6-046); spoof testing has no owning task yet | Observed gap: class declared, not measured. Bring-up (not qualified): unlock works; the first enforcing boot showed the module needs its debug service registered, which the HAL now answers in-process (r9s, not built). |
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
| Firmware security | Anyone exploiting an already fixed firmware bug | XBL, TrustZone, hypervisor, modem, DSP, Wi-Fi and Bluetooth firmware; peripheral controller firmware files in /vendor/firmware | Per-image firmware inventory with hashes (config/fp6-firmware-inventory.json); firmware delivery in OTAs from verified Fairphone releases (designed in FIRMWARE.md, not implemented); separate dated patch levels for platform, kernel, vendor and firmware; controller firmware files are exact, hash-pinned stock files under verified boot once locked | The project cannot build or sign firmware; no firmware update path yet; firmware lags ASB; the Gunyah hypervisor and its trusted VMs are closed; as on stock, the NFC HAL may update its controller from its firmware file, and from r9t the touch driver writes its file to the touch controller whenever the versions differ or it cannot read the controller's version, checking only the file's header, so verified boot is what keeps that file authentic | Firmware review and update path (FP6-206), stock input verification (FP6-040) | Observed gap: firmware stays at the last flashed stock release. |
| Keys, Gatekeeper throttling, fingerprint templates | Compromised system or HAL process; malicious app reaching TEE clients | TEE driver and client services, TEE listener daemon, fingerprint HAL | TEE access only for named HAL domains; unused userspace TEE proxy services removed; RPMB access limited; a wipe asks the TEE to delete all old keys | Closed TEE with a public history of key-extraction bugs; no StrongBox or Weaver; KeyMint key deletion on wipe (rollback resistance) set from r9s and unverified | TEE and credential validation (FP6-046, FP6-050), attestation limits (FP6-065, FP6-106), security status reporting (FP6-073) | Observed gap (builds before r9s): an unused userspace TEE proxy ran; removed from r9s, not yet built. Bring-up (not qualified): services run; enforcing and throttling persistence unverified. |

### Supply chain, signing and development process

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Source and build outputs | Compromised upstream host or project, build dependency or build host | Source fetch, prebuilt toolchains, build account | Signed GrapheneOS tags; immutable commit pins; signed downstream commits verified before build; network-denied compilation; two independent builds with final-content comparison | Qualcomm/CodeLinaro and Fairphone sources and toolchains carry no upstream signatures; common inputs are common-mode | Reproducible environment, dual build, release comparison | Observed gap (FP6 path): compiled with network available, on one host, with private scripts; the build identity is not yet separated from its own verification inputs; only a few projects are re-verified per build. Recorded (generic target): a network-denied build path. |
| Closed vendor inputs | Compromised vendor download or support page; network attacker at first download | Stock factory package | Exact archive hash pinned everywhere; per-file hash, component and purpose; fail-closed extraction; every name-loaded library declared (required; no build check yet) | The archive and its published hash come from the same vendor web estate, and a new package can be selected before Fairphone publishes its hash (FP6.QREL.16.111.0 was); no vendor signature check yet; per-file purpose generic for part of the set; name-loaded libraries declared for few files (a missing one broke a bring-up boot) | Stock input verification (FP6-040), firmware review (FP6-206), closure gate (FP6-208) | Observed gap: per-file necessity and name-loaded dependencies incomplete. Bring-up (not qualified): extraction is fail-closed and hash-bound. |
| Release signing keys | Key theft or misuse; targeted signed build | Signer host, hardware tokens, signing media, maintainers | Offline signer, token roles, 3-of-5 recovery, public release log before distribution | Single signing authority; the AVB root cannot be revoked without unlock and wipe; log inclusion does not mean benign; no signing role for kernel modules yet | Signing qualification (FP6-035, FP6-036) | Designed: generic dummy-key qualification only; no FP6 signing profile yet. |
| Repositories, domain, install page | Phishing; compromised workstation or assistant session; malicious contribution | Code hosting, registrar, DNS, mail, developer keys | Hardware MFA; separate signing and authentication keys; protected branches; human review of security-relevant changes; least-privilege assistant access; no untrusted code on release hosts | One-person review capacity | Account and workstation hardening, branch protection | Assumption: not yet qualified. |
| Exposure window for known bugs | n-day exploitation | Platform, kernel, vendor code, firmware, browser | Monthly releases within 7 days of GrapheneOS; urgent fixes within 48 hours; per-layer patch levels shown | Vendor and firmware fixes depend on Fairphone and Qualcomm and lag; bundled open-source libraries inside closed vendor files do not get platform fixes | Update cadence, patch dashboard | Observed gap: bring-up platform and vendor patch levels lag available upstream releases; no automated upstream intake yet. |
| Shell fork rebase lag: timely GrapheneOS security fixes under the Tally shell | n-day exploitation of a fix DiamaneOS has not shipped yet, or one lost while rebasing | DiamaneOS's Tally changes to frameworks/base (the SystemUI shell first; WM Shell, SettingsLib and core resources later), carried onto each GrapheneOS release | Tally in per-area commit series, mostly new files wired in through SystemUI's dependency injection, so upstream files change little; Tally code behind one build-time flag (fixed read-only, on in DiamaneOS's release config), so an area can be dropped, or the flag turned off, to ship a GrapheneOS security release on time; the planned release gate requires the shipped fork revision to contain the adopted GrapheneOS release's frameworks/base revision | The flag helps only while the Tally code still builds on the new release, and switches off a resource only where the resource names it; SystemUI, WM Shell (step 5) and the shared clock library read the flag; if a later GrapheneOS release moves WM Shell's activity transitions to its new transition planner, Tally's page motion falls back to stock until it is hooked there; conflicts in keyguard, privacy-indicator or biometric files still need a careful manual merge; frameworks/base is not in the tools' fork tracker yet; one-person review capacity The bouncer reads Tally token resources when it is built, so a build with broken tokens would break PIN and password entry: every build is tested by unlocking with a PIN and a password with the flag on. The foundation's colour fixes (SystemUI's "already applied" check and the Sodium fallback) apply with the flag off too; they change colours only, so the flag-off build is GrapheneOS's in behaviour, not byte for byte. | Rebase and build of each GrapheneOS release with the flag on and off (FP6-211); update cadence, patch dashboard | Assumption: the flag and its release-config value are on local branches, not built. |
| Install-time trust | Impersonating site or mirror; malicious installer | Browser download, WebUSB and CLI installers, first AVB key enrolment | Out-of-band key fingerprint in 3 independent places plus bootloader-displayed comparison before lock; `get_unlock_ability=1` gate refuses on 0; verified downloads before flash; relock never enrols a public test key | First install has no prior key; a page-controlled checkbox is not evidence; a compromised origin can substitute installer and key; stock restore inputs are authenticated by the vendor's published hash, not a signature | Recovery preflight, unlock and stock restoration, CLI and WebUSB installers, independent verification guidance | Assumption. |

### Everyday use

| Asset | Attacker capability | Entry point | Intended mitigation | Remaining limit | Validation | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Correct user decisions | User error, confusing warning, inaccessible flow | Setup, permissions, updates, backup and restore, recovery | Plain outcomes, progressive disclosure, scoped grants, explicit destructive confirmations; first-boot assistive path before setup needs it; critical wording gets fluent review or a disclosed source-language fallback; honest "unsupported" states instead of hidden gaps; a control that is shown must work | Bootloader and firmware screens may stay inaccessible; English fallback alone is not usability proof | Interface design, localization and accessibility, passphrase onboarding, accessible journey validation | Assumption. |
| Typed text and personal words | Someone with the device before first unlock, a copy of a backup, or a view of the screen | The keyboard's learned, personal-dictionary and contact word lists; backups; fields that ask the keyboard not to learn (incognito) | In the DiamaneOS keyboard fork: learned, personal and contact words only in credential-encrypted storage, loaded after unlock, old copies deleted; backups off; the no-learning flag and password fields never learned from and never shown learned words; no typed or personal data in logs; unused network, account and sync permissions and dormant entry points removed; no network permission (already true) | Until the fork ships, the inherited keyboard keeps these words in storage available before first unlock, allows them in backups and ignores the no-learning flag. After it: the recent-emoji list stays readable before first unlock | Keyboard fork tests and phone checks before merge (no owning task yet) | Designed (fix on a local keyboard branch, not built or tested); Observed gap on current builds. |
| Privacy indicators and disclosures under the Tally shell: knowing when the camera, microphone or location is in use or the screen is captured, which app is asking, whether the device is managed or on a VPN, and what a locked phone shows | App using a sensor or capturing the screen while the user reads its own interface or while it runs fullscreen; app asking for a biometric or credential; someone looking at a locked phone | Status bar privacy chip and lamps, capture chips, the fullscreen dots and the lens ring, the lock strip and indication line, Quick Settings' security footer, notification rows, toasts and the biometric prompt, all restyled by the Tally shell (plan FP6-211) | Tally changes how indicators and disclosures are drawn, never their data or presence: the platform's privacy and app-op tracking stays the only source, with no filtering by foreground app, quiet or decluttered views or the lock strip; camera, microphone, location (whenever the platform shows it) and screen sharing show in every state (app in front, immersive and fullscreen, lock screen, shade, Do Not Disturb, battery saver); stock SystemUI keeps the indicator timings (the 5 s and 10 s holds) and the chip height and icon size in sp, so chips grow with the text size; a lamp appears with no animation delay and only its disappearance may animate; tapping the privacy chip in the shade opens the stock privacy dialog and tapping a capture chip its stock stop dialog, as in stock; the status bar privacy chip and the dot take no tap of their own, as in stock, and the lens ring and the location lamp by the lens take no touch at all, so they never block the status bar or the app under them; the lens ring lights while an app uses a camera once the front camera has opened, as the camera service reports it, and counts the front camera as used for the longest privacy hold (10 s) after it closes, so it keeps the camera privacy item's hold whatever order the close and the privacy item arrive in (a switch to the back camera ends it after that window; a camera whose facing cannot be read counts as the front one, and without the open and close reports any camera lights it), while the chip and the dot show every camera; Tally changes only how the stock indicators are drawn (colours with a 2 dp edge, padding, radius, a 16 dp dot drawn as a lamp 16 dp from the top and side, with the status icons keeping clear of it, one chip for every sensor in the shade, screen capture alone in the capture colour, disappearance on the fill spring) and adds the lens ring and the location lamp by the lens, and its tokens and overlays never set the stock indicator resources; indicator colours are fixed SystemUI resources outside the palette and keep at least 3:1 against both a light and a dark bar, taking the variant from the area under them (the status icons' tint over the app, the lock screen or the shade; dark under the bouncer, a SystemUI dialog or the dozing screen, and wherever the area is not known to be light), never from night mode; the lens ring and the location lamp by the lens are drawn on active pixels at least 2 dp outside the display cutout, above apps and the lock screen; while locked, the lock strip says only "Camera in use" or "Microphone in use"; the lock strip takes its sensor items from the privacy chip's own source (holds included) and its other items from SystemUI's existing controllers, takes no touch, shows no app, network or device names, shows the next alarm only within the stock lock screen's 12 hours and is hidden on the always-on display, where a Tally always-on strip shows, only while the phone dozes, what stock's always-on date line showed (the next alarm within 12 hours, a Do Not Disturb icon, and the playing media's title, with its artist only beside the media notification's icon, and media only while the current user's lock screen shows notifications and "Show media on lock screen" is on, with GrapheneOS's defaults, as GrapheneOS's always-on line shows them) and never lamps, sensors, network or device names, app icons or the battery; the lock strip shows an alarm only when it is still to come and rings within 12 hours (GrapheneOS's condition) and only while the current user's lock screen shows notifications (as GrapheneOS's smartspace line, which shows nothing with them off, off unless set), and the always-on strip shows nothing at all while they are off, Bluetooth only while a device is connected as stock's status bar icon does, and the battery percentage only while charging or with the user's setting on; unlike stock's lock screen, which shows only the untyped privacy dot, it names the sensor in use ("Camera in use", "Microphone in use"), a deliberate difference; Tally lock views draw an empty or partial state without failing, since a SystemUI crash removes every privacy indicator until it restarts; the Tally lock clock and the date line under other clocks show only the time and date, and the date line under other clocks shows only while the lock screen shows notifications (as GrapheneOS's smartspace line); the indication line keeps stock's messages, shows fingerprint failure and help messages in the error colour and adds a 3-second side-sensor hint in stock's words only while fingerprint unlock is allowed, on at most five wakes per user (a count from 0 to 5 kept only in SystemUI's own device-protected storage, not backed up and removed with the user; read at screen-on, also before the first unlock, and written only when the hint shows, which needs fingerprint unlock to be allowed; it never affects unlocking, and if it cannot be read the hint stays away); the security footer (managed device, VPN, monitoring certificate) and the VPN icon show whenever stock shows them; lock-screen notifications follow the user's lock-screen settings and work-profile redaction; notification rows keep the app's label and icon, the biometric prompt the app's name and icon, and the indication line its strong-authentication, lockdown, administrator and trust-agent messages; toasts change position and shape only, keep their admission rules, app attribution and GrapheneOS's secure-paste notices, and no new kind of toast appears on the secure lock screen; the overlay check refuses product overlays of SystemUI privacy-indicator resources and of every Tally token or indicator resource in SystemUI (only a device overlay sets the lens config); the privacy dot's colour follows the area under it without ever delaying the dot; every Tally status bar indicator draws its 2 dp edge in every state, and where no edge can be drawn or no area is known it takes the variant that keeps 3:1 on its own | The Tally prototype follows the same indicator rules (stock's sp sizes at 100, 150 and 200 % text; colours from the area under the indicator with a 2 dp edge, so dims over the status bar keep 3:1), checked in every configuration; a new layout could still cover or clip an indicator without touching its data, which only review and phone tests catch; a preinstalled overlay outside the checked roots could still change the stock resources; SystemUI cannot see a scrim an app draws inside its own window (as opposed to a dimming window), so over the mid-tone it leaves, an indicator in its light variant can fall to about 2.3:1 with its white edge; the status bar chip and the capture chips sit in the status bar window, under the shade, the lock screen and SystemUI dialogs, where the dot, the lens ring and the lamp by the lens stay above everything; on the FP6 the device overlay declares the camera hole as measured on the panel (a circle of 37 px), so the lens ring is a true ring 2 dp outside it and the lamp by the lens sits level with it; the layout rectangle keeps the status bar and app insets at 110 px and takes the hole's full width, so no status bar content sits under it | Shell security review at roadmap steps 3.1 and 3.3 (FP6-211); prototype privacy checks at 100, 150 and 200 % text; phone test of every state listed before merge | Assumption: the SystemUI indicators of roadmap step 3.1 and the lock screen of step 3.3 are on the local tally-status and tally-lock branches, compiled only against stubs of the pinned signatures with their resources linked by aapt2; not built, not run on the phone. |
| Files archive handling (browse, extract, create) | A malicious archive the user opens or extracts, or an app holding MANAGE_DOCUMENTS | ArchivesProvider (exported, MANAGE_DOCUMENTS) parsing untrusted ZIP, 7z and TAR with AOSP commons-compress 1.19; DocumentsUI extraction (UnpackJob) | Entry paths are rooted and `.` and `..` collapsed (Archive.getEntryPath) and extraction goes through the storage framework, which re-roots and sanitises each name; symlink and hardlink entries are written as ordinary files, never followed; extraction checks free space against the declared sizes, caps each file at its declared size and removes partial files; parser exceptions end in a failed load, never a crash; the new archive code (zip_ng) adds the read-path size and CRC checks, so it is turned on (-142) | commons-compress 1.19 is old (a debug log line on a ZIP 0x0017 extra field, -143; a malformed TAR header throws a caught NullPointerException); browsing parses archives whether or not zip_ng is on; a large honest archive that fits the free space still costs time to extract (cancellable); no recursive extraction | Host review and fuzzing, 2026-09-30: 20 million path inputs and about 154 million mutated archives, no crash, hang or escape; phone extraction and picker tests with the next build | Designed and fuzzed on the host with the pinned library; zip_ng on in vendor_diamaneos local `files-flags`, not built. Files trash stays off until its wording (-140) and phone tests. |

## FP6 source and firmware boundary

Kernel and platform HAL sources follow Qualcomm's CodeLinaro release
`LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS
`kernel_common-6.1` release merged into the vendor kernel. The FP6 device trees,
the Samsung NFC sources and the stock images still come from Fairphone. Source
availability does not prove that GrapheneOS or Pixel patches apply, that
modules meet the selected KMI/UAPI, or that the binaries reproduce stock.

The camera, radio and IMS, secure-world, sensor, DRM and much of the graphics
and media runtime still depend on proprietary userspace or firmware. The
current bring-up selection holds 720 closed stock files (about 369 MB). Grouped
by purpose: camera 213, radio and IMS 150, sensors 84, display and GPU 82,
audio 41, credentials 36, power and thermal 29, remote-processor services 28,
plus smaller Bluetooth, NFC, GNSS and fingerprint sets. Each closed layer is to
be replaced by a source build where one exists.

The selected EU stock input is `FP6.QREL.16.111.0` (`FP6.QREL.16.100.0` until
2026-09-30); its verified factory package is the authoritative extraction
input. Vendor generation uses an
explicit per-file recipe with partition, path, hash, component and purpose.
Purposes are still generic for part of the set and consumer bindings are
partial. The generator excludes per-device identity, modem NV/EFS, calibration,
provisioning, DRM, attestation, keystore and userdata material. Candidate
removals must remove the complete reachable service and declaration path and
pass subsystem tests. Candidate open-source replacements require exact licence
compliance and must preserve security and capability; a software fallback is
not automatically safer than proprietary hardware-backed code. Firmware
images are hashed per image in `config/fp6-firmware-inventory.json`, but
DiamaneOS does not deliver them yet.

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
owner, measured rebase cost and regression checks. The project owner approved
a substantial shell rework on 2026-09-26 (lockscreen, shade and Quick Settings,
Home, Recents and app transitions through Launcher3, SystemUI and WM Shell);
framework authorities are not replaced and no shared visual controller gains
platform authority.

Every Tally shell commit (roadmap step 3, FP6-211) also keeps these rules. The
privacy-indicator and disclosure rules are in the everyday-use rows, the rebase
rule in the supply-chain rows.

- Lock screen and biometric prompt: the bouncer's window, state machine and
  input handling and the system keyboard stay as upstream; its look changes
  through resources and visual-only code (key shape, typeface, button and
  field styling), each such change with two reviewers; no custom keyboard and no show-passphrase
  key without an owner decision and a review; the bouncer stays protected from
  screenshots and screen recording; lockout, throttling and the duress and wipe
  counters stay untouched, and GrapheneOS's PIN scrambling is kept; the
  biometric prompt keeps the credential fallback, confirm-required, cancel on
  back and on an outside tap, and its protection against obscured touches; the
  power menu keeps Lockdown and its lock-screen filtering; the lock-screen
  camera key opens only the secure camera. Commits to keyguard, bouncer and
  biometric code get two reviewers and the keyguard and biometric CTS sets on
  the FP6. The bouncer's Tally look sits behind the Tally flag, and its Tally code only draws: the keys' corner radius, the typeface of the digits and messages, and how the password field and buttons look, including the field's fade while entry is off; input, the system keyboard, the key order and PIN scrambling, lockout, throttling, the duress and wipe counters and screenshot protection stay GrapheneOS's.
- Transitions, tested at animation scales 1x and 0.5x: while the phone is
  locked or an app shows over the lock screen, back and close lead only to the
  lock screen, never to Home or an app snapshot (tested from the secure camera
  and a ringing alarm); the shell's input consumer takes the catch gesture, so
  touches never reach an animating app; permission-grant, restricted-setting,
  package-installer, USB, biometric and bouncer windows keep stock or shorter
  enter animations.
- Quick Settings and the shade: Tally tiles, the first pull's keycaps included,
  reuse the upstream click, secondary-click and long-click handlers, so a tile
  that needs an unlock still asks for one; the security footer shows under
  upstream's conditions with its VPN or management icon; the shade header's
  privacy container keeps the platform's privacy data and its tap to the stock
  privacy dialog. Notification cards change drawing only: which notifications
  show, private-content redaction and work-profile redaction are untouched, and
  the live lamp is never drawn on a redacted notification. The media card no
  longer shows album art (less on the lock screen, not more). Heads-up
  notifications change only their motion, edge and shadow: what a heads-up
  shows, when and for how long, its gestures, full-screen intents and lock-screen
  redaction stay stock; a heads-up with a status bar chip keeps stock's reveal so
  it never covers the chip, and a leaving heads-up is fully off screen before it
  is removed. The camera and microphone access tiles light their lamps at once,
  like sensor lamps, but keep the tile colours: sensor green stays reserved for
  indicators of a sensor in use. Side effects are
  visual only: Quick Settings dialogs, the privacy dialog included, get a 20 dp
  corner radius.
- Recents' Stop (step 3.6): Launcher3 gets no new permission. SystemUI tells
  only the current user's recents app, checked by its uid, which apps the Active
  apps dialog would let the user stop, as package and user for the current user
  and its profiles, kept in memory only. Every Stop re-runs the dialog's checks
  at that moment: the lock screen is dismissed, a foreground service is running,
  the user is in the current profiles, and the platform gives the app no
  exemption that hides the dialog's Stop button (the system uid, the system
  allow-list, device and profile owners, protected and device-admin packages,
  persistent processes, the default dialer, system modules; the platform's own
  list of stoppable system apps is the exception). Only then does SystemUI ask
  the platform to stop the app, as the dialog does. The two new SystemUI-proxy
  methods accept calls only from the recents app's uid in the current user and do
  nothing with the Tally flag off. Limits: SystemUI cannot tell whether the user
  tapped Stop, or on which app, and trusts the recents app for that; the recents
  app can already stop apps in its own user with its own permission, and through
  SystemUI it can also stop apps in the current user's work profile or private
  space that the dialog would stop, and learns which of them run a foreground
  service; an app with two exemption reasons (for example a carrier-privileged
  device admin) can get a Stop button, in the dialog and in Recents alike; and
  Recents' Stop sits in the launcher's window, where app overlays can cover it.
  Launcher3's side (step 4b) sets SystemUI's listener only while Recents is open
  and removes it on close, keeps the list in memory only, offers Stop only for a
  card the user swiped away, named by that card's own task (package and user),
  and never stops an app itself; Launcher3's manifests, permissions and
  privileged-app allow-list stay as upstream. The Stop key and its row refuse
  touches that come through another window over them, and ignore a tap within
  0.5 s of the row appearing or changing. Limits: a window over another part of
  the launcher (picture-in-picture, a chat bubble) does not block a tap on Stop,
  since refusing such touches would break Stop whenever one shows; and the
  listener binder is not caller-checked, as with Launcher's other SystemUI
  listeners, but a forged list only decides which swiped apps get a row, and
  SystemUI re-checks every Stop.
- Settings switches: restyled only through their layouts and drawables, never
  their logic (protection against obscured touches, administrator- and
  restricted-setting-disabled states), with those states tested. SettingsLib's
  restyle changes its theme resources and adds Tally switch views (subclasses of
  the switch classes the preferences already bind, and a Compose twin for its
  Compose pages) that change only drawing and motion: checking, touch handling,
  accessibility and the enabled state stay the parent classes'. Disabled
  switches draw at 38 % in their real position, and the emergency-alert
  switches' logic is untouched (presidential alerts stay on). It is not behind
  the SystemUI Tally flag and reaches every app built on SettingsLib, but
  SystemUI's own switch screens take the Tally switch only through flagged
  layout twins and, on its Compose screens, calls behind the Tally flag with the
  same arguments and accessibility semantics as Material's switch, so with the
  flag off SystemUI is unchanged. Step 6b lets a Tally switch wait for the system: the thumb
  moves at the tap, and the lamp lights only while the switch is checked and the
  setting's own state reports on (Wi-Fi, Bluetooth, hotspot and tethering, NFC,
  Battery Saver, from the state their controllers already read), so a lamp never
  shows on for something that is off; turning off darkens it at once, and a
  failed change goes back unlit as stock. Accessibility still reports the
  checked state, as stock. Switch rows lose their own ripple and focus highlight
  (the switch shows both); restricted switches keep their code, including the
  window-wide filtering of obscured touches, and SettingsLib's rows and cards
  change only through layouts and drawables, which reach every app built on
  SettingsLib (the emergency alerts app included; presidential alerts stay on).
- Home and All apps (step 4a): keycap LEDs and the tallies row come only from
  Launcher's existing notification listener (the one behind notification dots),
  kept in memory: flags, category, channel, importance, the shade's own
  visibility rules and the progress bar and chronometer the system draws, never
  titles or texts. An LED lights only for live (a running notification, that is a
  foreground service, a Live Update or ongoing, that shows a live readout or an
  activity in progress: the system chronometer, a progress bar, a call,
  navigation, a stopwatch, a Live Update or playing media; permanent background
  services give nothing) or failed (an error notification) and respects the app's dot
  setting; the Notification dots switch turns both off. Nothing shows that the
  shade hides (a suspended app, Do Not Disturb's list suppression, with
  SystemUI's own exemptions), and the OS's own notices (the android and SystemUI
  packages, which only system-uid or SystemUI code can post as) give no tally or
  LED. One narrow attribution exception: the preinstalled system Clock
  (com.android.deskclock with FLAG_SYSTEM or FLAG_UPDATED_SYSTEM_APP, checked
  on its ApplicationInfo, never the package name alone) shows "Timer" or
  "Stopwatch" instead of its name while a count-down or count-up time shows,
  decided from structure only; screen readers still name the app, and every
  other app keeps its own name so no app can pose as the system timer. The row shows app names on the unlocked Home only;
  a work app shows its name and lamp but no readout, since a separately locked
  work profile is redacted in the shade and Launcher cannot see that lock
  without a new permission, and the private space never appears there. Sensors
  in use, screen capture and requested or on states are left out: Launcher would
  need new privileges to see them. The All apps letter rail takes its sections
  from stock's list, so a hidden private space gets no slot. No permission,
  manifest or allow-list change.
- Settings search (step 6c): SettingsIntelligence's panel is restyled and its
  target SDK goes from 31 to 37 with predictive back. Every platform behaviour
  change in between was checked against the app (173 checked, 22 apply, none
  changes what the app can do; the explicit-intent filter checks are either not
  tied to the caller's target or disabled at the pin). No permission or
  component change; the search index, its providers and their permissions are
  untouched, and results show what they showed.
- Clock (step 7, DeskClock fork: 182 reviewed LineageOS lineage-24.0 commits and
  19 of DiamaneOS's own, targetSdk 37): the exported surface goes from 7
  components to 4 (the launcher entry, the SET_ALARM-guarded API activities and
  the screensaver); the alarm init receiver and both widget providers are no
  longer exported, the new widget set-up activity is not exported and accepts
  only this app's digital-clock widget ids, widget intents are explicit, and every
  runtime receiver is RECEIVER_NOT_EXPORTED; GrapheneOS's e16191c4 (no foreign
  snooze or dismiss) and 9cde4084 stay; permissions are the approved set
  (notifications, promoted notifications, USE_EXACT_ALARM, one foreground-service
  type), DISABLE_KEYGUARD, READ_EXTERNAL_STORAGE and the legacy ones are dropped,
  and POWER_OFF_ALARM and MODIFY_AUDIO_SETTINGS never come in; an expired timer no
  longer asks to dismiss the keyguard; backup and device transfer stay off;
  alarms rely on GrapheneOS's power-save allowlist as before. Residual: the
  imported series was reviewed on its security paths (manifest, receivers,
  services, the public intent API, widgets, notifications), not every UI line,
  and two imported commits are marked as tool-assisted; the alarms database moves
  from version 8 to 12, so going back to GrapheneOS's Clock needs its data
  cleared. Timer and stopwatch are promoted MetricStyle notifications (status-bar
  chips) and, being public, show on the always-on display under the lock-screen
  notification filter; see -144 for chips over an occluded lock screen.
- Calculator (step 7, ExactCalculator fork): styling (DayNight with dynamic
  colour, Tally keycaps from the token library, resources only), the AC and
  "( )" keys, edge-to-edge and key labels that grow with the text size. The
  manifest is unchanged: no permission, component, backup (allowBackup stays
  false) or storage change; the keycap drawable is inflated by class name only
  from the app's own resources.
- Recovery and fastbootd titles (step 7, bootable_recovery fork, option A): the
  fork changes exactly two title literals ("DiamaneOS Recovery", "DiamaneOS
  Fastboot"); the compiled code is otherwise identical to the pin. Recovery runs
  as root and wipes, sideloads and verifies packages, so every GrapheneOS rebase
  of this fork is reviewed as a diff that must stay those two literals; signature
  checks against otacerts and GrapheneOS's recovery restrictions (no SD-card entry,
  serialno-constrained updates rejected, no serial number shown) are unchanged.
- Motion (step 5, WM Shell half, behind the Tally flag): pages opening and
  closing inside and between apps, predictive Back between activities and tasks,
  and the cold-start splash change how they move, never what opens or closes.
  Tally's page motion applies only where stock would play the framework's
  default animation for every moving window of a full-screen transition; stock
  keeps every transition with a keyguard or always-on-display flag (locked,
  occluding, unoccluding, appearing, going away), Recents, dreams, wallpaper
  and Home transitions (Launcher's), translucent windows (every permission,
  install and USB dialog), multi-window, freeform, letterboxed and embedded
  activities, apps' own transitions, and the consent and credential screens
  named in the code (the package installer, VPN and credential dialogs, all of
  SystemUI's activities, the permission grant, restricted settings, permission
  review and role request, device-admin activation and credential
  confirmation). Tally's motion is shorter than stock's (open about 310 ms,
  close about 190 ms, against 450 ms). The splash shows the app's keycap and a
  static lamp and goes only once the app has drawn, never leaving a blank
  window. The page transitions are measured as jank-monitor interactions, which
  write an event-log line with the interaction type, times and a constant tag,
  with no app identity, as stock's other interactions do.
- Motion (step 5, Launcher half): launching from a key, the return to Home and
  Back to Home change how the window moves (it grows out of its key and flies
  back into it on the Tally springs, Home dims and the key's neighbours part),
  never what opens, closes or goes Home, or when: the gesture's end target and
  the Back trigger stay GrapheneOS's. The Tally paths apply only to an upright
  phone with gesture navigation and one full-screen task (split screen, desktop
  windows, a trackpad, picture-in-picture and landscape keep stock); the recents
  input consumer, the keyguard paths and Launcher's start paths (quiet-mode
  profiles, the private space, disabled apps) are unchanged. A tally now opens
  its app through Launcher's own start path with the app's launcher intent
  (never the notification's), so it is logged on the device like any Home launch
  (Launcher's standard launch event); no new log or permission.
- Settings homepage (step 6a): regrouped and restyled only. Every top-level
  entry keeps its page, controller, conditions and restrictions (the pin's 30
  entries, compared by key and attribute), including GrapheneOS's own, work
  profile, private space and every security and privacy entry. State lamps show
  only what the entry's controller already reads (location on, a mode active),
  beside the summary that says it in words and never beside an administrator's
  restriction text, with no new read or listener. The bottom search opens the
  same Settings search with stock's conditions (hidden before setup, in setup
  flows and where search is off for the user); About phone gains a static mark
  and a "based on GrapheneOS" line; the homepage keeps stock's hiding of
  non-system overlays, and nothing new is logged or stored.
- Branding: the system's own label is the literal "DiamaneOS" (apps can read
  it; it reveals only the OS name, as the GrapheneOS label did), and the
  fallback boot logo, shown only when no boot animation is installed, is the
  DiamaneOS mark; neither carries any state.

## Decision record

- Daily-driver scope for privacy- and security-focused users; v1 scope as decided
  on 2026-09-26 (ten original entries after public v1, see the v1 summary).
- Passphrase-first is mandatory now; PIN-optional only via a later explicit
  decision.
- No scores or unsupported parity claims. Keep upstream attribution and a
  clear, separate project identity.
- 2G stays allowed by default (AOSP/GrapheneOS default). "2G network
  protection" and LTE-only are surfaced as opt-in hardening. (2026-09-25)
- Cellular security alerts: the plan's v1 item allows an honest "unsupported"
  state; on the current radio software that is the outcome. The radio software
  limitation is recorded as known. (2026-09-25)
- eSIM uses a maintained hardware-layer LPA with least privilege and
  hardening; OpenEUICC is not used. Candidate: the Qualcomm LPA. (2026-09-25)
  Reasons in GrapheneOS os-issue-tracker #6275 and #2631; from r9s the LPA's
  eSIM service is on by default. (2026-09-26) The stock LPA cannot list
  profiles on the FP6, so its service is off again from r9t (stock leaves it
  on); eSIM follows GrapheneOS (installed profiles keep working), and our own
  source LPA behind an off-by-default "eSIM support" switch is planned for its
  own build. (2026-09-27)
- The build properties and fingerprint keep Fairphone's stock product identity,
  as GrapheneOS keeps Google's, after Google blocked the DiamaneOS-branded
  identity as an uncertified device; DiamaneOS stays the name people see.
  (2026-09-26)
- Enforcing policy (r9s): the audio HAL may use QRTR sockets for DSP restart
  notifications (SELinux cannot limit which QMI service it reaches); nicmd may
  bind the ephemeral ports the modem reserves; the modem's study partition
  keeps its stock label; SystemUI cannot read the screen-decorations switch, so
  the camera/microphone privacy dot cannot be turned off over adb; the camera
  HAL's display-configuration hint stays denied. (2026-09-26)
- The production kernel keeps CONFIG_USERFAULTFD for ART's garbage collector, as
  GKI, Pixel and GrapheneOS do, with the user-mode-only restriction above, and
  turns KPROBES off (added back only if something needs it). (2026-09-26)
  Something needs it: the USB controller glue implements its controller hooks
  with kretprobes, so KPROBES stays on until those hooks are explicit calls;
  lockdown blocks probes from user space. The owner accepted this. (2026-09-27)
- debugfs stays as on r9s: built in and mountable. User builds never mount it;
  debuggable builds mount it until boot completes; SELinux governs access. In
  this kernel, turning mounts off also removes the
  in-kernel interface the display driver and recovery need. A kernel change
  that keeps the interface but refuses mounts is the stricter option and
  open. (2026-09-27)
- pstore/ramoops is on for bring-up builds with GrapheneOS's Pixel layout (the
  previous boot's kernel log and event logs in RAM across a soft reboot). Boot
  parameters handed to user space are logged by name only; release builds
  make the event-log device unwritable (a device init rule from r9t); moving
  the saved kernel log into credential-encrypted storage at first unlock is
  open. (2026-09-27)
  Reboots stay cold (the kernel default), so RAM is powered off and the
  saved logs do not survive a reboot or a kernel crash in practice; a warm
  reboot would keep them but also keep all of RAM. Not switching to warm
  reboots, since few kernel changes are still expected. (2026-09-27)
- VoLTE is required. The closed Qualcomm IMS stack is used with an explicit
  permission allowlist. (2026-09-25) The IMS data connection is to be brought
  up by DiamaneOS's own code (a small modem-facing service and a one-permission
  app) instead of Qualcomm's connectivity engine; closed code there is a
  fallback only. (2026-09-26) Replacing the IMS stack itself with source is plan
  direction, not a dated decision.
- The call-audio messenger keeps the one audio-routing permission it needs;
  replacing it with our own app is an open item. (2026-09-26) Replaced from
  r9s by DiamaneOS's own call-audio bridge with only the normal audio-settings
  permission; the audio-routing grant is gone. (2026-09-26)

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
  is enforcing). The flashlight controls reach the closed provider without the
  camera permission, with simple on/off and strength values only (AOSP design).
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
   null-cipher support state from the radio daemon; airplane-mode modem state.
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
- 2026-09-26: from the GrapheneOS branding inventory, added network location,
  geocoding and attestation services, the browser's own connectivity checks,
  probe names sent while checks are off, and the Android Auto DHCP hostname;
  endpoint contracts now number 17. Later the same day the plan gave network
  location and geocoding to FP6-103, and the Fresh-UI boundary recorded the
  owner-approved shell rework. The owner then chose network location with a
  DiamaneOS EU relay to Apple, Apple directly or Apple China directly, all
  opt-in and off by default, and no DiamaneOS geocoder: geocoding stays off unless the user opts
  in to OpenStreetMap's public Nominatim. A source review of the inherited keyboard found that it
  keeps personal-dictionary words and contact names in storage available before
  first unlock, allows them in backups and ignores the no-learning flag; the
  Everyday use rows record it. Swipe typing is not planned. The fix is written
  in a DiamaneOS keyboard fork and waits for a build and phone test.
- 2026-09-26: rows updated as bring-up findings landed (TIPC in the kernel,
  VoLTE and call audio, logging, adb authorization on debuggable builds, OEM
  unlocking, key provisioning and deletion, the source-versus-stock review);
  added the certificate-validity-at-boot row for DiamaneOS's own clock daemon;
  recorded the v1 scope change, the OpenEUICC
  no-go, the eSIM service default and JNI library, the retired Info release
  feed, the build identity, the r9s enforcing policy and the SoC serial
  exposure. On 2026-09-27 the fingerprint row recorded that the vendor debug
  service is never registered (kept inside the HAL process), and the kernel row
  recorded the r9t kernel: the module deny list, the production configuration
  and why KPROBES stays on. After the owner's decisions the same day, KPROBES
  is owner-accepted, debugfs stays as on r9s, boot parameters handed to user
  space are logged by name only, and a row covers the previous boot's logs
  kept by pstore/ramoops.
- 2026-09-27: for r9t, the eSIM row and decision record: the stock LPA cannot
  list profiles on the FP6, so its service is off again and its JNI library is
  gone; eSIM follows GrapheneOS until our own LPA ships. The firmware row
  records that the touch driver writes its stock firmware file to the
  controller when the versions differ, as on stock. The closed-file count stays
  at 720: the touch firmware came in and the JNI library went out. At
  integration the LPA wording was corrected (stock leaves its eSIM service
  on), the firmware row gained the touch driver's read-failure case, and the
  pstore row the device rule that makes the event-log device unwritable on
  release builds.
- 2026-09-27: added the privacy-indicator row for the Tally shell from the
  design-token review: the prototype's privacy chip does not grow with the text
  size, and its night-mode indicator colours fall below 3:1 over a light app
  bar. The native spec keeps stock's sp sizes and picks indicator colours by the
  area under them.
- 2026-09-28: the Tally prototype now draws the privacy chip at stock's sp sizes and
  takes indicator colours from the area under them, with a 2 dp edge; the
  prototype residual is removed from the privacy-indicator row.
- 2026-09-28: for the Tally shell's first commits (roadmap step 3), the
  privacy-indicator row becomes "Privacy indicators and disclosures under the
  Tally shell" and takes the plan's commit rules for indicators and for what
  the lock screen, prompts, footer, rows and toasts must show; a "Shell fork
  rebase lag" row covers carrying the shell onto each GrapheneOS release behind
  one build flag; the Fresh-UI boundary lists the lock-screen, transition, tile
  and settings-switch rules.
- 2026-09-28: the lock screen of roadmap step 3.3 (local tally-lock: the lock
  strip's sources and what it shows, the Tally clock, the indication line's
  fingerprint hint and error colour, the bouncer restyled by flagged resources)
  and step 3.5 (the SettingsLib switch restyle, the system label and the
  fallback boot logo) join the privacy row and the shell rules.
- 2026-09-28: the SystemUI privacy indicators of roadmap step 3.1 (local
  branch, not built): the privacy-indicator row now says that only the shade's
  privacy chip opens the stock privacy dialog, as in stock, while the lens ring
  and the new location lamp by the lens take no touch; it names the lamp by the
  lens beside the ring, lists the one-chip shade header and the 16 dp dot among
  the drawing changes, says how the area under an indicator is read, and records
  the limits: an app's own in-window scrim is not seen, the status bar chips sit
  under SystemUI's dialogs and shade, and the FP6's ring follows the declared
  rectangular cutout.
- 2026-09-28: security review B of step 3.3 (local tally-lock, not built) found
  that the lamp strip could crash SystemUI before its first items arrived (high
  if shipped: a crash takes every privacy indicator down), and that the strip
  showed an alarm more than 12 hours away, Bluetooth when on but not connected
  and the battery percentage always, and the always-on strip briefly after
  dozing with doze off, all more than stock's lock screen shows; the branch now
  fixes all of them, matching stock, with tests that fail on the old code.
- 2026-09-28: security review A of step 3.3 (local tally-lock, not built): the
  bouncer's Tally code only draws, and the fingerprint hint's count cannot affect
  unlocking; its background read and write are to be guarded, so a bad value only
  hides the hint instead of crashing SystemUI (fixed on the branch).
- 2026-09-28: the security review of step 3.6 (local tally-recents, not built)
  found that a Stop from Recents did not check that the lock screen was
  dismissed, as the Active apps dialog does, and two robustness gaps: a listener
  binder owned by SystemUI crashed it, and reports re-read every app's policy on
  the thread the privacy indicators use. The branch now refuses a Stop while the
  lock screen shows (occluded and dozing included), refuses SystemUI's own binders
  as the listener and survives a listener that throws, runs the work on SystemUI's
  long-running thread, and drops the listener when SystemUI lets go of Launcher.
- 2026-09-28: the security review of step 3.1 (local tally-status, not built) found
  that the area signal restarted the privacy dot's update delay, so an app
  flipping its status bar appearance fast enough could keep the dot from
  showing (critical if shipped), that the capture chips' edge was clipped, so
  their dark colour fell below 3:1 on light areas, and that the overlay check did
  not cover the Tally indicator resources; the check now refuses them, and the
  branch fixes the rest: the dot's colour no longer passes through its update
  delay, the capture chips' edge is their own border (light variants where no
  area is known), the VPN icon never overflows because of the dot's room, and the
  dot's touch area stays within its own column. Residuals: like the stock dot, the lens
  ring and the lamp by the lens are left out of screenshots, and on a locked
  phone and the always-on display they show whether the camera or location is in
  use.
- 2026-09-28: the volume panel, power menu and toasts of roadmap step 3.4 (local
  branches volume-left and tally-popups, not built): drawing, layout and motion
  only; ringer, Do Not Disturb, the safe-volume warning, stream muting and every
  accessibility action stay stock; the power menu keeps its actions, Lockdown,
  Emergency, its lock-screen filtering and the emergency affordance, and its new
  notes under Restart and Lockdown name the credential type in the lock screen's
  own words (the bouncer already shows it, so nothing new is disclosed); toasts
  change look and motion only. The volume dialog can sit on the left edge by a
  device value (FP6, as stock FP6).
- 2026-09-28: the step 3.3 fixes (local tally-lock, not built; two security reviews
  pending): the bouncer's PIN keys, typeface, emergency key and password field
  change by visual-only code behind the Tally flag (input, the keyboard, lockout,
  throttling, the duress and wipe counters and the emergency key's action are
  untouched); an always-on strip replaces what stock's always-on date line showed
  and shows nothing more; the fingerprint hint's count is kept per user in
  SystemUI's device-protected storage; the Tally clock moves into the shared clock
  library, which now reads the Tally flag itself, so Wallpaper & style lists it
  (ThemePicker and WallpaperPicker2 link the flag and token libraries; no new
  data reaches them).
- 2026-09-28: the step 3.1 fixes (local tally-status, not built): the privacy dot
  sits 16 dp from the top and side and the status icons keep clear of it, screen
  capture alone takes the capture colour, indicators go out on the fill spring
  (appearance stays instant), and a lens config lets a device place the lamp by
  the lens. Found and fixed before any build: the first round's instant dot show
  did not cancel a running fade-out, whose end then hid the dot and removed its
  window while a sensor was still in use; showing the dot now cancels it, as
  stock's fade-in does.
- 2026-09-28: Recents' Stop of roadmap step 3.6 (local branch tally-recents, not
  built; security review pending) joins the shell rules: a new SystemUI-proxy
  surface for the recents app only, with the Active apps checks re-run on every
  Stop.
- 2026-09-28: the shade and Quick Settings of roadmap step 3.2 (local branch
  tally-shade, not built) join the shell rules: tiles and keycaps keep the
  upstream handlers and unlock gating, the security footer and the privacy
  container keep their conditions, data and tap, notification cards keep every
  visibility and redaction rule and never light a lamp on a redacted card, and
  the media card drops album art.
- 2026-09-29: the security reviews of roadmap steps 4b and 6a (local Launcher3
  tally-recents and Settings tally-home, not built) found no issue. Launcher3
  gets no permission, manifest or allow-list change; Recents' Stop names only
  the swiped card's own app, holds SystemUI's list in memory only while Recents
  is open, and refuses touches through another window and taps within 0.5 s of
  a change. The Settings homepage keeps every top-level entry with its page,
  controller, conditions and restrictions, and its lamps and bottom search add
  no read, listener or stored state.
- 2026-09-29: the security review of roadmap step 4a (local Launcher3
  tally-home, not built) found three low issues, fixed on the branch before any
  build: the tallies row showed a work app's progress or timer while a
  separately locked work profile was redacted in the shade (work items now show
  no readout), the LEDs and the row showed notifications the shade hides (now
  SystemUI's own filters), and a tally tap could crash Home when its app had
  just gone away (the shade opens instead). The review of step 6c
  (SettingsIntelligence tally, not built) found no issue: the target SDK bump
  to 37 adds platform protections and changes nothing the app can do.
- 2026-09-29: the security review of roadmap step 6b (local frameworks_base
  tally-build and Settings tally-switches, not built) found no issue: switches
  that wait for the system light only once it reports on, restricted switches
  keep their logic and obscured-touch filtering, and SettingsLib's rows and cards
  change only in layouts and drawables.
- 2026-09-29: the security review of roadmap step 5's WM Shell half (local
  frameworks_base tally-build, not built) found no issue: the page motion,
  predictive Back and the keycap splash sit behind the Tally flag, keep stock
  for the keyguard in every state and for consent, credential, translucent and
  multi-window cases, and are shorter than stock's.
- 2026-09-29: the security review of roadmap step 5's Launcher half (local
  Launcher3 tally-motion, not built) found no issue: motion only, on the
  existing launch, gesture and Back paths, with no permission, manifest or
  allow-list change; a tally's launch is now logged on the device as every Home
  launch is.
- 2026-09-29: after the phone test of `tally.r9t.20260929.3` (local frameworks_base
  tally-build, not built): the Tally lock strip and always-on strip now show
  beside GrapheneOS's built-in smartspace, which hides its one line under them
  (952388b3eb35, 143dad85dd8c); before they showed, the always-on strip was made
  to show media only where GrapheneOS's always-on line does (-134). The lens ring
  lights only for the front camera (4bc99973aff3); the chip and the dot still
  show every camera.
- 2026-09-29: the owner's phone test of `tally.r9t.20260929.3` found that any
  app's decorated custom-view notification (a Clock timer) crash-looped SystemUI,
  taking the status bar, the shade and every privacy indicator with it, because
  the Tally card colour was read through the posting app's context (-135, high if
  shipped; fixed on frameworks_base tally-build de8a9edb4265). Tally code must
  read SystemUI and token resources only through SystemUI's own contexts, never
  a view inflated from another package; a review of that rule across the Tally
  code follows.
- 2026-09-29: with lock-screen notifications off (off unless set), GrapheneOS's
  smartspace line shows nothing; the Tally lock strip now drops its alarm and the
  always-on strip everything (frameworks_base tally-build 941eaab3f3be). The lamps
  for Wi-Fi, Bluetooth, Calm and the battery stay, as the keyguard status bar
  icons they mirror show whatever that setting.
- 2026-09-29: the lens ring keeps the camera privacy item's hold once the front
  camera has opened (frameworks_base tally-build f889e76a544e), and the date line
  under other clocks hides with lock-screen notifications off, as GrapheneOS's
  line (42cbc1c00780).
- 2026-09-29: the security review of the Tally contexts and fixes found no
  second crash of the -135 kind (no Tally code reads SystemUI, Launcher or token
  resources through a context another app controls, and no app-controlled data
  was found to crash SystemUI, WM Shell, Launcher, Settings or the pickers), and
  three low findings, fixed before any build: the lens ring's close/privacy-item
  race (-136) and a facing lookup that could throw on the main thread (-137)
  (frameworks_base tally-build 2e27b4dfdd12), and tallies for a profile
  Launcher's user cache did not know yet shown as the main user's (-138,
  Launcher3 tally-home 1ebdf19f1d). The lock strip's alarm now uses GrapheneOS's
  exact condition (cf4c47dc0240).
- 2026-09-29: Home's tallies band and search slot can be removed like a widget
  and restored from Home settings (Launcher3 tally-home 47aa5e6690): two
  booleans in Launcher's own backed-up preferences, no manifest, permission or
  allow-list change; removing the band only hides it (the listener keeps serving
  the key LEDs). The OS's own notices (the android and SystemUI packages) give no
  tally or LED (27e5352e94); they keep their status bar icons and screen
  recording its capture chip. Their security review (paused) found that the
  removable items' settings were read from credential-encrypted preferences
  through a real app context while the taskbar builds its device profile before
  the first unlock, which would crash-loop Launcher on every boot until then and,
  since Launcher's taskbar is the phone's navigation bar in this build, leave no
  Home or Recents before the first unlock, even from the emergency dialer (-139,
  high if shipped, never built; fixed on tally-home f885161fcb and Launcher3
  android17 240e56d46f, which read them through the device profile's own injected
  preferences). The rest of the review found no issue: no path reaches the model,
  the database, uninstall or app info; nothing new is exported; no manifest or
  permission change; only system-uid or SystemUI code can post as the android or
  SystemUI packages. Tally code in Launcher must not open credential-encrypted
  storage on paths that run before the first unlock.
- 2026-09-30: the Files review (step 7) fuzzed the archive code and reviewed trash
  (-140 to -143): the new archive code goes on (vendor_diamaneos local
  files-flags), trash stays off until DiamaneOS can say at trash time that files
  stay on the phone until emptied. Home's tallies and keycap LEDs count only
  things with a live readout or an activity in progress (owner, 30 September),
  never permanent background services.
- 2026-09-30: step 7's Calculator fork and recovery title fork (local tally
  branches, not built): no manifest change in Calculator; recovery changes two
  string literals only. Notification rows get themed app icons (vendor_diamaneos
  release config, the flag android.app.notifications_redesign_themed_app_icons,
  read only by SystemUI's notification icon provider).
- 2026-09-30: the Clock fork (local tally branch, emulator-checked, in build 6)
  and -144 (stock SystemUI: promoted chips show private content over an occluded
  lock screen; open).
- 2026-09-30: -144 reproduced on the FP6 with a test app (a Live Update's private
  chip text shows over the emergency dialer on a locked phone; GrapheneOS's
  chip code, not Tally's); a fix is being written as an upstream patch. Home's
  tallies read Clock's MetricStyle time with plain Bundle getters only, and the
  media-session read no longer throws on a malformed value (-145, fixed before
  any build). Every value Home reads from notification extras is app-controlled
  and must be read without a throwing path.
- 2026-09-30: -144 fixed (frameworks_base tally-build 7ea6b864d39e; the same
  change on the owner's fork, branch chip_leak, for GrapheneOS): a promoted chip
  whose notification the lock screen redacts shows only the public version's
  short text or the icon, also while the lock screen is occluded; notifications
  the lock screen hides give no chip. Its sweep left two low stock items open
  (-146: status bar notification icons are never redacted; -147: always-on
  promoted notifications with a trust agent and a separately locked work
  profile, and dream chips upstream).
