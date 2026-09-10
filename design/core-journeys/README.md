# Six journeys — Facet interface study

Use the gallery in `../prototypes/diamaneos-ui.html`. Optional design controls
select appearance, composition, palette, motion, text/layout stress and the
scenario. Scenario changes reset the current journey; ordinary controls then
navigate within it. All data and outcomes are simulated.

| Journey | Successful example | Failure / unavailable example | Review focus |
| --- | --- | --- | --- |
| Setup | Start → reveal public words → practice → Home | Failure/Unavailable → Retry → review | Clear completion; visible failure and recovery |
| Home | Search Files or open Files → select download | Failure/Unavailable → Search → Files/Settings; unmatched query → no results | Discoverable navigation; useful empty/error states |
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

Page changes announce destinations and focus the first control; Back attempts
to restore the invoking control. Sheets contain Tab navigation, Escape closes
them, and Home is a labelled button. German is partial draft copy; RTL is a
layout stress mode with English strings. Native screen-reader, fixed-viewport
and device qualification remain unperformed.
