# DiamaneOS Threat Model and Product Boundaries

Revised 2026-10-10. Every revision is listed in
[THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md#revisions).

> DiamaneOS is based on GrapheneOS. It is not made or endorsed by GrapheneOS or Fairphone.

## How to read this document

- What DiamaneOS protects on the Fairphone 6 (FP6) today, against whom, how, and what is missing.
- It shows only what current development builds contain: each asset lists the protections that are
  built, and a missing protection is stated as a gap.
- Nothing is qualified yet, and no hardware claim is a demonstrated protection until it is verified.
- "GrapheneOS-derived" describes source lineage, not equal security: the FP6 lacks several hardware
  features GrapheneOS relies on ([hardware limits](#hardware-limits-and-evidence)).

- Start with the risks below, then the [asset table](#threats-by-asset).
- Status words are defined in [Evidence states](#evidence-states), other terms in [Terms](#terms).
- Development builds are named by date: the 2026-09-26 build is the first SELinux-enforcing one, the
  2026-09-27 build the next.
- `FP6-nnn` is a project task; -nnn (for example -144) is a finding in the private findings
  register.
- Companion files: [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md) (every revision) and
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) (Tally shell rules).

## Biggest remaining risks

1. **Development builds are unprotected:** debuggable, unlocked, public test keys, SELinux
   enforcement not validated per subsystem ([details](#current-development-state)).
2. **No MTE, much closed code:**
   - memory-safety bugs are not contained as on current Pixels.
   - The selected closed files recorded in `config/fp6-minimal/vendor-files.json` include large
     parsers of untrusted input, and the closed GPU driver and shader compilers run in every app
     ([details](#camera-microphone-sensor-streams-device-integrity)).
3. **Unpatchable firmware** that lags Android Security Bulletins: the project cannot build or patch
   it; `flash-steps` installs the firmware of the vendor files' stock release over older firmware,
   but no OTA carries firmware ([details](#firmware-security)).
4. **Credentials rest on the TEE:**
   - No StrongBox or Weaver.
   - Throttling unverified.
   - No passphrase policy.
   - Fingerprint class as the device maker declares it ([details](#bfu-user-data)).
5. **The modem is outside Android's control:**
   - Its isolation is set by closed firmware.
   - Its traffic bypasses network controls.
   - No fake-base-station detection or null-cipher control.
   - The 2G and LTE-only controls reach the modem (checked on the phone)
     ([details](#call-and-sms-content-subscriber-identity-coarse-location)).
6. **USB-C port control not yet built or checked on the phone;** Off cuts data and charging, but
   Qualcomm firmware still runs Type-C and USB PD ([details](#locked-device-data-kernel-integrity)).
7. **Attestation fails** ([details](#hardware-limits-and-evidence)).
8. **Supply chain:**
   - No independent review.
   - Builds take the DiamaneOS manifest branch without checking a signature.
   - Every FP6 build so far ran on one host, and builds made with the earlier build scripts compiled
     with network access.
   - No FP6 release signing.
   - Vendor inputs checked only against the vendor's own published hash
     ([details](#source-and-build-outputs)).
9. **Keyboard words readable before first unlock** in builds without the keyboard fork (in the
   manifest since 2026-09-27) ([details](#typed-text-and-personal-words)).

## Current development state

- Every build so far is a private bring-up build on a development device; none was distributed.
- Bring-up builds deliberately trade away protections to make hardware work.
- Bring-up builds are never handed out or used as anyone else's daily phone; their test-key signing
  gives no verified-boot or signature-permission protection and their SELinux policy is not
  qualified, so personal accounts and sensitive data stay off them.

- **Build type:**
  - `userdebug`, ADB on by default, `adb root`.
  - Since the 2026-09-26 build, ADB asks on the phone before trusting a computer and no developer
    key is pre-trusted (earlier builds accepted any computer).
  - Recovery accepts ADB unasked on unlocked or debuggable phones (AOSP design).
- **SELinux:**
  - Earlier builds fully permissive.
  - Since the 2026-09-26 build, debuggable builds boot enforcing with a per-domain policy from the
    denials captured on the last permissive build:
    - Narrow rules only.
    - Generic proc and sysfs nodes relabelled before any write grant.
    - Nothing new for apps or shell.
    - Dontaudit rules only for denials checked to be harmless and recorded with the device policy,
      such as the sensors HAL's writes of a factory proximity value that nothing reads.
  - The kernel boots permissive until init turns enforcing on (development mode, needed for an
    update's first boot), then locks it: `setenforce 0` fails and the enforcing mode is read-only.
  - The 2026-09-26 build passed the phone feature tests enforcing, and every build since runs
    enforcing.
  - Enforcing runs per subsystem are unverified (no owning task yet).
- **Bootloader and keys:**
  - Unlocked, public AOSP test keys.
  - OEM unlocking is managed by the OS as on stock (Settings shows the toggle; Android's persistent
    data block service keeps the bootloader's unlock-ability flag).
- **Network endpoints:**
  - DiamaneOS's servers for connectivity and DNS checks, time, the CT list, the provisioning
    proxies, the SUPL proxy and the network location relay (since the 2026-10-09 builds).
  - GrapheneOS's for the app catalog, the browser's own checks and updates, and Auditor.
  - Official builds, made by DiamaneOS's builder, also include the Updater, which asks DiamaneOS's
    update server.
  - That server is not live (since the 2026-10-06 builds).
- **Closed vendor code:**
  - Selected Qualcomm/Fairphone inputs are hash-pinned in `config/fp6-minimal/vendor-files.json`.
  - Source replacements and remaining closed components require their recorded review and
    qualification.
- **Debug interfaces:**
  - Userspace Qualcomm diag removed.
  - Debug USB functions remain.
  - Qualcomm's embedded USB debugger (EUD) is off by default (not checked on the phone).
  - Its device-tree node is disabled and its driver is not shipped (checked on the phone
    2026-10-06).
- **Firmware:**
  - The last flashed stock firmware.
  - Image sets from the current tools carry the vendor files' stock release for `flash-steps` to
    install (none built or tested on a phone yet).

### IMS development evidence

- The tested enforcing development image has ordinary voice, SMS and mobile data on both test
  subscriptions, including incoming/outgoing Wi-Fi-only calls with VPN lockdown.
- One immediately observed reporter restart preserved both registrations at the sampled points; this
  does not establish universal or long-term recovery.
- Native self-target carrier activation/cache/job tests passed without carrier traffic.
- No real emergency call has been made; the emergency framework tests are simulations.

- The source integration separates the modem-facing DCM, the restricted-network metadata broker and
  passive Wi-Fi observer/reporter.
- The broker has no Internet permission and does not forward application traffic or bypass app VPN
  policy.
- Carrier checks have bounded physical workers and a whole-request deadline, native Android retries,
  and durable carrier-delay fences.
- Audio control requires the current unique bridge identity and a platform-owned permission, beyond
  the normal Modify audio settings permission.
- The paired kernel reserves the DCM publisher role and restricts TIPC to local IPC.
- Since the 2026-10-05 build:
  - Only the DCM role and kernel sockets may queue local packets to the DCM port.
  - Local client closures are no longer relayed to it.
  - The name service refuses DCM records from remote nodes and caps lookups at 32 per client.
  - The image check requires the QRTR tunnel device to be off.
- The DCM daemon in that build no longer exits on QRTR buffer exhaustion, an unreachable node, a
  single failed broker call, a full Binder queue or a failed rebind (unknown errors still stop it).
- It publishes its service when the name-service lookup goes unanswered (local exclusivity comes
  from the kernel role).
- It ends modem sessions at once when the broker is lost (a 15 s grace period was tried and removed:
  the modem's IMS registration then failed at its next refresh).
- It tells a stalled modem client its sessions ended before releasing the Android requests.
- The Wi-Fi observer keeps its last default-route answer while a VPN has no known underlying
  network.
- Further in-call audio keys can only be set or read by their platform owners, not by any app with
  the normal Modify audio settings permission:
  - Keys: TTY, hearing aid, HD voice, slow talk, volume boost, device mute, CRS volume and the
    Bluetooth voice link and suspend keys.
  - Owners: system, radio, audioserver, and the Bluetooth stack for its keys.
- Entitlement applies every IMS provisioning setting even when one fails, repairs from the stored
  carrier result without a new SIM authentication, caps a carrier Retry-After at 24 hours and
  delivers a portal's finishing callback once.
- These later controls are in the 2026-10-05 build, where IMS calls over cellular and Wi-Fi passed
  on the phone; none is qualified.
- A local test carrier on the phone (2026-10-10) showed:
  - The sign-up portal's callbacks accepted only from the configured origin's main frame and acted
    on once.
  - A 10 year Retry-After stored as one day.
  - A 1 second validity refreshed no sooner than 30 seconds.
  - Activation retries stopping after 7 queries.
- sign-in used a stored token, not SIM authentication.

- The proprietary IMS/IWLAN/certificate components, modem firmware and selected network-control
  daemon remain part of the trusted computing base.
- Presigned OEM apps retain OEM signature verification.
- The inherited GrapheneOS installer policy also blocks unknown-source system-app updates:
  production allows its first-party installer and explicitly authorized shell installs; a
  debuggable-only override is not a production allowance.
- OEM signatures alone therefore do not permit ordinary apps to install these updates.
- The first-party catalog uses GrapheneOS's update sources.
- Production signing and locked verified boot do not exist, and development test keys are public.
- eSIM profiles are managed and downloaded by DiamaneOS's own LPA, off by default (checked on the
  phone: profile list, turning a profile off and on on 2026-10-06; a download on 2026-10-08).
- No stock LPA is shipped.
- The [IMS integration notes](THREAT_MODEL-HISTORY.md#ims-integration-notes) preserve historical
  evidence without making it acceptance of this later source cut.

## What DiamaneOS aims to defend against

- Remote and proximity attack surface (network including IP over cellular, Bluetooth, NFC, USB,
  media parsing): inherited GrapheneOS hardening and a reduced vendor surface; without hardware
  memory tagging, memory-safety bugs are not contained as on current Pixels.
- Malicious or over-permissioned apps: sandboxing, Network and Sensors permissions, Storage and
  Contact Scopes (inherited).
- Opportunistic physical access to a powered-off (BFU) device: file-based encryption with
  hardware-wrapped keys; no passphrase policy.
- Data exfiltration by the OS vendor: no telemetry or Google Mobile Services.

## What DiamaneOS does not defend against

- Persistence after compromise: development builds run unlocked with public test keys, so verified
  boot does not hold ([OS integrity](#os-integrity-on-a-released-locked-build)).
- Offline brute force of a weak credential: Gatekeeper backoff in the Qualcomm TEE is expected but
  unverified, no discrete secure element is exposed to Android, and no passphrase policy is
  implemented ([BFU user data](#bfu-user-data)).
- Sophisticated forensic extraction from an unlocked or powered-on locked (AFU) device.
- Firmware compromise: boot, TrustZone, hypervisor, modem, DSP, Wi-Fi and Bluetooth firmware come
  from Fairphone and Qualcomm; the project cannot build or patch it.
- Baseband exploitation beyond what SoC isolation provides; on the FP6 that isolation is set by
  closed firmware Android cannot check.
- Traffic the modem sends by itself (IMS, SUPL, control-plane location), which Android's network
  controls do not see.
- Removal of eSIM profiles, modem state or factory calibration by factory reset; they live outside
  userdata.
- A determined adversary with unlimited physical access and time.

## Hardware limits and evidence

- **No MTE:**
  - The stock CPU feature set does not expose memory tagging.
  - Its control properties are absent.
- **No StrongBox or Weaver exposed:**
  - Stock declares only default TEE KeyMint and RKP instances and no Weaver HAL.
  - Stock ships disabled Secure Processing Unit (SPU) software, but the FP6 device tree describes no
    such unit and the images carry no SPU firmware or provisioning, so none is usable.
  - Whether the chip has one is unknown.
  - Accurate: "stock exposes no StrongBox or Weaver", not "the phone has no secure hardware".
  - The eUICC, NFC controller and TEE are separate boundaries.
- **Attestation is TEE-only:**
  - With a custom key, a locked FP6 reports yellow, never green.
  - GrapheneOS's Auditor does not support the FP6.
  - Earlier builds did not enable remote key provisioning, so attestation was expected to fail.
  - Since the 2026-09-26 build, the device sets stock's provisioning properties (requests went via
    GrapheneOS's proxy, and go via DiamaneOS's since the 2026-10-09 builds) and reports the factory
    attestation IDs.
  - Provisioning reaches the server, but the TEE's certificate request fails while the bootloader is
    unlocked, on stock too: a locked stock FP6 produces the request, and the same phone fails the
    same way once unlocked.
  - So no attestation keys are provisioned, and attestation fails until the phone is locked with our
    key.
  - Whether the TEE accepts the yellow state is unverified.
- **No pKVM:** on current firmware the kernel runs under Qualcomm's Gunyah hypervisor, not KVM, so
  Android protected VMs are unavailable (observed on a bring-up build).
- **Radio isolation:**
  - The modem, Wi-Fi core and DSPs run from their own reserved memory (no-map; TrustZone
    authenticates and loads them, the hypervisor switches ownership).
  - Their shared data paths (IPA, Wi-Fi DMA, FastRPC) sit behind the apps SMMU (device tree and the
    running phone, 2026-10-07; no SMMU faults or subsystem crashes logged).
  - What the cores themselves may access is enforced in closed Qualcomm firmware and cannot be
    checked from Android.
  - USB-C port control, the fingerprint class and firmware patch lag are under [Locked-device
    data](#locked-device-data-kernel-integrity), [AFU unlock](#afu-unlock-auth-bound-keys) and
    [Firmware security](#firmware-security).
- **Vendor horizon:**
  - The Android 14-level vendor (VINTF level 8) should still run on Android 18.
  - Android 19 will very likely drop level 8, HIDL and kernel 6.1.
  - Once Fairphone moves the FP6 to a newer vendor, fixes for the current vendor code and firmware
    stop.
- **Kernel:** 6.1 (android14 KMI), with a recorded, intentional deviation for a 48-bit virtual
  address space.
- **Unverified:** TEE KeyMint, Gatekeeper and attestation behaviour, A/B failure recovery, rollback
  behaviour.

## Evidence states

A state changes only with new evidence. An asset's state is the weakest among its protections; its
status names it first, stronger items after, then what is still unverified with its owning project
task.

- **Not implemented:** not in current builds.
- **Bring-up (not qualified):**
  - In current development builds.
  - The security property is untested.
- **Observed (bring-up):** a narrow behaviour seen on a development build.
- **Observed gap:** confirmed missing or not working today.
- **Recorded:** stated in a project record, not rechecked this revision.
- **Accepted limitation:** a documented limit that stays.
- **Qualified:**
  - Verified on a production (`user`) build.
  - None yet.

## Threats by asset

Assets are grouped by attacker position. Each lists the threat (attacker and entry point), the
protections in current builds, what remains, and the status.

| Group | Asset | Status (weakest first) |
| --- | --- | --- |
| Remote | [Service metadata and OS identity](#service-metadata-and-os-identity) | Bring-up (not qualified); Observed (static) |
| Remote | [DNS privacy](#dns-privacy) | Not implemented |
| Remote | [App data from remote exploit](#app-data-from-remote-exploit) | Bring-up (not qualified) |
| Remote | [Traffic outside Android network policy](#traffic-outside-android-network-policy) | Accepted limitation |
| Remote | [Certificate validity checks at boot](#certificate-validity-checks-at-boot) | Bring-up (not qualified) |
| Cellular | [Call and SMS content, subscriber identity, coarse location](#call-and-sms-content-subscriber-identity-coarse-location) | Observed gap (bring-up) |
| Cellular | [SMS, SIM applets, broadcast alerts, carrier configuration](#sms-sim-applets-broadcast-alerts-carrier-configuration) | Accepted limitation (inherited) |
| Cellular | [Application processor and user data](#application-processor-and-user-data) | Bring-up (not qualified) |
| Cellular | [Telephony control and subscriber data](#telephony-control-and-subscriber-data) | Bring-up (not qualified) |
| Cellular | [eSIM profiles](#esim-profiles) | Bring-up (not qualified) |
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
| AFU | [AFU unlock, auth-bound keys](#afu-unlock-auth-bound-keys) | Bring-up (not qualified) |
| AFU | [AFU data under coercion or seizure](#afu-data-under-coercion-or-seizure) | Bring-up (not qualified) |
| BFU | [BFU user data](#bfu-user-data) | Observed gap |
| BFU | [Device-persistent data outside userdata](#device-persistent-data-outside-userdata) | Observed gap |
| BFU | [Removable media](#removable-media) | Bring-up (not qualified) |
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

- **Threat:**
  - Network observer or endpoint operator, via:
    - Connectivity, captive-portal and DNS checks.
    - HTTPS time, the CT list, key and DRM provisioning.
    - App and browser updates, OS update checks (official builds).
    - Network location and geocoding (when on).
    - Auditor remote verification and sample submission (opt-in).
    - Carrier entitlement connections.
  - The eSIM manager connects only to the eSIM server of a download, check or search the user
    starts, and to the servers of its own profiles' notifications.
- **Protection:**
  - No telemetry or GMS.
  - Since the 2026-09-26 build, the build properties keep Fairphone's stock product identity (brand,
    product, device, model), as GrapheneOS keeps Google's.
- **Remaining:**
  - Connectivity and DNS checks, HTTPS time, the CT list, the SUPL, PSDS, key and Widevine
    provisioning proxies and the network location relay use DiamaneOS's servers (since the
    2026-10-09 builds; image verification refuses a build that names GrapheneOS's hosts for them).
  - The app catalog, the browser's own connectivity check and component updates, and Auditor's
    opt-in remote verification still use GrapheneOS's.
  - Small-user-base hostnames are a fingerprint; the HTTPS time bootstrap resolves outside Private
    DNS.
  - With connectivity checks off, the DNS resolver still sends its probe names (under
    `dnscheck.diamaneos.de`) and the browser GrapheneOS's; the browser's own checks can be repointed
    only by rebuilding it.
  - With network location on (the setup wizard's location switch turns it on), nearby Wi-Fi and cell
    identifiers go to DiamaneOS's relay for Apple's location service.
  - The geocoder offers only OpenStreetMap's public service (opt-in, off by default); its queries
    name DiamaneOS in the user agent.
  - Auditor's opt-in remote features use GrapheneOS's attestation service; sample submission sends
    the full system property list.
  - CT enforcement fails open once the CT list is over 70 days old, so mirror freshness is a
    security dependency.
  - Official builds ask `releases.diamaneos.de` for updates every 6 hours; a failed check retries
    after 4 minutes, each retry waiting twice as long, up to 6 hours.
  - Each request names the model and release channel and reveals the IP address.
  - The server is not live: the name has no address record, so every check fails at the name lookup
    and no server is contacted (observed on the 2026-10-06 build).
  - "No telemetry" in closed vendor files rests on a static scan and one idle night:
      - An 8-hour capture of every interface (Wi-Fi and mobile data, enforcing) showed only the
        carriers' Wi-Fi calling and VoLTE tunnels and DNS, GrapheneOS's connectivity and app update
        checks, and local network traffic.
  - Scripted active use (browser, camera, gallery, app store, Wi-Fi scan, Bluetooth) showed only the
    apps' own traffic.
  - A GNSS fix (SUPL, PSDS) is unmeasured.
- **Status:**
  - Bring-up (not qualified): DiamaneOS endpoints (FP6-100, FP6-102 to FP6-112, FP6-103) in the
    image since the 2026-10-09 builds.
  - Connectivity and DNS checks and HTTPS time seen answered by DiamaneOS's servers on the phone
    (2026-10-09), the proxies and the relay checked in the image only.
  - Not implemented: DiamaneOS's app catalog and attestation service (FP6-106), the update server
    (FP6-128).
  - Observed (static): no contacted host in the selected closed files except GNSS cloud hosts
    removed by pinned configuration.
  - Observed (idle, one night on the 2026-10-05 build): no connection from vendor code to any third
    party.
  - The same in a scripted active-use run.
  - Unverified: egress during a GNSS fix.

#### DNS privacy

- **Threat:** network observer or on-path resolver, via DNS queries.
- **Protection:** the inherited Android default only.
- **Remaining:**
  - No encrypted-DNS default.
  - The time bootstrap, apps with their own DoH/DoT and modem traffic bypass Private DNS.
- **Status:**
  - Not implemented (FP6-079).
  - The inherited Android default applies.

#### App data from remote exploit

- **Threat:**
  - Remote network, media or web content, via carrier and Wi-Fi data, media files and streams,
    browser and WebView, captive portals, web content reaching the GPU shader compiler, and decoded
    frames an app re-encodes with the hardware video encoder.
- **Protection:**
  - Inherited sandbox, hardened_malloc, exec spawning.
  - Vanadium browser and WebView as mirrored unmodified APKs.
  - Media is decoded by the platform software codecs in their sandboxed process unless the owner
    turns on Hardware video decoding (Exploit protection, off by default, from the next boot), which
    adds the Qualcomm H.264, HEVC and VP9 decoders (no secure or low-latency variant).
  - The hardware codec service keeps the platform codec domain and loads our own seccomp filter and
    a codec gate before its main():
    - Its store offers only the codecs the boot allows (encoders only by default), and none if the
      codec configuration or the decoder node differs from the boot's state, but it always
      registers, so nothing waits for it.
  - The codec device nodes have their own SELinux type, open only to the codec service and owned by
    root.
  - The service has no camera, DSP or display-configuration device access.
  - The stock Codec2 libraries were built against Android 14, where GraphicBuffer is 256 bytes.
  - Binding them to Android 17's 3376-byte GraphicBuffer would be a heap overflow reachable by any
    app that attaches a surface to a codec.
  - Instead, the library's GraphicBuffer is replaced by an Android 14 sized one that fails closed,
    so a surface attached to the encoder gets an error.
  - With decoding on, the decoders' Surface buffers are wrapped in that object instead.
  - A build check disassembles the library and confirms the 256-byte allocations bind the 256-byte
    object.
- **Remaining:**
  - No MTE.
  - Closed GPU driver and shader compilers in every app process.
  - Closed video encoder, video driver and video firmware, and with decoding on the closed hardware
    decoders parse untrusted media.
  - Inherited captive-portal WebView.
  - Browser component updates still point upstream.
- **Status:**
  - Bring-up (not qualified): exec spawning, sandbox permissions and browser delivery unvalidated on
    the FP6.
  - Observed (bring-up): the encoder-only hardware codec service runs with its seccomp filter and
    lists encoders only.
  - Camera recording in every offered quality (rear and front) uses the hardware H.264 encoder.
  - H.264, HEVC, VP9 and AV1 (H.264 and HEVC up to 4K) decode on the software decoders, and a
    surface attached to the hardware encoder gets a clean error instead of a heap overflow
    (2026-10-06).
  - With decoding off the store offers the 5 encoders only, the decoder node is root-only and the
    service runs under the seccomp filter.
  - With it on, apps see exactly the hardware H.264, HEVC and VP9 decoders and a video played on the
    H.264 one.
  - A forced codec configuration mismatch left the store registered with no hardware codec and
    storage mounted after a framework restart (2026-10-09).
  - hardened_malloc active (48-bit VA kernel).
  - Unverified: inherited hardening (FP6-046), debug exposure and component removal (FP6-060,
    FP6-061), DRM limits (FP6-065), browser delivery (FP6-105, FP6-111).

#### Traffic outside Android network policy

- **Threat:** carrier or network observer, via modem IMS signalling and media, SUPL, and
  control-plane and network-initiated location.
- **Protection:**
  - None.
  - This document records the traffic.
- **Remaining:** invisible to the Network permission, VPN and Private DNS.
- **Status:**
  - Accepted limitation.
  - Unverified: per-carrier configuration review (FP6-090).

#### Certificate validity checks at boot

- **Threat:**
  - on-path attacker with an expired certificate, or blocking time sources, via the clock between
    boot and first network time (the PMIC RTC cannot be set from Linux).
- **Protection:**
  - DiamaneOS's `timekeepd` saves the clock's lead over the RTC counter whenever time is set and at
    least hourly.
  - At post-fs-data, before zygote, a one-shot step sets the clock from counter plus offset, only if
    nothing set it since boot and only to a time before 2100. Only that step holds CAP_SYS_TIME.
  - The long-running part holds no capability.
  - Each has its own SELinux domain, with no modem, network or app (replacing Qualcomm's time daemon
    and time app).
  - Network time is HTTPS, certificate expiry checked against build time.
- **Remaining:**
  - The clock starts at the build date on first boot and after factory reset.
  - After PMIC power loss (battery removed), at the last saved time: up to about an hour before the
    phone's last wake, plus the time off.
  - Until network time arrives, certificates expired since then still validate, longer if time
    sources are blocked.
  - RTC drift between saves (1 s resolution).
- **Status:**
  - Bring-up (not qualified): on the 2026-09-26 build the clock was right after an offline reboot
    (phone test 2026-09-27).
  - Unverified: suspend, manual and network time changes and battery removal, with SELinux enforcing
    (no owning task yet).

### Cellular and baseband

#### Call and SMS content, subscriber identity, coarse location

- **Threat:** fake base station, IMSI catcher, downgrading or null-cipher network, via 2G fallback,
  pre-authentication identity requests and radio security events.
- **Protection:**
  - opt-in "2G network protection" and the LTE-only, 5G-only and 4G-or-5G network types.
  - Both set Android's allowed network types.
  - The radio daemon turns them into the modem's radio-technology preference, one bit per
    technology, so GSM is left out when 2G is off and LTE-only allows LTE alone.
  - Since the 2026-09-26 build the default network type is set, so the stored 2G setting keeps LTE
    and 5G.
  - Settings offers Android's network-type list, filtered by what the modem supports, not the stock
    list of 34 modes with CDMA-only entries.
  - Checked on the phone: with 2G protection on, the modem's own setting read back has no GSM
    technologies, and with LTE-only it has LTE alone.
  - Calls over LTE still work.
- **Remaining:**
  - 2G stays allowed by default ([decision](#decision-record)), so a downgrade works unless the user
    opts in.
  - LTE/NR identity exposure remains.
- **Status:**
  - Bring-up (not qualified): the source review of 2026-10-05 traces both settings to the modem's
    preference request.
  - A build before 2026-09-26 did not pass the choice to the modem.
  - On the 2026-10-05 build the modem's own setting read back matched both controls.
  - Unverified: the modem's behaviour once the preference is applied (FP6-044), a radio-policy
    regression check (no owning task yet).

#### SMS, SIM applets, broadcast alerts, carrier configuration

- **Threat:** network or SIM-side sender, or carrier, via SMS and class-0/type-0 messages, SIM
  toolkit proactive commands, cell broadcast, and SIM access rules granting carrier privileges.
- **Protection:**
  - Inherited AOSP handling.
  - No project change.
- **Remaining:**
  - SIM applets act partly outside OS control.
  - Network "silent" SMS are acknowledged without display (3GPP rules).
  - SMS are parsed in modem and framework.
  - Apps given carrier privileges by the SIM can change carrier configuration.
- **Status:**
  - Accepted limitation (inherited).
  - Observed gap: after the phone starts in airplane mode with Wi-Fi calling, no alert channels are
    set until the next reboot.
  - The setup is refused while the radio is off and not repeated when airplane mode ends.
  - On a normal start both SIMs accept the channels.
  - Unverified: telephony bring-up (FP6-044).
  - Broadcast alert delivery (no alert received yet).

#### Application processor and user data

- **Threat:**
  - over-the-air attacker or compromised modem firmware, via modem parsers (RRC/NAS, SMS, modem
    IMS), then shared memory, the IPA data path, QMI/QRTR services, modem remote-storage and file
    services.
- **Protection:**
  - Modem image authenticated by the stock secure-boot chain.
  - AP-side modem services narrowed and confined to own SELinux domains.
  - Modem crash dumps off.
  - The modem's file server (TFTP over QRTR) and the protection-domain mapper are open-source
    linux-msm builds (tqftpserv, pd-mapper) in place of Qualcomm's closed daemons, which started as
    root:
    - Each runs as its own user with no capabilities and no network, and reads only the modem
      partition and the remote processors' firmware names.
    - Only the file server writes, to its own directory in /data, where it deletes only its own
      files and follows no symbolic links.
  - The file server is a fork carrying upstream's pending fixes for memory errors and a path escape
    the modem could trigger, with host tests for them.
- **Remaining:**
  - Modem firmware unpatchable by the project and behind ASB.
  - What the modem may access is enforced in closed firmware and cannot be checked from Android (its
    memory and data paths are separated in the device tree).
  - Silent modem restarts hide crash-and-retry attacks.
  - Modem state in its own partitions survives factory reset, its file-server data in /data does
    not.
  - Both daemons parse modem-supplied data in C, contained by their domains.
  - QRTR has no per-service access control, so either can reach any QMI service.
  - pd-mapper is pinned to an older upstream commit because later ones do not build for Android.
- **Status:**
  - Bring-up (not qualified): no protection claim.
  - Unverified: modem and telephony bring-up (FP6-042, FP6-044), debug exposure and component
    removal (FP6-060, FP6-061).

#### Telephony control and subscriber data

- **Threat:**
  - Malicious app via binder or compromised modem via QMI, reaching the closed radio daemon (QCRIL),
    Qualcomm IMS app, DiamaneOS's call-audio bridge or the eSIM LPA.
- **Protection:**
  - Explicit privileged-permission allowlists with denials.
  - Own SELinux domains.
  - No radio daemon socket.
  - Radio daemon not a binder service.
  - diag access removed.
  - The call-audio bridge replacing Qualcomm's call-audio app holds the normal audio-settings
    permission and DiamaneOS's platform-owned call-audio control permission (no audio-routing or
    capture permission, no network).
  - Its domain reaches only the radio daemon's call-audio service and the audio server.
  - The eSIM manager holds two privileged permissions (embedded subscription control and privileged
    phone state, exact allowlist) and network access for eSIM downloads, runs in the platform's
    privileged-app domain and is bound only by the phone process.
  - The phone process takes eUICC card commands only from the active LPA.
- **Remaining:**
  - Closed code parses untrusted input.
  - The update path of presigned vendor apps is not locked.
  - Enforcing since the 2026-09-26 build (both SIMs in service), not qualified per subsystem.
- **Status:**
  - Bring-up (not qualified): allowlists and domains reviewed.
  - Runtime and enforcing unverified.
  - Calls with audio work through the call-audio bridge on development builds (last on the
    2026-10-05 build).
  - Unverified: telephony bring-up (FP6-044), enforcing runs (no owning task yet).

#### eSIM profiles

- **Threat:**
  - Thief or examiner after reset, coercer, a malicious app or compromised modem via profiles
    retained in the eUICC and the LPA's card access.
  - A network attacker or a rogue server during a download.
- **Protection:**
  - Installed profiles work as SIMs with or without an LPA (GrapheneOS baseline).
  - DiamaneOS's own eSIM manager (Apache-2.0, Kotlin, no native code) ships disabled.
  - Settings > Network & internet > eSIM support enables it and restarts the phone, as GrapheneOS
    does for Google's LPA.
  - When on, it lists, turns on and off, renames, deletes and downloads profiles (SGP.22 v2 consumer
    download: activation code, confirmation code, SM-DS search) and erases them when Android asks.
  - The card commands are Android's own (EuiccCardController over the radio's logical channels).
  - Every change is confirmed on its screen, which ignores taps while another app covers it.
  - Network only for a download, a check or a search the user starts, after a dialog that names the
    server and what it receives (EID, IMEI, model code, radio capabilities), and for the
    notifications of profiles it downloaded, right after the user's own change.
  - Other apps' download requests and Android's server lookups are refused.
  - No analytics.
  - HTTPS to the server in the code only (port 443, no redirects).
  - TLS trusts only the two GSMA production CI roots it ships that the eUICC also lists: never
    system or user CAs, never a test CI.
  - The server's certificate must carry the SGP.22 TLS role, server use and its host name; the eUICC
    checks the server's own signatures.
  - Responses are size-limited and parsed strictly.
  - Stopping before the package download cancels as "postponed", which keeps the operator's order
    usable; a dry run checks a code's server without sending the code.
  - Stores only salted hashes of the ICCIDs it installed.
  - No camera permission: the camera app's QR scanner reads codes, and its "Open with" hands an
    `LPA:` code to Add eSIM, or the user pastes it.
  - Any app on the phone can hand over a code that way, web pages can't; it only fills in the code,
    and the user still agrees before anything connects.
  - Codes stay out of screenshots and the recents preview; the Add eSIM windows hide other apps'
    overlays.
  - Never logs the EID, ICCIDs, IMSIs, codes or servers.
  - The framework's eUICC transport logs eUICC commands and answers in full at verbose level; those
    tags stay at info (-181, image check).
  - Android marks slot 1 as the built-in eUICC (stock leaves this unset).
  - No stock Qualcomm LPA, its libraries or grants (image check). OpenEUICC is not included
    (GrapheneOS os-issue-tracker #6275 and #2631).
- **Remaining:**
  - One download checked on the phone (2026-10-08); the FP6 eUICC reports SGP.22 2.2.2. Its CI list
    is not yet read.
  - Android's card command always puts the IMEI in the device information the eUICC signs for the
    server (SGP.22 allows it).
  - Server data is parsed in a privileged process with network and eUICC access (Kotlin parsers
    only).
  - Certificate revocation is not checked (optional for the LPA in SGP.22).
  - Notifications of profiles not downloaded here stay queued until the user removes them; a carrier
    may not release a deleted profile without them.
  - A factory reset erases eSIMs only after Android recorded a download (the manager's downloads
    count) or with developer options on.
  - Duress erases the profiles through the framework directly, without the LPA (inherited, untested
    on the FP6).
  - Android's eUICC code logs the EID on debuggable builds.
  - Its card error paths log only a redacted value (**** on user builds, a hash otherwise; telephony
    fork, implemented, not yet built), and the manager checks the slot first to keep those paths
    rare (-176).
  - Debuggable builds also log eUICC command data in the radio log.
- **Status:**
  - Bring-up (not qualified): checked on the phone (2026-10-06): the switch, the ISD-R channel, the
    profile list, and turning a profile off and on.
  - Downloads, checks, search and notifications implemented, not yet built.
  - Host unit tests pass.
  - Unverified on the phone: rename, delete, downloads, eSIM erase (FP6-089).

#### Radio-off expectation, location privacy

- **Threat:** network or passive observer, via the modem attaching on its own, or airplane mode.
- **Protection:** airplane mode puts the modem in low-power mode.
- **Remaining:**
  - Modem silence in airplane mode or without the radio daemon is unverified.
  - A device setting keeps the SIM powered in airplane mode.
- **Status:**
  - Bring-up (not qualified).
  - Unverified: telephony bring-up (FP6-044).

### Location

#### Location history

- **Threat:** vendor cloud, assistance-server operator, carrier or local log reader, via GNSS HAL
  configuration, SUPL, PSDS, control-plane positioning and logs.
- **Protection:**
  - The GNSS HAL and its location libraries are built from CodeLinaro source: they load only the
    GNSS adapter and the QMI LOC API, so Qualcomm's IZat, XTRA and NTRIP libraries cannot load, and
    the HAL serves no socket to assistance daemons.
  - Qualcomm cloud, XTRA and crowdsourcing paths also excluded by pinned configuration.
  - PSDS off.
  - SUPL user-selectable (Off, proxy, standard).
  - Network location and geocoder off by default.
  - IMS geolocation has no network geocoder.
- **Remaining:**
  - SUPL defaults to the GrapheneOS proxy.
  - "standard" SUPL goes straight to the carrier-configured server (Google's, on the carrier tested
    so far).
  - SUPL requests carry cell information.
  - The SUPL client and the GNSS engine are modem firmware: the HAL passes the server address and
    asks for the data connection, but cannot change what the modem sends.
  - control-plane positioning runs in the modem.
  - GNSS engine state on persist survives reset.
  - Builds before the 2026-10-01 build logged the GNSS engine at info level and wrote the
    serving-cell identity to the radio log on every registration poll (about twice a second on one
    SIM), readable over adb and in bug reports.
  - With the redaction, the radio log still shows the physical cell ID and channel, as AOSP does,
    and development builds show the redacted fields as unsalted hashes (AOSP behaviour; user builds
    hide them).
- **Status:**
  - Bring-up (not qualified): cloud paths absent and pinned.
  - No GNSS fix recorded.
  - warning-level GNSS engine logs and a radio log that redacts the serving-cell identity like the
    framework's other cell logs are in builds since the 2026-10-01 build.
  - On the 2026-10-05 build the radio log showed cell identities only as hashes.
  - Source-built HAL: implemented, not yet built.
  - Unverified: assisted GNSS (FP6-103), GNSS bring-up tests (FP6-045).

### Malicious and over-permissioned apps

#### App and user data

- **Threat:** malicious or over-permissioned app, via permissions, background listeners, IPC/URI
  grants and sensors.
- **Protection:** per-app Network and Sensors permissions, Storage and Contact Scopes (inherited).
- **Remaining:** no DiamaneOS-specific app controls are implemented.
- **Status:**
  - Bring-up (not qualified): inherited controls in the build, unvalidated on the FP6.
  - Unverified: inherited hardening (FP6-046).

#### Kernel integrity and all data

- **Threat:**
  - Malicious or compromised app or renderer process, via the GPU driver node (reachable by every
    app, as on stock), binder, loaded network-protocol modules and socket families, and the DSP
    remote-call driver (named groups only).
- **Protection:**
  - GrapheneOS kernel hardening configuration merged into the vendor kernel.
  - Lockdown at confidentiality level, as on GrapheneOS (user space can neither modify nor read the
    running kernel; the kernel policy also requires kcore, KGDB, kexec and hibernation off).
  - SELinux socket and device restrictions.
  - Modules reduced to product use (since the 2026-09-27 build no protocol modules without product
    use: CAN, 802.15.4/6LoWPAN, the kernel NFC socket family, PPTP/L2TP, the in-kernel Bluetooth
    stack).
  - SELinux enforcing locked on once set (development mode only for the boot-time handoff).
  - dmesg root-only.
  - Profiling work must never weaken lockdown or tracing restrictions in user builds.
- **Remaining:**
  - No MTE; large vendor driver surface; hardening hand-merged into a vendor kernel with an
    intentional KMI deviation.
  - KPROBES on, as the USB controller glue's hooks use kretprobes; debugfs built in for kernel code,
    not mountable since the 2026-10-01 build (both under [Decision record](#decision-record)).
  - USB host-class, debug and trace drivers still load.
  - TIPC re-enabled (GrapheneOS disables it) for Qualcomm's mobile-data stack, as a module without
    network bearers or crypto.
  - Under enforcing SELinux no user-installed or privileged app reaches it (sockets only for two
    radio daemons, configuration only for system components with the network-admin capability,
    including the network stack module).
  - Confidentiality lockdown also empties tracefs (no Perfetto or atrace system tracing) and refuses
    kernel-memory reads by BPF programs, so Android's per-app CPU time accounting (per-app CPU use
    in Battery usage) and lmkd's memory-event OOM listener do not start, as on GrapheneOS's Pixels.
  - lmkd still kills by memory pressure.
  - XFRM state dumps, which SELinux allows only netd, system_server, the network stack,
    netutils_wrapper, dumpstate and Qualcomm's nicmd, show IPsec keys as zeros:
      - Lockdown redacts them, and both kernels also redact them always (implemented, not yet
        built). nicmd needs the dump for Wi-Fi calling:
      - The modem negotiates the tunnel, nicmd installs its IPsec states over Wi-Fi and, at teardown
        or rekey, dumps every state to find and delete its own by SPI.
  - Every app reaches the kernel's userfaultfd code (ART's garbage collector needs it), but apps are
    forced into user-mode-only mode (`vm.unprivileged_userfaultfd=0`, `/dev/userfaultfd` root-only
    and SELinux-denied), so they cannot freeze the kernel mid-copy to widen race windows.
  - Bugs there remain ordinary kernel surface, fixed through 6.1 LTS updates.
- **Status:**
  - Observed (bring-up): module reduction and production configuration run on the phone since the
    2026-09-27 build (kernel check: the configuration matches policy, no shipped module depends on a
    removed one; phone check 2026-09-27: no removed module loaded, no symbol errors, configuration
    values as specified).
  - Debuggable builds run enforcing since the 2026-09-26 build.
  - Lockdown at confidentiality again: implemented, not yet built (builds before 2026-10-05 ran it,
    tracing empty; the 2026-10-05 build and later ran integrity).
  - Unverified: kernel build (FP6-041), debug exposure and component removal (FP6-060, FP6-061),
    enforcing runs (no owning task yet).

#### Camera, microphone, sensor streams, device integrity

- **Threat:**
  - Malicious app or remote content reaching closed vendor code through a platform service: camera,
    media, display, audio, sensors, NFC and Bluetooth HAL interfaces (the hardware video encoder
    service, which every app except isolated processes can call).
  - same-process GPU libraries.
  - persist and vendor data files parsed by closed code.
- **Protection:**
  - per-file allowlisted, hash-pinned stock selection.
  - SELinux grants bound to each service domain.
  - A closed HAL's internal endpoints reachable only from its own process (audio since the
    2026-09-26 build).
  - Fewer service users, groups and capabilities.
  - Tight device-node permissions.
  - Qualcomm diagnostics, telemetry and factory services excluded.
  - The GNSS HAL and its location libraries, which parse SUPL and network-initiated requests,
    injected data and the modem's QMI LOC reports, are CodeLinaro source builds with CFI and, on the
    parsers, the integer overflow sanitizer, as Qualcomm's own builds (implemented, not yet built).
  - Closed services with no client are not shipped or not registered: the display colour service,
    which any platform app could start, is not installed.
  - The camera provider's offline camera service has no VINTF declaration and no registration grant,
    so servicemanager refuses it (its library stays, because the CHI override links it) (since the
    2026-10-05 build).
  - Compressed music is decoded by Android's sandboxed software codecs: the audio policy has no
    compressed-offload output, so apps cannot hand MP3, AAC, FLAC or other bitstreams to the closed
    DSP decoders (since the 2026-10-05 build; music playback works, phone test 2026-10-05).
  - App audio effects run only in AOSP code:
    - The effects configuration lists AOSP's software equalizer, bass boost, virtualizer, reverb and
      visualizer without DSP offload halves, so Qualcomm's closed offload effect bundle and
      visualizer, which received every app's effect parameters even with offload playback off, are
      not shipped (-175).
  - Qualcomm's VoIP echo-cancellation and noise-suppression descriptors, which turn on the DSP's
    tuned processing, and its volume listener, which passes the speaker volume to the HAL for
    calibration, are built from CodeLinaro source (since the 2026-10-06 builds).
  - The primary audio HAL, PAL, AGM, AGM's ALSA plugins and the ADSP audio daemon are built from
    Fairphone's published FP6 sources with control-flow integrity and the integer overflow
    sanitizer, as stock.
  - The HAL registers only the AGM service inside its own process, no PAL service.
  - At each speaker start PAL applies the per-unit speaker calibration (checked on the phone
    2026-10-06, as on stock).
  - It no longer answers the factory speaker queries or accepts the factory speaker protection
    modes, and ignores voice UI keys without a sound trigger HAL.
  - This closes parameter-query bugs any app could reach and keeps those keys away from the closed
    voice UI library (-178; checked on the phone 2026-10-06).
  - In the same process the AudioReach graph services (Fairphone publishes only their headers),
    their tuning server, the voice UI interface and the deadline manager stay closed.
  - Qualcomm's closed perf2 daemon is not shipped: it ran as root with setuid, kill and sys_nice,
    could read and write every app's `/proc` files and change the scheduling of apps and the
    compositor.
  - The power HAL is LineageOS's open libperfmgr, run as system with CAP_SYS_NICE only:
    - It may write only the CPU and GPU frequency limits, the GPU wake trigger and the tap-to-wake
      switch that init hands to it, sets uclamp on the threads of performance hint sessions, and
      reaches the kernel's scheduler boost only through fixed init property triggers.
  - The source-built thermal HAL, which reports temperatures and throttling to the framework, runs
    as system without capabilities instead of root, as on Pixels.
  - ueventd hands it only the threshold nodes it writes (checked on the phone 2026-10-06).
  - The stock camera and composition extension load our stand-in for the closed perf client, so they
    no longer look up the perf2 service.
  - It passes on only the camera's open, close and snapshot hints, as the power HAL's camera launch
    and camera shot boosts:
    - At most 5 s each, a hint held until release at most 2 s, ended early when the camera releases
      it, and every call carries its own time limit, so the power HAL ends the boost even if the
      camera dies.
  - It sends them from its own thread with one-way calls, so the camera never waits for the power
    HAL.
  - Every other request does nothing.
  - For this the camera provider is a power HAL client, as on Pixels.
  - Observed (2026-10-05 build): the open, configure and snapshot boosts each end within about 1 s,
    when the camera releases them.
  - The first configureStreams runs 36 % faster.
  - The power stats HAL is our own source (stock has none):
    - It reads the SoC and remote-processor sleep counters through the qcom_stats driver's
      `/dev/stats`, runs as its own user without capabilities, and is the only process that may open
      that node, limited to the seven read commands it uses.
  - It serves the counters only to the platform's power statistics clients and has no energy meters.
  - The Adreno OpenCL runtime and its compiler (about 34 MB), selected only for the camera, are not
    shipped, nor the software chromatic-aberration library that links them.
  - The stock camera provider runs under a seccomp filter that our loader library installs before
    the provider's main(), so before CamX is loaded: threads but no child processes, Unix and QRTR
    sockets only, no memory both writable and executable.
  - The provider does not start without it.
  - For now calls outside the list are logged and allowed.
  - Image verification refuses an official user build (a release) whose filter only logs.
- **Remaining:**
  - Large closed parsers (camera about 185 MB) without MTE.
  - Most closed daemons without seccomp, and the camera provider's filter does not block yet.
  - The closed thermal engine runs as root, as on stock.
  - The power HAL may change the scheduling (uclamp) of any app's threads, as on Pixels.
  - The camera provider's power HAL access covers the whole interface, not only the camera boosts.
  - Code running in it (CamX parses data apps send through the camera service) could:
    - Keep power modes on (fixed or capped CPU and GPU clocks, a raised GPU floor, tap-to-wake).
    - Repeat boosts (raised CPU floors, the scheduler boost).
    - Raise the uclamp of app, SurfaceFlinger or system_server threads through a hint session.
    - An effect on speed, battery and heat, with no access to data (-172).
  - The camera sensor device nodes share one SELinux label with the video codec and other video
    nodes, so another HAL's grant to that label also reaches them (-188).
  - The camera HAL programs the camera image processor through command buffers the kernel runs
    unchecked (-191), and the image processor's firmware runs the camera software's other command
    programs (-192).
  - The speaker amplifiers' raw register files are root-writable under the generic sysfs label
    (-190).
  - Android 14 ABI vendor code on Android 17.
  - per-file purpose partly generic.
  - Closed code updated only through Fairphone stock releases.
  - The closed Qualcomm sensors sub-HAL lists a vendor-private ambient colour (RGB) sensor with no
    required permission (-153).
  - GrapheneOS's Sensors permission covers the standard sensor types and takes a vendor type's
    permission from the HAL, and an empty one meant none, so any app could read the sensor with the
    Sensors permission off (shown on the FP6, 2026-10-01).
  - The frameworks/native fork keeps the Sensors permission when the HAL names none (since the
    2026-10-01 build; the post-flash checks of the 2026-10-01 and 2026-10-02 builds found the colour
    sensor refused without the Sensors permission).
- **Status:**
  - Bring-up (not qualified): selection reviewed per subsystem.
  - Enforcing per subsystem untested.
  - Observed (bring-up): the colour sensor needs the Sensors permission (-153).
  - Unverified: debug exposure and component removal (FP6-060, FP6-061), closure gate (FP6-208),
    camera (FP6-045, FP6-203), audio (FP6-044, FP6-205), enforcing runs (no owning task yet).

#### Persistent hardware identifiers

- **Threat:** any app, via system properties, persist files, sysfs and logs.
- **Protection:**
  - vendor-internal property types with no read grant.
  - Narrowed sysfs labels.
  - Wi-Fi MAC randomization.
  - The stock traceability daemon excluded.
  - Our own IMEI tool, logging no identifier:
    - Reads only the two IMEIs, the Bluetooth address and the Wi-Fi MAC from the traceability
      partition (read-only, its own user, no capabilities).
    - Writes the IMEIs to the modem at boot.
    - Passes the address to the Bluetooth HAL in properties no app or other vendor service can read.
    - Passes the MAC to the Wi-Fi driver in a RAM-only file only ueventd's firmware loading may
      read.
  - Boot parameters handed to user space logged by name only (since the 2026-09-27 build; on the
    phone the Wi-Fi MAC appeared in no log, pstore or DropBox entry).
  - /proc/cmdline shows parameter names only, except module options, whose values Android's modprobe
    reads there (checked on the phone 2026-10-06).
  - Since the 2026-10-02 kernel the Wi-Fi platform driver logs no MAC address the modem or the
    device supplies.
  - Enforcing SELinux.
  - The SoC serial number (sysfs `soc0/serial_number`) has its own SELinux type that no vendor
    service may read (no shipped program reads it; only kernel code uses the serial) and is
    root-only.
  - So of the domains the platform's sysfs-wide read rules still name, only root platform daemons
    (init, ueventd, vendor_init, vold, apexd) can open it.
  - A build check lists every domain allowed to read it.
  - The UFS storage's serial number and its SCSI serial pages, whose generic label 22 system and
    vendor domains may read, are root-only too (checked on the phone 2026-10-06).
- **Remaining:**
  - Bluetooth uses the factory address, as stock and Pixels do (checked on the phone 2026-10-06;
    earlier builds generated one with a fixed prefix that marked the phone).
  - Bug reports show it in the settings dump, as on GrapheneOS, and the stock HCI implementation
    logged it at info level whenever Bluetooth started (its two tags are kept at warning level since
    the 2026-10-06 builds).
  - Bug reports also print the kernel command line, which up to the 2026-10-06 builds carries a
    hardware identifier the bootloader passes.
  - The device tree's copy of the boot arguments (`/proc/device-tree/chosen/bootargs`) still holds
    it, though no bug report or shipped program reads it.
  - Debuggable builds log subscriber identifiers (ICCID, IMSI, phone number) in full in the radio
    log, as AOSP does, so their logs and bug reports hold them.
  - Log Viewer's Report button (system error notifications) copies the log to the clipboard and
    opens the public DiamaneOS issue tracker, and so does sandboxed Google Play's crash notice
    (implemented, not yet built), so identifiers reach it only if the user pastes them.
  - The stock camera HAL logs camera module serial numbers at every start, at error level, so user
    builds silence its whole log tag (debuggable builds keep it; since the 2026-10-06 builds,
    effective on user builds).
  - In builds up to the 2026-10-05 build the SoC serial is readable by 16 system and vendor domains
    through platform sysfs read grants and imported Qualcomm rules (no app domain).
  - The Wi-Fi driver uses the factory MAC, as stock and Pixels do (checked on the phone 2026-10-06;
    earlier builds used the chip's generic Qualcomm address).
  - Android records it as the factory MAC and shows it in `dumpsys wifi`, `dumpsys netd` and bug
    reports, as on GrapheneOS.
  - netd's info lines and the supplicant's debug lines carry it at every Wi-Fi start on debuggable
    builds only (user builds do not log those levels).
  - The Wi-Fi service's lines when it records the MAC or fails to set it, and the driver's error
    line as Wi-Fi starts, no longer print it (all implemented, not yet built).
  - With Wi-Fi verbose logging on, the Wi-Fi service logs the address each connection uses, which is
    the factory MAC only for a network set to use it.
  - Since the 2026-10-05 build Wi-Fi joins networks with a randomised address (-170: earlier builds
    used the hardware address).
  - A flash dump reveals persist data.
- **Status:**
  - Observed gap (bring-up): some hardware serials exposed as system properties on permissive
    builds.
  - Whether enforcing builds deny the read is not yet shown.
  - Unverified: enforcing runs and an identifier probe test (no owning task yet), Wi-Fi and
    Bluetooth bring-up (FP6-043).

#### Secondary-profile data

- **Threat:** another user or profile, via user switch, stopped and running profiles, unified vs
  separate challenge.
- **Protection:** CE/DE separation (inherited).
- **Remaining:**
  - No credential policy is enforced across independent challenges.
  - unified-challenge profiles follow platform semantics, no invented independent key.
  - A stopped session holds data until deleted (session end is not deletion).
- **Status:**
  - Bring-up (not qualified): inherited behaviour, unvalidated.
  - Unverified: encryption and hardening validation (FP6-046).
  - Not implemented: credential-policy enforcement (FP6-071).

### Proximity attackers

#### Device integrity, paired-device data, Bluetooth address

- **Threat:** BR/EDR or LE attacker, or tracker, via closed controller firmware, the closed stock
  Bluetooth HCI HAL and the GrapheneOS host stack.
- **Protection:**
  - GrapheneOS host stack.
  - Bluetooth off by default.
  - consent-gated profiles.
  - SIM access profile off.
  - Closed HAL confined (no network, persist write or diag).
  - Since the 2026-10-05 build:
    - Our own small service hosts the closed HCI implementation in place of the stock service, so
      the FM, ANT, SAR, config-store and TPI libraries the stock service linked (but never
      registered) are not shipped.
    - The sensors HAL no longer loads the dynamic-sensor sub-HAL, which parsed HID sensor
      descriptors from paired Bluetooth and USB devices (head trackers are therefore not supported).
  - The HCI service installs a seccomp filter before it loads the closed implementation: threads but
    no child processes, Unix sockets only, no memory both writable and executable, kill only as
    SIGKILL (the implementation's restart after a controller failure).
  - A call outside the list stops the service (SIGSYS; init restarts it) (checked on the phone
    2026-10-06: pairing, music and a headset call in trap mode).
  - No service context lets the HAL register the ANT or SAR interfaces (since the 2026-10-06 build).
- **Remaining:**
  - Controller firmware and HAL unpatchable by the project.
  - No MTE.
  - The classic address is the factory one, fixed for the device's life, as on Pixels.
  - It reaches bug reports through the settings dump.
- **Status:**
  - Bring-up (not qualified): with our own HCI service, pairing, music (AAC) and a headset call
    (mSBC) work, and the seccomp filter in log mode saw no call outside its list.
  - The adapter uses the factory address (phone test 2026-10-06).
  - Unverified: Bluetooth and audio bring-up (FP6-043, FP6-044), debug exposure and component
    removal (FP6-060, FP6-061).

#### SIM applets, presence, NFC services

- **Threat:** contactless reader (also against a locked, screen-off or switched-off phone) or
  malicious tag, via NFC reader and listen modes and controller-to-SIM routing.
- **Protection:**
  - Default route pinned to the host (no SIM routing unless an app registers it).
  - No UICC or embedded secure element features declared.
  - Controller firmware file is an exact, hash-pinned stock file.
- **Remaining:**
  - NFC on by default.
  - Registered SIM routes reachable while locked or off.
  - secure-NFC support unverified.
  - Closed controller firmware, which the HAL can also update.
- **Status:**
  - Bring-up (not qualified): reader works.
  - Routing tests not run.
  - Unverified: NFC bring-up tests (FP6-045), eSIM/SIM (FP6-089).

#### Wi-Fi MAC (tracking), device integrity

- **Threat:**
  - Passive Wi-Fi observer, malicious access point, hotspot client, nearby Wi-Fi Direct or Aware
    device, or LAN peer, via probe requests, association, Wi-Fi driver and firmware, and
    wake-on-LAN.
- **Protection:**
  - Current builds expose only station features to Android.
  - Implemented, not yet built: hotspot, Wi-Fi Direct and Wi-Fi Aware, as GrapheneOS has them on
    Pixels. hostapd is AOSP's, in its own SELinux domain with no device rules.
  - The hotspot stays off until turned on and turns off after 10 minutes without clients.
  - Wi-Fi Direct starts only on an app's request and stops after 2.5 minutes idle.
  - Aware runs only while an app is attached.
  - The hotspot takes a new random address at each start and Wi-Fi Direct random addresses (the
    image check requires both declarations).
  - Source-built Wi-Fi HAL and driver.
  - MAC randomization.
  - No wake-on-LAN: the driver configuration turns the magic-packet wake-up off and the supplicant
    asks for none (since the 2026-10-06 build).
  - The hardware address is the factory MAC, written for the driver at every boot from the
    traceability partition (checked on the phone 2026-10-06).
- **Remaining:**
  - Builds before 2026-10-05 had no Wi-Fi capability overlay, so the Wi-Fi service treated the
    hardware as unable to randomise its MAC address and the phone joined networks with a globally
    administered (hardware) address (-170, seen on the phone on 2026-10-04).
  - Since the 2026-10-05 build the FP6 Wi-Fi overlay declares randomisation support, the image check
    requires it, and the phone associates with a locally administered address.
  - Closed Wi-Fi firmware on the over-the-air path.
  - On builds without the wake-on-LAN change a LAN peer can wake the device with a magic packet.
  - The factory MAC is sent only where Android does not randomise (networks set to use the device
    MAC), and scans, Wi-Fi Direct and the hotspot use random addresses only if the driver's
    randomisation works (not yet checked on the phone).
  - Once built, hostapd and the supplicant's Wi-Fi Direct code also parse frames from hotspot
    clients and nearby devices, next to the driver and firmware.
  - On Wi-Fi networks used for Android Auto the device sends a DHCP hostname derived from the device
    name (inherited default).
- **Status:**
  - Bring-up (not qualified): randomised association address seen on the phone (2026-10-05 build).
  - Unverified: connectivity bring-up (FP6-043).

#### Locked-device data, kernel integrity

- **Threat:**
  - Malicious USB device or host, forensic tool or malicious charger, via USB-C data lines (host and
    gadget roles), USB descriptors, USB PD and charger firmware.
- **Protection:** GrapheneOS USB-C port control (implemented, not yet built):
  - Modes: Off, Charging-only, Charging-only when locked (with or without the before-first-unlock
    exemption) and On.
  - User builds default to Charging-only when locked, as GrapheneOS, so USB data is off before first
    unlock; the mode takes effect at boot.
  - The framework sets the kernel's deny-new-USB hook, which refuses new connections, and the port
    state.
  - Init triggers turn USB data off with the USB controller's data switch for Off and Charging-only,
    and when the port is unplugged while locked; at unlock data comes back.
  - Off also turns off charging while the OS runs, as on Pixels: after the data cut, init suspends
    the charger input in Qualcomm's charger firmware, so the phone draws no current from the port
    and runs on its battery.
  - User builds start every normal boot with USB data and charging off, as GrapheneOS starts Pixels
    with the port off: at early-boot init turns the USB controller off and, as soon as the charger
    firmware is up, suspends the input.
  - When system_server starts, the stored mode turns them back on as it allows (On: both;
    Charging-only modes: charging; Off: neither).
  - New connections are refused from first-stage init.
  - Debuggable builds keep the port on at boot.
  - The input suspend is runtime state: every mode but Off lifts it, and so do charger mode and
    shutdown; the firmware restarts with every boot.
  - So the phone charges when powered off, in charger mode, fastboot, fastbootd and recovery.
  - Only vendor_init writes the data switch and the input suspend (and ueventd, which the platform
    lets write all of sysfs); the USB HAL may not.
  - Lockdown and device-policy requests to stop USB data become port states (data off, charging
    kept).
  - While such a request holds, the port reports data as force-disabled, so the later re-enable
    arrives and the stored mode comes back.
- **Remaining:**
  - Differs from GrapheneOS on Pixels:
    - Type-C and USB PD run in Qualcomm's charger firmware, and the OS has no switch for them (the
      Type-C driver offers only role swaps).
    - So in Off the port still attaches, negotiates USB PD with a charger and may still power an
      attached accessory (unverified).
  - Pixels turn the Type-C pins off, so nothing attaches.
  - Differs from Pixels at boot: a user build charges until the charger firmware is up in early
    second-stage init, and its USB controller runs until early-boot, with new connections refused
    from first-stage init (Pixels: both off once the Type-C driver loads in first-stage init).
  - A user build that never reaches system_server (a boot loop) does not charge while Android boots,
    as on Pixels; powered off, in charger mode, fastboot and recovery it charges.
  - When port control turns the port off and on to make devices reconnect (unlock after a connection
    while locked, leaving Charging-only), charging also pauses for about 1.5 s, as on Pixels.
  - Unverified: that a reboot or power-off without init's shutdown step clears the input suspend,
    what the charger firmware does with it after the firmware restarts by itself, and how soon after
    boot the firmware takes init's boot write.
  - A connection made before the phone locked stays up until unplugged (Charging-only when locked,
    as GrapheneOS).
  - Debuggable builds only: a restrictive mode is reset to On at boot, and the lock modes do nothing
    while USB debugging is on, unless a test property that a reboot clears is set.
  - The port is USB 2.0 only (no display or other alternate modes); the FP6 has no external
    accessory contacts.
  - Qualcomm's embedded USB debugger (EUD) is off by default (not checked on the phone); its
    device-tree node is disabled and its driver is not shipped, so the system cannot turn it on.
  - The USB controller takes its connect and role events from the Type-C port controller (UCSI)
    only; it no longer waits for EUD's cable events, which reported only EUD's own connects (checked
    on the phone 2026-10-06).
  - Debug USB functions present; broad USB host drivers loaded; the USB descriptor carries the
    device serial; closed charger firmware.
- **Status:**
  - Implemented, not yet built: port control, including Off's charging cut.
  - Bring-up (not qualified): userspace gadget configuration reviewed.
  - Unverified: port control, the data switch and the input suspend on the phone, debug exposure
    audit (FP6-060), USB modes (FP6-045).

### Physical access to a powered-on, locked device (AFU)

#### AFU user data

- **Threat:** forensic tools, via USB, the lock screen, and firmware download and dump modes.
- **Protection:**
  - Inactivity auto-reboot (inherited).
  - Panic RAM dumps off.
- **Remaining:**
  - USB-C port control not yet built.
  - Builds up to the 2026-10-06 builds honour download and dump reboots.
  - The kernel keeps a minidump on panic, retrieval path unverified.
  - EDL stays reachable with physical access (the hardware keys) and a signed programmer.
  - No forensic-proof claim.
- **Status:**
  - Implemented, not yet built: port control.
  - Checked on the phone (2026-10-06): the running system cannot reboot into emergency download or
    dump mode (reboots ignore those reasons and restart normally) or turn panic dumps on (the
    download-mode parameters and files are read-only).
  - Observed (bring-up): panic dump mode off (the kernel default, which the build checks).
  - Unverified: debug exposure audit (FP6-060).

#### Previous boot's kernel log and event logs

- **Threat:**
  - Root or system code after a reboot, anyone with USB debugging on and an authorized computer, or
    a forensic examiner, via a RAM region kept across a soft reboot (pstore/ramoops), its copy in
    the system's crash-report store, and Qualcomm minidumps.
- **Protection:**
  - Unreadable by apps.
  - Readable by the log group (which adb's shell user has) and a few system domains, the
    crash-report copy through dumpsys.
  - identifier-carrying boot parameters logged by name only.
  - dmesg root-only from boot.
  - On release builds init removes all permissions from the event-log device, so only DAC-override
    holders could write it (debuggable builds keep it writable).
- **Remaining:**
  - The next boot copies the kernel part to device-encrypted storage, readable by the system before
    first unlock.
  - The RAM copy stays until overwritten.
  - Qualcomm's minidump driver registers the log areas, so a collected minidump carries them.
  - Reboots and kernel crashes reset cold, powering RAM off, so in practice nothing survives.
  - A one-off warm reboot on the 2026-09-27 build kept both logs (the bootloader does not clear
    RAM).
  - Reboots stay cold.
  - Every domain holds the platform's write grant on the event-log device, so its file mode is the
    only control.
- **Status:**
  - Observed (bring-up): the region, with GrapheneOS's Pixel layout, registered on every boot of the
    2026-09-27 build.
  - The release-build rule is built but untested (that build is debuggable, so the rule is
    inactive).
  - No earlier build registered the region.
  - Kernel check (2026-09-27): placed at boot without a fixed address.
  - The saved console keeps only notice-level and more severe messages (the console log level), not
    the full log.
  - Observed on the 2026-09-27 build: same address every boot.
  - pstore empty after a normal reboot.
  - After a warm reboot both logs present, readable by the shell user by exact name (directory
    listing denied), the system's copy only after first unlock.
  - The boot-parameter value in none of them.
  - Unverified: debug exposure audit (FP6-060).

#### AFU unlock, auth-bound keys

- **Threat:** lifted or spoofed fingerprint, via the side fingerprint sensor.
- **Protection:**
  - Strong authentication after reboot and timeouts.
  - Lockout in the trusted app.
  - The vendor debug service never registered (kept inside the HAL process) and unreachable by
    policy.
- **Remaining:**
  - The DiamaneOS fingerprint HAL declares Class 3 (strong), the class Fairphone's stock service
    declares for the same sensor, matching code and trusted app.
  - Spoof resistance rests on Fairphone's qualification and is not measured by DiamaneOS.
- **Status:**
  - Class as on stock, not measured.
  - Bring-up (not qualified): unlock works.
  - The first enforcing boot showed the module needs its debug service registered, which the HAL
    answers in-process since the 2026-09-26 build (enrolment and unlock work, phone test
    2026-09-27).
  - Unverified: fingerprint bring-up (FP6-045), credential validation (FP6-046).

#### AFU data under coercion or seizure

- **Threat:** coercer, or seizure then extraction, via the lock screen and buttons.
- **Protection:** duress credential and wipe (inherited).
- **Remaining:**
  - Duress key destruction relies on TEE key deletion and flash erase, not a secure element.
  - Duress erases eSIM profiles through the framework, which needs the radio's channel to the eUICC
    (untested).
  - eSIM profiles may survive.
- **Status:**
  - Bring-up (not qualified): inherited code present, untested on the FP6.
  - Unverified: duress test with synthetic data (FP6-046), eSIM erase (FP6-089).

### Physical access to a powered-off device (BFU) and data outside userdata

#### BFU user data

- **Threat:** opportunistic access or flash readout (EDL, chip-off), via flash contents and the TEE.
- **Protection:**
  - FBE with metadata encryption and hardware-wrapped keys.
  - TEE Gatekeeper backoff (expected).
- **Remaining:**
  - No passphrase policy (the development device uses a PIN).
  - No StrongBox or Weaver, so throttling rests on the TEE, a larger surface with a public history
    of key-extraction attacks.
  - Backoff timing and survival across reboot and image restore unverified.
- **Status:**
  - Observed gap: passphrase policy not implemented (FP6-070, FP6-071).
  - Throttling unverified.
  - Observed (bring-up): FBE (policy version 2) with wrapped keys and metadata encryption running.
  - Unverified: encryption and hardening validation (FP6-046).

#### Device-persistent data outside userdata

- **Threat:** physical attacker with flash access, or an app on a permissive build, via the persist
  partition, modem file systems, eUICC and hypervisor VM storage.
- **Protection:**
  - SELinux labels.
  - Unsafe stock permissions closed.
- **Remaining:**
  - Factory reset clears none of these.
  - Hardware identifiers and calibration readable from a flash dump.
  - Modem and GNSS state persists.
- **Status:**
  - Observed gap: no reset-residue inventory.
  - Some stock permissions still to be tightened.
  - Unverified: debug exposure audit (FP6-060), eSIM erase (FP6-089).

#### Removable media

- **Threat:** anyone who takes the microSD card or USB storage, or hands the phone a tampered card.
- **Protection:**
  - An admin user can adopt the microSD card as phone storage (GrapheneOS removes adoption;
    DiamaneOS restores it for the card slot only).
  - An adopted card has dm-default-key AES-256-XTS metadata encryption and file-based encryption
    with its keys on the phone's own storage.
  - It is ext4 with metadata checksums, never f2fs.
  - Before every mount `e2fsck` checks it in the untrusted fsck domain with a 10 minute limit, and
    anything worse than safe repairs leaves it unmounted.
  - It is mounted noexec, nosuid and nodev with errors=remount-ro.
  - It holds shared storage only: apps are never installed on or moved to it.
  - Forgetting a card destroys its keys.
  - Formatting, forgetting and making a card portable need an admin user.
  - No adoption while an update's checkpoint is uncommitted.
- **Remaining:**
  - Portable cards and USB drives are not encrypted.
  - Encryption hides contents and file names, not the card's size, layout or that it is adopted, and
    does not stop someone with the card from corrupting it.
  - The keys are not hardware-wrapped (the card has no inline crypto engine) and go with the phone's
    data: after a wipe the card is unreadable.
- **Status:**
  - Bring-up (not qualified; FP6-076, FP6-077).
  - Observed (2026-10-09): adoption works, a raw read of the card's first 8 GB shows neither the
    test files nor an ext4 superblock, shared storage moved to the card, apps cannot be moved to it,
    and forgetting a card destroys its keys.
  - Unverified: the checkpoint case, factory reset and duress with an adopted card.

### Boot chain, firmware and trusted execution

#### OS integrity on a released, locked build

- **Threat:** write access to partitions, via the boot chain, OTA, recovery, sideload and the
  inactive A/B slot.
- **Protection:**
  - Public test builds are signed with the public AOSP/AVB test keys, carry test-keys in the
    fingerprint and a never-lock record, and `flash-steps` prints the never-lock rule first and
    prints commands only for an image set whose verify report matches it and passed.
  - In official builds the Updater installs only packages signed with a key in the system's OTA
    certificates (`otacerts.zip`, which RecoverySystem and update_engine check).
  - While the build is not tagged release-keys or those certificates include a public AOSP test key,
    it downloads and installs no update and says so (built since the 2026-10-06 builds; not checked
    on the phone).
  - The boot control HAL, which sets the active and successful slot, runs as its own user with
    CAP_SYS_RAWIO only (for the UFS boot-LUN switch) instead of root with every capability (as on
    stock).
  - ueventd gives it only the GPT disks of the A/B LUNs, misc and the UFS BSG node (checked on the
    phone 2026-10-06: it marks the slot successful).
- **Remaining:**
  - No locked configuration exists: development images run unlocked.
  - A test-key build must stay unlocked, since anyone can sign images with the public test keys.
  - recovery's sideload accepts packages signed with the test key (-68).
  - The `flash-steps` wipe writes Fairphone's factory FRP image (clearing factory reset protection
    and keeping OEM unlocking allowed) and zeros misc, as Fairphone's own factory flash does.
  - A locked phone also boots, as the stock OS, any images signed with Fairphone's own AVB keys.
  - In 16.111.0 those (vbmeta and vbmeta_system) are not AOSP's public test keys.
  - Whether the chip's secure-boot root is a production key is unchecked.
- **Status:**
  - Observed (2026-10-06 builds): SHA-256 hashtrees, as on stock (an image check requires it).
  - Observed gap (bring-up images): release-style rollback indexes and a public test key.
  - Observed (bring-up): AVB chain built and parsed.
  - Unverified: signing verifier (FP6-035).

#### Firmware security

- **Threat:** exploits of already fixed bugs in XBL, TrustZone, hypervisor, modem, DSP, Wi-Fi and
  Bluetooth firmware, or peripheral controller firmware files in `/vendor/firmware`.
- **Protection:**
  - per-image firmware inventory with hashes
    ([`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json)).
  - In the tools, in no build yet:
    - Every image set carries the firmware of the stock release its vendor files come from:
      - Copied byte for byte from the authenticated factory package.
      - Checked against those hashes when packaged and again by `build verify`.
      - No image below an older release's Qualcomm anti-rollback version.
  - `flash-steps` writes it before the OS, in Fairphone's order and on both slots, so a later slot
    switch cannot fall back to older firmware.
  - It prints firmware steps only over an older release and stops for a newer one, and only for a
    saved bootloader state showing an unlocked FP6 with unlocked critical partitions.
  - Controller firmware files are exact, hash-pinned stock files.
- **Remaining:**
  - The project cannot build or sign firmware.
  - Firmware lags ASB until Fairphone ships a release and DiamaneOS selects it.
  - No OTA carries firmware (`build verify` refuses an A/B partition list with only part of it).
  - The FP6 bootloader does not report its firmware version, so the downgrade check relies on the
    release the user states or the earlier image set.
  - A wrong statement can let older firmware through, which the boot chain's anti-rollback (version
    1 in both inventoried releases) does not stop.
  - With both slots written there is no firmware fallback slot during a flash, as with Fairphone's
    flash.
  - Gunyah and its trusted VMs are closed.
  - As on stock, the NFC HAL may update its controller from its firmware file.
  - Since the 2026-09-27 build, the touch driver writes its file to the touch controller whenever
    versions differ or the controller's version is unreadable, checking only the header, so verified
    boot is what keeps that file authentic.
- **Status:**
  - Observed gap: a phone keeps the firmware of its last stock flash.
  - Settings names that release from partition hashes (implemented, not yet built).
  - Firmware delivery through image sets and `flash-steps` is in the tools (2026-10-06), in no build
    yet and untested on a phone.
  - Unverified: firmware review (FP6-206), stock input verification (FP6-040).

#### Keys, Gatekeeper throttling, fingerprint templates

- **Threat:**
  - Compromised system or HAL process, or a malicious app reaching TEE clients, via the TEE driver
    and client services, TEE listener daemon and fingerprint HAL.
- **Protection:**
  - TEE access only for named HAL domains.
  - Unused userspace TEE proxy services removed.
  - RPMB access limited.
  - A wipe asks the TEE to delete all old keys.
- **Remaining:**
  - Closed TEE with a public history of key-extraction bugs.
  - No StrongBox or Weaver.
  - KeyMint key deletion on wipe (rollback resistance) set since the 2026-09-26 build, unverified.
- **Status:**
  - Observed gap (builds before the 2026-09-26 build): an unused userspace TEE proxy ran.
  - Removed from the 2026-09-26 build on.
  - Bring-up (not qualified): services run.
  - Enforcing and throttling persistence unverified.
  - Unverified: TEE and credential validation (FP6-046, FP6-050), attestation limits (FP6-065,
    FP6-106).

### Supply chain, signing and development process

#### Source and build outputs

- **Threat:**
  - Compromised upstream host or project, build dependency or build host, via source fetch, prebuilt
    toolchains, the kernel prebuilts, the build account and the image tools built from source.
- **Protection:**
  - Builders trust the DiamaneOS manifest branch.
  - It keeps GrapheneOS and AOSP projects at the exact commits of the GrapheneOS release it is based
    on, whose signed tag the maintainer verifies when merging that release (recorded in the merge
    commit).
  - Fairphone and CodeLinaro projects are pinned to exact commits.
  - DiamaneOS projects follow their `android17` branches.
  - The pinned `repo` tool's tag signature is checked.
  - Each build records the resolved manifest (every project's commit) in its image set, and a full
    source preflight (projects at their resolved commits, no local manifests, nothing undeclared)
    runs at sync and before and after every Android build.
  - Official builds need clean projects; other builds name local changes, list them in `build.json`
    (`modified`) and mark the set not reproducible.
  - Downstream commits are signed, but the public build commands do not verify those signatures.
  - Reproducing a recorded build (`build sync --resolved-manifest`) checks out the manifest commit
    the image set's `build.json` records, which must be in the history of the manifest branch (it is
    never fetched by its id).
  - The resolved manifest must match the SHA-256 `build.json` records and equal that commit's
    manifest apart from the project commits, so it chooses only commits, from the remotes that
    commit names.
  - After the sync, `repo manifest -r` must give it byte for byte.
  - The generated vendor tree is installed only when the recipe digests its generation recorded in
    its provenance equal the checkout's recipes, and the preflight accepts it only while a
    descriptor binds it to the environment file and those digests and every file matches its
    inventory.
  - The kernel, modules and device trees come from the kernel prebuilts the manifest selects; `build
    verify` checks boot, vendor_boot, dtbo and the module lists against that checkout at the commit
    `build.json` names.
  - `kernel publish` refuses a kernel build whose files differ from its inventory, that contains
    private key material or strings naming the build host, or that was built from a modified tools
    checkout.
  - Network-denied compilation: the public build commands compile in an unprivileged network
    namespace, with agent and bus socket variables removed from the compile environment.
  - Image tools built from the pinned source with their hashes recorded; one build/make fork, whose
    single change keeps the device's boot header fields when images are rebuilt, pinned like the
    other forks; every build recorded in `build.json`.
- **Remaining:**
  - Qualcomm/CodeLinaro and Fairphone sources and toolchains carry no upstream signatures; common
    inputs are common-mode.
  - The public build runs as an ordinary user on an unmanaged host, without a separate build
    account, root-owned tools checkout or service sandbox.
  - The network namespace blocks IP networking only: Unix sockets in the filesystem stay reachable,
    so a socket to a service with network access (a container daemon, for example) is a way out, and
    the build can read whatever the user can.
  - A set packaged from a build by hand (`build package` after `m`) records `out/` as `m` left it:
    the source and the vendor files are checked, the compile is not (no network isolation; times
    only for a note).
  - `build.json` marks it `android_build: manual` and not reproducible.
  - Host packages are recorded, not pinned; a build with `--allow-network` compiles with network
    access (recorded for the kernel, vendor and Android steps); a modified tools checkout, or
    packaging by another tools commit than the build, is recorded as not reproducible.
  - The manifest branch, the DiamaneOS branches it follows and the tools in it (`tools/diamaneos`,
    branch `main`) are not tagged or signature-checked at build time:
      - A builder takes what the branches hold when it syncs, so a compromised maintainer account or
        GitHub repository is caught only by review of the recorded resolved manifest.
  - Builders do not re-check GrapheneOS's signed tag; comparing the manifest's GrapheneOS projects
    with GrapheneOS's signed manifest is a manual step.
  - The maintainer keys that sign the commits are not published.
  - A reproduction builds the project commits the given resolved manifest names.
  - `repo` fetches them by id, and a host may serve a commit that is on no branch of the repository,
    such as a fork's commit on GitHub, so a reproduction is as trustworthy as the image set the
    resolved manifest and `build.json` come from.
  - The kernel prebuilts are binaries a maintainer built and published; a builder checks them
    against the published commit, not by rebuilding.
  - `kernel build` can rebuild them from `kernel_qcom-6.1` for comparison, but module signatures and
    the embedded certificate differ per build.
- **Status:**
  - Observed gap (FP6 path): every FP6 build so far ran on one host.
  - Builds made with the earlier build scripts compiled with network available.
  - No second independent build.
  - Bring-up (not qualified): the 2026-10-05 build ran the public build commands (syncing the
    DiamaneOS manifest branch, network-off compilation, the full preflight, bound generated inputs
    and the kernel prebuilts check), and its build record shows network isolation on, a clean tools
    checkout and the resolved manifest.
  - Recorded (generic target): a network-denied build path.
  - Unverified: reproducible environment.

#### Closed vendor inputs

- **Threat:** compromised vendor download or support page, or a network attacker at first download,
  via the stock factory package.
- **Protection:**
  - Exact archive hash pinned everywhere.
  - The public build downloads it only over HTTPS from Fairphone's official host and uses it only if
    size and SHA-256 match.
  - per-file hash, component and purpose.
  - fail-closed extraction.
- **Remaining:**
  - The archive and its published hash come from the same vendor web estate.
  - A new package can be selected before Fairphone publishes its hash (FP6.QREL.16.111.0 was; the
    hash Fairphone published on 2026-10-02 matches).
  - No vendor signature check.
  - per-file purpose partly generic.
  - name-loaded libraries declared for few files, with no build check (a missing one broke a
    bring-up boot).
- **Status:**
  - Observed gap: per-file necessity and name-loaded dependencies incomplete.
  - Bring-up (not qualified): extraction fail-closed and hash-bound.
  - Unverified: stock input verification (FP6-040), firmware review (FP6-206), closure gate
    (FP6-208).

#### Release signing keys

- **Threat:** key theft or misuse, or a targeted signed build, via the signer host, hardware tokens,
  signing media and maintainers.
- **Protection:**
  - None for the FP6: development images use public test keys.
  - The signing tooling is qualified only with generic dummy keys.
  - The OTA key is the signing contract's release key (`releasekey`): release signing writes its
    certificate into `otacerts.zip`, the only key the Updater, update_engine and recovery accept.
  - No FP6 release key exists.
- **Status:** Not implemented for the FP6 (FP6-035, FP6-036).

#### Repositories, domain, install page

- **Threat:** phishing, compromised workstation or assistant session, or malicious contribution, via
  code hosting, registrar, DNS, mail and developer keys.
- **Protection:**
  - Hardware MFA.
  - Separate signing and authentication keys.
  - Protected branches.
  - Human review of security-relevant changes.
  - least-privilege assistant access.
- **Remaining:** limited review capacity, no independent review.
- **Status:**
  - Recorded.
  - Unverified: account and workstation hardening, branch protection.

#### Exposure window for known bugs

- **Threat:** n-day exploitation of platform, kernel, vendor code, firmware or browser.
- **Protection:**
  - None.
  - Nothing is released.
- **Remaining:**
  - Vendor and firmware fixes depend on Fairphone and Qualcomm and lag.
  - open-source libraries bundled in closed vendor files miss platform fixes.
- **Status:**
  - Observed gap: bring-up platform and vendor patch levels lag available upstream releases.
  - No automated upstream intake.
  - The vendor patch level a build reports is the one the stock vendor image of its vendor files
    sets, read at build time and checked in every image set (since the 2026-10-05 build; Settings
    shows it as the vendor security update).
  - The firmware can be older: Settings also shows the installed Fairphone firmware release
    (implemented, not yet built).

#### Shell fork rebase lag: GrapheneOS security fixes under the Tally shell

- **Threat:**
  - n-day exploitation of a fix not yet shipped or lost while rebasing, via DiamaneOS's Tally
    changes to frameworks/base (SystemUI, WM Shell, SettingsLib and core resources) carried onto
    each GrapheneOS release.
- **Protection:**
  - per-area commit series, mostly new files wired in through SystemUI's dependency injection, so
    upstream files change little.
  - Tally code behind one build-time flag (fixed read-only, on in DiamaneOS's release config), so an
    area can be dropped or the flag turned off to take a GrapheneOS security release without waiting
    for the Tally changes to be rebased.
- **Remaining:**
  - The flag helps only while Tally code still builds on the new release, and switches a resource
    off only where the resource names it (SystemUI, WM Shell and the shared clock library read it).
  - If a later GrapheneOS release moves WM Shell's activity transitions to its new transition
    planner, Tally's page motion falls back to stock until hooked there.
  - Keyguard, privacy-indicator and biometric conflicts need careful manual merges.
  - Limited review capacity.
  - The bouncer reads Tally token resources when built, so broken tokens would break PIN and
    password entry: every build is tested by unlocking with a PIN and a password, flag on.
  - The foundation's colour fixes (SystemUI's "already applied" check, the Sodium fallback) also
    apply with the flag off.
  - They change colours only, so the flag-off build is GrapheneOS's in behaviour, not byte for byte.
- **Status:**
  - Bring-up (not qualified): the Tally code and its flag run in the 2026-09-29 and 2026-09-30 Tally
    test builds.
  - Unverified: rebase and build of each GrapheneOS release with the flag on and off (FP6-211).

#### Install-time trust

- **Threat:** impersonating site or mirror, or a tampered image set, via image downloads and the
  flashing commands.
- **Protection:**
  - `flash-steps` prints commands only for an image set whose verify report matches it and passed,
    and prints the never-lock rule first for test-key builds ([OS
    integrity](#os-integrity-on-a-released-locked-build)).
  - In the tools, in no build yet: firmware enters an image set only from the factory package with
    the pinned SHA-256, each image matching its own pinned hash ([Firmware
    security](#firmware-security)).
- **Remaining:**
  - No installer and no key enrolment exist.
  - Stock restore inputs are authenticated by the vendor's published hash, not a signature.
- **Status:** Not implemented: installers and key enrolment.

### Everyday use

#### Correct user decisions

- **Threat:** user error, confusing warnings or inaccessible flows, in setup, permissions, updates,
  backup and restore, and recovery.
- **Protection:** inherited GrapheneOS flows only.
- **Status:** Not implemented: no DiamaneOS-specific measures.

#### Typed text and personal words

- **Threat:**
  - Someone with the device before first unlock, a backup copy or a view of the screen, via the
    keyboard's learned, personal-dictionary and contact word lists, backups, and fields that ask not
    to be learned from (incognito).
- **Protection (DiamaneOS keyboard fork):**
  - Learned, personal and contact words only in credential-encrypted storage, loaded after unlock.
  - Old device-encrypted copies deleted at the first locked boot after the update
    (LOCKED_BOOT_COMPLETED), or at first dictionary setup or first unlock if those come first (an
    update never sends MY_PACKAGE_REPLACED).
  - Backups off.
  - no-learning and password fields never learned from or shown learned words.
  - No typed or personal data in logs.
  - Unused network, account and sync permissions and dormant entry points removed.
  - No network permission (already true).
- **Remaining:**
  - The recent-emoji list stays readable before first unlock.
  - A personal-dictionary word deleted while the keyboard is not running stays suggested and stored.
  - Builds without the fork (the inherited keyboard) keep these words in storage available before
    first unlock, allow them in backups and ignore the no-learning flag.
- **Status:**
  - Bring-up (not qualified): the keyboard fork is in the manifest and the test builds.
  - Verified on the FP6: planted device-encrypted word lists are deleted at the locked boot, before
    the first unlock.
  - With learning turned on, a word typed in a normal field is learned, suggested and stored only in
    credential-encrypted storage.
  - A word typed in a no-learning field is neither learned nor stored, and learned words are not
    suggested there.
  - Turning learning off deletes the learned list.
  - Password fields show no suggestions, teach nothing and, when hidden, show no key previews.
  - Personal and contact words are suggested and stored only in credential-encrypted storage.
  - Revoking contacts access deletes the contact list.
  - Unverified: the keyboard fork's tests.

#### Privacy indicators and disclosures under the Tally shell

Protects: knowing when the camera, microphone or location is in use or the screen is captured, which
app is asking, whether the device is managed or on a VPN, and what a locked phone shows.

- **Threat:**
  - An app using a sensor or capturing the screen while the user reads its interface or while
    fullscreen.
  - An app asking for a biometric or credential.
  - Someone looking at a locked phone.
  - Surfaces, all restyled by the Tally shell (FP6-211): status bar privacy chip and lamps, capture
    chips, fullscreen dots, lens ring, lock strip, indication line, Quick Settings' security footer,
    notification rows, toasts, biometric prompt.
- **Protection:**
  - *Data:*
    - Tally changes drawing, never data or presence.
    - The platform's privacy and app-op tracking is the only source, unfiltered by foreground app,
      quiet or decluttered views or the lock strip.
    - Camera, microphone, location (whenever the platform shows it) and screen sharing show in every
      state (app in front, immersive and fullscreen, lock screen, shade, Do Not Disturb, battery
      saver).
    - Stock SystemUI keeps the indicator timings (5 s and 10 s holds) and chip height and icon size
      in sp, so chips grow with text size.
    - Lamps appear with no animation delay.
    - Only disappearance may animate.
    - The dot's colour follows the area under it without ever delaying the dot.
    - Tally lock views draw empty or partial states without failing: a SystemUI crash removes every
      privacy indicator until restart.
    - Location alone runs the status bar chip on each app's first use in 10 minutes (stock's
      debounce; the flag that GrapheneOS leaves off is on), then the dot and the location lamp by
      the lens.
    - A camera or microphone use that starts while location alone shows the dot runs the chip (-151,
      a Tally regression against stock's dot colour change, fixed).
    - A sensor that joins while a chip shows updates that chip, so the dot ends with the current
      items (-152, a stock fix).
    - System location use and background use without a foreground service show nowhere, as in stock.
  - *Drawing:*
    - Only colours with a 2 dp edge, padding, radius.
    - A 16 dp dot drawn as a lamp 16 dp from top and side (status icons keep clear).
    - One shade chip for every sensor.
    - Screen capture alone in the capture colour.
    - Disappearance on the fill spring.
    - Added are the lens ring and the location lamp by the lens.
    - Tally tokens and overlays never set stock indicator resources.
    - Indicator colours are fixed SystemUI resources outside the palette, at least 3:1 on light and
      dark bars.
    - The variant is taken from the area underneath:
      - Status icons' tint over the app, lock screen or shade.
      - Dark under the bouncer, a SystemUI dialog, the dozing screen or any area not known to be
        light.
      - Never from night mode.
    - Every Tally status bar indicator draws its 2 dp edge in every state.
    - Where no edge can be drawn or no area is known, it takes the variant that keeps 3:1 alone.
    - The overlay check refuses product overlays of SystemUI privacy-indicator resources and of
      every Tally token or indicator resource in SystemUI (only a device overlay sets the lens
      config).
    - The prototype follows these rules, checked in every configuration (stock sp sizes at 100, 150
      and 200 % text; area colours with a 2 dp edge, so dims over the status bar keep 3:1).
  - *Touch:*
    - As in stock, the shade's privacy chip opens the stock privacy dialog and a capture chip its
      stop dialog.
    - The status bar chip and dot take no tap.
    - The lens ring and lamp take no touch, so never block the status bar or the app beneath.
  - *Lens ring and lamp:*
    - On active pixels at least 2 dp outside the display cutout, above apps and the lock screen.
    - The ring lights while an app uses a camera once the front camera has opened (as the camera
      service reports).
    - It counts the front camera as used for the longest privacy hold (10 s) after it closes,
      keeping the camera privacy item's hold whatever order close and item arrive in.
    - A back-camera switch ends it after that window.
    - A camera with unreadable facing counts as front.
    - Without open and close reports any camera lights it.
    - Chip and dot show every camera.
    - On the FP6 the device overlay declares the camera hole as measured on the panel (a 37 px
      circle), so the ring is a true ring 2 dp outside it and the lamp sits level with it.
    - The layout rectangle keeps status bar and app insets at 110 px and spans the hole's full
      width, so no status bar content sits under it.
  - *Lock strip:*
    - While locked, only "Camera in use" or "Microphone in use" (stock shows the untyped dot; naming
      the sensor is deliberate).
    - Sensor items from the privacy chip's own source (holds included), others from SystemUI's
      existing controllers.
    - No touch.
    - No app, network or device names.
    - An alarm only if still to come and within 12 hours (GrapheneOS's condition; stock's 12 hours),
      and only while the current user's lock screen shows notifications, which is off unless set
      (GrapheneOS's smartspace line then shows nothing either).
    - Bluetooth only while a device is connected, as stock's status bar icon.
    - Battery percentage only while charging or with the user's setting on.
    - Hidden on the always-on display.
  - *Always-on strip:*
    - Only while dozing, what stock's always-on date line showed: next alarm within 12 hours, a Do
      Not Disturb icon, the playing media's title (artist only beside the media notification's
      icon).
    - Media only while the current user's lock screen shows notifications and "Show media on lock
      screen" is on, with GrapheneOS's defaults, as GrapheneOS's always-on line.
    - Never lamps, sensors, network or device names, app icons or battery.
    - Nothing while lock-screen notifications are off.
  - *Clock and indication line:*
    - The Tally lock clock and the date line under other clocks show only time and date.
    - That date line only while the lock screen shows notifications (as GrapheneOS's smartspace
      line).
    - The indication line keeps stock's messages (strong-authentication, lockdown, administrator,
      trust-agent), shows fingerprint failure and help in the error colour, and adds a 3-second
      side-sensor hint in stock's words while fingerprint unlock is allowed, on at most five wakes
      per user.
    - The count (0 to 5) lives only in SystemUI's device-protected storage, not backed up, removed
      with the user.
    - Read at screen-on (also before first unlock), written only when the hint shows.
    - It never affects unlocking, and if unreadable the hint stays away.
  - *Footer, notifications, prompts, toasts:*
    - The security footer (managed device, VPN, monitoring certificate) and VPN icon show whenever
      stock shows them.
    - lock-screen notifications follow the user's lock-screen settings and work-profile redaction.
    - Notification rows keep the app's label and icon, the biometric prompt its name and icon.
    - Toasts change only position and shape, keeping admission rules, app attribution and
      GrapheneOS's secure-paste notices, with no new kind of toast on the secure lock screen.
- **Remaining:**
  - A new layout could cover or clip an indicator without touching its data (only review and phone
    tests catch it).
  - A preinstalled overlay outside the checked roots could change stock resources.
  - SystemUI cannot see a scrim an app draws inside its own window (as opposed to a dimming window),
    over whose mid-tone a light-variant indicator can fall to about 2.3:1 with its white edge.
  - The status bar and capture chips sit in the status bar window, under the shade, lock screen and
    SystemUI dialogs, while the dot, lens ring and lamp stay above everything.
- **Status:**
  - Bring-up (not qualified): the SystemUI indicators and lock screen run in the 2026-09-29 and
    2026-09-30 Tally test builds, after security reviews and prototype privacy checks at 100, 150
    and 200 % text.
  - Phone tests of those builds found and fixed findings listed in the
    [history](THREAT_MODEL-HISTORY.md).
  - Unverified: a phone test of every listed state (FP6-211).

#### Files archive handling (browse, extract, create)

- **Threat:**
  - A malicious archive the user opens or extracts, or an app holding MANAGE_DOCUMENTS, via
    ArchivesProvider (exported, MANAGE_DOCUMENTS) parsing untrusted ZIP, 7z and TAR with AOSP
    commons-compress 1.19, and DocumentsUI extraction (UnpackJob).
- **Protection:**
  - Entry paths rooted, `.` and `..` collapsed (Archive.getEntryPath).
  - Extraction through the storage framework, which re-roots and sanitises each name.
  - Symlink and hardlink entries written as ordinary files, never followed.
  - Free space checked against declared sizes, each file capped at its declared size, partial files
    removed.
  - Parser exceptions end in a failed load, never a crash.
  - The new archive code (zip_ng) adds read-path size and CRC checks, so it is on (-142).
- **Remaining:**
  - commons-compress 1.19 is old (a debug log line on a ZIP 0x0017 extra field, -143; a malformed
    TAR header throws a caught NullPointerException).
  - Browsing parses archives with or without zip_ng.
  - A large honest archive that fits the free space still takes time to extract (cancellable).
  - No recursive extraction.
- **Status:**
  - Bring-up (not qualified): host review and fuzzing with the pinned library, 2026-09-30 (20
    million path inputs, about 154 million mutated archives: no crash, hang or escape).
  - zip_ng on in vendor_diamaneos android17 (770d976), in two 2026-09-30 test builds.
  - Files trash is off (-140).
  - Unverified: phone extraction and picker tests.

## FP6 source and firmware boundary

- **Sources:**
  - Kernel and platform HAL sources follow Qualcomm's CodeLinaro release
    `LA.VENDOR.14.3.0.r1-23400-lanai.QSSI16.0`, with the GrapheneOS `kernel_common-6.1` release
    merged into the vendor kernel.
  - FP6 device trees, the Samsung NFC sources and stock images come from Fairphone.
  - Source availability does not prove that GrapheneOS or Pixel patches apply, that modules meet the
    selected KMI/UAPI, or that binaries reproduce stock.
- **Closed components:**
  - The camera, radio and IMS, secure-world, sensor, DRM and much of the graphics and media runtime
    depend on proprietary userspace or firmware.
  - The bring-up selection holds 671 closed stock files (about 336 MB): camera 211, radio and IMS
    154, sensors 84, display and GPU 74, audio 17, credentials 36, video encoding 31,
    remote-processor services 24, thermal 7, plus smaller Bluetooth, NFC, GNSS configuration and
    fingerprint sets.
- **Stock input:**
  - `FP6.QREL.16.111.0` for the EU (`FP6.QREL.16.100.0` until 2026-09-30).
  - Its verified factory package is the authoritative extraction input.
  - Vendor generation uses an explicit per-file recipe (partition, path, hash, component, purpose).
  - Purposes are partly generic and consumer bindings partial.
  - The generator excludes per-device identity, modem NV/EFS, calibration, provisioning, DRM,
    attestation, keystore and userdata material.
  - `FP6.QREL.16.104.0` (US region) is a comparison and validation input, not an EU restore input.
  - US-region devices are unverified.
- **Removals and replacements:**
  - A removal must remove the complete reachable service and declaration path and pass subsystem
    tests.
  - An open-source replacement needs exact licence compliance and must preserve security and
    capability (a software fallback is not automatically safer than proprietary hardware-backed
    code).
- **Firmware:**
  - Hashed per image in
    [`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json).
  - Image sets carry those exact images of the vendor files' release, which `flash-steps` installs
    ([FIRMWARE.md](FIRMWARE.md)).
  - OTAs carry none.
- **Licences and records:**
  - Imported or modified open-source code stays fail-closed on per-file licence, notice, attribution
    and corresponding-source obligations.
  - Exact source revisions, interfaces and experiments are in
    [`config/fp6-sources.json`](../config/fp6-sources.json) and
    [`config/fp6-capabilities.json`](../config/fp6-capabilities.json).
- **Vendor compatibility:**
  - The stock vendor declares VINTF target level 8 with vendor API 34.
  - bring-up builds combine it with the Android 17 framework.
  - Product assembly must pass `checkvintf` and applicable VTS checks without weakened enforcement
    or broad compatibility shims.

## Fresh-UI boundary

- Shared visual tokens and components carry no platform authority; credential, update, backup,
  network and content-processing authorities stay separate.
- Each nontrivial Settings, launcher or SystemUI change needs demonstrated benefit, an owner,
  measured rebase cost and regression checks.
- Framework authorities are not replaced; no shared visual controller gains platform authority.
- The system font is Sofia Sans Tally (Sofia Sans, OFL 1.1, scaled to Roboto's metrics), built
  reproducibly from the pinned upstream file.
- Every app parses it, so it passes OTS sanitising and fontTools decompilation before it ships; it
  is read-only on the verified product partition.
- Apps can see it (a small OS signal among many); WebView keeps the platform fonts, so websites see
  no difference from GrapheneOS.

- Every Tally shell commit (FP6-211) also keeps the indicator and disclosure rules under [Privacy
  indicators](#privacy-indicators-and-disclosures-under-the-tally-shell), the rebase rule under
  [Shell fork rebase lag](#shell-fork-rebase-lag-grapheneos-security-fixes-under-the-tally-shell),
  and the per-component rules in [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md):
  - Lock screen and biometric prompt, transitions, Quick Settings and the shade, Recents' Stop,
    Settings switches, Home and All apps, Settings search, Clock, Calculator, recovery titles,
    motion, the Settings homepage, branding, the Moments switch, and rules learned from findings.

## Decision record

Decisions that define current behaviour:

- **2G (2026-09-25):**
  - 2G stays allowed by default (AOSP/GrapheneOS default).
  - "2G network protection" and LTE-only are offered as opt-in hardening.
- **eSIM (2026-09-27, 2026-10-04, 2026-10-06):**
  - OpenEUICC is not used (GrapheneOS os-issue-tracker #6275 and #2631).
  - The stock LPA cannot list profiles on the FP6, so it is not shipped.
  - DiamaneOS's own LPA sits behind GrapheneOS's eSIM support switch, off by default.
  - Installed profiles keep working either way.
  - It sends notifications only for profiles it downloaded, and trusts only GSMA CI roots for its
    eSIM servers.
- **Build identity (2026-09-26):**
  - Build properties and fingerprint keep Fairphone's stock product identity, as GrapheneOS keeps
    Google's, after Google blocked the DiamaneOS-branded identity as an uncertified device.
  - DiamaneOS stays the name people see.
- **Enforcing policy of the 2026-09-26 build:**
  - The audio HAL may use QRTR sockets for DSP restart notifications (SELinux cannot limit which QMI
    service it reaches).
  - nicmd may bind the ephemeral ports the modem reserves.
  - The modem's study partition keeps its stock label.
  - SystemUI cannot read the screen-decorations switch, so the camera/microphone privacy dot cannot
    be turned off over adb.
  - The camera HAL's display-configuration hint stays denied.
- **userfaultfd and KPROBES:**
  - 2026-09-26: the production kernel keeps CONFIG_USERFAULTFD for ART's garbage collector, as GKI,
    Pixel and GrapheneOS do, with the user-mode-only restriction.
  - 2026-09-27: KPROBES stays on, since the USB controller glue implements its controller hooks with
    kretprobes.
- **Lockdown level (2026-10-08):**
  - Confidentiality, as GrapheneOS ships it without exceptions.
  - A GrapheneOS Pixel shows the same costs (empty tracefs, no lmkd memory events, no BPF per-app
    CPU times).
  - Replaces the 2026-10-05 choice of integrity, which let privileged processes read kernel memory.
- **debugfs (2026-09-27):**
  - Stays as in the 2026-09-26 build, built in and mountable: user builds never mount it, debuggable
    builds until boot completes, SELinux governs access.
  - In this kernel, turning mounts off also removes the in-kernel interface the display driver and
    recovery need.
  - 2026-10-01: both kernel forks fix that mode, so kernel code keeps the interface while debugfs
    cannot be mounted at all, not even by root.
  - The kernel policy requires it (in builds since the 2026-10-01 build; checked on the phone: not
    mountable).
- **pstore/ramoops (2026-09-27):**
  - On for bring-up builds with GrapheneOS's Pixel layout.
  - Reboots stay cold (the kernel default).
  - A warm reboot would keep the saved logs but also all of RAM.
  - Details under [Previous boot's kernel log and event
    logs](#previous-boots-kernel-log-and-event-logs).
- **IMS:**
  - 2026-09-25: the closed Qualcomm IMS stack runs under an explicit permission allowlist.
  - 2026-09-26: the IMS data connection is brought up by DiamaneOS's own code (a small modem-facing
    service and a one-permission app) instead of Qualcomm's connectivity engine.
- **Call audio (2026-09-26):**
  - Since the 2026-09-26 build, DiamaneOS's own call-audio bridge, with no audio-routing permission,
    replaces the call-audio messenger.
  - The audio-routing grant is gone.
- **Official builds (2026-10-06):**
  - GrapheneOS's `OFFICIAL_BUILD` stays refused.
  - DiamaneOS's own `DIAMANEOS_OFFICIAL_BUILD`, set only by DiamaneOS's builder, adds the Updater
    fork.
  - Other builds have no Updater.
  - The fork offers no security preview channel, since DiamaneOS publishes no security preview
    releases.
  - In the official builds since 2026-10-06.

## Architecture and verification review

- Paths reviewed: remote (network observer, DNS and captive portal).
- Remote media (remote media, software decoders, the hardware encoder service and video device
  nodes):
  - Media is decoded only by the software codecs.
  - The encoder service lists and creates encoders only with no camera, decoder, DSP or
    display-configuration access.
  - The stock codec library's Android 14 GraphicBuffer is replaced by a fail-closed Android 14 sized
    one so the surface path cannot overflow the service heap.
- Malicious app:
  - Permission and background listener.
  - Camera permission, camera service and closed camera provider, with grants bound to its domain,
    reduced groups and no network under enforcing SELinux.
  - Flashlight controls reach the closed provider without the camera permission, with simple on/off
    and strength values only, as AOSP designs it.
- Cellular (fake base station, 2G fallback, the user's hardening choice, the modem; a build before
  2026-09-26 did not pass the choice to the modem, current source does, not yet re-checked on the
  phone).
- cross-profile and physical (secondary user or profile, user-switch challenge (FP6-046, FP6-071);
  AFU and BFU, USB, EDL and lock screen, duress).
- Device-dependent claims stay unverified until checked.

## How this document is maintained

- This document shows only the current state. Every feature that is built updates it in the same
  change.
- Every security or privacy finding updates this document and the private findings register together
  with the work that found it (different repositories, so not one commit).
- Every security or privacy change updates this file and
  [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md) (revision history, IMS integration notes) in
  the same commit, and [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md) when a Tally shell rule
  changes.
- A finding is described here once fixed, or once safe to state without giving an attacker a working
  path; until then the affected asset names the gap in general terms.
- No device identifiers or unfixed exploit detail.
- An evidence state changes only with new evidence; wording alone never turns an assumption into a
  claim.
- Each revision is dated at the top.

## Terms

- **AFU / BFU:**
  - After / before the first unlock since boot.
  - Before it, user data stays encrypted.
- **AVB:**
  - Android Verified Boot.
  - A custom AVB root is a project key enrolled instead of the maker's.
  - Such builds report **yellow**, maker-key builds **green**.
- **CE / DE storage:** readable only after unlock / from boot.
- **TEE:**
  - Qualcomm's TrustZone secure world.
  - **KeyMint** keeps keys there, **Gatekeeper** checks the lock credential and throttles guesses,
    **RKP** provisions attestation keys.
  - **StrongBox** and **Weaver** are secure-chip equivalents.
- **MTE:** CPU memory tagging that catches many memory-safety bugs.
- **HAL:** the vendor service between Android and a hardware driver.
- **QCRIL:**
  - Qualcomm's radio daemon.
  - **QMI/QRTR:** its messages and transport to the modem and DSPs.
- **IMS:**
  - Carrier voice (VoLTE), SMS and Wi-Fi calling over IP.
  - **IWLAN:** IMS over Wi-Fi.
  - **DCM** broker and daemon: the source-built service that brings up IMS data connections
    (Android-side app, vendor-side service).
- **eUICC / LPA:** the eSIM chip / the app that manages its profiles.
- **ISD-R:** the eUICC's management applet, which the LPA's card commands address.
- **SUPL, PSDS, XTRA:** GNSS assistance from a network server, predicted satellite data, Qualcomm's
  assistance service.
- **EDL:** Qualcomm's low-level flashing mode, needing a signed programmer.
- **pstore/ramoops:** kernel logs kept in RAM across a reboot.
- **SELinux:**
  - **enforcing** blocks what policy denies, **permissive** only logs it.
  - A **domain** is a process's label.
- **KMI / VINTF:** kernel module interface / framework-vendor compatibility level.
- **Gunyah / pKVM:** Qualcomm's hypervisor / Android's protected-VM hypervisor.
- **userdebug / user:** debuggable / production build.
- **n-day:** an attack on a publicly fixed bug.
- **Tally:**
  - DiamaneOS's interface.
  - The **Tally flag** turns its code on at build time.
- **Bring-up:**
  - Early work to make the hardware function.
  - bring-up and test builds are private development images, named here by date.
