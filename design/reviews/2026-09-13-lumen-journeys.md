# Lumen selected and extended — 13 September 2026

The owner chose Lumen after reviewing three interactive alternatives. The
[six-journey reference](../prototypes/facet-lumen.html) now extends that direction
across setup, discovery, app privacy, notification recovery, battery/health,
and updates, with supporting Settings, Files, and permission screens.

This is the current browser design reference. It does not establish a native
implementation, device capability, security qualification, or ordinary-user
acceptance. The original baseline and three-direction comparison remain.

## What changed

- Preserved Lumen's porcelain/cobalt roles, visible search, app grid, and grouped
  Settings. Different tasks use different emphasis within that system.
- Setup now practises the complete six-word public fixture, including incorrect
  order and failure recovery, without accepting a real credential.
- Privacy starts with the current preset. Custom requires an explicit choice.
  The preview identifies changes and retained selections; partial failure keeps
  successful changes and offers targeted retry or Return to app.
- Notification troubleshooting separates permission enabled from delivery
  confirmed, identifies the app/profile, and handles stopped Work with an honest
  authentication handoff instead of a simulated unlock.
- Battery separates requested and confirmed limits, preserves confirmation on
  failure, and provides health/history and a scoped export preview.
- Updates expose identity, download, verification, installation, restart, and
  completion. Damaged downloads fail before installation. Progress continues
  offscreen and Settings reflects the stage.
- Decision sheets retain state on cancellation, contain keyboard focus, and
  close through phone Back or Escape. Shell motion is directional; reduced
  motion retains immediate state changes. Updater ticks update progress in
  place, and stage changes preserve the focused action when it still exists.

The direction is recorded in [UX](../../docs/UX.md), [DESIGN.md](../../DESIGN.md),
[journey annotations](../lumen-journeys.md), [palette](../prototypes/lumen-palette.json),
and [motion guidance](../prototypes/lumen-motion.md). Earlier design documents
were archived and historical journey notes link to the new reference.

## Credential correction

The preceding review's P1 fingerprint conflict was too strong and is withdrawn.
FP6-071 explicitly preserves inherited biometric and strong-auth semantics while
enforcing the accepted policy for normal primary credentials. Passphrase-only
primary credentials do not categorically prohibit optional supported fingerprint
use. Setup now explains required passphrase entry after restart/strong-auth
events and qualifies fingerprint support. No authentication policy was changed.

## Direct browser verification

The main walkthrough exercised:

| Journey | Observed result |
| --- | --- |
| Setup | Public reveal worked. A wrong six-word order was rejected; the correct complete order reached practice completion with an explicit no-credential-created statement. |
| Files | Notes.txt opened through Files. Its share preview named Notes.txt, had no recipient selected, and closed with Escape. The preview uses the selected file name rather than a fixed document label. |
| Privacy | Standard was the initial choice. Untrusted preview showed Network Allowed → Blocked and other controls unchanged. Custom had no replacement selected. In the failure case, Network remained Blocked while Sensors became Allowed; retry completed the remaining change. |
| Notifications | Disabled Personal notifications were enabled without claiming receipt. The separate sample test then showed delivery. Stopped Work linked to its own profile/authentication explanation; Escape dismissed the handoff. |
| Battery | Requesting 85% retained the confirmed 80% after failed Apply; Retry confirmed 85%. Health/export identified sample scope, and export completion explicitly wrote no file. |
| Updates | Download/verify/install stages appeared. The failure fixture rejected a damaged download before apply. Fresh-copy retry continued while away from the updater. Later cancelled restart without losing readiness; confirmed restart changed only the sample current build. |
| Keyboard update continuity | Focus remained on Later across Downloading → Verifying. Enter then returned Home. Progress ticks no longer replace the focused control. |

The first stress pass visited all six entry screens in dark mode with 200%
German draft text at a 390 px browser viewport. Each phone had 360 px content
and viewport widths, with no page or internal horizontal overflow. Content
scrolled vertically within a fixed 780 px outer phone. Reduced motion was on.
This pass exposed a malformed battery container, corrected before completion.

Other first-pass corrections were the ambiguous Cancel label after partial
permission mutation, disabled physical Back during a sheet, and stale Settings
update status. The corrected battery composition was also directly re-inspected
at normal size. Screenshots were viewed in the conversation, not saved as a
repository capture bundle.

## Independent confirmation

A fresh reviewer used live browser interaction in a separate context. It found
no P0–P3 defects in the specifically requested corrections:

- Battery layout at 100% and 200% German/dark; vertical scroll without horizontal
  overflow (388 px content and viewport in that desktop phone).
- Return to app after partial privacy failure preserves the actual Network,
  Sensors, file, and contact state.
- Restart and location sheets close through Back and Escape; browser focus
  returns to the respective invoking button.
- Settings changes from Update in progress to Ready to restart while staying
  on Settings.
- Restart and location sheets fit at 200% RTL/dark with their actions reachable.

This was an independent live-browser confirmation, substituting for the packaged
capture-based finish-review workflow. That packaged gate was not run; no capture
bundle or blanket approval of every possible state is claimed. The subsequent
updater keyboard-focus improvement was checked directly as described above.

## Mechanical checks and preservation

- `node --check design/prototypes/facet-lumen.js` passed after the final code edit.
- `git diff --check` passed during final verification. The palette and design
  sidecar parse as JSON.
- The final browser interaction returned no warning/error console entries.
- One Impeccable detector run returned 24 advisories about design-record colours,
  type sizes, and radii. These include the separate review controls, added
  choice/sheet shapes, and a default black reported during HTML analysis.
  Applicable roles/shapes were documented; the inherited stylesheet is imported
  by the extension. No second detector scan or injected overlay was used.
- Scoped text-role calculations include light muted/tonal 4.5018:1,
  muted/field 4.71:1, on/accent 5.83:1, and error/error-background 5.91:1;
  corresponding dark pairs are 5.25:1, 6.45:1, 8.07:1, and 7.00:1.
  These checks are not a complete accessibility audit.
- Original `diamaneos-ui.html` remains byte-identical to commit
  `49c92b9abe12f32f2f8dc95086e7bf276d99ac06`, SHA-256
  `c46317e4a4880736ca1a66fa5e9177279e46c0ab2280c8f87e26691c02d5593a`.
- Comparison JavaScript and styling were preserved. Its HTML gained a link to
  the selected six-journey extension. No native source or dependency changed.
- No commit, push, deployment, real device action, or external message occurred.

The existing loopback preview server remains available at port 8766. See the
[prototype README](../prototypes/README.md) to start it again. Detector output
is temporary at `/private/tmp/facet-lumen-detector.json`.

## Still requiring native and user validation

The browser does not prove Android gesture arbitration, predictive Back,
TalkBack/Switch Access, IME or system-bar insets, secret lifecycle protection,
font scaling, haptics, refresh rates, frame times, hardware acknowledgement,
notification compatibility, package trust, boot/rollback, or data retention.
German is partial draft copy; RTL is English layout stress, not localization.

Bind the selected surfaces to SetupWizard2, Launcher3, Settings, SystemUI,
DocumentsUI, the permission controller, and the existing updater/capability
owners. Prefer resource/component changes, justify layout/behavior patches,
and validate with ordinary Android users before broad native adoption.
