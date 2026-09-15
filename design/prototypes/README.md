# DiamaneOS interface studies

**Current direction: [Lumen, six journeys](facet-lumen.html), selected by the owner on 13 September 2026.**
See [journey annotations](../lumen-journeys.md), [palette](lumen-palette.json),
[motion](lumen-motion.md), and [verification](../reviews/2026-09-13-lumen-journeys.md).
The review rail resets a journey; ordinary navigation preserves state.

**Branding: [Lift](../branding/lift/preview.html), selected on 15 September 2026.**
The [brand guide](../branding/README.md) provides logo assets, banners, wallpapers,
and a browser boot-motion study. The Lumen reference header links to this kit;
the original UI and branding comparisons remain available.

## Preserved comparison and baseline

The [13 September review](../reviews/2026-09-13-facet-review.md) explores three
new interactive directions for Home, the notification shade, and Settings in
[facet-directions.html](facet-directions.html). The comparison records the alternatives before the owner selected Lumen. The original six-journey study below
is preserved. [baseline-review.html](baseline-review.html) adds the existing
stress controls without modifying its source.

The new comparison works as a standalone file with its adjacent CSS, JavaScript,
and icon assets. The baseline review wrapper requires HTTP. To serve both from
the repository root, run `python3 design/serve.py --port 8766`, then open
`http://127.0.0.1:8766/design/prototypes/facet-directions.html`.
The preview server declares UTF-8 for text resources, including linked Markdown,
so punctuation and mathematical symbols display correctly in the browser.

Open `diamaneos-ui.html` directly in a modern browser for the six documented
journeys. Keep its adjacent `assets/` folder: the bundled Lucide runtime renders
icons offline, including from a `file:` URL. No server, account, Android device
or real credential is needed. Icon provenance and licences are in
[assets/README.md](assets/README.md).

In a browser, **Preview theme**, above the phone, offers Follow system, Light
and Dark. Follow system responds to the operating system's appearance. Changing
theme preserves the current page and sample state; reloading resets everything.
This selector belongs to the preview, not the depicted operating system.

For a host with Lucide and optional design controls, extract the same study
without its standalone document and local script loader:

```sh
python3 design/prototypes/export-inline.py /path/to/preview.html
```

Run this from the tools repository; choose the output location. Edit
`diamaneos-ui.html`, then regenerate the host fragment with this command.
The exporter preserves the study verbatim between its boundary comments.

The gallery selects Home, Setup, Privacy, Notifications, Battery or Updates.
Ordinary controls inside the phone demonstrate the flows. Host design controls
offer Expressive/Quiet composition, Citrine/Iris/Glacier palettes, system/light/
dark appearance, reduced motion, text scaling, German draft copy, RTL layout
stress, and Normal/Failure/Unavailable/Loading/Empty scenarios. Selecting a journey or changing its scenario resets all fixture state,
including pending timers. Appearance controls preserve the current
page so treatments can be compared directly. Navigation keeps journey state.

Home has no permanent search bar. Pull down on its clock or empty middle space
to open app/settings search with the query focused. The accessible alternative
is All apps → Search. Home’s centred chevron opens All apps by tap, Enter/Space
or an upward swipe. The drawer rises from below and closes downward through
its top handle, a downward handle swipe, Back/Escape or Home.

The status area opens the shade by tap, keyboard activation or downward swipe.
It enters from above and dismisses upward with the centred chevron, an upward
swipe on its date/edge handle, Back/Escape or Home. Back restores the preceding
page. Home search enters from above and retreats upward. Ordinary nested pages
keep logical horizontal navigation. Gestures use these specific regions;
app buttons, settings sliders and ordinary content keep their own interaction.

Files contains two individually selectable sample documents. Meadow shows a
theme-aware independent messaging app; Atlas shows an independent app with
its own brand. The sample Meadow reply is local and sends nothing. These
illustrate two realistic levels of consistency without selecting bundled apps.

The Atlas permission sheet uses equally weighted Allow and Don’t allow buttons.
Location precision is labelled information, not a simulated input. Close/Back
cancels without changing the previous grant; Don’t allow explicitly clears the
sample grant. Presets, switches, navigational rows and read-only information
retain distinct roles while sharing the same components.

