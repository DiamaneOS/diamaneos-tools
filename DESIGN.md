---
name: Facet — Lumen with Lift branding
description: Selected Lumen browser direction and Lift brand reference; native integration remains pending.
colors:
  preview-muted: "#56635a"
  preview-selection-ink: "#254d9a"
  preview-ink: "#4b5951"
  lumen-light-bg: "#f2f5fa"
  lumen-light-surface: "#fff"
  lumen-light-field: "#e6ebf4"
  lumen-light-line: "#d0d8e5"
  lumen-light-ink: "#1e2a40"
  lumen-light-muted: "#5c687c"
  lumen-light-accent: "#305dd0"
  lumen-light-on: "#fff"
  lumen-light-tonal: "#dbe6ff"
  lumen-light-hero: "#d8e5ff"
  lumen-light-hero-ink: "#244fa2"
  lumen-dark-bg: "#151e30"
  lumen-dark-surface: "#202c42"
  lumen-dark-field: "#2b3952"
  lumen-dark-line: "#42526c"
  lumen-dark-ink: "#edf1fc"
  lumen-dark-muted: "#b5c2d8"
  lumen-dark-accent: "#acc8ff"
  lumen-dark-on: "#152d57"
  lumen-dark-tonal: "#30466d"
  lumen-dark-hero: "#294577"
  lumen-dark-hero-ink: "#d7e6ff"
  error-light: "#99392e"
  error-bg-light: "#ffe6dd"
  error-dark: "#ffb9a9"
  error-bg-dark: "#552f28"
typography:
  body:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
  label:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "normal"
  still-title:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "27px"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  lumen-title:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "34px"
    fontWeight: 570
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  contour-title:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "39px"
    fontWeight: 720
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  still-clock:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "65px"
    fontWeight: 360
    lineHeight: 1.15
    letterSpacing: "-0.04em"
  lumen-clock:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "76px"
    fontWeight: 430
    lineHeight: 1.15
    letterSpacing: "-0.04em"
  contour-clock:
    fontFamily: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
    fontSize: "83px"
    fontWeight: 730
    lineHeight: 0.94
    letterSpacing: "-0.035em"
rounded:
  choice: "16px"
  sheet: "26px"
  welcome-mark: "30px"
  step-mark: "2px"
  preview-control: "10px"
  still-surface: "14px"
  lumen-surface: "18px"
  contour-surface: "8px"
  field: "12px"
  preview-phone: "34px"
spacing:
  phone-inset: "22px"
  large-text-inset: "16px"
  quick-gap: "10px"
  search-gap: "9px"
components:
  lumen-button-primary:
    backgroundColor: "{colors.lumen-light-accent}"
    textColor: "{colors.lumen-light-on}"
    rounded: "{rounded.lumen-surface}"
    padding: "12px 17px"
  lumen-search:
    backgroundColor: "{colors.lumen-light-field}"
    textColor: "{colors.lumen-light-ink}"
    rounded: "{rounded.field}"
    padding: "0 12px"
---
# Facet design reference — Lumen and Lift

## Overview

**Creative North Star: "A familiar phone. A different feeling."**

The owner selected Lumen on 13 September 2026. The six-journey extension in
`design/prototypes/facet-lumen.html` is the current browser design reference.
The scope is visual and interaction direction, not an adopted native OS build
or a capability/security qualification. The user remains open to improvements.

Lumen uses porcelain/cobalt surfaces, visible search, familiar app icons,
grouped Settings, and clear distinctions between requested and confirmed
state. Each task gets its own hierarchy within that vocabulary. The typography
and forms from the selected comparison remain the foundation.

The [three-direction design record](design/reviews/2026-09-13-directions-design.md),
[comparison](design/prototypes/facet-directions.html), and
[original baseline](design/prototypes/diamaneos-ui.html) remain available.
The Still/Contour type tokens above describe historical comparison variants;
Lumen is the active direction. Authentication and service behavior remain with
the actual feature contracts.

**Key Characteristics:**

- Recognisable Android destinations and visible search.
- Task-specific hierarchy in a coherent tonal system.
- State and recovery that remain understandable while navigating.

## Branding — Lift

Lift was selected on 15 September 2026. Its three asymmetric folded planes
form the DiamaneOS mark. Use the [brand guide](design/branding/README.md) and
[offline reference](design/branding/lift/preview.html) for logo spacing,
outlined wordmarks, paired colour assets, wallpapers, banners and motion.
The [source record](design/branding/lift/source.json) preserves the selected
geometry; the [earlier options](design/branding/archive/fold-options.html)
remain available for comparison.

DiamaneOS remains the public name. Facet, Lumen and Lift describe the interface
and branding work, not additional consumer products. The master logo remains
independent of the current device and upstream base. Source attribution stays
in the relevant project and technical records.

