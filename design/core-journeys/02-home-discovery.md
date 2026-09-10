# 02 — Home and discovery

Proposed interface design mockup. Owners: everyday app flows / native UI integration. Pages: `home`, `search`, `apps`,
`files`, `file`, `settings`; `atlas` illustrates an independent-app boundary.

## Intent and inherited comparison

Entry: unlocked Home. Goal: find an app, setting or download without learning
internals. Keep Launcher3, Settings search and DocumentsUI responsibilities.
Add a distinctive clock, obvious destinations, labelled apps and reachable
search. This is not a persistent content feed or an indexing service.

Files/Settings show coherent inherited surfaces. Fictional Atlas retains its
own brand while its system location sheet shares DiamaneOS styling. No real
independent app is claimed to have been modified or selected for inclusion.

## Path and states

Home → Search → app/setting result → open. Alternatively Files → Downloads
→ document. Share previews one file and explicit recipient selection; it
sends nothing. All apps has a tap alternative to an upward gesture.

| State | Behavior | Recovery |
| --- | --- | --- |
| Ready | Apps/settings or explicit Files destination | Open selection |
| Empty | Unmatched query → no results | Edit or Open Downloads |
| Loading | Held Searching state | Back/Home |
| Failure/unavailable | Search unavailable; direct links remain | Files or Settings |
| Completed | Selected destination open | Back preserves query |

Search covers apps/settings only in this study. Files open on demand, with
no background crawl, clipboard listener, message indexing or cross-profile
search. Query is held in memory. Back dismisses a sheet before leaving its
parent and keeps the query after a result. Gallery selection resets a journey.

## Focus and locale

Home: profile → notifications → Files/Settings → apps → All apps → search
→ Home. Search: Back → query/clear → results → escape links. Counts are
announced; native results need type/position and focus restoration across
app launches. Every task has a non-gesture route.

Large text stacks Home tiles and reduces grid columns. German wraps. RTL
mirrors layout/navigation while time and file identities remain readable.
This exposes stress conditions without claiming native verification.

## Implementation mapping

Resources: colours, type, spacing, eligible icons. Apps: existing Files/Settings
themes/components. Narrow Launcher3 candidate: Home composition, search
placement and transition polish; compare in native UI integration. Reuse native lifecycle
and Back first. Visual consistency does not authorize a new indexer, parser,
app fork or broader grants. Atlas's permission sheet stays with the actual
permission controller, preserving app/user identity and scope.
