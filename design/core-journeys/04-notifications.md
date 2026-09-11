# 04 — Notifications

Proposed interface mockup. Owner: notification compatibility; shell composition candidate native UI integration.
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

Back preserves unrelated settings. No global exemption or disabling of
security/battery policy follows from diagnosis. Quick settings and brightness
only change local prototype state.

## Focus and locale

Shade: navigation → connectivity → brightness → entries → troubleshooting
→ Settings. Diagnosis: app/profile → cause → action → retest. State changes
and test receipt are announced. Text expresses cause and consequence.

Large text stacks connectivity controls. German wraps, RTL mirrors. Native
UI must keep accessible dismiss/expand actions and gesture alternatives;
this study does not reproduce the whole notification interaction model.

## Implementation mapping

Resources/shared Settings components: copy, diagnostic states and scoped
links. Narrow SystemUI candidate: shade/quick-setting composition, justified
only after resource alternatives and baseline comparison. native UI integration owns
maintenance/regressions. No universal delivery service or always-on reader.
