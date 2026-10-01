# DiamaneOS Threat Model: History

The revision history and historical notes of [THREAT_MODEL.md](THREAT_MODEL.md).
Every security or privacy change updates both files in the same commit. Entries
before 2026-09-30 call each asset section a "row". Development builds are named
by date: the 2026-09-26 build is the first SELinux-enforcing one, the 2026-09-27
build the next.

## Revision line until 2026-10-01

Revision: 2026-09-30 (rewritten for readability; no change in substance); 2026-09-30 (the stock input moved to FP6.QREL.16.111.0, selected before Fairphone published its checksum; firmware images hashed per image; the Clock, Calculator, recovery title, Files archive and keyboard privacy forks, with their security review; -140 to -150; Home's live tallies); 2026-09-29 (Recents' Stop in Launcher3, the Settings homepage, Home and All apps, Settings search, the switches and rows, and the motion in WM Shell and Launcher3, with their security reviews); 2026-09-28 (the Tally shell: the privacy-indicator row widened to privacy indicators and disclosures, a shell fork rebase lag row and the shell's commit rules in the Fresh-UI boundary; the prototype's privacy chip at stock's sizes; the native privacy indicators: which of them take a tap, the location lamp by the lens and the limits of the area rule; the lock screen, the SettingsLib switches and the system label; the shade and Quick Settings; Recents' Stop in SystemUI; the privacy indicator fixes and an indicator bug fixed before any build; the lock screen fixes: the bouncer's visual-only code, the always-on strip, the per-user hint count, the Tally clock in the shared clock library; the volume panel, power menu and toasts; the privacy indicators' security review; the Tally switch views; the shade's animated lamps and heads-up; the security review of Recents' Stop; the lock screen reviews A and B); 2026-09-27 (the 2026-09-27 build: the production kernel configuration and module deny list, KPROBES and debugfs kept as in the 2026-09-26 build, boot parameters logged by name only, the previous boot's logs kept by pstore/ramoops, the stock LPA's eSIM service off again, the touch controller firmware, what confidentiality lockdown costs at runtime, the saved console's log level, pstore phone results with cold reboots kept; the Tally privacy-indicator row; remote key provisioning failing in the TEE on the 2026-09-27 build); 2026-09-26 (endpoint additions from the branding inventory, the keyboard privacy gap, and the bring-up and 2026-09-26 build updates; the full refresh of 2026-09-25 replaced the initial model of 2026-09-14).

## Revision history

- **2026-09-14:** initial threat model.
- **2026-09-25:** full refresh from six internal reviews (cellular, proximity,
  vendor userspace, network, physical, supply chain) and two independent checks.
  Added development-state, cellular, SMS, location, kernel, closed vendor
  userspace, identifier, TEE, firmware, supply-chain and signing rows; added
  attestation and kernel limits; corrected the USB, pKVM, fingerprint,
  secure-hardware, egress and kernel-source statements; applied a weakest-state
  rule to every evidence state.
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
  the production configuration and why KPROBES stays on. After the owner's
  decisions the same day, KPROBES is owner-accepted, debugfs stays as in the
  2026-09-26 build, boot parameters handed to user space are logged by name
  only, and a row covers the previous boot's logs kept by pstore/ramoops.
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
- **2026-09-28:** the Tally lock screen (local tally-lock: the lock strip's
  sources and what it shows, the Tally clock, the indication line's fingerprint
  hint and error colour, the bouncer restyled by flagged resources) and the
  SettingsLib switch restyle, the system label and the fallback boot logo join
  the privacy row and the shell rules.
- **2026-09-28:** the SystemUI privacy indicators (local branch, not built): the
  privacy-indicator row now says that only the shade's privacy chip opens the
  stock privacy dialog, as in stock, while the lens ring and the new location
  lamp by the lens take no touch. It names the lamp by the lens beside the ring,
  lists the one-chip shade header and the 16 dp dot among the drawing changes,
  says how the area under an indicator is read, and records the limits: an app's
  own in-window scrim is not seen, the status bar chips sit under SystemUI's
  dialogs and shade, and the FP6's ring follows the declared rectangular cutout.
- **2026-09-28:** security review B of the lock screen (local tally-lock, not
  built) found that the lamp strip could crash SystemUI before its first items
  arrived (high if shipped: a crash takes every privacy indicator down), and
  that the strip showed an alarm more than 12 hours away, Bluetooth when on but
  not connected and the battery percentage always, and the always-on strip
  briefly after dozing with doze off, all more than stock's lock screen shows.
  The branch now fixes all of them, matching stock, with tests that fail on the
  old code.
- **2026-09-28:** security review A of the lock screen (local tally-lock, not
  built): the bouncer's Tally code only draws, and the fingerprint hint's count
  cannot affect unlocking; its background read and write are to be guarded, so a
  bad value only hides the hint instead of crashing SystemUI (fixed on the
  branch).
- **2026-09-28:** the security review of Recents' Stop in SystemUI (local
  tally-recents, not built) found that a Stop from Recents did not check that
  the lock screen was dismissed, as the Active apps dialog does, and two
  robustness gaps: a listener binder owned by SystemUI crashed it, and reports
  re-read every app's policy on the thread the privacy indicators use. The
  branch now refuses a Stop while the lock screen shows (occluded and dozing
  included), refuses SystemUI's own binders as the listener and survives a
  listener that throws, runs the work on SystemUI's long-running thread, and
  drops the listener when SystemUI lets go of Launcher.
