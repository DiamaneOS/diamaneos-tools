# DiamaneOS Threat Model: Tally Shell Rules

Per-component rules for the Tally shell, part of the [threat model's Fresh-UI
boundary](THREAT_MODEL.md#fresh-ui-boundary). Every Tally shell commit (FP6-211)
keeps them, together with the [privacy indicator
rules](THREAT_MODEL.md#privacy-indicators-and-disclosures-under-the-tally-shell)
and the [rebase
rule](THREAT_MODEL.md#shell-fork-rebase-lag-grapheneos-security-fixes-under-the-tally-shell).
This file follows the threat model's [maintenance
rules](THREAT_MODEL.md#how-this-document-is-maintained).

## Lock screen and biometric prompt

- The bouncer's window, state machine, input handling, system keyboard, key
  order, GrapheneOS's PIN scrambling, lockout, throttling, the duress and wipe
  counters, and protection from screenshots and screen recording stay
  GrapheneOS's. No custom keyboard or show-passphrase key without a reviewed
  decision.
- The bouncer's look changes only through resources and visual-only code
  behind the Tally flag, and that code only draws: key shape and corner radius,
  digit and message typeface, password field and button styling (including the
  field's fade while entry is off).
- The biometric prompt keeps the credential fallback, confirm-required, cancel
  on back and outside tap, and obscured-touch protection. The power menu keeps
  Lockdown and its lock-screen filtering. The lock-screen camera key opens only
  the secure camera.
- Keyguard, bouncer and biometric commits get two reviewers and the keyguard and
  biometric CTS sets on the FP6.

## Transitions

Tested at animation scales 1x and 0.5x:

- While locked or with an app over the lock screen, back and close lead only to
  the lock screen, never Home or an app snapshot (tested from the secure camera
  and a ringing alarm).
- The shell's input consumer takes the catch gesture, so touches never reach an
  animating app.
- Permission-grant, restricted-setting, package-installer, USB, biometric and
  bouncer windows keep stock or shorter enter animations.

## Quick Settings and the shade

- Tally tiles, including the first pull's keycaps, reuse upstream click,
  secondary-click and long-click handlers, so a tile needing unlock still asks.
- The security footer shows under upstream's conditions with its VPN or
  management icon; the shade header's privacy container keeps platform privacy
  data and its tap to the stock privacy dialog.
- Notification cards change drawing only: which notifications show,
  private-content and work-profile redaction are untouched; no live lamp on a
  redacted notification. The media card drops album art (less on the lock
  screen, not more).
- Heads-up notifications change only motion, edge and shadow; what they show,
  when and how long, gestures, full-screen intents and lock-screen redaction
  stay stock. One with a status bar chip keeps stock's reveal, never covering
  the chip; a leaving one is fully off screen before removal.
- Camera and microphone access tiles light their lamps at once, like sensor
  lamps, but keep tile colours: sensor green is reserved for sensors in use.
- Visual-only side effect: Quick Settings dialogs, the privacy dialog included,
  get a 20 dp corner radius.

## Recents' Stop

- SystemUI tells only the current user's recents app, checked by uid, which apps
  the Active apps dialog would let the user stop (package and user, current user
  and its profiles, in memory only); Launcher3 gets no new permission.
- Every Stop re-runs the dialog's checks at that moment: lock screen dismissed,
  foreground service running, user in the current profiles, and no platform
  exemption hiding the dialog's Stop button (system uid, system allow-list,
  device and profile owners, protected and device-admin packages, persistent
  processes, default dialer, system modules; the platform's list of stoppable
  system apps is the exception). Only then does SystemUI ask the platform to
  stop the app, as the dialog does.
- The two new SystemUI-proxy methods accept calls only from the recents app's
  uid in the current user and do nothing with the Tally flag off.
- Launcher3 sets SystemUI's listener only while Recents is open and removes it
  on close, keeps the list in memory only, offers Stop only for a card the user
  swiped away, named by that card's own task (package and user), and never stops
  an app itself; its manifests, permissions and privileged-app allow-list stay
  upstream. The Stop key and row refuse touches through another window over them
  and ignore taps within 0.5 s of the row appearing or changing.
- Limits:
  - SystemUI cannot tell whether, or on which app, the user tapped Stop and
    trusts the recents app for that.
  - The recents app can already stop apps in its own user with its own
    permission; through SystemUI it can also stop dialog-stoppable apps in the
    current user's work profile or private space, and learns which run a
    foreground service.
  - An app with two exemption reasons (for example a carrier-privileged device
    admin) can get a Stop button, in the dialog and Recents alike.
  - Stop sits in the launcher's window, where app overlays can cover it; a
    window over another part of the launcher (picture-in-picture, a chat
    bubble) does not block a tap on it, since refusing those would break Stop
    whenever one shows.
  - The listener binder is not caller-checked (like Launcher's other SystemUI
    listeners), but a forged list only decides which swiped apps get a row, and
    SystemUI re-checks every Stop.

## Settings switches

- Restyled only through layouts and drawables, never logic (obscured-touch
  protection, administrator- and restricted-setting-disabled states), with those
  states tested.
- SettingsLib's restyle changes theme resources and adds Tally switch views
  (subclasses of the switch classes preferences already bind, and a Compose
  twin) that change only drawing and motion; checking, touch handling,
  accessibility and the enabled state stay the parent classes'. Disabled
  switches draw at 38 % in their real position. Rows and cards change only
  through layouts and drawables.
- The SettingsLib restyle is not behind the SystemUI Tally flag and reaches
  every SettingsLib app, including emergency alerts, whose switch logic is
  untouched (presidential alerts stay on). SystemUI's own switch screens use the
  Tally switch only through flagged layout twins and, on Compose screens,
  flagged calls with Material's arguments and accessibility semantics, so with
  the flag off SystemUI is unchanged.
- Switches wait for the system: the thumb moves at the tap; the lamp lights only
  while the switch is checked and the setting's own state reports on (Wi-Fi,
  Bluetooth, hotspot and tethering, NFC, Battery Saver, from state their
  controllers already read; the FP6 has no Wi-Fi hotspot, so that lamp stays
  dark there), so no lamp shows on for something off. Turning off darkens it at
  once; a failed change goes back unlit and accessibility reports the checked
  state, both as stock.
- Switch rows lose their own ripple and focus highlight (the switch shows both).
  Restricted switches keep their code, including window-wide obscured-touch
  filtering.

## Home and All apps

- Keycap LEDs and the tallies row come only from Launcher's existing
  notification listener (behind notification dots), in memory: flags, category,
  channel, importance, the shade's visibility rules, and system-drawn progress
  bars and chronometers; never titles or texts.
- An LED lights only for a live notification (running: foreground service, Live
  Update or ongoing, showing a live readout or activity: system chronometer,
  progress bar, call, navigation, stopwatch, Live Update, playing media;
  permanent background services give nothing) or a failed one (error
  notification), and respects the app's dot setting; the Notification dots
  switch turns both off.
- Nothing the shade hides shows (suspended apps, Do Not Disturb list
  suppression, with SystemUI's exemptions). The OS's own notices (the android
  and SystemUI packages, postable only by system-uid or SystemUI code) give no
  tally or LED.
- One attribution exception: the preinstalled system Clock
  (com.android.deskclock with FLAG_SYSTEM or FLAG_UPDATED_SYSTEM_APP, checked on
  its ApplicationInfo, never the package name alone) shows "Timer" or
  "Stopwatch" instead of its name while a count-down or count-up time shows,
  decided from structure only; screen readers still name the app, and every
  other app keeps its name, so none can pose as the system timer.
- App names show on the unlocked Home only. Work apps show name and lamp but no
  readout (a separately locked work profile is redacted in the shade, and
  Launcher cannot see that lock without a new permission); the private space
  never appears. Sensors in use, screen capture and requested or on states are
  left out (Launcher would need new privileges).
- The All apps letter rail uses stock's sections, so a hidden private space gets
  no slot. No permission, manifest or allow-list change.
- The default Colour icon style gives a key in its own colour only to the system
  apps in a fixed list, checked by package name and system-app status, so an app
  installed under a listed name keeps its own icon and cannot take a system
  app's key; every other app keeps its own icon. Launcher keeps the chosen style
  and writes it to the Secure setting tally_icon_style with the
  WRITE_SECURE_SETTINGS it already holds; SystemUI reads it to draw notification
  icons the same way. The setting names only a look (apps that can read it learn
  the chosen icon style) and an unknown value means Colour. No permission is
  added.
- The default wallpaper, Paper, is a SystemUI wallpaper service that only draws:
  no permissions, no input, bindable only by holders of BIND_WALLPAPER (the
  system and the wallpaper picker). The lock screen draws no dim over it; other
  wallpapers keep stock's dim.

## Settings search

SettingsIntelligence's panel is restyled and its target SDK goes from 31 to 37
with predictive back. Every platform behaviour change in between was checked
against the app (173 checked, 22 apply, none changes what it can do; the
explicit-intent filter checks are not tied to the caller's target or are
disabled at the pin). No permission or component change; the search index, its
providers and permissions are untouched; results are unchanged.

## Clock

DeskClock fork: GrapheneOS's Clock plus 201 reviewed commits, 19 of them
DiamaneOS's own (the original authors stay in the fork's history); targetSdk 37.

- Exported components go from 7 to 4 (launcher entry, SET_ALARM-guarded API
  activities, screensaver); the alarm init receiver and both widget providers
  are no longer exported; the new widget set-up activity is unexported and
  accepts only this app's digital-clock widget ids; widget intents are explicit;
  every runtime receiver is RECEIVER_NOT_EXPORTED.
- GrapheneOS's e16191c4 (no foreign snooze or dismiss) and 9cde4084 stay.
- Permissions: the approved set (notifications, promoted notifications,
  USE_EXACT_ALARM, one foreground-service type); DISABLE_KEYGUARD,
  READ_EXTERNAL_STORAGE and legacy ones dropped; POWER_OFF_ALARM and
  MODIFY_AUDIO_SETTINGS never added. An expired timer no longer asks to dismiss
  the keyguard. Backup and device transfer stay off; alarms rely on GrapheneOS's
  power-save allowlist as before.
- Timer and stopwatch are promoted MetricStyle notifications (status bar chips);
  being public, they show on the always-on display under the lock-screen
  notification filter (see -144 for chips over an occluded lock screen).
- Residual: the import was reviewed on its security paths (manifest, receivers,
  services, public intent API, widgets, notifications), not every UI line; four
  imported commits are marked tool-assisted (notification styling, a channel ID,
  the promoted-notification permission, icons; no security effect).
- Fixed review findings:
  - -148 (medium): only audio modes needing MODIFY_PHONE_STATE count as a phone
    call. Modes any app with the normal MODIFY_AUDIO_SETTINGS can set do not, so
    another app cannot stop a ringing alarm or turn alarms and timers down to
    the in-call tone, and a call that only rings does not silence an alarm.
  - -149: the alarms database stays at version 8. Version 12, which
    GrapheneOS's Clock cannot open, would make going back silently stop alarms;
    earlier builds' databases go back to version 8 with their alarms.

## Calculator

ExactCalculator fork: styling (DayNight with dynamic colour, Tally keycaps from
the token library, resources only), AC and "( )" keys, edge-to-edge, key labels
that grow with text size. Manifest unchanged: no permission, component, backup
(allowBackup stays false) or storage change. The keycap drawable is inflated by
class name only from the app's own resources.

## Recovery and fastbootd titles

The bootable_recovery fork changes exactly two title literals ("DiamaneOS
Recovery", "DiamaneOS Fastboot"); compiled code otherwise matches the pin.
Recovery runs as root and wipes, sideloads and verifies packages, so every
GrapheneOS rebase of the fork is reviewed as a diff that must stay those two
literals. Signature checks against otacerts and GrapheneOS's recovery
restrictions (no SD-card entry, serialno-constrained updates rejected, no serial
number shown) are unchanged.

## Motion: WM Shell

- Behind the Tally flag, pages opening and closing inside and between apps,
  predictive Back between activities and tasks, and the cold-start splash change
  how they move, never what opens or closes.
- Tally motion applies only where stock would play the framework's default
  animation for every moving window of a full-screen transition. Stock keeps:
  transitions with a keyguard or always-on-display flag (locked, occluding,
  unoccluding, appearing, going away); Recents, dream, wallpaper and Home
  (Launcher's) transitions; translucent windows (every permission, install and
  USB dialog); multi-window, freeform, letterboxed and embedded activities;
  apps' own transitions; consent and credential screens named in the code
  (package installer, VPN and credential dialogs, all SystemUI activities,
  permission grant, restricted settings, permission review and role request,
  device-admin activation, credential confirmation).
- Shorter than stock: open about 310 ms, close about 190 ms, against 450 ms. The
  splash shows the app's keycap and a static lamp and leaves only once the app
  has drawn, never a blank window.
- Page transitions are jank-monitor interactions: an event-log line with the
  interaction type, times and a constant tag, no app identity, as stock's.

## Motion: Launcher

- Launching from a key, the return to Home and Back to Home change how the
  window moves (grows out of its key and flies back on the Tally springs; Home
  dims; the key's neighbours part), never what opens, closes or goes Home, or
  when: the gesture's end target and the Back trigger stay GrapheneOS's.
- Only on an upright phone with gesture navigation and one full-screen task;
  split screen, desktop windows, a trackpad, picture-in-picture and landscape
  keep stock. The recents input consumer, keyguard paths and Launcher's start
  paths (quiet-mode profiles, private space, disabled apps) are unchanged.
- A tally opens its app through Launcher's own start path with the app's
  launcher intent (never the notification's), so it is logged like any Home
  launch (Launcher's standard launch event); no new log or permission.

## Settings homepage

Regrouped and restyled only. Every top-level entry keeps page, controller,
conditions and restrictions (the pin's 30 entries, compared by key and
attribute), including GrapheneOS's own, work profile, private space and every
security and privacy entry. State lamps show only what the controller already
reads (location on, a mode active), beside the summary that says it in words,
never beside an administrator's restriction text, with no new read or listener.
The bottom search opens Settings search under stock's conditions (hidden before
setup, in setup flows and where search is off for the user). About phone gains a
static mark and a "based on GrapheneOS" line. Stock's hiding of non-system
overlays stays; nothing new is logged or stored.

## Branding

The system label is the literal "DiamaneOS" (apps can read it; it reveals only
the OS name, as GrapheneOS's did). The fallback boot logo, shown only without a
boot animation, is the DiamaneOS mark. Neither carries state.

## Rules learned from findings

Findings are described in [THREAT_MODEL-HISTORY.md](THREAT_MODEL-HISTORY.md).

- Tally code must read SystemUI and token resources only through SystemUI's own
  contexts, never a view inflated from another package (-135).
- Tally code in Launcher must not open credential-encrypted storage on paths
  that run before the first unlock (-139).
- Every value Home reads from notification extras is app-controlled and must be
  read without a throwing path (-145).
