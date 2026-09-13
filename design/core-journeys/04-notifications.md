# 04 — Notifications

> Historical baseline annotations. The selected Lumen journey reference is
> [documented here](../lumen-journeys.md) and [interactive here](../prototypes/facet-lumen.html).

Refined browser reference; native implementation remains pending. Owner: notification compatibility; shell composition candidate native UI integration.
Pages: `notifications`, `diagnosis`, `profile`.

## Intent and inherited comparison

Entry: shade or app notification settings. Goal: understand a missing
notification and change only what is relevant. Retain channels, profile
lifecycle and app-specific delivery state. The design connects cause to
scoped action and explores the shared language in the notification shade.

## Path and states

Meadow / Find out why → confirmed app notification block → Turn on → sample
test → received. This does not imply the OS can originate arbitrary apps'
tests. Native retest must use a supported path or ask the user to trigger an
event; distinguish permission enabled from delivery confirmed.

| State | Behavior | Recovery |
| --- | --- | --- |
| Loading | Checking with exit | Back/Home |
| Confirmed cause | Meadow notifications disabled in Personal | Change only that app |
| Enabled | No delivery claim yet | Supported test / event |
| Complete | Fixture received | Return |
| Failure | Work profile stopped | Profile settings → Personal |
| Unavailable | No confirmed cause | Inspect app controls or Retry |
| Empty | No entries is not automatically an error | Settings/troubleshooting stay reachable |

Stopped Work leads to a passphrase-required handoff and safe Personal exit,
not a fake unlock. Native continuation authenticates in that profile and
rechecks delivery. Channels, per-app background limits and supported
distributor state must be diagnosed rather than inferred from silence.
Compatible delivery mechanisms are not universal support.

Open the shade by pulling down on the status area or tapping/activating its
labelled button. Dismiss using the centred upward chevron, an upward swipe on
an edge handle or the date area, or Back/Escape. Dismiss returns to the previous page and its
invoking control; entering troubleshooting creates a normal page whose Back
returns to the shade. The shade enters downward from above and exits upward,
including Back/Home; it never uses the horizontal page-dismiss transition.
The Home middle pull-down opens Search instead. The shade has no
page-style Back button or redundant bell in its header.

Back preserves unrelated settings. No global exemption or disabling of
security/battery policy follows from diagnosis. Quick settings and brightness
only change local prototype state.

## Focus and locale

Shade reading order: status → date/Settings → connectivity → brightness →
notification entries/troubleshooting → dismiss → Home. Keyboard order follows
the actionable elements; the date is static. Diagnosis: Back → app/profile →
cause → action → retest. Empty/loading shades keep quick settings and dismiss
available; Settings still leads to app notification controls. State changes
and test receipt are announced. Text expresses cause and consequence.

Large text stacks connectivity controls. German wraps, RTL mirrors. Native
UI must keep accessible dismiss/expand actions and gesture alternatives;
this study does not reproduce the whole notification interaction model.

## Implementation mapping

Resources/shared Settings components: copy, diagnostic states and scoped
links. Narrow SystemUI candidate: shade/quick-setting composition, justified
only after resource alternatives and baseline comparison. native UI integration owns
maintenance/regressions. No universal delivery service or always-on reader.