- **2026-09-28:** the security review of the privacy indicators (local
  tally-status, not built) found that the area signal restarted the privacy
  dot's update delay, so an app flipping its status bar appearance fast enough
  could keep the dot from showing (critical if shipped); that the capture chips'
  edge was clipped, so their dark colour fell below 3:1 on light areas; and that
  the overlay check did not cover the Tally indicator resources. The check now
  refuses them, and the branch fixes the rest: the dot's colour no longer passes
  through its update delay, the capture chips' edge is their own border (light
  variants where no area is known), the VPN icon never overflows because of the
  dot's room, and the dot's touch area stays within its own column. Residuals:
  like the stock dot, the lens ring and the lamp by the lens are left out of
  screenshots, and on a locked phone and the always-on display they show whether
  the camera or location is in use.
- **2026-09-28:** the volume panel, power menu and toasts (local branches
  volume-left and tally-popups, not built): drawing, layout and motion only.
  Ringer, Do Not Disturb, the safe-volume warning, stream muting and every
  accessibility action stay stock. The power menu keeps its actions, Lockdown
  and its lock-screen filtering; its new notes under Restart and Lockdown name
  the credential type in the lock screen's own words (the bouncer already shows
  it, so nothing new is disclosed). Toasts change look and motion only. The
  volume dialog can sit on the left edge by a device value (FP6, as stock FP6).
- **2026-09-28:** the lock screen fixes (local tally-lock, not built; two
  security reviews pending): the bouncer's PIN keys, typeface and password field
  change by visual-only code behind the Tally flag (input, the keyboard,
  lockout, throttling and the duress and wipe counters are untouched); an
  always-on strip replaces what stock's always-on date line showed and shows
  nothing more; the fingerprint hint's count is kept per user in SystemUI's
  device-protected storage; the Tally clock moves into the shared clock library,
  which now reads the Tally flag itself, so Wallpaper & style lists it
  (ThemePicker and WallpaperPicker2 link the flag and token libraries; no new
  data reaches them).
- **2026-09-28:** the privacy indicator fixes (local tally-status, not built):
  the privacy dot sits 16 dp from the top and side and the status icons keep
  clear of it, screen capture alone takes the capture colour, indicators go out
  on the fill spring (appearance stays instant), and a lens config lets a device
  place the lamp by the lens. Found and fixed before any build: the first
  round's instant dot show did not cancel a running fade-out, whose end then hid
  the dot and removed its window while a sensor was still in use; showing the
  dot now cancels it, as stock's fade-in does.
- **2026-09-28:** Recents' Stop in SystemUI (local branch tally-recents, not
  built; security review pending) joins the shell rules: a new SystemUI-proxy
  surface for the recents app only, with the Active apps checks re-run on every
  Stop.
