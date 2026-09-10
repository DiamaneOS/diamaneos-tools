# 06 — Updates

Proposed interface design mockup. Owners: OTA installation testing / interrupted-update recovery testing / offline signed OTA and updater integration.
Page: `updates`; sheets: details, connection and restart.

## Intent and inherited comparison

Entry: updater or Settings. Goal: understand identity/stage and recover from
a named failure. Keep the inherited Updater and A/B state machine. Appearance
cannot invent package trust, slot state or retention evidence.

## Path and states

Inspect device/source/target → download → verify → install → restart needed
→ confirm → completion fixture. Short timed stages expose the flow, not
Android execution or performance. Preview 01/02 and 428 MB are sample values.

| State | Behavior | Recovery |
| --- | --- | --- |
| Loading | Held update check | Back/Home |
| Available | Identity, size, verification requirement | Download or leave |
| Download/verify/install | Stage/progress; Later/Home | State continues in the running example |
| Restart needed | Explicit choice | Restart or Later |
| Complete | New running build and retention fixture | Native next-update check still required |
| Verification failure | Damaged download rejected before install; current build active | Download a fresh copy |
| Unavailable | No connection; installed version unchanged | Connection/Retry |
| No update | Installed identity and last-check context | Check again |

The executable error occurs before apply. Native cases also need wrong
device/source/key, downgrade refusal, low space, interruption, failed boot
and rollback. They cannot share generic retry copy: derive slot, retention
confidence and allowed recovery from the actual updater. Full OTA is offered
only where supported; SD selection enters the same verified pipeline.

Back/Later retains status within the example; the external gallery resets
the scenario. Native apply cannot be cancelled by dismissing the screen.
Restart explains passphrase use. Actual completion requires booted target,
retained data and next-update acceptance; the browser proves none of these.

## Focus and locale

Order: navigation → identity → details → Download → stage → Restart
confirmation → result. Announce stage changes, not progress frames. Tap and
keyboard reach actions; Escape dismisses confirmation and restores context.

Isolate version identifiers under RTL. German recovery wording needs fluent
review; partial draft translation is not acceptance. Native long errors must
wrap and preserve recovery access with large text/scrolling.

## Implementation mapping

Resources/shared app components: identity, stages, errors and confirmation.
Updater retains validation, package handling, slots and rollback. No alternate
trust path, signer, privileged visual service or new polling schedule.
