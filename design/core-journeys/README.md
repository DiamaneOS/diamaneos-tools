# Six journeys — Facet interface study

Open `../prototypes/diamaneos-ui.html` and use its gallery. The standalone
browser provides Preview theme (system/light/dark); optional host design controls
select appearance, composition, palette, motion, text/layout stress and the
scenario. Scenario changes reset the current journey; ordinary controls then
navigate within it. All data and outcomes are simulated.

| Journey | Successful example | Failure / unavailable example | Review focus |
| --- | --- | --- | --- |
| Setup | Start → reveal public words → practice → Home | Failure/Unavailable → Retry → review | Clear completion; visible failure and recovery |
| Home | Middle pull-down or All apps → Search → Files; alternatively open Files → select download | Failure/Unavailable → Search → Files/Settings; unmatched query → no results | Discoverable navigation; useful empty/error states |
| Privacy | Change preset → preview → Apply → actual controls | Failure → Apply → partial → Retry; Unavailable → individual state retry | Accurate capability state; partial-result handling |
| Notifications | Find out why → enable app → sample test → received | Failure → stopped Work → profile settings → Personal; Unavailable → inspect or Retry | Scoped diagnosis and recovery; profile boundaries |
| Battery | Adjust → Apply → acknowledgement; health → export example | Failure → Apply → last confirmed value kept → Retry; Unavailable → care → health | Confirmed values; honest unsupported/error states |
| Updates | Download → verify → install → restart → completion fixture | Failure → verification rejection → fresh copy; Unavailable → connection/Retry | Verification before install; interruption and recovery |

Loading is a held inspection scenario. Home's loading state appears in Search.
Back/Home provides an exit; select Normal to inspect completion. Updater's
normal stages use a short timed fixture, not a performance measurement.

Empty exposes unmatched search, app selection, an empty shade, no battery
history (open Health), and no available update. An empty setup word source is
treated as a generation failure, never successful credential creation.

Companion files record conceptual inherited comparisons, entry, goal, state
coverage, completion, cancellation, focus, locale and implementation layer.
No upstream runtime was changed or exercised for this proposal. Implementing
owners must bind actual source/symbols before editing Android.

Page changes announce destinations and focus the first action; Search instead
focuses its query input. Back
restores the invoking action or falls back to the first control. The status
area offers labelled shade entry/dismissal before page controls. Same-page
updates preserve the active control, including timed updater transitions;
changes that replace an action move focus to its next useful action. Sheets contain Tab navigation, Escape closes
them, and Home is a labelled button. German is partial draft copy; RTL is a
layout stress mode with English strings. Native screen-reader, fixed-viewport
and device qualification remain unperformed.

The browser reference has received the geometry/copy/navigation pass described
in the [prototype record](../prototypes/README.md). Shared toolbar tracks,
content/group insets and button/row styles apply across all six flows and
supporting Settings, Files, permission and sharing screens. Public sample
warnings remain at credential/side-effect boundaries. Implementation notes
stay in these annotations, outside the depicted product flow.
