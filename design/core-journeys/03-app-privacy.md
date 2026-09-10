# Journey 3 — App privacy presets (interface design prototype, synthetic data)

Entry: app info → Privacy, or install-time preset sheet. Goal: restrict one
app in one pass and understand what changed. Implementation: app privacy presets.
Inherited path: GOS Network/Sensors/Storage/Contact Scopes; preset only
coordinates existing authorities.

Normal path: pick app → preset Untrusted/Standard/Trusted with per-capability
deltas (Network, Sensors, Storage, Contacts) → preview changes → Apply →
result shows applied per cell; manual edits flip label to Custom and persist.
Completion: summary + link to controlling setting (dashboard links, never
duplicates state). Back/cancel before Apply changes nothing.

States: loading (reading current controls) → error; partial failure shows
actual per-cell state + retry, never false all-applied; unsupported scope
labeled per cell. Failure state: one control fails → partial state + recovery
action (pattern for presets).

Keyboard/SR : order app→preset→preview→apply→result; each cell exposes
name/role/state ("Network: blocked"); Trusted never announced as "safe".

Locale: capability consequences translated as critical wording (fluent review
or fallback); "Trusted" glossed as user-chosen policy, never OS-certified.

Reuse : Settings app-info controllers + shared segmented/preview/list
components; preset is a coordinated request, not a permission source of truth.
No privileged umbrella controller for shared visuals (threat-model boundary).
