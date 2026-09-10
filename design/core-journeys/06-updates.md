# Journey 6 — Updates (interface design prototype, synthetic data)

Entry: Updater (or setup tip linking there). Goal: identify an update failure
and the next action. Implementation: OTA installation testing / interrupted-update recovery testing + Updater work.
Inherited path: GOS Updater + A/B (or Virtual A/B) state machine; UI derives
progress from actual stages, never invents trust.

Normal path: check → download (staged) → verify → apply → restart-needed →
completed; identity (source→target, rollback state) shown before apply.
Completion: booted target + data retained + next-update accepted.
Back/cancel: safe before apply; after apply, guided restart only.

States: loading/downloading/verifying/applying/restart-needed/completed +
named errors (wrong source/key/device, corrupt, downgrade refused, low space,
interrupted stage) each with recovery exit. Failure state: failed update →
slot/rollback state + retained-data status + valid next action; full-OTA
fallback offered where supported .

Keyboard/SR : order check→identity→apply→progress→restart; progress
announced at stage changes; destructive confirmations explicit and labeled.

Locale: update identity/rollback/recovery as critical wording (review or
fallback); version/firmware limits stated, never "just retry".

Reuse : Updater UI + shared progress/confirm components; scoped SD picker
(offline signed OTA) feeds the same verified pipeline, never a sideload bypass.