Lift's logo uses one colour; larger artwork may separate its planes with the
documented illustration tones. The brand does not replace status/error colours
or app identities. Instrument Sans is used for the brand lettering and preview,
with local font files and outlined export assets. Native Android typography
remains governed by the interface implementation. The Lift boot motion is a
browser reference, not an integrated native animation.

## Colors

Lumen uses porcelain and cobalt, with paired light and dark roles above. `bg` is
the phone background, `surface` groups content, `field` holds controls, `ink`
and `muted` carry text, `accent` and `on` indicate active actions, and `hero`
and `hero-ink` form the Home or profile field. These names map directly to the
CSS properties on the Lumen phone. Shared error roles remain separate from branding.

The light app identities remain constant across all proposals: Files blue,
Settings grey, Meadow green, and Atlas ochre. They illustrate independent app
recognition. The comparison host has its own neutral styling and is not OS UI.

## Typography

The browser uses its system sans-serif stack. The frontmatter records normal
desktop bases; it does not select a new font for native Android. Use the native
platform's font behavior when implementing, then verify weight and line breaks.
App labels use 11 px and Settings labels 14 px at 100%; supporting text uses
11–12 px. All three title and clock treatments are recorded separately.

At 150% and 200%, body text scales, the app grid becomes two columns (Still
remains a list), Quick Settings becomes one column, and title/clock bases
reduce before scaling. These browser stress controls approximate enlarged
text; they are not Android font-scale certification.

## Layout

The current reference contains all six journeys and supporting screens. Its
phone is at most 390 px wide and 800 px tall, with internal scrolling. Below
620 px, the review controls stack and the phone is 780 px tall. The rail and
controls rearrange at 900 px. Insets vary between 16 and 22 px. Preview chrome
and internal content are separate so larger content does not grow the device.

Still uses a labelled Home list and compact shade. Lumen uses an app grid and
grouped settings. Contour uses a stacked clock, a large colour field, and
unboxed rows. These historical comparison differences remain reviewable; they are not rules to combine.

## Elevation & Depth

The OS studies use tonal surfaces and dividers. Only transient feedback has a
small shadow (`0 6px 18px #0002`). The surrounding phone shadow is presentation
chrome, not an Android elevation token. Lumen's flat oblong is a wallpaper
motif, not an imitation material or a dependency on generated artwork.

## Shapes

Default surface corners are 14 px in Still, 18 px in Lumen, and 8 px in Contour.
Individual components vary: Still's Quick Settings uses 12 px, Contour uses
5 px, and Contour's Settings groups are square with a stronger leading divider.
Search fields use 12 px except Contour's 6 px. Shape alone does not communicate
state: controls also retain labels and pressed/checked semantics.

## Components

- **Actions:** 48 px minimum targets inside the phone. Primary actions use
  accent/on colours; secondary actions use field/ink. Focus uses a 3 px accent
  outline inset by 3 px. Press feedback uses a small brightness change.
- **Search:** visible Home entry and editable app/Settings search. Filtered
  Settings navigation restores the query and scroll position on Back.
- **Navigation:** three buttons for Back, Home, and Recents. The shade also
  has tap and directional swipe access. Browser controls are examples, not
  implementations of Android's native gesture navigation or predictive Back.
- **Quick Settings:** icon, label, state text, and pressed semantics. Brightness
  keeps its percentage on one line. Still prioritises density; Lumen uses larger
  vertical tiles; Contour uses a horizontal icon/label relationship.
- **Notifications:** expansion, app entry, dismissal, and Undo. Empty and
  connection-error states offer useful feedback without changing device state.
- **Battery:** requested and last-confirmed limits remain separate until Apply.
- **Motion:** 230 ms shell entry, 220 ms shell exit, 170 ms page movement, and
  120 ms control transitions. Reduced motion disables these animations. No
  frame-time, haptic, or native gesture quality is implied by these durations.

The sidecar contains representative components from the selected Lumen direction. Literal snippet values reflect the
phone-scoped CSS; the prototypes remain the runnable source of truth.

## Do's and Don'ts

- Do preserve the previous baseline and comparison when extending Lumen.
- Do distinguish requested changes from confirmed state.
- Do retain labels, focus, Back context, and recovery at larger text sizes.
- Don't treat the selected browser direction as a native implementation or qualification.
- Don't turn browser measurements into claims about native Android performance.
- Don't assume every third-party app shares the OS component system.

See [the review](design/reviews/2026-09-13-facet-review.md) and
[verification](design/reviews/2026-09-13-facet-verification.md) for evidence,
tradeoffs, limits, and implementation boundaries.

Current flow-specific shapes include 16 px phrase/choice containers, a 26 px
sheet top, and a 30 px welcome icon field. Sheets are immediate and protect
focus; they are used for restart, scope/export preview, and permission decisions.
See [six-journey annotations](design/lumen-journeys.md),
[the current motion reference](design/prototypes/lumen-motion.md), and
[verification](design/reviews/2026-09-13-lumen-journeys.md).
