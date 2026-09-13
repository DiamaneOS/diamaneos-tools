---
name: Facet comparative design study
description: Three implemented proposals; no replacement direction has been adopted.
colors:
  still-light-bg: "#edf1ea"
  still-light-surface: "#f8faf5"
  still-light-field: "#e0e7dc"
  still-light-line: "#c9d3c7"
  still-light-ink: "#24372b"
  still-light-muted: "#526550"
  still-light-accent: "#335e45"
  still-light-on: "#fff"
  still-light-tonal: "#d0e2cb"
  still-light-hero: "#c8d7bd"
  still-light-hero-ink: "#263d2d"
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
  contour-light-bg: "#f9eee8"
  contour-light-surface: "#fff7f2"
  contour-light-field: "#eeded5"
  contour-light-line: "#d9bdb1"
  contour-light-ink: "#382d29"
  contour-light-muted: "#70574d"
  contour-light-accent: "#a43e2d"
  contour-light-on: "#fff8f3"
  contour-light-tonal: "#f5caba"
  contour-light-hero: "#eea18e"
  contour-light-hero-ink: "#4c241d"
  still-dark-bg: "#17231e"
  still-dark-surface: "#202f28"
  still-dark-field: "#293b30"
  still-dark-line: "#3d5345"
  still-dark-ink: "#ecf1e8"
  still-dark-muted: "#b2c3b3"
  still-dark-accent: "#b5d4aa"
  still-dark-on: "#163322"
  still-dark-tonal: "#334f3b"
  still-dark-hero: "#334b35"
  still-dark-hero-ink: "#d6e7c9"
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
  contour-dark-bg: "#281f1e"
  contour-dark-surface: "#352925"
  contour-dark-field: "#49342e"
  contour-dark-line: "#65483d"
  contour-dark-ink: "#ffefe5"
  contour-dark-muted: "#d2b6a8"
  contour-dark-accent: "#ffb39c"
  contour-dark-on: "#54271c"
  contour-dark-tonal: "#63382b"
  contour-dark-hero: "#a4513d"
  contour-dark-hero-ink: "#fff2e8"
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
# Facet design reference — comparative proposals

## Overview

**Creative North Star: "A familiar phone. A different feeling."**

This file records the implemented Still, Lumen, and Contour comparisons. It does not select a new DiamaneOS identity or supersede the six-journey baseline. Lumen is the review recommendation; adoption remains a user decision. Tokens here apply only to the named proposal in `design/prototypes/facet-directions.*`.

The existing baseline remains `design/prototypes/diamaneos-ui.html`, with its
original tokens, motion notes, and journey annotations. `docs/UX.md` is a
revisable product proposal. Authentication and service behavior require their
own task decisions; a visual study does not establish those capabilities.

**Key Characteristics:**

- Recognisable Android destinations and visible search.
- Three distinct compositions using the same sample tasks.
- State and recovery that remain understandable while navigating.

## Colors

Still uses sage and forest tones. Lumen uses porcelain and cobalt. Contour
uses coral and ink. Each has its own light and dark role set above. `bg` is
the phone background, `surface` groups content, `field` holds controls, `ink`
and `muted` carry text, `accent` and `on` indicate active actions, and `hero`
and `hero-ink` form the Home or profile field. These names map directly to the
CSS properties on each phone. Shared error roles remain separate from branding.

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

Each proposal contains Home, shade, Settings, and sample supporting screens.
Desktop phones are at most 390 px wide and 780 px tall, with internal scrolling.
The browser study switches from three columns to one at 820 px. At 420 px and
below, phones are 760 px tall. Insets vary between 16 and 22 px. Phone chrome
and internal content are separate so larger content does not grow the device.

Still uses a labelled Home list and compact shade. Lumen uses an app grid and
grouped settings. Contour uses a stacked clock, a large colour field, and
unboxed rows. These differences are proposals to compare, not rules to combine.

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
- **Motion:** 230 ms shade entry, 220 ms shade exit, 170 ms page movement, and
  120 ms control transitions. Reduced motion disables these animations. No
  frame-time, haptic, or native gesture quality is implied by these durations.

The sidecar contains representative Lumen components for inspection. It does
not imply that Lumen has been adopted. Literal snippet values reflect the
phone-scoped CSS; the prototypes remain the runnable source of truth.

## Do's and Don'ts

- Do preserve the previous baseline while comparing proposals.
- Do distinguish requested changes from confirmed state.
- Do retain labels, focus, Back context, and recovery at larger text sizes.
- Don't treat a recommendation as an adopted visual identity.
- Don't turn browser measurements into claims about native Android performance.
- Don't assume every third-party app shares the OS component system.

See [the review](2026-09-13-facet-review.md) and
[verification](2026-09-13-facet-verification.md) for evidence,
tradeoffs, limits, and remaining direction selection.
