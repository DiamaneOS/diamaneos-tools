# Journey 5 — Battery controls (interface design prototype, synthetic data)

Entry: Settings → Battery. Goal: change charge limit, read health, export
history. Implementation: charge-limit integration / battery health reporting. Inherited path: Settings battery
controllers + validated charge-control adapter (power and thermal validation).

Normal path: charge ceiling slider (requested vs effective/observed state) →
health (cycles/capacity/temperature with age/quality) → trends → explicit
export (units/conditions, scoped data). Completion: ceiling acknowledged with
hardware tolerance; thermal/vendor safety stays authoritative and visible.

States: loading → error; unavailable control/measurement → honest
"unavailable", never zero or silent estimate; failed write → failure message,
no fake enforcement indicator. Failure state: control unavailable or write
fails → promise of enforcement disabled .

Keyboard/SR : order ceiling→effective state→health→trends→export; slider
announces value + effective state; safety limits announced, not color-only.

Locale: units/quality on every numeric field; replacement/reset marks a
discontinuity, never fake rejuvenation.

Reuse : battery controllers + shared slider/stat/export components; one
narrow authorized hardware adapter owns writes. No new background service.