Every person, app named Meadow or Atlas, file, measurement, network and build
is a fixture. The prototype changes only its in-memory state. It does not
create credentials, alter permissions, write charging controls, export files,
download an update, or contact a service. Reloading resets it. The sample
passphrase is deliberately public and must never be used as a real credential.

The phone grows with content and large type instead of clipping into a fixed
device viewport. It is a composition model, not a pixel-accurate Fairphone 6
screenshot. Browser keyboard semantics are included; native TalkBack,
Switch Access, haptics, predictive Back and device performance still require
Android implementation and testing.

See [the design vision](../../docs/UX.md), [motion specification](motion.md),
[tokens](tokens.json), and [journey annotations](../core-journeys/README.md).

## Refined reference

Status: browser design refinement complete, 2026-09-11. The selected baseline
uses Expressive/Citrine with paired light/dark roles. Shared insets, symmetric
toolbar tracks, aligned groups, effective 48 px controls and contextual spacing
apply throughout. Commit actions use centred labels; navigation icons carry
specific meaning rather than appearing on every button.

Repeated setup/app context and design commentary have been removed from
ordinary flows. Public-word warnings and explicit simulated export/credential
outcomes remain where they prevent a false claim. At 150–200% text, headings
use the documented responsive bases, the clock stacks whole hours/minutes,
and apps, quick settings and words reflow into single-column layouts.

These values bind this reference. Native owners must map them to actual
resources and validate fonts, colours, fixed viewports, interaction and
performance. Sample battery ranges, preset effects, credentials and updater
success states are not production contracts. See [changes](CHANGELOG.md).

## Recorded browser verification

Chromium 151.0.7922.34 with Playwright 1.62.1 and real Lucide 1.8.0 rendered the
actual standalone file and extracted fragment. A local host adapter supplied only optional design-control
object bindings; DOM, layout, focus, pointer input and animations were real.
Remote network access from test pages was blocked. Direct-file checks loaded
the adjacent icon bundle without a host adapter. This supersedes the first
preview’s source-only/stubbed-DOM checks and the later host-only icon checks.

- Twelve browser checks covered icon rendering, the six success/recovery flows, modal
  keyboard containment and focus return, touch/mouse swipes, immediate
  follow-up actions, interrupted motion, requested/confirmed battery state,
  verification-before-install, timed updater focus, fixture reset, independent
  app boundaries, and markup/long search input.
- Eight follow-up checks covered the direct `file:` URL, offline icons, live
  system/light/dark selection, state preservation, middle-versus-edge gestures,
  query focus, explicit denial versus cancellation, equivalent permission
  buttons, host-fragment parity and actual accessibility-tree exclusion of
  transient copies and underlying modal content. Fourteen paused transition
  captures verified the moving surface and axis, including Back/Home and RTL.
- Geometry checks covered 1,080 combinations: six journeys × five scenarios ×
  three text sizes × three language/layout modes × two appearances × two
  browser widths (320 and 736 px). Home failure states were inspected in Search.
  Checks covered visible text/control bounds, text within its parent, minimum
  control sizes and horizontal overflow.
- Screenshots and semantic snapshots covered 121 states, including supporting
  pages and sheets, failures and narrow 200% German/RTL flows. The six primary
  journeys and supporting success/recovery screens were visually inspected.
  Browser accessibility-tree inspection confirmed that an open permission dialog
  excludes underlying phone controls; outer prototype journey controls remain
  separate from the depicted OS. Dark phone previews retain readable gallery
  controls when the preview host itself uses light appearance.
- Browser checks passed with no recorded layout or interaction failures.
  Semantic snapshots and keyboard checks do not establish spoken screen-reader
  behavior. German remains draft copy; RTL uses English strings.

To repeat the review in a preview host, follow each route in the
[journey index](../core-journeys/README.md), then repeat at 150/200% text,
German/RTL and light/dark. Inspect every changed screen and sheet, navigate
without gestures, cancel/retry errors, and return while the updater runs.
Check visible geometry in addition to source/state assertions.

Native fixed-viewport, TalkBack/Switch Access, critical translation, hardware
state, Android Back, haptic/performance and intended-user usability checks
remain with the named component/integration owners. They are not browser
results or prerequisites for completing this mockup refinement.
