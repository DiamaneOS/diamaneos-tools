# DiamaneOS Threat Model: History

Every revision of [THREAT_MODEL.md](THREAT_MODEL.md), and historical notes. Every security
or privacy change updates both files in the same commit. Entries
before 2026-09-30 call each asset section a "row". Development builds are named
by date: the 2026-09-26 build is the first SELinux-enforcing one, the 2026-09-27
build the next.

## Revisions

One line per change, newest first. Add new revisions at the top; THREAT_MODEL.md only says when it was last revised.

### 2026-10-09

- Builds by hand: `build package` after a plain `m` records `out/` as the Android build after checking the source and the vendor files; the compile is not isolated or watched, so `build.json` marks the set `android_build: manual` and not reproducible. `build all` still builds Android itself.
- Builds that are not official accept local changes in source projects: the build warns, `build.json` lists them (`modified`) and marks the set not reproducible; official builds still need clean projects, and every build still refuses a pinned project at another commit, a changed manifest checkout, local manifests and undeclared files.

### 2026-10-08

- Geocoder: GrapheneOS's geocoding server is no longer offered; Settings offers OpenStreetMap's public service (opt-in) or Off, the default. A stored GrapheneOS choice reads back as Off, and the setup wizard's geocoder switch turns on OpenStreetMap; OpenStreetMap requests name DiamaneOS (NetworkLocation fork). Implemented, not yet built.
- eSIM: a download worked on the phone and the eUICC reports SGP.22 2.2.2. Scanning without the clipboard, implemented, not yet built: "Scan QR code" opens the camera app's QR scanner, whose "Open with" hands an LPA: code to Add eSIM (from apps on the phone, not web pages); the user still agrees before anything connects, and the Add eSIM windows hide other apps' overlays. The eSIM manager still has no camera permission.
- Fixes from a code review, implemented, not yet built: the fingerprint HAL frees a failed death link's cookie once, keeps closed sessions away from the module, never removes every enrolment for ID 0 and rejects an overflowing parcel size; the modem's file service keeps a file when a write request is rejected; the IMEI tool accepts the IMEI service only from the modem's node; the Updater recognises public test keys by their key, not their certificate; Recents' Stop refuses taps while another app's window covers part of the screen; the eSIM manager warns before a switch deletes the current profile.
- Vendor kernel fixes from a code review, implemented, not yet built: an audio DSP restart can no longer free the SLIMbus TX buffers under a message in flight (use after free); the SPI controller completes full-duplex DMA transfers whose halves finish in separate interrupts; the WCD9378 codec probe survives a failed debug allocation.
- Build records name the tools that built the images and those that packaged them; a set counts as reproducible only when both are clean and the same.
- Camera seccomp gate: image verification refuses an official build whose camera provider filter only logs; development builds keep logging until the camera profile is collected. Implemented, not yet built.
- Kernel lockdown at confidentiality level again, as GrapheneOS ships it (owner decision), implemented, not yet built: user space can neither modify nor read the running kernel; tracefs is empty and kprobes from user space and BPF kernel-memory reads are refused, so per-app CPU time and lmkd's memory-event listener do not start, as on GrapheneOS's Pixels; IPsec keys in XFRM dumps are redacted by lockdown and by both kernels' own redaction.
- Statuses: built and phone-checked: the Moments switch's camera block (apps lose the camera; recording stops, its sound after the switch is silent), its restart notice and Restart action, the kernel camera and microphone floor, the keyboard's learned, personal and contact words only in credential-encrypted storage, incognito and password fields, and Canvas's Copy and delete without a clipboard preview.

### 2026-10-07