- **2026-09-28:** the shade and Quick Settings (local branch tally-shade, not
  built) join the shell rules (see [Quick Settings and the
  shade](THREAT_MODEL-TALLY.md#quick-settings-and-the-shade)).
- **2026-09-29:** the security reviews of Recents' Stop in Launcher3 and the
  Settings homepage (local Launcher3 tally-recents and Settings tally-home, not
  built) found no issue (see [Recents' Stop](THREAT_MODEL-TALLY.md#recents-stop)
  and [Settings homepage](THREAT_MODEL-TALLY.md#settings-homepage)).
- **2026-09-29:** the security review of Home and All apps (local Launcher3
  tally-home, not built) found three low issues, fixed on the branch before any
  build: the tallies row showed a work app's progress or timer while a
  separately locked work profile was redacted in the shade (work items now show
  no readout); the LEDs and the row showed notifications the shade hides (now
  SystemUI's own filters); and a tally tap could crash Home when its app had
  just gone away (the shade opens instead). The review of Settings search
  (SettingsIntelligence tally, not built) found no issue: the target SDK bump to
  37 adds platform protections and changes nothing the app can do.
- **2026-09-29:** the security review of the Settings switches (local
  frameworks_base tally-build and Settings tally-switches, not built) found no
  issue (see [Settings switches](THREAT_MODEL-TALLY.md#settings-switches)).
- **2026-09-29:** the security review of the WM Shell motion (local
  frameworks_base tally-build, not built) found no issue (see [Motion: WM
  Shell](THREAT_MODEL-TALLY.md#motion-wm-shell)).
- **2026-09-29:** the security review of the Launcher motion (local Launcher3
  tally-motion, not built) found no issue: motion only, with no permission,
  manifest or allow-list change (see [Motion:
  Launcher](THREAT_MODEL-TALLY.md#motion-launcher)).
- **2026-09-29:** after the phone test of a 2026-09-29 Tally test build (local
  frameworks_base tally-build, not built): the Tally lock strip and always-on
  strip now show beside GrapheneOS's built-in smartspace, which hides its one
  line under them (952388b3eb35, 143dad85dd8c); before they showed, the
  always-on strip was made to show media only where GrapheneOS's always-on line
  does (-134). The lens ring lights only for the front camera (4bc99973aff3);
  the chip and the dot still show every camera.
- **2026-09-29:** the owner's phone test of the same build found that any app's
  decorated custom-view notification (a Clock timer) crash-looped SystemUI,
  taking the status bar, the shade and every privacy indicator with it, because
  the Tally card colour was read through the posting app's context (-135, high
  if shipped; fixed on frameworks_base tally-build de8a9edb4265). This set the
  context rule under [Rules learned from
  findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings); a review of that
  rule across the Tally code follows.
- **2026-09-29:** with lock-screen notifications off (off unless set),
  GrapheneOS's smartspace line shows nothing; the Tally lock strip now drops its
  alarm and the always-on strip everything (frameworks_base tally-build
  941eaab3f3be). The lamps for Wi-Fi, Bluetooth, Calm and the battery stay, as
  the keyguard status bar icons they mirror show whatever that setting.
- **2026-09-29:** the lens ring keeps the camera privacy item's hold once the
  front camera has opened (frameworks_base tally-build f889e76a544e), and the
  date line under other clocks hides with lock-screen notifications off, as
  GrapheneOS's line (42cbc1c00780).
- **2026-09-29:** the security review of the Tally contexts and fixes found no
  second crash of the -135 kind (no Tally code reads SystemUI, Launcher or token
  resources through a context another app controls, and no app-controlled data
  was found to crash SystemUI, WM Shell, Launcher, Settings or the pickers), and
  three low findings, fixed before any build: the lens ring's close/privacy-item
  race (-136) and a facing lookup that could throw on the main thread (-137)
  (frameworks_base tally-build 2e27b4dfdd12), and tallies for a profile
  Launcher's user cache did not know yet shown as the main user's (-138,
  Launcher3 tally-home 1ebdf19f1d). The lock strip's alarm now uses GrapheneOS's
  exact condition (cf4c47dc0240).
- **2026-09-29:** Home's tallies band and search slot can be removed like a
  widget and restored from Home settings (Launcher3 tally-home 47aa5e6690): two
  booleans in Launcher's own backed-up preferences, no manifest, permission or
  allow-list change; removing the band only hides it (the listener keeps serving
  the key LEDs). The OS's own notices (the android and SystemUI packages) give
  no tally or LED (27e5352e94); they keep their status bar icons, and screen
  recording keeps its capture chip. Their security review (paused) found that
  the removable items' settings were read from credential-encrypted preferences
  through a real app context while the taskbar builds its device profile before
  the first unlock. That would crash-loop Launcher on every boot until then and,
  since Launcher's taskbar is the phone's navigation bar in this build, leave no
  Home or Recents before the first unlock (-139, high if shipped, never built;
  fixed on tally-home f885161fcb and Launcher3 android17 240e56d46f, which read
  them through the device profile's own injected preferences). The rest of the
  review found no issue: no path reaches the model, the database, uninstall or
  app info; nothing new is exported; no manifest or permission change; only
  system-uid or SystemUI code can post as the android or SystemUI packages. This
  set the credential-encrypted storage rule under [Rules learned from
  findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings).
- **2026-09-30:** the Files review fuzzed the archive code and reviewed trash
  (-140 to -143): the new archive code goes on (vendor_diamaneos local
  files-flags); trash stays off. Home's tallies and keycap LEDs count only
  things with a live readout or an activity in progress (owner, 30 September),
  never permanent background services.
- **2026-09-30:** the Calculator fork and the recovery title fork (local tally
  branches, not built): no manifest change in Calculator; recovery changes two
  string literals only. Notification rows get themed app icons (vendor_diamaneos
  release config, the flag android.app.notifications_redesign_themed_app_icons,
  read only by SystemUI's notification icon provider).
- **2026-09-30:** the Clock fork (local tally branch, emulator-checked, in a
  2026-09-30 test build) and -144 (stock SystemUI: promoted chips show private
  content over an occluded lock screen; open).
- **2026-09-30:** -144 reproduced on the FP6 with a test app (a Live Update's
  private chip text shows over an app that occludes the lock screen on a locked
  phone; GrapheneOS's chip code, not Tally's); a fix is being written as an
  upstream patch. Home's tallies read Clock's MetricStyle time with plain Bundle
  getters only, and the media-session read no longer throws on a malformed value
  (-145, fixed before any build). This set the notification-extras rule under
  [Rules learned from
  findings](THREAT_MODEL-TALLY.md#rules-learned-from-findings).
- **2026-09-30:** -144 fixed (frameworks_base tally-build 7ea6b864d39e; the same
  change on the owner's fork, branch chip_leak, for GrapheneOS): a promoted chip
  whose notification the lock screen redacts shows only the public version's
  short text or the icon, also while the lock screen is occluded; notifications
  the lock screen hides give no chip. Its sweep left two low stock items open
  (-146: status bar notification icons are never redacted; -147: always-on
  promoted notifications with a trust agent and a separately locked work
  profile, and dream chips upstream).
- **2026-09-30:** the security review of the Clock, Calculator, recovery title,
  Files and keyboard forks. Calculator, recovery and vendor_diamaneos are clean,
  and the keyboard's password and no-learning handling holds. Three findings,
  fixes being written: -148 (medium if shipped) and -149 (low) in the Clock fork
  (see [Clock](THREAT_MODEL-TALLY.md#clock)), and -150 (low) in the keyboard
  (see [Typed text and personal
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
  enforcing policy and clock daemon passed the owner's tests on 2026-09-27. The
  keyboard fork's -150 fix (deletion at LOCKED_BOOT_COMPLETED) is verified on
  the FP6, and the fork is in the manifest. The Tally switch lamps note that the
  FP6 has no Wi-Fi hotspot.
- **2026-10-01:** current state only: version names, product-boundary text, step
  labels, unbuilt and deferred features, release gates and the next-validation
  list were removed; the threat model now shows only what is built, and every
  built feature updates it in the same change.

## IMS integration notes

These notes record how the IMS containment evolved. The current state is in [IMS
status](THREAT_MODEL.md#ims-status-2026-09-28).

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