- Radio isolation reviewed: own reserved memory per radio core and SMMU on the shared data paths (device tree and the running phone); the cores' own access limits are in closed firmware and cannot be checked from Android.
- IPsec keys redacted in XFRM state dumps again, as on GrapheneOS, implemented, not yet built.
- eUICC card ID redacted in the eSIM card error logs, implemented, not yet built.
- Fairphone's own AVB keys in 16.111.0 are not AOSP's public test keys.
- Sandboxed Google Play's crash notice reports to the DiamaneOS issue tracker, not GrapheneOS's: GmsCompat fork; implemented, not yet built.
- Firmware release in Settings, implemented, not yet built: Settings names the Fairphone firmware release the booted slot runs, or shows mixed or unknown, from hashes of its 22 A/B firmware partitions against the inventory, so the vendor patch level no longer stands for the firmware; the service that hashes them has no capabilities and may only read those partitions, which get their own SELinux type, and set one property that only Settings reads, not shell.
- Camera blocking on the FP6, implemented, not yet built: the camera HAL has no working mute, so with camera access off or the Moments switch blocking the camera, apps are disconnected and new opens fail as camera disabled, AOSP's path for such cameras, instead of black frames; Canvas's Copy and delete shows no clipboard preview, only for the system Canvas with its marker.
- Moments switch kernel floor for the cameras, implemented, not yet built: the camera driver holds every known image sensor on its test pattern while the switch is down, and the laser range finder does not range; the image processor firmware stays outside it (-192); see [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- Moments switch: the note for a blocked camera or microphone can be turned off for a silent block; implemented, not yet built.
- Moments switch: the page and one notification per boot say when only Android blocks the microphone until a restart, read from the kernel's own state; implemented, not yet built.
- Moments switch: no access prompt while the switch blocks the camera or microphone, a short toast instead, so recordings continue with black frames and silence; the user's own blocks keep the prompt; implemented, not yet built.
- Moments switch kernel floor, implemented, not yet built: with "Camera and microphone off" chosen, the kernel mutes the built-in microphones at the codec and keeps their bias off while the switch is down, whatever Android asks; the choice is sealed in the kernel once per boot; Android shows it as a hardware microphone toggle; emergency calls have no microphone while the switch is down; headset microphones and the cameras stay software-blocked; see [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- Screenshots, implemented, not yet built: the screenshot preview's Delete removes only the screenshot SystemUI just saved; Canvas, now a fork, offers Done (Save, Copy and delete, Delete) for screenshots from SystemUI's Edit, deletes only through the link it was granted, and puts copies on the clipboard as one private file with a new name each time; no new permissions; see [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#screenshots-delete-and-canvass-done).
- Moments switch: airplane mode and Lockdown are separate actions, Lockdown enabled only with a screen lock; implemented, not yet built.
- Moments switch fixes from a phone test, implemented, not yet built: camera and microphone access toggles turned on for the FP6 as on Pixels; the switch's toast names only what took effect; Lockdown offered only with a screen lock; the input reader no longer loses a switch change after a buffer overrun.

### 2026-10-06

- eSIM downloads, implemented, not yet built: the eSIM manager downloads profiles (SGP.22 consumer download, with confirmation code and an explicit SM-DS search) after the user agrees to contact the named server; HTTPS to that server only, trusting only the GSMA CI roots it ships that the eUICC also lists; notifications only for profiles it downloaded; the framework's eUICC transport no longer logs eUICC commands in full (-181).
- GNSS, implemented, not yet built: the HAL, its IGnss implementation and the location libraries are built from CodeLinaro source instead of Qualcomm's closed builds, with the public QMI framework's client linked in, so the GNSS process loads no closed code, and with CFI and the integer overflow sanitizer as in Qualcomm's builds; the libraries load only the GNSS adapter and the QMI LOC API, so Qualcomm's IZat, XTRA, NTRIP and other add-on libraries cannot load, and the HAL opens no socket for the XTRA and DGNSS daemons; SUPL and PSDS settings work as before.
- Wi-Fi MAC in logs, implemented, not yet built: on user builds netd logs no info lines and the supplicant no debug lines, which carried the factory MAC; the Wi-Fi service no longer prints it when it records it or fails to set it, and the Wi-Fi driver's error line at Wi-Fi start prints a peer id instead of four of its six octets.
- USB-C port control, implemented, not yet built: the framework setting is on, and init triggers turn USB data off and on with the USB controller's data switch, which only vendor_init may write; user builds default to charging-only when locked, as GrapheneOS; Off also turns off charging while the OS runs, as on Pixels, through the charger firmware's input suspend, which only vendor_init may write; user builds start every boot with USB data and charging off until the stored mode applies, as Pixels do; charging powered off, in charger mode, fastboot, fastbootd and recovery is unaffected; unlike Pixels, Off leaves Type-C and USB PD running in Qualcomm firmware and the boot cut starts once that firmware is up; debuggable builds reset a restrictive mode at boot and ignore the lock modes while USB debugging is on.
- Firmware delivery, implemented, not yet built or tested on a phone: image sets carry Fairphone's exact firmware of the vendor files' release, checked against the per-image hashes when packaged and verified; flash-steps writes it first, in the stock order and on both slots, only over older firmware and never over newer, taking the phone's release from the user or the earlier image set because the FP6 bootloader does not report it; no OTA carries firmware, and verify refuses an A/B partition list with only part of it.
- Moments switch, implemented, not yet built: the side slider's state reaches only SystemUI through a signature permission and only from the internal device; its settings are readable only by Settings, SystemUI and Launcher; it raises protection at once and lowers it only after the unlock; see [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- Statuses: items built and phone-checked today are marked so.
- Audio HAL, PAL and AGM, implemented, not yet built: built from Fairphone's published FP6 sources with control-flow integrity and the integer overflow sanitizer, as stock, so the HAL's setParameters keys, stream I/O and USB audio descriptor text are handled in code we can patch; the HAL registers no PAL HIDL service, and that service, the memory logger and the dynamic logging library are not shipped; the HAL no longer answers the factory speaker queries or accepts the factory speaker protection modes, and ignores voice UI keys without a sound trigger HAL, which closes parameter-query bugs any app could reach (-178); the graph services, their tuning server, the voice UI interface and the deadline manager stay closed in the same process; the Awinic calibration code Fairphone publishes without a licence is compiled into PAL as in stock; PAL's speaker protection calls it at each speaker start to apply the per-unit calibration.
- Kernel hardening, implemented, not yet built: /proc/cmdline, which bug reports copy, shows parameter names only, keeping only module options' values for Android's modprobe, so the hardware identifier the bootloader passes no longer reaches bug reports; the running system can no longer reboot into Qualcomm's emergency download or RAM dump modes or turn dumps on; the EUD debugger's device-tree node is off and its driver is no longer shipped, and USB takes its connect and role events from the Type-C port controller only.
- Wi-Fi MAC, implemented, not yet built: the Wi-Fi hardware address is the factory MAC from the traceability partition, as on stock and Pixels, not the chip's generic Qualcomm one; Android still randomises it per network.
- Camera serials, implemented, not yet built: user builds silence the camera HAL's log tag.
- Bluetooth seccomp enforced, implemented, not yet built: a call outside the HCI service's list stops the service instead of only being logged.
- Hardware encoder surface output phone-checked: it fails closed, no heap overflow.
- Hardware encoders phone-checked: every camera quality encodes on them, every decoder stays software.
- Bluetooth address, implemented, not yet built: the factory address from the traceability partition, not a generated one with a fixed prefix.
- Third hardening batch, implemented, not yet built: SHA-256 dm-verity hash trees, as on stock; the boot control HAL as its own user with CAP_SYS_RAWIO only; only ueventd and vendor_init may write the switch that turns the EUD debugger on, which stays off; no service context for the removed ANT and SAR interfaces; the published hash of the selected stock package checked.
- Wi-Fi, implemented, not yet built: no wake-on-LAN; the driver configuration turns the magic-packet wake-up off, which stock leaves on, and the supplicant asks for none.
- Bluetooth, implemented, not yet built: the HCI service runs Qualcomm's closed HCI implementation under a seccomp filter installed before it loads: threads but no child processes, Unix sockets only, no memory both writable and executable, kill only as SIGKILL; for now calls outside the list are logged, not blocked.
- eSIM, implemented, not yet built: DiamaneOS's own eSIM manager, off until the user turns on eSIM support, lists, turns on and off, renames and deletes the profiles on the eUICC and erases them on request, through Android's own eUICC card commands; no network access and no downloads; whether the radio daemon opens a channel to the eUICC's management applet is untested; slot 1 is marked as the built-in eUICC.
- Audio effects, implemented, not yet built: apps' equalizer, bass boost, virtualizer, reverb and visualizer run only in AOSP's software effects; Qualcomm's closed offload effect bundle and visualizer, which received every app's effect parameters, are no longer shipped (-175); its VoIP echo-cancellation and noise-suppression descriptors and its volume listener are built from source.
- Privacy hardening, implemented, not yet built: the SoC serial number is root-only with its own SELinux type that no vendor service may read, and a build check lists every domain that may; the thermal HAL runs as system without capabilities; nicmd, which installs the Wi-Fi calling IPsec states for the modem, dumps every IPsec state to remove its own, so it can read the keys of all of them; the UFS storage serial numbers are root-only; the stock Bluetooth HCI implementation no longer logs the Bluetooth address; the unused secure-processor and display colour service grants are gone; neverallows guard the fingerprint HAL's data label; the kernel policy also requires kcore, KGDB, kexec and hibernation off; statements on eSIM, OEM unlocking, the 2G controls, the radio log, debugfs and permissive mode brought up to date.
- USB-C: the built kernel has the deny-new-USB hook, data can go off while charging continues, no external accessory contacts, and a non-debuggable build would have no USB data until port control is wired up.
- Official builds: DiamaneOS's own build flag adds the Updater fork, which asks DiamaneOS's update server; that server is not live, so every check fails and later checks back off, and a build signed with public test keys never downloads or installs an update; implemented, not yet built.
- An idle night's network capture: no vendor connection to any third party; integrity lockdown and the camera boosts observed on the phone.

### 2026-10-05

- Camera performance hints: the stock camera's open, close and snapshot hints reach the power HAL as boosts of at most 5 s, ended early when the camera releases them; for this the camera provider is a power HAL client, which SELinux cannot limit to those boosts, so code running in the provider can make any power HAL request: CPU and GPU frequency limits, the scheduler boost, tap-to-wake and uclamp on hint-session threads, with no access to data; the camera streaming modes no longer cap the little cores; implemented, not yet built.
- Kernel lockdown at integrity level instead of confidentiality, owner decision, so Android's per-app CPU time and lmkd's memory-event listener work; kprobes from user space and BPF kernel-memory reads are then limited by SELinux instead: on user builds only init may write kprobe_events and only bpfloader may load BPF programs, on userdebug builds the adb root shell and the tracing tools can place kprobes; IPsec keys are no longer blanked in XFRM state dumps; implemented, not yet built.
- Source and build outputs: a build can be reproduced from the resolved manifest its image set records; its manifest commit must be in the manifest branch's history and the file may choose only project commits; a shallow sync fetches the largest prebuilt projects itself; implemented, not yet used for a build.
- Log Viewer reports go to the DiamaneOS issue tracker; built and checked on the phone: the power and power stats HALs, the camera seccomp loader, the OpenCL removal; the IMEI tool writes the IMEIs at boot; Bluetooth uses a random persistent address; hardware video: the stock Qualcomm Codec2 service is enabled for the hardware encoders only, decoding stays in the sandboxed software codecs; the stock codec library was built against Android 14, where GraphicBuffer is 256 bytes, so rather than overflow the service heap on Android 17 its GraphicBuffer is replaced by a fail-closed Android 14 sized one, and a build check proves the 256-byte allocations bind the 256-byte object, finding -115; built: camera recording uses the hardware encoder.
- A power stats HAL of our own reports SoC and remote-processor sleep time from the qcom_stats driver: own user, no capabilities, the only process that may open the driver's node, and only its seven read commands; cellular: 2G protection and LTE-only checked on the phone; camera: the stock camera provider runs under a seccomp filter set up before its main(), so before CamX loads: threads but no child processes, Unix and QRTR sockets only, no writable and executable memory; for now calls outside the list are logged, not blocked; implemented, not yet built.
- Camera: the Adreno OpenCL runtime and compiler, about 34 MB selected only for the camera, and the software chromatic-aberration library that links the runtime are no longer shipped; implemented, not yet built.
- Public-text pass: statuses brought up to date: Bluetooth pairing, music and a headset call work with our own HCI service; calls with audio work through the call-audio bridge, which also holds the call-audio control permission; compressed music is decoded in software; the -153 fix, the debugfs mount refusal, the location logging changes, the vendor patch level and the public build commands are in builds; the IMS evidence states what exists instead of release gates.
- Qualcomm's closed perf2 daemon, which ran as root with a broad policy, and its client libraries are no longer shipped: LineageOS's open libperfmgr power HAL replaces it and the CodeLinaro power HAL, running as system with CAP_SYS_NICE only in a narrow domain that may write only the CPU and GPU frequency limits it is handed; a no-op perf client ends the camera's perf2 lookups; implemented, not yet built.
- Cellular hardening, source review: the 2G and LTE-only settings reach the modem's network preference, not yet re-checked on the phone; the radio software answers "not supported" to the null-cipher control and predates cellular security notifications, so Settings hides both; Settings offers Android's network-type list instead of the stock list of CDMA world modes, implemented, not yet built.
- The modem's TFTP file server and the protection-domain mapper are the open-source linux-msm tqftpserv and pd-mapper instead of Qualcomm's closed daemons, which started as root: own users, no capabilities, narrow SELinux domains; the file server is our fork with upstream's pending memory and path fixes, deletes only files in its own directory and follows no symbolic links there; its read-write data moves from persist to /data, so a factory reset clears it; Qualcomm's QRTR library stays for its stock users; implemented, not yet built.
- Unused closed vendor entry points: the camera provider no longer declares or registers the offline camera service, the display colour service is no longer installed, our own Bluetooth HCI service replaces the stock one, so its eight unregistered FM, ANT, SAR, config-store and TPI libraries are gone, and the dynamic-sensor (HID) sub-HAL is no longer shipped or loaded; built and checked on the phone: camera, Bluetooth on/off and sensors work, Bluetooth pairing not yet tested.
- The vendor patch level is read from the stock vendor image and checked in every image set; built and shown in Settings.
- -170: Wi-Fi joined networks with the hardware MAC address because no Wi-Fi capability overlay was installed; overlay and image check added, randomised address seen on the phone; QRTR: local senders kept off the IMS DCM port, per-client lookup cap, remote DCM records refused, QRTR tunnel device kept out of the kernel; DCM daemon error handling and publication on lookup timeout; in-call TTY, hearing-aid, voice-quality and Bluetooth voice keys limited to their platform owners; entitlement applies every provisioning setting and caps Retry-After; built; IMS calls over cellular and Wi-Fi passed on the phone.

### 2026-10-04

- Source and build outputs: builds follow the DiamaneOS manifest branch and record the resolved manifest, the maintainer verifies GrapheneOS's signed tag when merging a release, and the kernel comes from the published prebuilts.

### 2026-10-03

- The Colour icon style: keys only for listed system apps, the style setting; the Paper wallpaper service.

### 2026-10-01

- -153: a vendor-private colour sensor readable without the Sensors permission; fix implemented, not yet built.
- SELinux triage of the remaining denials, implemented, not yet built.
- Kernel, implemented, not yet built: debugfs no longer mountable, -114; the Wi-Fi platform driver logs no MAC address, -112.
- Location logging, -95: warning-level GNSS engine logs and the serving cell redacted in the radio log, implemented, not yet built.
- Decision and test notes stated as plain facts, review notes shortened; no change in substance.
- Location's own privacy chip; -151 and -152 fixed: a camera or microphone start during a location-only dot and a sensor joining during a chip now show; the system font.
- Trimmed to the current state.
- Contradictions resolved with current facts; statuses updated from the 2026-09-26 and 2026-09-27 phone tests.
- Shortened; revision history and IMS integration notes moved to [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md), Tally shell rules to [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md); no change in substance.

### 2026-09-30

- Public build commands: what they enforce and their limits.
- Rewritten for readability; no change in substance.
- The stock input moved to FP6.QREL.16.111.0, selected before Fairphone published its checksum; firmware images hashed per image; the Clock, Calculator, recovery title, Files archive and keyboard privacy forks, with their security review; -140 to -150; Home's live tallies.

### 2026-09-29

- Recents' Stop in Launcher3, the Settings homepage, Home and All apps, Settings search, the switches and rows, and the motion in WM Shell and Launcher3, with their security reviews.

### 2026-09-28

- The Tally shell: the privacy-indicator row widened to privacy indicators and disclosures, a shell fork rebase lag row and the shell's commit rules in the Fresh-UI boundary; the prototype's privacy chip at stock's sizes; the native privacy indicators: which of them take a tap, the location lamp by the lens and the limits of the area rule; the lock screen, the SettingsLib switches and the system label; the shade and Quick Settings; Recents' Stop in SystemUI; the privacy indicator fixes and an indicator bug fixed before any build; the lock screen fixes: the bouncer's visual-only code, the always-on strip, the per-user hint count, the Tally clock in the shared clock library; the volume panel, power menu and toasts; the privacy indicators' security review; the Tally switch views; the shade's animated lamps and heads-up; the security review of Recents' Stop; the lock screen reviews.

### 2026-09-27

- The 2026-09-27 build: the production kernel configuration and module deny list, KPROBES and debugfs kept as in the 2026-09-26 build, boot parameters logged by name only, the previous boot's logs kept by pstore/ramoops, the stock LPA's eSIM service off again, the touch controller firmware, what confidentiality lockdown costs at runtime, the saved console's log level, pstore phone results with cold reboots kept; the Tally privacy-indicator row; remote key provisioning failing in the TEE on the 2026-09-27 build.

### 2026-09-26

- Endpoint additions from the branding inventory, the keyboard privacy gap, and the bring-up and 2026-09-26 build updates; the full refresh of 2026-09-25 replaced the initial model of 2026-09-14).

## Change notes

Longer notes for some revisions, oldest first.

- **2026-09-14:** initial threat model.
- **2026-09-25:** full refresh. Added development-state, cellular, SMS,
  location, kernel, closed vendor userspace, identifier, TEE, firmware,
  supply-chain and signing rows; added attestation and kernel limits; corrected
  the USB, pKVM, fingerprint, secure-hardware, egress and kernel-source
  statements; applied a weakest-state rule to every evidence state.
- **2026-09-26:** from the GrapheneOS branding inventory, added network
  location, geocoding and attestation services, the browser's own connectivity
  checks, probe names sent while checks are off, and the Android Auto DHCP
  hostname. A source review of the inherited keyboard found that it keeps
  personal-dictionary words and contact names in storage available before first
  unlock, allows them in backups and ignores the no-learning flag; the Everyday
  use rows record it. The fix is written in a DiamaneOS keyboard fork and waits
  for a build and phone test.
- **2026-09-26:** rows updated as bring-up findings landed (TIPC in the kernel,
  VoLTE and call audio, logging, adb authorization on debuggable builds, OEM
  unlocking, key provisioning and deletion, the source-versus-stock review);
  added the certificate-validity-at-boot row for DiamaneOS's own clock daemon;
  recorded the OpenEUICC no-go, the eSIM service default and JNI library, the
  build identity, the enforcing policy of the 2026-09-26 build and the SoC
  serial exposure. On 2026-09-27 the fingerprint row recorded that the vendor
  debug service is never registered (kept inside the HAL process), and the
  kernel row recorded the kernel for the 2026-09-27 build: the module deny list,
  the production configuration and why KPROBES stays on. The same day, KPROBES
  was accepted, debugfs stays as in the 2026-09-26 build, boot parameters handed
  to user space are logged by name only, and a row covers the previous boot's
  logs kept by pstore/ramoops.
- **2026-09-27:** for the 2026-09-27 build, the eSIM row and decision record:
  the stock LPA cannot list profiles on the FP6, so its service is off again and
  its JNI library is gone; eSIM follows GrapheneOS. The firmware row records
  that the touch driver writes its stock firmware file to the controller when
  the versions differ, as on stock. The closed-file count stays at 720: the
  touch firmware came in and the JNI library went out. At integration the LPA
  wording was corrected (stock leaves its eSIM service on), the firmware row
  gained the touch driver's read-failure case, and the pstore row the device
  rule that makes the event-log device unwritable on release builds.
- **2026-09-27:** added the privacy-indicator row for the Tally shell from the
  design-token review: the prototype's privacy chip did not grow with the text
  size, and its night-mode indicator colours fell below 3:1 over a light app
  bar. The native spec keeps stock's sp sizes and picks indicator colours by the
  area under them.
- **2026-09-28:** the Tally prototype now draws the privacy chip at stock's sp
  sizes and takes indicator colours from the area under them, with a 2 dp edge;
  the prototype residual is removed from the privacy-indicator row.
- **2026-09-28:** for the Tally shell's first commits, the privacy-indicator row
  becomes "Privacy indicators and disclosures under the Tally shell" and takes
  the commit rules for indicators and for what the lock screen, prompts, footer,
  rows and toasts must show; a "Shell fork rebase lag" row covers carrying the
  shell onto each GrapheneOS release behind one build flag; the Fresh-UI
  boundary lists the lock-screen, transition, tile and settings-switch rules.
- **2026-09-28:** the Tally lock screen (the lock strip's sources and what it
  shows, the Tally clock, the indication line's fingerprint hint and error
  colour, the bouncer restyled by flagged resources) and the SettingsLib switch
  restyle, the system label and the fallback boot logo join the privacy row and
  the shell rules.
- **2026-09-28:** the SystemUI privacy indicators: the privacy-indicator row now
  says that only the shade's privacy chip opens the stock privacy dialog, as in
  stock, while the lens ring and the new location lamp by the lens take no
  touch. It names the lamp by the lens beside the ring, lists the one-chip shade
  header and the 16 dp dot among the drawing changes, says how the area under an
  indicator is read, and records the limits: an app's own in-window scrim is not
  seen, the status bar chips sit under SystemUI's dialogs and shade, and the
  FP6's ring follows the declared rectangular cutout.
- **2026-09-28:** the security reviews of the lock screen, fixed before any
  build: the lamp strip could crash SystemUI, and with it every privacy
  indicator, before its first items arrived, and showed more than stock's lock
  screen (an alarm more than 12 hours away, Bluetooth when on but not connected,
  the battery percentage always, the always-on strip briefly after dozing with
  doze off); it now matches stock, with tests that fail on the old code. The
  bouncer's Tally code only draws, the fingerprint hint's count cannot affect
  unlocking, and a bad stored count only hides the hint instead of crashing
  SystemUI.
- **2026-09-28:** the security review of Recents' Stop in SystemUI found that a
  Stop from Recents did not check that the lock screen was dismissed, as the
  Active apps dialog does, and two robustness gaps: a listener binder owned by
  SystemUI crashed it, and reports re-read every app's policy on the thread the
  privacy indicators use. Fixed before any build: it now refuses a Stop while
  the lock screen shows (occluded and dozing included), refuses SystemUI's own
  binders as the listener and survives a listener that throws, runs the work on
  SystemUI's long-running thread, and drops the listener when SystemUI lets go
  of Launcher.
- **2026-09-28:** the security review of the privacy indicators found that the
  area signal restarted the privacy dot's update delay, so an app flipping its
  status bar appearance fast enough could keep the dot from showing; that the
  capture chips' edge was clipped, so their dark colour fell below 3:1 on light
  areas; and that the overlay check did not cover the Tally indicator resources.
  The check now refuses them, and the rest was fixed before any build: the dot's
  colour no longer passes through its update delay, the capture chips' edge is
  their own border (light variants where no area is known), the VPN icon never
  overflows because of the dot's room, and the dot's touch area stays within its
  own column. Residuals: like the stock dot, the lens ring and the lamp by the
  lens are left out of screenshots, and on a locked phone and the always-on
  display they show whether the camera or location is in use.
- **2026-09-28:** the volume panel, power menu and toasts: drawing, layout and
  motion only. Ringer, Do Not Disturb, the safe-volume warning, stream muting
  and every accessibility action stay stock. The power menu keeps its actions,
  Lockdown and its lock-screen filtering; its new notes under Restart and
  Lockdown name the credential type in the lock screen's own words (the bouncer
  already shows it, so nothing new is disclosed). Toasts change look and motion
  only. The volume dialog can sit on the left edge by a device value (FP6, as
  stock FP6).
- **2026-09-28:** the lock screen fixes: the bouncer's PIN keys, typeface and
  password field change by visual-only code behind the Tally flag (input, the
  keyboard, lockout, throttling and the duress and wipe counters are untouched);
  an always-on strip replaces what stock's always-on date line showed and shows
  nothing more; the fingerprint hint's count is kept per user in SystemUI's
  device-protected storage; the Tally clock moves into the shared clock library,
  which now reads the Tally flag itself, so Wallpaper & style lists it
  (ThemePicker and WallpaperPicker2 link the flag and token libraries; no new
  data reaches them).
- **2026-09-28:** the privacy indicator fixes: the privacy dot sits 16 dp from
  the top and side and the status icons keep clear of it, screen capture alone
  takes the capture colour, indicators go out on the fill spring (appearance
  stays instant), and a lens config lets a device place the lamp by the lens.
  Found and fixed before any build: the first round's instant dot show did not
  cancel a running fade-out, whose end then hid the dot and removed its window
  while a sensor was still in use; showing the dot now cancels it, as stock's
  fade-in does.
- **2026-09-28:** Recents' Stop in SystemUI joins the shell rules: a new
  SystemUI-proxy surface for the recents app only, with the Active apps checks
  re-run on every Stop.
- **2026-09-28:** the shade and Quick Settings join the shell rules (see [Quick
  Settings and the shade](THREAT_MODEL-TALLY.md#quick-settings-and-the-shade)).
- **2026-09-29:** the security reviews of Recents' Stop in Launcher3 and the
  Settings homepage found no issue (see [Recents'
  Stop](THREAT_MODEL-TALLY.md#recents-stop) and [Settings
  homepage](THREAT_MODEL-TALLY.md#settings-homepage)).
- **2026-09-29:** the security review of Home and All apps found three low
  issues, fixed before any build: the tallies row showed a work app's progress
  or timer while a separately locked work profile was redacted in the shade
  (work items now show no readout); the LEDs and the row showed notifications
  the shade hides (now SystemUI's own filters); and a tally tap could crash Home
  when its app had just gone away (the shade opens instead). The review of
  Settings search found no issue: the target SDK bump to 37 adds platform
  protections and changes nothing the app can do.
- **2026-09-29:** the security reviews of the Settings switches (see [Settings
  switches](THREAT_MODEL-TALLY.md#settings-switches)), the WM Shell motion (see
  [Motion: WM Shell](THREAT_MODEL-TALLY.md#motion-wm-shell)) and the Launcher
  motion found no issue; the Launcher motion is motion only, with no permission,
  manifest or allow-list change (see [Motion:
  Launcher](THREAT_MODEL-TALLY.md#motion-launcher)).
- **2026-09-29:** after the phone test of a 2026-09-29 Tally test build: the
  Tally lock strip and always-on strip now show beside GrapheneOS's built-in
  smartspace, which hides its one line under them (952388b3eb35, 143dad85dd8c);
  before they showed, the always-on strip was made to show media only where
  GrapheneOS's always-on line does (-134). The lens ring lights only for the
  front camera (4bc99973aff3); the chip and the dot still show every camera.
- **2026-09-29:** a phone test of the same build found that any app's decorated
  custom-view notification (a Clock timer) crash-looped SystemUI, taking the
  status bar, the shade and every privacy indicator with it, because the Tally
  card colour was read through the posting app's context (-135; fixed in
  frameworks_base de8a9edb4265). This set the context rule under [Rules learned
  from findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings).
- **2026-09-29:** with lock-screen notifications off (off unless set),
  GrapheneOS's smartspace line shows nothing; the Tally lock strip now drops its
  alarm and the always-on strip everything (frameworks_base 941eaab3f3be). The
  lamps for Wi-Fi, Bluetooth, Calm and the battery stay, as the keyguard status
  bar icons they mirror show whatever that setting.
- **2026-09-29:** the lens ring keeps the camera privacy item's hold once the
  front camera has opened (frameworks_base f889e76a544e), and the date line
  under other clocks hides with lock-screen notifications off, as GrapheneOS's
  line (42cbc1c00780).
- **2026-09-29:** the security review of the Tally contexts and fixes found no
  second crash of the -135 kind (no Tally code reads SystemUI, Launcher or token
  resources through a context another app controls, and no app-controlled data
  was found to crash SystemUI, WM Shell, Launcher, Settings or the pickers), and
  three low findings, fixed before any build: the lens ring's close/privacy-item
  race (-136) and a facing lookup that could throw on the main thread (-137)
  (frameworks_base 2e27b4dfdd12), and tallies for a profile Launcher's user
  cache did not know yet shown as the main user's (-138, Launcher3 1ebdf19f1d).
  The lock strip's alarm now uses GrapheneOS's exact condition (cf4c47dc0240).
- **2026-09-29:** Home's tallies band and search slot can be removed like a
  widget and restored from Home settings (Launcher3 47aa5e6690): two booleans in
  Launcher's own backed-up preferences, no manifest, permission or allow-list
  change; removing the band only hides it (the listener keeps serving the key
  LEDs). The OS's own notices (the android and SystemUI packages) give no tally
  or LED (27e5352e94); they keep their status bar icons, and screen recording
  keeps its capture chip. Their security review found that the removable items'
  settings were read from credential-encrypted preferences through a real app
  context while the taskbar builds its device profile before the first unlock.
  That would crash-loop Launcher on every boot until then and, since Launcher's
  taskbar is the phone's navigation bar in this build, leave no Home or Recents
  before the first unlock (-139, fixed before any build in Launcher3 f885161fcb
  and 240e56d46f, which read them through the device profile's own injected
  preferences). The rest of the review found no issue: no path reaches the
  model, the database, uninstall or app info; nothing new is exported; no
  manifest or permission change; only system-uid or SystemUI code can post as
  the android or SystemUI packages. This set the credential-encrypted storage
  rule under [Rules learned from
  findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings).
- **2026-09-30:** the Files review fuzzed the archive code and reviewed trash
  (-140 to -143): the new archive code goes on (vendor_diamaneos b5ec53b); trash
  stays off. Home's tallies and keycap LEDs count only things with a live
  readout or an activity in progress, never permanent background services.
- **2026-09-30:** the Calculator fork and the recovery title fork: no manifest
  change in Calculator; recovery changes two string literals only. Notification
  rows get themed app icons (vendor_diamaneos release config, the flag
  android.app.notifications_redesign_themed_app_icons, read only by SystemUI's
  notification icon provider).
- **2026-09-30:** the Clock fork (emulator-checked, in a 2026-09-30 test build)
  and -144 (stock SystemUI: promoted chips show private content over an occluded
  lock screen; open).
- **2026-09-30:** -144 reproduced on the FP6 with a test app (a Live Update's
  private chip text shows over an app that occludes the lock screen on a locked
  phone; GrapheneOS's chip code, not Tally's). Home's tallies read Clock's
  MetricStyle time with plain Bundle getters only, and the media-session read no
  longer throws on a malformed value (-145, fixed before any build). This set
  the notification-extras rule under [Rules learned from
  findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings).
- **2026-09-30:** -144 fixed (frameworks_base 7ea6b864d39e): a promoted chip
  whose notification the lock screen redacts shows only the public version's
  short text or the icon, also while the lock screen is occluded; notifications
  the lock screen hides give no chip. Its sweep left two low stock items open
  (-146: status bar notification icons are never redacted; -147: always-on
  promoted notifications with a trust agent and a separately locked work
  profile, and dream chips upstream).
- **2026-09-30:** the security review of the Clock, Calculator, recovery title,
  Files and keyboard forks. Calculator, recovery and vendor_diamaneos are clean,
  and the keyboard's password and no-learning handling holds. Three findings:
  -148 (medium) and -149 (low) in the Clock fork (see
  [Clock](THREAT_MODEL-TALLY.md#clock)), and -150 (low) in the keyboard (see
  [Typed text and personal
  words](THREAT_MODEL.md#typed-text-and-personal-words)). The Clock bullet and
  the keyboard and Files rows are corrected. All three are fixed in a later
  2026-09-30 test build; -149 and -150 are verified on the FP6 (the database
  went back to version 8 with its alarms; planted word lists were deleted at the
  locked boot, before the first unlock).
- **2026-09-30:** -146 reproduced on the FP6 (on the test build that fixed -148
  to -150): over an app that occludes the lock screen on a locked phone, a
  priority conversation's status bar icon showed the contact's avatar while the
  lock screen showed the app's icon. A SystemUI fix makes the status bar and
  chip icons follow the lock screen's redaction; verified on the FP6 in the next
  test build (the chat icon over the occluding app, the avatar again after
  unlock).
- **2026-09-30:** public build commands: what they enforce and their limits.
- **2026-09-30:** rewritten for readability, with no change in substance: each
  threat row became a section with fixed points, and summaries of the biggest
  risks and of every asset's status, an IMS integration notes section and a list
  of terms were added. The rules learned from -135, -139 and -145 moved from
  these entries to the Fresh-UI boundary.
- **2026-10-01:** shortened: the revision history and the IMS integration notes
  moved to this file, the Tally shell's per-component rules to
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md), and internal build names were
  replaced by dates; no change in substance.
- **2026-10-01:** contradictions resolved with current facts. The 2026-09-27
  build's kernel changes, log-name printing, ramoops region and eSIM service
  switch are no longer "not yet built": that build was flashed and checked on
  the phone on 2026-09-27 (no removed module loaded, the Wi-Fi MAC in no log,
  the region registered on every boot, no eSIM service). The 2026-09-26 build's
  enforcing policy and clock daemon passed phone tests on 2026-09-27. The
  keyboard fork's -150 fix (deletion at LOCKED_BOOT_COMPLETED) is verified on
  the FP6, and the fork is in the manifest. The Tally switch lamps note that the
  FP6 has no Wi-Fi hotspot.
- **2026-10-01:** trimmed to the current state: the threat model now shows only
  what is built, and every built feature updates it in the same change.
- **2026-10-01:** statuses brought up to date from the 2026-09-26 build's phone
  tests: the fingerprint HAL fix (enrolment and unlock work) and the TEE proxy
  removal are in tested builds, telephony runs enforcing with both SIMs in
  service, and the development-build rule no longer depends on SELinux mode.
- **2026-10-01:** decision and test notes stated as plain facts, review notes
  shortened and local branch names replaced by public commits; no change in
  substance.

- **2026-10-01:** location's own privacy chip (the stock flag GrapheneOS leaves
  off is on: each app's first location use in 10 minutes runs the chip, then the
  dot). -151 (medium, a Tally regression: a camera or microphone start while
  location alone showed the dot changed nothing in Tally's status bar, where
  stock turns its blue dot green) and -152 (low, stock: a sensor joining during
  a chip left the dot with the chip's old items) are fixed; both verified on the
  FP6 or in replay of the scheduler.
- **2026-10-01:** the system font is Sofia Sans Tally for every app, built from
  the pinned upstream font and checked with OTS and fontTools; WebView keeps the
  platform fonts.
- **2026-10-01:** location logging (-95), implemented, not yet built: the
  vendor step sets the GNSS engine to warnings and errors only, the level its
  library already uses on user builds, and the telephony fork logs the modem's
  registration results with the serving cell redacted as the framework's other
  cell logs are. The physical cell ID and channel stay in the radio log, as in
  AOSP.
- **2026-10-01:** kernel changes, implemented, not yet built: debugfs keeps its
  in-kernel interface for kernel code but can no longer be mounted, not even by
  root (-114; both kernel forks fix the upstream mount-refusal mode and kernel
  policy v8 requires it); the Wi-Fi platform driver no longer logs MAC
  addresses the modem or the device supplies (-112).
- **2026-10-01:** SELinux triage of the remaining enforcing denials,
  implemented, not yet built: system_server reads the DisplayPort and audio
  codec extcon cable names (sysfs_extcon, replacing per-index labels that
  followed probe order); the camera and media services read the gralloc
  properties the buffer mapper rereads per buffer; the sensors HAL's start-up
  list gets its own label, written by init with the stock types minus the hall
  sensor and only readable by the HAL; one dontaudit for the sensors HAL's denied writes of a factory
  proximity value; the source-built health service filters kernel uevents to
  power-supply events.
- **2026-10-01:** -153: the closed Qualcomm sensors sub-HAL lists a
  vendor-private ambient colour (RGB) sensor, wake-up and non-wake-up, with no
  required permission. GrapheneOS gives the Sensors permission to every sensor
  but lets a HAL-supplied permission replace it for vendor types, and an empty
  one means no permission, so any app can read the sensor. Nothing in the build
  uses it: no vendor file but the sub-HAL names it, the camera reads the
  ambient light sensor, not the colour sensor, and the framework does not know
  the type. The sub-HAL has no switch for single sensor types, the multi-HAL
  and sensor service have no filter, and removing the sensor's registry
  configuration would most likely still publish it with default values. The
  fix is the owner's decision: a source wrapper around the sub-HAL that
  withholds vendor types without a permission, or a platform change that keeps
  the Sensors permission when a HAL leaves it empty.
- **2026-10-01:** -153 fix implemented, not yet built: the DiamaneOS
  frameworks/native fork keeps the Sensors permission for a custom sensor whose
  HAL permission is empty, so the colour sensor needs the Sensors permission like
  every standard sensor.

- **2026-10-03:** the Colour icon style is the default: listed system apps get
  keys in their own colour (package name and system-app status checked), other
  apps keep their own icons; Launcher writes the chosen style to the Secure
  setting tally_icon_style with a permission it already holds, and SystemUI
  reads it for notification icons. The default wallpaper is Paper, a SystemUI
  wallpaper service with no permissions, bindable only with BIND_WALLPAPER; the
  lock screen draws no dim over it.
- **2026-10-05:** Qualcomm's closed perf2 daemon and its client libraries leave
  the build, implemented, not yet built. It ran as root with setuid, kill and
  sys_nice and could read and write every app's `/proc` files. LineageOS's
  libperfmgr power HAL replaces it and the CodeLinaro power HAL: system user,
  CAP_SYS_NICE only, write access to the CPU and GPU frequency limits, the GPU
  wake trigger and the tap-to-wake switch only, setsched on apps,
  SurfaceFlinger and system_server for ADPF, and the scheduler boost only
  through fixed init triggers. A no-op perf client stands in for the closed one
  the camera and composition extension load.
- **2026-10-05:** public-text pass. Statuses brought up to date with the
  2026-10-05 build and phone tests: Bluetooth pairing, music and a headset call
  work with DiamaneOS's own HCI service; calls with audio work through the
  call-audio bridge, which holds the call-audio control permission as well as
  the normal audio-settings one; compressed music is decoded in software; the
  -153 fix, the debugfs mount refusal and the location logging changes are in
  builds since the 2026-10-01 build; the vendor patch level and the public build
  commands are in the 2026-10-05 build. The IMS section states what exists
  instead of release gates, the "defends against" heading says what DiamaneOS
  aims to defend against, and the disclaimer names Fairphone.
- **2026-10-05:** the cellular controls are checked on the phone. With "2G
  network protection" on, the modem's own preference read back has no GSM
  technologies and the phone stays on LTE with calls working; with LTE-only it
  allows LTE alone and calls over LTE work; turning protection off brings GSM
  back. Settings shows Android's filtered network-type list.
- **2026-10-05:** statuses brought up to date with the phone: the libperfmgr
  power HAL, the power stats HAL (own user, no capabilities, only the seven
  read commands), the camera seccomp loader (log mode) and the OpenCL removal
  are built and run. The stock traceability daemon stays excluded; our IMEI
  tool reads only the two IMEIs from the traceability partition and writes
  them to the modem at every boot, because activating a slot restores the
  modem's file system from a copy without them. Bluetooth was checked: it uses
  a random, locally administered address kept until a reset.
- **2026-10-05:** the encoder-only hardware codec service is built and runs
  on the phone: seccomp filter installed, camera recording uses the hardware
  H.264 encoder, playback stays on the software decoders. The stock Codec2
  libraries that share names with platform libraries load from /odm. The
  modem file server fork is built and serves the modem.
- **2026-10-05:** Log Viewer is forked so its Report button opens the
  DiamaneOS issue tracker instead of GrapheneOS's; as before it copies the log
  to the clipboard and the user decides what to paste.
- **2026-10-05:** a power stats HAL of our own, implemented, not yet built.
  Stock ships none. It reports the SoC sleep modes and the modem, WPSS, ADSP
  and CDSP sleep time from the qcom_stats driver's `/dev/stats` ioctls, runs
  as its own vendor user without capabilities in the platform power stats HAL
  domain, and is the only vendor process that may open the node, limited to
  the seven read commands it uses. The driver's other commands read the wrong
  subsystem entries or send a request to the AOSS and stay denied. No energy
  meters: the FP6 has no on-device power monitor.
- **2026-10-05:** the kernel's forced lockdown moves from confidentiality to
  integrity (owner decision), implemented, not yet built. Confidentiality had
  emptied tracefs and denied BPF programs kernel-memory reads, so Android's
  per-app CPU time accounting and lmkd's memory-event listener did not start.
  Integrity still keeps user space from modifying the running kernel, but with
  KPROBES on, kprobes from user space are now limited by SELinux: on user
  builds only init may write tracefs's kprobe_events; on userdebug builds the
  permissive adb root shell and AOSP's tracing domains (shell without root,
  atrace, traced_probes, Traceur, simpleperf_boot, profcollectd, the atrace
  HAL) may. Only bpfloader loads BPF programs. XFRM state dumps carry IPsec
  keys again, readable only by netd, system_server, the network stack,
  netutils_wrapper, dumpstate and Qualcomm's nicmd. The kernel policy requires
  integrity and fails a confidentiality configuration.
- **2026-10-05:** the stock camera's performance hints reach the power HAL,
  implemented, not yet built. Our stand-in for the closed perf client passes
  on only the camera's open, close and snapshot hints, as the camera launch
  and camera shot boosts: at most 5 s each, a hint held until release at most
  2 s, ended early when the camera releases it, every call carrying its own
  time limit; it sends them from its own thread with one-way calls. For this
  the camera provider is a power HAL client, as on Pixels. SELinux grants a
  binder interface as a whole, so code running in the provider can make any
  power HAL request: power modes (fixed or capped CPU and GPU clocks, a raised
  GPU floor, tap-to-wake), boosts (raised CPU floors, the scheduler boost) and
  hint sessions that raise the uclamp of app, SurfaceFlinger or system_server
  threads; the effect is on speed, battery and heat, with no access to data
  (-172). The camera streaming modes, which capped the little cores CamX runs
  on, are gone from the power HAL configuration.
- **2026-10-06:** an 8-hour idle capture of every interface (Wi-Fi and
  mobile data, enforcing) showed no connection from vendor code to any third
  party: only the carriers' Wi-Fi calling and VoLTE tunnels and DNS,
  GrapheneOS's connectivity and app update checks, and local network traffic.
  Integrity lockdown observed on the phone (tracing, per-app CPU time and
  lmkd's memory events run); the camera boosts end within about 1 s, when the
  camera releases them.
- **2026-10-06:** official builds, implemented, not yet built:
  `DIAMANEOS_OFFICIAL_BUILD` (DiamaneOS's builder, `build --official`) adds
  the DiamaneOS fork of GrapheneOS's Updater. It asks `releases.diamaneos.de`
  over TLS pinned to the ISRG roots; that server is not live, so every check
  fails, and failed checks back off from 4 minutes to 6 hours instead of
  retrying every 4 minutes. An answer that is not update information fails
  the check instead of crashing the Updater. A build that is not tagged
  release-keys, or whose OTA certificates include a public AOSP test key,
  checks for updates but downloads and installs none. GrapheneOS's
  `OFFICIAL_BUILD` stays refused.
- **2026-10-06:** privacy hardening (implemented, not yet built). The SoC
  serial number gets its own SELinux type that no vendor rule grants (no
  shipped program reads it) and ueventd makes it root-only: the platform's
  sysfs-wide read rules still name some HAL domains, which run as their own
  users, so only root platform daemons can open it. The thermal HAL runs as
  system without capabilities. nicmd keeps its IPsec state dump: it installs
  the Wi-Fi calling states the modem negotiates and dumps all states to
  delete its own, so under integrity lockdown it can read every state's keys.
  The UFS storage serial numbers become root-only too, the stock Bluetooth
  HCI implementation's address logging is turned down, the unused
  secure-processor and display colour service grants are removed,
  neverallows guard the fingerprint HAL's data label, and the kernel policy
  pins kcore, KGDB, kexec and hibernation off. Statements corrected: the
  stock LPA is no longer shipped, OEM unlocking is managed by the OS, the 2G
  and LTE-only controls and the radio log redaction were checked on the
  phone, debugfs is not mountable on the phone, and the kernel cannot run
  SELinux permissive.
- **2026-10-06:** audio HAL, PAL and AGM, implemented, not yet built. The
  primary HAL, PAL, AGM with its HIDL service and ALSA plugins, and
  audioadsprpcd are built from Fairphone's published FP6 sources (the
  sources of the stock build, without Fairphone's closed headers) under the
  stock names, with control-flow integrity and the integer overflow
  sanitizer as in stock. The HAL registers no PAL HIDL service; it, the
  memory logger and the dynamic logging library are no longer shipped
  (closed audio files 36 to 17). The graph services, whose sources Fairphone
  did not publish, their tuning server (no diag access), the voice UI
  interface and the deadline manager stay closed in the HAL process. The
  Awinic calibration code, published without a licence header, is compiled
  into PAL as in stock; nothing calls it. The amplifier's factory
  calibration is read by the kernel driver, unchanged. The HAL no longer
  answers the factory speaker queries or accepts the factory speaker
  protection modes, and ignores voice UI keys without a sound trigger HAL, which closes parameter-query bugs any app could reach
  (-178).
- **2026-10-06:** audio effects, implemented, not yet built. AOSP's effect
  proxy creates both halves of an offloadable effect and sends every command
  to both, so with offload playback off every app's equalizer, bass boost,
  virtualizer, reverb and visualizer parameters still reached Qualcomm's
  closed offload effect bundle and visualizer (-175). Our effects
  configuration lists only AOSP's software effects and the two closed
  libraries are no longer shipped; the stock HAL loads them only if present.
  Qualcomm's VoIP echo-cancellation and noise-suppression descriptors and its
  volume listener are built from CodeLinaro source, identical to Fairphone's
  published FP6 audio sources. VoIP capture keeps the DSP's tuned echo
  cancellation instead of AOSP's software canceller.
- **2026-10-06:** eSIM (implemented, not yet built). DiamaneOS's own eSIM
  manager (LPA) ships disabled behind GrapheneOS's eSIM support switch, which
  the Settings fork points at it. When on, it lists, turns on and off, renames
  and deletes the profiles on the eUICC and erases them on request, all
  through Android's EuiccCardController; it holds two privileged permissions,
  has no network access, downloads nothing and logs no identifiers. Slot 1 is
  marked as the built-in eUICC. Statement corrected: duress erases eSIM
  profiles through the framework, not through an LPA.
- **2026-10-06:** Wi-Fi, implemented, not yet built. The qcacld driver takes
  its wake-on-WLAN setting from its configuration file and ignores the
  supplicant's WoWLAN triggers; stock left it at the default, magic packet and
  pattern match, so any peer on the network could wake the phone. The
  generated configuration sets `gEnableWoW=2` (pattern wake-ups only), and the
  supplicant overlay no longer asks for the magic packet (-76).
- **2026-10-06:** third hardening batch, implemented, not yet built.
  - dm-verity hash trees use SHA-256, as on stock; avbtool's default was SHA-1
    (-65). The AVB image check requires SHA-256 for all seven logical
    partitions.
  - The boot control HAL runs as its own user with CAP_SYS_RAWIO only (the
    UFS BSG ioctl that switches the boot LUN needs it) instead of root with
    every capability; ueventd gives its group the GPT disks of the A/B LUNs,
    misc and the UFS BSG node, and the other LUNs stay root-only.
  - Qualcomm's embedded USB debugger (EUD) stays off: its enable switch gets
    its own SELinux type, which only ueventd and vendor_init may write (vold,
    the USB HAL and vfio_handler could write it before); the image checks
    refuse an init file, module option or boot parameter that turns it on
    (-18). Removing the driver needs a device-tree change: the USB controller
    takes its cable events from the EUD node.
  - The ANT, ANT HCI and Bluetooth SAR service contexts are removed, so the
    Bluetooth HAL can no longer register those interfaces (-36).
  - The source-built gralloc no longer loads libubwcp when UBWC-P support is
    compiled out (-50).
  - Fairphone's published SHA-256 for FP6.QREL.16.111.0 matches the pinned
    archive (-52); the capability record marks protected VMs unsupported
    under Gunyah (-84).
- **2026-10-06:** Moments switch, implemented, not yet built. The input
  service reports the FP6's side slider to SystemUI only (signature
  permission, internal device only, repeats dropped); its settings are
  protected system settings readable by Settings, SystemUI and Launcher; it
  raises protection at once and lowers it after the unlock. Rules in
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- **2026-10-07:** Moments switch fixes after a phone test,
  implemented, not yet built. The FP6 offers the software camera and
  microphone access toggles (Settings, Quick Settings and the switch's action),
  enforced by the framework's app ops and the camera service as on Pixels; the
  switch's action was a silent no-op before and said it worked. Lockdown is
  offered only with a screen lock. A switch change in the frame the input
  reader drops after a buffer overrun is now reported; the first flip after
  each boot was lost before.
- **2026-10-06:** kernel hardening, implemented, not yet built.
  - /proc/cmdline shows parameter names only once init starts; module options
    keep their values because Android's modprobe reads them there (the
    display driver's panel, for example). Bug reports copy the line, so the
    hardware identifier the bootloader passes no longer reaches them (-112).
    The kernel build requires the change's symbol in the System.map.
  - Reboots ignore the "edl" and "qcom_dload" reasons and restart normally;
    the download-mode module parameters and dload sysfs files are read-only,
    so the running system cannot turn RAM dumps on (-17). Panics follow the
    build default (dumps off), which the vendor kernel policy now checks.
    EDL through the hardware keys is unchanged (-40).
  - The EUD debugger's device-tree node is disabled and its driver is on the
    deny list (-18). The USB controller drops its EUD extcon and takes connect
    and role events from UCSI only, as it already did for every event but
    EUD's own spoofed connects.
- **2026-10-07:** Moments switch: airplane mode and Lockdown are separate
  actions (the combined one left airplane mode stuck on without a screen lock);
  Lockdown is enabled only with a screen lock. Implemented, not yet built.
- **2026-10-07:** Moments switch kernel floor, implemented, not yet built.
  With "Camera and microphone off" chosen, the kernel mutes the built-in
  microphones at the codec and keeps their bias off while the switch is down;
  the choice is sealed in the kernel once per boot; Android shows the block as
  a hardware microphone toggle; emergency calls have no microphone while the
  switch is down. The switch can no longer be disabled from sysfs (-189).
  Rules in [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- **2026-10-07:** Moments switch kernel floor for the cameras, implemented, not
  yet built. With "Camera and microphone off" chosen, the camera driver holds
  every known image sensor on its test pattern while the switch is down, and
  the laser range finder does not range; Android shows a hardware camera
  toggle. Residuals: the image processor's firmware (-192) and the unmeasured
  command DMA reach (-191). Rules in
  [THREAT_MODEL-TALLY.md](THREAT_MODEL-TALLY.md#moments-switch).
- **2026-10-07:** Moments switch: while the switch blocks the camera or
  microphone, the platform's access prompt is suppressed for that sensor and a
  short toast shows instead (the prompt paused the camera app and broke the
  recording); a block the user made keeps the prompt. Implemented, not yet
  built.
- **2026-10-07:** Moments switch: when "Camera and microphone off" is chosen
  but the kernel was sealed without the microphone, the page asks for a
  restart and one notification per boot says only Android blocks the
  microphone; with another choice while the kernel still blocks, the page says
  so. Read from the kernel's state file. Implemented, not yet built.
- **2026-10-07:** Moments switch: the user may turn off the note for a
  blocked camera or microphone (silent block; still no prompt). Implemented,
  not yet built.
- **2026-10-06:** firmware delivery. Image sets carry Fairphone's firmware of
  the release the vendor files come from, copied from the authenticated factory
  package and checked against the per-image hashes when packaged and verified.
  `flash-steps` writes it before the OS, in the stock order and on both slots
  (the OS stays on slot a), with the modem file system emptied so the modem
  restores its factory backup, only over an older release and never over a
  newer one. The FP6 bootloader reports no firmware version, so the phone's
  release comes from the user or the earlier image set. No OTA carries
  firmware; verify refuses an A/B partition list with only part of it.
  Implemented, not yet built or tested on a phone.
- **2026-10-06:** USB-C port control (implemented, not yet built). The
  framework setting is on, and init triggers map GrapheneOS's port states to
  the USB controller's data switch, which gets its own SELinux type that only
  vendor_init may write. Off also turns off charging while the OS runs, as
  on Pixels: init suspends the charger input in Qualcomm's charger firmware,
  which also only vendor_init may write; every other mode, boot, charger
  mode and shutdown turn it back on. User builds start every boot with
  USB data and charging off until the stored mode applies, as Pixels do. Unlike Pixels,
  Type-C and USB PD keep running in Qualcomm firmware in Off. Without port
  control a user
  build had no USB data at all. On debuggable builds a restrictive mode is
  reset at boot and the lock modes do nothing while USB debugging is on.
- **2026-10-06:** eSIM downloads, implemented, not yet built.
  - The eSIM manager downloads profiles (SGP.22 v2 consumer download) through
    Android's ES10b card commands and its own ES9+ client; it gains network
    access, no camera permission and no policy change (priv_app).
  - Network only after the user agrees to contact the named server, or for
    its own profiles' notifications after the user's change; HTTPS trusts
    only the GSMA production CI roots the eUICC also lists.
  - The framework's eUICC transport logged every eUICC command and answer in
    full at verbose level on all builds (ICCIDs; during a download the
    matching ID, the IMEI and the EID); its tags stay at info (-181).
  - Phone checks: the ISD-R channel, the profile list and turning a profile
    off and on work.

## IMS integration notes

These notes record how the IMS containment evolved. The current state is in [IMS
status](THREAT_MODEL.md#ims-development-evidence).

### Candidate update (2026-09-27)

- The source DCM broker and the stock Qualcomm IWLAN and certificate frontend
  are prepared on an integration branch.
- The certificate helper adds a modem-facing QRTR client. It and IWLAN share
  their own application UID, keep the stock signer and receive no privileged
  Android permission grants.
- QRTR has no per-QMI-service isolation, so a compromised allowed client stays a
  modem trust risk. A device-owned domain inventory prevents silent additions.
- The two stock packages need narrow hidden-API exceptions for platform IPC.
  This does not grant Android signature permissions or bypass their SELinux
  domain.
- Carrier configuration is extracted as data for a source-built service,
  including a pinned repair of malformed stock no-SIM XML. The stock carrier APK
  is not installed.
- These are source-level mitigations awaiting native policy, package and carrier
  checks; see [carrier integration](CARRIER-INTEGRATION.md).
- Android already includes a privileged IMS entitlement client through its
  telephony product. The candidate replaces it with a source fork that removes
  Google push libraries, bounds carrier responses, restricts exported entry
  points and keeps provisioning state on failed queries. The inspected stock
  profiles and the current configuration do not enable that flow; activation
  stays carrier-driven. Carriers that depend on push stay a compatibility limit,
  not a reason to invent approval flags.

### Native integration follow-up

- Generated carrier-ID filenames use numeric IDs and a fixed suffix, so stock
  display labels cannot become shell syntax in Soong's asset-copy rules.
  Provenance still binds the XML bytes and the original names.
- The DCM service's policy separates inherited platform service discovery from
  invocation: system diagnostics can find its name, but inbound Binder calls
  stay restricted to the broker (and userdebug `su`), and the daemon validates
  the caller's UID.
- Product selection also installs the generated vendor user and group databases;
  a build-time AID declaration alone does not give init a runtime user mapping.
  Final-image checks require that mapping and the dedicated domains.
- These controls still need phone checks.

### First phone boot

- The broker reached its dedicated domain under enforcing policy, but the vendor
  daemon's lifecycle gate referenced a platform-internal property.
- The gate is now owned by system_ext init. The daemon stays vendor-confined and
  non-lazy, which keeps the persistent kill switch without widening property
  access.
- The Android 17 full SDK version is explicitly labelled as a public build
  property, instead of granting the vendor IWLAN app access to default_prop.
- These startup fixes are pending a new native build and phone test; IMS is not
  yet accepted.

### Second phone boot

- The DCM daemon started automatically under UID/GID 2990, with SELinux
  enforcing, seccomp and no-new-privileges, and with no effective or permitted
  capabilities.
- Both cellular IMS bearers connected, but the broker saw a background firewall
  block, because Android leaves apps without INTERNET out of those rule updates.
- The broker now declares INTERNET so its UID is tracked correctly. On this base
  INTERNET is a runtime-revocable Network permission, granted automatically by
  the base default; the broker does not bypass revocation. Its neverallows for
  direct IP, raw and modem sockets, and its blocked-state handling, are
  unchanged.
- This widens Android permission authorization and must be reviewed together
  with possible indirect IPC paths. It is not a claim that every network-capable
  system service is unreachable. No Internet socket grant or policy bypass is
  added.
- IWLAN gets the thermal-service lookup that the platform PowerManager
  constructor needs; unrelated service probes stay denied.
- These changes await native and phone qualification.
